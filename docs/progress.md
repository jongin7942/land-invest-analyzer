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
