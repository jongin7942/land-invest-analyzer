# 완료보고 — ALL_BUYERS 토허 입력 및 Hard Gate

작업지시서 §9 형식.

## 1. 수정한 파일

**신규**
- `rules/permit_all_buyers.csv` — 40행 (서울 25 · 경기 15)
- `tests/test_permit_all_buyers.py` — 필수 테스트 14종 포함 27개

**수정**
- `apt_engine/regulation/gate.py` — `matches_property` · `evaluate_candidate` ·
  두 겹 커버리지 · 토지지분 · 실거주계획 · 만료 재확인
- `rules/permit_coverage.csv` — BROAD_APARTMENT / PARCEL_SPECIFIC 로 분리
- `rules/permit_foreign.csv` — 행정구역 개편 주의사항 추가

## 2~3. 입력 건수

| buyer_scope | 건수 |
|---|---|
| **ALL_BUYERS** | **40** (서울 25 · 경기 2025 12 · 경기 2026 3) |
| **FOREIGN_ONLY** | **7** (인천) |

## 4. buyer_scope별 검증

- ALL_BUYERS 에 인천 코드 **0건** ✅
- 인천 규칙은 전부 FOREIGN_ONLY ✅
- ALL_BUYERS 40개 `lawd_cd` 전부 region 표에 존재 ✅ (지어낸 코드 없음)

## 5. property_scope별

| scope | 건수 |
|---|---|
| `APARTMENT_AND_SAME_COMPLEX_MULTI_HOUSING` | 37 |
| `APARTMENT_ONLY` | 3 (기흥·동탄·구리) |
| `단독/다가구/아파트/연립/다세대` (외국인 고시 원문) | 7 |

## 6. Hard Gate 코드 위치

```
apt_engine/regulation/gate.py
  applies_to_buyer(target_scope, nationality)        국적
  matches_property(rule, property_type, ...)         물건 유형
  evaluate_candidate(rules, candidate, ...)          ← 통합 판정
  load_rules / coverage_of / buyer_scope_of          DB 접근
```

**아직 `delta_pipeline` 에는 연결하지 않았습니다** — 아래 §10 참고.

## 7. 필수 테스트 14개 결과

| # | 케이스 | 기대 | 결과 |
|---|---|---|---|
| 1 | 서울 노원구 · 내국인 · 비거주 | BLOCK | ✅ |
| 2 | 서울 강남구 · 내국인 · 비거주 | BLOCK | ✅ |
| 3 | 성남 분당구 · 내국인 · 비거주 | BLOCK | ✅ |
| 4 | 화성 동탄구 · 내국인 · 비거주 | BLOCK | ✅ |
| 5 | 용인 기흥구 · 내국인 · 비거주 | BLOCK | ✅ |
| 6 | 구리시 · 내국인 · 비거주 | BLOCK | ✅ |
| 7 | 인천 부평 · 내국인 · 비거주 | **PASS** | ✅ 오차단 없음 |
| 8 | 인천 부평 · 외국인 · 비거주 | BLOCK | ✅ |
| 9 | 군포시 · 내국인 | PASS | ✅ + 필지 미수집 경고 |
| 10 | 안양 만안구 · 내국인 | PASS | ✅ (동안구는 BLOCK) |
| 11 | 토지지분 6㎡ | PASS | ✅ (6.01㎡ 는 BLOCK) |
| 12 | 토지지분 null | NEEDS_CHECK | ✅ |
| 13 | 서울 · 2027-01-01 계약 | 자동 BLOCK 금지 | ✅ NEEDS_CHECK |
| 14 | 동탄 · 2027-01-01 계약 | BLOCK | ✅ |

추가로 용인 처인구·수원 권선구·부천 원미구 미지정 확인, 실거주 예정
PASS_WITH_PERMIT, 계획 미입력 NEEDS_CHECK, 연립·다세대 판정도 고정했습니다.

## 8. 판정 분포 (활성 83개 시·구 · 아파트 · 토지지분 20㎡)

| 조건 | 결과 |
|---|---|
| 내국인 · 비거주 | **BLOCK 40 · PASS 43** |
| 내국인 · 실거주 | PASS_WITH_PERMIT 40 · PASS 43 |
| 내국인 · 계획 미입력 | NEEDS_CHECK 40 · PASS 43 |
| 외국인 · 비거주 | BLOCK 45 · PASS 38 |

외국인 45 = 광역 40 + 인천 5. (인천 7 중 2 는 아래 §10 참고)

## 9. 인천 내국인 오차단 테스트

```
부평 · 내국인 · 비거주 → PASS      ✅ 외국인 규칙이 막지 않음
부평 · 외국인 · 비거주 → BLOCKED   ✅
```

`Decision.not_applicable` 에 "이 규칙은 당신에게 적용되지 않았습니다" 가
남아, 화면에서 "외국인 토허구역이지만 내국인이라 해당 없음" 을 말할 수 있습니다.

## 10. 아직 구축되지 않은 범위

| 범위 | 상태 |
|---|---|
| 광역 아파트 (서울·경기·인천) | **COMPLETE** |
| **필지 단위** (재건축·재개발·신속통합기획·모아타운) | **INCOMPLETE** |
| 외국인 (서울·경기 자치구 목록) | INCOMPLETE |
| 외국인 인천 | **PARTIAL** ← 아래 |

### ⚠ 확인이 필요한 것 — 인천 행정구역 개편

지정 당시의 **중구(28110)·서구(28260)가 region 표에 없습니다.**

```
중구 → 제물포구(28125) · 영종구(28155)
서구 → 서해구(28275) · 검단구(28290)
```

어느 신설 구가 지정을 승계했는지 **공식 확인 전까지 임의로 매핑하지
않았습니다**(§8 추정 금지). 그래서 이 두 행은 조회되지 않고, 인천
FOREIGN_ONLY 커버리지를 `PARTIAL` 로 낮춰 "다 확인했다" 고 말하지
않게 했습니다.

## 11. 기존 기능 유지

**1241개 테스트 전부 통과.** UI 무변경 (`templates/`·`styles/`·`static/`).

## 12. 출처와 기준일

| 무엇 | 출처 | 기준일 |
|---|---|---|
| 서울 25구 · 경기 12시구 | [국토교통부 2025 10·15 대책](https://www.korea.kr/news/policyNewsView.do?newsId=148950973) | 2025-10-20 ~ 2026-12-31 |
| 경기 추가 3시구 | [경기도 공고](https://gnews.gg.go.kr/briefing/brief_gongbo_view.do?BS_CODE=S017&number=70759) | 2026-07-05 ~ 2027-12-31 |
| 서울 별도 토허 현황 | [서울부동산정보광장](https://land.seoul.go.kr/land/other/appointStatusSeoul.do) | 미수집 |
| 인천 외국인 | 국토교통부 · 인천광역시 | 2026-08-20 |

---

## Gate 활성화 조건 (§7) 점검

| # | 조건 | 상태 |
|---|---|---|
| 1 | ALL_BUYERS 40행 | ✅ |
| 2 | FOREIGN_ONLY 분리 | ✅ |
| 3 | 계약일 유효기간 검사 | ✅ |
| 4 | property_scope 검사 | ✅ |
| 5 | 토지지분 6㎡ **초과** 검사 | ✅ |
| 6 | 비거주 BLOCK | ✅ |
| 7 | 실거주 PASS_WITH_PERMIT | ✅ |
| 8 | 인천 오차단 방지 | ✅ |
| 9 | 시·구 오인식 방지 | ✅ (안양·용인·수원·성남·화성 전부 테스트) |
| 10 | coverage_status | ✅ 두 겹 |

**10개 조건을 모두 충족했고, 파이프라인 연결도 끝났습니다.**

---

# 전유부 대지권 — 연결 완료

## 저장 위치는 이미 있었다

`unit_type.land_share_m2` · `land_share_source` 가 이미 있었습니다
(재건축 스크리닝이 쓰던 것). 새 테이블을 만들지 않았습니다.

## 추정값은 문을 열 수 없다 ← 핵심 설계

`apt_engine/regulation/land_share.py`

| 값 | 출처 | 6㎡ 초과 판정 |
|---|---|---|
| 45㎡ | 공시 | **초과** (허가대상) |
| 5㎡ | 공시 | **이하** (대상 아님) |
| 45㎡ | 추정 | **초과** — 닫는 쪽이라 안전 |
| 17㎡ | 추정 | 확인 필요 |
| 5㎡ | 추정 | **확인 필요** ← 문을 열지 않는다 |

비대칭인 이유: **추정이 틀렸을 때 방향이 다릅니다.** 위로 틀리면 허가를
받으면 되지만, 아래로 틀리면 **허가 없이 계약해서 무효**가 됩니다.
경계 근처는 추정이 아무리 커도 안 씁니다 — 기준의 3배(18㎡)를 넘어야
"확실히 초과" 라고 말합니다.

`source` 에 **'공시'** 가 들어가야 확정값으로 취급합니다. 출처를 안 적으면
추정과 구분되지 않고, 구분되지 않으면 문을 여는 데 쓰이게 됩니다.

## 대지권이 없으면 추정한다 (문은 안 열림)

    단지 대지면적 × (이 타입 전용면적 / 전체 전유면적합)

`complex.land_area_m2` 가 있으면 자동으로 나옵니다. 등기부 대지권과
다를 수 있어서 **허가대상 '초과' 판정에만** 쓰입니다.

## 넣는 법

```powershell
python -m apt_engine.cli landshare import --template   # 서식 보기
python -m apt_engine.cli landshare import 대지권.csv
python -m apt_engine.cli landshare status              # 입력 현황
python -m apt_engine.cli landshare show --complex-id 1234 --band 84
```

부동산공시가격알리미(realtyprice.kr) → 공동주택 공시가격 → 산정기초자료,
또는 공공데이터포털 '주택 공시가격 산정기초자료' 파일데이터.

> **대량 수집용 API 는 없습니다.** 공동주택가격정보 API(15124003)는
> 공시가격 위주고 대지권이 응답에 없습니다. 열람·파일데이터가 실질적인
> 경로라, 관심 단지부터 넣는 방식이 현실적입니다.

## 파이프라인 연결

`ranking/delta_pipeline.py` 의 `_permit_gates()` 가 후보마다 대지권을
읽어 Gate 를 겁니다.

**국적이나 실거주 계획을 안 받았으면 Gate 를 걸지 않습니다.** 걸면 전
후보가 NEEDS_CHECK 로 막히는데, 그건 "확인이 필요하다" 를 "아무것도 못
산다" 로 바꿔 말하는 것입니다. 대신 그 사실을 메모로 남깁니다.

```
run(conn, ..., nationality="KOR", occupancy_plan="NON_OCCUPANCY")
```

### 종단간 확인

| 후보 | 대지권 | 판정 |
|---|---|---|
| 강남 · 추정 300㎡ | 추정 | **BLOCKED** (기준 3배 초과라 닫는 쪽 사용) |
| 강남 · 공시 42.7㎡ | 공시 | **BLOCKED** |
| 강남 · 공시 4.2㎡ | 공시 | **PASS** (6㎡ 이하) |
| 군포 · 미지정 지역 | 추정 | **PASS** + 필지 미수집 경고 |
