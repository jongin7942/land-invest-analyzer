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
