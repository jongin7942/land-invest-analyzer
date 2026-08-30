"""수집 → 저장 → 매칭 오케스트레이션 (PHASE 1).

각 단계는 개별 실패로 배치 전체가 죽지 않게 감싸되, **실패를 조용히 삼키지 않는다** —
`collection_log` 에 OK / EMPTY / FAILED 를 남긴다. 토지 파이프라인이 `except Exception`
후 그냥 진행해서 "데이터 없음"과 "수집 실패"가 뭉개졌던 문제([E-10])의 대응이다.
"""
from __future__ import annotations

from datetime import date

from apt_engine import regions
from apt_engine.collectors import apt_rent, apt_trade, kapt, matcher, molit
from apt_engine.db.connection import get_conn
from apt_engine.repo import apt as repo


def recent_yms(months: int, *, end: date | None = None) -> list[str]:
    """오늘 기준 최근 N개월의 YYYYMM (과거→현재)."""
    today = end or date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(months):
        out.append(f"{y:04d}{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


# ── 단지 ──────────────────────────────────────────────────────────────

def collect_complexes(sido: str | None = None, *, with_basis: bool = True,
                      db_path: str | None = None, progress=print) -> dict:
    """K-apt 단지 목록(+기본정보)을 수집한다.

    2단계다. 목록만 먼저 받아 단지코드를 확보하고(빠름), 기본정보는 단지당 1회
    호출이라 느리다 — 중단됐다 다시 돌려도 이미 채워진 단지는 건너뛴다.
    """
    codes = regions.all_codes(sido)
    stats = {"regions": len(codes), "listed": 0, "basis": 0, "failed": 0}

    with get_conn(db_path) as conn:
        repo.sync_regions(conn)
        src = repo.source_id(conn, kapt.SOURCE_KEY)

        for i, code in enumerate(codes, 1):
            try:
                rows = kapt.fetch_complex_list(code)
            except (kapt.KaptError, molit.MolitError) as e:
                stats["failed"] += 1
                repo.log_collection(conn, kapt.SOURCE_KEY, target=code, period=None,
                                    status="FAILED", error=str(e)[:500])
                progress(f"  [{i}/{len(codes)}] {regions.name_of(code)} 실패: {e}")
                continue

            for r in rows:
                r["name_norm"] = matcher.normalize(r["name"])
            n = repo.upsert_complexes(conn, rows, src_id=src)
            stats["listed"] += n
            repo.log_collection(conn, kapt.SOURCE_KEY, target=code, period=None,
                                status="OK" if n else "EMPTY", row_count=n)
            progress(f"  [{i}/{len(codes)}] {regions.name_of(code):16s} 단지 {n}개")

        if not with_basis:
            return stats

        pending = conn.execute(
            "SELECT kapt_code FROM complex "
            "WHERE kapt_code IS NOT NULL AND apt_households IS NULL"
        ).fetchall()
        progress(f"\n기본정보 미수집 단지 {len(pending)}개 조회 중...")
        for i, row in enumerate(pending, 1):
            code = row["kapt_code"]
            try:
                basis = kapt.fetch_basis(code)
            except (kapt.KaptError, molit.MolitError) as e:
                stats["failed"] += 1
                repo.log_collection(conn, kapt.SOURCE_KEY, target=code, period=None,
                                    status="FAILED", error=str(e)[:500])
                continue
            if not basis:
                repo.log_collection(conn, kapt.SOURCE_KEY, target=code, period=None,
                                    status="EMPTY")
                continue
            # lawd_cd 등 목록 단계에서 채운 값은 upsert 가 유지한다(NULL 은 덮지 않음).
            basis["name_norm"] = matcher.normalize(basis.get("name"))
            repo.upsert_complexes(conn, [basis], src_id=src)
            stats["basis"] += 1
            if i % 100 == 0:
                progress(f"  {i}/{len(pending)}")
    return stats


# ── 실거래 ────────────────────────────────────────────────────────────

def _collect_deals(kind: str, months: int, sido: str | None,
                   db_path: str | None, progress) -> dict:
    """매매/전월세 공통. kind ∈ {'trade', 'rent'}"""
    if kind == "trade":
        mod, source_key, insert = apt_trade, apt_trade.SOURCE_KEY, repo.insert_trades
    else:
        mod, source_key, insert = apt_rent, apt_rent.SOURCE_KEY, repo.insert_jeonse

    yms = recent_yms(months)
    stats = {"months": len(yms), "fetched": 0, "inserted": 0, "empty": 0, "failed": 0}

    with get_conn(db_path) as conn:
        repo.sync_regions(conn)
        src = repo.source_id(conn, source_key)

    for ym in yms:
        # 그 달에 유효했던 코드로 요청한다. 구 개편 이전 거래는 옛 코드로만 나온다.
        codes = regions.codes_for_ym(ym, sido)
        month_rows = 0
        for code in codes:
            try:
                rows = mod.fetch_month(code, ym)
            except molit.MolitAuthError as e:
                # 인증 문제는 계속 돌려도 소용없다 — 즉시 중단하고 알린다.
                with get_conn(db_path) as conn:
                    repo.log_collection(conn, source_key, target=code, period=ym,
                                        status="FAILED", error=str(e)[:500])
                raise
            except molit.MolitError as e:
                stats["failed"] += 1
                with get_conn(db_path) as conn:
                    repo.log_collection(conn, source_key, target=code, period=ym,
                                        status="FAILED", error=str(e)[:500])
                continue

            with get_conn(db_path) as conn:
                n = insert(conn, rows, src_id=src)
                repo.log_collection(conn, source_key, target=code, period=ym,
                                    status="OK" if rows else "EMPTY", row_count=len(rows))
            stats["fetched"] += len(rows)
            stats["inserted"] += n
            stats["empty"] += 0 if rows else 1
            month_rows += len(rows)
        progress(f"  {ym}  시군구 {len(codes)}개 · 조회 {month_rows}건")
    return stats


def collect_trades(months: int = 60, sido: str | None = None, *,
                   db_path: str | None = None, progress=print) -> dict:
    return _collect_deals("trade", months, sido, db_path, progress)


def collect_rents(months: int = 60, sido: str | None = None, *,
                  db_path: str | None = None, progress=print) -> dict:
    return _collect_deals("rent", months, sido, db_path, progress)


# ── 매칭 ──────────────────────────────────────────────────────────────

def run_matching(*, rebuild: bool = False, db_path: str | None = None,
                 progress=print) -> dict:
    """미매칭 거래를 단지에 붙인다.

    같은 (시군구·단지명·법정동·건축년도) 조합은 한 번만 판정하고 결과를 일괄 적용한다 —
    거래 수백만 건을 한 건씩 매칭하면 느리기도 하고, 같은 이름에 다른 판정이 나올
    수도 있다.
    """
    out = {}
    with get_conn(db_path) as conn:
        cache: dict[str, list] = {}
        for table, label in (("trade", "매매"), ("jeonse_contract", "전월세")):
            if rebuild:
                repo.clear_matches(conn, table)
            groups = repo.distinct_unmatched_names(conn, table)
            applied = {"EXACT": 0, "STRONG": 0, "WEAK": 0, "NONE": 0}
            for g in groups:
                lawd = g["lawd_cd"]
                if lawd not in cache:
                    cache[lawd] = repo.candidates_for(conn, lawd)
                result = matcher.match(
                    g["apt_name"], cache[lawd],
                    emd_name=g["emd_name"], build_year=g["build_year"],
                )
                repo.apply_match(
                    conn, table, lawd_cd=lawd, apt_name=g["apt_name"],
                    emd_name=g["emd_name"], build_year=g["build_year"],
                    complex_id=result.complex_id, confidence=result.confidence,
                    reason=result.reason,
                )
                applied[result.confidence] += g["cnt"]
            out[label] = applied
            progress(f"  {label}: " + " · ".join(f"{k} {v:,}" for k, v in applied.items()))

        created = repo.derive_unit_types_from_trades(conn)
        out["면적타입 생성"] = created
        progress(f"  실거래에서 면적타입 {created}개 도출")
    return out
