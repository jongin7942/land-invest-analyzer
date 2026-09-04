"""하락기 실측 방어력 테스트.

이 계층이 지키려는 선:
  1. 방어력은 같은 시군구 대비 **상대값**이다. 동네가 다 같이 빠진 만큼은 방어가 아니다.
  2. 고점·저점은 구간 중앙값이다. 한 건 거래가 고점·저점을 만들면 안 된다.
  3. 비교할 단지가 적으면 값을 만들지 않는다.
  4. 이 값은 하방(_downside)에만 들어가고 상승 점수에는 절대 안 들어간다.
"""
import pytest

from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.features import registry as reg
from apt_engine.features import resilience
from apt_engine.features.base import Status
from apt_engine.repo import apt as repo


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    resilience._REGION_CACHE.clear()
    yield tmp_db
    resilience._REGION_CACHE.clear()


def add_complex(conn, name, lawd="41135"):
    repo.upsert_complexes(conn, [{
        "kapt_code": f"K{name}", "name": name, "name_norm": name,
        "lawd_cd": lawd, "apt_households": 500}])
    return conn.execute("SELECT id FROM complex WHERE kapt_code=?", (f"K{name}",)).fetchone()[0]


def add_series(conn, cid, peak, trough, band="84", months=6):
    """고점 구간과 저점 구간에 각각 months 개월치 스냅샷을 넣는다."""
    peak_yms = ["202201", "202203", "202205", "202207", "202209", "202211"][:months]
    trough_yms = ["202301", "202303", "202305", "202307", "202309", "202311"][:months]
    for ym in peak_yms:
        _snap(conn, cid, band, ym, peak)
    for ym in trough_yms:
        _snap(conn, cid, band, ym, trough)


def _snap(conn, cid, band, ym, price):
    conn.execute(
        "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym, window_months, "
        " representative_price, method, sample_n, confidence, engine_version, data_grade, "
        " calc_trace) VALUES (?,?,?,3,?,'median',5,'HIGH','t','CONFIRMED','{}')",
        (cid, band, ym, price))


class TestRelative:
    def test_동네만큼_빠진_단지는_방어가_0이다(self, db):
        with get_conn(db) as conn:
            # 시군구 단지 8개가 전부 -20%
            for i in range(8):
                add_series(conn, add_complex(conn, f"이웃{i}"), 10_0000_0000, 8_0000_0000)
            me = add_complex(conn, "나")
            add_series(conn, me, 10_0000_0000, 8_0000_0000)
            f = resilience.crash_resilience(conn, me, "84")
        assert f.status is Status.OK
        assert f.value == pytest.approx(0.0, abs=1e-9)

    def test_덜_빠진_단지는_플러스_더_빠진_단지는_마이너스(self, db):
        with get_conn(db) as conn:
            for i in range(8):
                add_series(conn, add_complex(conn, f"이웃{i}"), 10_0000_0000, 8_0000_0000)
            tough = add_complex(conn, "버틴")
            add_series(conn, tough, 10_0000_0000, 9_0000_0000)       # -10%
            weak = add_complex(conn, "무너진")
            add_series(conn, weak, 10_0000_0000, 6_0000_0000)        # -40%
            a = resilience.crash_resilience(conn, tough, "84")
            b = resilience.crash_resilience(conn, weak, "84")
        assert a.value == pytest.approx(+0.10, abs=1e-6)
        assert b.value == pytest.approx(-0.20, abs=1e-6)


class TestRobustness:
    def test_한_건_거래가_고점을_만들지_못한다(self, db):
        """구간 중앙값을 쓰므로 튀는 한 달은 낙폭을 바꾸지 못한다."""
        with get_conn(db) as conn:
            for i in range(8):
                add_series(conn, add_complex(conn, f"이웃{i}"), 10_0000_0000, 8_0000_0000)
            me = add_complex(conn, "나")
            add_series(conn, me, 10_0000_0000, 8_0000_0000)
            _snap(conn, me, "84", "202212", 20_0000_0000)     # 튀는 한 건
            f = resilience.crash_resilience(conn, me, "84")
        assert f.value == pytest.approx(0.0, abs=1e-9)

    def test_구간에_달이_적으면_안_잰다(self, db):
        with get_conn(db) as conn:
            for i in range(8):
                add_series(conn, add_complex(conn, f"이웃{i}"), 10_0000_0000, 8_0000_0000)
            me = add_complex(conn, "드문")
            add_series(conn, me, 10_0000_0000, 8_0000_0000, months=2)
            f = resilience.crash_resilience(conn, me, "84")
        assert f.status is Status.DATA_MISSING

    def test_비교할_단지가_적으면_안_잰다(self, db):
        with get_conn(db) as conn:
            for i in range(3):
                add_series(conn, add_complex(conn, f"이웃{i}"), 10_0000_0000, 8_0000_0000)
            me = add_complex(conn, "나")
            add_series(conn, me, 10_0000_0000, 8_0000_0000)
            f = resilience.crash_resilience(conn, me, "84")
        assert f.status is Status.DATA_MISSING
        assert "비교할 단지" in f.detail["사유"]


class TestRole:
    def test_방어_전용이며_상승_모델에_들어가지_않는다(self):
        e = reg.get("crash_resilience")
        assert e is not None and e.role == reg.ROLE_RISK
        from apt_engine.scoring import models as models_mod
        used = {k for spec in models_mod.SPEC.values() for k, _ in spec}
        assert "crash_resilience" not in used, "상승 점수에 쓰면 저점 매수자에게 반대 신호를 준다"
