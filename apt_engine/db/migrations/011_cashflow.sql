-- 011 · 현금흐름 · Peak Equity · 세후 IRR (PHASE 7)
--
-- PHASE 3.9 까지가 "한 시점의 값"(실투자금·대출·취득비용)이었다면, 여기서 그걸
-- **시간축에 올린다.** 매수 시점의 실투자금만 보면 두 가지를 놓친다.
--
--   1) 보유하는 동안 돈이 더 들어간다. 역마진이면 실제로 묶이는 돈은 처음보다 크다
--      → Initial Equity 와 **Peak Equity** 를 따로 본다
--   2) 같은 수익도 2년 만에 나면 다르고 10년 걸리면 다르다
--      → 총 수익률이 아니라 **세후 IRR** 로 본다
--
-- 세율은 여기에도 없다. 재산세·종부세·양도세·장기보유특별공제 전부 tax_rule 에서
-- 온다. 규칙이 없으면 그 항목만 '확인 불가' 이고, 합계는 "얼마 이상/이하" 로만 말한다.

-- tax_kind 목록을 넓힌다. SQLite 는 CHECK 를 ALTER 로 못 고쳐 테이블을 다시 만든다.
--   장기보유특별공제  양도차익에서 보유기간에 따라 빼주는 공제. 세율이 아니라 공제율이다
--   공정시장가액비율  공시가격 → 과세표준 환산 비율 (재산세·종부세)
CREATE TABLE tax_rule_new (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_kind              TEXT NOT NULL
                          CHECK (tax_kind IN ('취득세','취득세감면','지방교육세',
                                              '농어촌특별세','재산세','종합부동산세',
                                              '양도소득세','지방소득세',
                                              '부가가치세','인지세',
                                              '장기보유특별공제','공정시장가액비율')),
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
    status                TEXT NOT NULL DEFAULT 'ENACTED'
                          CHECK (status IN ('ENACTED','ANNOUNCED','PROPOSED','EXPIRED')),
    verification          TEXT NOT NULL DEFAULT 'NEEDS_VERIFICATION'
                          CHECK (verification IN ('VERIFIED','ESTIMATED','UNKNOWN',
                                                  'NEEDS_VERIFICATION')),
    max_amount            INTEGER,
    base_kind             TEXT,
    rate_decimals         INTEGER,
    UNIQUE (tax_kind, rule_key, bracket_min, effective_from)
);

INSERT INTO tax_rule_new SELECT * FROM tax_rule;
DROP TABLE tax_rule;
ALTER TABLE tax_rule_new RENAME TO tax_rule;
CREATE INDEX idx_tax_rule_lookup ON tax_rule (tax_kind, effective_from, effective_to);
CREATE INDEX idx_tax_rule_status ON tax_rule (tax_kind, status, effective_from);

-- ── 현금흐름 결과 ─────────────────────────────────────────────────────
-- 가정에 기반한 값이라 data_grade 는 SCENARIO 하나뿐이다. 확정 수익률로 저장할
-- 방법이 스키마에 없다.
CREATE TABLE cashflow_snapshot (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id        INTEGER REFERENCES complex(id) ON DELETE CASCADE,
    area_band         TEXT,
    as_of             TEXT NOT NULL,
    scenario_key      TEXT NOT NULL,          -- Bear / Base / Bull / 사용자 정의
    holding_years     INTEGER NOT NULL CHECK (holding_years > 0),
    occupancy         TEXT NOT NULL CHECK (occupancy IN ('실거주','임대','전세승계')),

    purchase_price    INTEGER NOT NULL CHECK (purchase_price > 0),
    sale_price        INTEGER,
    initial_equity    INTEGER,
    peak_equity       INTEGER,
    net_profit        INTEGER,                -- 세후 순이익
    irr               REAL,                   -- 세후 IRR (연율)
    profit_per_100m   INTEGER,                -- 1억당 이익
    unknown_json      TEXT,                   -- 계산하지 못한 항목

    engine_version    TEXT NOT NULL,
    calc_trace        TEXT NOT NULL,
    data_grade        TEXT NOT NULL DEFAULT 'SCENARIO'
                      CHECK (data_grade = 'SCENARIO'),
    calculated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, area_band, as_of, scenario_key, holding_years, occupancy)
);

CREATE INDEX idx_cashflow_lookup ON cashflow_snapshot (complex_id, as_of, scenario_key);

INSERT INTO engine_version (version, note)
VALUES ('0.10.0', 'PHASE 7 — 보유세·양도세 · 상환 스케줄 · 연도별 현금흐름 · Initial/Peak Equity · 세후 IRR · Bear/Base/Bull + Stress Test');
