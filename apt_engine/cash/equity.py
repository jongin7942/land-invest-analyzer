"""실제 필요한 현금 (요구사항 27).

    매수가
    − 승계 전세보증금       ← 토허면 뺄 수 없다
    − 주택담보대출          ← LTV·DSR 중 더 제한적인 값
    + 취득세·지방교육세·농특세
    + 중개보수
    + 법무비
    + 수리비 · 기타 초기비용
    + 안전자금
    = 실제 필요한 현금

가장 중요한 규칙: **모르는 항목을 0으로 세지 않는다.** 취득세 규칙이 없으면
"취득세 0원"이 아니라 "확인 불가"이고, 그러면 합계도 "최소 얼마 이상"으로만 말한다.
0으로 세면 실투자금이 실제보다 작게 나오고, 그 위에 쌓은 수익률이 전부 부풀려진다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import rules, units, regions
from apt_engine.regulation import loan as loan_mod
from apt_engine.regulation import zone as zone_mod
from apt_engine.tax import acquisition
from apt_engine.trace import Calc


@dataclass(frozen=True)
class Item:
    name: str
    amount: int | None        # None = 확인 불가
    sign: int                 # +1 지출, -1 차감
    note: str = ""

    @property
    def known(self) -> bool:
        return self.amount is not None

    @property
    def signed(self) -> int:
        return (self.amount or 0) * self.sign


@dataclass(frozen=True)
class Equity:
    total: int
    items: list[Item]
    unknown: list[str] = field(default_factory=list)
    calc: Calc = None

    @property
    def complete(self) -> bool:
        return not self.unknown

    @property
    def label(self) -> str:
        return (units.fmt_eok(self.total) if self.complete
                else f"{units.fmt_eok(self.total)} 이상 (확인 불가 항목 {len(self.unknown)}개)")


def cost_by_rule(conn: sqlite3.Connection, cost_kind: str, *, price: int,
                 as_of: str | date, region: str | None = None,
                 allow_unverified: bool = False) -> tuple[int | None, str]:
    """중개보수·법무비 등 부대비용 한 항목. 규칙이 없으면 (None, 사유)."""
    day = rules.as_ymd(as_of)
    rows = conn.execute(
        f"SELECT * FROM cost_rule WHERE cost_kind = ? AND {rules.effective_clause()}",
        (cost_kind, day, day)).fetchall()
    rows = [r for r in rows if r["region"] is None or r["region"] == region]
    found = rules.pick(rows, {}, amount=price, min_col="price_min", max_col="price_max")
    if not found:
        return None, "규칙 미입력 — 확인 불가"
    rule = found[0]
    if not rule.verified and not allow_unverified:
        return None, "규칙 미검증 — 확인 불가"

    fixed = rule.get("fixed_amount")
    if fixed is not None:
        return int(fixed), f"정액 {units.fmt_won(int(fixed))}"
    rate = rule.get("rate")
    if rate is None:
        return None, "규칙에 요율이 없음"
    amount = int(units.won_round(price * float(rate)))
    cap = rule.get("max_amount")
    if cap is not None and amount > int(cap):
        return int(cap), f"{units.fmt_pct(float(rate), digits=2)} → 상한 {units.fmt_won(int(cap))}"
    return amount, f"{units.fmt_eok(price)} × {units.fmt_pct(float(rate), digits=2)}"


def compute(conn: sqlite3.Connection, *, price: int, as_of: str | date,
            house_count: int, lawd_cd: str, emd_name: str | None = None,
            exclusive_area_m2: float | None = None,
            jeonse_deposit: int | None = None,
            loan_amount: int | None = None,
            repair_cost: int = 0, other_cost: int = 0, buffer_cost: int = 0,
            scope: str = zone_mod.DOMESTIC, region: str | None = None,
            allow_unverified: bool = False) -> Equity:
    """실투자금. 규제·세금 데이터가 없으면 그 항목만 '확인 불가'로 남고 나머지는 계산된다."""
    price = units.as_won(price)
    day = rules.as_ymd(as_of)

    # 중개보수는 시·도 조례라 지역별로 다르다. 호출부가 region 을 빠뜨리면 지역이 적힌
    # 규칙이 통째로 걸러져 '규칙 미입력' 으로 보이므로, lawd_cd 에서 직접 유도한다.
    if region is None:
        region = regions.sido_of(lawd_cd)

    zone = zone_mod.zone_at(conn, lawd_cd, as_of=day, emd_name=emd_name)
    permit = zone_mod.permit_zone_at(conn, lawd_cd, as_of=day, scope=scope,
                                     emd_name=emd_name)

    items: list[Item] = [Item("매수가", price, +1)]
    unknown: list[str] = []
    evidence = list(zone.evidence) + list(permit.evidence)

    # ── 전세 승계 — 토허면 뺄 수 없다 ──
    can_jeonse = permit.can_use_jeonse
    if jeonse_deposit is None:
        items.append(Item("승계 전세보증금", None, -1, "전세 대표가 없음 — 확인 불가"))
        unknown.append("승계 전세보증금")
    elif can_jeonse is None:
        # 요구사항 62-11: 토허 확인 없이 갭투자 가능하다고 판단하지 않는다.
        items.append(Item("승계 전세보증금", None, -1,
                          "토허 데이터 미입력 — 전세 활용 가능 여부 확인 불가"))
        unknown.append("승계 전세보증금(토허 미확인)")
    elif not can_jeonse:
        items.append(Item("승계 전세보증금", 0, -1,
                          f"토지거래허가구역 — 실거주 의무로 전세 활용 불가"))
    else:
        items.append(Item("승계 전세보증금", units.as_won(jeonse_deposit), -1))

    # ── 대출 ──
    if loan_amount is not None:
        items.append(Item("주택담보대출", units.as_won(loan_amount), -1))
    else:
        items.append(Item("주택담보대출", 0, -1, "미사용(0)으로 계산"))

    # ── 취득세 ──
    try:
        tax = acquisition.compute(conn, price=price, as_of=day, house_count=house_count,
                                  regulated=bool(zone.types),
                                  exclusive_area_m2=exclusive_area_m2,
                                  allow_unverified=allow_unverified)
        items.append(Item("취득 관련 세금", tax.value, +1,
                          str(tax.intermediates.get("세목별", ""))))
        evidence.extend(tax.evidence)
    except rules.RuleError as e:
        items.append(Item("취득 관련 세금", None, +1, str(e).split(".")[0]))
        unknown.append("취득 관련 세금")

    # ── 부대비용 ──
    for kind, label in (("중개보수", "중개보수"), ("법무비", "법무비")):
        amount, note = cost_by_rule(conn, kind, price=price, as_of=day, region=region,
                                    allow_unverified=allow_unverified)
        items.append(Item(label, amount, +1, note))
        if amount is None:
            unknown.append(label)

    if repair_cost:
        items.append(Item("수리비", units.as_won(repair_cost), +1, "사용자 입력"))
    if other_cost:
        items.append(Item("기타 초기비용", units.as_won(other_cost), +1, "사용자 입력"))
    if buffer_cost:
        items.append(Item("안전자금", units.as_won(buffer_cost), +1, "사용자 입력"))

    total = sum(i.signed for i in items if i.known)

    intermediates = {
        "항목별": {i.name: (units.fmt_eok(i.amount) if i.known else "확인 불가")
                  + (f"  ({i.note})" if i.note else "")
                  for i in items},
        "규제지역": zone.label,
        "토지거래허가구역": permit.label,
        "전세 활용": ("확인 불가" if can_jeonse is None
                    else "가능" if can_jeonse else "불가 — 보증금을 차감하지 않았음"),
    }
    if unknown:
        intermediates["주의"] = (
            f"확인 불가 항목: {', '.join(unknown)}. 실제 필요현금은 이 금액보다 "
            f"**큽니다**. 빠진 항목을 0으로 세지 않았습니다.")

    calc = Calc(
        value=total, unit="원",
        formula="매수가 − 승계전세 − 대출 + 취득세 + 중개보수 + 법무비 + 수리비 + 기타 + 안전자금",
        inputs={"매수가": units.fmt_eok(price), "기준일": day, "주택수": house_count,
                "지역": lawd_cd + (f" {emd_name}" if emd_name else "")},
        intermediates=intermediates,
        evidence=tuple(evidence),
        grade="ESTIMATED",
    )
    return Equity(total, items, unknown, calc)
