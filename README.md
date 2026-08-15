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

## 알아둘 점 (한계·주의)

- **화성시·부천시 법정동코드 개편**: 화성시는 2026-02부터 4개 구(만세/효행/병점/동탄), 부천시는 원미/소사/오정 3구로 재편됨. `data/gyeonggi_sigungu.py` 에 반영돼 있음.
- **대형 필지 저평가율 주의**: 1,000평 이상 필지는 소규모 거래 위주 기준선과 비교하면 저평가율이 과장될 수 있어 면적 구간별로 별도 비교하지만(`price_baseline.py`), 90%대 극단치는 반드시 수동 확인 권장.
- **실거래가 지번은 마스킹**되어 개별 필지 매칭 불가 — 시세 기준선 산출 전용으로만 사용.
- **법원경매는 미지원** (무료 공식 API 없음).
