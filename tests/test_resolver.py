"""PropertyResolver · 값별 출처 속성 테스트 (지시서 §2·§3).

이 계층이 지키려는 선:
  1. 애매하면 붙이지 않는다 — 아무거나 고르면 그 뒤 모든 숫자가 다른 단지 것이 된다
  2. 시점을 본다 — 지금 이름으로 과거를 조회하면 백테스트가 조용히 틀린다
  3. 출처가 충돌하면 덮어쓰지 않고 양쪽을 남긴다
"""
import pytest

from apt_engine import resolver
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.repo import apt as repo
from apt_engine.repo import attributes as attr

LAWD = "28237"
OTHER = "28185"


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


def add(conn, name, kapt, *, lawd=LAWD, emd=None, year=None, households=None):
    repo.upsert_complexes(conn, [{
        "kapt_code": kapt, "name": name,
        "name_norm": __import__("apt_engine.collectors.matcher", fromlist=["x"]
                                ).normalize(name),
        "lawd_cd": lawd, "emd_name": emd, "approval_year": year,
        "apt_households": households}])
    return conn.execute("SELECT id FROM complex WHERE kapt_code=?",
                        (kapt,)).fetchone()[0]


class TestResolve:
    def test_이름_하나면_확정한다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "행복아파트", "K1")
            got = resolver.resolve(conn, "행복아파트")
        assert got.resolution is resolver.Resolution.EXACT
        assert got.complex_id == cid

    def test_표기가_흔들려도_정규화로_찾는다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "래미안 강남포레스트", "K1")
            got = resolver.resolve(conn, "래미안강남포레스트")
        assert got.ok and got.complex_id == cid

    def test_동명이_여럿이면_붙이지_않는다(self, db):
        with get_conn(db) as conn:
            add(conn, "주공1단지", "K1", emd="가동")
            add(conn, "주공1단지", "K2", emd="나동")
            got = resolver.resolve(conn, "주공1단지")
        assert got.resolution is resolver.Resolution.AMBIGUOUS
        assert got.complex_id is None
        assert len(got.candidates) == 2

    def test_법정동을_주면_좁혀진다(self, db):
        with get_conn(db) as conn:
            add(conn, "주공1단지", "K1", emd="가동")
            want = add(conn, "주공1단지", "K2", emd="나동")
            got = resolver.resolve(conn, "주공1단지", emd_name="나동")
        assert got.ok and got.complex_id == want

    def test_준공연도로도_좁혀진다(self, db):
        with get_conn(db) as conn:
            add(conn, "주공1단지", "K1", year=1988)
            want = add(conn, "주공1단지", "K2", year=2005)
            got = resolver.resolve(conn, "주공1단지", approval_year=2004)
        assert got.ok and got.complex_id == want

    def test_시군구가_다르면_섞이지_않는다(self, db):
        with get_conn(db) as conn:
            here = add(conn, "주공1단지", "K1", lawd=LAWD)
            add(conn, "주공1단지", "K2", lawd=OTHER)
            got = resolver.resolve(conn, "주공1단지", lawd_cd=LAWD)
        assert got.ok and got.complex_id == here

    def test_없는_이름은_NOT_FOUND(self, db):
        with get_conn(db) as conn:
            got = resolver.resolve(conn, "없는단지")
        assert got.resolution is resolver.Resolution.NOT_FOUND
        assert got.complex_id is None

    def test_빈_이름을_받아도_죽지_않는다(self, db):
        with get_conn(db) as conn:
            assert resolver.resolve(conn, "   ").resolution is (
                resolver.Resolution.NOT_FOUND)


class TestAlias:
    def test_이전_이름으로도_찾는다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "새이름자이", "K1")
            resolver.add_alias(conn, cid, "옛이름주공", kind="이전명",
                               reason="2021년 브랜드 변경", created_by="종인",
                               valid_from="2010-01-01", valid_to="2021-06-30")
            got = resolver.resolve(conn, "옛이름주공")
        assert got.resolution is resolver.Resolution.ALIAS
        assert got.complex_id == cid

    def test_시점_밖의_별칭은_쓰지_않는다(self, db):
        """2023년 자료에 옛 이름이 나오면 그건 다른 단지일 수 있다."""
        with get_conn(db) as conn:
            cid = add(conn, "새이름자이", "K1")
            resolver.add_alias(conn, cid, "옛이름주공", kind="이전명",
                               reason="2021년 변경", created_by="종인",
                               valid_from="2010-01-01", valid_to="2021-06-30")
            inside = resolver.resolve(conn, "옛이름주공", as_of="2015-01-01")
            outside = resolver.resolve(conn, "옛이름주공", as_of="2023-01-01")
        assert inside.ok
        assert outside.resolution is resolver.Resolution.NOT_FOUND

    def test_다른_단지가_쓰는_이름은_별칭으로_못_넣는다(self, db):
        with get_conn(db) as conn:
            a = add(conn, "가나단지", "K1")
            add(conn, "다라단지", "K2")
            with pytest.raises(resolver.AliasError, match="이미 그 이름"):
                resolver.add_alias(conn, a, "다라단지", kind="별칭",
                                   reason="테스트", created_by="종인")

    def test_근거_없는_별칭은_저장되지_않는다(self, db):
        import sqlite3
        with get_conn(db) as conn:
            cid = add(conn, "단지", "K1")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO complex_alias (complex_id, alias, alias_norm, kind, "
                    " reason, created_by) VALUES (?,?,?,?,?,?)",
                    (cid, "별칭", "별칭", "별칭", "  ", "종인"))


class TestMerge:
    def test_중복_등록을_대표행으로_접는다(self, db):
        with get_conn(db) as conn:
            keep = add(conn, "본체단지", "K1")
            drop = add(conn, "중복등록단지", "K2")
            resolver.merge(conn, keep=keep, drop=drop,
                           reason="K-apt 와 실거래에서 따로 등록됨", created_by="종인")
            assert resolver.canonical_id(conn, drop) == keep
            assert resolver.canonical_id(conn, keep) == keep
            got = resolver.resolve(conn, "중복등록단지")
        assert got.ok and got.complex_id == keep

    def test_행을_지우지_않는다(self, db):
        """이력이 남아야 과거 분석이 재현된다."""
        with get_conn(db) as conn:
            keep = add(conn, "본체", "K1")
            drop = add(conn, "중복", "K2")
            resolver.merge(conn, keep=keep, drop=drop, reason="중복", created_by="종인")
            n = conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0]
        assert n == 2

    def test_자기_자신과는_병합할_수_없다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "단지", "K1")
            with pytest.raises(resolver.AliasError, match="같은 단지"):
                resolver.merge(conn, keep=cid, drop=cid, reason="x", created_by="종인")


class TestAttributes:
    def test_값과_출처가_함께_저장된다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "단지", "K1")
            attr.put(conn, cid, attr.SCHOOL_ZONE, text="○○중학군",
                     as_of="2026-03-01", source_name="교육청 배정표", source_tier=1,
                     confidence="HIGH", verification="VERIFIED")
            got = attr.best(conn, cid, attr.SCHOOL_ZONE)
        assert got.value == "○○중학군"
        assert got.source_tier == 1
        assert "교육청" in got.label

    def test_값이_비면_저장하지_않는다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "단지", "K1")
            with pytest.raises(ValueError, match="비었"):
                attr.put(conn, cid, attr.SCHOOL_ZONE, source_name="x", source_tier=1)

    def test_공식_출처가_이긴다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "단지", "K1")
            attr.put(conn, cid, attr.PARKING_RATIO, num=1.2, as_of="2026-01-01",
                     source_name="포털", source_tier=5)
            attr.put(conn, cid, attr.PARKING_RATIO, num=0.8, as_of="2026-01-01",
                     source_name="건축물대장", source_tier=1)
            got = attr.best(conn, cid, attr.PARKING_RATIO)
        assert got.num == 0.8

    def test_시점_이후_값은_보이지_않는다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "단지", "K1")
            attr.put(conn, cid, attr.LIFE_ZONE, text="나중값", as_of="2026-01-01",
                     source_name="A", source_tier=2)
            assert attr.best(conn, cid, attr.LIFE_ZONE, as_of="2025-01-01") is None
            assert attr.best(conn, cid, attr.LIFE_ZONE, as_of="2026-06-01") is not None

    def test_시점_불명_값은_백테스트에서_제외된다(self, db):
        """언제 알았는지 모르는 값을 과거 모델에 넣으면 그게 look-ahead 다."""
        with get_conn(db) as conn:
            cid = add(conn, "단지", "K1")
            attr.put(conn, cid, attr.LIFE_ZONE, text="시점없음",
                     source_name="A", source_tier=2)
            assert attr.best(conn, cid, attr.LIFE_ZONE) is not None
            assert attr.best(conn, cid, attr.LIFE_ZONE, as_of="2026-01-01") is None

    def test_충돌을_덮어쓰지_않고_기록한다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "단지", "K1")
            attr.put(conn, cid, "approval_year", num=1986, as_of="2026-01-01",
                     source_name="건축물대장", source_tier=1)
            attr.put(conn, cid, "approval_year", num=1989, as_of="2026-01-01",
                     source_name="포털", source_tier=5)
            assert len(attr.conflicts(conn, cid, "approval_year")) == 1
            saved = attr.record_conflicts(conn, cid, "approval_year",
                                          field_label="준공연도")
            row = conn.execute("SELECT * FROM source_conflict").fetchone()
        assert saved == 1
        assert row["resolved_to"] == "1986.0"
        assert row["resolved_by"] == "tier"

    def test_같은_등급끼리_충돌하면_자동으로_정하지_않는다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "단지", "K1")
            attr.put(conn, cid, "approval_year", num=1986, as_of="2026-01-01",
                     source_name="A공공데이터", source_tier=2)
            attr.put(conn, cid, "approval_year", num=1989, as_of="2026-01-01",
                     source_name="B공공데이터", source_tier=2)
            attr.record_conflicts(conn, cid, "approval_year")
            row = conn.execute("SELECT * FROM source_conflict").fetchone()
        assert row["resolved_to"] is None
        assert "사람이 확인" in row["note"]

    def test_없는_속성은_None_이지_기본값이_아니다(self, db):
        with get_conn(db) as conn:
            cid = add(conn, "단지", "K1")
            assert attr.best(conn, cid, attr.SCHOOL_ZONE) is None


class TestLifeZone:
    def test_생활권은_근거_없이_만들_수_없다(self, db):
        import sqlite3
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO life_zone (key, name, rationale, curated_by) "
                    "VALUES ('z','생활권','   ','종인')")

    def test_인접관계는_방향이_없다(self, db):
        import sqlite3
        with get_conn(db) as conn:
            for k in ("aaa", "bbb"):
                conn.execute(
                    "INSERT INTO life_zone (key, name, rationale, curated_by) "
                    "VALUES (?,?,'테스트','종인')", (k, k))
            conn.execute(
                "INSERT INTO life_zone_adjacency (zone_a, zone_b, travel_min, "
                " substitution, rationale) VALUES ('aaa','bbb',15,'강','지하철 3정거장')")
            # (b,a) 로 뒤집어 넣는 것은 CHECK 가 막는다 — 같은 관계가 두 번 저장되면
            # 대체관계 계산이 두 배로 세진다
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO life_zone_adjacency (zone_a, zone_b, rationale) "
                    "VALUES ('bbb','aaa','뒤집힌 중복')")
