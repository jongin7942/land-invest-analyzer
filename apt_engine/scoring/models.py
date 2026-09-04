"""독립 모델 9종 (지시서 §49).

> 하나의 거대한 scoring formula 에 모든 것을 넣지 않는다.

이유는 셋이다.
  1. 한 덩어리로 만들면 어느 항목이 결과를 만들었는지 알 수 없다(§75·§76)
  2. Ablation(§71)으로 하나씩 빼서 검증할 수 없다
  3. 모델끼리 의견이 갈리는지(Consensus) 볼 수 없다 — 다 같이 좋다고 하는 후보와
     한 모델만 좋다고 하는 후보는 다르다

각 모델은 **쓸 수 있는 feature 가 없으면 점수를 만들지 않는다**(None).
빠진 자리를 0 이나 평균으로 채우지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine.features.base import FeatureSet
from apt_engine.scoring import normalize

# 모델 하나가 성립하려면 최소 이만큼의 입력이 있어야 한다.
MIN_INPUTS = 1


@dataclass(frozen=True)
class ModelScore:
    key: str
    value: float | None            # 0~1
    confidence: float
    used: dict[str, float] = field(default_factory=dict)   # feature → 기여 위치
    missing: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 ({', '.join(self.missing[:3])})"
        return f"{self.value:.2f}  (신뢰도 {self.confidence:.0%})"


# 모델 → (feature key, 높을수록 좋은가) 목록.
# **이 표가 곧 모델의 정의다.** 코드가 아니라 표라서 Ablation·설명이 쉽다.
SPEC: dict[str, list[tuple[str, bool]]] = {
    # 지금 싸게 사는가 (§7)
    "value": [("entry_position", False)],
    # 최근 흐름. discovery_lag 은 높을수록 나쁘다(§40)
    "momentum": [("momentum_6m", True), ("price_acceleration", True),
                 ("discovery_lag", False)],
    # 공급이 적을수록 좋고, 절벽이면 그 뒤가 좋다(§13)
    # supply_ratio 는 투자기간에 맞춰 바뀐다 - spec_for() 참고. 여기 적힌 3y 는
    # 기간을 안 넘겼을 때의 기본값이다.
    "supply": [("supply_ratio_3y", False), ("supply_cliff", True)],
    # 아직 반영되지 않은 호재 (§17)
    "catalyst": [("catalyst_alpha", True)],
    # 역세권 격차가 벌어지는 속도. '역세권이라 비싸다' 는 이미 값에 있으므로
    # 세지 않는다 - 앞으로 더 벌어질 몫만 센다(features/access.py).
    "access": [("station_access_drift", True)],
    # 재건축은 별도 엔진에서 온다 (§19~§22)
    "redevelopment": [("redev_mispricing", True)],
    # 비교단지 대비 위치 (§10)
    "relative": [("relative_gap", True)],
    # 전세는 하방 방어로만 쓴다 (§14)
    "jeonse": [("downside_defense", True), ("jeonse_lead", True)],
    # 위험이 적을수록 좋다
    "risk": [("transaction_quality", True), ("supply_ratio_1y", False)],
    # 같은 돈으로 더 큰 자산 (§28·§29)
    "capital_efficiency": [("capital_efficiency", True)],
}


# features/supply.py 가 계산해 두는 지평들. 투자기간에 맞는 것을 고른다.
SUPPLY_HORIZONS = (1, 2, 3, 5)


def supply_key_for(horizon_years: int | None) -> str:
    """투자기간에 맞는 공급 지평. 없으면 그보다 긴 쪽으로 올린다.

    4년 투자면 3년치로 내리는 게 아니라 5년치로 올린다 - 기간 안에 들어올 공급을
    빠뜨리는 쪽보다 넉넉히 세는 쪽이 안전하다.
    """
    if not horizon_years:
        return "supply_ratio_3y"
    for y in SUPPLY_HORIZONS:
        if y >= horizon_years:
            return f"supply_ratio_{y}y"
    return f"supply_ratio_{SUPPLY_HORIZONS[-1]}y"


def spec_for(model: str, horizon_years: int | None = None) -> list[tuple[str, bool]]:
    """모델의 입력 목록. 공급만 투자기간에 따라 지평이 달라진다.

    SPEC 을 그대로 쓰면 2년 투자와 5년 투자가 같은 3년치 공급을 본다. 그러면
    투자기간이 순위에 아무 영향도 주지 못한다(실측으로 30위까지 완전히 동일했다).
    """
    spec = SPEC[model]
    if model != "supply" or not horizon_years:
        return spec
    want = supply_key_for(horizon_years)
    return [(want if key.startswith("supply_ratio") else key, higher)
            for key, higher in spec]


def build_ranks(feature_sets: dict[int, FeatureSet]) -> dict[str, normalize.Ranked]:
    """후보 집단 전체에 대해 feature 별 상대 위치를 미리 계산한다.

    후보 하나씩 점수를 매기면 절대 임계값이 필요해진다. 집단으로 계산하면
    "이 후보군 안에서 몇 등인가" 만 보면 되고, 임계값을 지어낼 일이 없다.
    """
    keys: set[str] = set()
    for fs in feature_sets.values():
        keys |= set(fs.items)

    out: dict[str, normalize.Ranked] = {}
    for key in sorted(keys):
        higher = _higher_is_better(key)
        raw = {cid: (fs[key].value if fs[key].usable else None)
               for cid, fs in feature_sets.items()}
        clipped = normalize.winsorize(raw)
        ranked = normalize.percentile_rank(clipped, higher_is_better=higher)
        out[key] = normalize.Ranked(key, ranked.positions, ranked.missing, ranked.n)
    return out


def _higher_is_better(key: str) -> bool:
    for spec in SPEC.values():
        for name, higher in spec:
            if name == key:
                return higher
    return True


def score_one(model: str, complex_id: int, feature_set: FeatureSet,
              ranks: dict[str, normalize.Ranked],
              horizon_years: int | None = None) -> ModelScore:
    """모델 하나. 입력이 하나도 없으면 점수를 만들지 않는다."""
    if model not in SPEC:
        raise ValueError(f"모르는 모델: {model} (가능: {', '.join(SPEC)})")
    spec = spec_for(model, horizon_years)

    used: dict[str, float] = {}
    missing: list[str] = []
    confidences: list[float] = []

    for key, _ in spec:
        ranked = ranks.get(key)
        position = ranked.get(complex_id) if ranked else None
        if position is None:
            missing.append(key)
            continue
        used[key] = position
        confidences.append(feature_set[key].confidence)

    if len(used) < MIN_INPUTS:
        return ModelScore(model, None, 0.0, used, missing,
                          "입력이 없어 점수를 만들지 않았습니다 — 0 으로 채우지 않습니다")

    value = sum(used.values()) / len(used)
    confidence = min(confidences) if confidences else 0.0
    # 입력이 일부만 있으면 그만큼 덜 믿는다
    coverage = len(used) / len(spec)
    note = ("" if coverage == 1.0
            else f"입력 {len(used)}/{len(spec)}개로 계산 (없는 것: "
                 f"{', '.join(missing)})")
    return ModelScore(model, value, confidence * coverage, used, missing, note)


def score_all(complex_id: int, feature_set: FeatureSet,
              ranks: dict[str, normalize.Ranked],
              horizon_years: int | None = None) -> dict[str, ModelScore]:
    return {m: score_one(m, complex_id, feature_set, ranks, horizon_years)
            for m in SPEC}
