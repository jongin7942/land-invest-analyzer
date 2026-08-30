"""상대가치 테스트 (요구사항 3·4·17).

이 계층이 지키려는 선:
  1. 근거 없이 비교단지를 고르지 않는다 — 억지로 5개 채우느니 0개가 낫다
  2. Current Ratio 와 Historical Normal Ratio 를 섞지 않는다
  3. 다른 면적·다른 시점의 가격을 비교하지 않는다
  4. 비율이 벌어졌다는 사실만 말하고 "저평가"라고 단정하지 않는다
"""
import json
import sqlite3

import pytest

from apt_engine import units
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.relative import benchmark as bench
from apt_engine.relative import ladder as ladder_mod
from apt_engine.relative import ratio as ratio_mod
from apt_engine.relative.benchmark import Candidate
from apt_engine.repo import apt as repo
from apt_engine.repo import relative as rel_repo


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


def cand(cid, name, lawd, *, price_eok=6.0, households=1200, year=1990, emd=None,
         sido="인천"):
    return Candidate(cid, name, lawd, emd, households, year,
                     int(units.from_eok(price_eok)), sido)


def make_ladder(conn, name="테스트축", labels=(("평촌", "41173"), ("산본", "41410"),
                                              ("부평", "28237"))):
    axis_id = ladder_mod.upsert_axis(conn, name=name,
                                     rationale="테스트용 축", curated_by="test")
    ladder_mod.set_nodes(conn, axis_id, [{"label": l, "lawd_cd": c}
                                          for l, c in labels])
    return axis_id


def add_complex(conn, cid_name, lawd, *, emd=None, households=1200, year=1990):
    repo.upsert_complexes(conn, [{
        "kapt_code": f"K{cid_name}", "name": cid_name, "name_norm": cid_name,
        "lawd_cd": lawd, "emd_name": emd, "apt_households": households,
        "approval_year": year}])
    return conn.execute("SELECT id FROM complex WHERE kapt_code=?",
                        (f"K{cid_name}",)).fetchone()[0]


def add_snapshot(conn, complex_id, band, ym, price_eok, *, sample_n=10,
                 confidence="HIGH"):
    from apt_engine.trace import Calc
    calc = Calc(value=int(units.from_eok(price_eok)), unit="원", formula="테스트")
    conn.execute(
        "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym, window_months, "
        "representative_price, method, sample_n, confidence, engine_version, "
        "data_grade, calc_trace) VALUES (?,?,?,6,?,'median',?,?,?,'CONFIRMED',?)",
        (complex_id, band, ym, int(units.from_eok(price_eok)), sample_n, confidence,
         calc.engine_version, calc.to_json()))
    return conn.execute("SELECT id FROM price_snapshot ORDER BY id DESC LIMIT 1").fetchone()[0]


# ── 가격사다리 ────────────────────────────────────────────────────────

class TestLadder:
    def test_근거_없는_축은_만들_수_없다(self, db):
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO ladder_axis (name, rationale, curated_by) "
                             "VALUES ('축', '   ', 'me')")

    def test_축과_노드_등록(self, db):
        with get_conn(db) as conn:
            axis_id = make_ladder(conn)
            assert ladder_mod.axis_labels(conn, axis_id) == ["평촌", "산본", "부평"]

    def test_같은_지역의_노드를_찾는다(self, db):
        with get_conn(db) as conn:
            make_ladder(conn)
            nodes = ladder_mod.nodes_for_region(conn, "41410")
        assert len(nodes) == 1 and nodes[0].label == "산본" and nodes[0].rank == 1

    def test_이웃은_위아래_모두_포함하고_자기는_뺀다(self, db):
        with get_conn(db) as conn:
            make_ladder(conn)
            node = ladder_mod.nodes_for_region(conn, "41410")[0]
            nb = ladder_mod.neighbours(conn, node, span=1)
        assert {n.label for n in nb} == {"평촌", "부평"}

    def test_서식_import(self, db, tmp_path):
        path = ladder_mod.write_template(tmp_path / "l.csv")
        with get_conn(db) as conn:
            s = ladder_mod.import_csv(conn, path)
            axes = {a["name"] for a in ladder_mod.list_axes(conn)}
        assert s["axes"] == 4
        assert {"서남권", "경부축", "안양권", "서북권"} == axes

    def test_축의_첫_줄에_근거가_없으면_거부(self, db, tmp_path):
        p = tmp_path / "l.csv"
        p.write_text("axis_name,rationale,curated_by,label,lawd_cd,emd_name,note\n"
                     "축A,,,강남,11680,,\n", encoding="utf-8")
        with get_conn(db) as conn:
            with pytest.raises(ladder_mod.LadderError, match="rationale"):
                ladder_mod.import_csv(conn, p)

    def test_노드를_다시_넣으면_교체된다(self, db):
        with get_conn(db) as conn:
            axis_id = make_ladder(conn)
            ladder_mod.set_nodes(conn, axis_id, [{"label": "강남", "lawd_cd": "11680"}])
            assert ladder_mod.axis_labels(conn, axis_id) == ["강남"]


# ── 비교단지 선정 ─────────────────────────────────────────────────────

class TestBenchmarkSelection:
    def test_사다리에_있으면_강한_근거가_된다(self, db):
        with get_conn(db) as conn:
            make_ladder(conn)
            target = cand(1, "부평단지", "28237")
            pool = [target, cand(2, "산본단지", "41410", sido="경기")]
            picks = bench.select(conn, target, pool)
        assert len(picks) == 1
        assert picks[0].reasons["사다리인접"] > 0
        assert "축" in picks[0].note

    def test_사다리에_없으면_근거가_약해_안_잡힌다(self, db):
        # "비슷해 보여서" 골라주지 않는다.
        with get_conn(db) as conn:
            target = cand(1, "부평단지", "28237")
            pool = [target, cand(2, "엉뚱단지", "11680", price_eok=20.0,
                                 households=300, year=2020, sido="서울")]
            picks = bench.select(conn, target, pool)
        assert picks == []

    def test_사다리가_없으면_비교단지가_거의_안_잡힌다(self, db):
        # 의도한 동작 — 사다리를 안 채우면 상대가치 분석이 시작되지 않는다.
        with get_conn(db) as conn:
            target = cand(1, "A", "28237")
            pool = [target] + [cand(i, f"B{i}", "28237", sido="인천") for i in range(2, 8)]
            picks = bench.select(conn, target, pool)
        # 같은 시도 + 가격/규모/연식만으로는 0.6 을 못 넘지만 문턱(0.35)은 넘을 수 있다.
        # 다만 사다리 점수가 0이라 상위권을 못 차지한다.
        for p in picks:
            assert p.reasons["사다리인접"] == 0.0

    def test_자기_자신은_비교대상이_아니다(self, db):
        with get_conn(db) as conn:
            make_ladder(conn)
            target = cand(1, "부평단지", "28237")
            picks = bench.select(conn, target, [target])
        assert picks == []

    def test_상위_N개만_돌려준다(self, db):
        with get_conn(db) as conn:
            make_ladder(conn)
            target = cand(1, "부평단지", "28237")
            pool = [target] + [cand(i, f"산본{i}", "41410", sido="경기")
                               for i in range(2, 12)]
            picks = bench.select(conn, target, pool, top_n=3)
        assert len(picks) == 3

    def test_유사도가_내림차순이다(self, db):
        with get_conn(db) as conn:
            make_ladder(conn)
            target = cand(1, "부평단지", "28237", price_eok=6.0, households=1200)
            pool = [target,
                    cand(2, "닮음", "41410", price_eok=6.1, households=1250, sido="인천"),
                    cand(3, "덜닮음", "41410", price_eok=12.0, households=300,
                         year=2020, sido="경기")]
            picks = bench.select(conn, target, pool)
        assert picks[0].candidate.name == "닮음"
        assert picks[0].similarity >= picks[-1].similarity

    def test_선정근거가_항목별로_남는다(self, db):
        with get_conn(db) as conn:
            make_ladder(conn)
            target = cand(1, "부평단지", "28237")
            pick = bench.select(conn, target, [target, cand(2, "산본", "41410")])[0]
            calc = bench.to_calc(target, pick)
        assert set(pick.reasons) == set(bench.DEFAULT_WEIGHTS)
        assert "항목점수" in calc.intermediates and "기여도" in calc.intermediates
        assert calc.grade == "ESTIMATED"     # 가중치는 우리 가정이다

    def test_코드가_비어_있는_사다리_노드는_무시된다(self, db):
        # 노드에 lawd_cd 를 안 채우면 그 노드로는 사다리 근거가 생기지 않는다.
        # (나머지 항목만으로 후보에는 오를 수 있지만, 가장 강한 근거인 사다리 점수는 0이다)
        with get_conn(db) as conn:
            axis_id = ladder_mod.upsert_axis(conn, name="x", rationale="r",
                                             curated_by="t")
            ladder_mod.set_nodes(conn, axis_id, [
                {"label": "부평", "lawd_cd": "28237"},
                {"label": "미확정", "lawd_cd": ""},          # 코드 미입력
            ])
            target = cand(1, "부평단지", "28237")
            picks = bench.select(conn, target, [target, cand(2, "미확정단지", "41410")])
        assert all(p.reasons["사다리인접"] == 0.0 for p in picks)
        assert all("사다리 축에 없음" in p.note for p in picks)


class TestBenchmarkStorage:
    def test_근거_없이는_저장이_안_된다(self, db):
        with get_conn(db) as conn:
            a = add_complex(conn, "A", "28237")
            b = add_complex(conn, "B", "41410")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO benchmark_relation (complex_id, benchmark_complex_id, "
                    "area_band, rank, similarity, selection_reason_json, engine_version, "
                    "calc_trace) VALUES (?,?,'84',1,0.5,'{}','0.6.0','{}')", (a, b))

    def test_자기_자신을_비교대상으로_저장할_수_없다(self, db):
        with get_conn(db) as conn:
            a = add_complex(conn, "A", "28237")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO benchmark_relation (complex_id, benchmark_complex_id, "
                    "area_band, rank, similarity, selection_reason_json, engine_version, "
                    "calc_trace) VALUES (?,?,'84',1,0.5,'{\"a\":1}','0.6.0','{}')", (a, a))

    def test_저장과_조회_왕복(self, db):
        with get_conn(db) as conn:
            make_ladder(conn)
            a = add_complex(conn, "부평단지", "28237")
            b = add_complex(conn, "산본단지", "41410")
            add_snapshot(conn, a, "84", "202608", 6.0)
            add_snapshot(conn, b, "84", "202608", 8.0)

            target = rel_repo.candidate_of(conn, a, "84")
            pool = rel_repo.candidates(conn, "84")
            picks = bench.select(conn, target, pool)
            assert picks, "사다리가 있는데 비교단지가 안 잡혔다"

            rel_repo.replace_benchmarks(
                conn, a, "84", [(p, bench.to_calc(target, p)) for p in picks])
            rows = rel_repo.benchmarks_of(conn, a, "84")
        assert rows[0]["benchmark_name"] == "산본단지"
        assert json.loads(rows[0]["selection_reason_json"])["항목점수"]

    def test_대표가격이_없는_단지는_후보에서_빠진다(self, db):
        with get_conn(db) as conn:
            a = add_complex(conn, "가격있음", "28237")
            add_complex(conn, "가격없음", "41410")
            add_snapshot(conn, a, "84", "202608", 6.0)
            pool = rel_repo.candidates(conn, "84")
        assert [c.name for c in pool] == ["가격있음"]


# ── 가격비율 ──────────────────────────────────────────────────────────

class TestCurrentRatio:
    def _snap(self, price_eok, ym="202608", band="84", n=10, conf="HIGH"):
        return {"representative_price": int(units.from_eok(price_eok)),
                "as_of_ym": ym, "area_band": band, "sample_n": n, "confidence": conf}

    def test_비율_계산(self):
        calc = ratio_mod.current_ratio(self._snap(6.0), self._snap(9.0), area_band="84")
        assert calc.value == pytest.approx(6.0 / 9.0)

    def test_기준월이_다르면_거부(self):
        with pytest.raises(ValueError, match="기준월이 다른"):
            ratio_mod.current_ratio(self._snap(6.0, ym="202608"),
                                    self._snap(9.0, ym="202607"), area_band="84")

    def test_면적밴드가_다르면_거부(self):
        # 요구사항 26-4 — 84와 59를 비교하면 비율이 뜻을 잃는다.
        with pytest.raises(ValueError, match="면적밴드가 다른"):
            ratio_mod.current_ratio(self._snap(6.0, band="84"),
                                    self._snap(9.0, band="59"), area_band="84")

    def test_표본이_적은_쪽이_신뢰도를_결정한다(self):
        calc = ratio_mod.current_ratio(self._snap(6.0, conf="HIGH"),
                                       self._snap(9.0, conf="LOW"), area_band="84")
        assert calc.intermediates["신뢰도"] == "LOW"

    def test_한쪽이_없으면_계산하지_않는다(self):
        assert ratio_mod.current_ratio(None, self._snap(9.0), area_band="84") is None


class TestMarketPhase:
    @pytest.mark.parametrize("now,before,expected", [
        (110, 100, "상승"), (100, 100, "횡보"), (90, 100, "하락"),
        (102, 100, "횡보"), (104, 100, "상승"), (96, 100, "하락"),
    ])
    def test_12개월_변화로_판정(self, now, before, expected):
        assert ratio_mod.market_phase(now, before) == expected

    def test_비교할_값이_없으면_확인_불가(self):
        assert ratio_mod.market_phase(100, None) is None
        assert ratio_mod.market_phase(None, 100) is None


class TestHistoricalNormal:
    HISTORY = [
        {"as_of_ym": "202101", "ratio": 0.72, "market_phase": "상승"},
        {"as_of_ym": "202201", "ratio": 0.70, "market_phase": "상승"},
        {"as_of_ym": "202301", "ratio": 0.70, "market_phase": "하락"},
        {"as_of_ym": "202401", "ratio": 0.69, "market_phase": "하락"},
        {"as_of_ym": "202501", "ratio": 0.67, "market_phase": "횡보"},
        {"as_of_ym": "202601", "ratio": 0.60, "market_phase": "횡보"},
    ]

    def test_구간별로_따로_낸다(self):
        norms = {n.window_key: n for n in
                 ratio_mod.normals(self.HISTORY, as_of_ym="202608")}
        assert "all" in norms and "5y" in norms
        assert "상승기" in norms and "하락기" in norms
        assert norms["상승기"].sample_n == 2
        assert norms["상승기"].median == pytest.approx(0.71)

    def test_5년_구간이_전체보다_짧다(self):
        norms = {n.window_key: n for n in
                 ratio_mod.normals(self.HISTORY, as_of_ym="202608")}
        assert norms["5y"].sample_n < norms["all"].sample_n

    def test_표본이_없는_구간은_만들지_않는다(self):
        # 0건짜리 '평균'은 숫자가 아니다.
        history = [{"as_of_ym": "202601", "ratio": 0.6, "market_phase": "횡보"}]
        keys = {n.window_key for n in ratio_mod.normals(history, as_of_ym="202608")}
        assert "상승기" not in keys and "하락기" not in keys

    def test_시장국면_구간은_자체판정임을_근거에_남긴다(self):
        norms = {n.window_key: n for n in
                 ratio_mod.normals(self.HISTORY, as_of_ym="202608")}
        sources = [e.source for e in norms["상승기"].calc.evidence]
        assert "자체 시장국면 판정" in sources
        assert not norms["all"].calc.evidence     # 전체 구간은 자체판정이 아니다

    def test_이력이_없으면_빈_목록(self):
        assert ratio_mod.normals([], as_of_ym="202608") == []


class TestGapVsNormal:
    def test_격차를_계산하고_저평가라고_단정하지_않는다(self):
        current = ratio_mod.current_ratio(
            {"representative_price": units.from_eok(6.0), "as_of_ym": "202608",
             "area_band": "84", "sample_n": 10, "confidence": "HIGH"},
            {"representative_price": units.from_eok(10.0), "as_of_ym": "202608",
             "area_band": "84", "sample_n": 10, "confidence": "HIGH"},
            area_band="84")
        norm = ratio_mod.normals(TestHistoricalNormal.HISTORY, as_of_ym="202608")[0]
        gap = ratio_mod.gap_vs_normal(current, norm)

        assert gap.value < 0
        text = gap.intermediates["해석"]
        # 격차가 벌어졌다는 사실만 말하고, 저평가라고 선언하지 않는다.
        assert "저평가입니다" not in text and "저평가 상태" not in text
        assert "확인해야" in text     # 이유를 확인해야 알 수 있다고 말한다

    def test_한쪽이_없으면_계산하지_않는다(self):
        assert ratio_mod.gap_vs_normal(None, None) is None


# ── 파이프라인 ────────────────────────────────────────────────────────

class TestPipeline:
    def test_비교단지와_비율이_끝까지_만들어진다(self, db):
        from apt_engine import ingest

        with get_conn(db) as conn:
            make_ladder(conn)
            a = add_complex(conn, "부평단지", "28237")
            b = add_complex(conn, "산본단지", "41410")
            for i, ym in enumerate(["202506", "202512", "202606", "202608"]):
                add_snapshot(conn, a, "84", ym, 6.0 + i * 0.1)
                add_snapshot(conn, b, "84", ym, 9.0 + i * 0.1)

        bstats = ingest.build_benchmarks(area_band="84", db_path=db,
                                         progress=lambda *x: None)
        assert bstats["relations"] >= 1

        rstats = ingest.build_ratios(area_band="84", db_path=db,
                                     progress=lambda *x: None)
        assert rstats["ratios"] == 8      # 두 방향 × 4개월
        assert rstats["norms"] > 0

        view = ingest.relative_view(complex_id=a, area_band="84", db_path=db)
        bench_view = view["benchmarks"][0]
        assert bench_view["latest"]["ratio"] == pytest.approx(6.3 / 9.3, abs=0.001)
        assert bench_view["reasons"]["항목점수"]

    def test_공통_기준월이_없으면_비율을_만들지_않는다(self, db):
        from apt_engine import ingest

        with get_conn(db) as conn:
            make_ladder(conn)
            a = add_complex(conn, "부평단지", "28237")
            b = add_complex(conn, "산본단지", "41410")
            add_snapshot(conn, a, "84", "202601", 6.0)
            add_snapshot(conn, b, "84", "202608", 9.0)   # 겹치는 달이 없다

        ingest.build_benchmarks(area_band="84", db_path=db, progress=lambda *x: None)
        rstats = ingest.build_ratios(area_band="84", db_path=db, progress=lambda *x: None)
        assert rstats["ratios"] == 0
        assert rstats["skipped"] >= 1
