"""EarlyAlpha (신규 지시서 §21·§20·§36·§45).

지시서가 준 구조:

    EarlyAlpha ≈ RemainingRecoverableGap
               × PriceBandMigration
               × TransmissionProbability
               × BuyerPool
               × DownsideAnchor

    감점: PriceStretch · EntryRisk · EffectiveSupplyRisk
          · PersistentCheapness · TransmissionFailure

    NeighbourConfirmation · CrossSizeConfirmation → **Confidence 조정**

그리고 §21 이 못박은 것:

> 고정 Weight 를 임의로 확정하지 말고 Walk-Forward Backtest 로 학습/검증한다.

그래서 이 모듈은 **가중치를 갖고 있지 않다.** 구조만 갖고 있고, 숫자는
`backtest/usefulness.py` 가 채운다. 학습 전에는 균등 가중치로 계산하고
결과에 `HEURISTIC` 딱지가 붙는다.

**곱셈인 것이 중요하다.** 다섯 중 하나라도 0 이면 전체가 0 이다.
합으로 만들면 "회복 가능한 격차가 전혀 없지만 다른 게 좋아서 높은 점수" 같은
후보가 생긴다. 지시서가 찾는 것은 다섯이 **동시에** 성립하는 자리다.

§36 대로 결과를 셋으로 나눠 낸다.

    Expected Alpha  78
    Risk            42
    Confidence      83

셋을 하나로 합치지 않는다. Alpha 78 / Confidence 30 은
"좋아 보이지만 근거가 약하다" 이고, 그건 Alpha 40 과 완전히 다른 상태다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine.features import registry as registry_mod
from apt_engine.features.base import FeatureSet
from apt_engine.scoring import weights as weights_mod
from apt_engine.trace import Calc

# §21 이 준 곱셈 항. 각각 어느 Feature 에서 오는가.
MULTIPLIERS: dict[str, tuple[str, ...]] = {
    "RemainingRecoverableGap": ("recoverable_discount_ratio",),
    "PriceBandMigration": ("band_shift_strength",),
    "TransmissionProbability": ("next_node_score", "neighbour_confirmation"),
    "BuyerPool": ("buyer_pool",),
    "DownsideAnchor": ("downside_defense",),
}

# 감점 항. 전부 STRETCH/RISK 쪽이고 **ALPHA 와 겹치지 않는다**(§45).
PENALTIES: dict[str, str] = {
    "PriceStretch": "price_stretch",
    "EntryRisk": "acceleration_zone",
    "EffectiveSupplyRisk": "effective_supply_risk",
    "PersistentCheapness": "persistent_cheapness",
    "TransmissionFailure": "transmission_failure",
}

# §10·§11 — Alpha 가 아니라 Confidence 를 조정한다.
CONFIDENCE_INPUTS = ("neighbour_confirmation", "path_quality",
                     "reset_completion")

# 곱셈 항이 이 개수 미만이면 점수를 만들지 않는다. 하나만 있으면 그건
# EarlyAlpha 가 아니라 그 Feature 하나다.
MIN_MULTIPLIERS = 3

STRUCTURE_NOTE = (
    "다섯 항의 **곱**입니다. 하나라도 0 이면 전체가 0 입니다 — 합으로 만들면 "
    "'회복 가능한 격차가 없는데 다른 게 좋아서 높은 점수' 가 생깁니다(§21)")
WEIGHT_NOTE = ("가중치는 임의로 확정하지 않습니다. 학습 전에는 균등이고 "
               "Walk-Forward 백테스트가 대체합니다(§21)")


@dataclass(frozen=True)
class Alpha:
    """§36 — Alpha / Risk / Confidence 를 절대 합치지 않는다."""
    complex_id: int
    alpha: float | None                 # 0~100
    risk: float | None                  # 0~100
    confidence: float                   # 0~100
    multipliers: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, float] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    weights_source: str = weights_mod.HEURISTIC
    calc: Calc | None = None

    @property
    def known(self) -> bool:
        return self.alpha is not None

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — 없는 항목: {', '.join(self.missing[:3])}"
        risk = f"{self.risk:.0f}" if self.risk is not None else "확인 불가"
        tag = "" if self.weights_source == weights_mod.BACKTESTED else "  (가중치 임시)"
        return (f"Expected Alpha {self.alpha:.0f} · Risk {risk} · "
                f"Confidence {self.confidence:.0f}{tag}")

    @property
    def report(self) -> str:
        lines = [self.label]
        if self.multipliers:
            lines.append("  구성 (곱)")
            for k, v in sorted(self.multipliers.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {k:<26} {v:.3f}")
        if self.penalties:
            lines.append("  감점")
            for k, v in sorted(self.penalties.items(), key=lambda kv: -v):
                lines.append(f"    {k:<26} −{v:.3f}")
        if self.missing:
            lines.append(f"  없는 항목: {', '.join(self.missing)}")
        return "\n".join(lines)


def _value(fs: FeatureSet, key: str) -> float | None:
    f = fs.items.get(key)
    if f is None or not f.usable or f.value is None:
        return None
    return f.value


def _oriented(fs: FeatureSet, key: str) -> float | None:
    """등록부의 방향을 반영해 '높을수록 좋음' 으로 맞춘 0~1 값."""
    raw = _value(fs, key)
    if raw is None:
        return None
    entry = registry_mod.get(key)
    value = max(0.0, min(1.0, raw))
    if entry is not None and not entry.higher_is_better:
        # price_stretch 처럼 음수가 좋은 값은 별도로 다룬다(감점 쪽).
        return value
    return value


def compute(complex_id: int, fs: FeatureSet, *,
            weights: dict[str, float] | None = None,
            weights_source: str = weights_mod.HEURISTIC) -> Alpha:
    """§21 EarlyAlpha.

    `weights` 는 곱셈 항의 **지수**다(가중 기하평균). 주지 않으면 균등이고,
    그 사실이 `weights_source` 에 남는다.
    """
    multipliers: dict[str, float] = {}
    missing: list[str] = []

    for term, keys in MULTIPLIERS.items():
        values = [_oriented(fs, k) for k in keys]
        values = [v for v in values if v is not None]
        if not values:
            missing.append(term)
            continue
        multipliers[term] = sum(values) / len(values)

    if len(multipliers) < MIN_MULTIPLIERS:
        return Alpha(complex_id, None, None,
                     _confidence(fs, len(multipliers)), multipliers, {},
                     missing, weights_source,
                     Calc(value=None, unit="점",
                          formula=(f"곱셈 항이 {len(multipliers)}개뿐이라 점수를 "
                                   f"만들지 않았습니다(최소 {MIN_MULTIPLIERS}개). "
                                   f"{STRUCTURE_NOTE}"),
                          intermediates={"없는 항목": missing},
                          grade="SCENARIO"))

    # 가중 기하평균. 가중치를 안 주면 균등이다.
    w = {k: (weights or {}).get(k, 1.0) for k in multipliers}
    total_w = sum(w.values()) or 1.0
    product = 1.0
    for term, value in multipliers.items():
        product *= max(1e-6, value) ** (w[term] / total_w)

    penalties = _penalties(fs)
    penalty_total = min(0.9, sum(penalties.values()))

    alpha = max(0.0, min(100.0, product * 100.0 * (1 - penalty_total)))
    risk = min(100.0, penalty_total * 100.0)
    confidence = _confidence(fs, len(multipliers))

    return Alpha(complex_id, alpha, risk, confidence, multipliers, penalties,
                 missing, weights_source,
                 Calc(value=alpha, unit="점",
                      formula=("가중 기하평균(다섯 항) × (1 − 감점합) × 100. "
                               + STRUCTURE_NOTE),
                      intermediates={"곱셈항": multipliers, "감점": penalties,
                                     "가중치": w, "출처": weights_source},
                      grade="SCENARIO"))


def _penalties(fs: FeatureSet) -> dict[str, float]:
    """감점. §45 대로 ALPHA 쪽 Feature 를 여기서 다시 쓰지 않는다."""
    out: dict[str, float] = {}
    for term, key in PENALTIES.items():
        raw = _value(fs, key)
        if raw is None:
            continue
        if key == "price_stretch":
            # 정상가보다 비싼 만큼만 감점. 싼 것은 Cheapness 쪽에서 이미 셌다.
            out[term] = max(0.0, min(0.5, raw))
        else:
            out[term] = max(0.0, min(0.5, raw)) * 0.4
    return out


def _confidence(fs: FeatureSet, n_multipliers: int) -> float:
    """§36 — DataQuality 는 Confidence 만 움직인다. Alpha 를 올리지 않는다.

    §10·§11 대로 Neighbour Confirmation 과 경로 확인도 여기로 들어온다.
    """
    coverage = n_multipliers / len(MULTIPLIERS)
    signals = [f.confidence for f in fs.items.values() if f.usable]
    base = (sum(signals) / len(signals)) if signals else 0.0

    boost = 1.0
    for key in CONFIDENCE_INPUTS:
        v = _value(fs, key)
        if v is not None:
            boost *= (0.85 + 0.30 * max(0.0, min(1.0, v)))
    return max(0.0, min(100.0, base * coverage * boost * 100.0))


def remaining_alpha(alpha: Alpha, *, already_priced_in: float | None
                    ) -> tuple[float | None, str]:
    """§20 Proof–Price Tradeoff.

        RemainingAlpha = ThesisProbability × PotentialUpside − AlreadyPricedIn

    확신이 올라가도 이미 가격에 들어갔으면 남은 알파는 준다. 그래서
    "신호가 확실해질수록 점수가 계속 오르는" 구조를 만들지 않는다.
    """
    if not alpha.known:
        return None, "Alpha 를 계산하지 못했습니다"
    if already_priced_in is None:
        return None, ("이미 반영된 정도를 몰라 남은 알파를 내지 않습니다 — "
                      "0 으로 두면 확실해질수록 점수가 계속 오릅니다(§20)")
    priced = max(0.0, min(1.0, already_priced_in))
    value = alpha.alpha * (1 - priced)
    return value, (f"Alpha {alpha.alpha:.0f} 중 {priced:.0%} 는 이미 가격에 "
                   f"들어갔습니다 → 남은 {value:.0f}")
