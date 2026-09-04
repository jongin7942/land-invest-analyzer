# RESEARCH_LOG_EXIT_PRICE_v0.1 — 5년 뒤 가격 엔진: "왜 오르는가" 를 변수로, Walk-Forward 로 검증 (2026-09-04)

구현: `apt_engine/exitprice/{panel,model,jobs}.py`, `tools/run_exit_price.py`, `tools/fetch_nps_workplaces.py` · 산출: `reports/exit_price_backtest.json`, `rules/exit_price_2026.csv`, `reports/hierarchy_2026.json`.

## 0. 출발점 — 종인님의 가격 이론
집값은 더 많은 사람이 살고 싶어할 때 오른다. 사람들은 ① 직장 가까운 곳 ② 끼리끼리 ③ 수준에 맞는 교육·환경을 원하고, 안 되면 ④ 직장에서 멀어지더라도 끼리끼리 모이는 차선책을 택한다. ⑤ 먼저 자리 잡은 곳(선점)에 인프라가 붙고 일정 거리 안의 사람들이 그쪽으로 소비하러 가며 쏠린다.
이 다섯 가지를 변수로 옮겼다(§1). 통계로 맞추기 전에 **이론 변수가 실제로 설명력을 더하는가**를 같은 검증 규약에서 확인하는 것이 이 로그의 목적이다.

## 1. 이론 → 변수
| 이론 | 변수 | 자료 | 상태 |
|---|---|---|---|
| ① 직장 근접 | `jobs_emd`(법정동 국민연금 가입자, log), `jobs_3km`(3km 합), `jobs_growth5`, `station_km`, `station_planned` | NPS 사업장 내역 2016/2018/2021/2023/2026 스냅샷(수도권 가입자 570만→707만), transit_station | 2016 이전 진입은 2016 스냅샷 PROXY |
| ② 끼리끼리 | `tier`(법정동 ㎡단가 8단계, 진입 시점 재계산), `rel_gu`(시군구 대비 log 상대가격) | price_snapshot | VERIFIED |
| ③ 교육·환경 | `log_academy`(500m 학원 수), `age`, `log_hh` | 경기 학원 좌표(+NEIS 서울·인천 진행 중), K-apt | PROXY(학업성취 없음) |
| ④ 차선책 | `dist_center_km`(1~2급지 중심까지), `dist_tier1_km` | 급지 중심점 | 대리값 |
| ⑤ 선점·쏠림 | `metro_mom5`, `gu_mom5`, `gu_mom1`, `regime` | price_snapshot, §34 국면 | VERIFIED |
| 실수요·공급 | `jeonse_ratio`, `jeonse_mom1`, `supply_recent`(2km 3년 입주), `supply_planned`(2km 향후 2년, leakage 위험), `log_vol`, `own_pct`, `mom1`, `mom3` | jeonse_snapshot, K-apt | VERIFIED/PROXY |

## 2. 검증 규약
진입 매년 6월 2011~2021 · 목표 log(P₊₅/P₀)(±2개월 평균) · 테스트연도 T 학습 = 진입 ≤ T−5 · 결측 행 제외(중간값 채움 금지) · Ridge(λ 0.3/1/3/10) · 성적 = MAE·순위상관 IC·Winner Recall(실제 상위 10%를 예측 상위 20%가 잡는 비율)·예측 상위 10%의 실제 초과수익. 변수군 A(시장) → B(+자기 상태) → C(+이론) → D(+일자리).

## 3. 결과
(실행 결과 채움 예정)

## 4. 계급도
(실행 결과 채움 예정)

## 5. 다음 반복
(실행 결과 채움 예정)
