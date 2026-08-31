"""세법 규칙 조회 — 기억이 아니라 테이블에서 (요구사항 25·62-10).

세율을 코드에 적지 않는다. `tax_rule` 테이블에서 `as_of` 시점에 유효한 규칙을 찾고,
사람이 확인하지 않은 규칙(last_verified 비어 있음)으로는 계산을 거부한다.
"""
from __future__ import annotations

import ast

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


# rate_formula 에서 허용하는 것 — 숫자, 사칙연산, 괄호, 변수 `base` 뿐이다.
# 함수 호출·속성 접근·이름 참조는 전부 거부한다.
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def eval_rate_formula(expr: str, base: int) -> float:
    """'(base * 2 / 300000000 - 3) / 100' 같은 산식을 제한 평가해 세율을 낸다.

    지방세법 제11조 6억~9억 구간처럼 **구간 안에서 세율이 연속으로 변하는** 조문을
    표에 담을 수 없어서 필요하다. CSV 는 사람이 편집하는 파일이므로 eval() 은 쓰지 않는다.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise rules.RuleError(f"rate_formula 를 읽을 수 없습니다: {expr!r} ({e})") from e

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return float(node.value)
            raise rules.RuleError(f"rate_formula 에 숫자가 아닌 값: {node.value!r}")
        if isinstance(node, ast.Name):
            if node.id == "base":
                return float(base)
            raise rules.RuleError(
                f"rate_formula 에서 쓸 수 있는 변수는 'base' 뿐입니다: {node.id!r}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = walk(node.operand)
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise rules.RuleError(f"rate_formula 에서 0으로 나눕니다: {expr!r}")
            return left / right
        raise rules.RuleError(
            f"rate_formula 에 허용되지 않은 식이 있습니다: {ast.dump(node)[:80]}")

    rate = walk(tree)
    if not (0 <= rate <= 1):
        raise rules.RuleError(
            f"rate_formula 결과가 세율 범위(0~1)를 벗어났습니다: {rate} — 식: {expr!r}")
    return rate


def round_rate(rate: float, decimals: int | None) -> float:
    """세율을 조문이 정한 자리수로 반올림한다.

    지방세법 제11조제1항제8호 나목: "소수점 이하 다섯째 자리에서 반올림하여
    소수점 넷째 자리까지 계산한다". 은행식 반올림(round())은 5를 짝수로 보내므로
    쓰지 않는다 — 법령은 사사오입이다.
    """
    if decimals is None:
        return rate
    from decimal import Decimal, ROUND_HALF_UP
    quantum = Decimal(1).scaleb(-int(decimals))
    return float(Decimal(str(rate)).quantize(quantum, rounding=ROUND_HALF_UP))


def apply_rate(rule: rules.Rule, base: int) -> tuple[int, str]:
    """규칙 하나를 과세표준에 적용. (세액, 계산식 문자열)."""
    from apt_engine import units

    fixed = rule.get("fixed_amount")
    if fixed is not None:
        return int(fixed), f"정액 {units.fmt_won(int(fixed))}"

    rate = rule.get("rate")
    note = ""
    if rate is None:
        expr = rule.get("rate_formula")
        if not expr:
            raise rules.RuleError(
                f"규칙 '{rule.get('rule_key')}' 에 rate 도 rate_formula 도 "
                f"fixed_amount 도 없습니다")
        rate = eval_rate_formula(str(expr), base)
        note = f" [산식 {expr}]"

    decimals = rule.get("rate_decimals")
    if decimals is not None:
        rounded = round_rate(float(rate), int(decimals))
        if rounded != rate:
            note += f" [{decimals}자리 반올림 {rate:.8f}→{rounded}]"
        rate = rounded

    deduction = int(rule.get("progressive_deduction") or 0)
    amount = int(units.won_round(base * float(rate))) - deduction
    formula = f"{units.fmt_eok(base)} × {units.fmt_pct(float(rate), digits=2)}{note}"
    if deduction:
        formula += f" − 누진공제 {units.fmt_won(deduction)}"
    return max(amount, 0), formula
