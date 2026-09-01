-- ═══════════════════════════════════════════════════════════════════════
-- 018 — 토허: 국적 구분 · 대상 판정 · 커버리지 (작업지시서 §1·§2)
-- ═══════════════════════════════════════════════════════════════════════
--
-- **이 마이그레이션이 막으려는 사고 하나**
--
--   내국인이 부평 아파트를 사는데, 외국인 대상 토허구역이라는 이유로
--   후보에서 빠지는 것.
--
-- 2025-08-26 시행된 수도권 외국인 주택 토허는 **대한민국 국적이 없는
-- 개인·외국법인·외국정부** 에게만 적용된다. 내국인 매수와 아무 상관이
-- 없다. 그런데 기존 구조는 `target_scope` 문자열 하나뿐이라, 판정하는
-- 쪽에서 실수하면 그대로 통과한다.
--
-- 그래서 국적 축(`nationality_scope`)을 분리하고, 판정 함수가 매수자
-- 국적을 **반드시 받도록** 만든다.

-- ── 국적·대상 축 ──────────────────────────────────────────────────────
--
-- target_scope 허용값 (지시서 §2)
--   ALL_BUYERS · FOREIGN_ONLY · CORPORATE_ONLY · SPECIFIC_BUYER_TYPE · UNKNOWN
-- **ALL_BUYERS 만** 내국인 투자자의 Hard Gate 에 연결한다.
ALTER TABLE land_permit_zone ADD COLUMN rule_id TEXT;
ALTER TABLE land_permit_zone ADD COLUMN zone_group TEXT;
ALTER TABLE land_permit_zone ADD COLUMN nationality_scope TEXT;   -- NON_KOREAN / ANY / NULL
ALTER TABLE land_permit_zone ADD COLUMN property_scope TEXT;      -- 대상 주택유형
ALTER TABLE land_permit_zone ADD COLUMN parcel_scope TEXT;        -- 필지/구역 한정
ALTER TABLE land_permit_zone ADD COLUMN legal_dong_code TEXT;
ALTER TABLE land_permit_zone ADD COLUMN official_notice_no TEXT;

-- 허가 대상 면적 기준 (㎡). 이 면적 **미만**은 허가 대상이 아니다.
-- 셋을 나눠 두는 이유: 용도지역마다 기준이 다르다.
ALTER TABLE land_permit_zone ADD COLUMN residential_threshold_sqm REAL;
ALTER TABLE land_permit_zone ADD COLUMN commercial_threshold_sqm REAL;
ALTER TABLE land_permit_zone ADD COLUMN green_threshold_sqm REAL;

-- 상태. NEEDS_CHECK 는 "확인 못 했다" 지 "해당 없음" 이 아니다.
ALTER TABLE land_permit_zone ADD COLUMN status TEXT NOT NULL DEFAULT 'NEEDS_CHECK';

-- ── 커버리지 원장 ─────────────────────────────────────────────────────
--
-- **0건인 상태는 '전부 통과' 도 '전부 차단' 도 아니다** (지시서 §2).
--
-- 전부 통과시키면 실거주 의무 구역을 비거주 투자자에게 추천하게 되고,
-- 전부 차단하면 화면이 텅 빈다. 정직한 답은 "아직 확인하지 못했다" 이고,
-- 그걸 말하려면 **무엇을 어디까지 채웠는지** 를 알아야 한다.
--
-- 그래서 지역·범위별로 수집 상태를 따로 기록한다. 이게 없으면
-- "인천 외국인 토허는 다 넣었지만 내국인 일반 토허는 아직" 을
-- 구분해서 말할 수 없다.
CREATE TABLE IF NOT EXISTS permit_coverage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sido           TEXT NOT NULL,
    target_scope   TEXT NOT NULL,
    coverage_status TEXT NOT NULL
                   CHECK (coverage_status IN ('COMPLETE','PARTIAL','INCOMPLETE')),
    checked_on     TEXT,
    source_name    TEXT,
    source_url     TEXT,
    note           TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (sido, target_scope)
);

CREATE INDEX IF NOT EXISTS idx_permit_scope
    ON land_permit_zone(lawd_cd, target_scope, effective_from, effective_to);
