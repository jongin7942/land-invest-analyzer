"""보유기간 연도별 현금흐름 (요구사항 26·29·30·31).

    t=0      − 실투자금(Initial Equity)
    t=1..n-1 + 임대수입 − 보유세 − 대출 원리금 − 관리비·수선
    t=n      위 + 매각 순수입(매도가 − 중개보수 − 양도세 − 대출잔액 − 보증금 반환)

거주 형태를 세 가지로 나눈다. 현금흐름이 완전히 다르기 때문이다.

    실거주    임대수입이 없다. 대신 월세를 안 내는 만큼 아끼는데, 그 금액을
              우리가 추정하지 않는다 — `imputed_rent` 로 받고 없으면 0으로 두되
              "주거 편익이 빠졌다"고 밝힌다
    임대      월세 수입이 들어온다. 보증금은 t=0 에 차감되고 t=n 에 반환된다
    전세승계  월 현금흐름이 없다. 보증금이 t=0 에 차감되고 t=n 에 반환된다

**모르는 항목은 0으로 세지 않는다.** 보유세 규칙이 없으면 그 해의 순현금흐름은
'확인 불가' 이고, IRR 도 내지 않는다. 0으로 세면 수익률이 조용히 부풀려진다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import regions, rules, units
from apt_engine.cash import costs as cost_mod
from apt_engine.cash.self_capital import SelfCapital
from apt_engine.cashflow import irr as irr_mod
from apt_engine.cashflow import schedule as sched_mod
from apt_engine.tax import capital_gains, holding
from apt_engine.trace import Calc, Evidence

OCCUPANCIES = ("실거주", "임대", "전세승계")


@dataclass(frozen=True)
class YearFlow:
    year: int
    rental_income: int
    holding_tax: int | None
    loan_payment: int
    loan_interest: int
    loan_principal: int
    other_cost: int
    exit_proceeds: int | None = None      # 마지막 해에만

    @property
    def known(self) -> bool:
        return self.holding_tax is not None and (
            self.year == 0 or self.exit_proceeds is not None or True)

    @property
    def net(self) -> int | None:
        if self.holding_tax is None:
            return None
        base = (self.rental_income - self.holding_tax - self.loan_payment
                - self.other_cost)
        return base + (self.exit_proceeds or 0)

    @property
    def label(self) -> str:
        return units.fmt_won(self.net) if self.net is not None else "확인 불가"


@dataclass(frozen=True)
class Timeline:
    capital: SelfCapital
    occupancy: str
    holding_years: int
    sale_price: int | None
    years: list[YearFlow]
    schedule: sched_mod.Schedule | None
    gains: capital_gains.CapitalGains | None
    exit_items: list[cost_mod.CostItem] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    calc: Calc | None = None

    # ── 자기자본 ──
    @property
    def initial_equity(self) -> int | None:
        return self.capital.required

    @property
    def cumulative(self) -> list[int | None]:
        """해마다 '지금까지 넣은 내 돈' 누계. 음수면 회수한 것이다."""
        if self.initial_equity is None:
            return [None] * (self.holding_years + 1)
        out: list[int | None] = [self.initial_equity]
        running = self.initial_equity
        for flow in self.years:
            if flow.net is None:
                out.append(None)
                running = None
                break
            running -= flow.net           # 유입이면 줄고, 유출이면 는다
            out.append(running)
        while len(out) < self.holding_years + 1:
            out.append(None)
        return out

    @property
    def peak_equity(self) -> int | None:
        """보유기간 중 **가장 많이 묶였던 순간**의 자기자본.

        역마진이면 처음 넣은 돈보다 커진다. 수익률은 Initial 이 아니라 이 값으로
        나눠야 "내 돈이 얼마나 일했나" 를 제대로 말할 수 있다.
        """
        known = [c for c in self.cumulative if c is not None]
        return max(known) if known else None

    @property
    def flows(self) -> list[float] | None:
        """IRR 용 현금흐름. 하나라도 모르면 None."""
        if self.initial_equity is None:
            return None
        out = [float(-self.initial_equity)]
        for flow in self.years:
            if flow.net is None:
                return None
            out.append(float(flow.net))
        return out

    @property
    def irr(self) -> float | None:
        flows = self.flows
        return None if flows is None else irr_mod.irr(flows)

    @property
    def net_profit(self) -> int | None:
        """세후 순이익 = 받은 것 전부 − 넣은 것 전부."""
        flows = self.flows
        return None if flows is None else int(round(sum(flows)))

    @property
    def profit_per_100m(self) -> int | None:
        """1억당 이익. **Peak Equity 기준**이다 — 실제로 묶인 돈으로 나눈다."""
        peak = self.peak_equity
        if self.net_profit is None or not peak or peak <= 0:
            return None
        return int(round(self.net_profit / (peak / 100_000_000)))

    @property
    def payback_year(self) -> int | None:
        flows = self.flows
        return None if flows is None else irr_mod.payback_year(flows)

    @property
    def complete(self) -> bool:
        return not self.unknown

    @property
    def label(self) -> str:
        if self.irr is None:
            return "세후 IRR 확인 불가 — " + "; ".join(self.unknown[:3])
        return (f"세후 IRR {self.irr:.1%} · 순이익 {units.fmt_eok(self.net_profit)} · "
                f"Peak Equity {units.fmt_eok(self.peak_equity)}")


def build(conn: sqlite3.Connection, *, capital: SelfCapital, as_of: str | date,
          holding_years: int, sale_price: int | None,
          occupancy: str = "실거주",
          monthly_rent: int = 0, imputed_rent: int = 0,
          annual_other_cost: int = 0,
          official_price: int | None = None,
          holding_cost_override: int | None = None,
          interest_rate: float | None = None,
          mortgage_term_years: int = 30,
          repayment_type: str = "원리금균등",
          house_count: int = 1, resided_years: int | None = None,
          region: str | None = None, lawd_cd: str | None = None,
          allow_unverified: bool = False) -> Timeline:
    """보유기간 현금흐름 한 벌.

    holding_cost_override 를 주면 보유세 규칙 없이도 그 금액으로 계산한다
    (사용자가 관리비 고지서·세금 고지서로 아는 경우). 주지 않으면 규칙에서 찾고,
    규칙도 없으면 그 해 순현금흐름이 '확인 불가' 가 된다.
    """
    if occupancy not in OCCUPANCIES:
        raise ValueError(f"거주 형태는 {', '.join(OCCUPANCIES)} 중 하나입니다: {occupancy!r}")
    if holding_years <= 0:
        raise ValueError("보유기간은 1년 이상이어야 합니다")

    day = rules.as_ymd(as_of)
    if region is None and lawd_cd:
        region = regions.sido_of(lawd_cd)

    unknown: list[str] = list(capital.unknown)
    notes: list[str] = []
    evidence: list[Evidence] = []

    # ── 보유세 ──
    if holding_cost_override is not None:
        annual_holding = int(holding_cost_override)
        notes.append(f"연간 보유비용을 사용자 입력 {units.fmt_won(annual_holding)} 으로 "
                     f"계산했습니다(보유세 규칙 대신).")
    else:
        tax = holding.annual(conn, official_price=official_price, as_of=day,
                             house_count=house_count,
                             allow_unverified=allow_unverified)
        evidence.extend(tax.evidence)
        if tax.unknown:
            annual_holding = None
            unknown.append("보유세(" + ", ".join(tax.unknown) + ")")
            notes.append("보유세를 0원으로 세지 않았습니다 — 연도별 순현금흐름과 "
                         "IRR 을 계산하지 않습니다. --holding-cost 로 직접 넣을 수 있습니다.")
        else:
            annual_holding = tax.total

    # ── 대출 상환 ──
    loan = capital.available_mortgage or 0
    schedule = None
    if loan and interest_rate is None:
        unknown.append("대출금리 미입력")
        notes.append("금리를 넣어야 이자와 원금 상환을 나눌 수 있습니다.")
    elif loan:
        schedule = sched_mod.build(loan, annual_rate=interest_rate,
                                   term_years=mortgage_term_years,
                                   repayment_type=repayment_type)

    # ── 임대수입 ──
    if occupancy == "임대":
        annual_rent = monthly_rent * 12
    elif occupancy == "실거주":
        annual_rent = imputed_rent
        if not imputed_rent:
            notes.append("실거주의 주거 편익(월세를 안 내는 만큼)을 0으로 뒀습니다. "
                         "추정하지 않았으니 실제 효익은 이보다 큽니다.")
    else:                                    # 전세승계
        annual_rent = 0
        notes.append("전세 승계는 월 현금흐름이 없습니다. 보증금은 매수 시 차감되고 "
                     "매도 시 반환합니다.")

    # ── 연도별 ──
    years: list[YearFlow] = []
    for y in range(1, holding_years + 1):
        row = schedule.rows[y - 1] if schedule and y <= len(schedule.rows) else None
        years.append(YearFlow(
            year=y, rental_income=annual_rent, holding_tax=annual_holding,
            loan_payment=row.payment if row else 0,
            loan_interest=row.interest if row else 0,
            loan_principal=row.principal if row else 0,
            other_cost=annual_other_cost))

    # ── 매각 ──
    gains = None
    exit_items: list[cost_mod.CostItem] = []
    exit_proceeds: int | None = None
    if sale_price is None:
        unknown.append("예상 매도가 미입력 — 상승률을 지어내지 않습니다")
        notes.append("예상 매도가를 주면 세후 IRR 까지 나옵니다.")
    else:
        sale_price = units.as_won(sale_price)
        fee, vat, ev = cost_mod.brokerage(conn, price=sale_price, as_of=day,
                                          region=region,
                                          allow_unverified=allow_unverified)
        evidence.extend(ev)
        exit_items += [cost_mod.CostItem("매도 중개보수", fee.amount, fee.verification,
                                         fee.formula, fee.note),
                       cost_mod.CostItem("매도 중개보수 부가세", vat.amount,
                                         vat.verification, vat.formula)]

        # 필요경비 = 취득 시 들어간 부대비용 (매수가 제외)
        expenses = capital.total_purchase_cost - capital.purchase_price
        gains = capital_gains.compute(
            conn, sale_price=sale_price, purchase_price=capital.purchase_price,
            expenses=expenses, as_of=day, holding_years=holding_years,
            house_count=house_count, resided_years=resided_years,
            allow_unverified=allow_unverified)
        evidence.extend(gains.evidence)
        exit_items += [cost_mod.CostItem(i.name, i.amount, i.verification, i.formula,
                                         i.note) for i in gains.items]
        unknown += [f"매도 시 {u}" for u in gains.unknown]

        balance = schedule.balance_after(holding_years) if schedule else loan
        deposit = capital.assumable_deposit or 0
        exit_items += [
            cost_mod.CostItem("대출 잔액 상환", balance, rules.ESTIMATED,
                              f"{holding_years}년 후 잔액"
                              + ("" if schedule else " (상환 스케줄 없음 — 원금 전액)")),
            cost_mod.CostItem("전세보증금 반환", deposit, rules.VERIFIED,
                              "매수 시 차감했던 보증금을 돌려준다"),
        ]
        if all(i.known for i in exit_items):
            exit_proceeds = sale_price - sum(i.amount for i in exit_items)
        else:
            unknown += [i.name for i in exit_items if not i.known]

    if years and exit_proceeds is not None:
        last = years[-1]
        years[-1] = YearFlow(last.year, last.rental_income, last.holding_tax,
                             last.loan_payment, last.loan_interest,
                             last.loan_principal, last.other_cost, exit_proceeds)
    elif years and sale_price is not None:
        # 매각 수입을 못 구했으면 마지막 해도 '확인 불가' 여야 한다.
        last = years[-1]
        years[-1] = YearFlow(last.year, last.rental_income, None, last.loan_payment,
                             last.loan_interest, last.loan_principal, last.other_cost)

    timeline = Timeline(capital, occupancy, holding_years, sale_price, years,
                        schedule, gains, exit_items, unknown, notes, None)

    intermediates = {
        "거주 형태": occupancy,
        "보유기간": f"{holding_years}년",
        "연간": {
            "임대수입": units.fmt_won(annual_rent),
            "보유세": (units.fmt_won(annual_holding) if annual_holding is not None
                    else "확인 불가"),
            "기타비용": units.fmt_won(annual_other_cost),
        },
        "대출": schedule.label if schedule else ("대출 없음" if not loan else "확인 불가"),
        "연도별 순현금흐름": {f"{f.year}년": f.label for f in years},
        "Initial Equity": (units.fmt_eok(timeline.initial_equity)
                           if timeline.initial_equity is not None else "확인 불가"),
        "Peak Equity": (units.fmt_eok(timeline.peak_equity)
                        if timeline.peak_equity is not None else "확인 불가"),
        "매각 내역": {i.name: i.label for i in exit_items} or "매도가 미입력",
        "매각 순수입": (units.fmt_eok(exit_proceeds) if exit_proceeds is not None
                   else "확인 불가"),
        "세후 순이익": (units.fmt_eok(timeline.net_profit)
                   if timeline.net_profit is not None else "확인 불가"),
        "세후 IRR": f"{timeline.irr:.2%}" if timeline.irr is not None else "확인 불가",
        "1억당 이익": (units.fmt_won(timeline.profit_per_100m)
                  if timeline.profit_per_100m is not None else "확인 불가"),
        "원금 회수 시점": (f"{timeline.payback_year}년차"
                    if timeline.payback_year else "보유기간 안에 회수 안 됨"),
        "확인 불가": unknown or "없음",
        "비고": notes,
        "해석": ("Peak Equity 는 보유기간 중 가장 많이 묶였던 내 돈입니다. "
               "역마진이면 처음 넣은 돈보다 커지고, 그때 수익률의 분모도 커집니다."),
    }
    calc = Calc(
        value=timeline.irr, unit="연율",
        formula="세후 IRR = NPV(현금흐름) = 0 이 되는 할인율",
        inputs={"매수가": units.fmt_eok(capital.purchase_price),
                "예상 매도가": units.fmt_eok(sale_price) if sale_price else "미입력",
                "기준일": day},
        intermediates=intermediates,
        evidence=tuple(evidence),
        grade="SCENARIO",         # 미래 매도가가 가정이므로 결과 전체가 시나리오다
    )
    return Timeline(capital, occupancy, holding_years, sale_price, years, schedule,
                    gains, exit_items, unknown, notes, calc)
