-- 005 · 규칙 계층 — 규제 · 토허 · 세법 · 대출 · 부대비용 (PHASE 3)
--
-- 이 영역에는 **공식 API 가 없다.** 토지거래허가구역은 지자체마다 고시가 흩어져 있고,
-- 세법은 국가법령정보센터에 있지만 기계가 읽을 형태가 아니며, LTV/DSR 은 금융위 발표다.
--
-- 그래서 값을 코드에 적지 않고 이 테이블에 넣는다. 요구사항 25와 62-10이 요구하는 것:
--
--   effective_from / effective_to   언제부터 언제까지의 규칙인가
--   source_url / source_name        어디서 온 값인가
--   last_verified                   마지막으로 사람이 확인한 날. **NULL 이면 미검증**
--
-- 엔진은 `as_of` 날짜를 필수 인자로 받아 그 시점에 유효한 규칙만 고른다.
-- 그리고 last_verified 가 NULL 인 규칙으로는 계산을 **거부**한다 —
-- 기본값 세율로 조용히 계산하는 것보다 "확인 불가"가 낫다.

-- ── 규제지역 ──────────────────────────────────────────────────────────
CREATE TABLE regulation_zone (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    lawd_cd        TEXT NOT NULL,
    emd_name       TEXT,                        -- 동 단위 지정이면. NULL = 시군구 전체
    zone_type      TEXT NOT NULL
                   CHECK (zone_type IN ('조정대상지역','투기과열지구','투기지역')),
    effective_from TEXT NOT NULL,               -- YYYY-MM-DD
    effective_to   TEXT,                        -- NULL = 현재까지 유효
    source_name    TEXT,
    source_url     TEXT,
    last_verified  TEXT,                        -- NULL = 미검증. 엔진이 계산을 거부한다
    note           TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX idx_regulation_zone_lookup
    ON regulation_zone (lawd_cd, zone_type, effective_from);

-- ── 토지거래허가구역 ──────────────────────────────────────────────────
-- 요구사항 22·62-11: 토허 여부를 모르면서 전세 활용(갭투자) 가능성을 계산해서는 안 된다.
--
-- 스키마가 강제하는 두 가지:
--   effective_to  NOT NULL — 지정기간이 끝났는데 현재 토허로 표시하는 사고를 막는다.
--                 무기한이면 먼 미래 날짜를 넣고 last_verified 로 관리한다.
--   target_scope  NOT NULL — 내국인 대상과 외국인 대상 토허를 절대 섞지 않는다.
CREATE TABLE land_permit_zone (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    lawd_cd                   TEXT NOT NULL,
    emd_name                  TEXT,             -- 동 단위 지정이면. NULL = 시군구 전체
    designator                TEXT,             -- 지정 주체(국토부/시도지사 등)
    target_scope              TEXT NOT NULL
                              CHECK (target_scope IN ('내국인','외국인','전체')),
    target_use                TEXT,             -- 대상 용도(주거용/전체 등)

    effective_from            TEXT NOT NULL,
    effective_to              TEXT NOT NULL,    -- 반드시 끝을 적는다
    residence_duty_months     INTEGER,          -- 실거주 의무기간(개월)
    -- 실거주 의무가 있으면 전세를 끼고 살 수 없다 = Initial Equity 에서
    -- 전세보증금을 차감하면 안 된다(요구사항 22).
    jeonse_succession_allowed INTEGER NOT NULL DEFAULT 0
                              CHECK (jeonse_succession_allowed IN (0,1)),
    resale_restriction        TEXT,             -- 전매 제한
    source_name               TEXT,
    source_url                TEXT,
    last_verified             TEXT,
    note                      TEXT,
    created_at                TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX idx_permit_zone_lookup
    ON land_permit_zone (lawd_cd, target_scope, effective_from, effective_to);

-- ── 세법 ──────────────────────────────────────────────────────────────
-- 세율을 코드에 적지 않는다. 구간(bracket)과 조건(conditions_json)으로 표현한다.
--
-- conditions_json 예:
--   {"house_count": 1, "regulated": false}
--   {"house_count_gte": 2, "regulated": true}
--   {"exclusive_area_gt": 85}
-- 엔진이 조건을 하나씩 대조해, 조건이 더 많이 맞는 규칙을 우선한다.
CREATE TABLE tax_rule (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_kind              TEXT NOT NULL
                          CHECK (tax_kind IN ('취득세','지방교육세','농어촌특별세',
                                              '재산세','종합부동산세',
                                              '양도소득세','지방소득세')),
    rule_key              TEXT NOT NULL,        -- 사람이 알아볼 식별자
    conditions_json       TEXT NOT NULL DEFAULT '{}',

    bracket_min           INTEGER NOT NULL DEFAULT 0,   -- 원. 과세표준 하한(포함)
    bracket_max           INTEGER,                      -- 원. 상한(미만). NULL = 무한
    rate                  REAL,                         -- 0~1
    progressive_deduction INTEGER NOT NULL DEFAULT 0,   -- 원. 누진공제
    fixed_amount          INTEGER,                      -- 정액세면
    -- 구간 안에서 세율이 선형으로 변하는 경우(취득세 6~9억 구간 같은 형태).
    -- 비어 있으면 단순 rate 를 쓴다.
    rate_formula          TEXT,

    effective_from        TEXT NOT NULL,
    effective_to          TEXT,
    source_name           TEXT,
    source_url            TEXT,
    last_verified         TEXT,
    note                  TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (tax_kind, rule_key, bracket_min, effective_from)
);

CREATE INDEX idx_tax_rule_lookup ON tax_rule (tax_kind, effective_from, effective_to);

-- ── 대출 규제 ─────────────────────────────────────────────────────────
-- 요구사항 23·62-12: LTV 하나로 대출을 계산하지 않는다.
-- LTV 한도와 DSR 한도를 각각 구한 뒤 **더 제한적인 쪽**을 쓴다.
CREATE TABLE loan_rule (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key           TEXT NOT NULL,
    conditions_json    TEXT NOT NULL DEFAULT '{}',   -- {"house_count":0,"zone":"조정대상지역"}
    price_min          INTEGER NOT NULL DEFAULT 0,
    price_max          INTEGER,
    ltv                REAL,                          -- 0~1
    dsr                REAL,                          -- 0~1. 연소득 대비 연간 원리금 상한
    dti                REAL,
    stress_rate_bp     INTEGER NOT NULL DEFAULT 0,    -- 스트레스 DSR 가산금리(bp)
    max_loan_amount    INTEGER,                       -- 절대 상한(있으면)
    residence_required INTEGER NOT NULL DEFAULT 0 CHECK (residence_required IN (0,1)),
    effective_from     TEXT NOT NULL,
    effective_to       TEXT,
    source_name        TEXT,
    source_url         TEXT,
    last_verified      TEXT,
    note               TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX idx_loan_rule_lookup ON loan_rule (effective_from, effective_to);

-- ── 취득 부대비용 ─────────────────────────────────────────────────────
-- 중개보수 요율, 법무비, 인지세 등. 실투자금(요구사항 27)의 재료다.
CREATE TABLE cost_rule (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cost_kind      TEXT NOT NULL
                   CHECK (cost_kind IN ('중개보수','법무비','인지세','국민주택채권','기타')),
    rule_key       TEXT NOT NULL,
    region         TEXT,                        -- 시도. NULL = 전국
    price_min      INTEGER NOT NULL DEFAULT 0,
    price_max      INTEGER,
    rate           REAL,                        -- 0~1
    max_amount     INTEGER,                     -- 요율 상한액
    fixed_amount   INTEGER,
    effective_from TEXT NOT NULL,
    effective_to   TEXT,
    source_name    TEXT,
    source_url     TEXT,
    last_verified  TEXT,
    note           TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX idx_cost_rule_lookup ON cost_rule (cost_kind, effective_from, effective_to);

INSERT INTO data_source (key, name, kind, url, note) VALUES
 ('law_manual', '법령·고시 수기 입력', '공식문서',
  'https://www.law.go.kr',
  '세법·규제지역·토지거래허가구역·대출규제. 공식 API 가 없어 사람이 원문을 확인해 입력한다. last_verified 가 비면 엔진이 계산을 거부한다');

INSERT INTO engine_version (version, note)
VALUES ('0.5.0', 'PHASE 3 — 규제·토허·세법·대출 규칙 테이블 + as_of 기준 조회');
