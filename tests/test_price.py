"""대표가격 엔진 테스트 (PHASE 2).

요구사항 2가 든 예시를 그대로 시험한다:

    최근 84㎡ 정상거래  10.8  11.0  11.1  11.2  11.3  14.0 (억)
    → 14억 하나 때문에 현재가격을 14억으로 잡으면 안 된다.
"""
import pytest

from apt_engine import units
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.price import outlier, representative, snapshot
from apt_engine.repo import apt as repo


def trade(eok, ymd="20260601", floor=7, deal_type="중개거래", cancel=0):
    return {"deal_amount": int(units.from_eok(eok)), "deal_ymd": ymd,
            "floor": floor, "deal_type": deal_type, "cancel_yn": cancel}


def rent(eok, ymd="20260601", floor=7, monthly=0, renewal=None):
    return {"deposit": int(units.from_eok(eok)), "contract_ymd": ymd, "floor": floor,
            "monthly_rent": int(units.from_manwon(monthly)), "cancel_yn": 0,
            "use_renewal_right": renewal}


# ── 요구사항 2의 예시 ──────────────────────────────────────────────────

class TestRequirementExample:
    ROWS = [trade(v) for v in (10.8, 11.0, 11.1, 11.2, 11.3, 14.0)]

    def test_단일_신고가가_현재가격이_되지_않는다(self):
        snap = snapshot.build_price(self.ROWS, as_of_ym="202606")
        assert snap.value < units.from_eok(12), f"14억이 대표가격이 됐다: {snap.value}"
        assert snap.value == units.from_eok(11.1)   # 5건의 중앙값

    def test_신고가는_이상치로_빠지고_사유가_남는다(self):
        snap = snapshot.build_price(self.ROWS, as_of_ym="202606")
        assert snap.exclusions.get(outlier.OUTLIER_HIGH.code) == 1
        assert snap.sample_n == 5

    def test_계산근거에_제외사유와_분포가_들어간다(self):
        snap = snapshot.build_price(self.ROWS, as_of_ym="202606")
        text = snap.calc.explain()
        assert "중앙값" in text
        assert "국토교통부" in text
        assert snap.calc.grade == "CONFIRMED"


# ── 정상거래 필터 ──────────────────────────────────────────────────────

class TestHardExclusions:
    """어떤 경우에도 시세가 아닌 것 — 표본이 0이 되더라도 되살리지 않는다."""

    def test_취소거래는_제외된다(self):
        rows = [trade(11.0), trade(11.1), trade(20.0, cancel=1)]
        r = outlier.filter_normal(rows)
        assert len(r.kept) == 2
        assert r.exclusion_counts[outlier.CANCELLED.code] == 1

    def test_직거래는_제외된다(self):
        rows = [trade(11.0), trade(11.1), trade(6.0, deal_type="직거래")]
        r = outlier.filter_normal(rows)
        assert len(r.kept) == 2
        assert r.exclusion_counts[outlier.DIRECT_DEAL.code] == 1

    def test_hard_제외는_표본이_0이_되어도_되살리지_않는다(self):
        rows = [trade(11.0, cancel=1), trade(11.1, deal_type="직거래")]
        r = outlier.filter_normal(rows)
        assert r.kept == []
        assert r.relaxed == []

    def test_전세에서_월세_낀_계약은_제외된다(self):
        rows = [rent(3.5), rent(3.6), rent(1.0, monthly=80)]
        r = outlier.filter_normal(rows, jeonse=True, price_key="deposit")
        assert len(r.kept) == 2
        assert r.exclusion_counts[outlier.NOT_JEONSE.code] == 1


class TestSoftExclusions:
    """표본이 모자라면 되살리되, 되살렸다는 사실을 남긴다."""

    def test_1층_거래는_표본이_충분하면_빠진다(self):
        rows = [trade(11.0), trade(11.1), trade(11.2), trade(9.0, floor=1)]
        r = outlier.filter_normal(rows)
        assert len(r.kept) == 3
        assert r.exclusion_counts[outlier.SPECIAL_FLOOR.code] == 1
        assert r.relaxed == []

    def test_표본이_모자라면_1층을_되살린다(self):
        rows = [trade(11.0), trade(9.0, floor=1)]
        r = outlier.filter_normal(rows)
        assert len(r.kept) == 2
        assert outlier.SPECIAL_FLOOR.code in r.relaxed

    def test_되살린_사실이_계산근거에_남는다(self):
        # 조용히 포함하는 것과 다르다 — 화면에서 "1층 포함"이라고 말할 수 있어야 한다.
        rows = [trade(11.0), trade(9.0, floor=1)]
        snap = snapshot.build_price(rows, as_of_ym="202606")
        assert "표본부족 완화" in snap.calc.intermediates
        assert "1층" in str(snap.calc.intermediates["표본부족 완화"])

    def test_갱신요구권_갱신계약은_전세_시세에서_빠진다(self):
        rows = [rent(3.5), rent(3.6), rent(3.7), rent(2.8, renewal=1)]
        r = outlier.filter_normal(rows, jeonse=True, price_key="deposit")
        assert len(r.kept) == 3
        assert r.exclusion_counts[outlier.RENEWAL_RIGHT.code] == 1


class TestStatisticalOutliers:
    def test_표본이_적으면_이상치_판정을_하지_않는다(self):
        # 3~4건에서 중앙값 기준 이상치를 논하는 건 근거가 없다.
        rows = [trade(11.0), trade(11.1), trade(20.0)]
        r = outlier.filter_normal(rows)
        assert len(r.kept) == 3

    def test_지나치게_낮은_가격도_잡는다(self):
        rows = [trade(v) for v in (11.0, 11.1, 11.2, 11.15, 11.05, 3.0)]
        r = outlier.filter_normal(rows)
        assert r.exclusion_counts.get(outlier.OUTLIER_LOW.code) == 1

    def test_MAD가_0이어도_동작한다(self):
        # 절반 이상이 같은 값이면 MAD 가 0이 된다.
        values = [11.0] * 6 + [30.0]
        zs = outlier.modified_zscores(values)
        assert zs[-1] > outlier.Z_THRESHOLD

    def test_값이_전부_같으면_이상치가_없다(self):
        assert outlier.modified_zscores([11.0] * 5) == [0.0] * 5


# ── 대표가격 산출 ──────────────────────────────────────────────────────

class TestRepresentative:
    @pytest.mark.parametrize("n,expected", [
        (0, None), (1, "LOW"), (2, "LOW"), (3, "MEDIUM"), (9, "MEDIUM"),
        (10, "HIGH"), (50, "HIGH"),
    ])
    def test_신뢰도_기준은_요구사항_2_그대로(self, n, expected):
        assert representative.confidence_of(n) == expected

    def test_중앙값(self):
        assert representative.median_price([1, 2, 3]) == 2
        # 2.5 → 3. units.won_round 와 같은 절반올림 규칙을 쓴다(내장 round 는 2를 준다).
        assert representative.median_price([1, 2, 3, 4]) == 3

    def test_절사평균은_표본이_충분할_때만_쓴다(self):
        assert representative.pick_method(5, representative.TRIMMED_MEAN) == "median"
        assert representative.pick_method(10, representative.TRIMMED_MEAN) == "trimmed_mean"

    def test_절사평균이_양끝을_덜어낸다(self):
        values = list(range(1, 11))          # 1..10 → 1과 10을 덜어낸 2..9 의 평균 5.5 → 6
        assert representative.trimmed_mean(values) == 6

    def test_금액_반올림_규칙이_units_와_같다(self):
        # 모듈마다 반올림이 다르면 같은 표본에서 다른 대표가격이 나온다.
        assert representative.median_price([1, 2]) == units.won_round(1.5)

    def test_분포_요약(self):
        q = representative.quartiles([1, 2, 3, 4, 5])
        assert q["min"] == 1 and q["max"] == 5 and q["p50"] == 3

    def test_표본이_하나면_분포가_그_값_하나(self):
        q = representative.quartiles([7])
        assert set(q.values()) == {7}


# ── 집계창 ────────────────────────────────────────────────────────────

class TestWindow:
    def test_6개월_창은_양끝을_포함한다(self):
        assert snapshot.ym_window("202608", 6) == ("202603", "202608")

    def test_연도를_넘어간다(self):
        assert snapshot.ym_window("202602", 6) == ("202509", "202602")

    def test_1개월_창(self):
        assert snapshot.ym_window("202608", 1) == ("202608", "202608")

    def test_창_밖_거래는_제외된다(self):
        rows = [trade(11.0, "20260601"), trade(30.0, "20240101")]
        snap = snapshot.build_price(rows, as_of_ym="202606", window_months=6)
        assert snap.sample_n == 1
        assert snap.value == units.from_eok(11.0)

    @pytest.mark.parametrize("bad", ["2026", "20260", "abcdef"])
    def test_잘못된_기준월은_에러(self, bad):
        with pytest.raises(ValueError, match="YYYYMM"):
            snapshot.ym_window(bad, 6)


# ── 표본이 없을 때 ─────────────────────────────────────────────────────

class TestNoSamples:
    def test_정상거래가_0건이면_가격을_내지_않는다(self):
        # "확인 불가"와 "0원"은 완전히 다르다.
        snap = snapshot.build_price([trade(11.0, cancel=1)], as_of_ym="202606")
        assert snap.value is None
        assert snap.usable is False
        assert snap.confidence is None
        assert "0건" in snap.calc.formula

    def test_거래가_아예_없어도_에러가_아니다(self):
        snap = snapshot.build_price([], as_of_ym="202606")
        assert snap.value is None and snap.sample_n == 0


# ── 전세가율 ──────────────────────────────────────────────────────────

class TestJeonseRatio:
    def test_전세가율_계산(self):
        price = snapshot.build_price([trade(11.0), trade(11.2), trade(11.1)],
                                     as_of_ym="202606")
        jeonse = snapshot.build_jeonse([rent(6.6), rent(6.5), rent(6.7)],
                                       as_of_ym="202606")
        calc = snapshot.jeonse_ratio(price, jeonse)
        assert calc.value == pytest.approx(6.6 / 11.1, abs=0.001)
        assert calc.unit == "ratio"

    def test_전세가율_근거에_양쪽_표본이_들어간다(self):
        price = snapshot.build_price([trade(11.0), trade(11.2), trade(11.1)], as_of_ym="202606")
        jeonse = snapshot.build_jeonse([rent(6.6), rent(6.5), rent(6.7)], as_of_ym="202606")
        calc = snapshot.jeonse_ratio(price, jeonse)
        assert "대표매매가" in calc.inputs and "대표전세가" in calc.inputs
        assert calc.grade == "CONFIRMED"

    def test_한쪽_표본이_없으면_전세가율을_내지_않는다(self):
        price = snapshot.build_price([trade(11.0)], as_of_ym="202606")
        empty = snapshot.build_jeonse([], as_of_ym="202606")
        assert snapshot.jeonse_ratio(price, empty) is None

    def test_기준월이_다르면_거부한다(self):
        # 다른 시점의 매매가와 전세가를 섞으면 전세가율이 뜻을 잃는다.
        price = snapshot.build_price([trade(11.0)], as_of_ym="202606")
        jeonse = snapshot.build_jeonse([rent(6.6, "20260501")], as_of_ym="202605")
        with pytest.raises(ValueError, match="기준월이 다른"):
            snapshot.jeonse_ratio(price, jeonse)


# ── 저장 ──────────────────────────────────────────────────────────────

class TestPersistence:
    @pytest.fixture
    def db(self, tmp_db):
        mig.migrate(tmp_db)
        with get_conn(tmp_db) as conn:
            repo.sync_regions(conn)
            repo.upsert_complexes(conn, [{
                "kapt_code": "A1", "name": "테스트단지", "name_norm": "테스트단지",
                "lawd_cd": "28237", "emd_name": "산곡동", "apt_households": 1200,
                "approval_year": 1990,
            }])
        return tmp_db

    def test_스냅샷이_계산근거와_함께_저장된다(self, db):
        from apt_engine.trace import Calc
        snap = snapshot.build_price([trade(v) for v in (11.0, 11.1, 11.2)], as_of_ym="202606")
        with get_conn(db) as conn:
            cid = conn.execute("SELECT id FROM complex").fetchone()[0]
            sid = repo.save_price_snapshot(conn, complex_id=cid, area_band="84", snap=snap)
            row = conn.execute("SELECT * FROM price_snapshot WHERE id=?", (sid,)).fetchone()

        assert row["representative_price"] == units.from_eok(11.1)
        assert row["confidence"] == "MEDIUM"
        assert row["data_grade"] == "CONFIRMED"
        restored = Calc.from_json(row["calc_trace"])
        assert restored.value == row["representative_price"]

    def test_표본이_없으면_저장하지_않는다(self, db):
        snap = snapshot.build_price([], as_of_ym="202606")
        with get_conn(db) as conn:
            cid = conn.execute("SELECT id FROM complex").fetchone()[0]
            assert repo.save_price_snapshot(conn, complex_id=cid, area_band="84",
                                            snap=snap) is None
            assert conn.execute("SELECT COUNT(*) FROM price_snapshot").fetchone()[0] == 0

    def test_다시_계산하면_덮어쓴다(self, db):
        with get_conn(db) as conn:
            cid = conn.execute("SELECT id FROM complex").fetchone()[0]
            first = snapshot.build_price([trade(v) for v in (11.0, 11.1, 11.2)],
                                          as_of_ym="202606")
            repo.save_price_snapshot(conn, complex_id=cid, area_band="84", snap=first)
            second = snapshot.build_price([trade(v) for v in (12.0, 12.1, 12.2)],
                                           as_of_ym="202606")
            repo.save_price_snapshot(conn, complex_id=cid, area_band="84", snap=second)
            rows = conn.execute("SELECT representative_price FROM price_snapshot").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == units.from_eok(12.1)

    def test_전세가율까지_저장된다(self, db):
        price = snapshot.build_price([trade(v) for v in (11.0, 11.1, 11.2)], as_of_ym="202606")
        jeonse = snapshot.build_jeonse([rent(v) for v in (6.5, 6.6, 6.7)], as_of_ym="202606")
        ratio = snapshot.jeonse_ratio(price, jeonse)
        with get_conn(db) as conn:
            cid = conn.execute("SELECT id FROM complex").fetchone()[0]
            psid = repo.save_price_snapshot(conn, complex_id=cid, area_band="84", snap=price)
            repo.save_jeonse_snapshot(conn, complex_id=cid, area_band="84", snap=jeonse,
                                      price_snapshot_id=psid, ratio_calc=ratio)
            row = conn.execute("SELECT * FROM jeonse_snapshot").fetchone()
        assert row["jeonse_ratio"] == pytest.approx(6.6 / 11.1, abs=0.001)
        assert row["price_snapshot_id"] == psid
        assert row["ratio_calc_trace"]


class TestSnapshotPipeline:
    def test_ingest_가_시계열_스냅샷을_만든다(self, tmp_db):
        from apt_engine import ingest

        mig.migrate(tmp_db)
        with get_conn(tmp_db) as conn:
            repo.sync_regions(conn)
            repo.upsert_complexes(conn, [{
                "kapt_code": "A1", "name": "테스트단지", "name_norm": "테스트단지",
                "lawd_cd": "28237", "apt_households": 1200,
            }])
            cid = conn.execute("SELECT id FROM complex").fetchone()[0]
            repo.insert_trades(conn, [{
                "complex_id": cid, "lawd_cd": "28237", "apt_name": "테스트단지",
                "exclusive_area_m2": 84.5, "area_band": "84",
                "deal_amount": int(units.from_eok(5.9 + i * 0.05)),
                "deal_ymd": f"202605{10 + i:02d}", "floor": 5 + i,
                "deal_type": "중개거래", "cancel_yn": 0,
            } for i in range(6)])

        s = ingest.build_snapshots(as_of_ym="202606", months=3, window_months=6,
                                   db_path=tmp_db, progress=lambda *a: None)
        assert s["pairs"] == 1
        # 거래가 202605 에 몰려 있어 202604 기준 6개월 창(202511~202604)에는 표본이 없다.
        # 표본이 없는 시점은 스냅샷을 만들지 않는다 — "확인 불가"와 "0원"은 다르다.
        assert s["price"] == 2
        with get_conn(tmp_db) as conn:
            yms = [r[0] for r in conn.execute(
                "SELECT as_of_ym FROM price_snapshot ORDER BY as_of_ym")]
        assert yms == ["202605", "202606"]
