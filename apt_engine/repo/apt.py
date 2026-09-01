"""아파트 원자료 저장·조회.

엔진 함수는 DB를 모른다는 원칙에 따라, SQL 은 전부 여기에 모은다.
수집기는 dict 를 만들고, 엔진은 값을 받고, 그 사이를 이 모듈이 잇는다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from apt_engine import regions
from apt_engine.collectors.matcher import Candidate
from apt_engine.db.connection import get_conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 시군구 ────────────────────────────────────────────────────────────

def sync_regions(conn: sqlite3.Connection) -> int:
    """regions.py 의 코드표를 region 표에 반영한다(멱등).

    개편으로 사라진 코드(`RETIRED`)도 `is_active=0` 으로 함께 넣는다.
    **지우지 않는 이유**: 그 코드로 저장된 과거 거래와 과거 판정의 근거가
    남아 있어야 나중에 되짚을 수 있다. 지우면 그 데이터가 고아가 된다.
    """
    rows = [(code, regions.sido_of(code), name, 1, None)
            for code, name in regions.SIGUNGU.items()]
    rows += [(code, regions.sido_of(code), info["name"], 0, info["until_ym"])
             for code, info in regions.LEGACY.items()]
    rows += [(code, info["sido"], info["name"], 0, info["until_ym"])
             for code, info in regions.RETIRED.items()]
    conn.executemany(
        "INSERT INTO region (lawd_cd, sido, name, is_active, until_ym) VALUES (?,?,?,?,?) "
        "ON CONFLICT(lawd_cd) DO UPDATE SET "
        "sido=excluded.sido, name=excluded.name, "
        "is_active=excluded.is_active, until_ym=excluded.until_ym",
        rows,
    )
    sync_region_lineage(conn)
    return len(rows)


def sync_region_lineage(conn: sqlite3.Connection) -> int:
    """개편 승계 관계를 region_lineage 에 반영한다(멱등).

    옛 코드를 새 코드로 **치환하지 않고 잇는다.** 치환하면 개편 전
    이력이 사라지고, 그 시점에 어떤 규칙이 걸려 있었는지 다시 말할 수
    없게 된다.
    """
    rows = [(old, new, regions.LINEAGE_EFFECTIVE_FROM, relation, coverage,
             regions.LINEAGE_SOURCE,
             f"{regions.name_of(old)} → {regions.name_of(new)}")
            for old, new, relation, coverage in regions.LINEAGE]
    conn.executemany(
        "INSERT INTO region_lineage (predecessor_lawd_cd, successor_lawd_cd,"
        " effective_from, relation, coverage, source_name, note)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(predecessor_lawd_cd, successor_lawd_cd, effective_from)"
        " DO UPDATE SET relation=excluded.relation, coverage=excluded.coverage,"
        " source_name=excluded.source_name, note=excluded.note",
        rows,
    )
    return len(rows)


def source_id(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute("SELECT id FROM data_source WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


# ── 수집 이력 ─────────────────────────────────────────────────────────

def log_collection(conn: sqlite3.Connection, source_key: str, *, target: str | None,
                   period: str | None, status: str, row_count: int | None = None,
                   error: str | None = None) -> None:
    """"데이터 없음(EMPTY)"과 "수집 실패(FAILED)"를 반드시 구분해서 남긴다."""
    conn.execute(
        "INSERT INTO collection_log (source_key, target, period, status, row_count, error) "
        "VALUES (?,?,?,?,?,?)",
        (source_key, target, period, status, row_count, error),
    )


# ── 단지 ──────────────────────────────────────────────────────────────

_COMPLEX_COLS = (
    "kapt_code", "name", "name_norm", "lawd_cd", "emd_name", "jibun", "road_addr",
    "apt_households", "officetel_households", "building_count",
    "approval_date", "approval_year", "builder",
    "land_area_m2", "building_area_m2", "gross_floor_area_m2",
    "current_far", "current_bcr", "zoning", "heat_type", "parking_count",
    "source_id", "retrieved_at", "confidence", "raw_json",
)


def upsert_complexes(conn: sqlite3.Connection, rows: list[dict], *,
                     src_id: int | None = None) -> int:
    """kapt_code 기준 UPSERT.

    단지 수집은 2단계다 — 목록으로 단지코드를 먼저 받고(이름·시군구만 있음),
    기본정보를 나중에 받아 세대수·사용승인일을 채운다. 그래서 **나중에 온 값이
    NULL 이면 기존 값을 유지**해야 한다.

    `INSERT … ON CONFLICT DO UPDATE` 로는 안 된다. 그 구문도 INSERT 후보 행의
    NOT NULL 제약을 먼저 검사해서, lawd_cd 가 없는 기본정보 행이 곧바로 거부된다.
    그래서 존재 여부를 보고 INSERT / UPDATE 를 나눈다.
    """
    if not rows:
        return 0
    now = _now()
    n = 0
    for r in rows:
        code = r.get("kapt_code")
        if not code:
            raise ValueError(f"kapt_code 없는 단지는 저장할 수 없습니다: {r.get('name')!r}")

        payload = {}
        for c in _COMPLEX_COLS:
            if c == "raw_json":
                raw = r.get("raw")
                payload[c] = json.dumps(raw, ensure_ascii=False) if raw else None
            elif c == "source_id":
                payload[c] = r.get("source_id", src_id)
            elif c == "retrieved_at":
                payload[c] = r.get("retrieved_at", now)
            else:
                payload[c] = r.get(c)

        exists = conn.execute(
            "SELECT 1 FROM complex WHERE kapt_code = ?", (code,)).fetchone()
        if exists:
            cols = [c for c in _COMPLEX_COLS if c != "kapt_code"]
            sets = ",".join(f"{c}=COALESCE(?, {c})" for c in cols)
            conn.execute(
                f"UPDATE complex SET {sets}, updated_at=datetime('now','localtime') "
                f"WHERE kapt_code = ?",
                [payload[c] for c in cols] + [code],
            )
        else:
            placeholders = ",".join("?" for _ in _COMPLEX_COLS)
            conn.execute(
                f"INSERT INTO complex ({','.join(_COMPLEX_COLS)}) VALUES ({placeholders})",
                [payload[c] for c in _COMPLEX_COLS],
            )
        n += 1
    return n


def candidates_for(conn: sqlite3.Connection, lawd_cd: str) -> list[Candidate]:
    """매칭 후보 — **같은 시군구의 단지만**. 시군구가 다르면 다른 단지다."""
    rows = conn.execute(
        "SELECT id, name, name_norm, emd_name, approval_year FROM complex WHERE lawd_cd = ?",
        (lawd_cd,),
    ).fetchall()
    return [Candidate(r["id"], r["name"], r["name_norm"], r["emd_name"], r["approval_year"])
            for r in rows]


def upsert_unit_types(conn: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO unit_type (complex_id, exclusive_area_m2, area_band, supply_area_m2, "
        "households, land_share_m2) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(complex_id, exclusive_area_m2) DO UPDATE SET "
        "area_band=excluded.area_band, "
        "supply_area_m2=COALESCE(excluded.supply_area_m2, supply_area_m2), "
        "households=COALESCE(excluded.households, households), "
        "land_share_m2=COALESCE(excluded.land_share_m2, land_share_m2)",
        [(r["complex_id"], r["exclusive_area_m2"], r["area_band"], r.get("supply_area_m2"),
          r.get("households"), r.get("land_share_m2")) for r in rows],
    )
    return len(rows)


def derive_unit_types_from_trades(conn: sqlite3.Connection) -> int:
    """매칭된 실거래에서 면적타입을 역으로 만든다.

    K-apt 기본정보는 평형별 세대수를 정밀하게 주지 않는다. 실제로 거래된 전용면적이
    그 단지에 존재하는 타입이라는 건 확실하므로, 우선 이걸로 채우고 세대수는 비워 둔다
    (세대수가 필요한 계산은 PHASE 6 이후이고, 그때 정밀 데이터로 덮는다).
    """
    cur = conn.execute("""
        INSERT INTO unit_type (complex_id, exclusive_area_m2, area_band)
        SELECT DISTINCT complex_id, exclusive_area_m2, area_band
        FROM trade
        WHERE complex_id IS NOT NULL
        ON CONFLICT(complex_id, exclusive_area_m2) DO NOTHING
    """)
    return cur.rowcount


# ── 실거래 ────────────────────────────────────────────────────────────

_TRADE_COLS = (
    "complex_id", "match_confidence", "match_reason",
    "lawd_cd", "emd_name", "jibun", "apt_name", "apt_dong",
    "exclusive_area_m2", "area_band", "deal_amount", "deal_ymd", "floor", "build_year",
    "deal_type", "agent_region", "cancel_yn", "cancel_ymd", "registration_ymd",
    "seller_type", "buyer_type", "raw_json", "source_id", "retrieved_at",
)

_JEONSE_COLS = (
    "complex_id", "match_confidence", "match_reason",
    "lawd_cd", "emd_name", "jibun", "apt_name",
    "exclusive_area_m2", "area_band", "deposit", "monthly_rent", "contract_ymd",
    "floor", "build_year", "contract_type", "use_renewal_right",
    "prev_deposit", "prev_monthly_rent", "contract_term",
    "raw_json", "source_id", "retrieved_at",
)


def _insert_rows(conn: sqlite3.Connection, table: str, cols: tuple[str, ...],
                 rows: list[dict], src_id: int | None) -> int:
    """UNIQUE 충돌은 무시(같은 달을 다시 수집해도 중복되지 않는다). 신규 건수 반환.

    `INSERT OR IGNORE` 를 쓰지 않는다 — 그건 UNIQUE 뿐 아니라 CHECK·NOT NULL 위반까지
    조용히 삼켜서, 금액이 0인 거래나 밴드가 없는 행이 소리 없이 사라진다.
    `ON CONFLICT DO NOTHING` 은 중복만 건너뛰고 나머지 위반은 그대로 터진다.
    """
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in cols)
    sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT DO NOTHING")
    now = _now()
    inserted = 0
    for r in rows:
        values = []
        for c in cols:
            if c == "raw_json":
                raw = r.get("raw")
                values.append(json.dumps(raw, ensure_ascii=False) if raw else None)
            elif c == "source_id":
                values.append(r.get("source_id", src_id))
            elif c == "retrieved_at":
                values.append(r.get("retrieved_at", now))
            else:
                values.append(r.get(c))
        inserted += conn.execute(sql, values).rowcount
    return inserted


def insert_trades(conn, rows, *, src_id=None) -> int:
    return _insert_rows(conn, "trade", _TRADE_COLS, rows, src_id)


def insert_jeonse(conn, rows, *, src_id=None) -> int:
    return _insert_rows(conn, "jeonse_contract", _JEONSE_COLS, rows, src_id)


# ── 매칭 ──────────────────────────────────────────────────────────────

def distinct_unmatched_names(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    """아직 단지에 안 붙은 (시군구, 단지명, 법정동, 건축년도) 조합 — 건수 많은 순."""
    return conn.execute(f"""
        SELECT lawd_cd, apt_name, emd_name, build_year, COUNT(*) AS cnt
        FROM {table}
        WHERE complex_id IS NULL
        GROUP BY lawd_cd, apt_name, emd_name, build_year
        ORDER BY cnt DESC
    """).fetchall()


def apply_match(conn: sqlite3.Connection, table: str, *, lawd_cd: str, apt_name: str,
                emd_name: str | None, build_year: int | None,
                complex_id: int | None, confidence: str, reason: str) -> int:
    """같은 (시군구·단지명·법정동·건축년도) 행 전체에 매칭 결과를 적용."""
    return conn.execute(
        f"UPDATE {table} SET complex_id=?, match_confidence=?, match_reason=? "
        f"WHERE complex_id IS NULL AND lawd_cd=? AND apt_name=? "
        f"AND emd_name IS ? AND build_year IS ?",
        (complex_id, confidence, reason, lawd_cd, apt_name, emd_name, build_year),
    ).rowcount


def clear_matches(conn: sqlite3.Connection, table: str) -> int:
    """매칭 규칙을 고친 뒤 전부 다시 붙일 때. 원자료는 건드리지 않는다."""
    return conn.execute(
        f"UPDATE {table} SET complex_id=NULL, match_confidence=NULL, match_reason=NULL"
    ).rowcount


def match_stats(conn: sqlite3.Connection, table: str) -> dict:
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    by_conf = dict(conn.execute(
        f"SELECT COALESCE(match_confidence,'미시도'), COUNT(*) FROM {table} "
        f"GROUP BY 1"
    ).fetchall())
    unmatched = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE complex_id IS NULL").fetchone()[0]
    return {
        "total": total,
        "unmatched": unmatched,
        "unmatched_pct": (unmatched / total * 100) if total else 0.0,
        "by_confidence": by_conf,
    }


# ── 조회 ──────────────────────────────────────────────────────────────

def complexes_over(conn: sqlite3.Connection, min_households: int, *,
                   sido: str | None = None) -> list[sqlite3.Row]:
    """세대수 필터. **apt_households 만** 본다 — 오피스텔 세대수를 더하지 않는다.

    `>= min_households` 이므로 999세대 단지는 1000세대 필터에 들어오지 않는다
    (요구사항 26-1).
    """
    sql = ("SELECT c.* FROM complex c JOIN region r ON r.lawd_cd = c.lawd_cd "
           "WHERE c.apt_households >= ?")
    params: list = [min_households]
    if sido:
        sql += " AND r.sido = ?"
        params.append(sido)
    sql += " ORDER BY c.apt_households DESC"
    return conn.execute(sql, params).fetchall()


def monthly_coverage(conn: sqlite3.Connection, table: str, ymd_col: str) -> list[sqlite3.Row]:
    """시군구 × 거래월별 건수. 0건인 구간을 찾아 수집 공백을 잡는 데 쓴다."""
    return conn.execute(f"""
        SELECT lawd_cd, substr({ymd_col}, 1, 6) AS ym, COUNT(*) AS cnt
        FROM {table} GROUP BY lawd_cd, ym ORDER BY lawd_cd, ym
    """).fetchall()


# ── 가격 스냅샷 (PHASE 2) ─────────────────────────────────────────────

def save_price_snapshot(conn: sqlite3.Connection, *, complex_id: int, area_band: str,
                        snap) -> int | None:
    """대표가격 스냅샷 저장. 표본이 없으면 저장하지 않고 None 을 돌려준다 —
    "가격 없음"과 "가격 0원"은 완전히 다르다."""
    if not snap.usable:
        return None
    q = snap.quartiles
    conn.execute(
        """INSERT INTO price_snapshot
           (complex_id, area_band, as_of_ym, window_months, representative_price, method,
            sample_n, excluded_n, exclusion_reasons_json, relaxed_json, confidence,
            price_p25, price_p50, price_p75, price_min, price_max,
            engine_version, data_grade, calc_trace)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(complex_id, area_band, as_of_ym, window_months, method)
           DO UPDATE SET
             representative_price=excluded.representative_price,
             sample_n=excluded.sample_n, excluded_n=excluded.excluded_n,
             exclusion_reasons_json=excluded.exclusion_reasons_json,
             relaxed_json=excluded.relaxed_json, confidence=excluded.confidence,
             price_p25=excluded.price_p25, price_p50=excluded.price_p50,
             price_p75=excluded.price_p75, price_min=excluded.price_min,
             price_max=excluded.price_max,
             engine_version=excluded.engine_version, data_grade=excluded.data_grade,
             calc_trace=excluded.calc_trace,
             calculated_at=datetime('now','localtime')""",
        (complex_id, area_band, snap.as_of_ym, snap.window_months, snap.value, snap.method,
         snap.sample_n, snap.excluded_n,
         json.dumps(snap.exclusions, ensure_ascii=False),
         json.dumps(snap.relaxed, ensure_ascii=False) if snap.relaxed else None,
         snap.confidence,
         q.get("p25"), q.get("p50"), q.get("p75"), q.get("min"), q.get("max"),
         snap.calc.engine_version, snap.calc.grade, snap.calc.to_json()),
    )
    row = conn.execute(
        "SELECT id FROM price_snapshot WHERE complex_id=? AND area_band=? AND as_of_ym=? "
        "AND window_months=? AND method=?",
        (complex_id, area_band, snap.as_of_ym, snap.window_months, snap.method)).fetchone()
    return row[0] if row else None


def save_jeonse_snapshot(conn: sqlite3.Connection, *, complex_id: int, area_band: str,
                         snap, price_snapshot_id: int | None = None,
                         ratio_calc=None) -> int | None:
    if not snap.usable:
        return None
    q = snap.quartiles
    conn.execute(
        """INSERT INTO jeonse_snapshot
           (complex_id, area_band, as_of_ym, window_months, representative_deposit, method,
            sample_n, excluded_n, exclusion_reasons_json, relaxed_json, confidence,
            deposit_p25, deposit_p50, deposit_p75, deposit_min, deposit_max,
            price_snapshot_id, jeonse_ratio, ratio_calc_trace,
            engine_version, data_grade, calc_trace)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(complex_id, area_band, as_of_ym, window_months, method)
           DO UPDATE SET
             representative_deposit=excluded.representative_deposit,
             sample_n=excluded.sample_n, excluded_n=excluded.excluded_n,
             exclusion_reasons_json=excluded.exclusion_reasons_json,
             relaxed_json=excluded.relaxed_json, confidence=excluded.confidence,
             deposit_p25=excluded.deposit_p25, deposit_p50=excluded.deposit_p50,
             deposit_p75=excluded.deposit_p75, deposit_min=excluded.deposit_min,
             deposit_max=excluded.deposit_max,
             price_snapshot_id=excluded.price_snapshot_id,
             jeonse_ratio=excluded.jeonse_ratio,
             ratio_calc_trace=excluded.ratio_calc_trace,
             engine_version=excluded.engine_version, data_grade=excluded.data_grade,
             calc_trace=excluded.calc_trace,
             calculated_at=datetime('now','localtime')""",
        (complex_id, area_band, snap.as_of_ym, snap.window_months, snap.value, snap.method,
         snap.sample_n, snap.excluded_n,
         json.dumps(snap.exclusions, ensure_ascii=False),
         json.dumps(snap.relaxed, ensure_ascii=False) if snap.relaxed else None,
         snap.confidence,
         q.get("p25"), q.get("p50"), q.get("p75"), q.get("min"), q.get("max"),
         price_snapshot_id,
         ratio_calc.value if ratio_calc else None,
         ratio_calc.to_json() if ratio_calc else None,
         snap.calc.engine_version, snap.calc.grade, snap.calc.to_json()),
    )
    row = conn.execute(
        "SELECT id FROM jeonse_snapshot WHERE complex_id=? AND area_band=? AND as_of_ym=? "
        "AND window_months=? AND method=?",
        (complex_id, area_band, snap.as_of_ym, snap.window_months, snap.method)).fetchone()
    return row[0] if row else None


def trades_for(conn: sqlite3.Connection, complex_id: int, area_band: str) -> list[dict]:
    """대표가격 계산 입력. **엔진은 DB를 모르므로** 여기서 dict 로 꺼내 넘긴다."""
    rows = conn.execute(
        "SELECT deal_amount, deal_ymd, floor, deal_type, cancel_yn "
        "FROM trade WHERE complex_id=? AND area_band=? ORDER BY deal_ymd",
        (complex_id, area_band)).fetchall()
    return [dict(r) for r in rows]


def jeonse_for(conn: sqlite3.Connection, complex_id: int, area_band: str) -> list[dict]:
    rows = conn.execute(
        "SELECT deposit, monthly_rent, contract_ymd, floor, contract_type, "
        "use_renewal_right, 0 AS cancel_yn "
        "FROM jeonse_contract WHERE complex_id=? AND area_band=? ORDER BY contract_ymd",
        (complex_id, area_band)).fetchall()
    return [dict(r) for r in rows]


def matched_complex_bands(conn: sqlite3.Connection, *, min_households: int | None = None,
                          sido: str | None = None) -> list[sqlite3.Row]:
    """스냅샷을 만들 (단지, 면적밴드) 조합. 실거래가 붙어 있는 것만."""
    sql = ("SELECT DISTINCT t.complex_id, t.area_band, c.name, c.lawd_cd "
           "FROM trade t JOIN complex c ON c.id = t.complex_id "
           "JOIN region r ON r.lawd_cd = c.lawd_cd WHERE t.complex_id IS NOT NULL")
    params: list = []
    if min_households is not None:
        sql += " AND c.apt_households >= ?"
        params.append(min_households)
    if sido:
        sql += " AND r.sido = ?"
        params.append(sido)
    sql += " ORDER BY c.name, t.area_band"
    return conn.execute(sql, params).fetchall()


def latest_price_snapshot(conn: sqlite3.Connection, complex_id: int,
                          area_band: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM price_snapshot WHERE complex_id=? AND area_band=? "
        "ORDER BY as_of_ym DESC LIMIT 1", (complex_id, area_band)).fetchone()


def latest_jeonse_snapshot(conn: sqlite3.Connection, complex_id: int,
                           area_band: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jeonse_snapshot WHERE complex_id=? AND area_band=? "
        "ORDER BY as_of_ym DESC LIMIT 1", (complex_id, area_band)).fetchone()


def find_complexes(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    """이름 일부로 단지를 찾는다. CLI 에서 '동아1단지' 같은 입력을 받기 위한 것."""
    from apt_engine.collectors.matcher import normalize
    norm = normalize(query)
    return conn.execute(
        "SELECT c.*, r.sido FROM complex c LEFT JOIN region r ON r.lawd_cd = c.lawd_cd "
        "WHERE c.name LIKE ? OR c.name_norm LIKE ? ORDER BY c.apt_households DESC LIMIT 50",
        (f"%{query}%", f"%{norm}%")).fetchall()


__all__ = [
    "sync_regions", "sync_region_lineage", "source_id", "log_collection",
    "upsert_complexes", "candidates_for", "upsert_unit_types",
    "derive_unit_types_from_trades", "insert_trades", "insert_jeonse",
    "distinct_unmatched_names", "apply_match", "clear_matches", "match_stats",
    "complexes_over", "monthly_coverage", "get_conn",
    "save_price_snapshot", "save_jeonse_snapshot", "trades_for", "jeonse_for",
    "matched_complex_bands", "latest_price_snapshot", "latest_jeonse_snapshot",
    "find_complexes",
]
