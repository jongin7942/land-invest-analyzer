"""아파트 스키마·저장소·검증 테스트.

핵심 질문: **요구사항 26의 금지사항을 스키마가 실제로 막는가.**
문서에 "합치지 마세요"라고 적는 것과 DB가 거부하는 것은 다르다.
"""
import sqlite3

import pytest

from apt_engine import regions
from apt_engine.collectors import matcher
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn, table_names
from apt_engine.repo import apt as repo
from apt_engine.validation import rules as validation


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


def make_complex(name="은마", lawd="11680", households=4424, year=1979, emd="대치동",
                 kapt_code=None):
    return {
        "kapt_code": kapt_code or f"A{abs(hash(name)) % 10**8}",
        "name": name, "name_norm": matcher.normalize(name),
        "lawd_cd": lawd, "emd_name": emd,
        "apt_households": households, "approval_year": year,
        "approval_date": f"{year}0101",
    }


def make_trade(**kw):
    row = {
        "lawd_cd": "11680", "emd_name": "대치동", "jibun": "316",
        "apt_name": "은마", "exclusive_area_m2": 84.43, "area_band": "84",
        "deal_amount": 2_800_000_000, "deal_ymd": "20260703", "floor": 7,
        "build_year": 1979, "deal_type": "중개거래", "cancel_yn": 0,
    }
    row.update(kw)
    return row


class TestSchemaForbidsBadData:
    def test_세대수_합계_컬럼이_아예_없다(self, db):
        # 요구사항 26-1/26-2: 합계를 만들지 않으면 어길 수가 없다.
        with get_conn(db) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(complex)")}
        assert "apt_households" in cols
        assert "officetel_households" in cols
        assert "total_households" not in cols

    def test_근거_없는_단지_병합은_거부된다(self, db):
        # 요구사항 26-3
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO complex_group (name, merge_reason, created_by) "
                    "VALUES ('○○ 통합', '   ', 'me')")
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO complex_group (name, created_by) VALUES ('○○ 통합', 'me')")

    def test_근거가_있으면_병합할_수_있다(self, db):
        with get_conn(db) as conn:
            conn.execute(
                "INSERT INTO complex_group (name, merge_reason, created_by) VALUES (?,?,?)",
                ("올림픽선수기자촌", "1·2·3단지가 단일 재건축 조합으로 통합 추진 중", "jongin"))
            assert conn.execute("SELECT COUNT(*) FROM complex_group").fetchone()[0] == 1

    def test_금액이_0이거나_음수면_거부(self, db):
        with get_conn(db) as conn:
            repo.upsert_complexes(conn, [make_complex()])
            with pytest.raises(sqlite3.IntegrityError):
                repo.insert_trades(conn, [make_trade(deal_amount=0)])

    def test_전용면적이_0이면_거부(self, db):
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                repo.insert_trades(conn, [make_trade(exclusive_area_m2=0)])

    def test_매칭_신뢰도는_정해진_값만(self, db):
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                repo.insert_trades(conn, [make_trade(match_confidence="아마도")])


class TestUpsert:
    def test_같은_달을_두_번_수집해도_중복되지_않는다(self, db):
        with get_conn(db) as conn:
            assert repo.insert_trades(conn, [make_trade()]) == 1
            assert repo.insert_trades(conn, [make_trade()]) == 0
            assert conn.execute("SELECT COUNT(*) FROM trade").fetchone()[0] == 1

    def test_층이_다르면_다른_거래다(self, db):
        with get_conn(db) as conn:
            repo.insert_trades(conn, [make_trade(floor=7), make_trade(floor=8)])
            assert conn.execute("SELECT COUNT(*) FROM trade").fetchone()[0] == 2

    def test_단지_기본정보를_나중에_채워도_목록_정보가_안_지워진다(self, db):
        # 2단계 수집(목록 → 기본정보) 흐름에서 NULL 이 기존 값을 덮으면 안 된다.
        with get_conn(db) as conn:
            repo.upsert_complexes(conn, [{
                "kapt_code": "A1", "name": "은마", "name_norm": "은마",
                "lawd_cd": "11680", "emd_name": "대치동",
            }])
            repo.upsert_complexes(conn, [{
                "kapt_code": "A1", "name": "은마", "name_norm": "은마",
                "lawd_cd": None, "emd_name": None,
                "apt_households": 4424, "approval_year": 1979,
            }])
            row = conn.execute("SELECT * FROM complex WHERE kapt_code='A1'").fetchone()
        assert row["lawd_cd"] == "11680"      # 유지
        assert row["emd_name"] == "대치동"     # 유지
        assert row["apt_households"] == 4424  # 새로 채워짐


class TestHouseholdFilter:
    def test_999세대는_1000세대_필터에_안_들어간다(self, db):
        # 요구사항 26-1을 그대로 시험한다.
        with get_conn(db) as conn:
            repo.upsert_complexes(conn, [
                make_complex("999단지", households=999, kapt_code="A999"),
                make_complex("1000단지", households=1000, kapt_code="A1000"),
                make_complex("4424단지", households=4424, kapt_code="A4424"),
            ])
            names = [r["name"] for r in repo.complexes_over(conn, 1000)]
        assert "999단지" not in names
        assert set(names) == {"1000단지", "4424단지"}

    def test_시도로_좁힐_수_있다(self, db):
        with get_conn(db) as conn:
            repo.upsert_complexes(conn, [
                make_complex("서울단지", lawd="11680", kapt_code="S1"),
                make_complex("경기단지", lawd="41135", kapt_code="G1"),
            ])
            seoul = [r["name"] for r in repo.complexes_over(conn, 1000, sido="서울")]
        assert seoul == ["서울단지"]


class TestMatchingPipeline:
    def test_매칭이_거래에_적용되고_되돌릴_수_있다(self, db):
        with get_conn(db) as conn:
            repo.upsert_complexes(conn, [make_complex(kapt_code="A1")])
            cid = conn.execute("SELECT id FROM complex").fetchone()[0]
            repo.insert_trades(conn, [make_trade(), make_trade(floor=8)])

            groups = repo.distinct_unmatched_names(conn, "trade")
            assert len(groups) == 1 and groups[0]["cnt"] == 2

            cands = repo.candidates_for(conn, "11680")
            result = matcher.match(groups[0]["apt_name"], cands,
                                   emd_name=groups[0]["emd_name"],
                                   build_year=groups[0]["build_year"])
            assert result.complex_id == cid

            n = repo.apply_match(conn, "trade", lawd_cd="11680", apt_name="은마",
                                 emd_name="대치동", build_year=1979,
                                 complex_id=result.complex_id,
                                 confidence=result.confidence, reason=result.reason)
            assert n == 2
            assert repo.match_stats(conn, "trade")["unmatched"] == 0

            repo.clear_matches(conn, "trade")
            assert repo.match_stats(conn, "trade")["unmatched"] == 2

    def test_미매칭_거래도_버리지_않고_저장된다(self, db):
        # 매칭 실패 건을 버리면 미매칭 리포트도, 규칙 수정 후 재매칭도 불가능하다.
        with get_conn(db) as conn:
            repo.insert_trades(conn, [make_trade(apt_name="K-apt에없는단지")])
            assert conn.execute("SELECT COUNT(*) FROM trade").fetchone()[0] == 1
            assert repo.match_stats(conn, "trade")["unmatched"] == 1

    def test_실거래에서_면적타입이_도출된다(self, db):
        with get_conn(db) as conn:
            repo.upsert_complexes(conn, [make_complex(kapt_code="A1")])
            cid = conn.execute("SELECT id FROM complex").fetchone()[0]
            repo.insert_trades(conn, [
                make_trade(complex_id=cid, exclusive_area_m2=84.43, area_band="84"),
                make_trade(complex_id=cid, exclusive_area_m2=84.43, area_band="84", floor=9),
                make_trade(complex_id=cid, exclusive_area_m2=59.98, area_band="59", floor=3),
            ])
            repo.derive_unit_types_from_trades(conn)
            rows = conn.execute(
                "SELECT exclusive_area_m2, area_band FROM unit_type ORDER BY 1").fetchall()
        assert [(r[0], r[1]) for r in rows] == [(59.98, "59"), (84.43, "84")]


class TestRegionCodes:
    def test_수도권_시군구_수(self):
        assert len(regions.SEOUL) == 25
        assert len(regions.INCHEON) == 10
        assert len(regions.SIGUNGU) == 25 + 10 + len(regions.GYEONGGI)

    def test_과거_거래월에는_폐지된_통합코드를_쓴다(self):
        # 2021년 화성시 거래는 신설 4개 구 코드로는 조회되지 않는다.
        old = regions.codes_for_ym("202101", "경기")
        assert "41590" in old
        assert "41591" not in old

    def test_현재_거래월에는_신설_구코드를_쓴다(self):
        now = regions.codes_for_ym("202608", "경기")
        assert "41590" not in now
        assert {"41591", "41593", "41595", "41597"} <= set(now)

    def test_서울은_개편_영향을_안_받는다(self):
        assert regions.codes_for_ym("202101", "서울") == regions.all_codes("서울")

    def test_잘못된_거래월은_에러(self):
        with pytest.raises(ValueError, match="YYYYMM"):
            regions.codes_for_ym("2021")

    def test_시군구_테이블_동기화(self, db):
        with get_conn(db) as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM region WHERE is_active=1").fetchone()[0]
            legacy = conn.execute(
                "SELECT COUNT(*) FROM region WHERE is_active=0").fetchone()[0]
        assert active == len(regions.SIGUNGU)
        assert legacy == len(regions.LEGACY)


class TestValidation:
    def test_빈_DB는_전부_통과한다(self, db):
        with get_conn(db) as conn:
            results = validation.run_all(conn)
            summary = validation.summarize(results)
        assert summary["errors"] == 0

    def test_밴드가_전용면적과_어긋나면_잡아낸다(self, db):
        with get_conn(db) as conn:
            # 59㎡ 거래를 84 밴드로 잘못 저장한 상황(요구사항 26-4 위반)
            repo.insert_trades(conn, [make_trade(exclusive_area_m2=59.98, area_band="84")])
            violations = validation.area_band_consistent(conn)
        assert violations
        assert "'59' 밴드인데 '84'" in violations[0]

    def test_국민평형_밴드에_다른_면적이_섞이면_잡아낸다(self, db):
        with get_conn(db) as conn:
            conn.execute(
                "INSERT INTO trade (lawd_cd, apt_name, exclusive_area_m2, area_band, "
                "deal_amount, deal_ymd) VALUES ('11680','X',88.0,'84',1000000000,'20260101')")
            assert validation.kookmin_band_range(conn)

    def test_거래유형이_전부_비면_경고한다(self, db):
        with get_conn(db) as conn:
            repo.insert_trades(conn, [make_trade(deal_type=None)])
            violations = validation.deal_type_present(conn)
        assert violations and "상세 데이터셋" in violations[0]

    def test_만원_단위로_잘못_저장하면_잡아낸다(self, db):
        # 28억을 280000(만원)으로 저장한 상황 — 단위 혼동의 전형
        with get_conn(db) as conn:
            repo.insert_trades(conn, [make_trade(deal_amount=280_000)])
            violations = validation.amounts_sane(conn)
        assert violations and "단위 혼동" in violations[0]

    def test_미매칭_비율이_높으면_잡아낸다(self, db):
        with get_conn(db) as conn:
            for i in range(10):
                repo.insert_trades(conn, [make_trade(floor=i, apt_name=f"미등록{i}")])
            violations = validation.match_rate(conn)
        assert violations and "미매칭" in violations[0]

    def test_모든_규칙에_고유_설명이_있다(self, db):
        for r in validation.all_rules():
            assert r.rule_id and r.title
            assert r.severity in ("ERROR", "WARN")


class TestMigrationChain:
    def test_마이그레이션이_순서대로_전부_적용된다(self, tmp_db):
        applied = mig.migrate(tmp_db)
        assert applied == list(range(1, len(applied) + 1))
        with get_conn(tmp_db) as conn:
            tables = set(table_names(conn))
        assert {"data_source", "collection_log", "engine_version",
                "region", "complex", "unit_type", "trade", "jeonse_contract",
                "complex_group", "complex_group_member", "complex_block",
                "price_snapshot", "jeonse_snapshot"} <= tables

    def test_출처가_등록돼_있다(self, db):
        with get_conn(db) as conn:
            keys = {r[0] for r in conn.execute("SELECT key FROM data_source")}
        assert {"molit_apt_trade", "molit_apt_rent", "kapt_complex", "manual"} <= keys

    def test_수집이력이_없음과_실패를_구분해_기록한다(self, db):
        with get_conn(db) as conn:
            repo.log_collection(conn, "molit_apt_trade", target="41290", period="202101",
                                status="EMPTY", row_count=0)
            repo.log_collection(conn, "molit_apt_trade", target="11680", period="202101",
                                status="FAILED", error="HTTP 500")
            rows = conn.execute(
                "SELECT status, error FROM collection_log ORDER BY status").fetchall()
        assert [r["status"] for r in rows] == ["EMPTY", "FAILED"]
        assert rows[1]["error"] == "HTTP 500"


class TestIngestOrchestration:
    """수집 자체는 네트워크가 필요하지만, 매칭 단계는 DB만으로 끝까지 돌려볼 수 있다."""

    def test_매칭_단계가_통째로_돌아간다(self, db):
        from apt_engine import ingest

        with get_conn(db) as conn:
            repo.upsert_complexes(conn, [
                make_complex("은마", kapt_code="A1"),
                make_complex("래미안대치팰리스", households=1608, year=2015, kapt_code="A2"),
            ])
            repo.insert_trades(conn, [
                make_trade(apt_name="은마아파트"),                       # 표기 차이 → EXACT
                make_trade(apt_name="래미안 대치팰리스", floor=12,
                           exclusive_area_m2=59.98, area_band="59", build_year=2015),
                make_trade(apt_name="K-apt에없는단지", floor=3),          # → NONE
            ])

        result = ingest.run_matching(db_path=db, progress=lambda *a: None)
        assert result["매매"]["EXACT"] == 2
        assert result["매매"]["NONE"] == 1

        with get_conn(db) as conn:
            st = repo.match_stats(conn, "trade")
            bands = {r[0] for r in conn.execute("SELECT area_band FROM unit_type")}
        assert st["unmatched"] == 1          # 미매칭 건은 남아 있다(버리지 않는다)
        assert bands == {"84", "59"}         # 면적타입이 실거래에서 도출됐다

    def test_재매칭하면_기존_결과를_지우고_다시_붙인다(self, db):
        from apt_engine import ingest

        with get_conn(db) as conn:
            repo.insert_trades(conn, [make_trade(apt_name="은마")])
        ingest.run_matching(db_path=db, progress=lambda *a: None)
        with get_conn(db) as conn:
            assert repo.match_stats(conn, "trade")["unmatched"] == 1   # 단지가 아직 없음
            repo.upsert_complexes(conn, [make_complex("은마", kapt_code="A1")])

        ingest.run_matching(rebuild=True, db_path=db, progress=lambda *a: None)
        with get_conn(db) as conn:
            assert repo.match_stats(conn, "trade")["unmatched"] == 0

    def test_최근_N개월_목록(self):
        from datetime import date
        from apt_engine import ingest
        yms = ingest.recent_yms(14, end=date(2026, 2, 15))
        assert yms[0] == "202501" and yms[-1] == "202602" and len(yms) == 14


class TestCli:
    def test_도움말이_렌더링된다(self, capsys):
        from apt_engine import cli
        with pytest.raises(SystemExit) as exc:
            cli.main(["--help"])
        assert exc.value.code == 0
        assert "usage:" in capsys.readouterr().out

    @pytest.mark.parametrize("sub", ["init", "status", "probe", "collect",
                                     "match", "validate", "report"])
    def test_서브명령_도움말(self, sub, capsys):
        from apt_engine import cli
        with pytest.raises(SystemExit) as exc:
            cli.main([sub, "--help"])
        assert exc.value.code == 0
        assert "usage:" in capsys.readouterr().out

    def test_status_가_빈_DB에서도_돈다(self, db, capsys):
        from apt_engine import cli
        cli.main(["--db", db, "status"])
        out = capsys.readouterr().out
        assert "스키마" in out and "complex" in out

    def test_validate_가_빈_DB에서_통과한다(self, db, capsys):
        from apt_engine import cli
        cli.main(["--db", db, "validate"])
        assert "위반 0" in capsys.readouterr().out
