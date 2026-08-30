"""MarketPressureScore — 매도자 우위인가 매수자 우위인가 (요구사항 10).

**LLM 이 느낌으로 점수를 만들지 않는다.** 기초 데이터를 먼저 계산하고, 그 위에서
가중합한다. 각 구성요소는 원값과 기여도가 그대로 남아, "왜 72점인지"를 숫자로 되짚을 수 있다.

구성요소는 각각 −1 ~ +1 로 정규화한다. +1 이 매도자 우위 쪽이다.

    매물 감소      매물이 줄면 +
    최저호가 상승   호가가 오르면 +
    중위호가 상승   "
    가격인하 감소   인하 매물이 줄면 +
    실거래 상승     대표 실거래가가 오르면 +
    전세가 상승     전세가 오르면 +

점수 = 50 + 50 × 가중평균 → 0~100.
구성요소 중 데이터가 없는 것은 **0점 처리하지 않고 가중치에서 아예 뺀다** —
없는 데이터를 '중립'으로 세면 실제보다 점수가 50 쪽으로 끌려간다.
"""
from __future__ import annotations

from dataclasses import dataclass

from apt_engine import units
from apt_engine.trace import Calc

# 요구사항 10: 가중치는 config 로 수정할 수 있어야 한다.
# 값을 바꿔 build() 에 넘기면 되고, PHASE 8에서 score_weight 테이블로 옮긴다.
DEFAULT_WEIGHTS: dict[str, float] = {
    "매물증감": 0.25,
    "최저호가": 0.20,
    "중위호가": 0.15,
    "가격인하": 0.15,
    "실거래방향": 0.15,
    "전세방향": 0.10,
}

# 이 변화율에서 정규화 값이 ±1 로 포화한다.
SATURATION = {
    "매물증감": 0.50,     # 매물 50% 증감이면 극단
    "최저호가": 0.05,     # 호가 5% 변화면 극단
    "중위호가": 0.05,
    "가격인하": 0.30,     # 인하매물 비중 30%p 변화면 극단
    "실거래방향": 0.05,
    "전세방향": 0.05,
}

SELLER, NEUTRAL, BUYER = "매도자우위", "중립", "매수자우위"
SELLER_THRESHOLD = 60.0
BUYER_THRESHOLD = 40.0


def _clamp(value: float, limit: float) -> float:
    """±limit 에서 포화하는 −1~+1 정규화."""
    if limit <= 0:
        return 0.0
    return max(-1.0, min(1.0, value / limit))


@dataclass(frozen=True)
class Component:
    key: str
    raw: float | None        # 원값(변화율 등). None 이면 데이터 없음
    normalized: float | None
    weight: float
    note: str

    @property
    def available(self) -> bool:
        return self.normalized is not None

    @property
    def contribution(self) -> float:
        return (self.normalized or 0.0) * self.weight


@dataclass(frozen=True)
class Pressure:
    """근거가 하나도 없으면 score 는 의미가 없다.

    그 경우에도 50 을 담아 두지만 `available_components` 가 비어 있으므로,
    표시 계층은 반드시 그걸 보고 "확인 불가"로 내야 한다. 근거 없는 50점을
    '중립'이라고 부르면 판단하지 않은 것을 판단한 것처럼 보이게 만든다.
    """
    score: float
    direction: str
    components: list[Component]
    calc: Calc

    @property
    def available_components(self) -> list[Component]:
        return [c for c in self.components if c.available]

    @property
    def coverage(self) -> float:
        """가중치 기준으로 몇 %의 근거가 실제로 있었나."""
        total = sum(c.weight for c in self.components)
        have = sum(c.weight for c in self.components if c.available)
        return have / total if total else 0.0


def build(*, change=None, trade_trend: float | None = None,
          jeonse_trend: float | None = None,
          weights: dict[str, float] | None = None) -> Pressure:
    """시장압력 점수.

    change        listing.change.Change (매물·호가 변화)
    trade_trend   대표 실거래가 변화율 (예: 0.03 = 3% 상승)
    jeonse_trend  대표 전세가 변화율
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)

    components: list[Component] = []

    # ── 매물 증감: 줄면 매도자 우위 ──
    if change is not None and change.count_before > 0:
        ratio = (change.count_after - change.count_before) / change.count_before
        components.append(Component(
            "매물증감", ratio, _clamp(-ratio, SATURATION["매물증감"]), w["매물증감"],
            f"{change.count_before} → {change.count_after}건 "
            f"({units.fmt_pct(ratio, sign=True)})"))
    else:
        components.append(Component("매물증감", None, None, w["매물증감"],
                                    "매물 스냅샷 2개 이상 필요 — 확인 불가"))

    # ── 호가 변화: 오르면 매도자 우위 ──
    for key, getter, label in (
        ("최저호가", "low_delta_ratio", "최저호가"),
        ("중위호가", "median_delta_ratio", "중위호가"),
    ):
        ratio = getattr(change, getter) if change is not None else None
        if ratio is None:
            components.append(Component(key, None, None, w[key], "확인 불가"))
        else:
            components.append(Component(
                key, ratio, _clamp(ratio, SATURATION[key]), w[key],
                f"{label} {units.fmt_pct(ratio, sign=True)}"))

    # ── 가격인하 비중: 줄면 매도자 우위 ──
    if change is not None and change.count_before > 0:
        cut_ratio = len(change.cuts) / change.count_before
        raise_ratio = len(change.raises) / change.count_before
        net = raise_ratio - cut_ratio          # 인상이 많으면 +
        components.append(Component(
            "가격인하", net, _clamp(net, SATURATION["가격인하"]), w["가격인하"],
            f"인하 {len(change.cuts)}건 · 인상 {len(change.raises)}건"))
    else:
        components.append(Component("가격인하", None, None, w["가격인하"], "확인 불가"))

    # ── 실거래·전세 방향 ──
    for key, value in (("실거래방향", trade_trend), ("전세방향", jeonse_trend)):
        if value is None:
            components.append(Component(key, None, None, w[key],
                                        "스냅샷 2개 이상 필요 — 확인 불가"))
        else:
            components.append(Component(key, value, _clamp(value, SATURATION[key]), w[key],
                                        units.fmt_pct(value, sign=True)))

    available = [c for c in components if c.available]
    if available:
        total_w = sum(c.weight for c in available)
        weighted = sum(c.contribution for c in available) / total_w if total_w else 0.0
        score = 50.0 + 50.0 * weighted
    else:
        # 근거가 하나도 없으면 중립 50 이 아니라 "확인 불가"다. 점수를 만들지 않는다.
        weighted, score = 0.0, 50.0

    direction = (SELLER if score >= SELLER_THRESHOLD
                 else BUYER if score <= BUYER_THRESHOLD else NEUTRAL)

    coverage = (sum(c.weight for c in available) / sum(c.weight for c in components)
                if components else 0.0)

    calc = Calc(
        value=round(score, 1), unit="점",
        formula="50 + 50 × Σ(정규화값 × 가중치) ÷ Σ(사용된 가중치)",
        inputs={c.key: c.note for c in components},
        intermediates={
            "정규화": {c.key: (None if c.normalized is None else round(c.normalized, 3))
                      for c in components},
            "가중치": w,
            "근거 확보율": units.fmt_pct(coverage, digits=0),
            "판정": direction,
            **({"주의": "근거가 하나도 없어 점수를 신뢰할 수 없습니다 — 매물 스냅샷을 "
                        "며칠 쌓은 뒤 다시 계산하세요."} if not available else {}),
        },
        # 관측된 변화에서 계산한 값이지만, 가중치는 우리가 정한 가정이다.
        grade="CONFIRMED" if available else "ESTIMATED",
    )
    return Pressure(round(score, 1), direction, components, calc)
