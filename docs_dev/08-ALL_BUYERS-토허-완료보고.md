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

**10개 조건을 모두 충족했습니다.** 다만 파이프라인 연결은 남겨 뒀습니다 —
`evaluate_candidate` 가 **토지지분(`land_share_sqm`)** 을 요구하는데
`complex` 표에 그 컬럼이 없습니다. 지분 없이 연결하면 서울·경기 40개
지역 후보가 전부 `LAND_SHARE_AREA_MISSING` 으로 NEEDS_CHECK 가 됩니다.

토지지분을 어디서 받을지 정하면 (등기부·건축물대장·공동주택 공시가격
전유부 대지권) 바로 연결하겠습니다.
