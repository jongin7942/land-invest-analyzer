# 수도권 아파트 투자 추천 엔진 — MASTER SPEC

업데이트 기준일: 2026-09-04 (v2026-09-04a — 정비사업 Option Value Engine DELTA v0.1 병합, 섹션 번호 재정렬)

> 이 문서가 단일 기준문서(Single Source of Truth)다. 병합 전 원본은 git `b22a2e0`(`spec/MASTER_SPEC.md`, 2026-09-03판)이고, 병합 diff·회귀결과는 `RESEARCH_LOG_REDEVELOPMENT_OPTION_v0.1.md`에 있다. 변경 이력은 §36.

## 1. 최종 목표

사용 가능한 자기자본 X를 기준으로, 실제 실행 가능한 `아파트 × 면적 × 타입/동호조건 × 실제 진입가격 × 평가일` 가운데 5년 후 세후·이자후·비용후 순자산을 가장 크게 만들 투자상품을 찾는다.

핵심 질문은 다음 하나다.

> 지금 이 가격에 사는 것이 같은 자기자본으로 가능한 모든 대안보다 좋은가?

단지 자체의 우수성보다 **현재 가격에서의 투자상품성**을 평가한다. 동일 단지도 진입가격이 다르면 다른 투자상품이다.

재건축·재개발·리모델링 등 정비사업의 가치는 이 목적함수의 **하위 요소**다(§14). 독립적인 정성 가점으로 작동하지 않으며, 검증된 확률 × 순증 Terminal Wealth로만 들어온다.

---

## 2. 실행 순서

1. Investable Universe 생성
2. 토지거래허가/실거주 Hard Gate
3. 자금조달·DSR/LTV·세금·비용 Feasibility Gate
4. 실제 매물/정상체결가격 Asset Availability Gate
5. CORE 투자지표 계산
6. 5년 Future Choice Set / Buyer Depth / Price Runway 계산
7. 정비사업 Option Value Engine — Stage·확률·사업성·분담금·기간 → 시나리오별 ΔTW (§14)
8. Liquid Exit Price 및 Terminal Wealth 추정 (시나리오 트리 확률가중)
9. 동일 자기자본 대안 비교
10. CASH 포함 순위 산출
11. TOP10/TOP20 및 Good Buy 가격 제시

레이어 관계:

```text
CORE 6
    ↓
Base Asset Value (정비사업 미반영 기준가치)
    ↓
Redevelopment / Reconstruction Option Engine (§14)
    ↓
Scenario-specific Liquid Exit Price (§12)
    ↓
Terminal Wealth (§13)
```

### Investable Universe 식별·재사용 규칙

- 후보 기본키는 `법정동 + 단지코드 + 전용면적 + 타입/동호조건 + 실제 진입가격 + 평가일`로 둔다.
- 표시명이 비슷하다는 이유로 서로 다른 단지를 합치지 않고, 같은 단지라도 면적·타입·가격·평가일이 다르면 다른 투자상품으로 취급한다.
- 다른 자기자본 시나리오에서 계산된 Capital Efficiency와 자금효율 등급은 재사용하지 않고 현재 자기자본·대출·세금 조건으로 다시 계산한다.
- Hard Gate를 통과한 뒤에만 CORE와 Terminal Wealth 순위를 계산한다.

---

## 3. 절대 금지 규칙

- 사용자가 많이 언급한 지역/아파트에 Alpha 가점을 주지 않는다.
- `UserMentionCount`, `ResearchFrequency`, `WatchlistStatus`, `ManualInterest`는 FORBIDDEN FEATURES.
- 호재 이름 자체에 점수를 주지 않는다. 실제 가격·수요·소득·거래·전세·구매력 변화로 반영될 때만 인정한다.
- 정비사업 관련 문구 자체에도 점수를 주지 않는다. 다음 표현만으로는 어떤 점수도 올리지 않는다: `재건축 기대`, `재개발 기대`, `역세권 재건축`, `안전진단 추진`, `주민들이 추진 중`, `GTX 수혜 + 재건축`, `종상향 가능`, `용적률 완화 가능`, `정비사업 후보`, `노후계획도시 수혜`. 공식적·정량적 증거가 없으면 `OPTION_STATUS = UNVERIFIED`, `OPTION_VALUE = NOT_CALCULATED`로 둔다(§14.1).
- 토허·실거주 제한은 점수 감점이 아니라 실행 Gate다.
- 현재 호가와 실거래를 혼합하지 않는다.
- 평형·타입을 섞은 연평균 가격을 사용하지 않는다.
- 단일 고가/저가 거래를 정상가격으로 사용하지 않는다.
- 과거 백테스트에서 미래 정보를 사용하지 않는다.
- 서울/경기/인천·신축/재건축 같은 라벨 자체에 보너스를 주지 않는다.
- `UNKNOWN`을 0이나 중간값으로 임의 치환하지 않는다(§32). 0으로 확정하는 것도 임의 치환이다.

---

## 4. 현재 핵심 CORE 6개

### 4.1 Entry Advantage
현재 정상체결가격 대비 얼마나 유리하게 진입하는가.

### 4.2 Buyer Depth
현재보다 높은 가격에서도 실제 구매 가능한 다음 매수자가 얼마나 남는가.

### 4.3 Price Runway
현재가격에서 미래 구매력 천장까지 열려 있는 상승 통로.

### 4.4 Settlement Strength
가격이 한 단계 오른 뒤에도 그 가격에서 거래가 지속되고 하단이 유지되는가.

### 4.5 Downside Floor
전세·실수요·대체가치·공급구조가 가격 하락을 얼마나 방어하는가.

### 4.6 Exit Liquidity
5년 뒤 합리적 매도기간 안에 실제 체결 가능한 가격과 구매자층의 깊이.

나머지 변수는 이 6개를 계산하는 하위 변수로 둔다.

### 4.7 실측으로 유지 중인 하위 변수 (2026-09-04 로컬 엔진 기준)
아래 변수는 이름에 가점을 주는 것이 아니라 **실측된 효과**만큼만 CORE의 하위 변수로 들어간다. 이 DELTA 병합으로 바뀌지 않는다.

| 하위 변수 | 소속 CORE | 실측 근거 | 상태 |
|---|---|---|---|
| 역 접근 드리프트(`station_access_drift`) | Buyer Depth / Exit Liquidity | 거리 밴드별 시군구 대비 연간 드리프트(~500m +0.13%p/년 … 2km 밖 −0.15%p/년), 개통 자체 효과 ≈ 0(117건) | VERIFIED(표본 얇은 밴드 confidence 하향) |
| 학원가 밀도(`academy_density`) | Buyer Depth(학군 수요) | 500m 안 입시계열 학원 수 상위 5%의 시군구 대비 드리프트: 경기 +0.32%p/년(17년 +87% vs 하위 50% +48%) · **서울 +0.38%p/년(17년 +180% vs +111%)** · 인천 상위 20% +0.40%p/년(표본 52). 세 지역 모두 같은 방향 | VERIFIED(2026-09-05, NEIS 19,023 + 경기 33,872) |
| 학업성취도·진학실적 | Buyer Depth(학군 수요) | 초품아(거리)보다 성취도·진학실적 우선 원칙. 데이터 원천 미확보 | UNKNOWN(원천 필요) |
| 하락기 상대 방어력(`crash_resilience`) | Downside Floor | 2021~22 고점 → 2023 저점 낙폭의 시군구 대비 실측. 상승 신호로 쓰지 않음 | VERIFIED |
| 병원·공원·초등학교 거리 | — | 실측 효과 없음 또는 역세권과 중복 → 점수 미반영 | 측정 완료, 미채택 |
| 산업단지 지정 | — | 측정 가능 표본 4건 | UNKNOWN |

정비사업 Option Value는 **7번째 CORE가 아니다.** CORE 6은 정비사업이 없다고 가정한 Base Asset Value를 만들고, Option Value는 그 위의 별도 레이어(§14)에서 시나리오별 Terminal Wealth로만 들어온다. 정비사업 기대가 검증되지 않은 후보의 CORE 점수를 올리지 않는다.

---

## 5. Price Runway 엔진

기존의 단순 저평가를 다음으로 확장한다.

`Total Price Runway = Current Recoverable Gap + Expected Leader Transmission + Expected Buyer Ceiling Expansion - Already Priced In - Replacement Supply Pressure`

### Current Headroom
현재 구매력 천장 - 현재 정상가격

### Ceiling Growth
미래 구매력 천장 - 현재 구매력 천장

### Buyer-Supported Upside
`min(회복가능 상대가격, 구매력 천장가격, 미래 경쟁대체재 허용가격) - 현재 정상가격`

### Price Runway와 Option Value의 분리
Price Runway는 **현재 자산 구조**(구축 그대로)에서 남은 가격여력이다. Option Value는 정비사업 사건이 일어났을 때 추가되는 비대칭 수익이다. 두 값을 합치지 않고, 정비사업 기대를 Runway에 넣지 않는다. 정비사업 기대가 이미 최근 거래가격에 들어 있으면 그것은 `Already Priced In`이며 §14.9의 `option_already_priced_ratio`로 관리한다.

---

## 6. 미래 구매자 깊이

가격별 구매자 수요곡선을 만든다.

예시:
- 4.0억: 100
- 4.5억: 72
- 5.0억: 38
- 5.5억: 12

절대 구매자 수보다 **가격 상승에 따른 구매자 감소속도**를 중요하게 본다.

### Buyer Elasticity
`구매자 감소율 / 가격 상승률`

낮을수록 가격 상승을 잘 버틴다.

### Buyer Replacement Ratio
가격 상승으로 기존 구매자가 탈락했을 때 상위 생활권·갈아타기·외부 유입에서 새 구매자가 얼마나 보충되는지 계산한다.

`신규 유입 구매자 / 가격 상승으로 탈락한 기존 구매자`

---

## 7. 구매자 예산 전이

가격전파를 '대장 상승' 자체가 아니라 **구매자 예산군 이동**으로 모델링한다.

- 지역 내 첫 매수
- 지역 내 갈아타기
- 상위 생활권에서 내려오는 대체수요
- 직장·산업·교통 등으로 새로 유입되는 외부수요

`Buyer Budget Migration Rate`를 계산하고, 실제로 같은 선택집합에 들어가는 아파트끼리만 전이효과를 인정한다.

---

## 8. 입지-면적 교환곡선

구매자는 동일 면적만 비교하지 않는다.

동일 자기자본에서
- 더 좋은 입지 + 작은 평형
- 낮은 입지 + 큰 평형

사이의 실제 선택을 학습한다.

Same-Budget Size Choice와 Future Choice Set에 반영한다.

---

## 9. 전세 엔진

전세가율을 단순 가점으로 쓰지 않는다.

전세의 역할을 세 가지로 분리한다.

1. 하방 방어력
2. 전세→매매 전환 가능성
3. 미래 구매자의 자기자본 형성

### Rent-to-Buy Pressure
`전세 유지력 × 매매-전세 Gap 축소 × 대출 가능성`

전세가격 방향·거래량·신규 공급·갱신/신규계약을 함께 본다.

---

## 10. Future Choice Set

5년 뒤 매수자가 우리 아파트와 동시에 비교할 수 있는 경쟁상품 집합을 예측한다.

신규 공급을 단순 악재로 처리하지 않는다.

- 주변 신축이 매우 비싸게 공급되어 생활권 가격천장을 올리면 기존 구축에 긍정적일 수 있다.
- 비슷하거나 더 좋은 상품이 비슷한 가격에 대량 공급되면 직접적인 대체재가 되어 부정적이다.

향후 `Future Competitive Position`으로 수치화한다.

### Future Competitive Position 적용 규칙

- FCP는 기존 CORE 지표의 단순 재조합이 아니라, 구체적인 미래 경쟁상품과의 상대평가로 계산한다.
- 실제 Future Choice Set이 구성되지 않았거나 경쟁상품 근거가 부족하면 FCP는 `N/A`로 두고 랭킹 점수에 반영하지 않는다.
- Buyer Pool, 공급위험, Exit Liquidity, Price Stretch 등 CORE 입력값을 FCP에서 그대로 다시 사용해 이중 가중하지 않는다.
- Confidence는 자산가치 보너스가 아니라 측정오차 확대, 보수적 할인, 결과 신뢰구간에 사용한다.

### 정비사업 완료 후 대체재 집합
정비사업 완료 후 가격(§14.6 `POST_REDEV_LIQUID_EXIT_PRICE`)의 비교집합은 이 Future Choice Set 논리를 그대로 쓴다: ① 동일 생활권 신축 ② 인접 상위 생활권 준신축 ③ 동일 역세권 신축 ④ 미래 입주시점 경쟁 신축 ⑤ 동일 평형·동일 총액 구매자 선택집합. 주변 최고가를 단순 복사하지 않는다. 같은 대체재 집합을 FCP와 Option Engine에서 각각 다른 목적(현 자산의 경쟁위치 vs 신축 전환 후 가격)으로 쓰되, 신축 전환 가치를 FCP 점수에 다시 넣지 않는다.

---

## 11. Settlement Strength

단순 거래량 상승보다 가격 정착을 본다.

### Movement Stage
0 정체
1 거래량 증가
2 가격하단 상승
2.5 새 가격대 정착
3 중위가격 상승
4 시장 전체 재평가

목표 진입구간은 주로 2~2.5 단계다.

### Breakout Strength
이전 가격대를 얼마나 빠르게 넘어서는가.

### Price Holding Strength
올라간 가격에서 다시 내려오지 않고 유지되는가.

분류:
- 돌파↑ 유지↑ : 진짜 재평가
- 돌파↑ 유지↓ : 가짜 돌파
- 돌파↓ 유지↑ : Quiet Compounder
- 돌파↓ 유지↓ : Value Trap

### 정비사업 뉴스와 Settlement
정비사업 기대감으로 가격이 한 번 급등했다고 Settlement가 된 것이 아니다. Settlement 판정 기준은 그대로다: 반복 정상거래, 가격 하단 이동, Median 이동, 충분한 거래량, 취소/직거래 이상치 제거. 옵션 뉴스 뒤의 단일 고가 거래는 `SETTLEMENT = UNKNOWN` 또는 `EVENT_SPIKE_ONLY`로 분류하고 정착 증거로 쓰지 않는다.

---

## 12. Exit Price Engine

5년 후 가격 하나를 찍지 않는다.

- 하락/정체
- 보수
- 기준
- 강세

각 시나리오별 확률을 둔다.

### Fundamental Exit Price
미래 구매력·전세·상품성으로 설명 가능한 가격.

### Market Exit Price
시장 과열/침체까지 반영한 예상 거래가격.

### Liquid Exit Price
5년 후 정상적인 매도기간 안에 체결될 가능성이 높은 현실적인 가격.

투자수익 계산 기본값은 Liquid Exit Price를 사용한다.

### Exit Price Engine v0.1 — 가격 이론에서 변수로 (2026-09-04)
5년 뒤 가격을 통계로 먼저 맞추지 않고, **집값이 왜 오르는가**의 인과 사슬을 변수로 옮긴 뒤 Walk-Forward로 검증한다.

| 이론(수요의 뿌리) | 변수 | 자료 | 상태 |
|---|---|---|---|
| 직장과 가까운 곳 | 법정동 일자리 밀도·5년 증감(국민연금 가입자 수), 역 거리, 진입 시점 공표된 미개통 역 | NPS 사업장 내역(수집 중), transit_station | 역 = VERIFIED, 일자리 = 수집 중 |
| 끼리끼리(수준이 맞는 사람들) | 급지 tier(법정동 ㎡단가 8단계, 매 시점 재계산), 시군구 대비 상대가격 | price_snapshot | VERIFIED |
| 수준에 맞는 교육·환경 | 학원 밀도(500m), 연식, 세대수 | 학원 좌표, K-apt | PROXY(학업성취 없음) |
| 차선책(멀지만 끼리끼리) | 상위 급지 중심(선점지)까지 거리, 1급지까지 거리 | 급지 중심점 | VERIFIED(대리) |
| 선점·쏠림 | 시군구·수도권 5년 모멘텀(상위지 상승의 전파), 시군구 국면 | price_snapshot, §34 Regime | VERIFIED |
| 실수요 하한·공급 | 전세가율·전세 1년 변화, 2km 내 최근 3년 입주·향후 2년 입주(leakage 위험 표시), 거래량, 자기 가격 백분위 | jeonse_snapshot, K-apt 준공연도 | VERIFIED / PROXY |

검증 규약: 진입 = 매년 6월(2011~2021), 목표 = log(P₊₅/P₀)(±2개월 평균), 테스트연도 T의 학습은 진입 ≤ T−5인 창만(결과가 T 전에 확정). 변수군 A(시장만) → B(+자기 상태) → C(+이론 변수)를 같은 규약으로 비교해 **이론 변수가 실제로 설명력을 더하는지**를 본다. 성적은 MAE·순위상관(IC)·Winner Recall(실제 상위 10%를 예측 상위 20%가 잡는 비율)·예측 상위 10%의 실제 초과수익. Bear/Base/Bull = 예측값 + 잔차 P20/P50/P80. 결측은 중간값으로 채우지 않고 행을 뺀다.

### 아파트 계급도와 계급 상승 조건(= 호재의 정의)
- 급지 tier 사이의 **가격 격차(%)**는 데이터가 정한다(급지별 ㎡단가 중앙값 비율). 격차가 시점마다 안정적인지, 어느 급지에서 벌어지고 좁혀지는지를 기록한다.
- **계급 상승** = 진입 시점의 법정동 급지가 5년 뒤 한 단계 이상 올라감. 상승 전에 관측된 조건(공표된 역 계획, 이후 개통, 2km 내 신축 입주, 학원가 규모, 상위 급지 중심까지 거리, 시군구 모멘텀, 전세가율)별로 `lift = 조건 있을 때 상승률 ÷ 기본 상승률`을 잰다. **lift가 유의하게 1을 넘는 조건만 호재로 인정**하고, 그 확률과 폭으로만 Exit Price·Terminal Wealth에 들어간다(§3 이름 가점 금지 유지).
- 시뮬레이션에서 더 나은 변수·구조가 나오면 이 절을 갱신한다. 실행 결과는 `RESEARCH_LOG_EXIT_PRICE_v0.1.md`.

### 5년 시점 잔여 옵션가치
정비사업 후보의 5년 후 매도가격은 신축가가 아니라 **그 시점 매수자가 당시 Stage를 얼마로 평가하는가**다.

```text
EXIT_VALUE_AT_YEAR_5 =
    Base_Asset_Value_Year5
  + Market_Value_of_Remaining_Option_At_Year5
```

5년 안에 준공되지 않는다고 Option Value를 0으로 만들지 않고, "15년 뒤 신축가" 전체를 5년 투자자에게 귀속시키지도 않는다. `Market_Value_of_Remaining_Option_At_Year5`는 §14.7의 Stage Premium 실측(지역·가격대·사업유형별)으로 추정하며, 실측이 없으면 `PROXY` 또는 `N/A`다.

---

## 13. Terminal Wealth

최종 순위는 가격상승률이 아니라 **5년 후 순자산 증가**로 결정한다.

`Terminal Wealth = 현실적 매도가 - 남은대출 - 양도세 - 매도중개비 - 보유기간 이자 - 취득비용 - 보유비용 - 수리/정비비 + 보유기간 현금흐름 + 미사용 현금 미래가치`

CASH도 하나의 실제 후보로 포함한다.

### 시나리오 확률가중
정비사업 후보는 시나리오별 TW를 따로 계산하고 확률가중한다.

```text
TW_BASE      정비사업 진전 없음(구축 그대로 5년 보유)
TW_PROJECT   사업 진전(§14.6 시나리오 트리의 해당 말단)
TW_UPSIDE    강한 정책/종상향 확정

EXPECTED_TW = Σ(P_scenario × TW_scenario)
```

정비사업이 없는 경쟁단지도 **같은 TW 구조**로 비교한다. 정비사업 후보에만 다른 계산식을 쓰지 않는다.

### Wealth Floor
불리한 시나리오에서 예상되는 5년 후 순자산 하한.

기본 사용자 랭킹은 균형형:
`Expected Terminal Wealth + Wealth Floor + Confidence`

공격형과 방어형은 선택 옵션으로 둔다.

### 위험 프로필

세 프로필 모두 토허·실거주·DSR/LTV·최소 자기자본·비용·실제 매물 Hard Gate를 동일하게 적용한다. 공격성은 Gate 완화가 아니라 Gate 통과 후보 사이의 목적함수 차이다.

- 균형형: Expected Terminal Wealth와 Wealth Floor, Exit Liquidity를 함께 중시
- 공격형: Expected Terminal Wealth와 Price Runway, Settlement Strength를 더 중시하고 하방·환금성 비중을 낮춤
- 고공격형: Price Runway와 상승 정착을 가장 강하게 반영하는 후보 발굴용 프로필. 실제 레버리지·세후 Terminal Wealth 검증 전에는 최종 매수순위로 사용하지 않음

정비사업·교통호재 등 정성 `Optionality`는 직접 가점으로 사용하지 않는다. 정비사업은 §14의 `OPTION_VALUE`(검증된 사업확률·기간·분담금·순증가치의 시나리오별 TW 반영)로만 인정한다. (기존 `Optionality` 변수명은 `OPTION_VALUE`로 통일한다.)

### Opportunity Cost — 기다리는 비용
정비사업 후보는 기다리는 동안 자본이 묶인다. 반드시 다음을 비교한다.

```text
TW_RECONSTRUCTION_CANDIDATE  vs  TW_BEST_NON_RECONSTRUCTION_ALTERNATIVE
```

사업이 성공해도 기다리는 동안 다른 아파트가 더 많이 오르면 좋은 투자가 아니다. 핵심 질문은 §1과 같다: 같은 자기자본으로 더 좋은 선택이 있었는가?

### Hold vs Switch — 이미 보유한 자산
이미 보유한 자산(정비사업 후보 포함)은 매수순위와 별도로 계산한다.

```text
SWITCH_ALPHA = TW_ALTERNATIVE_AFTER_SWITCH_COSTS − TW_HOLD
```

- `TW_HOLD`에는 현재 보유 자산의 `OPTION_VALUE`(§14)를 포함한다. 계산되지 않았으면 `N/A`로 두고 결론을 내지 않는다.
- `TW_ALTERNATIVE_AFTER_SWITCH_COSTS`에는 양도비용·세금·중개보수·기존대출 상환·신규 취득세·신규 중개비·법무비·신규 금융비·수리/이사비·남는 현금 미래가치를 전부 포함한다.
- 다른 후보의 Expected Return이 높다는 이유만으로 갈아타기를 권하지 않는다. 대안은 Settlement Evidence를 통과해야 하고, SWITCH_ALPHA가 거래비용·세금을 다 이긴 뒤 의미 있게 양(+)일 때만 갈아타기 후보다.
- 보유 자산은 스크리닝 대표가가 아니라 **실제 매수가**로, 대안은 **지금 실제로 살 수 있는 가격**으로 넣는다.

### 순위 안정성 출력

단일 점수와 단일 순위만 표시하지 않는다. 가중치·측정오차·시장성향을 흔든 반복 검증을 통해 다음을 함께 출력한다.

- 평균순위
- TOP10 생존율
- TOP5 진입률
- 불리한 경우 순위(P90)

순위 생존율이 낮은 후보는 확정 TOP10과 분리해 경계 후보로 표시한다. 구체적인 경계값은 Walk-Forward 백테스트로 정한다.

---

## 14. 정비사업 Option Value Engine (재건축·재개발·리모델링) — v0.1

### 14.0 목적과 위치
정비사업 가능성을 무시하지도, 이름만 보고 점수를 주지도 않는다. 정비사업은 CORE 점수가 아니라 **확률가중 Option Value**로 평가한다.

- "재건축 가능"이라는 문구 자체에는 점수를 주지 않는다.
- 사업단계가 올라갈수록 사업확률이 바뀐다.
- 용적률·대지지분·일반분양·분담금·사업기간을 계산한다.
- 실제 가치 증가는 Terminal Wealth 시나리오에만 반영한다.
- 같은 정비사업 기대를 Price Runway, Settlement, Optionality 등 여러 항목에서 중복 계산하지 않는다.

정식 원칙:

```text
OPTION_VALUE = PROBABILITY × INCREMENTAL_TERMINAL_WEALTH
             − DELAY / CONTRIBUTION / FINANCING / EXECUTION RISK
```

정확한 질문은 "되면 대박인가"가 아니라 **"현재 가격에 비해, 사업이 진전될 확률 × 진전됐을 때의 순증가치가 얼마인가"**다.

### 14.1 검증되지 않은 옵션
공식적·정량적 증거가 없으면:

```text
OPTION_STATUS = UNVERIFIED
OPTION_VALUE  = NOT_CALCULATED
```

0점으로 확정하지 않는다. `UNKNOWN`을 0이나 중간값으로 치환하지 않는다(§3, §32).

### 14.2 Option Stage Ladder
모든 재건축·재개발·리모델링 후보는 같은 사다리를 쓴다. 코드의 한글 단계(`redev.stage.STAGES`)는 아래 `option_stage`로 매핑해 저장한다.

| option_stage | option_stage_label | 진입 기준(예) | 코드 한글 단계 매핑 |
|---:|---|---|---|
| 0 | PRE_PROJECT | 공식 절차 없음. 노후·주민 관심·역세권·용적률 완화 가능성·정비구역 미지정 | 미지정 |
| 1 | POLICY_ELIGIBLE | 공식 역세권 정비대상 범위 포함, 도시기본계획·정비기본계획 반영, 노후계획도시 대상, 공식 용도지역 상향 검토, 지자체가 정비 가능 대상으로 명시, 정비예정구역 | (신설) 정비예정구역 |
| 2 | EARLY_PROJECT | 주민설명회, 추진 준비위, 동의서 모집, 안전진단 신청, 정비계획 입안 제안, 신탁방식 사업제안 | 예비안전진단·정밀안전진단(신청/진행) |
| 3 | FORMAL_ENTRY | 정비계획 입안, 안전진단 통과, 정비구역 지정, 추진위원회 승인 | 정비구역지정·추진위원회 |
| 4 | OPERATOR_FORMED | 조합설립인가, 사업시행자 지정, 신탁 사업시행자 지정 | 조합설립 |
| 5 | PROJECT_APPROVED | 사업시행인가, 건축계획 확정, 세대수·평형·분담금 추정 가능 | 사업시행인가 |
| 6 | DISPOSITION_APPROVED | 관리처분인가, 분양신청 완료 | 관리처분인가 |
| 7 | CONSTRUCTION | 이주·철거·착공 | 이주철거·착공 |
| 8 | NEAR_COMPLETE | 준공·입주 임박 또는 완료. Option Value가 아니라 신축 자산가치로 전환 | 준공 |

- Stage 1은 단순 언론기사보다 공식 고시·계획·조례를 우선한다.
- Stage 2는 주민 카페·중개업소 말만으로 승격하지 않는다.
- Stage 0에서는 사업성 계산을 할 수 있어도 사업확률을 높게 두지 않는다.
- 각 Stage에는 `stage_verification = VERIFIED | PROXY | HEURISTIC | UNKNOWN`과 출처·일자를 붙인다. 공식 등록부(서울 정보몽땅, 경기데이터드림 추진현황, 인천 renewal)에서 확인되면 VERIFIED, 단지-사업 매칭이 불확실하면 PROXY_MATCH.

### 14.3 Option Probability
Stage 숫자를 그대로 확률로 쓰지 않는다.

```text
PROJECT_PROBABILITY = f(
    stage,
    regulatory_feasibility,        # 규제·용도지역·조례
    physical_feasibility,          # 대지·용적률·구조
    economic_feasibility,          # 일반분양·분담금·비례율
    resident_alignment,            # 동의율·갈등
    financing_feasibility,         # 시공사·신탁·금융
    historical_stage_conversion_rate   # 과거 단계별 전환율(학습)
)
```

가능하면 실제 과거 정비사업의 **단계별 전환율**을 학습한다. 데이터가 없으면 정밀한 확률을 지어내지 않고 `probability_status = VERIFIED | PROXY | HEURISTIC | UNKNOWN`을 값과 함께 저장한다. 현재(2026-09-04) 전환율 데이터는 없으므로 모든 후보의 `probability_status = UNKNOWN`이다.

### 14.4 사업성 계산
정비사업의 핵심은 "오래됐다"가 아니라 **실제 신축 가능한 면적과 일반분양 여력**이다.

최소 입력 (기존 코드 변수와의 대응은 §14.10):

```text
site_area                 대지면적
existing_households       기존 세대수
existing_floor_area       기존 연면적
existing_far              현재 용적률
land_share_per_unit       세대당 대지지분
allowed_far_base          현행 허용 용적률
allowed_far_policy        정책·조례상 완화 용적률
expected_new_gfa          신축 연면적
expected_new_households   신축 세대수
expected_member_units     조합원 분양
expected_general_sale_units   일반분양
```

#### 용적률 시나리오 (단일값 금지)
```text
FAR_BASE    현행 도시계획·현행 허용 용적률 (코드 far.KINDS: 조례 / 법정상한)
FAR_POLICY  공식 정책·조례상 적용 가능한 완화 (역세권특례 등, STATION_POLICY=VERIFIED_APPLICABLE일 때)
FAR_UPSIDE  추가 종상향·역세권 특례 등이 실제 확정될 경우
```
정비계획에 용적률이 확정되어 있으면(far.KINDS `정비계획`) 그 값이 해당 시나리오의 확정값이다. UPSIDE는 공식 근거가 없으면 확률을 부여하지 않는다.

#### 세대수 (정비계획 값이 있으면 그것을 우선)
```text
NEW_GFA         = SITE_AREA × ALLOWED_FAR
RESIDENTIAL_GFA = NEW_GFA × RESIDENTIAL_RATIO
NEW_HOUSEHOLDS  = RESIDENTIAL_GFA / AVG_NEW_UNIT_GFA
```

#### 일반분양 여력
```text
GENERAL_SALE_UNITS = NEW_HOUSEHOLDS − MEMBER_ALLOCATION − PUBLIC/RENTAL_ALLOCATION − OTHER_REQUIRED_ALLOCATION
GENERAL_SALE_RATIO = GENERAL_SALE_UNITS / EXISTING_HOUSEHOLDS
```
일반분양이 적거나 음수이면 "재건축 가능성"과 "재건축 경제성"을 분리해 표시한다.

#### 1:1 재건축 테스트 (필수)
```text
ONE_TO_ONE_FEASIBILITY = NEW_MEMBER_CAPACITY >= EXISTING_MEMBER_DEMAND
```
기존 조합원조차 수용하지 못하면 `RECONSTRUCTION_ECONOMICS = STRUCTURALLY_WEAK`. 단, 종상향·완화 시나리오에서는 다시 계산한다.

#### 입력값의 역할 — 독립 가점 금지
- **대지지분**은 독립 호재점수가 아니다. `LAND_SHARE → MEMBER_RIGHT → DEVELOPMENT_CAPACITY → CONTRIBUTION → OPTION_VALUE` 경로의 입력값일 뿐이며 별도 점수로 다시 더하지 않는다.
- **현재 용적률이 낮다**는 사실도 독립 가점하지 않는다. `EXISTING_FAR + ALLOWED_FAR + SITE_AREA + MEMBER_COUNT`로 추가 개발가능 면적을 계산한다.
- **연식(노후도)**은 독립 상승요인이 아니다. 양(+)으로는 정비사업 Eligibility에만, 음(−)으로는 상품성 저하·수선비·전세 경쟁력 저하·Buyer Pool 감소로 작동한다. 음의 효과는 Downside Floor·Exit Liquidity(CORE)에서 이미 반영하므로 Option Engine에서 다시 빼지 않는다. 정비사업 확률이 낮은 오래된 아파트는 결과적으로 패널티가 될 수 있다.

### 14.5 분담금·공사비·기간·지연
#### 분담금 Engine
```text
TOTAL_PROJECT_COST, GENERAL_SALE_REVENUE, OTHER_REVENUE,
MEMBER_CONTRIBUTION_TOTAL, MEMBER_CONTRIBUTION_PER_UNIT
MEMBER_CONTRIBUTION = NEW_UNIT_VALUE − MEMBER_RIGHT_VALUE     (사업 전체 수지와 교차검증)
```
포함 비용: 공사비, 금융비, 설계비, 조합운영비, 철거비, 각종 부담금, 이주비 금융비용, 예비비, 사업지연 비용.

#### 공사비 Stress Test (최소 3개)
```text
CONSTRUCTION_COST_BASE / CONSTRUCTION_COST_STRESS / CONSTRUCTION_COST_SEVERE
```
공사비 상승에 따른 분담금 증가를 계산한다. (기존 `redev.scenario.KEYS = 보수/기준/낙관`은 이 세 단계로 통일: 낙관→BASE, 기준→STRESS, 보수→SEVERE. 기존 배율은 관측치가 아닌 감도 가정이므로 `HEURISTIC`으로 표시한다.)

#### 사업기간 Engine
```text
YEARS_TO_NEXT_STAGE / YEARS_TO_APPROVAL / YEARS_TO_COMPLETION
```
완공까지 15년 걸리는 5억과 5년 걸리는 5억의 미래가치를 같게 보지 않는다. 기존 `stage_duration_ref`(단계별 소요기간 참조)를 이 세 변수의 원천으로 쓴다.

#### Delay Cost
```text
DELAY_COST = Financing Cost + Opportunity Cost + Additional Maintenance + Construction Inflation + Regulatory Risk
```
사업기간이 늘수록 Option Value를 할인한다.

#### Option Decay
진전이 없는 구축은 Option Value가 시간이 지나며 줄 수 있다.
```text
OPTION_DECAY = No_Stage_Progress + Rising_Construction_Cost + Resident_Disagreement + Policy_Reversal + New_Supply_Competition
```
장기간 Stage 0~1에 머물면 `AGING_DISCOUNT > OPTION_PREMIUM`이 될 수 있다. 기존 코드의 정체 판정(`STALL_MONTHS = 36`)은 `No_Stage_Progress`의 입력이다.

### 14.6 완료 후 가치 · 순증가치 · Option Value
#### 완료 후 가격
`POST_REDEV_LIQUID_EXIT_PRICE`: §10의 대체재 집합 5종으로 산출한 **실제 매도 가능한** Liquid Exit Price. 평균 호가·주변 최고가 복사 금지.

#### 순증가치
```text
NET_REDEVELOPMENT_UPSIDE =
    Post_Project_Liquid_Exit_Value
  − Base_Case_Liquid_Exit_Value
  − Member_Contribution
  − Extra_Taxes
  − Financing_Cost
  − Delay_Cost
  − Required_Cash_Infusion
```
`신축가 − 현재가`로 계산하지 않는다.

#### Option Value
```text
OPTION_VALUE = Σ[ Scenario_Probability × Net_Incremental_Terminal_Wealth ]
```
정책·사업·완공은 포함관계이므로 독립확률처럼 중복 합산하지 않는다. 시나리오 트리의 **말단 노드 확률(상호배타, 합 1)**로 구현한다.

#### 권장 Scenario Tree
```text
BASE
│
├─ 정책 변화 없음
│   └─ 기존 구축으로 5년 보유                          → TW_BASE
│
└─ 정책/계획 진전
    │
    ├─ 사업 중단
    │   └─ 노후 구축 상태 (+ Option Decay)             → TW_ABANDON
    │
    └─ 사업 공식 진입
        │
        ├─ 지연                                       → TW_DELAY
        │
        └─ 정상 진행
            │
            ├─ 5년 내 미완공 (잔여 옵션가치로 매도)     → TW_PROJECT
            │
            └─ 완공 또는 가치 상당부분 선반영           → TW_COMPLETE
```
각 말단마다 §13 Terminal Wealth를 계산한다. 5년 규칙(§12)에 따라 미완공 말단의 매도가는 `EXIT_VALUE_AT_YEAR_5`다.

#### 종상향 Option (별도 Branch)
```text
예: CURRENT_FAR 220% / BASE_ALLOWED_FAR 250% / UPZONING_ALLOWED_FAR 350%
BASE_REDEV_VALUE, UPZONING_REDEV_VALUE 를 따로 계산
UPZONING_OPTION_VALUE = P(UPZONING) × (UPZONING_TW − BASE_TW)
```
"350%가 될 수도 있다"는 말만으로 P를 부여하지 않는다.

#### 역세권 특례
역세권이라는 이유만으로 완화를 적용하지 않는다. 반드시 확인: 역세권 정의, 거리 기준, 측정 기준점(단지 경계/중심), 대상 용도지역, 적용 가능한 사업유형, 공공기여, 임대주택 의무, 최대 허용 용적률, 조례 시행일, 해당 필지 적용 여부.
```text
STATION_POLICY = VERIFIED_APPLICABLE | VERIFIED_NOT_APPLICABLE | POTENTIALLY_APPLICABLE | UNKNOWN
```
`VERIFIED_APPLICABLE`일 때만 FAR_POLICY에 들어가고, `POTENTIALLY_APPLICABLE`은 FAR_UPSIDE Branch에만 둔다.

### 14.7 Stage Premium 실증
Stage별 가격 프리미엄은 가능하면 실증으로 학습한다. 각 전환(PRE_PROJECT → FORMAL_ENTRY → OPERATOR → APPROVAL → DISPOSITION → CONSTRUCTION) 전후의 `단지/시군구`, `단지/생활권`, `단지/대체재` **상대가격** 변화를 측정한다. 단순 상승률은 금지(시장 전체 효과 제거).

#### 지역 간 전이 금지
서울에서 측정한 Stage Premium을 인천·경기에 그대로 적용하지 않는다. `REGION, PRICE_TIER, PROJECT_TYPE, INITIAL_FAR, LAND_SHARE, MARKET_CYCLE`로 분리하고, 표본이 부족하면 `STAGE_PREMIUM_STATUS = PROXY`.

#### 현재 실측 상태 (2026-09-04, 시군구 중앙값 대비, ±12개월 창)
| 지역 | 단계 | 표본 | 중앙값 Δ | 상태 |
|---|---|---:|---:|---|
| 서울 | 추진위 승인 | 29 | +4.6% | PROXY(표본 소) |
| 서울 | 조합설립 | 50 | +1.2% | PROXY |
| 서울 | 사업시행인가 | 27 | +3.0% | PROXY |
| 서울 | 관리처분 | 18 | +2.9% | PROXY |
| 서울 | 착공 | 7 | +7.3% | PROXY(표본 극소) |
| 경기 | 정비구역지정/추진위/안전진단/관리처분/착공/준공 | 5~21 | −6%~+1% | PROXY(표본 극소, 방향 불일치) |
| 인천 | 정비구역지정/추진위/조합설립 | 7~8 | −6%~+20% | PROXY(표본 극소) |
어느 값도 확정 계수로 쓰지 않는다. 가격대·사업유형·초기 용적률 분리 전이라 `STAGE_PREMIUM_STATUS = PROXY`다.

### 14.8 Pure Alpha / Executable · Data Confidence
- 토허·실거주 의무가 있는 정비사업 후보도 Universe에서 삭제하지 않는다. `Pure Alpha = IN_UNIVERSE`, `Executable = BLOCKED`. 정비사업 가치가 아무리 높아도 Hard Gate(§16)를 통과시키지 않는다.
- 모든 주요 입력에 `VERIFIED | PROXY | HEURISTIC | UNKNOWN`을 붙인다. Option Value가 높아도 근거가 약하면 순위에 직접 반영하지 않는다. Confidence는 기대수익을 올리는 점수가 아니라 오차폭을 넓히는 변수다(§32).

### 14.9 Double Counting 방지
다음 구조는 금지한다: `재건축 기대 → Price Runway +20 → Optionality +20 → Future Price +20`.

- 정비사업 효과가 이미 최근 거래가격에 반영됐다면 그것은 Entry/Settlement 가격에 들어 있다. Option Engine은 **현재 가격에 아직 포함되지 않은 미래 순증분**만 계산한다.
- `option_already_priced_ratio = 시장이 얹어 놓은 프리미엄(현재가 − 정비사업 미반영 기준가) / OPTION_VALUE`를 가능하면 추정한다. 기존 코드 `redev.naked.Premium(implied, expected_net, efficiency)`이 이 변수의 원천이며, `efficiency`의 역수 개념을 `option_already_priced_ratio`로 통일한다.
- Price Runway(§5) ↔ Option Value: 분리, 합산 금지.
- Settlement(§11) ↔ Option 뉴스 급등: 급등은 정착이 아님.
- 대지지분·낮은 용적률·연식: 사업성 입력값일 뿐, 별도 점수 금지(§14.4).
- 연식의 음(−) 효과: CORE(Downside/Exit)에서 한 번만.
- Future Choice Set(§10) ↔ 완료 후 대체재 집합: 같은 집합을 다른 목적으로 쓰되 신축 전환가치를 FCP 점수에 재투입 금지.
- Buyer Depth(§6): 정비사업으로 늘어나는 미래 구매자는 완료 후 Liquid Exit Price 안에 이미 들어 있으므로 Buyer Depth 점수에 다시 넣지 않는다.

### 14.10 필수 출력 컬럼과 코드 변수 대응
정비사업 후보별 최소 출력(`rules/option_stage_registry.csv` / 향후 `redev_option` 테이블):

```text
project_type, option_stage, option_stage_label, stage_verification,
existing_far, existing_households, site_area, land_share,
base_allowed_far, policy_allowed_far, upside_allowed_far,
estimated_new_households, member_households, general_sale_units, general_sale_ratio,
estimated_member_contribution, contribution_status,
years_to_next_stage, years_to_completion,
project_probability, probability_status,
base_case_liquid_exit, project_case_liquid_exit, upside_case_liquid_exit,
base_terminal_wealth, project_terminal_wealth, upside_terminal_wealth,
net_project_upside, option_value, option_already_priced_ratio,
option_data_confidence, option_research_status, option_research_priority
```

기존 코드 변수와의 정규화(같은 뜻은 하나로):

| MASTER 변수 | 기존 코드 | 처리 |
|---|---|---|
| `option_stage` 0~8 | `redev.stage.STAGES` 한글 11단계, `redevelopment_project.stage` | 매핑표(§14.2) 추가, 한글 라벨은 `stage_label_kr`로 보존 |
| `existing_far` | `redev_candidate.current_far`, `screening.Candidate.current_far` | `existing_far`로 통일 |
| `land_share` | `redev_candidate.land_share_m2` | `land_share`(㎡)로 통일 |
| `base/policy/upside_allowed_far` | `redev.far.KINDS`(정비계획/역세권특례/조례/법정상한), `redevelopment_project.planned_far` | KINDS→시나리오 매핑: 조례·법정상한→BASE, 역세권특례→POLICY(검증 시)/UPSIDE, 정비계획→해당 시나리오 확정값 |
| `estimated_new_households / member_households / general_sale_units` | `feasibility.Feasibility.new_units / member_units / general_units` | 이름만 통일 |
| `estimated_member_contribution` | `Feasibility.member_price − right_value`, `proportion_rate`(비례율) | 비례율은 보조 출력으로 유지 |
| `CONSTRUCTION_COST_BASE/STRESS/SEVERE` | `redev.scenario.KEYS`(보수/기준/낙관) | 낙관→BASE, 기준→STRESS, 보수→SEVERE, 배율 `HEURISTIC` |
| `years_to_*` | `redev.stage.Duration.months`, `stage_duration_ref` | 개월→년 환산 |
| `option_already_priced_ratio` | `redev.naked.Premium.implied / efficiency` | §14.9 |
| `option_value` | (기존) `Optionality`, `redev_mispricing` 모델 점수 | `Optionality`는 폐기·통일. `redev_mispricing`은 consensus 점수에서 제거 예정(§35 체크리스트) |
| `probability_status / stage_verification` | `Project.verified`, `Project.data_grade` | 4단계 상태값으로 통일 |

### 14.11 Quick Scan에서의 사용
642 → 수천 개 Universe의 Quick Scan에서는 정밀 Option Value를 전부 계산하지 않는다. 다음만 **Option Deep Dive 연구대상**으로 올린다(랭킹 승격이 아니다).

- A. 사업단계 증거: Stage 2 이상이 공식 확인됨
- B. 구조적 사업성: 낮은 기존 용적률 + 충분한 대지 + 의미 있는 일반분양 가능성 — 실제 계산으로 확인된 경우만
- C. 정책 Option: 공식 정책 변경으로 용적률·용도지역 변경 가능성이 구체화됨

출력: `OPTION_RESEARCH_PRIORITY = HIGH | MEDIUM | LOW | NONE`. 이 값은 연구 순서일 뿐 점수·순위에 들어가지 않으며, §30의 Settlement Promotion Gate와 §33의 `NO_CHEAPNESS_PROMOTION`을 대체하지 않는다.

#### Cheap Old Apartment 방지
싸고 오래됐다는 이유만으로 정비사업 후보를 상위에 올리지 않는다. `Low Price + Old Age + Low Product Quality + No Settlement + No Official Project` 패턴은 `PERSISTENT_CHEAPNESS_RISK = HIGH`, `OPTION_PROMOTION = BLOCK`. Settlement Evidence 규칙(§30, §33)을 그대로 적용한다.

### 14.12 회귀 예시
부평 동아1차 76㎡급은 **회귀 테스트용 예시**일 뿐 특별대우하지 않는다. 모든 수도권 후보에 같은 규칙을 적용한다. 예시 결과와 다른 재건축 후보 10개 이상의 회귀 결과는 `RESEARCH_LOG_REDEVELOPMENT_OPTION_v0.1.md`에 둔다. 요지: `OPTION_STAGE = 0 / PRE_PROJECT`, 현재 용적률 약 219.5%·대지지분 약 14.1평은 `NEEDS_VERIFICATION`, 정비구역 미확인, 역세권 종상향은 `STATION_POLICY = POTENTIALLY_APPLICABLE`(개별 단지 적용 미확정), 350%는 `FAR_UPSIDE` 시나리오로만 존재 → `OPTION_VALUE = UNKNOWN / NOT_CALCULATED`이며 0으로 확정하지 않는다.

### 14.13 연구 우선순위와 백테스트
고도화 순서:
1. 수도권 정비사업 전체 Registry 구축
2. 단지별 현재 Stage 자동 매핑 ← `tools/build_option_stage_registry.py`(v0.1 구현)
3. Stage별 과거 전환율
4. Stage별 상대가격 변화 ← 서울·경기·인천 1차 실측(§14.7, PROXY)
5. 사업기간 분포
6. 기존/허용 용적률
7. 대지지분
8. 일반분양률
9. 분담금
10. 공사비 민감도
11. 준공 후 Liquid Exit Price
12. 5년 보유 시점 Remaining Option Value
13. Terminal Wealth 통합
14. Walk-Forward Backtest

백테스트는 §17의 2015/2017/2019/2021/2023 시점을 그대로 쓰고, 각 시점에 알 수 있었던 정보만 사용한다(미래에 사업이 성공했다는 사실을 과거 평가에 넣지 않는다). 옵션 전용 KPI: `Option Value Calibration`, `Stage Conversion Calibration`, `False Positive Redevelopment Rate`, `Missed Redevelopment Winner Rate`. **Stage 0~1에서 싸 보였지만 5년간 아무 일도 없었던 구축**을 반드시 Failure Case로 포함한다.

---

## 15. Good Buy 가격

적정 매수가를 과거 저점만으로 정하지 않는다.

미래 Liquid Exit Price와 목표수익, 거래비용, 세금, 이자, 실패확률을 반대로 역산한다.

즉:
> 다음 구매자가 받아줄 수 있는 미래가격에서 목표수익과 안전마진을 역산한 현재 매수가

동일 아파트도 매수가에 따라 순위가 실시간으로 달라져야 한다.

정비사업 후보의 Good Buy는 확률가중 `EXPECTED_TW`(§13)에서 역산하되, `OPTION_VALUE = NOT_CALCULATED`인 후보는 `TW_BASE`만으로 역산하고 그 사실을 표시한다.

---

## 16. 토허 Hard Gate

비거주 투자 기본 시나리오에서 국내 일반 토허 + 실거주 의무가 있으면 Executable Ranking에서 제외한다.

Pure Alpha Ranking에는 남겨 정책 변경 Watchlist로 관리한다.

필드:
- Domestic_Torheo
- Foreign_Torheo
- Torheo_Expiry_Date
- ActualResidenceRequired
- ResidenceDeferralEligible
- ProjectSpecificTorheo
- InvestmentExecutable

---

## 17. 백테스트 목표

평가시점 후보 Universe를 고정한 뒤 2015/2017/2019/2021/2023 등에서 Walk-Forward 검증한다.

주요 KPI:
- Winner Recall
- Exit Price Error
- Rank Regret
- Missed Better Alternative
- Feature Survival
- Gate False Negative
- (정비사업, §14.13) Option Value Calibration · Stage Conversion Calibration · False Positive Redevelopment Rate · Missed Redevelopment Winner Rate

'그때 올랐는가'가 아니라 **같은 자기자본으로 더 좋은 선택이 있었는가**를 본다.

---

## 18. 현재 3억원 비거주 — 50개 유니버스 잠정 스크리닝 TOP10

| 평균순위 | 후보 | 스크리닝 입력가격 | TOP10 생존율 | P90 |
|---:|---|---:|---:|---:|
| 1.13 | 상동 한아름마을 삼환·동아·동성 59㎡ | 3.75억 | 100% | 2위 |
| 2.45 | 김포 사우아이파크 59㎡ | 5.20억 | 100% | 4위 |
| 4.90 | 권선 보성·유원 59/60㎡ | 3.725억 | 92% | 9위 |
| 6.98 | 계양 하늘채파크포레 59㎡ | 4.30억 | 79% | 12위 |
| 7.26 | 일산 강촌마을1단지 84㎡ | 6.20억 | 83% | 13위 |
| 8.21 | 부평 동아1차 52㎡ | 3.65억 | 74% | 14위 |
| 8.70 | 평택센트럴자이2단지 59㎡ | 3.00억 | 73% | 14위 |
| 9.48 | 부평 동아1차 76㎡ | 5.30억 | 65% | 16위 |
| 9.99 | 일산 백마5단지 쌍용·한성 70㎡급 | 4.50억 | 60% | 17위 |
| 11.42 | 구월아시아드선수촌2단지 59㎡ | 3.90억 | 48% | 17위 |

※ 위 가격은 목표수익에서 역산한 Good Buy가 아니라 이번 스크리닝의 비교 입력가격이다.
※ 신규 진입한 일산 강촌마을1단지, 평택센트럴자이2단지, 일산 백마5단지는 원자료·자금조달·Terminal Wealth 검증 전까지 검증대기 후보로 표시한다.
※ 이 TOP10은 50개 정성 유니버스의 민감도 분석이며, 전체 수도권 블라인드 Universe와 세후·이자후 Terminal Wealth가 완성되기 전에는 최종 순위로 취급하지 않는다.
※ 실제 실행 가능한 매물가격에 따라 순위는 즉시 달라진다.

### 공격형 프로필 추가 결과

공격형 100회 평균순위 상위 후보는 상동 한아름마을, 권선 보성·유원, 김포 사우아이파크, 평택센트럴자이2단지, 계양 하늘채파크포레, 부평 동아1차 76㎡ 순이다.

고공격형에서는 부평 동아1차 76㎡가 평균 7.50위, TOP10 생존율 80%로 상승했다. 반면 일산 강촌마을1단지 84㎡는 균형형 6.44위에서 고공격형 15.78위로 내려가 방어형 성격이 확인됐다.

세 모델 모두 평균순위 10위 안에 남은 공통 코어는 다음 5개다.

1. 상동 한아름마을 59㎡
2. 권선 보성·유원 59/60㎡
3. 김포 사우아이파크 59㎡
4. 평택센트럴자이2단지 59㎡
5. 계양 하늘채파크포레 59㎡

※ 프로필별 세부 가중치와 전체 순위는 `RESEARCH_LOG_AGGRESSIVE_v0.1.md`에서 관리한다.
※ 공격형 결과도 실제 `SELF_CAPITAL_REQUIRED`, DSR, 세금·비용, Liquid Exit Price와 Terminal Wealth 검증 전에는 연구용이다.

---

## 19. 다음 연구 우선순위

1. **Universe Expansion Engine 구축 — 50개 수동 후보군을 폐기하고 수도권 단지×면적 수천 개에서 자동 후보 생성**
2. Hard Gate 기반 1,000~2,000개 실행가능 후보 Quick Scan
3. 상위 300개 정밀 분석: Buyer Depth / Price Runway / Settlement Strength / Downside Floor / Exit Liquidity
4. 상위 100개 Terminal Wealth 및 Liquid Exit Price 계산
5. 상위 30~50개 실제 매물 검증 및 Good Buy 역산
6. Future Competitive Position / Future Choice Set 정식 모델링
7. Buyer Ceiling Growth 학습
8. 2019 유사가격 30~50개 블라인드 역검증 및 2015/2017/2021/2023 Walk-Forward 확대
9. Exit Price Error / Rank Regret / Winner Recall 백테스트
10. 중복 변수 제거 및 CORE 6개로 압축
11. 정비사업 Option Value Engine 고도화(§14.13 순서) — 특히 Stage 전환율과 지역별 Stage Premium

---

## 20. 작업공간 이전 및 기준문서 운영 규칙

2026-09-03부터 이 문서를 아파트 투자엔진의 단일 기준문서로 사용한다. 이전 대화와 개별 산출물에 있는 내용은 다음 원칙으로 승계한다.

- 이 문서의 `확정 규칙`이 개별 연구 로그·과거 순위표와 충돌하면 이 문서를 우선한다.
- 연구 로그의 가설은 검증 전까지 정식 점수식에 넣지 않는다.
- 과거 TOP10/TOP20은 당시 유니버스·입력가격의 스냅샷이며 최신 추천으로 자동 승계하지 않는다.
- 새 백테스트에서 살아남은 규칙만 이 문서의 CORE 또는 Hard Gate로 승격한다.
- 이후 확정된 변경은 이 문서에 누적하고, 실험 결과와 폐기된 가설은 별도 연구 로그에 남긴다.
- 로컬 코드(`land-invest-analyzer`)가 이 문서와 충돌하면 이 문서를 우선하되, 기존 구현을 바로 삭제하지 않고 diff를 먼저 남긴다.

### 승계된 핵심 산출물

- `RESEARCH_LOG_AGGRESSIVE_v0.1.md`: 균형형·공격형·고공격형 가중치 민감도와 100회 반복 결과
- `RESEARCH_LOG_FCP_v0.1.md`: Future Choice Set/FCP 설계, 중복 변수 제거, 50개 유니버스 확장 결과
- `RESEARCH_LOG_REDEVELOPMENT_OPTION_v0.1.md`: 정비사업 Option Value Engine 병합 diff, 변수 통일, 구현/미구현, 회귀 결과
- `3억_비거주_TOP20_투자판단표_2026-08-31.xlsx`: 초기 정성 TOP20과 동탄 출발 임장시간
- `아내와_함께보는_3억_비거주_아파트_TOP20_2026-08-31.xlsx`: 쉬운 설명용 3억원 결과표
- `직원약사와_함께보는_1억_비거주_아파트_TOP20_평택출발_2026-08-31.xlsx`: 1억원·평택 출발 결과표

## 21. 사용자 시나리오 레지스트리

기본 조건은 `비거주 투자 · 5년 보유 · 수도권 · 토허/실거주 Hard Gate · 세후/이자후/비용후 Terminal Wealth`다.

| 시나리오 | 자기자본 | 출력 | 출발지/용도 | 상태 |
|---|---:|---|---|---|
| 직원약사형 | 1억원 | TOP20 + 쉬운 설명 | 평택 출발 | 기존 결과 있음, 최신 Gate 재계산 필요 |
| 기본형 | 3억원 | TOP10/TOP20 | 동탄 출발 | 50개 정성 유니버스까지 확장 |
| 확장형 | 5억원 | TOP20 | 필요 시 동탄 출발 | 최신 전수 재계산 필요 |
| 고자본형 | 10억원 | TOP10/TOP20 | 필요 시 동탄 출발 | 최신 전수 재계산 필요 |

투자금이 바뀌면 기존 순위와 Capital Efficiency를 재사용하지 않는다. 같은 후보라도 대출·세금·미사용 현금의 미래가치가 달라지므로 전체 Feasibility와 Terminal Wealth를 다시 계산한다.

## 22. 현재 데이터·정책 블로커

### 실행 전 필수

1. 매매·전월세 원자료 240개월 수집 완료 및 면적·타입·동호조건 정규화
2. 서울·경기·인천 `ALL_BUYERS` 토허와 실거주 의무의 시행일·종료일·필지 범위 검증
3. 실제 차주 조건에 따른 LTV·DSR·주택담보대출 총액한도 계산
4. 취득세·양도세·농특세·인지세·중개보수·법무비·수리비·보유비용의 시행일별 규칙 검증
5. 현재 매매 호가·최근 실거래·전세 호가·최근 전세 실거래를 분리 수집하고 정상체결가격 산출
6. (정비사업) 수도권 정비사업 Registry의 단계·일자·용적률·세대수 원자료, 역세권 특례 조례 원문, 단계별 전환율

### 정책값 검증 원칙

- 공식 원문과 부칙으로 시행일을 확인하기 전에는 `NEEDS_VERIFICATION`을 유지한다.
- 특히 6억원 이하 1%, 6~9억원 2%, 9억원 초과 3% 체계의 과거 시행일과 6~9억원 구간 산식 전환 시행일은 원문 대조 후 확정한다.
- 외국인 대상 토허를 국내 일반 매수자 `ALL_BUYERS` 토허로 오인하지 않는다.
- 행정구역 개편으로 코드가 바뀐 지역은 필지 단위 고시가 없으면 보수적으로 Gate하되 `scope_uncertainty`를 표시한다.
- 정책 데이터가 0건이거나 범위가 비어 있으면 통과로 보지 않고 `UNKNOWN/NEEDS_CHECK`로 처리한다. 다만 전체 화면이 비는 것을 막기 위해 실행순위와 조사대기 목록을 분리한다.

## 23. 다음 구현 라운드

1. Hard Gate 데이터부터 완성한다: 토허 → LTV/DSR → 세금·비용 → 실제 매물.
2. 1억·3억·5억·10억원별 Investable Universe를 블라인드로 다시 생성한다.
3. 상위 후보의 실거래 원장·전세·거래회전·동호조건을 검증한다.
4. 가격별 Buyer Depth와 후보별 실제 Future Choice Set 10~20개를 구축한다.
5. 진입가격 민감도와 Good Buy 가격을 계산한다.
6. 2015/2017/2019/2021/2023 Walk-Forward로 CORE와 Exit Price를 검증한다.
7. 검증 통과 후보만 실행가능 TOP10/TOP20으로 승격한다.
8. 화면에는 평균순위·TOP10 생존율·TOP5 진입률·P90·Good Buy·Confidence를 함께 표시한다.
9. 정비사업 Option Engine: Stage Registry → 사업성·분담금 → 시나리오 트리 TW → Hold vs Switch 연결(§35 체크리스트).

UI는 현재 단계에서 Streamlit 기능을 유지한 Fluent형 외관을 사용하고, 서비스 완성 후 React 프런트엔드로 분리한다. 시작화면과 결과화면은 전문성을 우선하되 비전문가도 판단 근거를 이해할 수 있게 설명한다.

---

## 24. Universe Expansion Engine — 확정 구조

### 목적
현재의 50개 후보는 연구용 정성 유니버스일 뿐이며 최종 추천 Universe로 사용하지 않는다. 사용자가 한 번도 언급하지 않은 단지가 #1로 올라올 수 있어야 객관적인 엔진으로 본다.

### 데이터 원천
1. 국토교통부 아파트 매매 실거래가 API: 법정동코드×계약월 단위로 수도권 매매 원장 수집
2. 국토교통부 아파트 전월세 실거래가 API: 전세·월세 원장 및 Rent-to-Buy/Downside 분석
3. K-apt 공동주택관리정보: 단지코드, 세대수, 사용승인일, 면적구성, 주차·관리 특성 등 기본정보 결합
4. 토허·대출·세금·비용 정책테이블: 평가일 기준 Hard Gate와 Terminal Wealth 계산
5. 현재 호가 데이터는 실거래와 별도 필드로 유지하며 실행가격 확인용으로만 사용
6. 정비사업 공식 등록부(서울 정보몽땅·경기데이터드림·인천 renewal): §14 Stage Registry

### Funnel
- 원시 수도권 Universe: 단지×전용면적×타입 기준 **수천 개**
- Hard Gate 통과: 목표 **1,000~2,000개**
- Quick Scan: 약 **1,000개**
- 정밀분석: **300개**
- Terminal Wealth: **100개**
- 실제 매물 검증: **30~50개**
- 최종 Executable TOP20

### 1차 후보 기본키
`법정동코드 + 단지식별자 + 전용면적 bucket + 타입/동호조건 + 평가일`

### 1차 Quick Scan 최소 지표
1. Entry Advantage
2. Buyer Depth 대리값
3. Price Runway 대리값
4. Settlement Strength
5. Downside Floor
6. Exit Liquidity
7. Data Confidence — 점수 직접 가산 금지
8. Option Research Priority — 점수 아님, 연구 순서(§14.11)

### Quick Scan 원칙
- 목적은 정밀한 순위가 아니라 **Winner Recall** 확보다.
- 가격이 싸다는 이유만으로 통과시키지 않는다.
- 최근 고가 1건이 아니라 P25/Median/거래하단/거래지속성을 사용한다.
- 최근 거래가 적어도 장기 Floor·전세·Buyer Pool이 강하면 관찰군으로 남길 수 있다.
- 지역별 quota는 Discovery Coverage용으로만 쓰고 최종점수에는 넣지 않는다.
- 사용자 관심·검색횟수·기존 Watchlist는 Candidate Generation과 Rank에 입력하지 않는다.

### Coverage KPI
매 실행마다 다음을 출력한다.
- 수도권 단지 Coverage
- 거래금액 Coverage
- 거래건수 Coverage
- 세대수 Coverage
- 시군구 Coverage
- 자기자본 가격대 Coverage
- 49/59/74/84㎡ 등 면적 Coverage

Coverage가 기준 미달이면 `FULL CAPITAL UNIVERSE TOP20`이라는 표현을 금지하고 `PARTIAL VERIFIED UNIVERSE`로 표시한다.

### 3억원 기본형 Candidate Generation
비거주·5년 보유·자기자본 3억원을 기본으로 하되 매매가격 상한을 고정하지 않는다. 각 후보별 실제 LTV/DSR/세금/비용으로 `SELF_CAPITAL_REQUIRED`를 계산한 뒤 3억원 이하만 Executable Universe에 둔다. 낮은 매수가로 남는 현금은 현금의 미래가치를 포함해 Terminal Wealth에서 비교한다.

### Stage A — Raw Discovery
모든 거래에서 단지×면적 그룹을 생성하고 최근 24개월 거래 존재 여부, 정상가격 산출 가능성, 세대수/연식/면적 정보를 붙인다.

### Stage B — Hard Gate
토허/실거주, 자금조달, 거래가능성, 데이터 최소품질만 본다. Alpha 점수는 사용하지 않는다.
**단지 규모 Gate(2026-09-05 확정)**: 세대수 1,000 미만(또는 세대수 미상) 단지는 Universe 집계와 모든 추천 순위에서 제외한다. 소규모 단지는 거래가 드물어 가격 신호가 흔들리고 Buyer Pool 이 얇아 대장 전파·Exit Liquidity 를 논할 수 없다. 급지·생활권·대장·Pair·Exit Price 패널·실측 드리프트도 같은 기준으로 집계한다(`relative/store.load_complexes(min_households=1000)`). 개별 단지 조회는 가능하되 "추천 대상 아님" 을 표시한다.

### Stage C — Quick Scan 1,000
저비용 집계로 후보를 넓게 남긴다. 목표는 좋은 후보 누락 최소화다.

### Stage D — Deep 300
P25/Median/P75, Money Arrival, Buyer Budget Migration, Leader Transmission Failure, Future Choice Set 초안, 공급·전세·동호조건 정규화, 정비사업 Option Deep Dive(§14.11 대상만)를 수행한다.

### Stage E — Terminal Wealth 100
하락/보수/기준/강세 시나리오별 Liquid Exit Price, 이자, 세금, 대출잔액, 매도비용, 미사용현금 미래가치를 계산한다. 정비사업 후보는 §14.6 시나리오 트리를 추가한다.

### Stage F — Executable 30~50
현재 호가/급매/최근 정상체결가격을 비교하고 실제로 Good Buy 이하 물건이 존재하는 후보만 최종 실행순위에 둔다.

### 자동 검증
- User Interest Invariance
- Name Blind Ranking
- Duplicate Complex/Area Detection
- Future Leakage Check
- Policy Vintage Check
- Coverage Drop Alert
- Winner Recall / Gate False Negative

## 25. Universe Expansion 데이터 수집 구현 우선순위

1. 서울·경기·인천 시군구 법정동 코드 목록 확보
2. 최근 24개월 매매 실거래 일괄수집 → 단지×면적 그룹 생성
3. 최근 24개월 전월세 일괄수집 → 전세 Floor/Gap 계산
4. K-apt 단지 기본정보 결합
5. 토허/실거주 Gate 결합
6. 3억원 SELF_CAPITAL_REQUIRED 근사 Gate
7. Quick Scan 1차 1,000개 생성
8. 지역·면적·세대수 Coverage 검증
9. 상위 300개 Deep Scan
10. 기존 50개 후보가 새 Blind Universe에서 어디에 위치하는지 비교

기존 50개 후보는 Seed 추천 목록이 아니라 **회귀 테스트 세트**로만 유지한다. 새 Universe에서 기존 후보가 떨어지더라도 수동으로 복원하지 않는다.

---

## 26. 공공데이터 API 연결 상태 — 2026-09-03

### 준비 완료
- 공공데이터포털 인증키 확보. 인증키 원문은 문서/코드/Library에 저장하지 않고 환경변수 `DATA_GO_KR_SERVICE_KEY`로만 주입한다.
- 사용자가 아파트 매매·아파트 전월세·K-apt 관련 활용신청이 완료되었다고 확인했다.
- 공식 명세 기준 아파트 매매는 `https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev`, 전월세는 `https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent`를 사용한다.
- K-apt는 단지목록 및 기본/상세정보를 결합한다.

### 구현 산출물
`UNIVERSE_COLLECTOR_v0.1/`를 기준 수집기로 사용한다.
- `universe_collector.py`: K-apt 수도권 단지목록, 매매/전월세 월별 원장, SQLite 적재, 1차 단지×면적 Universe view 생성
- `.env.example`: 인증키 환경변수 예시
- `.gitignore`: 인증키와 DB 파일 커밋 차단
- `requirements.txt`
- `README.md`

### 실행 순서
1. K-apt 수도권 단지목록 수집
2. 최근 24개월 매매·전월세 원장 수집
3. `단지 × 전용면적` 원시 그룹 생성
4. 거래 최소품질·토허·자금조달 Hard Gate
5. Quick Scan 약 1,000개 생성
6. Coverage KPI 확인
7. 상위 300개 Deep Scan
8. 백테스트를 위해 2015년~현재 원장 확장

### 현재 실행환경 제약
현재 ChatGPT 컨테이너는 외부 DNS 연결이 제한되어 `apis.data.go.kr` 실호출을 직접 완료하지 못했다. 이 제약은 API/인증 상태 실패로 해석하지 않는다. 수집기는 외부 네트워크가 허용된 Claude/로컬 실행환경에서 즉시 실행 가능하도록 작성한다.

### 보안 규칙
- 인증키를 로그, Git, DB, MASTER_SPEC, 오류 메시지에 기록하지 않는다.
- URL을 로그로 남길 때 `serviceKey` query parameter를 마스킹한다.
- 기존 노출 키는 가능하면 추후 재발급하고 새 키는 `.env`에서만 관리한다.

---

## 27. 외부 탐색 후보 유입 규칙 — Claude 30개 후보 추가 (2026-09-03)

사용자가 별도 Claude 스캔에서 발견한 30개 후보를 **Discovery Candidate Pool**에 추가한다. 이 목록의 Claude 점수(75~84)는 현재 MASTER의 CORE 점수와 정의·가중치가 다르므로 **절대 이식하지 않는다**. 매수가·실투자금도 원자료 검증 전에는 `SOURCE_REPORTED`로만 보관한다.

적용 원칙:
- 후보 이름/지역/제시 매수가/제시 실투자금은 탐색 힌트로만 사용한다.
- 사용자 또는 Claude가 골랐다는 이유로 Ranking 가점 금지.
- 기존 Universe와 중복 후보는 동일 기본키로 병합하고 연구횟수는 점수에 반영하지 않는다.
- 토허/실거주 Hard Gate를 먼저 재검증한다. 수원 영통구 후보 등 실행불가 후보는 Pure Alpha 연구목록에는 남기되 비거주 Executable Ranking에서는 제외한다.
- 호재 미반영 목록이므로 교통·산업·정비사업 등은 이름 자체로 가점하지 않고, 검증된 확률×순가치만 Terminal Wealth에 반영한다(§14).
- 신규 후보는 실거래·전세·K-apt·정상체결가격을 동일 기준으로 채운 뒤 Quick Scan에 진입한다.
- Claude 원점수는 `external_source_score`로 보관 가능하나 최종점수/순위 계산에는 사용하지 않는다.

이번 30개 추가로 수동/외부 Discovery Pool의 폭은 넓어지지만, `FULL CAPITAL UNIVERSE` 표기는 금지한다. 자동 수도권 전수 Universe가 완성될 때까지 `PARTIAL VERIFIED UNIVERSE`를 유지한다.

---

## 28. Discovery Universe 수동·웹 확장 상태 — 2026-09-03

API 전수 수집 전에도 Winner Recall을 높이기 위해 주변 경쟁상품을 넓게 발굴한다.

### 현재 상태
- 기존 Claude Discovery 및 v0.1/v0.2 주변확장 로그에서 약 85개 후보 라벨 확인
- v0.3에서 웹 기반 신규 후보 147개 추가
- 단순 합산 약 232개 후보상품이며, 기본키 정규화 전이므로 동일단지 표기차이·면적버킷 중복이 일부 포함될 수 있다.
- 이 수치는 `FULL UNIVERSE`가 아니며 `DISCOVERY COVERAGE`로만 관리한다.

### 운영 규칙
1. Discovery 후보는 추천 Seed가 아니다.
2. 후보가 많이 발견된 지역이라는 이유로 가점을 주지 않는다.
3. 모든 후보는 Hard Gate와 Quick Scan에서 기존 후보와 동일하게 처음부터 평가한다.
4. 기존 TOP10이 신규 Blind Universe에서 밀리면 수동 복원하지 않는다.
5. 사용자 언급 여부·Claude 점수·연구량은 Confidence 외 투자점수에 사용하지 않는다.
6. API 전수 Universe가 생성되면 이 수동 Discovery Pool은 Winner Recall/회귀 테스트용으로 전환한다.

### 다음 목표
- 수동·웹 Discovery 300개 이상
- API Raw Discovery 수천 개
- Hard Gate 통과 1,000~2,000개
- Quick Scan 1,000개
- Deep 300개
- Terminal Wealth 100개
- Executable 30~50개

### 2026-09-03 Round 4 업데이트
- Discovery 후보 라벨을 시흥·군포·남양주·김포까지 확장했다.
- 기본키 정규화 전 단순 누적 후보는 약 323개다.
- 동일 단지 표기차이와 면적버킷 중복이 포함될 수 있으므로 공식 Universe 수치로 쓰지 않는다.
- 다음 단계는 후보 추가가 아니라 300+ Discovery Pool의 기본키 정규화 → Hard Gate → Quick Scan 재정렬이다.

## 29. Discovery Universe 진행상태 — 2026-09-03 19시대

- 수동/웹 기반 블라인드 Discovery Registry를 323개에서 **346개 단지×면적 후보**로 확장했다.
- 신규 후보는 기존 후보에 가점을 주는 방식이 아니라 동일 생활권·유사가격·대체평형을 기계적으로 펼쳐 생성한다.
- 현재 346개 Registry는 `DISCOVERY_ONLY`이며 최종 순위가 아니다.
- 다음 단계는 후보 확대를 계속하면서 동시에 최소비용 Hard/Data Gate를 적용해 첫 Blind Quick Scan을 시작하는 것이다.
- 기존 50개 및 기존 TOP10은 Seed 추천이 아니라 회귀 테스트 세트로만 유지한다.

---

## 30. 346 Discovery Universe Quick Scan — 2026-09-03

### 실행 결과
- Discovery 후보 346개를 단지×면적 단위로 1차 정규화했다.
- 일반 토허/실거주 지역, 자기자본 3억원 Proxy Gate, 가격 파싱/면적 식별 Gate를 적용한 결과 309개가 provisional Quick Scan 대상이 되었다.
- 이 수치는 실제 DSR·세금·현재 호가가 반영된 Executable 수가 아니며 `PARTIAL VERIFIED UNIVERSE`로만 표시한다.

### 신규 확정 규칙 — Settlement Promotion Gate
Cheapness와 같은 생활권 상단 대비 가격차만으로 Deep Scan에 승격시키지 않는다. 다음 중 최소 하나가 필요하다.
1. 정상층 가격하단 또는 Median의 상향 이동
2. 높아진 가격대에서 반복 체결되는 Settlement Evidence
3. 강한 전세 Floor + 충분한 거래회전으로 설명되는 Downside/Exit 증거

청구1차59와 호매실동쌍용59는 단순 상대가격 기준으로는 상위였으나 최근 원장 재검증에서 새로운 가격대 정착이 약해 Deep 우선순위를 낮췄다. 이는 `Persistent Cheapness ≠ Recoverable Discount` 규칙의 실제 사례로 보존한다.

정비사업 후보도 이 Gate를 그대로 통과해야 한다. `OPTION_RESEARCH_PRIORITY = HIGH`는 Deep 승격 사유가 아니다(§14.11).

### 산출물
- `QUICK_SCAN_346_v0.1.csv`
- `DEEP_SHORTLIST_60_v0.1.csv`
- `PROVISIONAL_TOP30_3EOK_v0.1.md`

### 남은 Hard Blocker
Terminal Wealth와 Executable TOP20은 실제 차주 DSR, 최신 정책테이블, 현재 호가/매물 원장이 필요하다. 이 값이 없을 때 임의 가정으로 최종순위를 확정하지 않는다.

---

## 31. 대규모 선택 로직 시뮬레이션 — 2026-09-04

현재 Deep Shortlist를 대상으로 100,000회 Monte Carlo + 추가 500,000회 확인 시뮬레이션 + 50,000개 점수식 랜덤 탐색을 수행했다.

### 확정에 가까운 구조적 결론
1. 가중치 미세조정보다 Deep Promotion Gate의 품질이 더 중요하다.
2. `Price Runway`가 높아도 Settlement Evidence가 약하면 가짜 승자/Value Trap 위험이 크다.
3. Deep 승격은 `Settlement Evidence OR (Strong Downside Floor AND Strong Liquidity)`를 기본으로 한다.
4. 핵심 비선형 상호작용은 `Runway × Settlement`, `Buyer Depth × Runway`, `Entry Advantage × Settlement`로 관리한다.
5. `high Runway × low Settlement`에는 Fragile Runway Penalty를 적용한다.
6. Confidence는 직접 큰 점수 감점으로 쓰지 않고 오차폭·검증 우선순위·P90에 사용한다.
7. 단일 점수보다 평균순위, TOP10 생존율, TOP5 진입률, P90, 시장국면별 최악순위를 함께 본다.
8. 본 시뮬레이션은 미래 수익률 라벨 없는 강건성 테스트이므로 예측력 확정 근거로 쓰지 않는다. 최종 검증은 2015/2017/2019/2021/2023 Walk-Forward로 한다.

세부 결과는 `RESEARCH_LOG_SELECTION_SIMULATION_v0.1.md`에서 관리한다.

---

## 32. Missingness-aware Ranking — 2026-09-04 시뮬레이션 반영

### 핵심 원칙
`UNKNOWN != 중간값`.

현재 Discovery/Quick Scan에서는 일부 미측정 항목이 `0.50`, `0.48` 같은 중립 placeholder로 들어가 있다. 앞으로는 이를 실제 중간 수준으로 해석하지 않는다.

각 핵심 변수는 최소 다음 필드를 가진다.
- `value`
- `measurement_status = VERIFIED | PROXY | HEURISTIC | UNKNOWN` (정비사업 입력값 §14.8과 동일 체계)
- `uncertainty_width`
- `source_date`

UNKNOWN 값은 점수에 고정 중립값으로 직접 사용하지 않고 Monte Carlo 분포로 샘플링한다. Confidence는 직접 가점/감점이 아니라 분포 폭과 검증 우선순위에 반영한다.

### Promotion Gate 수정
- `VERIFIED Settlement가 낮음` → Fragile Runway Penalty 또는 Deep 승격 제한 가능
- `Settlement UNKNOWN` → 탈락 금지. Deep 검증대기로 유지
- `강한 전세하방 + 거래유동성`이 VERIFIED이면 Settlement 일부를 대체하는 승격 근거로 사용 가능

### 2026-09-04 추가 시뮬레이션
- 구조 비교 200,000회 + Missingness-aware 120,000회 = 320,000회 추가 실행
- 이전 연구 650,000회와 합산 약 970,000회 수준
- 강한 Settlement Hard Gate는 현재 데이터 결측 구조에서 Winner Recall을 크게 훼손
- 방어적 강건형 TOP10 잠재 승자 포착률 약 74.1%, 현재 균형형 약 72.5%, 공격형 약 68.7%
- Settlement 0.55~0.60 강한 Gate는 약 50.5%로 하락

따라서 현재 단계의 최적 방향은 `강한 탈락 Gate`가 아니라 `UNKNOWN 분리 + Fragile Runway Penalty + Robust Ranking + Deep 검증`이다.

세부 결과는 `RESEARCH_LOG_SELECTION_SIMULATION_v0.2.md`와 `SELECTION_MONTE_CARLO_320K_v0.2.csv`에서 관리한다.

---

## 33. Universe Expansion v0.3 — 2026-09-04

### 확장 결과
- `DISCOVERY_REGISTRY_642_v0.3.csv`: **346 → 642**, 순증 +296
- 서울 73 / 경기 553 / 인천 16 단지×면적 후보
- 행정권역 표현 기준 17 → 57개로 확대
- `QUICK_SCAN_642_DATA_AWARE_v0.2.csv`: Settlement missingness를 중립값으로 오인하지 않는 Data-aware Quick Scan
- `RESEARCH_LOG_UNIVERSE_EXPANSION_v0.3.md`: 신규 후보/토허/데이터 품질/Promotion 결과 기록

### Pure Alpha / Executable 분리
- 토허 후보는 Universe에서 삭제하지 않는다.
- 서울 전역 및 경기 12개 일반 토허 범위 후보는 `Pure Alpha=IN_UNIVERSE`로 남기고, 비거주 Executable에서는 `PURE_ALPHA_ONLY_TORHEO_BLOCK_FOR_NONRESIDENT`로 분리한다.
- 경기 12개: 과천, 광명, 성남 분당·수정·중원, 수원 영통·장안·팔달, 안양 동안, 용인 수지, 의왕, 하남.
- 2025-10-20 효력 발생, 현재 확인한 지정기한은 2026-12-31.
- 인천의 외국인 토허와 국내 일반 매수자 토허를 절대 혼용하지 않는다.
- 일반 토허 범위 밖도 사업/필지별 토허는 계약 직전 별도 확인한다.
- 정비사업 후보도 같은 규칙이다(§14.8).

### Settlement Evidence 규칙 강화
낮은 가격 또는 큰 Price Runway만으로 Deep에 승격시키지 않는다.
- 반복 정상거래로 가격대 유지/상향 확인 → Deep 우선
- 비중립 Settlement Proxy → 검토 승격
- 거래량만 강함 → Data Enrichment 우선, Settlement는 UNKNOWN 유지
- Settlement UNKNOWN → 후보는 보존하지만 `NO_CHEAPNESS_PROMOTION`
- UNKNOWN은 중립값 0.5로 계산하지 않는다.

### 현재 Data-aware Quick Scan
- Quick Data Ready: 611
- Data Verify Required: 31
- Settlement Evidence Pass: 23
- Settlement Proxy Review: 14
- Liquidity-only Evidence: 15
- Settlement Verify Required: 590
- Deep Priority: 23
- Data Enrichment Priority: 15

### 데이터 한계
이번 642 Registry는 누락 최소화를 위한 Discovery 확대다. 최근 단일 실거래는 정상체결 Median이 아니며, 전세/24개월 분포/거래회전이 없는 후보는 Deep 투자점수를 확정하지 않는다. 최종 순위는 외부 네트워크 환경에서 `UNIVERSE_COLLECTOR_v0.1`을 실행해 매매·전월세 원장을 확보한 뒤 산출한다.

---

## 34. Walk-Forward Backtest v0.1 — 2026-09-04

공개 실거래 연도별 평균으로 2015→2020, 2017→2022, 2019→2024, 2021→2026의 4개 진입시점을 1차 재구성했다. 표본은 총 71 단지×면적-기간 관측치다.

### 1차 결과
- 정적 Momentum, Value, Middle 전략보다 `Market Regime → Candidate Selection`의 2단계 구조가 가장 강했다.
- Regime-Aware TOP5 평균 5년 수익률: 4구간 평균 약 51.9%
- Momentum: 약 47.6%
- Middle: 약 41.8%
- Value: 약 32.1%
- Winner Recall 평균: Regime-Aware 약 64.2%, Momentum 약 46.7%

### 새 정식 연구축
CORE 계산 전에 `Market Regime Layer`를 둔다.
- Broad Reset / Early Recovery
- Normal Expansion
- Late Expansion
- Overheated / Priced-In

후보의 Movement/Settlement/Price Runway는 시장국면에 따라 다르게 해석한다. 특히 과열국면에서는 높은 Movement를 그대로 가점하지 않고 Price Stretch/추격벌점을 강화한다.

주의: 이번 v0.1은 수도권 전체 원장이 아니라 공개 실거래 페이지에서 재구성한 1차 패널이다. 정식 CORE 승격 전 200+ 관측치와 거래량·P25·전세·금리·공급을 포함한 확대 Walk-Forward를 수행한다.

---

## 35. Relative Price Gap Engine — 급지별 대장/준대장 상대가격 갭 + 후행주 탐색 (v0.1, 2026-09-04)

### 36.0 목적과 위치
수도권 전체에서 **상위 급지·동일 급지의 대장 가격이 먼저 오른 뒤, 역사적 상대가격 관계에 비해 아직 덜 따라간 단지×면적**을 객관적으로 찾는다. 이 모듈은 §1 목적함수(동일 자기자본 5년 후 Terminal Wealth 최대화)를 돕는 **설명·예측 모듈**이며 목적함수를 대체하지 않는다. 출력 `Relative Mispricing`은 점수에 더하지 않고 Liquid Exit Price(§12)·Terminal Wealth(§13)의 입력으로만 쓴다. 특정 단지의 순위를 올리기 위한 로직은 없다(§3).

### 36.1 절대가격이 아니라 비율
`Follower / Leader Price Ratio`를 월별로 계산하고 과거 여러 시장국면과 비교한다. 단순 차액(4억)은 쓰지 않는다. 2021 같은 극단적 유동성 장세의 비율을 정상값으로 자동 채택하지 않는다 — 과열 국면 월은 정상비율 계산에서 뺀다.

### 36.2 급지 체계 — 데이터로 구축
행정구역으로 급지를 나누지 않는다. 법정동 단위로
- **급지(tier)**: 최근 24개월 ㎡단가(log) 를 1차원 자연분류(8단계)로 나눈다. 1 = 최고 급지.
- **생활권(life_zone)**: 중심점 거리 ≤ 2.5km · 12개월 변화율 상관 ≥ 0.75 · 가격수준 근접(log 차 ≤ 0.35) 인 법정동을 묶는다(union-find, 생활권 지름 ≤ 5km).
- 통근축·학군·상급지 구매자 이동·외부 매수자 이동·신축 가격은 v0.1 에 넣지 못했다 → `method = PROXY` 로 표시하고 순차 반영한다. 수동 급지표를 쓰면 반드시 `PROXY`.
저장: `relative_zone`(법정동→생활권·급지), `complex.life_zone`, `life_zone`.

### 36.3 대장 자동 선정
생활권×면적별로 **LEADER_1~3** 을 뽑는다. 최고가 하나로 정하지 않고 다음 다섯 항목의 생활권 내 백분위 순위 평균(동일가중, HEURISTIC)으로 정한다: 최근 60개월 상위 3위 안 비율(지속적 상위 가격) · 거래량(24개월 표본) · 하락기 상대강도(2021~22 고점→2023 저점, 생활권 중앙값 대비) · 선행성(내 12개월 변화율 vs 3개월 뒤 생활권 변화율 상관 − 반대 방향 상관) · 설명력(생활권 변화율과의 상관). 84㎡ 대장과 59㎡ 대장은 따로 뽑는다. 저장: `zone_leader`.

### 36.4 Follower 의 Leader Set
후보마다 Leader 를 하나만 두지 않는다.
- **LOCAL** 같은 생활권 대장 · **GRADE** 같은 급지의 가장 가까운 다른 생활권 대장 · **UPPER_GRADE** 한 단계 위 급지의 가장 가까운 생활권 대장 · **BUYER_CHOICE** 같은 총액대(1.10~1.35배)·10km 안에서 실제 비교되는 상급 상품(면적이 달라도 됨 — 총액 비교).
기존 `relative/leaders.py` 의 LOCAL/PRICE/FLOW/CAPITAL_COHORT/METRO 다섯 종류는 이 넷으로 정규화한다(PRICE·CAPITAL_COHORT → BUYER_CHOICE, FLOW → 대장 선정의 거래량 항목, METRO → 폐지: 수도권 최고가는 Buyer Pool 이 겹치지 않는다).

### 36.5 Historical Relative Price Band 와 국면별 정상비율
Pair 마다 ≥ 60개월의 비율로 P10/P25/Median/P75/P90 을 계산한다(과열 월 제외). 현재 비율 = 최근 6개월 중앙값. **정상비율**은 현 시장국면(§34 Regime, 시군구 월별)과 같은 과거 국면의 중앙값을 우선 쓰고(≥ 12개월), 없으면 과열 제외 장기 중앙값을 쓴다. `Observed Relative Gap = (정상 − 현재) / 정상`. 이 값은 곧바로 상승여력이 아니다.

### 36.6 구조적 가격차와 회복 가능한 가격차
`Observed Gap = Structural Gap + Recoverable Gap`.
- 회복가능 비중은 **그 Pair 의 실제 과거 전달 실적**(§35.7 추종률 중앙값)으로만 정한다. 과거 평균으로 무조건 회귀한다고 보지 않는다.
- 구조 변화·구조 격차 플래그가 확인될 때마다 회복가능 비중을 10%p 씩 낮춘다(HEURISTIC): 학군(학원가 밀도 2배 이상 격차), 연식·상품성(12년 이상), 세대수(3배 이상), 역 접근(1km 밖 vs 500m 안), 최근 5년 내 1.5km 철도 개통(§35.10 평균회귀 함정).
- 전달 실적이 없으면 분해하지 않는다(전부 회복가능으로 두면 "Spread 가 크다 = 기회" 가 된다).
- **학군은 구조적 가격차에 명시적으로 넣는다.** 학군이 구조적으로 다른 Pair 는 Historical Ratio 가 낮게 유지되는 것이 정상 Gap 이다. `School-driven Buyer Depth`·`School Premium Persistence` 는 학업성취도 데이터 확보 후 정식화한다(현재 학원가 밀도 대리값 = PROXY).

### 36.7 Leader Transmission Probability
정의: 대장의 가격 상승이 그 후행 후보로 실제 전파될 확률. Pair 의 과거 에피소드(Leader 12개월 상승 ≥ 8%, 간격 ≥ 24개월)마다 Follower 의 24개월 추종률(= Follower 상승 / Leader 상승)을 재고, 0.5 이상이면 성공. `P = 성공/에피소드`. 에피소드 ≥ 3 → VERIFIED, 1~2 → PROXY, 0 → UNKNOWN(중립값 금지). 같은 에피소드 원장으로 Gap 축소율(12/36/60개월)을 함께 저장한다.

### 36.8 Leader Move / Follower Settlement 확인
- **Leader Move Confirmation**: Leader P25·Median·P75 12개월 상승이 모두 +1% 이상이고 거래량이 줄지 않으면 CONFIRMED, Median 만 오르면 PARTIAL, 아니면 NONE. 호가만으로 판정하지 않는다(실거래 스냅샷만 사용).
- **Follower Settlement Start**: P25↑ · Median↑ · 거래량↑ · Gap 축소 중 하나 이상. 아무 움직임도 없으면 `Persistent Cheapness` 로 보고 할인율을 Alpha 로 인정하지 않는다(전세 Floor·매매-전세 Gap 은 v0.2).

### 36.9 Multi-Leader Consensus 와 Relative Mispricing
Leader ≥ 3 개의 Gap 이 모두 +5% 이상이고 산포 ≤ 12%p 면 STRONG, 모두 양이면 OK, 한 Leader 에만 크게 싸면 DISTORTED, 그 밖 WEAK, Leader 3개 미만 THIN.
```text
Relative Mispricing = Recoverable Gap × Transmission P × Settlement 계수(무반응 0.5)
                      × 과열/하락전환 계수(0.5) × Leader Move 계수(CONFIRMED 1.0 · PARTIAL 0.7 · NONE 0.4)
```
Superior Substitute Risk · Future Choice Set Risk · Supply Risk 는 v0.1 에서 차감하지 못했다(N/A, §10·§24 결합 후). Follower 집계는 Leader 별 Mispricing 의 중앙값.

### 36.10 Mean Reversion 함정 방지
생활권 구조 변화·신축 대단지 입주·학군 변화·철도 개통·산업축·대규모 재개발·행정/도시계획 변화·재건축 진행도 급변·상품성 격차 확대·인구구조 변화가 있으면 과거 Ratio 를 폐기하거나 낮은 Confidence 를 준다. `Past Relative Ratio ≠ Future Fair Ratio`. `Future Fair Relative Ratio`(§35.13)는 별도 추정.

### 36.11 결과표
- `RELATIVE_LAG_TOP50`: 구조적 가격차를 제거한 뒤에도 덜 따라갔고, 합의 STRONG/OK, Leader Move 확인, Follower 움직임 시작, Mispricing > 3% 인 후보.
- `FALSE_CHEAP_TOP50`: 대장 대비 12% 이상 싸지만 구조적 비중 ≥ 60% · 과거 전달확률 < 30% · Follower 무반응 중 하나로 가격차가 정상인 후보(Value Trap 확인용).
- 정기 스캔 출력: Leader Rally(급지) · Follower Lag · Recoverable Gap · Settlement Start · Best Entry(TW 최대). Best Entry 는 §13 TW 완성 후.
후보별 출력 컬럼: 급지·생활권·Local/Grade/Upper Leader·현재가·Leader 현재가·현재 비율·역사 중앙값·현 국면 정상비율·구조적/회복가능 가격차·Transmission P·Relative Mispricing·Settlement Evidence·(Future Choice Set Risk·5년 Liquid Exit·TW 는 N/A→§13 결합 후).

### 36.12 정비사업 옵션 엔진과의 결합
`Relative Mispricing` 과 `Renewal Option Mispricing`(§14)은 분리한다. 주변 신축과의 가격차가 재건축 초기단계 때문이라면 두 번 가점하지 않는다: 정비사업 Stage ≥ 2 후보의 상대가격 Gap 중 옵션으로 설명되는 부분은 §14 에서만 계산하고 §36 회복가능 Gap 에서 뺀다(v0.2 구현).

### 36.13 도시변화·시장국면과의 결합
- `Future Fair Relative Ratio`: GTX·공원·도시재편으로 정상비율 자체가 바뀔 수 있다(예: 65% → 72%). 실측 전에는 N/A.
- Market Regime 별로 Gap 축소확률·평균 축소폭·소요시간·재확대 위험을 추정한다. "현재 Gap 이 큰 것" 과 "현재 Gap 을 사야 하는 것" 을 구분한다. 상승장 말기의 급격한 추격은 되돌림도 크다.

### 36.14 백테스트와 KPI
과거 "대장 상승 → 후행 상승" Pair 를 대량 수집해(에피소드 원장 `relative_backtest_episodes.csv`) 1·3·5년 뒤 Gap 축소를 확인하고, **실패사례**(대장은 올랐는데 후행은 끝까지 못 따라간 경우)를 같은 비중으로 학습한다. KPI: Relative Gap Winner Recall · Leader Transmission Accuracy · Gap Closure Error · False Cheap Rate · Structural Gap Misclassification · Multi-Leader Consensus Accuracy · Relative Value Rank Regret · Leader Transmission Failure Rate. Future Leakage 금지(에피소드 당시 알 수 있던 정보만).

### 36.15 절대 규칙
특정 단지에 맞춰 정상비율 조정 금지 · 2021 고점 비율 자동 채택 금지 · 면적 다르면 보정(BUYER_CHOICE 는 총액 비교 + ㎡단가 프리미엄 별도 표시) · 학군 등 구조 프리미엄 무시 금지 · 호가로 Leader Rally 판정 금지 · 싸다는 이유만으로 추천 금지 · 무조건 평균회귀 가정 금지 · 사용자 언급 단지 가점 금지 · 기존 TOP10 보호 금지 · Future Leakage 금지 · UNKNOWN 중립값 채움 금지.

구현: `apt_engine/relative/{store,zones,gap}.py`, `tools/run_relative_gap.py`. 실행 결과·§35.27 보고 항목은 `RESEARCH_LOG_RELATIVE_GAP_v0.1.md`.

---

## 36. 변경 이력

### 2026-09-06a — KOSIS 수집(공사비지수·인구이동·소득·M2·환율)과 검증
- KOSIS 오픈API 로 건설공사비지수·시군구 인구이동·시군구 근로소득·M2·환율 수집(연구로그 §27.1). 순위 변수로는 인구이동(악화)·소득(자료 짧음)·공사비(잡음) 모두 미채택(§27.2).
- 거시 타이밍(§27.3): 전세가율(+0.94)이 5년 시장 수익을 지배. M2 증가율은 최고 분위 뒤가 가장 나빴고, 환율은 전세가율 통제 후 +0.47(독립 창 ≈9개, 참고), 공사비 급등기 뒤 약세. 현재 원/달러 1,527 은 관측 범위 밖 → §6 조건부 시나리오는 전세가율×금리 유지, M2·환율·공사비는 관측 지표로만 표기.

### 2026-09-05d — 정책·공급 자료 수집, 규제 사건연구, 용적률 정정, 모델 선택(안정형·공격형)
- 수집: 규제지역 연혁 2016-11-03~2025-10-16(조정대상·투기과열·투기지역·분양가상한제, 공고번호 기준, `rules/regulation_zone_history.csv` → DB `regulation_zone`), 시군구 미분양 월별 2007~2026(통계누리), HUG 시도 분양가 2015~2026. 청약경쟁률·인구이동·소득·건설공사비지수는 키/활용신청 대기. 호가는 약관상 수집하지 않음.
- §12 판정: 규제·분양가상한제 변수는 학습구간에 변동이 없어 walk-forward 불가 → 사건연구(연구로그 §25.2): 규제 지정은 하락을 만들지 않았고(후행 지표), 풍선효과는 2020-06·11 두 사건(시도 규제 비중 ≥70% & 상승국면)에서만 +10~32%p 실측, 해제는 가격을 살리지 못함. 미분양·분양가는 순위 예측력 없음(§25.4). → **정책 변수 미채택, 관측 변수로 앱 '앞으로' 탭에 노출**(`reg_balloon` 후보 조건: 비규제 & 시도 규제비중 ≥0.7 & 12개월 모멘텀 > 0).
- §14 입력 정정: `existing_far` 가 K-apt 관리비부과면적(지하 포함)÷대표 필지로 계산돼 과대(부평 동아1단지 219.5% → 실제 181%). `tools/refit_far.py` 로 주거전용면적×1.15÷대지(PROXY)로 전 단지 재산정, 외부 표기 확정값은 `rules/far_overrides.csv`. 확정은 건축물대장 총괄표제부 `vlRat` 필요.
- 앱: 모델 선택(기본 v0.8 / 안정형 / 공격형) 구조 추가(`tools/model_select.py` 가 선택·확인 구간으로 고름 — 결과는 연구로그 §26). 규제 상태·풍선효과 후보·용적률(추정) 표기.

### 2026-09-05c — 일반인용 앱 v3 · 전문가 이론 대입 실험
- 앱(`tools/build_app.py`, `reports/apt_app.html`) v3: 1차 표기를 일반인 말로(남는 돈·운 나쁘면·믿을 만함), '근거·검증' 패널(자료 출처·건수·walk-forward 성적·예측하지 않는 것), 단지별 '앞으로(호재·위험)' 탭. 호재는 §3 원칙대로 이름이 아니라 실측 사건(정비 단계·다음 단계 확률 §14.3, 신축 대비 배율 §14.6, 대장 선행 §35, 확산 계급 §35.6, 학원가·역세권·전세가율·하락기 낙폭)만 쓰고 "확인 안 된 소문은 넣지 않는다"를 화면에 명시. 배포: claude.ai 아티팩트(본인) + 웹서버 `/app`(초대 링크, `web/share_link.ps1` 가 카톡 자동 전송).
- §12 실험: '전문가 이론' 12종 → 변수 24개(`panel.EXPERT_GROUPS`), 결측 인지 부스팅(`exitprice/boost.py`), 목표 백분위화·분위변환, 앙상블을 walk-forward 로 비교(`tools/expert_theories.py`). 전월세 실거래가 2011년 시작이라 E 의 검증 연도는 실제로 2016~2021(이전 "2013~2021 평균"은 2017~2021 값이었음 — 정정). 결과·채택은 `RESEARCH_LOG_EXIT_PRICE_v0.1.md` §24.
- **채택: Exit Price v0.8 = E 변수 + 결측 인지 부스팅 ×3시드**(변수 추가 없음). 운영 기준(전체 행) Recall@20 0.36 → 0.44, 확인 구간(2019~21) 0.53 → 0.61, 폴백 사다리 폐지(커버리지 = NOW 전체 행), Bear/Bull 오차폭은 walk-forward 잔차 분위. 전문가 이론 변수 24개는 어느 것도 연도 간 재현되지 않아 미채택(브랜드·용적률·대지지분은 이미 가격에 반영). `tools/predict_exit_fallback.py --boost`.

### 2026-09-05b — 단지 규모 Gate(1,000세대), Exit Price v0.2, 투자시점
- §24 Stage B 에 1,000세대 미만 제외 규칙 확정. 수도권 좌표 단지 10,123 → 1,478(서울 400·인천 209·경기 869).
- Exit Price v0.2: 이론 변수(C) IC 0.32 · Recall@20 44% · 예측 상위 20% 의 71% 가 중앙값 이상. 상호작용·사이클 변수는 상대 순위 모델에서 제외(악화). 사이클은 §12 시장 수준 시나리오에만 사용.
- 투자시점: 진입 시점 전세가율이 이후 5년 시장 수익과 순위상관 0.96(n=11), 기준금리 −0.49. 2026-06 전세가율 0.56 은 시계열 최저 → 시장 Base 가정 낙관 편향 경고. 상세 `RESEARCH_LOG_EXIT_PRICE_v0.1.md` §6~7.
- Exit Price v0.3(1,000세대 이상 · 2007~2021 진입 · 확산 변수): 채택 E(비선형) IC 0.22 · Recall@20 40% · 중앙값이상 69%. 확산 계급 검증: 선행·빠른추종 계급 지속률 61~62%, 후행지는 5년 상대수익 −7%(상승국면 −14.5%) → 후행성은 회피 신호, 빠른추종×상승국면이 발굴 조건(§35.6 플래그·v0.4). 상세 §8~9.

### 2026-09-05 — Exit Price Engine v0.1 실행·채택
- §12 에 "가격 이론 → 변수" 표와 Walk-Forward 규약, 계급도·계급 상승 조건 정의 추가.
- 결과: 시장·모멘텀만 쓰면 순위가 거꾸로(IC −0.23, 평균회귀), 이론 변수 + 일자리(국민연금 사업장)로 IC +0.26 · 승자 포착률 40%. **시장 수준은 예측하지 않고 과거 분포(최저/중앙/상위20%)를 Bear/Base/Bull 가정으로 분리**한다. 계급 상승 조건 중 lift 1.5 를 넘는 '호재'는 아직 없음(역 개통 1.17 · 신축 입주 1.16 · 학원가 1.13), 이미 오른 시군구는 0.54.
- `rules/exit_price_2026.csv` 가 §13 TW 의 매도가 입력이 됐다(`invest/exit_price.py`). 상세: `RESEARCH_LOG_EXIT_PRICE_v0.1.md`.

### 2026-09-04c — Mispricing·Option Value 의 Terminal Wealth 결합
- `invest/exit_price.py`: §35 Mispricing(신뢰도 반영, Base·Bull 만)과 §14 Option Value(NOT_CALCULATED → N/A)를 매도가 시나리오에 넣는다. Base 는 현재가 무성장(§12 Fundamental Exit Price 미구현, 성장률 미가정).
- `scoring/weights.py`: `relative` 0.15 → 0, `redevelopment` 0.08 → 0 (점수 이중계산 제거). 순위 변화·TW 결과는 `RESEARCH_LOG_TW_COMBINE_v0.1.md`.
- 결과: 무성장 Base 에서 EXPECTED_TW > 0 후보 0개 → TW 순위는 저가순으로 퇴화. **§12 Exit Price Engine 이 다음 라운드 1순위.**

### 2026-09-04b — Relative Price Gap Engine DELTA 병합
- 신설 §35(급지·생활권 데이터 구축, 대장 자동 선정, Leader Set 4종, 역사 비율 밴드·국면별 정상비율, 구조/회복 분해, Transmission P, 합의, Mispricing, TOP50 결과표, 백테스트 KPI). §5 Price Runway 의 Leader Transmission 은 §35.7 로 연결.
- 변수 정규화: 기존 leader_kind 5종(LOCAL/PRICE/FLOW/CAPITAL_COHORT/METRO) → 4종(LOCAL/GRADE/UPPER_GRADE/BUYER_CHOICE). 기존 `relative_gap`(features/relative.py, 동일 시군구 비교단지 중앙값 비율) 은 §35.5 Observed Gap 의 전신 → 통일 대상(체크리스트).
- 구현: `apt_engine/relative/{store,zones,gap}.py`, `tools/run_relative_gap.py`; DB `relative_zone`, `zone_leader`, `complex.life_zone` 채움. 결과·보고 18항목은 `RESEARCH_LOG_RELATIVE_GAP_v0.1.md`.
- 기존 `relative` 모델(consensus 가중 0.15)이 relative_gap 을 점수로 직접 쓰는 것은 §35.0 과 충돌 → 이번 라운드 미변경, 체크리스트.

### 2026-09-04a — 정비사업 Option Value Engine DELTA v0.1 병합
- 신설: §14 전체. §2 실행순서 7단계·레이어 도식, §3 정비사업 문구 금지 목록·UNKNOWN≠0, §4 "7번째 CORE 아님", §5 Runway/Option 분리, §10 완료 후 대체재 집합, §11 EVENT_SPIKE_ONLY, §12 5년 시점 잔여 옵션가치, §13 시나리오 확률가중·Opportunity Cost·Hold vs Switch, §15 Good Buy의 옵션 처리, §17·§19·§22·§23·§24 정비사업 항목, §20 로컬 코드 우선순위 규칙.
- 변수 통일: `Optionality` → `OPTION_VALUE`; `current_far` → `existing_far`; `land_share_m2` → `land_share`; `redev.scenario.KEYS`(보수/기준/낙관) → `CONSTRUCTION_COST_SEVERE/STRESS/BASE`; `Premium.efficiency` → `option_already_priced_ratio`(§14.10 표).
- 섹션 재정렬: 구 §27(4회 중복) → §27·§28·§29·§30, 구 §28 → §31, 구 §29 → §32, 구 §31(순서 오류) → §34, 구 §30 → §33. 구 §14~§26은 +1(§15~§27).
- DELTA 중 제외·수정: DELTA §38 동아1차 예시 본문은 MASTER에 넣지 않고 연구로그로(특별대우 금지 규칙과의 충돌 회피); DELTA §16의 `P_policy + P_project + P_completion` 합산식은 상호배타 말단확률 트리로 대체; DELTA §33-B(구조적 사업성)는 랭킹 승격이 아닌 연구 우선순위로 한정.
- 구현: `tools/build_option_stage_registry.py` → `rules/option_stage_registry.csv`(Stage 매핑, 나머지 N/A). 코드 체크리스트·회귀 결과는 `RESEARCH_LOG_REDEVELOPMENT_OPTION_v0.1.md`.
