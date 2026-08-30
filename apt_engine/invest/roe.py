"""레버리지 투자수익률 (지시 §15).

    EXPECTED_PROFIT = 예상 매도가
                    − 매도 부대비용(양도세·지방소득세·중개보수)
                    − 총취득비용
                    − 금융비용(보유기간 이자)

    EXPECTED_ROE    = EXPECTED_PROFIT ÷ SELF_CAPITAL_REQUIRED

매매가 상승률이 아니라 **내 돈 대비 얼마를 버는가**가 핵심 지표다. 같은 10% 상승도
실투자금이 절반이면 수익률은 두 배가 된다 — 손실도 마찬가지다.

예상 매도가는 이 모듈이 만들지 않는다. 미래가격은 가정이므로 호출부가 넣어야 하고,
넣지 않으면 수익률을 계산하지 않는다. 상승률을 지어내서 곱하지 않는다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import rules, units
from apt_engine.cash import costs as cost_mod
from apt_engine.cash.self_capital import SelfCapital
from apt_engine.regulation import mortgage as mortgage_mod
from apt_engine.tax import rules as tax_rules
from apt_engine.trace import Calc


@dataclass(frozen=True)
class ExitCost:
    items: list[cost_mod.CostItem]

    @property
    def total(self) -> int:
        return sum(i.amount for i in self.items if i.known)

    @property
    def unknown(self) -> list[str]:
        return [i.name for i in self.items if not i.known]

    @property
    def label(self) -> str:
        head = units.fmt_eok(self.total)
        return head if not self.unknown else f"{head} 이상 (확인 불가 {len(self.unknown)}개)"


def exit_costs(conn: sqlite3.Connection, *, sale_price: int, gain: int,
               as_of: str | date, region: str | None = None,
               holding_years: int | None = None, house_count: int = 1,
               allow_unverified: bool = False) -> ExitCost:
    """매도 시 나가는 돈. 양도세 규칙이 없으면 0 원이 아니라 '확인 불가'."""
    sale_price = units.as_won(sale_price)
    day = rules.as_ymd(as_of)
    items: list[cost_mod.CostItem] = []

    context = {"house_count": house_count, "holding_years": holding_years}
    for kind in (tax_rules.CAPITAL_GAINS, tax_rules.LOCAL_INCOME):
        base = max(gain, 0)
        found = tax_rules.find(conn, kind, as_of=day, base=base, context=context)
        found = [r for r in found if r.verified or allow_unverified]
        if not found:
            items.append(cost_mod.CostItem(
                kind, None, rules.UNKNOWN, "규칙 미입력",
                "매도 세금을 0원으로 세지 않았습니다 — 실제 순이익은 이보다 작습니다"))
            continue
        amount, formula = tax_rules.apply_rate(found[0], base)
        items.append(cost_mod.CostItem(kind, amount, found[0].verification, formula))

    fee, vat, _ = cost_mod.brokerage(conn, price=sale_price, as_of=day, region=region,
                                     allow_unverified=allow_unverified)
    items.append(cost_mod.CostItem("매도 중개보수", fee.amount, fee.verification,
                                   fee.formula, fee.note))
    items.append(cost_mod.CostItem("매도 중개보수 부가세", vat.amount, vat.verification,
                                   vat.formula))
    return ExitCost(items)


def financing_cost(principal: int | None, *, annual_rate: float | None,
                   years: int | None, term_years: int = 30,
                   repayment_type: str = "원리금균등") -> cost_mod.CostItem:
    """보유기간 동안 실제로 나가는 이자.

    **스트레스 금리가 아니라 실제 금리로 계산한다.** 스트레스 금리는 DSR 한도를
    구할 때만 쓰는 값이라, 이자비용에 쓰면 비용이 부풀려진다.
    """
    if not principal:
        return cost_mod.CostItem("금융비용", 0, rules.VERIFIED, "대출 없음")
    if annual_rate is None or years is None:
        return cost_mod.CostItem("금융비용", None, rules.UNKNOWN,
                                 "금리 또는 보유기간 미입력",
                                 "이자비용을 0으로 세지 않았습니다")
    if repayment_type == "원리금균등":
        monthly = mortgage_mod.annuity_payment(principal, annual_rate, term_years)
        months = min(years, term_years) * mortgage_mod.MONTHS_PER_YEAR
        # 상환한 총액 − 그동안 줄어든 원금 = 낸 이자
        paid = monthly * months
        remaining = mortgage_mod.annuity_principal(
            monthly, annual_rate, term_years - min(years, term_years))
        interest = paid - (principal - remaining)
        formula = (f"{units.fmt_eok(principal)} · {units.fmt_pct(annual_rate, digits=2)} · "
                   f"{term_years}년 원리금균등, {years}년 보유 시 납입이자")
    else:
        # 원금균등·만기일시는 상환 스케줄이 달라 이자가 달라진다. 단리 근사로 두되
        # 그 사실을 남긴다 — 조용히 원리금균등 값을 쓰지 않는다.
        interest = int(units.won_round(principal * annual_rate * years))
        formula = (f"{units.fmt_eok(principal)} × {units.fmt_pct(annual_rate, digits=2)} "
                   f"× {years}년 (단리 근사 — {repayment_type} 스케줄 미반영)")
    return cost_mod.CostItem("금융비용", max(interest, 0), rules.ESTIMATED, formula)


@dataclass(frozen=True)
class Return:
    sale_price: int
    exit_cost: ExitCost
    financing: cost_mod.CostItem
    total_acquisition_cost: int
    self_capital: int | None
    profit: int | None
    roe: float | None
    price_return: float | None
    unknown: list[str] = field(default_factory=list)
    calc: Calc | None = None

    @property
    def label(self) -> str:
        if self.roe is None:
            return "확인 불가 — " + "; ".join(self.unknown[:3])
        head = f"세후 순이익 {units.fmt_eok(self.profit)} · ROE {self.roe:.1%}"
        return head if not self.unknown else head + f" (확인 불가 {len(self.unknown)}개)"


def expected_return(conn: sqlite3.Connection, *, capital: SelfCapital,
                    future_sale_price: int | None, as_of: str | date,
                    holding_years: int, annual_rate: float | None = None,
                    mortgage_term_years: int = 30,
                    repayment_type: str = "원리금균등",
                    region: str | None = None, house_count: int = 1,
                    allow_unverified: bool = False) -> Return:
    """레버리지 수익률. 미래 매도가를 주지 않으면 계산하지 않는다."""
    unknown = list(capital.unknown)

    if future_sale_price is None:
        return Return(0, ExitCost([]),
                      cost_mod.CostItem("금융비용", None, rules.UNKNOWN, "미계산"),
                      capital.total_purchase_cost, capital.required, None, None, None,
                      unknown + ["예상 매도가 미입력 — 상승률을 지어내지 않습니다"], None)

    sale_price = units.as_won(future_sale_price)
    gain = sale_price - capital.purchase_price
    exits = exit_costs(conn, sale_price=sale_price, gain=gain, as_of=as_of,
                       region=region, holding_years=holding_years,
                       house_count=house_count, allow_unverified=allow_unverified)
    unknown += exits.unknown

    fin = financing_cost(capital.available_mortgage, annual_rate=annual_rate,
                         years=holding_years, term_years=mortgage_term_years,
                         repayment_type=repayment_type)
    if not fin.known:
        unknown.append(fin.name)

    profit = None
    roe = None
    if fin.known:
        profit = (sale_price - exits.total - capital.total_purchase_cost - fin.amount)
        if capital.required and capital.required > 0:
            roe = profit / capital.required

    price_return = (sale_price - capital.purchase_price) / capital.purchase_price

    calc = Calc(
        value=roe, unit="비율",
        formula=("EXPECTED_ROE = (예상 매도가 − 매도비용 − 총취득비용 − 금융비용) "
                 "÷ SELF_CAPITAL_REQUIRED"),
        inputs={"매수가": units.fmt_eok(capital.purchase_price),
                "예상 매도가": units.fmt_eok(sale_price),
                "보유기간": f"{holding_years}년",
                "기준일": rules.as_ymd(as_of)},
        intermediates={
            "총취득비용": units.fmt_eok(capital.total_purchase_cost),
            "매도비용": exits.label,
            "매도비용 내역": {i.name: i.label for i in exits.items},
            "금융비용": fin.label + (f"  ({fin.formula})" if fin.formula else ""),
            "실투자금": (units.fmt_eok(capital.required) if capital.required is not None
                     else "확인 불가"),
            "EXPECTED_PROFIT": units.fmt_eok(profit) if profit is not None else "확인 불가",
            "EXPECTED_ROE": f"{roe:.1%}" if roe is not None else "확인 불가",
            "EXPECTED_PRICE_RETURN": f"{price_return:.1%}",
            "해석": ("가격 상승률과 ROE 는 다른 숫자입니다. 레버리지가 크면 같은 "
                   "상승률에서 ROE 가 커지지만, 하락 시 손실률도 같은 배수로 커집니다."),
            "확인 불가": unknown or "없음",
        },
        grade="SCENARIO",   # 미래 매도가가 가정이므로 결과 전체가 시나리오다
    )
    return Return(sale_price, exits, fin, capital.total_purchase_cost,
                  capital.required, profit, roe, price_return, unknown, calc)
