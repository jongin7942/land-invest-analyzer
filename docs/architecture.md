# 구조 (지시서 §77)

> 마지막 갱신 2026-08-31 · 엔진 v0.15.0 · 모듈 140개 · 테스트 1091개

---

## 0. 두 프로그램은 DB 가 다르다

```
land_invest.db     토지 공매 급매 탐지기 (기존)
apt_invest.db      아파트 투자분석 엔진   ← 이 문서
```

아파트 쪽 마이그레이션이 **잘 돌아가는 토지 DB 를 건드릴 수 없어야** 한다.
`apt_engine/` 은 독립 패키지이고 토지 코드를 import 하지 않는다.

---

## 1. 계층

```
  수집          collectors/     data.go.kr · V-World
    ↓
  정규화        price/          이상치 제외 → 대표가격 → Normal Executable Price
    ↓
  컷오프        blind/cutoff.py 그 시점에 몰랐던 것을 못 읽게 막는다
    ↓
  Feature       features/       49개 등록 · 4 State
    ↓
  스코어링      scoring/        Consensus 9모델 · EarlyAlpha
    ↓
  랭킹          ranking/        Executable / Pre-Breakout Watch
    ↓
  백테스트      backtest/       walk-forward → 가중치 학습 → CORE 승격
```

백테스트가 **맨 아래가 아니라 옆에 있다.** 랭킹 결과를 채점해서 위쪽
Feature 의 티어와 가중치를 바꾼다. 그 되먹임이 §74 가 요구한 순서다.

---

## 2. 규칙을 코드가 아니라 구조로 막는다

이 프로젝트의 핵심 설계 판단이다. "조심하자" 로는 안 막힌다.

| 막으려는 것 | 어떻게 |
|---|---|
| 그 시점에 몰랐던 데이터 사용 | `GuardedConnection` 이 컷오프 없는 쿼리를 **거부** |
| 백테스트 정답지를 Feature 가 읽음 | `ANSWER_KEY_TABLES` — 어떤 조건을 붙여도 거부 |
| 누출이 있는데 성공으로 기록 | 스키마 CHECK: 검사 통과 전엔 `COMPLETE` 불가 |
| 관심단지 우대 | `blind/universe.py` 가 `watchlist` 를 **import 조차 안 함** + AST 테스트 |
| 특정 단지에 맞추기 | 엔진 코드에 단지명 문자열이 있으면 테스트 실패 |
| 한 Feature 를 두 번 세기 | 등록부에서 role 이 하나 · 교집합 테스트 |
| 근거 없이 CORE 승격 | 스키마 CHECK: `survived_folds >= 2 AND promoted_run IS NOT NULL` |
| 결측을 0 으로 | `Feature.missing()` → None · 스키마 CHECK 로 사유 필수 |
| 값 없이 타입 쪼개기 | CHECK: `observed_months >= 6 AND sample_n >= 10` |

**스키마 CHECK 를 많이 쓴 이유**: 코드는 고칠 수 있지만 제약은 INSERT 자체를
실패시킨다. 성적을 먼저 보고 나서 규칙을 완화하고 싶어지는 순간을 막는다.

---

## 3. 패키지

| 패키지 | 무엇 |
|---|---|
| `db/` | 마이그레이션 16개 · 커넥션(WAL · 읽기전용 지원) |
| `collectors/` | 실거래 · K-apt · 좌표 · 단지명 매칭 |
| `price/` | 이상치 제외 · 대표가격 · 스냅샷 · **정규화(§35)** |
| `blind/` | 컷오프 가드 · Universe · 익명화 |
| `features/` | base · registry · momentum · regime · flow · supply · jeonse · entry · catalyst · **bands · stretch · cycle · stage · leader · demand** |
| `scoring/` | normalize · weights · models(9종) · consensus · kill · thesis · **early_alpha** |
| `ranking/` | pipeline · lists · explain · **delta_pipeline · executable · rotation · frontier · uncertainty** |
| `backtest/` | windows · outcome · kpi(19종) · usefulness · leakage · synthetic · runner · **sanity** |
| `cash/` `invest/` `cashflow/` | 실투자금 · 버킷 · ROE · **CASH 후보** · IRR |
| `regulation/` `tax/` | 규제지역 · 토허 · 세금 |
| `relative/` | 비교단지 · 가격사다리 · **Leader 망 생성** |
| `redev/` | 재건축 사업성 · **NakedApartmentValue** |
| `repo/` | 테이블별 접근 · Lessons · **Control Pair** |

굵은 것이 이번 DELTA UPGRADE 에서 추가된 것이다. **기존 것은 하나도 지우지
않았다** — 4 State 를 위에 얹고 기존 7그룹을 DIAGNOSTIC 으로 내렸다.

---

## 4. 두 파이프라인이 공존한다

```
ranking/pipeline.py         Consensus 9모델 (기존)
ranking/delta_pipeline.py   그 위에 Stage · EarlyAlpha · CASH · Coverage
```

합치지 않은 이유는 §49-15 다 — 정상 작동하는 기능을 이유 없이 재작성하지
않는다. `delta_pipeline` 이 `pipeline` 을 **호출해서** Universe 와
Capital Gate 를 받아 온다.

---

## 5. 데이터 무결성 축

모든 계산 결과가 `Calc` 를 들고 다닌다.

```
value · unit · formula · inputs · intermediates · evidence · grade
grade ∈ {CONFIRMED, ESTIMATED, SCENARIO}
```

`derive()` 로 합칠 때 **가장 약한 등급이 전파된다.** ESTIMATED 하나가 섞이면
결과는 CONFIRMED 가 될 수 없다.

Feature 는 네 값을 분리해서 들고 있다(§50).

```
value        숫자
confidence   얼마나 믿을 수 있나
status       OK / DATA_MISSING / LOW_CONFIDENCE / NEEDS_VERIFICATION
calc         어떻게 나왔나
```

---

## 6. CLI

31개 서브명령. 자주 쓰는 것:

```bash
apt init                        # 마이그레이션
apt collect trades --months 240 # 수집 (종인님 PC 에서만)
apt match ; apt snapshot        # 매칭 · 대표가격
apt leaders build               # Leader 망
apt today --cash 3 --columns    # 오늘 실행 가능 후보 (§37)
apt backtest plan / run / sanity / weights
apt rank --weights backtested   # 기존 3리스트
```

---

## 7. 테스트가 지키는 것

1091개 중 상당수가 **금지 규칙**이다. 기능이 아니라 하면 안 되는 것을 고정한다.

```
tests/test_blind.py      누출 · 익명성 · 단지명 하드코딩
tests/test_backtest.py   정답지 격리 · 누출을 심어서 잡히는지
tests/test_delta.py      §49 의 15개 금지 중 코드로 막을 수 있는 것
tests/test_land_regression.py  토지 프로그램이 안 깨졌는지
```

특히 `test_미래를_심으면_잡는다` 는 **컷오프 이후 가격을 3배로 부풀리고 그걸
읽는 코드를 검사가 잡아내는지** 확인한다. 이 테스트가 실패하면
"누출 없음" 은 아무 뜻도 없는 문장이다.
