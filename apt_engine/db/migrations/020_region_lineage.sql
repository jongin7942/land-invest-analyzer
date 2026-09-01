-- ═══════════════════════════════════════════════════════════════════════
-- 020 — 행정구역 개편 이력 (2026-07-01 인천형 행정체제 개편)
-- ═══════════════════════════════════════════════════════════════════════
--
-- **이 마이그레이션이 막으려는 사고**
--
--   2026-07-01 인천 개편으로 중구(28110)·동구(28140)·서구(28260) 코드가
--   사라졌다. 이 세 구에 걸려 있던 규칙(외국인 토지거래허가)을 신설 구
--   코드로 "옮겨 적으면" 두 가지가 동시에 깨진다.
--
--     ① 옛 코드로 저장된 과거 거래·과거 판정의 근거가 사라진다.
--        2026-03 에 중구 아파트를 산 외국인이 허가 대상이었는지를
--        나중에 되짚을 수 없게 된다.
--     ② 옛 코드를 지우면 그 코드로 들어온 데이터가 고아가 된다.
--
--   그래서 **옮기지 않고 잇는다.** 옛 코드는 is_active=0 으로 남기고,
--   어느 코드가 어느 코드를 승계했는지를 이 표에 적는다. 판정은
--   `regulation/gate.load_rules` 가 이 표를 타고 양방향으로 코드를
--   넓혀서, 계약일 기준으로 그때 유효했던 규칙을 찾는다.
--
-- ── 이 표를 codes_for_ym() 에 절대 연결하지 않는다 ────────────────────
--
--   `regions.LEGACY` 는 비어 있고 그게 의도된 상태다. 국토교통부
--   실거래가 API 는 과거 거래까지 **현재 행정구역 코드로 소급 재편**해서
--   돌려주기 때문이다(regions.py 의 라이브 확인 기록 참조). 수집 코드가
--   이 표를 보고 폐지 코드로 요청하면 그 구간이 통째로 0건이 된다.
--
--   즉 이 표의 용도는 **규칙 판정과 이력 조회** 뿐이고, 수집 대상 코드
--   결정에는 쓰지 않는다.
CREATE TABLE IF NOT EXISTS region_lineage (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    predecessor_lawd_cd TEXT NOT NULL,     -- 개편 전 코드 (region 에 is_active=0 으로 남는다)
    successor_lawd_cd   TEXT NOT NULL,     -- 개편 후 코드
    effective_from      TEXT NOT NULL,     -- 개편 시행일 YYYY-MM-DD
    relation            TEXT NOT NULL
                        CHECK (relation IN ('SPLIT','MERGE','RENAME','ABSORB')),
    -- 승계 범위. 옛 구의 '전부' 가 넘어갔는지 '일부' 인지는 규칙을 그대로
    -- 물려줘도 되는지를 좌우한다. 일부만 넘어갔는데 전부로 취급하면
    -- 지정된 적 없는 땅까지 규제 대상이 된다.
    coverage            TEXT NOT NULL DEFAULT 'PARTIAL'
                        CHECK (coverage IN ('FULL','PARTIAL')),
    source_name         TEXT,
    source_url          TEXT,
    last_verified       TEXT,
    note                TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (predecessor_lawd_cd, successor_lawd_cd, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_region_lineage_pred
    ON region_lineage (predecessor_lawd_cd);
CREATE INDEX IF NOT EXISTS idx_region_lineage_succ
    ON region_lineage (successor_lawd_cd);
