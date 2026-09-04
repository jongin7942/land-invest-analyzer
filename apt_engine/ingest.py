"""수집 → 저장 → 매칭 오케스트레이션 (PHASE 1).

각 단계는 개별 실패로 배치 전체가 죽지 않게 감싸되, **실패를 조용히 삼키지 않는다** —
`collection_log` 에 OK / EMPTY / FAILED 를 남긴다. 토지 파이프라인이 `except Exception`
후 그냥 진행해서 "데이터 없음"과 "수집 실패"가 뭉개졌던 문제([E-10])의 대응이다.
"""
from __future__ import annotations

import json
from datetime import date

from apt_engine import regions
from apt_engine.collectors import apt_rent, apt_trade, kapt, matcher, molit
from apt_engine.listing import change as change_mod
from apt_engine.listing import distribution as dist_mod
from apt_engine.listing import gap as gap_mod
from apt_engine.listing import pressure as pressure_mod
from apt_engine.listing.provider import ManualListingProvider
from apt_engine.catalyst import analogue as analogue_mod
from apt_engine.catalyst import assemble as assemble_mod
from apt_engine.catalyst import supply as supply_mod
from apt_engine.catalyst import transit as transit_mod
from apt_engine.collectors import geocode as geocode_mod
from apt_engine.price import snapshot as snap_mod
from apt_engine.repo import catalyst as cat_repo
from apt_engine.relative import benchmark as bench_mod
from apt_engine.relative import ratio as ratio_mod
from apt_engine.repo import relative as rel_repo
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
    BASIS_CHUNK = 100          # 기본정보 커밋 단위

    with get_conn(db_path) as conn:
        repo.sync_regions(conn)

    # ── 1단계: 시군구별 단지 목록. 시군구 하나가 곧 트랜잭션 하나다 ──
    for i, code in enumerate(codes, 1):
        try:
            rows = kapt.fetch_complex_list(code)
        except (kapt.KaptError, molit.MolitError) as e:
            stats["failed"] += 1
            with get_conn(db_path) as conn:
                repo.log_collection(conn, kapt.SOURCE_KEY, target=code, period=None,
                                    status="FAILED", error=str(e)[:500])
            progress(f"  [{i}/{len(codes)}] {regions.name_of(code)} 실패: {e}")
            continue

        for r in rows:
            r["name_norm"] = matcher.normalize(r["name"])
        with get_conn(db_path) as conn:
            src = repo.source_id(conn, kapt.SOURCE_KEY)
            n = repo.upsert_complexes(conn, rows, src_id=src)
            repo.log_collection(conn, kapt.SOURCE_KEY, target=code, period=None,
                                status="OK" if n else "EMPTY", row_count=n)
        stats["listed"] += n
        progress(f"  [{i}/{len(codes)}] {regions.name_of(code):16s} 단지 {n}개")

    if not with_basis:
        return stats

    # ── 2단계: 단지별 기본정보. 단지당 1회 호출이라 느리다 ──
    with get_conn(db_path) as conn:
        pending = [r["kapt_code"] for r in conn.execute(
            "SELECT kapt_code FROM complex "
            "WHERE kapt_code IS NOT NULL AND apt_households IS NULL").fetchall()]
    progress(f"\n기본정보 미수집 단지 {len(pending)}개 조회 중...")

    buf: list[dict] = []
    for i, code in enumerate(pending, 1):
        try:
            basis = kapt.fetch_basis(code)
        except (kapt.KaptError, molit.MolitError) as e:
            stats["failed"] += 1
            with get_conn(db_path) as conn:
                repo.log_collection(conn, kapt.SOURCE_KEY, target=code, period=None,
                                    status="FAILED", error=str(e)[:500])
            continue
        if not basis:
            with get_conn(db_path) as conn:
                repo.log_collection(conn, kapt.SOURCE_KEY, target=code, period=None,
                                    status="EMPTY")
            continue
        # 응답이 단지코드를 안 돌려주는 단지가 있다. 우리는 그 코드로 조회했으니
        # 어느 단지인지 알고 있다 — 요청한 코드를 채운다. 이게 없으면
        # upsert_complexes 가 묶음 전체를 거부해서 수집이 통째로 죽는다.
        basis.setdefault("kapt_code", code)
        if not basis.get("kapt_code"):
            basis["kapt_code"] = code
        if not basis.get("name"):
            # 이름조차 없으면 저장할 것이 없다. 세대수만 있는 응답은 아래에서
            # upsert 가 NULL 을 덮지 않으므로 그대로 두면 된다.
            with get_conn(db_path) as conn:
                repo.log_collection(conn, kapt.SOURCE_KEY, target=code, period=None,
                                    status="EMPTY", error="이름 없는 응답")
            continue

        # lawd_cd 등 목록 단계에서 채운 값은 upsert 가 유지한다(NULL 은 덮지 않음).
        basis["name_norm"] = matcher.normalize(basis.get("name"))
        buf.append(basis)

        if len(buf) >= BASIS_CHUNK or i == len(pending):
            with get_conn(db_path) as conn:
                src = repo.source_id(conn, kapt.SOURCE_KEY)
                repo.upsert_complexes(conn, buf, src_id=src)
            stats["basis"] += len(buf)
            buf = []
            progress(f"  {i}/{len(pending)}")

    if buf:                                   # 마지막 자투리
        with get_conn(db_path) as conn:
            src = repo.source_id(conn, kapt.SOURCE_KEY)
            repo.upsert_complexes(conn, buf, src_id=src)
        stats["basis"] += len(buf)

    return stats


# 최근 이 개월수는 재실행 때 항상 다시 받는다(신고 지연 반영).
REFETCH_MONTHS = 3


def _completed_pairs(conn, source_key: str) -> set[tuple[str, str]]:
    """이미 끝난 (시군구, 거래월). EMPTY 도 '받아봤더니 없었다' 라 끝난 것이다."""
    return {(r["target"], r["period"]) for r in conn.execute(
        "SELECT target, period FROM collection_log "
        "WHERE source_key = ? AND status IN ('OK','EMPTY') "
        "AND target IS NOT NULL AND period IS NOT NULL", (source_key,))}


def _collect_deals(kind: str, months: int, sido: str | None,
                   db_path: str | None, progress, full: bool = False) -> dict:
    """매매/전월세 공통. kind ∈ {'trade', 'rent'}

    full=True 면 이미 받은 달도 전부 다시 받는다.
    """
    if kind == "trade":
        mod, source_key, insert = apt_trade, apt_trade.SOURCE_KEY, repo.insert_trades
    else:
        mod, source_key, insert = apt_rent, apt_rent.SOURCE_KEY, repo.insert_jeonse

    yms = recent_yms(months)
    stats = {"months": len(yms), "fetched": 0, "inserted": 0, "empty": 0,
             "failed": 0, "skipped": 0, "quota_exhausted": False}

    with get_conn(db_path) as conn:
        repo.sync_regions(conn)
        src = repo.source_id(conn, source_key)
        done = set() if full else _completed_pairs(conn, source_key)

    # 최근 REFETCH_MONTHS 개월은 이미 받았어도 다시 받는다 — 실거래는 계약 후
    # 30일 내 신고라, 한 번 OK 로 찍힌 달에도 나중에 신고분이 더 들어온다.
    always = set(yms[-REFETCH_MONTHS:]) if yms else set()

    for ym in yms:
        # 그 달에 유효했던 코드로 요청한다. 구 개편 이전 거래는 옛 코드로만 나온다.
        codes = regions.codes_for_ym(ym, sido)
        month_rows = 0
        for code in codes:
            if ym not in always and (code, ym) in done:
                stats["skipped"] += 1
                continue
            try:
                rows = mod.fetch_month(code, ym)
            except molit.MolitQuotaError as e:
                # 한도 소진. 계속 두드려봐야 전부 거부된다 — 여기까지 받고 멈춘다.
                # 실패로 남기지 않는다. 안 받은 것이지 실패한 것이 아니고,
                # 실패로 쌓으면 다음 날 재시도 목록만 지저분해진다.
                progress(f"\n  {ym} {code} 에서 일일 한도 소진. 여기까지 받고 멈춥니다.")
                progress(f"  {e}")
                stats["quota_exhausted"] = True
                return stats
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
        note = ""
        if stats["skipped"]:
            note = f" · 누적 건너뜀 {stats['skipped']:,}"
        progress(f"  {ym}  시군구 {len(codes)}개 · 조회 {month_rows}건{note}")
    return stats


def collect_trades(months: int = 60, sido: str | None = None, *,
                   db_path: str | None = None, progress=print,
                   full: bool = False) -> dict:
    return _collect_deals("trade", months, sido, db_path, progress, full=full)


def collect_rents(months: int = 60, sido: str | None = None, *,
                  db_path: str | None = None, progress=print,
                  full: bool = False) -> dict:
    return _collect_deals("rent", months, sido, db_path, progress, full=full)


# ── 매칭 ──────────────────────────────────────────────────────────────

# 판정표를 몇 개씩 끊어 넣을지. INSERT 는 가벼워서 크게 잡아도 된다.
PLAN_CHUNK = 5_000

# 판정 결과를 담는 임시표. 조인 대상이라 (시군구, 단지명) 인덱스가 필요하다.
_PLAN_DDL = """
CREATE TABLE IF NOT EXISTS match_plan (
    tbl        TEXT NOT NULL,
    lawd_cd    TEXT NOT NULL,
    apt_name   TEXT NOT NULL,
    emd_name   TEXT,
    build_year INTEGER,
    complex_id INTEGER,
    confidence TEXT NOT NULL,
    reason     TEXT
)
"""


def run_matching(*, rebuild: bool = False, db_path: str | None = None,
                 progress=print) -> dict:
    """미매칭 거래를 단지에 붙인다.

    같은 (시군구·단지명·법정동·건축년도) 조합은 한 번만 판정한다 — 거래 수백만 건을
    한 건씩 매칭하면 느리기도 하고, 같은 이름에 다른 판정이 나올 수도 있다.

    **판정과 적용을 분리한다.** 예전에는 그룹마다 UPDATE 를 날렸는데, 1,218만 건에서
    40,852번의 UPDATE 가 6시간 30분이 지나도 절반이었다. UPDATE 하나하나가 WAL 에
    프레임을 쌓고 뒤따르는 조회가 그 WAL 을 훑느라 뒤로 갈수록 느려진다.

    판정은 순수 계산이라 DB 가 필요 없다. 메모리에서 다 판정한 뒤 판정표를 만들고,
    조인 한 번으로 적용한다 — 인덱스 탐색 4만 번이 테이블 한 번 통과로 바뀐다.
    """
    out = {}
    cache: dict[str, list] = {}

    for table, label in (("trade", "매매"), ("jeonse_contract", "전월세")):
        # rebuild 라도 기존 매칭을 NULL 로 지우지 않는다. 어차피 덮어쓸 것이라
        # 1,218만 행을 두 번 쓰게 될 뿐이다(실측: 지우는 UPDATE 만 7분+).
        # 대신 전부 다시 판정해서 덮는다.
        # ── 1. 판정할 이름을 읽고 후보 단지를 시군구별로 준비 ──
        with get_conn(db_path) as conn:
            groups = (repo.distinct_names(conn, table) if rebuild
                      else repo.distinct_unmatched_names(conn, table))
            for lawd in {g["lawd_cd"] for g in groups}:
                if lawd not in cache:
                    cache[lawd] = repo.candidates_for(conn, lawd)
        total = len(groups)
        progress(f"  {label}: 판정할 이름 {total:,}개")

        # ── 2. 메모리에서 전부 판정 (DB 쓰기 없음) ──
        applied = {"EXACT": 0, "STRONG": 0, "WEAK": 0, "NONE": 0}
        rows = []
        for i, g in enumerate(groups, 1):
            lawd = g["lawd_cd"]
            r = matcher.match(
                g["apt_name"], cache[lawd],
                emd_name=g["emd_name"], build_year=g["build_year"],
                sgg_name=regions.name_of(lawd),
            )
            rows.append((table, lawd, g["apt_name"], g["emd_name"], g["build_year"],
                         r.complex_id, r.confidence, r.reason))
            applied[r.confidence] += g["cnt"]
            if i % 10_000 == 0:
                progress(f"    판정 {i:,}/{total:,}")

        # ── 3. 판정표를 넣고 조인 한 번으로 적용 ──
        with get_conn(db_path) as conn:
            conn.execute(_PLAN_DDL)
            conn.execute("DELETE FROM match_plan WHERE tbl = ?", (table,))
            for i in range(0, len(rows), PLAN_CHUNK):
                conn.executemany(
                    "INSERT INTO match_plan (tbl, lawd_cd, apt_name, emd_name, "
                    "build_year, complex_id, confidence, reason) "
                    "VALUES (?,?,?,?,?,?,?,?)", rows[i:i + PLAN_CHUNK])
            conn.execute("CREATE INDEX IF NOT EXISTS idx_match_plan "
                         "ON match_plan (tbl, lawd_cd, apt_name)")
        progress(f"  {label}: 판정표 {len(rows):,}행 기록. 적용 중...")

        # rebuild 면 기존 값을 덮어쓴다. 아니면 아직 안 붙은 것만 채운다.
        only_new = "" if rebuild else " AND t.complex_id IS NULL"
        with get_conn(db_path) as conn:
            changed = conn.execute(
                f"UPDATE {table} AS t SET complex_id = p.complex_id, "
                f"       match_confidence = p.confidence, match_reason = p.reason "
                f"  FROM match_plan AS p "
                f" WHERE p.tbl = ?{only_new} "
                f"   AND t.lawd_cd = p.lawd_cd AND t.apt_name = p.apt_name "
                f"   AND t.emd_name IS p.emd_name AND t.build_year IS p.build_year",
                (table,)).rowcount
        progress(f"  {label}: {changed:,}건 적용 · "
                 + " · ".join(f"{k} {v:,}" for k, v in applied.items()))
        out[label] = applied

    with get_conn(db_path) as conn:
        created = repo.derive_unit_types_from_trades(conn)
    out["면적타입 생성"] = created
    progress(f"  실거래에서 면적타입 {created}개 도출")
    return out


# ── 가격 스냅샷 (PHASE 2) ─────────────────────────────────────────────

# 스냅샷을 몇 쌍마다 커밋할지. 쌍 하나가 모든 달을 함께 쓰므로 작게 잡는다.
SNAPSHOT_CHUNK = 500


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

    # 쌍 단위로 끊어 커밋한다. 전체를 한 트랜잭션으로 묶으면 WAL 이 계속 자라고
    # 진행률도 안 보인다 — 매칭에서 그렇게 몇 시간을 버렸다.
    # 쌍 하나가 그 쌍의 모든 달을 함께 처리하므로 경계에서 끊어도 어중간하지 않다.
    for chunk_start in range(0, len(pairs), SNAPSHOT_CHUNK):
      with get_conn(db_path) as conn:
        for i, pair in enumerate(pairs[chunk_start:chunk_start + SNAPSHOT_CHUNK],
                                 chunk_start + 1):
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
                progress(f"  {i:,}/{len(pairs):,} "
                         f"(비율 {stats['ratios']:,} · 정상비율 {stats['norms']:,} "
                         f"· 겹치는 달 없음 {stats['skipped']:,})")
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


# ── 상대가치 (PHASE 4) ────────────────────────────────────────────────

def build_benchmarks(*, area_band: str, min_households: int | None = None,
                     sido: str | None = None, top_n: int = bench_mod.DEFAULT_TOP_N,
                     db_path: str | None = None, progress=print) -> dict:
    """모든 단지에 비교단지를 붙인다. 근거가 부족한 단지는 0개로 남는다."""
    stats = {"targets": 0, "with_benchmarks": 0, "relations": 0, "no_ground": 0}
    with get_conn(db_path) as conn:
        pool = rel_repo.candidates(conn, area_band, min_households=min_households,
                                   sido=sido)
        stats["targets"] = len(pool)
        if not pool:
            progress("  대표가격이 있는 단지가 없습니다 — `cli snapshot` 을 먼저 돌리세요.")
            return stats

        for i, target in enumerate(pool, 1):
            picks = bench_mod.select(conn, target, pool, top_n=top_n)
            if not picks:
                stats["no_ground"] += 1
                continue
            rel_repo.replace_benchmarks(
                conn, target.complex_id, area_band,
                [(p, bench_mod.to_calc(target, p)) for p in picks])
            stats["with_benchmarks"] += 1
            stats["relations"] += len(picks)
            if i % 200 == 0:
                progress(f"  {i}/{len(pool)}")
    return stats


def build_ratios(*, area_band: str, db_path: str | None = None,
                 progress=print) -> dict:
    """비교단지별 월별 가격비율 시계열 + 구간별 정상비율."""
    stats = {"pairs": 0, "ratios": 0, "norms": 0, "skipped": 0}
    with get_conn(db_path) as conn:
        pairs = conn.execute(
            "SELECT DISTINCT complex_id, benchmark_complex_id FROM benchmark_relation "
            "WHERE area_band = ?", (area_band,)).fetchall()
        stats["pairs"] = len(pairs)

        snapshot_cache: dict[int, dict] = {}

        def snaps(cid: int) -> dict:
            if cid not in snapshot_cache:
                snapshot_cache[cid] = rel_repo.snapshots_by_ym(conn, cid, area_band)
            return snapshot_cache[cid]

        for i, pair in enumerate(pairs, 1):
            cid, bid = pair["complex_id"], pair["benchmark_complex_id"]
            mine, theirs = snaps(cid), snaps(bid)
            shared = sorted(set(mine) & set(theirs))
            if not shared:
                stats["skipped"] += 1
                continue

            for ym in shared:
                calc = ratio_mod.current_ratio(mine[ym], theirs[ym], area_band=area_band)
                if calc is None:
                    continue
                year_ago = _shift_ym(ym, -12)
                phase = ratio_mod.market_phase(
                    theirs[ym]["representative_price"],
                    theirs[year_ago]["representative_price"] if year_ago in theirs else None)
                rel_repo.save_ratio(
                    conn, complex_id=cid, benchmark_id=bid, area_band=area_band,
                    as_of_ym=ym, ratio=calc.value,
                    price_snapshot_id=mine[ym]["id"],
                    benchmark_snapshot_id=theirs[ym]["id"],
                    market_phase=phase,
                    confidence=calc.intermediates["신뢰도"], calc=calc)
                stats["ratios"] += 1

            history = rel_repo.ratio_history(conn, cid, bid, area_band)
            for norm in ratio_mod.normals(history, as_of_ym=shared[-1]):
                rel_repo.save_norm(conn, complex_id=cid, benchmark_id=bid,
                                   area_band=area_band, norm=norm)
                stats["norms"] += 1
            if i % 200 == 0:
                progress(f"  {i:,}/{len(pairs):,} "
                         f"(비율 {stats['ratios']:,} · 정상비율 {stats['norms']:,} "
                         f"· 겹치는 달 없음 {stats['skipped']:,})")
    return stats


def _shift_ym(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def relative_view(*, complex_id: int, area_band: str,
                  db_path: str | None = None) -> dict:
    """한 단지의 상대가치 — 비교단지별 현재비율 · 정상비율 · 격차."""
    out = {"benchmarks": []}
    with get_conn(db_path) as conn:
        rows = rel_repo.benchmarks_of(conn, complex_id, area_band)
        for b in rows:
            bid = b["benchmark_complex_id"]
            latest = rel_repo.latest_ratio(conn, complex_id, bid, area_band)
            norms = {n["window_key"]: n
                     for n in rel_repo.norms_of(conn, complex_id, bid, area_band)}
            out["benchmarks"].append({
                "row": b, "latest": latest, "norms": norms,
                "reasons": json.loads(b["selection_reason_json"]),
            })
    return out


# ── 촉매 (PHASE 5) ────────────────────────────────────────────────────

def geocode_complexes(*, limit: int | None = None, db_path: str | None = None,
                      progress=print) -> dict:
    """좌표가 없는 단지를 V-World 로 채운다. 실패는 남기고 추측하지 않는다."""
    # 한 건마다 외부 API 를 부르는 작업이라 몇 시간이 걸린다. 그동안 DB 를
    # 통째로 잡고 있으면 다른 작업이 하나도 못 돌고(실측: landarea 가
    # 'database is locked' 로 죽었다), 중간에 끊기면 전부 날아간다.
    CHUNK = 100

    stats = {"targets": 0, "filled": 0, "failed": 0}
    with get_conn(db_path) as conn:
        targets = cat_repo.complexes_missing_coords(conn, limit)
        stats["targets"] = len(targets)
    progress(f"좌표 없는 단지 {len(targets)}개 조회 중...")

    buf: list[tuple] = []
    logs: list[tuple] = []

    def flush() -> None:
        if not buf and not logs:
            return
        with get_conn(db_path) as conn:
            for cid, lat, lon in buf:
                cat_repo.set_coords(conn, cid, lat, lon)
            for cid, status, err in logs:
                repo.log_collection(conn, geocode_mod.SOURCE_KEY, target=str(cid),
                                    period=None, status=status, error=err)
        buf.clear()
        logs.clear()

    for i, row in enumerate(targets, 1):
        try:
            coords = geocode_mod.geocode_complex(row["road_addr"], row["jibun"])
        except geocode_mod.GeocodeError as e:
            logs.append((row["id"], "FAILED", str(e)[:300]))
            stats["failed"] += 1
            coords = None
        else:
            if coords:
                buf.append((row["id"], coords[0], coords[1]))
                stats["filled"] += 1
            else:
                logs.append((row["id"], "EMPTY", "주소로 좌표를 찾지 못함"))
                stats["failed"] += 1

        if i % CHUNK == 0 or i == len(targets):
            flush()
            progress(f"  {i}/{len(targets)} (채움 {stats['filled']} · "
                     f"실패 {stats['failed']})")
    flush()
    return stats


def collect_supply(*, limit: int | None = None, db_path: str | None = None,
                   progress=print) -> dict:
    """청약홈 분양정보 -> supply_plan. 정부 원천이라 announced_ym 을 실제 값으로 채운다.

    한국부동산원 청약홈이 주는 필드가 supply_plan 이 요구하는 것과 정확히 맞는다 -
    HOUSE_NM(단지명) · TOT_SUPLY_HSHLDCO(세대수) · MVN_PREARNGE_YM(입주예정월) ·
    RCRIT_PBLANC_DE(모집공고일 = announced_ym). "분양은 준공 30개월 전" 추정을
    쓸 필요가 없다 - 실제 공고일이 있다.

    주소만 주므로 지오코딩해서 좌표·법정동을 구한다. 여러 구역에 걸친 광역 개발
    (예: '인천계양지구 A6블록…경기도 부천시…서울특별시 강서구…')은 지오코딩이
    실패하고, 그러면 건너뛴다 - 대표 좌표를 지어내지 않는다.
    """
    from datetime import date
    from apt_engine.collectors import applyhome, geocode as geocode_mod, landinfo

    CHUNK = 100
    stats = {"targets": 0, "filled": 0, "skipped": 0, "failed": 0}

    try:
        rows = applyhome.fetch_all()
    except applyhome.ApplyhomeError as e:
        progress(f"청약홈 연결 실패: {e}")
        return stats
    metro = applyhome.metro_apartments(rows)
    if limit:
        metro = metro[:limit]
    stats["targets"] = len(metro)
    progress(f"청약홈 수도권 아파트 분양 {len(metro)}건 조회 중...")

    updates: list[tuple] = []
    logs: list[tuple] = []

    def flush() -> None:
        if not updates and not logs:
            return
        with get_conn(db_path) as conn:
            for row in updates:
                conn.execute(
                    "INSERT INTO supply_plan (lawd_cd, emd_name, complex_name, "
                    " households, move_in_ym, announced_ym, stage, kind, lat, lon, "
                    " source_name, source_url, last_verified, note) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(lawd_cd, complex_name, move_in_ym) DO UPDATE SET "
                    " announced_ym=excluded.announced_ym, households=excluded.households, "
                    " lat=excluded.lat, lon=excluded.lon, last_verified=excluded.last_verified",
                    row)
            for key, status, err in logs:
                repo.log_collection(conn, applyhome.SOURCE_KEY, target=key,
                                    period=None, status=status, error=err)
        updates.clear()
        logs.clear()

    for i, r in enumerate(metro, 1):
        name = r["HOUSE_NM"]
        addr = r["HSSPLY_ADRES"]
        approx = False
        try:
            coord = geocode_mod.geocode(addr, road=False) or geocode_mod.geocode(addr, road=True)
        except geocode_mod.GeocodeError as e:
            stats["failed"] += 1
            logs.append((name, "FAILED", str(e)[:300]))
            coord = None

        if not coord:
            # 공공택지지구 블록 분양은 지번이 아직 없어 통째로는 지오코딩이 안 된다.
            # "시도 시군구 읍면동" 만 남기고 재시도한다 - 실제 행정구역 이름이라 된다.
            # 좌표는 못 구해도 lawd_cd 만 맞으면 시군구 단위 집계에는 들어간다
            # (features/supply.py 는 lawd_cd 로만 거른다).
            prefix = applyhome.admin_prefix(addr)
            if prefix:
                try:
                    coord = geocode_mod.geocode(prefix, road=False)
                except geocode_mod.GeocodeError:
                    coord = None
                if coord:
                    approx = True

        if not coord:
            stats["skipped"] += 1
            logs.append((name, "EMPTY", f"주소를 지오코딩하지 못함: {addr[:200]}"))
        else:
            try:
                found = landinfo.pnu_at(coord[0], coord[1])
            except landinfo.LandInfoError:
                found = None
            dong_code = found[0][:10] if found else None
            if not dong_code:
                stats["skipped"] += 1
                logs.append((name, "EMPTY", "좌표는 찾았지만 법정동을 못 찾음"))
            else:
                move_in = (r.get("MVN_PREARNGE_YM") or "").strip()
                announced = (r.get("RCRIT_PBLANC_DE") or "").replace("-", "")[:6]
                note = (f"청약홈 관리번호 {r.get('HOUSE_MANAGE_NO', '')} · {addr[:150]}")
                if approx:
                    note += " · 지번 없는 공공택지 블록이라 행정동 중심 근사 좌표(시군구 단위만 신뢰)"
                updates.append((
                    dong_code[:5], None, name, int(r["TOT_SUPLY_HSHLDCO"]),
                    move_in, announced, "분양", "신규분양",
                    None if approx else coord[0], None if approx else coord[1],
                    applyhome.SOURCE_NAME, applyhome.SOURCE_URL,
                    date.today().isoformat(), note))
                stats["filled"] += 1

        if i % CHUNK == 0 or i == len(metro):
            flush()
            progress(f"  {i}/{len(metro)} (채움 {stats['filled']} · "
                     f"건너뜀 {stats['skipped']} · 실패 {stats['failed']})")
    flush()
    return stats


def collect_land_area(*, limit: int | None = None, db_path: str | None = None,
                      progress=print) -> dict:
    """단지 대지면적과 현재 용적률. 좌표가 있어야 한다.

    재건축 판정의 출발점이 용적률인데, 건축물대장의 대지면적 칸이 비어 있어
    토지 쪽(V-World LT_C_LANDINFOBASEMAP)에서 가져온다. 자세한 사정은
    collectors/landinfo.py 의 머리말에 적었다.

    **못 믿을 값은 넣지 않는다.** 여러 필지에 걸친 단지는 대표 필지 하나만
    잡혀서 용적률이 36,000% 같은 값이 되는데, 그런 단지는 '확인 불가' 로 남긴다.
    """
    from apt_engine.collectors import landinfo

    CHUNK = 100
    stats = {"targets": 0, "filled": 0, "skipped": 0, "failed": 0}

    with get_conn(db_path) as conn:
        sql = ("SELECT id, name, lat, lon, gross_floor_area_m2, apt_households "
               "  FROM complex "
               " WHERE lat IS NOT NULL AND lon IS NOT NULL "
               "   AND land_area_m2 IS NULL "
               " ORDER BY id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        targets = conn.execute(sql).fetchall()
    stats["targets"] = len(targets)
    progress(f"대지면적 조회 대상 {len(targets)}개...")

    updates: list[tuple] = []      # (id, pnu, area, far, source)  area 가 None 이면 PNU 만
    logs: list[tuple] = []

    def flush() -> None:
        if not updates and not logs:
            return
        with get_conn(db_path) as conn:
            for cid, pnu, area, far, source in updates:
                # PNU 는 못 믿을 면적이어도 남긴다 — 나중에 필지를 합산할 때 열쇠다.
                conn.execute("UPDATE complex SET pnu = ? WHERE id = ?", (pnu, cid))
                if area is not None:
                    conn.execute(
                        "UPDATE complex SET land_area_m2 = ?, current_far = ?, "
                        "  land_area_source = ?, land_area_verified = 0, "
                        "  updated_at = datetime('now','localtime') WHERE id = ?",
                        (area, far, source, cid))
            for cid, status, err in logs:
                repo.log_collection(conn, landinfo.SOURCE_KEY, target=str(cid),
                                    period=None, status=status, error=err)
        updates.clear()
        logs.clear()

    for i, row in enumerate(targets, 1):
        try:
            got = landinfo.land_of(
                row["lat"], row["lon"],
                gross_floor_area_m2=row["gross_floor_area_m2"],
                households=row["apt_households"])
        except landinfo.LandInfoError as e:
            stats["failed"] += 1
            logs.append((row["id"], "FAILED", str(e)[:300]))
            got = None
        else:
            if not got:
                stats["failed"] += 1
                logs.append((row["id"], "EMPTY", "좌표로 필지를 찾지 못함"))
            elif "skipped" in got:
                stats["skipped"] += 1
                updates.append((row["id"], got["pnu"], None, None, None))
                logs.append((row["id"], "EMPTY", got["skipped"][:300]))
            else:
                area = got["area_m2"]
                gross = row["gross_floor_area_m2"]
                # current_far 는 퍼센트다(redev/screening.py MAX_CURRENT_FAR=200.0
                # 등과 스케일을 맞춘다). 연면적/대지면적 은 소수 비율이라 x100 한다.
                far = (gross / area * 100) if (gross and area) else None
                updates.append((row["id"], got["pnu"], area, far,
                                f"V-World 필지 {got['pnu']} ({got['jibun']})"))
                stats["filled"] += 1

        if i % CHUNK == 0 or i == len(targets):
            flush()
            progress(f"  {i}/{len(targets)} (채움 {stats['filled']} · "
                     f"건너뜀 {stats['skipped']} · 실패 {stats['failed']})")
    flush()
    return stats


def build_catalysts(*, as_of: str, years: int = 5, area_band: str = "84",
                    complex_id: int | None = None, db_path: str | None = None,
                    progress=print) -> dict:
    """역세권 거리 → 촉매 생성. 개통한 역이 있으면 선행사례도 만든다."""
    stats = {"distances": 0, "complexes": 0, "catalysts": 0, "analogues": 0}
    as_of_ym = as_of[:4] + as_of[5:7]

    with get_conn(db_path) as conn:
        stats["distances"] = transit_mod.compute_distances(conn, complex_id=complex_id)

        # 선행사례 — 실제 개통한 역만
        for station in cat_repo.opened_stations(conn):
            a = analogue_mod.build(conn, station, area_band=area_band)
            if a:
                cat_repo.save_analogue(conn, a)
                stats["analogues"] += 1

        sql = "SELECT id FROM complex"
        params: list = []
        if complex_id is not None:
            sql += " WHERE id = ?"
            params.append(complex_id)
        for row in conn.execute(sql, params).fetchall():
            cid = row["id"]
            items = assemble_mod.from_transit(conn, cid, as_of=as_of, years=years)
            supply_item = assemble_mod.from_supply(conn, cid, as_of_ym=as_of_ym)
            if supply_item:
                items.append(supply_item)
            if not items:
                continue
            for item in items:
                cat_repo.save_catalyst(
                    conn, complex_id=cid, kind=item.kind, label=item.label,
                    station_id=item.station_id, expected_year=item.expected_year,
                    within_horizon=item.within_horizon, direction=item.direction,
                    evidence=item.evidence, confidence=item.confidence, calc=item.calc)
            stats["complexes"] += 1
            stats["catalysts"] += len(items)
    return stats


def catalyst_view(*, complex_id: int, as_of: str, years: int = 5,
                  area_band: str = "84", db_path: str | None = None) -> dict:
    """한 단지의 촉매 + 선행사례."""
    as_of_ym = as_of[:4] + as_of[5:7]
    with get_conn(db_path) as conn:
        stations = transit_mod.nearby(conn, complex_id)
        items = assemble_mod.from_transit(conn, complex_id, as_of=as_of, years=years)
        supply_item = assemble_mod.from_supply(conn, complex_id, as_of_ym=as_of_ym)
        if supply_item:
            items.append(supply_item)
        summary = assemble_mod.summarize(items, years=years)

        row = conn.execute("SELECT lawd_cd, lat, lon, apt_households FROM complex "
                           "WHERE id = ?", (complex_id,)).fetchone()
        supply_calc = supply_mod.analyze(
            conn, lawd_cd=row["lawd_cd"], as_of_ym=as_of_ym,
            lat=row["lat"], lon=row["lon"], radius_m=3000,
            stock_households=row["apt_households"]) if row else None

        projects = {s.project_name for s in stations if not s.opened}
        cases = []
        for pname in sorted(projects):
            cases.extend(cat_repo.analogues(conn, area_band=area_band))
        analogue_summary = None
        if cases:
            from apt_engine.catalyst.analogue import Analogue
            deltas = [dict(c) for c in cases]
            analogue_summary = deltas
    return {"stations": stations, "items": items, "summary": summary,
            "supply": supply_calc, "analogues": analogue_summary,
            "has_coords": bool(row and row["lat"] and row["lon"])}
