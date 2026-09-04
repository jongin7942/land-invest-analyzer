"""역세권 접근성 feature 테스트.

이 계층이 지키려는 선:
  1. **수준을 알파로 세지 않는다.** 역세권이라 비싼 것은 이미 값에 있다.
     여기서 세는 것은 격차가 앞으로 더 벌어질 속도뿐이다.
  2. **미개통 역은 세지 않는다.** 개통 사례 117건에서 개통 효과가 0 으로
     측정됐다(tools/event_study.py). 계획·착공 중인 역 옆이라고 가점을 주면
     측정으로 부정된 가정에 돈을 거는 것이 된다.
  3. **'모른다' 와 '멀다' 를 섞지 않는다.** 좌표가 없는 단지를 최하위 밴드로
     보내면, 데이터가 없다는 이유로 실제보다 나쁘게 평가된다.
  4. 투자기간이 길수록 누적 폭이 커진다.
"""
import csv

import pytest

from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.features import access
from apt_engine.features.base import Status
from apt_engine.repo import apt as repo
from apt_engine.repo import catalyst as cat_repo

# 서울 시청 근처를 기준점으로 잡고 거리를 만든다.
BASE_LAT, BASE_LON = 37.5665, 126.9780


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


@pytest.fixture
def table(tmp_path):
    """작은 밴드표. 실제 측정값과 같은 모양이되 숫자는 읽기 쉽게 잡았다."""
    path = tmp_path / "drift.csv"
    rows = [
        {"band": "~500m", "max_meters": 500, "annual_drift": 0.001,
         "level_first": 1.02, "level_last": 1.05, "complexes": 700,
         "years": 18, "first_year": 2008, "last_year": 2025,
         "source_name": "테스트", "note": "테스트"},
        {"band": "1~2km", "max_meters": 2000, "annual_drift": -0.001,
         "level_first": 0.98, "level_last": 0.95, "complexes": 90,
         "years": 18, "first_year": 2008, "last_year": 2025,
         "source_name": "테스트", "note": "얇은 밴드"},
        {"band": "2km 밖", "max_meters": "", "annual_drift": -0.002,
         "level_first": 0.95, "level_last": 0.91, "complexes": 80,
         "years": 18, "first_year": 2008, "last_year": 2025,
         "source_name": "테스트", "note": "얇은 밴드"},
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    access.bands.cache_clear()
    yield str(path)
    access.bands.cache_clear()


def add_complex(conn, name, *, lat=None, lon=None):
    repo.upsert_complexes(conn, [{
        "kapt_code": f"K{name}", "name": name, "name_norm": name,
        "lawd_cd": "11110", "apt_households": 500}])
    cid = conn.execute("SELECT id FROM complex WHERE kapt_code=?",
                       (f"K{name}",)).fetchone()[0]
    if lat is not None:
        cat_repo.set_coords(conn, cid, lat, lon)
    return cid


def add_station(conn, name, *, status, lat=BASE_LAT, lon=BASE_LON):
    conn.execute("INSERT INTO transit_project (name, kind) VALUES (?, '지하철') "
                 "ON CONFLICT(name) DO NOTHING", (f"P{name}",))
    pid = conn.execute("SELECT id FROM transit_project WHERE name=?",
                       (f"P{name}",)).fetchone()[0]
    conn.execute(
        "INSERT INTO transit_station (project_id, name, lat, lon, status, opened_ym) "
        "VALUES (?,?,?,?,?,?)",
        (pid, name, lat, lon, status, "201001" if status == "개통" else None))
    return conn.execute("SELECT id FROM transit_station WHERE name=?",
                        (name,)).fetchone()[0]


def link(conn, complex_id, station_id, meters):
    conn.execute("INSERT INTO station_distance (complex_id, station_id, meters, "
                 "method) VALUES (?,?,?,'직선')", (complex_id, station_id, meters))


class TestBandTable:
    def test_상한이_없는_밴드가_맨_뒤에_온다(self, table):
        names = [b.name for b in access.bands(table)]
        assert names[-1] == "2km 밖"

    def test_밴드표가_없으면_값을_지어내지_않는다(self, db, tmp_path):
        access.bands.cache_clear()
        with get_conn(db) as conn:
            cid = add_complex(conn, "가", lat=BASE_LAT, lon=BASE_LON)
            f = access.drift(conn, cid, horizon_years=5,
                             rules_path=str(tmp_path / "없는파일.csv"))
        access.bands.cache_clear()
        assert f.status is Status.DATA_MISSING
        assert f.value is None


class TestDrift:
    def test_역에_가까우면_플러스_멀면_마이너스(self, db, table):
        with get_conn(db) as conn:
            near = add_complex(conn, "가까운", lat=BASE_LAT, lon=BASE_LON)
            far = add_complex(conn, "먼", lat=BASE_LAT, lon=BASE_LON)
            sid = add_station(conn, "역", status="운영중")
            link(conn, near, sid, 300)
            link(conn, far, sid, 1800)

            a = access.drift(conn, near, horizon_years=5, rules_path=table)
            b = access.drift(conn, far, horizon_years=5, rules_path=table)

        assert a.value == pytest.approx(0.005)     # 0.001 × 5년
        assert b.value == pytest.approx(-0.005)
        assert a.value > b.value

    def test_투자기간이_길수록_누적_폭이_커진다(self, db, table):
        with get_conn(db) as conn:
            cid = add_complex(conn, "가", lat=BASE_LAT, lon=BASE_LON)
            sid = add_station(conn, "역", status="운영중")
            link(conn, cid, sid, 200)
            short = access.drift(conn, cid, horizon_years=2, rules_path=table)
            long = access.drift(conn, cid, horizon_years=5, rules_path=table)
        assert long.value > short.value
        assert long.value == pytest.approx(short.value * 2.5)

    def test_미개통_역은_세지_않는다(self, db, table):
        """착공·계획 중인 역 옆이라고 가점을 주지 않는다.

        개통 효과가 0 으로 측정됐기 때문이다. 이 단지는 200m 앞에 착공한 역이
        있지만, 다니는 역이 없으므로 '2km 밖' 으로 취급돼야 한다.
        """
        with get_conn(db) as conn:
            cid = add_complex(conn, "가", lat=BASE_LAT, lon=BASE_LON)
            planned = add_station(conn, "미개통역", status="착공")
            link(conn, cid, planned, 200)
            f = access.drift(conn, cid, horizon_years=5, rules_path=table)
        assert f.detail["밴드"] == "2km 밖"
        assert f.value == pytest.approx(-0.010)

    def test_개통한_역은_센다(self, db, table):
        with get_conn(db) as conn:
            cid = add_complex(conn, "가", lat=BASE_LAT, lon=BASE_LON)
            sid = add_station(conn, "개통역", status="개통")
            link(conn, cid, sid, 200)
            f = access.drift(conn, cid, horizon_years=5, rules_path=table)
        assert f.detail["밴드"] == "~500m"
        assert f.value > 0


class TestUnknownIsNotFar:
    def test_좌표가_없으면_최하위가_아니라_모른다(self, db, table):
        """데이터가 없다는 이유로 나쁘게 평가하지 않는다."""
        with get_conn(db) as conn:
            cid = add_complex(conn, "좌표없음")          # lat 를 안 넣는다
            f = access.drift(conn, cid, horizon_years=5, rules_path=table)
        assert f.status is Status.DATA_MISSING
        assert f.value is None
        assert "좌표" in f.detail["사유"]

    def test_좌표는_있고_역이_멀면_값이_나온다(self, db, table):
        with get_conn(db) as conn:
            cid = add_complex(conn, "외딴", lat=BASE_LAT, lon=BASE_LON)
            f = access.drift(conn, cid, horizon_years=5, rules_path=table)
        assert f.status is Status.OK
        assert f.detail["밴드"] == "2km 밖"


class TestConfidence:
    def test_얇은_밴드는_신뢰도를_깎는다(self, db, table):
        with get_conn(db) as conn:
            thick = add_complex(conn, "두꺼운", lat=BASE_LAT, lon=BASE_LON)
            thin = add_complex(conn, "얇은", lat=BASE_LAT, lon=BASE_LON)
            sid = add_station(conn, "역", status="운영중")
            link(conn, thick, sid, 300)      # 단지 700개 밴드
            link(conn, thin, sid, 1500)      # 단지 90개 밴드
            a = access.drift(conn, thick, horizon_years=5, rules_path=table)
            b = access.drift(conn, thin, horizon_years=5, rules_path=table)
        assert a.confidence > b.confidence


class TestTrace:
    def test_근거에_측정_표본과_한계가_남는다(self, db, table):
        with get_conn(db) as conn:
            cid = add_complex(conn, "가", lat=BASE_LAT, lon=BASE_LON)
            sid = add_station(conn, "역", status="운영중")
            link(conn, cid, sid, 300)
            f = access.drift(conn, cid, horizon_years=5, rules_path=table)
        assert f.calc is not None
        assert f.calc.evidence, "근거 없이 값을 만들면 안 된다"
        inter = f.calc.intermediates
        assert "측정 표본" in inter
        # 수준을 알파로 세지 않는다는 사실이 근거에 남아 있어야 한다.
        assert "이미 값에 있" in inter["뜻"]
        assert "보장은 없" in inter["주의"]
