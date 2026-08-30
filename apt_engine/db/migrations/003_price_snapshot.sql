-- 003 · 가격 스냅샷 계층 (PHASE 2)
--
-- 원자료(trade / jeonse_contract)에서 계산해 나온 **파생** 테이블이다.
-- 그래서 원자료에는 없는 세 가지를 반드시 갖는다:
--   engine_version  어느 버전 산식으로 나온 값인가 (산식이 바뀌면 재계산 대상 식별)
--   calc_trace      입력값 → 계산식 → 결과값 → 근거 (요구사항 25)
--   confidence      표본이 몇 건이었나 (요구사항 2의 HIGH/MEDIUM/LOW)
--
-- 지우고 다시 만들어도 원자료는 그대로다. 산식을 고치면 언제든 재계산한다.
--
-- 월별로 쌓으면 요구사항 4(Historical Price Ratio)가 저절로 따라온다 —
-- 같은 실거래를 다른 창으로 다시 집계하기만 하면 되고, 추가 수집이 필요 없다.

CREATE TABLE price_snapshot (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id             INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band              TEXT NOT NULL,
    as_of_ym               TEXT NOT NULL,               -- 기준월 YYYYMM
    window_months          INTEGER NOT NULL,            -- 집계창 길이(요구사항 2: 3~6개월)

    representative_price   INTEGER NOT NULL CHECK (representative_price > 0),  -- 원
    method                 TEXT NOT NULL CHECK (method IN ('median','trimmed_mean')),

    sample_n               INTEGER NOT NULL CHECK (sample_n > 0),
    excluded_n             INTEGER NOT NULL DEFAULT 0,
    exclusion_reasons_json TEXT,                        -- {"DIRECT_DEAL":2,"OUTLIER_HIGH":1}
    relaxed_json           TEXT,                        -- 표본부족으로 되살린 soft 제외
    confidence             TEXT NOT NULL CHECK (confidence IN ('HIGH','MEDIUM','LOW')),

    price_p25              INTEGER,
    price_p50              INTEGER,
    price_p75              INTEGER,
    price_min              INTEGER,
    price_max              INTEGER,

    engine_version         TEXT NOT NULL,
    data_grade             TEXT NOT NULL CHECK (data_grade IN ('CONFIRMED','ESTIMATED','SCENARIO')),
    calc_trace             TEXT NOT NULL,
    calculated_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),

    UNIQUE (complex_id, area_band, as_of_ym, window_months, method)
);

CREATE INDEX idx_price_snapshot_lookup ON price_snapshot (complex_id, area_band, as_of_ym);
CREATE INDEX idx_price_snapshot_ym     ON price_snapshot (as_of_ym, area_band);

-- 전세 스냅샷. 구조는 같고 전세가율이 붙는다.
-- price_snapshot_id 로 짝을 명시해, 다른 시점이나 다른 면적의 매매가와
-- 섞여 계산되는 일이 없게 한다.
CREATE TABLE jeonse_snapshot (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id             INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band              TEXT NOT NULL,
    as_of_ym               TEXT NOT NULL,
    window_months          INTEGER NOT NULL,

    representative_deposit INTEGER NOT NULL CHECK (representative_deposit > 0),  -- 원
    method                 TEXT NOT NULL CHECK (method IN ('median','trimmed_mean')),

    sample_n               INTEGER NOT NULL CHECK (sample_n > 0),
    excluded_n             INTEGER NOT NULL DEFAULT 0,
    exclusion_reasons_json TEXT,
    relaxed_json           TEXT,
    confidence             TEXT NOT NULL CHECK (confidence IN ('HIGH','MEDIUM','LOW')),

    deposit_p25            INTEGER,
    deposit_p50            INTEGER,
    deposit_p75            INTEGER,
    deposit_min            INTEGER,
    deposit_max            INTEGER,

    -- 전세가율 = 대표 전세보증금 / 대표 매매가 (0~1)
    price_snapshot_id      INTEGER REFERENCES price_snapshot(id) ON DELETE SET NULL,
    jeonse_ratio           REAL CHECK (jeonse_ratio IS NULL OR jeonse_ratio > 0),
    ratio_calc_trace       TEXT,

    engine_version         TEXT NOT NULL,
    data_grade             TEXT NOT NULL CHECK (data_grade IN ('CONFIRMED','ESTIMATED','SCENARIO')),
    calc_trace             TEXT NOT NULL,
    calculated_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),

    UNIQUE (complex_id, area_band, as_of_ym, window_months, method)
);

CREATE INDEX idx_jeonse_snapshot_lookup ON jeonse_snapshot (complex_id, area_band, as_of_ym);
CREATE INDEX idx_jeonse_snapshot_ratio  ON jeonse_snapshot (as_of_ym, area_band, jeonse_ratio);

INSERT INTO engine_version (version, note)
VALUES ('0.3.0', 'PHASE 2 — 대표가격(정상거래 필터 + 중앙값) · 대표 전세가 · 전세가율');
