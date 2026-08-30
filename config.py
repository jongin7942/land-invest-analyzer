"""환경설정 로드. .env 파일에서 API 키와 DB 경로를 읽는다."""
import os
import socket
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _lan_ip() -> str:
    """이 PC의 로컬망(LAN) IP 추정. 실제 패킷은 안 보내는 UDP 소켓 트릭 —
    같은 와이파이의 폰에서 카톡 링크로 접속하려면 localhost 대신 이 IP가 필요하다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()

BASE_DIR = Path(__file__).resolve().parent

DATA_GO_KR_SERVICE_KEY = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "").strip()
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "").strip()
KAKAO_TOKEN_PATH = BASE_DIR / "kakao_token.json"  # refresh_token은 회전될 수 있어 .env 대신 별도 파일
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "").strip()

_db_path_raw = os.getenv("DB_PATH", "").strip() or "land_invest.db"
# 상대경로면 실행 위치(cwd)와 무관하게 항상 이 프로젝트 폴더 기준으로 고정한다.
# (app.py 를 다른 작업폴더에서 launch.json으로 띄우면 cwd가 달라져 엉뚱한 빈 DB가 생기는 버그 방지)
DB_PATH = _db_path_raw if os.path.isabs(_db_path_raw) else str(BASE_DIR / _db_path_raw)

# 아파트 투자분석 엔진 전용 DB(apt_engine 패키지). 토지 DB와 **별도 파일**로 둔다 —
# 아파트 쪽 재수집·마이그레이션이 잘 돌아가는 land_invest.db 를 건드릴 수 없게 하기 위해서다.
_apt_db_path_raw = os.getenv("APT_DB_PATH", "").strip() or "apt_invest.db"
APT_DB_PATH = _apt_db_path_raw if os.path.isabs(_apt_db_path_raw) else str(BASE_DIR / _apt_db_path_raw)

# 카톡 알림에 넣을 웹앱 링크의 기준 주소. .env 에 BASE_URL 을 직접 지정하면 그걸 쓰고,
# 없으면 이 PC의 LAN IP로 자동 구성한다(같은 와이파이의 폰에서 접속 가능, PC 켜져있어야 함).
BASE_URL = os.getenv("BASE_URL", "").strip() or f"http://{_lan_ip()}:5000"

# GitHub Pages 공개 링크(정적 사이트, build_static.py 로 생성). 설정돼 있으면 카톡 알림이
# LAN 링크 대신 이걸 우선 사용 — PC가 꺼져 있어도, 다른 사람도 열람 가능.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip()


def require_data_go_kr_key() -> str:
    """실거래가 수집 전 키 존재를 확인. 없으면 친절한 에러."""
    if not DATA_GO_KR_SERVICE_KEY:
        raise SystemExit(
            "DATA_GO_KR_SERVICE_KEY 가 비어 있습니다.\n"
            ".env 파일에 data.go.kr 에서 발급받은 '일반 인증키(Decoding)'를 넣어주세요.\n"
            "발급 방법은 README.md 참고."
        )
    return DATA_GO_KR_SERVICE_KEY
