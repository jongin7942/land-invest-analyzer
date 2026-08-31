"""Thesis Survival — 가장 강한 논리를 빼도 남는가 (지시서 §23).

> 각 후보의 가장 강한 투자논리 하나를 제거한다. 그럼에도 투자논리가 남는지 계산한다.

이유는 단순하다. GTX 하나에 기대는 후보는 GTX 가 밀리면 끝난다. 재건축 하나에
기대는 후보는 사업이 지연되면 끝난다. 여러 이유가 겹쳐 있는 후보가 실제로 더 안전하다.

계산 방법: 가장 크게 기여한 모델을 빼고 다시 합쳐서, 점수가 얼마나 남는지 본다.
그래서 이건 별도 지표가 아니라 **consensus 를 한 번 더 돌리는 것**이다.
§22 Reconstruction Ablation 도 같은 방식이므로 함께 처리한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine.scoring import consensus as consensus_mod
from apt_engine.scoring import weights as weights_mod


@dataclass(frozen=True)
class Survival:
    value: float                       # 0~1. 논리 하나를 빼도 남는 점수 비율
    removed: str
    before: float
    after: float
    ablations: dict[str, float] = field(default_factory=dict)

    @property
    def fragile(self) -> bool:
        """한 논리에 기대고 있는가."""
        return self.value < 0.6

    @property
    def label(self) -> str:
        tag = " ⚠ 한 논리에 기댐" if self.fragile else ""
        return (f"{self.value:.0%}  ('{self.removed}' 제거 시 "
                f"{self.before:.0f} → {self.after:.0f}점){tag}")


def evaluate(base: consensus_mod.Consensus,
             weights: weights_mod.Weights) -> Survival:
    """가장 크게 기여한 모델을 빼고 다시 계산한다."""
    if not base.attribution or base.score <= 0:
        return Survival(0.0, "확인 불가", base.score, base.score, {})

    ablations: dict[str, float] = {}
    for model in base.attribution:
        reduced = {m: s for m, s in base.scores.items() if m != model}
        ablations[model] = consensus_mod.combine(base.complex_id, reduced,
                                                 weights).score

    strongest = max(base.attribution.items(), key=lambda kv: kv[1])[0]
    after = ablations[strongest]
    return Survival(min(1.0, after / base.score), strongest, base.score, after,
                    ablations)


def reconstruction_dependency(base: consensus_mod.Consensus,
                              weights: weights_mod.Weights) -> dict:
    """§22 — 재건축 점수를 0 으로 만들면 순위가 얼마나 떨어지나."""
    if "redevelopment" not in base.attribution:
        return {"의존도": "확인 불가", "사유": "재건축 모델이 계산되지 않았습니다"}
    reduced = {m: s for m, s in base.scores.items() if m != "redevelopment"}
    after = consensus_mod.combine(base.complex_id, reduced, weights).score
    share = base.attribution["redevelopment"]
    return {
        "의존도": f"{share:.0%}",
        "제거 시": f"{base.score:.0f} → {after:.0f}점",
        "판정": ("재건축에 크게 기대는 후보 — 사업 지연 위험을 함께 봐야 합니다"
               if share > 0.35 else "재건축을 빼도 논리가 남습니다"),
    }
