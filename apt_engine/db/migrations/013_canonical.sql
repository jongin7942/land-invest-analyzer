-- 013 · Canonical Data Model + PropertyResolver (지시서 §2·§3)
--
-- §2 가 단지별로 요구하는 것 중 아직 저장할 곳이 없던 것들:
--   행정동 · 좌표(있음) · 역 거리 · 주요 업무지 접근성 · 학군 · 생활권 · 대지지분
--
-- 이걸 전부 complex 컬럼으로 만들지 않는다. 이유가 둘이다.
--   1) 대부분 공공 API 로 안 나와서 사람이 넣어야 하고, 항목마다 출처·확인일·
--      신뢰도가 따로 붙어야 한다(§3). 컬럼으로 만들면 그걸 담을 자리가 없다.
--   2) 항목이 계속 늘어난다. 늘 때마다 마이그레이션을 하면 스키마가 누더기가 된다.
--
-- 그래서 값마다 출처가 붙는 속성 테이블(complex_attribute)로 간다.
-- 자주 쓰이고 반드시 있어야 하는 것(행정동·최근접역)만 컬럼으로 둔다.

ALTER TABLE complex ADD COLUMN admin_dong TEXT;          -- 행정동 (법정동은 emd_name)
ALTER TABLE complex ADD COLUMN life_zone TEXT;           -- 생활권 키 (행정구역과 다르다)
ALTER TABLE complex ADD COLUMN nearest_station_id INTEGER
    REFERENCES transit_station(id);
ALTER TABLE complex ADD COLUMN nearest_station_m REAL;   -- 직선거리. 도보거리 아님
ALTER TABLE complex ADD COLUMN canonical_id INTEGER REFERENCES complex(id);
    -- 같은 실체를 가리키는 중복 행이 발견되면 대표 행을 가리킨다. NULL = 자기 자신이 대표

CREATE INDEX idx_complex_life_zone ON complex (life_zone);
CREATE INDEX idx_complex_canonical ON complex (canonical_id);

-- ── PropertyResolver: 단지명 변경·동명 중복 (§2) ──────────────────────
-- "래미안OO" → "OO자이" 로 이름이 바뀌거나, 같은 이름이 여러 시군구에 있는 경우를
-- 처리한다. **자동으로 합치지 않는다** — 근거(reason)와 누가 합쳤는지가 필수다.
CREATE TABLE complex_alias (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id   INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    alias        TEXT NOT NULL,
    alias_norm   TEXT NOT NULL,
    kind         TEXT NOT NULL
                 CHECK (kind IN ('이전명','별칭','오기','분양명','한자','영문')),
    valid_from   TEXT,                    -- 이름이 바뀐 시점(알면). 백테스트에 필요하다
    valid_to     TEXT,
    reason       TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    created_by   TEXT NOT NULL CHECK (length(trim(created_by)) > 0),
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (complex_id, alias_norm)
);

CREATE INDEX idx_complex_alias_norm ON complex_alias (alias_norm);

-- ── 값마다 출처가 붙는 속성 (§2·§3) ───────────────────────────────────
-- 학군·생활권·업무지 접근성처럼 공식 API 가 없어 사람이 넣는 값들.
-- 같은 key 에 다른 source 로 값이 여러 개 있을 수 있고, 그건 충돌이 아니라 정상이다
-- (source_conflict 는 '같은 것을 다르게 말할 때' 쓴다).
CREATE TABLE complex_attribute (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    complex_id    INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    attr_key      TEXT NOT NULL,          -- 'school_zone' 'life_zone' 'parking_ratio' …
    value_text    TEXT,
    value_num     REAL,
    unit          TEXT,
    as_of         TEXT,                   -- 이 값이 유효한 시점. 백테스트가 이걸 본다
    source_name   TEXT NOT NULL,
    source_url    TEXT,
    source_tier   INTEGER NOT NULL REFERENCES source_tier(tier),
    confidence    TEXT NOT NULL DEFAULT 'MEDIUM'
                  CHECK (confidence IN ('HIGH','MEDIUM','LOW')),
    verification  TEXT NOT NULL DEFAULT 'NEEDS_VERIFICATION'
                  CHECK (verification IN ('VERIFIED','ESTIMATED','UNKNOWN',
                                          'NEEDS_VERIFICATION')),
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    -- 값이 둘 다 비면 저장할 이유가 없다. '모른다' 는 행을 만들지 않는 것으로 표현한다.
    CHECK (value_text IS NOT NULL OR value_num IS NOT NULL),
    UNIQUE (complex_id, attr_key, source_name, as_of)
);

CREATE INDEX idx_complex_attribute_lookup
    ON complex_attribute (complex_id, attr_key, as_of);

-- ── 주요 업무지 (§2 '주요 업무지 접근성') ─────────────────────────────
-- 어디를 업무지로 볼지는 도메인 판단이라 사람이 정한다. 종사자수 같은 근거를 함께 적는다.
CREATE TABLE job_center (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,    -- '강남권' '여의도' '광화문' '판교' …
    lat          REAL,
    lon          REAL,
    workers      INTEGER,                 -- 종사자수. 가중치의 근거
    source_name  TEXT,
    source_url   TEXT,
    last_verified TEXT,
    note         TEXT
);

-- 단지 → 업무지 이동시간. **직선거리로 대체하지 않는다** —
-- 통근은 직선거리가 아니라 환승 횟수와 배차가 결정한다.
CREATE TABLE complex_job_access (
    complex_id     INTEGER NOT NULL REFERENCES complex(id) ON DELETE CASCADE,
    job_center_id  INTEGER NOT NULL REFERENCES job_center(id) ON DELETE CASCADE,
    minutes        INTEGER CHECK (minutes IS NULL OR minutes > 0),
    method         TEXT NOT NULL CHECK (method IN ('대중교통','자가','도보','직선근사')),
    as_of          TEXT,
    source_name    TEXT NOT NULL,
    source_url     TEXT,
    verification   TEXT NOT NULL DEFAULT 'NEEDS_VERIFICATION'
                   CHECK (verification IN ('VERIFIED','ESTIMATED','UNKNOWN',
                                           'NEEDS_VERIFICATION')),
    note           TEXT,
    PRIMARY KEY (complex_id, job_center_id, method)
);

-- ── 생활권 (§12 Quality Adjacent Discount 의 기반) ─────────────────────
-- 행정구역이 아니라 **실제 주거 대체관계**다. 인접 관계는 방향이 없다.
CREATE TABLE life_zone (
    key          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    sido         TEXT,
    rationale    TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
    curated_by   TEXT NOT NULL CHECK (length(trim(curated_by)) > 0),
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE life_zone_adjacency (
    zone_a       TEXT NOT NULL REFERENCES life_zone(key) ON DELETE CASCADE,
    zone_b       TEXT NOT NULL REFERENCES life_zone(key) ON DELETE CASCADE,
    travel_min   INTEGER CHECK (travel_min IS NULL OR travel_min >= 0),
    substitution TEXT NOT NULL DEFAULT '보통'
                 CHECK (substitution IN ('강','보통','약')),
    rationale    TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
    -- 대체관계는 방향이 없다. (a,b) 만 저장하고 조회 시 양방향으로 본다.
    PRIMARY KEY (zone_a, zone_b),
    CHECK (zone_a < zone_b)
);

INSERT INTO data_source (key, name, kind, url, note) VALUES
 ('attribute_manual', '단지 속성 (수기 입력)', '수기', NULL,
  '학군·생활권·업무지 접근성. 공공 API 로 나오지 않아 사람이 넣는다. 값마다 출처·확인일·신뢰도가 붙는다');

INSERT INTO engine_version (version, note)
VALUES ('0.12.0', 'Phase 2 — Canonical Data Model: PropertyResolver · 값별 출처 속성 · 업무지 접근성 · 생활권');
