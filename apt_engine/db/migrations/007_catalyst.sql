-- 007 · 촉매 계층 — 교통호재 · 공급 · 선행사례 (PHASE 5)
--
-- 요구사항 21·26-10·62-8: 계획 단계 교통호재를 확정 호재처럼 쓰지 않는다.
-- 그래서 status 를 enum 으로 강제하고, **"그 상태가 된 날"(확정 사실)과
-- "개통 예정월"(추정)을 다른 컬럼**에 둔다. 둘을 한 칸에 넣으면 반드시 섞인다.
--
-- 요구사항 5: 근거 없는 촉매는 저장 자체를 거부한다(evidence_json NOT NULL).
-- 요구사항 6: "GTX 생기면 몇 % 오른다" 같은 절대 상승률을 만들지 않는다.
--             이미 개통된 노선의 역세권/비역세권 **가격비율 변화**만 기록한다.

-- ── 노선 ──────────────────────────────────────────────────────────────
CREATE TABLE transit_project (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,          -- 'GTX-A', 'GTX-B', '신안산선'
    kind          TEXT NOT NULL
                  CHECK (kind IN ('GTX','지하철','광역철도','일반철도','도로','기타')),
    source_name   TEXT,
    source_url    TEXT,
    last_verified TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ── 역 ────────────────────────────────────────────────────────────────
-- status 는 뒤로 갈수록 확실하다. '개통'만 사실이고 나머지는 전부 예정이다.
CREATE TABLE transit_station (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES transit_project(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    lawd_cd          TEXT,
    lat              REAL,
    lon              REAL,

    status           TEXT NOT NULL
                     CHECK (status IN ('계획','예비타당성','기본계획','착공',
                                       '공사중','개통예정','개통')),
    status_date      TEXT,          -- 그 상태가 된 날. 확정 사실이다
    expected_open_ym TEXT,          -- 개통 '예정'. 추정이며 자주 밀린다
    opened_ym        TEXT,          -- 실제 개통월. 확정 사실이다

    source_name      TEXT,
    source_url       TEXT,
    last_verified    TEXT,
    note             TEXT,
    UNIQUE (project_id, name),
    -- 개통했다고 적으려면 개통월이 있어야 한다. 없으면 아직 개통이 아니다.
    CHECK (status != '개통' OR opened_ym IS NOT NULL)
);

CREATE INDEX idx_station_region ON transit_station (lawd_cd, status);
CREATE INDEX idx_station_coord  ON transit_station (lat, lon);

-- ── 단지 ↔ 역 거리 ────────────────────────────────────────────────────
-- method 를 반드시 남긴다. 직선거리(haversine)와 도보거리는 완전히 다른 값이고,
-- 직선 500m 를 "도보 7분 역세권"이라고 부르면 안 된다(요구사항 14).
CREATE TABLE station_distance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id    INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    station_id    INTEGER NOT NULL REFERENCES transit_station(id) ON DELETE CASCADE,
    meters        REAL NOT NULL CHECK (meters >= 0),
    walk_minutes  INTEGER,
    method        TEXT NOT NULL CHECK (method IN ('직선','도보경로')),
    calculated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, station_id)
);

CREATE INDEX idx_station_distance_lookup ON station_distance (complex_id, meters);

-- ── 공급 ──────────────────────────────────────────────────────────────
CREATE TABLE supply_plan (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lawd_cd       TEXT NOT NULL,
    emd_name      TEXT,
    complex_name  TEXT NOT NULL,
    households    INTEGER NOT NULL CHECK (households > 0),
    move_in_ym    TEXT NOT NULL,                 -- 입주(예정)월 YYYYMM
    stage         TEXT NOT NULL
                  CHECK (stage IN ('계획','분양','착공','입주예정','입주완료')),
    kind          TEXT CHECK (kind IS NULL
                              OR kind IN ('신규분양','재건축','재개발','공공','기타')),
    lat           REAL,
    lon           REAL,
    source_name   TEXT,
    source_url    TEXT,
    last_verified TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (lawd_cd, complex_name, move_in_ym)
);

CREATE INDEX idx_supply_lookup ON supply_plan (lawd_cd, move_in_ym);

-- ── 선행사례 ──────────────────────────────────────────────────────────
-- 요구사항 6: 이미 개통된 노선에서 역세권/비역세권 가격비율이 어떻게 변했는지.
-- 절대 상승률이 아니라 **상대 비율의 변화**만 기록한다.
CREATE TABLE transit_analogue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id     INTEGER REFERENCES transit_station(id) ON DELETE SET NULL,
    station_name   TEXT NOT NULL,
    project_name   TEXT NOT NULL,
    opened_ym      TEXT NOT NULL,
    area_band      TEXT NOT NULL,
    radius_m       INTEGER NOT NULL,
    before_ym      TEXT NOT NULL,
    after_ym       TEXT NOT NULL,
    near_n         INTEGER NOT NULL CHECK (near_n > 0),
    far_n          INTEGER NOT NULL CHECK (far_n > 0),
    ratio_before   REAL NOT NULL CHECK (ratio_before > 0),
    ratio_after    REAL NOT NULL CHECK (ratio_after > 0),
    delta          REAL NOT NULL,                -- ratio_after − ratio_before
    engine_version TEXT NOT NULL,
    calc_trace     TEXT NOT NULL,
    calculated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (station_name, area_band, before_ym, after_ym, radius_m)
);

-- ── 촉매 ──────────────────────────────────────────────────────────────
-- evidence_json 이 NOT NULL 이라, 근거를 적지 못하면 촉매를 저장할 수 없다.
-- 요구사항 5의 "AI 가 이유 없이 숫자를 만들어내면 안 된다"를 스키마가 강제한다.
CREATE TABLE future_catalyst (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id     INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL
                   CHECK (kind IN ('교통','재건축','공급','학군','업무지구','신축희소성')),
    label          TEXT NOT NULL,
    station_id     INTEGER REFERENCES transit_station(id) ON DELETE SET NULL,
    expected_year  INTEGER,
    -- 투자기간 안에 일어나는가(요구사항 55). NULL = 시점을 모른다
    within_horizon INTEGER CHECK (within_horizon IS NULL OR within_horizon IN (0,1)),
    direction      TEXT NOT NULL CHECK (direction IN ('상승','하락','중립')),
    evidence_json  TEXT NOT NULL CHECK (length(trim(evidence_json)) > 2),
    confidence     TEXT NOT NULL CHECK (confidence IN ('HIGH','MEDIUM','LOW')),
    data_grade     TEXT NOT NULL
                   CHECK (data_grade IN ('CONFIRMED','ESTIMATED','SCENARIO')),
    engine_version TEXT NOT NULL,
    calc_trace     TEXT NOT NULL,
    calculated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, kind, label)
);

CREATE INDEX idx_catalyst_lookup ON future_catalyst (complex_id, kind);

INSERT INTO data_source (key, name, kind, url, note) VALUES
 ('transit_manual', '교통사업 단계 (수기 입력)', '공식문서', NULL,
  '국가철도공단·국토부 보도자료 기준. 계획/예타/기본계획/착공/공사중/개통예정/개통을 구분해 적는다. 기사 제목만 보고 확정 호재로 적지 않는다'),
 ('supply_manual', '입주물량 (수기 입력)', '수기', NULL,
  '단지 단위 입주물량은 공공 API 가 없다. 분양·착공·입주예정 단계를 구분해 적는다'),
 ('vworld_geocode', 'V-World 지오코딩', 'API', 'https://api.vworld.kr',
  '단지 주소 → 좌표. 역세권 거리 계산에 필요하다. VWORLD_API_KEY 사용');

INSERT INTO engine_version (version, note)
VALUES ('0.7.0', 'PHASE 5 — 교통호재 단계 구분 · 역세권 거리 · 공급 · 개통 선행사례 · 촉매');
