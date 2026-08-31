"""대출·세금·취득비용·실투자금 테스트 (2026-08-30 지시 §1~§19).

이 계층이 지키려는 선:
  1. 6~9억 취득세는 2% 고정이 아니라 점증세율이다
  2. 취득세·지방교육세·농특세를 하나의 세율로 합치지 않는다
  3. 농특세는 전용 85㎡ 를 실제로 판정한다 (일반분 / 감면분도 구분)
  4. 발표만 된 정책은 금액에 넣지 않는다
  5. 대출은 LTV 하나로 계산하지 않는다 — min(LTV, DSR, 상한, 요청액)
  6. 모르는 비용을 0원으로, 모르는 대출을 최대치로 세지 않는다
  7. "현금 3억" 은 매매가 3억이 아니라 실투자금 3억이다
  8. 과거를 분석할 때 현재 정책을 소급 적용하지 않는다
"""
import sqlite3

import pytest

from apt_engine import rules, units
from apt_engine.cash import costs as cost_mod
from apt_engine.cash import self_capital as capital_mod
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.invest import budget as budget_mod
from apt_engine.invest import ranking, roe as roe_mod
from apt_engine.regulation import mortgage as mortgage_mod
from apt_engine.repo import apt as repo
from apt_engine.tax import acquisition

TODAY = "2026-08-30"
LAWD = "28237"          # 인천 부평구


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


# ── 규칙 넣기 도우미 ───────────────────────────────────────────────────

def add_tax(conn, *, kind, key, conditions="{}", lo=0, hi=None, rate=None,
            formula=None, fixed=None, max_amount=None, verified=TODAY,
            status="ENACTED", verification=None, effective_from="2020-01-01",
            effective_to=None):
    conn.execute(
        "INSERT INTO tax_rule (tax_kind, rule_key, conditions_json, bracket_min, "
        " bracket_max, rate, rate_formula, fixed_amount, max_amount, effective_from, "
        " effective_to, source_name, last_verified, status, verification) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, key, conditions, lo, hi, rate, formula, fixed, max_amount,
         effective_from, effective_to, f"{kind} 근거", verified, status,
         verification or ("VERIFIED" if verified else "NEEDS_VERIFICATION")))


def add_loan(conn, *, rule_type, value, key=None, home_status=None,
             regulated_area=None, first_home_buyer=None, region=None,
             lo=0, hi=None, verified=TODAY, status="ENACTED",
             effective_from="2020-01-01", effective_to=None):
    conn.execute(
        "INSERT INTO loan_rule (rule_key, rule_type, value, home_status, "
        " regulated_area, first_home_buyer, region, price_min, price_max, "
        " effective_from, effective_to, source_name, last_verified, status, "
        " verification) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key or f"{rule_type}/{home_status or 'any'}", rule_type, value, home_status,
         regulated_area, first_home_buyer, region, lo, hi, effective_from,
         effective_to, "금융위 근거", verified, status,
         "VERIFIED" if verified else "NEEDS_VERIFICATION"))


def add_cost(conn, *, kind, key, rate=None, fixed=None, cap=None, region=None,
             lo=0, hi=None, vat=0, verified=TODAY):
    conn.execute(
        "INSERT INTO cost_rule (cost_kind, rule_key, region, price_min, price_max, "
        " rate, max_amount, fixed_amount, vat_applicable, effective_from, "
        " source_name, last_verified, status, verification) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, key, region, lo, hi, rate, cap, fixed, vat, "2020-01-01",
         f"{kind} 근거", verified, "ENACTED",
         "VERIFIED" if verified else "NEEDS_VERIFICATION"))


def standard_tax(conn):
    """지시 §1 의 1주택 표준세율 + §2 지방교육세 + §3 농특세."""
    add_tax(conn, kind="취득세", key="acq/1주택/6억이하",
            conditions='{"house_count": 1}', hi=600000000, rate=0.01)
    add_tax(conn, kind="취득세", key="acq/1주택/6~9억",
            conditions='{"house_count": 1}', lo=600000000, hi=900000000,
            formula="(base * 2 / 300000000 - 3) / 100")
    add_tax(conn, kind="취득세", key="acq/1주택/9억초과",
            conditions='{"house_count": 1}', lo=900000000, rate=0.03)
    add_tax(conn, kind="지방교육세", key="edu/6억이하",
            conditions='{"house_count": 1}', hi=600000000, rate=0.001)
    add_tax(conn, kind="지방교육세", key="edu/6~9억",
            conditions='{"house_count": 1}', lo=600000000, hi=900000000,
            formula="((base * 2 / 300000000 - 3) / 100) / 10")
    add_tax(conn, kind="지방교육세", key="edu/9억초과",
            conditions='{"house_count": 1}', lo=900000000, rate=0.003)
    add_tax(conn, kind="농어촌특별세", key="rural/85초과",
            conditions='{"exclusive_area_gt": 85}', rate=0.002)
    add_tax(conn, kind="농어촌특별세", key="rural/85이하",
            conditions='{"exclusive_area_lte": 85}', fixed=0)


def full_costs(conn):
    standard_tax(conn)
    add_tax(conn, kind="부가가치세", key="vat", rate=0.1)
    add_cost(conn, kind="중개보수", key="brok/2~9억", region="인천",
             lo=200000000, hi=900000000, rate=0.004, vat=1)
    add_cost(conn, kind="법무비", key="legal/5~10억", lo=500000000, hi=1000000000,
             fixed=600000, vat=1)
    add_cost(conn, kind="인지세", key="stamp/1~10억", lo=100000000, hi=1000000000,
             fixed=150000)
    add_cost(conn, kind="국민주택채권", key="bond", rate=0.0)
    add_cost(conn, kind="등기신청수수료", key="reg", fixed=13000)
    add_cost(conn, kind="증명서발급", key="cert", fixed=5000)


# ── §1 취득세 점증세율 ─────────────────────────────────────────────────

class TestAcquisitionRate:
    @pytest.mark.parametrize("eok, expected_rate", [
        (5.0, 0.01),
        (6.0, 0.01),
        (7.0, 1 / 60),          # (7 × 2 ÷ 3 − 3) % = 1.6667%
        (7.5, 0.02),
        (8.0, 7 / 300),         # 2.3333%
        (9.0, 0.03),
        (10.0, 0.03),
        (20.0, 0.03),
    ])
    def test_점증세율이_구간_안에서_연속으로_변한다(self, db, eok, expected_rate):
        with get_conn(db) as conn:
            standard_tax(conn)
            price = units.from_eok(eok)
            got = acquisition.assess(conn, price=price, as_of=TODAY,
                                     resulting_home_count=1, exclusive_area_m2=84)
        assert got.acquisition_tax.amount == pytest.approx(price * expected_rate, rel=1e-6)

    def test_6억에서_9억을_2퍼센트로_뭉뚱그리지_않는다(self, db):
        with get_conn(db) as conn:
            standard_tax(conn)
            seven = acquisition.assess(conn, price=units.from_eok(7), as_of=TODAY,
                                       resulting_home_count=1, exclusive_area_m2=84)
            eight = acquisition.assess(conn, price=units.from_eok(8), as_of=TODAY,
                                       resulting_home_count=1, exclusive_area_m2=84)
        # 둘 다 2% 라면 7억 세율 == 8억 세율이 된다. 실제로는 다르다.
        assert (seven.acquisition_tax.amount / units.from_eok(7)) < 0.02
        assert (eight.acquisition_tax.amount / units.from_eok(8)) > 0.02


# ── §2 세 세목 분리 ────────────────────────────────────────────────────

def test_세목을_하나의_세율로_합치지_않는다(db):
    with get_conn(db) as conn:
        standard_tax(conn)
        got = acquisition.assess(conn, price=units.from_eok(6), as_of=TODAY,
                                 resulting_home_count=1, exclusive_area_m2=101)
    assert got.acquisition_tax.amount == 6_000_000
    assert got.local_education_tax.amount == 600_000
    assert got.rural_special_tax_regular.amount == 1_200_000
    assert got.total == 7_800_000
    # 각각의 이름으로 접근 가능해야 한다(지시 §2)
    assert {i.name for i in got.items} >= {"취득세", "지방교육세", "농어촌특별세(일반)"}


def test_부가세목_규칙이_없으면_0이_아니라_확인_불가(db):
    with get_conn(db) as conn:
        add_tax(conn, kind="취득세", key="acq", conditions='{"house_count": 1}', rate=0.01)
        got = acquisition.assess(conn, price=units.from_eok(6), as_of=TODAY,
                                 resulting_home_count=1, exclusive_area_m2=84)
    assert got.local_education_tax.amount is None
    assert got.rural_special_tax_regular.amount is None
    assert not got.complete
    assert "이상" in got.label
    assert got.verification == rules.UNKNOWN


# ── §3 농어촌특별세 85㎡ ───────────────────────────────────────────────

class TestRuralSpecialTax:
    @pytest.mark.parametrize("area, expected", [(59, 0), (84, 0), (85, 0),
                                                (85.01, None), (101, None)])
    def test_국민주택규모_경계를_실제로_판정한다(self, db, area, expected):
        with get_conn(db) as conn:
            standard_tax(conn)
            got = acquisition.assess(conn, price=units.from_eok(6), as_of=TODAY,
                                     resulting_home_count=1, exclusive_area_m2=area)
        rural = got.rural_special_tax_regular.amount
        if expected == 0:
            assert rural == 0          # 85㎡ 이하는 비과세
        else:
            assert rural == pytest.approx(units.from_eok(6) * 0.002)

    def test_전용면적을_모르면_비과세로_넘기지_않는다(self, db):
        with get_conn(db) as conn:
            standard_tax(conn)
            got = acquisition.assess(conn, price=units.from_eok(6), as_of=TODAY,
                                     resulting_home_count=1, exclusive_area_m2=None)
        assert got.rural_special_tax_regular.amount is None
        assert "농어촌특별세(일반)" in got.unknown

    def test_일반분과_감면분을_다른_칸에_담는다(self, db):
        with get_conn(db) as conn:
            standard_tax(conn)
            add_tax(conn, kind="취득세감면", key="red/생애최초",
                    conditions='{"first_home_buyer": true}', rate=1.0,
                    max_amount=2_000_000)
            add_tax(conn, kind="농어촌특별세", key="rural/감면분", rate=0.2)
            got = acquisition.assess(conn, price=units.from_eok(6), as_of=TODAY,
                                     resulting_home_count=1, exclusive_area_m2=84,
                                     first_home_buyer=True)
        assert got.reduction.amount == 2_000_000                 # 한도 적용
        assert got.acquisition_tax.amount == 6_000_000 - 2_000_000
        assert got.rural_special_tax_regular.amount == 0         # 85㎡ 이하
        assert got.rural_special_tax_from_exemption.amount == 400_000   # 감면액 × 20%

    def test_감면이_없으면_감면분_농특세도_없다(self, db):
        with get_conn(db) as conn:
            standard_tax(conn)
            got = acquisition.assess(conn, price=units.from_eok(6), as_of=TODAY,
                                     resulting_home_count=1, exclusive_area_m2=84)
        assert got.rural_special_tax_from_exemption.amount == 0
        assert got.reduction.amount == 0


# ── §4 정책 생애주기 ───────────────────────────────────────────────────

class TestPolicyStatus:
    def test_발표만_된_감면은_금액에_넣지_않는다(self, db):
        with get_conn(db) as conn:
            standard_tax(conn)
            add_tax(conn, kind="취득세감면", key="red/개편안",
                    conditions='{"first_home_buyer": true}', rate=1.0,
                    status="ANNOUNCED")
            got = acquisition.assess(conn, price=units.from_eok(6), as_of=TODAY,
                                     resulting_home_count=1, exclusive_area_m2=84,
                                     first_home_buyer=True)
        assert got.reduction.amount == 0            # 감면 미반영
        assert got.acquisition_tax.amount == 6_000_000
        assert any("ANNOUNCED" in p for p in got.pending_policies)
        assert "향후 정책 변경 가능" in got.calc.intermediates

    def test_입법예고도_계산에_들어가지_않는다(self, db):
        with get_conn(db) as conn:
            standard_tax(conn)
            add_tax(conn, kind="취득세", key="acq/인하안",
                    conditions='{"house_count": 1}', rate=0.005, status="PROPOSED")
            got = acquisition.assess(conn, price=units.from_eok(6), as_of=TODAY,
                                     resulting_home_count=1, exclusive_area_m2=84)
        assert got.acquisition_tax.amount == 6_000_000   # 1%, 0.5% 가 아니다

    def test_시행_전_규칙은_pick_이_걸러낸다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, kind="취득세", key="a", rate=0.01, status="ANNOUNCED")
            rows = conn.execute("SELECT * FROM tax_rule").fetchall()
        assert rules.pick(rows, {}) == []
        assert len(rules.pick(rows, {}, statuses=(rules.ANNOUNCED,))) == 1


def test_다주택_규칙이_없으면_취득세를_지어내지_않는다(db):
    with get_conn(db) as conn:
        standard_tax(conn)          # 1주택 규칙만 있다
        got = acquisition.assess(conn, price=units.from_eok(6), as_of=TODAY,
                                 current_home_count=1, resulting_home_count=2,
                                 regulated_area=True, exclusive_area_m2=84)
    assert got.acquisition_tax.amount is None
    assert "중과세율 규칙 입력이 필요" in got.acquisition_tax.note


# ── §5~§8 대출 ─────────────────────────────────────────────────────────

class TestMortgage:
    def test_LTV_하나로_계산하지_않는다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="LTV", value=0.7)
            add_loan(conn, rule_type="DSR", value=0.4)
            got = mortgage_mod.calculate_final_mortgage_limit(
                conn, price=units.from_eok(10), as_of=TODAY, annual_income=units.from_eok(0.6),
                interest_rate=0.045, mortgage_term_years=30)
        names = {l.name for l in got.limits}
        assert {"LTV 한도", "DSR 한도"} <= names
        # 소득 6천만원으로는 7억을 감당할 수 없다 → DSR 이 한도를 결정한다
        assert got.binding == "DSR 한도"
        assert got.policy_max < got.limits[0].amount

    def test_소득이_없으면_DSR_한도를_계산하지_않는다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="LTV", value=0.7)
            add_loan(conn, rule_type="DSR", value=0.4)
            got = mortgage_mod.calculate_final_mortgage_limit(
                conn, price=units.from_eok(10), as_of=TODAY, interest_rate=0.045)
        assert "DSR 한도" in got.unknown
        assert "작을 수 있습니다" in got.calc.intermediates["주의"]

    def test_스트레스_가산금리가_한도를_줄인다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="DSR", value=0.4)
            plain, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="무주택",
                regulated_area=False, annual_income=units.from_eok(1),
                interest_rate=0.04)
            add_loan(conn, rule_type="STRESS_DSR", value=150)
            stressed, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="무주택",
                regulated_area=False, annual_income=units.from_eok(1),
                interest_rate=0.04)
        assert stressed.amount < plain.amount
        assert "150bp" in stressed.note

    def test_규제지역과_비규제지역_규칙이_섞이지_않는다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="LTV", value=0.7, regulated_area=0)
            add_loan(conn, rule_type="LTV", value=0.4, regulated_area=1)
            free, _ = mortgage_mod.calculate_ltv_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="무주택",
                regulated_area=False)
            reg, _ = mortgage_mod.calculate_ltv_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="무주택",
                regulated_area=True)
        assert free.amount == units.from_eok(7)
        assert reg.amount == units.from_eok(4)

    def test_주택수에_따라_다른_LTV_가_잡힌다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="LTV", value=0.7, home_status="무주택")
            add_loan(conn, rule_type="LTV", value=0.4, home_status="다주택")
            none, _ = mortgage_mod.calculate_ltv_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="무주택",
                regulated_area=False)
            many, _ = mortgage_mod.calculate_ltv_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="다주택",
                regulated_area=False)
        assert none.amount == units.from_eok(7)
        assert many.amount == units.from_eok(4)

    def test_절대상한이_있으면_그것도_후보에_들어간다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="LTV", value=0.7)
            add_loan(conn, rule_type="MORTGAGE_CAP", value=units.from_eok(6))
            got = mortgage_mod.calculate_final_mortgage_limit(
                conn, price=units.from_eok(10), as_of=TODAY)
        assert got.policy_max == units.from_eok(6)
        assert got.binding == "절대 상한"

    def test_규칙이_없으면_대출을_추정하지_않는다(self, db):
        with get_conn(db) as conn:
            got = mortgage_mod.calculate_final_mortgage_limit(
                conn, price=units.from_eok(10), as_of=TODAY)
        assert got.policy_max is None
        assert got.expected is None
        assert "확인 불가" in got.label

    def test_은행_견적이_없으면_정책_추정치임을_밝힌다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="LTV", value=0.7)
            got = mortgage_mod.calculate_final_mortgage_limit(
                conn, price=units.from_eok(10), as_of=TODAY)
        assert got.expected == got.policy_max
        assert "실제 금융기관 심사 결과와 다를 수 있습니다" in got.calc.intermediates["예상액 근거"]

    def test_은행_견적이_있으면_그것을_쓴다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="LTV", value=0.7)
            got = mortgage_mod.calculate_final_mortgage_limit(
                conn, price=units.from_eok(10), as_of=TODAY,
                bank_quote=units.from_eok(5))
        assert got.policy_max == units.from_eok(7)
        assert got.expected == units.from_eok(5)

    def test_만기일시상환은_산정방식을_모른다고_말한다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="DSR", value=0.4)
            got, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="무주택",
                regulated_area=False, annual_income=units.from_eok(1),
                interest_rate=0.04, repayment_type="만기일시")
        assert got.amount is None
        assert "감독규정" in got.note

    def test_원금균등은_첫해_상환액으로_본다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="DSR", value=0.4)
            level, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="무주택",
                regulated_area=False, annual_income=units.from_eok(1),
                interest_rate=0.04, repayment_type="원리금균등")
            equal, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="무주택",
                regulated_area=False, annual_income=units.from_eok(1),
                interest_rate=0.04, repayment_type="원금균등")
        # 원금균등은 첫 해 부담이 커서 한도가 더 작게 나온다
        assert equal.amount < level.amount

    def test_기존_대출이_DSR_여력을_줄인다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="DSR", value=0.4)
            free, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="무주택",
                regulated_area=False, annual_income=units.from_eok(1),
                interest_rate=0.04)
            loaded, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=TODAY, home_status="무주택",
                regulated_area=False, annual_income=units.from_eok(1),
                existing_annual_payment=20_000_000, interest_rate=0.04)
        assert loaded.amount < free.amount


# ── §9·§10 거래비용 ────────────────────────────────────────────────────

class TestTransactionCosts:
    def test_중개보수와_부가세를_분리한다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            fee, vat, _ = cost_mod.brokerage(conn, price=units.from_eok(6),
                                             as_of=TODAY, region="인천")
        assert fee.amount == 2_400_000
        assert vat.amount == 240_000
        assert fee.name != vat.name

    def test_기본은_법정_상한요율이다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            fee, _, _ = cost_mod.brokerage(conn, price=units.from_eok(6),
                                            as_of=TODAY, region="인천")
        assert "법정 상한요율" in fee.note

    def test_협의_요율이_상한을_넘으면_상한으로_깎는다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            fee, _, _ = cost_mod.brokerage(conn, price=units.from_eok(6), as_of=TODAY,
                                            region="인천", negotiated_rate=0.009)
        assert fee.amount == 2_400_000
        assert "법정 상한을 넘어" in fee.note

    def test_협의_요율이_낮으면_그것을_쓴다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            fee, vat, _ = cost_mod.brokerage(conn, price=units.from_eok(6), as_of=TODAY,
                                             region="인천", negotiated_rate=0.002)
        assert fee.amount == 1_200_000
        assert vat.amount == 120_000

    def test_법무비를_정액으로_박아넣지_않는다(self, db):
        with get_conn(db) as conn:
            standard_tax(conn)          # 법무비 규칙 없음
            legal = cost_mod.calculate_legal_fee(conn, price=units.from_eok(6),
                                                 as_of=TODAY)
        assert legal.base_fee.amount is None
        assert "임의의 정액을 쓰지 않습니다" in legal.base_fee.note

    def test_법무비는_기본보수와_실비를_나눈다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            legal = cost_mod.calculate_legal_fee(conn, price=units.from_eok(6),
                                                 as_of=TODAY)
        assert legal.base_fee.amount == 600_000
        assert legal.vat.amount == 60_000
        assert {i.name for i in legal.registration_expenses} == {
            "인지세", "국민주택채권", "등기신청수수료"}
        assert legal.total == 600_000 + 60_000 + 150_000 + 0 + 13_000 + 5_000

    def test_실비를_모르면_합계에_0으로_넣지_않는다(self, db):
        with get_conn(db) as conn:
            standard_tax(conn)
            add_cost(conn, kind="법무비", key="legal", fixed=600_000)
            legal = cost_mod.calculate_legal_fee(conn, price=units.from_eok(6),
                                                 as_of=TODAY)
        assert "인지세" in legal.unknown
        assert legal.total == 600_000
        assert "이상" in legal.label


# ── §11·§12·§17 실투자금 ───────────────────────────────────────────────

class TestSelfCapital:
    def test_총취득비용은_매수가에_모든_비용을_더한_값이다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                      lawd_cd=LAWD, exclusive_area_m2=84,
                                      use_mortgage=False)
        assert cap.total_purchase_cost > cap.purchase_price
        assert cap.total_purchase_cost == sum(i.amount for i in cap.cost_items
                                              if i.known)

    def test_전세_승계가_아니면_보증금을_차감하지_않는다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                      lawd_cd=LAWD, exclusive_area_m2=84,
                                      use_mortgage=False,
                                      jeonse_deposit=units.from_eok(3))
        assert cap.assumable_deposit == 0
        assert cap.required == cap.total_purchase_cost

    def test_전세_승계면_보증금을_차감한다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            conn.execute(
                "INSERT INTO land_permit_zone (lawd_cd, designator, target_scope, "
                " target_use, effective_from, effective_to, jeonse_succession_allowed, "
                " source_name, last_verified) VALUES (?,?,?,?,?,?,?,?,?)",
                (LAWD, "인천시장", "내국인", "주거용", "2020-01-01", "2019-12-31", 1,
                 "고시", TODAY))     # 이미 만료된 지정 → 현재는 토허 아님
            cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                      lawd_cd=LAWD, exclusive_area_m2=84,
                                      use_mortgage=False, assume_jeonse=True,
                                      jeonse_deposit=units.from_eok(3))
        assert cap.assumable_deposit == units.from_eok(3)
        assert cap.required == cap.total_purchase_cost - units.from_eok(3)

    def test_대출을_모르면_실투자금을_확정하지_않는다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)            # 대출 규칙 없음
            cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                      lawd_cd=LAWD, exclusive_area_m2=84)
        assert cap.required is None
        assert "대출 가능액" in cap.unknown
        assert cap.affordable(units.from_eok(3)) is None    # '가능' 으로 넘기지 않는다
        assert cap.required_without_loan == cap.total_purchase_cost

    def test_비용을_모르면_확정이_아니라_예상이다(self, db):
        with get_conn(db) as conn:
            standard_tax(conn)          # 법무비·실비 규칙 없음
            cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                      lawd_cd=LAWD, exclusive_area_m2=84,
                                      use_mortgage=False)
        assert not cap.confirmed
        assert cap.title == "예상 실투자금"
        assert cap.unknown

    def test_모든_항목이_확인되면_확정이라고_부른다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                      lawd_cd=LAWD, exclusive_area_m2=84,
                                      use_mortgage=False)
        assert cap.confirmed
        assert cap.title == "실투자금 확정"

    def test_미검증_규칙이_섞이면_확정이_아니다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            conn.execute("UPDATE tax_rule SET verification='NEEDS_VERIFICATION' "
                         "WHERE tax_kind='농어촌특별세'")
            cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                      lawd_cd=LAWD, exclusive_area_m2=84,
                                      use_mortgage=False)
        assert not cap.confirmed
        assert cap.verification == rules.NEEDS_VERIFICATION

    def test_대출과_보증금을_빼면_실투자금이_준다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            add_loan(conn, rule_type="LTV", value=0.5)
            cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                      lawd_cd=LAWD, exclusive_area_m2=84)
        assert cap.available_mortgage == units.from_eok(3)
        assert cap.required == cap.total_purchase_cost - units.from_eok(3)
        assert cap.required < cap.required_without_loan


# ── §13·§14 예산 판정 ──────────────────────────────────────────────────

class TestBudget:
    def test_매매가가_아니라_실투자금으로_거른다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            add_loan(conn, rule_type="LTV", value=0.6)
            cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                      lawd_cd=LAWD, exclusive_area_m2=84)
        cash = units.from_eok(3)
        # 매매가 6억 > 현금 3억 이지만 실투자금은 3억 미만이라 살 수 있다
        assert cap.purchase_price > cash
        assert cap.required < cash
        assert cap.affordable(cash) is True

    def test_투자금_사용_효율(self, db):
        with get_conn(db) as conn:
            full_costs(conn)
            add_loan(conn, rule_type="LTV", value=0.5)
            cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                      lawd_cd=LAWD, exclusive_area_m2=84)
        use = cap.cash_utilization(units.from_eok(4))
        assert use == pytest.approx(cap.required / units.from_eok(4))

    def test_프로필은_DB에_저장된다(self, db):
        with get_conn(db) as conn:
            budget_mod.Profile(name="종인", available_cash=units.from_eok(3),
                               annual_income=units.from_eok(0.8),
                               interest_rate=0.045).save(conn)
            got = budget_mod.Profile.load(conn, "종인")
        assert got.available_cash == units.from_eok(3)
        assert got.interest_rate == 0.045

    def test_현금이_없으면_판정을_거부한다(self, db):
        with get_conn(db) as conn:
            with pytest.raises(ValueError, match="가용 현금"):
                budget_mod.screen(conn, profile=budget_mod.Profile(), as_of=TODAY)

    def test_확정하지_못한_단지는_가능_목록에_넣지_않는다(self, db):
        with get_conn(db) as conn:
            full_costs(conn)            # 대출 규칙 없음 → required 가 None
            repo.upsert_complexes(conn, [{
                "kapt_code": "K1", "name": "테스트", "name_norm": "테스트",
                "lawd_cd": LAWD}])
            cid = conn.execute("SELECT id FROM complex").fetchone()[0]
            conn.execute(
                "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym, "
                " window_months, representative_price, method, sample_n, "
                " confidence, data_grade, engine_version, calc_trace) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cid, "84", "202608", 6, units.from_eok(6), "median", 10, "HIGH",
                 "CONFIRMED", "0.9.0", "{}"))
            result = budget_mod.screen(
                conn, profile=budget_mod.Profile(available_cash=units.from_eok(3)),
                as_of=TODAY, area_band="84")
        assert result.affordable == []
        assert len(result.undecidable) == 1


# ── §15·§16 수익률과 랭킹 ──────────────────────────────────────────────

class TestReturn:
    def _capital(self, conn):
        full_costs(conn)
        add_loan(conn, rule_type="LTV", value=0.5)
        return capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                   lawd_cd=LAWD, exclusive_area_m2=84)

    def test_미래_매도가가_없으면_수익률을_만들지_않는다(self, db):
        with get_conn(db) as conn:
            cap = self._capital(conn)
            got = roe_mod.expected_return(conn, capital=cap, future_sale_price=None,
                                          as_of=TODAY, holding_years=5)
        assert got.roe is None
        assert any("상승률을 지어내지 않습니다" in u for u in got.unknown)

    def test_양도세_규칙이_없으면_매도비용이_확인_불가다(self, db):
        with get_conn(db) as conn:
            cap = self._capital(conn)
            got = roe_mod.expected_return(conn, capital=cap,
                                          future_sale_price=units.from_eok(8),
                                          as_of=TODAY, holding_years=5,
                                          annual_rate=0.045, region="인천")
        assert "양도소득세" in got.unknown
        assert got.exit_cost.total > 0          # 중개보수는 계산된다

    def test_ROE_는_내_돈_대비_수익률이다(self, db):
        with get_conn(db) as conn:
            cap = self._capital(conn)
            add_tax(conn, kind="양도소득세", key="cgt", rate=0.2)
            add_tax(conn, kind="지방소득세", key="linc", rate=0.02)
            got = roe_mod.expected_return(conn, capital=cap,
                                          future_sale_price=units.from_eok(8),
                                          as_of=TODAY, holding_years=5,
                                          annual_rate=0.045, region="인천")
        assert got.roe == pytest.approx(got.profit / cap.required)
        # 가격은 33% 올랐지만 레버리지 때문에 ROE 는 그와 다르다
        assert got.price_return == pytest.approx(1 / 3, rel=1e-6)
        assert got.roe != pytest.approx(got.price_return)

    def test_금융비용은_실제금리로_계산한다(self, db):
        low = roe_mod.financing_cost(units.from_eok(3), annual_rate=0.04, years=5)
        high = roe_mod.financing_cost(units.from_eok(3), annual_rate=0.06, years=5)
        assert high.amount > low.amount

    def test_금리를_모르면_이자를_0으로_세지_않는다(self, db):
        got = roe_mod.financing_cost(units.from_eok(3), annual_rate=None, years=5)
        assert got.amount is None

    def test_하락_시나리오가_없으면_DOWNSIDE_RISK_를_추정하지_않는다(self, db):
        with get_conn(db) as conn:
            cap = self._capital(conn)
            add_tax(conn, kind="양도소득세", key="cgt", rate=0.2)
            add_tax(conn, kind="지방소득세", key="linc", rate=0.02)
            ret = roe_mod.expected_return(conn, capital=cap,
                                          future_sale_price=units.from_eok(8),
                                          as_of=TODAY, holding_years=5,
                                          annual_rate=0.045, region="인천")
            m = ranking.build(conn, capital=cap, expected=ret,
                              available_cash=units.from_eok(4), as_of=TODAY)
        assert m.downside_risk is None
        assert any("DOWNSIDE_RISK" in u for u in m.unknown)
        assert not m.comparable

    def test_랭킹_지표_8종이_모두_나온다(self, db):
        with get_conn(db) as conn:
            cap = self._capital(conn)
            add_tax(conn, kind="양도소득세", key="cgt", rate=0.2)
            add_tax(conn, kind="지방소득세", key="linc", rate=0.02)
            ret = roe_mod.expected_return(conn, capital=cap,
                                          future_sale_price=units.from_eok(8),
                                          as_of=TODAY, holding_years=5,
                                          annual_rate=0.045, region="인천")
            m = ranking.build(conn, capital=cap, expected=ret,
                              available_cash=units.from_eok(4),
                              downside_sale_price=units.from_eok(5), as_of=TODAY,
                              annual_rate=0.045, region="인천")
        assert set(m.as_dict()) == set(ranking.FIELDS)
        assert m.comparable
        assert m.downside_risk < 0          # 5억에 팔면 손실이다
        assert m.leverage_ratio > 0


# ── §18 시점 관리 (백테스트) ───────────────────────────────────────────

class TestPointInTime:
    def test_현재_세법을_과거에_소급하지_않는다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, kind="취득세", key="acq/2021",
                    conditions='{"house_count": 1}', rate=0.01,
                    effective_from="2021-01-01", effective_to="2025-12-31")
            add_tax(conn, kind="취득세", key="acq/2026",
                    conditions='{"house_count": 1}', rate=0.03,
                    effective_from="2026-01-01")
            past = acquisition.assess(conn, price=units.from_eok(6), as_of="2021-06-01",
                                      resulting_home_count=1, exclusive_area_m2=84)
            now = acquisition.assess(conn, price=units.from_eok(6), as_of=TODAY,
                                     resulting_home_count=1, exclusive_area_m2=84)
        assert past.acquisition_tax.amount == 6_000_000
        assert now.acquisition_tax.amount == 18_000_000

    def test_현재_LTV_를_과거에_소급하지_않는다(self, db):
        with get_conn(db) as conn:
            add_loan(conn, rule_type="LTV", value=0.7, key="ltv/2021",
                     effective_from="2021-01-01", effective_to="2025-12-31")
            add_loan(conn, rule_type="LTV", value=0.4, key="ltv/2026",
                     effective_from="2026-01-01")
            past, _ = mortgage_mod.calculate_ltv_limit(
                conn, price=units.from_eok(10), as_of="2021-06-01",
                home_status="무주택", regulated_area=False)
            now, _ = mortgage_mod.calculate_ltv_limit(
                conn, price=units.from_eok(10), as_of=TODAY,
                home_status="무주택", regulated_area=False)
        assert past.amount == units.from_eok(7)
        assert now.amount == units.from_eok(4)


# ── §19 조합 테스트 (매트릭스) ─────────────────────────────────────────

PRICES_EOK = (5, 6, 7, 7.5, 8, 9, 10, 15, 20)
AREAS = (59, 84, 101)


@pytest.mark.parametrize("eok", PRICES_EOK)
@pytest.mark.parametrize("area", AREAS)
def test_가격x면적_조합에서_취득비용이_모순없이_나온다(db, eok, area):
    with get_conn(db) as conn:
        full_costs(conn)
        add_loan(conn, rule_type="LTV", value=0.5)
        add_loan(conn, rule_type="DSR", value=0.4)
        cap = capital_mod.compute(
            conn, price=units.from_eok(eok), as_of=TODAY, lawd_cd=LAWD,
            exclusive_area_m2=area, annual_income=units.from_eok(1),
            interest_rate=0.045)
    assert cap.total_purchase_cost > cap.purchase_price
    assert cap.required is not None
    assert cap.required <= cap.required_without_loan
    # 85㎡ 이하는 농특세가 0, 초과는 양수여야 한다
    rural = next(i for i in cap.cost_items if i.name == "농어촌특별세(일반)")
    assert (rural.amount == 0) if area <= 85 else (rural.amount > 0)


@pytest.mark.parametrize("homes, regulated", [(0, False), (0, True),
                                              (1, False), (1, True),
                                              (2, False), (2, True)])
def test_주택수x규제지역_조합에서_규칙이_없으면_지어내지_않는다(db, homes, regulated):
    with get_conn(db) as conn:
        standard_tax(conn)              # 1주택 규칙만 있다
        if regulated:
            conn.execute(
                "INSERT INTO regulation_zone (lawd_cd, zone_type, effective_from, "
                " source_name, last_verified) VALUES (?,?,?,?,?)",
                (LAWD, "조정대상지역", "2020-01-01", "국토부 공고", TODAY))
        cap = capital_mod.compute(conn, price=units.from_eok(6), as_of=TODAY,
                                  lawd_cd=LAWD, current_home_count=homes,
                                  exclusive_area_m2=84, use_mortgage=False)
    tax_item = next(i for i in cap.cost_items if i.name == "취득세")
    if homes == 0:                       # 취득 후 1주택 → 규칙이 있다
        assert tax_item.amount == 6_000_000
    else:                                # 취득 후 2주택 이상 → 규칙 없음
        assert tax_item.amount is None
        assert "취득세" in cap.unknown


# ── 서식과 테이블 컬럼이 어긋나지 않는다 ────────────────────────────────

def test_입력_서식이_실제_테이블_컬럼을_모두_담는다():
    """서식에 없는 컬럼은 사용자가 채울 방법이 없다.

    009 에서 status·verification·rule_type 을 추가했는데 서식을 안 고치면,
    사용자가 발표 단계 정책을 시행 중인 것으로 넣게 된다.
    """
    import tempfile
    from pathlib import Path
    from apt_engine.repo import rules as rule_repo

    tmp = Path(tempfile.mkdtemp())
    for kind, (_, columns) in rule_repo.TABLES.items():
        path = rule_repo.write_template(kind, tmp / f"{kind}.csv")
        header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
        assert set(columns) <= set(header), f"{kind} 서식에 빠진 컬럼: " \
                                            f"{set(columns) - set(header)}"


def test_서식_예시는_전부_주석이라_그대로_넣어도_규칙이_안_생긴다(tmp_path):
    from apt_engine.repo import rules as rule_repo

    for kind in rule_repo.TEMPLATES:
        path = rule_repo.write_template(kind, tmp_path / f"{kind}.csv")
        body = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                if ln and not ln.startswith("#")]
        assert len(body) == 1, f"{kind} 서식에 값이 들어 있습니다: {body[1:]}"


def test_VERIFIED_인데_확인일이_없으면_거부한다(db, tmp_path):
    from apt_engine.repo import rules as rule_repo

    path = tmp_path / "tax.csv"
    path.write_text(
        "tax_kind,rule_key,rate,effective_from,last_verified,verification\n"
        "취득세,acq/a,0.01,2020-01-01,,VERIFIED\n", encoding="utf-8")
    with get_conn(db) as conn:
        with pytest.raises(rule_repo.RuleImportError, match="확인한 날짜"):
            rule_repo.import_csv(conn, "tax", path)


def test_알_수_없는_status_는_거부한다(db, tmp_path):
    from apt_engine.repo import rules as rule_repo

    path = tmp_path / "tax.csv"
    path.write_text(
        "tax_kind,rule_key,rate,effective_from,last_verified,status\n"
        "취득세,acq/a,0.01,2020-01-01,2026-08-30,아마도시행\n", encoding="utf-8")
    with get_conn(db) as conn:
        with pytest.raises(rule_repo.RuleImportError, match="ENACTED"):
            rule_repo.import_csv(conn, "tax", path)


def test_실제_규칙_CSV_가_그대로_들어간다(db):
    """리포지토리에 들어 있는 rules/*.csv 가 항상 임포트 가능해야 한다."""
    from pathlib import Path
    from apt_engine.repo import rules as rule_repo

    root = Path(__file__).resolve().parents[1] / "rules"
    with get_conn(db) as conn:
        for kind in ("tax", "cost", "loan", "permit"):
            path = root / f"{kind}.csv"
            if path.exists():
                rule_repo.import_csv(conn, kind, path)
        # 취득세 6~9억 점증세율이 실제로 동작하는지 확인
        got = acquisition.assess(conn, price=units.from_eok(7.5), as_of=TODAY,
                                 resulting_home_count=1, exclusive_area_m2=84)
    assert got.acquisition_tax.amount == pytest.approx(units.from_eok(7.5) * 0.02)


# ── 실제 rules/*.csv 로 하는 검증 (2026-08-31 제공값) ───────────────────
#
# 아래 테스트는 픽스처가 아니라 **리포지토리에 들어 있는 실제 규칙 파일**을 쓴다.
# CSV 를 잘못 고치면 여기서 깨진다.

REAL_AS_OF = "2026-08-31"
LAWD_FREE = "28237"          # 인천 부평구 — 비규제
LAWD_REG = "28185"           # 인천 연수구 — 아래 픽스처에서 조정대상지역으로 지정


@pytest.fixture
def real_rules(db):
    """리포지토리의 rules/*.csv 를 그대로 적재한 DB."""
    from pathlib import Path
    from apt_engine.repo import rules as rule_repo

    root = Path(__file__).resolve().parents[1] / "rules"
    with get_conn(db) as conn:
        for kind in ("tax", "cost", "loan"):
            rule_repo.import_csv(conn, kind, root / f"{kind}.csv")
        conn.execute(
            "INSERT INTO regulation_zone (lawd_cd, zone_type, effective_from, "
            " source_name, last_verified) VALUES (?,?,?,?,?)",
            (LAWD_REG, "조정대상지역", "2025-10-16", "테스트용 지정", REAL_AS_OF))
    return db


class TestVerifiedRates:
    """§4 취득세 점증세율 — 조문의 반올림까지 맞는가."""

    @pytest.mark.parametrize("eok, expected_rate, expected_tax", [
        (5.0,   0.0100,   5_000_000),
        (6.0,   0.0100,   6_000_000),
        (7.0,   0.0167,  11_690_000),    # 1.6666…% → 넷째 자리 반올림 1.67%
        (7.5,   0.0200,  15_000_000),
        (8.0,   0.0233,  18_640_000),    # 2.3333…% → 2.33%
        (9.0,   0.0300,  27_000_000),
        (10.0,  0.0300,  30_000_000),
        (20.0,  0.0300,  60_000_000),
    ])
    def test_취득세율과_세액(self, real_rules, eok, expected_rate, expected_tax):
        with get_conn(real_rules) as conn:
            got = acquisition.assess(conn, price=units.from_eok(eok), as_of=REAL_AS_OF,
                                     resulting_home_count=1, exclusive_area_m2=84)
        assert got.acquisition_tax.amount == expected_tax
        assert got.acquisition_tax.amount / units.from_eok(eok) == pytest.approx(
            expected_rate, abs=1e-9)

    def test_반올림하지_않으면_틀린다(self, real_rules):
        """조문의 반올림이 빠지면 7억에서 23,333원이 어긋난다."""
        with get_conn(real_rules) as conn:
            got = acquisition.assess(conn, price=units.from_eok(7), as_of=REAL_AS_OF,
                                     resulting_home_count=1, exclusive_area_m2=84)
        unrounded = units.from_eok(7) * (7 * 2 / 3 - 3) / 100
        assert got.acquisition_tax.amount != int(unrounded)
        assert got.acquisition_tax.amount - unrounded == pytest.approx(23_333, abs=1)

    def test_지방교육세는_취득세율의_1할이다(self, real_rules):
        with get_conn(real_rules) as conn:
            for eok in (5, 7, 8, 10):
                got = acquisition.assess(conn, price=units.from_eok(eok),
                                         as_of=REAL_AS_OF, resulting_home_count=1,
                                         exclusive_area_m2=84)
                assert got.local_education_tax.amount == got.acquisition_tax.amount // 10


class TestHeavyTaxTable:
    """§6 다주택 판정표 — 조합마다 맞는 세율이 나오는가."""

    @pytest.mark.parametrize("homes, regulated, temporary, rate", [
        (1, False, False, 0.03),
        (1, True,  False, 0.03),
        (2, False, False, 0.03),          # 2주택 비조정 → 일반세율
        (2, True,  False, 0.08),          # 2주택 조정   → 8%
        (2, True,  True,  0.03),          # 일시적 2주택 → 일반세율
        (3, False, False, 0.08),          # 3주택 비조정 → 8%
        (3, True,  False, 0.12),          # 3주택 조정   → 12%
        (4, False, False, 0.12),          # 4주택 비조정 → 12%
        (4, True,  False, 0.12),
        (5, True,  False, 0.12),
    ])
    def test_판정표(self, real_rules, homes, regulated, temporary, rate):
        with get_conn(real_rules) as conn:
            got = acquisition.assess(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF,
                resulting_home_count=homes, regulated_area=regulated,
                temporary_two_home=temporary, exclusive_area_m2=84)
        assert got.acquisition_tax.amount == pytest.approx(units.from_eok(10) * rate)

    def test_법인은_개인_세율이_잡히지_않는다(self, real_rules):
        with get_conn(real_rules) as conn:
            got = acquisition.assess(conn, price=units.from_eok(10), as_of=REAL_AS_OF,
                                     resulting_home_count=1, buyer_type="법인",
                                     exclusive_area_m2=84)
        assert got.acquisition_tax.amount == pytest.approx(units.from_eok(10) * 0.12)

    def test_중과_시_지방교육세는_아직_확인_불가다(self, real_rules):
        """중과세율 건의 지방교육세 산식을 받지 못했다. 0원으로 세지 않는다."""
        with get_conn(real_rules) as conn:
            got = acquisition.assess(conn, price=units.from_eok(10), as_of=REAL_AS_OF,
                                     resulting_home_count=2, regulated_area=True,
                                     exclusive_area_m2=84)
        assert got.local_education_tax.amount is None
        assert "지방교육세" in got.unknown


class TestRealCosts:
    """§7 인지세 · §9 법무사 보수 · §10 부가세."""

    @pytest.mark.parametrize("eok, stamp", [
        (0.05, 0), (0.2, 20_000), (0.4, 40_000), (0.8, 70_000),
        (5.0, 150_000), (15.0, 350_000),
    ])
    def test_인지세_구간(self, real_rules, eok, stamp):
        with get_conn(real_rules) as conn:
            legal = cost_mod.calculate_legal_fee(conn, price=units.from_eok(eok),
                                                 as_of=REAL_AS_OF)
        got = next(i for i in legal.registration_expenses if i.name == "인지세")
        assert got.amount == stamp
        assert got.verification == rules.VERIFIED

    @pytest.mark.parametrize("eok, fee", [
        (0.4,  105_000),                       # 5천만원까지 정액
        (1.0,  105_000 + 50_000_000 * 0.0005),   # 130,000
        (2.0,  180_000),
        (5.0,  180_000 + 300_000_000 * 0.0002),  # 240,000
        (10.0, 340_000),
        (15.0, 340_000 + 500_000_000 * 0.0001),  # 390,000
    ])
    def test_법무사_기본보수_누진식(self, real_rules, eok, fee):
        with get_conn(real_rules) as conn:
            legal = cost_mod.calculate_legal_fee(conn, price=units.from_eok(eok),
                                                 as_of=REAL_AS_OF)
        assert legal.base_fee.amount == int(fee)

    def test_법무비는_세금이_아니라서_확정으로_표시하지_않는다(self, real_rules):
        with get_conn(real_rules) as conn:
            legal = cost_mod.calculate_legal_fee(conn, price=units.from_eok(6),
                                                 as_of=REAL_AS_OF)
        assert legal.base_fee.verification == rules.ESTIMATED
        assert legal.verification != rules.VERIFIED

    def test_중개보수와_법무보수의_부가세가_각각_계산된다(self, real_rules):
        with get_conn(real_rules) as conn:
            fee, vat, _ = cost_mod.brokerage(conn, price=units.from_eok(6),
                                             as_of=REAL_AS_OF, region="인천")
            legal = cost_mod.calculate_legal_fee(conn, price=units.from_eok(6),
                                                 as_of=REAL_AS_OF, region="인천")
        assert vat.amount == fee.amount // 10
        assert legal.vat.amount == legal.base_fee.amount // 10
        assert vat.amount != legal.vat.amount        # 서로 다른 항목이다


class TestRealLoan:
    """§1 DSR · §2 LTV · §3 절대한도."""

    def test_업권마다_DSR_이_다르다(self, real_rules):
        with get_conn(real_rules) as conn:
            bank, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF, home_status="무주택",
                regulated_area=False, region="인천", annual_income=units.from_eok(1),
                interest_rate=0.045, lender_type="은행")
            nonbank, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF, home_status="무주택",
                regulated_area=False, region="인천", annual_income=units.from_eok(1),
                interest_rate=0.045, lender_type="비은행")
        assert nonbank.amount > bank.amount
        assert nonbank.amount / bank.amount == pytest.approx(0.5 / 0.4, rel=0.01)

    def test_수도권은_스트레스_금리가_300bp_다(self, real_rules):
        with get_conn(real_rules) as conn:
            metro, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF, home_status="무주택",
                regulated_area=False, region="인천", annual_income=units.from_eok(1),
                interest_rate=0.045)
            other, _ = mortgage_mod.calculate_dsr_limit(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF, home_status="무주택",
                regulated_area=False, region="부산", annual_income=units.from_eok(1),
                interest_rate=0.045)
        assert "300bp" in metro.note
        assert "150bp" in other.note
        assert metro.amount < other.amount        # 가산금리가 크면 한도가 준다

    def test_규제지역_무주택_LTV_는_40퍼센트다(self, real_rules):
        with get_conn(real_rules) as conn:
            got, _ = mortgage_mod.calculate_ltv_limit(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF, home_status="무주택",
                regulated_area=True, region="인천")
        assert got.amount == units.from_eok(4)

    def test_비규제_LTV_는_아직_확인_불가다(self, real_rules):
        """값을 받지 못했다. 70% 같은 숫자를 지어내지 않는다."""
        with get_conn(real_rules) as conn:
            got, _ = mortgage_mod.calculate_ltv_limit(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF, home_status="무주택",
                regulated_area=False, region="인천")
        assert got.amount is None
        assert got.verification == rules.UNKNOWN

    def test_처분조건_없는_1주택자는_추정하지_않는다(self, real_rules):
        with get_conn(real_rules) as conn:
            without, _ = mortgage_mod.calculate_ltv_limit(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF, home_status="1주택",
                regulated_area=True, region="인천", disposal_condition=False)
            with_cond, _ = mortgage_mod.calculate_ltv_limit(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF, home_status="1주택",
                regulated_area=True, region="인천", disposal_condition=True)
        assert without.amount is None
        assert with_cond.amount == units.from_eok(4)

    @pytest.mark.parametrize("eok, cap_eok", [
        (10, 6), (15, 4), (20, 4), (25, 2), (30, 2),
    ])
    def test_절대한도_3구간(self, real_rules, eok, cap_eok):
        with get_conn(real_rules) as conn:
            got, _ = mortgage_mod.calculate_absolute_mortgage_cap(
                conn, price=units.from_eok(eok), as_of=REAL_AS_OF,
                home_status="무주택", regulated_area=False, region="인천")
        assert got.amount == units.from_eok(cap_eok)

    def test_수도권이_아니고_규제지역도_아니면_절대한도가_없다(self, real_rules):
        with get_conn(real_rules) as conn:
            got, _ = mortgage_mod.calculate_absolute_mortgage_cap(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF,
                home_status="무주택", regulated_area=False, region="부산")
        assert got.amount is None          # '상한 없음' 이 아니라 '확인 불가'

    def test_최종한도는_세_한도의_최솟값이다(self, real_rules):
        with get_conn(real_rules) as conn:
            got = mortgage_mod.calculate_final_mortgage_limit(
                conn, price=units.from_eok(10), as_of=REAL_AS_OF, regulated_area=True,
                region="인천", annual_income=units.from_eok(3), interest_rate=0.045)
        usable = {l.name: l.amount for l in got.limits if l.known}
        assert got.policy_max == min(usable.values())
        assert got.policy_max == units.from_eok(4)      # LTV 40% 와 절대한도 6억 중 작은 값


# ── §11 여섯 개 CASE ───────────────────────────────────────────────────

CASES = [
    # 이름,   매매가(억), 전용, 취득후주택수, 조정지역, 기대 취득세
    ("CASE 1",  5, 84,  1, False,   5_000_000),
    ("CASE 2",  8, 84,  1, False,  18_640_000),
    ("CASE 3", 10, 101, 1, False,  30_000_000),
    ("CASE 4", 10, 84,  2, True,   80_000_000),
    ("CASE 5", 10, 84,  3, False,  80_000_000),
    ("CASE 6", 10, 84,  3, True,  120_000_000),
]


@pytest.mark.parametrize("name, eok, area, homes, regulated, expected_tax", CASES)
def test_지정된_6개_CASE(real_rules, name, eok, area, homes, regulated, expected_tax):
    with get_conn(real_rules) as conn:
        cap = capital_mod.compute(
            conn, price=units.from_eok(eok), as_of=REAL_AS_OF,
            lawd_cd=LAWD_REG if regulated else LAWD_FREE,
            current_home_count=homes - 1, exclusive_area_m2=area,
            annual_income=units.from_eok(0.8), interest_rate=0.045, region="인천")

    by_name = {i.name: i for i in cap.cost_items}
    assert by_name["취득세"].amount == expected_tax

    # 농특세는 전용면적으로 갈린다
    assert by_name["농어촌특별세(일반)"].amount == (
        0 if area <= 85 else int(units.from_eok(eok) * 0.002))

    # 중개보수와 부가세는 별도 항목이다
    assert by_name["중개보수 부가세"].amount == by_name["중개보수"].amount // 10
    assert by_name["법무보수 부가세"].amount == by_name["법무사 기본보수"].amount // 10

    # 대출 세 한도가 모두 계산 시도되고, 최종값은 그중 최솟값이다
    limits = {l.name: l for l in cap.mortgage.limits}
    assert set(limits) >= {"LTV 한도", "DSR 한도", "절대 상한"}
    known = {k: v.amount for k, v in limits.items() if v.known}
    assert cap.mortgage.policy_max == min(known.values())

    # 총취득비용과 실투자금이 앞뒤가 맞는다
    assert cap.total_purchase_cost > cap.purchase_price
    assert cap.required == (cap.total_purchase_cost - cap.available_mortgage
                            - cap.assumable_deposit)
    # 실비를 못 구했으므로 '확정' 이 아니라 '예상' 이어야 한다
    assert cap.title == "예상 실투자금"


def test_CASE_4_와_5_는_세율이_같고_6_은_더_높다(real_rules):
    """조정 2주택 = 비조정 3주택 = 8%, 조정 3주택 = 12%."""
    out = {}
    with get_conn(real_rules) as conn:
        for name, eok, area, homes, regulated, _ in CASES[3:]:
            got = acquisition.assess(
                conn, price=units.from_eok(eok), as_of=REAL_AS_OF,
                resulting_home_count=homes, regulated_area=regulated,
                exclusive_area_m2=area)
            out[name] = got.acquisition_tax.amount
    assert out["CASE 4"] == out["CASE 5"]
    assert out["CASE 6"] > out["CASE 4"]


# ── §12 랭킹 순서: 거른 다음 줄 세운다 ─────────────────────────────────

def _seed_complex(conn, name, lawd, price_eok, kapt):
    from apt_engine import units as u
    repo.upsert_complexes(conn, [{"kapt_code": kapt, "name": name, "name_norm": name,
                                  "lawd_cd": lawd}])
    cid = conn.execute("SELECT id FROM complex WHERE kapt_code=?", (kapt,)).fetchone()[0]
    conn.execute(
        "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym, window_months, "
        " representative_price, method, sample_n, confidence, data_grade, "
        " engine_version, calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, "84", "202608", 6, u.from_eok(price_eok), "median", 10, "HIGH",
         "CONFIRMED", "0.9.1", "{}"))
    return cid


def test_현금_한도를_넘는_단지는_랭킹에_아예_오르지_않는다(real_rules):
    with get_conn(real_rules) as conn:
        cheap = _seed_complex(conn, "싼단지", LAWD_FREE, 4, "KC")
        pricey = _seed_complex(conn, "비싼단지", LAWD_FREE, 30, "KP")
        profile = budget_mod.Profile(
            available_cash=units.from_eok(3), annual_income=units.from_eok(1),
            interest_rate=0.045, region="인천")
        ranked, screened = budget_mod.rank(
            conn, profile=profile, as_of=REAL_AS_OF, area_band="84",
            future_prices={cheap: units.from_eok(5), pricey: units.from_eok(40)})

    ids = [r.candidate.complex_id for r in ranked]
    assert cheap in ids
    # 30억짜리는 기대수익이 아무리 커도 살 수 없으므로 순위에 없다
    assert pricey not in ids
    assert pricey in [c.complex_id for c in screened.too_expensive]


def test_예상_매도가를_안_주면_비교_대상에서_빠진다(real_rules):
    with get_conn(real_rules) as conn:
        _seed_complex(conn, "가격미상", LAWD_FREE, 4, "KU")
        profile = budget_mod.Profile(
            available_cash=units.from_eok(3), annual_income=units.from_eok(1),
            interest_rate=0.045, region="인천")
        ranked, _ = budget_mod.rank(conn, profile=profile, as_of=REAL_AS_OF,
                                    area_band="84")
    assert len(ranked) == 1
    assert ranked[0].metrics.expected_roe is None
    assert not ranked[0].comparable


def test_ROE_가_높은_단지가_먼저_온다(real_rules):
    with get_conn(real_rules) as conn:
        a = _seed_complex(conn, "저수익", LAWD_FREE, 4, "KA")
        b = _seed_complex(conn, "고수익", LAWD_FREE, 4, "KB")
        conn.execute("INSERT INTO tax_rule (tax_kind, rule_key, rate, effective_from, "
                     " source_name, last_verified, status, verification) "
                     "VALUES ('양도소득세','cgt',0.2,'2020-01-01','x',?, 'ENACTED',"
                     " 'VERIFIED')", (REAL_AS_OF,))
        conn.execute("INSERT INTO tax_rule (tax_kind, rule_key, rate, effective_from, "
                     " source_name, last_verified, status, verification) "
                     "VALUES ('지방소득세','linc',0.02,'2020-01-01','x',?, 'ENACTED',"
                     " 'VERIFIED')", (REAL_AS_OF,))
        profile = budget_mod.Profile(
            available_cash=units.from_eok(3), annual_income=units.from_eok(1),
            interest_rate=0.045, region="인천")
        ranked, _ = budget_mod.rank(
            conn, profile=profile, as_of=REAL_AS_OF, area_band="84",
            future_prices={a: units.from_eok(4.5), b: units.from_eok(6)})
    assert [r.candidate.name for r in ranked] == ["고수익", "저수익"]
    assert ranked[0].metrics.expected_roe > ranked[1].metrics.expected_roe
