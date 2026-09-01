"""현금흐름 · Peak Equity · 세후 IRR · Stress Test 테스트 (PHASE 7).

이 계층이 지키려는 선:
  1. 상환방식마다 현금흐름이 다르다 — 뭉뚱그리지 않는다
  2. Initial Equity 와 Peak Equity 는 다른 값이다
  3. 모르는 비용을 0으로 세지 않는다 — 그러면 IRR 도 내지 않는다
  4. 예상 매도가를 안 주면 수익률을 만들지 않는다
  5. 보유세 과세표준을 매매가로 갈음하지 않는다
  6. 스트레스는 한 번에 하나씩만 흔든다
"""
import pytest

from apt_engine import rules, units
from apt_engine.cash import self_capital as capital_mod
from apt_engine.cashflow import irr as irr_mod
from apt_engine.cashflow import scenario as scen_mod
from apt_engine.cashflow import schedule as sched_mod
from apt_engine.cashflow import timeline as timeline_mod
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.repo import apt as repo
from apt_engine.repo import cashflow as cf_repo
from apt_engine.tax import capital_gains, holding

TODAY = "2026-08-31"
LAWD = "28237"


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


def add_tax(conn, kind, key, *, cond="{}", lo=0, hi=None, rate=None, ded=0,
            fixed=None, verified=TODAY):
    conn.execute(
        "INSERT INTO tax_rule (tax_kind, rule_key, conditions_json, bracket_min, "
        " bracket_max, rate, progressive_deduction, fixed_amount, effective_from, "
        " source_name, last_verified, status, verification) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, key, cond, lo, hi, rate, ded, fixed, "2020-01-01", "근거", verified,
         "ENACTED", "VERIFIED" if verified else "NEEDS_VERIFICATION"))


def add_cost(conn, kind, key, *, rate=None, fixed=None, region=None, vat=0,
             lo=0, hi=None):
    conn.execute(
        "INSERT INTO cost_rule (cost_kind, rule_key, region, price_min, price_max, "
        " rate, fixed_amount, vat_applicable, effective_from, source_name, "
        " last_verified, status, verification) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, key, region, lo, hi, rate, fixed, vat, "2020-01-01", "근거", TODAY,
         "ENACTED", "VERIFIED"))


def full_rules(conn):
    """실투자금·매각까지 전부 계산되는 규칙 한 벌."""
    add_tax(conn, "취득세", "acq", cond='{"house_count": 1}', rate=0.01)
    add_tax(conn, "지방교육세", "edu", cond='{"house_count": 1}', rate=0.001)
    add_tax(conn, "농어촌특별세", "rural/85이하", cond='{"exclusive_area_lte": 85}',
            fixed=0)
    add_tax(conn, "부가가치세", "vat", rate=0.1)
    add_cost(conn, "중개보수", "brok", region="인천", rate=0.004, vat=1)
    add_cost(conn, "법무비", "legal", fixed=300_000, vat=1)
    add_cost(conn, "인지세", "stamp", fixed=150_000)
    add_cost(conn, "국민주택채권", "bond", rate=0.0)
    add_cost(conn, "등기신청수수료", "reg", fixed=13_000)
    add_cost(conn, "증명서발급", "cert", fixed=5_000)
    # 매각
    add_tax(conn, "장기보유특별공제", "ltd", cond='{"holding_years_gte": 3}', rate=0.10)
    add_tax(conn, "양도소득세", "cgt/기본공제", fixed=2_500_000)
    add_tax(conn, "양도소득세", "cgt/기본세율", rate=0.24, ded=5_760_000)
    add_tax(conn, "지방소득세", "linc", rate=0.10)
    # 대출 — 이게 없으면 대출가능액이 확인 불가라 실투자금도 확정되지 않는다
    for rule_type, value in (("LTV", 0.5), ("DSR", 0.4)):
        conn.execute(
            "INSERT INTO loan_rule (rule_key, rule_type, value, conditions_json, "
            " price_min, effective_from, source_name, last_verified, status, "
            " verification) VALUES (?,?,?,'{}',0,?,?,?,?,?)",
            (f"{rule_type}/test", rule_type, value, "2020-01-01", "근거", TODAY,
             "ENACTED", "VERIFIED"))


def make_capital(conn, *, price_eok=6, **kw):
    return capital_mod.compute(
        conn, price=units.from_eok(price_eok), as_of=TODAY, lawd_cd=LAWD,
        exclusive_area_m2=84, region="인천", **kw,
        # 이 파일은 현금흐름을 보는 것이라 실투자금이 확정돼야 한다.
        # 과세유형을 안 넘기면 부가세가 '확인 불가' 라 IRR 이 안 나온다(§5·§10).
        agent_vat_registered=True)


# ── 상환 스케줄 ────────────────────────────────────────────────────────

class TestSchedule:
    def test_상환방식마다_현금흐름이_다르다(self):
        got = {t: sched_mod.build(units.from_eok(3), annual_rate=0.045,
                                  term_years=30, repayment_type=t)
               for t in sched_mod.REPAYMENT_TYPES}
        # 만기일시는 원금을 안 갚아 이자가 가장 크고, 잔액이 그대로다
        assert got["만기일시"].interest_through(5) > got["원리금균등"].interest_through(5)
        assert got["만기일시"].balance_after(5) == units.from_eok(3)
        # 원금균등은 첫 해 상환액이 가장 크다
        assert got["원금균등"].rows[0].payment > got["원리금균등"].rows[0].payment
        # 잔액은 원금균등이 가장 빨리 준다
        assert got["원금균등"].balance_after(5) < got["원리금균등"].balance_after(5)

    def test_원리금균등은_초반에_이자_비중이_크다(self):
        s = sched_mod.build(units.from_eok(3), annual_rate=0.045, term_years=30)
        assert s.rows[0].interest > s.rows[0].principal
        assert s.rows[-1].interest < s.rows[-1].principal

    def test_만기일시는_마지막_해에_원금을_한꺼번에_갚는다(self):
        s = sched_mod.build(units.from_eok(3), annual_rate=0.045, term_years=10,
                            repayment_type="만기일시")
        assert all(r.principal == 0 for r in s.rows[:-1])
        assert s.rows[-1].principal == units.from_eok(3)
        assert s.rows[-1].balance_end == 0

    def test_상환_총액이_원금과_맞는다(self):
        for t in sched_mod.REPAYMENT_TYPES:
            s = sched_mod.build(units.from_eok(3), annual_rate=0.045, term_years=30,
                                repayment_type=t)
            assert sum(r.principal for r in s.rows) == pytest.approx(
                units.from_eok(3), rel=1e-4)

    def test_대출이_없으면_빈_스케줄이다(self):
        assert sched_mod.build(0, annual_rate=0.045, term_years=30).rows == []

    def test_모르는_상환방식은_거부한다(self):
        with pytest.raises(ValueError, match="상환방식"):
            sched_mod.build(1000, annual_rate=0.04, term_years=10,
                            repayment_type="자유상환")


# ── IRR ────────────────────────────────────────────────────────────────

class TestIRR:
    def test_알려진_값(self):
        assert irr_mod.irr([-100, 0, 0, 0, 0, 200]) == pytest.approx(0.1487, abs=1e-3)
        assert irr_mod.irr([-100, 110]) == pytest.approx(0.10, abs=1e-6)

    def test_부호가_안_바뀌면_구하지_않는다(self):
        assert irr_mod.irr([-100, -10, -10]) is None
        assert irr_mod.irr([100, 10, 10]) is None

    def test_현금흐름이_하나면_구하지_않는다(self):
        assert irr_mod.irr([-100]) is None

    def test_손실이면_음수_IRR(self):
        got = irr_mod.irr([-100, 0, 50])
        assert got is not None and got < 0

    def test_원금_회수_시점(self):
        assert irr_mod.payback_year([-100, 30, 30, 30, 30]) == 4
        assert irr_mod.payback_year([-100, 10, 10]) is None


# ── 보유세 ─────────────────────────────────────────────────────────────

class TestHoldingTax:
    def test_공시가격이_없으면_계산하지_않는다(self, db):
        with get_conn(db) as conn:
            got = holding.annual(conn, official_price=None, as_of=TODAY)
        assert got.total == 0 and got.unknown
        assert "매매가로 갈음하지 않습니다" in got.property_tax.note

    def test_공정시장가액비율이_없으면_공시가격을_과세표준으로_쓰지_않는다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, "재산세", "prop", rate=0.001)
            got = holding.annual(conn, official_price=units.from_eok(5), as_of=TODAY)
        assert got.property_tax.amount is None
        assert "그대로 과세표준으로 쓰지 않습니다" in got.property_tax.note

    def test_비율과_세율이_다_있으면_계산된다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, "공정시장가액비율", "재산세/주택", rate=0.6)
            add_tax(conn, "재산세", "prop", rate=0.001)
            got = holding.annual(conn, official_price=units.from_eok(5), as_of=TODAY)
        assert got.property_tax.amount == pytest.approx(
            units.from_eok(5) * 0.6 * 0.001)
        assert "종합부동산세" in got.unknown      # 종부세 규칙은 없다


# ── 양도소득세 ─────────────────────────────────────────────────────────

class TestCapitalGains:
    def test_양도차익이_없으면_세금도_없다(self, db):
        with get_conn(db) as conn:
            got = capital_gains.compute(
                conn, sale_price=units.from_eok(5), purchase_price=units.from_eok(6),
                expenses=0, as_of=TODAY, holding_years=5)
        assert got.gain < 0
        assert got.income_tax.amount == 0
        assert got.complete

    def test_규칙이_없으면_비과세로_넘기지_않는다(self, db):
        with get_conn(db) as conn:
            got = capital_gains.compute(
                conn, sale_price=units.from_eok(8), purchase_price=units.from_eok(6),
                expenses=0, as_of=TODAY, holding_years=5)
        assert got.income_tax.amount is None
        assert not got.exempt
        assert "모르는 것과 안 내는 것은 다릅니다" in got.income_tax.note

    def test_장기보유특별공제를_0으로_두지_않는다(self, db):
        with get_conn(db) as conn:
            add_tax(conn, "양도소득세", "cgt", rate=0.24)
            got = capital_gains.compute(
                conn, sale_price=units.from_eok(8), purchase_price=units.from_eok(6),
                expenses=0, as_of=TODAY, holding_years=5)
        assert got.long_term_deduction.amount is None
        assert "장기보유특별공제" in got.unknown

    def test_전부_있으면_단계별로_계산된다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            got = capital_gains.compute(
                conn, sale_price=units.from_eok(8), purchase_price=units.from_eok(6),
                expenses=10_000_000, as_of=TODAY, holding_years=5)
        gain = units.from_eok(2) - 10_000_000
        assert got.gain == gain
        assert got.long_term_deduction.amount == int(gain * 0.10)
        assert got.taxable_base == gain - int(gain * 0.10) - 2_500_000
        assert got.local_tax.amount == got.income_tax.amount // 10
        assert got.complete

    def test_필요경비가_양도차익을_줄인다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            low = capital_gains.compute(
                conn, sale_price=units.from_eok(8), purchase_price=units.from_eok(6),
                expenses=0, as_of=TODAY, holding_years=5)
            high = capital_gains.compute(
                conn, sale_price=units.from_eok(8), purchase_price=units.from_eok(6),
                expenses=50_000_000, as_of=TODAY, holding_years=5)
        assert high.income_tax.amount < low.income_tax.amount


# ── 현금흐름 · Peak Equity ────────────────────────────────────────────

class TestTimeline:
    def test_매도가가_없으면_IRR_을_만들지_않는다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn)
            tl = timeline_mod.build(conn, capital=cap, as_of=TODAY, holding_years=5,
                                    sale_price=None, holding_cost_override=1_500_000,
                                    region="인천", lawd_cd=LAWD, agent_vat_registered=True)
        assert tl.irr is None
        assert any("지어내지 않습니다" in u for u in tl.unknown)

    def test_보유세를_모르면_순현금흐름도_모른다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn)
            tl = timeline_mod.build(conn, capital=cap, as_of=TODAY, holding_years=5,
                                    sale_price=units.from_eok(8),
                                    region="인천", lawd_cd=LAWD, agent_vat_registered=True)   # 보유세 미입력
        assert all(f.net is None for f in tl.years)
        assert tl.irr is None
        assert any("보유세" in u for u in tl.unknown)

    def test_역마진이면_Peak_Equity_가_Initial_보다_크다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn, annual_income=units.from_eok(1),
                               interest_rate=0.045)
            tl = timeline_mod.build(
                conn, capital=cap, as_of=TODAY, holding_years=5,
                sale_price=units.from_eok(8), occupancy="실거주",
                holding_cost_override=1_500_000, interest_rate=0.045,
                region="인천", lawd_cd=LAWD, agent_vat_registered=True)
        assert tl.initial_equity is not None
        assert tl.peak_equity > tl.initial_equity      # 매년 돈이 더 들어간다

    def test_임대수입이_크면_Peak_이_Initial_과_같다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn, use_mortgage=False)
            tl = timeline_mod.build(
                conn, capital=cap, as_of=TODAY, holding_years=5,
                sale_price=units.from_eok(8), occupancy="임대",
                monthly_rent=3_000_000, holding_cost_override=1_500_000,
                region="인천", lawd_cd=LAWD, agent_vat_registered=True)
        # 매년 순유입이라 누계가 줄기만 한다 → 최대는 t=0
        assert tl.peak_equity == tl.initial_equity

    def test_세후_IRR_과_순이익이_앞뒤가_맞는다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn, use_mortgage=False)
            tl = timeline_mod.build(
                conn, capital=cap, as_of=TODAY, holding_years=5,
                sale_price=units.from_eok(9), holding_cost_override=1_000_000,
                region="인천", lawd_cd=LAWD, agent_vat_registered=True)
        assert tl.irr is not None
        assert tl.net_profit == int(round(sum(tl.flows)))
        # 이분법은 금리 구간을 1e-7 까지 좁힌다. 금액 단위로 환산하면 몇십 원 오차다.
        assert irr_mod.npv(tl.irr, tl.flows) == pytest.approx(
            0, abs=abs(tl.initial_equity) * 1e-6)

    def test_1억당_이익은_Peak_Equity_로_나눈다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn, use_mortgage=False)
            tl = timeline_mod.build(
                conn, capital=cap, as_of=TODAY, holding_years=5,
                sale_price=units.from_eok(9), holding_cost_override=1_000_000,
                region="인천", lawd_cd=LAWD, agent_vat_registered=True)
        assert tl.profit_per_100m == int(round(
            tl.net_profit / (tl.peak_equity / 100_000_000)))

    def test_전세승계는_월_현금흐름이_없고_만기에_반환한다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            # 토허 여부를 확인하지 않으면 보증금을 차감하지 않는다(요구사항 62-11).
            # 이미 만료된 지정을 넣어 '토허 아님' 을 확정한다.
            conn.execute(
                "INSERT INTO land_permit_zone (lawd_cd, designator, target_scope, "
                " target_use, effective_from, effective_to, jeonse_succession_allowed, "
                " source_name, last_verified) VALUES (?,?,?,?,?,?,?,?,?)",
                (LAWD, "인천시장", "내국인", "주거용", "2020-01-01", "2021-12-31", 1,
                 "고시", TODAY))
            cap = make_capital(conn, use_mortgage=False, assume_jeonse=True,
                               jeonse_deposit=units.from_eok(3))
            tl = timeline_mod.build(
                conn, capital=cap, as_of=TODAY, holding_years=5,
                sale_price=units.from_eok(8), occupancy="전세승계",
                holding_cost_override=1_000_000, region="인천", lawd_cd=LAWD, agent_vat_registered=True)
        assert all(f.rental_income == 0 for f in tl.years)
        returned = next(i for i in tl.exit_items if i.name == "전세보증금 반환")
        assert returned.amount == units.from_eok(3)
        assert any("보증금은 매수 시 차감" in n for n in tl.notes)

    def test_실거주의_주거_편익을_추정하지_않는다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn, use_mortgage=False)
            tl = timeline_mod.build(
                conn, capital=cap, as_of=TODAY, holding_years=5,
                sale_price=units.from_eok(8), occupancy="실거주",
                holding_cost_override=1_000_000, region="인천", lawd_cd=LAWD, agent_vat_registered=True)
        assert all(f.rental_income == 0 for f in tl.years)
        assert any("추정하지 않았으니" in n for n in tl.notes)

    def test_대출_잔액을_매도_시_갚는다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn, annual_income=units.from_eok(1),
                               interest_rate=0.045)
            tl = timeline_mod.build(
                conn, capital=cap, as_of=TODAY, holding_years=5,
                sale_price=units.from_eok(8), holding_cost_override=1_000_000,
                interest_rate=0.045, region="인천", lawd_cd=LAWD, agent_vat_registered=True)
        repay = next(i for i in tl.exit_items if i.name == "대출 잔액 상환")
        assert repay.amount == tl.schedule.balance_after(5)
        assert repay.amount < cap.available_mortgage      # 5년간 원금을 갚았다

    def test_보유기간이_길수록_이자를_더_낸다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn, annual_income=units.from_eok(1),
                               interest_rate=0.045)
            short = timeline_mod.build(
                conn, capital=cap, as_of=TODAY, holding_years=2,
                sale_price=units.from_eok(8), holding_cost_override=1_000_000,
                interest_rate=0.045, region="인천", lawd_cd=LAWD, agent_vat_registered=True)
            long = timeline_mod.build(
                conn, capital=cap, as_of=TODAY, holding_years=10,
                sale_price=units.from_eok(8), holding_cost_override=1_000_000,
                interest_rate=0.045, region="인천", lawd_cd=LAWD, agent_vat_registered=True)
        assert (long.schedule.interest_through(10)
                > short.schedule.interest_through(2))

    def test_모르는_상환방식이나_거주형태는_거부한다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn, use_mortgage=False)
            with pytest.raises(ValueError, match="거주 형태"):
                timeline_mod.build(conn, capital=cap, as_of=TODAY, holding_years=5,
                                   sale_price=None, occupancy="월세살이", agent_vat_registered=True)
            with pytest.raises(ValueError, match="보유기간"):
                timeline_mod.build(conn, capital=cap, as_of=TODAY, holding_years=0,
                                   sale_price=None, agent_vat_registered=True)

    def test_결과는_SCENARIO_등급이다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            cap = make_capital(conn, use_mortgage=False)
            tl = timeline_mod.build(
                conn, capital=cap, as_of=TODAY, holding_years=5,
                sale_price=units.from_eok(8), holding_cost_override=1_000_000,
                region="인천", lawd_cd=LAWD, agent_vat_registered=True)
        assert tl.calc.grade == "SCENARIO"


# ── 시나리오 · Stress Test ─────────────────────────────────────────────

class TestScenario:
    def _band(self, conn, **kw):
        cap = make_capital(conn, annual_income=units.from_eok(1), interest_rate=0.045)
        return scen_mod.band(conn, capital=cap, as_of=TODAY, holding_years=5,
                             base_sale_price=units.from_eok(8),
                             holding_cost_override=1_500_000, interest_rate=0.045,
                             region="인천", lawd_cd=LAWD, **kw, agent_vat_registered=True)

    def test_IRR_은_하나가_아니라_구간이다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            got = self._band(conn)
        assert set(got.results) == set(scen_mod.KEYS)
        lo, hi = got.span
        assert lo < hi
        assert "~" in got.label

    def test_Bear_가_가장_나쁘고_Bull_이_가장_좋다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            got = self._band(conn)
        assert got.irrs["Bear"] < got.irrs["Base"] < got.irrs["Bull"]

    def test_배율이_관측치가_아니라_가정이라고_밝힌다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            got = self._band(conn)
        assert "관측된 통계가 아니" in got.calc.intermediates["배율 성격"]
        assert got.calc.grade == "SCENARIO"

    def test_실제_가격을_주면_배율보다_우선한다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            got = self._band(conn, scenario_prices={"Bear": units.from_eok(5)})
        assert got.results["Bear"].sale_price == units.from_eok(5)
        assert got.results["Base"].sale_price == units.from_eok(8)

    def test_Bear_에서도_이익이면_위험조정을_내지_않는다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            got = self._band(conn, scenario_prices={
                "Bear": units.from_eok(12), "Base": units.from_eok(13),
                "Bull": units.from_eok(14)})
        assert got.risk_adjusted is None

    def test_위험조정은_Base이익_나누기_Bear손실(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            got = self._band(conn)
        base, bear = got.results["Base"], got.results["Bear"]
        if base.net_profit > 0 and bear.net_profit < 0:
            assert got.risk_adjusted == pytest.approx(
                base.net_profit / abs(bear.net_profit))


class TestStress:
    def _stress(self, conn, **kw):
        cap = make_capital(conn, annual_income=units.from_eok(1), interest_rate=0.045)
        return scen_mod.stress(conn, capital=cap, as_of=TODAY, holding_years=5,
                               sale_price=units.from_eok(8),
                               holding_cost_override=1_500_000, interest_rate=0.045,
                               region="인천", lawd_cd=LAWD, **kw, agent_vat_registered=True)

    def test_모든_충격이_기준보다_나쁘다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            shocks, _ = self._stress(conn)
        base = shocks[0]
        assert base.name == "기준"
        for s in shocks[1:]:
            if s.irr is not None:
                assert s.irr <= base.irr + 1e-9

    def test_가장_아픈_충격을_알려준다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            _, calc = self._stress(conn)
        assert calc.intermediates["가장 아픈 충격"] in scen_mod.DEFAULT_SHOCKS
        assert "관측된 분포가 아니" in calc.intermediates["충격 크기 성격"]

    def test_금리_상승이_IRR_을_낮춘다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            shocks, _ = self._stress(conn, shocks={"금리 +2%p": {"rate_delta": 0.02}})
        assert shocks[1].delta_irr < 0

    def test_충격을_직접_정의할_수_있다(self, db):
        with get_conn(db) as conn:
            full_rules(conn)
            shocks, _ = self._stress(conn, shocks={"매도가 −50%": {"price_factor": 0.5}})
        assert [s.name for s in shocks] == ["기준", "매도가 −50%"]


# ── 저장 ───────────────────────────────────────────────────────────────

def test_현금흐름은_SCENARIO_등급으로만_저장된다(db):
    import sqlite3
    with get_conn(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cashflow_snapshot (as_of, scenario_key, holding_years, "
                " occupancy, purchase_price, engine_version, calc_trace, data_grade) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (TODAY, "Base", 5, "실거주", 600_000_000, "0.10.0", "{}", "CONFIRMED"))


def test_저장하고_다시_읽는다(db):
    import json
    with get_conn(db) as conn:
        full_rules(conn)
        repo.upsert_complexes(conn, [{"kapt_code": "K1", "name": "테스트",
                                      "name_norm": "테스트", "lawd_cd": LAWD}])
        cid = conn.execute("SELECT id FROM complex").fetchone()[0]
        cap = make_capital(conn, use_mortgage=False)
        got = scen_mod.band(conn, capital=cap, as_of=TODAY, holding_years=5,
                            base_sale_price=units.from_eok(8),
                            holding_cost_override=1_000_000, region="인천",
                            lawd_cd=LAWD, agent_vat_registered=True)
        for key, tl in got.results.items():
            cf_repo.save(conn, complex_id=cid, area_band="84", as_of=TODAY,
                         scenario_key=key, timeline=tl)
        rows = cf_repo.latest(conn, cid)
    assert len(rows) == 3
    for r in rows:
        assert r["data_grade"] == "SCENARIO"
        assert r["peak_equity"] >= r["initial_equity"]
        assert json.loads(r["calc_trace"])["grade"] == "SCENARIO"
