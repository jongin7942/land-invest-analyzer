"""pytest 공통 설정.

pytest 는 테스트 파일이 있는 폴더를 sys.path 에 넣지, 리포지토리 루트를 넣지 않는다.
루트를 넣어야 config / apt_engine / analysis 를 import 할 수 있다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path):
    """테스트마다 새 빈 DB 파일 경로. 실제 apt_invest.db 는 건드리지 않는다."""
    return str(tmp_path / "test_apt.db")
