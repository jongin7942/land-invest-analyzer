-- ═══════════════════════════════════════════════════════════════════════
-- 023 — 역 상태에 '운영중' 을 더한다
-- ═══════════════════════════════════════════════════════════════════════
--
-- **왜 필요한가**
--
--   전국도시철도역사정보 표준데이터에는 역 1,099개의 좌표·노선·주소가 있는데
--   **개통월이 없다.** 그런데 status 는 계획~개통 일곱 가지뿐이라 넣을 자리가
--   없었다.
--
--   '개통' 으로 적으면 import_transit 이 막는다 — opened_ym 없이 개통이라고
--   적지 말라는 규칙이고, 그 규칙은 옳다. transit_analogue 는 개통 전후를
--   갈라서 효과를 재기 때문에, 개통월 없는 역을 '개통' 으로 두면 그 계산이
--   조용히 틀린다.
--
--   그렇다고 '개통예정' 이나 '공사중' 으로 적을 수는 없다. 이 역들은 지금
--   실제로 운영 중이다. 사실이 아닌 것을 적는 것이다.
--
-- ── '운영중' 의 뜻 ──────────────────────────────────────────────────
--   개통한 것은 확실하다. 다만 **개통월을 이 자료가 담고 있지 않다.**
--
--   쓸 수 있는 것   역세권 거리(station_distance), 최근접역
--   쓸 수 없는 것   transit_analogue — 개통 전후를 가르려면 개통월이 필요하다
--
--   개통월을 나중에 확인하면 status 를 '개통' 으로 올리고 opened_ym 을 채운다.
--   그러면 그 역도 유사사례 측정에 들어온다.
--
-- SQLite 는 CHECK 제약을 ALTER 로 못 바꾼다. 표를 다시 만들고 옮긴다.

PRAGMA foreign_keys = OFF;

CREATE TABLE transit_station_new (
    id               INTEGER PRIMARY KEY,
    project_id       INTEGER NOT NULL REFERENCES transit_project(id),
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
    note             TEXT
);

INSERT INTO transit_station_new
    (id, project_id, name, lawd_cd, lat, lon, status, status_date,
     expected_open_ym, opened_ym, source_name, source_url, last_verified, note)
SELECT id, project_id, name, lawd_cd, lat, lon, status, status_date,
       expected_open_ym, opened_ym, source_name, source_url, last_verified, note
  FROM transit_station;

DROP TABLE transit_station;
ALTER TABLE transit_station_new RENAME TO transit_station;

CREATE INDEX IF NOT EXISTS idx_station_region ON transit_station (lawd_cd, status);

PRAGMA foreign_keys = ON;
