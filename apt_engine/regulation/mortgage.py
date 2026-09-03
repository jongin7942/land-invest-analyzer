"""주택담보대출 한도 — LTV 하나로 계산하지 않는다 (요구사항 23·62-12, 지시 §5~8).

    POLICY_MAX_MORTGAGE = min(LTV 한도, DSR 한도, 절대 상한, 기타 한도)

네 값을 각각 내고 **가장 작은 것**이 답이다. "최대한 대출"을 골라도 소득이 안 되면
그 돈은 안 나온다. 그리고 한도 하나라도 확인 불가면 최종값도 확인 불가다 —
모르는 한도를 무한대로 두면 대출이 과대계상되고, 실투자금이 실제보다 작게 나온다.

정책값(LTV·DSR·스트레스 가산금리·절대상한)은 코드에 없다. `loan_rule` 에
**rule_type 별로 따로** 들어간다. 한 행에 LTV 와 DSR 을 같이 적으면 시점이 다른
두 정책(예: LTV 는 2023년 개정, DSR 은 2024년 개정)을 표현할 수 없다.

그리고 은행 심사는 우리가 못 한다(§8). 그래서 두 단계로 나눠 말한다.

    POLICY_MAX_MORTGAGE  법·정책상 계산되는 최대 대출 추정액
    EXPECTED_MORTGAGE    실제 금융기관 심사를 고려한 예상값
                         (은행 견적이 없으면 정책 최대치와 같고, 그 사실을 밝힌다)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import rules, units
from apt_engine.trace import Calc, Evidence

MONTHS_PER_YEAR = 12

LTV = "LTV"
DSR = "DSR"
STRESS_DSR = "STRESS_DSR"
MORTGAGE_CAP = "MORTGAGE_CAP"
DTI = "DTI"
RULE_TYPES = (LTV, DSR, STRESS_DSR, MORTGAGE_CAP, DTI)

REPAYMENT_TYPES = ("원리금균등", "원금균등", "만기일시")

# DSR 한도는 업권마다 다르다(은행권 40% / 비은행권 50%). 코드가 아니라 규칙이
# 정하는 값이지만, 어느 업권으로 물어볼지는 호출부가 정해야 한다.
LENDER_TYPES = ("은행", "비은행")
DEFAULT_LENDER = "은행"

# 수도권. 스트레스 DSR 가산금리가 여기서 달라진다.
METRO_SIDO = ("서울", "경기", "인천")


def is_metro(region: str | None) -> bool:
    return region in METRO_SIDO

DISCLAIMER = ("정책 기준 추정치이며 실제 금융기관 심사 결과와 다를 수 있습니다 "
              "(은행별 가산금리·내부등급·담보평가에 따라 달라집니다)")


def home_status_of(current_home_count: int) -> str:
    """규칙 조회용 주택 보유 상태."""
    if current_home_count <= 0:
        return "무주택"
    return "1주택" if current_home_count == 1 else "다주택"


# ── 상환 산식 ─────────────────────────────────────────────────────────

def annuity_principal(monthly_payment: float, annual_rate: float, years: int) -> int:
    """원리금균등: 월 상환액으로 감당 가능한 원금.  P = M × (1 − (1+i)^−N) / i"""
    if monthly_payment <= 0 or years <= 0:
        return 0
    n = years * MONTHS_PER_YEAR
    i = annual_rate / MONTHS_PER_YEAR
    if i <= 0:
        return int(units.won_round(monthly_payment * n))
    return int(units.won_round(monthly_payment * (1 - (1 + i) ** -n) / i))


def annuity_payment(principal: int, annual_rate: float, years: int) -> int:
    """원리금균등: 원금에 대한 월 상환액."""
    if principal <= 0 or years <= 0:
        return 0
    n = years * MONTHS_PER_YEAR
    i = annual_rate / MONTHS_PER_YEAR
    if i <= 0:
        return int(units.won_round(principal / n))
    return int(units.won_round(principal * i / (1 - (1 + i) ** -n)))


def equal_principal_capacity(annual_cap: float, annual_rate: float, years: int) -> int:
    """원금균등: 연간 상환여력으로 감당 가능한 원금.

    원금균등은 **첫 해 상환액이 가장 크다** (원금 P/N + 이자 P×r).
    DSR 은 그 최대치로 봐야 안전하다.  P × (1/years + rate) ≤ 여력
    """
    if annual_cap <= 0 or years <= 0:
        return 0
    denom = 1.0 / years + annual_rate
    return 0 if denom <= 0 else int(units.won_round(annual_cap / denom))


# ── 한도 하나 ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Limit:
    name: str
    amount: int | None                 # None = 확인 불가
    verification: str
    formula: str = ""
    note: str = ""

    @property
    def known(self) -> bool:
        return self.amount is not None

    @property
    def label(self) -> str:
        return units.fmt_eok(self.amount) if self.known else "확인 불가"


def find_policy(conn: sqlite3.Connection, rule_type: str, *, as_of: str | date,
                price: int, home_status: str, regulated_area: bool,
                region: str | None = None, first_home_buyer: bool = False,
                lender_type: str = DEFAULT_LENDER, purpose: str = "주택구입",
                disposal_condition: bool = False,
                allow_unverified: bool = False) -> rules.Rule | None:
    """rule_type 하나에 대해 그 시점·조건에 맞는 정책 규칙."""
    day = rules.as_ymd(as_of)
    rows = conn.execute(
        f"SELECT * FROM loan_rule WHERE rule_type = ? AND {rules.effective_clause()}",
        (rule_type, day, day)).fetchall()

    context = {
        "house_count": {"무주택": 0, "1주택": 1, "다주택": 2}.get(home_status, 0),
        "regulated": regulated_area,
        "regulated_area": regulated_area,
        "home_status": home_status,
        "first_home_buyer": first_home_buyer,
        "lender_type": lender_type,
        "metro": is_metro(region),
        "purpose": purpose,
        # 규제지역 1주택자는 '기존 주택 처분 조건' 을 걸어야 대출이 나온다.
        # 조건을 걸지 않은 1주택자의 한도는 별개 규칙이라, 여기서 추정하지 않는다.
        "disposal_condition": disposal_condition,
    }
    found = rules.pick(rows, context, amount=price,
                       min_col="price_min", max_col="price_max")

    out: list[tuple[int, rules.Rule]] = []
    for rule in found:
        # NULL 컬럼은 '무관'이다. 값이 있으면 반드시 맞아야 한다.
        specific = 0
        for column, actual in (("region", region),
                               ("home_status", home_status)):
            want = rule.get(column)
            if want is None:
                continue
            if want != actual:
                break
            specific += 1
        else:
            for column, actual in (("regulated_area", regulated_area),
                                   ("first_home_buyer", first_home_buyer)):
                want = rule.get(column)
                if want is None:
                    continue
                if bool(want) != bool(actual):
                    break
                specific += 1
            else:
                if rule.verified or allow_unverified:
                    out.append((specific + rule.specificity, rule))
    if not out:
        return None
    out.sort(key=lambda pair: -pair[0])
    return out[0][1]


def calculate_ltv_limit(conn: sqlite3.Connection, *, price: int, as_of: str | date,
                        home_status: str, regulated_area: bool,
                        region: str | None = None, first_home_buyer: bool = False,
                        lender_type: str = DEFAULT_LENDER, purpose: str = "주택구입",
                        disposal_condition: bool = False,
                        allow_unverified: bool = False) -> tuple[Limit, Evidence | None]:
    """담보만 보는 이론상 한도. 이것만으로 대출가능액이라 부르지 않는다."""
    rule = find_policy(conn, LTV, as_of=as_of, price=price, home_status=home_status,
                       regulated_area=regulated_area, region=region,
                       first_home_buyer=first_home_buyer, lender_type=lender_type,
                       purpose=purpose, disposal_condition=disposal_condition,
                       allow_unverified=allow_unverified)
    if rule is None:
        return Limit("LTV 한도", None, rules.UNKNOWN, "규칙 미입력",
                     f"{home_status} · {'규제지역' if regulated_area else '비규제지역'} "
                     f"LTV 규칙이 없습니다. `cli rule template loan` 으로 넣으세요"), None
    ratio = rule.get("value")
    if ratio is None:
        return Limit("LTV 한도", None, rules.UNKNOWN, "규칙에 value 없음"), None
    ratio = float(ratio)
    if ratio > 1:            # 70 처럼 퍼센트로 적어도 받아준다
        ratio /= 100.0
    amount = int(units.won_round(price * ratio))
    return Limit("LTV 한도", amount, rule.verification,
                 f"{units.fmt_eok(price)} × {units.fmt_pct(ratio)}",
                 str(rule.get("note") or "")), rule.evidence


def calculate_dsr_limit(conn: sqlite3.Connection, *, price: int, as_of: str | date,
                        home_status: str, regulated_area: bool,
                        annual_income: int | None,
                        existing_annual_payment: int = 0,
                        interest_rate: float | None = None,
                        mortgage_term_years: int = 30,
                        repayment_type: str = "원리금균등",
                        region: str | None = None, first_home_buyer: bool = False,
                        lender_type: str = DEFAULT_LENDER, purpose: str = "주택구입",
                        disposal_condition: bool = False,
                        allow_unverified: bool = False) -> tuple[Limit, Evidence | None]:
    """소득으로 감당 가능한 원리금에서 역산한 한도.

    스트레스 DSR 은 **한도 계산에만** 가산금리를 쓴다. 실제 이자비용은 원래 금리로
    계산해야 한다 — 둘을 섞으면 이자비용이 부풀려진다.
    """
    rule = find_policy(conn, DSR, as_of=as_of, price=price, home_status=home_status,
                       regulated_area=regulated_area, region=region,
                       first_home_buyer=first_home_buyer, lender_type=lender_type,
                       purpose=purpose, disposal_condition=disposal_condition,
                       allow_unverified=allow_unverified)
    if rule is None:
        return Limit("DSR 한도", None, rules.UNKNOWN, "규칙 미입력",
                     "DSR 규칙이 없습니다. 소득 기준 한도를 계산할 수 없습니다"), None
    if not annual_income:
        return Limit("DSR 한도", None, rules.UNKNOWN, "연소득 미입력",
                     "연소득을 넣어야 실제 대출가능액이 나옵니다. "
                     "LTV 한도만으로는 대출가능액이 아닙니다"), None

    ratio = rule.get("value")
    if ratio is None:
        return Limit("DSR 한도", None, rules.UNKNOWN, "규칙에 value 없음"), None
    ratio = float(ratio)
    if ratio > 1:
        ratio /= 100.0

    if interest_rate is None:
        return Limit("DSR 한도", None, rules.UNKNOWN, "금리 미입력",
                     "예상 대출금리를 넣어야 DSR 한도를 계산할 수 있습니다"), None

    # 스트레스 가산금리도 규칙에서 온다. 없으면 가산 없이 계산하고 그 사실을 남긴다.
    stress = find_policy(conn, STRESS_DSR, as_of=as_of, price=price,
                         home_status=home_status, regulated_area=regulated_area,
                         region=region, first_home_buyer=first_home_buyer,
                         lender_type=lender_type, purpose=purpose,
                         disposal_condition=disposal_condition,
                         allow_unverified=allow_unverified)
    stress_bp = float(stress.get("value") or 0) if stress is not None else 0.0
    applied_rate = interest_rate + stress_bp / 10000.0
    stress_note = (f"스트레스 가산 {stress_bp:g}bp 적용" if stress is not None
                   else "스트레스 DSR 규칙 미입력 — 가산 없이 계산했습니다(한도가 크게 나옵니다)")

    annual_cap = annual_income * ratio - existing_annual_payment
    if annual_cap <= 0:
        return Limit("DSR 한도", 0, rule.verification,
                     f"연소득 {units.fmt_eok(annual_income)} × {units.fmt_pct(ratio)} "
                     f"− 기존 원리금 {units.fmt_won(existing_annual_payment)} ≤ 0",
                     "기존 대출만으로 DSR 여력이 없습니다"), rule.evidence

    if repayment_type == "원리금균등":
        amount = annuity_principal(annual_cap / MONTHS_PER_YEAR, applied_rate,
                                   mortgage_term_years)
    elif repayment_type == "원금균등":
        amount = equal_principal_capacity(annual_cap, applied_rate, mortgage_term_years)
    else:
        return Limit("DSR 한도", None, rules.UNKNOWN, f"{repayment_type} 산정방식 미입력",
                     "만기일시상환의 DSR 원금 산정방식은 감독규정 사항입니다. "
                     "규칙으로 넣기 전까지 계산하지 않습니다"), rule.evidence

    return Limit(
        "DSR 한도", amount, rule.verification,
        f"(연소득 {units.fmt_eok(annual_income)} × {units.fmt_pct(ratio)} "
        f"− 기존 원리금 {units.fmt_won(existing_annual_payment)}) 로 "
        f"{units.fmt_pct(applied_rate, digits=2)} · {mortgage_term_years}년 "
        f"{repayment_type} 역산",
        stress_note), rule.evidence


def calculate_absolute_mortgage_cap(
        conn: sqlite3.Connection, *, price: int, as_of: str | date,
        home_status: str, regulated_area: bool, region: str | None = None,
        first_home_buyer: bool = False, lender_type: str = DEFAULT_LENDER,
        purpose: str = "주택구입", disposal_condition: bool = False,
        allow_unverified: bool = False) -> tuple[Limit, Evidence | None]:
    """대출 총액 자체에 걸리는 상한(있을 때만)."""
    rule = find_policy(conn, MORTGAGE_CAP, as_of=as_of, price=price,
                       home_status=home_status, regulated_area=regulated_area,
                       region=region, first_home_buyer=first_home_buyer,
                       lender_type=lender_type, purpose=purpose,
                       disposal_condition=disposal_condition,
                       allow_unverified=allow_unverified)
    if rule is None:
        # 상한 규칙이 '없다'는 것과 '상한이 없다'는 것은 다르다. 후자로 단정하지 않는다.
        return Limit("절대 상한", None, rules.UNKNOWN, "규칙 미입력",
                     "대출 총액 상한 규칙이 없습니다. 상한이 없다고 단정하지 않습니다"), None
    value = rule.get("value")
    if value is None:
        return Limit("절대 상한", None, rules.UNKNOWN, "규칙에 value 없음"), None
    return Limit("절대 상한", int(value), rule.verification,
                 f"정책 상한 {units.fmt_eok(int(value))}",
                 str(rule.get("note") or "")), rule.evidence


@dataclass(frozen=True)
class Mortgage:
    policy_max: int | None
    expected: int | None
    binding: str | None
    limits: list[Limit] = field(default_factory=list)
    verification: str = rules.UNKNOWN
    unknown: list[str] = field(default_factory=list)
    residence_required: bool = False
    calc: Calc | None = None

    @property
    def known(self) -> bool:
        return self.policy_max is not None

    @property
    def label(self) -> str:
        if not self.known:
            return "확인 불가 — " + ("; ".join(self.unknown) or "규칙 미입력")
        head = units.fmt_eok(self.policy_max)
        if self.unknown:
            head += f" 이하 (확인 불가 한도 {len(self.unknown)}개 — 더 작을 수 있습니다)"
        return head


def calculate_final_mortgage_limit(
        conn: sqlite3.Connection, *, price: int, as_of: str | date,
        current_home_count: int = 0, regulated_area: bool = False,
        region: str | None = None, first_home_buyer: bool = False,
        annual_income: int | None = None, existing_annual_payment: int = 0,
        interest_rate: float | None = None, mortgage_term_years: int = 30,
        repayment_type: str = "원리금균등",
        requested: int | None = None, bank_quote: int | None = None,
        lender_type: str = DEFAULT_LENDER, purpose: str = "주택구입",
        disposal_condition: bool = False,
        allow_unverified: bool = False) -> Mortgage:
    """최종 대출 한도. min(LTV, DSR, 절대상한, 요청액)."""
    price = units.as_won(price)
    day = rules.as_ymd(as_of)
    home_status = home_status_of(current_home_count)
    common = dict(price=price, as_of=day, home_status=home_status,
                  regulated_area=regulated_area, region=region,
                  first_home_buyer=first_home_buyer, lender_type=lender_type,
                  purpose=purpose, disposal_condition=disposal_condition,
                  allow_unverified=allow_unverified)

    evidence: list[Evidence] = []
    limits: list[Limit] = []
    for limit, ev in (calculate_ltv_limit(conn, **common),
                      calculate_dsr_limit(
                          conn, annual_income=annual_income,
                          existing_annual_payment=existing_annual_payment,
                          interest_rate=interest_rate,
                          mortgage_term_years=mortgage_term_years,
                          repayment_type=repayment_type, **common),
                      calculate_absolute_mortgage_cap(conn, **common)):
        limits.append(limit)
        if ev:
            evidence.append(ev)

    if requested is not None:
        limits.append(Limit("요청액", units.as_won(requested), rules.VERIFIED,
                            "사용자가 받겠다고 한 금액"))

    usable = {l.name: l.amount for l in limits if l.known}
    unknown = [l.name for l in limits if not l.known]

    # 담보가액은 **정책이 아니라 산술적 상한**이다. LTV 는 정의상 100%를 넘을 수
    # 없으므로 집값보다 많이 빌릴 수는 없다. 이걸 두지 않으면 LTV 규칙이 없고
    # 소득만 큰 경우 DSR 한도만 남아 "집값보다 큰 대출"이 나오고 실투자금이 음수가 된다.
    #
    # 다만 이건 **좁히기만 한다.** 정책 한도를 하나도 못 구했으면 여전히
    # '확인 불가' 다 — 규칙이 없다고 "집값만큼 빌릴 수 있다"고 말하지 않는다.
    collateral = Limit("담보가액(LTV 100%)", price, rules.VERIFIED,
                       f"집값 {units.fmt_eok(price)}",
                       "정책이 아니라 산술적 상한입니다. 실제 LTV 는 이보다 낮습니다")
    if usable:
        limits.append(collateral)
        usable[collateral.name] = price

    # LTV 를 못 구했으면 대출 가능액 자체가 '확인 불가' 다.
    #
    # 총액 상한(6억)은 '이보다 많이는 못 빌린다' 일 뿐, '집값만큼 빌릴 수 있다'
    # 를 뜻하지 않는다. 그런데 상한만 있어도 usable 이 비지 않아서 담보가액이
    # 후보에 들어가고 min(상한, 집값) = 집값이 됐다. 실측으로 평택 4.32억
    # 아파트의 실투자금이 800만원(98% 차입)으로 나왔다.
    #
    # LTV 는 담보대출의 근본 제약이라 이것만은 대체할 수 없다. 모르면 모른다.
    ltv_known = any(l.name == "LTV 한도" and l.known for l in limits)
    if not ltv_known:
        unknown.append("LTV 한도가 없어 대출 가능액을 계산하지 못했습니다 — "
                       "0원으로도, 집값만큼으로도 세지 않았습니다")
        policy_max = None
        binding = None
    else:
        policy_max = min(usable.values()) if usable else None
        binding = min(usable, key=usable.get) if usable else None

    verification = rules.weakest_verification(
        *([l.verification for l in limits if l.known] or [rules.UNKNOWN]))
    if unknown:
        # 한도 하나를 못 구했으면 최종값은 '이하'다. 확정으로 말하지 않는다.
        verification = rules.weakest_verification(verification, rules.ESTIMATED)

    if bank_quote is not None:
        expected = units.as_won(bank_quote)
        expected_note = "금융기관 견적 반영"
    elif policy_max is None:
        expected = None
        expected_note = "정책 한도를 계산하지 못해 예상 대출액도 없습니다"
    else:
        expected = policy_max
        expected_note = DISCLAIMER

    intermediates = {
        "한도별": {l.name: l.label for l in limits},
        "계산식": {l.name: l.formula for l in limits if l.formula},
        "비고": {l.name: l.note for l in limits if l.note},
        "결정 요인": binding or "확인 불가",
        "POLICY_MAX_MORTGAGE": (units.fmt_eok(policy_max) if policy_max is not None
                                else "확인 불가"),
        "EXPECTED_MORTGAGE": (units.fmt_eok(expected) if expected is not None
                              else "확인 불가"),
        "예상액 근거": expected_note,
        "조건": {"주택보유": home_status, "규제지역": regulated_area,
                "지역": (region or "전국") + (" · 수도권" if is_metro(region) else ""),
                "업권": lender_type, "자금용도": purpose,
                "생애최초": first_home_buyer,
                "연소득": units.fmt_eok(annual_income) if annual_income else "미입력",
                "금리": (units.fmt_pct(interest_rate, digits=2)
                       if interest_rate is not None else "미입력"),
                "기간": f"{mortgage_term_years}년", "상환방식": repayment_type},
        "신뢰도": verification,
    }
    if unknown:
        intermediates["확인 불가 한도"] = unknown
        intermediates["주의"] = (
            f"{', '.join(unknown)} 을(를) 구하지 못했습니다. 모르는 한도를 무한대로 두지 "
            f"않았으므로 실제 대출가능액은 이 값보다 **작을 수 있습니다**.")

    calc = Calc(
        value=policy_max, unit="원",
        formula="POLICY_MAX_MORTGAGE = min(LTV 한도, DSR 한도, 절대 상한, 요청액)",
        inputs={"집값": units.fmt_eok(price), "기준일": day,
                "보유주택수": current_home_count,
                "규제지역": regulated_area},
        intermediates=intermediates,
        evidence=tuple(evidence),
        grade="ESTIMATED",
    )
    return Mortgage(policy_max, expected, binding, limits, verification, unknown,
                    False, calc)
