"""취득세 · 지방교육세 · 농어촌특별세 — 세 개를 따로 계산해 합친다 (요구사항 25).

세율은 이 파일 어디에도 없다. `tax_rule` 테이블에서 **매수 시점**(`as_of`) 기준으로
찾아 쓴다. 2026년 세법으로 2021년 매수를 계산하면 백테스트가 통째로 거짓이 된다.

세 세목을 하나의 합산세율로 저장하지 않는 이유는 단순하다 — 셋의 과세표준과 감면이
서로 다르다. 특히 농어촌특별세는 두 갈래다.

    rural_special_tax_regular         일반 농특세. 국민주택규모(85㎡) 이하면 비과세
    rural_special_tax_from_exemption  취득세를 감면받으면 그 **감면액에** 붙는 농특세

생애최초 감면처럼 취득세가 줄면 농특세가 새로 생기는 구조라, 둘을 한 칸에 넣으면
감면을 반영한 순간 숫자가 틀린다.

그리고 **시행 중인 법령만 계산에 쓴다.** 발표만 된 감면(status=ANNOUNCED/PROPOSED)은
금액에 넣지 않고 "향후 정책 변경 가능" 안내로만 돌려준다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import rules, units
from apt_engine.tax import rules as tax_rules
from apt_engine.trace import Calc, Evidence

# 취득세 감면은 별도 세목으로 관리한다. 표준세율 규칙과 섞이면 안 된다.
REDUCTION = "취득세감면"

# 감면분 농특세 규칙을 알아보는 표식. rule_key 에 이 말이 들어간다.
FROM_EXEMPTION_MARK = "감면"

# 국민주택규모. 이 숫자는 법령의 정의(주택법 시행령)이고 세율이 아니라 **경계**다.
# 실제 비과세 여부는 tax_rule 의 conditions_json(exclusive_area_lte)이 판정한다.
NATIONAL_HOUSING_M2 = 85.0


@dataclass(frozen=True)
class TaxItem:
    """세목 하나. 금액을 모르면 amount 가 None 이고, 0 원과 구분된다."""
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


@dataclass(frozen=True)
class AcquisitionTax:
    acquisition_tax: TaxItem
    local_education_tax: TaxItem
    rural_special_tax_regular: TaxItem
    rural_special_tax_from_exemption: TaxItem
    reduction: TaxItem
    total: int
    verification: str
    unknown: list[str] = field(default_factory=list)
    pending_policies: list[str] = field(default_factory=list)
    calc: Calc | None = None

    @property
    def items(self) -> list[TaxItem]:
        return [self.acquisition_tax, self.local_education_tax,
                self.rural_special_tax_regular, self.rural_special_tax_from_exemption]

    @property
    def complete(self) -> bool:
        return not self.unknown

    @property
    def label(self) -> str:
        head = units.fmt_won(self.total)
        return head if self.complete else f"{head} 이상 (확인 불가 {len(self.unknown)}개)"


def build_context(*, current_home_count: int, resulting_home_count: int,
                  regulated_area: bool, exclusive_area_m2: float | None,
                  temporary_two_home: bool = False, first_home_buyer: bool = False,
                  buyer_type: str = "개인") -> dict:
    """규칙 조건(conditions_json)이 보는 값들.

    `house_count` / `exclusive_area` / `regulated` 는 이미 입력된 규칙이 쓰는
    기존 키라 그대로 유지한다. 새 키는 더 세밀한 판정(중과·감면)을 위해 추가한다.
    """
    return {
        # 기존 규칙이 쓰는 키 — 취득 '후' 주택 수가 세율을 가른다
        "house_count": resulting_home_count,
        "regulated": regulated_area,
        "exclusive_area": exclusive_area_m2,
        # 중과·감면 판정용
        "current_home_count": current_home_count,
        "resulting_home_count": resulting_home_count,
        "regulated_area": regulated_area,
        "temporary_two_home": temporary_two_home,
        "first_home_buyer": first_home_buyer,
        "buyer_type": buyer_type,
    }


def _pending(conn: sqlite3.Connection, tax_kind: str, *, as_of: str,
             base: int, context: dict) -> list[str]:
    """아직 시행 전인 정책. 금액에 넣지 않고 안내만 한다."""
    day = rules.as_ymd(as_of)
    rows = conn.execute(
        f"SELECT * FROM tax_rule WHERE tax_kind = ? AND {rules.effective_clause()}",
        (tax_kind, day, day)).fetchall()
    found = rules.pick(rows, context, amount=base,
                       statuses=(rules.ANNOUNCED, rules.PROPOSED))
    return [f"{r.get('rule_key')} ({r.status}) — {r.get('note') or r.get('source_name')}"
            for r in found]


def _surtax(conn: sqlite3.Connection, kind: str, *, as_of: str, base: int,
            context: dict, name: str, allow_unverified: bool,
            exclude_mark: str | None = None,
            require_mark: str | None = None) -> tuple[TaxItem, Evidence | None]:
    """부가세목 하나. 규칙이 없으면 0 원이 아니라 '확인 불가'."""
    found = tax_rules.find(conn, kind, as_of=as_of, base=base, context=context)
    if exclude_mark:
        found = [r for r in found if exclude_mark not in str(r.get("rule_key") or "")]
    if require_mark:
        found = [r for r in found if require_mark in str(r.get("rule_key") or "")]

    if not found:
        return TaxItem(name, None, rules.UNKNOWN, "규칙 미입력",
                       "0원이 아니라 '확인 불가'입니다. 실제 부담액은 이보다 큽니다"), None
    rule = found[0]
    if not rule.verified and not allow_unverified:
        return TaxItem(name, None, rules.NEEDS_VERIFICATION, "규칙 미검증",
                       f"'{rule.get('rule_key')}' 규칙을 사람이 확인하지 않았습니다"), None
    amount, formula = tax_rules.apply_rate(rule, base)
    item = TaxItem(name, amount, rule.verification, formula,
                   str(rule.get("note") or "")
                   + ("" if rule.verified else "  ⚠ 미검증 규칙으로 계산함"))
    return item, rule.evidence


def assess(conn: sqlite3.Connection, *, price: int, as_of: str | date,
           current_home_count: int | None = None,
           resulting_home_count: int | None = None,
           regulated_area: bool = False,
           exclusive_area_m2: float | None = None,
           temporary_two_home: bool = False,
           first_home_buyer: bool = False,
           buyer_type: str = "개인",
           house_count: int | None = None,
           allow_unverified: bool = False) -> AcquisitionTax:
    """취득 관련 세금 일체.

    house_count 는 예전 호출부(취득 후 주택 수)를 위한 이름이다.
    새 코드는 current_home_count / resulting_home_count 를 따로 준다.
    """
    price = units.as_won(price)
    day = rules.as_ymd(as_of)

    if resulting_home_count is None:
        resulting_home_count = (house_count if house_count is not None
                                else (current_home_count or 0) + 1)
    if current_home_count is None:
        current_home_count = max(resulting_home_count - 1, 0)

    context = build_context(
        current_home_count=current_home_count,
        resulting_home_count=resulting_home_count,
        regulated_area=regulated_area, exclusive_area_m2=exclusive_area_m2,
        temporary_two_home=temporary_two_home, first_home_buyer=first_home_buyer,
        buyer_type=buyer_type)

    evidence: list[Evidence] = []
    unknown: list[str] = []
    pending: list[str] = []
    unverified_used: list[str] = []      # allow_unverified 로 통과시킨 규칙
    blocked_unverified: list[str] = []   # 미검증이라 계산하지 못한 세목

    # ── ① 취득세 (표준세율 또는 중과세율) ──
    found = tax_rules.find(conn, tax_rules.ACQUISITION, as_of=day, base=price,
                           context=context)
    if not found:
        gross = TaxItem("취득세", None, rules.UNKNOWN, "규칙 미입력",
                        f"주택수 {resulting_home_count} · "
                        f"{'규제지역' if regulated_area else '비규제지역'} 조건에 맞는 "
                        f"취득세 규칙이 없습니다. 중과세율 규칙 입력이 필요합니다")
    elif not found[0].verified and not allow_unverified:
        gross = TaxItem("취득세", None, rules.NEEDS_VERIFICATION, "규칙 미검증",
                        f"'{found[0].get('rule_key')}' 미검증")
        blocked_unverified.append("취득세")
    else:
        amount, formula = tax_rules.apply_rate(found[0], price)
        gross = TaxItem("취득세", amount, found[0].verification, formula,
                        str(found[0].get("note") or ""))
        evidence.append(found[0].evidence)
        if not found[0].verified:
            unverified_used.append("취득세")
    pending += _pending(conn, tax_rules.ACQUISITION, as_of=day, base=price,
                        context=context)

    # ── ② 취득세 감면 (생애최초 등). 시행 중인 것만 반영한다 ──
    reduction = TaxItem("취득세 감면", 0, rules.VERIFIED, "해당 감면 없음")
    red_found = tax_rules.find(conn, REDUCTION, as_of=day, base=price, context=context)
    pending += _pending(conn, REDUCTION, as_of=day, base=price, context=context)
    if red_found and gross.known:
        rule = red_found[0]
        if not rule.verified and not allow_unverified:
            reduction = TaxItem("취득세 감면", None, rules.NEEDS_VERIFICATION,
                                "감면 규칙 미검증", f"'{rule.get('rule_key')}' 미검증")
        else:
            rate = rule.get("rate")
            cut = (int(units.won_round(gross.amount * float(rate)))
                   if rate is not None else int(rule.get("fixed_amount") or 0))
            cap = rule.get("max_amount")
            capped = ""
            if cap is not None and cut > int(cap):
                cut, capped = int(cap), f" → 한도 {units.fmt_won(int(cap))}"
            reduction = TaxItem(
                "취득세 감면", cut, rule.verification,
                f"{gross.label} × {units.fmt_pct(float(rate), digits=1)}{capped}"
                if rate is not None else f"정액 {units.fmt_won(cut)}",
                str(rule.get("note") or ""))
            evidence.append(rule.evidence)

    if gross.known and reduction.known:
        net_amount = max(gross.amount - reduction.amount, 0)
        acquisition = TaxItem(
            "취득세", net_amount, rules.weakest_verification(
                gross.verification, reduction.verification),
            gross.formula + (f" − 감면 {units.fmt_won(reduction.amount)}"
                             if reduction.amount else ""),
            gross.note)
    else:
        acquisition = TaxItem("취득세", None,
                              rules.weakest_verification(gross.verification,
                                                         reduction.verification),
                              gross.formula, gross.note)
        unknown.append("취득세")

    # ── ③ 지방교육세 ──
    edu, ev = _surtax(conn, tax_rules.LOCAL_EDUCATION, as_of=day, base=price,
                      context=context, name="지방교육세",
                      allow_unverified=allow_unverified)
    if ev:
        evidence.append(ev)
    if not edu.known:
        unknown.append("지방교육세")

    # ── ④ 농어촌특별세 (일반분) — 85㎡ 이하면 비과세 ──
    rural, ev = _surtax(conn, tax_rules.RURAL_SPECIAL, as_of=day, base=price,
                        context=context, name="농어촌특별세(일반)",
                        allow_unverified=allow_unverified,
                        exclude_mark=FROM_EXEMPTION_MARK)
    if ev:
        evidence.append(ev)
    if not rural.known:
        unknown.append("농어촌특별세(일반)")

    # ── ⑤ 농어촌특별세 (감면분) — 감면을 받았을 때만 생긴다 ──
    if reduction.known and reduction.amount:
        rural_ex, ev = _surtax(conn, tax_rules.RURAL_SPECIAL, as_of=day,
                               base=reduction.amount, context=context,
                               name="농어촌특별세(감면분)",
                               allow_unverified=allow_unverified,
                               require_mark=FROM_EXEMPTION_MARK)
        if ev:
            evidence.append(ev)
        if not rural_ex.known:
            unknown.append("농어촌특별세(감면분)")
    else:
        rural_ex = TaxItem("농어촌특별세(감면분)", 0, rules.VERIFIED,
                           "감면이 없어 발생하지 않음")

    items = [acquisition, edu, rural, rural_ex]
    total = sum(i.amount for i in items if i.known)
    verification = rules.weakest_verification(*(i.verification for i in items))

    if exclusive_area_m2 is None:
        area_label = "미입력 — 농어촌특별세 비과세 여부를 판정할 수 없습니다"
    elif exclusive_area_m2 <= NATIONAL_HOUSING_M2:
        area_label = f"{exclusive_area_m2:g}㎡ (국민주택규모 {NATIONAL_HOUSING_M2:g}㎡ 이하)"
    else:
        area_label = f"{exclusive_area_m2:g}㎡ (국민주택규모 {NATIONAL_HOUSING_M2:g}㎡ 초과)"

    intermediates = {
        "세목별": {i.name: i.label for i in items},
        "계산식": {i.name: i.formula for i in items},
        "감면": (reduction.label + (f"  ({reduction.formula})" if reduction.formula else "")),
        "신뢰도": {i.name: i.verification for i in items},
        "조건": {
            "취득 전 주택수": current_home_count,
            "취득 후 주택수": resulting_home_count,
            "규제지역": regulated_area,
            "전용면적": area_label,
            "일시적 2주택": temporary_two_home,
            "생애최초": first_home_buyer,
            "매수주체": buyer_type,
        },
        "기준일": day,
    }
    if unknown:
        intermediates["주의"] = (
            f"확인 불가 항목: {', '.join(unknown)}. 0원으로 세지 않았으므로 "
            f"실제 부담액은 이 합계보다 **큽니다**.")
    unverified_used += [i.name for i in items
                        if i.known and i.verification != rules.VERIFIED
                        and i.name not in unverified_used]
    if unverified_used:
        intermediates["미검증"] = (
            f"{', '.join(unverified_used)} 규칙이 사람 확인을 거치지 않았습니다. "
            f"원문을 확인한 뒤 `cli rule verify` 로 표시하세요.")
    if pending:
        intermediates["향후 정책 변경 가능"] = pending

    calc = Calc(
        value=total, unit="원",
        formula="취득세(− 감면) + 지방교육세 + 농어촌특별세(일반) + 농어촌특별세(감면분)",
        inputs={"취득가액": units.fmt_eok(price), "기준일": day},
        intermediates=intermediates,
        evidence=tuple(evidence),
        # 세법 적용은 개인 사정(세대 판정·일시적 2주택 처분기한 등)에 따라 달라진다.
        grade="ESTIMATED",
    )
    return AcquisitionTax(acquisition, edu, rural, rural_ex, reduction,
                          total, verification, unknown, pending, calc)


def compute(conn: sqlite3.Connection, *, price: int, as_of: str | date,
            house_count: int, regulated: bool, exclusive_area_m2: float | None = None,
            allow_unverified: bool = False) -> Calc:
    """예전 호출부용 얇은 래퍼. 규칙이 없으면 예외를 던지던 동작을 유지한다."""
    got = assess(conn, price=price, as_of=as_of, house_count=house_count,
                 regulated_area=regulated, exclusive_area_m2=exclusive_area_m2,
                 allow_unverified=allow_unverified)
    if not got.acquisition_tax.known and got.acquisition_tax.verification == (
            rules.NEEDS_VERIFICATION):
        raise rules.UnverifiedRuleError(
            f"취득세 규칙을 아직 사람이 확인하지 않았습니다"
            f"(last_verified 비어 있음). 원문을 확인한 뒤 `cli rule verify` 로 "
            f"표시하거나, 확인 없이 진행하려면 allow_unverified=True 를 명시하세요. "
            f"— {got.acquisition_tax.note}")
    if not got.acquisition_tax.known:
        raise rules.NoRuleError(
            f"{rules.as_ymd(as_of)} 기준 취득세 규칙이 없습니다 "
            f"(과세표준 {units.as_won(price):,}원, 주택수 {house_count}, "
            f"규제지역 {regulated}). {got.acquisition_tax.note}")
    return got.calc
