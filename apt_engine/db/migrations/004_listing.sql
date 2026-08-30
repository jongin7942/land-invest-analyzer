-- 004 · 호가(매물) 계층 (PHASE 2.5)
--
-- 실거래는 "과거에 얼마에 팔렸나", 호가는 "지금 얼마를 부르나"다. 둘은 다른 것이고
-- 절대 섞어서 계산하지 않는다(요구사항 62-3). 그래서 테이블부터 분리한다.
--
-- 특정 서비스에 종속되지 않게 provider 를 컬럼으로 두고, 공급자가 바뀌어도
-- 이 스키마는 그대로 쓴다. 지금은 ManualListingProvider(CSV/JSON) 하나뿐이다.

-- ── 매물 ──────────────────────────────────────────────────────────────
CREATE TABLE listing (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    provider           TEXT NOT NULL,          -- manual / (향후 제휴 데이터 공급자)
    listing_key        TEXT NOT NULL,          -- provider 내 고유키. 없으면 지문으로 생성
    external_id        TEXT,                   -- 공급자가 준 원본 ID(있으면)

    complex_id         INTEGER REFERENCES complex(id),
    match_confidence   TEXT CHECK (match_confidence IS NULL
                                   OR match_confidence IN ('EXACT','STRONG','WEAK','NONE')),
    match_reason       TEXT,
    lawd_cd            TEXT,
    apt_name           TEXT NOT NULL,

    trade_type         TEXT NOT NULL CHECK (trade_type IN ('매매','전세','월세')),
    price              INTEGER NOT NULL CHECK (price > 0),   -- 원. 매매가 또는 보증금
    monthly_rent       INTEGER NOT NULL DEFAULT 0 CHECK (monthly_rent >= 0),

    exclusive_area_m2  REAL NOT NULL CHECK (exclusive_area_m2 > 0),
    area_band          TEXT NOT NULL,
    dong               TEXT,
    floor              INTEGER,
    top_floor          INTEGER,
    floor_group        TEXT CHECK (floor_group IS NULL
                                   OR floor_group IN ('저층','중층','고층')),
    direction          TEXT,
    features           TEXT,                   -- 매물특징 원문
    move_in_date       TEXT,                   -- 입주가능일
    tenant_status      TEXT,                   -- 세입자 승계 여부 등
    agency             TEXT,

    -- 특수조건(급매/수리필요/세입자승계/입주불가 등). 최저호가가 특수매물이면
    -- '최저호가'와 '정상매물 최저호가'를 반드시 따로 표시해야 한다(요구사항 4).
    special_flags_json TEXT,
    is_special         INTEGER NOT NULL DEFAULT 0 CHECK (is_special IN (0,1)),

    dup_group          TEXT,                   -- 중복 추정 그룹 키(요구사항 5)

    source_url         TEXT,
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL,
    is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),

    raw_json           TEXT,
    source_id          INTEGER REFERENCES data_source(id),
    retrieved_at       TEXT,
    UNIQUE (provider, listing_key)
);

CREATE INDEX idx_listing_lookup ON listing (complex_id, area_band, trade_type, is_active);
CREATE INDEX idx_listing_active ON listing (is_active, last_seen_at);
CREATE INDEX idx_listing_name   ON listing (lawd_cd, apt_name) WHERE complex_id IS NULL;

-- ── 일별 매물 스냅샷 ──────────────────────────────────────────────────
-- 요구사항 9. 매일 현재 매물을 그대로 찍어 두면 7/30/90일 변화를 계산할 수 있다.
-- 사라진 매물이 거래된 것인지 철회된 것인지는 확정할 수 없으므로,
-- 이 테이블은 "있었다/없다"만 기록하고 해석은 하지 않는다.
CREATE TABLE listing_snapshot (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date      TEXT NOT NULL,          -- YYYY-MM-DD
    provider           TEXT NOT NULL,
    listing_key        TEXT NOT NULL,
    complex_id         INTEGER REFERENCES complex(id),
    area_band          TEXT NOT NULL,
    trade_type         TEXT NOT NULL,
    price              INTEGER NOT NULL,
    monthly_rent       INTEGER NOT NULL DEFAULT 0,
    dong               TEXT,
    floor              INTEGER,
    floor_group        TEXT,
    is_special         INTEGER NOT NULL DEFAULT 0,
    features           TEXT,
    UNIQUE (snapshot_date, provider, listing_key)
);

CREATE INDEX idx_listing_snapshot_lookup
    ON listing_snapshot (complex_id, area_band, trade_type, snapshot_date);

-- ── 시장 압력 지표 ────────────────────────────────────────────────────
-- 요구사항 10. LLM 이 느낌으로 점수를 만들지 않는다. 기초 데이터(매물 증감·호가 변화·
-- 가격인하 건수·실거래 방향)를 먼저 계산하고, 그 위에서 가중합한다.
CREATE TABLE market_pressure (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id         INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band          TEXT NOT NULL,
    as_of_date         TEXT NOT NULL,
    window_days        INTEGER NOT NULL,
    score              REAL NOT NULL CHECK (score >= 0 AND score <= 100),
    direction          TEXT NOT NULL CHECK (direction IN ('매도자우위','중립','매수자우위')),
    components_json    TEXT NOT NULL,          -- 각 구성요소의 원값과 기여도
    engine_version     TEXT NOT NULL,
    data_grade         TEXT NOT NULL CHECK (data_grade IN ('CONFIRMED','ESTIMATED','SCENARIO')),
    calc_trace         TEXT NOT NULL,
    calculated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, area_band, as_of_date, window_days)
);

-- ── 현장 확인값 ───────────────────────────────────────────────────────
-- 요구사항 46. "실제로 6.05억이면 살 수 있다더라"는 호가가 아니다.
-- Listing Asking Price 와 Negotiated Price 를 절대 같은 컬럼에 넣지 않는다.
-- kind 로 성격을 구분하고, source(누가 말했나)를 필수로 둔다.
CREATE TABLE field_note (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id         INTEGER REFERENCES complex(id),
    area_band          TEXT,
    noted_on           TEXT NOT NULL,          -- YYYY-MM-DD
    kind               TEXT NOT NULL
                       CHECK (kind IN ('협상가','임장관찰','중개사확인','기타')),
    listing_key        TEXT,                   -- 특정 매물에 대한 것이면
    price              INTEGER CHECK (price IS NULL OR price > 0),
    note               TEXT NOT NULL CHECK (length(trim(note)) > 0),
    source             TEXT NOT NULL CHECK (length(trim(source)) > 0),  -- 누가 말한 것인가
    created_at         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX idx_field_note_lookup ON field_note (complex_id, area_band, noted_on);

INSERT INTO data_source (key, name, kind, url, note) VALUES
 ('manual_listing', '수기 입력 매물(CSV/JSON)', '수기', NULL,
  '공식 API 나 제휴 데이터가 연결되기 전까지의 호가 입력 경로. 공급자가 생기면 provider 만 바꿔 같은 스키마에 넣는다'),
 ('field_note', '현장·중개사 확인값', '수기', NULL,
  '호가(asking)가 아니라 협상 가능가·임장 관찰값. 절대 호가와 같은 컬럼에 넣지 않는다');

INSERT INTO engine_version (version, note)
VALUES ('0.4.0', 'PHASE 2.5 — 호가 계층(분포·중복추정·특수매물·괴리율·일별 스냅샷·시장압력)');
