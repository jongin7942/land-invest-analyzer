"""보유 vs 갈아타기 — SWITCH_ALPHA = TW_갈아타기 − TW_보유 (종인님 지시 2026-09-04).

엔진이 결국 답해야 할 질문은 "이 단지가 몇 위냐" 가 아니라 **"이미 보유한 자산을
지금 실제로 살 수 있는 대안으로 바꾸면, 거래비용과 세금을 전부 치른 뒤 5년 뒤
순자산이 더 커지나"** 다. 순위가 높다는 이유만으로 갈아타기를 권하지 않는다.

    A. 보유      지금 자산을 그대로 5년 들고 간다 → 세후·이자후 순자산 TW_hold
    B. 갈아타기  지금 매도 → 남는 현금 + 새 대출로 대안 매수 → 5년 뒤 매도 → TW_switch
    SWITCH_ALPHA = TW_switch − TW_hold

갈아타기 비용은 하나도 빼지 않는다(MASTER_SPEC):
    보유자산 매도 중개보수 · 양도 관련 세금 · 대출상환비용(잔액·중도상환) ·
    새 아파트 취득세 · 새 아파트 중개보수 · 법무비 · 신규 대출이자 · 수리/이사비 ·
    남는 현금의 미래가치 · 전세보증금 변동

── 임의 가정 금지 ──────────────────────────────────────────────────
계약일, 대출 금액·금리·상환방식, 전세 승계 여부, 거주 형태, 기존 주택 처분시점
같은 값은 **모르면 USER_INPUT_REQUIRED 로 남기고 결론을 내지 않는다.** 이 모듈은
필수 입력이 하나라도 비어 있으면 숫자 대신 '무엇이 비었는지' 를 돌려준다.
"가정값으로 계산한 결론" 은 없다.

── 결론 승격 기준 ──────────────────────────────────────────────────
SWITCH_ALPHA 가 양수라고 바로 '갈아타기' 가 아니다. 거래비용·세금을 다 치른 뒤에도
보유 대비 **의미 있게** 커야 한다. 그 문턱(MIN_MEANINGFUL_RATIO)은 판정 기준이지
관측값이 아니며, MASTER_SPEC 이 정하면 그 값으로 바꾼다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import rules, units
from apt_engine.cash import costs as cost_mod
from apt_engine.cash import self_capital as capital_mod
from apt_engine.cashflow import timeline as timeline_mod
from apt_engine.tax import capital_gains as cg_mod
from apt_engine.trace import Calc

USER_INPUT_REQUIRED = "USER_INPUT_REQUIRED"

# 보유 자산 대비 이만큼은 더 남아야 '의미 있는' 갈아타기다. 판정 기준(가정).
MIN_MEANINGFUL_RATIO = 0.05

# 갈아타기에서 반드시 세는 항목. 하나라도 값을 못 만들면 결론을 내지 않는다.
SWITCH_COST_ITEMS = (
    "보유자산 매도 중개보수", "양도 관련 세금", "대출상환비용",
    "새 아파트 취득세", "새 아파트 중개보수", "법무비", "신규 대출이자",
    "수리/이사비", "남는 현금의 미래가치", "전세보증금 변동",
)


@dataclass(frozen=True)
class HeldAsset:
    """이미 보유한 자산. 스크리닝 대표가가 아니라 **실제 매수가**를 쓴다."""
    complex_id: int
    name: str
    lawd_cd: str
    exclusive_area_m2: float
    purchase_price: int                     # 실제 매수가 (원)
    purchase_date: str | object = USER_INPUT_REQUIRED   # 잔금일 YYYY-MM-DD
    mortgage_amount: int | object = USER_INPUT_REQUIRED
    interest_rate: float | object = USER_INPUT_REQUIRED
    repayment_type: str | object = USER_INPUT_REQUIRED  # 원리금균등/원금균등/만기일시
    assumed_deposit: int | object = USER_INPUT_REQUIRED # 전세 승계 보증금 (없으면 0)
    occupancy: str | object = USER_INPUT_REQUIRED       # 실거주/임대/전세승계
    house_count_at_purchase: int = 2                    # 일시적 2주택 (확정)
    temporary_two_home: bool = True
    other_home_disposal_date: str | object = USER_INPUT_REQUIRED
    current_market_price: int | object = USER_INPUT_REQUIRED  # 지금 팔면 받을 가격(저층 시세)

    def missing(self) -> list[str]:
        out = []
        for k in ("purchase_date", "mortgage_amount", "interest_rate", "repayment_type",
                  "assumed_deposit", "occupancy", "other_home_disposal_date",
                  "current_market_price"):
            if getattr(self, k) == USER_INPUT_REQUIRED:
                out.append(k)
        return out


@dataclass(frozen=True)
class Alternative:
    """지금 실제로 살 수 있는 대안. 대표가가 아니라 **매수 가능 가격**(호가·최근 실거래)."""
    complex_id: int
    name: str
    lawd_cd: str
    exclusive_area_m2: float
    buyable_price: int
    expected_sale_price: int | None          # 5년 뒤 매도가 (Base 시나리오, 없으면 None)
    deposit: int = 0                         # 전세 승계 보증금
    occupancy: str = "임대"
    settlement_status: str = "SETTLEMENT_VERIFY_REQUIRED"


@dataclass(frozen=True)
class Result:
    tw_hold: int | None
    tw_switch: int | None
    switch_alpha: int | None
    verdict: str                              # 보유 / 갈아타기 후보 / 결론 없음
    missing: list[str] = field(default_factory=list)
    cost_items: dict[str, int | None] = field(default_factory=dict)
    calc: Calc | None = None

    @property
    def label(self) -> str:
        if self.switch_alpha is None:
            return f"결론 없음 — 입력 필요: {', '.join(self.missing)}"
        return (f"SWITCH_ALPHA {units.fmt_eok(self.switch_alpha)} "
                f"(보유 {units.fmt_eok(self.tw_hold)} → 갈아타기 {units.fmt_eok(self.tw_switch)}) · {self.verdict}")


def _terminal_wealth(tl: timeline_mod.Timeline) -> int | None:
    """5년 뒤 세후·이자후 순자산 = 매각 순수입 + 보유기간 순현금흐름 합.

    timeline.build 가 이미 매도가에서 중개보수·양도세·대출잔액·보증금 반환을 빼고,
    해마다 임대수입·보유세·원리금·기타비용을 넣어 둔다. 여기서는 그것을 합칠 뿐이다.
    t=0 의 실투자금은 두 케이스가 같은 '지금' 에서 출발하므로 비교에는 들어가지만,
    TW 자체는 5년 뒤 손에 남는 돈으로 정의한다.
    """
    if not tl.years or any(y.exit_proceeds is None for y in tl.years[-1:]):
        return None
    running = 0
    for y in tl.years:
        holding = y.holding_tax if y.holding_tax is not None else 0
        running += y.rental_income - holding - y.loan_payment - y.other_cost
    return running + (tl.years[-1].exit_proceeds or 0)


def hold_case(conn: sqlite3.Connection, held: HeldAsset, *, as_of: str | date,
              holding_years: int, expected_sale_price: int | None,
              allow_unverified: bool = False) -> tuple[int | None, list[str], Calc]:
    """A. 보유. 실제 매수가·실제 대출·실제 거주형태로 5년 현금흐름을 만든다."""
    missing = held.missing()
    if expected_sale_price is None:
        missing.append("expected_sale_price(5년 뒤 매도가 시나리오)")
    if missing:
        return None, missing, Calc(value=None, unit="원", formula="TW_hold",
                                   inputs={"비어 있는 입력": missing}, grade="SCENARIO")
    cap = capital_mod.compute(
        conn, price=held.purchase_price, as_of=held.purchase_date, lawd_cd=held.lawd_cd,
        current_home_count=held.house_count_at_purchase - 1,
        exclusive_area_m2=held.exclusive_area_m2,
        temporary_two_home=held.temporary_two_home,
        interest_rate=held.interest_rate, repayment_type=held.repayment_type,
        requested_mortgage=held.mortgage_amount,
        allow_unverified=allow_unverified)
    tl = timeline_mod.build(
        conn, capital=cap, as_of=as_of, holding_years=holding_years,
        sale_price=expected_sale_price, occupancy=held.occupancy,
        interest_rate=held.interest_rate, repayment_type=held.repayment_type,
        house_count=held.house_count_at_purchase, lawd_cd=held.lawd_cd,
        allow_unverified=allow_unverified)
    tw = _terminal_wealth(tl)
    calc = Calc(value=tw, unit="원", formula="TW_hold = 매각 순수입 + Σ(연간 순현금흐름)",
                inputs={"매수가": units.fmt_eok(held.purchase_price),
                        "5년 뒤 매도가": units.fmt_eok(expected_sale_price)},
                intermediates={"매각 내역": {i.name: i.label for i in tl.exit_items},
                               "모르는 것": tl.unknown or "없음"},
                grade="SCENARIO")
    return tw, list(tl.unknown), calc


def switch_case(conn: sqlite3.Connection, held: HeldAsset, alt: Alternative, *,
                as_of: str | date, holding_years: int, moving_cost: int | object = USER_INPUT_REQUIRED,
                cash_yield: float | object = USER_INPUT_REQUIRED,
                allow_unverified: bool = False) -> tuple[int | None, list[str], dict, Calc]:
    """B. 지금 매도 → 대안 매수 → 5년 뒤 매도.

    비용 항목(SWITCH_COST_ITEMS)을 전부 만든다. 하나라도 못 만들면 TW 를 내지 않는다.
    """
    missing = held.missing()
    if alt.expected_sale_price is None:
        missing.append("alt.expected_sale_price")
    if moving_cost == USER_INPUT_REQUIRED:
        missing.append("moving_cost(수리/이사비)")
    if cash_yield == USER_INPUT_REQUIRED:
        missing.append("cash_yield(남는 현금의 연 수익률)")
    if missing:
        return None, missing, {}, Calc(value=None, unit="원", formula="TW_switch",
                                       inputs={"비어 있는 입력": missing}, grade="SCENARIO")

    items: dict[str, int | None] = {}
    # ① 보유자산을 지금 판다 — 중개보수·양도세·대출잔액. timeline 을 보유기간 0..1 로
    #    쓰지 않고 capital_gains 를 직접 부른다(보유기간은 잔금일부터 지금까지).
    d_now = date.fromisoformat(rules.as_ymd(as_of))
    d_buy = date.fromisoformat(rules.as_ymd(held.purchase_date))
    held_years = max(1, (d_now - d_buy).days // 365)
    sale_now = held.current_market_price
    cap_held = capital_mod.compute(
        conn, price=held.purchase_price, as_of=held.purchase_date, lawd_cd=held.lawd_cd,
        current_home_count=held.house_count_at_purchase - 1,
        exclusive_area_m2=held.exclusive_area_m2, temporary_two_home=held.temporary_two_home,
        interest_rate=held.interest_rate, repayment_type=held.repayment_type,
        requested_mortgage=held.mortgage_amount, allow_unverified=allow_unverified)
    fee, vat, _ = cost_mod.brokerage(conn, price=sale_now, as_of=as_of,
                                     allow_unverified=allow_unverified)
    # 부가세는 중개사무소가 일반과세자일 때만 붙는다 — 모르면 붙이지 않되 그 사실을 남긴다.
    fee_sell = (fee.amount + (vat.amount or 0)) if fee.amount is not None else None
    items["보유자산 매도 중개보수"] = fee_sell
    gains = cg_mod.compute(conn, sale_price=sale_now, purchase_price=held.purchase_price,
                           expenses=(cap_held.total_purchase_cost - held.purchase_price) + (fee_sell or 0),
                           as_of=as_of, holding_years=held_years,
                           house_count=held.house_count_at_purchase, allow_unverified=allow_unverified)
    tax_sell = (gains.income_tax.amount or 0) + (gains.local_tax.amount or 0) if gains.income_tax.known else None
    items["양도 관련 세금"] = tax_sell
    loan_balance = held.mortgage_amount if isinstance(held.mortgage_amount, int) else None
    items["대출상환비용"] = loan_balance          # 중도상환수수료는 은행 조건 — 모르면 0 이 아니라 표시
    deposit_back = held.assumed_deposit if isinstance(held.assumed_deposit, int) else None
    items["전세보증금 변동"] = (alt.deposit - (deposit_back or 0))
    if any(v is None for v in (fee_sell, tax_sell, loan_balance, deposit_back)):
        missing = [k for k, v in items.items() if v is None]
        return None, missing, items, Calc(value=None, unit="원", formula="TW_switch",
                                          inputs={"못 만든 비용": missing}, grade="SCENARIO")
    cash_after_sale = sale_now - fee_sell - tax_sell - loan_balance - deposit_back

    # ② 대안을 지금 산다 — 취득세·중개보수·법무비는 self_capital 이 cost_items 로 만든다.
    cap_alt = capital_mod.compute(
        conn, price=alt.buyable_price, as_of=as_of, lawd_cd=alt.lawd_cd,
        current_home_count=held.house_count_at_purchase - 1,
        exclusive_area_m2=alt.exclusive_area_m2, temporary_two_home=held.temporary_two_home,
        interest_rate=held.interest_rate, repayment_type=held.repayment_type,
        allow_unverified=allow_unverified)
    by_name = {i.name: i.amount for i in cap_alt.cost_items}
    items["새 아파트 취득세"] = next((v for k, v in by_name.items() if "취득세" in k), None)
    items["새 아파트 중개보수"] = next((v for k, v in by_name.items() if "중개" in k), None)
    items["법무비"] = next((v for k, v in by_name.items() if "법무" in k or "등기" in k), None)
    items["수리/이사비"] = int(moving_cost)
    tl_alt = timeline_mod.build(
        conn, capital=cap_alt, as_of=as_of, holding_years=holding_years,
        sale_price=alt.expected_sale_price, occupancy=alt.occupancy,
        interest_rate=held.interest_rate, repayment_type=held.repayment_type,
        house_count=held.house_count_at_purchase, lawd_cd=alt.lawd_cd,
        allow_unverified=allow_unverified)
    items["신규 대출이자"] = sum(y.loan_interest for y in tl_alt.years) if tl_alt.years else None
    tw_alt = _terminal_wealth(tl_alt)
    # ③ 남는 현금(매도 현금 − 대안 실투자금 − 이사비)은 놀지 않는다 — 5년 복리.
    need = cap_alt.required
    if tw_alt is None or need is None or any(items[k] is None for k in SWITCH_COST_ITEMS if k in items):
        missing = [k for k in SWITCH_COST_ITEMS if items.get(k) is None] + (tl_alt.unknown or [])
        return None, missing, items, Calc(value=None, unit="원", formula="TW_switch",
                                          inputs={"못 만든 것": missing}, grade="SCENARIO")
    leftover = cash_after_sale - need - int(moving_cost)
    items["남는 현금의 미래가치"] = int(round(leftover * (1 + float(cash_yield)) ** holding_years))
    tw = tw_alt + items["남는 현금의 미래가치"]
    calc = Calc(value=tw, unit="원",
                formula="TW_switch = (대안 매각 순수입 + Σ연간 순현금흐름) + 남는 현금×(1+r)^n",
                inputs={"지금 매도가": units.fmt_eok(sale_now), "대안 매수가": units.fmt_eok(alt.buyable_price),
                        "대안 5년 뒤 매도가": units.fmt_eok(alt.expected_sale_price)},
                intermediates={k: (units.fmt_eok(v) if isinstance(v, int) else v) for k, v in items.items()},
                grade="SCENARIO")
    return tw, [], items, calc


def compare(conn: sqlite3.Connection, held: HeldAsset, alt: Alternative, *,
            as_of: str | date, holding_years: int, held_sale_price: int | None,
            moving_cost: int | object = USER_INPUT_REQUIRED,
            cash_yield: float | object = USER_INPUT_REQUIRED,
            allow_unverified: bool = False) -> Result:
    tw_h, miss_h, calc_h = hold_case(conn, held, as_of=as_of, holding_years=holding_years,
                                     expected_sale_price=held_sale_price,
                                     allow_unverified=allow_unverified)
    tw_s, miss_s, items, calc_s = switch_case(conn, held, alt, as_of=as_of, holding_years=holding_years,
                                              moving_cost=moving_cost, cash_yield=cash_yield,
                                              allow_unverified=allow_unverified)
    missing = sorted(set(miss_h) | set(miss_s))
    if tw_h is None or tw_s is None or missing:
        return Result(tw_h, tw_s, None, "결론 없음 — 입력이 비어 있어 계산하지 않았습니다",
                      missing, items, Calc(value=None, unit="원", formula="SWITCH_ALPHA",
                                           inputs={"보유": calc_h.to_dict(), "갈아타기": calc_s.to_dict()},
                                           grade="SCENARIO"))
    alpha = tw_s - tw_h
    if alt.settlement_status != "SETTLEMENT_EVIDENCE_PASS":
        verdict = f"보유 (대안이 Settlement 미통과: {alt.settlement_status})"
    elif alpha > MIN_MEANINGFUL_RATIO * abs(tw_h):
        verdict = "갈아타기 후보 (거래비용·세금 후에도 의미 있게 큼)"
    else:
        verdict = "보유 (갈아타기 이득이 거래비용·세금을 넘지 못함)"
    calc = Calc(value=alpha, unit="원", formula="SWITCH_ALPHA = TW_갈아타기 − TW_보유",
                inputs={"TW_보유": units.fmt_eok(tw_h), "TW_갈아타기": units.fmt_eok(tw_s)},
                intermediates={"판정": verdict, "문턱": f"보유 TW 의 {MIN_MEANINGFUL_RATIO:.0%} (판정 기준)",
                               "갈아타기 비용": {k: units.fmt_eok(v) for k, v in items.items() if isinstance(v, int)}},
                grade="SCENARIO")
    return Result(tw_h, tw_s, alpha, verdict, [], items, calc)
