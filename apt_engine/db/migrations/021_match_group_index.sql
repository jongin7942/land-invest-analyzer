-- ═══════════════════════════════════════════════════════════════════════
-- 021 — 매칭 그룹 키 인덱스
-- ═══════════════════════════════════════════════════════════════════════
--
-- **이 인덱스가 없어서 무슨 일이 있었나**
--
--   실거래 1,218만 건에 `match --rebuild` 를 돌렸더니 세 번 연속 몇 시간씩
--   걸리고도 안 끝났다. 마지막에 원인을 잡았다.
--
--   매칭은 (시군구, 단지명, 법정동, 건축년도) 그룹마다 판정한 결과를 그 조합에
--   해당하는 행 전체에 적용한다. 그런데 그 조합으로 행을 찾을 인덱스가 없었다.
--
--   있던 것은 `idx_trade_unmatched (lawd_cd, apt_name) WHERE complex_id IS NULL`
--   하나뿐인데, 이건 **부분 인덱스**라 질의에 `complex_id IS NULL` 이 있어야만
--   쓸 수 있다. 전체 재계산(rebuild)은 이미 붙은 행도 덮어써야 해서 그 조건을
--   못 붙인다. 그래서 옵티마이저가 `idx_trade_region (lawd_cd, deal_ymd)` 로
--   떨어졌고, 그룹 하나를 찾을 때마다 **그 시군구 전체를 훑었다.**
--
--       19,682 그룹 × 시군구당 평균 5.8만 행 ≈ 11억 행 방문
--
--   전용 인덱스를 만들면 커버링 검색이 된다:
--       SEARCH t USING COVERING INDEX idx_trade_group
--           (lawd_cd=? AND apt_name=? AND emd_name=? AND build_year=?)
--
-- ── 부분 인덱스를 지우지 않는 이유 ─────────────────────────────────────
--   `idx_trade_unmatched` 는 증분 매칭(안 붙은 것만 채우기)에서 여전히 가장 좋다.
--   아직 안 붙은 행만 담고 있어 훨씬 작다. 둘 다 둔다.

CREATE INDEX IF NOT EXISTS idx_trade_group
    ON trade (lawd_cd, apt_name, emd_name, build_year);

CREATE INDEX IF NOT EXISTS idx_jeonse_group
    ON jeonse_contract (lawd_cd, apt_name, emd_name, build_year);
