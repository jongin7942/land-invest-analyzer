"""Feature 계층 테스트 (지시서 §8·§13~§16·§39·§40·§50·§71).

이 계층이 지키려는 선:
  1. 값과 신뢰도를 합치지 않는다 — 표본 1건과 10건이 같은 무게일 수 없다
  2. 못 구한 값을 0 으로 만들지 않는다
  3. 상승률을 매수 신호로 바꾸지 않는다 (§39 Past Winner != Current Winner)
  4. 거래량을 BUY 신호로 바꾸지 않는다 (§15)
  5. 전세를 Upside 로 쓰지 않는다 (§14)
  6. 공급은 절대물량이 아니라 stock 대비 비율이다 (§13)
  7. 컷오프 이후 데이터를 쓰지 않는다
  8. feature 그룹을 이름으로 끌 수 있다 (§71 Ablation)
"""
import pytest

from apt_engine import units
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.features import assemble, flow, jeonse, momentum, regime, supply
from apt_engine.features.base import (Feature, FeatureSet, Status, combine,
                                      freshness_confidence, sample_confidence)
from apt_engine.repo import apt as repo

LAWD = "28237"
BAND = "84"
AS_OF = cutoff_mod.AsOf("2026-01-01")      # 관측가능 = 2025-12-02 → 완료월 202511


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


def add_complex(conn, name="단지", kapt="K1", *, lawd=LAWD, lat=None, lon=None,
                households=1000):
    repo.upsert_complexes(conn, [{
        "kapt_code": kapt, "name": name, "name_norm": name, "lawd_cd": lawd,
        "apt_households": households, "lat": lat, "lon": lon}])
    return conn.execute("SELECT id FROM complex WHERE kapt_code=?",
                        (kapt,)).fetchone()[0]


def add_price(conn, cid, ym, eok, *, n=10, band=BAND):
    conn.execute(
        "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym, window_months, "
        " representative_price, method, sample_n, confidence, data_grade, "
        " engine_version, calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, band, ym, 6, int(units.from_eok(eok)), "median", n, "HIGH",
         "CONFIRMED", "0.13.0", "{}"))


def add_jeonse(conn, cid, ym, eok, *, n=10, band=BAND):
    conn.execute(
        "INSERT INTO jeonse_snapshot (complex_id, area_band, as_of_ym, window_months, "
        " representative_deposit, method, sample_n, confidence, data_grade, "
        " engine_version, calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, band, ym, 6, int(units.from_eok(eok)), "median", n, "HIGH",
         "CONFIRMED", "0.13.0", "{}"))


def add_trade(conn, cid, ymd, eok, *, floor=10, band=BAND, cancelled=0):
    conn.execute(
        "INSERT INTO trade (complex_id, lawd_cd, apt_name, exclusive_area_m2, "
        " area_band, deal_amount, deal_ymd, floor, cancel_yn) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, LAWD, "단지", 84.0, band, int(units.from_eok(eok)), ymd, floor,
         cancelled))


def monthly(conn, cid, start_ym, values, *, n=10):
    """연속된 달에 대표가격을 깐다."""
    ym = start_ym
    for v in values:
        add_price(conn, cid, ym, v, n=n)
        ym = momentum._shift(ym, 1)


# ── base 계약 ──────────────────────────────────────────────────────────

class TestFeatureContract:
    def test_못_구한_값은_0_이_아니라_None(self):
        f = Feature.missing("x", "표본 없음")
        assert f.value is None and not f.known and not f.usable
        assert f.status is Status.DATA_MISSING

    def test_신뢰도가_낮으면_값이_있어도_쓰지_않는다(self):
        f = Feature("x", 1.0, "", 0.0, Status.OK).with_confidence(0.1)
        assert f.known and not f.usable
        assert f.status is Status.LOW_CONFIDENCE

    def test_표본이_많을수록_신뢰도가_높다(self):
        assert sample_confidence(1) < sample_confidence(5) < sample_confidence(20)
        assert sample_confidence(0) == 0.0
        assert sample_confidence(100) == 1.0

    def test_오래될수록_신뢰도가_낮다(self):
        assert freshness_confidence(0) > freshness_confidence(6) > freshness_confidence(24)

    def test_신뢰도_합성은_가장_약한_것에_끌려간다(self):
        """산술평균이면 '표본1건+최신' 이 '표본10건+6개월전' 과 같아진다."""
        assert combine(1.0, 0.1) < (1.0 + 0.1) / 2
        assert combine(0.9, 0.0) == 0.0

    def test_feature_그룹을_이름으로_끌_수_있다(self):
        fs = FeatureSet(1, BAND, "2026-01-01", {
            "a": Feature("a", 1.0, "", 0.9, Status.OK),
            "b": Feature("b", 2.0, "", 0.9, Status.OK)})
        assert set(fs.without("a").items) == {"b"}
        assert set(fs.items) == {"a", "b"}          # 원본은 그대로

    def test_coverage_는_쓸_수_있는_비율이다(self):
        fs = FeatureSet(1, BAND, "2026-01-01", {
            "a": Feature("a", 1.0, "", 0.9, Status.OK),
            "b": Feature.missing("b", "없음")})
        assert fs.coverage == 0.5
        assert fs.missing_keys == ["b"]


# ── §16·§39·§40 모멘텀 ─────────────────────────────────────────────────

class TestMomentum:
    def test_상승률을_계산한다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            monthly(conn, cid, "202411", [5.0] * 12 + [6.0])   # 12개월 뒤 +20%
            series = momentum.load_series(conn, cid, BAND, as_of=AS_OF)
            got = momentum.change(series, 12)
        assert got.known
        assert got.value == pytest.approx(0.2, abs=1e-6)

    def test_시작점이_없으면_0_이_아니라_확인_불가(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            monthly(conn, cid, "202509", [5.0, 5.0, 5.0])
            series = momentum.load_series(conn, cid, BAND, as_of=AS_OF)
            got = momentum.change(series, 12)
        assert not got.known
        assert "없습니다" in got.detail["사유"]

    def test_표본이_적으면_신뢰도가_낮다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            monthly(conn, cid, "202411", [5.0] * 13, n=1)
            series = momentum.load_series(conn, cid, BAND, as_of=AS_OF)
            got = momentum.change(series, 12)
        assert got.known and not got.usable

    def test_가속도는_최근과_직전을_비교한다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            # 앞 3개월 보합, 뒤 3개월 급등
            monthly(conn, cid, "202505", [5.0, 5.0, 5.0, 5.0, 5.5, 6.0, 6.6])
            series = momentum.load_series(conn, cid, BAND, as_of=AS_OF)
            got = momentum.acceleration(series)
        assert got.known and got.value > 0

    def test_많이_오른_뒤_발견하면_discovery_lag_이_커진다(self, db):
        """§40 — 이미 오른 뒤 발견한 것은 성공이 아니다."""
        with get_conn(db) as conn:
            hot = add_complex(conn, "급등", "K1")
            calm = add_complex(conn, "보합", "K2")
            monthly(conn, hot, "202505", [5.0, 5.0, 5.0, 5.0, 6.0, 6.5, 7.0])
            monthly(conn, calm, "202505", [5.0] * 7)
            hot_lag = momentum.discovery_lag(
                momentum.load_series(conn, hot, BAND, as_of=AS_OF))
            calm_lag = momentum.discovery_lag(
                momentum.load_series(conn, calm, BAND, as_of=AS_OF))
        assert hot_lag.value > calm_lag.value
        assert calm_lag.value == 0.0

    def test_상승률을_매수점수로_바꾸지_않는다(self, db):
        """§39 — feature 이름과 근거 어디에도 'buy' 나 점수가 없어야 한다."""
        with get_conn(db) as conn:
            cid = add_complex(conn)
            monthly(conn, cid, "202411", [5.0] * 12 + [6.0])
            got = momentum.change(
                momentum.load_series(conn, cid, BAND, as_of=AS_OF), 12)
        assert "매수 신호가 아니" in str(got.calc.intermediates)


# ── §8 Regime ──────────────────────────────────────────────────────────

class TestRegime:
    @pytest.mark.parametrize("p12, p3, vol, expected", [
        (-0.10, -0.03, 0.8, "침체"),
        (-0.10, 0.04, 1.4, "바닥형성"),
        (0.00, 0.03, 1.4, "회복초기"),
        (0.05, 0.02, 1.0, "상승초기"),
        (0.12, 0.03, 1.1, "상승확산"),
        (0.25, 0.05, 1.2, "과열"),
        (0.25, -0.04, 0.6, "하락전환"),
    ])
    def test_7국면이_모두_판정된다(self, p12, p3, vol, expected):
        name, _ = regime.classify(price_12m=p12, price_3m=p3, volume_ratio=vol)
        assert name == expected

    def test_장기_변화율이_없으면_국면을_정하지_않는다(self):
        name, reasons = regime.classify(price_12m=None, price_3m=0.05,
                                        volume_ratio=1.5)
        assert name == "확인 불가"

    def test_경계값이_가정이라고_밝힌다(self):
        r = regime.Regime("과열", 0.25, 0.05, 1.2, 30, [])
        assert "판정 기준" in regime.feature(r).calc.intermediates["경계값 성격"]

    def test_같은_시점_같은_지역이면_모든_단지가_같은_국면을_본다(self, db):
        with get_conn(db) as conn:
            a = add_complex(conn, "가", "K1")
            b = add_complex(conn, "나", "K2")
            for cid in (a, b):
                monthly(conn, cid, "202411", [5.0] * 12 + [6.0])
            ra = regime.region_regime(conn, LAWD, as_of=AS_OF)
            rb = regime.region_regime(conn, LAWD, as_of=AS_OF)
        assert ra.name == rb.name


# ── §15·§16 거래량과 거래 질 ───────────────────────────────────────────

class TestFlow:
    def test_거래량은_매수신호가_아니라_조사우선순위다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            for d in ("20251101", "20251105", "20251110"):
                add_trade(conn, cid, d, 6.0)
            stage = flow.flow_stage(conn, cid, BAND, as_of=AS_OF)
            got = flow.investigation_priority(stage, 3, 1)
        assert got.key == "investigation_priority"
        assert "매수 신호가 아니다" in str(got.calc.intermediates)

    def test_어느_단계가_좋은지_정하지_않는다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            add_trade(conn, cid, "20251101", 6.0)
            got = flow.flow_stage(conn, cid, BAND, as_of=AS_OF)
        assert "백테스트가 학습" in got.calc.intermediates["주의"]

    def test_취소거래는_세지_않는다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            add_trade(conn, cid, "20251101", 6.0)
            add_trade(conn, cid, "20251102", 9.0, cancelled=1)
            got = flow.transaction_quality(conn, cid, BAND, as_of=AS_OF)
        assert got.calc.inputs["거래 수"] == 1

    def test_저층만_거래되면_질이_낮다(self, db):
        with get_conn(db) as conn:
            low = add_complex(conn, "저층만", "K1")
            mixed = add_complex(conn, "중층", "K2")
            for i, d in enumerate(("20251101", "20251105", "20251110", "20251115")):
                add_trade(conn, low, d, 6.0, floor=2)
                add_trade(conn, mixed, d, 6.0, floor=12)
            low_q = flow.transaction_quality(conn, low, BAND, as_of=AS_OF)
            mixed_q = flow.transaction_quality(conn, mixed, BAND, as_of=AS_OF)
        assert low_q.value < mixed_q.value

    def test_거래가_없으면_확인_불가(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            got = flow.transaction_quality(conn, cid, BAND, as_of=AS_OF)
        assert not got.known


# ── §13 공급 ───────────────────────────────────────────────────────────

class TestSupply:
    def _plan(self, conn, households, move_in, *, stage="입주예정",
              announced="202401", name=None, lat=None, lon=None, kind=None):
        conn.execute(
            "INSERT INTO supply_plan (lawd_cd, complex_name, households, move_in_ym, "
            " stage, kind, lat, lon, announced_ym) VALUES (?,?,?,?,?,?,?,?,?)",
            (LAWD, name or f"신규{move_in}{households}", households, move_in, stage,
             kind, lat, lon, announced))

    def test_절대물량이_아니라_stock_대비_비율이다(self, db):
        with get_conn(db) as conn:
            big = add_complex(conn, "큰동네", "K1", households=50000)
            self._plan(conn, 3000, "202706")
            view = supply.build(conn, big, as_of=AS_OF)
            got = supply.ratio_feature(view, 3)
        assert got.known
        assert got.value == pytest.approx(3000 / 50000, rel=0.01)
        assert "절대물량으로 비교하면" in got.calc.intermediates["해석"]

    def test_stock_을_모르면_절대물량으로_대체하지_않는다(self, db):
        with get_conn(db) as conn:
            conn.execute("UPDATE complex SET apt_households = NULL")
            cid = add_complex(conn, "세대수없음", "K9", households=None)
            conn.execute("UPDATE complex SET apt_households = NULL WHERE id = ?", (cid,))
            self._plan(conn, 3000, "202706")
            got = supply.ratio_feature(supply.build(conn, cid, as_of=AS_OF), 3)
        assert not got.known
        assert "절대물량으로 대체하지 않습니다" in got.detail["사유"]

    def test_단계마다_실효물량이_다르다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, households=10000)
            self._plan(conn, 1000, "202706", stage="계획", name="계획단지")
            planned = supply.build(conn, cid, as_of=AS_OF).by_horizon[3]
            conn.execute("DELETE FROM supply_plan")
            self._plan(conn, 1000, "202706", stage="입주예정", name="확정단지")
            confirmed = supply.build(conn, cid, as_of=AS_OF).by_horizon[3]
        assert planned < confirmed
        assert confirmed == 1000

    def test_발표_전_공급은_보이지_않는다(self, db):
        """§18 — 나중에 발표된 계획을 과거 모델이 알면 반칙이다."""
        with get_conn(db) as conn:
            cid = add_complex(conn, households=10000)
            self._plan(conn, 1000, "202706", announced="202601")   # 컷오프 이후 발표
            view = supply.build(conn, cid, as_of=AS_OF)
        assert view.items == []

    def test_발표시점이_없으면_쓰지_않는다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, households=10000)
            self._plan(conn, 1000, "202706", announced=None)
            view = supply.build(conn, cid, as_of=AS_OF)
        assert view.items == []

    def test_어느_기준으로_셌는지_밝힌다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, households=10000)
            view = supply.build(conn, cid, as_of=AS_OF)
        assert "좌표 없음" in view.basis

    def test_공급절벽을_탐지한다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, households=10000)
            self._plan(conn, 2000, "202703", name="곧입주")
            got = supply.cliff_feature(supply.build(conn, cid, as_of=AS_OF))
        assert got.known and got.value > 0
        assert "정보 부족" in got.calc.intermediates["주의"]


# ── §14 전세 ───────────────────────────────────────────────────────────

class TestJeonse:
    def test_같은_기준월끼리만_나눈다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            add_price(conn, cid, "202511", 6.0)
            add_jeonse(conn, cid, "202509", 3.0)      # 다른 달
            got = jeonse.ratio_feature(conn, cid, BAND, as_of=AS_OF)
        assert not got.known
        assert "다른 달을 섞어 나누지 않습니다" in got.detail["사유"]

    def test_전세가율을_계산한다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            add_price(conn, cid, "202511", 6.0)
            add_jeonse(conn, cid, "202511", 3.6)
            got = jeonse.ratio_feature(conn, cid, BAND, as_of=AS_OF)
        assert got.value == pytest.approx(0.6, abs=1e-6)

    def test_전세는_Upside_가_아니라_Downside_로만_쓴다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            add_price(conn, cid, "202511", 6.0)
            add_jeonse(conn, cid, "202511", 4.8)
            ratio = jeonse.ratio_feature(conn, cid, BAND, as_of=AS_OF)
            got = jeonse.downside_defense(ratio)
        assert got.key == "downside_defense"
        assert "Upside 에 더하지 않는다" in got.calc.intermediates["쓰임"]
        assert got.value == 1.0                       # 80% 이상이면 만점

    def test_전세_선행을_자동_매수신호로_쓰지_않는다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            for i, ym in enumerate(["202411", "202511"]):
                add_price(conn, cid, ym, 6.0)
                add_jeonse(conn, cid, ym, 3.0 + i * 0.6)
            got = jeonse.jeonse_lead(conn, cid, BAND, as_of=AS_OF, months=12)
        assert got.known and got.value > 0
        assert "자동 매수 신호가 아니다" in got.calc.intermediates["주의"]


# ── 조립 · Ablation ────────────────────────────────────────────────────

class TestAssemble:
    def _full(self, conn):
        cid = add_complex(conn, lat=37.49, lon=126.72)
        monthly(conn, cid, "202411", [5.0] * 12 + [6.0])
        for ym in ("202411", "202511"):
            add_jeonse(conn, cid, ym, 3.6)
        for d in ("20251101", "20251105", "20251110"):
            add_trade(conn, cid, d, 6.0)
        return cid

    def test_모든_그룹이_한_번에_나온다(self, db):
        with get_conn(db) as conn:
            cid = self._full(conn)
            fs = assemble.build(conn, cid, BAND, as_of=AS_OF)
        assert fs.complex_id == cid
        for key in ("momentum_12m", "regime", "flow_stage", "supply_ratio_3y",
                    "jeonse_ratio", "downside_defense", "discovery_lag"):
            assert key in fs, key

    def test_그룹을_지정하면_그것만_계산한다(self, db):
        with get_conn(db) as conn:
            cid = self._full(conn)
            fs = assemble.build(conn, cid, BAND, as_of=AS_OF, groups=["jeonse"])
        assert "jeonse_ratio" in fs
        assert "momentum_12m" not in fs

    def test_모르는_그룹은_거부한다(self, db):
        with get_conn(db) as conn:
            cid = self._full(conn)
            with pytest.raises(ValueError, match="모르는 feature 그룹"):
                assemble.build(conn, cid, BAND, as_of=AS_OF, groups=["없는그룹"])

    def test_Ablation_으로_그룹을_뺄_수_있다(self, db):
        with get_conn(db) as conn:
            cid = self._full(conn)
            fs = assemble.build(conn, cid, BAND, as_of=AS_OF)
        keys = [k for k in fs.items if assemble.group_of(k) == "jeonse"]
        assert keys
        reduced = fs.without(*keys)
        assert not any(assemble.group_of(k) == "jeonse" for k in reduced.items)
        assert len(reduced.items) < len(fs.items)

    def test_모든_feature_가_그룹에_속한다(self, db):
        """새 feature 를 만들고 그룹 등록을 잊으면 Ablation 에서 빠진다."""
        with get_conn(db) as conn:
            cid = self._full(conn)
            fs = assemble.build(conn, cid, BAND, as_of=AS_OF)
        orphans = [k for k in fs.items if assemble.group_of(k) is None]
        assert orphans == [], f"그룹에 속하지 않은 feature: {orphans}"

    def test_데이터가_없어도_죽지_않고_DATA_MISSING_이_된다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            fs = assemble.build(conn, cid, BAND, as_of=AS_OF)
        assert fs.items
        assert fs.coverage < 1.0
        assert all(f.value is None or f.known for f in fs.items.values())

    def test_컷오프_이후_데이터는_feature_에_들어오지_않는다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn)
            monthly(conn, cid, "202411", [5.0] * 12 + [6.0])
            add_price(conn, cid, "202602", 20.0)      # 컷오프 이후 급등
            fs = assemble.build(conn, cid, BAND, as_of=AS_OF)
        assert fs["momentum_12m"].value == pytest.approx(0.2, abs=1e-6)
