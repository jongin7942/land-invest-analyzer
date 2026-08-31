"""Phase 8 — walk-forward 백테스트 하네스 (지시서 §55·§56·§57·§71·§72·§74).

이 테스트들이 지키려는 것은 "백테스트가 돈다" 가 아니라
**"백테스트가 거짓말을 하지 못한다"** 이다. 그래서 절반이 실패 경로다:
누출을 심고 잡히는지, 신호가 없는 시장에서 아무것도 배우지 않는지,
표본이 없을 때 숫자를 만들지 않는지.
"""
import sqlite3

import pytest

from apt_engine.backtest import kpi as kpi_mod
from apt_engine.backtest import leakage as leakage_mod
from apt_engine.backtest import outcome as outcome_mod
from apt_engine.backtest import runner as runner_mod
from apt_engine.backtest import synthetic as synth_mod
from apt_engine.backtest import usefulness as useful_mod
from apt_engine.backtest import windows as win_mod
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.invest import buckets as bucket_mod
from apt_engine.invest.budget import Profile
from apt_engine.ranking import pipeline as pipeline_mod


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    return tmp_db


def _market(conn, rule=synth_mod.MOMENTUM, n=30, start="201501", end="202512"):
    return synth_mod.build(conn, n_complexes=n, start_ym=start, end_ym=end,
                           rule=rule)


def _run(conn, market, *, run_key="t", start="2015-01-01", end="2025-12-01",
         horizons=(2,), step=6, buckets=(900_000_000,), **kw):
    return runner_mod.run(
        conn, run_key=run_key, data_start=start, data_end=end,
        profile=Profile(name="bt", available_cash=600_000_000),
        horizons=horizons, step_months=step, cash_buckets=buckets,
        market_source=runner_mod.SYNTHETIC,
        gate=pipeline_mod.GATE_PRICE_ONLY, **kw)


# ── §72 시간 분할 ────────────────────────────────────────────────────

class TestWindows:
    def test_분할은_시간순이고_겹치지_않는다(self):
        ws = win_mod.generate("2015-01-01", "2026-01-01", horizons=(2,))
        assert win_mod.overlaps(ws) == []
        bounds = win_mod.boundary(ws)
        assert bounds["TRAIN"][1] < bounds["VALIDATION"][0]
        assert bounds["VALIDATION"][1] < bounds["OOT"][0]

    def test_정답이_아직_없는_창은_버리지_않고_사유를_남긴다(self):
        ws = win_mod.generate("2015-01-01", "2020-01-01", horizons=(5,))
        unscorable = [w for w in ws if not w.scorable]
        assert unscorable, "5년 horizon 이면 끝쪽 창은 채점할 수 없어야 한다"
        for w in unscorable:
            assert "정답이 존재하지 않습니다" in w.skip_reason

    def test_겹친_창을_독립_관측으로_세지_않는다(self):
        ws = win_mod.generate("2015-01-01", "2026-01-01", horizons=(2,),
                              step_months=3)
        scorable = [w for w in ws if w.scorable]
        assert win_mod.independent_count(scorable) < len(scorable) / 4, (
            "3개월 간격 × 2년 보유면 독립 관측은 창 수의 1/8 근처여야 한다")

    def test_정답구간이_다음_분할을_침범하면_보고한다(self):
        ws = win_mod.generate("2015-01-01", "2026-01-01", horizons=(5,))
        assert win_mod.embargo_conflicts(ws), (
            "5년 horizon 이면 TRAIN 의 정답이 VALIDATION 구간을 덮는다")

    def test_purge_하면_침범이_사라진다(self):
        ws = win_mod.generate("2015-01-01", "2026-01-01", horizons=(2,))
        kept = win_mod.purge(ws)
        assert len(kept) < len(ws)
        train_bad = [w for w in kept if w.split == win_mod.TRAIN and w.scorable
                     and w.eval_day > win_mod.boundary(ws)["VALIDATION"][0]]
        assert train_bad == []

    def test_검정력을_미리_말해_준다(self):
        ws = win_mod.generate("2015-01-01", "2026-01-01", horizons=(5,))
        lines = win_mod.power_report(ws)
        assert any("부족" in line for line in lines), (
            "11년 데이터로 5년 보유를 검증할 독립 관측은 나오지 않는다")


# ── §55 정답지 격리 ──────────────────────────────────────────────────

class TestAnswerKeyIsolation:
    def test_컷오프_안에서는_정답지를_못_읽는다(self, db):
        with get_conn(db) as conn:
            with cutoff_mod.guard(conn, cutoff_mod.AsOf("2020-01-01")) as g:
                with pytest.raises(cutoff_mod.LookAheadError, match="정답지"):
                    g.execute("SELECT * FROM backtest_outcome WHERE window_id=1")

    def test_조건을_붙여도_못_읽는다(self, db):
        """날짜 조건을 붙이면 통과하는 다른 테이블과 다르다 — 정답지는 무조건 거부."""
        with get_conn(db) as conn:
            with cutoff_mod.guard(conn, cutoff_mod.AsOf("2020-01-01")) as g:
                with pytest.raises(cutoff_mod.LookAheadError):
                    g.execute("SELECT * FROM weight_fit WHERE fitted_at <= ?",
                              ("2020-01-01",))

    def test_결정_경로_코드가_정답지를_언급하지_않는다(self):
        assert leakage_mod.scan_sources() == []


# ── §55·§69 누출 감지 — 일부러 심어서 잡히는지 본다 ──────────────────

class TestLeakageDetection:
    def _decide(self, as_of):
        def go(conn):
            result = pipeline_mod.run(
                conn, as_of=as_of,
                profile=Profile(name="bt", available_cash=900_000_000),
                horizon_years=2, area_band="84",
                gate=pipeline_mod.GATE_PRICE_ONLY)
            return [(i, c.complex_id, c.score)
                    for i, c in enumerate(result.top10, 1)]
        return go

    def test_누출이_없으면_미래를_지워도_결정이_같다(self, db):
        with get_conn(db) as conn:
            _market(conn, n=12, start="201801", end="202312")
            as_of = cutoff_mod.AsOf("2021-01-01")
            found = leakage_mod.compare_decisions(conn, as_of, self._decide(as_of))
        assert found == [], f"누출이 없는데 잡혔습니다: {[f.label for f in found]}"

    def test_미래를_심으면_잡는다(self, db):
        """이 테스트가 실패하면 누출 검사는 아무 것도 보장하지 않는다."""
        with get_conn(db) as conn:
            market = _market(conn, n=12, start="201801", end="202312")
            as_of = cutoff_mod.AsOf("2021-01-01")
            clean = leakage_mod.compare_decisions(conn, as_of, self._decide(as_of))
            assert clean == []

            # 컷오프 **이후** 가격을 부풀린다. 정상이라면 결정이 안 변해야 한다.
            synth_mod.plant_leak(conn, market, as_of_ym="202101", boost=3.0)

            def leaky(conn_):
                # 일부러 컷오프를 무시하고 미래 가격으로 순위를 매긴다
                rows = conn_.execute(
                    "SELECT complex_id, AVG(representative_price) p "
                    "  FROM price_snapshot GROUP BY complex_id "
                    " ORDER BY p DESC").fetchall()
                return [(i, int(r[0]), float(r[1]))
                        for i, r in enumerate(rows, 1)]

            found = leakage_mod.compare_decisions(conn, as_of, leaky)
        assert found, "미래를 읽는 코드를 잡지 못했습니다"
        assert "이후 데이터를 읽고 있습니다" in found[0].detail

    def test_누출이_있으면_실행이_무효가_된다(self, db):
        with get_conn(db) as conn:
            market = _market(conn, n=10, start="201801", end="202312")
            result = _run(conn, market, run_key="bad", start="2018-01-01",
                          end="2023-12-01", run_leakage_audit=False)
            row = conn.execute(
                "SELECT status, invalid_reason FROM backtest_run WHERE run_key='bad'"
            ).fetchone()
        assert result.status == "INVALID"
        assert row["status"] == "INVALID"
        assert "누출 검사를 하지 않았습니다" in row["invalid_reason"]

    def test_검사를_통과하지_않으면_COMPLETE_가_될_수_없다(self, db):
        """구조로 막는다 — 코드가 실수해도 스키마가 거부한다."""
        with get_conn(db) as conn:
            conn.execute(
                "INSERT INTO backtest_run (run_key, engine_version, data_start, "
                " data_end, step_months, horizons_json, buckets_json, top_k, "
                " market_source) VALUES ('x','0',	'2020-01-01','2021-01-01',"
                " 6,'[2]','[1]',10,'SYNTHETIC')")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE backtest_run SET status='COMPLETE' "
                             " WHERE run_key='x'")


# ── §33~§41 정답 계산 ────────────────────────────────────────────────

class TestOutcome:
    def _window(self, as_of="2020-01-01", horizon=2):
        return win_mod.Window(as_of, horizon,
                              win_mod.add_years(as_of, horizon),
                              win_mod.TRAIN, True)

    def test_가격이_없으면_추정하지_않는다(self, db):
        with get_conn(db) as conn:
            _market(conn, n=3, start="201801", end="201912")
            out = outcome_mod.compute(conn, 1, "84", window=self._window())
        assert not out.known
        assert out.unknown_reason
        assert out.forward_return is None

    def test_확인_불가는_0_으로_세지_않는다(self, db):
        known = outcome_mod.Outcome(1, "84", forward_return=0.30)
        unknown = outcome_mod.Outcome(2, "84", unknown_reason="가격 없음")
        ranked = outcome_mod.classify([known, unknown], {1})
        by_id = {o.complex_id: o for o in ranked}
        assert by_id[2].ex_post_rank is None
        assert by_id[2].winner_class is None
        assert by_id[1].ex_post_rank == 1

    def test_고른_것과_실제_결과의_조합이_4상태다(self):
        outs = [outcome_mod.Outcome(i, "84", forward_return=r)
                for i, r in enumerate([0.5, 0.4, 0.1, -0.1, -0.2], start=1)]
        ranked = outcome_mod.classify(outs, {1, 5}, top_fraction=0.4)
        got = {o.complex_id: o.winner_class for o in ranked}
        assert got[1] == outcome_mod.WINNER_FOUND      # 골랐고 실제로 좋았다
        assert got[2] == outcome_mod.MISSED_WINNER     # 좋았는데 놓쳤다
        assert got[5] == outcome_mod.FALSE_POSITIVE    # 골랐는데 나빴다
        assert got[3] == outcome_mod.CORRECT_REJECT

    def test_낙폭은_시작가가_아니라_고점_대비다(self):
        series = [("202001", 100), ("202002", 150), ("202003", 90),
                  ("202004", 160)]
        mdd, trough = outcome_mod._drawdown(series)
        assert trough == "202003"
        assert mdd == pytest.approx((90 - 150) / 150)

    def test_회복하지_못했으면_개월수를_만들지_않는다(self):
        series = [("202001", 100), ("202002", 60), ("202003", 70)]
        months, recovered = outcome_mod._recovery(series, "202002")
        assert months is None
        assert recovered is False        # '모른다(None)' 와 다르다

    def test_정답을_못_내면_스키마가_사유를_요구한다(self, db):
        with get_conn(db) as conn:
            _market(conn, n=2, start="202001", end="202012")
            conn.execute(
                "INSERT INTO backtest_run (run_key, engine_version, data_start, "
                " data_end, step_months, horizons_json, buckets_json, top_k, "
                " market_source) VALUES ('r','0','2020-01-01','2021-01-01',6,"
                " '[2]','[1]',10,'SYNTHETIC')")
            conn.execute(
                "INSERT INTO backtest_window (run_id, as_of, horizon_years, "
                " eval_day, split) VALUES (1,'2020-01-01',2,'2022-01-01','TRAIN')")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO backtest_outcome (window_id, complex_id, "
                    " area_band) VALUES (1, 1, '84')")


# ── §57 KPI ──────────────────────────────────────────────────────────

class TestKpi:
    def test_지시서가_요구한_KPI_가_전부_있다(self):
        """§57 의 14종 + DELTA §26 의 5종."""
        assert len(set(kpi_mod.KPI_KEYS)) == len(kpi_mod.KPI_KEYS), "중복"
        required = {
            # §57
            "winner_recall_at_k", "precision_at_k", "false_positive_rate",
            "false_follower_rate", "regret", "opportunity_alpha",
            "ex_post_capital_rank", "median_forward_return", "hit_rate",
            "rank_ic", "max_drawdown", "recovery_months", "discovery_lag",
            "coverage",
            # DELTA §26
            "missed_better_alternative_rate", "cash_accuracy",
            "after_cost_return", "after_interest_return", "after_tax_return",
        }
        assert required <= set(kpi_mod.KPI_KEYS), (
            f"빠진 KPI: {sorted(required - set(kpi_mod.KPI_KEYS))}")

    def test_모든_KPI_에_이름과_단위가_있다(self):
        for key in kpi_mod.KPI_KEYS:
            assert key in kpi_mod.KPI_LABEL
            assert key in kpi_mod.KPI_UNIT
            assert key in kpi_mod.HIGHER_IS_BETTER

    def test_표본이_없으면_숫자를_만들지_않는다(self):
        outs = [outcome_mod.Outcome(1, "84", unknown_reason="가격 없음")]
        kpis = kpi_mod.compute_window(outs, picked_order=[], scores={})
        by_key = {k.key: k for k in kpis}
        assert by_key["winner_recall_at_k"].value is None
        assert by_key["winner_recall_at_k"].note
        assert by_key["coverage"].value == 0.0

    def test_Regret_은_최선_대비라_음수가_될_수_없다(self):
        outs = [outcome_mod.Outcome(i, "84", forward_return=r)
                for i, r in enumerate([0.5, 0.2, 0.1], start=1)]
        ranked = outcome_mod.classify(outs, {2})
        kpis = {k.key: k for k in kpi_mod.compute_window(
            ranked, picked_order=[2], scores={})}
        assert kpis["regret"].value == pytest.approx(0.3)

    def test_순위상관은_동점을_평균순위로_본다(self):
        assert kpi_mod.rank_ic({1: 1, 2: 1, 3: 2},
                       {1: 5, 2: 5, 3: 9}) == pytest.approx(1.0)

    def test_전부_동점이면_상관을_정의하지_않는다(self):
        assert kpi_mod.rank_ic({1: 1, 2: 1, 3: 1}, {1: 5, 2: 6, 3: 7}) is None

    def test_표본이_있어야_값이_저장된다(self, db):
        """스키마 CHECK: value 가 있으면 sample_n > 0 이어야 한다."""
        with get_conn(db) as conn:
            conn.execute(
                "INSERT INTO backtest_run (run_key, engine_version, data_start, "
                " data_end, step_months, horizons_json, buckets_json, top_k, "
                " market_source) VALUES ('r','0','2020-01-01','2021-01-01',6,"
                " '[2]','[1]',10,'SYNTHETIC')")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO backtest_kpi (run_id, kpi_key, value, sample_n) "
                    "VALUES (1, 'hit_rate', 1.0, 0)")


# ── §27 현금 버킷 ────────────────────────────────────────────────────

class TestBuckets:
    def test_지시서가_정한_아홉개다(self):
        assert len(bucket_mod.BUCKETS) == 9
        assert bucket_mod.BUCKETS[0] == 200_000_000
        assert bucket_mod.BUCKETS[-1] == 1_000_000_000

    def test_구간_밖이면_억지로_넣지_않는다(self):
        assert bucket_mod.nearest(100_000_000) == 0

    def test_버킷을_올릴_때_무엇이_바뀌는지_말한다(self):
        steps = bucket_mod.frontier({2: [1, 2], 3: [2, 3, 4]},
                                    {2: 60.0, 3: 65.0})
        assert len(steps) == 1
        assert steps[0].gained == [3, 4]
        assert steps[0].lost == [1]
        assert steps[0].best_score_delta == pytest.approx(5.0)

    def test_점수를_모르면_변화를_지어내지_않는다(self):
        steps = bucket_mod.frontier({2: [1], 3: [2]}, {2: None, 3: 65.0})
        assert steps[0].best_score_delta is None
        assert "확인 불가" in steps[0].label


# ── §74 Feature usefulness → Weight ──────────────────────────────────

class TestUsefulness:
    def test_겹친_창을_표본으로_부풀리지_않는다(self):
        ws = [win_mod.Window(f"20{y:02d}-01-01", 2, f"20{y + 2:02d}-01-01",
                             win_mod.TRAIN, True) for y in range(10, 20)]
        assert useful_mod.effective_n(ws) == 5     # 2년 간격으로 5개만 독립

    def test_부호가_뒤집히면_평균내지_않고_0_으로_둔다(self):
        train = [useful_mod.Usefulness("value", "TRAIN", 0.30, 0.6, 10,
                                       useful_mod.USEFUL, effective_n=5)]
        val = [useful_mod.Usefulness("value", "VALIDATION", -0.20, 0.4, 5,
                                     useful_mod.HARMFUL, effective_n=3)]
        got = useful_mod.confirm(train, val)
        assert got["value"].verdict == useful_mod.NEUTRAL
        assert "부호가 뒤집혔습니다" in got["value"].note

    def test_VALIDATION_에서_확인되지_않으면_가중치를_주지_않는다(self):
        train = [useful_mod.Usefulness("value", "TRAIN", 0.30, 0.6, 10,
                                       useful_mod.USEFUL, effective_n=5)]
        got = useful_mod.confirm(train, [])
        assert got["value"].verdict == useful_mod.INSUFFICIENT
        w, notes = useful_mod.fit_weights(got)
        assert w.source == "HEURISTIC"
        assert any("heuristic 을 유지" in n for n in notes)

    def test_근거가_있으면_학습하고_출처를_남긴다(self):
        got = {"momentum": useful_mod.Usefulness(
            "momentum", "TRAIN", 0.30, 0.6, 10, useful_mod.USEFUL,
            effective_n=5)}
        w, notes = useful_mod.fit_weights(got)
        assert w.source == "BACKTESTED"
        assert w.values["momentum"] == pytest.approx(1.0)
        assert any("기존 생각을 버린다" in n for n in notes), (
            "heuristic 이 크게 줬던 모델을 0 으로 내렸으면 그 사실을 말해야 한다")

    def test_합성_가중치는_실전_랭킹이_읽지_않는다(self, db):
        with get_conn(db) as conn:
            conn.execute(
                "INSERT INTO backtest_run (run_key, engine_version, data_start, "
                " data_end, step_months, horizons_json, buckets_json, top_k, "
                " market_source) VALUES ('r','0','2020-01-01','2021-01-01',6,"
                " '[2]','[1]',10,'SYNTHETIC')")
            conn.execute(
                "INSERT INTO weight_fit (run_id, model_key, weight, sample_n, "
                " market_source) VALUES (1,'momentum',1.0,5,'SYNTHETIC')")
            assert useful_mod.load_weights(conn, market_source="REAL") is None
            assert useful_mod.load_weights(conn, market_source="SYNTHETIC")


# ── 합성 시장 자체의 규칙 ────────────────────────────────────────────

class TestSyntheticGuards:
    def test_실제_DB_에는_쓸_수_없다(self):
        import config
        with pytest.raises(synth_mod.SyntheticDataError):
            synth_mod.require_scratch(config.APT_DB_PATH)

    def test_임시_DB_는_허용된다(self, db):
        synth_mod.require_scratch(db)
        synth_mod.require_scratch(":memory:")

    def test_합성_시장은_실제가_아니라고_말한다(self, db):
        with get_conn(db) as conn:
            market = _market(conn, n=5, start="202001", end="202112")
        assert "실제 시세가 아닙니다" in market.label

    def test_같은_seed_면_같은_시장이_나온다(self, tmp_path):
        prices = []
        for i in range(2):
            path = str(tmp_path / f"m{i}.db")
            mig.migrate(path)
            with get_conn(path) as conn:
                _market(conn, n=5, start="202001", end="202012")
                prices.append([tuple(r) for r in conn.execute(
                    "SELECT complex_id, as_of_ym, representative_price "
                    "  FROM price_snapshot ORDER BY complex_id, as_of_ym")])
        assert prices[0] == prices[1]


# ── 전체 구동 ────────────────────────────────────────────────────────

class TestRunner:
    def test_끝까지_돌고_유효_판정을_받는다(self, db):
        with get_conn(db) as conn:
            market = _market(conn, n=12, start="201801", end="202312")
            result = _run(conn, market, start="2018-01-01", end="2023-12-01")
        assert result.status == "COMPLETE"
        assert result.scored_windows
        assert len(result.aggregate) == len(kpi_mod.KPI_KEYS)

    def test_합성_시장이라는_사실이_항상_붙어_다닌다(self, db):
        with get_conn(db) as conn:
            market = _market(conn, n=10, start="201801", end="202312")
            result = _run(conn, market, start="2018-01-01", end="2023-12-01")
        assert "실제 성과로 읽지 마세요" in result.summary

    def test_채점하지_못한_창은_사유가_남는다(self, db):
        with get_conn(db) as conn:
            market = _market(conn, n=8, start="201801", end="202312")
            _run(conn, market, start="2018-01-01", end="2023-12-01",
                 horizons=(5,), run_key="skip")
            rows = conn.execute(
                "SELECT status, skip_reason FROM backtest_window "
                " WHERE status='SKIPPED'").fetchall()
        assert rows
        for r in rows:
            assert r["skip_reason"]

    def test_고른_것만이_아니라_후보_전체를_채점한다(self, db):
        with get_conn(db) as conn:
            market = _market(conn, n=12, start="201801", end="202312")
            _run(conn, market, start="2018-01-01", end="2023-12-01",
                 run_key="all", top_k=3)
            row = conn.execute(
                "SELECT COUNT(*) picked, "
                "       (SELECT COUNT(*) FROM backtest_outcome) total "
                "  FROM backtest_outcome WHERE picked=1").fetchone()
        assert row["total"] > row["picked"], (
            "고른 것만 채점하면 Regret 도 Missed Winner 도 계산할 수 없다")

    def test_신호가_없는_시장에서는_아무것도_배우지_않는다(self, db):
        """가장 중요한 테스트 — 없는 신호를 찾아내면 그 엔진은 위험하다."""
        with get_conn(db) as conn:
            market = _market(conn, rule=synth_mod.NONE, n=60,
                             start="199601", end="202601")
            result = _run(conn, market, start="1996-01-01", end="2026-01-01",
                          step=3, run_key="null")
        assert result.fitted is not None
        assert result.fitted.source == "HEURISTIC", (
            f"신호가 없는 시장에서 가중치를 학습했습니다: {result.fitted.values}")

    def test_시장이_바뀌면_학습_결과도_바뀐다(self, tmp_path):
        """같은 코드가 항상 같은 답을 내면 그건 데이터를 읽는 게 아니다."""
        learned = {}
        for rule in (synth_mod.MOMENTUM, synth_mod.MEAN_REVERT):
            path = str(tmp_path / f"{rule}.db")
            mig.migrate(path)
            with get_conn(path) as conn:
                market = _market(conn, rule=rule, n=60,
                                 start="199601", end="202601")
                result = _run(conn, market, start="1996-01-01",
                              end="2026-01-01", step=3, run_key=rule)
            learned[rule] = {k: v for k, v in result.fitted.values.items()
                             if v > 0}

        assert learned[synth_mod.MOMENTUM].get("momentum", 0) > 0, (
            f"추세 시장에서 momentum 을 못 찾았습니다: {learned}")
        assert learned[synth_mod.MEAN_REVERT].get("value", 0) > 0, (
            f"가치 시장에서 value 를 못 찾았습니다: {learned}")
        assert learned[synth_mod.MOMENTUM] != learned[synth_mod.MEAN_REVERT]
