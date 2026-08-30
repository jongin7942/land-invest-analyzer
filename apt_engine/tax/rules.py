"""세법 규칙 조회 — 기억이 아니라 테이블에서 (요구사항 25·62-10).

세율을 코드에 적지 않는다. `tax_rule` 테이블에서 `as_of` 시점에 유효한 규칙을 찾고,
사람이 확인하지 않은 규칙(last_verified 비어 있음)으로는 계산을 거부한다.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from apt_engine import rules

ACQUISITION = "취득세"
LOCAL_EDUCATION = "지방교육세"
RURAL_SPECIAL = "농어촌특별세"
PROPERTY = "재산세"
COMPREHENSIVE = "종합부동산세"
CAPITAL_GAINS = "양도소득세"
LOCAL_INCOME = "지방소득세"


def find(conn: sqlite3.Connection, tax_kind: str, *, as_of: str | date,
         base: int, context: dict | None = None) -> list[rules.Rule]:
    """과세표준 `base` 와 조건에 맞는 규칙을 구체적인 것부터."""
    day = rules.as_ymd(as_of)
    rows = conn.execute(
        f"SELECT * FROM tax_rule WHERE tax_kind = ? AND {rules.effective_clause()}",
        (tax_kind, day, day)).fetchall()
    return rules.pick(rows, context or {}, amount=base)


def pick_one(conn: sqlite3.Connection, tax_kind: str, *, as_of: str | date,
             base: int, context: dict | None = None,
             allow_unverified: bool = False) -> rules.Rule:
    """가장 적합한 규칙 하나. 없거나 미검증이면 예외."""
    found = find(conn, tax_kind, as_of=as_of, base=base, context=context)
    if not found:
        raise rules.NoRuleError(
            f"{rules.as_ymd(as_of)} 기준 {tax_kind} 규칙이 없습니다 "
            f"(과세표준 {base:,}원, 조건 {context or {}}). "
            f"`cli rule template tax` 로 서식을 받아 입력하세요.")
    rule = found[0]
    return rule if allow_unverified else rule.require_verified(tax_kind)


def apply_rate(rule: rules.Rule, base: int) -> tuple[int, str]:
    """규칙 하나를 과세표준에 적용. (세액, 계산식 문자열)."""
    from apt_engine import units

    fixed = rule.get("fixed_amount")
    if fixed is not None:
        return int(fixed), f"정액 {units.fmt_won(int(fixed))}"

    rate = rule.get("rate")
    if rate is None:
        raise rules.RuleError(
            f"규칙 '{rule.get('rule_key')}' 에 rate 도 fixed_amount 도 없습니다")

    deduction = int(rule.get("progressive_deduction") or 0)
    amount = int(units.won_round(base * float(rate))) - deduction
    formula = f"{units.fmt_eok(base)} × {units.fmt_pct(float(rate), digits=2)}"
    if deduction:
        formula += f" − 누진공제 {units.fmt_won(deduction)}"
    return max(amount, 0), formula
