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
