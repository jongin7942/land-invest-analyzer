# RESEARCH_LOG_REDEVELOPMENT_OPTION_v0.1 — 정비사업 Option Value Engine 병합 기록

작성일: 2026-09-04 · 기준문서: `spec/MASTER_SPEC.md` v2026-09-04a · 병합 전 원본: git `b22a2e0`

## 1. 병합된 규칙 (DELTA → MASTER 위치)

| DELTA 항목 | MASTER 위치 | 처리 |
|---|---|---|
| 목적·레이어(CORE 6 → Base → Option → Exit → TW) | §1, §2, §4 | 신설 |
| 호재 문구 직접 가점 금지 목록, UNVERIFIED/NOT_CALCULATED | §3, §14.1 | 기존 §3 금지규칙에 확장 병합 |
| Stage Ladder 0~8 | §14.2 | 신설 + 코드 한글 11단계 매핑표 |
| Option Probability 함수·상태값 | §14.3 | 신설 |
| 사업성 입력·용적률 3시나리오·세대수·일반분양·1:1 테스트 | §14.4 | 신설 |
| 대지지분·현재 용적률·연식의 역할(독립 가점 금지) | §14.4 | 통합(DELTA §23·§24·§25를 한 항목으로) |
| 분담금·공사비 스트레스·사업기간·Delay Cost·Option Decay | §14.5 | 신설 |
| 완료 후 가치·순증가치·Option Value·시나리오 트리·종상향·역세권 특례 | §14.6, §10, §12 | 신설; 대체재 집합은 기존 Future Choice Set 논리에 붙임 |
| 5년 보유 규칙(EXIT_VALUE_AT_YEAR_5) | §12 | 기존 Exit Price Engine 안에 신설 |
| Stage Premium 실증·지역 전이 금지 | §14.7 | 신설 + 현재 실측표 |
| Double Counting 방지, Settlement/Runway 분리 | §14.9, §5, §11 | 기존 §5·§11에 분리 조항 추가 |
| Pure Alpha / Executable | §14.8 → §33 참조 | 기존 규칙(v0.3)과 동일 → **중복 생성 안 함**, 참조만 |
| Data Confidence 4단계 | §14.8, §32 | 기존 §32 measurement_status 체계에 HEURISTIC 추가로 통일 |
| 필수 출력 컬럼 | §14.10 | 신설 + 기존 코드 변수 대응표 |
| Quick Scan 사용법·OPTION_RESEARCH_PRIORITY·Cheap Old 방지 | §14.11, §24, §30 | 신설; Settlement Promotion Gate를 대체하지 않음을 명시 |
| Terminal Wealth 통합·Opportunity Cost·Hold vs Switch | §13 | 기존 §13에 병합. Optionality → OPTION_VALUE |
| 연구 우선순위·백테스트 KPI | §14.13, §17, §19 | 기존 백테스트 시점(2015~2023) 재사용, KPI 4개 추가 |
| 동아1차 예시 | §14.12(요지) + 본 로그 §8 | 본문은 연구로그로 이동 |

## 2. 삭제·통합된 중복 규칙
- DELTA §30(Pure Alpha/Executable)은 MASTER §33과 동일 → 신설하지 않고 §14.8에서 참조.
- DELTA §31(Data Confidence)은 MASTER §32(Missingness-aware)와 같은 체계 → 상태값만 4단계로 통일.
- DELTA §40 백테스트 시점은 MASTER §17과 동일 → KPI만 추가.
- DELTA §23·§24·§25(대지지분·용적률·연식)는 하나의 "입력값의 역할" 항목으로 합침.
- DELTA §2 금지 문구 목록은 MASTER §3에 흡수(별도 절 없음).
- 기존 §13의 한 줄 규칙("정비사업·교통호재 Optionality 직접 가점 금지")은 유지하고 §14를 가리키도록 확장.

## 3. 새 변수·공식
새 변수: `option_stage`, `option_stage_label`, `stage_verification`, `PROJECT_PROBABILITY`/`probability_status`, `FAR_BASE/FAR_POLICY/FAR_UPSIDE`, `NEW_GFA`, `RESIDENTIAL_RATIO`, `AVG_NEW_UNIT_GFA`, `GENERAL_SALE_RATIO`, `ONE_TO_ONE_FEASIBILITY`, `RECONSTRUCTION_ECONOMICS`, `MEMBER_CONTRIBUTION(_PER_UNIT)`, `CONSTRUCTION_COST_BASE/STRESS/SEVERE`, `YEARS_TO_NEXT_STAGE/APPROVAL/COMPLETION`, `DELAY_COST`, `OPTION_DECAY`, `POST_REDEV_LIQUID_EXIT_PRICE`, `NET_REDEVELOPMENT_UPSIDE`, `OPTION_VALUE`, `EXIT_VALUE_AT_YEAR_5`, `UPZONING_OPTION_VALUE`, `STATION_POLICY`, `STAGE_PREMIUM_STATUS`, `option_already_priced_ratio`, `OPTION_RESEARCH_PRIORITY`, `PERSISTENT_CHEAPNESS_RISK`, `OPTION_PROMOTION`, `TW_BASE/TW_PROJECT/TW_UPSIDE/EXPECTED_TW`, `SWITCH_ALPHA`(§13에 정식 수록).

새 공식: §14.4 세대수·일반분양·1:1, §14.5 분담금·Delay·Decay, §14.6 순증가치·Option Value·종상향, §12 EXIT_VALUE_AT_YEAR_5, §13 EXPECTED_TW·SWITCH_ALPHA.

변수 통일(§14.10 표): `Optionality`→`OPTION_VALUE`, `current_far`→`existing_far`, `land_share_m2`→`land_share`, `scenario.KEYS 보수/기준/낙관`→`SEVERE/STRESS/BASE`, `Premium.efficiency`→`option_already_priced_ratio`, `Project.verified/data_grade`→`stage_verification/probability_status`. **코드 식별자는 아직 바꾸지 않았다**(§7 체크리스트) — 바꾸는 순간 참조 전체를 함께 고친다.

## 4. 기존 규칙과 충돌하여 수정·제외한 DELTA 항목
1. **DELTA §16 확률 합산식** `P_policy×ΔTW + P_project×ΔTW + P_completion×ΔTW` — 정책·사업·완공은 포함관계라 DELTA 자신도 "독립확률처럼 중복 합산 금지"라고 했다. 상호배타 말단 노드(합 1)의 시나리오 트리로만 정의했다.
2. **DELTA §33-B 구조적 사업성 승격** — 낮은 용적률·큰 대지·일반분양 가능성으로 승격. MASTER §30 Settlement Promotion Gate와 §33 `NO_CHEAPNESS_PROMOTION`이 더 엄격하므로, 이 조건은 **랭킹 승격이 아니라 Option Deep Dive 연구 우선순위**로만 한정했다.
3. **DELTA §38 동아1차 예시** — MASTER 본문에 특정 단지 상태를 두면 DELTA 병합지시 13(특별대우 금지)과 어긋난다. 요지만 §14.12에 남기고 본문은 이 로그로 옮겼다.
4. **DELTA §25 연식 패널티** — 연식의 음(−) 효과는 기존 CORE(Downside Floor·Exit Liquidity)가 이미 반영한다. Option Engine에서 다시 빼면 이중 감점이라 "CORE에서 한 번만"으로 제한했다.
5. **DELTA §14 대체재 집합** — 기존 §10 Future Choice Set과 같은 논리이므로 별도 엔진을 만들지 않고 §10에 붙였다.
6. **DELTA §12 사업기간** — 기존 코드의 `stage_duration_ref`가 이미 있으므로 그 테이블을 원천으로 지정했다(새 테이블 아님).

## 5. NEEDS_VERIFICATION / UNKNOWN으로 남긴 것
- Stage별 과거 전환율(§14.3): 데이터 없음 → 모든 후보 `probability_status = UNKNOWN`.
- Stage Premium(§14.7): 서울 5단계(표본 7~50) PROXY, 경기 6단계(5~21) PROXY·방향 불일치, 인천 3단계(7~8) PROXY. 가격대·사업유형·초기 용적률 분리 전.
- 역세권 특례 용적률(350%/500%)·조례 시행일·측정 기준점: `STATION_POLICY = POTENTIALLY_APPLICABLE`, 원문 미확인.
- 공사비 BASE/STRESS/SEVERE 기준 단가·기준연도: 기존 코드 배율은 HEURISTIC.
- `RESIDENTIAL_RATIO`, `AVG_NEW_UNIT_GFA`, 공공·임대 의무 배분: 지역·사업유형별 값 미확보.
- 동아1차 현재 용적률 219.5%·대지지분 46.5㎡(≈14.1평): K-apt 원자료 재검증 필요.
- 인천 정비사업 등록부는 148건 중 87건만 일자 보유, 단지 매칭 21건 — Coverage 낮음.
- 경기 등록부의 '예정구역'(Stage 1)은 매칭된 68건 중 24건이지만, 기본계획 반영 여부를 개별 고시로 확인하지 않았다 → Stage 1 등재 0건(보수적으로 Stage 2 이상만 라벨링, 예정구역은 날짜 컬럼 `정비예정구역고시일자`가 있을 때만).

## 6. Double Counting 검사 결과
| 쌍 | 판정 | 조치 |
|---|---|---|
| Option Value ↔ Price Runway | 충돌 가능 | §5 분리 조항. Runway는 구축 그대로의 여력만 |
| Option 뉴스 급등 ↔ Settlement | 충돌 가능 | §11 `EVENT_SPIKE_ONLY` |
| 대지지분·낮은 용적률·연식 ↔ 별도 점수 | 기존 코드 **충돌 있음**: `redev.screening.WEIGHTS = {대지지분 0.45, 용적률여유 0.35, 연식 0.20}`이 독립 점수 | 체크리스트 #2: 점수 폐지, 사업성 입력값으로만 사용 |
| `redev_mispricing` 모델(consensus 가중 0.08) ↔ OPTION_VALUE | 기존 코드 **충돌 있음**: 정비사업 관련 점수가 CORE consensus에 직접 들어감 | 체크리스트 #1: consensus에서 제거하고 TW 레이어로 이동. 이번 라운드에서는 결과 변화 회피를 위해 **미변경**(변경 시 TOP100 재산출·기록) |
| 연식 음(−) 효과 ↔ Downside/Exit | 중복 위험 | CORE에서 한 번만(§14.4) |
| 완료 후 대체재 집합 ↔ Future Choice Set | 같은 집합 | 목적 분리, 신축 전환가치를 FCP 점수에 재투입 금지(§10) |
| 정비사업 미래 구매자 ↔ Buyer Depth | 중복 위험 | Liquid Exit Price 안에서만(§14.9) |
| 학군·역 접근·하락기 방어력(§4.7) ↔ Option Engine | 겹침 없음 | 그대로 유지 |

## 7. 구현 체크리스트 (코드·DB)
우선순위 순. 체크 안 된 것은 미구현.

- [x] `tools/build_option_stage_registry.py` → `rules/option_stage_registry.csv` (2,404건: 공식 등록부 매칭 318건 + 30년 이상 노후 단지 2,086건 Stage 0). 32개 필수 컬럼 전부 존재, 계산 안 된 값은 N/A.
- [ ] **#1 `scoring/weights.py` `redevelopment: 0.08` 제거 + `scoring/models.py` `"redevelopment": [("redev_mispricing", True)]` 폐지** → `redev_mispricing`은 `option_already_priced_ratio`로 개명해 TW 레이어(§14.9)에서만 사용. 변경 시 TOP100 재산출하고 이 로그에 순위 변화 기록.
- [ ] **#2 `redev/screening.py`**: `WEIGHTS`(대지지분/용적률여유/연식) 점수와 `MAX_CURRENT_FAR = 200` 탈락 제거. 대신 §14.4 개발가능 면적·1:1 테스트·`OPTION_RESEARCH_PRIORITY` 산출. `redev_candidate.score`·`rank_in_region` 컬럼은 deprecated.
- [ ] #3 DB: `redevelopment_project`에 `option_stage INTEGER`, `stage_verification TEXT`, `probability_status TEXT`, `station_policy TEXT` 추가; `redev_candidate.current_far → existing_far`, `land_share_m2 → land_share` 개명(마이그레이션 + 참조 `screening.py`, `cli`, `web/app.py` 수정).
- [ ] #4 `redev/stage.py`: `STAGES` 한글 11단계 → `option_stage` 매핑 함수 `to_option_stage()` 추가(§14.2 표), `Duration.months` → `years_to_next_stage/completion` 출력.
- [ ] #5 `redev/far.py`: `resolve()`가 단일 FarBasis 대신 `{BASE, POLICY, UPSIDE}` 세 시나리오를 돌려주도록 확장. `역세권특례`는 `STATION_POLICY == VERIFIED_APPLICABLE`일 때만 POLICY, 아니면 UPSIDE.
- [ ] #6 `redev/feasibility.py`: `general_units/existing_households` → `general_sale_ratio`, 1:1 테스트, `member_price − right_value` → `estimated_member_contribution` 출력 필드 추가.
- [ ] #7 `redev/scenario.py`: `KEYS 보수/기준/낙관` → `SEVERE/STRESS/BASE` 개명(참조: `scenario.variant`, `band`, 테스트 `tests/test_redev_*`), 배율에 `HEURISTIC` 표시.
- [ ] #8 시나리오 트리 TW: `cashflow/timeline.py` 위에 `invest/option_tree.py` 신설 — 말단 5개(BASE/ABANDON/DELAY/PROJECT/COMPLETE) 각각 `timeline.build` 호출, 말단 확률은 `probability_status != UNKNOWN`일 때만 채움. `EXIT_VALUE_AT_YEAR_5` = base + 잔여 옵션(Stage Premium PROXY 사용 시 confidence 하향).
- [ ] #9 `invest/switch_alpha.py`: `HeldAsset.option_value`(N/A 허용) 추가, `hold_case`의 TW에 포함. N/A면 결론 보류 유지.
- [ ] #10 Stage Premium 실증 확장: 가격대·사업유형·초기 용적률로 분리, 표본 ≥ 30 단계만 PROXY→VERIFIED 승격 검토(`tools/measure_*_stages.py`).
- [ ] #11 Stage 전환율: 서울 정보몽땅 733건 시계열로 단계별 전환율·소요기간 분포 산출 → `stage_duration_ref` 갱신.
- [ ] #12 `web/app.py` 단지 화면에 §14.10 컬럼 표시(값 없으면 N/A 그대로).
- [ ] #13 백테스트 KPI 4종 추가(`tools/backtest_*`), Stage 0~1 Failure Case 세트 구성.

## 8. 테스트 결과
- 전체 테스트: **102 passed** (병합 후, 코드 변경은 새 도구 추가뿐이라 기존 결과 불변).
- 기존 TOP100·순위 결과: 변화 없음(consensus 가중치 미변경). 체크리스트 #1·#2 적용 시 바뀔 것이며 그때 기록한다.

## 9. 회귀 결과 — 동아1차 예시 (특별대우 없음, 규칙 그대로)
`rules/option_stage_registry.csv` complex_id 482, 부평 동아1단지:

| 컬럼 | 값 |
|---|---|
| option_stage / label | 0 / PRE_PROJECT |
| stage_verification | NO_OFFICIAL_RECORD (서울·경기·인천 등록부에 없음) |
| existing_far | 219.5% (K-apt 계산값, NEEDS_VERIFICATION) |
| land_share | 46.5㎡ ≈ 14.1평 (NEEDS_VERIFICATION) |
| base/policy/upside_allowed_far | N/A / N/A / N/A (역세권 특례 `POTENTIALLY_APPLICABLE`, 350%는 UPSIDE 시나리오 라벨만) |
| general_sale_ratio, contribution | N/A, NOT_CALCULATED |
| project_probability / status | N/A / UNKNOWN |
| option_value | NOT_CALCULATED (0으로 확정하지 않음) |
| option_research_priority | LOW (Stage 0, 공식 정책·사업 증거 없음) |

해석: 현 규칙에서 동아1차의 정비사업 옵션은 **계산 대상이 아니라 관측 대상**이다. 정비예정구역·역세권 정비기준 고시·주민 절차 진입 중 하나가 공식 확인되면 Stage 1~2로 올라가고 그때 §14.4 사업성 계산을 시작한다.

## 10. 회귀 결과 — 다른 정비사업 후보 (Stage 사다리 매핑, 지역·단계별 1건씩 14건)
| 지역 | Stage | 단지 | 등록부 라벨 | 검증 | existing_far | option_value |
|---|---:|---|---|---|---|---|
| 경기 | 8 | 박달1차한신휴플러스 | 준공 | VERIFIED | 128% | NOT_CALCULATED(신축 전환) |
| 서울 | 8 | 가락현대5차 | 준공인가 | VERIFIED | N/A | NOT_CALCULATED |
| 인천 | 8 | 석남브라운스톤더프라임 | 준공 | VERIFIED | N/A | NOT_CALCULATED |
| 경기 | 7 | e편한세상 시흥 더블스퀘어 | 착공 | VERIFIED | 180% | NOT_CALCULATED |
| 서울 | 7 | 개봉한진 | 착공 | VERIFIED | N/A | NOT_CALCULATED |
| 인천 | 7 | 청학동보 | 착공 | VERIFIED | N/A | NOT_CALCULATED |
| 서울 | 6 | 가락미륭아파트 | 관리처분인가 | VERIFIED | N/A | NOT_CALCULATED |
| 인천 | 6 | 새사미2차아파트 | 사업시행인가(+관리처분 일자) | VERIFIED | 155% | NOT_CALCULATED |
| 경기 | 6 | 미륭아파트 | 관리처분 | PROXY_MATCH | 255% | NOT_CALCULATED |
| 경기 | 5 | 미성아파트 | 사업시행 | VERIFIED | 200% | NOT_CALCULATED |
| 서울 | 5 | 가락1차현대아파트 | 사업시행인가 | VERIFIED | N/A | NOT_CALCULATED |
| 인천 | 5 | 송월 아파트 | 사업시행인가 | VERIFIED | N/A | NOT_CALCULATED |
| 경기 | 4 | 고잔주공6단지 | 조합설립 | VERIFIED | 72% | NOT_CALCULATED |
| 서울 | 4 | 가락쌍용1차 | 조합설립인가 | VERIFIED | N/A | NOT_CALCULATED |

전체 분포: Stage 0 2,086 / 2 33 / 3 69 / 4 91 / 5 31 / 6 29 / 7 23 / 8 42 (Stage 1 = 0건, §5 참조). 검증상태: VERIFIED 273 · PROXY_MATCH 45 · NO_STAGE_EVIDENCE_IN_RECORD 4 · NO_OFFICIAL_RECORD 2,082.
모든 후보에서 `option_value = NOT_CALCULATED` — 확률·분담금·기간 데이터가 없는 상태에서 숫자를 만들지 않는다는 규칙이 그대로 적용된 결과다. Stage ≥ 5 후보는 정비계획 값(세대수·용적률)이 등록부에 있어 §14.4 계산의 첫 대상이다.

## 11. 필요한 데이터
1. 정비사업 단계별 전환율·소요기간(서울 정보몽땅 733건 시계열 → 산출 가능, 경기·인천은 원자료 보강)
2. 역세권 정비 특례 조례 원문(서울·인천·경기 시군별) — 거리 기준·기준점·최대 용적률·시행일
3. 정비계획 확정 단지의 세대수·용적률·임대비율·분담금 추정치(사업시행인가 이상)
4. 지역별 공사비 단가와 기준연도
5. 준공 후 실거래(신축 전환 후 Liquid Exit Price 학습용)
6. K-apt 대지면적·연면적 원자료 검증(현재 `land_area_verified` 플래그)
