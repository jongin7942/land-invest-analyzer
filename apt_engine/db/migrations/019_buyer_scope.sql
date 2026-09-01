-- ═══════════════════════════════════════════════════════════════════════
-- 019 — 토허 적용대상 어휘를 작업지시서에 맞춘다
-- ═══════════════════════════════════════════════════════════════════════
--
-- 기존 `target_scope` 는 CHECK 로 '내국인/외국인/전체' 만 허용한다.
-- 작업지시서는 ALL_BUYERS / FOREIGN_ONLY / CORPORATE_ONLY /
-- SPECIFIC_BUYER_TYPE / UNKNOWN 을 요구한다.
--
-- **기존 컬럼을 바꾸지 않는다.** 이미 들어간 데이터와 그걸 읽는 코드가
-- 있고, CHECK 를 고치려면 테이블을 다시 만들어야 한다. 대신 새 축을
-- 하나 더 두고, 안 채워진 행은 기존 값에서 유도한다.
--
-- 유도 규칙 (gate.buyer_scope_of)
--   외국인 → FOREIGN_ONLY
--   내국인 → ALL_BUYERS      내국인 대상 지정은 내국인 매수자 전부에 걸린다
--   전체   → ALL_BUYERS
ALTER TABLE land_permit_zone ADD COLUMN buyer_scope TEXT
    CHECK (buyer_scope IS NULL OR buyer_scope IN
           ('ALL_BUYERS','FOREIGN_ONLY','CORPORATE_ONLY',
            'SPECIFIC_BUYER_TYPE','UNKNOWN'));
