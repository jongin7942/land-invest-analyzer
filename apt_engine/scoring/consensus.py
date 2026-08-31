"""모델 합성 — Score 와 Confidence 를 절대 합치지 않는다 (지시서 §49·§50·§76).

> Score 92 / Confidence 41 이면 "좋아 보이지만 데이터가 약한 후보" 다.

그래서 결과는 숫자 하나가 아니라 넷이다.

    score        0~100. 모델들의 가중 평균
    confidence   0~100. 데이터가 얼마나 받쳐 주는가
    consensus    모델들이 얼마나 같은 말을 하는가 (§49)
    attribution  어느 모델이 점수의 몇 %를 만들었나 (§76)

`attribution` 은 SHAP 이 아니라 **가법 모델의 정확한 기여도 분해**다.
점수가 가중합이므로 각 항의 기여는 정확히 계산되고 근사 오차가 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine.scoring import models as models_mod
from apt_engine.scoring import weights as weights_mod
from apt_engine.trace import Calc


@dataclass(frozen=True)
class Consensus:
    complex_id: int
    score: float                       # 0~100
    confidence: float                  # 0~100
    agreement: float                   # 0~1. 모델 간 의견 일치도
    scores: dict[str, models_mod.ModelScore] = field(default_factory=dict)
    attribution: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    weights_source: str = weights_mod.HEURISTIC
    missing_models: list[str] = field(default_factory=list)
    calc: Calc | None = None

    @property
    def label(self) -> str:
        warn = "" if self.confidence >= 50 else "  ⚠ 데이터가 약함"
        return (f"점수 {self.score:.0f} / 신뢰도 {self.confidence:.0f} / "
                f"일치도 {self.agreement:.0%}{warn}")

    @property
    def top_drivers(self) -> list[tuple[str, float]]:
        """점수를 가장 크게 만든 모델 순. §75 '왜 이 아파트인가' 의 재료."""
        return sorted(self.attribution.items(), key=lambda kv: -abs(kv[1]))


def combine(complex_id: int, scores: dict[str, models_mod.ModelScore],
            weights: weights_mod.Weights) -> Consensus:
    """모델들을 합친다. 없는 모델은 0 이 아니라 **가중치에서 제외**된다."""
    available = [m for m, s in scores.items() if s.known]
    missing = sorted(m for m, s in scores.items() if not s.known)
    w = weights.for_models(available)

    if not w:
        return Consensus(complex_id, 0.0, 0.0, 0.0, scores, {}, {},
                         weights.source, missing,
                         Calc(value=None, unit="점",
                              formula="쓸 수 있는 모델이 없어 점수를 만들지 않았습니다",
                              intermediates={"없는 모델": missing},
                              grade="SCENARIO"))

    contributions = {m: w[m] * scores[m].value * 100.0 for m in w}
    score = sum(contributions.values())

    # 신뢰도: 각 모델 신뢰도의 가중 평균 × 모델 커버리지.
    # 모델이 절반만 계산됐으면 점수는 나오지만 믿을 근거는 절반이다.
    weighted_conf = sum(w[m] * scores[m].confidence for m in w)
    coverage = len(available) / len(scores)
    confidence = weighted_conf * coverage * 100.0

    # 일치도: 모델 점수들이 얼마나 모여 있나. 흩어져 있으면 한 모델만 좋다는 뜻이다.
    values = [scores[m].value for m in available]
    if len(values) >= 2:
        mean = sum(values) / len(values)
        spread = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        agreement = max(0.0, 1.0 - spread * 2)      # 표준편차 0.5 면 일치도 0
    else:
        agreement = 0.0

    attribution = {m: c / score if score else 0.0 for m, c in contributions.items()}

    calc = Calc(
        value=score, unit="점",
        formula="점수 = Σ(모델 점수 × 가중치) × 100",
        inputs={"가중치 출처": weights.label},
        intermediates={
            "모델별": {m: s.label for m, s in sorted(scores.items())},
            "가중치": {m: round(v, 3) for m, v in sorted(w.items())},
            "기여도": {m: f"{v:+.0%}" for m, v in sorted(attribution.items(),
                                                    key=lambda kv: -kv[1])},
            "계산 못한 모델": missing or "없음",
            "일치도": f"{agreement:.0%}",
            "신뢰도": f"{confidence:.0f}/100",
            "주의": ("점수와 신뢰도는 다른 축입니다(§50). 점수가 높아도 신뢰도가 "
                   "낮으면 '좋아 보이지만 데이터가 약한 후보' 입니다"),
            "가중치 성격": (weights_mod.ADJUST_NOTE
                      if weights.source == weights_mod.HEURISTIC
                      else "백테스트로 학습된 가중치"),
        },
        grade="SCENARIO",
    )
    return Consensus(complex_id, score, confidence, agreement, scores, attribution,
                     w, weights.source, missing, calc)


def explain_pair(a: Consensus, b: Consensus) -> dict:
    """왜 A 가 B 보다 높은가 (§75).

    점수가 가법이라 차이도 항별로 정확히 쪼개진다.
    """
    keys = set(a.attribution) | set(b.attribution)
    diffs = {}
    for m in keys:
        a_part = a.weights.get(m, 0.0) * (a.scores[m].value or 0.0) * 100
        b_part = b.weights.get(m, 0.0) * (b.scores[m].value or 0.0) * 100
        diffs[m] = a_part - b_part
    ordered = sorted(diffs.items(), key=lambda kv: -abs(kv[1]))
    return {
        "점수 차": round(a.score - b.score, 1),
        "항목별 기여 차": {m: round(v, 1) for m, v in ordered},
        "가장 큰 이유": ordered[0][0] if ordered else "확인 불가",
        "해석": "점수가 가중합이라 차이도 항별로 정확히 쪼개집니다(근사 아님)",
    }
