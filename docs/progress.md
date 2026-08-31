# Progress Log

지시서 §79 산출물. Phase 가 끝날 때마다 append 한다. 위가 최신.

---

## Phase 1 — Audit (2026-08-31) · 완료

### 구현 내용
- `docs/architecture_audit.md` — 디렉터리·수집모듈·스키마 42테이블·기구현/미구현·중복 분석
- `docs/gap_matrix.md` — 지시서 81항목 대비 GAP (✅24 / 🟡17 / ⬜26 / 🔴14)
- `build_static.py` 수정 — `docs/` 를 통째로 `rmtree` 하던 것을 생성물만 지우도록.
  지시서 §77 이 요구한 `docs/*.md` 가 정적사이트 빌드 한 번에 사라지는 구조였다.

### 테스트 결과
733개 통과. 회귀 테스트 2개 추가(`docs` 보존 · 소스에 `rmtree(DOCS_DIR)` 없음).

### 데이터 부족
`trade` 0건 · `jeonse_contract` 0건 · `complex` 0건.
data.go.kr 이 이 작업 환경에서 차단돼 수집은 사용자 PC 에서만 가능하다.
→ 🔴 14개 항목(백테스트·가격전이·Winner Recall 등)이 여기서 막힌다.

### 발견한 오류
1. `build_static.py` 의 `docs/` 전체 삭제 (위)
2. `apt_engine/scoring` `sensitivity` `reverse` `narrative` 가 빈 스텁

### 다음 단계
Phase 2 이전에 P0(누출 방지)을 먼저 한다 — 랭킹을 만든 뒤 붙이면 이미 오염된
모델을 검증할 방법이 없기 때문이다.

---

## Phase 1.5 — P0 반칙 방지 (2026-08-31) · 완료

지시서가 "절대 원칙" 으로 지정한 두 가지를 **코드가 막게** 했다.

### 구현 내용

**마이그레이션 012**
- `watchlist` — 사용자 관심단지. **랭킹 산출물과 물리적으로 분리**
- `source_conflict` + `source_tier` — 출처 충돌을 덮어쓰지 않고 양쪽 보존, 6단계 등급
- `ranking_run` + `ranking_entry` — 시점별 랭킹 스냅샷(§64·§66).
  `weights_source ∈ (HEURISTIC, BACKTESTED)` 로 **heuristic 결과를 학습 결과처럼
  보여주지 못하게** 했다. `data_grade='SCENARIO'` 고정
- `investment_lesson` — Lessons DB. CONFIRMED 는 근거·표본이 있어야 저장됨(CHECK)

**`apt_engine/blind/`**
- `cutoff.py` — as-of 컷오프. 날짜 컬럼이 있는 테이블을 컷오프 없이 조회하면
  `LookAheadError`. 스키마의 모든 테이블을 `DATED_TABLES` / `TIMELESS_TABLES` 로
  분류하게 하고, 등록하지 않은 테이블 조회도 거부한다.
  **신고 지연**(실거래 계약 후 30일 내 신고)을 반영한 `observable` 컷오프를 둬서,
  "그날 아직 신고되지 않은 거래" 를 쓰지 않는다.
- `anonymize.py` — 익명 ID + Placebo Test 지문
- `universe.py` — Blind Candidate Generation. **watchlist 를 조회하지 않고**,
  `UniverseRow` 에 단지명을 담을 칸 자체가 없다. 정렬도 이름이 아니라 id 순

**`apt_engine/repo/lessons.py`** — §59 의 20개 가설을 전부 `HYPOTHESIS` 로 seed.
`CONFIRMED` 승격에 표본 200건 이상 + 서로 다른 시장국면 2개 이상을 요구한다.
CLI `lessons seed / list / promote`.

### 테스트 결과
733개 통과 (신규 29개). 주요 항목:

| 테스트 | 무엇을 막나 |
|---|---|
| 컷오프 없이 실거래 조회 → 거부 | look-ahead |
| 스키마 모든 테이블이 시점 분류에 등록됨 | 새 테이블 등록 누락 |
| 미래 스냅샷은 후보에 없음 | look-ahead |
| 신고 지연 반영 (2023-01-01 → 202211 까지만) | 미신고 거래 사용 |
| watchlist 추가해도 후보·순서 동일 | user-interest leakage |
| blind 계층이 watchlist 를 SQL 로 읽지 않음 (AST) | 〃 |
| 단지명을 바꿔도 결과 동일 (Placebo) | 이름 누출 |
| 엔진 코드에 특정 단지명 하드코딩 없음 (AST) | §73 |
| CONFIRMED 승격에 표본·국면 요구 | 몇 사례로 규칙 확정 |
| 랭킹은 시점별 저장, SCENARIO 등급 고정 | §66 덮어쓰기 |

### 데이터 부족
변함없음. 누출 방지 구조는 합성 데이터로 전부 검증했다.

### 발견한 오류
- `AsOf` 를 월 단위 스냅샷에 적용할 때 "진행 중인 달" 을 포함하면 그 달 말에
  신고될 거래를 미리 쓰게 된다 → **마지막으로 완료된 달**까지만 보도록 수정

### 다음 단계
Phase 2 (Canonical Data Model) — `complex` 에 §2 가 요구하는 컬럼
(행정동·역거리·업무지 접근성·학군·생활권) 추가와 PropertyResolver.
그다음 Phase 3~7 을 heuristic 가중치(`weights_source='HEURISTIC'`)로 올리고,
데이터가 들어오면 Phase 8 백테스트가 그 가중치를 대체한다.

---

## Phase 2 — Canonical Data Model + PropertyResolver (2026-08-31) · 완료

### 구현 내용

**마이그레이션 013**
- `complex` 확장: `admin_dong` `life_zone` `nearest_station_id/m` `canonical_id`
- `complex_alias` — 이름 변경·별칭. **근거(reason)와 등록자가 NOT NULL**,
  `valid_from/to` 로 그 이름이 언제 유효했는지를 남긴다
- `complex_attribute` — **값마다 출처가 붙는 속성 테이블**.
  학군·생활권·업무지 접근성처럼 공식 API 가 없어 사람이 넣는 값들.
  컬럼으로 만들지 않은 이유: 값마다 출처·시점·신뢰도가 따로 붙어야 하고(§3),
  항목이 계속 늘어난다
- `job_center` + `complex_job_access` — 업무지 접근성. **직선거리로 대체하지 않는다**
  (통근은 직선거리가 아니라 환승·배차가 정한다). `method` 로 무엇으로 쟀는지 남긴다
- `life_zone` + `life_zone_adjacency` — 생활권. 행정구역이 아니라 실제 대체관계.
  `zone_a < zone_b` CHECK 로 같은 관계가 두 번 저장되는 걸 막는다

**`apt_engine/resolver.py` (PropertyResolver)**
- 이름 → 단지. **애매하면 붙이지 않는다**(`AMBIGUOUS`).
  아무거나 고르면 그 뒤의 가격·수익률이 전부 다른 단지 것이 된다
- `as_of` 를 주면 **그 시점에 유효했던 별칭만** 본다.
  지금 이름으로 과거를 조회하면 백테스트가 조용히 틀린다
- 다른 단지가 이미 쓰는 이름은 별칭으로 **거부**한다 (등록 시점에 사람이 판단할 문제)
- `merge()` 는 행을 지우지 않고 `canonical_id` 로 접는다 — 이력이 남아야 재현된다

**`apt_engine/repo/attributes.py`**
- `best()` — 출처 등급(공식 우선) → 최신 → 신뢰도 순
- `conflicts()` / `record_conflicts()` — 값이 다르면 덮어쓰지 않고 `source_conflict` 에.
  **등급이 같으면 자동으로 정하지 않는다**("사람이 확인하세요")
- `as_of` 조회 시 **시점 불명 값은 제외**한다 — 언제 알았는지 모르는 값을 과거
  모델에 넣으면 그게 look-ahead 다

CLI `resolve lookup/alias/merge` 추가.

### 테스트 결과
759개 통과 (신규 26개).

### 발견한 오류
1. `_fold_canonical` 이 후보 1개일 때 건너뛰어, **병합된(중복) 행 id 를 그대로
   돌려주는** 버그. 그 id 로 조회하면 반쪽짜리 가격이 나온다. 항상 접도록 수정
2. `merge()` 안에서 `add_alias` 가 자기 자신을 이름 충돌로 오인해 실패.
   이미 그 단지로 병합된 행은 충돌이 아니다 — 조회 조건에서 제외
3. `resolve "단지명"` 이 argparse 에서 죽었다(위치인자 choices 함정, redev 에 이어
   두 번째). 회귀 테스트로 고정
4. **누출 방지 테스트가 새 테이블 4개를 잡아냈다** — `complex_attribute` 등을
   시점 분류에 등록하지 않자 `test_스키마의_모든_테이블이_시점_분류에_들어_있다`
   가 실패. 설계대로 동작했다

### 데이터 부족
변함없음(사용자 PC 수집 보류 중). 새 테이블도 전부 수기 입력 대상이라
값이 없어도 구조는 완성됐다.

### 다음 단계
Phase 3~6 Feature 계층 — Entry Price(§7) · Regime(§8) · Supply Ratio(§13) ·
Jeonse Lead(§14) · Flow Stage(§15) · Transaction Quality(§16) · Catalyst Alpha(§17).
각 Feature 는 값과 함께 confidence·UNKNOWN 을 돌려주고, 데이터가 없으면
점수를 만들지 않는다.

---

## Phase 3~6 (1차) — Feature 계층 (2026-08-31) · 진행 중

지시서 §74 의 순서에서 **두 번째 칸**을 만들었다. 여기서 점수를 매기지 않는다 —
값과 신뢰도를 따로 내놓고, 어떻게 섞을지는 백테스트가 정한다.

### 구현 내용

**`features/base.py` — Feature 계약**
- §50 대로 **값과 신뢰도를 절대 합치지 않는다.** Feature 는 value·confidence·
  status·calc 네 가지를 들고 다닌다
- `Feature.missing()` — 못 구한 값은 **0 이 아니라 None**
- `usable` — 값이 있어도 신뢰도가 0.35 미만이면 랭킹에 쓰지 않는다
- `sample_confidence` / `freshness_confidence` / `combine`(기하평균).
  산술평균을 쓰면 "표본 1건 + 최신" 이 "표본 10건 + 6개월 전" 과 같아진다
- `FeatureSet.without()` — §71 Ablation 을 위해 이름으로 끌 수 있다

**`features/momentum.py` (§16·§39·§40)**
- 3/6/12개월 변화율 + 가속도 + `discovery_lag`
- **상승률을 매수 점수로 바꾸지 않는다.** "이미 많이 올랐다" 는 사실은 별도
  feature 로 분리하고, 그걸 감점으로 쓸지는 백테스트가 정한다

**`features/regime.py` (§8)** — 7국면 분류. 단지가 아니라 **지역**의 성질이라
같은 시점 모든 후보가 같은 국면을 본다. 경계값은 `THRESHOLDS` 에 모아 두고
"백테스트가 대체한다" 를 근거에 적는다

**`features/flow.py` (§15·§16)**
- Flow Stage 6단계. **어느 단계가 좋은지 정하지 않는다**(지시서가 백테스트로
  학습하라고 못 박음)
- 출력 이름이 `buy_signal` 이 아니라 `investigation_priority` 다. 이름이 곧 계약이다
- Transaction Quality — 표본충분도 × 중층이상 비중 × 가격 응집도.
  저층 한 건과 중층 여러 건은 같은 '거래 3건' 이 아니다

**`features/supply.py` (§13)**
- **Supply Ratio = 실효 입주물량 ÷ 기존 stock.** 절대물량으로 비교하면 큰 도시가
  늘 공급과다로 나온다
- 단계별 실현 가중치(계획 0.25 → 입주예정 1.0). 경쟁 4분류. Supply Cliff
- stock 을 모르면 **절대물량으로 대체하지 않고** 확인 불가

**`features/jeonse.py` (§14)**
- 전세가율은 **같은 기준월끼리만** 나눈다
- `downside_defense` 와 `capital_efficiency` 로만 쓰고 **Upside 에 더하지 않는다**
- `jeonse_lead` — 자동 매수 신호가 아니라고 근거에 명시

**마이그레이션 014 — Catalyst Ledger (§17·§18)**
- `catalyst` + `catalyst_state` + `catalyst_exposure`.
  호재의 **시점별 상태를 덮어쓰지 않고 쌓는다.** 2024년에 확정된 노선을
  2023년 백테스트가 읽으면 반칙이므로, 그 시점 행만 읽게 했다
- Catalyst Alpha 다섯 항목(경제효과·실현확률·시간적합성·노출도·선반영률)을
  **각각** 저장한다. 합쳐 두면 어느 항목이 틀렸는지 알 수 없다
- `supply_plan.announced_ym` 추가 — '언제 들어오나'(move_in_ym)와
  '언제 알았나'(announced_ym)는 다른 것이고, 백테스트에 필요한 건 후자다

### 테스트 결과
805개 통과 (신규 46개). 확인한 것:

| 테스트 | 지키는 원칙 |
|---|---|
| 못 구한 값이 0 이 아니라 None | §67 |
| 신뢰도 합성이 가장 약한 것에 끌려감 | §50 |
| 많이 오른 뒤 발견하면 discovery_lag 이 커짐 | §40 |
| 거래량 feature 이름이 investigation_priority | §15 |
| 저층만 거래되면 질 점수가 낮음 | §16 |
| stock 모르면 절대물량으로 대체 안 함 | §13 |
| 발표 전 공급은 보이지 않음 | §18 |
| 전세를 Upside 에 더하지 않음 | §14 |
| 컷오프 이후 급등이 momentum 에 안 들어옴 | §69 |
| 모든 feature 가 Ablation 그룹에 속함 | §71 |

### 발견한 오류
1. `trade.cancel_yn` 은 0/1 정수인데 regime.py 가 `IS NOT 'Y'` 로 비교했다.
   취소거래가 전부 정상으로 세어졌을 것이다
2. `supply_plan` 에 "언제 알려진 계획인가" 컬럼이 아예 없었다.
   `effective_from` 을 쓰는 코드를 썼는데 그런 컬럼이 없었다 →
   `announced_ym` 을 추가하고 컷오프 기준을 그것으로 바꿈

### 데이터 부족
변함없음. 모든 Feature 를 합성 데이터로 검증했다.

### 다음 단계
- Entry Price Engine(§7) · Catalyst Alpha(§17) 나머지 feature
- Phase 7 랭킹 (Consensus Model 9종, heuristic 가중치, TOP100→30→10)
- Phase 8 walk-forward 백테스트 하네스

---

## PHASE 7 — 랭킹 (Consensus · Kill · TOP10) · 2026-08-31

### 구현 내용

**§74 순서를 지켰다.** 점수부터 만들지 않았다. Phase 6 에서 Feature 를 먼저
만들었고, 이번 Phase 는 그 Feature 를 "아직 백테스트되지 않은 임시 가중치"로
합성하는 층이다. 그래서 모든 ranking_run 은 `weights_source='HEURISTIC'` 으로
기록되고, 리포트 머리에 `가중치 임시(heuristic)` 이 항상 찍힌다.
Phase 8 백테스트가 끝나야 `'BACKTESTED'` 로 바뀐다.

#### 새 파일

| 파일 | 역할 |
|---|---|
| `apt_engine/scoring/normalize.py` | 절대 임계값 대신 횡단면 percentile. 결측은 제외(0 아님) |
| `apt_engine/scoring/weights.py` | HEURISTIC / BACKTESTED 두 출처, 국면별 가중치 조정 |
| `apt_engine/scoring/models.py` | Consensus 9개 모델을 선언형 SPEC 으로 정의 |
| `apt_engine/scoring/consensus.py` | 9모델 합성 + 요인 기여도(가법 분해) + A vs B 비교 |
| `apt_engine/scoring/kill.py` | 위험 7종. 감점이 아니라 **제외** |
| `apt_engine/scoring/thesis.py` | 논리 1개 무너뜨리고 재계산 (Thesis Survival) |
| `apt_engine/scoring/regime_bridge.py` | 국면 정의와 가중치 표가 어긋나지 않게 강제 |
| `apt_engine/ranking/pipeline.py` | Blind Universe → 자본 게이트 → Feature → Consensus → Kill → TOP10 |
| `apt_engine/ranking/lists.py` | 절대/위험조정/비대칭 3개 리스트 + Highest Conviction |
| `apt_engine/ranking/explain.py` | WHY BUY / WHY NOT / 시장이 이미 아는 것 / A가 B보다 나은 이유 |
| `apt_engine/repo/ranking.py` | ranking_run · ranking_entry 저장, 이전 순위 조회, 탈락 목록 |
| `apt_engine/cli.py` `rank` | CLI 진입점 |

#### 설계 판단 (그리고 이유)

1. **자본 게이트를 Feature 계산보다 먼저 돌린다.**
   "3억으로 살 수 없는 20억 아파트가 1위" 는 §29 위반이다. 못 사는 물건은
   점수 계산 자체를 하지 않는다. 계산 비용도 같이 줄었다.

2. **Kill 은 TOP10 문턱에서만 돈다.**
   전 후보에 대해 7종 위험을 다 조회하면 쿼리가 폭발한다. 순위가 밀려서
   어차피 안 보일 후보의 위험은 계산할 이유가 없다.

3. **Kill 은 감점이 아니라 제외다(§65).** 대신 탈락 사유를 버리지 않고
   `dropped` 리스트에 남긴다. "왜 어제 있던 게 오늘 없나" 에 답해야 하기 때문.

4. **확인 못 한 위험을 '위험 없음' 으로 쓰지 않는다.**
   Kill Score 0.00 옆에 `(미확인 3개)` 가 같이 찍힌다. 데이터가 없어서 위험을
   못 본 것과, 봤는데 위험이 없는 것은 다른 상태다.

5. **하방 방어력을 모르면 0 이 아니라 0.5.**
   `lists.py` 의 위험조정 정렬에서 전세비율 미확인 단지를 0 으로 두면
   "모르는 것" 이 "최악" 으로 취급돼서 순위에서 부당하게 밀린다.

6. **SHAP 대신 가법 분해.** 점수가 가중합이므로 각 모델의 기여도는
   근사가 아니라 **정확히** 계산된다. 외부 의존성도 안 늘었다.

7. **정렬 키에 이름이 절대 안 들어간다.** 동점 처리까지 `complex_id` 로 한다.
   §1 blind ranking 을 정렬 단계에서도 깨지 않기 위해서다.

8. **없는 모델은 가중치를 재정규화한다.** 입력이 없는 모델을 0점으로 두면
   "데이터가 없다" 가 "나쁘다" 로 바뀐다. 대신 가능한 모델끼리 가중치 합이
   1이 되게 다시 나누고, 커버리지를 신뢰도에 곱한다.

#### 3개 리스트가 실제로 다르다

같은 점수를 세 번 보여주는 게 아니라 정렬 기준이 다르다.

- ABSOLUTE — 점수만 (최대 기대수익)
- RISK_ADJUSTED — 점수 ÷ 하방위험 (전세방어력 반영)
- ASYMMETRIC — 상방 대비 하방이 작은 순 (비대칭)

세 리스트 모두 상위 5위 안에 드는 단지만 **Highest Conviction** 이 된다.

### 테스트 결과

852개 통과 (신규 32개). 확인한 것:

| 테스트 | 지키는 원칙 |
|---|---|
| 단지명을 전부 바꿔도 점수 배열이 동일 (Placebo) | §1 |
| watchlist 에 넣어도 순위가 안 변함 | §1 |
| 결측 모델이 0점이 아니라 가중치 재정규화 | §67 |
| Kill 이 감점이 아니라 제외 | §65 |
| 탈락 단지의 사유가 보존됨 | §64 |
| 3개 리스트의 정렬이 실제로 다름 | §60 |
| 못 사는 물건이 순위에 안 들어옴 | §29 |
| 국면 정의와 가중치 표가 1:1 대응 | §11 |
| 28개 서브커맨드 전부 `--help` 가 죽지 않음 | 회귀 |
| 같은 이름의 최상위 함수가 두 번 정의되지 않음 | 회귀 |

### 발견한 오류

1. **`_resolve_complex` 가 cli.py 안에 두 번 정의돼 있었다.**
   뒤쪽 3인자 버전이 앞쪽 2인자 버전을 덮어써서
   `cash` `loan` `regulation` `relative` `catalyst` 5개 명령이 런타임에
   깨졌다. import 도 `--help` 도 통과하기 때문에 기존 테스트가 못 잡았다.
   → 이름을 분리하고, **최상위 함수 중복 정의를 AST 로 검사하는 테스트**를 추가.

2. **`--lender` 도움말에 `%` 리터럴이 들어가 argparse 가 죽었다.**
   argparse 는 help 문자열을 `%` 포매팅한다. `analyze.py` 에서 한 번 겪은
   것과 같은 버그가 재발했다. → `%%` 로 고치고,
   **28개 서브커맨드 전부에 `format_help()` 를 호출하는 테스트**를 추가.

3. **주담대가 매매가를 넘을 수 있었다.**
   LTV 규칙이 없고 소득이 높으면 DSR 만 걸려서 4억짜리 집에 4.77억 대출이
   나왔고 실투자금이 음수가 됐다. → `담보가액(LTV 100%)` 상한을 추가하되
   **이미 알려진 정책 상한을 좁히기만** 하게 했다. 규칙이 하나도 없으면
   답은 여전히 "확인 불가" 다 (없는 규제를 만들어내지 않는다).

4. **`--holding-cost 150` 이 150억으로 파싱됐다.**
   `parse_price` 는 1000 미만을 억으로 본다. 보유비용·월세·기타비용은
   만원 단위가 자연스럽다. → `units.from_manwon` 으로 교체.
   (이 버그로 Peak Equity 가 749억이 되고 IRR 이 전부 확인 불가였다)

5. **`build_static.py` 가 `shutil.rmtree(docs/)` 를 하고 있었다.**
   §77 이 요구하는 `docs/*.md` 를 통째로 지운다. 실제로 한 번 지웠고
   `git checkout -- docs` 로 복구했다. → 생성물만 지우도록 바꾸고,
   소스에 `rmtree(DOCS_DIR)` 가 다시 나타나면 실패하는 테스트를 추가.

### 데이터 부족

여전히 `trade` · `jeonse_contract` · `complex` 이 0행이다 (data.go.kr 이
이 컨테이너에서 차단). 이번 Phase 의 전 구간을 12개 단지 합성 시장으로
검증했다. 합성 데이터는 DB 에 남기지 않는다.

그래서 **지금 나오는 점수는 순위의 근거가 아니라 배관 검증 결과다.**
실제 수집이 돌기 전까지 TOP10 을 투자 판단에 쓰면 안 된다.
리포트가 이것을 매번 명시한다.

`models.SPEC` 이 참조하는 `redev_mispricing` · `relative_gap` 두 feature 는
아직 생산되지 않는다. 해당 모델은 항상 None 을 내고 가중치에서 빠진다
(0점 처리가 아니다).

### 다음 단계

- Phase 8 walk-forward 백테스트 하네스 (§55~§57)
  - as-of 스냅샷 · 정답(1Y/3Y/5Y) 계산 · KPI (Winner Recall@K, Regret,
    Ex-post Capital Rank, MDD, Recovery Time, Discovery Lag)
  - 누수를 일부러 심고 잡히는지 확인하는 테스트
  - 끝나면 `weights_source` 를 `'BACKTESTED'` 로 전환
- §27 현금 버킷 · §30 Capital Frontier · §31 대안매수 테스트
- §52 예상 순위 구간 (bootstrap) · §53 몬테카를로
- §71 Ablation 러너 · §72 Train/Validation/Out-of-time 분할

---

## PHASE 8 — Walk-forward 백테스트 하네스 · 2026-08-31

### 구현 내용

§74 의 세 번째·네 번째 칸을 채웠다.

    데이터 → Feature → **Historical Backtest → Feature usefulness** → Weight → Ranking

이제 `weights_source` 가 `HEURISTIC` 을 벗어날 수 있는 경로가 존재한다.
다만 실거래가 0건이라 그 경로는 합성 시장에서만 돌았다.

| 파일 | 역할 |
|---|---|
| `db/migrations/015_backtest.sql` | 실행·창·선택·**정답지**·KPI·유용성·가중치 7개 테이블 |
| `backtest/windows.py` | 창 생성 · 시간분할(§72) · embargo · **검정력 리포트** |
| `backtest/outcome.py` | 정답 계산 — 수익률·MDD·회복·상승시작·Winner 4상태 |
| `backtest/kpi.py` | KPI 14종(§57) + 순수 파이썬 Spearman |
| `backtest/usefulness.py` | Feature 유용성 → 가중치(§74) · Ablation(§71) |
| `backtest/leakage.py` | 누출 감사 3겹 + 미래삭제 비교 |
| `backtest/synthetic.py` | 합성 시장 3종 (MOMENTUM / MEAN_REVERT / NONE) |
| `backtest/runner.py` | 전체 구동 |
| `invest/buckets.py` | 현금 버킷 9종(§27) + Capital Frontier 증감(§30) |
| `cli.py` `backtest` | `plan` / `run` / `weights` |

#### 설계 판단 (그리고 이유)

1. **정답지를 구조로 격리했다.**
   `backtest_outcome` 등 4개 테이블을 `ANSWER_KEY_TABLES` 에 넣어서,
   컷오프 guard 안에서는 **어떤 조건을 붙여도** 조회가 거부되게 했다.
   백테스트에서 가장 위험한 사고는 Feature 코드가 미래 수익률을 한 번 읽는
   것인데, 그 사고는 "성적이 아주 좋게 나온다" 는 형태로 나타나서 눈에
   안 띈다.

2. **누출이 발견되면 COMPLETE 가 될 수 없게 스키마로 막았다.**
   `CHECK (status != 'COMPLETE' OR (leakage_checked=1 AND leakage_found=0))`.
   성적을 먼저 보고 누출을 찾으면 사람은 좋았던 실행을 살리고 싶어진다.
   코드가 아니라 제약으로 막아야 하는 종류의 규칙이다.

3. **누출 검사가 작동하는지를 테스트한다.**
   컷오프 이후 가격을 일부러 3배로 부풀리고(`plant_leak`), 그걸 읽는 코드를
   검사가 잡아내는지 확인한다. 이 테스트가 없으면 "누출 없음" 은 아무 뜻도
   없는 문장이다.

4. **정답은 고른 것이 아니라 후보 전체에 대해 계산한다.**
   고른 것만 채점하면 Regret 도 Missed Winner 도 나오지 않는다.
   놓친 것을 봐야 놓친 걸 안다.

5. **값을 못 내면 사유를 스키마가 요구한다.**
   `CHECK (forward_return IS NOT NULL OR unknown_reason IS NOT NULL)`.
   결측을 0 으로 채우면 "그 단지는 평범했다" 는 사실을 만들어낸 것이 된다.

6. **대출 규칙이 없는 시점을 위한 게이트 모드를 명시적으로 뒀다.**
   과거 시점에는 그때의 LTV·DSR 규칙이 DB 에 없어서 실투자금을 낼 수 없다.
   `PRICE_ONLY` 는 **대출이 나온다고 가정하지 않고** 전액 현금 매수만
   가능하다고 본다. 실제보다 후보가 좁아지는 대신 없는 규제를 지어내지
   않는다. 이 모드로 돈 결과에는 그 사실이 항상 붙어 다닌다.

### 테스트 결과

898개 통과 (신규 46개). 확인한 것:

| 테스트 | 지키는 원칙 |
|---|---|
| 컷오프 안에서 정답지를 조건 붙여도 못 읽는다 | §55 |
| 미래를 심으면 누출 검사가 잡는다 | §69 |
| 누출이 있으면 실행이 무효가 된다 | §55 |
| 검사를 통과하지 않으면 COMPLETE 가 될 수 없다 (스키마) | §55 |
| 겹친 창을 독립 관측으로 세지 않는다 | §57 |
| 정답이 아직 없는 창은 버리지 않고 사유를 남긴다 | §67 |
| 확인 불가를 0 으로 세지 않는다 | §67 |
| 낙폭이 시작가가 아니라 고점 대비다 | §36 |
| 회복 못 함(False)과 모름(None)을 구분한다 | §67 |
| 표본 없이 KPI 값을 만들지 않는다 (스키마) | §57 |
| 부호가 뒤집히면 평균내지 않고 0 으로 둔다 | §74 |
| **신호가 없는 시장에서는 아무것도 배우지 않는다** | §74 |
| **시장이 바뀌면 학습 결과도 바뀐다** | §8·§74 |
| 합성 가중치를 실전 랭킹이 읽지 않는다 | §0 |
| 합성 데이터를 실제 DB 에 못 쓴다 | §0 |

마지막 두 개가 이 Phase 의 핵심 검증이다. 추세 시장에서는 momentum 이,
가치 시장에서는 value 가 가중치를 받고, 신호가 없는 시장에서는 아무것도
학습하지 않는다. **같은 코드가 시장에 따라 다른 답을 낸다** — 항상 같은 답이
나오면 데이터를 읽는 게 아니라 미리 정해 둔 답을 되풀이하는 것이다.

### 발견한 오류

1. **`DATED_TABLES` 의 컷오프 컬럼 4개가 실제 스키마에 없었다.**

   | 테이블 | 등록돼 있던 이름 | 실제 컬럼 |
   |---|---|---|
   | `jeonse_contract` | `deal_ymd` | `contract_ymd` |
   | `field_note` | `observed_at` | `noted_on` |
   | `future_catalyst` | `as_of` | (없음 → `calculated_at`) |
   | `ratio_norm` | `as_of_ym` | `to_ym` |

   guard 는 SQL 문자열에 그 컬럼 이름이 들어 있는지로 검사한다. 이름이 틀려
   있으면 **올바르게 컷오프한 쿼리가 반칙으로 거부된다.** 전세 실거래를
   백테스트에 넣는 순간 터졌을 버그다.
   → 고치고, 컷오프 컬럼이 실제로 그 테이블에 있는지 검사하는 테스트를 추가.

2. **합성 시장의 "신호 없음" 모드가 사실은 신호가 있었다.** 두 번 틀렸다.
   * 처음엔 매달 독립인 잡음을 고정 수준에 더했다 → 그 자체가 평균회귀 신호
   * 고친 뒤에도 **덧셈** 잡음을 곱셈 추세에 얹었다 → 같은 금액이 올라도
     싼 단지의 **수익률**이 자동으로 높아져서 value 모델이 IC +0.13 을 받았다

   → 전 과정을 곱셈(로그)으로 바꿨다. 귀무가설 시장이 진짜로 귀무가설이 아니면
   그 시장으로 한 검증은 전부 무의미하다.

3. **임계값 하나로는 잡음을 못 막는다.**
   "IC ≥ 0.05 이고 VALIDATION 에서 부호가 같으면 유용" 이라는 기준으로는,
   **신호를 0 으로 넣은 시장에서도 feature 가 통과했다.** 원인은 두 가지였다.
   * 겹친 창을 독립 관측으로 셌다 (3개월 간격 × 2년 보유 = 8배 부풀림)
   * 부호 일치는 창이 몇 개 안 되면 절반의 확률로 우연히 맞는다

   → 유효표본(겹치지 않는 창 수)을 도입하고, 그 위에
   **t = |IC| / (창별 IC 표준편차 ÷ √유효표본) ≥ 2.0** 을 요구하게 했다.
   이걸 넣은 뒤 귀무가설 시장의 오탐이 사라졌다(t=0.7 → NEUTRAL).

### 데이터 부족 — 이번에 새로 알게 된 제약

수집 기간이 백테스트의 가능/불가능을 그대로 결정한다.

| 보유기간 | 검증 구간에 필요한 햇수 | 필요한 전체 데이터(60/20/20) |
|---:|---:|---:|
| 2년 | 6년 | 30년 |
| 5년 | 15년 | 75년 |
| 10년 | 30년 | 150년 |

국토부 실거래 공개는 2006년부터라 최대 20년이다. 따라서:

* **2년 랭킹** — 학습 비율을 줄이면(`--train-frac 0.45 --val-frac 0.35`)
  검증 가능
* **5년·10년 랭킹** — 데이터가 아무리 많아도 **정식 검증이 불가능**하다.
  TRAIN IC 까지만 보고 가중치는 heuristic 을 유지한다.

이건 구현의 한계가 아니라 데이터 길이의 한계다. 감추지 않고 `backtest plan`
이 돌리기 전에 알려주고, 리포트에도 표시한다.

`docs_dev/03-종인님-할일-정리.md` 의 수집 명령을 `--months 60` → `--months 240`
으로 바꿨다. 60개월이면 2년 보유조차 검증할 수 없다.

### 다음 단계

- §30 Capital Frontier / Pareto 곡선 · §31 Alternative Purchase Test
- §52 Expected Rank Range (bootstrap) · §53 Monte Carlo · §51 Ranking Persistence
- §61 Rotation Engine · §62 TOP10 전체 컬럼 · §64 순위변경 설명
- §71 Ablation 러너를 파이프라인에 연결 (지금은 비교 함수만 있다)
- `redev_mispricing` · `relative_gap` feature — `models.SPEC` 이 참조하는데
  아직 생산되지 않아 해당 모델이 항상 None 이다
- §77 나머지 문서 8종

---

## PHASE 9 — DELTA UPGRADE (신규 지시서 §1~§49) · 2026-08-31

### §48 보고서

#### A. 기존에 이미 구현되어 있던 것

새 지시서 46개 항목 중 3개는 손댈 필요가 없었다.

| § | 내용 | 어디에 |
|---:|---|---|
| 32 | 사용자 관심 편향 금지 | `blind/universe.py` 가 `watchlist` 를 import 조차 안 함 + Placebo 테스트 |
| 33 | 지역명 자체에 Alpha 금지 | 정렬 키가 `(-score, complex_id)` — 이름이 한 번도 안 들어감 |
| 41 | 연구 후보를 Regression 전용으로 | 기존 §73 규칙이 이미 고정 |

그리고 부분적으로 이미 있던 것들:
Capital Gate 순서(§2), MNTP 90일 정규화(§35), Score/Confidence 분리(§36),
매수가 구간 strong/fair/wait(§39), walk-forward + Catalyst Vintage(§30),
`feature_usefulness` 생존 기록(§44).

#### B. 이번에 새로 구현한 것

| 파일 | § | 무엇 |
|---|---|---|
| `db/migrations/016_delta_upgrade.sql` | 1·4·11·12·27·38·43·44 | 7개 테이블 + 랭킹 확장 |
| `features/registry.py` | 4·44·45 | Feature 등록부 — State·role·tier |
| `features/bands.py` | 7·8·9 | P25/중앙값/P75 이동 · Latent/Visible · 기울기 지속 |
| `features/stretch.py` | 5·6 | 장기정상가 대비 이탈 · 역U 가속도 · 상승폭 |
| `features/stage.py` | 17·22·23·38 | 8단계 Stage · 4분면 · Quiet Compounder |
| `features/leader.py` | 10~16 | Leader 5종 · 전달실패 · 회복가능할인 · Next Node |
| `invest/cash_candidate.py` | 2·3·24·46 | CASH 후보 · 남는현금 수익 · 총자본수익률 |
| `invest/buckets.py` | 27·30 | 현금 버킷 9종 (Phase 8 에서 만듦) |
| `ranking/executable.py` | 37·39·40·43 | 실행/Watch 분리 · Competitive Buy Price · 시장온도 · Coverage |
| `repo/control.py` + `rules/*.csv` | 27·28·41 | Control Pair · 연구셋 |
| `backtest/kpi.py` 확장 | 25·26 | KPI 14 → 19종 · 성공 3단계 |

Feature 등록부 현황: 47개
(CHEAPNESS 4 · MOVEMENT 15 · SUSTAINABILITY 7 · STRETCH 12 · GATE 2 · CONTEXT 7)
역할별: ALPHA 18 · RISK 12 · GATE 2 · CONFIDENCE 1 · CONTEXT 14
**CORE 0개** — 백테스트 전이라 비어 있는 것이 정상이다(§44).

#### C. 기존 로직을 수정한 것

| 수정 전 | 수정 후 | 이유 |
|---|---|---|
| `models.SPEC` 에 `("price_acceleration", True)` — 가속도가 높을수록 가점 | `acceleration_zone` 역U. Emerging 최고, Extreme 은 감점 | §6. 선형 가산은 상투를 잡는다. §49-5 와도 어긋났다 |
| 저평가 판단에 자기 과거 평균 사용 | 장기 **추세선**의 현재 위치 | §5. 우상향한 단지는 과거 평균 대비 항상 '고평가' 로 나온다 |
| `entry_position`·`discovery_lag`·`supply_ratio_2y`·`downside_defense`·`transaction_quality` 가 ALPHA 모델과 `kill.RULES` 양쪽에 | Feature 하나가 role 하나만 | §45. Kill 이 배제라 산술적 이중가산은 아니었지만 같은 신호가 순위와 생존을 두 번 움직였다 |
| 전세 승계 + 주담대를 각각의 최대치로 **더함** | 조합 자체를 엔진이 거부 | 5억짜리에 주담대 3억 + 보증금 3억이 잡혀 실투자금이 **음수**로 나왔다. UI 에서만 막으면 `rank`·`backtest` 로 샌다 |
| CASH 가 boolean 플래그 | 순위표의 **행** | §3. 행이 아니면 "3위보다 낫고 2위보다 못하다" 를 말할 수 없고 `cash_accuracy` 를 채점할 수 없다 |
| 연구후보 단지명이 `repo/control.py` 모듈 상수 | `rules/*.csv` | §73. 이름이 코드에 박히면 언젠가 그 이름을 참조하는 분기가 생긴다 |

#### D. 삭제/비활성화한 Feature

**없다.** 기존 19개와 7그룹을 하나도 지우지 않았다.
§4 가 요구한 4 State 는 그 위에 얹었고, 기존 7그룹은 §44 의 `DIAGNOSTIC` 으로
내렸다. 삭제 대신 강등이다 — `models.SPEC`·`group_of`·`kill.RULES` 가 전부
7그룹을 참조 중이라 지우면 랭킹이 통째로 멈춘다.

#### E. 데이터 부족으로 아직 구현하지 못한 것

| § | 무엇 | 필요한 것 |
|---:|---|---|
| 12·13 | 전달 실패 **실측** | 계산기는 있음. Leader 12개월 시계열 필요 |
| 16 | Next Node **실행** | 생활권 사다리 + 각 칸의 상승률 |
| 19 | Path-Dependent Valuation | 도달 경로 전체 |
| 27 | Control Pair 채점 | 2019 시점 실거래 |
| 28 | 2021 CASH Reverse Sanity | 2021 실거래 + **그 시점 LTV·DSR** |
| 29 | 2017/2019 Opportunity | 〃 |
| 31 | 2023 Recovery | 〃 |

수집은 종인님 PC 에서 진행 중이다(매매 585,294건 · 27/240개월).
**과거 정책 행이 없으면 실거래가 다 와도 §28·§29·§31 은 못 돈다** —
그 시점 실투자금이 "확인 불가" 가 되어 Capital Gate 를 아무도 통과하지 못한다.

#### F. 백테스트가 필요한 가설

이번에 넣은 숫자 중 **관측이 아니라 판정 기준**인 것들. 전부 코드에
`THRESHOLD_NOTE` 로 표시했고, §21 이 요구한 대로 학습이 대체한다.

- 가속 구간별 남은 알파 (Dormant 0.35 / Emerging 1.00 / Confirmation 0.70 / Overheated 0.10)
- 밴드 상승 판정선 1% · Spike 판정 3M 10% vs 장기 2%
- Stage 경계 (Cheap ≤ −3% · Expensive ≥ +10% · Moving ≥ 0.5)
- Latent HIGH 0.6 · Visible EARLY 0.45 / CLEAR 0.70
- 전달 실패 (Leader +8% · Follower +2% · 12개월)
- Persistent Cheapness 시작점 24개월
- 시장온도 경계 (20% / 8% / 2%)

그리고 §7 이 준 핵심 가설: **LatentMovement HIGH + VisibleMovement EARLY 가
가장 좋다** — 이건 결론이 아니라 검증 대상이다.

#### G. Regression Test 결과 (2017 / 2019 / 2021 / 2023)

**아직 낼 수 없다.** 실거래 수집이 27/240개월(11%)이고, 받은 구간이
200609부터라 2017 이후가 비어 있다. 없는 데이터로 회귀 결과를 만들지 않는다.

대신 **하네스와 판정 로직은 완성했고 합성 시장으로 검증했다.**
`repo/control.py` 의 `discriminates()` 가 "점수가 없으면 실패가 아니라 모름"
으로 구분하는 것까지 테스트로 고정했다.

#### H. Universe Coverage

**측정할 모수가 아직 확정되지 않았다.** `ranking/executable.py` 의
`measure()` 가 단지수·세대수·시군구 커버리지를 계산하고, 80% 미만이면 화면
제목이 자동으로 `PARTIAL VERIFIED UNIVERSE` 로 바뀐다(§43·§49-13).
모수를 못 구하면 1.0 으로 가정하지 않고 PARTIAL 로 떨어진다.

#### I·J. 투자금별 Ranking · TOP 후보 상세

**낼 수 없다.** G 와 같은 이유다. 코드 경로는 전부 연결돼 있어서
수집이 끝나면 명령 한 줄로 나온다.

### 테스트 결과

998개 통과 (신규 34개). 이 중 절반이 **금지 규칙**이다.

| 테스트 | 지키는 금지 |
|---|---|
| 싸고 안 움직이면 PRE_BREAKOUT 이 아니다 | §49-8 |
| 전고점 대비가 아니라 추세 대비다 | §49-6 |
| 가속도가 선형이 아니다 | §49-5 |
| 한 Feature 가 ALPHA·RISK 양쪽에 없다 | §45 |
| 연구후보 이름이 결정 경로 코드에 없다 | §49-2 |
| 전세승계+주담대 조합을 거부한다 | §2 |
| 현금수익률을 모르면 0 으로 가정하지 않는다 | §3 |
| 겹침을 모르면 Leader 로 인정하지 않는다 | §11 |
| 전달실패를 모르면 할인을 분해하지 않는다 | §12 |
| 다섯 구성요소 없이 Next Node 점수를 만들지 않는다 | §16 |
| 다 못 봤으면 '전체' 라고 쓰지 않는다 | §49-13 |
| 근거 없이 CORE 로 못 올린다 (스키마) | §44 |

### 발견한 오류

1. **`repo/control.py` 에 연구후보 단지명을 모듈 상수로 박았다.**
   기존 `test_엔진_코드에_특정_단지명이_하드코딩돼_있지_않다` 가 즉시 잡았다.
   §73 이 금지한 바로 그 형태였다. → CSV 로 옮겼다.
   **테스트가 깨진 채로 한 번 푸시했다.** 커밋 전 전체 스위트를 돌리지 않고
   해당 파일만 돌린 탓이다.

2. **`price_acceleration` 이 선형 가산이었다.**
   `models.SPEC` 에 `(key, True)` 라 "많이 오를수록 좋다" 였고, 이는 §49-5
   (거래량·상승 증가만으로 가산점 금지)와도 어긋났다. 급등 후 매수를 가산하는
   방향이라 상투를 잡는 구조였다.

3. **실투자금이 음수가 될 수 있었다.** (수집 세션 메모에서 넘어옴)
   전세 승계와 주담대의 각각의 최대치를 더하고 있었다. 현실에 없는 조합이
   가장 매력적인 후보로 올라온다.

### 2차 (같은 날 이어서) — §18~§21·§34·§35·§44 완료

| § | 무엇 | 파일 |
|---:|---|---|
| 18 | Excess Reset Completion (7단계 순서) | `features/cycle.py` |
| 19 | Path-Dependent Valuation | 〃 |
| 20 | Proof–Price Tradeoff (RemainingAlpha) | `scoring/early_alpha.py` |
| 21 | EarlyAlpha 핵심식 | 〃 |
| 34 | NakedApartmentValue · 프리미엄 효율 · 전달경로 | `redev/naked.py` |
| 35 | 30일 방향성 · Type 정규화 · 급매 흡수 | `price/normalize.py` |
| 28·29·31 | 시점별 Sanity Test | `backtest/sanity.py` |
| 37·46 | 파이프라인 조립 + `today` 명령 | `ranking/delta_pipeline.py` |
| 44 | CORE 승격을 백테스트에 연결 | `backtest/usefulness.py` |

**추가로 발견한 오류 3건**

4. **`executable.split()` 이 기대수익 미상인 후보를 통과시켰다.**
   `if better is None and returns:` 라 **아무 후보도 점수가 없으면 `returns` 가
   비고, 그러면 검사 자체를 건너뛰어 전부 통과**했다. 합성 시장에서 Alpha 를
   하나도 못 낸 채 5개가 EXECUTABLE 에 올라왔고 동시에 CASH 가 1위로 표시되는
   모순이 났다. §46 은 "모르면 YES 가 아니다" 이므로 제외로 고쳤다.

5. **Feature usefulness 의 IC 를 원시값으로 계산했다.**
   `price_stretch` 처럼 낮을수록 좋은 Feature 는 원시 IC 가 음수여야 정상인데,
   방향 보정 없이 판정해서 **낮을수록 좋은 Feature 12개가 통째로 HARMFUL** 로
   나왔다. 그대로 두면 §44 CORE 승격이 정확히 반대로 돈다.
   보정 후 가치 시장에서 cheapness 계열이 USEFUL, 모멘텀 계열이 HARMFUL 로
   — 그 시장에서 나와야 할 정확히 그 결과가 나온다.

6. **CORE 승격이 모델 이름을 보고 있었다.** 가중치는 모델(9종) 단위로 학습하고
   CORE 티어는 Feature 단위인데, 등록부 키가 feature key 라 하나도 안 맞았다.
   두 수준을 각각 재도록 분리했다.

### 다음 단계

- §61 Rotation Engine · §62 TOP10 전체 컬럼 · §64 순위변경 설명
- `buyer_pool` · `effective_supply_risk` · `replacement_availability` —
  등록부에는 있는데 아직 생산되지 않는다 (EarlyAlpha 의 곱셈 항 하나가 빔)
- Leader 망 자동 생성 (`leader_link` 를 채우는 수집·계산 경로)
- 수집 완료 후: `backtest sanity` · `backtest run` · `today --weights backtested`
