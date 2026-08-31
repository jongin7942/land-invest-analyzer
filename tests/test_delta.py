"""DELTA UPGRADE — 신규 지시서 §1~§46 중 이번에 구현한 것.

이 파일의 절반은 **금지 규칙**이다. 지시서 §49가 15개를 금지했는데, 그중
코드로 막을 수 있는 것을 여기서 고정한다. 특히:

    §49-8  싸다는 이유만으로 Pre-Breakout 분류 금지
    §49-5  거래량 증가만으로 가산점 금지
    §49-6  전고점 대비 하락률만으로 저평가 판단 금지
    §45    한 Feature 를 여러 영역에서 중복 가산/감점 금지
"""
import sqlite3

import pytest

from apt_engine.backtest import synthetic as synth_mod
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.features import assemble, bands, registry, stage
from apt_engine.features import stretch as stretch_mod
from apt_engine.features.base import Feature, FeatureSet, Status


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    return tmp_db


def _fs(**values) -> FeatureSet:
    """Stage 판정용 최소 FeatureSet. 주지 않은 키는 '확인 불가' 다."""
    items = {}
    for key, value in values.items():
        items[key] = (Feature.missing(key, "테스트에서 주지 않음") if value is None
                      else Feature(key, value, "", 0.8, Status.OK))
    return FeatureSet(1, "84", "2023-01-01", items)


# ── §45 GATE / ALPHA / RISK 중복 금지 ────────────────────────────────

class TestRoleSeparation:
    def test_한_Feature_가_두_역할을_갖지_않는다(self):
        assert registry.audit_roles() == []

    def test_모든_등록_Feature_가_State_와_role_을_갖는다(self):
        for key, e in registry.REGISTRY.items():
            assert e.state, key
            assert e.role, key
            assert e.note, f"{key}: 왜 이 Feature 가 있는지 적혀 있지 않습니다"

    def test_실제로_생산되는_Feature_가_전부_등록돼_있다(self, db):
        """새 Feature 를 만들면서 등록부에 넣는 것을 잊으면 잡는다."""
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=3, start_ym="201801",
                            end_ym="202412")
            fs = assemble.build(conn, 1, "84",
                                as_of=cutoff_mod.AsOf("2023-01-01"),
                                lawd_cd="11110")
        missing = [k for k in fs.items if registry.get(k) is None]
        assert missing == [], (
            f"등록부에 없는 Feature: {missing}. "
            f"features/registry.py 에 State·role 을 정하세요")

    def test_STRETCH_는_전부_낮을수록_좋다(self):
        for e in registry.by_state(registry.STRETCH):
            if e.role == registry.ROLE_CONTEXT:
                continue
            assert not e.higher_is_better, (
                f"{e.feature_key} 는 STRETCH 인데 높을수록 좋다고 돼 있습니다")

    def test_CORE_는_백테스트_전에는_비어_있다(self, db):
        """§44 — 사람이 CORE 를 정하지 않는다."""
        assert registry.core_keys() == []
        with get_conn(db) as conn:
            registry.sync(conn)
            assert registry.core_keys(conn) == []

    def test_Fold_가_모자라면_CORE_로_못_올린다(self, db):
        with get_conn(db) as conn:
            registry.sync(conn)
            with pytest.raises(registry.RegistryError, match="Fold"):
                registry.promote(conn, "price_stretch", run_key="wf1", folds=1)

    def test_근거_없이_CORE_로_바꾸면_스키마가_거부한다(self, db):
        with get_conn(db) as conn:
            registry.sync(conn)
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE feature_registry SET tier='CORE' "
                             " WHERE feature_key='price_stretch'")


# ── §9 Price Band Migration ──────────────────────────────────────────

class TestBandMigration:
    def _series(self, rows):
        return bands.BandSeries(1, "84", [
            bands.BandPoint(ym, p25, p50, p75, n) for ym, p25, p50, p75, n in rows])

    def test_P75_만_오르면_Weak_로_본다(self):
        """§9 — 비싼 물건 몇 건 팔린 것을 가격대 이동으로 세지 않는다."""
        series = self._series([
            ("202301", 100, 110, 150, 8),
            ("202201", 100, 110, 120, 8),
        ])
        feats = {f.key: f for f in bands.migration_features(series, months=12)}
        shift = feats["band_shift_strength"]
        assert shift.value == pytest.approx(0.25)
        assert "Weak" in shift.detail["판정"]

    def test_셋_다_오르면_Strong(self):
        series = self._series([
            ("202301", 120, 130, 150, 8),
            ("202201", 100, 110, 120, 8),
        ])
        feats = {f.key: f for f in bands.migration_features(series, months=12)}
        assert feats["band_shift_strength"].value == pytest.approx(1.0)
        assert "Strong" in feats["band_shift_strength"].detail["판정"]

    def test_분위수가_없으면_대표가격으로_채우지_않는다(self):
        """p25/p75 를 대표가격으로 채우면 분포가 한 점으로 눌린다."""
        series = self._series([("202301", None, 110, None, 8)])
        assert series.usable == []
        feats = {f.key: f for f in bands.migration_features(series)}
        assert feats["band_shift_strength"].value is None
        assert feats["band_shift_strength"].detail["사유"]


# ── §8 Slope Persistence ─────────────────────────────────────────────

class TestSlopes:
    def test_긴_구간에_더_큰_가중치를_준다(self):
        short_only = bands.Slopes({3: 0.20, 6: -0.01, 12: -0.01, 24: -0.01})
        long_too = bands.Slopes({3: 0.02, 6: 0.02, 12: 0.02, 24: 0.02})
        a = bands.slope_persistence(short_only).value
        b = bands.slope_persistence(long_too).value
        assert a < b, "3개월만 튄 것이 전 구간 상승보다 높게 나오면 안 됩니다"

    def test_3M_만_뛰면_Spike_경고(self):
        spiked, why = bands.spike_warning(
            bands.Slopes({3: 0.20, 12: 0.00, 24: 0.01}))
        assert spiked
        assert "단발" in why

    def test_전_구간_상승은_Spike_가_아니다(self):
        spiked, _ = bands.spike_warning(
            bands.Slopes({3: 0.20, 12: 0.15, 24: 0.25}))
        assert not spiked


# ── §7 Latent / Visible ──────────────────────────────────────────────

class TestLatentVisible:
    def test_관측이_짧으면_Latent_를_만들지_않는다(self):
        series = bands.BandSeries(1, "84", [
            bands.BandPoint(f"2023{m:02d}", 100, 110, 120, 5)
            for m in range(1, 7)])
        f = bands.latent_movement(series, bands.Slopes({6: 0.02}))
        assert f.value is None
        assert "조용히 오래" in f.detail["사유"]

    def test_못_구한_조건은_X_가_아니라_분모에서_뺀다(self):
        rows = [(f"{2024 - i // 12:04d}{12 - i % 12:02d}",
                 100 - i, 110 - i, 120 - i, 5) for i in range(30)]
        series = bands.BandSeries(1, "84", [bands.BandPoint(*r) for r in rows])
        with_jeonse = bands.latent_movement(series, bands.Slopes({}),
                                            jeonse_floor_ok=True)
        without = bands.latent_movement(series, bands.Slopes({}))
        assert with_jeonse.detail["확인한 조건"] == "4/4"
        assert without.detail["확인한 조건"] == "3/4"

    def test_Visible_은_높다고_좋은_게_아니라고_말한다(self):
        series = bands.BandSeries(1, "84", [
            bands.BandPoint("202301", 100, 110, 120, 9)])
        shift = Feature("band_shift_strength", 1.0, "", 0.8, Status.OK)
        f = bands.visible_movement(series, shift)
        assert "늦은 것" in f.detail["주의"]


# ── §5 Price Stretch ─────────────────────────────────────────────────

class TestPriceStretch:
    def _long_series(self, prices):
        return bands.BandSeries(1, "84", [
            bands.BandPoint(f"{2024 - i // 12:04d}{12 - i % 12:02d}",
                            int(p * 0.95), int(p), int(p * 1.06), 8)
            for i, p in enumerate(prices)])

    def test_전고점_대비가_아니라_추세_대비다(self):
        """§5·§49-6 — 전고점이 높았다는 이유로 싸다고 하지 않는다."""
        # 한 번 크게 튀었다가 추세로 돌아온 시계열
        prices = [100] * 12 + [180] + [100] * 24
        series = self._long_series(prices)
        normal = stretch_mod.historical_normal(series)
        f = stretch_mod.price_stretch(series, normal)
        assert f.usable
        # 전고점(180) 대비면 -44% 라 '매우 쌈' 이 되어야 하지만,
        # 추세 대비로는 그렇지 않다.
        assert f.value > -0.30, (
            f"전고점 대비 하락률을 저평가로 쓰고 있습니다 (stretch={f.value:.1%})")
        assert "전고점" in f.detail["주의"]

    def test_관측이_짧으면_정상가를_만들지_않는다(self):
        series = self._long_series([100] * 12)
        normal = stretch_mod.historical_normal(series)
        assert not normal.known
        assert "장기 정상가격을 만들지 않습니다" in normal.reason

    def test_못한_보정을_1_0_으로_가정하지_않고_기록한다(self):
        series = self._long_series([100 + i for i in range(36)])
        normal = stretch_mod.historical_normal(series)
        assert normal.known
        assert set(normal.skipped) == {"지역 Beta", "생활권 Beta", "상품 Quality(연식)"}
        f = stretch_mod.price_stretch(series, normal)
        assert f.calc.grade == "ESTIMATED", "보정을 못 했으면 CONFIRMED 가 아닙니다"

    def test_보정을_주면_반영된다(self):
        series = self._long_series([100 + i for i in range(36)])
        plain = stretch_mod.historical_normal(series)
        adjusted = stretch_mod.historical_normal(series, region_beta=1.5)
        assert adjusted.value < plain.value
        assert "지역 Beta" in adjusted.adjustments


# ── §6 Acceleration 역U ──────────────────────────────────────────────

class TestAccelerationZone:
    def _z(self, six, three=None):
        values = {6: six}
        if three is not None:
            values[3] = three
        series = bands.BandSeries(1, "84", [
            bands.BandPoint("202301", 100, 110, 120, 8)])
        return stretch_mod.acceleration_zone(series, bands.Slopes(values))

    def test_선형이_아니다(self):
        """§6·§49-5 — 많이 오를수록 계속 가산하면 상투를 잡는다."""
        dormant = self._z(0.00).value
        emerging = self._z(0.05).value
        confirmation = self._z(0.15).value
        overheated = self._z(0.40).value
        # STRETCH 값이므로 낮을수록 좋다. Emerging 이 가장 낮아야 한다.
        assert emerging < confirmation < dormant
        assert overheated > confirmation
        assert emerging == min(dormant, emerging, confirmation, overheated)

    def test_Extreme_가속은_Stretch_를_올린다(self):
        calm = self._z(0.05, three=0.02).value
        extreme = self._z(0.05, three=0.25).value
        assert extreme > calm
        assert "예" in self._z(0.05, three=0.25).detail["Extreme 가속"]

    def test_기울기가_없으면_구간을_지어내지_않는다(self):
        f = self._z(None) if False else stretch_mod.acceleration_zone(
            bands.BandSeries(1, "84", []), bands.Slopes({}))
        assert f.value is None


# ── §22·§38·§49-8 Stage 분류 ─────────────────────────────────────────

class TestStage:
    def test_싸고_안_움직이면_PRE_BREAKOUT_이_아니다(self):
        """§49-8 — 이 테스트가 이번 업그레이드의 핵심이다."""
        v = stage.classify(_fs(price_stretch=-0.20, band_shift_strength=0.0,
                               latent_movement=0.1, visible_movement=0.0,
                               slope_persistence=0.0))
        assert v.stage != stage.PRE_BREAKOUT
        assert v.stage == stage.DORMANT
        assert any("싸다는 이유만으로" in r for r in v.reasons)

    def test_오래_싼_채로_안_움직이면_VALUE_TRAP(self):
        v = stage.classify(_fs(price_stretch=-0.20, band_shift_strength=0.0,
                               latent_movement=0.1, visible_movement=0.0),
                           persistent_cheap_months=36)
        assert v.stage == stage.VALUE_TRAP
        assert v.quadrant == stage.VALUE_TRAP_CANDIDATE

    def test_싸고_바닥이_움직이면_PRE_BREAKOUT(self):
        v = stage.classify(_fs(price_stretch=-0.10, band_shift_strength=0.75,
                               latent_movement=0.85, visible_movement=0.2,
                               slope_persistence=0.9))
        assert v.stage == stage.PRE_BREAKOUT
        assert v.quadrant == stage.TARGET

    def test_비싼데_움직이면_CHASE(self):
        v = stage.classify(_fs(price_stretch=0.25, band_shift_strength=0.9,
                               latent_movement=0.5, visible_movement=0.8))
        assert v.stage == stage.CHASE
        assert v.quadrant == stage.CHASE

    def test_이미_다_보이면_CONFIRMED_로_내려간다(self):
        """§20 Proof–Price Tradeoff — 확실해질수록 남은 알파는 준다."""
        early = stage.classify(_fs(price_stretch=-0.05, band_shift_strength=0.8,
                                   latent_movement=0.5, visible_movement=0.50))
        late = stage.classify(_fs(price_stretch=-0.05, band_shift_strength=0.8,
                                  latent_movement=0.5, visible_movement=0.95))
        assert early.stage == stage.EMERGING
        assert late.stage == stage.CONFIRMED

    def test_값을_모르면_DORMANT_가_아니라_UNKNOWN(self):
        """모르는 것을 '움직임 없음' 으로 세면 데이터 공백이 판정으로 둔갑한다."""
        v = stage.classify(_fs(price_stretch=None, band_shift_strength=None))
        assert v.stage == stage.UNKNOWN
        assert v.unknown_reason

    def test_UNKNOWN_은_사유_없이_저장할_수_없다(self, db):
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=1, start_ym="202301",
                            end_ym="202312")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO stage_state (complex_id, area_band, as_of, "
                    " stage, reasons_json) VALUES (1,'84','2023-01-01',"
                    " 'UNKNOWN','{\"a\":1}')")

    def test_EXHAUSTED_는_실행_목록에서_빠진다(self):
        """§38 — 좋은 아파트라도 EXHAUSTED 면 신규매수 순위는 낮다."""
        v = stage.classify(_fs(price_stretch=0.30, band_shift_strength=0.0,
                               latent_movement=0.0, visible_movement=0.0))
        assert v.stage == stage.EXHAUSTED
        assert not stage.executable(v)

    def test_QUIET_COMPOUNDER_는_조용한_것과_다르다(self):
        quiet = stage.classify(_fs(price_stretch=-0.02, band_shift_strength=0.6,
                                   latent_movement=0.7, visible_movement=0.3,
                                   slope_persistence=0.9))
        just_dormant = stage.classify(_fs(price_stretch=-0.02,
                                          band_shift_strength=0.0,
                                          latent_movement=0.2,
                                          visible_movement=0.0,
                                          slope_persistence=0.1))
        assert quiet.quiet_compounder
        assert not just_dormant.quiet_compounder

    def test_Watch_목록은_조용히_오르는_것만_받는다(self):
        quiet = stage.classify(_fs(price_stretch=-0.02, band_shift_strength=0.3,
                                   latent_movement=0.7, visible_movement=0.2,
                                   slope_persistence=0.9))
        dead = stage.classify(_fs(price_stretch=-0.20, band_shift_strength=0.0,
                                  latent_movement=0.1, visible_movement=0.0,
                                  slope_persistence=0.0))
        assert stage.watchable(quiet)
        assert not stage.watchable(dead), (
            "그냥 조용한 것과 조용히 오르는 것은 다릅니다(§23)")


# ── §1 Type 축 ───────────────────────────────────────────────────────

class TestTypeAxis:
    def test_근거_없이_타입을_쪼갤_수_없다(self, db):
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=1, start_ym="202301",
                            end_ym="202312")
            with pytest.raises(sqlite3.IntegrityError):
                # observed_months 6 미만 — '지속적' 이 아니다
                conn.execute(
                    "INSERT INTO complex_type (complex_id, area_band, type_key, "
                    " label, observed_months, median_gap_pct, sample_n, "
                    " evidence_json) VALUES (1,'84','84A','A타입',2,0.05,20,"
                    " '{\"a\":1}')")

    def test_표본이_적으면_타입을_쪼갤_수_없다(self, db):
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=1, start_ym="202301",
                            end_ym="202312")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO complex_type (complex_id, area_band, type_key, "
                    " label, observed_months, median_gap_pct, sample_n, "
                    " evidence_json) VALUES (1,'84','84A','A타입',12,0.05,3,"
                    " '{\"a\":1}')")


# ── §11·§12 Leader 망 ────────────────────────────────────────────────

class TestLeaderSchema:
    def test_자기_자신을_Leader_로_둘_수_없다(self, db):
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=2, start_ym="202301",
                            end_ym="202312")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO leader_link (follower_id, leader_id, area_band, "
                    " leader_kind, as_of, evidence_json) "
                    "VALUES (1,1,'84','LOCAL','2023-01-01','{\"a\":1}')")

    def test_회복가능비율을_못_내면_사유가_있어야_한다(self, db):
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=1, start_ym="202301",
                            end_ym="202312")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO transmission_state (follower_id, area_band, "
                    " as_of) VALUES (1,'84','2023-01-01')")


# ── §27 Control Pair ─────────────────────────────────────────────────

class TestControlPair:
    def test_회귀_검사_외의_용도로_넣을_수_없다(self, db):
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO control_pair (pair_key, as_of, winner_label, "
                    " loser_label, area_band, hypothesis, purpose) "
                    "VALUES ('p','2019-01-01','A','B','59','가설','SCORING')")


# ── §43 Coverage ─────────────────────────────────────────────────────

class TestCoverage:
    def test_전체_스캔이_아니면_FULL_이라고_쓸_수_없다(self, db):
        """스키마가 두 값만 허용한다 — 세 번째 표현을 만들어내지 못한다."""
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO universe_coverage (as_of, area_band, scanned_n, "
                    " known_n, verdict) VALUES ('2023-01-01','84',10,100,'거의 전체')")


# ── §2·§3 Capital Gate · CASH 후보 ───────────────────────────────────

class TestCashCandidate:
    def test_전세승계와_주담대를_동시에_적용하면_거부한다(self, db):
        """메모에 있던 실투자금 음수 버그. UI 가 아니라 엔진이 막아야 한다(§2)."""
        from apt_engine.cash import self_capital as cap
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=1, start_ym="202301",
                            end_ym="202312")
            with pytest.raises(cap.CapitalCombinationError, match="선순위"):
                cap.compute(conn, price=500_000_000, as_of="2023-06-01",
                            lawd_cd="11110", use_mortgage=True,
                            assume_jeonse=True, jeonse_deposit=300_000_000)

    def test_현금수익률이_없으면_0_으로_가정하지_않는다(self):
        """0% 로 가정하면 현금이 항상 최악이 되어 §3 이 무의미해진다."""
        from apt_engine.invest import cash_candidate as cc
        c = cc.CashOption(300_000_000, None, 5, "프로필에 없음")
        assert c.expected_return is None
        assert not c.known
        better, why = cc.beats(c, candidate_return=0.10)
        assert better is None
        assert "기준선이 없습니다" in why

    def test_남는_현금을_버리지_않는다(self):
        from apt_engine.invest import cash_candidate as cc
        c = cc.CashOption(500_000_000, 0.03, 5)
        leftover, gain, why = cc.unused_cash_return(c,
                                                    required_equity=300_000_000)
        assert leftover == 200_000_000
        assert gain > 0

    def test_남는_현금_수익을_모르면_총자본수익률을_내지_않는다(self):
        """0 으로 세면 비싼 물건이 부당하게 유리해진다."""
        from apt_engine.invest import cash_candidate as cc
        c = cc.CashOption(500_000_000, None, 5, "없음")
        total, detail = cc.total_capital_return(
            property_gain=0.20, purchase_price=400_000_000,
            required_equity=300_000_000, cash=c)
        assert total is None
        assert "부당하게 유리" in detail["주의"]

    def test_현금보다_못하면_False_모르면_None(self):
        from apt_engine.invest import cash_candidate as cc
        c = cc.CashOption(300_000_000, 0.03, 5)
        assert cc.beats(c, candidate_return=0.50)[0] is True
        assert cc.beats(c, candidate_return=0.05)[0] is False
        assert cc.beats(c, candidate_return=None)[0] is None


# ── §37·§39·§40·§43 실행 랭킹 ────────────────────────────────────────

class _C:
    def __init__(self, cid):
        self.complex_id = cid


class TestExecutableSplit:
    def _stage(self, name, quiet=False):
        return stage.Verdict(name, None, quiet, ["테스트"])

    def test_EXHAUSTED_는_실행목록에_안_들어간다(self):
        from apt_engine.invest import cash_candidate as cc
        from apt_engine.ranking import executable as ex
        cands = [_C(1), _C(2)]
        stages = {1: self._stage(stage.EMERGING), 2: self._stage(stage.EXHAUSTED)}
        s = ex.split(cands, stages, cash=cc.CashOption(300_000_000, 0.03, 5),
                     expected_returns={1: 0.50, 2: 0.50})
        assert [c.complex_id for c in s.executable] == [1]
        assert any(cid == 2 for cid, _ in s.excluded)

    def test_현금보다_못하면_TOP_에_넣지_않는다(self):
        """§46 — YES 가 아니면 억지로 넣지 않는다."""
        from apt_engine.invest import cash_candidate as cc
        from apt_engine.ranking import executable as ex
        s = ex.split([_C(1)], {1: self._stage(stage.EMERGING)},
                     cash=cc.CashOption(300_000_000, 0.05, 5),
                     expected_returns={1: 0.02})
        assert s.executable == []
        assert "매수하지 않는 것이 우위" in s.excluded[0][1]

    def test_기대수익을_모르면_YES_로_치지_않는다(self):
        from apt_engine.invest import cash_candidate as cc
        from apt_engine.ranking import executable as ex
        s = ex.split([_C(1), _C(2)], {1: self._stage(stage.EMERGING),
                                      2: self._stage(stage.EMERGING)},
                     cash=cc.CashOption(300_000_000, 0.03, 5),
                     expected_returns={1: 0.50})
        assert [c.complex_id for c in s.executable] == [1]

    def test_CASH_가_순위를_갖는다(self):
        from apt_engine.invest import cash_candidate as cc
        from apt_engine.ranking import executable as ex
        s = ex.split([_C(1), _C(2)], {1: self._stage(stage.EMERGING),
                                      2: self._stage(stage.EMERGING)},
                     cash=cc.CashOption(300_000_000, 0.03, 5),
                     expected_returns={1: 0.50, 2: 0.40})
        assert s.cash_rank == 3, "둘 다 현금을 이겼으면 CASH 는 3위다"

    def test_살_만한_게_없으면_CASH_DOMINANT(self):
        from apt_engine.invest import cash_candidate as cc
        from apt_engine.ranking import executable as ex
        cands = [_C(i) for i in range(1, 11)]
        stages = {i: self._stage(stage.EXHAUSTED) for i in range(1, 11)}
        s = ex.split(cands, stages, cash=cc.CashOption(300_000_000, 0.03, 5))
        assert s.temperature == ex.CASH_DOMINANT

    def test_시장온도가_개별_점수를_바꾸지_않는다(self):
        from apt_engine.ranking import executable as ex
        assert "강제로 바꾸지 않습니다" in ex.TEMPERATURE_NOTE

    def test_대안이_좋으면_최대매수가를_낮춘다(self):
        """§39 Competitive Buy Price."""
        from apt_engine.ranking import executable as ex
        plain = ex.price_bands(500_000_000, entry_position=0.25)
        rich = ex.price_bands(500_000_000, entry_position=0.25,
                              alternatives_quality=1.0)
        assert rich.do_not_buy < plain.do_not_buy
        assert "Competitive Buy Price" in rich.competitive_note

    def test_매수구간을_모르면_가격대를_지어내지_않는다(self):
        from apt_engine.ranking import executable as ex
        b = ex.price_bands(500_000_000, entry_position=None)
        assert b.strong_buy is None
        assert b.verdict() == "확인 불가"

    def test_다_못_봤으면_전체라고_쓰지_않는다(self, db):
        """§43·§49-13."""
        from apt_engine.ranking import executable as ex
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=20, start_ym="202301",
                            end_ym="202312")
            partial = ex.measure(conn, as_of="2023-06-01", area_band="84",
                                 scanned_ids={1, 2, 3})
            full = ex.measure(conn, as_of="2023-06-01", area_band="84",
                              scanned_ids=set(range(1, 21)))
        assert partial.verdict == "PARTIAL_VERIFIED_UNIVERSE"
        assert "PARTIAL" in partial.title
        assert full.verdict == "FULL_UNIVERSE"

    def test_모수를_모르면_전체로_치지_않는다(self, db):
        from apt_engine.ranking import executable as ex
        with get_conn(db) as conn:
            c = ex.measure(conn, as_of="2023-06-01", area_band="84",
                           scanned_ids=set())
        assert c.verdict == "PARTIAL_VERIFIED_UNIVERSE"


# ── §10~§16 Leader 망 · 전달 실패 · 회복가능 할인 ────────────────────

def _bs(prices, start_ym=202401):
    """가격 목록(최신순)으로 BandSeries."""
    pts = []
    y, m = start_ym // 100, start_ym % 100
    for p in prices:
        pts.append(bands.BandPoint(f"{y:04d}{m:02d}", int(p * 0.95), int(p),
                                   int(p * 1.06), 8))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return bands.BandSeries(1, "84", pts)


class TestLeaderNetwork:
    def test_겹침을_모르면_Leader_로_인정하지_않는다(self):
        """§11 — 가까운 아파트를 무조건 Leader 로 지정하지 않는다."""
        from apt_engine.features import leader
        near = leader.Leader(2, leader.LOCAL, None, "거리 300m")
        overlapped = leader.Leader(3, leader.LOCAL, 0.6, "같은 학군·가격대")
        assert not near.relevant
        assert overlapped.relevant
        assert leader.relevant_leaders([near, overlapped]) == [overlapped]

    def test_겹침을_모르면_전달실패를_판정하지_않는다(self):
        """겹침 없이 '안 따라왔다' 는 의미가 없다."""
        from apt_engine.features import leader
        t = leader.transmission(_bs([100] * 24), _bs([130] * 12 + [100] * 12),
                                buyer_overlap=None)
        assert not t.known
        assert "겹침 없이" in t.reason

    def test_Leader_가_올랐는데_무반응이면_전달실패(self):
        from apt_engine.features import leader
        follower = _bs([100] * 24)
        lead = _bs([130] * 12 + [100] * 12)
        t = leader.transmission(follower, lead, buyer_overlap=0.7)
        assert t.known and t.failure > 0

    def test_Follower_가_따라오면_전달실패가_아니다(self):
        from apt_engine.features import leader
        follower = _bs([125] * 12 + [100] * 12)
        lead = _bs([130] * 12 + [100] * 12)
        t = leader.transmission(follower, lead, buyer_overlap=0.7)
        assert t.failure == 0.0

    def test_Leader_가_안_올랐으면_전달을_논하지_않는다(self):
        from apt_engine.features import leader
        t = leader.transmission(_bs([100] * 24), _bs([101] * 24),
                                buyer_overlap=0.7)
        assert t.failure == 0.0
        assert "논할 상황이 아닙니다" in t.reason


class TestRecoverableDiscount:
    def test_전달실패를_모르면_분해하지_않는다(self):
        """§12·§13 — 분해 없이 전부 회복가능으로 보면 'Spread 가 크다 =
        기회가 크다' 라는 금지된 결론이 나온다."""
        from apt_engine.features import leader
        d = leader.decompose(0.40, transmission_failure=None)
        assert not d.known
        assert "Spread 가 크다" in d.reason

    def test_전달실패가_클수록_회복가능분이_준다(self):
        from apt_engine.features import leader
        low = leader.decompose(0.40, transmission_failure=0.1)
        high = leader.decompose(0.40, transmission_failure=0.9)
        assert low.recoverable > high.recoverable
        assert low.ratio > high.ratio

    def test_구조적_이유가_확인되면_구조적_할인으로_옮긴다(self):
        """§14 Why Not Yet."""
        from apt_engine.features import leader
        plain = leader.decompose(0.40, transmission_failure=0.2)
        explained = leader.decompose(0.40, transmission_failure=0.2,
                                     why_not_yet=["교통", "학군", "공급"])
        assert explained.structural > plain.structural
        assert explained.ratio < plain.ratio

    def test_이웃이_함께_움직이면_회복쪽에_무게를_준다(self):
        from apt_engine.features import leader
        alone = leader.decompose(0.40, transmission_failure=0.5)
        together = leader.decompose(0.40, transmission_failure=0.5,
                                    neighbour_confirmation=1.0)
        assert together.recoverable > alone.recoverable

    def test_Alpha_에는_회복가능분만_쓴다고_말한다(self):
        from apt_engine.features import leader
        f = leader.recoverable_feature(
            leader.decompose(0.40, transmission_failure=0.3))
        assert "회복가능 부분만" in f.detail["주의"]


class TestPersistentCheapness:
    def test_오래_싼_것_자체는_저평가_증거가_아니다(self):
        """§14."""
        from apt_engine.features import leader
        short = leader.persistent_cheapness(months_cheap=12, gap_closed=None)
        long = leader.persistent_cheapness(months_cheap=48, gap_closed=None)
        assert short.value == 0.0
        assert long.value > 0.5

    def test_격차가_닫히는_중이면_감점을_줄인다(self):
        from apt_engine.features import leader
        stuck = leader.persistent_cheapness(months_cheap=48, gap_closed=0.0)
        closing = leader.persistent_cheapness(months_cheap=48, gap_closed=0.10)
        assert closing.value < stuck.value

    def test_기간을_모르면_판정하지_않는다(self):
        from apt_engine.features import leader
        f = leader.persistent_cheapness(months_cheap=None, gap_closed=None)
        assert f.value is None


class TestNeighbourConfirmation:
    def test_Alpha_가_아니라_신뢰도라고_말한다(self):
        """§10 — 이 값을 직접 Alpha 로 크게 가산하지 않는다."""
        from apt_engine.features import leader
        f = leader.neighbour_confirmation(moving=3, valid=5)
        assert f.value == pytest.approx(0.6)
        assert "Alpha 가 아니라" in f.detail["주의"]
        assert registry.get("neighbour_confirmation").role == registry.ROLE_CONFIDENCE

    def test_비교단지가_없으면_1_0_으로_치지_않는다(self):
        from apt_engine.features import leader
        f = leader.neighbour_confirmation(moving=0, valid=0)
        assert f.value is None


class TestMoneyArrivalAndNextNode:
    def test_꼬리까지_왔으면_Chase_경고(self):
        """§15 — Depth 4 이후는 Chase Risk."""
        from apt_engine.features import leader
        ladder = leader.Ladder([(1, 11, 0.20), (2, 12, 0.15), (3, 13, 0.10),
                                (4, 14, 0.08)])
        f = leader.money_arrival_depth(ladder, self_rank=4)
        assert f.value == 4.0
        assert "경고" in f.detail

    def test_구성요소가_없으면_NextNode_점수를_만들지_않는다(self):
        """§16·§49-8 — 싸다는 것만으로 Next Node 로 보지 않는다."""
        from apt_engine.features import leader
        ladder = leader.Ladder([(1, 11, 0.20), (2, 12, 0.01)])
        f = leader.next_node(ladder, self_rank=2, buyer_overlap=None,
                             remaining_gap=0.4, early_band_migration=0.5,
                             transmission_probability=0.6)
        assert f.value is None
        assert "싸다는 것만으로" in f.detail["사유"]

    def test_바로_아래_칸이_아니면_점수가_낮다(self):
        from apt_engine.features import leader
        ladder = leader.Ladder([(1, 11, 0.20), (2, 12, 0.0), (3, 13, 0.0),
                                (4, 14, 0.0)])
        kw = dict(buyer_overlap=0.8, remaining_gap=0.4,
                  early_band_migration=0.7, transmission_probability=0.7)
        adjacent = leader.next_node(ladder, self_rank=2, **kw)
        far = leader.next_node(ladder, self_rank=4, **kw)
        assert adjacent.value > far.value
        assert far.value == 0.0, "두 칸 넘게 아래면 아직 순서가 아닙니다"

    def test_위_칸이_안_움직였으면_NextNode_가_아니다(self):
        from apt_engine.features import leader
        ladder = leader.Ladder([(1, 11, 0.0), (2, 12, 0.0)])
        f = leader.next_node(ladder, self_rank=2, buyer_overlap=0.8,
                             remaining_gap=0.4, early_band_migration=0.7,
                             transmission_probability=0.7)
        assert f.value == 0.0


# ── §25·§26 백테스트 성공 정의 · 새 Metric ──────────────────────────

class TestSuccessLevels:
    def _k(self, **kv):
        from apt_engine.backtest import kpi as kpi_mod
        return [kpi_mod.Kpi(k, v, 10) for k, v in kv.items()]

    def test_최종_목표는_Capital_Opportunity_다(self):
        from apt_engine.backtest import kpi as kpi_mod
        assert kpi_mod.OPTIMIZATION_TARGET == kpi_mod.CAPITAL_OPPORTUNITY

    def test_절대수익만_나면_1단계까지만_성공이다(self):
        from apt_engine.backtest import kpi as kpi_mod
        level, why = kpi_mod.success_level(
            self._k(median_forward_return=0.30, opportunity_alpha=-0.05))
        assert level == kpi_mod.ABSOLUTE
        assert "Benchmark 실패" in why

    def test_더_좋은_대안이_있었으면_최종_실패다(self):
        """§24 — 절대적으로는 성공이어도 상대적으로는 실패일 수 있다."""
        from apt_engine.backtest import kpi as kpi_mod
        level, why = kpi_mod.success_level(
            self._k(median_forward_return=0.30, opportunity_alpha=0.10,
                    missed_better_alternative_rate=0.20))
        assert level == kpi_mod.BENCHMARK
        assert "상대적으로는 실패" in why

    def test_셋_다_통과해야_최종_성공(self):
        from apt_engine.backtest import kpi as kpi_mod
        level, _ = kpi_mod.success_level(
            self._k(median_forward_return=0.30, opportunity_alpha=0.10,
                    missed_better_alternative_rate=0.0))
        assert level == kpi_mod.CAPITAL_OPPORTUNITY

    def test_모르면_성공으로_치지_않는다(self):
        from apt_engine.backtest import kpi as kpi_mod
        level, why = kpi_mod.success_level(
            self._k(median_forward_return=0.30, opportunity_alpha=0.10))
        assert level == kpi_mod.BENCHMARK
        assert "최종 목표" in why


class TestNewMetrics:
    def _out(self, cid, ret, picked):
        from apt_engine.backtest import outcome as o
        return o.Outcome(cid, "84", forward_return=ret, picked=picked)

    def test_더_좋은_대안을_놓쳤으면_잡는다(self):
        from apt_engine.backtest import kpi as kpi_mod, outcome as out_mod
        raw = [self._out(1, 0.10, True), self._out(2, 0.60, False),
               self._out(3, 0.05, False)]
        ranked = out_mod.classify(raw, {1})
        kpis = {k.key: k for k in kpi_mod.compute_window(
            ranked, picked_order=[1], scores={})}
        assert kpis["missed_better_alternative_rate"].value > 0

    def test_현금수익률을_모르면_판정하지_않는다(self):
        """0 으로 가정하면 현금이 항상 오답이 된다."""
        from apt_engine.backtest import kpi as kpi_mod, outcome as out_mod
        ranked = out_mod.classify([self._out(1, 0.10, True)], {1})
        kpis = {k.key: k for k in kpi_mod.compute_window(
            ranked, picked_order=[1], scores={})}
        assert kpis["cash_accuracy"].value is None
        assert "0 으로 가정하면" in kpis["cash_accuracy"].note

    def test_비용을_모르면_세후수익률을_지어내지_않는다(self):
        from apt_engine.backtest import kpi as kpi_mod, outcome as out_mod
        ranked = out_mod.classify([self._out(1, 0.10, True)], {1})
        kpis = {k.key: k for k in kpi_mod.compute_window(
            ranked, picked_order=[1], scores={})}
        for key in ("after_cost_return", "after_interest_return",
                    "after_tax_return"):
            assert kpis[key].value is None
            assert "부풀려집니다" in kpis[key].note

    def test_비용을_주면_단계별로_차감한다(self):
        from apt_engine.backtest import kpi as kpi_mod, outcome as out_mod
        ranked = out_mod.classify([self._out(1, 0.30, True)], {1})
        costs = {1: kpi_mod.Costs(acquisition=0.05, interest=0.08, tax=0.06)}
        kpis = {k.key: k for k in kpi_mod.compute_window(
            ranked, picked_order=[1], scores={}, costs=costs)}
        assert kpis["after_cost_return"].value == pytest.approx(0.25)
        assert kpis["after_interest_return"].value == pytest.approx(0.17)
        assert kpis["after_tax_return"].value == pytest.approx(0.11)

    def test_일부_비용만_알면_그_단계까지만_낸다(self):
        from apt_engine.backtest import kpi as kpi_mod, outcome as out_mod
        ranked = out_mod.classify([self._out(1, 0.30, True)], {1})
        costs = {1: kpi_mod.Costs(acquisition=0.05)}
        kpis = {k.key: k for k in kpi_mod.compute_window(
            ranked, picked_order=[1], scores={}, costs=costs)}
        assert kpis["after_cost_return"].value == pytest.approx(0.25)
        assert kpis["after_tax_return"].value is None


# ── §27·§41·§49-2 Control Pair 가 스코어링으로 새지 않는다 ──────────

class TestControlPairIsolation:
    def test_연구후보_이름이_결정경로_코드에_없다(self):
        """§41·§49-2 — 이 목록에 있다는 이유로 점수가 바뀌면 안 된다.

        `repo/control.py` 에만 이름이 있어야 하고, feature·scoring·ranking·
        blind 어디에도 등장하면 안 된다.
        """
        import pathlib
        from apt_engine.repo import control

        names = {label for _, label in control.load_research_set()}
        assert names, "연구셋 CSV 를 읽지 못했습니다"
        base = pathlib.Path(control.__file__).resolve().parent.parent
        offenders = []
        for package in ("features", "scoring", "ranking", "blind", "invest",
                        "repo"):
            for path in sorted((base / package).rglob("*.py")):
                text = path.read_text(encoding="utf-8")
                for name in names:
                    # 단지명은 공백·기호가 섞여 있어 핵심 토큰으로 본다
                    token = name.split()[0][:6]
                    if len(token) >= 4 and token in text:
                        offenders.append(f"{package}/{path.name}: {name}")
        assert offenders == [], (
            f"결정 경로에 연구후보 이름이 있습니다: {offenders}. "
            f"§41 은 Regression 전용이라고 못박았습니다")

    def test_회귀_외_용도로_저장할_수_없다(self, db):
        from apt_engine.repo import control
        with get_conn(db) as conn:
            control.seed(conn)
            rows = conn.execute(
                "SELECT purpose FROM control_pair").fetchall()
        assert rows and all(r["purpose"] == "REGRESSION" for r in rows)

    def test_점수가_없으면_실패가_아니라_모름이다(self):
        """데이터가 없어 점수가 안 나온 것과 모델이 틀린 것은 다르다."""
        from apt_engine.repo import control
        pair = control.Pair("p", "2019-01-01", "A", "B", "59", "가설",
                            winner_id=1, loser_id=2)
        ok, why = control.discriminates({1: 70.0}, pair)
        assert ok is None
        assert "판정 불가" in why

    def test_Winner_를_더_높게_보면_통과(self):
        from apt_engine.repo import control
        pair = control.Pair("p", "2019-01-01", "A", "B", "59", "가설",
                            winner_id=1, loser_id=2)
        assert control.discriminates({1: 70.0, 2: 40.0}, pair)[0] is True
        ok, why = control.discriminates({1: 40.0, 2: 70.0}, pair)
        assert ok is False
        assert "설명할 Feature 가" in why

    def test_2021_검사는_True_Blind_가_아니라고_명시한다(self):
        """§28 — 이미 결과를 아는 후보로 하는 검사다."""
        from apt_engine.repo import control
        assert "True Blind Test 가 아닙니다" in control.USAGE_NOTE


# ── §18 Excess Reset · §19 Path-Dependent ────────────────────────────

def _cycle_series(prices, samples=None):
    """가격 목록(**과거→현재** 순서)으로 BandSeries."""
    n = len(prices)
    pts = []
    for i, p in enumerate(prices):
        idx = n - 1 - i                       # 최신이 0
        total = 2024 * 12 + 11 - idx
        ym = f"{total // 12:04d}{total % 12 + 1:02d}"
        sample = (samples[i] if samples else 8)
        pts.append(bands.BandPoint(ym, int(p * 0.95), int(p), int(p * 1.06),
                                   sample))
    pts.reverse()                             # 최신순
    return bands.BandSeries(1, "84", pts)


class TestExcessReset:
    def test_고점_대비_하락률만으로_회복이라고_하지_않는다(self):
        """§18·§49-6 — 과열이 없었으면 '과열 후 조정' 이 아니다."""
        from apt_engine.features import cycle
        # 꾸준히 내려오기만 한 시계열. 고점 대비 -30% 이지만 과열이 없었다.
        r = cycle.excess_reset(_cycle_series(
            [100 - i for i in range(40)]))
        assert not r.known
        assert "고점 대비 하락률만으로" in r.reason

    def test_조정이_얕으면_다음_단계로_안_간다(self):
        from apt_engine.features import cycle
        prices = [100 + i * 3 for i in range(20)] + [157 - i for i in range(20)]
        r = cycle.excess_reset(_cycle_series(prices))
        assert cycle.OVERHEAT in r.completed
        assert cycle.CORRECTION not in r.completed or r.current_step is not None

    def test_전세를_모르면_그_다음_단계를_판정하지_않는다(self):
        """모르는 것을 통과로 세지 않는다."""
        from apt_engine.features import cycle
        # 과열 → 조정 → 거래마름 → 안정
        prices = ([100 + i * 4 for i in range(15)]      # 100 → 156 과열
                  + [156 - i * 5 for i in range(10)]    # 156 → 111 조정
                  + [110 + (i % 2) for i in range(15)])  # 안정
        samples = [12] * 15 + [4] * 10 + [4] * 15
        r = cycle.excess_reset(_cycle_series(prices, samples))
        assert cycle.OVERHEAT in r.completed
        assert cycle.JEONSE_HOLD not in r.completed
        assert "전세가 버텼는지 몰라" in r.reason

    def test_관측이_짧으면_사이클을_만들지_않는다(self):
        from apt_engine.features import cycle
        r = cycle.excess_reset(_cycle_series([100] * 12))
        assert not r.known
        assert "최소" in r.reason

    def test_단계를_순서대로_센다(self):
        from apt_engine.features import cycle
        f = cycle.reset_feature(cycle.Reset(
            [cycle.OVERHEAT, cycle.CORRECTION, cycle.DRY_UP],
            cycle.DRY_UP, "202110", "202301", -0.30))
        assert f.value == pytest.approx(3 / 7)
        assert "순서를 봅니다" in f.detail["주의"]


class TestPricePath:
    def test_같은_가격이라도_경로가_다르면_다른_상품이다(self):
        """§19 — 3.0→4.0→5.5 와 6.5→4.5→5.5 는 다르다."""
        from apt_engine.features import cycle
        spike = cycle.price_path(_cycle_series(
            [300] * 12 + [300 + i * 20 for i in range(13)]))
        recovery = cycle.price_path(_cycle_series(
            [650] * 6 + [650 - i * 20 for i in range(11)]
            + [440 + i * 10 for i in range(12)]))
        assert spike.kind == cycle.PATH_SPIKE
        assert recovery.kind == cycle.PATH_RESET_RECOVERY
        a = cycle.path_feature(spike).value
        b = cycle.path_feature(recovery).value
        assert b > a, "회복 경로가 급등 경로보다 높아야 합니다"

    def test_위쪽_수요_증거를_구분한다(self):
        from apt_engine.features import cycle
        recovery = cycle.price_path(_cycle_series(
            [650] * 6 + [650 - i * 20 for i in range(11)]
            + [440 + i * 10 for i in range(12)]))
        assert recovery.overhead_proof > 0, (
            "과거에 더 비싸게 거래된 이력 = 그 가격을 낼 사람이 있었다는 증거")

    def test_이력이_짧으면_경로를_판정하지_않는다(self):
        from apt_engine.features import cycle
        p = cycle.price_path(_cycle_series([100] * 6))
        assert not p.known
        assert cycle.path_feature(p).value is None


# ── §21·§36 EarlyAlpha ───────────────────────────────────────────────

class TestEarlyAlpha:
    def _fs_full(self, **over):
        base = {
            "recoverable_discount_ratio": 0.6,
            "band_shift_strength": 0.8,
            "next_node_score": 0.5,
            "buyer_pool": 0.7,
            "downside_defense": 0.6,
        }
        base.update(over)
        return _fs(**base)

    def test_가중치를_임의로_확정하지_않는다(self):
        """§21 — 학습 전에는 균등이고 그 사실이 표시된다."""
        from apt_engine.scoring import early_alpha
        a = early_alpha.compute(1, self._fs_full())
        assert a.weights_source == "HEURISTIC"
        assert "가중치 임시" in a.label
        assert "임의로 확정하지 않습니다" in early_alpha.WEIGHT_NOTE

    def test_곱이라_하나가_0_이면_전체가_0_이다(self):
        """§21 — 합으로 만들면 '격차가 없는데 높은 점수' 가 생긴다."""
        from apt_engine.scoring import early_alpha
        full = early_alpha.compute(1, self._fs_full())
        zeroed = early_alpha.compute(
            1, self._fs_full(recoverable_discount_ratio=0.0))
        assert full.alpha > 0
        assert zeroed.alpha < full.alpha * 0.2

    def test_항목이_모자라면_점수를_만들지_않는다(self):
        from apt_engine.scoring import early_alpha
        a = early_alpha.compute(1, _fs(band_shift_strength=0.8,
                                       buyer_pool=0.7))
        assert a.alpha is None
        assert "최소" in a.calc.formula

    def test_Alpha_Risk_Confidence_를_합치지_않는다(self):
        """§36."""
        from apt_engine.scoring import early_alpha
        a = early_alpha.compute(1, self._fs_full())
        assert a.alpha is not None
        assert a.confidence is not None
        assert "Expected Alpha" in a.label and "Confidence" in a.label

    def test_DataQuality_는_Alpha_가_아니라_Confidence_를_움직인다(self):
        from apt_engine.scoring import early_alpha
        plain = early_alpha.compute(1, self._fs_full())
        confirmed = early_alpha.compute(
            1, self._fs_full(neighbour_confirmation=1.0))
        assert confirmed.confidence > plain.confidence
        assert confirmed.alpha == pytest.approx(plain.alpha, rel=0.35)

    def test_감점_Feature_가_ALPHA_와_겹치지_않는다(self):
        """§45."""
        from apt_engine.scoring import early_alpha
        alpha_inputs = {k for keys in early_alpha.MULTIPLIERS.values()
                        for k in keys}
        penalty_inputs = set(early_alpha.PENALTIES.values())
        assert alpha_inputs & penalty_inputs == set()

    def test_이미_반영된_정도를_모르면_남은_알파를_내지_않는다(self):
        """§20 — 0 으로 두면 확실해질수록 점수가 계속 오른다."""
        from apt_engine.scoring import early_alpha
        a = early_alpha.compute(1, self._fs_full())
        value, why = early_alpha.remaining_alpha(a, already_priced_in=None)
        assert value is None
        assert "계속 오릅니다" in why

    def test_이미_반영됐으면_남은_알파가_준다(self):
        from apt_engine.scoring import early_alpha
        a = early_alpha.compute(1, self._fs_full())
        early, _ = early_alpha.remaining_alpha(a, already_priced_in=0.1)
        late, _ = early_alpha.remaining_alpha(a, already_priced_in=0.8)
        assert late < early


# ── §35 Normal Executable Price ──────────────────────────────────────

class TestNormalExecutablePrice:
    def test_보정할_수_없으면_보정했다고_하지_않는다(self):
        from apt_engine.price import normalize as norm
        r = norm.normal_executable_price(500_000_000)
        assert r.price == 500_000_000
        assert len(r.skipped) == 3
        assert r.calc.grade == "ESTIMATED"

    def test_한_달_표본으로_가격을_크게_흔들지_않는다(self):
        """§35 — 3건짜리 최근 표본으로 10% 올리면 정규화가 아니라 노이즈다."""
        from apt_engine.price import normalize as norm
        a = norm.direction([200, 210, 205], [100, 100, 100, 100])
        assert a.applied
        assert a.factor <= 1 + norm.MAX_DIRECTION_ADJUST
        assert "상한 적용" in a.why

    def test_표본이_적으면_방향성을_안_본다(self):
        from apt_engine.price import normalize as norm
        a = norm.direction([200], [100, 100, 100])
        assert not a.applied
        assert "표본 부족" in a.why

    def test_타입_격차를_모르면_0_으로_가정하지_않는다(self):
        """0 으로 두면 A타입과 B타입이 한 가격으로 눌린다."""
        from apt_engine.price import normalize as norm
        assert not norm.type_normalize(None).applied

    def test_급매가_많으면_그_가격에_살_수_있다고_본다(self):
        """§35 urgent-sale absorption."""
        from apt_engine.price import normalize as norm
        absorbed = norm.urgent_absorption(4, 10)     # 40%
        rare = norm.urgent_absorption(1, 100)        # 1%
        assert absorbed.factor == 1.0
        assert rare.factor > 1.0
        assert "흡수하고 있어" in absorbed.why

    def test_급매_건수를_모르면_보정하지_않는다(self):
        from apt_engine.price import normalize as norm
        assert not norm.urgent_absorption(None, 10).applied


# ── §34 Naked Apartment Value ────────────────────────────────────────

class TestNakedValue:
    def _peers(self, n=4, redev=False):
        from apt_engine.redev import naked
        return [naked.Peer(i, 7_000_000, 1992, redev) for i in range(n)]

    def test_비교단지가_모자라면_추정하지_않는다(self):
        from apt_engine.redev import naked
        v = naked.naked_value(area_m2=84, peers=self._peers(2), own_year=1990)
        assert not v.known
        assert "추정하지 않습니다" in v.reason

    def test_재건축_기대가_있는_단지는_비교에_쓰지_않는다(self):
        from apt_engine.redev import naked
        v = naked.naked_value(area_m2=84, peers=self._peers(5, redev=True),
                              own_year=1990)
        assert not v.known

    def test_연식이_너무_다르면_비교에서_뺀다(self):
        from apt_engine.redev import naked
        far = [naked.Peer(i, 7_000_000, 2020, False) for i in range(5)]
        v = naked.naked_value(area_m2=84, peers=far, own_year=1985)
        assert not v.known

    def test_할인율을_모르면_현재가치로_환산하지_않는다(self):
        """0% 로 두면 20년 뒤 5억이 지금 5억이 되어 모든 노후 단지가 좋아 보인다."""
        from apt_engine.redev import naked
        v = naked.naked_value(area_m2=84, peers=self._peers(), own_year=1990)
        p = naked.premium_efficiency(
            current_price=900_000_000, naked=v,
            expected_gross_value=400_000_000, years_to_completion=15,
            discount_rate=None)
        assert not p.known
        assert "모든 노후 단지가 좋아 보입니다" in p.reason

    def test_프리미엄이_기대가치보다_크면_비싸다고_말한다(self):
        from apt_engine.redev import naked
        v = naked.naked_value(area_m2=84, peers=self._peers(), own_year=1990)
        p = naked.premium_efficiency(
            current_price=900_000_000, naked=v,
            expected_gross_value=400_000_000, years_to_completion=15,
            discount_rate=0.05)
        assert p.efficiency < 1.0
        assert "이미 비쌉니다" in p.verdict

    def test_시간이_길수록_효율이_떨어진다(self):
        from apt_engine.redev import naked
        v = naked.naked_value(area_m2=84, peers=self._peers(), own_year=1990)
        kw = dict(current_price=900_000_000, naked=v,
                  expected_gross_value=600_000_000, discount_rate=0.05)
        soon = naked.premium_efficiency(years_to_completion=5, **kw)
        late = naked.premium_efficiency(years_to_completion=25, **kw)
        assert soon.efficiency > late.efficiency

    def test_서사에는_점수를_주지_않는다(self):
        """§34·§49-9 — 재건축·GTX 이니까 좋다는 없다."""
        from apt_engine.redev import naked
        value, detail = naked.catalyst_paths({"BuyerPool": None,
                                              "Accessibility": None})
        assert value is None
        assert "서사에는 점수를 주지 않습니다" in detail["사유"]

    def test_경로가_설명되면_점수가_난다(self):
        from apt_engine.redev import naked
        value, detail = naked.catalyst_paths({"Accessibility": 0.8,
                                              "BuyerPool": 0.6})
        assert value is not None
        assert len(detail["설명 안 된 경로"]) == 2

    def test_경로가_적게_설명될수록_점수가_낮다(self):
        from apt_engine.redev import naked
        few, _ = naked.catalyst_paths({"Accessibility": 0.8})
        many, _ = naked.catalyst_paths({k: 0.8
                                        for k in naked.TRANSMISSION_PATHS})
        assert few < many


# ── §37·§46 DELTA 파이프라인 조립 ────────────────────────────────────

class TestDeltaPipeline:
    def _run(self, conn, **kw):
        from apt_engine.blind import cutoff as cutoff_mod
        from apt_engine.invest.budget import Profile
        from apt_engine.ranking import delta_pipeline as delta
        from apt_engine.ranking import pipeline as bp
        profile = Profile(name="t", available_cash=900_000_000,
                          cash_hurdle_rate=kw.pop("hurdle", 0.03))
        profile.save(conn)
        return delta.run(conn, as_of=cutoff_mod.AsOf("2024-06-01"),
                         profile=profile, gate=bp.GATE_PRICE_ONLY, **kw)

    def test_기대수익을_못_내면_EXECUTABLE_에_안_들어간다(self, db):
        """§46 — 모르면 YES 가 아니다.

        전에는 `returns` 가 비었을 때 검사를 건너뛰어서, 아무 후보도 점수가
        없을 때 **전부 통과**했다. 동시에 CASH 가 1위로 표시되는 모순이 났다.
        """
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=12, start_ym="201501",
                            end_ym="202412", rule=synth_mod.MEAN_REVERT)
            r = self._run(conn, limit=5)
        scored = [c for c in r.candidates if c.alpha.known]
        if not scored:
            assert r.split.executable == [], (
                "Alpha 를 하나도 못 냈는데 EXECUTABLE 에 후보가 있습니다")

    def test_CASH_순위와_EXECUTABLE_이_모순되지_않는다(self, db):
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=12, start_ym="201501",
                            end_ym="202412", rule=synth_mod.MEAN_REVERT)
            r = self._run(conn, limit=5)
        if r.split.cash_rank == 1:
            assert r.split.executable == [], (
                "CASH 가 1위인데 그 위에 후보가 있습니다")

    def test_다_못_봤으면_제목이_PARTIAL_이다(self, db):
        """§43·§49-13."""
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=30, start_ym="201501",
                            end_ym="202412")
            r = self._run(conn, scan_limit=5, limit=5)
        assert "PARTIAL" in r.title

    def test_가중치가_임시면_그_사실이_표시된다(self, db):
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=8, start_ym="201501",
                            end_ym="202412")
            r = self._run(conn, limit=5)
        assert "가중치 임시" in r.summary

    def test_CASH_기준선이_없으면_경고한다(self, db):
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=8, start_ym="201501",
                            end_ym="202412")
            r = self._run(conn, hurdle=None, limit=5)
        assert any("CASH 기준선이 없어" in n for n in r.notes)

    def test_정렬에_이름이_들어가지_않는다(self, db):
        """§1·§33 — 동점도 id 로 깬다."""
        import pathlib
        from apt_engine.ranking import delta_pipeline as delta
        src = pathlib.Path(delta.__file__).read_text(encoding="utf-8")
        assert "c.name" not in src and '"name"' not in src

    def test_상세에_핵심_지표가_전부_나온다(self, db):
        """§48-J."""
        from apt_engine.ranking import delta_pipeline as delta
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=8, start_ym="201501",
                            end_ym="202412")
            r = self._run(conn, limit=5)
        assert r.candidates
        text = delta.detail(r.candidates[0])
        for label in ("Normal Executable Price", "매수가 구간", "Stage",
                      "실투자금", "Price Stretch", "Money Arrival Depth",
                      "Latent / Visible"):
            assert label in text, f"상세에 '{label}' 이 없습니다"


# ── §28·§29·§31 시점별 Sanity Test ───────────────────────────────────

class TestSanity:
    def test_실거래가_없으면_통과로_치지_않는다(self, db):
        """후보 0개가 나오면 '보수적이라 통과' 로 읽힐 수 있다."""
        from apt_engine.backtest import sanity
        with get_conn(db) as conn:
            ok, why = sanity.data_available(conn, "2021-01-01")
        assert not ok
        assert "실거래가 없습니다" in why

    def test_그_시점_정책이_없으면_검사하지_않는다(self, db):
        """정책이 없으면 실투자금이 확인 불가라 아무도 게이트를 못 통과한다."""
        from apt_engine.backtest import sanity
        with get_conn(db) as conn:
            conn.execute(
                "INSERT INTO trade (lawd_cd, apt_name, exclusive_area_m2, "
                " area_band, deal_amount, deal_ymd) "
                "VALUES ('11110','합성',84.0,'84',500000000,'20201201')")
            ok, why = sanity.data_available(conn, "2021-01-01")
        assert not ok
        assert "대출 규칙이 없습니다" in why
        assert "보수적이라 통과" in why

    def test_후보가_0개면_판정_불가다(self, db):
        from apt_engine.backtest import sanity
        with get_conn(db) as conn:
            c = sanity.check(conn, "2021-01-01", sanity.REVERSE,
                             run_fn=lambda *_: (0, 0))
        assert c.passed is None

    def test_과열기에_BUY_가_많으면_실패다(self, db):
        """§28 — MoneyArrival = BUY 로 단순 판단하지 않는지."""
        from apt_engine.backtest import sanity
        with get_conn(db) as conn:
            conn.execute(
                "INSERT INTO trade (lawd_cd, apt_name, exclusive_area_m2, "
                " area_band, deal_amount, deal_ymd) "
                "VALUES ('11110','합성',84.0,'84',500000000,'20201201')")
            conn.execute(
                "INSERT INTO loan_rule (rule_key, effective_from, "
                " source_name, verification, rule_type, value) "
                "VALUES ('t','2015-01-01','테스트','VERIFIED','LTV',0.4)")
            greedy = sanity.check(conn, "2021-01-01", sanity.REVERSE,
                                  run_fn=lambda *_: (8, 10))
            careful = sanity.check(conn, "2021-01-01", sanity.REVERSE,
                                   run_fn=lambda *_: (1, 10))
        assert greedy.passed is False
        assert "MoneyArrival 만 보고" in greedy.detail
        assert careful.passed is True

    def test_2021_만_통과하는_모델은_2019_에서_걸린다(self, db):
        """§29 — 전부 CASH 라고 하면 2021 은 통과하지만 2019 에서 실패한다."""
        from apt_engine.backtest import sanity
        with get_conn(db) as conn:
            conn.execute(
                "INSERT INTO trade (lawd_cd, apt_name, exclusive_area_m2, "
                " area_band, deal_amount, deal_ymd) "
                "VALUES ('11110','합성',84.0,'84',500000000,'20161201')")
            conn.execute(
                "INSERT INTO loan_rule (rule_key, effective_from, "
                " source_name, verification, rule_type, value) "
                "VALUES ('t','2015-01-01','테스트','VERIFIED','LTV',0.4)")
            never_buy = sanity.check(conn, "2017-01-01", sanity.OPPORTUNITY,
                                     run_fn=lambda *_: (0, 10))
        assert never_buy.passed is False
        assert "좋은 기회까지 CASH 로 흘렸습니다" in never_buy.detail

    def test_하나라도_판정불가면_전체가_판정불가다(self):
        """통과한 것만 세면 '2021 은 통과했다' 로 읽히고 그건 반쪽이다."""
        from apt_engine.backtest import sanity
        r = sanity.SanityReport([
            sanity.Check("2021-01-01", sanity.REVERSE, True, 0.1, 10, ""),
            sanity.Check("2019-01-01", sanity.OPPORTUNITY, None, None, 0, "",
                         "데이터 없음"),
        ])
        assert r.all_passed is None
        assert "판정 불가" in r.summary

    def test_시점별_가중치를_따로_만들지_않는다고_말한다(self):
        """§29·§49-14."""
        from apt_engine.backtest import sanity
        r = sanity.SanityReport([
            sanity.Check("2021-01-01", sanity.REVERSE, True, 0.1, 10, ""),
            sanity.Check("2019-01-01", sanity.OPPORTUNITY, False, 0.02, 10, ""),
        ])
        assert r.all_passed is False
        assert "시점별로 가중치를 따로 만들지 않습니다" in r.summary

    def test_True_Blind_가_아니라고_항상_표시한다(self):
        """§28."""
        from apt_engine.backtest import sanity
        r = sanity.SanityReport([])
        assert "True Blind Test 가 아닙니다" in r.summary


# ── §44 CORE 승격 ────────────────────────────────────────────────────

class TestCorePromotion:
    def test_낮을수록_좋은_Feature_의_IC_를_뒤집어_본다(self):
        """§44 — 방향을 안 맞추면 CORE 승격이 정확히 반대로 돈다.

        `price_stretch` 는 낮을수록 좋으므로 원시 IC 가 음수여야 정상이다.
        보정 없이 보면 '낮을수록 좋은' Feature 12개가 통째로 HARMFUL 로 나온다.
        """
        from apt_engine.backtest import usefulness as u
        assert u._orientation("price_stretch") == -1.0
        assert u._orientation("band_shift_strength") == 1.0
        assert u._orientation("모르는_피처") == 1.0

    def test_한_분할에서만_좋으면_CORE_가_아니다(self, db):
        """§44 — 한 시기에서만 잘 맞는 Feature 는 Diagnostic 에 둔다."""
        from apt_engine.backtest import usefulness as u
        from apt_engine.features import registry as reg
        with get_conn(db) as conn:
            reg.sync(conn)
            promoted, _ = u.promote_core(
                conn, run_key="t",
                per_split={"TRAIN": [u.Usefulness("price_stretch", "TRAIN",
                                                  0.3, 0.6, 10, u.USEFUL,
                                                  effective_n=5)],
                           "VALIDATION": []})
        assert promoted == []

    def test_두_분할을_살아남으면_CORE_로_올라간다(self, db):
        from apt_engine.backtest import usefulness as u
        from apt_engine.features import registry as reg
        with get_conn(db) as conn:
            reg.sync(conn)
            promoted, _ = u.promote_core(
                conn, run_key="t",
                per_split={
                    "TRAIN": [u.Usefulness("price_stretch", "TRAIN", 0.3, 0.6,
                                           10, u.USEFUL, effective_n=5)],
                    "VALIDATION": [u.Usefulness("price_stretch", "VALIDATION",
                                                0.3, 0.6, 8, u.USEFUL,
                                                effective_n=4)]})
            assert promoted == ["price_stretch"]
            assert reg.core_keys(conn) == ["price_stretch"]

    def test_다음_실행에서_못_살아남으면_강등된다(self, db):
        from apt_engine.backtest import usefulness as u
        from apt_engine.features import registry as reg
        good = {"TRAIN": [u.Usefulness("price_stretch", "TRAIN", 0.3, 0.6, 10,
                                       u.USEFUL, effective_n=5)],
                "VALIDATION": [u.Usefulness("price_stretch", "VALIDATION", 0.3,
                                            0.6, 8, u.USEFUL, effective_n=4)]}
        with get_conn(db) as conn:
            reg.sync(conn)
            u.promote_core(conn, run_key="t1", per_split=good)
            _, demoted = u.promote_core(conn, run_key="t2",
                                        per_split={"TRAIN": [], "VALIDATION": []})
            assert demoted == ["price_stretch"]
            assert reg.core_keys(conn) == []

    def test_등록부에_없는_키는_승격_대상이_아니다(self, db):
        from apt_engine.backtest import usefulness as u
        from apt_engine.features import registry as reg
        entry = u.Usefulness("모르는_피처", "TRAIN", 0.3, 0.6, 10, u.USEFUL,
                             effective_n=5)
        with get_conn(db) as conn:
            reg.sync(conn)
            promoted, _ = u.promote_core(
                conn, run_key="t",
                per_split={"TRAIN": [entry], "VALIDATION": [entry]})
        assert promoted == []


# ── §4-C·§33 수요 쪽 Feature ─────────────────────────────────────────

class TestDemandFeatures:
    def _market(self, n=10, price=500_000_000, households=500, sample=8):
        from apt_engine.features import demand
        return demand.Market([
            demand.Cohort(i, int(price * (1 + (i - n / 2) * 0.02)),
                          households, sample, "11110")
            for i in range(1, n + 1)])

    def test_동급이_모자라면_BuyerPool_을_만들지_않는다(self):
        from apt_engine.features import demand
        f = demand.buyer_pool(self._market(2), price=500_000_000,
                              lawd_cd="11110", households=500, sample_n=8)
        assert f.value is None
        assert "표본이 없습니다" in f.detail["사유"]

    def test_대리지표라고_말하고_신뢰도에_상한을_둔다(self):
        from apt_engine.features import demand
        f = demand.buyer_pool(self._market(), price=500_000_000,
                              lawd_cd="11110", households=500, sample_n=8)
        assert f.usable
        assert f.confidence <= demand.PROXY_CONFIDENCE_CAP
        assert "근사한 값" in f.detail["주의"]

    def test_세대수를_모르면_거래건수만으로_안_센다(self):
        """거래건수만 보면 큰 단지가 무조건 유리해진다."""
        from apt_engine.features import demand
        market = demand.Market([demand.Cohort(i, 500_000_000, None, 8, "11110")
                                for i in range(1, 8)])
        f = demand.buyer_pool(market, price=500_000_000, lawd_cd="11110",
                              households=None, sample_n=8)
        # 시장 형성 정도만으로는 계산되지만 활성도는 빠진다
        assert "거래 활성도" not in f.detail.get("구성", {})

    def test_공급을_하나도_모르면_0_으로_두지_않는다(self):
        from apt_engine.features import demand
        f = demand.effective_supply_risk({})
        assert f.value is None
        assert "0 으로 두지 않습니다" in f.detail["사유"]

    def test_가까운_공급에_더_큰_무게를_준다(self):
        from apt_engine.features import demand
        near = demand.effective_supply_risk({"supply_ratio_1y": 0.08})
        far = demand.effective_supply_risk({"supply_ratio_5y": 0.08})
        assert near.value > far.value

    def test_공급_절벽이면_위험을_낮춘다(self):
        from apt_engine.features import demand
        plain = demand.effective_supply_risk({"supply_ratio_2y": 0.08})
        cliff = demand.effective_supply_risk({"supply_ratio_2y": 0.08},
                                             cliff=0.8)
        assert cliff.value < plain.value
        assert "공급 절벽" in cliff.detail

    def test_공급_감점이_한_곳으로만_나간다(self):
        """§45 — 전에는 supply_ratio 가 ALPHA 모델과 Kill 양쪽에 있었다."""
        from apt_engine.features import demand, registry as reg
        f = demand.effective_supply_risk({"supply_ratio_1y": 0.05})
        assert "§45 중복 금지" in f.detail["주의"]
        for key in ("supply_ratio_1y", "supply_ratio_2y", "supply_ratio_3y",
                    "supply_ratio_5y"):
            assert reg.get(key).role == reg.ROLE_CONTEXT

    def test_대체재가_많으면_감점이다(self):
        from apt_engine.features import demand
        many = demand.replacement_availability(
            self._market(20), price=500_000_000, required_equity=None,
            lawd_cd="11110")
        few = demand.replacement_availability(
            self._market(3), price=500_000_000, required_equity=None,
            lawd_cd="11110")
        assert many.value > few.value
        assert "많을수록 감점" in many.detail["주의"]
        assert registry.get("replacement_availability").higher_is_better is False


# ── §11·§12 Leader 망 자동 생성 ──────────────────────────────────────

class TestLeaderBuilder:
    def _nodes(self):
        from apt_engine.relative import leaders
        return [
            leaders.Node(1, 500_000_000, "11110", "강남권", "84", 10),
            leaders.Node(2, 600_000_000, "11110", "강남권", "84", 20),
            leaders.Node(3, 700_000_000, "11110", "강남권", "84", 5),
            leaders.Node(4, 650_000_000, "41110", "경기권", "84", 30),
            leaders.Node(5, 480_000_000, "11110", "강남권", "84", 8),
        ]

    def test_더_싼_단지는_Leader_가_아니다(self):
        from apt_engine.relative import leaders
        nodes = self._nodes()
        links = leaders.pick_leaders(nodes[0], nodes)
        assert all(l.leader_id != 5 for l in links)

    def test_너무_비싸면_다른_시장이라_제외한다(self):
        from apt_engine.relative import leaders
        follower = leaders.Node(1, 100_000_000, "11110", "강남권", "84", 10)
        huge = leaders.Node(9, 2_000_000_000, "11110", "강남권", "84", 10)
        assert not leaders._in_lead_range(follower, huge)

    def test_다섯_종류가_서로_다른_규칙이다(self):
        """한 규칙으로 다섯 개를 만들면 이름만 다섯 개다."""
        from apt_engine.relative import leaders
        nodes = self._nodes()
        links = leaders.pick_leaders(nodes[0], nodes)
        kinds = {l.kind for l in links}
        assert len(kinds) >= 3
        by_kind = {l.kind: l.leader_id for l in links}
        # 가장 가까이 위(PRICE)와 가장 비싼 곳(METRO)이 달라야 한다
        if leaders.PRICE in by_kind and leaders.METRO in by_kind:
            assert by_kind[leaders.PRICE] != by_kind[leaders.METRO]

    def test_다른_생활권이면_겹침이_낮다(self):
        from apt_engine.relative import leaders
        nodes = self._nodes()
        same, _ = leaders.buyer_overlap(nodes[0], nodes[1])
        other, _ = leaders.buyer_overlap(nodes[0], nodes[3])
        assert same > other

    def test_면적이_다르면_겹치지_않는다(self):
        from apt_engine.relative import leaders
        a = leaders.Node(1, 500_000_000, "11110", "강남권", "84", 10)
        b = leaders.Node(2, 600_000_000, "11110", "강남권", "59", 10)
        overlap, _ = leaders.buyer_overlap(a, b)
        same, _ = leaders.buyer_overlap(
            a, leaders.Node(3, 600_000_000, "11110", "강남권", "84", 10))
        assert overlap < same

    def test_겹침은_대리지표라_상한이_있다(self):
        from apt_engine.relative import leaders
        a = leaders.Node(1, 500_000_000, "11110", "강남권", "84", 10)
        b = leaders.Node(2, 550_000_000, "11110", "강남권", "84", 10)
        overlap, _ = leaders.buyer_overlap(a, b)
        assert overlap <= leaders.MAX_PROXY_OVERLAP

    def test_저장한_링크를_같은_시점에_읽을_수_있다(self, db):
        """as_of 를 요청일로 저장하면 컷오프 때문에 자기가 쓴 걸 못 읽는다.

        실제로 링크 109개를 쓰고 하나도 못 읽었다.
        """
        from apt_engine.features import leader as leader_mod
        from apt_engine.relative import leaders
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=12, start_ym="202001",
                            end_ym="202412")
            as_of = cutoff_mod.AsOf("2024-06-01")
            result = leaders.build(conn, as_of=as_of, area_band="84")
            assert result["링크"] > 0
            # 링크가 붙은 팔로워를 골라 읽는다. 최상위 단지는 Leader 가
            # 없는 것이 정상이라 그걸로 검사하면 의미가 없다.
            follower = conn.execute(
                "SELECT follower_id FROM leader_link LIMIT 1").fetchone()[0]
            got = leader_mod.load_leaders(conn, follower, "84", as_of=as_of)
        assert got, "방금 쓴 Leader 링크를 같은 시점에 못 읽습니다"

    def test_Leader_가_없으면_관련_Feature_가_전부_확인_불가다(self, db):
        """Leader 를 못 찾은 것과 안 따라온 것은 다른 상태다."""
        from apt_engine.ranking import delta_pipeline as delta
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=3, start_ym="202001",
                            end_ym="202412")
            feats, leader_label = delta._leader_features(
                conn, 1, "84", as_of=cutoff_mod.AsOf("2024-06-01"))
        assert leader_label is None, "Leader 가 없는데 라벨이 붙었습니다"
        keys = {f.key for f in feats}
        assert keys == {"transmission_failure", "recoverable_discount_ratio",
                        "leader_exhaustion", "next_node_score"}
        for f in feats:
            assert f.value is None
            assert "다릅니다" in f.detail["사유"] or "사다리" in f.detail["사유"]


# ── §61·§62·§64 회전 · 순위변경 설명 · 전체 컬럼 ─────────────────────

class TestRankChange:
    def test_점수는_그대로인데_밀렸으면_남이_움직인_것이다(self):
        """§64 — 이 후보가 나빠진 것과 다른 후보가 좋아진 것은 다른 사건이다."""
        from apt_engine.ranking import rotation
        changes = rotation.explain(
            previous={1: (3, 70.0, 60.0)},
            current={1: (11, 69.5, 60.0)})
        assert changes[0].cause == rotation.OTHERS_MOVED
        assert "다른 후보들이 변한 것입니다" in changes[0].detail

    def test_점수가_떨어졌으면_이_후보가_나빠진_것이다(self):
        from apt_engine.ranking import rotation
        changes = rotation.explain(
            previous={1: (3, 70.0, 60.0)},
            current={1: (11, 55.0, 60.0)})
        assert changes[0].cause == rotation.SELF_WORSE

    def test_신뢰도만_크게_바뀌었으면_우리가_더_알게_된_것이다(self):
        """후보가 변한 게 아니라 우리가 변한 것이다."""
        from apt_engine.ranking import rotation
        changes = rotation.explain(
            previous={1: (3, 70.0, 30.0)},
            current={1: (12, 71.0, 75.0)})
        assert changes[0].cause == rotation.MORE_KNOWN
        assert "후보가 변한 게 아닙니다" in rotation.CAUSE_LABEL[changes[0].cause]

    def test_한두_칸_흔들림은_변화로_보지_않는다(self):
        from apt_engine.ranking import rotation
        changes = rotation.explain(previous={1: (3, 70.0, 60.0)},
                                   current={1: (4, 70.0, 60.0)})
        assert changes[0].cause == rotation.UNCHANGED

    def test_탈락한_후보의_사유가_남는다(self):
        """§65."""
        from apt_engine.ranking import rotation
        changes = rotation.explain(
            previous={1: (3, 70.0, 60.0)}, current={},
            dropped_reasons={1: "공급충격 — 2년 입주물량 12%"})
        assert changes[0].cause == rotation.DROPPED
        assert "공급충격" in changes[0].label

    def test_사유가_없으면_없다고_말한다(self):
        from apt_engine.ranking import rotation
        changes = rotation.explain(previous={1: (3, 70.0, None)}, current={})
        assert "기록되지 않았습니다" in changes[0].detail


class TestRotation:
    def test_거래비용을_모르면_회전을_판정하지_않는다(self):
        """§61 — 0 으로 두면 순위가 한 칸만 높아도 회전하라는 답이 나온다."""
        from apt_engine.ranking import rotation
        r = rotation.rotation(holding_id=1, holding_return=0.10,
                              candidate_id=2, candidate_return=0.15,
                              sell_cost_ratio=None, buy_cost_ratio=0.05)
        assert r.worth_it is None
        assert "매도비용" in r.reason
        assert "한 칸만 높아도" in r.reason

    def test_비용을_넘지_못하면_회전이_아니다(self):
        from apt_engine.ranking import rotation
        r = rotation.rotation(holding_id=1, holding_return=0.10,
                              candidate_id=2, candidate_return=0.15,
                              sell_cost_ratio=0.06, buy_cost_ratio=0.05)
        assert r.worth_it is False
        assert "그냥 순위 차이입니다" in r.label

    def test_비용을_넘으면_회전할_만하다(self):
        from apt_engine.ranking import rotation
        r = rotation.rotation(holding_id=1, holding_return=0.10,
                              candidate_id=2, candidate_return=0.40,
                              sell_cost_ratio=0.06, buy_cost_ratio=0.05)
        assert r.worth_it is True


class TestTopColumns:
    def test_지시서가_요구한_컬럼이_있다(self):
        """§62 — TOP10 화면에 무엇이 나와야 하는가."""
        from apt_engine.ranking import rotation
        keys = {k for k, _ in rotation.COLUMNS}
        for need in ("stage", "price", "strong_buy", "do_not_buy", "alpha",
                     "risk", "confidence", "required_equity",
                     "recoverable_gap", "price_stretch", "money_depth",
                     "rank_change", "coverage"):
            assert need in keys, f"§62 컬럼 '{need}' 이 없습니다"

    def test_없는_값은_확인_불가로_나온다(self, db):
        from apt_engine.ranking import delta_pipeline as delta
        from apt_engine.ranking import rotation
        from apt_engine.invest.budget import Profile
        from apt_engine.ranking import pipeline as bp
        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=8, start_ym="202001",
                            end_ym="202412")
            profile = Profile(name="t", available_cash=900_000_000)
            profile.save(conn)
            r = delta.run(conn, as_of=cutoff_mod.AsOf("2024-06-01"),
                          profile=profile, gate=bp.GATE_PRICE_ONLY, limit=3)
        assert r.candidates
        row = rotation.row_of(r.candidates[0], rank=1)
        assert row["rank"] == 1
        # 데이터가 없는 항목은 반드시 '확인 불가' 이지 0 이 아니다
        assert all(v != 0 for v in row.values())
        assert "확인 불가" in row.values()


# ── §51·§52·§53 순위 불확실성 ────────────────────────────────────────

class TestUncertainty:
    def test_신뢰도가_낮으면_구간이_넓다(self):
        """§36·§52 — '점수는 높은데 근거가 약한' 후보의 구간이 넓어야 한다."""
        from apt_engine.ranking import uncertainty as u
        sim = u.rank_ranges({1: 80.0, 2: 80.0, 3: 50.0, 4: 20.0},
                            {1: 95.0, 2: 20.0, 3: 95.0, 4: 95.0})
        assert sim.ranges[2].spread > sim.ranges[1].spread

    def test_구간이_넓으면_불안정하다고_말한다(self):
        from apt_engine.ranking import uncertainty as u
        sim = u.rank_ranges({i: 50.0 for i in range(1, 11)},
                            {i: 5.0 for i in range(1, 11)})
        assert sim.unstable
        assert "우연에 가깝습니다" in sim.summary

    def test_불확실성을_줄이는_게_아니라_드러낸다고_말한다(self):
        from apt_engine.ranking import uncertainty as u
        assert "드러내는" in u.NOTE

    def test_후보가_하나면_구간을_만들지_않는다(self):
        from apt_engine.ranking import uncertainty as u
        assert u.rank_ranges({1: 50.0}, {1: 90.0}).ranges == {}

    def test_직전_실행이_없으면_지속성을_내지_않는다(self):
        """§51."""
        from apt_engine.ranking import uncertainty as u
        p = u.persistence([], [1, 2, 3])
        assert not p.known
        assert "직전 실행이 없습니다" in p.reason

    def test_매번_크게_바뀌면_모델이_흔들리는_것이다(self):
        from apt_engine.ranking import uncertainty as u
        p = u.persistence([1, 2, 3, 4, 5], [6, 7, 8, 9, 10], k=5)
        assert p.stable is False
        assert "모델이 흔들리는" in p.label

    def test_변동성을_모르면_시뮬레이션하지_않는다(self):
        """§53 — 임의의 변동성을 넣으면 그 숫자가 결과를 지배한다."""
        from apt_engine.ranking import uncertainty as u
        assert u.monte_carlo(expected_return=0.3, volatility=None) is None
        assert u.monte_carlo(expected_return=None, volatility=0.2) is None

    def test_전세_방어력이_있으면_하방이_얕다(self):
        from apt_engine.ranking import uncertainty as u
        weak = u.monte_carlo(expected_return=0.0, volatility=0.3,
                             downside_defense=0.0)
        strong = u.monte_carlo(expected_return=0.0, volatility=0.3,
                               downside_defense=0.9)
        assert strong.p10 > weak.p10
        assert strong.p90 == pytest.approx(weak.p90, rel=0.01), (
            "전세는 하방만 받쳐야 합니다 — 상방을 올리면 §14 위반입니다")


# ── §30·§31 Capital Frontier · 대안매수 ──────────────────────────────

class _FakeAlpha:
    def __init__(self, value):
        self.alpha = value
        self.known = value is not None


class _FakeCand:
    def __init__(self, cid, alpha, required):
        self.complex_id = cid
        self.alpha = _FakeAlpha(alpha)
        self.required_equity = required


class _FakeSplit:
    def __init__(self, cands):
        self.executable = cands


class _FakeResult:
    def __init__(self, cands):
        self.split = _FakeSplit(cands)


class TestFrontier:
    def test_문턱을_찾아낸다(self):
        """§30 — '얼마를 더 모아야 하는가' 에 답할 수 있어야 한다."""
        from apt_engine.ranking import frontier as fr
        results = {
            200_000_000: _FakeResult([_FakeCand(1, 40.0, 200_000_000)]),
            250_000_000: _FakeResult([_FakeCand(1, 40.5, 200_000_000)]),
            300_000_000: _FakeResult([_FakeCand(9, 65.0, 300_000_000)]),
        }
        f = fr.build(results)
        assert len(f.thresholds) == 1
        assert f.thresholds[0].to.cash == 300_000_000
        assert "문턱" in f.summary

    def test_문턱이_없으면_없다고_말한다(self):
        from apt_engine.ranking import frontier as fr
        results = {c: _FakeResult([_FakeCand(1, 40.0, c)])
                   for c in (200_000_000, 250_000_000, 300_000_000)}
        assert fr.build(results).thresholds == []
        assert "뚜렷한 문턱이 없습니다" in fr.build(results).summary

    def test_후보가_없는_버킷을_0점으로_세지_않는다(self):
        from apt_engine.ranking import frontier as fr
        f = fr.build({200_000_000: _FakeResult([]),
                      300_000_000: _FakeResult([_FakeCand(1, 60.0,
                                                          300_000_000)])})
        assert f.rungs[0].best_score is None
        assert "확인 불가" in f.rungs[0].label

    def test_금액이_아니라_자기자본_대비로_비교한다(self):
        """§31 — 금액으로 비교하면 비싼 것이 항상 이긴다."""
        from apt_engine.ranking import frontier as fr
        cheap = _FakeCand(1, 60.0, 200_000_000)
        pricey = _FakeCand(2, 62.0, 400_000_000)
        better, why = fr.alternative_purchase(cheap, pricey,
                                              cash=400_000_000)
        # 자기자본 4억 기준: 싼 쪽 0.60×2/4=30%, 비싼 쪽 0.62×4/4=62%
        assert better is True
        # 자기자본이 2억이면 비싼 쪽은 애초에 못 산다 → 배치가 달라진다
        assert "자기자본 대비" in why

    def test_더_비싼_걸_사는_것과_더_나은_투자를_구분한다(self):
        from apt_engine.ranking import frontier as fr
        cheap = _FakeCand(1, 60.0, 300_000_000)
        pricey = _FakeCand(2, 60.5, 300_000_000)
        better, why = fr.alternative_purchase(cheap, pricey,
                                              cash=300_000_000)
        assert better is False
        assert "더 나은 투자가 아닙니다" in why

    def test_실투자금을_모르면_비교하지_않는다(self):
        from apt_engine.ranking import frontier as fr
        better, why = fr.alternative_purchase(
            _FakeCand(1, 60.0, None), _FakeCand(2, 70.0, 300_000_000),
            cash=300_000_000)
        assert better is None
        assert "비싼 것이 항상 이깁니다" in why

    def test_현금_주변_버킷만_돌린다(self):
        from apt_engine.ranking import frontier as fr
        near = fr.default_buckets(300_000_000)
        assert 1_000_000_000 not in near
        assert 300_000_000 in near


# ── 등록부와 생산의 일치 ─────────────────────────────────────────────

class TestRegistryCoverage:
    def test_등록된_Feature_가_실제로_생산된다(self, db):
        """등록만 하고 만들지 않으면 EarlyAlpha 의 곱셈 항이 조용히 빈다.

        가격사다리가 필요한 `money_arrival_depth` 만 예외다 — 그건 종인님이
        `ladder import` 로 넣어야 하는 데이터다.
        """
        from apt_engine.features import registry as reg
        from apt_engine.invest.budget import Profile
        from apt_engine.ranking import delta_pipeline as delta
        from apt_engine.ranking import pipeline as bp
        from apt_engine.relative import leaders

        with get_conn(db) as conn:
            synth_mod.build(conn, n_complexes=20, start_ym="201501",
                            end_ym="202412", rule=synth_mod.MEAN_REVERT)
            as_of = cutoff_mod.AsOf("2024-06-01")
            leaders.build(conn, as_of=as_of, area_band="84")
            profile = Profile(name="t", available_cash=900_000_000,
                              cash_hurdle_rate=0.03)
            profile.save(conn)
            r = delta.run(conn, as_of=as_of, profile=profile,
                          gate=bp.GATE_PRICE_ONLY, limit=3)

        produced = set(r.candidates[0].features.items)
        needs_ladder = {"money_arrival_depth"}
        never = set(reg.REGISTRY) - produced - needs_ladder
        assert never == set(), (
            f"등록만 되고 생산되지 않는 Feature: {sorted(never)}. "
            f"등록부에서 빼거나 생산 경로를 만드세요")


class TestNewFeatures:
    def _series(self, samples, price=100):
        pts = [bands.BandPoint(f"{2024 - i // 12:04d}{12 - i % 12:02d}",
                               int(price * 0.95), price, int(price * 1.06), n)
               for i, n in enumerate(samples)]
        return bands.BandSeries(1, "84", pts)

    def test_거래회복은_매수신호가_아니라고_말한다(self):
        """§49-5 — 거래량 증가만으로 가산점 금지."""
        f = bands.transaction_recovery(self._series([10] * 6 + [4] * 12))
        assert f.usable
        assert "거래량 증가만으로" in f.detail["주의"]

    def test_이전_구간_거래가_0_이면_비율을_만들지_않는다(self):
        f = bands.transaction_recovery(self._series([10] * 6 + [0] * 12))
        assert f.value is None

    def test_중앙값이_P75_에_붙으면_소진이다(self):
        tight = bands.BandSeries(1, "84", [
            bands.BandPoint("202412", 95, 100, 100, 9)])
        loose = bands.BandSeries(1, "84", [
            bands.BandPoint("202412", 95, 100, 115, 9)])
        assert (bands.distribution_exhaustion(tight).value >
                bands.distribution_exhaustion(loose).value)

    def test_정상가를_모르면_싼_기간을_세지_않는다(self):
        """기준선 없이 '싸다'는 말이 성립하지 않는다."""
        from apt_engine.features import stretch as st
        months, why = st.months_cheap(
            self._series([8] * 30), st.NormalPrice(None, 0, reason="없음"))
        assert months is None

    def test_전세가율_이력이_없으면_절대수준으로_대체하지_않는다(self):
        """절대 수준은 지역마다 달라 그대로 쓸 수 없다."""
        from apt_engine.features import stretch as st
        f = st.price_to_jeonse_stretch(None, 0.55, history=None)
        assert f.value is None
        assert "절대 수준은 지역마다" in f.detail["사유"]

    def test_전세가_뒤처지면_stretch_가_커진다(self):
        from apt_engine.features import stretch as st
        wide = st.price_to_jeonse_stretch(None, 0.45, history=[0.60] * 24)
        tight = st.price_to_jeonse_stretch(None, 0.59, history=[0.60] * 24)
        assert wide.value > tight.value

    def test_Leader_가_꼭대기면_따라갈_자리가_없다고_말한다(self):
        """Follower 논리의 전제가 무너진다."""
        from apt_engine.features import leader as lm
        rising = bands.BandSeries(1, "84", [
            bands.BandPoint(f"{2024 - i // 12:04d}{12 - i % 12:02d}",
                            95, 200 - i * 3, 210, 8) for i in range(24)])
        f = lm.leader_exhaustion(rising)
        assert f.value > 0.85
        assert "따라갈 자리가 없습니다" in f.detail["해석"]

    def test_실투자금을_모르면_같은자본_비교를_안_한다(self):
        from apt_engine.features import demand
        market = demand.Market([demand.Cohort(i, 500_000_000, 500, 8, "11110")
                                for i in range(1, 8)])
        f = demand.same_capital_value(market, price=500_000_000,
                                      required_equity=None,
                                      capital=300_000_000)
        assert f.value is None

    def test_이웃_확인은_이미_계산된_값에서_읽는다(self):
        """이웃마다 시계열을 다시 로드하면 후보 수의 제곱만큼 쿼리가 나간다."""
        from apt_engine.features import demand
        sets = {
            1: _fs(band_shift_strength=0.9),
            2: _fs(band_shift_strength=0.8),
            3: _fs(band_shift_strength=0.1),
        }
        regions = {1: "11110", 2: "11110", 3: "11110"}
        f = demand.neighbour_confirmation_from(sets, complex_id=1,
                                               regions=regions)
        assert f.value == pytest.approx(0.5)      # 이웃 2개 중 1개가 움직임

    def test_다른_생활권_이웃은_안_센다(self):
        from apt_engine.features import demand
        sets = {1: _fs(band_shift_strength=0.9),
                2: _fs(band_shift_strength=0.9)}
        f = demand.neighbour_confirmation_from(
            sets, complex_id=1, regions={1: "11110", 2: "41110"})
        assert f.value is None


# ── 목표수익률 역산 매수가 — "얼마 이하에서 사야 하는가" ─────────────

class TestTargetPrice:
    def _linear(self, exit_value=130_000_000, cost=1.10, years=5):
        """비싸게 살수록 수익률이 낮아지는 단순 모델."""
        def ret(price):
            return (exit_value - price * cost) / price / years
        return ret

    def test_목표가_높을수록_매수가가_낮아진다(self):
        from apt_engine.reverse import target_price as tp
        low = tp.solve(target_return=0.05, current_price=100_000_000,
                       return_at=self._linear())
        high = tp.solve(target_return=0.15, current_price=100_000_000,
                        return_at=self._linear())
        assert low.known and high.known
        assert high.price < low.price

    def test_도달_불가능하면_싸게_사면_된다고_하지_않는다(self):
        """0원에 사도 안 되는 경우가 있다 — 보유비용이 기대 상승을 넘을 때."""
        from apt_engine.reverse import target_price as tp
        # 탐색 하한(현재가의 30% = 3천만원)에 사도 매도가가 비용에 못 미친다
        r = tp.solve(target_return=0.10, current_price=100_000_000,
                     return_at=self._linear(exit_value=20_000_000, cost=1.10))
        assert not r.known
        assert "이 목표로는 살 수 없습니다" in r.reason

    def test_수익률을_못_내면_가격을_지어내지_않는다(self):
        from apt_engine.reverse import target_price as tp
        r = tp.solve(target_return=0.07, current_price=100_000_000,
                     return_at=lambda p: None)
        assert not r.known
        assert "계산하지 못했습니다" in r.reason

    def test_단조성이_깨지면_그_사실을_남긴다(self):
        """싸게 살수록 수익률이 낮게 나오면 결과를 그대로 믿으면 안 된다."""
        from apt_engine.reverse import target_price as tp
        r = tp.solve(target_return=0.05, current_price=100_000_000,
                     return_at=lambda p: p / 1_000_000_000)   # 비쌀수록 좋음
        assert any("단조성 가정이 깨졌" in n for n in r.notes)

    def test_지금_살_수_있는지_말한다(self):
        from apt_engine.reverse import target_price as tp
        cheap = tp.solve(target_return=0.02, current_price=100_000_000,
                         return_at=self._linear())
        assert cheap.buyable_now is True
        assert "지금 가능" in cheap.label

    def test_사다리는_목표별로_가격을_준다(self):
        from apt_engine.reverse import target_price as tp
        rungs = tp.ladder(targets=(0.05, 0.10), current_price=100_000_000,
                          return_at=self._linear())
        assert len(rungs) == 2
        assert rungs[0].price > rungs[1].price

    def test_시장이_요구수익률을_안_주면_말한다(self):
        from apt_engine.reverse import target_price as tp
        from apt_engine.ranking import executable as ex
        rungs = tp.ladder(targets=(0.30,), current_price=100_000_000,
                          return_at=self._linear())
        bands_obj = ex.PriceBands(50_000_000, 90_000_000, 150_000_000,
                                  100_000_000)
        msgs = tp.compare_with_bands(rungs, bands_obj)
        assert any("요구수익률을 주지 않습니다" in m for m in msgs)
