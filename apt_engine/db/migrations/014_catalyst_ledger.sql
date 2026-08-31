-- 014 · Catalyst Ledger + 공급 발표시점 (지시서 §17·§18)
--
-- §18 이 요구하는 것은 하나다: **백테스트 시점마다 "그때 알려진 것" 만 쓴다.**
--
--   당시 알려진 호재 · 당시 단계 · 당시 예상 완료시점 · 당시 실현확률 ·
--   당시 가격반영률
--
-- 지금 스키마는 호재의 **현재 상태**만 담는다. 2024년에 확정된 GTX 노선이 하나의
-- 행으로 있으면, 2023년 백테스트가 그 행을 읽는 순간 반칙이다.
-- 그래서 상태를 덮어쓰지 않고 **시점별로 쌓는 원장(ledger)** 을 만든다.

-- ── 공급: 언제 알려진 계획인가 ────────────────────────────────────────
-- move_in_ym(입주 예정월)은 '언제 들어오나' 이고, announced_ym 은 '언제 알았나' 다.
-- 백테스트에 필요한 건 후자다. 2020년 모델은 2023년에 발표된 공급을 몰랐다.
ALTER TABLE supply_plan ADD COLUMN announced_ym TEXT;

CREATE INDEX idx_supply_announced ON supply_plan (lawd_cd, announced_ym, move_in_ym);

-- ── Catalyst Ledger (§17·§18) ─────────────────────────────────────────
-- 호재 하나가 시간에 따라 어떻게 바뀌었는지를 행으로 쌓는다. 덮어쓰지 않는다.
--
-- Catalyst Alpha = Economic Impact × Realization Probability × Time Relevance
--                  × Complex Exposure × (1 − Priced In Fraction)
--
-- 다섯 항목을 각각 저장하는 이유: 하나로 합쳐 두면 "왜 이 호재 점수가 이런가" 를
-- 되짚을 수 없고, 어느 항목이 틀렸는지도 알 수 없다.
CREATE TABLE catalyst (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    catalyst_key       TEXT NOT NULL UNIQUE,      -- 사람이 알아볼 식별자
    catalyst_type      TEXT NOT NULL
                       CHECK (catalyst_type IN ('GTX','지하철','철도','도로','재건축',
                                                '재개발','신도시정비','역세권개발',
                                                '업무지구','산업단지','기업이전',
                                                '상업시설','학교','공원','생활SOC',
                                                '기타')),
    name               TEXT NOT NULL,
    lat                REAL,
    lon                REAL,
    benefit_radius_m   INTEGER CHECK (benefit_radius_m IS NULL OR benefit_radius_m > 0),
    source_name        TEXT,
    source_url         TEXT,
    note               TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 시점별 상태. **같은 호재의 과거 행을 고치지 않는다** — 새 행을 넣는다.
CREATE TABLE catalyst_state (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    catalyst_id            INTEGER NOT NULL REFERENCES catalyst(id) ON DELETE CASCADE,
    as_of                  TEXT NOT NULL,          -- 이 상태를 알 수 있었던 시점
    stage                  TEXT NOT NULL
                           CHECK (stage IN ('발표','계획','예비타당성','기본계획',
                                            '착공','공사중','완공예정','완공','무산')),
    announcement_date      TEXT,
    confirmation_date      TEXT,
    construction_date      TEXT,
    expected_completion    TEXT,
    actual_completion      TEXT,

    -- 다섯 항목. 전부 0~1 이거나 금액이고, 모르면 NULL 이다(0 이 아니다).
    realization_probability REAL
                           CHECK (realization_probability IS NULL
                                  OR (realization_probability >= 0
                                      AND realization_probability <= 1)),
    economic_impact        INTEGER,                -- 원. 이 호재가 만드는 가치
    priced_in_fraction     REAL
                           CHECK (priced_in_fraction IS NULL
                                  OR (priced_in_fraction >= 0
                                      AND priced_in_fraction <= 1)),

    evidence_json          TEXT NOT NULL CHECK (length(trim(evidence_json)) > 2),
    source_name            TEXT,
    source_url             TEXT,
    verification           TEXT NOT NULL DEFAULT 'NEEDS_VERIFICATION'
                           CHECK (verification IN ('VERIFIED','ESTIMATED','UNKNOWN',
                                                   'NEEDS_VERIFICATION')),
    note                   TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    -- 같은 호재·같은 시점의 상태는 하나뿐이다. 시점이 다르면 새 행이다.
    UNIQUE (catalyst_id, as_of),
    -- 완공으로 적으려면 실제 완공일이 있어야 한다(교통 status='개통' 과 같은 원칙).
    CHECK (stage != '완공' OR actual_completion IS NOT NULL)
);

CREATE INDEX idx_catalyst_state_asof ON catalyst_state (catalyst_id, as_of);

-- 단지별 노출도. 거리만으로 정하지 않는다 — 같은 500m 라도 역 방향이냐
-- 반대쪽이냐에 따라 다르고, 그건 사람이 판단할 문제다.
CREATE TABLE catalyst_exposure (
    catalyst_id    INTEGER NOT NULL REFERENCES catalyst(id) ON DELETE CASCADE,
    complex_id     INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    exposure       REAL NOT NULL CHECK (exposure >= 0 AND exposure <= 1),
    meters         REAL,
    method         TEXT NOT NULL CHECK (method IN ('직선거리','도보경로','수기판단')),
    rationale      TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
    as_of          TEXT,
    PRIMARY KEY (catalyst_id, complex_id)
);

INSERT INTO data_source (key, name, kind, url, note) VALUES
 ('catalyst_ledger', '호재 원장 (수기 입력)', '공식문서', NULL,
  '호재의 시점별 상태를 덮어쓰지 않고 쌓는다. 백테스트는 그 시점 행만 읽는다(§18)');

INSERT INTO engine_version (version, note)
VALUES ('0.13.0', 'Phase 4 — Catalyst Ledger(시점별 상태) · 공급 발표시점 · Feature 계층');
