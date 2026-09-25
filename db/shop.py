"""상가(개원 입지) 모듈 전용 테이블과 저장 헬퍼.

기존 토지 모듈(land_trade / auction_candidate)과 같은 SQLite 파일을 쓰되,
테이블은 완전히 분리한다. schema.init_db() 가 이 모듈의 init() 도 함께 호출한다.

테이블
  region            네이버 법정동(cortarNo 10자리) 트리. 수도권 시도→시군구→동.
  shop_listing      네이버 상가 매물 스냅샷(매물번호 UNIQUE). 처음/마지막 관측 시각으로
                    잔존일수·소멸 여부를 추적한다.
  shop_price_hist   같은 매물의 보증금/월세/매매가가 바뀔 때마다 한 줄.
  clinic_poi        심평원 병의원·약국 목록(좌표 포함). 경쟁·보완 시설 밀도 계산용.
  poi_cache         반경 조회(소상공인 상권 API 등) 결과 캐시.
"""
from __future__ import annotations

import json

from db.schema import get_conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS region (
    cortar_no    TEXT PRIMARY KEY,   -- 법정동코드 10자리
    name         TEXT,
    parent_no    TEXT,
    level        INTEGER,            -- 1=시도 2=시군구 3=읍면동
    sido         TEXT,
    sgg          TEXT,
    umd          TEXT,
    center_lat   REAL,
    center_lon   REAL,
    updated_at   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS shop_listing (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    article_no     TEXT UNIQUE,      -- 네이버 매물번호
    source         TEXT,             -- naver_mobile / naver_new / fixture
    cortar_no      TEXT,
    sido           TEXT,
    sgg            TEXT,
    umd            TEXT,
    name           TEXT,             -- 매물명
    building_name  TEXT,
    re_type        TEXT,             -- 상가/사무실/건물 등 (네이버 표기)
    trade_type     TEXT,             -- 월세/매매/전세 (네이버 표기)
    deposit        INTEGER,          -- 보증금(만원). 매매면 NULL
    rent           INTEGER,          -- 월세(만원)
    sale_price     INTEGER,          -- 매매가(만원)
    area_contract  REAL,             -- 계약면적(㎡)
    area_exclusive REAL,             -- 전용면적(㎡)
    floor          INTEGER,          -- 해당층(지하는 음수)
    floor_total    INTEGER,
    floor_band     TEXT,             -- 지하/1층/2층/3층이상/미상
    direction      TEXT,
    premium        TEXT,             -- 권리금: 있음/없음/미상
    premium_amount INTEGER,          -- 권리금 금액(만원, 파악된 경우)
    vacancy_hint   INTEGER,          -- 공실/즉시입주 키워드 여부(0/1)
    medical_flag   TEXT,             -- 병원양도/전병원자리/메디컬빌딩/약국인접 등 콤마구분
    feature_desc   TEXT,             -- 매물 특징 설명 원문
    tags           TEXT,             -- 네이버 태그(콤마)
    lat            REAL,
    lon            REAL,
    realtor        TEXT,
    confirm_ymd    TEXT,             -- 네이버 확인매물 일자 YYYYMMDD
    conv_rent      REAL,             -- 환산월세(만원) = 월세 + 보증금×전환율/12
    rent_per_py    REAL,             -- 전용평당 환산월세(만원/평)
    status         TEXT DEFAULT 'active',  -- active / gone
    first_seen     TEXT DEFAULT (datetime('now','localtime')),
    last_seen      TEXT DEFAULT (datetime('now','localtime')),
    raw_json       TEXT
);
CREATE INDEX IF NOT EXISTS idx_shop_listing_umd ON shop_listing (sgg, umd);
CREATE INDEX IF NOT EXISTS idx_shop_listing_status ON shop_listing (status);

CREATE TABLE IF NOT EXISTS shop_price_hist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_no  TEXT,
    seen_at     TEXT DEFAULT (datetime('now','localtime')),
    deposit     INTEGER,
    rent        INTEGER,
    sale_price  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_shop_price_hist_no ON shop_price_hist (article_no);

CREATE TABLE IF NOT EXISTS clinic_poi (
    ykiho       TEXT PRIMARY KEY,    -- 심평원 요양기관 암호화 코드
    name        TEXT,
    cl_cd       TEXT,                -- 종별코드(31 의원, 51 치과의원, 92 한의원, 81 약국 ...)
    cl_name     TEXT,
    sido_cd     TEXT,
    sggu_cd     TEXT,
    sggu_name   TEXT,
    emdong_name TEXT,
    addr        TEXT,
    lat         REAL,
    lon         REAL,
    open_ymd    TEXT,
    dgsbjt      TEXT,                -- 진료과목(콤마, 조회했을 때만)
    updated_at  TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_clinic_poi_sggu ON clinic_poi (sggu_cd);

CREATE TABLE IF NOT EXISTS poi_cache (
    cache_key   TEXT PRIMARY KEY,
    payload     TEXT,
    fetched_at  TEXT DEFAULT (datetime('now','localtime'))
);
"""


def init():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------- region ----

def upsert_regions(rows: list[dict]) -> int:
    n = 0
    with get_conn() as conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO region (cortar_no, name, parent_no, level, sido, sgg, umd,
                                    center_lat, center_lon, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?, datetime('now','localtime'))
                ON CONFLICT(cortar_no) DO UPDATE SET
                    name=excluded.name, parent_no=excluded.parent_no, level=excluded.level,
                    sido=excluded.sido, sgg=excluded.sgg, umd=excluded.umd,
                    center_lat=excluded.center_lat, center_lon=excluded.center_lon,
                    updated_at=excluded.updated_at
                """,
                (r["cortar_no"], r.get("name"), r.get("parent_no"), r.get("level"),
                 r.get("sido"), r.get("sgg"), r.get("umd"),
                 r.get("center_lat"), r.get("center_lon")),
            )
            n += 1
    return n


def regions(level: int | None = None, sido: str | None = None, sgg: str | None = None,
            parent_no: str | None = None) -> list[dict]:
    sql = "SELECT * FROM region WHERE 1=1"
    params: list = []
    if level is not None:
        sql += " AND level = ?"; params.append(level)
    if sido:
        sql += " AND sido LIKE ?"; params.append(f"%{sido}%")
    if sgg:
        sql += " AND sgg LIKE ?"; params.append(f"%{sgg}%")
    if parent_no:
        sql += " AND parent_no = ?"; params.append(parent_no)
    sql += " ORDER BY cortar_no"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------- shop_listing ----

_LISTING_COLS = [
    "article_no", "source", "cortar_no", "sido", "sgg", "umd", "name", "building_name",
    "re_type", "trade_type", "deposit", "rent", "sale_price", "area_contract",
    "area_exclusive", "floor", "floor_total", "floor_band", "direction", "premium",
    "premium_amount", "vacancy_hint", "medical_flag", "feature_desc", "tags", "lat", "lon",
    "realtor", "confirm_ymd", "conv_rent", "rent_per_py", "raw_json",
]


def upsert_listings(rows: list[dict]) -> dict:
    """매물 스냅샷 반영. 반환: {"new": 신규, "updated": 갱신, "price_changed": 가격변동}.
    가격이 바뀐 매물은 shop_price_hist 에 이력을 남긴다. 처음 보는 매물도 첫 이력을 남긴다."""
    stats = {"new": 0, "updated": 0, "price_changed": 0}
    if not rows:
        return stats
    placeholders = ",".join("?" for _ in _LISTING_COLS)
    update_set = ",".join(f"{c}=excluded.{c}" for c in _LISTING_COLS if c != "article_no")
    sql = f"""
        INSERT INTO shop_listing ({",".join(_LISTING_COLS)})
        VALUES ({placeholders})
        ON CONFLICT(article_no) DO UPDATE SET {update_set},
            status='active', last_seen=datetime('now','localtime')
    """
    with get_conn() as conn:
        for r in rows:
            prev = conn.execute(
                "SELECT deposit, rent, sale_price FROM shop_listing WHERE article_no = ?",
                (r["article_no"],),
            ).fetchone()
            vals = [
                json.dumps(r.get("raw", {}), ensure_ascii=False) if c == "raw_json" else r.get(c)
                for c in _LISTING_COLS
            ]
            conn.execute(sql, vals)
            changed = prev is None or (
                prev["deposit"] != r.get("deposit") or prev["rent"] != r.get("rent")
                or prev["sale_price"] != r.get("sale_price")
            )
            if prev is None:
                stats["new"] += 1
            else:
                stats["updated"] += 1
                if changed:
                    stats["price_changed"] += 1
            if changed:
                conn.execute(
                    "INSERT INTO shop_price_hist (article_no, deposit, rent, sale_price) VALUES (?,?,?,?)",
                    (r["article_no"], r.get("deposit"), r.get("rent"), r.get("sale_price")),
                )
    return stats


def mark_gone(cortar_no: str, seen_article_nos: set[str]) -> int:
    """이번 수집에서 안 보인 같은 동의 활성 매물을 gone 처리(거래완료/내려감 추정)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT article_no FROM shop_listing WHERE cortar_no = ? AND status = 'active'",
            (cortar_no,),
        ).fetchall()
        gone = [r["article_no"] for r in rows if r["article_no"] not in seen_article_nos]
        for no in gone:
            conn.execute("UPDATE shop_listing SET status='gone' WHERE article_no = ?", (no,))
    return len(gone)


def listings(sgg: str | None = None, umd: str | None = None, trade_type: str | None = None,
             active_only: bool = True, medical_only: bool = False, limit: int | None = None) -> list[dict]:
    sql = "SELECT * FROM shop_listing WHERE 1=1"
    params: list = []
    if active_only:
        sql += " AND status = 'active'"
    if sgg:
        sql += " AND sgg LIKE ?"; params.append(f"%{sgg}%")
    if umd:
        sql += " AND umd LIKE ?"; params.append(f"%{umd}%")
    if trade_type:
        sql += " AND trade_type = ?"; params.append(trade_type)
    if medical_only:
        sql += " AND medical_flag IS NOT NULL AND medical_flag != ''"
    sql += " ORDER BY sgg, umd, floor_band, rent_per_py"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def price_history(article_no: str) -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM shop_price_hist WHERE article_no = ? ORDER BY seen_at", (article_no,)
        ).fetchall()]


def listing_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM shop_listing").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM shop_listing WHERE status='active'").fetchone()[0]
        by_sgg = conn.execute(
            "SELECT sido, sgg, COUNT(*) c FROM shop_listing WHERE status='active' "
            "GROUP BY sido, sgg ORDER BY c DESC"
        ).fetchall()
        medical = conn.execute(
            "SELECT COUNT(*) FROM shop_listing WHERE status='active' AND medical_flag != '' "
            "AND medical_flag IS NOT NULL"
        ).fetchone()[0]
        premium = conn.execute(
            "SELECT premium, COUNT(*) FROM shop_listing WHERE status='active' GROUP BY premium"
        ).fetchall()
        regions_n = conn.execute("SELECT COUNT(*) FROM region").fetchone()[0]
        clinics_n = conn.execute("SELECT COUNT(*) FROM clinic_poi").fetchone()[0]
    return {
        "total": total, "active": active, "medical": medical,
        "by_sgg": [(r[0], r[1], r[2]) for r in by_sgg],
        "premium": {r[0]: r[1] for r in premium},
        "regions": regions_n, "clinics": clinics_n,
    }


# ------------------------------------------------------------ clinic_poi ----

def upsert_clinics(rows: list[dict]) -> int:
    n = 0
    with get_conn() as conn:
        for r in rows:
            if not r.get("ykiho"):
                continue
            conn.execute(
                """
                INSERT INTO clinic_poi (ykiho, name, cl_cd, cl_name, sido_cd, sggu_cd, sggu_name,
                                        emdong_name, addr, lat, lon, open_ymd, dgsbjt, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now','localtime'))
                ON CONFLICT(ykiho) DO UPDATE SET
                    name=excluded.name, cl_cd=excluded.cl_cd, cl_name=excluded.cl_name,
                    sido_cd=excluded.sido_cd, sggu_cd=excluded.sggu_cd, sggu_name=excluded.sggu_name,
                    emdong_name=excluded.emdong_name, addr=excluded.addr, lat=excluded.lat,
                    lon=excluded.lon, open_ymd=excluded.open_ymd,
                    dgsbjt=COALESCE(excluded.dgsbjt, clinic_poi.dgsbjt),
                    updated_at=excluded.updated_at
                """,
                (r["ykiho"], r.get("name"), r.get("cl_cd"), r.get("cl_name"), r.get("sido_cd"),
                 r.get("sggu_cd"), r.get("sggu_name"), r.get("emdong_name"), r.get("addr"),
                 r.get("lat"), r.get("lon"), r.get("open_ymd"), r.get("dgsbjt")),
            )
            n += 1
    return n


def clinics(sggu_name: str | None = None, emdong_name: str | None = None,
            cl_cds: tuple[str, ...] | None = None) -> list[dict]:
    sql = "SELECT * FROM clinic_poi WHERE 1=1"
    params: list = []
    if sggu_name:
        sql += " AND sggu_name LIKE ?"; params.append(f"%{sggu_name}%")
    if emdong_name:
        sql += " AND emdong_name LIKE ?"; params.append(f"%{emdong_name}%")
    if cl_cds:
        sql += f" AND cl_cd IN ({','.join('?' for _ in cl_cds)})"; params.extend(cl_cds)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def all_clinics_with_coords() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM clinic_poi WHERE lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchall()]


# ------------------------------------------------------------- poi_cache ----

def cache_get(key: str, max_age_days: int = 30):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload FROM poi_cache WHERE cache_key = ? "
            "AND fetched_at >= datetime('now','localtime', ?)",
            (key, f"-{int(max_age_days)} days"),
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def cache_put(key: str, payload) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO poi_cache (cache_key, payload, fetched_at) "
            "VALUES (?, ?, datetime('now','localtime')) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at",
            (key, json.dumps(payload, ensure_ascii=False)),
        )
