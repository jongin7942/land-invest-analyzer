"""보수 · 기준 · 낙관 3구간과 민감도 (요구사항 18).

추가분담금을 하나의 숫자로 말하지 않는다. 재건축 분담금은 공사비와 분양가에
정면으로 노출돼 있어서, 가정이 10% 움직이면 분담금은 그보다 크게 움직인다.
그래서 항상 **구간**으로 답하고, 무엇이 그 구간을 만들었는지 함께 말한다.

아래 배율은 데이터가 아니라 **가정**이다. 어디서 관측한 값이 아니므로
그렇게 표시하고, CLI 에서 사용자가 바꿀 수 있게 열어둔다. 이 배율을
"과거 통계상 이렇습니다"라고 말하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from apt_engine import units
from apt_engine.redev.feasibility import (Assumptions, Feasibility, MissingAssumption,
                                          compute)
from apt_engine.trace import Calc, Evidence

KEYS = ("보수", "기준", "낙관")

# 기준 가정에 곱하는 배율. **관측치가 아니라 가정이다.**
#   공사비는 올라가는 쪽이 위험이고, 분양가는 내려가는 쪽이 위험이다.
#   용적률은 보수 시나리오에서만 낮춘다 — 고시된 값보다 더 받는 가정은 하지 않는다.
ADJUST = {
    "보수": {"cost": 1.15, "price": 0.90, "far": 0.90},
    "기준": {"cost": 1.00, "price": 1.00, "far": 1.00},
    "낙관": {"cost": 0.95, "price": 1.10, "far": 1.00},
}

ADJUST_NOTE = ("보수/낙관 배율은 관측된 통계가 아니라 감도를 보기 위한 가정입니다 "
               "(보수: 공사비 +15%·분양가 −10%·용적률 −10%, 낙관: 공사비 −5%·분양가 +10%). "
               "band(adjust=...) 로 배율을 바꿀 수 있습니다")


def variant(base: Assumptions, key: str,
            adjust: dict[str, dict[str, float]] | None = None) -> Assumptions:
    factors = (adjust or ADJUST)[key]
    return replace(
        base,
        far=round(base.far * factors.get("far", 1.0), 2),
        cost_per_py=int(round(base.cost_per_py * factors.get("cost", 1.0))),
        new_price_per_m2=int(round(base.new_price_per_m2 * factors.get("price", 1.0))),
    )


@dataclass(frozen=True)
class Band:
    """3구간 결과."""
    results: dict[str, Feasibility]
    failed: dict[str, str]              # 계산하지 못한 시나리오와 사유
    calc: Calc | None = None

    @property
    def charges(self) -> dict[str, int]:
        return {k: f.extra_charge for k, f in self.results.items()}

    @property
    def span(self) -> tuple[int, int] | None:
        if not self.results:
            return None
        values = list(self.charges.values())
        return min(values), max(values)

    @property
    def label(self) -> str:
        if not self.results:
            return "확인 불가 — " + "; ".join(self.failed.values())
        lo, hi = self.span
        parts = [f"{k} {units.fmt_eok(self.charges[k])}"
                 for k in KEYS if k in self.results]
        return (f"추가분담금 {units.fmt_eok(lo)} ~ {units.fmt_eok(hi)}  "
                f"({' / '.join(parts)})")


def band(*, land_area_m2: float, base: Assumptions,
         adjust: dict[str, dict[str, float]] | None = None,
         evidence: Iterable[Evidence] = ()) -> Band:
    """보수·기준·낙관 세 판. 하나가 실패해도 나머지는 계산한다."""
    results: dict[str, Feasibility] = {}
    failed: dict[str, str] = {}
    ev = tuple(evidence)

    for key in KEYS:
        try:
            results[key] = compute(land_area_m2=land_area_m2,
                                   a=variant(base, key, adjust), evidence=ev)
        except MissingAssumption as e:
            failed[key] = str(e)

    if not results:
        return Band(results, failed, None)

    # 분담금이 가장 큰 시나리오가 투자자에게 가장 나쁜 경우다.
    worst = max(results, key=lambda k: results[k].extra_charge)
    calc = Calc(
        value=[results[k].extra_charge for k in KEYS if k in results],
        unit="원",
        formula="추가분담금 = 조합원분양가 − 권리가액, 가정 3벌(보수/기준/낙관)",
        inputs={"대지면적": f"{land_area_m2:,.0f}㎡",
                "기준 용적률": f"{base.far:g}% ({base.far_kind})",
                "기준 평당공사비": f"{base.cost_per_py:,}원 ({base.cost_base_year}년)",
                "기준 일반분양가": f"{base.new_price_per_m2:,}원/㎡"},
        intermediates={
            "시나리오별": {k: {"용적률": f"{variant(base, k, adjust).far:g}%",
                          "평당공사비": f"{variant(base, k, adjust).cost_per_py:,}원",
                          "일반분양가": f"{variant(base, k, adjust).new_price_per_m2:,}원/㎡",
                          "비례율": f"{results[k].proportion_rate:.1%}",
                          "추가분담금": units.fmt_eok(results[k].extra_charge)}
                      for k in KEYS if k in results},
            "가장 나쁜 경우": f"{worst} 시나리오 — 추가분담금 "
                        f"{units.fmt_eok(results[worst].extra_charge)}",
            "배율 성격": ADJUST_NOTE,
            "계산 실패": failed or "없음",
        },
        evidence=ev,
        grade="SCENARIO",
    )
    return Band(results, failed, calc)


# ── 민감도 ────────────────────────────────────────────────────────────
# 한 번에 하나씩만 흔든다. 두 개를 같이 흔들면 어느 쪽이 원인인지 알 수 없다.
SENSITIVITY_STEPS = (-0.20, -0.10, 0.0, 0.10, 0.20)

FACTOR_FIELDS = {
    "공사비": "cost_per_py",
    "일반분양가": "new_price_per_m2",
    "용적률": "far",
}


@dataclass(frozen=True)
class Sensitivity:
    factor: str
    rows: list[tuple[float, int | None, str | None]]   # (변동률, 추가분담금, 실패사유)

    @property
    def swing(self) -> int | None:
        got = [v for _, v, _ in self.rows if v is not None]
        return None if len(got) < 2 else max(got) - min(got)

    @property
    def label(self) -> str:
        if self.swing is None:
            return f"{self.factor}: 확인 불가"
        return (f"{self.factor} ±20% → 추가분담금 최대 "
                f"{units.fmt_eok(self.swing)} 차이")


def sensitivity(*, land_area_m2: float, base: Assumptions, factor: str,
                steps: Iterable[float] = SENSITIVITY_STEPS) -> Sensitivity:
    field = FACTOR_FIELDS.get(factor)
    if field is None:
        raise ValueError(f"민감도 항목은 {', '.join(FACTOR_FIELDS)} 중 하나입니다: {factor!r}")

    rows: list[tuple[float, int | None, str | None]] = []
    for step in steps:
        value = getattr(base, field) * (1 + step)
        shifted = replace(base, **{field: round(value, 2) if field == "far"
                                   else int(round(value))})
        try:
            rows.append((step, compute(land_area_m2=land_area_m2, a=shifted).extra_charge,
                         None))
        except MissingAssumption as e:
            rows.append((step, None, str(e)))
    return Sensitivity(factor, rows)


def sensitivity_calc(*, land_area_m2: float, base: Assumptions) -> Calc:
    """세 항목 민감도를 한 번에. 어느 가정이 결과를 가장 크게 흔드는지 보여준다."""
    results = {f: sensitivity(land_area_m2=land_area_m2, base=base, factor=f)
               for f in FACTOR_FIELDS}
    ranked = sorted((r for r in results.values() if r.swing is not None),
                    key=lambda r: -r.swing)
    return Calc(
        value={f: r.swing for f, r in results.items()},
        unit="원",
        formula="가정을 하나씩 ±20% 흔들었을 때 추가분담금의 변동폭",
        inputs={"대지면적": f"{land_area_m2:,.0f}㎡"},
        intermediates={
            "항목별": {f: {f"{s:+.0%}": (units.fmt_eok(v) if v is not None else "확인 불가")
                        for s, v, _ in r.rows}
                    for f, r in results.items()},
            "변동폭": {f: (units.fmt_eok(r.swing) if r.swing is not None else "확인 불가")
                    for f, r in results.items()},
            "가장 민감한 항목": ranked[0].factor if ranked else "확인 불가",
            "해석": ("변동폭이 큰 항목일수록 그 가정이 틀렸을 때 손실이 큽니다. "
                   "그 항목부터 실제 자료로 확인하세요"),
        },
        grade="SCENARIO",
    )
