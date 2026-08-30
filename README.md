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
- 진행 상황: **PHASE 0(기반) · 1(수집·매칭) · 2(대표가격) · 2.5(호가) · 3(규제·세금·대출) · 4(상대가치) 완료** · PHASE 5(교통호재·공급) 착수 예정

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
