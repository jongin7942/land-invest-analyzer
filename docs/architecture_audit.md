# Architecture Audit — 현황 분석

작성: 2026-08-31 · 엔진 v0.10.0 · 브랜치 `claude/new-program-development-f7gi4z`
· 테스트 704개 통과 · 지시서 §0·§81 산출물

---

## 0. 한 줄 요약

**계산 엔진은 상당 부분 이미 있다. 없는 것은 "데이터"와 "학습 루프"다.**

- 코드 14,292줄(apt_engine) + 테스트 5,976줄, 42개 테이블, 마이그레이션 011까지
- 세금·대출·실투자금·현금흐름·IRR·재건축 사업성은 **동작한다**
- 반면 **실거래 데이터가 0건**이라 백테스트·가중치 학습·가격전이 네트워크는
  코드 이전에 **입력 자체가 없다** (§B 참고)

---

## 1. 디렉터리 구조

```
land-invest-analyzer/
├── main.py analyze.py pipeline.py app.py build_static.py publish.py   ← 토지(기존)
├── collectors/ analysis/ db/ notify/                                   ← 토지(기존)
├── docs/            GitHub Pages 산출물 + 이 문서들
├── docs_dev/        설계·진행 문서
├── rules/           수기 규칙 CSV (tax/cost/loan/permit)
├── templates/       Flask 템플릿(토지)
├── tests/           17 파일 · 704 테스트
└── apt_engine/      아파트 엔진 83 파일 · 14,292줄
```

`apt_engine/` 은 토지 파이프라인과 **네임스페이스·DB 파일이 모두 분리**돼 있다.
`tests/test_land_regression.py` 가 AST 로 "apt_engine 이 토지 모듈을 import 하지
않는다"를 강제한다.

### apt_engine 하위

| 패키지 | 상태 | 내용 |
|---|---|---|
| `units` `trace` `rules` `regions` `area` `geo` | ✅ | 단위·계산추적(Calc)·규칙조회·시군구·면적밴드·거리 |
| `db/` | ✅ | 커넥션(WAL)·마이그레이션 011 |
| `collectors/` | ✅ 코드 / ❌ 데이터 | molit·apt_trade·apt_rent·kapt·geocode·matcher |
| `price/` | ✅ | outlier·representative·snapshot (대표가격) |
| `listing/` | ✅ | provider·distribution·dedupe·gap·change·pressure·special |
| `regulation/` | ✅ | zone(규제·토허)·loan(구)·mortgage(신) |
| `tax/` | ✅ | rules·acquisition·holding·capital_gains |
| `cash/` | ✅ | costs·equity(구)·self_capital(신) |
| `cashflow/` | ✅ | schedule·timeline·irr·scenario |
| `relative/` | ✅ | ladder·benchmark·ratio |
| `catalyst/` | ✅ | transit·supply·analogue·assemble |
| `redev/` | ✅ | screening·far·stage·feasibility·scenario·conversion |
| `invest/` | 🟡 | budget·roe·ranking (지표는 있고 랭킹 학습이 없음) |
| `repo/` | ✅ | apt·listing·rules·relative·catalyst·redev·cashflow |
| `validation/` | ✅ | 검증규칙 26개 |
| `scoring/` `sensitivity/` `reverse/` `narrative/` | ⬜ | **빈 스텁** (docstring만) |

---

## 2. 데이터 수집 모듈

| 모듈 | 출처 | 상태 |
|---|---|---|
| `collectors/kapt.py` | K-apt 단지 목록·기본정보 | 코드 검증 완료(사용자 PC에서 라이브 확인) |
| `collectors/apt_trade.py` | 국토부 아파트 매매 실거래 | 〃 |
| `collectors/apt_rent.py` | 국토부 아파트 전월세 | 〃 |
| `collectors/geocode.py` | V-World 지오코딩 | 기존 키 재사용 |
| `collectors/matcher.py` | 실거래 ↔ 단지 매칭 | 미매칭 26% (개선 필요) |
| `listing/provider.py` | 호가 (수기 CSV/JSON) | 크롤링 없음 — 약관 준수 |

**data.go.kr 이 이 작업 환경에서 차단돼 있다**(egress proxy). 수집은 사용자 PC 에서만
가능하고, 현재 리포지토리의 `apt_invest.db` 는 **비어 있다**.

---

## 3. DB Schema (42 테이블)

| 계층 | 테이블 |
|---|---|
| 메타 | `data_source` `collection_log` `engine_version` `_migration` |
| 단지 | `region` `complex` `complex_block` `complex_group` `complex_group_member` `unit_type` |
| 실거래 | `trade` `jeonse_contract` |
| 대표가격 | `price_snapshot` `jeonse_snapshot` |
| 호가 | `listing` `listing_snapshot` `market_pressure` `field_note` |
| 규칙 | `regulation_zone` `land_permit_zone` `tax_rule` `loan_rule` `cost_rule` |
| 상대가치 | `ladder_axis` `ladder_node` `benchmark_relation` `price_ratio_history` `ratio_norm` |
| 촉매 | `transit_project` `transit_station` `station_distance` `supply_plan` `transit_analogue` `future_catalyst` |
| 재건축 | `far_standard` `redevelopment_project` `redevelopment_scenario` `redev_candidate` `stage_duration_ref` `construction_cost_ref` |
| 금융 | `user_profile` `cashflow_snapshot` |

**스키마가 구조적으로 금지하는 것들**(이미 구현됨, 지시서 요구와 일치):

- 세대수 **합계 컬럼이 없다** → 아파트+오피스텔 혼합 불가
- `complex_group.merge_reason` NOT NULL → 근거 없는 단지 병합 불가
- `land_permit_zone.effective_to` NOT NULL → 만료된 토허를 현재로 표시 불가
- `transit_station` `status='개통'` → `opened_ym` NOT NULL
- `future_catalyst.evidence_json` NOT NULL → 근거 없는 호재 저장 불가
- `redevelopment_scenario.data_grade = 'SCENARIO'` 고정 → 분담금 확정 저장 불가
- `cashflow_snapshot.data_grade = 'SCENARIO'` 고정 → 수익률 확정 저장 불가
- `redevelopment_project` CHECK → 구역지정 전 정비계획 용적률 저장 불가

---

## 4~8. 이미 구현된 계산 기능

| 지시서 항목 | 현재 구현 | 위치 |
|---|---|---|
| §4 가격 분리 | 매매/전세 스냅샷 분리, 신규/갱신 전세 구분, 동일 면적밴드 비교 | `price/snapshot.py` `area.py` |
| §5 MNTP | **거의 그대로 있다** — 취소·직거래 hard 제외, 특수층·갱신권 soft 제외, MAD z-score 이상치, 중앙값, 표본수→confidence | `price/outlier.py` `price/representative.py` |
| §6 Actual Buyable | 최저호가/정상최저호가/중앙호가 분리 + 실거래 괴리 | `listing/distribution.py` `listing/gap.py` |
| §13 공급(일부) | 반경 1/3/5km, 단계별 weight, 기준 명시 | `catalyst/supply.py` |
| §14 전세(일부) | 신규/갱신 분리, 전세가율(기준월 불일치 시 거부) | `price/snapshot.py` |
| §17 Catalyst(일부) | 단계 7종, 사실/추정 컬럼 분리, 투자기간 연결, 개통 선행사례 | `catalyst/transit.py` `catalyst/analogue.py` |
| §19 재건축 | 비례율·권리가액·추가분담금, Bear/Base/Bull, 민감도, 신축전환원가 | `redev/*` |
| §24 대출 | LTV·DSR·스트레스·절대한도 4중, `as_of` 기준 versioning | `regulation/mortgage.py` |
| §25 세금 | 취득세(점증세율·중과)·지방교육세·농특세·인지세·법무비·보유세·양도세, 전부 `as_of` versioning | `tax/*` `cash/costs.py` |
| §26 Capital Feasibility | `SELF_CAPITAL_REQUIRED` 계산 + 부족 시 후보 제외 | `cash/self_capital.py` `invest/budget.py` |
| §29 Return on Deployable Cash | `EXPECTED_ROE = 순이익 ÷ 실투자금` | `invest/roe.py` |
| §47 투자기간별 | `--holding` 인자로 2/5/10년 계산 가능 | `cashflow/timeline.py` |
| §50 Confidence | 표본수 기반 HIGH/MEDIUM/LOW + `verification` 4단계 | `price/representative.py` `rules.py` |
| §53 Monte Carlo(일부) | Bear/Base/Bull + Stress Test 4종 | `cashflow/scenario.py` |
| §67 데이터 없을 때 | `UNKNOWN`/`NEEDS_VERIFICATION`/확인 불가 전면 적용 | 전 계층 |
| §74 Calc 추적 | 모든 계산이 `Calc`(입력→식→중간값→근거→등급) 반환 | `trace.py` |

---

## 9. 미완성 / 빈 스텁

| 패키지 | 지시서 대응 |
|---|---|
| `scoring/` | §49 Consensus Model, §8 Regime, §45 Kill Score |
| `sensitivity/` | §53 Monte Carlo 확장 |
| `reverse/` | §7 Entry Price Engine (역산 매수가) |
| `narrative/` | §63 단지 상세화면, §75 Explainability |

---

## 10. 중복 구현 가능성

| 중복 | 판단 |
|---|---|
| `cash/equity.py` ↔ `cash/self_capital.py` | equity 는 구버전. self_capital 이 상위호환. **equity 는 기존 호출부 호환용으로 남김**, 신규 코드는 self_capital 사용 |
| `regulation/loan.py` ↔ `regulation/mortgage.py` | loan 은 구버전(단일 행 규칙). mortgage 가 rule_type 분리형. 동일 정책 |
| `invest/roe.py` ↔ `cashflow/timeline.py` | roe 는 단순 총수익률, timeline 은 연도별 IRR. **역할이 다르나 매도비용 계산이 중복** → 정리 대상 |
| `relative/ratio.py` ↔ 지시서 §10 Leader-Follower Spread | ratio 는 사람이 정한 사다리 기반. 지시서는 **데이터로 학습한 네트워크**를 요구 → 대체 아닌 확장 |
| `catalyst/supply.py` ↔ 지시서 §13 Supply Ratio | 현재는 절대물량. **stock 대비 비율**로 확장 필요 |

---

## 11. 토지 프로그램(기존)

`main.py` `analyze.py` `pipeline.py` `app.py` `build_static.py` + `collectors/`
`analysis/` `db/` `notify/` — **건드리지 않는다.** 회귀 테스트 23개가 지키고 있다.

이번 감사에서 발견해 고친 것: `build_static.py` 가 `docs/` 를 통째로 `rmtree` 해서
**지시서 §77 이 요구한 `docs/*.md` 가 빌드 한 번에 사라지는** 구조였다.
생성물만 지우도록 고치고 회귀 테스트를 넣었다.

---

## 12. 사용 가능한 라이브러리

`Flask` `requests` `python-dotenv` `pytest` — **numpy/pandas/scikit-learn/shap 없음.**

영향:
- §76 SHAP → 사용 불가. 대신 **가법 모델의 정확한 factor contribution 분해**로
  대체한다(가법 모델에서는 SHAP 과 수학적으로 동일하고 근사 오차가 없다).
- §53 Monte Carlo → 순수 파이썬 `random` 으로 구현 (표본 1만개 수준이면 충분히 빠름)
- 회귀·상관 계산 → 직접 구현 (피어슨/스피어만/lead-lag 는 수십 줄)

의존성을 늘리지 않는 편이 낫다고 판단했다. 이 프로젝트는 지금까지 표준 라이브러리로만
돌아왔고, 사용자 PC 환경 재현성이 중요하다.
