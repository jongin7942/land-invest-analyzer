-- 016 · DELTA UPGRADE — 후보 객체 확장 · 4 State · Leader 망 · Stage
--       (신규 지시서 §1·§4·§11·§12·§13·§27·§38·§43·§44·§45)
--
-- 이 마이그레이션은 기존 테이블을 **지우지 않는다.** 기존 19개 Feature 와 7그룹은
-- 그대로 돌고, 그 위에 새 층을 얹는다. 삭제 대신 강등이다(§44 DIAGNOSTIC).

-- ── §1 Layout / Type 축 ───────────────────────────────────────────────
-- "같은 전용면적이라도 타입/동/구조에 따라 정상가격 차이가 지속적으로 발생하면
--  별도 Type 으로 분리한다."
--
-- 핵심은 **지속적으로** 다. 한 달 우연히 벌어진 차이로 타입을 쪼개면 표본만
-- 잘게 부서지고 대표가격 신뢰도가 떨어진다. 그래서 분리하려면 근거가 필요하고,
-- 근거 없는 행은 INSERT 자체가 실패한다.
CREATE TABLE complex_type (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id      INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band       TEXT NOT NULL,
    type_key        TEXT NOT NULL,          -- '84A' · '84B' · '타워' 등
    label           TEXT NOT NULL,

    -- 왜 나눴는가. 셋 다 있어야 분리를 인정한다.
    observed_months INTEGER NOT NULL CHECK (observed_months >= 6),
    median_gap_pct  REAL NOT NULL,          -- 같은 면적 평균 대비 지속 격차
    sample_n        INTEGER NOT NULL CHECK (sample_n >= 10),

    evidence_json   TEXT NOT NULL CHECK (length(trim(evidence_json)) > 2),
    verification    TEXT NOT NULL DEFAULT 'ESTIMATED'
                    CHECK (verification IN ('VERIFIED','ESTIMATED','UNKNOWN',
                                            'NEEDS_VERIFICATION')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, area_band, type_key)
);

-- ── §4·§44 Feature 등록부 ─────────────────────────────────────────────
-- 모든 Feature 가 **어느 State 에 속하고 어느 티어인가**를 한 곳에서 정한다.
--
-- 이 표가 §45(GATE/ALPHA/RISK 중복 가산 금지)를 강제하는 자리다.
-- 한 Feature 는 role 을 하나만 갖는다. ALPHA 이면서 RISK 일 수 없다.
CREATE TABLE feature_registry (
    feature_key     TEXT PRIMARY KEY,
    state           TEXT NOT NULL
                    CHECK (state IN ('CHEAPNESS','MOVEMENT','SUSTAINABILITY',
                                     'STRETCH','GATE','CONFIDENCE','CONTEXT')),
    role            TEXT NOT NULL CHECK (role IN ('GATE','ALPHA','RISK',
                                                  'CONFIDENCE','CONTEXT')),
    tier            TEXT NOT NULL DEFAULT 'RESEARCH'
                    CHECK (tier IN ('CORE','DIAGNOSTIC','RESEARCH')),
    higher_is_better INTEGER NOT NULL CHECK (higher_is_better IN (0,1)),
    legacy_group    TEXT,                   -- 기존 7그룹 중 어디서 왔나
    note            TEXT NOT NULL,
    -- CORE 로 올리려면 백테스트 생존 근거가 있어야 한다(§44).
    survived_folds  INTEGER NOT NULL DEFAULT 0,
    promoted_run    TEXT,
    CHECK (tier != 'CORE' OR (survived_folds >= 2 AND promoted_run IS NOT NULL))
);

-- ── §11 Leader 망 ─────────────────────────────────────────────────────
-- "가까운 아파트를 무조건 Leader 로 지정하지 않는다. 실제 Buyer Overlap 기준."
CREATE TABLE leader_link (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    follower_id     INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    leader_id       INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band       TEXT NOT NULL,          -- 59 와 84 의 Leader 망은 분리한다
    leader_kind     TEXT NOT NULL
                    CHECK (leader_kind IN ('LOCAL','PRICE','FLOW',
                                           'CAPITAL_COHORT','METRO')),
    as_of           TEXT NOT NULL,          -- 이 관계를 알 수 있었던 시점

    buyer_overlap   REAL CHECK (buyer_overlap IS NULL
                                OR (buyer_overlap >= 0 AND buyer_overlap <= 1)),
    overlap_basis   TEXT,                   -- 무엇으로 겹침을 봤나
    evidence_json   TEXT NOT NULL CHECK (length(trim(evidence_json)) > 2),
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (follower_id != leader_id),
    UNIQUE (follower_id, leader_id, area_band, leader_kind, as_of)
);

CREATE INDEX idx_leader_link_follower
    ON leader_link (follower_id, area_band, as_of);

-- ── §12·§13 전달 실패 · 회복가능 할인 ─────────────────────────────────
-- "Leader 가 올랐는데 Follower 가 싸다는 이유만으로 추천하지 않는다."
CREATE TABLE transmission_state (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    follower_id           INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band             TEXT NOT NULL,
    as_of                 TEXT NOT NULL,

    leader_rise_12m       REAL,             -- Relevant Leader 가 얼마나 올랐나
    follower_rise_12m     REAL,
    months_no_response    INTEGER,          -- Leader 상승 후 무반응 개월
    buyer_overlap         REAL,

    observed_discount     REAL,             -- Leader 대비 관측 할인
    structural_discount   REAL,             -- 구조적으로 설명되는 부분
    recoverable_discount  REAL,             -- 닫힐 것으로 보는 부분
    recoverable_ratio     REAL CHECK (recoverable_ratio IS NULL
                                      OR (recoverable_ratio >= 0
                                          AND recoverable_ratio <= 1)),
    transmission_failure  REAL,             -- 높을수록 구조적 할인

    -- §14 Why Not Yet — 구조적 이유를 못 찾았으면 그 사실을 남긴다.
    why_not_yet_json      TEXT,
    unknown_reason        TEXT,
    CHECK (recoverable_ratio IS NOT NULL OR unknown_reason IS NOT NULL),
    UNIQUE (follower_id, area_band, as_of)
);

-- ── §17·§38 Stage ────────────────────────────────────────────────────
-- Stage 와 Investment Score 를 혼동하지 않는다. 좋은 아파트라도 EXHAUSTED 면
-- 신규매수 순위는 낮을 수 있다.
CREATE TABLE stage_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id      INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band       TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    stage           TEXT NOT NULL
                    CHECK (stage IN ('DORMANT','PRE_BREAKOUT','EMERGING',
                                     'CONFIRMED','MATURE','EXHAUSTED',
                                     'VALUE_TRAP','CHASE','UNKNOWN')),
    quadrant        TEXT CHECK (quadrant IN ('TARGET','VALUE_TRAP_CANDIDATE',
                                             'CHASE','OVERPRICED_DEAD',NULL)),
    quiet_compounder INTEGER NOT NULL DEFAULT 0
                    CHECK (quiet_compounder IN (0,1)),
    reasons_json    TEXT NOT NULL CHECK (length(trim(reasons_json)) > 2),
    -- UNKNOWN 은 반드시 왜 모르는지가 있어야 한다(§67).
    unknown_reason  TEXT,
    CHECK (stage != 'UNKNOWN' OR unknown_reason IS NOT NULL),
    UNIQUE (complex_id, area_band, as_of)
);

CREATE INDEX idx_stage_state_lookup ON stage_state (as_of, stage);

-- ── §27 Winner / Loser Control Pair ──────────────────────────────────
-- "Winner 만 넣지 않는다." 비슷한 가격대에서 이후 성과가 갈린 쌍을 모은다.
-- 이 표의 단지명은 **Feature 로 쓰지 않는다.** 모델 회귀 검사 전용이다.
CREATE TABLE control_pair (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key        TEXT NOT NULL UNIQUE,
    as_of           TEXT NOT NULL,           -- 두 후보를 비교한 시점
    winner_id       INTEGER REFERENCES complex(id),
    loser_id        INTEGER REFERENCES complex(id),
    winner_label    TEXT NOT NULL,           -- 단지가 아직 매칭 안 됐을 때를 위해
    loser_label     TEXT NOT NULL,
    area_band       TEXT NOT NULL,

    entry_price_gap REAL,                    -- 당시 가격대가 비슷했는가
    outcome_gap     REAL,                    -- 이후 성과 차이
    hypothesis      TEXT NOT NULL,           -- 왜 갈렸다고 보는가
    purpose         TEXT NOT NULL DEFAULT 'REGRESSION'
                    CHECK (purpose = 'REGRESSION'),   -- 다른 용도 금지
    note            TEXT
);

-- ── §43 Universe Coverage ────────────────────────────────────────────
-- 다 못 봤으면 "수도권 전체 TOP10" 이라고 쓰지 않는다.
CREATE TABLE universe_coverage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of           TEXT NOT NULL,
    area_band       TEXT NOT NULL,
    scanned_n       INTEGER NOT NULL,
    known_n         INTEGER NOT NULL,        -- 모수(단지 마스터 기준)

    txn_value_coverage  REAL,
    txn_count_coverage  REAL,
    household_coverage  REAL,
    region_coverage     REAL,
    cohort_coverage     REAL,

    verdict         TEXT NOT NULL
                    CHECK (verdict IN ('FULL_UNIVERSE','PARTIAL_VERIFIED_UNIVERSE')),
    note            TEXT,
    UNIQUE (as_of, area_band)
);

-- ── §1·§36·§38 랭킹 결과 확장 ─────────────────────────────────────────
-- EntryPrice 를 차원으로, Alpha/Risk/Confidence 를 따로.
ALTER TABLE ranking_entry ADD COLUMN entry_price INTEGER;
ALTER TABLE ranking_entry ADD COLUMN type_key TEXT;
ALTER TABLE ranking_entry ADD COLUMN stage TEXT;
ALTER TABLE ranking_entry ADD COLUMN alpha REAL;
ALTER TABLE ranking_entry ADD COLUMN risk REAL;
ALTER TABLE ranking_entry ADD COLUMN strong_buy_price INTEGER;
ALTER TABLE ranking_entry ADD COLUMN fair_buy_price INTEGER;
ALTER TABLE ranking_entry ADD COLUMN do_not_buy_price INTEGER;

-- §37 두 랭킹을 분리한다. 기존 3종은 유지하고 두 종류를 더한다.
DROP INDEX IF EXISTS idx_ranking_run_lookup;
CREATE TABLE ranking_run_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key         TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    executed_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    cash            INTEGER NOT NULL CHECK (cash > 0),
    horizon_years   INTEGER NOT NULL CHECK (horizon_years > 0),
    profile         TEXT NOT NULL,
    list_kind       TEXT NOT NULL
                    CHECK (list_kind IN ('absolute','risk_adjusted','asymmetric',
                                         'executable','pre_breakout')),
    universe_size   INTEGER NOT NULL,
    feasible_size   INTEGER NOT NULL,
    engine_version  TEXT NOT NULL,
    weights_json    TEXT NOT NULL,
    weights_source  TEXT NOT NULL
                    CHECK (weights_source IN ('HEURISTIC','BACKTESTED')),
    coverage_verdict TEXT
                    CHECK (coverage_verdict IS NULL
                           OR coverage_verdict IN ('FULL_UNIVERSE',
                                                   'PARTIAL_VERIFIED_UNIVERSE')),
    market_temperature TEXT
                    CHECK (market_temperature IS NULL
                           OR market_temperature IN ('OPPORTUNITY_RICH',
                                                     'SELECTIVE_BUY',
                                                     'PRICE_CAUTION',
                                                     'CASH_DOMINANT')),
    cash_rank       INTEGER,                 -- §3 CASH 가 몇 위였나
    note            TEXT,
    UNIQUE (run_key, as_of, cash, horizon_years, profile, list_kind)
);

INSERT INTO ranking_run_new (id, run_key, as_of, executed_at, cash, horizon_years,
                             profile, list_kind, universe_size, feasible_size,
                             engine_version, weights_json, weights_source, note)
SELECT id, run_key, as_of, executed_at, cash, horizon_years, profile, list_kind,
       universe_size, feasible_size, engine_version, weights_json, weights_source,
       note FROM ranking_run;

DROP TABLE ranking_run;
ALTER TABLE ranking_run_new RENAME TO ranking_run;
CREATE INDEX idx_ranking_run_lookup ON ranking_run (as_of, cash, horizon_years);

-- ── §3 CASH 후보 · §2 미사용 현금 ─────────────────────────────────────
ALTER TABLE user_profile ADD COLUMN cash_hurdle_rate REAL;      -- 세후 무위험 수익률
ALTER TABLE user_profile ADD COLUMN initial_repair_cost INTEGER; -- §2 초기 수리비

INSERT INTO engine_version (version, note)
VALUES ('0.15.0', 'DELTA UPGRADE — 후보객체(Type·EntryPrice) · Feature 등록부(4 State·티어) · Leader 망 · 전달실패 · Stage · Coverage');
