-- 009 · 금융·세금·거래비용 계층 보강 (PHASE 3.9)
--
-- 요구사항(2026-08-30 지시):
--   * 시행 중인 법령과 발표/예정 정책을 절대 같은 것으로 취급하지 않는다 → status
--   * 계산할 수 없는 항목을 임의로 0원 처리하지 않는다 → verification
--   * 정책값을 코드가 아니라 규칙표에서 관리한다 → rule_type/value 로 LTV·DSR·상한을 분리
--
-- 기존 컬럼은 하나도 지우지 않는다. 이미 입력된 취득세·지방교육세·중개보수 규칙과
-- 그 규칙을 읽는 코드가 그대로 동작해야 한다.

-- ── 정책 생애주기와 데이터 신뢰도 ─────────────────────────────────────
--
--   status        ENACTED   현재 시행 중인 법령·고시. **계산에 쓰는 유일한 상태**
--                 ANNOUNCED 발표됐으나 아직 시행 전 (개정안 통과·시행일 미도래)
--                 PROPOSED  입법예고·정책발표 단계
--                 EXPIRED   시행이 끝난 과거 규칙 (백테스트에는 여전히 필요하다)
--
--   verification  VERIFIED            사람이 원문을 확인함
--                 ESTIMATED           추정치 (실비처럼 사전에 확정 불가한 것)
--                 UNKNOWN             값을 모른다
--                 NEEDS_VERIFICATION  값은 적었으나 원문 확인 전
--
-- 둘은 다른 축이다. 시행 중인 법령(ENACTED)이라도 우리가 확인 안 했으면
-- NEEDS_VERIFICATION 이고, 그 규칙으로 계산하면 결과에 그 사실이 따라붙는다.

-- tax_rule 은 tax_kind 의 CHECK 목록을 넓혀야 해서 테이블을 다시 만든다.
-- SQLite 는 CHECK 를 ALTER 로 못 고친다. 기존 행은 전부 그대로 옮긴다.
--   추가: 취득세감면(생애최초 등) · 부가가치세(중개보수·법무보수에 붙는다) · 인지세
CREATE TABLE tax_rule_new (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_kind              TEXT NOT NULL
                          CHECK (tax_kind IN ('취득세','취득세감면','지방교육세',
                                              '농어촌특별세','재산세','종합부동산세',
                                              '양도소득세','지방소득세',
                                              '부가가치세','인지세')),
    rule_key              TEXT NOT NULL,
    conditions_json       TEXT NOT NULL DEFAULT '{}',
    bracket_min           INTEGER NOT NULL DEFAULT 0,
    bracket_max           INTEGER,
    rate                  REAL,
    progressive_deduction INTEGER NOT NULL DEFAULT 0,
    fixed_amount          INTEGER,
    rate_formula          TEXT,
    effective_from        TEXT NOT NULL,
    effective_to          TEXT,
    source_name           TEXT,
    source_url            TEXT,
    last_verified         TEXT,
    note                  TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    -- 009 에서 추가
    status                TEXT NOT NULL DEFAULT 'ENACTED'
                          CHECK (status IN ('ENACTED','ANNOUNCED','PROPOSED','EXPIRED')),
    verification          TEXT NOT NULL DEFAULT 'NEEDS_VERIFICATION'
                          CHECK (verification IN ('VERIFIED','ESTIMATED','UNKNOWN',
                                                  'NEEDS_VERIFICATION')),
    max_amount            INTEGER,        -- 감면 한도액 등
    base_kind             TEXT,           -- 과세표준이 무엇인가(취득가액/취득세액/감면세액…)
    UNIQUE (tax_kind, rule_key, bracket_min, effective_from)
);

INSERT INTO tax_rule_new (id, tax_kind, rule_key, conditions_json, bracket_min,
    bracket_max, rate, progressive_deduction, fixed_amount, rate_formula,
    effective_from, effective_to, source_name, source_url, last_verified, note,
    created_at)
SELECT id, tax_kind, rule_key, conditions_json, bracket_min, bracket_max, rate,
       progressive_deduction, fixed_amount, rate_formula, effective_from,
       effective_to, source_name, source_url, last_verified, note, created_at
  FROM tax_rule;

DROP TABLE tax_rule;
ALTER TABLE tax_rule_new RENAME TO tax_rule;
CREATE INDEX idx_tax_rule_lookup ON tax_rule (tax_kind, effective_from, effective_to);

ALTER TABLE loan_rule ADD COLUMN status TEXT NOT NULL DEFAULT 'ENACTED';
-- loan_rule 은 CHECK 를 새로 걸 필요가 없어 ALTER 로 넓힌다.
ALTER TABLE loan_rule ADD COLUMN verification TEXT NOT NULL DEFAULT 'NEEDS_VERIFICATION';
ALTER TABLE loan_rule ADD COLUMN rule_type TEXT;         -- LTV / DSR / STRESS_DSR / MORTGAGE_CAP / DTI
ALTER TABLE loan_rule ADD COLUMN value REAL;             -- 그 rule_type 의 정책값
ALTER TABLE loan_rule ADD COLUMN region TEXT;            -- 시도. NULL = 전국
ALTER TABLE loan_rule ADD COLUMN regulated_area INTEGER; -- 1/0/NULL(무관)
ALTER TABLE loan_rule ADD COLUMN home_status TEXT;       -- 무주택 / 1주택 / 다주택
ALTER TABLE loan_rule ADD COLUMN first_home_buyer INTEGER;

-- cost_rule 도 cost_kind 목록을 넓힌다.
--   추가: 등기신청수수료 · 증명서발급 · 국민주택채권(기존) 외 법무 실비 항목들
CREATE TABLE cost_rule_new (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cost_kind      TEXT NOT NULL
                   CHECK (cost_kind IN ('중개보수','법무비','인지세','국민주택채권',
                                        '등기신청수수료','증명서발급','기타')),
    rule_key       TEXT NOT NULL,
    region         TEXT,
    price_min      INTEGER NOT NULL DEFAULT 0,
    price_max      INTEGER,
    rate           REAL,
    max_amount     INTEGER,
    fixed_amount   INTEGER,
    effective_from TEXT NOT NULL,
    effective_to   TEXT,
    source_name    TEXT,
    source_url     TEXT,
    last_verified  TEXT,
    note           TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    -- 009 에서 추가
    status         TEXT NOT NULL DEFAULT 'ENACTED'
                   CHECK (status IN ('ENACTED','ANNOUNCED','PROPOSED','EXPIRED')),
    verification   TEXT NOT NULL DEFAULT 'NEEDS_VERIFICATION'
                   CHECK (verification IN ('VERIFIED','ESTIMATED','UNKNOWN',
                                           'NEEDS_VERIFICATION')),
    vat_applicable INTEGER NOT NULL DEFAULT 0,   -- 부가가치세가 별도로 붙는 보수인가
    conditions_json TEXT
);

INSERT INTO cost_rule_new (id, cost_kind, rule_key, region, price_min, price_max,
    rate, max_amount, fixed_amount, effective_from, effective_to, source_name,
    source_url, last_verified, note, created_at)
SELECT id, cost_kind, rule_key, region, price_min, price_max, rate, max_amount,
       fixed_amount, effective_from, effective_to, source_name, source_url,
       last_verified, note, created_at
  FROM cost_rule;

DROP TABLE cost_rule;
ALTER TABLE cost_rule_new RENAME TO cost_rule;
CREATE INDEX idx_cost_rule_lookup ON cost_rule (cost_kind, effective_from, effective_to);

-- 이미 사람이 확인한 규칙은 VERIFIED 로 옮긴다. last_verified 가 신뢰도의 원천이었다.
UPDATE tax_rule  SET verification = 'VERIFIED'
 WHERE last_verified IS NOT NULL AND trim(last_verified) != '';
UPDATE loan_rule SET verification = 'VERIFIED'
 WHERE last_verified IS NOT NULL AND trim(last_verified) != '';
UPDATE cost_rule SET verification = 'VERIFIED'
 WHERE last_verified IS NOT NULL AND trim(last_verified) != '';

-- 중개보수는 부가가치세가 별도로 붙는다(요구사항 10). 기존 행에 표시해 둔다.
UPDATE cost_rule SET vat_applicable = 1 WHERE cost_kind IN ('중개보수', '법무비');

CREATE INDEX idx_loan_rule_type ON loan_rule (rule_type, effective_from, effective_to);
CREATE INDEX idx_tax_rule_status ON tax_rule (tax_kind, status, effective_from);

-- ── 사용자 프로필 ─────────────────────────────────────────────────────
-- 요구사항 24: 소득·현금·주택수를 코드에 하드코딩하지 않는다.
-- 요구사항 13: "현금 3억" 은 매매가 3억이 아니라 **실투자금 3억**이라는 뜻이다.
CREATE TABLE user_profile (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL UNIQUE,
    available_cash        INTEGER CHECK (available_cash IS NULL OR available_cash >= 0),
    annual_income         INTEGER CHECK (annual_income IS NULL OR annual_income >= 0),
    existing_annual_payment INTEGER NOT NULL DEFAULT 0,
    current_home_count    INTEGER NOT NULL DEFAULT 0,
    first_home_buyer      INTEGER NOT NULL DEFAULT 0,
    buyer_type            TEXT NOT NULL DEFAULT '개인'
                          CHECK (buyer_type IN ('개인','법인')),
    mortgage_term_years   INTEGER NOT NULL DEFAULT 30,
    interest_rate         REAL,
    repayment_type        TEXT NOT NULL DEFAULT '원리금균등'
                          CHECK (repayment_type IN ('원리금균등','원금균등','만기일시')),
    region                TEXT,
    note                  TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

INSERT INTO data_source (key, name, kind, url, note) VALUES
 ('legal_fee_manual', '법무사 보수표 (수기 입력)', '공식문서',
  'https://www.kabl.kr',
  '소유권이전등기 기본보수. 대한법무사협회 보수표를 확인해 구간별로 넣는다. 정액 30만원 같은 임의값을 쓰지 않는다'),
 ('fsc_manual', 'LTV·DSR 금융정책 (수기 입력)', '공식문서',
  'https://www.fsc.go.kr',
  '금융위원회 보도자료·감독규정. 시점별로 크게 바뀌므로 effective_from/to 를 반드시 적는다. 백테스트가 이 값에 의존한다');

INSERT INTO engine_version (version, note)
VALUES ('0.9.0', 'PHASE 3.9 — 취득세 3세목 분리 · 감면/농특세 구분 · LTV/DSR 분리 규칙 · 법무비 · 실투자금 · 사용자 프로필');
