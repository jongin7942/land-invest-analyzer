"""수집 → 저장 → 매칭 오케스트레이션 (PHASE 1).

각 단계는 개별 실패로 배치 전체가 죽지 않게 감싸되, **실패를 조용히 삼키지 않는다** —
`collection_log` 에 OK / EMPTY / FAILED 를 남긴다. 토지 파이프라인이 `except Exception`
후 그냥 진행해서 "데이터 없음"과 "수집 실패"가 뭉개졌던 문제([E-10])의 대응이다.
"""
from __future__ import annotations

from datetime import date

from apt_engine import regions
from apt_engine.collectors import apt_rent, apt_trade, kapt, matcher, molit
from apt_engine.listing import change as change_mod
from apt_engine.listing import distribution as dist_mod
from apt_engine.listing import gap as gap_mod
from apt_engine.listing import pressure as pressure_mod
from apt_engine.listing.provider import ManualListingProvider
from apt_engine.price import snapshot as snap_mod
from apt_engine.repo import listing as listing_repo
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
                    sgg_name=regions.name_of(lawd),
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


# ── 가격 스냅샷 (PHASE 2) ─────────────────────────────────────────────

def build_snapshots(*, as_of_ym: str | None = None, months: int = 1,
                    window_months: int = snap_mod.DEFAULT_WINDOW_MONTHS,
                    min_households: int | None = None, sido: str | None = None,
                    db_path: str | None = None, progress=print) -> dict:
    """매칭된 (단지 × 면적밴드)마다 대표 매매가·전세가·전세가율을 계산해 저장한다.

    months 를 늘리면 그만큼 과거 시점의 스냅샷도 만든다 — 같은 실거래를 다른 창으로
    다시 집계할 뿐이라 추가 수집이 필요 없고, 이게 그대로 요구사항 4(과거 가격비율)의
    재료가 된다.
    """
    as_of = as_of_ym or _current_ym()
    targets_ym = _recent_from(as_of, months)
    stats = {"pairs": 0, "price": 0, "jeonse": 0, "ratio": 0, "skipped": 0}

    with get_conn(db_path) as conn:
        pairs = repo.matched_complex_bands(conn, min_households=min_households, sido=sido)
        stats["pairs"] = len(pairs)
        progress(f"대상 (단지×면적) {len(pairs)}쌍 × {len(targets_ym)}개월")

        for i, pair in enumerate(pairs, 1):
            cid, band = pair["complex_id"], pair["area_band"]
            trades = repo.trades_for(conn, cid, band)
            jeonse = repo.jeonse_for(conn, cid, band)

            for ym in targets_ym:
                ps = snap_mod.build_price(trades, as_of_ym=ym, window_months=window_months)
                js = snap_mod.build_jeonse(jeonse, as_of_ym=ym, window_months=window_months)

                if not ps.usable and not js.usable:
                    stats["skipped"] += 1
                    continue

                psid = repo.save_price_snapshot(conn, complex_id=cid, area_band=band, snap=ps)
                if psid:
                    stats["price"] += 1
                ratio = snap_mod.jeonse_ratio(ps, js) if (ps.usable and js.usable) else None
                if js.usable:
                    repo.save_jeonse_snapshot(conn, complex_id=cid, area_band=band, snap=js,
                                              price_snapshot_id=psid, ratio_calc=ratio)
                    stats["jeonse"] += 1
                if ratio:
                    stats["ratio"] += 1

            if i % 200 == 0:
                progress(f"  {i}/{len(pairs)}")
    return stats


def _current_ym() -> str:
    today = date.today()
    return f"{today.year:04d}{today.month:02d}"


def _recent_from(as_of_ym: str, months: int) -> list[str]:
    """as_of 를 끝으로 하는 최근 N개월 목록(과거→현재)."""
    y, m = int(as_of_ym[:4]), int(as_of_ym[4:])
    total = y * 12 + (m - 1)
    return [f"{(total - k) // 12:04d}{(total - k) % 12 + 1:02d}"
            for k in range(months - 1, -1, -1)]


# ── 호가 (PHASE 2.5) ──────────────────────────────────────────────────

def import_listings(path: str, *, fmt: str = "csv", seen_on: str | None = None,
                    provider: str | None = None, db_path: str | None = None,
                    deactivate: bool = True, progress=print) -> dict:
    """수기 매물 파일을 읽어 저장하고, 그날의 스냅샷을 찍고, 단지에 붙인다."""
    loader = (ManualListingProvider.from_csv if fmt == "csv"
              else ManualListingProvider.from_json)
    prov = loader(path, seen_on=seen_on, provider=provider)
    rows = prov.get_all()
    if not rows:
        return {"read": 0, "new": 0, "updated": 0, "snapshot": 0, "matched": 0}

    seen = prov.seen_on
    with get_conn(db_path) as conn:
        src = repo.source_id(conn, "manual_listing")

        # 단지 매칭 — 실거래와 같은 규칙을 쓴다.
        cache: dict[str, list] = {}
        matched = 0
        for r in rows:
            lawd = r.get("lawd_cd")
            if not lawd:
                # 시군구를 안 적었으면 이름만으로 전체에서 찾는다(동명 단지는 붙지 않는다).
                found = repo.find_complexes(conn, r["apt_name"])
                exact = [c for c in found
                         if c["name_norm"] == matcher.normalize(r["apt_name"])]
                if len(exact) == 1:
                    r["complex_id"] = exact[0]["id"]
                    r["match_confidence"] = "EXACT"
                    r["match_reason"] = "이름 완전일치(시군구 미지정)"
                    r["lawd_cd"] = exact[0]["lawd_cd"]
                    matched += 1
                else:
                    r["match_confidence"] = "NONE"
                    r["match_reason"] = (
                        f"lawd_cd 없이 이름으로 {len(exact)}개 후보 — 특정 불가. "
                        f"CSV 에 lawd_cd 를 넣으면 정확해집니다")
                continue
            if lawd not in cache:
                cache[lawd] = repo.candidates_for(conn, lawd)
            result = matcher.match(r["apt_name"], cache[lawd])
            r["complex_id"] = result.complex_id
            r["match_confidence"] = result.confidence
            r["match_reason"] = result.reason
            matched += 1 if result.complex_id else 0

        stats = listing_repo.upsert_listings(conn, rows, src_id=src)
        snapped = listing_repo.save_daily_snapshot(conn, rows, seen)
        gone = (listing_repo.deactivate_missing(conn, prov.provider, seen)
                if deactivate else 0)
        repo.log_collection(conn, "manual_listing", target=path, period=seen,
                            status="OK", row_count=len(rows))

    progress(f"  매물 {len(rows)}건 · 신규 {stats['new']} · 갱신 {stats['updated']} · "
             f"단지매칭 {matched} · 시장이탈 처리 {gone}")
    return {"read": len(rows), **stats, "snapshot": snapped,
            "matched": matched, "deactivated": gone}


def analyze_listings(*, complex_id: int, area_band: str, trade_type: str = "매매",
                     window_days: int = 30, db_path: str | None = None) -> dict:
    """호가 분포 + 실거래 괴리 + 변화 + 시장압력. 없는 부분은 None 으로 돌려준다."""
    from datetime import datetime, timedelta

    with get_conn(db_path) as conn:
        listings = listing_repo.active_listings(
            conn, complex_id=complex_id, area_band=area_band, trade_type=trade_type)
        dist = dist_mod.analyze(listings, trade_type=trade_type) if listings else None

        ps_row = repo.latest_price_snapshot(conn, complex_id, area_band)
        trades = repo.trades_for(conn, complex_id, area_band)
        recent = None
        normal = [t for t in trades if not t["cancel_yn"] and t["deal_type"] != "직거래"]
        if normal:
            recent = int(max(normal, key=lambda t: t["deal_ymd"])["deal_amount"])

        dates = listing_repo.snapshot_dates(
            conn, complex_id=complex_id, area_band=area_band, trade_type=trade_type)
        chg = None
        if len(dates) >= 2:
            latest = dates[-1]
            target = (datetime.fromisoformat(latest) - timedelta(days=window_days)).date()
            earlier = next((d for d in reversed(dates[:-1])
                            if datetime.fromisoformat(d).date() <= target), dates[0])
            chg = change_mod.compare(
                listing_repo.snapshot_rows(conn, earlier, complex_id=complex_id,
                                           area_band=area_band, trade_type=trade_type),
                listing_repo.snapshot_rows(conn, latest, complex_id=complex_id,
                                           area_band=area_band, trade_type=trade_type),
                from_date=earlier, to_date=latest)

        trade_trend = _snapshot_trend(conn, "price_snapshot", "representative_price",
                                      complex_id, area_band)
        jeonse_trend = _snapshot_trend(conn, "jeonse_snapshot", "representative_deposit",
                                       complex_id, area_band)

    press = pressure_mod.build(change=chg, trade_trend=trade_trend,
                               jeonse_trend=jeonse_trend)
    gap_calc = None
    if dist is not None:
        ps = _row_to_snapshot(ps_row)
        gap_calc = gap_mod.analyze(dist, ps, recent_trade_price=recent)

    return {"distribution": dist, "gap": gap_calc, "change": chg,
            "pressure": press, "recent_trade": recent,
            "price_snapshot_row": ps_row, "listing_count": len(listings)}


def _snapshot_trend(conn, table: str, col: str, complex_id: int,
                    area_band: str) -> float | None:
    """최근 두 스냅샷의 변화율. 하나뿐이면 방향을 알 수 없으므로 None."""
    rows = conn.execute(
        f"SELECT {col} FROM {table} WHERE complex_id=? AND area_band=? "
        f"ORDER BY as_of_ym DESC LIMIT 2", (complex_id, area_band)).fetchall()
    if len(rows) < 2 or not rows[1][0]:
        return None
    return (rows[0][0] - rows[1][0]) / rows[1][0]


def _row_to_snapshot(row):
    """DB 행 → 괴리 계산이 쓸 수 있는 최소 형태."""
    if row is None:
        return None
    from apt_engine.trace import Calc

    class _S:
        usable = True
        value = row["representative_price"]
        calc = Calc.from_json(row["calc_trace"])
    return _S()
