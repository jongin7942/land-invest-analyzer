# GAP Matrix — 지시서 81개 항목 대비 현황

작성: 2026-08-31 · 지시서 §81-3 산출물

범례: ✅ 구현됨 · 🟡 부분 · ⬜ 미구현 · 🔴 **데이터가 없어 코드로 해결 불가**

---

## 요약

| 상태 | 개수 | 뜻 |
|---|---:|---|
| ✅ | 24 | 이미 동작 |
| 🟡 | 17 | 뼈대는 있고 확장 필요 |
| ⬜ | 26 | 코드를 만들면 되는 것 |
| 🔴 | 14 | **실거래 이력 데이터가 있어야 시작 가능** |

**🔴 14개가 이 프로젝트의 실제 병목이다.** 지시서 §74 가 요구하는
"데이터 → Feature → Backtest → Feature usefulness → Weight → Ranking" 순서에서
첫 칸이 비어 있다. 아래 §B 참고.

---

## A. 항목별

| § | 요구사항 | 상태 | 현재 위치 / 남은 일 |
|---|---|:--:|---|
| 0 | 코드 audit | ✅ | `docs/architecture_audit.md` |
| 1 | 사용자 관심 편향 금지 · Placebo Test | ⬜ | **최우선.** 익명 ID 랭킹 + 누출 테스트 |
| 2 | 수도권 Universe · PropertyResolver | 🟡 | `complex` 스키마 있음. 학군·업무지접근성·생활권 컬럼 없음. 매칭 26% 미매칭 |
| 3 | 데이터 신뢰도 · SOURCE_CONFLICT | 🟡 | `data_source` `confidence` `verification` 있음. **conflict 이력 테이블 없음** |
| 4 | 가격 8종 분리 | ✅ | `price/` `listing/` |
| 5 | MNTP | ✅ | `price/outlier.py` + `representative.py` (90일 창은 파라미터화 필요) |
| 6 | Actual Buyable Price | 🟡 | 세 값 모두 계산됨. **최종 추천에서 이걸 쓰는 경로가 없음** |
| 7 | Entry Price Engine (Strong/Fair/Wait/Overpriced) | ⬜ | `reverse/` 빈 스텁 |
| 8 | Regime Engine (7국면) | ⬜ | `relative/ratio.py` 에 3국면(상승/하락/횡보)만 |
| 9 | Price Transmission Network | 🔴 | 상관·lead/lag 에 **월별 가격 시계열 5년+ 필요** |
| 10 | Leader-Follower Spread | 🔴 | 〃 |
| 11 | Discount Decomposition · Value Trap | 🔴 | 구조적 할인 판별에 과거 spread 이력 필요 |
| 12 | Quality Adjacent Discount | 🔴 | "백테스트로 검증한다"가 요구사항 자체 |
| 13 | Supply Ratio · Supply Cliff | 🟡 | `catalyst/supply.py` 절대물량만. **stock 대비 비율·4분류·Cliff 없음** |
| 14 | Jeonse Lead · Downside Defense | 🟡 | 신규/갱신 분리됨. **선행성 계산 없음** |
| 15 | 거래량 Flow Stage 6단계 | ⬜ | |
| 16 | Transaction Quality | 🟡 | 층·급매·취소·특수 플래그 있음. **가격 가속도·분산 없음** |
| 17 | Catalyst Alpha 공식 | 🟡 | 단계·실현확률 있음. **priced_in_fraction·economic_impact 없음** |
| 18 | Catalyst Ledger (시점별) | ⬜ | **look-ahead 방지의 핵심** |
| 19 | 재건축 엔진 Bear/Base/Bull | ✅ | `redev/` |
| 20 | Reconstruction Mispricing | ⬜ | 사업성 ÷ 가격반영 |
| 21 | Time Arbitrage | 🟡 | `redev/stage.py` 에 기간·지연위험. **시장 예상기간 추정 없음** |
| 22 | Reconstruction Ablation | ⬜ | |
| 23 | Thesis Survival | ⬜ | |
| 24 | 대출 시점별 versioning | ✅ | `regulation/mortgage.py` + `loan_rule.effective_from/to` |
| 25 | 세금 시점별 versioning | ✅ | `tax/*` + `tax_rule.effective_from/to` |
| 26 | Capital Feasibility Gate | ✅ | `cash/self_capital.py` |
| 27 | 투자금 버킷 9종 | ✅ | `invest/buckets.py` + 백테스트가 버킷별로 돈다 |
| 28 | Capital Utilization Efficiency | 🟡 | `cash_utilization` 있음. **한계효용·기회비용 없음** |
| 29 | Return on Deployable Cash | ✅ | `invest/roe.py` |
| 30 | Capital Frontier · Pareto | 🟡 | `buckets.frontier()` (버킷 간 증감). Pareto 곡선 미구현 |
| 31 | Alternative Purchase Test | ⬜ | |
| 32 | Price/Capital Cohort 2중 랭킹 | ⬜ | |
| 33 | Opportunity Alpha | 🟡 | `kpi.opportunity_alpha`. **실거래 대기** |
| 34 | Ex-post Capital Rank | 🟡 | `kpi.ex_post_capital_rank`. **실거래 대기** |
| 35 | Regret | 🟡 | `kpi.regret`. **실거래 대기** |
| 36 | Recovery Time | 🟡 | `outcome._recovery` + `kpi.recovery_months`. **실거래 대기** |
| 37 | Recovery Quality | 🔴 | 〃 |
| 38 | Recovery Exhaustion | 🔴 | 〃 |
| 39 | Remaining Alpha · Past≠Current | ⬜ | 개념은 `redev/`·`catalyst/` 에 부분 반영 |
| 40 | Discovery Lag · MISSED_WINNER | 🟡 | `outcome._rise_start` + `kpi.discovery_lag`. **실거래 대기** |
| 41 | Winner 4상태 분류 | ✅ | `outcome.classify()` |
| 42 | Winner Recall@K | 🟡 | `kpi.winner_recall_at_k`. **실거래 대기** |
| 43 | False Positive 분석 | 🟡 | `kpi.false_positive_rate` + `precision_at_k`. **실거래 대기** |
| 44 | False Follower | 🟡 | `kpi.false_follower_rate`. **실거래 대기** |
| 45 | Kill Score | ⬜ | 재료(공급·전세·급등·선반영)는 대부분 있음 |
| 46 | Winner vs Survivor 별도학습 | 🔴 | |
| 47 | 2Y/5Y/10Y 별도 랭킹 | 🟡 | `cashflow` 가 기간별 계산은 함. 랭킹 분리 없음 |
| 48 | Absolute/Risk-adj/Asymmetric TOP10 | ⬜ | |
| 49 | Consensus Model 9종 | ⬜ | `scoring/` 빈 스텁 |
| 50 | Score ≠ Confidence | 🟡 | Confidence 개념은 전 계층에 있음. **분리 저장 구조 없음** |
| 51 | Ranking Persistence | ⬜ | |
| 52 | Expected Rank Range | ⬜ | bootstrap |
| 53 | Monte Carlo | 🟡 | Bear/Base/Bull + Stress 4종. **확률분포 시뮬 없음** |
| 54 | Historical Analog Engine | 🔴 | |
| 55 | Walk-forward Backtest | ✅ | `backtest/` 전체. 합성 시장으로 검증. **실거래 대기** |
| 56 | 2022~2023 하락장 테스트 | 🔴 | |
| 57 | Backtest KPI 14종 | ✅ | `backtest/kpi.py` |
| 58 | Investment Lessons DB | ⬜ | 테이블 설계만 하면 됨 |
| 59 | Lessons 20개 seed | ⬜ | |
| 60 | CASH 옵션 | ⬜ | |
| 61 | Rotation Engine | ⬜ | |
| 62 | 최종 TOP10 화면 (26컬럼) | ⬜ | |
| 63 | 단지 상세 (WHY BUY/NOT/PRICED) | ⬜ | `narrative/` 빈 스텁 |
| 64 | 순위변경 설명 | ⬜ | |
| 65 | 탈락 이유 | ⬜ | |
| 66 | 날짜별 snapshot 비덮어쓰기 | 🟡 | 가격·호가·시나리오는 시점별 저장. **랭킹 snapshot 없음** |
| 67 | DATA_MISSING 등 표시 | ✅ | 전 계층 |
| 68 | unit tests 13종 | 🟡 | 704개 중 상당수 해당. 신규 항목(익명성·누출) 없음 |
| 69 | Look-ahead Leakage Test | ✅ | 세 겹(구조·정적·미래삭제 비교) + 누출을 심어 잡히는지 확인 |
| 70 | User Interest Leakage Test | ⬜ | **최우선** |
| 71 | Ablation Test | ⬜ | |
| 72 | Train/Validation/Out-of-time 분리 | ✅ | `windows.assign_splits` + embargo 보고 + 검정력 리포트 |
| 73 | known examples 는 fixture 전용 | ⬜ | 회귀 테스트로 고정 |
| 74 | 데이터→Feature→Backtest→Weight 순서 | 🟡 | `usefulness.py` 가 그 순서를 강제. **실거래가 오면 즉시 돈다** |
| 75 | Explainability (왜 A가 B보다) | ⬜ | |
| 76 | Feature Attribution | ⬜ | SHAP 대신 가법 분해 |
| 77 | 문서 11종 | 🟡 | 3/11 (이 문서 + audit + backtest_methodology) |
| 78 | `rank --cash --horizon --profile` | ⬜ | |
| 79 | Phase 1~10 진행 | 🟡 | Phase 1 완료 |
| 80 | 완료조건 20개 | ⬜ | 현재 6/20 |

---

## B. 🔴 항목이 왜 코드로 해결되지 않는가

지시서 §74:

> 점수부터 만들지 마라. 데이터 → Feature → Historical Backtest →
> Feature usefulness → Weight → Ranking 순서로 간다.

이 순서의 **첫 칸이 비어 있다.**

| 필요한 것 | 왜 | 현재 |
|---|---|---|
| 수도권 아파트 매매 실거래 2015~2026 | MNTP 시계열, 가격전이, Winner 판정, 백테스트 정답 | **0건** |
| 전월세 실거래 같은 기간 | Jeonse Lead, Downside Defense | **0건** |
| K-apt 단지 마스터 | Universe 자체 | **0건** |

수집 코드는 다 있고 사용자 PC 에서 라이브 검증까지 끝났지만,
**data.go.kr 이 이 작업 환경의 네트워크 정책에서 차단**돼 있다.

그래서 이 프로젝트에서 지금 할 수 있는 일은 두 갈래다.

1. **데이터가 없어도 만들 수 있는 것** — 누출 방지 구조, 익명 랭킹, 백테스트 하네스,
   Feature 계산기, Lessons DB, 설명 엔진. 합성 데이터로 전부 테스트 가능하다.
2. **데이터가 와야 하는 것** — 가중치 학습, Winner Recall, Regret, 가격전이 네트워크.

1을 먼저 완성해 두면, 사용자가 수집을 돌린 **그날 바로** 2가 실행된다.
그 반대 순서는 불가능하다.

---

## C. 우선순위 (지시서 §81-4)

| 순위 | 무엇 | 왜 먼저인가 |
|---|---|---|
| **P0** | §1·§69·§70 누출 방지 + 익명 랭킹 + 테스트 | 나중에 넣으면 이미 오염된 모델을 검증할 수 없다. 지시서가 "절대 원칙"으로 지정 |
| **P1** | §55 백테스트 하네스 (as-of 스냅샷 · 정답 계산 · KPI) | 데이터가 오면 즉시 돌아야 한다. 하네스가 Feature 계약을 정의한다 |
| **P2** | §58·§59 Lessons DB + 20개 seed | 가설을 코드에 하드코딩하지 않기 위한 그릇 |
| **P3** | §49 Consensus Model 9종 (heuristic, discovery 전용) | §74 가 허용하는 범위. 백테스트로 가중치를 대체할 자리 |
| **P4** | §7 Entry Price · §45 Kill Score · §23 Thesis Survival | P3 위에서 동작 |
| **P5** | §62~§65 화면·설명 | 마지막 |

P0~P2 는 **데이터 없이 완성 가능**하다. P3 이후는 heuristic 으로 시작해
데이터가 오면 학습값으로 교체한다.
