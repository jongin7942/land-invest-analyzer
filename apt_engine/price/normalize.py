"""Normal Executable Price — 지금 실제로 살 수 있는 가격 (신규 지시서 §35).

§49-4 가 금지한 것:

> 최근 최고가 1건을 현재가격으로 사용하는 것 금지.

기존 `price/representative.py` 가 이미 중앙값 + 이상치 제외로 그걸 막고 있다.
이 모듈은 §35 가 추가로 요구한 세 가지를 얹는다.

    ① 30일 방향성      최근 한 달이 그 앞 두 달과 다른 방향이면 반영한다
    ② Type 정규화      같은 면적이라도 타입별 정상 격차가 있으면 보정한다
    ③ 급매 흡수        급매가 몇 건 섞였는지에 따라 '실제 살 수 있는 가격' 이 다르다

③이 미묘하다. 급매를 **전부 빼면** 호가에 가까운 값이 나오고, **전부 넣으면**
운 좋아야 살 수 있는 값이 나온다. 지시서의 표현이 "urgent-sale absorption" 인
이유다 — 시장이 급매를 얼마나 흡수하고 있는지를 보고, 그 비중만큼만 반영한다.

급매 비중이 높다 = 시장이 약하다 = 그 가격에 살 수 있다.
급매 비중이 낮다 = 급매는 예외다 = 그 가격에 못 산다.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from apt_engine import units
from apt_engine.trace import Calc

# 방향성을 볼 최근 구간
DIRECTION_DAYS = 30
DIRECTION_BASE_MONTHS = 3

# 방향성 반영 상한. 한 달치 표본으로 가격을 크게 흔들지 않는다.
MAX_DIRECTION_ADJUST = 0.03

# 급매가 이 비중을 넘으면 '시장이 흡수하고 있다' 로 본다.
URGENT_ABSORBED = 0.20

# 방향성을 낼 최소 표본
MIN_DIRECTION_SAMPLES = 3

NOTE = ("Normal Executable Price 는 최근 최고가가 아니라 정규화된 분포에서 "
        "나온 값입니다(§35·§49-4)")


@dataclass(frozen=True)
class Adjustment:
    name: str
    factor: float | None          # 곱할 값. None = 못 구함
    why: str

    @property
    def applied(self) -> bool:
        return self.factor is not None

    @property
    def label(self) -> str:
        if not self.applied:
            return f"{self.name}: 보정 안 함 — {self.why}"
        return f"{self.name}: ×{self.factor:.4f} ({self.why})"


@dataclass(frozen=True)
class Normalized:
    base_price: int
    price: int | None
    adjustments: list[Adjustment] = field(default_factory=list)
    sample_n: int = 0
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.price is not None

    @property
    def skipped(self) -> list[str]:
        return [a.name for a in self.adjustments if not a.applied]

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        tail = (f"  (보정 {len([a for a in self.adjustments if a.applied])}개"
                + (f", 못한 보정 {len(self.skipped)}개" if self.skipped else "")
                + ")")
        return f"{units.fmt_eok(self.price)}{tail}"

    @property
    def calc(self) -> Calc:
        return Calc(
            value=self.price, unit="원",
            formula="90일 정규화 중앙값 × 30일 방향성 × Type 보정 × 급매 흡수",
            intermediates={"기준가": self.base_price,
                           "보정": [a.label for a in self.adjustments],
                           "표본": self.sample_n},
            grade="CONFIRMED" if not self.skipped else "ESTIMATED",
            evidence=[])


def direction(recent: list[int], base: list[int]) -> Adjustment:
    """최근 30일이 그 앞 구간과 다른 방향인가 (§35).

    한 달 표본은 적다. 그래서 방향만 보고 **크기는 상한을 건다** —
    3건짜리 최근 표본으로 가격을 10% 올리면 그건 정규화가 아니라 노이즈다.
    """
    if len(recent) < MIN_DIRECTION_SAMPLES or len(base) < MIN_DIRECTION_SAMPLES:
        return Adjustment("30일 방향성", None,
                          f"표본 부족 (최근 {len(recent)}건 · 기준 {len(base)}건, "
                          f"각 {MIN_DIRECTION_SAMPLES}건 필요)")
    r = statistics.median(recent)
    b = statistics.median(base)
    if b <= 0:
        return Adjustment("30일 방향성", None, "기준 중앙값이 0 이하입니다")
    raw = (r - b) / b
    capped = max(-MAX_DIRECTION_ADJUST, min(MAX_DIRECTION_ADJUST, raw))
    tail = ("" if abs(raw) <= MAX_DIRECTION_ADJUST
            else f", 원래 {raw:+.1%} 였지만 상한 적용")
    return Adjustment("30일 방향성", 1 + capped,
                      f"최근 30일 {raw:+.1%}{tail}")


def type_normalize(type_gap_pct: float | None) -> Adjustment:
    """같은 면적 안의 타입 격차 보정 (§35·§1).

    `complex_type.median_gap_pct` 에서 온다. **없으면 보정하지 않는다** —
    타입 차이를 0 으로 가정하면 A타입과 B타입이 한 가격으로 눌린다.
    """
    if type_gap_pct is None:
        return Adjustment("Type 정규화", None,
                          "지속 격차가 확인된 타입 정보가 없습니다")
    return Adjustment("Type 정규화", 1 + type_gap_pct,
                      f"같은 면적 평균 대비 {type_gap_pct:+.1%}")


def urgent_absorption(urgent_n: int | None, total_n: int) -> Adjustment:
    """급매를 얼마나 반영할 것인가 (§35).

    전부 빼면 호가에 가깝고, 전부 넣으면 운 좋아야 사는 값이다.
    시장이 급매를 흡수하는 비중만큼만 반영한다.
    """
    if urgent_n is None or total_n <= 0:
        return Adjustment("급매 흡수", None,
                          "급매 건수를 몰라 보정하지 않습니다")
    share = urgent_n / total_n
    if share <= 0:
        return Adjustment("급매 흡수", 1.0, "급매 거래가 없습니다")
    if share >= URGENT_ABSORBED:
        return Adjustment("급매 흡수", 1.0,
                          f"급매 비중 {share:.0%} — 시장이 흡수하고 있어 "
                          f"이 가격에 살 수 있습니다")
    # 급매가 예외적이면 그만큼 실제 체결가는 위쪽이다.
    factor = 1 + (URGENT_ABSORBED - share) * 0.10
    return Adjustment("급매 흡수", factor,
                      f"급매 비중 {share:.0%} — 예외적이라 실제 체결가는 "
                      f"{(factor - 1):.1%} 위입니다")


def normal_executable_price(base_price: int | None, *,
                            recent_30d: list[int] | None = None,
                            base_window: list[int] | None = None,
                            type_gap_pct: float | None = None,
                            urgent_n: int | None = None,
                            total_n: int = 0) -> Normalized:
    """§35 Normal Executable Price.

    `base_price` 는 이미 이상치·직거래·취소·저층을 걸러낸 90일 중앙값
    (`price/representative.py` 의 결과)이다. 여기서 다시 거르지 않는다.
    """
    if not base_price or base_price <= 0:
        return Normalized(0, None, [], total_n,
                          "정규화할 기준 가격이 없습니다")

    adjustments = [
        direction(recent_30d or [], base_window or []),
        type_normalize(type_gap_pct),
        urgent_absorption(urgent_n, total_n),
    ]
    price = float(base_price)
    for a in adjustments:
        if a.applied:
            price *= a.factor
    return Normalized(base_price, int(units.won_round(price)), adjustments,
                      total_n)
