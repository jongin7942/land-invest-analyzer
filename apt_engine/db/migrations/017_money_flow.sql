-- ═══════════════════════════════════════════════════════════════════════
-- 017 — 돈의 흐름 분리 · 토허 실행판정 · 참고정보 (2차 DELTA)
-- ═══════════════════════════════════════════════════════════════════════
--
-- 지시서 §8~§10 · §6 · §38.
--
-- 이 마이그레이션의 핵심은 **두 종류의 돈을 절대 한 테이블에 섞지 않는 것**이다.
-- 지역에 일자리가 늘어 생긴 구매력(Income Flow)과, 서울 가격이 올라 밀려
-- 내려온 구매자(Capital Migration Flow)는 원인도 지속성도 다르다. 합쳐서
-- 하나의 '자금 유입 점수' 로 만들면 왜 오를 것인지 설명할 수 없게 된다.

-- ── §8-A 지역 소득 유입 ───────────────────────────────────────────────
--
-- 실제 지역 구매력을 높이는 돈. 고용·사업체·성과급 같은 것.
--
-- ⚠ 이 표는 **수기 입력 전용**이다. 국토부 실거래에는 소득 정보가 없고,
--   추정하면 그 순간 "삼성이 있으니 오른다" 는 이름값 점수가 된다(§49-3).
--   출처와 기준일이 없으면 INSERT 자체가 실패하게 막는다.
CREATE TABLE IF NOT EXISTS region_income (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lawd_cd         TEXT NOT NULL,
    as_of_ym        TEXT NOT NULL,                  -- 관측 시점 YYYYMM

    metric          TEXT NOT NULL
                    CHECK (metric IN ('고용자수','사업체수','평균소득',
                                      '고소득일자리','성과급','산업단지가동')),
    value           REAL,                           -- 모르면 NULL. 0 이 아니다.
    unit            TEXT NOT NULL,
    yoy_change_pct  REAL,                           -- 전년 대비 변화율

    -- 값이 없으면 왜 없는지 반드시 남긴다 (§49-16)
    unknown_reason  TEXT,

    source_name     TEXT NOT NULL CHECK (length(trim(source_name)) > 0),
    source_url      TEXT,
    observed_on     TEXT NOT NULL,                  -- 자료의 기준일
    last_verified   TEXT,                           -- 비면 계산에 쓰지 않는다
    confidence      TEXT NOT NULL DEFAULT 'ESTIMATED'
                    CHECK (confidence IN ('CONFIRMED','ESTIMATED','UNKNOWN')),
    note            TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (lawd_cd, as_of_ym, metric),
    -- 값이 없으면 왜 없는지 반드시 남긴다 (§49-16)
    CHECK (value IS NOT NULL OR (unknown_reason IS NOT NULL
                                 AND length(trim(unknown_reason)) > 0))
);
CREATE INDEX IF NOT EXISTS idx_region_income_region
    ON region_income(lawd_cd, as_of_ym);

-- ── §8-B 가격 사다리 이동 ─────────────────────────────────────────────
--
-- 서울/대장 가격이 올라 기존 구매자가 더 싼 지역으로 내려오는 흐름.
-- 사다리 축(ladder_axis)은 이미 있으므로 **관측 결과만** 여기 쌓는다.
--
-- ⚠ 사다리에 이웃해 있다는 사실만으로는 아무 점수도 주지 않는다.
--   위 칸이 실제로 올랐고(upper_rise_pct), 구매자가 겹치고(buyer_overlap),
--   가격차가 아직 남아 있어야(gap_pct) 흐름으로 인정한다.
CREATE TABLE IF NOT EXISTS migration_flow (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    axis_id           INTEGER NOT NULL REFERENCES ladder_axis(id) ON DELETE CASCADE,
    from_lawd_cd      TEXT NOT NULL,                -- 돈이 나온 위 칸
    to_lawd_cd        TEXT NOT NULL,                -- 돈이 향하는 아래 칸
    as_of_ym          TEXT NOT NULL,

    upper_rise_pct    REAL,                         -- 위 칸이 얼마나 올랐나
    gap_pct           REAL,                         -- 아직 남은 가격차
    buyer_overlap     REAL CHECK (buyer_overlap IS NULL
                                  OR (buyer_overlap >= 0 AND buyer_overlap <= 1)),
    observed_lag_m    INTEGER,                      -- 과거 전달까지 걸린 개월

    unknown_reason    TEXT,

    evidence_json     TEXT NOT NULL CHECK (length(trim(evidence_json)) > 2),
    engine_version    TEXT NOT NULL,
    calculated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (axis_id, from_lawd_cd, to_lawd_cd, as_of_ym),
    CHECK (upper_rise_pct IS NOT NULL OR (unknown_reason IS NOT NULL
                                          AND length(trim(unknown_reason)) > 0))
);
CREATE INDEX IF NOT EXISTS idx_migration_to
    ON migration_flow(to_lawd_cd, as_of_ym);

-- ── §6 토허 실행 판정 ─────────────────────────────────────────────────
--
-- land_permit_zone 에 이미 scope(내국인/외국인) · 실거주의무 · 기간이 있다.
-- 여기서 더 필요한 것은 **실거주 유예**와 **실행 가능 여부**, 그리고 신뢰도다.
--
-- 실행 가능 여부를 컬럼으로 두는 이유: 감점(-5점)이 아니라 Gate 이기 때문이다.
-- 점수로 두면 기대수익이 크면 통과해 버린다(§5·§49-9).
ALTER TABLE land_permit_zone ADD COLUMN residence_grace_allowed INTEGER;
ALTER TABLE land_permit_zone ADD COLUMN residence_grace_note TEXT;
ALTER TABLE land_permit_zone ADD COLUMN confidence TEXT
     DEFAULT 'ESTIMATED';
ALTER TABLE land_permit_zone ADD COLUMN parcel_recheck_required INTEGER
     NOT NULL DEFAULT 1;   -- 매수 직전 필지 기준 재확인 필요 (§6)

-- ── §38 참고정보: 차량 이동시간 ───────────────────────────────────────
--
-- ⚠ 이 값은 **투자점수에 절대 들어가지 않는다.** 임장 편의성 참고용이다.
--   그래서 feature 테이블이 아니라 별도 표에 둔다 — 같은 표에 있으면
--   언젠가 누가 점수에 섞는다.
CREATE TABLE IF NOT EXISTS drive_time (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_label  TEXT NOT NULL,                    -- '동탄' · '평택' 등
    complex_id    INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    minutes       INTEGER,
    unknown_reason TEXT,
    condition     TEXT NOT NULL DEFAULT '평시'
                  CHECK (condition IN ('평시','출근','주말')),
    source_name   TEXT NOT NULL,
    measured_on   TEXT NOT NULL,
    -- 점수에 쓰지 않는다는 것을 스키마로 못박는다. 1 이외의 값은 못 들어간다.
    excluded_from_score INTEGER NOT NULL DEFAULT 1
                  CHECK (excluded_from_score = 1),
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (origin_label, complex_id, condition),
    CHECK (minutes IS NOT NULL OR (unknown_reason IS NOT NULL
                                   AND length(trim(unknown_reason)) > 0))
);

-- ── §41 결과 필드 보강 ────────────────────────────────────────────────
ALTER TABLE ranking_entry ADD COLUMN compare_buy_price INTEGER;   -- §2 비교 필요
ALTER TABLE ranking_entry ADD COLUMN chase_risk_price INTEGER;    -- §2 추격 위험
ALTER TABLE ranking_entry ADD COLUMN income_flow REAL;            -- §8-A
ALTER TABLE ranking_entry ADD COLUMN migration_flow REAL;         -- §8-B
ALTER TABLE ranking_entry ADD COLUMN dual_flow INTEGER;           -- §9
ALTER TABLE ranking_entry ADD COLUMN relative_stretch REAL;       -- §20
ALTER TABLE ranking_entry ADD COLUMN loan_feasibility TEXT;       -- §40
ALTER TABLE ranking_entry ADD COLUMN reason_text TEXT;            -- §36 문장 설명
