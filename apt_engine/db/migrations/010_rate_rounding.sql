-- 010 · 세율 반올림 자리수 + 대출 규칙 조건 (2026-08-31 검증값 반영)
--
-- 지방세법 제11조제1항제8호 나목(6~9억 점증구간)은 조문 자체가 반올림을 규정한다.
--   "소수점 이하 다섯째 자리에서 반올림하여 소수점 넷째 자리까지 계산한다"
--
-- 7억이면 (7억 × 2 ÷ 3억 − 3) × 1/100 = 0.0166666… 이고, 조문대로 넷째 자리까지
-- 반올림하면 0.0167 이다. 반올림하지 않으면 7억에서 23,333원이 어긋난다.
-- 이 자리수는 세목마다 다르므로(지방교육세는 취득세율의 1/10 이라 한 자리 더 필요)
-- 코드 상수가 아니라 규칙의 속성으로 둔다.
ALTER TABLE tax_rule ADD COLUMN rate_decimals INTEGER;

INSERT INTO engine_version (version, note)
VALUES ('0.9.1', '세율 반올림 자리수(rate_decimals) · 법무사 보수 누진식 · 대출 조건(은행권/수도권)');

-- 업권(은행권/비은행권)에 따라 DSR 한도가 다르다. 프로필에 저장한다.
ALTER TABLE user_profile ADD COLUMN lender_type TEXT NOT NULL DEFAULT '은행';
