-- ═══════════════════════════════════════════════════════════════════════
-- 022 — 규제지역 커버리지 (어느 시도·어느 기간을 전수 확인했는가)
-- ═══════════════════════════════════════════════════════════════════════
--
-- **이 표가 막으려는 사고**
--
--   zone_at() 은 regulation_zone 이 **전역으로** 비었는지만 본다. 한 행이라도
--   넣는 순간, 안 넣은 시군구·안 넣은 기간이 전부 "확인했고 지정 안 됨"(비조정)
--   으로 **단정**된다.
--
--   규제지역은 2016~2023 사이에 열 번 넘게 바뀌었고, 고시마다 대상 시군구가
--   다르다. 서울 2023년치만 넣고 경기 2018년을 조회하면 "비조정" 이 나온다.
--   그러면 취득세 중과(조정 2주택 8%)가 빠지고 LTV 도 완화되어, 실투자금이
--   실제보다 작게 나온다. 틀린 값이 조용히 섞이는 쪽이 '확인 불가' 보다 나쁘다.
--
--   토허에서 같은 문제를 시도 단위 커버리지로 막았는데(regulation/zone.py),
--   규제지역은 **기간**이 핵심이라 시도만으로는 부족하다. "이 시도의 이 기간은
--   고시를 전수 입력했다" 를 명시적으로 선언한다.
--
-- ── 쓰는 법 ────────────────────────────────────────────────────────────
--   커버리지 안 : regulation_zone 에 있으면 지정, 없으면 지정 안 됨(확정)
--   커버리지 밖 : 확인 불가. 취득세 중과도 대출 규제도 '모른다' 로 남는다
--
--   전수 입력을 못 한 기간은 **커버리지를 선언하지 않는 것이 옳다.** 모르면
--   모른다고 두면 되고, 나중에 고시를 채우면서 기간을 넓히면 된다.

CREATE TABLE IF NOT EXISTS regulation_coverage (
    id             INTEGER PRIMARY KEY,
    sido_prefix    TEXT NOT NULL,          -- lawd_cd 앞 2자리: 11 서울 · 28 인천 · 41 경기
    effective_from TEXT NOT NULL,          -- 이 날부터
    effective_to   TEXT,                   -- 이 날까지(포함). 비우면 현재까지
    source_name    TEXT NOT NULL,
    source_url     TEXT,
    last_verified  TEXT,                   -- 비어 있으면 커버리지로 치지 않는다
    note           TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (sido_prefix, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_regulation_coverage
    ON regulation_coverage (sido_prefix, effective_from, effective_to);
