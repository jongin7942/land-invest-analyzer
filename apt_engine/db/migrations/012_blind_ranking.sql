-- 012 · 누출 방지 · 랭킹 스냅샷 · Lessons DB (지시서 §1·§3·§18·§58·§64·§66·§69·§70)
--
-- 이 마이그레이션은 계산을 추가하지 않는다. **모델이 반칙하지 못하게 막는 구조**를
-- 만든다. 순서가 중요해서 먼저 넣는다 — 랭킹을 만든 뒤에 누출 방지를 붙이면,
-- 이미 오염된 모델을 검증할 방법이 없다.

-- ── 사용자 관심단지 ───────────────────────────────────────────────────
-- §1: 이 테이블은 **candidate generation 과 scoring 에서 절대 읽지 않는다.**
-- 최종 ranking 이 확정된 뒤 표시(annotation)에만 쓴다.
-- 코드가 이걸 어기지 못하도록 tests/test_blind.py 가 AST 로 검사한다.
CREATE TABLE watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id  INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    added_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    note        TEXT,
    UNIQUE (complex_id)
);

-- ── 출처 충돌 (§3) ────────────────────────────────────────────────────
-- 같은 필드에 서로 다른 값이 오면 **덮어쓰지 않고 둘 다 남긴다.**
-- 공식 출처를 우선하되, 우선했다는 사실과 진 값도 기록한다.
CREATE TABLE source_conflict (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type    TEXT NOT NULL,              -- 'complex' / 'unit_type' …
    entity_id      INTEGER NOT NULL,
    field_name     TEXT NOT NULL,              -- '준공연도' 처럼 사람이 읽는 이름
    value_a        TEXT NOT NULL,
    source_a       TEXT NOT NULL,
    source_a_tier  INTEGER NOT NULL,           -- 낮을수록 공식 (source_tier 참고)
    value_b        TEXT NOT NULL,
    source_b       TEXT NOT NULL,
    source_b_tier  INTEGER NOT NULL,
    resolved_to    TEXT,                       -- 채택한 값. NULL = 미해결
    resolved_by    TEXT,                       -- 'tier' / 'manual' / NULL
    observed_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    note           TEXT
);

CREATE INDEX idx_source_conflict_entity
    ON source_conflict (entity_type, entity_id, field_name);

-- 출처 등급. 숫자가 작을수록 공식이다(§3 Source priority).
CREATE TABLE source_tier (
    tier        INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT
);

INSERT INTO source_tier (tier, name, description) VALUES
 (1, '공식 정부/지자체', '법령·고시·건축물대장·정비사업 고시'),
 (2, '공식 공공데이터/API', '국토부 실거래가, K-apt, V-World'),
 (3, '공신력 높은 원천데이터', '한국부동산원, 감정평가, 조합 공고'),
 (4, '민간 데이터 서비스', '시세 제공 업체'),
 (5, '포털', '포털 매물·시세'),
 (6, '기타', '현장 확인, 중개사 구두');

-- ── 랭킹 실행 스냅샷 (§64·§66) ────────────────────────────────────────
-- 과거 snapshot 을 덮어쓰지 않는다. 매 실행이 새 run 이고, 순위 변화를 추적한다.
CREATE TABLE ranking_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key         TEXT NOT NULL,             -- 사람이 알아볼 이름
    as_of           TEXT NOT NULL,             -- **데이터 컷오프**. 이 날짜 이후 데이터 사용 금지
    executed_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    cash            INTEGER NOT NULL CHECK (cash > 0),
    horizon_years   INTEGER NOT NULL CHECK (horizon_years > 0),
    profile         TEXT NOT NULL,             -- balanced / aggressive / defensive …
    list_kind       TEXT NOT NULL
                    CHECK (list_kind IN ('absolute','risk_adjusted','asymmetric')),
    universe_size   INTEGER NOT NULL,          -- 컷오프 시점 전체 후보 수
    feasible_size   INTEGER NOT NULL,          -- Capital Feasibility 통과 수
    engine_version  TEXT NOT NULL,
    weights_json    TEXT NOT NULL,             -- 이 실행이 쓴 가중치 (재현성)
    weights_source  TEXT NOT NULL
                    CHECK (weights_source IN ('HEURISTIC','BACKTESTED')),
    note            TEXT,
    UNIQUE (run_key, as_of, cash, horizon_years, profile, list_kind)
);

CREATE INDEX idx_ranking_run_lookup ON ranking_run (as_of, cash, horizon_years);

CREATE TABLE ranking_entry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES ranking_run(id) ON DELETE CASCADE,
    rank            INTEGER NOT NULL CHECK (rank > 0),
    complex_id      INTEGER NOT NULL REFERENCES complex(id),
    area_band       TEXT NOT NULL,

    score           REAL NOT NULL,
    confidence      REAL,                      -- §50: score 와 절대 합치지 않는다
    kill_score      REAL,
    thesis_survival REAL,

    required_equity INTEGER,
    buyable_price   INTEGER,
    expected_roe    REAL,
    downside_roe    REAL,

    factors_json    TEXT NOT NULL,             -- §76 factor contribution
    reasons_json    TEXT,                      -- §63 WHY BUY / WHY NOT
    calc_trace      TEXT NOT NULL,
    data_grade      TEXT NOT NULL DEFAULT 'SCENARIO'
                    CHECK (data_grade = 'SCENARIO'),
    UNIQUE (run_id, rank),
    UNIQUE (run_id, complex_id, area_band)
);

CREATE INDEX idx_ranking_entry_complex ON ranking_entry (complex_id, run_id);

-- ── Investment Lessons DB (§58·§59) ───────────────────────────────────
-- 백테스트에서 얻은 규칙을 코드에 하드코딩하지 않기 위한 그릇.
-- 사례 몇 개로 CONFIRMED 로 올리지 못하게 sample_size 를 필수로 둔다.
CREATE TABLE investment_lesson (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_key          TEXT NOT NULL UNIQUE,
    original_hypothesis TEXT NOT NULL,
    evidence            TEXT,
    tested_regions      TEXT,
    tested_regimes      TEXT,
    sample_size         INTEGER,
    result              TEXT,
    modified_rule       TEXT,
    status              TEXT NOT NULL DEFAULT 'HYPOTHESIS'
                        CHECK (status IN ('HYPOTHESIS','PROVISIONAL',
                                          'CONFIRMED','REJECTED')),
    created_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    -- CONFIRMED 로 올리려면 근거와 표본이 있어야 한다. 숫자는 코드가 정한다.
    CHECK (status != 'CONFIRMED'
           OR (sample_size IS NOT NULL AND evidence IS NOT NULL))
);

INSERT INTO engine_version (version, note)
VALUES ('0.11.0', 'PHASE 8-P0 — 누출 방지(as-of 컷오프·익명 랭킹) · 랭킹 스냅샷 · 출처충돌 · Lessons DB');
