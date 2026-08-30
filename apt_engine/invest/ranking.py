"""랭킹 엔진에 넘길 지표 묶음 (지시 §16).

    EXPECTED_PRICE_RETURN      가격 상승률 (매도가 ÷ 매수가 − 1)
    EXPECTED_ROE               내 돈 대비 수익률
    SELF_CAPITAL_REQUIRED      실투자금
    CASH_UTILIZATION           내 현금의 몇 %가 들어가나
    LEVERAGE_RATIO             총취득비용 대비 타인자본(대출+승계보증금) 비율
    FINANCING_COST             보유기간 이자
    TAX_AND_TRANSACTION_COST   세금 + 거래비용 (매수가를 뺀 나머지)
    DOWNSIDE_RISK              가격이 하락 시나리오까지 떨어졌을 때의 ROE

**DOWNSIDE_RISK 를 추정하지 않는다.** 하락 시나리오 가격을 호출부가 주지 않으면
'확인 불가'다. "보통 20% 빠진다" 같은 숫자를 만들면 그게 곧 근거 없는 예측이다.

여덟 값 중 하나라도 확인 불가면 그 사실이 결과에 남는다. 랭킹은 확인 불가 항목을
0점이나 평균값으로 채우지 않고, 그 단지를 '비교 불가'로 분류할 책임이 호출부에 있다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import rules, units
from apt_engine.cash.self_capital import SelfCapital
from apt_engine.invest import roe as roe_mod
from apt_engine.trace import Calc

FIELDS = ("EXPECTED_PRICE_RETURN", "EXPECTED_ROE", "SELF_CAPITAL_REQUIRED",
          "CASH_UTILIZATION", "LEVERAGE_RATIO", "FINANCING_COST",
          "TAX_AND_TRANSACTION_COST", "DOWNSIDE_RISK")


@dataclass(frozen=True)
class Metrics:
    expected_price_return: float | None
    expected_roe: float | None
    self_capital_required: int | None
    cash_utilization: float | None
    leverage_ratio: float | None
    financing_cost: int | None
    tax_and_transaction_cost: int | None
    downside_risk: float | None
    unknown: list[str] = field(default_factory=list)
    calc: Calc | None = None

    @property
    def comparable(self) -> bool:
        """랭킹에 올려도 되는가. 하나라도 비면 다른 단지와 나란히 세우지 않는다."""
        return not self.unknown

    def as_dict(self) -> dict:
        return {
            "EXPECTED_PRICE_RETURN": self.expected_price_return,
            "EXPECTED_ROE": self.expected_roe,
            "SELF_CAPITAL_REQUIRED": self.self_capital_required,
            "CASH_UTILIZATION": self.cash_utilization,
            "LEVERAGE_RATIO": self.leverage_ratio,
            "FINANCING_COST": self.financing_cost,
            "TAX_AND_TRANSACTION_COST": self.tax_and_transaction_cost,
            "DOWNSIDE_RISK": self.downside_risk,
        }

    @property
    def display(self) -> dict:
        def pct(v):
            return "확인 불가" if v is None else f"{v:.1%}"

        def won(v):
            return "확인 불가" if v is None else units.fmt_eok(v)

        return {
            "EXPECTED_PRICE_RETURN": pct(self.expected_price_return),
            "EXPECTED_ROE": pct(self.expected_roe),
            "SELF_CAPITAL_REQUIRED": won(self.self_capital_required),
            "CASH_UTILIZATION": pct(self.cash_utilization),
            "LEVERAGE_RATIO": pct(self.leverage_ratio),
            "FINANCING_COST": won(self.financing_cost),
            "TAX_AND_TRANSACTION_COST": won(self.tax_and_transaction_cost),
            "DOWNSIDE_RISK": pct(self.downside_risk),
        }


def build(conn: sqlite3.Connection, *, capital: SelfCapital,
          expected: roe_mod.Return, available_cash: int | None,
          downside_sale_price: int | None = None, as_of: str | date | None = None,
          holding_years: int = 5, annual_rate: float | None = None,
          region: str | None = None, house_count: int = 1,
          allow_unverified: bool = False) -> Metrics:
    unknown: list[str] = []

    if capital.required is None:
        unknown.append("SELF_CAPITAL_REQUIRED")
    if expected.roe is None:
        unknown.append("EXPECTED_ROE")

    utilization = (capital.cash_utilization(available_cash)
                   if available_cash else None)
    if utilization is None:
        unknown.append("CASH_UTILIZATION")

    borrowed = None
    if capital.available_mortgage is not None and capital.assumable_deposit is not None:
        borrowed = capital.available_mortgage + capital.assumable_deposit
    leverage = (borrowed / capital.total_purchase_cost
                if borrowed is not None and capital.total_purchase_cost else None)
    if leverage is None:
        unknown.append("LEVERAGE_RATIO")

    financing = expected.financing.amount if expected.financing.known else None
    if financing is None:
        unknown.append("FINANCING_COST")

    transaction = (capital.total_purchase_cost - capital.purchase_price
                   if not capital.unknown else None)
    if transaction is None:
        unknown.append("TAX_AND_TRANSACTION_COST")

    # ── 하락 시나리오 ──
    downside = None
    if downside_sale_price is None:
        unknown.append("DOWNSIDE_RISK (하락 시나리오 가격 미입력 — 추정하지 않습니다)")
    else:
        down = roe_mod.expected_return(
            conn, capital=capital, future_sale_price=downside_sale_price,
            as_of=as_of or "1970-01-01", holding_years=holding_years,
            annual_rate=annual_rate, region=region, house_count=house_count,
            allow_unverified=allow_unverified)
        downside = down.roe
        if downside is None:
            unknown.append("DOWNSIDE_RISK")

    metrics = Metrics(
        expected_price_return=expected.price_return,
        expected_roe=expected.roe,
        self_capital_required=capital.required,
        cash_utilization=utilization,
        leverage_ratio=leverage,
        financing_cost=financing,
        tax_and_transaction_cost=transaction,
        downside_risk=downside,
        unknown=unknown)

    calc = Calc(
        value=metrics.as_dict(), unit="혼합",
        formula="랭킹 지표 8종",
        inputs={"매수가": units.fmt_eok(capital.purchase_price),
                "가용 현금": units.fmt_eok(available_cash) if available_cash else "미입력",
                "보유기간": f"{holding_years}년"},
        intermediates={
            "지표": metrics.display,
            "확인 불가": unknown or "없음",
            "해석": ("CASH_UTILIZATION 이 낮다고 좋은 게 아닙니다. 적게 들어가는 만큼 "
                   "남는 현금으로 무엇을 하는지까지 봐야 하고, 레버리지가 높으면 "
                   "DOWNSIDE_RISK 도 같은 배수로 커집니다."),
            "비교 가능": metrics.comparable,
        },
        grade="SCENARIO",
    )
    return Metrics(**{**metrics.__dict__, "calc": calc})
