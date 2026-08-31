-- 015 · Walk-forward 백테스트 (지시서 §55·§56·§57·§72·§74)
--
-- 이 마이그레이션이 만드는 것은 "성적표" 가 아니라 **정답지** 다.
--
-- 백테스트에서 가장 위험한 테이블이 정답지다. 미래 수익률이 들어 있으므로,
-- Feature 계산 코드가 실수로 한 번이라도 이걸 조회하면 그 백테스트는 물론
-- 그 뒤의 가중치까지 전부 거짓이 된다.
--
-- 그래서 blind/cutoff.py 에 ANSWER_KEY_TABLES 를 두고, 컷오프 guard 안에서
-- 아래 backtest_outcome / backtest_kpi 를 조회하면 **무조건** LookAheadError 를
-- 던지게 했다. "조심하자" 가 아니라 구조로 막는다.

-- ── 실험 한 번 (§55) ──────────────────────────────────────────────────
CREATE TABLE backtest_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key         TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    engine_version  TEXT NOT NULL,

    -- 데이터가 실제로 존재하는 구간. 이 밖의 시점은 창을 만들 수 없다.
    data_start      TEXT NOT NULL,
    data_end        TEXT NOT NULL,
    step_months     INTEGER NOT NULL CHECK (step_months > 0),
    horizons_json   TEXT NOT NULL,             -- [2,5,10]
    buckets_json    TEXT NOT NULL,             -- 현금 버킷 9종 (§27)
    top_k           INTEGER NOT NULL CHECK (top_k > 0),

    -- 데이터 출처. 합성 데이터로 돈 결과를 실제 성적으로 읽으면 안 된다.
    market_source   TEXT NOT NULL
                    CHECK (market_source IN ('REAL','SYNTHETIC')),

    -- §55 "LOOK-AHEAD LEAKAGE 가 발견되면 해당 backtest 는 무효 처리한다"
    leakage_checked INTEGER NOT NULL DEFAULT 0 CHECK (leakage_checked IN (0,1)),
    leakage_found   INTEGER NOT NULL DEFAULT 0 CHECK (leakage_found IN (0,1)),
    invalid_reason  TEXT,

    status          TEXT NOT NULL DEFAULT 'RUNNING'
                    CHECK (status IN ('RUNNING','COMPLETE','INVALID')),
    note            TEXT,

    -- 누출 검사를 통과하지 않은 실행은 COMPLETE 가 될 수 없다.
    CHECK (status != 'COMPLETE' OR (leakage_checked = 1 AND leakage_found = 0)),
    -- 무효 처리에는 반드시 이유가 붙는다.
    CHECK (status != 'INVALID' OR invalid_reason IS NOT NULL)
);

-- ── 시점 하나 (§55 walk-forward) ──────────────────────────────────────
-- as_of 에서 결정하고, eval_day 에 채점한다. 두 날짜가 한 행에 같이 있는 것은
-- 채점 쪽에서만 의미가 있다 — 결정 쪽 코드는 as_of 만 본다.
CREATE TABLE backtest_window (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES backtest_run(id) ON DELETE CASCADE,
    as_of           TEXT NOT NULL,             -- 결정 시점 (데이터 컷오프)
    horizon_years   INTEGER NOT NULL CHECK (horizon_years > 0),
    eval_day        TEXT NOT NULL,             -- 채점 시점 = as_of + horizon

    -- §72 Train / Validation / Out-of-time 은 **시간으로** 나눈다.
    -- 무작위 분할은 미래를 학습에 섞는 것과 같다.
    split           TEXT NOT NULL
                    CHECK (split IN ('TRAIN','VALIDATION','OOT')),

    -- 아직 채점 시점이 오지 않은 창. 버리지 않고 PENDING 으로 남긴다 —
    -- "왜 창이 이것밖에 없나" 에 답할 수 있어야 한다.
    status          TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','SCORED','SKIPPED')),
    skip_reason     TEXT,

    universe_size   INTEGER,
    scored_n        INTEGER,

    CHECK (eval_day > as_of),
    CHECK (status != 'SKIPPED' OR skip_reason IS NOT NULL),
    UNIQUE (run_id, as_of, horizon_years)
);

CREATE INDEX idx_bt_window_run ON backtest_window (run_id, split, as_of);

-- ── 그 시점에 우리가 고른 것 (§27 버킷별) ─────────────────────────────
CREATE TABLE backtest_pick (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id       INTEGER NOT NULL REFERENCES backtest_window(id) ON DELETE CASCADE,
    cash_bucket     INTEGER NOT NULL CHECK (cash_bucket > 0),
    list_kind       TEXT NOT NULL
                    CHECK (list_kind IN ('absolute','risk_adjusted','asymmetric')),
    rank            INTEGER NOT NULL CHECK (rank > 0),
    complex_id      INTEGER NOT NULL REFERENCES complex(id),
    area_band       TEXT NOT NULL,

    score           REAL NOT NULL,
    confidence      REAL,
    entry_price     INTEGER NOT NULL CHECK (entry_price > 0),
    required_equity INTEGER,
    weights_source  TEXT NOT NULL
                    CHECK (weights_source IN ('HEURISTIC','BACKTESTED')),

    UNIQUE (window_id, cash_bucket, list_kind, rank)
);

CREATE INDEX idx_bt_pick_complex ON backtest_pick (window_id, complex_id);

-- ── 정답지 (§33·§34·§36·§41) ─────────────────────────────────────────
-- 후보 **전체** 에 대해 계산한다. 우리가 고른 것만 채점하면 Regret 도
-- Missed Winner 도 계산할 수 없다 — 놓친 것을 봐야 놓친 걸 안다.
--
-- ⚠ 이 테이블은 미래를 담고 있다. Feature 코드에서 조회 금지(구조로 차단됨).
CREATE TABLE backtest_outcome (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id       INTEGER NOT NULL REFERENCES backtest_window(id) ON DELETE CASCADE,
    complex_id      INTEGER NOT NULL REFERENCES complex(id),
    area_band       TEXT NOT NULL,

    entry_price     INTEGER,
    exit_price      INTEGER,
    forward_return  REAL,                      -- (exit - entry) / entry
    annualized      REAL,

    max_drawdown    REAL,                      -- 보유기간 중 최대 낙폭 (§36)
    trough_ym       TEXT,
    recovery_months INTEGER,                   -- 저점에서 직전 고점 회복까지 (§36)
    recovered       INTEGER CHECK (recovered IN (0,1)),

    rise_start_ym   TEXT,                      -- 실제 상승이 시작된 달 (§40)
    months_late     INTEGER,                   -- as_of 가 그보다 얼마나 늦었나

    -- §41 Winner 4상태. 우리가 골랐는지와 실제로 좋았는지의 조합이다.
    winner_class    TEXT
                    CHECK (winner_class IN ('WINNER_FOUND','MISSED_WINNER',
                                            'FALSE_POSITIVE','CORRECT_REJECT')),
    picked          INTEGER NOT NULL DEFAULT 0 CHECK (picked IN (0,1)),
    ex_post_rank    INTEGER,                   -- 사후 실제 성과 순위 (§34)
    sample_n        INTEGER,

    -- 값을 못 냈으면 왜 못 냈는지가 반드시 있어야 한다(§67).
    -- "없음" 이 아니라 "확인 불가 + 사유" 로 남는다.
    unknown_reason  TEXT,
    CHECK (forward_return IS NOT NULL OR unknown_reason IS NOT NULL),

    UNIQUE (window_id, complex_id, area_band)
);

CREATE INDEX idx_bt_outcome_window ON backtest_outcome (window_id, forward_return);

-- ── KPI (§57) ────────────────────────────────────────────────────────
-- window_id 가 NULL 이면 run 전체 집계다.
CREATE TABLE backtest_kpi (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES backtest_run(id) ON DELETE CASCADE,
    window_id       INTEGER REFERENCES backtest_window(id) ON DELETE CASCADE,
    split           TEXT CHECK (split IN ('TRAIN','VALIDATION','OOT')),
    cash_bucket     INTEGER,
    list_kind       TEXT,
    horizon_years   INTEGER,

    kpi_key         TEXT NOT NULL,
    value           REAL,
    unit            TEXT,
    sample_n        INTEGER NOT NULL DEFAULT 0,
    note            TEXT,

    -- 표본 없이 나온 숫자를 성적으로 읽지 않게 한다.
    CHECK (value IS NULL OR sample_n > 0),
    -- 값이 없으면 사유가 있어야 한다(§67).
    CHECK (value IS NOT NULL OR note IS NOT NULL)
);

CREATE INDEX idx_bt_kpi_lookup ON backtest_kpi (run_id, kpi_key, split);

-- ── Feature usefulness (§74) ─────────────────────────────────────────
-- "데이터 → Feature → Backtest → **Feature usefulness** → Weight → Ranking"
-- 이 테이블이 그 네 번째 칸이다. 여기가 비어 있으면 가중치는 HEURISTIC 을
-- 벗어날 수 없다.
CREATE TABLE feature_usefulness (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES backtest_run(id) ON DELETE CASCADE,
    split           TEXT NOT NULL CHECK (split IN ('TRAIN','VALIDATION','OOT')),
    regime          TEXT,                      -- NULL = 전 국면 합산
    feature_key     TEXT NOT NULL,

    rank_ic         REAL,                      -- 점수 순위 vs 실제 성과 순위 상관
    hit_rate        REAL,                      -- 상위 절반이 시장 중앙값을 이긴 비율
    ablation_delta  REAL,                      -- 빼면 KPI 가 얼마나 나빠지나 (§71)
    sample_n        INTEGER NOT NULL DEFAULT 0,

    verdict         TEXT NOT NULL
                    CHECK (verdict IN ('USEFUL','NEUTRAL','HARMFUL','INSUFFICIENT')),
    note            TEXT,

    -- 표본이 없으면 판정은 INSUFFICIENT 밖에 될 수 없다.
    CHECK (sample_n > 0 OR verdict = 'INSUFFICIENT'),
    UNIQUE (run_id, split, regime, feature_key)
);

-- ── 학습된 가중치 (§74) ───────────────────────────────────────────────
-- weights.BACKTESTED 가 읽는 자리. 검증(VALIDATION)에서 확인되지 않은 가중치는
-- 여기 들어올 수 없다.
CREATE TABLE weight_fit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES backtest_run(id) ON DELETE CASCADE,
    fitted_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    regime          TEXT,                      -- NULL = 국면 무관 기본값
    model_key       TEXT NOT NULL,
    weight          REAL NOT NULL CHECK (weight >= 0),

    train_ic        REAL,
    validation_ic   REAL,
    sample_n        INTEGER NOT NULL CHECK (sample_n > 0),
    market_source   TEXT NOT NULL
                    CHECK (market_source IN ('REAL','SYNTHETIC')),
    note            TEXT,
    UNIQUE (run_id, regime, model_key)
);

INSERT INTO engine_version (version, note)
VALUES ('0.14.0', 'PHASE 8 — walk-forward 백테스트 하네스 · 정답지 격리 · KPI 14종 · Feature usefulness');
