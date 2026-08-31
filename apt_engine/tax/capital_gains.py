"""양도소득세 · 지방소득세 (요구사항 25·30).

매도할 때 내는 세금이다. 이걸 모르면 "세후" 수익률이라고 말할 수 없다.

    양도차익     = 양도가액 − 취득가액 − 필요경비(취득세·중개보수·법무비 등)
    장기보유특별공제 = 양도차익 × 공제율(보유기간에 따라)
    양도소득금액 = 양도차익 − 장기보유특별공제
    과세표준     = 양도소득금액 − 기본공제
    산출세액     = 과세표준 × 세율 − 누진공제
    지방소득세   = 양도소득세 × 세율

**1세대1주택 비과세**는 세율 0 인 별도 규칙으로 표현한다. 보유·거주 요건과
고가주택 기준이 붙으므로 conditions_json 으로 건다. 규칙이 없으면 비과세로
넘기지 않는다 — 모르면 '확인 불가' 이고, 그게 비과세보다 안전하다.

지금은 양도세 규칙이 비어 있어 전부 확인 불가다. 채우면 바로 동작한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import rules, units
from apt_engine.tax import rules as tax_rules
from apt_engine.tax.acquisition import TaxItem
from apt_engine.trace import Calc, Evidence

LONG_TERM_DEDUCTION = "장기보유특별공제"

# 양도소득 기본공제. 금액이 아니라 '규칙 키' 다 — 값은 tax_rule 에서 온다.
BASIC_DEDUCTION_KEY = "기본공제"


@dataclass(frozen=True)
class CapitalGains:
    gain: int                       # 양도차익
    long_term_deduction: TaxItem
    taxable_base: int | None        # 과세표준
    income_tax: TaxItem             # 양도소득세
    local_tax: TaxItem              # 지방소득세
    exempt: bool = False            # 비과세로 판정됐나
    unknown: list[str] = field(default_factory=list)
    evidence: tuple[Evidence, ...] = ()
    calc: Calc | None = None

    @property
    def items(self) -> list[TaxItem]:
        return [self.income_tax, self.local_tax]

    @property
    def total(self) -> int:
        return sum(i.amount for i in self.items if i.known)

    @property
    def complete(self) -> bool:
        return not self.unknown

    @property
    def label(self) -> str:
        if self.exempt:
            return "비과세"
        head = units.fmt_won(self.total)
        return head if self.complete else f"{head} 이상 (확인 불가 {len(self.unknown)}개)"


def _deduction_rate(conn: sqlite3.Connection, *, as_of: str, gain: int, context: dict,
                    allow_unverified: bool) -> tuple[float | None, str, Evidence | None]:
    found = tax_rules.find(conn, LONG_TERM_DEDUCTION, as_of=as_of, base=gain,
                           context=context)
    found = [r for r in found if r.verified or allow_unverified]
    if not found:
        return None, "장기보유특별공제 규칙 미입력", None
    rate = found[0].get("rate")
    if rate is None:
        return None, "규칙에 공제율이 없음", None
    return float(rate), f"양도차익 × {units.fmt_pct(float(rate))}", found[0].evidence


def compute(conn: sqlite3.Connection, *, sale_price: int, purchase_price: int,
            expenses: int, as_of: str | date, holding_years: int,
            house_count: int = 1, resided_years: int | None = None,
            allow_unverified: bool = False) -> CapitalGains:
    """양도세 일체.

    expenses  필요경비 — 취득세·중개보수·법무비 등 취득/양도에 든 비용
    """
    day = rules.as_ymd(as_of)
    sale_price = units.as_won(sale_price)
    purchase_price = units.as_won(purchase_price)
    gain = sale_price - purchase_price - int(expenses)

    context = {
        "house_count": house_count,
        "holding_years": holding_years,
        "resided_years": resided_years if resided_years is not None else 0,
        "sale_price": sale_price,
    }
    evidence: list[Evidence] = []
    unknown: list[str] = []

    if gain <= 0:
        # 손실이면 양도세가 없다. 이건 규칙 없이도 확정할 수 있는 사실이다.
        zero = TaxItem("양도소득세", 0, rules.VERIFIED, "양도차익 없음 — 과세 대상 아님")
        zero_local = TaxItem("지방소득세", 0, rules.VERIFIED, "양도세가 0")
        return CapitalGains(gain, TaxItem(LONG_TERM_DEDUCTION, 0, rules.VERIFIED,
                                          "양도차익 없음"),
                            0, zero, zero_local, False, [], (), None)

    # ── 장기보유특별공제 ──
    rate, formula, ev = _deduction_rate(conn, as_of=day, gain=gain, context=context,
                                        allow_unverified=allow_unverified)
    if rate is None:
        # 공제를 0으로 두면 세금이 과대계상된다. 그래도 '확인 불가' 라고 말한다 —
        # 방향이 보수적이라도 지어낸 값은 쓰지 않는다.
        deduction = TaxItem(LONG_TERM_DEDUCTION, None, rules.UNKNOWN, formula,
                            "공제를 0으로 두지 않았습니다. 실제 세금은 이보다 작습니다")
        unknown.append(LONG_TERM_DEDUCTION)
        income_amount = None
    else:
        if ev:
            evidence.append(ev)
        deduction = TaxItem(LONG_TERM_DEDUCTION, int(units.won_round(gain * rate)),
                            rules.VERIFIED, formula)
        income_amount = gain - deduction.amount

    # ── 기본공제 ──
    taxable = None
    if income_amount is not None:
        basic = tax_rules.find(conn, tax_rules.CAPITAL_GAINS, as_of=day,
                               base=income_amount, context=context)
        basic = [r for r in basic
                 if BASIC_DEDUCTION_KEY in str(r.get("rule_key") or "")]
        basic = [r for r in basic if r.verified or allow_unverified]
        basic_amount = int(basic[0].get("fixed_amount") or 0) if basic else 0
        if basic:
            evidence.append(basic[0].evidence)
        taxable = max(income_amount - basic_amount, 0)

    # ── 양도소득세 ──
    found = tax_rules.find(conn, tax_rules.CAPITAL_GAINS, as_of=day,
                           base=taxable if taxable is not None else 0, context=context)
    found = [r for r in found
             if BASIC_DEDUCTION_KEY not in str(r.get("rule_key") or "")]
    found = [r for r in found if r.verified or allow_unverified]

    exempt = False
    if not found:
        income = TaxItem("양도소득세", None, rules.UNKNOWN, "규칙 미입력",
                         "0원으로 세지 않았습니다. 비과세로 넘기지도 않았습니다 — "
                         "모르는 것과 안 내는 것은 다릅니다")
        unknown.append("양도소득세")
    elif taxable is None:
        income = TaxItem("양도소득세", None, rules.UNKNOWN, "과세표준 확인 불가")
        unknown.append("양도소득세")
    else:
        rule = found[0]
        amount, formula = tax_rules.apply_rate(rule, taxable)
        exempt = amount == 0 and (rule.get("rate") == 0 or rule.get("rate") is None)
        income = TaxItem("양도소득세", amount, rule.verification, formula,
                         str(rule.get("note") or ""))
        evidence.append(rule.evidence)

    # ── 지방소득세 (양도세액에 붙는다) ──
    if not income.known:
        local = TaxItem("지방소득세", None, rules.UNKNOWN, "양도세를 몰라 계산 불가")
        unknown.append("지방소득세")
    else:
        found = tax_rules.find(conn, tax_rules.LOCAL_INCOME, as_of=day,
                               base=income.amount, context=context)
        found = [r for r in found if r.verified or allow_unverified]
        if not found:
            local = TaxItem("지방소득세", None, rules.UNKNOWN, "규칙 미입력")
            unknown.append("지방소득세")
        else:
            amount, formula = tax_rules.apply_rate(found[0], income.amount)
            local = TaxItem("지방소득세", amount, found[0].verification, formula)
            evidence.append(found[0].evidence)

    calc = Calc(
        value=income.amount if income.known else None, unit="원",
        formula=("양도차익 − 장기보유특별공제 − 기본공제 = 과세표준 → "
                 "× 세율 − 누진공제 = 양도소득세"),
        inputs={"양도가액": units.fmt_eok(sale_price),
                "취득가액": units.fmt_eok(purchase_price),
                "필요경비": units.fmt_won(int(expenses)),
                "보유기간": f"{holding_years}년",
                "거주기간": f"{resided_years}년" if resided_years is not None else "미입력",
                "주택수": house_count, "기준일": day},
        intermediates={
            "양도차익": units.fmt_eok(gain),
            "장기보유특별공제": deduction.label,
            "과세표준": units.fmt_eok(taxable) if taxable is not None else "확인 불가",
            "양도소득세": income.label,
            "지방소득세": local.label,
            "비과세 판정": "비과세" if exempt else "과세",
            "확인 불가": unknown or "없음",
        },
        evidence=tuple(evidence),
        grade="ESTIMATED",
    )
    return CapitalGains(gain, deduction, taxable, income, local, exempt, unknown,
                        tuple(evidence), calc)
