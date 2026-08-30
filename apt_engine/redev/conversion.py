"""신축전환원가와 재건축 마진 (요구사항 18·30).

재건축 단지를 볼 때 진짜 질문은 "분담금이 얼마냐"가 아니라
**"이 헌 집을 새 집으로 바꾸는 데 결국 총 얼마가 드느냐"** 이고,
그 값이 **지금 옆 동네 신축을 그냥 사는 값보다 싼가**이다.

    신축전환원가 = 매수가
                 + 취득 관련 세금·부대비용
                 + 추가분담금
                 + 사업기간 동안의 금융비용
                 + 사업기간 동안의 보유비용
                 + 이주비 이자 등 기타

    재건축 마진 = 준공 후 예상 가치 − 신축전환원가

이 값을 "얼마 번다"로 읽으면 안 된다. 사업기간이 10년이면 그 10년의 기회비용이
빠져 있고(그건 PHASE 7 의 IRR 이 다룬다), 준공 후 가치는 시나리오다.

모르는 항목은 0으로 세지 않는다. 보유비용을 모르면 원가는 "얼마 이상"으로만 말한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import regions, rules, units
from apt_engine.cash.equity import Item, cost_by_rule
from apt_engine.redev.feasibility import Assumptions
from apt_engine.tax import acquisition
from apt_engine.trace import Calc, Evidence


@dataclass(frozen=True)
class Conversion:
    total: int
    items: list[Item]
    unknown: list[str] = field(default_factory=list)
    future_value: int | None = None
    margin: int | None = None
    calc: Calc | None = None

    @property
    def complete(self) -> bool:
        return not self.unknown

    @property
    def label(self) -> str:
        head = units.fmt_eok(self.total)
        return head if self.complete else f"{head} 이상 (확인 불가 {len(self.unknown)}개)"

    @property
    def margin_label(self) -> str:
        if self.margin is None:
            return "확인 불가 — 준공 후 예상 가치가 없습니다"
        # 원가에 구멍이 있으면 실제 마진은 계산값보다 **작다**. 이익이면 이익이 줄고,
        # 손실이면 손실이 커진다 — 문장을 방향에 맞춰 쓴다.
        if self.margin >= 0:
            tail = "" if self.complete else " 이하 (원가에 확인 불가 항목이 있어 실제 마진은 더 작습니다)"
            return f"마진 {units.fmt_eok(self.margin)}{tail}"
        tail = "" if self.complete else " 이상 (원가에 확인 불가 항목이 있어 실제 손실은 더 큽니다)"
        return f"손실 {units.fmt_eok(abs(self.margin))}{tail}"


def compute(conn: sqlite3.Connection, *, price: int, as_of: str | date,
            lawd_cd: str, extra_charge: int | None,
            house_count: int = 1, emd_name: str | None = None,
            exclusive_area_m2: float | None = None,
            regulated: bool | None = None,
            years: int | None = None,
            loan_amount: int = 0, loan_rate: float | None = None,
            annual_holding_cost: int | None = None,
            other_cost: int = 0,
            future_value: int | None = None,
            allow_unverified: bool = False,
            evidence: tuple[Evidence, ...] = ()) -> Conversion:
    """헌 집 → 새 집 전환에 드는 총원가.

    years 는 준공까지 남은 기간(년)이다. `redev.stage.remaining()` 이 '확인 불가'를
    돌려주면 여기서도 금융·보유비용을 계산하지 않는다 — 기간을 지어내지 않는다.
    """
    price = units.as_won(price)
    day = rules.as_ymd(as_of)
    region = regions.sido_of(lawd_cd)

    items: list[Item] = [Item("매수가", price, +1)]
    unknown: list[str] = []
    ev = list(evidence)

    # ── 취득 관련 세금 ──
    try:
        tax = acquisition.compute(
            conn, price=price, as_of=day, house_count=house_count,
            regulated=bool(regulated), exclusive_area_m2=exclusive_area_m2,
            allow_unverified=allow_unverified)
        items.append(Item("취득 관련 세금", tax.value, +1,
                          str(tax.intermediates.get("세목별", ""))))
        ev.extend(tax.evidence)
    except rules.RuleError as e:
        items.append(Item("취득 관련 세금", None, +1, str(e).split(".")[0]))
        unknown.append("취득 관련 세금")

    for kind in ("중개보수", "법무비"):
        amount, note = cost_by_rule(conn, kind, price=price, as_of=day, region=region,
                                    allow_unverified=allow_unverified)
        items.append(Item(kind, amount, +1, note))
        if amount is None:
            unknown.append(kind)

    # ── 추가분담금 ──
    if extra_charge is None:
        items.append(Item("추가분담금", None, +1, "사업성 시나리오 계산 불가"))
        unknown.append("추가분담금")
    elif extra_charge < 0:
        items.append(Item("추가분담금(환급)", extra_charge, +1,
                          "비례율 100% 초과 — 이 시나리오에서는 환급"))
    else:
        items.append(Item("추가분담금", extra_charge, +1, "시나리오 값"))

    # ── 금융비용 ──
    if years is None:
        items.append(Item("금융비용", None, +1, "사업기간 확인 불가 — 계산하지 않음"))
        unknown.append("금융비용(사업기간 미상)")
    elif loan_amount and loan_rate is None:
        items.append(Item("금융비용", None, +1, "대출금리 미입력 — 확인 불가"))
        unknown.append("금융비용(금리 미입력)")
    elif loan_amount:
        interest = int(units.won_round(loan_amount * loan_rate * years))
        items.append(Item("금융비용", interest, +1,
                          f"{units.fmt_eok(loan_amount)} × {loan_rate:.2%} × {years}년 "
                          f"(단리 근사, 상환 스케줄 미반영)"))
    else:
        items.append(Item("금융비용", 0, +1, "대출 없음"))

    # ── 보유비용 ──
    if annual_holding_cost is None:
        items.append(Item("보유비용", None, +1,
                          "연간 보유비용 미입력 — 재산세·종부세 규칙이 들어오면 계산됩니다"))
        unknown.append("보유비용")
    elif years is None:
        items.append(Item("보유비용", None, +1, "사업기간 확인 불가"))
        unknown.append("보유비용(사업기간 미상)")
    else:
        items.append(Item("보유비용", annual_holding_cost * years, +1,
                          f"연 {units.fmt_eok(annual_holding_cost)} × {years}년"))

    if other_cost:
        items.append(Item("기타(이주비 이자 등)", units.as_won(other_cost), +1, "사용자 입력"))

    total = sum(i.signed for i in items if i.known)
    margin = None if future_value is None else future_value - total

    calc = Calc(
        value=total, unit="원",
        formula="신축전환원가 = 매수가 + 취득비용 + 추가분담금 + 금융비용 + 보유비용 + 기타",
        inputs={"매수가": units.fmt_eok(price), "기준일": day,
                "사업기간": f"{years}년" if years is not None else "확인 불가",
                "지역": lawd_cd + (f" {emd_name}" if emd_name else "")},
        intermediates={
            "항목별": {i.name: (units.fmt_eok(i.amount) if i.known else "확인 불가")
                    + (f"  ({i.note})" if i.note else "") for i in items},
            "신축전환원가": units.fmt_eok(total) + ("" if not unknown else " 이상"),
            "준공 후 예상 가치": (units.fmt_eok(future_value) if future_value is not None
                          else "확인 불가"),
            "재건축 마진": (units.fmt_eok(margin) if margin is not None else "확인 불가"),
            "확인 불가 항목": unknown or "없음",
            "해석": ("마진은 '얼마 번다'가 아닙니다. 사업기간의 기회비용과 지연위험이 "
                   "빠져 있고, 준공 후 가치는 가정입니다. "
                   "같은 돈으로 지금 인근 신축을 사는 경우와 비교해야 의미가 있습니다"),
        },
        evidence=tuple(ev),
        grade="SCENARIO",
    )
    return Conversion(total, items, unknown, future_value, margin, calc)


def future_value_of(a: Assumptions, *, per_m2: int | None = None) -> tuple[int, str]:
    """준공 후 예상 가치 한 세대. (금액, 근거설명).

    별도 시세 가정이 없으면 **일반분양가로 갈음한다.** 통상 준공 시점 시세는
    분양가보다 높으므로 이 갈음은 보수적이고, 그 사실을 설명에 적는다.
    미래 상승률을 곱해 값을 부풀리지 않는다.
    """
    if per_m2 is not None:
        return (int(units.won_round(a.avg_new_unit_area_m2 * per_m2)),
                f"입력한 준공 후 단가 {per_m2:,}원/㎡ × {a.avg_new_unit_area_m2:g}㎡")
    return (int(units.won_round(a.avg_new_unit_area_m2 * a.new_price_per_m2)),
            f"일반분양가 {a.new_price_per_m2:,}원/㎡ 로 갈음 — "
            f"미래 상승률을 곱하지 않았습니다(보수적)")
