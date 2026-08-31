# 김종률식 토지투자 급매 탐지기 (land-invest-analyzer)

경기도 토지 물건 중 **"지을 수 있는 건물 기준의 적정 토지가"** 대비 저평가된 급매/공매 물건을
찾아 카카오톡으로 알리는 프로그램.

> 핵심 명제(김종률): **"이 땅에 어떤 건물을 지을 수 있냐가 돈을 번다."**
> 그래서 이 프로그램의 심장은 *저평가율 필터*가 아니라 **건축가능성 판정 → 건축규모 산출 → 토지가 역산 엔진**이다.

## 로드맵 (Phase 1~5 전부 완료)

- ✅ Phase 1 — 데이터 파이프라인: 국토부 토지 실거래가 수집 (경기도 전 시군구)
- ✅ Phase 2 — 시세 기준선(면적구간 보정) + 건축규모 엔진 + V-World 도로접면/맹지 판정
- ✅ Phase 4 — 온비드 공매 물건(압류재산 등 5개 유형) 연동 + 종합 스코어링
- ✅ Phase 5 — 카카오톡 알림 (stock-alert 카카오 계정 재사용)
- 보류 — 법원경매: 무료 공식 API 없음 (courtauction.go.kr 스크래핑은 법적 회색지대, CODEF는 유료)

## 사전 준비 — API 키 발급 (직접 하셔야 합니다)

프로그램은 두 개의 공공 API 키가 필요합니다. 무료입니다.

### 1) 국토부 실거래가 (data.go.kr)
1. https://www.data.go.kr 회원가입/로그인
2. "국토교통부_토지 매매 실거래가 자료" 검색 → **활용신청**
3. 승인 후 마이페이지 > 오픈API > 인증키에서 **일반 인증키(Decoding)** 복사
4. `.env` 파일의 `DATA_GO_KR_SERVICE_KEY` 에 붙여넣기

### 2) V-World (vworld.kr) — 도로접면·용도지역 판정용
1. https://www.vworld.kr 회원가입/로그인
2. 개발자 > 인증키 발급 (도메인은 로컬 테스트면 `localhost` 로, 2D 데이터 API 포함)
3. `.env` 파일의 `VWORLD_API_KEY` 에 붙여넣기 (승인 즉시 사용 가능)

### 3) 온비드 공매 물건목록 (data.go.kr, 별도 활용신청 필요)
1. data.go.kr에서 **"한국자산관리공사_차세대 온비드 부동산 물건목록 조회서비스"** 검색 → 활용신청 (자동승인)
2. 위 1)과 같은 `DATA_GO_KR_SERVICE_KEY` 그대로 사용 (계정당 인증키 공유, 데이터셋별 활용신청만 별도)

### 4) 카카오톡 알림 — stock-alert 계정 재사용
`C:\Users\jongi\stock-alert` 에서 이미 카카오 "나에게 보내기" 연동을 마쳤다면, PowerShell로 그대로 복사:
```powershell
$src = Get-Content "C:\Users\jongi\stock-alert\config.json" -Raw -Encoding utf8 | ConvertFrom-Json
Add-Content "C:\Users\jongi\land-invest-analyzer\.env" "`nKAKAO_REST_API_KEY=$($src.kakao.rest_api_key)"
@{ refresh_token = $src.kakao.refresh_token } | ConvertTo-Json | Set-Content "C:\Users\jongi\land-invest-analyzer\kakao_token.json" -Encoding utf8
```
(처음부터 새로 연동하려면 stock-alert의 `get_kakao_token.py` 방식을 참고)

## 설치 (Python 3.12)

> **주의**: venv는 OneDrive 동기화 폴더 밖에 두세요(동기화 충돌 방지). 이 프로젝트는 이미 `C:\Users\jongi\` 아래(OneDrive 밖)에 있습니다.

```bash
cd C:\Users\jongi\land-invest-analyzer
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

그 다음 `.env` 를 열어 발급받은 키를 채웁니다.

## 실행

### Phase 1 — 실거래가 수집 (시세 기준선의 재료)
```bash
python main.py --months 3      # 최근 3개월치 경기도 전 시군구
python main.py --stats         # 수집 현황 확인
```

### Phase 2 — 시세·건축규모·맹지 조회 (주소 한 건)
```bash
python analyze.py --address "경기도 이천시 율면 고당리 1"
python analyze.py --baseline --zoning 계획관리지역     # 용도지역 동별 시세
```

### Phase 4 — 온비드 공매 수집 + 스코어링 (핵심 파이프라인)
```bash
python pipeline.py --run                  # 5개 재산유형(공유/기타/압류/수탁/국유재산) 전체
python pipeline.py --run --prpt-div 0007  # 압류재산만 (가장 핵심 카테고리)
python pipeline.py --top 20               # 저장된 상위 후보만 다시 보기
```
결과는 `land_invest.db` 의 `auction_candidate` 테이블에 쌓입니다. 점수 = 저평가율 + 도로접면등급 + 용도지역가점.

### Phase 5 — 카카오톡 알림
```bash
python -m notify.alert --test              # 연동 테스트
python -m notify.alert --send --top 5      # 상위 5건 카톡 발송
python -m notify.alert --send --top 10 --min-score 60   # 점수 60 이상만
```

### 웹 대시보드 — "왜 급매인지" 직접 열람
```bash
python app.py
```
브라우저에서 **http://localhost:5000** 접속. 목록에서 점수·용도지역·맹지 여부로 필터링하고,
물건을 클릭하면 상세 페이지에서 세 가지를 확인할 수 있습니다:
- **💰 왜 급매인가**: 시세 기준선 대비 저평가율, 유찰회차, 감정가 대비 낙찰률
- **🧭 옥탑방보보스(김종률)라면 이렇게 볼 것**: 용도지역·도로접면·건축규모를 그의 노트 원칙과 매칭한 해설
- **✅ 직접 확인할 것**: 프로그램이 못 하는 부분(도로 실측, 조례 확인, 유치권 등) 체크리스트

### 공개 웹사이트 — GitHub Pages (PC 꺼져있어도 카톡 링크로 열람)
```bash
python publish.py    # docs/ 정적사이트 재생성 + git commit + push
```
공개 주소: https://jongin7942.github.io/land-invest-analyzer/ (검색엔진엔 안 걸리게 robots.txt 처리, 링크를 아는 사람만 접근).
`pipeline.py --run` 으로 데이터를 갱신한 뒤 `python publish.py` 를 돌리면 몇 분 내로 사이트에 반영됩니다.
`notify/alert.py` 는 `.env` 의 `PUBLIC_BASE_URL` 이 설정돼 있으면 카톡 링크로 이 주소를 우선 사용합니다.

---

# 수도권 아파트 상대가치 투자 분석 엔진 (`apt_engine/`)

서울·경기·인천 아파트를 대상으로 **"현재 가진 현금으로 어느 아파트를 사는 것이 가장 효율적인가"**
를 계산하는 별도 엔진. "싼 아파트 찾기"가 아니라 상대가치 · 실투자금 · 세금 · 레버리지 ·
재건축 사업성 · 세후 IRR 까지 종합해 **목표수익률에서 오늘의 적정 매수가를 역산**하는 게 목적이다.

위 토지 프로그램과 **완전히 분리**돼 있다 — 코드 네임스페이스(`apt_engine/`)도,
DB 파일(`apt_invest.db`)도 별개다. 아파트 쪽 작업이 토지 파이프라인을 건드릴 수 없다.

- 전체 설계·개발계획: [`docs_dev/00-현황분석-및-고도화계획.md`](docs_dev/00-현황분석-및-고도화계획.md)
- 요구사항 60개 현황표: [`docs_dev/01-요구사항-60개-현황표.md`](docs_dev/01-요구사항-60개-현황표.md)
- 진행 상황: **PHASE 0~6 + 3.9 완료** (기반·수집·대표가격·호가·규제세금대출·상대가치·촉매·재건축 사업성·실투자금/ROE) · PHASE 7(현금흐름·세후 IRR) 착수 예정

## PHASE 0 — 기반

도메인 로직을 얹기 전에, 나중에는 못 붙이는 것들을 먼저 깔았다.

| 모듈 | 역할 |
|---|---|
| `apt_engine/units.py` | 금액은 원(int) · 면적은 ㎡ · 비율은 0~1 단일 단위계. 억/만원/평/%는 입출력에서만 |
| `apt_engine/trace.py` | `Calc` — "입력값 → 계산식 → 결과값 → 근거". 확정/추정/시나리오 등급이 합성 시 **가장 약한 쪽으로 자동 전파**된다 |
| `apt_engine/db/migrate.py` | `PRAGMA user_version` 기반 순차 마이그레이션. 각 단계가 원자적으로 적용된다 |
| `apt_engine/db/migrations/001_meta.sql` | 출처(`data_source`) · 수집이력(`collection_log`) · 엔진버전. 모든 데이터에 출처와 신뢰도를 붙이기 위한 뿌리 |

```bash
pip install -r requirements-dev.txt   # 런타임 3종 + pytest

python -m apt_engine.cli init         # apt_invest.db 생성 + 마이그레이션 적용
python -m apt_engine.cli status       # 스키마 버전 · 테이블 · 매칭 현황

pytest                                # 전체 테스트 (토지 프로그램 회귀 포함)
pytest tests/test_land_regression.py  # 토지 프로그램만 회귀 확인
```

## PHASE 1 — 데이터 수집

### 사전 준비 — data.go.kr 활용신청 3종

기존 `DATA_GO_KR_SERVICE_KEY` 를 그대로 쓴다. 데이터셋별 활용신청만 추가하면 된다.

1. **국토교통부_아파트 매매 실거래가 상세 자료** — 거래유형(중개/직거래)·해제여부·등기일자 포함
2. **국토교통부_아파트 전월세 실거래가 자료** — 계약구분(신규/갱신)·갱신요구권 포함
3. **한국부동산원_공동주택 단지 기본정보(K-apt)** — 세대수·동수·사용승인일. **없으면 "1,000세대 이상" 필터가 불가능하다**

### 필드명 먼저 확인 (권장)

개발 환경에서 data.go.kr 에 접근할 수 없어 응답 필드명을 라이브 검증하지 못했다.
2023년 개편 전후의 영문·한글 이름을 모두 후보로 넣어 뒀지만, 첫 수집 전에 한 번 확인하는 게 안전하다.

```bash
python -m apt_engine.cli probe trade --lawd 11680 --ym 202607
python -m apt_engine.cli probe rent  --lawd 11680 --ym 202607
python -m apt_engine.cli probe kapt-list --lawd 11680
```

응답의 필드명이 다르면 해당 수집기(`apt_engine/collectors/apt_trade.py` 등)의
`FIELDS` 후보에 한 줄 추가하면 된다. K-apt 는 엔드포인트 버전(V2/V3)이 자주 바뀌어
후보 URL 을 순서대로 시도하도록 돼 있다.

### 수집 — 순서가 중요하다

단지가 먼저 있어야 실거래를 붙일 수 있다.

```bash
python -m apt_engine.cli collect complexes            # K-apt 단지 (수도권 전체, 시간 걸림)
python -m apt_engine.cli collect trades --months 60   # 매매 5년치
python -m apt_engine.cli collect rents  --months 60   # 전월세 5년치
python -m apt_engine.cli match                        # 단지 매칭
python -m apt_engine.cli validate                     # 요구사항 26 검증
```

수도권 약 80개 시군구 × 60개월 × 2종 ≒ **1만 회 호출**이라 하룻밤 걸린다.
중단해도 이미 저장된 건 건너뛰므로 다시 돌리면 이어서 받는다.
`--sido 서울` 로 좁히거나 `--months 12` 로 짧게 시작해도 된다.

### 점검

```bash
python -m apt_engine.cli status              # 매칭 신뢰도별 분포
python -m apt_engine.cli report unmatched    # 안 붙은 단지명 (빈도순)
python -m apt_engine.cli report gaps         # 시군구별 수집 공백 월
python -m apt_engine.cli validate            # 검증 규칙 12종
```

`report unmatched` 에 자주 나오는 이름이 있으면 `apt_engine/collectors/matcher.py` 의
`BRAND_ALIASES` 에 표기 규칙을 추가하고 `python -m apt_engine.cli match --rebuild` 로 다시 붙인다.
**원자료는 건드리지 않으므로 매칭은 몇 번이든 다시 할 수 있다.**

## PHASE 2 — 대표가격 · 전세 · 전세가율

```bash
python -m apt_engine.cli snapshot --window 6      # 최근 6개월 창으로 대표가격 산출
python -m apt_engine.cli snapshot --months 60     # 과거 60개월 시계열까지 (요구사항 4의 재료)
python -m apt_engine.cli price "동아1단지" --verbose  # 근거까지 펼쳐 보기
```

단일 최고가를 현재가격으로 쓰지 않는다. 취소거래·직거래·월세 낀 계약은 무조건 빼고,
1층·통계적 이상치·갱신요구권 갱신계약은 표본이 3건 아래로 떨어질 때만 되살리며
**되살렸다는 사실을 계산근거에 남긴다.** 신뢰도는 10건+ HIGH / 3~9 MEDIUM / 1~2 LOW.

## PHASE 2.5 — 호가 (외부 API 불필요)

실거래와 호가는 다른 것이라 테이블부터 분리돼 있다. 지금은 수기 입력만 지원한다.

```bash
python -m apt_engine.cli listing template 매물.csv    # 입력 서식 생성
# 서식에 임장/네이버에서 본 매물을 적는다. 가격은 6.2(억) 또는 620000000(원) 둘 다 됨
python -m apt_engine.cli listing import 매물.csv      # 저장 + 오늘 스냅샷 + 단지 매칭
python -m apt_engine.cli market "동아1단지" --band 84  # 분포·괴리·변화·시장압력
```

**매일 한 번씩 넣으면** 7/30/90일 매물 증감·가격인하·최저호가 변화가 계산되고,
그게 시장압력 점수(매도자우위/매수자우위)의 근거가 된다. 하루치만 있으면
점수를 만들지 않고 "확인 불가"로 표시한다.

중개사에게 들은 협상 가능가는 호가가 **아니다**. 별도 테이블에 출처와 함께 기록한다.

```bash
python -m apt_engine.cli listing note --complex-id 1 --band 84 \
  --kind 중개사확인 --price 6.05 --note "6.05억이면 맞출 수 있다고 함" --source "○○공인 김실장"
```

## PHASE 3 — 규제 · 토허 · 세법 · 대출 · 실투자금

이 영역에는 공식 API 가 없다. 값을 코드에 적지 않고 규칙 테이블에 넣으며,
**사람이 원문을 확인했다는 표시(`last_verified`)가 없으면 엔진이 계산을 거부**한다.

```bash
python -m apt_engine.cli rule status                    # 채워진 정도
python -m apt_engine.cli rule template tax  세법.csv     # 서식 (값은 비어 있다)
python -m apt_engine.cli rule import   tax  세법.csv
python -m apt_engine.cli rule verify   tax  --id 3      # 원문 확인 표시

python -m apt_engine.cli regulation "동아1단지"           # 규제지역·토허 판정
python -m apt_engine.cli loan "동아1단지" --price 6.2 --income 1.0 --house-count 1
python -m apt_engine.cli cash "동아1단지" --price 6.2 --house-count 1 --verbose
```

실투자금이 나오는 최소 조합은 **취득세 + 중개보수 + 대출(LTV/DSR)** 셋이다.

- 토허를 모르면 전세보증금을 차감하지도, 차감했다고 말하지도 않는다
- LTV 한도와 DSR 한도를 각각 내고 **더 작은 쪽**을 쓴다
- 모르는 항목을 0으로 세지 않는다 — 확인 불가 항목이 있으면 "N억 이상"으로만 말한다

## PHASE 3.9 — 대출 · 세금 · 취득비용 · 실투자금 (FINANCING / TAX / SELF CAPITAL)

이 프로그램의 질문은 **"매매가가 얼마인가"가 아니라 "내 현금이 실제로 얼마 들어가는가"** 다.

```bash
python -m apt_engine.cli rule status                          # 규칙이 얼마나 찼나
python -m apt_engine.cli rule template loan 대출.csv           # ★ 지금 비어 있는 곳
python -m apt_engine.cli rule import   loan 대출.csv

python -m apt_engine.cli profile set --name 종인 \
    --cash 3 --income 0.8 --rate 0.045 --home-count 0          # 현금·소득 (하드코딩 금지)

python -m apt_engine.cli cash "동아1단지" --price 6.2 --area 84.9 \
    --income 0.8 --rate 0.045 --cash 3                         # 총취득비용 · 실투자금
python -m apt_engine.cli budget --profile 종인 --band 84       # 살 수 있는 단지만
```

### 취득세는 세 세목을 따로 계산한다

`취득세` / `지방교육세` / `농어촌특별세` 를 **하나의 합산세율로 저장하지 않는다.**
셋의 과세표준과 감면이 서로 다르기 때문이다. 특히 농특세는 두 갈래로 나뉜다.

| 항목 | 언제 생기나 |
|---|---|
| `rural_special_tax_regular` | 일반분. 전용 **85㎡ 이하는 비과세** |
| `rural_special_tax_from_exemption` | 취득세를 **감면받으면 그 감면액에** 붙는다 |

6억~9억 구간은 2% 고정이 아니라 **점증세율**이다 — `(취득가액 × 2 ÷ 3억 − 3) %`.
7억은 1.6667%, 7.5억은 2%, 8억은 2.3333%. 구간 안에서 세율이 연속으로 변해 표에
담을 수 없어서, `tax_rule.rate_formula` 에 산식을 넣고 제한된 AST 로 평가한다
(`eval` 은 쓰지 않는다 — CSV 는 사람이 편집하는 파일이다).

### 시행 중인 법령과 발표된 정책을 섞지 않는다

모든 규칙에 `status` 가 붙는다.

| status | 계산 |
|---|---|
| `ENACTED` | **계산에 쓰는 유일한 상태** |
| `ANNOUNCED` · `PROPOSED` | 금액에 넣지 않고 "향후 정책 변경 가능"으로만 표시 |
| `EXPIRED` | 백테스트에는 여전히 필요하다 |

`verification`(VERIFIED / ESTIMATED / UNKNOWN / NEEDS_VERIFICATION)은 다른 축이다.
**하나라도 UNKNOWN 이면 "실투자금 확정"이 아니라 "예상 실투자금"으로만 표시된다.**

### 대출은 LTV 하나로 계산하지 않는다

```
POLICY_MAX_MORTGAGE = min(LTV 한도, DSR 한도, 절대 상한, 요청액)
EXPECTED_MORTGAGE   = 은행 견적이 있으면 그 값, 없으면 정책 최대치 + 안내문구
```

정책값은 `loan_rule` 에 **rule_type(LTV / DSR / STRESS_DSR / MORTGAGE_CAP / DTI) 별로
한 행씩** 들어간다. 한 행에 LTV 와 DSR 을 같이 적으면 시행일이 다른 두 정책을
표현할 수 없다. 스트레스 가산금리는 **한도 계산에만** 쓰고 이자비용에는 쓰지 않는다
(섞으면 이자가 부풀려진다). 한도 하나라도 확인 불가면 최종값에 "이보다 작을 수
있음"이 붙는다 — 모르는 한도를 무한대로 두지 않는다.

### 실투자금이 판정 기준이다

```
TOTAL_PURCHASE_COST   = 매수가 + 취득세 3종 + 중개보수(+VAT) + 법무·등기비 + 기타
SELF_CAPITAL_REQUIRED = TOTAL_PURCHASE_COST − AVAILABLE_MORTGAGE − ASSUMABLE_DEPOSIT
```

"현금 3억"은 **매매가 3억 이하**가 아니라 **`SELF_CAPITAL_REQUIRED` ≤ 3억**이다.
대출이 나오고 전세를 승계하면 6억짜리도 실투자금은 2억일 수 있고, 대출이 막히면
4억짜리도 4.3억이 든다. 매매가로 거르면 둘 다 틀린다.

실투자금을 **확정하지 못한 단지는 '매수 가능' 목록에 넣지 않는다.** 모르는 비용을
0원으로, 모르는 대출을 최대치로 세면 살 수 없는 집이 살 수 있는 집으로 올라온다.

### 내 돈 대비 수익률

```
EXPECTED_PROFIT = 예상 매도가 − 매도비용(양도세·지방소득세·중개보수) − 총취득비용 − 금융비용
EXPECTED_ROE    = EXPECTED_PROFIT ÷ SELF_CAPITAL_REQUIRED
```

예상 매도가는 엔진이 만들지 않는다 — 넣지 않으면 수익률을 계산하지 않는다.
`DOWNSIDE_RISK` 도 하락 시나리오 가격을 주지 않으면 '확인 불가'다.
"보통 20% 빠진다" 같은 숫자를 만들지 않는다.

### 백테스트가 규칙표에 직접 의존한다

2021년 물건을 분석하면 2021년 당시 세법·LTV·DSR·규제지역이 적용돼야 한다.
그래서 모든 규칙 조회는 `as_of` 를 **키워드 필수 인자**로 받고 기본값이 없다.
정책이 바뀌면 기존 행을 덮어쓰지 말고 `effective_to` 를 채운 뒤 새 행을 추가한다.

### 규칙표 현황 (2026-08-31)

| 파일 | 행 | 상태 |
|---|---:|---|
| `rules/loan.csv` | 13 | DSR(은행 40%/비은행 50%) · 스트레스 DSR(150/300bp) · 규제지역 LTV 40% · 절대한도 3구간 |
| `rules/tax.csv` | 26 | 취득세 일반 9 + 중과 5 · 지방교육세 9 · 농특세 2 · 부가세 1 |
| `rules/cost.csv` | 16 | 인천 중개보수 6 · 인지세 6 · 법무사 보수 4(ESTIMATED) |

> **아직 비어 있는 것**: 비규제·생애최초·다주택 LTV, 중과 건 지방교육세,
> 법무 실비 3종, 양도소득세, 재산세·종부세, 서울·경기 중개보수 조례.
> 각 CSV 안에 채울 자리를 주석 행으로 만들어 뒀고, 무엇이 막히는지는
> [`docs_dev/02-확인필요-정책값.md`](docs_dev/02-확인필요-정책값.md) 에 있다.

## PHASE 4 — 상대가치 (가격사다리 · 비교단지 · 가격비율)

```bash
python -m apt_engine.cli ladder template 사다리.csv   # 축 서식 (요구사항의 예시 축 포함)
# lawd_cd 를 채운 뒤
python -m apt_engine.cli ladder import 사다리.csv
python -m apt_engine.cli ladder list

python -m apt_engine.cli relative build --band 84
python -m apt_engine.cli relative show "동아1단지" --band 84 --verbose
```

가격사다리는 데이터가 아니라 **도메인 지식**이라 사람이 적고, 축마다 근거(`rationale`)와
작성자(`curated_by`)를 남긴다. 사다리가 비어 있으면 비교단지가 거의 안 잡히는데,
그게 의도한 동작이다 — "비슷해 보여서" 골라주지 않는다.

- 비교단지는 **선정 근거를 항목별 점수로** 남긴다(`selection_reason_json` NOT NULL)
- Current Ratio(지금)와 Historical Normal Ratio(과거 정상)를 다른 테이블에 둔다
- 시장 상승기/하락기는 공식 지수가 없어 **자체 판정**이고, 그 사실을 근거에 남긴다
- 비율이 벌어졌다는 사실만 말하고 "저평가"라고 선언하지 않는다 — 벌어진 이유는 PHASE 5의 일

## PHASE 5 — 교통호재 · 공급 · 개통 선행사례

```bash
python -m apt_engine.cli transit template 교통.csv    # 노선·역 단계 서식
python -m apt_engine.cli transit import 교통.csv
python -m apt_engine.cli supply  template 공급.csv    # 입주물량 서식
python -m apt_engine.cli supply  import 공급.csv

python -m apt_engine.cli geocode --limit 200          # 단지 좌표 (V-World, 기존 키)
python -m apt_engine.cli catalyst build --as-of 2026-08-31 --years 5
python -m apt_engine.cli catalyst show "동아1단지" --verbose
```

**계획과 개통을 절대 섞지 않는다.** `status`(계획/예타/기본계획/착공/공사중/개통예정/개통)와
`status_date`(그 단계가 된 날 — 사실), `expected_open_ym`(개통 예정 — 추정),
`opened_ym`(실제 개통 — 사실)이 각각 다른 컬럼이다. `status='개통'` 인데 `opened_ym` 이
없으면 DB가 거부한다.

- **호재를 투자기간과 연결**한다 — "2035년 개통 예정, 투자기간 5년 → 개통은 기간 밖,
  기대감만 기간 안"
- **"GTX 생기면 몇 % 오른다"를 만들지 않는다.** 이미 개통한 역의
  *역세권/비역세권 가격비율* 변화만 기록한다. 시장 전체의 등락은 분자·분모에서 상쇄된다
- 직선거리와 도보거리를 구분한다. 직선 500m 를 "도보 7분 역세권"이라 부르지 않는다
- 공급은 반경별로 세되 **어느 기준으로 셌는지 밝힌다**. 좌표 없는 공급을 뺐으면 그것도 알린다
- 근거 없는 촉매는 `evidence_json` NOT NULL 이라 저장 자체가 안 된다

## PHASE 6 — 재건축 사업성 (2단계 스크리닝)

이 구간이 어려운 건 산식이 아니라 **입력**이다. 대지면적·정비계획 용적률·평당 공사비·
종전자산 감정평가액은 공공 API 로 나오지 않는다. 그래서 두 단계로 나눈다.

```bash
# 1단계 — 전국 자동. 이미 수집된 값(연식·현재 용적률·대지지분)만 쓴다
python -m apt_engine.cli redev screen --lawd 28237 --limit 50
python -m apt_engine.cli redev mark "동아1단지" --status 조사중

# 2단계 — 상위 후보만 사람이 채운다
python -m apt_engine.cli redev template landarea 대지면적.csv   # 건축물대장
python -m apt_engine.cli redev template project  정비사업.csv   # 단계·정비계획
python -m apt_engine.cli redev template far      용적률.csv     # 조례/법정상한/특례
python -m apt_engine.cli redev template cost     공사비.csv     # 평당 공사비(기준연도 필수)
python -m apt_engine.cli redev template duration 소요기간.csv   # 단계별 기간 통계
python -m apt_engine.cli redev import  project   정비사업.csv

python -m apt_engine.cli redev status                          # 입력 진행률
python -m apt_engine.cli redev show "동아1단지" \
    --cost-per-py 750 --cost-base-year 2025 --other-cost-rate 0.25 \
    --new-price-py 2200 --prior-asset 6.2 --price 6.2 --save
```

**1단계에서는 금액을 만들지 않는다.** 스크리닝 결과는 "후보인가"와 순위뿐이고,
추가분담금·비례율 같은 숫자는 2단계 자료가 들어온 단지에서만 나온다.

**법정 최대 용적률을 사업 용적률로 쓰지 않는다.** `far_standard.kind` 가
`법정상한 / 조례 / 정비계획 / 역세권특례` 네 종류를 다른 행으로 저장하고,
숫자는 언제나 종류를 달고 다닌다(`250% (조례)`). 조례·정비계획이 없고 법정상한만
있으면 엔진은 **자동으로 고르지 않고 거부한다** — 쓰려면 `--far-kind` 로 명시해야 한다.
정비계획 용적률은 정비구역지정 이후 단계에만 저장할 수 있다(스키마 CHECK).

**추가분담금은 하나의 숫자가 아니라 구간이다.** 보수/기준/낙관 3구간과
민감도(공사비·분양가·용적률을 하나씩 ±20%)를 함께 낸다. 저장 등급은
`SCENARIO` 하나뿐이라, 분담금을 확정 금액처럼 저장할 방법이 스키마에 없다.

**신축전환원가** = 매수가 + 취득비용 + 추가분담금 + 금융비용 + 보유비용.
모르는 항목은 0으로 세지 않고 "N억 이상"으로만 말한다. 재건축 마진은
`준공 후 예상 가치 − 신축전환원가` 이고, 준공 후 가치에 **미래 상승률을 곱하지 않는다**
(별도 시세 가정이 없으면 일반분양가로 갈음하고 그 사실을 밝힌다).

**사업기간은 지어내지 않는다.** `stage_duration_ref`(단계별 소요기간 통계, 수기)가
비어 있으면 "약 10년" 같은 숫자를 만들지 않고 `확인 불가` 로 답한다. 참고치가 있는
구간만 더하고 없는 구간은 "N개 구간 참고치 없음"으로 밝힌다.
지연위험은 점수를 지어내지 않고 관측 사실 세 가지(현재 단계 / 스스로 적은 예정일이
지났는가 / 마지막 단계 변경 이후 경과)로만 판정한다.

### PHASE 1 이 막는 것

| 요구사항 26 | 어떻게 막는가 |
|---|---|
| 26-1 999세대를 1000세대 필터에 넣지 말 것 | 세대수 필터가 `apt_households >= N` 만 본다 |
| 26-2 아파트와 오피스텔 세대수를 합치지 말 것 | **합계 컬럼을 아예 만들지 않았다** |
| 26-3 근거 없이 단지를 합치지 말 것 | `complex_group.merge_reason` NOT NULL + 매칭이 애매하면 안 붙임 |
| 26-4 84㎡에 59/74/101을 섞지 말 것 | 모든 거래에 `area_band` 저장(84 = 80~85㎡), 검증 규칙이 대조 |
| 26-5 취소거래 | `cancel_yn` / `cancel_ymd` 수집 |
| 26-6 직거래를 동일 취급하지 말 것 | `deal_type` 수집 + 비어 있으면 검증이 경고 |

각 PHASE 를 끝낼 때마다 `pytest tests/test_land_regression.py` 로 **기존 토지 기능이
그대로인지** 확인한다. 이 테스트는 네트워크를 타지 않아 API 키 없이도 돌아간다.

## 다음에 할 일

종인님이 채워야 할 데이터와 제가 이어서 만들 PHASE 는
[`docs_dev/03-종인님-할일-정리.md`](docs_dev/03-종인님-할일-정리.md) 에 정리돼 있다.
급한 것 세 가지만 옮기면:

1. **인천 토지거래허가구역이 2026-08-25 로 만료**됐다 — 재지정 여부 확인 (기한 있음)
2. 법무·등기 실비 3종을 넣으면 실투자금이 '예상'에서 **'확정'**이 된다
3. 양도소득세를 넣으면 **EXPECTED_ROE**(내 돈 대비 수익률)가 열린다

## 설계 원칙 (엔진 코드가 지켜야 할 것)

1. **엔진은 DB를 모른다.** 조회는 `repo/` 가 하고 엔진 함수에는 값으로 주입한다.
2. **`as_of` 는 모든 규칙 조회의 필수 인자**이고 기본값이 없다. 지정기간이 끝난 토지거래허가구역을
   현재로 표시하거나, 작년 세법으로 올해를 계산하는 사고를 호출 시점에 막는다.
3. **모든 엔진 함수는 값이 아니라 `Calc` 를 반환한다.** 그 안에 입력·계산식·중간값·근거·등급이 다 들어 있다.
4. **금액은 원(int) 하나로만.** 엔진 본문에 `/ 10000` 이나 `* 3.3058` 이 보이면 반려.
5. **LLM 은 `narrative/` 계층에만.** 리포트 문장을 쓰는 역할이고,
   가격·세금·대출·거래량·토허·용적률 숫자는 전부 계산엔진에서 온다.

## 알아둘 점 (한계·주의)

- **화성시·부천시 법정동코드 개편**: 화성시는 2026-02부터 4개 구(만세/효행/병점/동탄), 부천시는 원미/소사/오정 3구로 재편됨. `data/gyeonggi_sigungu.py` 에 반영돼 있음.
- **대형 필지 저평가율 주의**: 1,000평 이상 필지는 소규모 거래 위주 기준선과 비교하면 저평가율이 과장될 수 있어 면적 구간별로 별도 비교하지만(`price_baseline.py`), 90%대 극단치는 반드시 수동 확인 권장.
- **실거래가 지번은 마스킹**되어 개별 필지 매칭 불가 — 시세 기준선 산출 전용으로만 사용.
- **법원경매는 미지원** (무료 공식 API 없음).
