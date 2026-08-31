"""보유세 — 재산세 · 종합부동산세 (요구사항 25·26).

보유하는 동안 해마다 나가는 돈이다. 5년 보유면 다섯 번 낸다. 이걸 빼먹으면
수익률이 조용히 부풀려진다.

**과세표준은 매매가가 아니라 공시가격에서 나온다.** 공시가격은 시세와 다르고,
"시세의 몇 %" 로 추정하면 그게 곧 지어낸 숫자다. 그래서 이 모듈은 공시가격을
**입력으로 요구**하고, 없으면 보유세를 '확인 불가' 로 돌려준다.

    과세표준 = 공시가격 × 공정시장가액비율   ← 비율도 규칙에서 온다
    재산세   = 과세표준 × 세율 − 누진공제
    종부세   = (공시가격 − 기본공제) × 공정시장가액비율 × 세율 − 누진공제

세율·공제·비율 어느 것도 코드에 없다. 지금은 규칙이 비어 있어 전부 확인 불가다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import rules, units
from apt_engine.tax import rules as tax_rules
from apt_engine.tax.acquisition import TaxItem
from apt_engine.trace import Calc, Evidence

FAIR_MARKET_RATIO = "공정시장가액비율"


@dataclass(frozen=True)
class HoldingTax:
    """한 해의 보유세."""
    property_tax: TaxItem
    comprehensive_tax: TaxItem
    evidence: tuple[Evidence, ...] = ()

    @property
    def items(self) -> list[TaxItem]:
        return [self.property_tax, self.comprehensive_tax]

    @property
    def total(self) -> int:
        return sum(i.amount for i in self.items if i.known)

    @property
    def unknown(self) -> list[str]:
        return [i.name for i in self.items if not i.known]

    @property
    def verification(self) -> str:
        return rules.weakest_verification(*(i.verification for i in self.items))

    @property
    def label(self) -> str:
        head = units.fmt_won(self.total)
        return head if not self.unknown else f"{head} 이상 (확인 불가 {len(self.unknown)}개)"


def _ratio(conn: sqlite3.Connection, *, tax_kind: str, as_of: str,
           allow_unverified: bool) -> tuple[float | None, Evidence | None]:
    """공정시장가액비율. 세목별로 다르므로 rule_key 로 구분한다."""
    found = tax_rules.find(conn, FAIR_MARKET_RATIO, as_of=as_of, base=0,
                           context={"tax_kind": tax_kind})
    found = [r for r in found if tax_kind in str(r.get("rule_key") or "")] or found
    found = [r for r in found if r.verified or allow_unverified]
    if not found:
        return None, None
    rate = found[0].get("rate")
    return (float(rate) if rate is not None else None), found[0].evidence


def _one(conn: sqlite3.Connection, kind: str, *, base: int, as_of: str, context: dict,
         name: str, allow_unverified: bool) -> tuple[TaxItem, Evidence | None]:
    found = tax_rules.find(conn, kind, as_of=as_of, base=base, context=context)
    found = [r for r in found if r.verified or allow_unverified]
    if not found:
        return TaxItem(name, None, rules.UNKNOWN, "규칙 미입력",
                       "보유세를 0원으로 세지 않았습니다 — 실제 수익률은 이보다 낮습니다"), None
    rule = found[0]
    amount, formula = tax_rules.apply_rate(rule, base)
    return TaxItem(name, amount, rule.verification, formula,
                   str(rule.get("note") or "")), rule.evidence


def annual(conn: sqlite3.Connection, *, official_price: int | None,
           as_of: str | date, house_count: int = 1,
           allow_unverified: bool = False) -> HoldingTax:
    """한 해 보유세. 공시가격이 없으면 계산하지 않는다."""
    day = rules.as_ymd(as_of)
    if not official_price:
        note = ("공시가격 미입력 — 보유세를 계산할 수 없습니다. "
                "매매가로 갈음하지 않습니다(공시가격은 시세와 다릅니다)")
        return HoldingTax(TaxItem("재산세", None, rules.UNKNOWN, "공시가격 미입력", note),
                          TaxItem("종합부동산세", None, rules.UNKNOWN, "공시가격 미입력", note))

    official_price = units.as_won(official_price)
    context = {"house_count": house_count, "official_price": official_price}
    evidence: list[Evidence] = []

    items: list[TaxItem] = []
    for kind, name in ((tax_rules.PROPERTY, "재산세"),
                       (tax_rules.COMPREHENSIVE, "종합부동산세")):
        ratio, ev = _ratio(conn, tax_kind=kind, as_of=day,
                           allow_unverified=allow_unverified)
        if ratio is None:
            # 비율을 모르면 과세표준을 만들 수 없다. 1.0 으로 두면 세금이 과대계상된다.
            items.append(TaxItem(
                name, None, rules.UNKNOWN, "공정시장가액비율 미입력",
                f"{name} 과세표준을 만들 수 없습니다. 공시가격을 그대로 과세표준으로 "
                f"쓰지 않습니다"))
            continue
        if ev:
            evidence.append(ev)
        base = int(units.won_round(official_price * ratio))
        item, ev2 = _one(conn, kind, base=base, as_of=day, context=context, name=name,
                         allow_unverified=allow_unverified)
        if ev2:
            evidence.append(ev2)
        items.append(item)

    return HoldingTax(items[0], items[1], tuple(evidence))


def to_calc(tax: HoldingTax, *, official_price: int | None, as_of: str) -> Calc:
    return Calc(
        value=tax.total if not tax.unknown else None, unit="원",
        formula="연간 보유세 = 재산세 + 종합부동산세",
        inputs={"공시가격": units.fmt_eok(official_price) if official_price else "미입력",
                "기준일": as_of},
        intermediates={
            "세목별": {i.name: i.label for i in tax.items},
            "계산식": {i.name: i.formula for i in tax.items},
            "확인 불가": tax.unknown or "없음",
            "주의": ("보유세는 해마다 나갑니다. 5년 보유면 다섯 번입니다. "
                   "빼먹으면 수익률이 부풀려집니다"),
        },
        evidence=tax.evidence,
        grade="ESTIMATED",
    )
