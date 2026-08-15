"""SQLite 스키마 및 저장 헬퍼."""
import json
import sqlite3
from contextlib import contextmanager

import config

# land_trade: 국토부 토지 매매 실거래가 원자료
# raw_json 에 API 원본 필드를 통째로 보관해, 나중에 필드 매핑이 바뀌어도 재파싱 가능하게 한다.
SCHEMA = """
CREATE TABLE IF NOT EXISTS land_trade (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sgg_cd        TEXT,      -- 시군구코드(LAWD_CD 5자리)
    sgg_nm        TEXT,      -- 시군구명(로컬 매핑)
    umd_nm        TEXT,      -- 법정동
    jibun         TEXT,      -- 지번
    jimok         TEXT,      -- 지목
    zoning        TEXT,      -- 용도지역
    deal_area     REAL,      -- 거래면적(㎡)
    deal_amount   INTEGER,   -- 거래금액(만원)
    deal_ymd      TEXT,      -- 거래일 YYYYMMDD
    share_type    TEXT,      -- 지분/구분
    raw_json      TEXT,      -- API 원본 필드 전체(JSON)
    collected_at  TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (sgg_cd, umd_nm, jibun, deal_area, deal_amount, deal_ymd)
);

CREATE INDEX IF NOT EXISTS idx_land_trade_sgg ON land_trade (sgg_cd);
CREATE INDEX IF NOT EXISTS idx_land_trade_ymd ON land_trade (deal_ymd);

-- auction_candidate: 온비드 공매 물건 + V-World 판정 + 저평가 스코어 (Phase 4)
CREATE TABLE IF NOT EXISTS auction_candidate (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mgmt_no       TEXT,      -- 물건관리번호
    plnm_no       TEXT,      -- 공고번호
    name          TEXT,      -- 물건명
    address       TEXT,      -- 소재지(온비드)
    use_name      TEXT,      -- 용도/종류(온비드)
    appraisal     INTEGER,   -- 감정가(원)
    min_bid       INTEGER,   -- 최저입찰가(원)
    disposal      TEXT,      -- 처분방식
    bid_begin     TEXT,
    bid_end       TEXT,
    status        TEXT,      -- 입찰상태(입찰준비중/입찰진행중 등)
    round         INTEGER,   -- 입찰회차(유찰 누적 횟수 참고)
    -- V-World 필지 판정
    pnu           TEXT,
    zoning        TEXT,      -- 용도지역
    jimok         TEXT,
    area_m2       REAL,
    road_side     TEXT,      -- 도로접면 원본
    road_grade    TEXT,      -- 건축양호/확인필요/건축애로/맹지
    -- 시세 판정
    ppp_min_bid   REAL,      -- 최저입찰가 평당(만원)
    baseline_med  REAL,      -- 기준선 중앙값 평당(만원)
    pct_below     REAL,      -- 기준선 대비 저평가율(%)
    baseline_lvl  TEXT,
    score         REAL,      -- 종합 점수(개발용 관점)
    tags          TEXT,      -- 태그(계획관리/맹지 등)
    -- 토지보상용 관점
    land_price_m2 REAL,      -- 개별공시지가(원/㎡)
    land_price_yr TEXT,
    land_group    TEXT,      -- 대지군/농지군/미분류
    comp_score    REAL,      -- 토지보상용 관점 점수
    comp_discount REAL,      -- 공시지가 대비 저평가율(%)
    -- 개발호재 뉴스
    news_count    INTEGER,   -- 매칭된 개발호재 뉴스 건수
    news_json     TEXT,      -- 뉴스 목록(제목/링크/날짜) JSON
    raw_json      TEXT,
    evaluated_at  TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (mgmt_no, plnm_no)
);
CREATE INDEX IF NOT EXISTS idx_auction_score ON auction_candidate (score);
"""
# idx_auction_comp_score 는 여기서 만들지 않는다 — comp_score 는 나중에 추가된 컬럼이라
# 기존 DB엔 executescript(SCHEMA) 시점에 아직 없다(마이그레이션 전). _migrate()에서 생성한다.


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn):
    """CREATE TABLE IF NOT EXISTS 는 기존 테이블에 새 컬럼을 추가하지 않으므로,
    스키마 진화 시 누락 컬럼을 여기서 보강한다(존재하면 무시)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(auction_candidate)")}
    new_cols = (
        ("status", "TEXT"), ("round", "INTEGER"),
        ("land_price_m2", "REAL"), ("land_price_yr", "TEXT"), ("land_group", "TEXT"),
        ("comp_score", "REAL"), ("comp_discount", "REAL"),
        ("news_count", "INTEGER"), ("news_json", "TEXT"),
    )
    for col, decl in new_cols:
        if col not in cols:
            conn.execute(f"ALTER TABLE auction_candidate ADD COLUMN {col} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_auction_comp_score ON auction_candidate (comp_score)")


def upsert_land_trades(rows: list[dict]) -> int:
    """rows: land_trade 컬럼에 맞춘 dict 목록. 중복(UNIQUE)은 무시. 신규 삽입 건수 반환."""
    if not rows:
        return 0
    inserted = 0
    with get_conn() as conn:
        for r in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO land_trade
                    (sgg_cd, sgg_nm, umd_nm, jibun, jimok, zoning,
                     deal_area, deal_amount, deal_ymd, share_type, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r.get("sgg_cd"),
                    r.get("sgg_nm"),
                    r.get("umd_nm"),
                    r.get("jibun"),
                    r.get("jimok"),
                    r.get("zoning"),
                    r.get("deal_area"),
                    r.get("deal_amount"),
                    r.get("deal_ymd"),
                    r.get("share_type"),
                    json.dumps(r.get("raw", {}), ensure_ascii=False),
                ),
            )
            inserted += cur.rowcount
    return inserted


_AC_COLS = [
    "mgmt_no", "plnm_no", "name", "address", "use_name", "appraisal", "min_bid",
    "disposal", "bid_begin", "bid_end", "status", "round", "pnu", "zoning", "jimok",
    "area_m2", "road_side", "road_grade", "ppp_min_bid", "baseline_med", "pct_below",
    "baseline_lvl", "score", "tags",
    "land_price_m2", "land_price_yr", "land_group", "comp_score", "comp_discount",
    "news_count", "news_json", "raw_json",
]


def upsert_auction_candidates(rows: list[dict]) -> int:
    """rows: auction_candidate 컬럼에 맞춘 dict 목록. UNIQUE(mgmt_no, plnm_no) 충돌 시
    갱신(UPSERT) — 같은 물건을 재판정하면 새 계산 결과(토지보상 점수·뉴스 등)로 덮어쓴다."""
    if not rows:
        return 0
    placeholders = ",".join("?" for _ in _AC_COLS)
    update_set = ",".join(f"{c}=excluded.{c}" for c in _AC_COLS if c not in ("mgmt_no", "plnm_no"))
    sql = f"""
        INSERT INTO auction_candidate ({",".join(_AC_COLS)})
        VALUES ({placeholders})
        ON CONFLICT(mgmt_no, plnm_no) DO UPDATE SET {update_set}
    """
    n = 0
    with get_conn() as conn:
        for r in rows:
            vals = [r.get(c) if c != "raw_json" else json.dumps(r.get("raw", {}), ensure_ascii=False)
                    for c in _AC_COLS]
            conn.execute(sql, vals)
            n += 1
    return n


def top_candidates(limit: int = 20):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT * FROM auction_candidate
            WHERE score IS NOT NULL
            ORDER BY score DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()


def stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM land_trade").fetchone()[0]
        by_sgg = conn.execute(
            "SELECT sgg_nm, COUNT(*) c FROM land_trade GROUP BY sgg_nm ORDER BY c DESC"
        ).fetchall()
        ymd_range = conn.execute(
            "SELECT MIN(deal_ymd), MAX(deal_ymd) FROM land_trade"
        ).fetchone()
    return {
        "total": total,
        "by_sgg": [(row[0], row[1]) for row in by_sgg],
        "ymd_range": (ymd_range[0], ymd_range[1]),
    }
