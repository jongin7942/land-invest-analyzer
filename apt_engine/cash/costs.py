"""거래 부대비용 — 중개보수와 법무·등기비 (지시 §9·§10).

두 항목 모두 "얼마쯤 하더라" 로 알고 있는 숫자가 있지만, 그걸 코드에 적으면
전국 어느 지역·어느 가격대에도 같은 값이 나오고 백테스트에서도 같은 값이 나온다.
그래서 여기서도 값은 전부 `cost_rule` 에서 온다.

중개보수(§10)
    시·도 조례의 **상한요율**이 기본이다. 실제 보수는 그 안에서 협의하므로,
    협의된 요율을 알면 그걸 쓰고 모르면 보수적으로 상한을 쓴다.
    부가가치세는 보수에 **별도로** 붙는다 — 합쳐서 하나의 숫자로 만들지 않는다.

법무·등기비(§9)
    정액 30만원 같은 값을 쓰지 않는다. 기본보수(법무사 보수표)와 실비를 나누고,
    사전에 확정할 수 없는 실비는 ESTIMATED 로 표시한다. 그래서 화면 이름도
    "법무비"가 아니라 **"법무·등기비 예상액"** 이다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import rules, units
from apt_engine.tax import rules as tax_rules
from apt_engine.trace import Evidence

VAT = "부가가치세"

# 법무·등기 단계에서 함께 나가는 실비 항목들. 각각 다른 규칙에서 온다.
REGISTRATION_KINDS = ("인지세", "국민주택채권", "등기신청수수료")
CERTIFICATE_KINDS = ("증명서발급",)


@dataclass(frozen=True)
class CostItem:
    name: str
    amount: int | None
    verification: str
    formula: str = ""
    note: str = ""

    @property
    def known(self) -> bool:
        return self.amount is not None

    @property
    def label(self) -> str:
        return units.fmt_won(self.amount) if self.known else "확인 불가"


def _find(conn: sqlite3.Connection, cost_kind: str, *, price: int, as_of: str,
          region: str | None, allow_unverified: bool) -> rules.Rule | None:
    day = rules.as_ymd(as_of)
    rows = conn.execute(
        f"SELECT * FROM cost_rule WHERE cost_kind = ? AND {rules.effective_clause()}",
        (cost_kind, day, day)).fetchall()
    # region 이 NULL 인 규칙은 전국 적용이다. 지역이 적힌 규칙을 먼저 쓴다.
    rows = [r for r in rows if r["region"] is None or r["region"] == region]
    found = rules.pick(rows, {}, amount=price,
                       min_col="price_min", max_col="price_max")
    found = [r for r in found if r.verified or allow_unverified]
    if not found:
        return None
    found.sort(key=lambda r: (r.get("region") is None,))
    return found[0]


def _apply(rule: rules.Rule, price: int) -> tuple[int, str]:
    """요율 / 정액 / 누진(기본액 + 초과분 × 요율) / 상한을 적용한다.

    법무사 보수표처럼 "5천만원까지 105,000원, 초과분은 0.05%" 형태의 누진 구조는
    fixed_amount(구간 기본액) 와 rate(초과분 요율)를 **둘 다** 적은 행으로 표현한다.
    구간 하한은 price_min 이다. 하나만 적으면 정액 또는 단순 요율이다.
    """
    fixed = rule.get("fixed_amount")
    rate = rule.get("rate")
    floor = int(rule.get("price_min") or 0)

    if fixed is not None and rate is not None:
        excess = max(price - floor, 0)
        amount = int(units.won_round(int(fixed) + excess * float(rate)))
        formula = (f"{units.fmt_won(int(fixed))} + (초과 {units.fmt_won(excess)} × "
                   f"{units.fmt_pct(float(rate), digits=4)})")
    elif fixed is not None:
        return int(fixed), f"정액 {units.fmt_won(int(fixed))}"
    elif rate is not None:
        amount = int(units.won_round(price * float(rate)))
        formula = f"{units.fmt_eok(price)} × {units.fmt_pct(float(rate), digits=2)}"
    else:
        raise rules.RuleError(f"'{rule.get('rule_key')}' 에 요율도 정액도 없습니다")

    cap = rule.get("max_amount")
    if cap is not None and amount > int(cap):
        return int(cap), formula + f" → 한도 {units.fmt_won(int(cap))}"
    return amount, formula


def vat_rate(conn: sqlite3.Connection, *, as_of: str | date,
             allow_unverified: bool = False) -> tuple[float | None, Evidence | None]:
    """부가가치세율. 이것도 법률이라 코드에 적지 않는다."""
    found = tax_rules.find(conn, VAT, as_of=as_of, base=0, context={})
    found = [r for r in found if r.verified or allow_unverified]
    if not found:
        return None, None
    rate = found[0].get("rate")
    return (float(rate) if rate is not None else None), found[0].evidence


def brokerage(conn: sqlite3.Connection, *, price: int, as_of: str | date,
              region: str | None = None, negotiated_rate: float | None = None,
              allow_unverified: bool = False) -> tuple[CostItem, CostItem,
                                                       list[Evidence]]:
    """중개보수와 그 부가가치세. (보수, 부가세, 근거).

    negotiated_rate 를 주면 협의 요율로 계산하되, 법정 상한을 넘으면 상한으로 깎는다.
    """
    price = units.as_won(price)
    day = rules.as_ymd(as_of)
    evidence: list[Evidence] = []

    rule = _find(conn, "중개보수", price=price, as_of=day, region=region,
                 allow_unverified=allow_unverified)
    if rule is None:
        fee = CostItem("중개보수", None, rules.UNKNOWN, "규칙 미입력",
                       f"{region or '전국'} 중개보수 조례가 입력되지 않았습니다")
        vat = CostItem("중개보수 부가세", None, rules.UNKNOWN, "중개보수를 몰라 계산 불가")
        return fee, vat, evidence

    legal_max, formula = _apply(rule, price)
    evidence.append(rule.evidence)

    if negotiated_rate is None:
        amount = legal_max
        note = "법정 상한요율 적용 — 실제 보수는 이 한도 안에서 협의합니다(보수적)"
    else:
        negotiated = int(units.won_round(price * float(negotiated_rate)))
        if negotiated > legal_max:
            amount = legal_max
            note = (f"협의 요율 {units.fmt_pct(negotiated_rate, digits=2)} 이 "
                    f"법정 상한을 넘어 상한으로 계산했습니다")
        else:
            amount = negotiated
            note = f"협의 요율 {units.fmt_pct(negotiated_rate, digits=2)} 적용"
            formula = (f"{units.fmt_eok(price)} × "
                       f"{units.fmt_pct(negotiated_rate, digits=2)} "
                       f"(법정 상한 {units.fmt_won(legal_max)})")

    fee = CostItem("중개보수", amount, rule.verification, formula, note)

    if not rule.get("vat_applicable"):
        vat = CostItem("중개보수 부가세", 0, rule.verification,
                       "이 규칙은 부가세 별도 항목이 아님")
        return fee, vat, evidence

    rate, ev = vat_rate(conn, as_of=day, allow_unverified=allow_unverified)
    if rate is None:
        vat = CostItem("중개보수 부가세", None, rules.UNKNOWN, "부가가치세율 규칙 미입력",
                       "중개보수에는 부가세가 별도로 붙습니다. 0원으로 세지 않았습니다")
    else:
        if ev:
            evidence.append(ev)
        vat = CostItem("중개보수 부가세", int(units.won_round(amount * rate)),
                       rules.VERIFIED,
                       f"{units.fmt_won(amount)} × {units.fmt_pct(rate)}",
                       "간이과세 중개사무소는 부가세를 따로 받지 않을 수 있습니다")
    return fee, vat, evidence


@dataclass(frozen=True)
class LegalCost:
    """법무·등기비 예상액. 확정액이 아니다."""
    base_fee: CostItem
    vat: CostItem
    registration_expenses: list[CostItem] = field(default_factory=list)
    certificate_expenses: list[CostItem] = field(default_factory=list)
    other_estimated: CostItem | None = None
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def items(self) -> list[CostItem]:
        out = [self.base_fee, self.vat, *self.registration_expenses,
               *self.certificate_expenses]
        if self.other_estimated is not None:
            out.append(self.other_estimated)
        return out

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


def calculate_legal_fee(conn: sqlite3.Connection, *, price: int, as_of: str | date,
                        region: str | None = None, other_estimated: int = 0,
                        allow_unverified: bool = False) -> LegalCost:
    """소유권이전등기 기본보수 + 실비. 정액을 박아 넣지 않는다."""
    price = units.as_won(price)
    day = rules.as_ymd(as_of)
    evidence: list[Evidence] = []

    rule = _find(conn, "법무비", price=price, as_of=day, region=region,
                 allow_unverified=allow_unverified)
    if rule is None:
        base = CostItem(
            "법무사 기본보수", None, rules.UNKNOWN, "보수표 미입력",
            "대한법무사협회 보수표(소유권이전등기)를 `cost` 규칙에 넣어야 계산됩니다. "
            "임의의 정액을 쓰지 않습니다")
        vat = CostItem("법무보수 부가세", None, rules.UNKNOWN, "기본보수를 몰라 계산 불가")
    else:
        amount, formula = _apply(rule, price)
        evidence.append(rule.evidence)
        base = CostItem("법무사 기본보수", amount, rule.verification, formula,
                        str(rule.get("note") or ""))
        if rule.get("vat_applicable"):
            rate, ev = vat_rate(conn, as_of=day, allow_unverified=allow_unverified)
            if rate is None:
                vat = CostItem("법무보수 부가세", None, rules.UNKNOWN,
                               "부가가치세율 규칙 미입력")
            else:
                if ev:
                    evidence.append(ev)
                vat = CostItem("법무보수 부가세", int(units.won_round(amount * rate)),
                               rules.VERIFIED,
                               f"{units.fmt_won(amount)} × {units.fmt_pct(rate)}")
        else:
            vat = CostItem("법무보수 부가세", 0, rule.verification, "부가세 별도 아님")

    registration: list[CostItem] = []
    for kind in REGISTRATION_KINDS:
        found = _find(conn, kind, price=price, as_of=day, region=region,
                      allow_unverified=allow_unverified)
        if found is None:
            registration.append(CostItem(
                kind, None, rules.UNKNOWN, "규칙 미입력",
                "0원이 아니라 확인 불가입니다"))
            continue
        amount, formula = _apply(found, price)
        evidence.append(found.evidence)
        registration.append(CostItem(kind, amount, found.verification, formula,
                                     str(found.get("note") or "")))

    certificates: list[CostItem] = []
    for kind in CERTIFICATE_KINDS:
        found = _find(conn, kind, price=price, as_of=day, region=region,
                      allow_unverified=allow_unverified)
        if found is None:
            certificates.append(CostItem(kind, None, rules.UNKNOWN, "규칙 미입력"))
            continue
        amount, formula = _apply(found, price)
        evidence.append(found.evidence)
        certificates.append(CostItem(kind, amount, found.verification, formula))

    other = CostItem("기타 예상 실비", units.as_won(other_estimated), rules.ESTIMATED,
                     "사용자 입력") if other_estimated else None

    return LegalCost(base, vat, registration, certificates, other, evidence)
