"""Naked Apartment Value · 재건축 프리미엄 효율 (신규 지시서 §34).

§49-9 가 금지한 것:

> 재건축 / GTX 등 Narrative 에 고정점수 부여 금지.

"재건축 가능성 있음 → +10점" 은 두 가지가 틀렸다.

    ① 재건축 기대가 **이미 가격에 얼마나 들어갔는지**를 안 본다
    ② 그 기대가 **실현되면 얼마를 버는지**를 안 본다

같은 "재건축 호재" 라도 프리미엄이 1억 붙었는데 기대 순이익이 5억이면 좋고,
프리미엄이 4억 붙었는데 기대 순이익이 3억이면 사면 안 된다. 고정점수는 이
둘을 구분하지 못한다.

그래서 §34 가 제시한 순서로 간다.

    ① NakedApartmentValue          재건축을 빼고, 그냥 낡은 아파트로서의 값
    ② ImpliedReconstructionPremium = 현재가 − NakedApartmentValue
                                     시장이 재건축에 얹어 놓은 금액
    ③ ReconstructionPremiumEfficiency = 기대 순가치 ÷ 얹힌 프리미엄
                                        **시간가치까지 반영해서**

③이 1보다 크면 프리미엄이 싸고, 1보다 작으면 이미 비싸다.

①이 가장 어렵다. "재건축을 뺀 값" 은 관측되지 않는다. 그래서 **비교 단지에서
빌려 온다** — 같은 생활권·같은 면적·비슷한 연식인데 재건축 기대가 없는 단지의
㎡당 가격. 그런 단지를 못 찾으면 **추정하지 않는다.**
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine import units
from apt_engine.trace import Calc

# 비교 단지가 이만큼은 있어야 Naked Value 를 만든다.
MIN_PEERS = 3

# 연식 차이를 이 이상 벌어지면 비교로 쓰지 않는다(상품성이 달라진다).
MAX_YEAR_GAP = 8

NOTE = ("재건축에 고정점수를 주지 않습니다(§34·§49-9). "
        "얹힌 프리미엄과 기대 순가치를 나눠 봅니다")


@dataclass(frozen=True)
class Peer:
    complex_id: int
    price_per_m2: float
    approval_year: int | None
    has_redev_expectation: bool


@dataclass(frozen=True)
class NakedValue:
    value: int | None
    peers_used: int = 0
    basis: str = ""
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        return f"{units.fmt_eok(self.value)} ({self.basis})"


def naked_value(*, area_m2: float | None, peers: list[Peer],
                own_year: int | None) -> NakedValue:
    """재건축 기대를 뺀 값 (§34 ①).

    재건축 기대가 **없는** 비교 단지의 ㎡당 가격을 쓴다.
    비교 단지가 모자라면 **만들어내지 않는다.**
    """
    if not area_m2 or area_m2 <= 0:
        return NakedValue(None, 0, "", "전용면적을 몰라 계산할 수 없습니다")

    usable = [p for p in peers if not p.has_redev_expectation
              and p.price_per_m2 > 0]
    if own_year is not None:
        usable = [p for p in usable
                  if p.approval_year is None
                  or abs(p.approval_year - own_year) <= MAX_YEAR_GAP]

    if len(usable) < MIN_PEERS:
        return NakedValue(None, len(usable), "", (
            f"재건축 기대가 없는 비교 단지가 {len(usable)}개뿐입니다"
            f"(최소 {MIN_PEERS}개). 재건축을 뺀 값을 추정하지 않습니다"))

    import statistics
    per_m2 = statistics.median(p.price_per_m2 for p in usable)
    return NakedValue(int(units.won_round(per_m2 * area_m2)), len(usable),
                      f"재건축 기대 없는 비교단지 {len(usable)}개의 "
                      f"㎡당 중앙값 {per_m2:,.0f}원")


@dataclass(frozen=True)
class Premium:
    implied: int | None                 # 시장이 얹어 놓은 금액
    expected_net: int | None            # 기대 순가치 (현재가치 환산)
    efficiency: float | None            # 순가치 ÷ 프리미엄
    years: float | None = None
    discount_rate: float | None = None
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.efficiency is not None

    @property
    def verdict(self) -> str:
        if not self.known:
            return "확인 불가"
        if self.efficiency >= 1.5:
            return "프리미엄이 쌉니다 — 기대 순가치가 얹힌 값보다 큽니다"
        if self.efficiency >= 1.0:
            return "프리미엄이 적정합니다"
        return "프리미엄이 이미 비쌉니다 — 실현돼도 남는 게 없습니다"

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        return (f"얹힌 프리미엄 {units.fmt_eok(self.implied)} vs "
                f"기대 순가치(현재가치) {units.fmt_eok(self.expected_net)} "
                f"→ 효율 {self.efficiency:.2f}  {self.verdict}")


def premium_efficiency(*, current_price: int | None, naked: NakedValue,
                       expected_gross_value: int | None,
                       years_to_completion: float | None,
                       discount_rate: float | None) -> Premium:
    """§34 ②③ — 얹힌 프리미엄 대비 기대 순가치.

    **시간가치를 반영한다.** 20년 뒤 5억을 지금 5억으로 세면 모든 노후 단지가
    좋아 보인다. 할인율을 모르면 계산하지 않는다 — 0% 로 두는 것이 그 실수다.
    """
    notes: list[str] = []
    if current_price is None or current_price <= 0:
        return Premium(None, None, None, reason="현재가격이 없습니다")
    if not naked.known:
        return Premium(None, None, None, reason=(
            f"재건축을 뺀 값을 몰라 프리미엄을 분리할 수 없습니다 — "
            f"{naked.reason}"))

    implied = current_price - naked.value
    if implied <= 0:
        return Premium(implied, None, None, reason=(
            "현재가격이 재건축 없는 비교단지 수준 이하입니다 — "
            "시장이 재건축을 가격에 넣지 않았습니다"))

    if expected_gross_value is None:
        return Premium(implied, None, None, reason=(
            "재건축 기대 가치를 몰라 효율을 내지 않습니다"))
    if years_to_completion is None:
        return Premium(implied, None, None, reason=(
            "완공까지 걸리는 기간을 몰라 시간가치를 반영할 수 없습니다"))
    if discount_rate is None:
        return Premium(implied, None, None, reason=(
            "할인율을 몰라 현재가치로 환산하지 않습니다 — 0% 로 두면 "
            "20년 뒤 5억이 지금 5억이 되어 모든 노후 단지가 좋아 보입니다"))

    present = expected_gross_value / ((1 + discount_rate) ** years_to_completion)
    efficiency = present / implied
    notes.append(f"{years_to_completion:.0f}년 뒤 "
                 f"{units.fmt_eok(expected_gross_value)} 를 연 "
                 f"{discount_rate:.1%} 로 할인 → "
                 f"{units.fmt_eok(int(present))}")
    return Premium(implied, int(present), efficiency, years_to_completion,
                   discount_rate, notes=notes)


def to_calc(p: Premium, naked: NakedValue) -> Calc:
    if not p.known:
        return Calc(value=None, unit="배",
                    formula="재건축 프리미엄 효율을 계산하지 않았습니다",
                    intermediates={"사유": p.reason, "NakedValue": naked.label},
                    grade="SCENARIO")
    return Calc(
        value=p.efficiency, unit="배",
        formula=("기대 순가치(현재가치) ÷ (현재가 − NakedApartmentValue). "
                 + NOTE),
        intermediates={"NakedValue": naked.label,
                       "얹힌 프리미엄": p.implied,
                       "기대 순가치": p.expected_net,
                       "할인": p.notes},
        grade="SCENARIO")


# ── §34 전달경로 ─────────────────────────────────────────────────────
#
# > Catalyst → BuyerPool 변화 / Accessibility 변화 / Price Ladder 변화 /
# >            Effective Supply 변화  처럼 실제 전달경로만 평가한다.
#
# 호재가 가격을 올리는 길은 넷뿐이다. 어느 길로도 설명이 안 되면 그 호재는
# 이 단지에 영향이 없는 것이고, 그러면 점수를 주지 않는다.
TRANSMISSION_PATHS = ("BuyerPool", "Accessibility", "PriceLadder",
                      "EffectiveSupply")

PATH_QUESTION = {
    "BuyerPool": "이 호재로 이 단지를 살 수 있는 사람이 늘어나는가",
    "Accessibility": "이 호재로 이 단지에서 일자리·중심지 접근이 좋아지는가",
    "PriceLadder": "이 호재로 이 단지의 생활권 내 서열이 올라가는가",
    "EffectiveSupply": "이 호재로 경쟁 공급이 늘거나 줄어드는가",
}


def catalyst_paths(effects: dict[str, float | None]) -> tuple[float | None, dict]:
    """호재를 전달경로로 평가한다 (§34).

    `effects` 는 경로별 효과(0~1). **하나도 설명되지 않으면 None** 이다 —
    "재건축이니까 좋다" 같은 서사에는 점수가 없다.
    """
    known = {k: v for k, v in effects.items()
             if k in TRANSMISSION_PATHS and v is not None}
    detail: dict = {"질문": PATH_QUESTION}
    if not known:
        detail["사유"] = ("네 전달경로 중 어느 것으로도 설명되지 않았습니다. "
                        "서사에는 점수를 주지 않습니다(§34·§49-9)")
        return None, detail
    detail["설명된 경로"] = {k: f"{v:.2f}" for k, v in known.items()}
    detail["설명 안 된 경로"] = [k for k in TRANSMISSION_PATHS if k not in known]
    # 경로가 적게 설명될수록 덜 믿는다.
    coverage = len(known) / len(TRANSMISSION_PATHS)
    return sum(known.values()) / len(known) * coverage, detail
