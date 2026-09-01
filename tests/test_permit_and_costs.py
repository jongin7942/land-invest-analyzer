"""작업지시서 §11 검증 테스트 10종.

각 테스트는 "이렇게 계산된다" 가 아니라 **"이렇게 계산하면 안 된다"** 를
지킨다. 지시서가 금지한 것이 코드에서 실제로 막히는지가 전부다.
"""
import pytest

from apt_engine.cash import costs
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.regulation import gate
from apt_engine.tax import acquisition

AS_OF = "2026-09-01"

# 인천 부평구 — 외국인 대상 토허구역 (2026-08-26 ~ 2027-08-25)
BUPYEONG_FOREIGN = gate.Rule(
    rule_id="LPZ_FOREIGN_ICN_BUPYEONG", target_scope=gate.FOREIGN_ONLY,
    nationality_scope="NON_KOREAN", residence_duty_months=24,
    status="CONFIRMED", effective_from="2026-08-26", effective_to="2027-08-25",
    property_scope="단독/다가구/아파트/연립/다세대")


@pytest.fixture
def rules_db(tmp_db):
    """규칙 CSV 를 실제로 import 한 DB. 코드가 아니라 데이터로 검증한다."""
    import subprocess
    import sys
    mig.migrate(tmp_db)
    env = {"APT_DB_PATH": tmp_db}
    import os
    env = {**os.environ, **env}
    for kind in ("tax", "cost", "loan"):
        subprocess.run([sys.executable, "-m", "apt_engine.cli", "rule",
                        "import", kind, f"rules/{kind}.csv"],
                       check=True, capture_output=True, env=env)
    return tmp_db


# ═══ 테스트 1~3 · 토허 국적 판정 ═══════════════════════════════════════

def test_1_내국인은_외국인_토허구역에_막히지_않는다():
    """**가장 중요한 테스트.**

    내국인이 부평 아파트를 사는데 외국인 대상 토허라는 이유로 후보에서
    빠지면 심각한 오류다. 어떤 커버리지 상태에서도 BLOCK 이 나오면 안 된다.
    """
    for coverage in (gate.INCOMPLETE, gate.PARTIAL, gate.COMPLETE):
        d = gate.evaluate([BUPYEONG_FOREIGN], nationality=gate.KOREAN,
                          purpose=gate.INVEST, coverage_status=coverage)
        assert d.verdict != gate.BLOCKED, (
            f"커버리지 {coverage} 에서 내국인이 외국인 토허로 막혔다")
        # 그 규칙이 '나에게 적용되지 않았다' 는 것이 기록에 남아야 한다
        assert len(d.not_applicable) == 1


def test_1b_커버리지가_확인되면_내국인은_PASS다():
    """지시서 §11 테스트 1 — 내국인에게 적용되지 않는 규칙뿐이면 PASS."""
    d = gate.evaluate([BUPYEONG_FOREIGN], nationality=gate.KOREAN,
                      purpose=gate.INVEST, coverage_status=gate.COMPLETE)
    assert d.verdict == gate.PASS and d.executable


def test_1c_커버리지_미확인이면_PASS로_단정하지_않는다():
    """지시서 §2 — 개별 확인 못 한 주소를 자동 PASS 하지 않는다.

    §11 테스트 1 과 §2 가 긴장 관계라서, 사유를 코드로 구분한다.
    막힌 이유가 '외국인 규칙' 이 아니라 '내국인 토허 미수집' 이어야 한다.
    """
    d = gate.evaluate([BUPYEONG_FOREIGN], nationality=gate.KOREAN,
                      purpose=gate.INVEST, coverage_status=gate.INCOMPLETE)
    assert d.verdict == gate.NEEDS_CHECK
    assert d.check_code == "COVERAGE_INCOMPLETE"


def test_2_외국인_비거주는_실거주의무로_막힌다():
    d = gate.evaluate([BUPYEONG_FOREIGN], nationality=gate.FOREIGN,
                      purpose=gate.INVEST, coverage_status=gate.COMPLETE)
    assert d.verdict == gate.BLOCKED and not d.executable
    assert d.residence_duty_months == 24


def test_2b_외국인이라도_실거주_목적이면_허가받고_살_수_있다():
    d = gate.evaluate([BUPYEONG_FOREIGN], nationality=gate.FOREIGN,
                      purpose=gate.LIVE_IN, coverage_status=gate.COMPLETE)
    assert d.verdict == gate.PASS_WITH_PERMIT and d.executable
    assert d.permit_required


def test_3_국적_미입력이면_NEEDS_CHECK다():
    d = gate.evaluate([BUPYEONG_FOREIGN], nationality=None,
                      purpose=gate.INVEST, coverage_status=gate.COMPLETE)
    assert d.verdict == gate.NEEDS_CHECK
    assert d.check_code == "NATIONALITY_UNKNOWN"
    assert not d.executable, "국적을 모르면 확정 추천 목록에 넣지 않는다"


# ═══ 테스트 4~6 · 취득세율 ════════════════════════════════════════════

@pytest.mark.parametrize("price,want_pct", [
    (600_000_000, 1.0),      # 6억 이하 1%
    (750_000_000, 2.0),      # (7.5 × 2/3 − 3) = 2%
    (900_000_000, 3.0),      # 9억 초과 3%
])
def test_4to6_일반세율_취득세(rules_db, price, want_pct):
    """6~9억 구간을 단순히 2% 로 처리하면 안 된다 — 산식이다."""
    with get_conn(rules_db) as conn:
        r = acquisition.assess(conn, price=price, as_of=AS_OF,
                               current_home_count=0, resulting_home_count=1)
    a = r.acquisition_tax
    assert a.known, "일반세율 취득세를 계산하지 못했다"
    assert a.amount / price * 100 == pytest.approx(want_pct, abs=0.01)


def test_구간_경계에서_세율이_이어진다(rules_db):
    """6억과 9억 경계에서 산식과 고정세율이 어긋나면 안 된다."""
    with get_conn(rules_db) as conn:
        def rate(p):
            r = acquisition.assess(conn, price=p, as_of=AS_OF,
                                   current_home_count=0, resulting_home_count=1)
            return r.acquisition_tax.amount / p * 100
        assert rate(600_000_000) == pytest.approx(1.0, abs=0.01)
        assert rate(600_000_001) == pytest.approx(1.0, abs=0.01)
        assert rate(899_999_999) == pytest.approx(3.0, abs=0.01)


def test_다주택_중과는_조건_없이_적용하지_않는다(rules_db):
    """8%·12% 는 법에 있지만, 주택 수만으로 적용하면 안 된다(지시서 §3)."""
    with get_conn(rules_db) as conn:
        r = acquisition.assess(conn, price=700_000_000, as_of=AS_OF,
                               current_home_count=2, resulting_home_count=3)
    # 조정대상지역 여부 등이 없으면 확정 세율을 내지 않는다
    assert (not r.acquisition_tax.known) or r.unknown, (
        "다주택 중과를 조건 확인 없이 확정했다")


# ═══ 테스트 7 · 중개보수 ══════════════════════════════════════════════

def test_7_중개보수_상한과_부가세(rules_db):
    """10억 → 0.5% → 500만원. 부가세는 과세유형 미확인이면 붙이지 않는다."""
    with get_conn(rules_db) as conn:
        fee, vat, _ = costs.brokerage(conn, price=1_000_000_000, as_of=AS_OF,
                                      region="인천")
    assert fee.amount == 5_000_000
    assert not vat.known, "과세유형을 모르는데 부가세를 자동 합산했다"


def test_7b_과세유형이_확인되면_계산한다(rules_db):
    with get_conn(rules_db) as conn:
        _, yes, _ = costs.brokerage(conn, price=1_000_000_000, as_of=AS_OF,
                                    region="인천", agent_vat_registered=True)
        _, no, _ = costs.brokerage(conn, price=1_000_000_000, as_of=AS_OF,
                                   region="인천", agent_vat_registered=False)
    assert yes.amount == 500_000
    assert no.amount == 0, "간이과세면 부가세가 0원이다"


# ═══ 테스트 8 · 대출 ══════════════════════════════════════════════════

def test_8_LTV만으로_대출가능액을_확정하지_않는다(rules_db):
    """DSR 이 없는데 LTV 금액을 '대출가능액' 으로 표시하면 안 된다.

    최종 한도는 LTV·DSR·절대한도·은행심사 중 **최솟값**이다.
    소득이 없으면 DSR 을 못 내므로 최종 한도도 못 낸다.
    """
    from apt_engine.regulation import mortgage
    with get_conn(rules_db) as conn:
        m = mortgage.calculate_final_mortgage_limit(
            conn, price=500_000_000, as_of=AS_OF, current_home_count=0,
            regulated_area=False, annual_income=None)   # ← 소득 없음
    # LTV 상한(policy_max)은 낼 수 있어도, 소득이 없으면 DSR 을 못 내므로
    # **최종 기대 한도(expected)** 는 확정할 수 없어야 한다.
    assert m.expected is None or m.unknown, (
        f"소득 없이 최종 대출가능액을 확정했다: expected={m.expected}")
    assert m.unknown, "무엇을 못 구했는지 남기지 않았다"


def test_8b_비규제지역_LTV_규칙이_있다(rules_db):
    with get_conn(rules_db) as conn:
        row = conn.execute(
            "SELECT value FROM loan_rule WHERE rule_type='LTV' "
            "AND regulated_area=0 AND last_verified IS NOT NULL").fetchone()
    assert row and row["value"] == pytest.approx(0.70)


def test_8c_다주택_수도권_LTV_0퍼센트_규칙이_있다(rules_db):
    with get_conn(rules_db) as conn:
        row = conn.execute(
            "SELECT value FROM loan_rule WHERE rule_type='LTV' "
            "AND home_status='다주택' AND region='수도권'").fetchone()
    assert row and row["value"] == pytest.approx(0.0)


# ═══ 테스트 9 · 국민주택채권 ══════════════════════════════════════════

def test_9_시가표준액_없이_매매가로_대체하지_않는다(rules_db):
    """채권 매입액은 시가표준액 × 법정 매입률이다. 매매가가 아니다."""
    with get_conn(rules_db) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM cost_rule WHERE cost_kind='국민주택채권' "
            "AND last_verified IS NOT NULL").fetchone()[0]
        item = costs._find(conn, "국민주택채권", price=700_000_000, as_of=AS_OF,
                           region=None, allow_unverified=False)
    if n == 0:
        assert item is None, "규칙이 없는데 값이 나왔다 — 매매가로 대체한 것"


# ═══ 테스트 10 · 커버리지 ═════════════════════════════════════════════

def test_10_데이터_0건은_자동_PASS도_자동_BLOCK도_아니다(tmp_db):
    """지시서 §2 — land_permit_zone 이 비어 있는 상태의 정직한 답."""
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        assert gate.coverage_of(conn, sido="서울특별시") == gate.INCOMPLETE
        d = gate.evaluate([], nationality=gate.KOREAN, purpose=gate.INVEST,
                          coverage_status=gate.INCOMPLETE)
    assert d.verdict == gate.NEEDS_CHECK
    assert not d.executable                    # 자동 PASS 아님
    assert d.verdict != gate.BLOCKED           # 자동 BLOCK 아님
    assert "미확인" in d.reason


def test_10b_만료된_지정은_적용하지_않는다(tmp_db):
    from apt_engine.repo import apt as repo
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
        conn.execute(
            "INSERT INTO land_permit_zone (lawd_cd, target_scope, buyer_scope,"
            " effective_from, effective_to, residence_duty_months, rule_id,"
            " status, jeonse_succession_allowed)"
            " VALUES ('28237','전체','ALL_BUYERS','2024-01-01','2025-12-31',"
            "24,'OLD','CONFIRMED',0)")
        conn.commit()
        rules = gate.load_rules(conn, lawd_cd="28237", as_of=AS_OF)
    assert rules == [], "만료된 토허 지정이 적용됐다"
