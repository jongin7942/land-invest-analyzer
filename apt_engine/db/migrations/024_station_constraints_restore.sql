-- ═══════════════════════════════════════════════════════════════════════
-- 024 — 023 이 떨어뜨린 제약을 되돌린다
-- ═══════════════════════════════════════════════════════════════════════
--
-- 023 에서 '운영중' 상태를 더하려고 표를 다시 만들었는데, 원래 있던 것들을
-- 같이 지웠다. SQLite 는 CHECK 를 ALTER 로 못 바꿔서 표를 새로 만들어야 하는데,
-- 그때 원본의 제약을 그대로 옮겨 적지 않은 것이 원인이다.
--
--   UNIQUE (project_id, name)      임포터의 ON CONFLICT 가 이걸 쓴다.
--                                  없으니 재수집이 통째로 실패했다:
--                                  "ON CONFLICT clause does not match any
--                                   PRIMARY KEY or UNIQUE constraint"
--   CHECK (개통이면 opened_ym)      개통월 없이 '개통' 이라 적는 것을 막는 규칙.
--                                  transit_analogue 가 개통 전후를 가르기 때문에
--                                  이게 뚫리면 유사사례 측정이 조용히 틀린다.
--   AUTOINCREMENT · ON DELETE CASCADE · idx_station_coord
--
-- '운영중' 은 CHECK 에 걸리지 않는다 — 개통한 것은 확실하되 개통월을 자료가
-- 담고 있지 않다는 뜻이고, 그래서 유사사례 측정에서는 빠진다.

PRAGMA foreign_keys = OFF;

CREATE TABLE transit_station_fixed (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES transit_project(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    lawd_cd          TEXT,
    lat              REAL,
    lon              REAL,

    status           TEXT NOT NULL
                     CHECK (status IN ('계획','예비타당성','기본계획','착공',
                                       '공사중','개통예정','개통','운영중')),
    status_date      TEXT,
    expected_open_ym TEXT,
    opened_ym        TEXT,

    source_name      TEXT,
    source_url       TEXT,
    last_verified    TEXT,
    note             TEXT,
    UNIQUE (project_id, name),
    -- 개통했다고 적으려면 개통월이 있어야 한다. 없으면 '운영중' 이다.
    CHECK (status != '개통' OR opened_ym IS NOT NULL)
);

INSERT INTO transit_station_fixed
    (id, project_id, name, lawd_cd, lat, lon, status, status_date,
     expected_open_ym, opened_ym, source_name, source_url, last_verified, note)
SELECT id, project_id, name, lawd_cd, lat, lon, status, status_date,
       expected_open_ym, opened_ym, source_name, source_url, last_verified, note
  FROM transit_station;

DROP TABLE transit_station;
ALTER TABLE transit_station_fixed RENAME TO transit_station;

CREATE INDEX IF NOT EXISTS idx_station_region ON transit_station (lawd_cd, status);
CREATE INDEX IF NOT EXISTS idx_station_coord  ON transit_station (lat, lon);

PRAGMA foreign_keys = ON;
