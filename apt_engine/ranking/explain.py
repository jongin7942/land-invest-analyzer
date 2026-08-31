"""설명 — 왜 이 아파트인가, 그리고 왜 다른 아파트가 아닌가 (지시서 §63·§75·§76).

지시서가 요구한 것은 두 가지다.

> §75 "왜 이 아파트인가?" 뿐 아니라 "왜 다른 아파트가 아닌가?" 를 설명해야 한다.
> §63 WHY BUY 상위 3개 / WHY NOT 상위 3개 / 시장이 반영한 것 / 반영 안 된 것

점수가 **가중합**이라 기여도가 정확히 쪼개진다. SHAP 같은 근사가 필요 없다 —
가법 모델에서는 각 항의 기여가 그 자체로 정확한 답이다.
"""
from __future__ import annotations

from apt_engine.features.base import FeatureSet
from apt_engine.ranking.pipeline import Candidate
from apt_engine.scoring import consensus as consensus_mod

# 모델 이름 → 사람이 읽는 말
MODEL_LABEL = {
    "value": "지금 가격이 싸다",
    "momentum": "최근 흐름이 좋다",
    "supply": "공급 부담이 적다",
    "catalyst": "아직 반영 안 된 호재가 있다",
    "redevelopment": "재건축 사업성이 있다",
    "relative": "비교단지 대비 싸다",
    "jeonse": "전세가 하방을 받친다",
    "risk": "거래 신호가 건전하다",
    "capital_efficiency": "적은 돈으로 큰 자산을 잡는다",
}


def why_buy(c: Candidate, *, limit: int = 3) -> list[dict]:
    """점수를 만든 이유 상위 N개."""
    out = []
    for model, share in c.consensus.top_drivers[:limit]:
        score = c.consensus.scores.get(model)
        if not score or not score.known:
            continue
        out.append({
            "이유": MODEL_LABEL.get(model, model),
            "모델": model,
            "기여": f"{share:.0%}",
            "모델 점수": f"{score.value:.2f}",
            "근거 feature": {k: f"{v:.2f}" for k, v in score.used.items()},
        })
    return out


def why_not(c: Candidate, *, limit: int = 3) -> list[dict]:
    """사지 말아야 할 이유 상위 N개 (§43 Why Not).

    Kill 에 걸린 위험이 먼저고, 그다음이 점수가 낮은 모델이다.
    **하나도 없으면 '없음' 이 아니라 '확인된 위험 없음' 이다** — 확인 못 한 위험이
    몇 개인지 함께 말한다.
    """
    out = [{"이유": h.reason, "설명": h.why,
            "값": f"{h.feature_key} = {h.value:.2f}"} for h in c.kill.hits[:limit]]
    if len(out) < limit:
        weak = sorted((m for m, s in c.consensus.scores.items() if s.known),
                      key=lambda m: c.consensus.scores[m].value)
        for model in weak:
            if len(out) >= limit:
                break
            score = c.consensus.scores[model]
            if score.value >= 0.4:
                break
            out.append({"이유": f"{MODEL_LABEL.get(model, model)} — 아님",
                        "설명": f"{model} 모델 점수 {score.value:.2f} (하위권)",
                        "값": ""})
    if c.survival.fragile:
        out.append({"이유": "논리가 하나뿐",
                    "설명": f"'{c.survival.removed}' 를 빼면 점수가 "
                          f"{c.survival.before:.0f} → {c.survival.after:.0f} 로 떨어집니다",
                    "값": f"Thesis Survival {c.survival.value:.0%}"})
    return out[:limit + 1]


def what_market_prices(features: FeatureSet) -> dict:
    """시장이 이미 반영한 것 / 아직 반영 안 된 것 (§63)."""
    priced, unpriced = [], []

    catalyst = features["catalyst_alpha"]
    if catalyst.usable:
        unpriced.append(f"남은 호재 알파 {catalyst.label}")
    elif catalyst.known:
        unpriced.append("호재 알파가 있으나 신뢰도가 낮습니다")
    else:
        unpriced.append("호재 데이터 없음 — 반영 여부를 알 수 없습니다")

    lag = features["discovery_lag"]
    if lag.usable and lag.value > 0.3:
        priced.append(f"최근 상승이 이미 진행됐습니다 (discovery_lag {lag.value:.2f})")

    entry = features["entry_position"]
    if entry.usable:
        where = entry.detail.get("판정", "")
        (priced if entry.value > 0.6 else unpriced).append(f"매수가 구간: {where}")

    return {
        "시장이 반영한 것": priced or ["확인된 것 없음"],
        "아직 반영 안 된 것": unpriced or ["확인된 것 없음"],
        "주의": "'반영 안 됨' 은 데이터가 없다는 뜻일 수도 있습니다. "
              "신뢰도를 함께 보세요",
    }


def why_a_over_b(a: Candidate, b: Candidate) -> dict:
    """§75 — A 가 B 보다 높은 이유를 항별로."""
    diff = consensus_mod.explain_pair(a.consensus, b.consensus)
    top = list(diff["항목별 기여 차"].items())[:3]
    diff["요약"] = [
        f"{MODEL_LABEL.get(m, m)}: {v:+.1f}점" for m, v in top]
    return diff


def full_report(c: Candidate) -> dict:
    """§63 단지 상세화면의 재료."""
    return {
        "점수": f"{c.score:.0f}",
        "신뢰도": f"{c.confidence:.0f}",
        "모델 일치도": f"{c.consensus.agreement:.0%}",
        "WHY BUY": why_buy(c),
        "WHY NOT": why_not(c),
        **what_market_prices(c.features),
        "Kill Score": c.kill.label,
        "Thesis Survival": c.survival.label,
        "실투자금": (c.capital.label if c.capital else "확인 불가"),
        "데이터 커버리지": f"{c.features.coverage:.0%}",
        "빠진 feature": c.features.missing_keys or "없음",
    }
