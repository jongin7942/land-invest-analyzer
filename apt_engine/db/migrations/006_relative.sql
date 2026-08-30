-- 006 · 상대가치 계층 — 가격사다리 · 벤치마크 · 가격비율 (PHASE 4)
--
-- 요구사항 3: "무작위로 비교하지 말 것."
-- 그래서 benchmark_relation.selection_reason_json 을 NOT NULL 로 둔다.
-- 왜 이 단지를 비교대상으로 골랐는지 적지 못하면 행을 만들 수 없다.
--
-- 요구사항 4: Current Ratio / Historical Normal Ratio 를 절대 섞지 않는다.
-- 지금 비율은 price_ratio_history 의 최신 행이고, 과거 정상 비율은 ratio_norm 이다.
-- 테이블이 다르니 코드에서 헷갈릴 여지가 없다.

-- ── 가격사다리 축 ─────────────────────────────────────────────────────
-- 데이터가 아니라 **도메인 지식**이다. 공공 API 로 받을 수 있는 게 아니라
-- 사람이 "이 지역들은 이 순서로 가격이 이어진다"고 판단해 적는 것이다.
-- 그래서 rationale(왜 이 순서인가)과 curated_by(누가 정했나)를 필수로 둔다.
CREATE TABLE ladder_axis (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    rationale  TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
    curated_by TEXT NOT NULL CHECK (length(trim(curated_by)) > 0),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 축 위의 지역들. rank 0 이 최상위(가장 비싼 쪽).
CREATE TABLE ladder_node (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    axis_id  INTEGER NOT NULL REFERENCES ladder_axis(id) ON DELETE CASCADE,
    rank     INTEGER NOT NULL CHECK (rank >= 0),
    label    TEXT NOT NULL,        -- '강남', '잠실', '산본' 등
    lawd_cd  TEXT,                 -- 시군구코드. 있으면 단지 매칭에 쓴다
    emd_name TEXT,                 -- 동 단위 축이면
    note     TEXT,
    UNIQUE (axis_id, rank),
    UNIQUE (axis_id, label)
);

CREATE INDEX idx_ladder_node_region ON ladder_node (lawd_cd, emd_name);

-- ── 비교단지 ──────────────────────────────────────────────────────────
-- selection_reason_json 이 NOT NULL 이라 근거 없이는 저장 자체가 안 된다.
CREATE TABLE benchmark_relation (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id            INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    benchmark_complex_id  INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band             TEXT NOT NULL,
    axis_id               INTEGER REFERENCES ladder_axis(id) ON DELETE SET NULL,
    rank                  INTEGER NOT NULL CHECK (rank >= 1),
    similarity            REAL NOT NULL CHECK (similarity >= 0 AND similarity <= 1),
    -- 어느 기준이 얼마나 맞았는지. 빈 객체('{}')로는 통과하지 못하게 길이를 본다.
    selection_reason_json TEXT NOT NULL CHECK (length(trim(selection_reason_json)) > 2),
    is_manual             INTEGER NOT NULL DEFAULT 0 CHECK (is_manual IN (0,1)),
    engine_version        TEXT NOT NULL,
    calc_trace            TEXT NOT NULL,
    created_at            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, benchmark_complex_id, area_band),
    CHECK (complex_id != benchmark_complex_id)
);

CREATE INDEX idx_benchmark_lookup ON benchmark_relation (complex_id, area_band, rank);

-- ── 가격비율 시계열 ───────────────────────────────────────────────────
-- 두 스냅샷 id 를 함께 저장해, 어느 표본으로 만든 비율인지 되짚을 수 있게 한다.
-- 같은 면적밴드·같은 기준월끼리만 비교한다.
CREATE TABLE price_ratio_history (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id            INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    benchmark_complex_id  INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band             TEXT NOT NULL,
    as_of_ym              TEXT NOT NULL,
    ratio                 REAL NOT NULL CHECK (ratio > 0),
    price_snapshot_id     INTEGER REFERENCES price_snapshot(id) ON DELETE SET NULL,
    benchmark_snapshot_id INTEGER REFERENCES price_snapshot(id) ON DELETE SET NULL,
    -- 시장 국면. 한국부동산원 지수가 없어 벤치마크 가격의 12개월 변화로 자체 판정한다.
    -- 자체 판정이라는 사실을 calc_trace 에 남긴다.
    market_phase          TEXT CHECK (market_phase IS NULL
                                      OR market_phase IN ('상승','하락','횡보')),
    confidence            TEXT NOT NULL
                          CHECK (confidence IN ('HIGH','MEDIUM','LOW')),
    engine_version        TEXT NOT NULL,
    calc_trace            TEXT NOT NULL,
    calculated_at         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, benchmark_complex_id, area_band, as_of_ym)
);

CREATE INDEX idx_ratio_history_lookup
    ON price_ratio_history (complex_id, benchmark_complex_id, area_band, as_of_ym);

-- ── Historical Normal Ratio ───────────────────────────────────────────
-- 요구사항 4: 단순 평균뿐 아니라 median · 5년 · 10년 · 상승기 · 하락기를 구분한다.
-- window_key 로 구간을 나눈다('window' 는 SQLite 예약어라 피한다).
CREATE TABLE ratio_norm (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id            INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    benchmark_complex_id  INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band             TEXT NOT NULL,
    window_key            TEXT NOT NULL
                          CHECK (window_key IN ('all','5y','10y','상승기','하락기','횡보기')),
    median_ratio          REAL NOT NULL,
    mean_ratio            REAL NOT NULL,
    p25_ratio             REAL,
    p75_ratio             REAL,
    sample_n              INTEGER NOT NULL CHECK (sample_n > 0),
    from_ym               TEXT,
    to_ym                 TEXT,
    engine_version        TEXT NOT NULL,
    calc_trace            TEXT NOT NULL,
    calculated_at         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, benchmark_complex_id, area_band, window_key)
);

CREATE INDEX idx_ratio_norm_lookup
    ON ratio_norm (complex_id, benchmark_complex_id, area_band);

INSERT INTO data_source (key, name, kind, url, note) VALUES
 ('ladder_curated', '가격사다리 축 (수기 정의)', '수기', NULL,
  '공공 데이터가 아니라 도메인 지식이다. 어떤 지역들이 어떤 순서로 가격이 이어지는지 사람이 판단해 적고, 그 근거(rationale)를 함께 남긴다');

INSERT INTO engine_version (version, note)
VALUES ('0.6.0', 'PHASE 4 — 가격사다리 · 비교단지 자동선정 · Current/Historical 가격비율');
