"""총취득비용과 실투자금 (지시 §11·§12·§17).

이 프로그램에서 가장 중요한 숫자다. 사용자가 "현금 3억" 이라고 말할 때 찾아야 하는
것은 **매매가 3억 이하**인 아파트가 아니라 **실투자금 3억 이하**인 아파트다(§13).

    TOTAL_PURCHASE_COST = 매수가
                        + 취득세 + 지방교육세 + 농어촌특별세
                        + 중개보수 + 중개보수 부가세
                        + 법무·등기비(기본보수 + 부가세 + 실비)
                        + 기타 필수비용

    SELF_CAPITAL_REQUIRED = TOTAL_PURCHASE_COST
                          − AVAILABLE_MORTGAGE
                          − ASSUMABLE_DEPOSIT

지키는 규칙 두 가지.

1. **모르는 비용을 0원으로 세지 않는다.** 하나라도 UNKNOWN 이면 "실투자금 확정"이
   아니라 "예상 실투자금"이고, 실제 필요액은 그보다 크다.
2. **모르는 대출을 최대치로 세지 않는다.** 대출을 못 구했으면 차감하지 않고,
   "대출을 못 받는다고 보면 얼마" 를 함께 보여준다. 대출을 낙관하면 실투자금이
   작게 나오고, 살 수 없는 집을 살 수 있다고 말하게 된다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import regions, rules, units
from apt_engine.cash import costs as cost_mod
from apt_engine.regulation import mortgage as mortgage_mod
from apt_engine.regulation import zone as zone_mod
from apt_engine.tax import acquisition
from apt_engine.trace import Calc, Evidence

Item = cost_mod.CostItem


class CapitalCombinationError(ValueError):
    """현실에 없는 자금조달 조합. 실투자금을 계산하지 않는다."""


@dataclass(frozen=True)
class SelfCapital:
    purchase_price: int
    cost_items: list[Item]
    total_purchase_cost: int
    mortgage: mortgage_mod.Mortgage | None
    available_mortgage: int | None
    assumable_deposit: int | None
    required: int | None                 # 대출·보증금을 모두 반영한 실투자금
    required_without_loan: int           # 대출을 못 받는다고 볼 때
    verification: str
    unknown: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pending_policies: list[str] = field(default_factory=list)
    calc: Calc | None = None

    @property
    def confirmed(self) -> bool:
        """모든 항목이 VERIFIED 일 때만 '확정'이라고 부를 수 있다."""
        return not self.unknown and self.verification == rules.VERIFIED

    @property
    def title(self) -> str:
        return "실투자금 확정" if self.confirmed else "예상 실투자금"

    @property
    def label(self) -> str:
        if self.required is None:
            return (f"확인 불가 — 대출 가능액을 몰라 실투자금을 확정할 수 없습니다. "
                    f"대출 없이 사면 {units.fmt_eok(self.required_without_loan)}")
        head = units.fmt_eok(self.required)
        return head if self.confirmed else f"{head} (예상 · 확인 불가 {len(self.unknown)}개)"

    def cash_utilization(self, available_cash: int) -> float | None:
        """투자금 사용 효율 (§14). 내 현금의 몇 %가 이 집에 들어가나."""
        if self.required is None or available_cash <= 0:
            return None
        return self.required / available_cash

    def affordable(self, available_cash: int) -> bool | None:
        """이 현금으로 살 수 있나. 모르면 None — '가능'으로 넘기지 않는다."""
        if self.required is None:
            return None
        return self.required <= available_cash


def compute(conn: sqlite3.Connection, *, price: int, as_of: str | date,
            lawd_cd: str, emd_name: str | None = None,
            current_home_count: int = 0,
            exclusive_area_m2: float | None = None,
            first_home_buyer: bool = False,
            temporary_two_home: bool = False,
            buyer_type: str = "개인",
            annual_income: int | None = None,
            existing_annual_payment: int = 0,
            interest_rate: float | None = None,
            mortgage_term_years: int = 30,
            repayment_type: str = "원리금균등",
            requested_mortgage: int | None = None,
            bank_quote: int | None = None,
            lender_type: str = mortgage_mod.DEFAULT_LENDER,
            disposal_condition: bool = False,
            use_mortgage: bool = True,
            jeonse_deposit: int | None = None,
            assume_jeonse: bool = False,
            negotiated_brokerage_rate: float | None = None,
            other_required_costs: int = 0,
            other_legal_expenses: int = 0,
            scope: str = zone_mod.DOMESTIC,
            region: str | None = None,
            allow_unverified: bool = False) -> SelfCapital:
    """총취득비용과 실투자금.

    assume_jeonse 가 False 면 일반 매수다 — ASSUMABLE_DEPOSIT = 0 (§12).
    True 면 토지거래허가구역 판정을 거쳐 **승계 가능할 때만** 차감한다.
    """
    price = units.as_won(price)
    day = rules.as_ymd(as_of)
    if region is None:
        region = regions.sido_of(lawd_cd)

    zone = zone_mod.zone_at(conn, lawd_cd, as_of=day, emd_name=emd_name)
    permit = zone_mod.permit_zone_at(conn, lawd_cd, as_of=day, scope=scope,
                                     emd_name=emd_name)
    regulated = bool(zone.types)

    evidence: list[Evidence] = list(zone.evidence) + list(permit.evidence)
    notes: list[str] = []

    items: list[Item] = [Item("매수가", price, rules.VERIFIED, "실거래·호가 기준 입력값")]

    # ── 취득 관련 세금 ──
    tax = acquisition.assess(
        conn, price=price, as_of=day, current_home_count=current_home_count,
        resulting_home_count=current_home_count + 1, regulated_area=regulated,
        exclusive_area_m2=exclusive_area_m2, temporary_two_home=temporary_two_home,
        first_home_buyer=first_home_buyer, buyer_type=buyer_type,
        allow_unverified=allow_unverified)
    for t in tax.items:
        items.append(Item(t.name, t.amount, t.verification, t.formula, t.note))
    evidence.extend(tax.calc.evidence)

    # ── 중개보수 + 부가세 ──
    fee, vat, ev = cost_mod.brokerage(
        conn, price=price, as_of=day, region=region,
        negotiated_rate=negotiated_brokerage_rate, allow_unverified=allow_unverified)
    items += [fee, vat]
    evidence.extend(ev)

    # ── 법무·등기비 ──
    legal = cost_mod.calculate_legal_fee(
        conn, price=price, as_of=day, region=region,
        other_estimated=other_legal_expenses, allow_unverified=allow_unverified)
    items += legal.items
    evidence.extend(legal.evidence)

    if other_required_costs:
        items.append(Item("기타 필수비용", units.as_won(other_required_costs),
                          rules.ESTIMATED, "사용자 입력"))

    unknown = [i.name for i in items if not i.known]
    total_purchase_cost = sum(i.amount for i in items if i.known)

    # ── 전세 승계와 주담대는 동시에 성립하지 않는다 ──
    #
    # 전세보증금은 주택에 걸린 선순위 채권이다. 세입자가 있는 집에 은행이
    # 주담대를 그 한도대로 내주지 않는다(보증금만큼 담보여력이 줄고, 대개
    # 아예 안 나온다). 그런데 예전 코드는 **각각의 최대치를 그냥 뺐다.**
    #
    #     실투자금 = 총취득비용 − 주담대최대 − 전세보증금
    #
    # 그래서 5억짜리에 주담대 3억 + 보증금 3억이 동시에 잡혀 실투자금이
    # **음수**로 나왔다. 현실에 없는 조합이 가장 매력적인 후보로 올라온다.
    #
    # UI 에서만 막으면 `rank` · `backtest` 경로로 그대로 샌다. §2 가 Capital
    # Gate 를 모든 Ranking 보다 먼저 두라고 한 이상, 거부는 엔진이 해야 한다.
    if use_mortgage and assume_jeonse and jeonse_deposit:
        raise CapitalCombinationError(
            "전세 승계와 주택담보대출을 동시에 적용할 수 없습니다. "
            "전세보증금이 선순위라 담보여력이 남지 않습니다 — 각각의 최대치를 "
            "더하면 실투자금이 실제보다 작게(때로는 음수로) 나옵니다. "
            "`use_mortgage=False`(갭투자) 또는 `assume_jeonse=False`(실입주) 중 "
            "하나를 고르세요.")

    # ── 대출 ──
    mortgage = None
    available_mortgage: int | None = 0
    if use_mortgage:
        mortgage = mortgage_mod.calculate_final_mortgage_limit(
            conn, price=price, as_of=day, current_home_count=current_home_count,
            regulated_area=regulated, region=region, first_home_buyer=first_home_buyer,
            annual_income=annual_income,
            existing_annual_payment=existing_annual_payment,
            interest_rate=interest_rate, mortgage_term_years=mortgage_term_years,
            repayment_type=repayment_type, requested=requested_mortgage,
            bank_quote=bank_quote, lender_type=lender_type,
            disposal_condition=disposal_condition,
            allow_unverified=allow_unverified)
        available_mortgage = mortgage.expected
        if available_mortgage is None:
            unknown.append("대출 가능액")
        evidence.extend(mortgage.calc.evidence)
    else:
        notes.append("대출을 쓰지 않는 조건으로 계산했습니다(AVAILABLE_MORTGAGE = 0).")

    # ── 승계 전세보증금 ──
    assumable_deposit: int | None = 0
    if not assume_jeonse:
        notes.append("전세 승계 없는 일반 매수 — ASSUMABLE_DEPOSIT = 0.")
    elif jeonse_deposit is None:
        assumable_deposit = None
        unknown.append("승계 전세보증금")
        notes.append("승계할 보증금 금액이 없습니다 — 전세 대표가를 먼저 계산하세요.")
    elif permit.can_use_jeonse is None:
        assumable_deposit = None
        unknown.append("승계 전세보증금(토허 미확인)")
        notes.append("토지거래허가구역 여부를 확인하지 못해 전세 승계 가능성을 "
                     "판단하지 않았습니다. 확인 없이 갭투자 가능으로 보지 않습니다.")
    elif not permit.can_use_jeonse:
        assumable_deposit = 0
        notes.append(f"{permit.label} — 실거주 의무로 전세 승계가 불가해 "
                     f"보증금을 차감하지 않았습니다.")
    else:
        assumable_deposit = units.as_won(jeonse_deposit)

    required_without_loan = total_purchase_cost - (assumable_deposit or 0)
    if available_mortgage is None or assumable_deposit is None:
        required = None
    else:
        required = total_purchase_cost - available_mortgage - assumable_deposit

    verification = rules.weakest_verification(
        *(i.verification for i in items),
        *( [mortgage.verification] if mortgage is not None else [] ))

    intermediates = {
        "항목별": {i.name: i.label + (f"  ({i.formula})" if i.formula else "")
                for i in items},
        "신뢰도": {i.name: i.verification for i in items},
        "TOTAL_PURCHASE_COST": units.fmt_eok(total_purchase_cost)
                               + (" 이상" if unknown else ""),
        "AVAILABLE_MORTGAGE": (units.fmt_eok(available_mortgage)
                               if available_mortgage is not None else "확인 불가"),
        "ASSUMABLE_DEPOSIT": (units.fmt_eok(assumable_deposit)
                              if assumable_deposit is not None else "확인 불가"),
        "SELF_CAPITAL_REQUIRED": (units.fmt_eok(required) if required is not None
                                  else "확인 불가"),
        "대출 없이 살 경우": units.fmt_eok(required_without_loan),
        "규제지역": zone.label,
        "토지거래허가구역": permit.label,
        "비고": notes,
    }
    if unknown:
        intermediates["확인 불가"] = unknown
        intermediates["주의"] = (
            f"확인 불가 항목 {len(unknown)}개를 0원으로 세지 않았습니다. "
            f"'실투자금 확정'이 아니라 '예상 실투자금'으로 표시해야 합니다 — "
            f"실제 필요한 현금은 이 값보다 큽니다.")
    if mortgage is not None:
        intermediates["대출"] = {
            "POLICY_MAX_MORTGAGE": mortgage.calc.intermediates["POLICY_MAX_MORTGAGE"],
            "EXPECTED_MORTGAGE": mortgage.calc.intermediates["EXPECTED_MORTGAGE"],
            "결정 요인": mortgage.binding or "확인 불가",
            "안내": mortgage_mod.DISCLAIMER,
        }

    calc = Calc(
        value=required, unit="원",
        formula=("SELF_CAPITAL_REQUIRED = TOTAL_PURCHASE_COST "
                 "− AVAILABLE_MORTGAGE − ASSUMABLE_DEPOSIT"),
        inputs={"매수가": units.fmt_eok(price), "기준일": day,
                "지역": lawd_cd + (f" {emd_name}" if emd_name else ""),
                "보유주택수": current_home_count,
                "전용면적": (f"{exclusive_area_m2:g}㎡" if exclusive_area_m2 else "미입력"),
                "생애최초": first_home_buyer},
        intermediates=intermediates,
        evidence=tuple(evidence),
        grade="ESTIMATED",
    )
    return SelfCapital(
        purchase_price=price, cost_items=items,
        total_purchase_cost=total_purchase_cost, mortgage=mortgage,
        available_mortgage=available_mortgage, assumable_deposit=assumable_deposit,
        required=required, required_without_loan=required_without_loan,
        verification=verification, unknown=unknown, notes=notes,
        pending_policies=tax.pending_policies, calc=calc)
