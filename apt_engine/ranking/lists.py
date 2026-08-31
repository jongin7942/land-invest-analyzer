"""세 가지 TOP10 (지시서 §48).

    Absolute        최대 기대수익
    Risk-adjusted   위험 대비 수익
    Asymmetric      하방 대비 상방

세 리스트 **모두** 상위에 등장하면 `Highest Conviction` 이다. 한 리스트에서만
1위인 후보는 그 축에서만 좋은 것이고, 그건 다른 축에서 나쁘다는 뜻일 수 있다.

세 리스트가 같은 점수를 다르게 정렬하는 게 아니다. **정렬 키가 다르다.**
같은 점수라도 하방이 얕은 후보가 Risk-adjusted 에서 올라오고, 상방이 큰 후보가
Asymmetric 에서 올라온다.
"""
from __future__ import annotations

from dataclasses import dataclass

from apt_engine.ranking.pipeline import Candidate

ABSOLUTE = "absolute"
RISK_ADJUSTED = "risk_adjusted"
ASYMMETRIC = "asymmetric"
KINDS = (ABSOLUTE, RISK_ADJUSTED, ASYMMETRIC)

# 세 리스트 모두에서 이 등수 안이면 Highest Conviction.
CONVICTION_RANK = 5


@dataclass(frozen=True)
class Entry:
    rank: int
    candidate: Candidate
    sort_value: float

    @property
    def complex_id(self) -> int:
        return self.candidate.complex_id


def _downside(c: Candidate) -> float:
    """하방 위험 0~1. 클수록 위험하다.

    Kill Score(관측된 위험 신호)와 하방방어 부족을 함께 본다.
    데이터가 없어 확인 못 한 위험은 **0 이 아니라 중립(0.5)** 으로 둔다 —
    모른다는 걸 안전하다고 읽으면 정확히 반대의 실수를 한다.
    """
    defense = c.features["downside_defense"]
    unknown_penalty = 0.5 if not defense.usable else (1.0 - defense.value)
    return min(1.0, 0.5 * c.kill.value + 0.5 * unknown_penalty)


def _upside(c: Candidate) -> float:
    """상방 0~1. 아직 반영되지 않은 알파 쪽 모델만 본다."""
    parts = []
    for model in ("catalyst", "value", "relative", "redevelopment"):
        score = c.consensus.scores.get(model)
        if score and score.known:
            parts.append(score.value)
    return sum(parts) / len(parts) if parts else 0.0


def sort_key(kind: str, c: Candidate) -> float:
    if kind == ABSOLUTE:
        return c.score
    if kind == RISK_ADJUSTED:
        # 같은 점수면 하방이 얕은 쪽. Thesis Survival 도 함께 본다(§23)
        return c.score * (1.0 - _downside(c)) * (0.5 + 0.5 * c.survival.value)
    if kind == ASYMMETRIC:
        # 하방 대비 상방. 하방이 0 에 가까우면 분모가 폭발하므로 바닥을 둔다
        return _upside(c) / max(0.15, _downside(c))
    raise ValueError(f"모르는 리스트 종류: {kind} (가능: {', '.join(KINDS)})")


def build(candidates: list[Candidate], kind: str, *, limit: int = 10) -> list[Entry]:
    """한 종류의 리스트. 동점은 complex_id 로 깬다 — 이름이 순위에 끼지 않게."""
    scored = [(sort_key(kind, c), c) for c in candidates]
    scored.sort(key=lambda pair: (-pair[0], pair[1].complex_id))
    return [Entry(i, c, v) for i, (v, c) in enumerate(scored[:limit], start=1)]


def all_lists(candidates: list[Candidate], *,
              limit: int = 10) -> dict[str, list[Entry]]:
    return {kind: build(candidates, kind, limit=limit) for kind in KINDS}


def highest_conviction(lists: dict[str, list[Entry]]) -> list[int]:
    """세 리스트 모두에서 상위에 든 후보 (§48)."""
    ranks: dict[int, list[int]] = {}
    for entries in lists.values():
        for e in entries:
            ranks.setdefault(e.complex_id, []).append(e.rank)
    return sorted(cid for cid, rs in ranks.items()
                  if len(rs) == len(lists) and max(rs) <= CONVICTION_RANK)


def explain(kind: str) -> str:
    return {
        ABSOLUTE: "최대 기대수익 — 점수만 본다",
        RISK_ADJUSTED: "위험 대비 수익 — 같은 점수면 하방이 얕고 논리가 여러 개인 쪽",
        ASYMMETRIC: "하방 대비 상방 — 잃을 게 적고 얻을 게 큰 쪽",
    }[kind]
