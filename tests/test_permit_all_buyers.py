"""작업지시서 §6 필수 테스트 14종 — ALL_BUYERS 토허.

실제 CSV 를 import 한 DB 로 돌린다. 코드가 아니라 **데이터가** 맞는지를
보는 테스트라 픽스처를 가짜로 만들면 의미가 없다.
"""
import csv
import os
import subprocess
import sys

import pytest

from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.regulation import gate

TODAY = "2026-09-01"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("permit") / "apt.db")
    mig.migrate(path)
    # region 을 채워야 lawd_cd 검증이 의미가 있다 (migrate 만으로는 비어 있다)
    from apt_engine.repo import apt as repo
    with get_conn(path) as conn:
        repo.sync_regions(conn)
    env = {**os.environ, "APT_DB_PATH": path}
    for f in ("rules/permit_all_buyers.csv", "rules/permit_foreign.csv"):
        r = subprocess.run([sys.executable, "-m", "apt_engine.cli", "rule",
                            "import", "permit", f],
                           capture_output=True, text=True, env=env)
        assert "입력" in r.stdout, f"{f} import 실패: {r.stdout}{r.stderr}"
    with get_conn(path) as conn:
        with open("rules/permit_coverage.csv", encoding="utf-8") as fh:
            for row in csv.DictReader(l for l in fh if not l.startswith("#")):
                conn.execute(
                    "INSERT OR REPLACE INTO permit_coverage (sido,target_scope,"
                    "coverage_status,checked_on,source_name,note)"
                    " VALUES (?,?,?,?,?,?)",
                    (row["sido"], row["target_scope"], row["coverage_status"],
                     row["checked_on"] or None, row["source_name"] or None,
                     row["note"]))
        conn.commit()
    return path


def judge(db, lawd_cd, *, nationality=gate.KOREAN, occupancy=gate.NON_OCCUPANCY,
          land_share=20.0, contract="2026-09-01", ptype=gate.APARTMENT,
          same_complex=None, coverage=None, parcel=gate.INCOMPLETE,
          sido="서울특별시"):
    with get_conn(db) as conn:
        rules = gate.load_rules(conn, lawd_cd=lawd_cd, as_of=contract)
        # 계약일이 유효기간 밖이면 load_rules 가 못 읽으므로 전 기간을 읽어
        # evaluate_candidate 가 만료 판정을 하도록 한다
        allrows = conn.execute(
            "SELECT rule_id, target_scope, buyer_scope, nationality_scope,"
            " residence_duty_months, status, effective_from, effective_to,"
            " property_scope, parcel_recheck_required, source_url,"
            " residential_threshold_sqm FROM land_permit_zone WHERE lawd_cd=?",
            (lawd_cd,)).fetchall()
        rules = [gate.Rule(
            rule_id=r["rule_id"], target_scope=gate.buyer_scope_of(r),
            nationality_scope=r["nationality_scope"],
            residence_duty_months=r["residence_duty_months"], status=r["status"],
            effective_from=r["effective_from"], effective_to=r["effective_to"],
            property_scope=r["property_scope"],
            parcel_recheck_required=bool(r["parcel_recheck_required"]),
            source_url=r["source_url"],
            residential_threshold_sqm=r["residential_threshold_sqm"])
            for r in allrows]
    if coverage is None:
        with get_conn(db) as conn:
            coverage = gate.coverage_of(conn, sido=sido,
                                        target_scope=gate.BROAD_APARTMENT)
            parcel = gate.coverage_of(conn, sido=sido,
                                      target_scope=gate.PARCEL_SPECIFIC)
    return gate.evaluate_candidate(
        rules,
        gate.Candidate(lawd_cd=lawd_cd, property_type=ptype,
                       land_share_sqm=land_share,
                       same_complex_has_apartment=same_complex),
        nationality=nationality, occupancy_plan=occupancy,
        contract_date=contract, coverage_status=coverage,
        parcel_coverage=parcel)


# ── 데이터 자체 검증 ──────────────────────────────────────────────────

def test_ALL_BUYERS_40행이_들어갔다(db):
    with get_conn(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM land_permit_zone "
                         "WHERE buyer_scope='ALL_BUYERS'").fetchone()[0]
    assert n == 40, f"지시서 §7 이 요구한 40행이 아니다: {n}"


def test_인천_7개구는_FOREIGN_ONLY로만_남는다(db):
    """인천 외국인 지정이 ALL_BUYERS 로 섞여 들어가면 안 된다."""
    with get_conn(db) as conn:
        rows = conn.execute(
            "SELECT lawd_cd, buyer_scope FROM land_permit_zone "
            "WHERE lawd_cd LIKE '28%'").fetchall()
    assert rows, "인천 규칙이 사라졌다"
    assert all(r["buyer_scope"] == "FOREIGN_ONLY" for r in rows), (
        "인천 외국인 지정이 ALL_BUYERS 로 들어갔다")


def test_ALL_BUYERS에_인천이_없다(db):
    with get_conn(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM land_permit_zone "
                         "WHERE buyer_scope='ALL_BUYERS' AND lawd_cd LIKE '28%'"
                         ).fetchone()[0]
    assert n == 0


def test_ALL_BUYERS_코드는_전부_실제_행정구역이다(db):
    """지어낸 코드가 있으면 그 규칙은 영원히 안 걸린다 — 조용한 실패다."""
    with get_conn(db) as conn:
        bad = conn.execute(
            "SELECT DISTINCT z.lawd_cd, z.rule_id FROM land_permit_zone z "
            "LEFT JOIN region r ON r.lawd_cd = z.lawd_cd "
            "WHERE r.lawd_cd IS NULL AND z.buyer_scope='ALL_BUYERS'").fetchall()
    assert not bad, f"region 표에 없는 코드: {[(r[0], r[1]) for r in bad]}"


def test_인천_외국인_중구서구는_행정구역_개편으로_조회_안_된다(db):
    """2026 개편으로 중구(28110)·서구(28260)가 사라졌다.

    어느 신설 구가 지정을 승계했는지 공식 확인 전까지 **임의로 매핑하지
    않는다**(§8). 대신 그 사실을 여기에 못박고, 인천 FOREIGN_ONLY
    커버리지를 PARTIAL 로 낮춰 '다 확인했다' 고 말하지 않게 한다.
    """
    with get_conn(db) as conn:
        orphan = {r["lawd_cd"] for r in conn.execute(
            "SELECT z.lawd_cd FROM land_permit_zone z "
            "LEFT JOIN region r ON r.lawd_cd = z.lawd_cd "
            "WHERE r.lawd_cd IS NULL")}
        cov = gate.coverage_of(conn, sido="인천광역시",
                               target_scope=gate.FOREIGN_ONLY)
    assert orphan == {"28110", "28260"}, f"예상 못 한 미매칭 코드: {orphan}"
    assert cov != gate.COMPLETE, (
        "일부만 조회되는데 커버리지가 COMPLETE 다")


# ── §6 테스트 1~6 · 광역 지정은 BLOCK ─────────────────────────────────

@pytest.mark.parametrize("name,lawd_cd", [
    ("서울 노원구", "11350"),
    ("서울 강남구", "11680"),
    ("성남시 분당구", "41135"),
    ("화성시 동탄구", "41597"),
    ("용인시 기흥구", "41463"),
    ("구리시", "41310"),
])
def test_1to6_내국인_비거주는_BLOCK(db, name, lawd_cd):
    d = judge(db, lawd_cd, sido="경기도" if lawd_cd.startswith("41") else "서울특별시")
    assert d.verdict == gate.BLOCKED, f"{name}: {d.verdict} — {d.reason}"
    assert d.residence_duty_months == 24


# ── §6 테스트 7·8 · 인천 오차단 방지 ──────────────────────────────────

def test_7_인천_부평_내국인은_외국인규칙으로_막히지_않는다(db):
    """**가장 중요한 회귀 테스트.**"""
    d = judge(db, "28237", sido="인천광역시")     # 부평구
    assert d.verdict != gate.BLOCKED, (
        f"내국인이 외국인 토허로 막혔다: {d.reason}")
    assert len(d.not_applicable) >= 1, "미적용 규칙이 기록되지 않았다"


def test_8_인천_부평_외국인_비거주는_BLOCK(db):
    d = judge(db, "28237", nationality=gate.FOREIGN, sido="인천광역시")
    assert d.verdict == gate.BLOCKED


# ── §6 테스트 9·10 · 지정 안 된 곳을 확대 적용하지 않는다 ─────────────

def test_9_군포시는_광역지정_대상이_아니다(db):
    """광역 지정을 다 넣었으므로 미지정 지역은 PASS 다.

    다만 필지 단위(재건축·모아타운)를 아직 못 봤다는 경고는 붙는다 —
    판정을 뒤집지는 않는다(§4).
    """
    d = judge(db, "41410", sido="경기도")     # 군포시
    assert d.verdict == gate.PASS, f"{d.verdict} — {d.reason}"
    assert d.check_code == gate.PARCEL_WARNING
    assert "필지" in d.reason


def test_10_안양_만안구에_동안구_규칙을_확대하지_않는다(db):
    """같은 안양시라도 동안구만 지정됐다. 시 단위로 뭉치면 오차단이다."""
    dongan = judge(db, "41173", sido="경기도")    # 동안구 — 지정됨
    manan = judge(db, "41171", sido="경기도")     # 만안구 — 미지정
    assert dongan.verdict == gate.BLOCKED
    assert manan.verdict == gate.PASS, f"만안구가 막혔다: {manan.reason}"


@pytest.mark.parametrize("name,lawd_cd", [
    ("용인 처인구", "41461"),      # 수지·기흥만 지정
    ("수원 권선구", "41113"),      # 영통·장안·팔달만 지정
    ("부천 원미구", "41192"),
])
def test_10b_같은_시의_미지정_구를_막지_않는다(db, name, lawd_cd):
    d = judge(db, lawd_cd, sido="경기도")
    assert d.verdict == gate.PASS, f"{name}: {d.verdict} — {d.reason}"


# ── §6 테스트 11·12 · 토지지분 ────────────────────────────────────────

def test_11_토지지분_6제곱미터는_허가대상이_아니다(db):
    """'6㎡ **초과**' 다. 6 과 같으면 대상이 아니다 — 한 글자 차이다."""
    assert judge(db, "11680", land_share=6.0).verdict == gate.PASS
    assert judge(db, "11680", land_share=6.01).verdict == gate.BLOCKED


def test_12_토지지분을_모르면_통과시키지_않는다(db):
    d = judge(db, "11680", land_share=None)
    assert d.verdict == gate.NEEDS_CHECK
    assert d.check_code == "LAND_SHARE_AREA_MISSING"


# ── §6 테스트 13·14 · 계약일 ──────────────────────────────────────────

def test_13_지정_만료_후를_자동_BLOCK하지_않는다(db):
    """서울 지정은 2026-12-31 까지다. 2027-01-01 계약을 자동으로 막으면
    안 되고, 연장·재지정 여부를 확인해야 한다."""
    d = judge(db, "11680", contract="2027-01-01")
    assert d.verdict != gate.BLOCKED
    assert d.verdict == gate.NEEDS_CHECK
    assert d.check_code == "DESIGNATION_EXPIRED_RECHECK"


def test_14_동탄은_2027년까지_유효하다(db):
    """경기 2026 추가지정은 2027-12-31 까지라 그때도 걸린다."""
    d = judge(db, "41597", contract="2027-01-01")
    assert d.verdict == gate.BLOCKED


# ── 실거주 계획 · 물건 유형 ───────────────────────────────────────────

def test_실거주_예정이면_허가받고_살_수_있다(db):
    d = judge(db, "11680", occupancy=gate.OCCUPANCY)
    assert d.verdict == gate.PASS_WITH_PERMIT and d.executable


def test_실거주_계획_미입력은_NEEDS_CHECK(db):
    d = judge(db, "11680", occupancy=None)
    assert d.verdict == gate.NEEDS_CHECK
    assert d.check_code == "OCCUPANCY_PLAN_MISSING"


def test_동탄은_아파트만_대상이라_연립은_안_걸린다(db):
    """APARTMENT_ONLY 규칙을 연립·다세대로 확대하면 안 된다."""
    d = judge(db, "41597", ptype=gate.ROW_HOUSE, same_complex=True)
    assert d.verdict == gate.PASS


def test_서울은_같은_단지_연립이면_걸린다(db):
    assert judge(db, "11680", ptype=gate.ROW_HOUSE,
                 same_complex=True).verdict == gate.BLOCKED
    assert judge(db, "11680", ptype=gate.ROW_HOUSE,
                 same_complex=False).verdict == gate.PASS


def test_같은_단지_아파트_여부를_모르면_NEEDS_CHECK(db):
    d = judge(db, "11680", ptype=gate.ROW_HOUSE, same_complex=None)
    assert d.verdict == gate.NEEDS_CHECK
    assert d.check_code == "SAME_COMPLEX_APARTMENT_STATUS_UNKNOWN"
