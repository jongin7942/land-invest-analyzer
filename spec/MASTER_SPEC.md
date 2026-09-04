# 수도권 아파트 투자 추천 엔진 — MASTER SPEC

업데이트 기준일: 2026-09-03

## 1. 최종 목표

사용 가능한 자기자본 X를 기준으로, 실제 실행 가능한 `아파트 × 면적 × 타입/동호조건 × 실제 진입가격 × 평가일` 가운데 5년 후 세후·이자후·비용후 순자산을 가장 크게 만들 투자상품을 찾는다.

핵심 질문은 다음 하나다.

> 지금 이 가격에 사는 것이 같은 자기자본으로 가능한 모든 대안보다 좋은가?

단지 자체의 우수성보다 **현재 가격에서의 투자상품성**을 평가한다. 동일 단지도 진입가격이 다르면 다른 투자상품이다.

---

## 2. 실행 순서

1. Investable Universe 생성
2. 토지거래허가/실거주 Hard Gate
3. 자금조달·DSR/LTV·세금·비용 Feasibility Gate
4. 실제 매물/정상체결가격 Asset Availability Gate
5. CORE 투자지표 계산
6. 5년 Future Choice Set / Buyer Depth / Price Runway 계산
7. Liquid Exit Price 및 Terminal Wealth 추정
8. 동일 자기자본 대안 비교
9. CASH 포함 순위 산출
10. TOP10/TOP20 및 Good Buy 가격 제시

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
- 토허·실거주 제한은 점수 감점이 아니라 실행 Gate다.
- 현재 호가와 실거래를 혼합하지 않는다.
- 평형·타입을 섞은 연평균 가격을 사용하지 않는다.
- 단일 고가/저가 거래를 정상가격으로 사용하지 않는다.
- 과거 백테스트에서 미래 정보를 사용하지 않는다.
- 서울/경기/인천·신축/재건축 같은 라벨 자체에 보너스를 주지 않는다.

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

가격전파를 ‘대장 상승’ 자체가 아니라 **구매자 예산군 이동**으로 모델링한다.

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

---

## 13. Terminal Wealth

최종 순위는 가격상승률이 아니라 **5년 후 순자산 증가**로 결정한다.

`Terminal Wealth = 현실적 매도가 - 남은대출 - 양도세 - 매도중개비 - 보유기간 이자 - 취득비용 - 보유비용 - 수리/정비비 + 보유기간 현금흐름 + 미사용 현금 미래가치`

CASH도 하나의 실제 후보로 포함한다.

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

정비사업·교통호재 등 정성 `Optionality`는 직접 가점으로 사용하지 않는다. 검증된 사업확률·기간·분담금·순증가치를 시나리오별 Terminal Wealth에 반영할 때만 인정한다.

### 순위 안정성 출력

단일 점수와 단일 순위만 표시하지 않는다. 가중치·측정오차·시장성향을 흔든 반복 검증을 통해 다음을 함께 출력한다.

- 평균순위
- TOP10 생존율
- TOP5 진입률
- 불리한 경우 순위(P90)

순위 생존율이 낮은 후보는 확정 TOP10과 분리해 경계 후보로 표시한다. 구체적인 경계값은 Walk-Forward 백테스트로 정한다.

---

## 14. Good Buy 가격

적정 매수가를 과거 저점만으로 정하지 않는다.

미래 Liquid Exit Price와 목표수익, 거래비용, 세금, 이자, 실패확률을 반대로 역산한다.

즉:
> 다음 구매자가 받아줄 수 있는 미래가격에서 목표수익과 안전마진을 역산한 현재 매수가

동일 아파트도 매수가에 따라 순위가 실시간으로 달라져야 한다.

---

## 15. 토허 Hard Gate

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

## 16. 백테스트 목표

평가시점 후보 Universe를 고정한 뒤 2015/2017/2019/2021/2023 등에서 Walk-Forward 검증한다.

주요 KPI:
- Winner Recall
- Exit Price Error
- Rank Regret
- Missed Better Alternative
- Feature Survival
- Gate False Negative

‘그때 올랐는가’가 아니라 **같은 자기자본으로 더 좋은 선택이 있었는가**를 본다.

---

## 17. 현재 3억원 비거주 — 50개 유니버스 잠정 스크리닝 TOP10

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

## 18. 다음 연구 우선순위

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

---

## 19. 작업공간 이전 및 기준문서 운영 규칙

2026-09-03부터 이 문서를 아파트 투자엔진의 단일 기준문서로 사용한다. 이전 대화와 개별 산출물에 있는 내용은 다음 원칙으로 승계한다.

- 이 문서의 `확정 규칙`이 개별 연구 로그·과거 순위표와 충돌하면 이 문서를 우선한다.
- 연구 로그의 가설은 검증 전까지 정식 점수식에 넣지 않는다.
- 과거 TOP10/TOP20은 당시 유니버스·입력가격의 스냅샷이며 최신 추천으로 자동 승계하지 않는다.
- 새 백테스트에서 살아남은 규칙만 이 문서의 CORE 또는 Hard Gate로 승격한다.
- 이후 확정된 변경은 이 문서에 누적하고, 실험 결과와 폐기된 가설은 별도 연구 로그에 남긴다.

### 승계된 핵심 산출물

- `RESEARCH_LOG_AGGRESSIVE_v0.1.md`: 균형형·공격형·고공격형 가중치 민감도와 100회 반복 결과
- `RESEARCH_LOG_FCP_v0.1.md`: Future Choice Set/FCP 설계, 중복 변수 제거, 50개 유니버스 확장 결과
- `3억_비거주_TOP20_투자판단표_2026-08-31.xlsx`: 초기 정성 TOP20과 동탄 출발 임장시간
- `아내와_함께보는_3억_비거주_아파트_TOP20_2026-08-31.xlsx`: 쉬운 설명용 3억원 결과표
- `직원약사와_함께보는_1억_비거주_아파트_TOP20_평택출발_2026-08-31.xlsx`: 1억원·평택 출발 결과표

## 20. 사용자 시나리오 레지스트리

기본 조건은 `비거주 투자 · 5년 보유 · 수도권 · 토허/실거주 Hard Gate · 세후/이자후/비용후 Terminal Wealth`다.

| 시나리오 | 자기자본 | 출력 | 출발지/용도 | 상태 |
|---|---:|---|---|---|
| 직원약사형 | 1억원 | TOP20 + 쉬운 설명 | 평택 출발 | 기존 결과 있음, 최신 Gate 재계산 필요 |
| 기본형 | 3억원 | TOP10/TOP20 | 동탄 출발 | 50개 정성 유니버스까지 확장 |
| 확장형 | 5억원 | TOP20 | 필요 시 동탄 출발 | 최신 전수 재계산 필요 |
| 고자본형 | 10억원 | TOP10/TOP20 | 필요 시 동탄 출발 | 최신 전수 재계산 필요 |

투자금이 바뀌면 기존 순위와 Capital Efficiency를 재사용하지 않는다. 같은 후보라도 대출·세금·미사용 현금의 미래가치가 달라지므로 전체 Feasibility와 Terminal Wealth를 다시 계산한다.

## 21. 현재 데이터·정책 블로커

### 실행 전 필수

1. 매매·전월세 원자료 240개월 수집 완료 및 면적·타입·동호조건 정규화
2. 서울·경기·인천 `ALL_BUYERS` 토허와 실거주 의무의 시행일·종료일·필지 범위 검증
3. 실제 차주 조건에 따른 LTV·DSR·주택담보대출 총액한도 계산
4. 취득세·양도세·농특세·인지세·중개보수·법무비·수리비·보유비용의 시행일별 규칙 검증
5. 현재 매매 호가·최근 실거래·전세 호가·최근 전세 실거래를 분리 수집하고 정상체결가격 산출

### 정책값 검증 원칙

- 공식 원문과 부칙으로 시행일을 확인하기 전에는 `NEEDS_VERIFICATION`을 유지한다.
- 특히 6억원 이하 1%, 6~9억원 2%, 9억원 초과 3% 체계의 과거 시행일과 6~9억원 구간 산식 전환 시행일은 원문 대조 후 확정한다.
- 외국인 대상 토허를 국내 일반 매수자 `ALL_BUYERS` 토허로 오인하지 않는다.
- 행정구역 개편으로 코드가 바뀐 지역은 필지 단위 고시가 없으면 보수적으로 Gate하되 `scope_uncertainty`를 표시한다.
- 정책 데이터가 0건이거나 범위가 비어 있으면 통과로 보지 않고 `UNKNOWN/NEEDS_CHECK`로 처리한다. 다만 전체 화면이 비는 것을 막기 위해 실행순위와 조사대기 목록을 분리한다.

## 22. 다음 구현 라운드

1. Hard Gate 데이터부터 완성한다: 토허 → LTV/DSR → 세금·비용 → 실제 매물.
2. 1억·3억·5억·10억원별 Investable Universe를 블라인드로 다시 생성한다.
3. 상위 후보의 실거래 원장·전세·거래회전·동호조건을 검증한다.
4. 가격별 Buyer Depth와 후보별 실제 Future Choice Set 10~20개를 구축한다.
5. 진입가격 민감도와 Good Buy 가격을 계산한다.
6. 2015/2017/2019/2021/2023 Walk-Forward로 CORE와 Exit Price를 검증한다.
7. 검증 통과 후보만 실행가능 TOP10/TOP20으로 승격한다.
8. 화면에는 평균순위·TOP10 생존율·TOP5 진입률·P90·Good Buy·Confidence를 함께 표시한다.

UI는 현재 단계에서 Streamlit 기능을 유지한 Fluent형 외관을 사용하고, 서비스 완성 후 React 프런트엔드로 분리한다. 시작화면과 결과화면은 전문성을 우선하되 비전문가도 판단 근거를 이해할 수 있게 설명한다.

---

## 23. Universe Expansion Engine — 확정 구조

### 목적
현재의 50개 후보는 연구용 정성 유니버스일 뿐이며 최종 추천 Universe로 사용하지 않는다. 사용자가 한 번도 언급하지 않은 단지가 #1로 올라올 수 있어야 객관적인 엔진으로 본다.

### 데이터 원천
1. 국토교통부 아파트 매매 실거래가 API: 법정동코드×계약월 단위로 수도권 매매 원장 수집
2. 국토교통부 아파트 전월세 실거래가 API: 전세·월세 원장 및 Rent-to-Buy/Downside 분석
3. K-apt 공동주택관리정보: 단지코드, 세대수, 사용승인일, 면적구성, 주차·관리 특성 등 기본정보 결합
4. 토허·대출·세금·비용 정책테이블: 평가일 기준 Hard Gate와 Terminal Wealth 계산
5. 현재 호가 데이터는 실거래와 별도 필드로 유지하며 실행가격 확인용으로만 사용

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

### Stage C — Quick Scan 1,000
저비용 집계로 후보를 넓게 남긴다. 목표는 좋은 후보 누락 최소화다.

### Stage D — Deep 300
P25/Median/P75, Money Arrival, Buyer Budget Migration, Leader Transmission Failure, Future Choice Set 초안, 공급·전세·동호조건 정규화를 수행한다.

### Stage E — Terminal Wealth 100
하락/보수/기준/강세 시나리오별 Liquid Exit Price, 이자, 세금, 대출잔액, 매도비용, 미사용현금 미래가치를 계산한다.

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

## 24. Universe Expansion 데이터 수집 구현 우선순위

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

## 25. 공공데이터 API 연결 상태 — 2026-09-03

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


## 26. 외부 탐색 후보 유입 규칙 — Claude 30개 후보 추가 (2026-09-03)

사용자가 별도 Claude 스캔에서 발견한 30개 후보를 **Discovery Candidate Pool**에 추가한다. 이 목록의 Claude 점수(75~84)는 현재 MASTER의 CORE 점수와 정의·가중치가 다르므로 **절대 이식하지 않는다**. 매수가·실투자금도 원자료 검증 전에는 `SOURCE_REPORTED`로만 보관한다.

적용 원칙:
- 후보 이름/지역/제시 매수가/제시 실투자금은 탐색 힌트로만 사용한다.
- 사용자 또는 Claude가 골랐다는 이유로 Ranking 가점 금지.
- 기존 Universe와 중복 후보는 동일 기본키로 병합하고 연구횟수는 점수에 반영하지 않는다.
- 토허/실거주 Hard Gate를 먼저 재검증한다. 수원 영통구 후보 등 실행불가 후보는 Pure Alpha 연구목록에는 남기되 비거주 Executable Ranking에서는 제외한다.
- 호재 미반영 목록이므로 교통·산업·정비사업 등은 이름 자체로 가점하지 않고, 검증된 확률×순가치만 Terminal Wealth에 반영한다.
- 신규 후보는 실거래·전세·K-apt·정상체결가격을 동일 기준으로 채운 뒤 Quick Scan에 진입한다.
- Claude 원점수는 `external_source_score`로 보관 가능하나 최종점수/순위 계산에는 사용하지 않는다.

이번 30개 추가로 수동/외부 Discovery Pool의 폭은 넓어지지만, `FULL CAPITAL UNIVERSE` 표기는 금지한다. 자동 수도권 전수 Universe가 완성될 때까지 `PARTIAL VERIFIED UNIVERSE`를 유지한다.


---

## 27. Discovery Universe 수동·웹 확장 상태 — 2026-09-03

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


## 27. Discovery Universe 진행상태 — 2026-09-03 19시대

- 수동/웹 기반 블라인드 Discovery Registry를 323개에서 **346개 단지×면적 후보**로 확장했다.
- 신규 후보는 기존 후보에 가점을 주는 방식이 아니라 동일 생활권·유사가격·대체평형을 기계적으로 펼쳐 생성한다.
- 현재 346개 Registry는 `DISCOVERY_ONLY`이며 최종 순위가 아니다.
- 다음 단계는 후보 확대를 계속하면서 동시에 최소비용 Hard/Data Gate를 적용해 첫 Blind Quick Scan을 시작하는 것이다.
- 기존 50개 및 기존 TOP10은 Seed 추천이 아니라 회귀 테스트 세트로만 유지한다.


---

## 27. 346 Discovery Universe Quick Scan — 2026-09-03

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

### 산출물
- `QUICK_SCAN_346_v0.1.csv`
- `DEEP_SHORTLIST_60_v0.1.csv`
- `PROVISIONAL_TOP30_3EOK_v0.1.md`

### 남은 Hard Blocker
Terminal Wealth와 Executable TOP20은 실제 차주 DSR, 최신 정책테이블, 현재 호가/매물 원장이 필요하다. 이 값이 없을 때 임의 가정으로 최종순위를 확정하지 않는다.

---

## 28. 대규모 선택 로직 시뮬레이션 — 2026-09-04

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

## 29. Missingness-aware Ranking — 2026-09-04 시뮬레이션 반영

### 핵심 원칙
`UNKNOWN != 중간값`.

현재 Discovery/Quick Scan에서는 일부 미측정 항목이 `0.50`, `0.48` 같은 중립 placeholder로 들어가 있다. 앞으로는 이를 실제 중간 수준으로 해석하지 않는다.

각 핵심 변수는 최소 다음 필드를 가진다.
- `value`
- `measurement_status = VERIFIED | PROXY | UNKNOWN`
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
## 31. Walk-Forward Backtest v0.1 — 2026-09-04

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

## 30. Universe Expansion v0.3 — 2026-09-04

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
