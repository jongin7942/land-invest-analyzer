# 점수를 어떻게 매기는가 (지시서 §77)

> 마지막 갱신 2026-08-31 · 엔진 v0.15.0

이 문서의 목적은 **점수 하나가 어디서 나왔는지 되짚을 수 있게** 하는 것이다.
숫자가 마음에 안 들 때 "이 값이 왜 이렇지" 를 코드까지 따라갈 수 있어야 한다.

---

## 0. 가장 중요한 원칙 — 셋을 절대 합치지 않는다

```
Expected Alpha  78      얼마나 좋아 보이는가
Risk            42      무엇이 잘못될 수 있는가
Confidence      83      그 판단을 뒷받침하는 데이터가 얼마나 있는가
```

Alpha 78 / Confidence 30 은 **Alpha 40 과 완전히 다른 상태다.**
전자는 "좋아 보이는데 근거가 약하다", 후자는 "그냥 별로다" 다.
하나로 합치면 이 구분이 사라진다(§36).

그래서 데이터 품질이 좋다는 이유로 **점수가 오르지 않는다.** Confidence 만
오른다. 반대도 마찬가지다.

---

## 1. 순서 — 이 순서가 규칙이다

```
① Blind Universe        수도권 전체. 이름 없이, 관심단지 표 없이
② Capital Gate          자기자본으로 실제 살 수 있는가        ← 점수 계산 전
③ Feature 41종          4 State 로 묶임
④ Stage 분류            8단계 + 4분면
⑤ EarlyAlpha            Alpha / Risk / Confidence
⑥ CASH 와 비교          못 이기면 TOP 에 안 들어감
⑦ Executable / Watch    두 화면으로 분리
⑧ Coverage · 시장온도
```

**②가 ③보다 먼저인 것이 중요하다.** 3억으로 못 사는 20억 아파트를 점수 매기는
것은 낭비이고, 더 나쁘게는 "좋은데 못 산다" 는 후보가 상위에 남아 판단을 흐린다.

**⑥이 마지막에서 두 번째인 것도 규칙이다.** 점수를 다 낸 뒤에 비교해야
"이 후보가 몇 등이고 CASH 는 그 사이 어디" 를 말할 수 있다.

---

## 2. Feature — 4 State

49개가 등록부(`features/registry.py`)에 있고, 각각 **State 하나 · 역할 하나**를
갖는다.

| State | 개수 | 질문 |
|---|---:|---|
| CHEAPNESS | 5 | 현재 가격이 실제로 저평가되어 있는가 |
| MOVEMENT | 15 | 실제 구매력이 이 후보로 이동하기 시작했는가 |
| SUSTAINABILITY | 8 | 현재 움직임이 지속될 수 있는가 |
| STRETCH | 12 | 이미 기대가 가격에 과도하게 반영되었는가 |
| GATE | 2 | 살 수 있는가 · 데이터가 최소선을 넘는가 |
| CONTEXT | 7 | 점수에 직접 안 들어가고 다른 값의 입력이 됨 |

역할은 GATE / ALPHA / RISK / CONFIDENCE / CONTEXT 중 하나다.
**한 Feature 가 ALPHA 이면서 RISK 일 수 없다.**

### 왜 이 규칙이 필요한가

전에는 다섯 개가 양쪽에 있었다.

| Feature | ALPHA 에서 | RISK 에서 |
|---|---|---|
| `entry_position` | `value` 모델의 유일한 입력 | `kill.상대고평가` |
| `discovery_lag` | `momentum` 감점 | `kill.급등후매수` |
| `supply_ratio_2y` | `supply` 모델 | `kill.공급충격` |
| `downside_defense` | `jeonse` 모델 | `kill.전세약화` |
| `transaction_quality` | `risk` 모델 | `kill.거래질악화` |

Kill 은 감점이 아니라 배제라 산술적 이중가산은 아니었지만, **같은 신호가
순위와 생존 판정을 둘 다 움직였다.** 지금은 공급 감점이
`effective_supply_risk` 하나로만 나가고 원시 비율은 CONTEXT 로 내려갔다.

---

## 3. EarlyAlpha — 곱이지 합이 아니다

```
EarlyAlpha ≈ RemainingRecoverableGap
           × PriceBandMigration
           × TransmissionProbability
           × BuyerPool
           × DownsideAnchor
```

**곱인 것이 핵심이다.** 하나라도 0 이면 전체가 0 이다. 합으로 만들면
"회복 가능한 격차가 전혀 없는데 다른 게 좋아서 높은 점수" 같은 후보가 생긴다.
찾는 것은 다섯이 **동시에** 성립하는 자리다.

곱셈 항이 3개 미만이면 **점수를 만들지 않는다.** 하나만 있으면 그건
EarlyAlpha 가 아니라 그 Feature 하나다.

### 가중치

가중 기하평균의 지수로 들어간다. 그런데 **이 모듈은 가중치를 갖고 있지 않다.**
구조만 있고 숫자는 `backtest/usefulness.py` 가 채운다. 학습 전에는 균등이고
결과에 `가중치 임시(heuristic)` 이 붙는다(§21).

### 감점

```
PriceStretch · EntryRisk · EffectiveSupplyRisk
· PersistentCheapness · TransmissionFailure
```

전부 STRETCH/RISK 쪽이고 **위 곱셈 항과 교집합이 없다.** 테스트가 검사한다.

---

## 4. 정규화 — 절대 임계값을 쓰지 않는다

"전세가율 70% 이상이면 좋다" 같은 기준을 쓰지 않는다. 시장 전체가 움직이면
그 기준이 통째로 틀리기 때문이다. 대신 **횡단면 백분위**를 쓴다.

```
percentile_rank(values, higher_is_better=...)
```

- 결측은 **제외**한다. 0 으로 채우지 않는다.
- 동점은 평균 순위를 준다.
- winsorize 는 극단값을 **경계로 옮기고** 버리지 않는다(표본이 5개 미만이면 아무것도 안 한다).

---

## 5. 신뢰도 — 가장 약한 것에 끌려간다

여러 신호를 합칠 때 **기하평균**을 쓴다. 산술평균이면 하나가 0 이어도 나머지가
받쳐서 높게 나온다. 기하평균은 가장 약한 것이 전체를 끌어내린다.

```
combine(0.9, 0.9, 0.1)  →  0.43   (산술평균이면 0.63)
```

신뢰도를 만드는 것:
- 표본 수 (`sample_confidence`)
- 최신성 (`freshness_confidence`, 반감기 6개월)
- 모델 커버리지 (9모델 중 몇 개가 계산됐나)
- Neighbour Confirmation · 경로 확인 · Reset 단계 (§10 — **Alpha 가 아니라 여기로**)

`usable` 은 신뢰도 0.35 이상일 때만 참이다. 그 아래는 값이 있어도 안 쓴다.

---

## 6. Stage — 점수가 아니라 라벨

```
DORMANT · PRE_BREAKOUT · EMERGING · CONFIRMED
MATURE · EXHAUSTED · VALUE_TRAP · CHASE · UNKNOWN
```

**Stage 와 Investment Score 를 혼동하지 않는다.** 좋은 아파트라도 EXHAUSTED 면
신규매수 순위는 낮다. 랭킹은 Stage 를 필터로 쓴다.

판정 순서가 규칙이다. **VALUE_TRAP 검사를 PRE_BREAKOUT 보다 먼저** 한다.

```
싸고 + 안 움직임 + 오래 그랬음   →  VALUE_TRAP
싸고 + 안 움직임                 →  DORMANT
싸고 + 바닥이 조용히 움직임      →  PRE_BREAKOUT
```

싸다는 이유만으로 PRE_BREAKOUT 이 되지 않는다(§49-8). Movement 증거
(P25 이동 · Slope 지속 · 거래 회복)가 있어야 한다.

**값을 모르면 DORMANT 가 아니라 UNKNOWN 이다.** 모르는 것을 "움직임 없음" 으로
세면 데이터가 부족한 단지가 전부 VALUE_TRAP 이 된다 — 그건 판정이 아니라
데이터 공백이다.

---

## 7. 매수가 구간 — 점수 하나가 아니라 가격대

```
≤ 2.85억  STRONG BUY
≤ 9.50억  BUY
≥ 19.0억  DO NOT BUY
```

그리고 **Competitive Buy Price**: 같은 자기자본으로 살 수 있는 다른 후보가
좋을수록 이 후보의 최대 매수가를 낮춘다. 굳이 이걸 살 이유가 줄기 때문이다(§39).

---

## 8. 순위 — 숫자 하나가 아니라 구간

```
3위 (구간 2~9위)
```

점수 47.7 / 47.5 / 47.2 가 붙어 있으면 3위와 5위는 사실상 같은 자리다.
각 후보의 신뢰도만큼 점수를 흔들어 200번 매기고 95% 구간을 낸다(§52).

신뢰도가 낮을수록 구간이 넓어진다 — Alpha/Confidence 분리가 순위에서도 보인다.
구간이 후보 수의 절반을 넘으면 `⚠ 순위가 불안정합니다` 가 붙는다.

**정렬 키에 이름이 들어가지 않는다.** 동점도 `complex_id` 로 깬다(§1).

---

## 9. CASH 와의 비교 — 마지막 관문

> "지금 이 가격에 이것을 사는 것이, 현재 자기자본으로 살 수 있는 모든 대안
>  및 CASH 보다 좋은가?"

YES 가 아니면 TOP 에 넣지 않는다. **모르면 YES 가 아니다.**

현금 수익률(Cash Hurdle)은 **추정하지 않는다.** 프로필에 없으면 CASH 순위를
만들지 않는다 — 0% 로 가정하면 현금이 항상 최악이 되어 §3 자체가 무의미해진다.

남는 현금도 버리지 않는다. 5억으로 실투자금 3억짜리를 사면 2억이 남고,
그 2억의 수익까지 넣어야 **같은 자기자본끼리** 비교가 된다(§2).

---

## 10. 판정 기준 — 관측이 아닌 숫자

이 문서에 나온 경계값은 전부 "이 정도면 그렇다고 보자" 로 정한 것이다.
관측된 분포가 아니다. 코드에 `THRESHOLD_NOTE` 로 표시돼 있고,
전체 목록은 `docs/current_limitations.md` 6절에 있다.

**백테스트가 이 값들을 대체할 자리다.** 그 전까지 나오는 점수는 순위의
근거가 아니라 배관 검증 결과다.

---

## 되짚는 법

점수 하나가 왜 그런지 보려면:

```bash
apt today --cash 3 --verbose          # 1위 상세 (Alpha 구성 · 감점 · 없는 항목)
apt today --cash 3 --columns          # 26컬럼 전부
apt today --cash 3 --show-dropped     # 왜 빠졌는지
```

모든 Feature 는 `calc` 를 들고 있고, 거기에 공식·중간값·근거·등급이 있다.
