# 투자 교훈 DB (지시서 §77·§58·§59)

> 마지막 갱신 2026-08-31

---

## 0. 왜 표로 두는가

백테스트에서 배운 규칙을 **코드에 하드코딩하지 않기 위한 그릇**이다.

"전세가율 70% 이상이 좋더라" 를 코드에 박으면 두 가지가 나빠진다.

```
① 언제 왜 그렇게 정했는지가 사라진다
② 다음 백테스트가 반대 결과를 내도 아무도 안 고친다
```

표로 두면 근거·표본·검증 지역·검증 국면이 값과 함께 남는다.

---

## 1. 상태 네 가지

| 상태 | 뜻 |
|---|---|
| `HYPOTHESIS` | 가설. 아직 검증 안 됨 |
| `PROVISIONAL` | 일부 확인됨 |
| `CONFIRMED` | 검증됨 |
| `REJECTED` | 반증됨 |

**`CONFIRMED` 로 올리려면 스키마가 근거를 요구한다.**

```sql
CHECK (status != 'CONFIRMED'
       OR (sample_size IS NOT NULL AND evidence IS NOT NULL))
```

그리고 코드가 더 건다.

```
표본 200개 이상   AND   서로 다른 국면 2개 이상
```

한 국면에서만 맞는 규칙은 그 국면의 특성이지 투자 원리가 아니다.
상승장에서만 통하는 규칙을 CONFIRMED 로 올리면 다음 하락장에서 그대로 틀린다.

---

## 2. 씨앗 20개

지시서 §59 가 준 가설 20개가 전부 `HYPOTHESIS` 로 들어 있다.

```bash
apt lessons seed      # 넣기
apt lessons           # 보기
```

**하나도 CONFIRMED 가 아니다.** 실거래 백테스트가 안 돌았기 때문이다.
지금 이 표는 "우리가 무엇을 믿고 있는지" 의 목록이지 "무엇이 사실인지" 가
아니다.

---

## 3. 승격은 사람이 하지 않는다

```bash
apt lessons promote --key <lesson_key> --status CONFIRMED
```

이 명령은 조건을 못 채우면 `LessonError` 를 던진다. 표본이 모자라거나
국면이 하나뿐이면 거부한다.

**백테스트 결과가 기존 생각과 다르면 기존 생각을 버린다**(§74).
그래서 `REJECTED` 도 정상적인 결말이다. 가설이 틀렸다는 것을 기록으로
남기는 것이 지우는 것보다 낫다.

---

## 4. Feature 등록부와의 관계

둘이 비슷해 보이지만 다르다.

| | 무엇 | 승격 조건 |
|---|---|---|
| `investment_lesson` | **사람의 가설** | 표본 200 + 국면 2 |
| `feature_registry` | **계산되는 값** | Fold 2개 생존 |

Lessons 는 "왜 그럴 것이라 생각했나" 를 담고, 등록부는 "실제로 쓸모가
있었나" 를 담는다. 백테스트가 둘 다 갱신한다.

---

## 5. Control Pair 와의 관계 (§27)

`control_pair` 는 Lessons 를 **검증하는** 표다.

```
2019 상계주공13 소형  vs  2019 부개주공5 소형
당시 가격대는 비슷했는데 이후 성과가 갈렸다.  왜?
```

Lessons 에 있는 가설(Buyer Pool · Latent Movement · Price Ladder ·
Replacement Gap · Slope Persistence · Transmission · 공급)로 그 차이를
설명할 수 있는지 본다. 설명 못 하면 그 가설로는 부족한 것이다.

**단지 이름은 Feature 가 아니다.** `rules/research_set.csv` 에 데이터로만
있고, 엔진 코드에 이름이 나오면 테스트가 실패한다(§41·§49-2).

---

## 6. 지금 상태

```
씨앗 20개 전부 HYPOTHESIS
CONFIRMED 0개
Control Pair 1쌍 (단지 매칭 전)
```

수집이 끝나고 `backtest run` 이 돌면 여기가 바뀐다.
