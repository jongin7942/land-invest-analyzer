-- 002 · 아파트 원자료 계층 (PHASE 1)
--
-- 설계 원칙은 docs_dev/00-현황분석-및-고도화계획.md 의 F 절.
-- 여기서 중요한 건 컬럼 목록보다 **무엇을 스키마가 금지하는가**다.
-- 요구사항 26의 금지사항 중 스키마로 막을 수 있는 것은 전부 여기서 막는다.

-- ── 시군구 ────────────────────────────────────────────────────────────
CREATE TABLE region (
    lawd_cd      TEXT PRIMARY KEY,          -- 법정동코드 앞 5자리 (실거래가 API 파라미터)
    sido         TEXT NOT NULL,             -- 서울 / 경기 / 인천
    name         TEXT NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1,-- 0 = 개편으로 폐지된 코드(과거 조회용)
    until_ym     TEXT                       -- 폐지 코드가 유효했던 마지막 거래월
);

-- ── 단지 마스터 ───────────────────────────────────────────────────────
-- 주의: **총세대수 컬럼을 만들지 않는다.**
-- 요구사항 26-1(999세대를 1000세대 필터에 넣지 말 것) / 26-2(아파트와 오피스텔
-- 세대수를 합치지 말 것)는 "합계를 만들지 않으면" 애초에 어길 수가 없다.
-- 세대수 필터는 항상 apt_households 만 본다.
CREATE TABLE complex (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    kapt_code            TEXT UNIQUE,        -- K-apt 단지코드. 이게 단지의 정체성이다
    name                 TEXT NOT NULL,
    name_norm            TEXT NOT NULL,      -- 매칭용 정규화 이름 (collectors/matcher.py)
    lawd_cd              TEXT NOT NULL REFERENCES region(lawd_cd),
    emd_name             TEXT,               -- 법정동명
    jibun                TEXT,
    road_addr            TEXT,
    lat                  REAL,
    lon                  REAL,
    pnu                  TEXT,

    apt_households       INTEGER CHECK (apt_households IS NULL OR apt_households >= 0),
    officetel_households INTEGER CHECK (officetel_households IS NULL OR officetel_households >= 0),
    building_count       INTEGER,
    approval_date        TEXT,               -- 사용승인일 YYYYMMDD
    approval_year        INTEGER,            -- 매칭 보조키 + 재건축 1차 스크리닝
    builder              TEXT,

    land_area_m2         REAL,               -- 대지면적. 평균 대지지분 = 이것 / apt_households
                                             -- K-apt 기본정보에는 없다. PHASE 6에서 건축물대장/V-World 로 채운다
    building_area_m2     REAL,               -- 건축면적(1층 바닥)
    gross_floor_area_m2  REAL,               -- 연면적. K-apt kaptTarea
    current_far          REAL,               -- 현재 용적률(%). 법정 상한도 정비계획 용적률도 아니다
    current_bcr          REAL,
    zoning               TEXT,
    heat_type            TEXT,
    parking_count        INTEGER,

    source_id            INTEGER REFERENCES data_source(id),
    retrieved_at         TEXT,
    confidence           TEXT CHECK (confidence IS NULL OR confidence IN ('HIGH','MEDIUM','LOW')),
    raw_json             TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX idx_complex_lawd      ON complex (lawd_cd);
CREATE INDEX idx_complex_name_norm ON complex (lawd_cd, name_norm);
CREATE INDEX idx_complex_size      ON complex (apt_households);

-- ── 단지 내 블록/차수 ─────────────────────────────────────────────────
CREATE TABLE complex_block (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id    INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    block_name    TEXT NOT NULL,             -- '1단지', '2차' 등
    households    INTEGER,
    approval_date TEXT,
    UNIQUE (complex_id, block_name)
);

-- ── 단지 묶음 ─────────────────────────────────────────────────────────
-- 요구사항 26-3: 여러 블록으로 나뉜 단지를 **근거 없이** 하나로 합치지 말 것.
-- merge_reason 이 NOT NULL 이라 근거 없이는 행을 만들 수 없고, created_by 로
-- "누가 묶었는지"가 남는다. 수집기가 자동으로 묶는 경로는 만들지 않는다.
CREATE TABLE complex_group (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    merge_reason TEXT NOT NULL CHECK (length(trim(merge_reason)) > 0),
    created_by   TEXT NOT NULL CHECK (length(trim(created_by)) > 0),
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE complex_group_member (
    group_id   INTEGER NOT NULL REFERENCES complex_group(id) ON DELETE CASCADE,
    complex_id INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, complex_id)
);

-- ── 면적타입 ──────────────────────────────────────────────────────────
-- 가격은 언제나 이 단위(또는 area_band)로 본다. 단지 평균가 컬럼은 없다.
CREATE TABLE unit_type (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id        INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    exclusive_area_m2 REAL NOT NULL CHECK (exclusive_area_m2 > 0),
    area_band         TEXT NOT NULL,
    supply_area_m2    REAL,
    households        INTEGER,
    land_share_m2     REAL,                  -- 평형별 대지지분(있으면). 없으면 단지 평균으로 근사
    UNIQUE (complex_id, exclusive_area_m2)
);

CREATE INDEX idx_unit_type_band ON unit_type (complex_id, area_band);

-- ── 매매 실거래 ───────────────────────────────────────────────────────
-- complex_id 가 NULL 이어도 저장한다. 매칭 실패 건을 버리면 미매칭 리포트를
-- 만들 수 없고, 매칭 규칙을 고친 뒤 다시 붙일 수도 없다.
CREATE TABLE trade (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id        INTEGER REFERENCES complex(id),
    match_confidence  TEXT CHECK (match_confidence IS NULL
                                  OR match_confidence IN ('EXACT','STRONG','WEAK','NONE')),
    match_reason      TEXT,

    lawd_cd           TEXT NOT NULL,
    emd_name          TEXT,
    jibun             TEXT,
    apt_name          TEXT NOT NULL,          -- API 원문 단지명(정규화 전)
    apt_dong          TEXT,

    exclusive_area_m2 REAL NOT NULL CHECK (exclusive_area_m2 > 0),
    area_band         TEXT NOT NULL,
    deal_amount       INTEGER NOT NULL CHECK (deal_amount > 0),  -- 원. 만원 아님
    deal_ymd          TEXT NOT NULL,
    floor             INTEGER,
    build_year        INTEGER,

    -- 요구사항 26-6: 직거래를 정상 중개거래와 동일하게 처리하지 말 것.
    deal_type         TEXT,                   -- 중개거래 / 직거래
    agent_region      TEXT,
    -- 요구사항 26-5 준비: 취소거래를 대표가격에 넣지 말 것.
    cancel_yn         INTEGER NOT NULL DEFAULT 0 CHECK (cancel_yn IN (0,1)),
    cancel_ymd        TEXT,
    registration_ymd  TEXT,
    seller_type       TEXT,
    buyer_type        TEXT,

    raw_json          TEXT,
    source_id         INTEGER REFERENCES data_source(id),
    retrieved_at      TEXT,
    UNIQUE (lawd_cd, emd_name, jibun, apt_name, exclusive_area_m2,
            deal_amount, deal_ymd, floor)
);

CREATE INDEX idx_trade_complex   ON trade (complex_id, area_band, deal_ymd);
CREATE INDEX idx_trade_region    ON trade (lawd_cd, deal_ymd);
CREATE INDEX idx_trade_ymd       ON trade (deal_ymd);
CREATE INDEX idx_trade_unmatched ON trade (lawd_cd, apt_name) WHERE complex_id IS NULL;

-- ── 전월세 실거래 ─────────────────────────────────────────────────────
-- 갱신계약·갱신요구권 사용 건은 시세가 아니라 기존 계약의 연장이다.
-- 대표 전세가에서 빼려면 구분해서 저장해야 한다.
CREATE TABLE jeonse_contract (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id        INTEGER REFERENCES complex(id),
    match_confidence  TEXT CHECK (match_confidence IS NULL
                                  OR match_confidence IN ('EXACT','STRONG','WEAK','NONE')),
    match_reason      TEXT,

    lawd_cd           TEXT NOT NULL,
    emd_name          TEXT,
    jibun             TEXT,
    apt_name          TEXT NOT NULL,

    exclusive_area_m2 REAL NOT NULL CHECK (exclusive_area_m2 > 0),
    area_band         TEXT NOT NULL,
    deposit           INTEGER NOT NULL CHECK (deposit >= 0),   -- 원
    monthly_rent      INTEGER NOT NULL DEFAULT 0 CHECK (monthly_rent >= 0), -- 원. 0 = 순수 전세
    contract_ymd      TEXT NOT NULL,
    floor             INTEGER,
    build_year        INTEGER,

    contract_type     TEXT,                   -- 신규 / 갱신
    use_renewal_right INTEGER CHECK (use_renewal_right IS NULL OR use_renewal_right IN (0,1)),
    prev_deposit      INTEGER,
    prev_monthly_rent INTEGER,
    contract_term     TEXT,

    raw_json          TEXT,
    source_id         INTEGER REFERENCES data_source(id),
    retrieved_at      TEXT,
    UNIQUE (lawd_cd, emd_name, jibun, apt_name, exclusive_area_m2,
            deposit, monthly_rent, contract_ymd, floor)
);

CREATE INDEX idx_jeonse_complex ON jeonse_contract (complex_id, area_band, contract_ymd);
CREATE INDEX idx_jeonse_region  ON jeonse_contract (lawd_cd, contract_ymd);

-- ── 출처 등록 ─────────────────────────────────────────────────────────
INSERT INTO data_source (key, name, kind, url, note) VALUES
 ('molit_apt_trade', '국토교통부 아파트 매매 실거래가 상세', 'API',
  'https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev',
  '거래유형(중개/직거래)·해제여부·등기일자를 제공한다. 요구사항 26-5/26-6의 원천'),
 ('molit_apt_rent', '국토교통부 아파트 전월세 실거래가', 'API',
  'https://apis.data.go.kr/1613000/RTMSDataSvcAptRent',
  '계약구분(신규/갱신)·갱신요구권 사용 여부를 제공한다'),
 ('kapt_complex', '한국부동산원 공동주택 단지 기본정보(K-apt)', 'API',
  'https://apis.data.go.kr/1613000/AptBasisInfoServiceV3',
  '세대수·동수·사용승인일·대지면적·용적률. 1,000세대 필터의 원천'),
 ('manual', '수기 입력', '수기', NULL,
  '토허·규제지역·세법·정비사업 등 공식 API가 없는 데이터. PHASE 3부터 사용');

INSERT INTO engine_version (version, note)
VALUES ('0.2.0', 'PHASE 1 — 단지/면적타입/매매/전월세 원자료 + 단지 매칭');
