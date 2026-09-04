"""가중치 — 국면별로 다르고, 출처가 기록된다 (지시서 §8·§74).

> §8 시장국면마다 factor weight 가 달라져야 한다.
>    고정 weight 하나로 모든 시장을 평가하지 않는다.
> §74 사람이 "이 정도 중요하겠지" 로 만든 scoring table 을 최종 모델로 쓰지 않는다.

두 요구를 동시에 지키는 방법: 가중치를 **데이터로** 두고, 어디서 왔는지를
값과 함께 저장한다. 지금은 전부 `HEURISTIC` 이고, 백테스트(§55)가 돌면
같은 자리에 `BACKTESTED` 값이 들어간다. 랭킹 결과에는 어느 쪽을 썼는지가 남는다.

**아래 숫자를 근거로 투자 판단을 하면 안 된다.** 후보를 좁히는 용도(discovery)로만
쓰라고 지시서가 정해 뒀고, 코드도 그 사실을 결과에 표시한다.
"""
from __future__ import annotations

from dataclasses import dataclass

HEURISTIC = "HEURISTIC"
BACKTESTED = "BACKTESTED"

# 모델 10종 (§49 의 9종 + access)
MODELS = ("value", "momentum", "supply", "catalyst", "redevelopment",
          "relative", "jeonse", "risk", "capital_efficiency", "access")

# 기본 가중치. 합이 1 이 아니어도 된다 — 쓸 수 있는 모델만 골라 정규화한다.
BASE: dict[str, float] = {
    "value": 0.20,
    "momentum": 0.10,
    "supply": 0.12,
    "catalyst": 0.12,
    # 2026-09-04 MASTER_SPEC §14·§35 결합: 정비사업 옵션과 상대가격 Mispricing 은
    # 점수가 아니라 Liquid Exit Price / Terminal Wealth 의 입력이다(invest/exit_price.py).
    # 같은 사실을 consensus 점수로 또 더하면 Double Counting 이라 0 으로 내린다.
    # 0 이면 for_models() 가 빼고 남은 모델끼리 정규화한다. 순위 변화는
    # RESEARCH_LOG_TW_COMBINE_v0.1.md 에 기록.
    "redevelopment": 0.0,
    "relative": 0.0,
    "jeonse": 0.10,
    "risk": 0.08,
    "capital_efficiency": 0.05,
    # access 는 다른 항목보다 작게 잡았다. 측정된 폭 자체가 작기 때문이다
    # (5년에 ~500m +0.66%p vs 2km 밖 -0.75%p). 방향이 18년 내내 뒤집히지
    # 않았고 밴드 다섯 개가 순서대로 늘어서서 넣을 만하지만, 크기를 부풀리지
    # 않는다. 다른 값들과 마찬가지로 백테스트가 이 자리를 대체한다.
    "access": 0.06,
}

# 국면별 조정 배율. 침체기에 모멘텀을 크게 보면 계속 떨어지는 걸 사게 되고,
# 과열기에 모멘텀을 크게 보면 상투를 잡는다.
REGIME_ADJUST: dict[str, dict[str, float]] = {
    "침체":     {"value": 1.4, "jeonse": 1.3, "momentum": 0.4, "risk": 1.3},
    "바닥형성": {"value": 1.3, "jeonse": 1.2, "momentum": 0.7, "supply": 1.2},
    "회복초기": {"value": 1.2, "momentum": 1.2, "relative": 1.2},
    "상승초기": {"momentum": 1.2, "relative": 1.2, "catalyst": 1.1},
    "상승확산": {"momentum": 1.0, "relative": 1.1, "supply": 1.2, "risk": 1.1},
    "과열":     {"momentum": 0.5, "value": 1.3, "risk": 1.5, "supply": 1.3},
    "하락전환": {"risk": 1.5, "value": 1.2, "momentum": 0.3, "jeonse": 1.2},
}

ADJUST_NOTE = ("국면별 배율은 관측된 통계가 아니라 판정 기준입니다. "
               "백테스트(§55·§71 Ablation)가 이 값을 대체합니다")


@dataclass(frozen=True)
class Weights:
    values: dict[str, float]
    source: str
    regime: str | None = None

    def for_models(self, available: list[str]) -> dict[str, float]:
        """쓸 수 있는 모델만 남기고 **합이 1 이 되게 다시 정규화**한다.

        모델 하나가 데이터 부족으로 빠졌을 때 그 자리를 0 으로 두면 전체 점수가
        낮아진다. 그건 "그 항목이 나쁘다" 가 아니라 "모른다" 인데, 점수는
        나쁘다고 읽힌다. 그래서 남은 모델들끼리 다시 나눈다.
        """
        picked = {m: self.values.get(m, 0.0) for m in available
                  if self.values.get(m, 0.0) > 0}
        total = sum(picked.values())
        if total <= 0:
            return {}
        return {m: w / total for m, w in picked.items()}

    @property
    def label(self) -> str:
        tag = "학습됨" if self.source == BACKTESTED else "임시(heuristic)"
        return f"{tag}" + (f" · 국면 {self.regime}" if self.regime else "")


def for_regime(regime: str | None, *, source: str = HEURISTIC) -> Weights:
    """국면에 맞춘 가중치. 국면을 모르면 기본값을 쓰고 그 사실이 label 에 남는다."""
    values = dict(BASE)
    if regime and regime in REGIME_ADJUST:
        for model, factor in REGIME_ADJUST[regime].items():
            values[model] = values.get(model, 0.0) * factor
    return Weights(values, source, regime)
