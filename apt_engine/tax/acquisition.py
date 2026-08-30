"""취득세 + 지방교육세 + 농어촌특별세 (요구사항 25).

세 가지를 따로 계산해 합친다. 실투자금(요구사항 27)의 첫 번째 항목이다.

세율은 이 파일 어디에도 없다. `tax_rule` 테이블에서 매수 시점(`as_of`) 기준으로
찾아 쓰고, 사람이 확인하지 않은 규칙이면 계산을 거부한다.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from apt_engine import rules, units
from apt_engine.tax import rules as tax_rules
from apt_engine.trace import Calc

# 취득 시 함께 매기는 부가세목. 없으면 0으로 두되 "없음"과 "미입력"을 구분한다.
SURTAXES = (tax_rules.LOCAL_EDUCATION, tax_rules.RURAL_SPECIAL)


def compute(conn: sqlite3.Connection, *, price: int, as_of: str | date,
            house_count: int, regulated: bool, exclusive_area_m2: float | None = None,
            allow_unverified: bool = False) -> Calc:
    """취득 관련 세금 총액.

    price              취득가액(원)
    as_of              **매수 시점**. 이 날짜의 세법으로 계산한다
    house_count        취득 후 주택 수(본인 세대 기준)
    regulated          조정대상지역 등 규제지역 여부
    exclusive_area_m2  85㎡ 초과 여부가 농특세를 가른다
    """
    price = units.as_won(price)
    day = rules.as_ymd(as_of)
    context = {
        "house_count": house_count,
        "regulated": regulated,
        "exclusive_area": exclusive_area_m2,
    }

    main = tax_rules.pick_one(conn, tax_rules.ACQUISITION, as_of=day, base=price,
                              context=context, allow_unverified=allow_unverified)
    acq_amount, acq_formula = tax_rules.apply_rate(main, price)

    parts = {tax_rules.ACQUISITION: acq_amount}
    formulas = {tax_rules.ACQUISITION: acq_formula}
    evidence = [main.evidence]
    unverified = [] if main.verified else [tax_rules.ACQUISITION]

    for kind in SURTAXES:
        found = tax_rules.find(conn, kind, as_of=day, base=price, context=context)
        if not found:
            parts[kind] = None            # "0원"이 아니라 "미입력"
            formulas[kind] = "규칙 미입력 — 확인 불가"
            continue
        rule = found[0]
        if not rule.verified and not allow_unverified:
            rule.require_verified(kind)
        amount, formula = tax_rules.apply_rate(rule, price)
        parts[kind] = amount
        formulas[kind] = formula
        evidence.append(rule.evidence)
        if not rule.verified:
            unverified.append(kind)

    known = [v for v in parts.values() if v is not None]
    total = sum(known)
    missing = [k for k, v in parts.items() if v is None]

    intermediates = {
        "세목별": {k: (units.fmt_won(v) if v is not None else "확인 불가")
                  for k, v in parts.items()},
        "계산식": formulas,
        "기준일": day,
        "조건": {"주택수": house_count, "규제지역": regulated,
                "전용면적": exclusive_area_m2},
    }
    if missing:
        intermediates["주의"] = (
            f"{', '.join(missing)} 규칙이 입력되지 않아 합계에서 빠졌습니다. "
            f"실제 부담액은 이보다 큽니다.")
    if unverified:
        intermediates["미검증"] = (
            f"{', '.join(unverified)} 규칙이 사람 확인을 거치지 않았습니다.")

    return Calc(
        value=total, unit="원",
        formula="취득세 + 지방교육세 + 농어촌특별세",
        inputs={"취득가액": units.fmt_eok(price), "기준일": day},
        intermediates=intermediates,
        evidence=tuple(evidence),
        # 세법 해석은 개인 사정에 따라 달라진다. 확정값으로 표시하지 않는다.
        grade="ESTIMATED",
    )
