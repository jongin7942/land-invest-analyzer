"""사업성 한 판 — 비례율 · 권리가액 · 추가분담금 (요구사항 18).

산식은 정비사업 실무의 표준이다.

    신축 연면적(용적률 기준) = 대지면적 × 용적률
    신축 세대수             = 신축 연면적 ÷ 세대당 분양면적
    일반분양 세대수         = 신축 세대수 − 조합원 세대수 − 임대 세대수
    총수입                 = (일반분양 + 조합원분양 + 임대매각) 수입
    총사업비               = 공사비 + 기타사업비
    비례율                 = (총수입 − 총사업비) ÷ 종전자산 감정평가 총액
    권리가액               = 조합원 종전자산 × 비례율
    추가분담금             = 조합원분양가 − 권리가액

여기서 중요한 건 산식이 아니라 **가정이 전부 입력이라는 점**이다. 공사비·분양가·
용적률·감정평가액 중 하나라도 없으면 이 함수는 숫자를 만들지 않고 무엇이 없는지
알려준다. 재건축에서 "대충 3억쯤 분담금"이라는 숫자가 제일 위험하다.

결과 등급은 언제나 SCENARIO 다. 관리처분인가가 난 단지라도, 이건 우리가 가정으로
다시 계산한 값이지 조합이 통보한 분담금이 아니다.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from apt_engine import units
from apt_engine.trace import Calc, Evidence

# 필수 가정. 하나라도 없으면 계산하지 않는다.
REQUIRED = ("far", "cost_per_py", "cost_base_year", "new_price_per_m2",
            "avg_new_unit_area_m2", "construction_area_factor", "other_cost_rate",
            "member_discount", "prior_asset_per_member", "member_count")


# 아래 두 값은 **관측치가 아니라 구조적 가정**이다. 단지마다 다르고, 정확히
# 알려면 정비계획 도면이 필요하다. 그래서 결과에는 항상 "가정"으로 표시되고
# 민감도 분석의 대상이 된다. CLI 에서 바꿀 수 있다.
SUPPLY_AREA_RATIO = 1.35        # 공급(분양)면적 ÷ 전용면적
CONSTRUCTION_AREA_RATIO = 1.40  # 공사연면적 ÷ 용적률 산입 연면적 (지하주차장 등)

ASSUMPTION_NOTE = (
    "세대당 분양면적·공사연면적 비율·조합원 할인율은 관측치가 아니라 구조적 가정입니다. "
    "단지마다 다르므로 정비계획 자료가 있으면 --new-unit-area 등으로 바꾸세요")


class MissingAssumption(ValueError):
    """가정이 빠졌다. 기본값으로 채우지 않는다."""


@dataclass(frozen=True)
class Assumptions:
    """사업성 계산에 필요한 가정 전부. 전국 어느 단지든 이 값들만 주면 계산된다."""
    far: float                          # 사업 용적률 (%)
    far_kind: str                       # 그 용적률의 출처 종류 (redev.far.KINDS)
    cost_per_py: int                    # 평당 공사비 (원/평, 공사연면적 기준)
    cost_base_year: int                 # 공사비 기준연도. 표시할 때 반드시 함께 쓴다
    new_price_per_m2: int               # 일반분양가 (원/㎡, 분양면적 기준)
    avg_new_unit_area_m2: float         # 신축 세대당 분양면적 (㎡)
    construction_area_factor: float     # 공사연면적 ÷ 용적률연면적 (지하주차장 등)
    other_cost_rate: float              # 기타사업비 ÷ 공사비
    member_discount: float              # 조합원분양가 ÷ 일반분양가
    prior_asset_per_member: int         # 조합원 1인 종전자산 감정평가액 (원)
    member_count: int                   # 조합원 세대수
    rental_ratio: float = 0.0           # 임대 세대 비율
    rental_price_per_m2: int | None = None   # 임대 매각단가. None = 수입 0으로 본다
    prior_asset_total: int | None = None     # 감정평가 총액이 확정됐으면 그걸 쓴다

    def check(self) -> list[str]:
        missing = [f for f in REQUIRED if getattr(self, f) in (None, 0)]
        return missing


@dataclass(frozen=True)
class Feasibility:
    new_units: int
    member_units: int
    rental_units: int
    general_units: int
    revenue: int
    construction_cost: int
    other_cost: int
    total_cost: int
    prior_asset_total: int
    proportion_rate: float              # 비례율
    right_value: int                    # 권리가액 (조합원 1인)
    member_price: int                   # 조합원분양가 (1세대)
    extra_charge: int                   # 추가분담금. 음수면 환급
    caveats: list[str] = field(default_factory=list)
    calc: Calc | None = None

    @property
    def profitable(self) -> bool:
        return self.proportion_rate >= 1.0

    @property
    def label(self) -> str:
        word = "환급" if self.extra_charge < 0 else "분담"
        return (f"비례율 {self.proportion_rate:.1%} · "
                f"추가{word} {units.fmt_eok(abs(self.extra_charge))}")


def compute(*, land_area_m2: float, a: Assumptions,
            evidence: tuple[Evidence, ...] = ()) -> Feasibility:
    """가정 한 벌로 사업성 한 판. 가정이 비면 MissingAssumption."""
    missing = a.check()
    if missing:
        raise MissingAssumption(
            "가정이 비어 있어 사업성을 계산하지 않았습니다: " + ", ".join(missing))
    if not land_area_m2 or land_area_m2 <= 0:
        raise MissingAssumption(
            "대지면적 미입력 — 사업성을 계산할 수 없습니다. "
            "건축물대장 총괄표제부의 대지면적을 `redev landarea` 로 넣으세요")

    caveats: list[str] = []

    # ── 규모 ──
    far_area = land_area_m2 * a.far / 100.0            # 용적률 산입 연면적
    new_units = int(far_area // a.avg_new_unit_area_m2)
    if new_units <= 0:
        raise MissingAssumption(
            f"신축 세대수가 0으로 계산됩니다(연면적 {far_area:,.0f}㎡ ÷ "
            f"세대당 {a.avg_new_unit_area_m2:g}㎡). 가정을 확인하세요")

    rental_units = int(round(new_units * a.rental_ratio))
    member_units = min(a.member_count, max(new_units - rental_units, 0))
    if member_units < a.member_count:
        caveats.append(
            f"신축 세대수({new_units})가 조합원({a.member_count})을 다 담지 못합니다 — "
            f"이 가정에서는 사업이 성립하지 않습니다")
    general_units = new_units - rental_units - member_units

    # ── 수입 ──
    unit_price = a.avg_new_unit_area_m2 * a.new_price_per_m2
    general_revenue = int(units.won_round(general_units * unit_price))
    member_price = int(units.won_round(unit_price * a.member_discount))
    member_revenue = member_price * member_units
    if a.rental_price_per_m2 is None:
        rental_revenue = 0
        if rental_units:
            caveats.append(
                f"임대 {rental_units}세대의 공공 매각수입을 0으로 두었습니다 — "
                f"실제 사업성은 이 계산보다 **좋아집니다**(보수적)")
    else:
        rental_revenue = int(units.won_round(
            rental_units * a.avg_new_unit_area_m2 * a.rental_price_per_m2))
    revenue = general_revenue + member_revenue + rental_revenue

    # ── 지출 ──
    construction_area = far_area * a.construction_area_factor
    construction_cost = int(units.won_round(
        units.to_pyeong(construction_area) * a.cost_per_py))
    other_cost = int(units.won_round(construction_cost * a.other_cost_rate))
    total_cost = construction_cost + other_cost

    # ── 비례율 ──
    prior_total = a.prior_asset_total or (a.prior_asset_per_member * a.member_count)
    if a.prior_asset_total is None:
        caveats.append(
            "종전자산 총액을 '조합원 1인 평가액 × 조합원수'로 근사했습니다 — "
            "실제 감정평가는 세대마다 다릅니다")
    proportion_rate = (revenue - total_cost) / prior_total
    right_value = int(units.won_round(a.prior_asset_per_member * proportion_rate))
    extra_charge = member_price - right_value

    if general_units <= 0:
        caveats.append(
            "일반분양 물량이 없습니다 — 사업비를 조합원이 전부 부담하는 구조입니다")

    calc = Calc(
        value=extra_charge, unit="원",
        formula="추가분담금 = 조합원분양가 − 권리가액(종전자산 × 비례율)",
        inputs={
            "대지면적": f"{land_area_m2:,.0f}㎡",
            "적용 용적률": f"{a.far:g}% ({a.far_kind})",
            "평당 공사비": f"{a.cost_per_py:,}원 ({a.cost_base_year}년 기준)",
            "일반분양가": f"{a.new_price_per_m2:,}원/㎡",
            "세대당 분양면적": f"{a.avg_new_unit_area_m2:g}㎡",
            "조합원수": a.member_count,
            "조합원 종전자산": units.fmt_eok(a.prior_asset_per_member),
        },
        intermediates={
            "신축 연면적(용적률)": f"{far_area:,.0f}㎡",
            "신축 세대수": new_units,
            "조합원/임대/일반": f"{member_units} / {rental_units} / {general_units}",
            "총수입": units.fmt_eok(revenue),
            "공사비": units.fmt_eok(construction_cost),
            "기타사업비": units.fmt_eok(other_cost),
            "총사업비": units.fmt_eok(total_cost),
            "종전자산 총액": units.fmt_eok(prior_total),
            "비례율": f"{proportion_rate:.1%}",
            "권리가액": units.fmt_eok(right_value),
            "조합원분양가": units.fmt_eok(member_price),
            "단서": caveats or "없음",
            "성격": ("가정에 기반한 시나리오입니다. 조합이 통보한 분담금이 아닙니다"),
        },
        evidence=evidence,
        grade="SCENARIO",
    )
    return Feasibility(
        new_units=new_units, member_units=member_units, rental_units=rental_units,
        general_units=general_units, revenue=revenue,
        construction_cost=construction_cost, other_cost=other_cost,
        total_cost=total_cost, prior_asset_total=prior_total,
        proportion_rate=proportion_rate, right_value=right_value,
        member_price=member_price, extra_charge=extra_charge,
        caveats=caveats, calc=calc)


def cost_reference(conn: sqlite3.Connection, *, region: str | None = None,
                   grade: str = "보통", base_year: int | None = None,
                   allow_unverified: bool = False) -> tuple[int, int, float | None,
                                                            Evidence] | None:
    """평당 공사비 참고치. (원/평, 기준연도, 기타사업비율, 근거). 없으면 None."""
    rows = conn.execute(
        "SELECT * FROM construction_cost_ref WHERE grade = ?", (grade,)).fetchall()
    rows = [r for r in rows if r["region"] is None or r["region"] == region]
    rows = [r for r in rows if r["last_verified"] or allow_unverified]
    if base_year is not None:
        rows = [r for r in rows if int(r["base_year"]) <= base_year]
    if not rows:
        return None
    # 지역이 적힌 것 먼저, 그다음 최신 연도.
    row = sorted(rows, key=lambda r: (r["region"] is None, -int(r["base_year"])))[0]
    ev = Evidence(
        source=row["source_name"] or "평당 공사비 참고치 (수기 입력)",
        url=row["source_url"], effective_date=str(row["base_year"]),
        note=f"{row['base_year']}년 기준 · {row['grade']}"
             + ("" if row["last_verified"] else " · 미검증"))
    return int(row["cost_per_py"]), int(row["base_year"]), row["other_cost_rate"], ev
