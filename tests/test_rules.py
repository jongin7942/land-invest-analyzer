"""규제 · 토허 · 세법 · 대출 · 실투자금 테스트 (PHASE 3).

이 계층이 지키려는 선:
  1. `as_of` 없이는 아무것도 조회되지 않는다
  2. 사람이 확인하지 않은 규칙으로는 계산하지 않는다
  3. 토허 여부를 모르면서 갭투자 가능하다고 하지 않는다
  4. 모르는 항목을 0으로 세지 않는다
"""
import sqlite3

import pytest

from apt_engine import rules, units
from apt_engine.cash import equity as equity_mod
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.regulation import loan as loan_mod
from apt_engine.regulation import zone as zone_mod
from apt_engine.repo import apt as repo
from apt_engine.repo import rules as rule_repo
from apt_engine.tax import acquisition
from apt_engine.tax import rules as tax_rules

VERIFIED = "2026-08-30"


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
        repo.upsert_complexes(conn, [{
            "kapt_code": "A1", "name": "테스트단지", "name_norm": "테스트단지",
            "lawd_cd": "28237", "emd_name": "산곡동", "apt_households": 1200}])
    return tmp_db


def add_tax(conn, *, kind="취득세", key="t1", conditions="{}", lo=0, hi=None,
            rate=0.01, verified=VERIFIED, frm="2020-01-01", to=None):
    conn.execute(
        "INSERT INTO tax_rule (tax_kind, rule_key, conditions_json, bracket_min, "
        "bracket_max, rate, effective_from, effective_to, source_name, last_verified) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (kind, key, conditions, lo, hi, rate, frm, to, "테스트 출처", verified))


def add_permit(conn, *, lawd="28237", scope="내국인", frm="2026-01-01", to="2027-12-31",
               jeonse_ok=0, duty=24, verified=VERIFIED, emd=None):
    conn.execute(
        "INSERT INTO land_permit_zone (lawd_cd, emd_name, target_scope, effective_from, "
        "effective_to, residence_duty_months, jeonse_succession_allowed, "
        "source_name, last_verified) VALUES (?,?,?,?,?,?,?,?,?)",
        (lawd, emd, scope, frm, to, duty, jeonse_ok, "테스트 고시", verified))


# ── as_of 강제 ────────────────────────────────────────────────────────

class TestAsOfIsMandatory:
    def test_규제지역_조회에_as_of_가_없으면_호출이_안_된다(self, db):
        with get_conn(db) as conn:
            with pytest.raises(TypeError):
                zone_mod.zone_at(conn, "28237")

    def test_토허_조회에_scope_가_없으면_호출이_안_된다(self, db):
        with get_conn(db) as conn:
            with pytest.raises(TypeError):
                zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-30")

    def test_잘못된_날짜_형식은_거부(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            rules.as_ymd("20260830")

    def test_모르는_scope_는_거부(self, db):
        with get_conn(db) as conn:
            with pytest.raises(ValueError, match="scope"):
                zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-30", scope="법인")


# ── 기간 판정 ─────────────────────────────────────────────────────────

class TestEffectivePeriod:
    def test_지정기간이_끝났으면_토허가_아니다(self, db):
        # 요구사항 26-7: 기간이 끝났는데 현재 토허라고 표시하지 말 것.
        with get_conn(db) as conn:
            add_permit(conn, frm="2024-01-01", to="2025-12-31")
            during = zone_mod.permit_zone_at(conn, "28237", as_of="2025-06-01",
                                             scope="내국인")
            after = zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-30",
                                            scope="내국인")
        assert during.designated is True
        assert after.designated is False

    def test_외국인_토허를_내국인에게_적용하지_않는다(self, db):
        # 요구사항 26-8
        with get_conn(db) as conn:
            add_permit(conn, scope="외국인")
            domestic = zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-30",
                                               scope="내국인")
            foreign = zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-30",
                                              scope="외국인")
        assert domestic.designated is False
        assert foreign.designated is True

    def test_전체_대상_지정은_내국인에게도_적용된다(self, db):
        with get_conn(db) as conn:
            add_permit(conn, scope="전체")
            assert zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-30",
                                           scope="내국인").designated is True

    def test_데이터가_없으면_아니다가_아니라_확인_불가다(self, db):
        # 요구사항 62-11: 토허를 확인하지 않고 갭투자 가능하다고 판단하지 않는다.
        with get_conn(db) as conn:
            p = zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-30", scope="내국인")
        assert p.checked is False
        assert p.can_use_jeonse is None      # False 가 아니다
        assert "확인 불가" in p.label

    def test_토허면_전세_활용_불가가_전달된다(self, db):
        with get_conn(db) as conn:
            add_permit(conn, jeonse_ok=0)
            p = zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-30", scope="내국인")
        assert p.can_use_jeonse is False
        assert "전세승계 불가" in p.label

    def test_규제지역_데이터가_없으면_비규제가_아니라_확인_불가(self, db):
        with get_conn(db) as conn:
            z = zone_mod.zone_at(conn, "28237", as_of="2026-08-30")
        assert z.checked is False and z.regulated is None


# ── 미검증 규칙 거부 ──────────────────────────────────────────────────

class TestVerification:
    def test_미검증_세법으로는_계산하지_않는다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, verified=None)
            with pytest.raises(rules.UnverifiedRuleError, match="확인하지 않았습니다"):
                acquisition.compute(conn, price=units.from_eok(6.2), as_of="2026-08-30",
                                    house_count=1, regulated=False)

    def test_명시적으로_허용하면_계산은_된다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, verified=None)
            calc = acquisition.compute(conn, price=units.from_eok(6.2), as_of="2026-08-30",
                                       house_count=1, regulated=False,
                                       allow_unverified=True)
        assert "미검증" in calc.intermediates

    def test_규칙이_아예_없으면_다른_예외(self, db):
        with get_conn(db) as conn:
            with pytest.raises(rules.NoRuleError, match="규칙이 없습니다"):
                acquisition.compute(conn, price=units.from_eok(6.2), as_of="2026-08-30",
                                    house_count=1, regulated=False)

    def test_확인_표시하면_계산된다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, verified=None)
            rid = conn.execute("SELECT id FROM tax_rule").fetchone()[0]
            rule_repo.mark_verified(conn, "tax", rule_id=rid, verified_on=VERIFIED)
            calc = acquisition.compute(conn, price=units.from_eok(6.2), as_of="2026-08-30",
                                       house_count=1, regulated=False)
        assert calc.value == units.from_eok(6.2) * 0.01


# ── 조건 매칭 ─────────────────────────────────────────────────────────

class TestConditions:
    @pytest.mark.parametrize("conditions,context,expected", [
        ('{}', {}, True),
        ('{"house_count":1}', {"house_count": 1}, True),
        ('{"house_count":1}', {"house_count": 2}, False),
        ('{"house_count_gte":2}', {"house_count": 3}, True),
        ('{"house_count_gte":2}', {"house_count": 1}, False),
        ('{"exclusive_area_gt":85}', {"exclusive_area": 84.9}, False),
        ('{"exclusive_area_gt":85}', {"exclusive_area": 85.1}, True),
        ('{"regulated":true}', {"regulated": True}, True),
        ('{"zone_in":["조정대상지역"]}', {"zone": "조정대상지역"}, True),
        ('{"house_count":1}', {}, False),          # 값을 모르면 매칭 실패
    ])
    def test_연산자(self, conditions, context, expected):
        ok, _ = rules.matches(conditions, context)
        assert ok is expected

    def test_조건이_더_많이_맞는_규칙이_우선된다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, key="일반", conditions="{}", rate=0.03)
            add_tax(conn, key="1주택", conditions='{"house_count":1}', rate=0.01)
            found = tax_rules.find(conn, "취득세", as_of="2026-08-30",
                                   base=units.from_eok(6.2), context={"house_count": 1})
        assert found[0].get("rule_key") == "1주택"

    def test_과세표준_구간이_맞아야_한다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, key="6억이하", lo=0, hi=units.from_eok(6), rate=0.01)
            add_tax(conn, key="9억초과", lo=units.from_eok(9), hi=None, rate=0.03)
            low = tax_rules.find(conn, "취득세", as_of="2026-08-30",
                                 base=units.from_eok(5), context={})
            high = tax_rules.find(conn, "취득세", as_of="2026-08-30",
                                  base=units.from_eok(10), context={})
            mid = tax_rules.find(conn, "취득세", as_of="2026-08-30",
                                 base=units.from_eok(7), context={})
        assert low[0].get("rule_key") == "6억이하"
        assert high[0].get("rule_key") == "9억초과"
        assert mid == []      # 구간이 비면 억지로 고르지 않는다


# ── 취득세 ────────────────────────────────────────────────────────────

class TestAcquisitionTax:
    def test_부가세목이_없으면_0이_아니라_확인_불가(self, db):
        # 0으로 세면 실투자금이 실제보다 작게 나온다.
        with get_conn(db) as conn:
            add_tax(conn, kind="취득세", rate=0.01)
            calc = acquisition.compute(conn, price=units.from_eok(6.2),
                                       as_of="2026-08-30", house_count=1, regulated=False)
        assert calc.intermediates["세목별"]["지방교육세"] == "확인 불가"
        assert "주의" in calc.intermediates

    def test_부가세목까지_있으면_합산된다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, kind="취득세", key="acq", rate=0.01)
            add_tax(conn, kind="지방교육세", key="edu", rate=0.001)
            add_tax(conn, kind="농어촌특별세", key="rural", rate=0.002)
            calc = acquisition.compute(conn, price=units.from_eok(10),
                                       as_of="2026-08-30", house_count=1, regulated=False)
        assert calc.value == units.from_eok(10) * 0.013
        assert "주의" not in calc.intermediates

    def test_세금은_항상_추정_등급이다(self, db):
        # 세법 해석은 개인 사정에 따라 달라진다. 확정값으로 표시하지 않는다.
        with get_conn(db) as conn:
            add_tax(conn, rate=0.01)
            calc = acquisition.compute(conn, price=units.from_eok(6.2),
                                       as_of="2026-08-30", house_count=1, regulated=False)
        assert calc.grade == "ESTIMATED"

    def test_누진공제가_반영된다(self, db):
        with get_conn(db) as conn:
            conn.execute(
                "INSERT INTO tax_rule (tax_kind, rule_key, bracket_min, rate, "
                "progressive_deduction, effective_from, last_verified) "
                "VALUES ('취득세','p',0,0.03,10000000,'2020-01-01',?)", (VERIFIED,))
            calc = acquisition.compute(conn, price=units.from_eok(10),
                                       as_of="2026-08-30", house_count=1, regulated=False)
        assert calc.value == int(units.from_eok(10) * 0.03) - 10_000_000


# ── 대출 ──────────────────────────────────────────────────────────────

class TestLoan:
    def _add_loan(self, conn, *, ltv=0.7, dsr=0.4, verified=VERIFIED, stress_bp=0):
        conn.execute(
            "INSERT INTO loan_rule (rule_key, conditions_json, price_min, ltv, dsr, "
            "stress_rate_bp, effective_from, source_name, last_verified) "
            "VALUES ('l1','{}',0,?,?,?,'2024-01-01','테스트',?)",
            (ltv, dsr, stress_bp, verified))

    def test_규칙이_없으면_추정하지_않는다(self, db):
        with get_conn(db) as conn:
            cap = loan_mod.capacity(conn, price=units.from_eok(6.2), as_of="2026-08-30",
                                    house_count=0, zone_types=[])
        assert cap.checked is False
        assert cap.available is None

    def test_LTV_와_DSR_중_작은_쪽이_실제_한도다(self, db):
        # 요구사항 62-12: LTV 하나로 계산하지 않는다.
        with get_conn(db) as conn:
            self._add_loan(conn, ltv=0.7, dsr=0.4)
            cap = loan_mod.capacity(
                conn, price=units.from_eok(10), as_of="2026-08-30", house_count=0,
                zone_types=[], annual_income=units.from_eok(0.5),  # 5천만원
                rate=0.04, years=30)
        assert cap.ltv_limit == units.from_eok(7)
        assert cap.dsr_limit < cap.ltv_limit
        assert cap.available == cap.dsr_limit
        assert cap.binding == "DSR 한도"

    def test_소득이_없으면_DSR_을_계산하지_못한다고_말한다(self, db):
        with get_conn(db) as conn:
            self._add_loan(conn)
            cap = loan_mod.capacity(conn, price=units.from_eok(10), as_of="2026-08-30",
                                    house_count=0, zone_types=[])
        assert cap.dsr_limit is None
        assert "연소득" in cap.calc.intermediates["주의"]

    def test_요청액이_한도보다_작으면_요청액이_결정한다(self, db):
        # "최대한 대출"을 골라도 LTV 최대치로 계산하지 않는다.
        with get_conn(db) as conn:
            self._add_loan(conn)
            cap = loan_mod.capacity(conn, price=units.from_eok(10), as_of="2026-08-30",
                                    house_count=0, zone_types=[],
                                    annual_income=units.from_eok(2),
                                    requested=units.from_eok(3))
        assert cap.available == units.from_eok(3)
        assert cap.binding == "요청액"

    def test_스트레스_금리가_DSR_한도를_낮춘다(self, db):
        with get_conn(db) as conn:
            self._add_loan(conn, stress_bp=0)
            base = loan_mod.capacity(conn, price=units.from_eok(10), as_of="2026-08-30",
                                     house_count=0, zone_types=[],
                                     annual_income=units.from_eok(1))
            conn.execute("UPDATE loan_rule SET stress_rate_bp = 150")
            stressed = loan_mod.capacity(conn, price=units.from_eok(10),
                                         as_of="2026-08-30", house_count=0, zone_types=[],
                                         annual_income=units.from_eok(1))
        assert stressed.dsr_limit < base.dsr_limit

    def test_기존_대출이_DSR_여력을_줄인다(self, db):
        with get_conn(db) as conn:
            self._add_loan(conn)
            clean = loan_mod.capacity(conn, price=units.from_eok(10), as_of="2026-08-30",
                                      house_count=0, zone_types=[],
                                      annual_income=units.from_eok(1))
            burdened = loan_mod.capacity(conn, price=units.from_eok(10),
                                         as_of="2026-08-30", house_count=0, zone_types=[],
                                         annual_income=units.from_eok(1),
                                         existing_annual_payment=20_000_000)
        assert burdened.dsr_limit < clean.dsr_limit

    def test_원리금_상환_왕복(self):
        principal = units.from_eok(3)
        payment = loan_mod.annuity_payment(principal, 0.04, 30)
        assert loan_mod.annuity_principal(payment, 0.04, 30) == pytest.approx(
            principal, rel=0.001)


# ── 실투자금 ──────────────────────────────────────────────────────────

class TestEquity:
    def test_토허면_전세보증금을_차감하지_않는다(self, db):
        # 요구사항 22의 핵심.
        with get_conn(db) as conn:
            add_tax(conn, rate=0.01)
            add_permit(conn, jeonse_ok=0)
            eq = equity_mod.compute(
                conn, price=units.from_eok(6.2), as_of="2026-08-30", house_count=1,
                lawd_cd="28237", emd_name="산곡동",
                jeonse_deposit=units.from_eok(3.7))
        jeonse_item = next(i for i in eq.items if i.name == "승계 전세보증금")
        assert jeonse_item.amount == 0
        assert "토지거래허가구역" in jeonse_item.note
        assert eq.total > units.from_eok(6)

    def test_토허가_아니면_전세보증금을_뺀다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, rate=0.01)
            add_permit(conn, jeonse_ok=1)
            eq = equity_mod.compute(
                conn, price=units.from_eok(6.2), as_of="2026-08-30", house_count=1,
                lawd_cd="28237", emd_name="산곡동",
                jeonse_deposit=units.from_eok(3.7))
        assert eq.total < units.from_eok(3)

    def test_토허_데이터가_없으면_전세를_뺄지_말지_판단하지_않는다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, rate=0.01)
            eq = equity_mod.compute(
                conn, price=units.from_eok(6.2), as_of="2026-08-30", house_count=1,
                lawd_cd="28237", jeonse_deposit=units.from_eok(3.7))
        assert "승계 전세보증금(토허 미확인)" in eq.unknown
        assert eq.complete is False
        assert "이상" in eq.label

    def test_모르는_항목을_0으로_세지_않는다(self, db):
        with get_conn(db) as conn:
            add_permit(conn, jeonse_ok=1)
            eq = equity_mod.compute(
                conn, price=units.from_eok(6.2), as_of="2026-08-30", house_count=1,
                lawd_cd="28237", jeonse_deposit=units.from_eok(3.7))
        assert "취득 관련 세금" in eq.unknown
        assert "중개보수" in eq.unknown
        assert "이 금액보다" in eq.calc.intermediates["주의"]

    def test_모든_규칙이_있으면_완결된다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, kind="취득세", key="acq", rate=0.01)
            add_tax(conn, kind="지방교육세", key="edu", rate=0.001)
            add_tax(conn, kind="농어촌특별세", key="rural", rate=0.0)
            add_permit(conn, jeonse_ok=1)
            for kind in ("중개보수", "법무비"):
                conn.execute(
                    "INSERT INTO cost_rule (cost_kind, rule_key, price_min, rate, "
                    "effective_from, source_name, last_verified) VALUES (?,?,0,?,?,?,?)",
                    (kind, kind, 0.005 if kind == "중개보수" else 0.001,
                     "2021-01-01", "테스트", VERIFIED))
            eq = equity_mod.compute(
                conn, price=units.from_eok(6.2), as_of="2026-08-30", house_count=1,
                lawd_cd="28237", jeonse_deposit=units.from_eok(3.7),
                loan_amount=units.from_eok(1.0), buffer_cost=units.from_eok(0.2))
        assert eq.complete is True
        assert "이상" not in eq.label


# ── 수기 입력 ─────────────────────────────────────────────────────────

class TestRuleImport:
    def test_서식을_그대로_import_해도_에러가_아니다(self, db, tmp_path):
        # 주석만 있고 데이터가 없는 상태.
        for kind in rule_repo.TEMPLATES:
            path = rule_repo.write_template(kind, tmp_path / f"{kind}.csv")
            with get_conn(db) as conn:
                s = rule_repo.import_csv(conn, kind, path)
            assert s["inserted"] == 0

    def test_퍼센트_표기를_받는다(self, db, tmp_path):
        p = tmp_path / "tax.csv"
        p.write_text(
            "tax_kind,rule_key,conditions_json,bracket_min,bracket_max,rate,"
            "progressive_deduction,fixed_amount,rate_formula,effective_from,"
            "effective_to,source_name,source_url,last_verified,note\n"
            "취득세,t1,{},0,,1%,0,,,2020-01-01,,지방세법,https://law.go.kr,2026-08-30,\n",
            encoding="utf-8")
        with get_conn(db) as conn:
            rule_repo.import_csv(conn, "tax", p)
            rate = conn.execute("SELECT rate FROM tax_rule").fetchone()[0]
        assert rate == pytest.approx(0.01)

    def test_잘못된_줄이_있으면_전부_안_들어간다(self, db, tmp_path):
        p = tmp_path / "tax.csv"
        p.write_text(
            "tax_kind,rule_key,conditions_json,bracket_min,bracket_max,rate,"
            "progressive_deduction,fixed_amount,rate_formula,effective_from,"
            "effective_to,source_name,source_url,last_verified,note\n"
            "취득세,t1,{},0,,1%,0,,,2020-01-01,,a,b,2026-08-30,\n"
            "취득세,t2,{},0,,비쌈,0,,,2020-01-01,,a,b,2026-08-30,\n",
            encoding="utf-8")
        with get_conn(db) as conn:
            with pytest.raises(rule_repo.RuleImportError, match="3행"):
                rule_repo.import_csv(conn, "tax", p)
            assert conn.execute("SELECT COUNT(*) FROM tax_rule").fetchone()[0] == 0

    def test_잘못된_JSON_조건은_거부(self, db, tmp_path):
        p = tmp_path / "tax.csv"
        p.write_text(
            "tax_kind,rule_key,conditions_json,bracket_min,bracket_max,rate,"
            "progressive_deduction,fixed_amount,rate_formula,effective_from,"
            "effective_to,source_name,source_url,last_verified,note\n"
            "취득세,t1,{house_count:1},0,,1%,0,,,2020-01-01,,a,b,2026-08-30,\n",
            encoding="utf-8")
        with get_conn(db) as conn:
            with pytest.raises(rule_repo.RuleImportError, match="JSON"):
                rule_repo.import_csv(conn, "tax", p)

    def test_진행률_집계(self, db):
        with get_conn(db) as conn:
            add_tax(conn, key="a", verified=VERIFIED)
            add_tax(conn, key="b", verified=None)
            cov = rule_repo.coverage(conn)
        # verified = last_verified 가 있어 계산에 쓸 수 있는 규칙
        # confirmed = verification 이 VERIFIED 인 규칙 (직접 INSERT 는 기본값이 미확인)
        assert cov["tax"]["total"] == 2
        assert cov["tax"]["verified"] == 1
        assert cov["tax"]["confirmed"] == 0
        assert cov["tax"]["pending"] == 0


class TestSchemaGuards:
    def test_토허_종료일은_필수다(self, db):
        # 끝을 안 적으면 지정기간이 끝났는지 알 수 없다.
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO land_permit_zone (lawd_cd, target_scope, effective_from) "
                    "VALUES ('28237','내국인','2026-01-01')")

    def test_토허_대상은_정해진_값만(self, db):
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO land_permit_zone (lawd_cd, target_scope, effective_from, "
                    "effective_to) VALUES ('28237','법인','2026-01-01','2027-01-01')")

    def test_세목은_정해진_값만(self, db):
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO tax_rule (tax_kind, rule_key, effective_from) "
                    "VALUES ('개근세','x','2020-01-01')")


class TestRateFormula:
    """지방세법 제11조 6억~9억 구간처럼 세율이 구간 안에서 연속으로 변하는 조문.

    표로 못 담아서 산식을 넣는다. CSV 는 사람이 손으로 채우는 파일이므로
    eval() 을 쓰지 않고 ast 화이트리스트로 제한 평가한다.
    """

    ACQ = "(base * 2 / 300000000 - 3) / 100"

    @pytest.mark.parametrize("price,expected_pct", [
        (600_000_000, 1.0),      # 구간 시작 — 6억 이하 세율 1% 와 이어진다
        (750_000_000, 2.0),      # 한가운데
        (900_000_000, 3.0),      # 구간 끝 — 9억 초과 세율 3% 와 이어진다
    ])
    def test_지방세법_구간_산식(self, price, expected_pct):
        from apt_engine.tax.rules import eval_rate_formula
        assert eval_rate_formula(self.ACQ, price) * 100 == pytest.approx(expected_pct)

    @pytest.mark.parametrize("expr", [
        '__import__("os").system("echo x")',
        'open("secret.txt").read()',
        'foo + 1',
        '(1).__class__',
    ])
    def test_임의_코드는_거부한다(self, expr):
        from apt_engine.tax.rules import eval_rate_formula
        with pytest.raises(rules.RuleError):
            eval_rate_formula(expr, 600_000_000)

    def test_세율_범위를_벗어나면_거부한다(self):
        # 단위를 틀려서 100 배로 적는 실수(1% 를 1 로) 를 잡는다.
        from apt_engine.tax.rules import eval_rate_formula
        with pytest.raises(rules.RuleError, match="세율 범위"):
            eval_rate_formula("base / 1000", 600_000_000)

    def test_0으로_나누면_거부한다(self):
        from apt_engine.tax.rules import eval_rate_formula
        with pytest.raises(rules.RuleError, match="0으로"):
            eval_rate_formula("1 / 0", 1)


class TestRegionScopedCost:
    """중개보수는 시·도 조례라 cost_rule.region 에 시도가 적힌다.

    equity.compute() 호출부가 region 을 빠뜨리면 지역이 적힌 규칙이 통째로
    걸러져 '규칙 미입력' 으로 보인다 — last_verified 를 채워도 영원히 안 잡힌다.
    lawd_cd 에서 시도를 유도하는지 고정한다.
    """

    def test_시도는_lawd_cd_에서_유도된다(self):
        from apt_engine import regions
        assert regions.sido_of("28237") == "인천"   # 부평구
        assert regions.sido_of("11680") == "서울"   # 강남구
        assert regions.sido_of("41135") == "경기"   # 분당구

    def test_지역이_적힌_중개보수_규칙이_잡힌다(self, db):
        with get_conn(db) as conn:
            conn.execute(
            "INSERT INTO cost_rule (cost_kind, rule_key, region, price_min, price_max,"
            " rate, effective_from, source_name, last_verified) "
            "VALUES ('중개보수','brok/test','인천',200000000,900000000,0.004,"
            "'2021-10-19','테스트','2026-08-31')")

            from apt_engine.cash import equity
            eq = equity.compute(conn, price=620_000_000, as_of="2026-08-31",
                            house_count=1, lawd_cd="28237", emd_name="부평동")
            brok = next(i for i in eq.items if i.name == "중개보수")
            assert brok.known, f"지역 규칙이 걸러졌다: {brok.note}"
            assert brok.amount == 2_480_000      # 6.2억 × 0.4%

    def test_다른_시도의_규칙은_안_잡힌다(self, db):
        with get_conn(db) as conn:
            conn.execute(
            "INSERT INTO cost_rule (cost_kind, rule_key, region, price_min, price_max,"
            " rate, effective_from, source_name, last_verified) "
            "VALUES ('중개보수','brok/seoul','서울',200000000,900000000,0.004,"
            "'2021-10-19','테스트','2026-08-31')")

            from apt_engine.cash import equity
            eq = equity.compute(conn, price=620_000_000, as_of="2026-08-31",
                            house_count=1, lawd_cd="28237", emd_name="부평동")
            brok = next(i for i in eq.items if i.name == "중개보수")
            assert not brok.known, "서울 조례가 인천 매물에 적용됐다"


class TestPermitCoverageBySido:
    """토허 커버리지는 시·도 단위다.

    토허는 시·도별로 따로 고시된다. 인천 자료만 넣은 상태에서 서울 매물을
    '확인함, 지정 없음' 으로 단정하면 전세보증금을 근거 없이 차감하게 된다.
    요구사항 62-11 이 막으려는 바로 그 동작이다.
    """

    INCHEON_FOREIGN = (
        "INSERT INTO land_permit_zone (lawd_cd, designator, target_scope,"
        " effective_from, effective_to, jeonse_succession_allowed,"
        " source_name, last_verified) "
        "VALUES ('28237','인천광역시장','외국인','2025-08-26','2026-08-25',0,"
        "'테스트','2026-08-31')")

    def test_자료가_전혀_없으면_확인_불가(self, db):
        with get_conn(db) as conn:
            st = zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-01",
                                     scope=zone_mod.DOMESTIC)
            assert st.checked is False
            assert st.can_use_jeonse is None

    def test_같은_시도_자료가_있으면_확인된_것으로_본다(self, db):
        with get_conn(db) as conn:
            conn.execute(self.INCHEON_FOREIGN)
            # 부평구 내국인 매수 — 외국인 지정은 적용되지 않는다
            st = zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-01",
                                     scope=zone_mod.DOMESTIC)
            assert st.checked is True
            assert st.designated is False
            assert st.can_use_jeonse is True

    def test_다른_시도는_여전히_확인_불가(self, db):
        with get_conn(db) as conn:
            conn.execute(self.INCHEON_FOREIGN)
            # 인천 자료만 넣었다. 서울 강남구를 안다고 하면 안 된다.
            st = zone_mod.permit_zone_at(conn, "11680", as_of="2026-08-01",
                                     scope=zone_mod.DOMESTIC)
            assert st.checked is False
            assert st.can_use_jeonse is None

    def test_외국인_지정은_외국인_매수에만_걸린다(self, db):
        with get_conn(db) as conn:
            conn.execute(self.INCHEON_FOREIGN)
            st = zone_mod.permit_zone_at(conn, "28237", as_of="2026-08-01",
                                     scope=zone_mod.FOREIGN)
            assert st.designated is True
            assert st.can_use_jeonse is False
