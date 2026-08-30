"""대출 가능액 (요구사항 23·62-12).

**LTV 하나로 계산하지 않는다.** 세 값을 따로 내고, 실제 한도는 그중 가장 작은 것이다.

    Theoretical LTV Limit   집값 × LTV. 담보만 보는 이론상 한도
    Estimated DSR Capacity  소득으로 감당 가능한 원리금에서 역산한 한도
    Actual Loan Input       사용자가 실제로 받겠다고 한 금액

사용자가 "최대한 대출"을 골라도 LTV 최대치로 계산하지 않는다.
소득이 안 되면 그 돈은 안 나온다.

스트레스 DSR 은 실제 금리가 아니라 **가산된 금리**로 상환액을 계산하는 규제다.
`stress_rate_bp` 가 규칙에 있으면 DSR 한도 계산에만 반영하고, 실제 이자비용은
원래 금리로 계산한다 — 둘을 섞으면 이자비용이 과대계상된다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from apt_engine import rules, units
from apt_engine.trace import Calc

MONTHS_PER_YEAR = 12


def annuity_principal(monthly_payment: float, annual_rate: float, years: int) -> int:
    """원리금균등상환에서 월 상환액으로 감당 가능한 원금.

    P = M × (1 − (1+i)^−N) / i     i = 월이율, N = 총 개월수
    """
    if monthly_payment <= 0 or years <= 0:
        return 0
    n = years * MONTHS_PER_YEAR
    i = annual_rate / MONTHS_PER_YEAR
    if i <= 0:
        return int(units.won_round(monthly_payment * n))
    factor = (1 - (1 + i) ** -n) / i
    return int(units.won_round(monthly_payment * factor))


def annuity_payment(principal: int, annual_rate: float, years: int) -> int:
    """원금에 대한 월 상환액."""
    if principal <= 0 or years <= 0:
        return 0
    n = years * MONTHS_PER_YEAR
    i = annual_rate / MONTHS_PER_YEAR
    if i <= 0:
        return int(units.won_round(principal / n))
    return int(units.won_round(principal * i / (1 - (1 + i) ** -n)))


@dataclass(frozen=True)
class LoanCapacity:
    ltv_limit: int | None
    dsr_limit: int | None
    requested: int | None
    available: int | None          # 실제 가능액 = 셋 중 최소
    binding: str | None            # 무엇이 한도를 결정했나
    residence_required: bool
    calc: Calc

    @property
    def checked(self) -> bool:
        return self.available is not None


def find_rule(conn: sqlite3.Connection, *, as_of: str | date, price: int,
              house_count: int, zone_types: list[str]) -> rules.Rule | None:
    day = rules.as_ymd(as_of)
    rows = conn.execute(
        f"SELECT * FROM loan_rule WHERE {rules.effective_clause()}", (day, day)).fetchall()
    context = {
        "house_count": house_count,
        "regulated": bool(zone_types),
        "zone": zone_types[0] if zone_types else "비규제",
    }
    found = rules.pick(rows, context, amount=price,
                       min_col="price_min", max_col="price_max")
    return found[0] if found else None


def capacity(conn: sqlite3.Connection, *, price: int, as_of: str | date,
             house_count: int, zone_types: list[str],
             annual_income: int | None = None,
             existing_annual_payment: int = 0,
             rate: float = 0.04, years: int = 30,
             requested: int | None = None,
             allow_unverified: bool = False) -> LoanCapacity:
    """대출 가능액. 규칙이 없으면 추측하지 않고 '확인 불가'로 돌려준다."""
    price = units.as_won(price)
    day = rules.as_ymd(as_of)
    rule = find_rule(conn, as_of=day, price=price, house_count=house_count,
                     zone_types=zone_types)

    if rule is None:
        return LoanCapacity(
            None, None, requested, None, None, False,
            Calc(value=None, unit="원",
                 formula="대출 규칙이 입력되지 않아 계산 불가",
                 inputs={"집값": units.fmt_eok(price), "기준일": day,
                         "주택수": house_count,
                         "규제지역": " · ".join(zone_types) or "비규제"},
                 intermediates={"주의": "`cli rule template loan` 으로 서식을 받아 "
                                       "LTV·DSR 을 입력하세요. 값 없이 추정하지 않습니다."},
                 grade="ESTIMATED"))
    if not allow_unverified:
        rule.require_verified("대출")

    # ── ① 이론상 LTV 한도 ──
    ltv = rule.get("ltv")
    ltv_limit = int(units.won_round(price * float(ltv))) if ltv is not None else None

    # ── ② DSR 한도 ──
    dsr = rule.get("dsr")
    dsr_limit = None
    stress_rate = rate + (rule.get("stress_rate_bp") or 0) / 10000.0
    if dsr is not None and annual_income:
        yearly_cap = annual_income * float(dsr) - existing_annual_payment
        dsr_limit = max(0, annuity_principal(yearly_cap / MONTHS_PER_YEAR,
                                             stress_rate, years))

    # ── ③ 절대 상한 ──
    hard_cap = rule.get("max_loan_amount")

    candidates = {"LTV 한도": ltv_limit, "DSR 한도": dsr_limit,
                  "규정 상한": int(hard_cap) if hard_cap else None,
                  "요청액": requested}
    usable = {k: v for k, v in candidates.items() if v is not None}
    available = min(usable.values()) if usable else None
    binding = min(usable, key=usable.get) if usable else None

    intermediates = {
        "후보 한도": {k: units.fmt_eok(v) for k, v in usable.items()},
        "결정 요인": binding,
        "적용 규칙": rule.get("rule_key"),
        "금리": f"{units.fmt_pct(rate, digits=2)}"
                + (f" (스트레스 {units.fmt_pct(stress_rate, digits=2)})"
                   if stress_rate != rate else ""),
        "기간": f"{years}년",
    }
    if dsr is not None and not annual_income:
        intermediates["주의"] = ("연소득을 입력하지 않아 DSR 한도를 계산하지 못했습니다. "
                                "LTV 한도만으로는 실제 대출가능액이 아닙니다.")
    if not rule.verified:
        intermediates["미검증"] = "대출 규칙이 사람 확인을 거치지 않았습니다."

    calc = Calc(
        value=available, unit="원",
        formula="min(LTV 한도, DSR 한도, 규정 상한, 요청액)",
        inputs={"집값": units.fmt_eok(price), "주택수": house_count,
                "규제지역": " · ".join(zone_types) or "비규제",
                "연소득": units.fmt_eok(annual_income) if annual_income else "미입력",
                "기존 연간 원리금": units.fmt_won(existing_annual_payment),
                "기준일": day},
        intermediates=intermediates,
        evidence=(rule.evidence,),
        grade="ESTIMATED",
    )
    return LoanCapacity(ltv_limit, dsr_limit, requested, available, binding,
                        bool(rule.get("residence_required")), calc)
