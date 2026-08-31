"""반칙 방지 테스트 (지시서 §1·§68·§69·§70·§73).

이 파일이 지키는 두 가지 선:

  1. Look-ahead leakage — 그 시점에 몰랐던 데이터가 모델에 들어가면 실패
  2. User-interest leakage — 관심단지를 추가해도 점수가 변하지 않아야 함

둘 다 "조심하겠다"로는 안 지켜진다. 실패하는 테스트로 못 박는다.
"""
import ast
import sqlite3
from pathlib import Path

import pytest

from apt_engine import units
from apt_engine.blind import anonymize, cutoff as cutoff_mod
from apt_engine.blind import universe as universe_mod
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.repo import apt as repo

ROOT = Path(__file__).resolve().parents[1]
LAWD = "28237"


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


def add_complex(conn, name, kapt, *, lawd=LAWD, households=1000, year=2000):
    repo.upsert_complexes(conn, [{
        "kapt_code": kapt, "name": name, "name_norm": name, "lawd_cd": lawd,
        "apt_households": households, "approval_year": year}])
    return conn.execute("SELECT id FROM complex WHERE kapt_code=?",
                        (kapt,)).fetchone()[0]


def add_snapshot(conn, cid, ym, price_eok, *, band="84", sample_n=10):
    conn.execute(
        "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym, window_months, "
        " representative_price, method, sample_n, confidence, data_grade, "
        " engine_version, calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, band, ym, 6, units.from_eok(price_eok), "median", sample_n, "HIGH",
         "CONFIRMED", "0.11.0", "{}"))


# ── §69 Look-ahead ─────────────────────────────────────────────────────

class TestLookAhead:
    def test_컷오프_없이_실거래를_읽으면_거부한다(self, db):
        with get_conn(db) as conn:
            with cutoff_mod.guard(conn, cutoff_mod.AsOf("2023-01-01")) as g:
                with pytest.raises(cutoff_mod.LookAheadError, match="컷오프 없이"):
                    g.execute("SELECT * FROM trade")

    def test_컷오프를_걸면_통과한다(self, db):
        with get_conn(db) as conn:
            as_of = cutoff_mod.AsOf("2023-01-01")
            with cutoff_mod.guard(conn, as_of) as g:
                g.execute("SELECT * FROM trade WHERE deal_ymd <= ?", (as_of.ymd,))
            assert g.checked == 1

    def test_시점_분류에_없는_테이블은_거부한다(self, db):
        """새 테이블을 만들고 등록을 잊으면 조용히 통과하면 안 된다."""
        with get_conn(db) as conn:
            conn.execute("CREATE TABLE unregistered_table (id INTEGER, ymd TEXT)")
            with cutoff_mod.guard(conn, cutoff_mod.AsOf("2023-01-01")) as g:
                with pytest.raises(cutoff_mod.LookAheadError, match="등록"):
                    g.execute("SELECT * FROM unregistered_table")

    def test_스키마의_모든_테이블이_시점_분류에_들어_있다(self, db):
        """마이그레이션을 추가하면서 분류 등록을 잊는 걸 막는다."""
        with get_conn(db) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")}
        classified = set(cutoff_mod.DATED_TABLES) | set(cutoff_mod.TIMELESS_TABLES)
        missing = tables - classified
        assert missing == set(), (
            f"시점 분류가 없는 테이블: {sorted(missing)}. "
            f"blind/cutoff.py 의 DATED_TABLES 또는 TIMELESS_TABLES 에 넣으세요")

    def test_분류에만_있고_스키마에_없는_테이블은_오타다(self, db):
        with get_conn(db) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")}
        classified = set(cutoff_mod.DATED_TABLES) | set(cutoff_mod.TIMELESS_TABLES)
        assert classified - tables == set()

    def test_미래_스냅샷은_후보에_들어오지_않는다(self, db):
        with get_conn(db) as conn:
            past = add_complex(conn, "과거단지", "KP")
            future = add_complex(conn, "미래단지", "KF")
            add_snapshot(conn, past, "202211", 6)
            add_snapshot(conn, future, "202312", 6)      # 컷오프 이후
            got = universe_mod.build(conn, as_of=cutoff_mod.AsOf("2023-01-01"))
        ids = [r.complex_id for r in got.rows]
        assert past in ids
        assert future not in ids

    def test_신고_지연을_감안해_컷오프를_당긴다(self, db):
        """2023-01-01 에 실제로 볼 수 있던 건 대략 2022-12-01 이전 신고분이다."""
        as_of = cutoff_mod.AsOf("2023-01-01")
        assert as_of.observable.day < as_of.day
        with get_conn(db) as conn:
            cid = add_complex(conn, "직전단지", "KJ")
            add_snapshot(conn, cid, "202212", 6)   # 컷오프 직전 달 — 아직 신고 전
            got = universe_mod.build(conn, as_of=as_of)
        assert [r.complex_id for r in got.rows] == []

    def test_컷오프를_바꾸면_후보가_달라진다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, "단지", "K1")
            add_snapshot(conn, cid, "202305", 6)
            early = universe_mod.build(conn, as_of=cutoff_mod.AsOf("2023-01-01"))
            late = universe_mod.build(conn, as_of=cutoff_mod.AsOf("2023-12-01"))
        assert len(early) == 0
        assert len(late) == 1

    def test_raw_는_검사를_건너뛴다(self, db):
        """우회 경로는 있되 코드에 흔적이 남아야 한다."""
        with get_conn(db) as conn:
            with cutoff_mod.guard(conn, cutoff_mod.AsOf("2023-01-01")) as g:
                g.raw("SELECT * FROM trade")      # 예외 없음


# ── §70 User-interest leakage ──────────────────────────────────────────

class TestUserInterest:
    def test_관심단지를_추가해도_후보와_순서가_같다(self, db):
        with get_conn(db) as conn:
            ids = [add_complex(conn, f"단지{i}", f"K{i}") for i in range(5)]
            for cid in ids:
                add_snapshot(conn, cid, "202210", 5 + cid % 3)
            before = universe_mod.build(conn, as_of=cutoff_mod.AsOf("2023-01-01"))

            conn.execute("INSERT INTO watchlist (complex_id, note) VALUES (?,?)",
                         (ids[3], "관심 있음"))
            after = universe_mod.build(conn, as_of=cutoff_mod.AsOf("2023-01-01"))

        assert [r.complex_id for r in before.rows] == [r.complex_id for r in after.rows]
        assert [r.representative_price for r in before.rows] == [
            r.representative_price for r in after.rows]

    def test_후보생성_코드는_watchlist_를_조회하지_않는다(self):
        """읽을 방법이 없으면 우대할 방법도 없다.

        실행 코드의 문자열 상수만 본다 — 주석·docstring 에 단어가 나오는 건
        괜찮고, **SQL 로 그 테이블을 읽는 것**이 문제다.
        (cutoff.py 는 watchlist 를 '시점 무관 테이블' 목록에 이름만 올려 둔다.
         그건 등록부이지 조회가 아니다.)
        """
        offenders = []
        for path in ((ROOT / "apt_engine" / "blind").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docs = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)):
                    continue
                text = node.value
                if text in docs:
                    continue
                lowered = text.lower()
                if "watchlist" in lowered and any(
                        kw in lowered for kw in ("select", "from", "join", "insert")):
                    offenders.append(f"{path.name}: {text[:60]!r}")
        assert offenders == [], (
            f"후보생성 계층이 사용자 관심 테이블을 조회합니다: {offenders}")

    def test_관심단지는_랭킹_뒤_표시용_테이블이다(self, db):
        """watchlist 는 존재하되 랭킹 산출물과 물리적으로 분리돼 있다."""
        with get_conn(db) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(ranking_entry)")}
        assert "watchlist" not in cols
        assert not any("interest" in c or "watch" in c for c in cols)


# ── §1 Placebo (익명성) ────────────────────────────────────────────────

class TestAnonymity:
    def test_익명_ID_는_재현_가능하고_이름과_무관하다(self):
        assert anonymize.anon_id(7) == anonymize.anon_id(7)
        assert anonymize.anon_id(7) != anonymize.anon_id(8)
        assert anonymize.anon_id(7, salt="a") != anonymize.anon_id(7, salt="b")

    def test_식별_필드가_지워진다(self):
        hidden = anonymize.Anonymizer().hide({
            "complex_id": 3, "name": "부평동아1단지", "road_addr": "부평대로 1",
            "emd_name": "부평동", "representative_price": 600_000_000})
        assert "name" not in hidden and "road_addr" not in hidden
        assert "부평" not in str(hidden)
        assert hidden["representative_price"] == 600_000_000
        assert hidden["anon_id"].startswith(anonymize.ANON_PREFIX)

    def test_후보생성_결과에_단지명이_없다(self, db):
        """UniverseRow 자체가 이름을 담지 않는다 — 담을 칸이 없다."""
        with get_conn(db) as conn:
            cid = add_complex(conn, "부평동아1단지", "K1")
            add_snapshot(conn, cid, "202210", 6)
            got = universe_mod.build(conn, as_of=cutoff_mod.AsOf("2023-01-01"))
        assert got.rows
        assert not hasattr(got.rows[0], "name")
        assert "부평" not in str(got.rows[0])

    def test_이름을_바꿔도_후보와_순서가_같다(self, db):
        """Placebo Test — 이름이 결과에 영향을 주면 실패한다."""
        def run(names):
            with get_conn(db) as conn:
                conn.execute("DELETE FROM price_snapshot")
                conn.execute("DELETE FROM complex")
                for i, name in enumerate(names):
                    cid = add_complex(conn, name, f"K{i}")
                    add_snapshot(conn, cid, "202210", 5 + i)
                return universe_mod.ranking_fingerprint_input(
                    universe_mod.build(conn, as_of=cutoff_mod.AsOf("2023-01-01")))

        real = run(["부평동아1단지", "산본주공11", "개나리13"])
        fake = run(["AAA", "BBB", "CCC"])
        assert real == fake


# ── §73 known examples 는 fixture 전용 ─────────────────────────────────

KNOWN_EXAMPLES = ("부평동아1", "산본주공11", "개나리13", "벽적골주공8", "벽적골한신",
                  "대야역두산위브더파크", "수원아이파크시티7", "은계센트럴타운",
                  "별내푸르지오", "상계주공13", "한강센트럴자이")


def test_엔진_코드에_특정_단지명이_하드코딩돼_있지_않다():
    """지시서 §73: 이 단지들이 TOP10 에 들어가도록 맞추지 마라.

    테스트와 문서에는 나와도 되지만 **엔진 코드에는 없어야 한다.**
    """
    offenders = []
    for path in (ROOT / "apt_engine").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        # 주석·docstring 을 걷어낸 실행 코드만 본다
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        for lit in literals:
            if lit in docstrings:
                continue
            for name in KNOWN_EXAMPLES:
                if name in lit:
                    offenders.append(f"{path.relative_to(ROOT)}: {lit[:40]!r}")
    assert offenders == [], f"엔진 코드에 특정 단지명이 있습니다: {offenders}"


# ── §58·§59 Lessons DB ─────────────────────────────────────────────────

class TestLessons:
    def test_20개_가설이_전부_HYPOTHESIS_로_들어간다(self, db):
        from apt_engine.repo import lessons

        with get_conn(db) as conn:
            n = lessons.seed(conn)
            rows = lessons.by_status(conn)
        assert n == 20 and len(rows) == 20
        assert {r["status"] for r in rows} == {"HYPOTHESIS"}

    def test_다시_seed_해도_사람이_올린_상태를_덮지_않는다(self, db):
        from apt_engine.repo import lessons

        with get_conn(db) as conn:
            lessons.seed(conn)
            lessons.promote(conn, "volume_is_not_buy_signal", status="PROVISIONAL",
                            evidence="2018~2021 서울 표본", sample_size=50)
            lessons.seed(conn)
            row = conn.execute(
                "SELECT status FROM investment_lesson WHERE lesson_key = ?",
                ("volume_is_not_buy_signal",)).fetchone()
        assert row["status"] == "PROVISIONAL"

    def test_표본이_적으면_CONFIRMED_로_못_올린다(self, db):
        from apt_engine.repo import lessons

        with get_conn(db) as conn:
            lessons.seed(conn)
            with pytest.raises(lessons.LessonError, match="표본"):
                lessons.promote(conn, "entry_price_over_location", status="CONFIRMED",
                                evidence="사례 3건", sample_size=3,
                                tested_regimes="상승기,하락기")

    def test_한_국면에서만_맞으면_CONFIRMED_가_아니다(self, db):
        from apt_engine.repo import lessons

        with get_conn(db) as conn:
            lessons.seed(conn)
            with pytest.raises(lessons.LessonError, match="시장국면"):
                lessons.promote(conn, "entry_price_over_location", status="CONFIRMED",
                                evidence="충분한 표본", sample_size=500,
                                tested_regimes="상승기")

    def test_조건을_갖추면_CONFIRMED_가_된다(self, db):
        from apt_engine.repo import lessons

        with get_conn(db) as conn:
            lessons.seed(conn)
            lessons.promote(conn, "entry_price_over_location", status="CONFIRMED",
                            evidence="2015~2024 수도권 walk-forward",
                            sample_size=1200, tested_regions="서울,경기,인천",
                            tested_regimes="상승기,하락기,횡보기",
                            modified_rule="entry_price weight 0.15 → 0.25")
            rules_out = lessons.confirmed_rules(conn)
        assert len(rules_out) == 1
        assert rules_out[0]["lesson_key"] == "entry_price_over_location"

    def test_REJECTED_도_남긴다(self, db):
        """왜 아닌지가 자산이다. 지우지 않는다."""
        from apt_engine.repo import lessons

        with get_conn(db) as conn:
            lessons.seed(conn)
            lessons.promote(conn, "newish_high_jeonse_not_alpha", status="REJECTED",
                            evidence="검증 결과 유의미한 Alpha 없음", sample_size=800)
            rows = lessons.by_status(conn, "REJECTED")
        assert len(rows) == 1

    def test_확정되지_않은_lesson_은_계산에_쓰이지_않는다(self, db):
        from apt_engine.repo import lessons

        with get_conn(db) as conn:
            lessons.seed(conn)
            lessons.promote(conn, "jeonse_is_downside_defense", status="PROVISIONAL",
                            evidence="일부 확인", sample_size=300)
            assert lessons.confirmed_rules(conn) == []


# ── §3 Source Conflict ─────────────────────────────────────────────────

class TestSourceConflict:
    def test_충돌을_덮어쓰지_않고_양쪽을_남긴다(self, db):
        with get_conn(db) as conn:
            cid = add_complex(conn, "단지", "K1")
            conn.execute(
                "INSERT INTO source_conflict (entity_type, entity_id, field_name, "
                " value_a, source_a, source_a_tier, value_b, source_b, source_b_tier, "
                " resolved_to, resolved_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("complex", cid, "준공연도", "1986", "건축물대장", 1,
                 "1989", "포털 시세", 5, "1986", "tier"))
            row = conn.execute("SELECT * FROM source_conflict").fetchone()
        assert row["value_a"] == "1986" and row["value_b"] == "1989"
        assert row["resolved_to"] == "1986"      # 공식 출처가 이긴다
        assert row["resolved_by"] == "tier"

    def test_출처_등급이_공식부터_정렬돼_있다(self, db):
        with get_conn(db) as conn:
            tiers = conn.execute(
                "SELECT tier, name FROM source_tier ORDER BY tier").fetchall()
        assert tiers[0]["name"] == "공식 정부/지자체"
        assert tiers[-1]["name"] == "기타"
        assert [t["tier"] for t in tiers] == list(range(1, 7))


# ── §66 랭킹 스냅샷은 덮어쓰지 않는다 ──────────────────────────────────

def test_랭킹_실행은_시점별로_따로_저장된다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn, "단지", "K1")
        for as_of in ("2025-01-01", "2026-01-01"):
            conn.execute(
                "INSERT INTO ranking_run (run_key, as_of, cash, horizon_years, "
                " profile, list_kind, universe_size, feasible_size, engine_version, "
                " weights_json, weights_source) "
                "VALUES ('monthly',?,?,?,'balanced','absolute',100,40,'0.11.0','{}',"
                " 'HEURISTIC')",
                (as_of, units.from_eok(3), 5))
        runs = conn.execute("SELECT as_of FROM ranking_run ORDER BY as_of").fetchall()
    assert [r["as_of"] for r in runs] == ["2025-01-01", "2026-01-01"]


def test_랭킹_결과는_시나리오_등급으로만_저장된다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn, "단지", "K1")
        conn.execute(
            "INSERT INTO ranking_run (id, run_key, as_of, cash, horizon_years, "
            " profile, list_kind, universe_size, feasible_size, engine_version, "
            " weights_json, weights_source) "
            "VALUES (1,'r','2026-01-01',?,5,'balanced','absolute',10,5,'0.11.0','{}',"
            " 'HEURISTIC')", (units.from_eok(3),))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ranking_entry (run_id, rank, complex_id, area_band, "
                " score, factors_json, calc_trace, data_grade) "
                "VALUES (1,1,?,'84',90.0,'{}','{}','CONFIRMED')", (cid,))


def test_가중치_출처가_기록된다(db):
    """§74 — heuristic 가중치로 낸 결과를 학습 결과처럼 보여주지 않는다."""
    with get_conn(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ranking_run (run_key, as_of, cash, horizon_years, "
                " profile, list_kind, universe_size, feasible_size, engine_version, "
                " weights_json, weights_source) "
                "VALUES ('r','2026-01-01',?,5,'balanced','absolute',10,5,'0.11.0',"
                " '{}','대충')", (units.from_eok(3),))
