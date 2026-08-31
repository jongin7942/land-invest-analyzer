"""수집 재시작 — 이미 받은 (거래월 × 시군구)를 다시 받지 않는다.

240개월 수집은 하루가 넘는다. 중간에 인터넷이 끊겨 다시 돌릴 때 처음부터 다시
훑으면 몇 시간과 API 호출을 그대로 다시 태운다. 데이터는 UNIQUE 로 중복되지
않으니 조용히 느려지기만 해서, 테스트가 없으면 눈치채기 어렵다.

여기서는 API 를 실제로 부르지 않는다. `fetch_month` 를 가짜로 바꿔 **몇 번
불렸는지** 만 센다. 그래야 '건너뛰었다' 를 실제로 확인할 수 있다.
"""
import pytest

from apt_engine import ingest
from apt_engine.collectors import apt_trade
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        ingest.repo.sync_regions(conn)
    return tmp_db


@pytest.fixture
def fake_fetch(monkeypatch):
    """fetch_month 를 가짜로. 호출된 (시군구, 거래월) 을 전부 기록한다."""
    calls: list[tuple[str, str]] = []

    def _fetch(lawd_cd, ym):
        calls.append((lawd_cd, ym))
        return [{
            "lawd_cd": lawd_cd, "emd_name": "테스트동", "jibun": "1",
            "apt_name": f"테스트{lawd_cd}", "exclusive_area_m2": 84.0,
            "area_band": "84", "deal_amount": 500_000_000,
            "deal_ymd": f"{ym}15", "floor": 5, "build_year": 2000,
            "deal_type": "중개거래", "cancel_yn": 0, "raw": {},
        }]

    monkeypatch.setattr(apt_trade, "fetch_month", _fetch)
    return calls


def test_두번째_실행은_이미_받은_달을_건너뛴다(db, fake_fetch):
    first = ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)
    n_first = len(fake_fetch)
    assert n_first > 0
    assert first["skipped"] == 0

    fake_fetch.clear()
    second = ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)

    # 최근 REFETCH_MONTHS 개월은 신고 지연 때문에 일부러 다시 받는다.
    codes = len({c for c, _ in [(x[0], x[1]) for x in fake_fetch]}) or 1
    assert second["skipped"] > 0, "두 번째 실행이 아무것도 건너뛰지 않았습니다"
    assert len(fake_fetch) < n_first, "두 번째 실행이 처음과 같은 횟수를 호출했습니다"

    refetched = {ym for _, ym in fake_fetch}
    assert len(refetched) == ingest.REFETCH_MONTHS, \
        f"다시 받은 달이 {sorted(refetched)} — {ingest.REFETCH_MONTHS}개월이어야 합니다"


def test_최근_3개월은_항상_다시_받는다(db, fake_fetch):
    """실거래는 계약 후 30일 내 신고라, 지난달 데이터가 나중에 더 들어온다."""
    ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)
    yms = ingest.recent_yms(6)

    fake_fetch.clear()
    ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)

    refetched = {ym for _, ym in fake_fetch}
    assert refetched == set(yms[-ingest.REFETCH_MONTHS:])


def test_full_이면_전부_다시_받는다(db, fake_fetch):
    ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)
    n_first = len(fake_fetch)

    fake_fetch.clear()
    s = ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None,
                              full=True)
    assert s["skipped"] == 0
    assert len(fake_fetch) == n_first


def test_데이터없음_도_끝난_것으로_본다(db, monkeypatch):
    """EMPTY 는 '받아봤더니 그 달에 거래가 없었다' 라 다시 받을 이유가 없다."""
    calls = []
    monkeypatch.setattr(apt_trade, "fetch_month",
                        lambda lawd, ym: calls.append((lawd, ym)) or [])

    ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)
    n_first = len(calls)

    calls.clear()
    s = ingest.collect_trades(6, "인천", db_path=db, progress=lambda *_: None)
    assert s["skipped"] > 0
    assert len(calls) < n_first
