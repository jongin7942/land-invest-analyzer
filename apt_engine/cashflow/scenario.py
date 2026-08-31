"""Bear / Base / Bull 과 Stress Test (요구사항 31·39·47).

하나의 IRR 을 말하지 않는다. 미래 매도가는 가정이고, 가정이 10% 움직이면
레버리지 때문에 수익률은 그보다 크게 움직인다.

    시나리오   매도가를 흔든다 (Bear / Base / Bull)
    스트레스   **한 번에 하나씩** 흔든다 — 금리 · 매도가 · 보유기간 · 공실
               두 개를 같이 흔들면 어느 쪽이 원인인지 알 수 없다

아래 배율은 **관측된 통계가 아니라 가정**이다. 그렇게 표시하고, 호출부가 바꿀 수
있게 열어둔다. "과거 통계상 이렇습니다" 라고 말하지 않는다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import units
from apt_engine.cash.self_capital import SelfCapital
from apt_engine.cashflow import timeline as timeline_mod
from apt_engine.trace import Calc

KEYS = ("Bear", "Base", "Bull")

# 기준 매도가에 곱하는 배율. **관측치가 아니라 감도를 보기 위한 가정이다.**
PRICE_ADJUST = {"Bear": 0.85, "Base": 1.00, "Bull": 1.15}

ADJUST_NOTE = ("Bear/Bull 배율(−15% / +15%)은 관측된 통계가 아니라 감도를 보기 위한 "
               "가정입니다. scenario_prices 로 실제 근거가 있는 가격을 직접 넣으면 "
               "그 값이 우선합니다")


@dataclass(frozen=True)
class Band:
    results: dict[str, timeline_mod.Timeline]
    calc: Calc | None = None

    @property
    def irrs(self) -> dict[str, float | None]:
        return {k: t.irr for k, t in self.results.items()}

    @property
    def span(self) -> tuple[float, float] | None:
        got = [v for v in self.irrs.values() if v is not None]
        return (min(got), max(got)) if got else None

    @property
    def label(self) -> str:
        if self.span is None:
            return "세후 IRR 확인 불가 — 세 시나리오 모두 계산하지 못했습니다"
        lo, hi = self.span
        parts = [f"{k} {v:.1%}" for k, v in self.irrs.items() if v is not None]
        return f"세후 IRR {lo:.1%} ~ {hi:.1%}  ({' / '.join(parts)})"

    @property
    def risk_adjusted(self) -> float | None:
        """위험조정 기대수익 = Base 순이익 ÷ Bear 손실.

        Bear 에서도 이익이면 정의되지 않는다(위험이 관측되지 않음) — None.
        값이 클수록 "잘될 때 버는 돈이 못될 때 잃는 돈보다 크다".
        """
        base = self.results.get("Base")
        bear = self.results.get("Bear")
        if base is None or bear is None:
            return None
        if base.net_profit is None or bear.net_profit is None:
            return None
        if bear.net_profit >= 0 or base.net_profit <= 0:
            # Bear 에서도 이익이면 관측된 위험이 없고, Base 에서도 손실이면
            # '기대수익' 자체가 없다. 어느 쪽이든 비율로 말하면 오해를 부른다.
            return None
        return base.net_profit / abs(bear.net_profit)


def band(conn: sqlite3.Connection, *, capital: SelfCapital, as_of: str | date,
         holding_years: int, base_sale_price: int | None,
         scenario_prices: dict[str, int] | None = None,
         adjust: dict[str, float] | None = None, **kwargs) -> Band:
    """세 시나리오. 하나가 실패해도 나머지는 계산한다."""
    factors = adjust or PRICE_ADJUST
    results: dict[str, timeline_mod.Timeline] = {}

    for key in KEYS:
        if scenario_prices and key in scenario_prices:
            price = scenario_prices[key]
        elif base_sale_price is None:
            price = None
        else:
            price = int(units.won_round(base_sale_price * factors.get(key, 1.0)))
        results[key] = timeline_mod.build(
            conn, capital=capital, as_of=as_of, holding_years=holding_years,
            sale_price=price, **kwargs)

    got = Band(results, None)
    calc = Calc(
        value=got.irrs, unit="연율",
        formula="매도가 가정 3벌(Bear/Base/Bull)에 대한 세후 IRR",
        inputs={"기준 매도가": (units.fmt_eok(base_sale_price) if base_sale_price
                          else "미입력"),
                "보유기간": f"{holding_years}년"},
        intermediates={
            "시나리오별": {
                k: {"매도가": (units.fmt_eok(t.sale_price) if t.sale_price
                            else "미입력"),
                    "세후 IRR": f"{t.irr:.2%}" if t.irr is not None else "확인 불가",
                    "순이익": (units.fmt_eok(t.net_profit)
                            if t.net_profit is not None else "확인 불가"),
                    "Peak Equity": (units.fmt_eok(t.peak_equity)
                                    if t.peak_equity is not None else "확인 불가")}
                for k, t in results.items()},
            "위험조정 기대수익": (f"{got.risk_adjusted:.2f}배"
                          if got.risk_adjusted is not None
                          else "확인 불가 (Bear 에서도 손실이 아니거나 계산 불가)"),
            "배율 성격": ADJUST_NOTE,
        },
        grade="SCENARIO",
    )
    return Band(results, calc)


# ── Stress Test ───────────────────────────────────────────────────────
# 한 번에 하나씩만 흔든다.

@dataclass(frozen=True)
class Shock:
    name: str
    description: str
    irr: float | None
    net_profit: int | None
    peak_equity: int | None
    delta_irr: float | None = None

    @property
    def label(self) -> str:
        if self.irr is None:
            return f"{self.name}: 확인 불가"
        arrow = ""
        if self.delta_irr is not None:
            arrow = f"  ({self.delta_irr:+.1%}p)"
        return f"{self.name}: IRR {self.irr:.1%}{arrow}"


DEFAULT_SHOCKS = {
    "금리 +2%p": {"rate_delta": 0.02},
    "매도가 −20%": {"price_factor": 0.80},
    "보유기간 +3년": {"years_delta": 3},
    "공실 1년치": {"vacancy_years": 1},
}

SHOCK_NOTE = ("충격의 크기(+2%p, −20% 등)는 관측된 분포가 아니라 "
              "'이 정도 나빠지면 어떻게 되나' 를 보기 위한 가정입니다")


def stress(conn: sqlite3.Connection, *, capital: SelfCapital, as_of: str | date,
           holding_years: int, sale_price: int | None,
           shocks: dict[str, dict] | None = None,
           interest_rate: float | None = None, monthly_rent: int = 0,
           **kwargs) -> tuple[list[Shock], Calc]:
    """기준 대비 각 충격의 영향. 하나씩만 흔든다."""
    base = timeline_mod.build(conn, capital=capital, as_of=as_of,
                              holding_years=holding_years, sale_price=sale_price,
                              interest_rate=interest_rate,
                              monthly_rent=monthly_rent, **kwargs)
    base_irr = base.irr
    out: list[Shock] = [Shock("기준", "충격 없음", base_irr, base.net_profit,
                              base.peak_equity, 0.0 if base_irr is not None else None)]

    for name, spec in (shocks or DEFAULT_SHOCKS).items():
        years = holding_years + int(spec.get("years_delta", 0))
        price = sale_price
        if price is not None and "price_factor" in spec:
            price = int(units.won_round(price * spec["price_factor"]))
        rate = interest_rate
        if rate is not None and "rate_delta" in spec:
            rate = rate + spec["rate_delta"]
        rent = monthly_rent
        note = ""
        if spec.get("vacancy_years"):
            # 공실은 그 해 임대수입이 사라지는 것이다. 연 단위로 근사한다.
            lost = monthly_rent * 12 * int(spec["vacancy_years"])
            rent = monthly_rent
            note = f"임대수입 {units.fmt_won(lost)} 상실을 기타비용으로 반영"
            kwargs = {**kwargs,
                      "annual_other_cost": kwargs.get("annual_other_cost", 0)
                      + (lost // max(years, 1))}

        shocked = timeline_mod.build(
            conn, capital=capital, as_of=as_of, holding_years=years,
            sale_price=price, interest_rate=rate, monthly_rent=rent, **kwargs)
        delta = (None if shocked.irr is None or base_irr is None
                 else shocked.irr - base_irr)
        out.append(Shock(name, note or str(spec), shocked.irr, shocked.net_profit,
                         shocked.peak_equity, delta))

    worst = min((s for s in out[1:] if s.delta_irr is not None),
                key=lambda s: s.delta_irr, default=None)
    calc = Calc(
        value={s.name: s.irr for s in out}, unit="연율",
        formula="가정을 하나씩 나쁘게 흔들었을 때의 세후 IRR",
        inputs={"기준 매도가": units.fmt_eok(sale_price) if sale_price else "미입력",
                "보유기간": f"{holding_years}년"},
        intermediates={
            "충격별": {s.name: s.label for s in out},
            "가장 아픈 충격": worst.name if worst else "확인 불가",
            "충격 크기 성격": SHOCK_NOTE,
            "해석": ("가장 아픈 항목이 그 투자의 진짜 위험입니다. "
                   "그 가정부터 실제 자료로 확인하세요."),
        },
        grade="SCENARIO",
    )
    return out, calc
