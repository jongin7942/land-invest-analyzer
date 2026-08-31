"""순위의 불확실성 — 예상 순위 구간 · 지속성 · 몬테카를로
   (지시서 §51·§52·§53).

§52 가 요구하는 것:

> 예상 순위 구간 (bootstrap)

"3위" 라는 숫자 하나는 거짓말에 가깝다. 후보 20개의 점수가 47.7 / 47.5 / 47.2
로 붙어 있으면 3위와 5위는 사실상 같은 자리다. 표본을 조금만 다르게 뽑아도
순위가 뒤집힌다.

그래서 점수를 **한 번만 계산하지 않는다.** 각 Feature 의 신뢰도만큼 값을
흔들어 여러 번 매긴 뒤, 그 후보가 실제로 몇 위에서 몇 위 사이에 있었는지를
구간으로 낸다.

    3위 (구간 2~9위)     ← 이게 정직한 표현이다

§51 지속성은 그 위에 있다. 매번 순위가 크게 요동치면 그 모델은 순위를
만들 자격이 없다. 후보가 변한 게 아니라 모델이 흔들리는 것이기 때문이다.

⚠ 이 모듈은 **불확실성을 줄이지 않는다.** 드러낼 뿐이다. 구간이 넓다는 것은
"데이터가 모자라다" 는 사실이고, 그걸 좁은 것처럼 보여주지 않는 게 목적이다.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field

# 반복 횟수. 많을수록 구간이 안정되지만 느려진다.
DEFAULT_TRIALS = 200

# 신뢰도가 낮을수록 크게 흔든다. 신뢰도 1.0 이면 흔들지 않는다.
MAX_JITTER = 0.35

# 순위가 이 폭 넘게 흔들리면 "순위를 만들 자격이 없다" 고 본다.
UNSTABLE_SPREAD = 0.5      # 후보 수 대비 비율

NOTE = ("구간은 불확실성을 **드러내는** 것이지 줄이는 것이 아닙니다. "
        "넓다는 것은 데이터가 모자라다는 뜻입니다")


@dataclass(frozen=True)
class RankRange:
    complex_id: int
    point: int                       # 한 번 계산했을 때의 순위
    low: int                         # 가장 좋았던 순위
    high: int                        # 가장 나빴던 순위
    median: float
    trials: int
    n_candidates: int

    @property
    def spread(self) -> int:
        return self.high - self.low

    @property
    def stable(self) -> bool:
        if self.n_candidates <= 1:
            return True
        return self.spread / self.n_candidates < UNSTABLE_SPREAD

    @property
    def label(self) -> str:
        tail = "" if self.stable else "  ⚠ 순위가 불안정합니다"
        return f"{self.point}위 (구간 {self.low}~{self.high}위){tail}"


@dataclass
class Simulation:
    ranges: dict[int, RankRange] = field(default_factory=dict)
    trials: int = 0
    unstable: list[int] = field(default_factory=list)

    @property
    def summary(self) -> str:
        lines = [f"예상 순위 구간 ({self.trials}회 반복)"]
        for r in sorted(self.ranges.values(), key=lambda x: x.point):
            lines.append(f"  #{r.complex_id}: {r.label}")
        if self.unstable:
            lines.append(f"  불안정 {len(self.unstable)}개 — "
                         f"이 후보들의 순위는 우연에 가깝습니다")
        lines.append(f"  {NOTE}")
        return "\n".join(lines)


def rank_ranges(scores: dict[int, float], confidences: dict[int, float], *,
                trials: int = DEFAULT_TRIALS, seed: int = 20260831
                ) -> Simulation:
    """§52 — 점수를 신뢰도만큼 흔들어 순위 구간을 낸다.

    신뢰도가 높은 후보는 거의 안 흔들리고, 낮은 후보는 크게 흔들린다.
    그래서 "점수는 높은데 근거가 약한" 후보의 구간이 넓게 나온다 —
    §36 이 요구한 Alpha/Confidence 분리가 순위에서도 보이게 된다.
    """
    if len(scores) < 2:
        return Simulation({}, 0, [])

    rng = random.Random(seed)
    ids = sorted(scores)
    n = len(ids)

    base_order = sorted(ids, key=lambda c: (-scores[c], c))
    point = {cid: i + 1 for i, cid in enumerate(base_order)}

    observed: dict[int, list[int]] = {cid: [] for cid in ids}
    for _ in range(trials):
        jittered = {}
        for cid in ids:
            conf = max(0.0, min(1.0, confidences.get(cid, 0.0) / 100.0))
            spread = MAX_JITTER * (1 - conf)
            jittered[cid] = scores[cid] * (1 + rng.gauss(0, spread))
        order = sorted(ids, key=lambda c: (-jittered[c], c))
        for i, cid in enumerate(order):
            observed[cid].append(i + 1)

    ranges: dict[int, RankRange] = {}
    unstable: list[int] = []
    for cid in ids:
        got = observed[cid]
        # 양 끝 2.5% 를 잘라 95% 구간으로. 한 번의 극단값이 구간을 지배하지
        # 않게 한다.
        got.sort()
        lo = got[max(0, int(len(got) * 0.025))]
        hi = got[min(len(got) - 1, int(len(got) * 0.975))]
        r = RankRange(cid, point[cid], lo, hi, statistics.median(got),
                      trials, n)
        ranges[cid] = r
        if not r.stable:
            unstable.append(cid)
    return Simulation(ranges, trials, unstable)


# ── §51 Ranking Persistence ──────────────────────────────────────────

@dataclass(frozen=True)
class Persistence:
    overlap: float | None            # 직전 TOP K 와 겹치는 비율
    churn: int | None                # 바뀐 개수
    k: int
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.overlap is not None

    @property
    def stable(self) -> bool | None:
        if not self.known:
            return None
        return self.overlap >= 0.6

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        verdict = ("안정적입니다" if self.stable else
                   "매 실행마다 크게 바뀝니다 — 후보가 아니라 모델이 흔들리는 "
                   "것일 수 있습니다")
        return (f"직전 TOP{self.k} 와 {self.overlap:.0%} 겹칩니다 "
                f"({self.churn}개 교체) · {verdict}")


def persistence(previous: list[int], current: list[int], *, k: int = 10
                ) -> Persistence:
    """§51 — 순위가 매번 뒤집히면 그 모델은 순위를 만들 자격이 없다."""
    if not previous:
        return Persistence(None, None, k, "직전 실행이 없습니다")
    a, b = set(previous[:k]), set(current[:k])
    if not a:
        return Persistence(None, None, k, "직전 TOP 이 비어 있습니다")
    kept = len(a & b)
    return Persistence(kept / len(a), len(a) - kept, k)


# ── §53 Monte Carlo ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Outcome:
    p10: float
    p50: float
    p90: float
    loss_probability: float
    trials: int
    inputs: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return (f"수익률 P10 {self.p10:+.1%} · 중앙값 {self.p50:+.1%} · "
                f"P90 {self.p90:+.1%} · 손실확률 {self.loss_probability:.0%}")


def monte_carlo(*, expected_return: float | None, volatility: float | None,
                downside_defense: float | None = None,
                trials: int = 1000, seed: int = 20260831) -> Outcome | None:
    """§53 — 기대수익 하나가 아니라 분포로.

    **변동성을 모르면 시뮬레이션하지 않는다.** 임의의 변동성을 넣으면
    그 숫자가 결과를 지배한다 — 분포의 모양이 우리가 정한 가정 그 자체가 된다.
    """
    if expected_return is None or volatility is None or volatility <= 0:
        return None

    rng = random.Random(seed)
    draws = []
    # 전세가 받쳐 주면 하방이 얕다(§14). 상방은 건드리지 않는다.
    floor = None
    if downside_defense is not None:
        floor = -(1 - max(0.0, min(1.0, downside_defense))) * volatility * 2

    for _ in range(trials):
        r = rng.gauss(expected_return, volatility)
        if floor is not None:
            r = max(r, floor)
        draws.append(r)

    draws.sort()
    return Outcome(
        draws[int(trials * 0.10)], draws[int(trials * 0.50)],
        draws[int(trials * 0.90)],
        sum(1 for d in draws if d < 0) / trials, trials,
        {"기대수익": expected_return, "변동성": volatility,
         "하방바닥": floor})
