"""촉매 테스트 (요구사항 5·6·19·21·55·62-8).

이 계층이 지키려는 선:
  1. 계획 단계 교통호재를 확정 호재처럼 쓰지 않는다
  2. "GTX 생기면 몇 % 오른다"를 만들지 않는다 — 상대 비율 변화만
  3. 근거 없는 촉매는 저장 자체가 안 된다
  4. 호재를 투자기간과 연결한다
"""
import sqlite3

import pytest

from apt_engine import geo, units
from apt_engine.catalyst import analogue as analogue_mod
from apt_engine.catalyst import assemble, supply as supply_mod, transit as transit_mod
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.repo import apt as repo
from apt_engine.repo import catalyst as cat_repo

TODAY = "2026-08-31"


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


def add_complex(conn, name, lawd="28237", *, lat=None, lon=None, households=1200):
    repo.upsert_complexes(conn, [{
        "kapt_code": f"K{name}", "name": name, "name_norm": name,
        "lawd_cd": lawd, "apt_households": households}])
    cid = conn.execute("SELECT id FROM complex WHERE kapt_code=?",
                       (f"K{name}",)).fetchone()[0]
    if lat is not None:
        cat_repo.set_coords(conn, cid, lat, lon)
    return cid


def add_station(conn, name="부평", *, project="GTX-B", status="착공",
                lat=37.4894, lon=126.7246, opened=None, expected=None,
                verified=TODAY, lawd="28237"):
    conn.execute("INSERT INTO transit_project (name, kind) VALUES (?, 'GTX') "
                 "ON CONFLICT(name) DO NOTHING", (project,))
    pid = conn.execute("SELECT id FROM transit_project WHERE name=?",
                       (project,)).fetchone()[0]
    conn.execute(
        "INSERT INTO transit_station (project_id, name, lawd_cd, lat, lon, status, "
        "status_date, expected_open_ym, opened_ym, last_verified) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pid, name, lawd, lat, lon, status, "2025-01-01", expected, opened, verified))
    return conn.execute("SELECT id FROM transit_station ORDER BY id DESC "
                        "LIMIT 1").fetchone()[0]


def add_snapshot(conn, cid, band, ym, price_eok):
    from apt_engine.trace import Calc
    calc = Calc(value=1, unit="원", formula="t")
    conn.execute(
        "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym, window_months, "
        "representative_price, method, sample_n, confidence, engine_version, "
        "data_grade, calc_trace) VALUES (?,?,?,6,?,'median',10,'HIGH',?,'CONFIRMED',?)",
        (cid, band, ym, int(units.from_eok(price_eok)), calc.engine_version,
         calc.to_json()))


# ── 거리 ──────────────────────────────────────────────────────────────

class TestGeo:
    def test_직선거리(self):
        # 서울시청 ↔ 강남역 대략 8~9km
        d = geo.haversine_m(37.5665, 126.9780, 37.4979, 127.0276)
        assert 8_000 < d < 10_000

    def test_같은_점은_0(self):
        assert geo.haversine_m(37.5, 127.0, 37.5, 127.0) == pytest.approx(0, abs=0.001)

    def test_도보시간은_직선보다_길게_잡는다(self):
        # 직선 500m 를 그대로 도보 7분이라고 하지 않는다.
        assert geo.rough_walk_minutes(500) > 500 / geo.WALK_M_PER_MIN


# ── 교통 단계 ─────────────────────────────────────────────────────────

class TestTransitStage:
    def test_개통으로_적으려면_개통월이_있어야_한다(self, db):
        # 요구사항 62-8 — 계획을 확정 호재로 만드는 걸 스키마가 막는다.
        with get_conn(db) as conn:
            conn.execute("INSERT INTO transit_project (name, kind) VALUES ('X','GTX')")
            pid = conn.execute("SELECT id FROM transit_project").fetchone()[0]
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO transit_station (project_id, name, status) "
                    "VALUES (?, '어딘가', '개통')", (pid,))

    def test_모르는_단계는_거부(self, db):
        with get_conn(db) as conn:
            conn.execute("INSERT INTO transit_project (name, kind) VALUES ('X','GTX')")
            pid = conn.execute("SELECT id FROM transit_project").fetchone()[0]
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO transit_station (project_id, name, status) "
                             "VALUES (?, '어딘가', '곧생김')", (pid,))

    def test_단계_비교(self):
        assert transit_mod.stage_at_least("착공", "기본계획") is True
        assert transit_mod.stage_at_least("계획", "착공") is False
        assert transit_mod.stage_at_least("개통", "개통") is True

    def test_착공_전은_실현신뢰도가_낮다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, "A", lat=37.4890, lon=126.7250)
            add_station(conn, status="계획", expected="203012")
            transit_mod.compute_distances(conn)
            st = transit_mod.nearby(conn, cid)[0]
        assert st.confidence == "LOW"
        assert st.opened is False


class TestHorizon:
    """요구사항 55 — 호재를 투자기간과 연결한다."""

    def _station(self, **kw):
        base = dict(station_id=1, name="부평", project_name="GTX-B", kind="GTX",
                    status="착공", status_date="2025-01-01", expected_open_ym=None,
                    opened_ym=None, meters=500.0, method="직선", verified=True)
        base.update(kw)
        return transit_mod.NearbyStation(**base)

    def test_투자기간_안이면_True(self):
        st = self._station(expected_open_ym="203012")
        within, note = st.horizon_label(as_of=TODAY, years=5)
        assert within is True and "안" in note

    def test_투자기간_밖이면_False_이고_기대감만이라고_말한다(self):
        st = self._station(expected_open_ym="203512")
        within, note = st.horizon_label(as_of=TODAY, years=5)
        assert within is False
        assert "기대감만" in note

    def test_개통_시점을_모르면_확인_불가(self):
        within, note = self._station().horizon_label(as_of=TODAY, years=5)
        assert within is None and "미상" in note

    def test_이미_개통했으면_반영됐을_가능성을_말한다(self):
        st = self._station(status="개통", opened_ym="202403")
        within, note = st.horizon_label(as_of=TODAY, years=5)
        assert within is True and "이미 개통" in note

    def test_계산근거가_사실과_추정을_갈라_놓는다(self):
        st = self._station(expected_open_ym="203012")
        calc = transit_mod.to_calc(st, as_of=TODAY, years=5)
        assert "확정된 사실" in calc.intermediates
        assert "추정" in calc.intermediates
        assert calc.intermediates["확정된 사실"]["현재 단계"] == "착공"
        assert calc.intermediates["추정"]["개통 예정"] == "203012"
        assert calc.grade == "ESTIMATED"      # 개통 전은 전부 추정

    def test_개통한_역만_확정_등급(self):
        st = self._station(status="개통", opened_ym="202403")
        assert transit_mod.to_calc(st, as_of=TODAY, years=5).grade == "CONFIRMED"


# ── 거리 계산 ─────────────────────────────────────────────────────────

class TestDistances:
    def test_좌표가_없으면_거리를_만들지_않는다(self, db):
        with get_conn(db) as conn:
            add_complex(conn, "좌표없음")          # lat/lon NULL
            add_station(conn)
            assert transit_mod.compute_distances(conn) == 0

    def test_반경_밖은_저장하지_않는다(self, db):
        with get_conn(db) as conn:
            # 부평역에서 약 20km 떨어진 좌표
            add_complex(conn, "멀리", lat=37.6500, lon=126.9000)
            add_station(conn)
            assert transit_mod.compute_distances(conn) == 0

    def test_거리와_방식이_함께_저장된다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, "가까움", lat=37.4890, lon=126.7250)
            add_station(conn)
            transit_mod.compute_distances(conn)
            st = transit_mod.nearby(conn, cid)[0]
        assert st.meters < 500
        assert st.method == "직선"     # 도보거리라고 부르지 않는다


# ── 선행사례 ──────────────────────────────────────────────────────────

class TestAnalogue:
    def _setup(self, conn, *, near_before, near_after, far_before, far_after):
        sid = add_station(conn, name="동탄", project="GTX-A", status="개통",
                          opened="202403", lat=37.2001, lon=127.0985, lawd="28237")
        near_ids, far_ids = [], []
        for i in range(3):
            # 역 바로 옆
            cid = add_complex(conn, f"역세권{i}", lat=37.2005, lon=127.0990)
            near_ids.append(cid)
            add_snapshot(conn, cid, "84", "202303", near_before)
            add_snapshot(conn, cid, "84", "202503", near_after)
            # 같은 시군구지만 먼 곳
            fid = add_complex(conn, f"비역세권{i}", lat=37.2400, lon=127.1500)
            far_ids.append(fid)
            add_snapshot(conn, fid, "84", "202303", far_before)
            add_snapshot(conn, fid, "84", "202503", far_after)
        transit_mod.compute_distances(conn)
        return sid

    def test_상대_비율_변화를_계산한다(self, db):
        with get_conn(db) as conn:
            self._setup(conn, near_before=10.0, near_after=13.0,
                        far_before=10.0, far_after=11.0)
            station = cat_repo.opened_stations(conn)[0]
            a = analogue_mod.build(conn, station, area_band="84")

        assert a is not None
        assert a.ratio_before == pytest.approx(1.0)
        assert a.ratio_after == pytest.approx(13.0 / 11.0, abs=0.001)
        assert a.delta > 0

    def test_시장_전체_상승은_상쇄된다(self, db):
        # 역세권도 비역세권도 똑같이 30% 올랐으면 그 역의 몫은 0이다.
        with get_conn(db) as conn:
            self._setup(conn, near_before=10.0, near_after=13.0,
                        far_before=10.0, far_after=13.0)
            station = cat_repo.opened_stations(conn)[0]
            a = analogue_mod.build(conn, station, area_band="84")
        assert a.delta == pytest.approx(0.0, abs=0.001)

    def test_절대_상승률을_만들지_않는다(self, db):
        with get_conn(db) as conn:
            self._setup(conn, near_before=10.0, near_after=13.0,
                        far_before=10.0, far_after=11.0)
            station = cat_repo.opened_stations(conn)[0]
            a = analogue_mod.build(conn, station, area_band="84")
        assert a.calc.unit == "%p"
        assert "절대 상승률이 아니다" in a.calc.intermediates["주의"]

    def test_표본이_모자라면_만들지_않는다(self, db):
        with get_conn(db) as conn:
            sid = add_station(conn, name="동탄", project="GTX-A", status="개통",
                              opened="202403", lat=37.2001, lon=127.0985)
            cid = add_complex(conn, "하나뿐", lat=37.2005, lon=127.0990)
            add_snapshot(conn, cid, "84", "202303", 10.0)
            transit_mod.compute_distances(conn)
            station = cat_repo.opened_stations(conn)[0]
            assert analogue_mod.build(conn, station, area_band="84") is None

    def test_개통하지_않은_역으로는_만들지_않는다(self, db):
        with get_conn(db) as conn:
            add_station(conn, status="착공", expected="203012")
            rows = conn.execute("SELECT s.*, p.name AS project_name FROM transit_station s "
                                "JOIN transit_project p ON p.id=s.project_id").fetchall()
            assert analogue_mod.build(conn, rows[0], area_band="84") is None

    def test_여러_사례는_범위로_요약한다(self, db):
        from apt_engine.catalyst.analogue import Analogue
        from apt_engine.trace import Calc

        def fake(delta):
            return Analogue(1, "역", "GTX-A", "202403", "84", 800, "202303", "202503",
                            3, 3, 1.0, 1.0 + delta, Calc(value=delta, unit="%p",
                                                          formula="t"))
        summary = analogue_mod.summarize([fake(0.05), fake(0.09), fake(0.12)])
        assert "범위" in summary.intermediates
        assert summary.grade == "ESTIMATED"     # 미개통 노선에 쓰는 순간 추정

    def test_사례가_없으면_요약하지_않는다(self):
        assert analogue_mod.summarize([]) is None

    def test_ym_이동(self):
        assert analogue_mod.shift_ym("202403", -12) == "202303"
        assert analogue_mod.shift_ym("202403", 12) == "202503"
        assert analogue_mod.shift_ym("202401", -1) == "202312"


# ── 공급 ──────────────────────────────────────────────────────────────

class TestSupply:
    def _add(self, conn, name, households, ym, stage="착공", lat=None, lon=None):
        conn.execute(
            "INSERT INTO supply_plan (lawd_cd, complex_name, households, move_in_ym, "
            "stage, lat, lon, last_verified) VALUES ('28237',?,?,?,?,?,?,?)",
            (name, households, ym, stage, lat, lon, TODAY))

    def test_데이터가_없으면_0세대가_아니라_확인_불가(self, db):
        with get_conn(db) as conn:
            calc = supply_mod.analyze(conn, lawd_cd="28237", as_of_ym="202608")
        assert calc.value is None
        assert "확인 불가" in calc.intermediates["주의"]

    def test_1_2년과_3_5년을_나눈다(self, db):
        with get_conn(db) as conn:
            self._add(conn, "가까운단지", 1000, "202703")
            self._add(conn, "먼단지", 2000, "203003")
            calc = supply_mod.analyze(conn, lawd_cd="28237", as_of_ym="202608")
        assert calc.value == 1000
        assert "2,000세대" in calc.intermediates["3~5년"]

    def test_좌표가_없으면_시군구_전체로_세고_그렇게_밝힌다(self, db):
        with get_conn(db) as conn:
            self._add(conn, "단지", 1000, "202703")
            calc = supply_mod.analyze(conn, lawd_cd="28237", as_of_ym="202608")
        assert "시군구 전체" in calc.intermediates["집계 기준"]

    def test_반경으로_거르고_기준을_밝힌다(self, db):
        with get_conn(db) as conn:
            self._add(conn, "옆단지", 1000, "202703", lat=37.4895, lon=126.7250)
            self._add(conn, "먼단지", 5000, "202703", lat=37.6500, lon=126.9000)
            calc = supply_mod.analyze(conn, lawd_cd="28237", as_of_ym="202608",
                                      lat=37.4890, lon=126.7246, radius_m=3000)
        assert calc.value == 1000
        assert "3,000m" in calc.intermediates["집계 기준"]

    def test_좌표_없는_공급은_제외하되_그_사실을_알린다(self, db):
        with get_conn(db) as conn:
            self._add(conn, "좌표없음", 1000, "202703")
            calc = supply_mod.analyze(conn, lawd_cd="28237", as_of_ym="202608",
                                      lat=37.4890, lon=126.7246, radius_m=3000)
        assert "좌표 없는 공급 1건 제외" in calc.intermediates["집계 기준"]
        assert "더 클 수 있음" in calc.intermediates["집계 기준"]

    def test_입주완료는_향후_공급이_아니다(self, db):
        with get_conn(db) as conn:
            self._add(conn, "이미입주", 1000, "202703", stage="입주완료")
            calc = supply_mod.analyze(conn, lawd_cd="28237", as_of_ym="202608")
        assert calc.value == 0

    def test_계획_단계는_가중치를_낮춘다(self, db):
        with get_conn(db) as conn:
            self._add(conn, "계획단지", 1000, "202703", stage="계획")
            calc = supply_mod.analyze(conn, lawd_cd="28237", as_of_ym="202608")
        assert "단계보정" in calc.intermediates["1~2년"]

    def test_공급은_항상_추정_등급(self, db):
        with get_conn(db) as conn:
            self._add(conn, "단지", 1000, "202703")
            calc = supply_mod.analyze(conn, lawd_cd="28237", as_of_ym="202608")
        assert calc.grade == "ESTIMATED"     # 분양·착공은 일정이 밀린다


# ── 촉매 조립 ─────────────────────────────────────────────────────────

class TestAssemble:
    def test_근거_없는_촉매는_저장이_안_된다(self, db):
        # 요구사항 5 — 스키마가 막는다.
        with get_conn(db) as conn:
            cid = add_complex(conn, "A")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO future_catalyst (complex_id, kind, label, direction, "
                    "evidence_json, confidence, data_grade, engine_version, calc_trace) "
                    "VALUES (?,'교통','X','상승','{}','HIGH','ESTIMATED','0.7.0','{}')",
                    (cid,))

    def test_이미_개통한_역은_중립으로_둔다(self, db):
        # 가격에 이미 반영됐을 가능성이 높다.
        with get_conn(db) as conn:
            cid = add_complex(conn, "A", lat=37.4890, lon=126.7250)
            add_station(conn, status="개통", opened="202403")
            transit_mod.compute_distances(conn)
            items = assemble.from_transit(conn, cid, as_of=TODAY, years=5)
        assert items[0].direction == "중립"

    def test_미개통_역은_상승_방향(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, "A", lat=37.4890, lon=126.7250)
            add_station(conn, status="착공", expected="203012")
            transit_mod.compute_distances(conn)
            items = assemble.from_transit(conn, cid, as_of=TODAY, years=5)
        assert items[0].direction == "상승"
        assert items[0].within_horizon is True

    def test_촉매가_없으면_확인_불가라고_말한다(self):
        summary = assemble.summarize([], years=5)
        assert summary.value is None
        assert "확인 불가" in summary.intermediates["주의"]

    def test_요약이_기간_안팎을_가른다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, "A", lat=37.4890, lon=126.7250)
            add_station(conn, name="가까운역", status="착공", expected="202912")
            add_station(conn, name="먼미래역", status="계획", expected="204012",
                        lat=37.4891, lon=126.7251)
            transit_mod.compute_distances(conn)
            items = assemble.from_transit(conn, cid, as_of=TODAY, years=5)
            summary = assemble.summarize(items, years=5)
        assert summary.value == 1
        assert len(summary.intermediates["기간 밖"]) == 1

    def test_촉매_수가_상승폭이_아니라고_명시한다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, "A", lat=37.4890, lon=126.7250)
            add_station(conn, status="착공", expected="202912")
            transit_mod.compute_distances(conn)
            items = assemble.from_transit(conn, cid, as_of=TODAY, years=5)
        assert "곧 상승폭은 아니다" in assemble.summarize(items, years=5).intermediates["주의"]


# ── 수기 입력 ─────────────────────────────────────────────────────────

class TestImport:
    def test_서식을_그대로_import_해도_에러가_아니다(self, db, tmp_path):
        t = cat_repo.write_transit_template(tmp_path / "t.csv")
        s = cat_repo.write_supply_template(tmp_path / "s.csv")
        with get_conn(db) as conn:
            assert cat_repo.import_transit(conn, t)["stations"] == 0
            assert cat_repo.import_supply(conn, s)["inserted"] == 0

    def test_개통인데_개통월이_없으면_거부(self, db, tmp_path):
        p = tmp_path / "t.csv"
        p.write_text(
            "project_name,kind,station_name,lawd_cd,lat,lon,status,status_date,"
            "expected_open_ym,opened_ym,source_name,source_url,last_verified,note\n"
            "GTX-A,GTX,동탄,41597,37.2,127.1,개통,2024-03-30,,,국토부,https://x,2026-08-31,\n",
            encoding="utf-8")
        with get_conn(db) as conn:
            with pytest.raises(cat_repo.CatalystImportError, match="opened_ym"):
                cat_repo.import_transit(conn, p)

    def test_정상_입력(self, db, tmp_path):
        p = tmp_path / "t.csv"
        p.write_text(
            "project_name,kind,station_name,lawd_cd,lat,lon,status,status_date,"
            "expected_open_ym,opened_ym,source_name,source_url,last_verified,note\n"
            "GTX-A,GTX,동탄,41597,37.2,127.1,개통,2024-03-30,,202403,국토부,https://x,2026-08-31,\n"
            "GTX-B,GTX,부평,28237,37.49,126.72,착공,2025-01-01,203012,,국토부,https://x,,\n",
            encoding="utf-8")
        with get_conn(db) as conn:
            s = cat_repo.import_transit(conn, p)
            opened = cat_repo.opened_stations(conn)
        assert s == {"projects": 2, "stations": 2, "unverified": 1}
        assert len(opened) == 1 and opened[0]["name"] == "동탄"
