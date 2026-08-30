-- 008 · 재건축·재개발 사업성 계층 (PHASE 6)
--
-- 이 구간이 계획서에서 "최상 난이도"인 이유는 계산이 어려워서가 아니라
-- **입력 데이터가 공공 API 로 나오지 않기 때문**이다. 대지면적·정비계획 용적률·
-- 평당 공사비·조합원 종전자산평가액은 전부 사람이 원문을 보고 넣어야 한다.
--
-- 그래서 스키마가 강제하는 것은 다음 세 가지다.
--
-- 1) 요구사항 62-7 — 법정 최대 용적률을 확정된 사업 용적률처럼 쓰지 않는다.
--    far_standard.kind 로 '법정상한' / '조례' / '정비계획' / '역세권특례' 를
--    **다른 행**으로 저장한다. 한 컬럼에 넣으면 반드시 섞인다.
--    조합이 실제로 받은 용적률은 redevelopment_project.planned_far 하나뿐이고,
--    그건 정비구역지정 이후에만 존재한다(CHECK 로 막는다).
--
-- 2) 사실과 예정을 다른 컬럼에 둔다(007 의 교통과 같은 원칙).
--    stage / stage_date 는 확정 사실, expected_* 는 추정이다.
--
-- 3) 모든 사업성 숫자는 시나리오다. redevelopment_scenario 의 data_grade 는
--    'SCENARIO' 만 허용한다. 추가분담금을 확정 금액처럼 보여줄 수 없다.

-- ── 용적률 기준 ───────────────────────────────────────────────────────
-- kind 의 의미(뒤로 갈수록 사업에 실제로 적용될 확률이 높다):
--   법정상한     국토계획법 시행령의 용도지역별 최대치. **거의 못 받는다**
--   조례         해당 지자체 도시계획조례 상한. 실무 출발점
--   정비계획     그 구역에 실제로 고시된 정비계획 용적률. 사실에 가장 가깝다
--   역세권특례   역세권 등 특례로 상향 가능한 한도. 조건부이며 공공기여가 따라온다
CREATE TABLE far_standard (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sido           TEXT,                       -- NULL = 전국(법정상한)
    lawd_cd        TEXT,                       -- NULL = 시도 전체
    zoning         TEXT NOT NULL,              -- '제2종일반주거지역' 등
    kind           TEXT NOT NULL
                   CHECK (kind IN ('법정상한','조례','정비계획','역세권특례')),
    max_far        REAL NOT NULL CHECK (max_far > 0),
    conditions_json TEXT,                      -- 특례 조건(역세권 반경, 공공기여율 등)
    public_contribution_rate REAL              -- 그 용적률을 받으려면 내야 하는 공공기여 비율 0~1
                   CHECK (public_contribution_rate IS NULL
                          OR (public_contribution_rate >= 0 AND public_contribution_rate < 1)),
    effective_from TEXT NOT NULL,
    effective_to   TEXT,
    source_name    TEXT,
    source_url     TEXT,
    last_verified  TEXT,                       -- NULL = 미검증. 엔진이 계산을 거부한다
    note           TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX idx_far_standard_lookup ON far_standard (zoning, kind, effective_from);

-- ── 정비사업 ──────────────────────────────────────────────────────────
-- stage 는 뒤로 갈수록 확실하다. '미지정'은 "아직 아무것도 아니다"라는 뜻이고,
-- 오래된 단지라는 이유만으로 재건축 가능성을 숫자로 만들지 않기 위한 기본값이다.
CREATE TABLE redevelopment_project (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id     INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    project_type   TEXT NOT NULL
                   CHECK (project_type IN ('재건축','재개발','리모델링')),
    name           TEXT,                       -- 구역명/조합명
    stage          TEXT NOT NULL
                   CHECK (stage IN ('미지정','예비안전진단','정밀안전진단','정비구역지정',
                                    '추진위원회','조합설립','사업시행인가','관리처분인가',
                                    '이주철거','착공','준공')),
    stage_date     TEXT,                       -- 그 단계가 된 날. 확정 사실
    safety_grade   TEXT,                       -- 안전진단 등급 A~E. 사실

    -- 아래는 전부 '예정'이다. 확정 사실과 절대 같은 칸에 두지 않는다.
    expected_approval_ym TEXT,                 -- 사업시행인가 예정
    expected_move_ym     TEXT,                 -- 이주 예정
    expected_done_ym     TEXT,                 -- 준공 예정

    -- 정비계획 고시가 있어야만 존재하는 값들.
    planned_far          REAL CHECK (planned_far IS NULL OR planned_far > 0),
    planned_units        INTEGER CHECK (planned_units IS NULL OR planned_units > 0),
    rental_ratio         REAL CHECK (rental_ratio IS NULL
                                     OR (rental_ratio >= 0 AND rental_ratio < 1)),
    public_contribution_rate REAL CHECK (public_contribution_rate IS NULL
                                     OR (public_contribution_rate >= 0
                                         AND public_contribution_rate < 1)),
    member_count         INTEGER CHECK (member_count IS NULL OR member_count > 0),
    prior_asset_total    INTEGER CHECK (prior_asset_total IS NULL OR prior_asset_total > 0),
                                               -- 종전자산 감정평가 총액(원). 관리처분 이후에만 확정

    source_name    TEXT,
    source_url     TEXT,
    last_verified  TEXT,
    data_grade     TEXT NOT NULL DEFAULT 'ESTIMATED'
                   CHECK (data_grade IN ('CONFIRMED','ESTIMATED','SCENARIO')),
    note           TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, project_type),

    -- 정비계획 용적률은 구역지정 전에는 존재할 수 없다.
    -- 이걸 막지 않으면 "조례 상한"이 슬그머니 planned_far 로 들어온다(요구사항 62-7).
    CHECK (planned_far IS NULL
           OR stage IN ('정비구역지정','추진위원회','조합설립','사업시행인가',
                        '관리처분인가','이주철거','착공','준공')),
    -- 단계를 적었으면 언제 그렇게 됐는지도 적는다. '미지정'만 예외.
    CHECK (stage = '미지정' OR stage_date IS NOT NULL)
);

CREATE INDEX idx_redev_project_complex ON redevelopment_project (complex_id, stage);

-- ── 단계별 소요기간 참고치 ────────────────────────────────────────────
-- 요구사항 19(사업기간)·20(지연위험)의 근거. 비어 있으면 엔진은 사업기간을
-- '확인 불가'로 답한다. 그럴듯한 평균 연수를 코드에 적어 넣지 않는다.
CREATE TABLE stage_duration_ref (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_type   TEXT NOT NULL CHECK (project_type IN ('재건축','재개발','리모델링')),
    from_stage     TEXT NOT NULL,
    to_stage       TEXT NOT NULL,
    region         TEXT,                        -- 시도. NULL = 전국
    median_months  INTEGER NOT NULL CHECK (median_months > 0),
    p25_months     INTEGER CHECK (p25_months IS NULL OR p25_months > 0),
    p75_months     INTEGER CHECK (p75_months IS NULL OR p75_months > 0),
    sample_n       INTEGER CHECK (sample_n IS NULL OR sample_n > 0),
    source_name    TEXT,
    source_url     TEXT,
    last_verified  TEXT,
    note           TEXT,
    UNIQUE (project_type, from_stage, to_stage, region)
);

-- ── 공사비 참고치 ─────────────────────────────────────────────────────
-- 평당 공사비는 해마다 크게 변한다. 기준연도 없이 쓰면 안 된다.
CREATE TABLE construction_cost_ref (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    region         TEXT,                        -- 시도. NULL = 전국
    grade          TEXT NOT NULL DEFAULT '보통'
                   CHECK (grade IN ('보통','고급','최고급')),
    base_year      INTEGER NOT NULL,            -- 기준연도. 표시할 때 반드시 같이 보여준다
    cost_per_py    INTEGER NOT NULL CHECK (cost_per_py > 0),  -- 원/평(공사연면적 기준)
    other_cost_rate REAL CHECK (other_cost_rate IS NULL
                                OR (other_cost_rate >= 0 AND other_cost_rate < 1)),
                                               -- 기타사업비/공사비 비율
    source_name    TEXT,
    source_url     TEXT,
    last_verified  TEXT,
    note           TEXT,
    UNIQUE (region, grade, base_year)
);

-- ── 사업성 시나리오 ───────────────────────────────────────────────────
-- data_grade 가 'SCENARIO' 로 고정돼 있다. 추가분담금을 확정 금액처럼 저장할
-- 방법이 스키마에 없다(요구사항 18·62-5).
CREATE TABLE redevelopment_scenario (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id        INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    area_band         TEXT NOT NULL,
    as_of             TEXT NOT NULL,             -- YYYY-MM-DD
    scenario_key      TEXT NOT NULL CHECK (scenario_key IN ('보수','기준','낙관')),

    -- 가정 (전부 입력값이다)
    far               REAL NOT NULL CHECK (far > 0),
    far_kind          TEXT NOT NULL,             -- 그 용적률이 어디서 왔는지
    cost_per_py       INTEGER NOT NULL CHECK (cost_per_py > 0),
    cost_base_year    INTEGER NOT NULL,
    new_price_per_m2  INTEGER NOT NULL CHECK (new_price_per_m2 > 0),

    -- 결과
    new_units         INTEGER CHECK (new_units IS NULL OR new_units > 0),
    general_units     INTEGER,
    sale_revenue      INTEGER,
    total_cost        INTEGER,
    proportion_rate   REAL,                      -- 비례율
    right_value       INTEGER,                   -- 권리가액(조합원 1인)
    member_price      INTEGER,                   -- 조합원분양가
    extra_charge      INTEGER,                   -- 추가분담금 (음수면 환급)

    engine_version    TEXT NOT NULL,
    calc_trace        TEXT NOT NULL,
    data_grade        TEXT NOT NULL DEFAULT 'SCENARIO'
                      CHECK (data_grade = 'SCENARIO'),
    calculated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, area_band, as_of, scenario_key)
);

CREATE INDEX idx_redev_scenario_lookup
    ON redevelopment_scenario (complex_id, as_of, scenario_key);

-- ── 2단계 스크리닝 ────────────────────────────────────────────────────
-- 1단계는 자동(연식·용적률·대지지분)이고 2단계는 사람이 상위 후보만 조사한다.
-- 조사하지 않은 단지는 manual_status='미조사' 로 남고, 그 단지의 사업성 숫자는
-- 만들지 않는다. "전국 아파트에 같은 구조" 요구를 충족하면서도,
-- 조사하지 않은 단지에 그럴듯한 분담금을 붙이지 않기 위한 장치다.
CREATE TABLE redev_candidate (
    complex_id     INTEGER PRIMARY KEY REFERENCES complex(id) ON DELETE CASCADE,
    screened_at    TEXT NOT NULL,
    as_of          TEXT NOT NULL,
    age_years      INTEGER,
    current_far    REAL,
    land_share_m2  REAL,                        -- 평균 대지지분(대지면적/아파트세대수)
    score          REAL NOT NULL,
    rank_in_region INTEGER,
    reason_json    TEXT NOT NULL CHECK (length(trim(reason_json)) > 2),
    manual_status  TEXT NOT NULL DEFAULT '미조사'
                   CHECK (manual_status IN ('미조사','조사중','완료','제외')),
    manual_note    TEXT,
    engine_version TEXT NOT NULL
);

CREATE INDEX idx_redev_candidate_rank ON redev_candidate (score DESC);

-- ── 대지면적 출처 ─────────────────────────────────────────────────────
-- 대지지분은 이 엔진에서 가장 민감한 입력이다. 어디서 온 값인지 남기지 않으면
-- 나중에 검증할 수 없다.
ALTER TABLE complex ADD COLUMN land_area_source TEXT;
ALTER TABLE complex ADD COLUMN land_area_verified TEXT;
ALTER TABLE unit_type ADD COLUMN land_share_source TEXT;

INSERT INTO data_source (key, name, kind, url, note) VALUES
 ('redev_manual', '정비사업 단계·정비계획 (수기 입력)', '공식문서',
  'https://cleanup.seoul.go.kr',
  '정비사업 단계와 정비계획 용적률은 공공 API 로 나오지 않는다. 서울 정비사업 정보몽땅·각 지자체 고시를 사람이 확인해 넣는다. 조례 상한을 정비계획 용적률로 적지 않는다'),
 ('landbook_manual', '대지면적·대지지분 (수기 입력)', '공식문서',
  'https://www.eum.go.kr',
  '건축물대장 총괄표제부의 대지면적. 등기부 대지권 비율이 있으면 평형별로 넣는다'),
 ('costref_manual', '평당 공사비 참고치 (수기 입력)', '수기', NULL,
  '기준연도를 반드시 함께 적는다. 연도 없는 공사비는 쓰지 않는다');

INSERT INTO engine_version (version, note)
VALUES ('0.8.0', 'PHASE 6 — 재건축 사업성: 용적률 기준 분리 · 정비사업 단계 · 비례율/추가분담금 시나리오 · 신축전환원가 · 2단계 스크리닝');
