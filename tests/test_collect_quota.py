"""일일 트래픽 한도(HTTP 429) 를 만나면 즉시 멈춘다.

2026-08-31 실측: 매매 116/240개월에서 data.go.kr 일일 한도가 소진됐다. 그때부터
남은 124개월을 계속 두드려 실패가 558건 쌓였다. 한도가 찬 뒤의 호출은 전부
거부되므로 계속 도는 것은 시간만 버리고, 실패 기록만 지저분해진다.

'안 받은 것' 과 '실패한 것' 은 다르다. 한도 소진은 실패로 남기지 않는다 —
다음 날 이어받으면 그만이다.
"""
import pytest

from apt_engine import ingest
from apt_engine.collectors import apt_trade, molit
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        ingest.repo.sync_regions(conn)
    return tmp_db


def test_한도_초과면_즉시_멈춘다(db, monkeypatch):
    calls = []

    def _fetch(lawd_cd, ym):
        calls.append((lawd_cd, ym))
        if len(calls) > 3:                     # 네 번째부터 한도 소진
            raise molit.MolitQuotaError("일일 트래픽 한도를 다 썼습니다 (HTTP 429).")
        return []

    monkeypatch.setattr(apt_trade, "fetch_month", _fetch)
    s = ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)

    assert s["quota_exhausted"] is True
    assert len(calls) == 4, f"한도 소진 뒤에도 {len(calls) - 4}번 더 불렀습니다"


def test_한도_초과는_실패로_남기지_않는다(db, monkeypatch):
    """실패로 쌓으면 다음 날 재시도 목록이 지저분해진다. 안 받은 것일 뿐이다."""
    monkeypatch.setattr(apt_trade, "fetch_month",
                        lambda *_: (_ for _ in ()).throw(
                            molit.MolitQuotaError("한도 소진")))
    ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)

    with get_conn(db) as conn:
        failed = conn.execute(
            "SELECT COUNT(*) FROM collection_log WHERE status='FAILED'").fetchone()[0]
    assert failed == 0, f"한도 소진이 실패 {failed}건으로 기록됐습니다"


def test_한도_소진_전까지_받은_것은_남는다(db, monkeypatch):
    calls = []

    def _fetch(lawd_cd, ym):
        calls.append((lawd_cd, ym))
        if len(calls) > 2:
            raise molit.MolitQuotaError("한도 소진")
        return [{
            "lawd_cd": lawd_cd, "emd_name": "테스트동", "jibun": "1",
            "apt_name": "테스트", "exclusive_area_m2": 84.0, "area_band": "84",
            "deal_amount": 500_000_000, "deal_ymd": f"{ym}15", "floor": 5,
            "build_year": 2000, "deal_type": "중개거래", "cancel_yn": 0, "raw": {},
        }]

    monkeypatch.setattr(apt_trade, "fetch_month", _fetch)
    ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)

    with get_conn(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM trade").fetchone()[0]
    assert n == 2, "한도 소진 전에 받은 거래가 남아 있지 않습니다"


def test_다음날_다시_돌리면_이어받는다(db, monkeypatch):
    """한도 소진으로 멈춘 뒤, 한도가 풀리면 안 받은 곳부터 이어받아야 한다."""
    state = {"quota": 2}
    calls = []

    def _fetch(lawd_cd, ym):
        if state["quota"] <= 0:
            raise molit.MolitQuotaError("한도 소진")
        state["quota"] -= 1
        calls.append((lawd_cd, ym))
        return []

    monkeypatch.setattr(apt_trade, "fetch_month", _fetch)

    ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)
    first = list(calls)
    assert len(first) == 2

    state["quota"] = 99                        # 자정이 지나 한도가 풀렸다
    calls.clear()
    s = ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)

    assert s["skipped"] > 0, "이미 받은 것을 건너뛰지 않았습니다"
    assert not set(calls) & set(first[:1]), "이미 받은 조합을 다시 받았습니다"


def test_429_응답이_MolitQuotaError_가_된다():
    """HTTP 429 나 LIMITED_NUMBER_OF_SERVICE_REQUESTS 는 재시도해도 소용없다."""
    assert issubclass(molit.MolitQuotaError, molit.MolitError)
    # 인증 오류와는 다른 종류여야 한다 — 인증은 사람이 고쳐야 하고, 한도는 기다리면 된다.
    assert not issubclass(molit.MolitQuotaError, molit.MolitAuthError)
