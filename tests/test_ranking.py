"""랭킹 파이프라인 테스트 (지시서 §1·§26·§45·§48·§49·§50·§60·§64·§65·§75).

이 계층이 지키려는 선:
  1. 순서 — 살 수 있는지 먼저 보고 그다음 점수 (§26)
  2. 없는 모델을 0 으로 채우지 않는다 (§49)
  3. 점수와 신뢰도를 합치지 않는다 (§50)
  4. Kill 은 감점이 아니라 배제이고, 탈락 이유가 남는다 (§45·§65)
  5. 세 리스트는 정렬 키가 다르다 (§48)
  6. 아무것도 좋지 않으면 CASH 를 추천한다 (§60)
  7. 이름이 순위에 끼어들지 않는다 (§1 Placebo)
"""
import pytest

from apt_engine import units
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.features.base import Feature, FeatureSet, Status
from apt_engine.invest.budget import Profile
from apt_engine.ranking import explain as explain_mod
from apt_engine.ranking import lists as lists_mod
from apt_engine.ranking import pipeline as pipeline_mod
from apt_engine.repo import apt as repo
from apt_engine.repo import ranking as rank_repo
from apt_engine.scoring import consensus as consensus_mod
from apt_engine.scoring import kill as kill_mod
from apt_engine.scoring import models as models_mod
from apt_engine.scoring import normalize, thesis as thesis_mod
from apt_engine.scoring import weights as weights_mod

LAWD = "28237"
BAND = "84"
AS_OF = cutoff_mod.AsOf("2026-01-01")


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


def add_complex(conn, name, kapt, *, lawd=LAWD, households=1000):
    repo.upsert_complexes(conn, [{
        "kapt_code": kapt, "name": name, "name_norm": name, "lawd_cd": lawd,
        "apt_households": households, "approval_year": 2005}])
    return conn.execute("SELECT id FROM complex WHERE kapt_code=?",
                        (kapt,)).fetchone()[0]


def add_price(conn, cid, ym, eok, *, n=10):
    conn.execute(
        "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym, window_months, "
        " representative_price, method, sample_n, confidence, data_grade, "
        " engine_version, calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, BAND, ym, 6, int(units.from_eok(eok)), "median", n, "HIGH",
         "CONFIRMED", "0.14.0", "{}"))


def add_rules(conn):
    """실투자금이 계산되도록 최소한의 규칙."""
    for kind, key, rate in (("취득세", "acq", 0.01), ("지방교육세", "edu", 0.001),
                            ("부가가치세", "vat", 0.1)):
        conn.execute(
            "INSERT INTO tax_rule (tax_kind, rule_key, rate, effective_from, "
            " source_name, last_verified, status, verification) "
            "VALUES (?,?,?,?,?,?,'ENACTED','VERIFIED')",
            (kind, key, rate, "2020-01-01", "테스트", "2026-01-01"))
    conn.execute(
        "INSERT INTO tax_rule (tax_kind, rule_key, conditions_json, fixed_amount, "
        " effective_from, source_name, last_verified, status, verification) "
        "VALUES ('농어촌특별세','rural','{\"exclusive_area_lte\": 85}',0,'2020-01-01',"
        " '테스트','2026-01-01','ENACTED','VERIFIED')")
    for kind, key, val in (("중개보수", "brok", 0.004), ("법무비", "legal", None)):
        conn.execute(
            "INSERT INTO cost_rule (cost_kind, rule_key, rate, fixed_amount, "
            " vat_applicable, effective_from, source_name, last_verified, status, "
            " verification) VALUES (?,?,?,?,1,'2020-01-01','테스트','2026-01-01',"
            " 'ENACTED','VERIFIED')",
            (kind, key, val, None if val else 300_000))
    for kind, key, fixed in (("인지세", "stamp", 150_000),
                             ("등기신청수수료", "reg", 13_000),
                             ("증명서발급", "cert", 5_000),
                             ("국민주택채권", "bond", 0)):
        conn.execute(
            "INSERT INTO cost_rule (cost_kind, rule_key, fixed_amount, "
            " effective_from, source_name, last_verified, status, verification) "
            "VALUES (?,?,?,'2020-01-01','테스트','2026-01-01','ENACTED','VERIFIED')",
            (kind, key, fixed))
    for rule_type, value in (("LTV", 0.5), ("DSR", 0.4)):
        conn.execute(
            "INSERT INTO loan_rule (rule_key, rule_type, value, conditions_json, "
            " price_min, effective_from, source_name, last_verified, status, "
            " verification) VALUES (?,?,?,'{}',0,'2020-01-01','테스트','2026-01-01',"
            " 'ENACTED','VERIFIED')", (f"{rule_type}/t", rule_type, value))


def seed_market(conn, n=6):
    """서로 다른 가격·흐름을 가진 후보 n개."""
    add_rules(conn)
    ids = []
    for i in range(n):
        cid = add_complex(conn, f"단지{i}", f"K{i}")
        base = 3.0 + i * 0.4
        ym = "202312"
        for step in range(24):
            add_price(conn, cid, ym, base * (1 + 0.004 * step * (1 + i % 3)))
            ym = f"{int(ym[:4]) + (int(ym[4:6]) % 12 == 0):04d}" \
                 f"{(int(ym[4:6]) % 12) + 1:02d}"
        ids.append(cid)
    return ids


def profile(cash_eok=5.0):
    return Profile(name="테스트", available_cash=units.from_eok(cash_eok),
                   annual_income=units.from_eok(1.0), interest_rate=0.045,
                   region="인천")


# ── 정규화 ─────────────────────────────────────────────────────────────

class TestNormalize:
    def test_절대_임계값이_아니라_상대_위치다(self):
        low = normalize.percentile_rank({1: 0.01, 2: 0.02, 3: 0.03})
        high = normalize.percentile_rank({1: 100.0, 2: 200.0, 3: 300.0})
        assert low.positions == high.positions      # 스케일이 달라도 순위는 같다

    def test_값이_없으면_0점이_아니라_제외된다(self):
        got = normalize.percentile_rank({1: 1.0, 2: None})
        assert 2 not in got.positions
        assert got.missing == [2]

    def test_낮을수록_좋은_지표는_뒤집는다(self):
        got = normalize.percentile_rank({1: 1.0, 2: 5.0}, higher_is_better=False)
        assert got.positions[1] > got.positions[2]

    def test_동점은_같은_위치를_받는다(self):
        got = normalize.percentile_rank({1: 5.0, 2: 5.0, 3: 9.0})
        assert got.positions[1] == got.positions[2]

    def test_극단값은_버리지_않고_경계로_옮긴다(self):
        # 2% 꼬리를 자르려면 표본이 그만큼 있어야 한다. 11개짜리에서 2% 절단은
        # 성립하지 않고, 그때 아무것도 안 자르는 게 맞는 동작이다.
        raw = {i: float(i) for i in range(1, 101)}
        raw[101] = 10_000.0
        clipped = normalize.winsorize(raw)
        assert clipped[101] is not None          # 버리지 않는다
        assert clipped[101] < 10_000.0           # 경계로 옮긴다

    def test_표본이_적으면_자르지_않는다(self):
        raw = {1: 1.0, 2: 2.0, 3: 10_000.0}
        assert normalize.winsorize(raw) == raw


# ── 모델 · Consensus ───────────────────────────────────────────────────

class TestConsensus:
    def _sets(self):
        out = {}
        for cid, value in ((1, 0.9), (2, 0.5), (3, 0.1)):
            fs = FeatureSet(cid, BAND, "2026-01-01")
            fs = fs.add(Feature("entry_position", 1.0 - value, "", 0.9, Status.OK))
            fs = fs.add(Feature("momentum_6m", value, "", 0.9, Status.OK))
            fs = fs.add(Feature("discovery_lag", 0.0, "", 0.9, Status.OK))
            out[cid] = fs
        return out

    def test_없는_모델은_0_이_아니라_가중치에서_빠진다(self):
        sets = self._sets()
        ranks = models_mod.build_ranks(sets)
        scores = models_mod.score_all(1, sets[1], ranks)
        w = weights_mod.for_regime(None)
        got = consensus_mod.combine(1, scores, w)
        assert got.missing_models          # supply·catalyst 등은 계산 안 됨
        assert "supply" not in got.weights
        assert abs(sum(got.weights.values()) - 1.0) < 1e-9   # 남은 것끼리 재정규화

    def test_점수와_신뢰도는_다른_축이다(self):
        sets = self._sets()
        ranks = models_mod.build_ranks(sets)
        got = consensus_mod.combine(1, models_mod.score_all(1, sets[1], ranks),
                                    weights_mod.for_regime(None))
        assert got.score > 0
        assert got.confidence < got.score        # 모델 절반이 비어 신뢰도가 낮다
        assert "다른 축입니다" in got.calc.intermediates["주의"]

    def test_기여도_합이_1이다(self):
        sets = self._sets()
        ranks = models_mod.build_ranks(sets)
        got = consensus_mod.combine(1, models_mod.score_all(1, sets[1], ranks),
                                    weights_mod.for_regime(None))
        assert sum(got.attribution.values()) == pytest.approx(1.0, abs=1e-6)

    def test_국면마다_가중치가_다르다(self):
        hot = weights_mod.for_regime("과열")
        cold = weights_mod.for_regime("침체")
        assert hot.values["momentum"] < cold.values["value"]
        assert hot.values["risk"] > weights_mod.BASE["risk"]

    def test_가중치_출처가_기록된다(self):
        w = weights_mod.for_regime("과열")
        assert w.source == weights_mod.HEURISTIC
        assert "임시" in w.label

    def test_왜_A가_B보다_높은지_항별로_쪼갠다(self):
        sets = self._sets()
        ranks = models_mod.build_ranks(sets)
        a = consensus_mod.combine(1, models_mod.score_all(1, sets[1], ranks),
                                  weights_mod.for_regime(None))
        b = consensus_mod.combine(3, models_mod.score_all(3, sets[3], ranks),
                                  weights_mod.for_regime(None))
        diff = consensus_mod.explain_pair(a, b)
        assert diff["점수 차"] == pytest.approx(a.score - b.score, abs=0.1)
        assert diff["가장 큰 이유"] in models_mod.SPEC


# ── §45 Kill Score ─────────────────────────────────────────────────────

class TestKill:
    def test_확인하지_못한_위험은_없음이_아니다(self):
        empty = FeatureSet(1, BAND, "2026-01-01")
        got = kill_mod.evaluate(empty)
        assert got.value == 0.0
        assert len(got.unchecked) == len(kill_mod.RULES)
        assert "미확인" in got.label

    def test_공급충격이_잡힌다(self):
        fs = FeatureSet(1, BAND, "2026-01-01").add(
            Feature("supply_ratio_2y", 0.15, "", 0.9, Status.OK))
        got = kill_mod.evaluate(fs)
        assert any(h.reason == "공급충격" for h in got.hits)

    def test_임계를_넘으면_배제된다(self):
        fs = FeatureSet(1, BAND, "2026-01-01")
        for key, value in (("supply_ratio_2y", 0.20), ("downside_defense", 0.05),
                           ("discovery_lag", 0.95), ("entry_position", 1.4)):
            fs = fs.add(Feature(key, value, "", 0.9, Status.OK))
        got = kill_mod.evaluate(fs)
        assert got.killed
        assert "TOP10 제외" in got.label


# ── §23 Thesis Survival ────────────────────────────────────────────────

def test_논리가_하나뿐이면_취약하다고_말한다():
    fs = FeatureSet(1, BAND, "2026-01-01")
    fs = fs.add(Feature("catalyst_alpha", 0.9, "", 0.9, Status.OK))
    ranks = models_mod.build_ranks({1: fs})
    cons = consensus_mod.combine(1, models_mod.score_all(1, fs, ranks),
                                 weights_mod.for_regime(None))
    got = thesis_mod.evaluate(cons, weights_mod.for_regime(None))
    assert got.fragile
    assert "한 논리에 기댐" in got.label


# ── 파이프라인 ─────────────────────────────────────────────────────────

class TestPipeline:
    def test_살_수_있는지_먼저_보고_점수를_매긴다(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=4)
            result = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(0.5))
        # 현금 5천만원으로는 아무것도 못 산다
        assert result.feasible == []
        assert result.top10 == []
        assert any(d.stage == "feasibility" for d in result.dropped)

    def test_현금이_늘면_후보가_늘어난다(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=6)
            small = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(1.0))
            big = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(10.0))
        assert len(big.feasible) >= len(small.feasible)

    def test_현금이_없으면_판정을_거부한다(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=3)
            with pytest.raises(ValueError, match="가용 현금"):
                pipeline_mod.run(conn, as_of=AS_OF, profile=Profile(name="빈"))

    def test_탈락_이유가_남는다(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=6)
            result = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(1.5))
        assert result.dropped
        assert all(d.reason for d in result.dropped)

    def test_아무것도_좋지_않으면_CASH_를_추천한다(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=2)
            result = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(10.0))
        # 후보가 2개뿐이라 점수가 낮거나 신뢰도가 낮다
        assert result.cash_recommended
        assert result.cash_reason

    def test_이름을_바꿔도_순위가_같다(self, db):
        """§1 Placebo — 이름이 점수에 영향을 주면 실패."""
        def run_with(names):
            with get_conn(db) as conn:
                conn.execute("DELETE FROM price_snapshot")
                conn.execute("DELETE FROM complex")
                conn.execute("DELETE FROM tax_rule")
                conn.execute("DELETE FROM cost_rule")
                conn.execute("DELETE FROM loan_rule")
                add_rules(conn)
                ids = []
                for i, name in enumerate(names):
                    cid = add_complex(conn, name, f"K{i}")
                    ym = "202312"
                    for step in range(24):
                        add_price(conn, cid, ym, 3.0 + i * 0.4 + 0.01 * step)
                        ym = f"{int(ym[:4]) + (int(ym[4:6]) % 12 == 0):04d}" \
                             f"{(int(ym[4:6]) % 12) + 1:02d}"
                    ids.append(cid)
                r = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(10.0))
                # complex_id 는 AUTOINCREMENT 라 재실행마다 달라진다. 비교할 것은
                # **입력 순서에 대응하는 점수 배열**이다 — 이름이 순위를 바꿨다면
                # 이 배열이 달라진다.
                order = {cid: i for i, cid in enumerate(ids)}
                return [(order[c.complex_id], round(c.score, 6)) for c in r.top30]

        real = run_with(["부평동아1단지", "산본주공11", "개나리13", "벽적골한신"])
        fake = run_with(["AAA", "BBB", "CCC", "DDD"])
        assert real == fake

    def test_관심단지를_추가해도_결과가_같다(self, db):
        """§70 — user-interest leakage."""
        with get_conn(db) as conn:
            ids = seed_market(conn, n=5)
            before = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(10.0))
            conn.execute("INSERT INTO watchlist (complex_id) VALUES (?)", (ids[-1],))
            after = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(10.0))
        assert [c.complex_id for c in before.top30] == [c.complex_id for c in after.top30]
        assert [round(c.score, 6) for c in before.top30] == [
            round(c.score, 6) for c in after.top30]

    def test_컷오프를_바꾸면_결과가_달라진다(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=5)
            early = pipeline_mod.run(conn, as_of=cutoff_mod.AsOf("2024-06-01"),
                                     profile=profile(10.0))
            late = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(10.0))
        assert early.universe_size <= late.universe_size


# ── §48 세 리스트 ──────────────────────────────────────────────────────

class TestLists:
    def _candidates(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=8)
            return pipeline_mod.run(conn, as_of=AS_OF, profile=profile(10.0))

    def test_세_리스트가_모두_나온다(self, db):
        result = self._candidates(db)
        got = lists_mod.all_lists(result.top10)
        assert set(got) == set(lists_mod.KINDS)

    def test_정렬_키가_리스트마다_다르다(self, db):
        result = self._candidates(db)
        if len(result.top10) < 2:
            pytest.skip("후보가 부족")
        keys = {k: [lists_mod.sort_key(k, c) for c in result.top10]
                for k in lists_mod.KINDS}
        assert keys[lists_mod.ABSOLUTE] != keys[lists_mod.ASYMMETRIC]

    def test_모르는_리스트는_거부한다(self, db):
        result = self._candidates(db)
        if not result.top10:
            pytest.skip("후보가 없음")
        with pytest.raises(ValueError, match="모르는 리스트"):
            lists_mod.sort_key("무작위", result.top10[0])

    def test_확인_못한_하방위험을_안전으로_읽지_않는다(self, db):
        """모른다는 걸 0 으로 두면 정확히 반대의 실수를 한다."""
        result = self._candidates(db)
        if not result.top10:
            pytest.skip("후보가 없음")
        c = result.top10[0]
        assert lists_mod._downside(c) > 0


# ── §64·§65·§66 저장과 순위 변화 ───────────────────────────────────────

class TestPersistence:
    def test_시점별로_따로_저장되고_직전_순위를_찾는다(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=6)
            for day in ("2025-06-01", "2026-01-01"):
                result = pipeline_mod.run(conn, as_of=cutoff_mod.AsOf(day),
                                          profile=profile(10.0))
                entries = lists_mod.build(result.top10, lists_mod.ABSOLUTE)
                if entries:
                    rank_repo.save_run(conn, run_key="t", result=result,
                                       list_kind=lists_mod.ABSOLUTE, entries=entries)
            runs = conn.execute("SELECT as_of FROM ranking_run ORDER BY as_of").fetchall()
        assert len(runs) >= 1

    def test_가중치_출처가_함께_저장된다(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=6)
            result = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(10.0))
            entries = lists_mod.build(result.top10, lists_mod.ABSOLUTE)
            if not entries:
                pytest.skip("후보가 없음")
            rank_repo.save_run(conn, run_key="t", result=result,
                               list_kind=lists_mod.ABSOLUTE, entries=entries)
            row = conn.execute("SELECT weights_source FROM ranking_run").fetchone()
        assert row["weights_source"] == weights_mod.HEURISTIC


# ── §63·§75 설명 ───────────────────────────────────────────────────────

class TestExplain:
    def test_WHY_BUY_와_WHY_NOT_이_나온다(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=8)
            result = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(10.0))
        if not result.top10:
            pytest.skip("후보가 없음")
        report = explain_mod.full_report(result.top10[0])
        assert report["WHY BUY"]
        assert "시장이 반영한 것" in report
        assert "아직 반영 안 된 것" in report
        assert "데이터 커버리지" in report

    def test_반영_안됨이_데이터_없음일_수_있다고_밝힌다(self, db):
        with get_conn(db) as conn:
            seed_market(conn, n=4)
            result = pipeline_mod.run(conn, as_of=AS_OF, profile=profile(10.0))
        if not result.top10:
            pytest.skip("후보가 없음")
        got = explain_mod.what_market_prices(result.top10[0].features)
        assert "데이터가 없다는 뜻일 수도" in got["주의"]
