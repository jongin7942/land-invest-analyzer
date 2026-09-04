# RESEARCH_LOG_TW_COMBINE_v0.1 — 상대가격 Mispricing(§35) + 정비사업 옵션(§14) → Terminal Wealth 결합 (2026-09-04)

구현: `apt_engine/invest/exit_price.py`, `tools/combine_tw.py` · 산출: `reports/tw_combined_2026-09-04.{csv,json}` · 점수 가중치 변경: `scoring/weights.py` (`relative` 0.15→0, `redevelopment` 0.08→0).

## 1. 무엇을 결합했나
- **매도가 시나리오**(Bear/Base/Bull)에만 넣었다. 점수에는 넣지 않았다.
- Base = 현재 대표가격 **그대로(명목 무성장)**. §12 Fundamental Exit Price(미래 구매력·전세·상품성)는 아직 없으므로 성장률을 지어내지 않았다. Bear/Bull 배율은 기존 감도 가정(−15%/+15%).
- 상대가격 Mispricing = Follower 집계값 × 신뢰도(VERIFIED 1.0 · PROXY 0.5) → Base·Bull 에만 곱함. FALSE_CHEAP 은 0. Bear 에는 얹지 않음(전달 실패 세계).
- 정비사업 Option Value: 등록부 전 후보가 NOT_CALCULATED → **N/A, 매도가 미반영**(0 확정 아님). 이번 실행에서 옵션이 반영된 후보 0개.
- 시나리오 확률 0.25/0.50/0.25(HEURISTIC) 로 `EXPECTED_TW`, Bear 순이익 = `Wealth Floor`.
- 후보 비교용 표준 프로필: 자기자본 3억 · 비거주(임대, 월세 0) · 5년 · 대출금리 4%(HEURISTIC) · 공시가격 = 매매가×0.65(ESTIMATED) · 세법 초안 규칙 사용(allow_unverified). → 결과 등급 SCENARIO.

## 2. 결과 — 기존 TOP100 후보 100개
- 계산 완료 100 / 상대가격 상승분 반영 74 / 옵션 반영 0.
- **EXPECTED_TW 가 양(+)인 후보: 0개.** 무성장 Base 에서는 취득·매도 비용, 이자, 보유세를 넘는 후보가 없다. TW 순위는 "손실이 가장 작은 순" = **가격이 싼 순**으로 퇴화한다(1위 동두천에이스1차 1.17억, 2위 강화 세광엔리치빌 1.51억 …).
- 이 목록은 **매수 순위로 쓰지 않는다.** 결합 배관은 완성됐지만, 순위를 가르는 것은 Mispricing(최대 +10.6%p, 중앙값 +1%p)이 아니라 빠져 있는 §12 Fundamental Exit Price 다. Mispricing 만으로는 어떤 후보도 비용을 넘지 못한다.
- 상대가격 상승분이 큰 후보(삼성마을 시티프라디움 +10.6%p, 삼성마을센트럴파크 +8.9%p, 부평창보 +4.8%p, 교동마을마북2차 +4.9%p)는 대출 0 가정에서는 TW 상위였으나 4% 금리를 넣자 저가 후보 뒤로 밀렸다 → 레버리지 비용이 Mispricing 효과보다 크다.

## 3. 점수 TOP100 의 변화 (relative·redevelopment 모델 제거)
(재산출 완료 후 아래 표를 채운다 — `reports/top100_before_combine_2026-09-04.json` vs `reports/top100_latest.json`)

## 4. 회귀 예시 — 부평 동아1단지 74㎡ 저층 4.6억 (표준 프로필, 종인님 실제 조건 아님)
| 항목 | 값 |
|---|---|
| 실투자금(표준 LTV·4%) | 1.46억 |
| 매도가 Bear / Base / Bull | 3.91억 / 4.65억 / 5.34억 (Base = 4.6억 × (1 + 0.0099)) |
| 상대가격 상승분 | +0.99%p (Mispricing 0.020 × PROXY 0.5) |
| 정비사업 옵션 | Stage 0 · NOT_CALCULATED → N/A |
| 순이익 Bear / Base / Bull | −1.42억 / −0.68억 / −0.08억 |
| EXPECTED_TW / Wealth Floor | **−0.71억 / −1.42억** |
| Base 세후 IRR | −8.6%/년 |
| 미확인 | 중개보수 부가세(중개사 과세유형) |
읽는 법: "5년 뒤 가격이 지금과 같으면(무성장) 비용·이자로 0.7억을 잃는다" 는 뜻이지 동아의 기대수익이 아니다. 같은 프로필의 모든 후보가 같은 이유로 음수다. 보유 vs 갈아타기(SWITCH_ALPHA)는 종인님 실제 조건(대출·전세·거주·처분시점)이 와야 하며 이 표로 대체하지 않는다.

## 5. 결론과 다음 단계
1. 결합 자체는 완료: `exit_price.build()` 가 Mispricing·Option 을 매도가에 넣고, `scenario.band` → `EXPECTED_TW` 까지 한 경로로 돈다. 점수 이중계산은 제거했다.
2. **가장 큰 구멍은 §12 Exit Price Engine.** 무성장 Base 로는 어떤 순위도 의미가 없다. 다음 라운드 1순위: 시군구 국면·전세·구매력으로 설명되는 Fundamental Exit Price(실측 기반, 확률 분포 포함).
3. 옵션가치가 계산되는 후보가 0 → §14 사업성·확률 데이터 채우기 전에는 옵션이 TW 에 영향을 줄 수 없다.
4. 시나리오 확률·금리·공시가격 비율은 전부 가정 → 결과 등급 SCENARIO 유지.
