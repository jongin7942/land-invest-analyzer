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

        names = (set(control.RESEARCH_CANDIDATES)
                 | set(control.CONTROL_TRAP_CANDIDATES)
                 | set(control.TOO_LATE_CANDIDATES)
                 | set(control.REVERSE_SANITY_2021))
        base = pathlib.Path(control.__file__).resolve().parent.parent
        offenders = []
        for package in ("features", "scoring", "ranking", "blind", "invest"):
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
