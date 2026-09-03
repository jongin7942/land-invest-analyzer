"""비교단지 자동선정 (요구사항 3).

**무작위로 비교하지 않는다.** 후보마다 왜 골랐는지를 항목별 점수로 남기고,
근거가 하나도 없으면 아예 고르지 않는다 — 억지로 3~5개를 채우느니 0개가 낫다.
근거 없는 비교단지는 그 위에 쌓는 가격비율·저평가 판정을 통째로 무의미하게 만든다.

선정 기준과 기본 가중치:

    사다리 인접   0.40   같은 가격사다리 축에서 위아래 칸. 가장 강한 근거다
    가격대        0.20   대표가격이 비슷한 급인가
    단지 규모     0.15   세대수가 비슷한가 (500세대와 5,000세대는 다른 물건이다)
    연식          0.15   준공연도가 비슷한가
    같은 시도     0.10   최소한의 생활권 근접성

사다리에 등록되지 않은 지역이면 사다리 점수가 0이 되고, 나머지만으로는 문턱값을
넘기 어렵게 해 뒀다. 즉 **사다리를 안 채우면 비교단지가 거의 안 잡힌다.** 의도한 것이다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from apt_engine import units
from apt_engine.relative import ladder
from apt_engine.trace import Calc, Evidence

DEFAULT_WEIGHTS: dict[str, float] = {
    "사다리인접": 0.32,
    "같은시군구": 0.18,
    "가격대": 0.20,
    "단지규모": 0.13,
    "연식": 0.12,
    "같은시도": 0.05,
}

# 이 점수 아래는 비교대상으로 삼지 않는다.
MIN_SIMILARITY = 0.35
DEFAULT_TOP_N = 5

# 사다리에서 몇 칸까지 이웃으로 볼 것인가.
LADDER_SPAN = 2

LADDER_EVIDENCE = Evidence(
    source="가격사다리 축 (수기 정의)",
    note="공공 데이터가 아니라 도메인 지식이다. rationale 과 curated_by 가 함께 저장된다.")


@dataclass(frozen=True)
class Candidate:
    complex_id: int
    name: str
    lawd_cd: str
    emd_name: str | None
    apt_households: int | None
    approval_year: int | None
    price: int | None
    sido: str | None


@dataclass(frozen=True)
class Pick:
    candidate: Candidate
    similarity: float
    reasons: dict[str, float]      # 항목 → 0~1 점수
    axis_id: int | None
    axis_name: str | None
    note: str

    @property
    def has_ground(self) -> bool:
        return any(v > 0 for v in self.reasons.values())


def _closeness(a: float | None, b: float | None, *, tolerance: float) -> float:
    """두 값이 얼마나 가까운가. tolerance 만큼 벌어지면 0."""
    if a is None or b is None or a <= 0 or b <= 0:
        return 0.0
    diff = abs(a - b) / max(a, b)
    return max(0.0, 1.0 - diff / tolerance)


def _year_closeness(a: int | None, b: int | None, *, tolerance: int = 15) -> float:
    if a is None or b is None:
        return 0.0
    return max(0.0, 1.0 - abs(a - b) / tolerance)


def score(target: Candidate, other: Candidate, *,
          ladder_hits: dict[str, tuple[int, str, int]],
          weights: dict[str, float] | None = None) -> Pick:
    """후보 하나에 대한 유사도와 근거.

    ladder_hits: 후보 지역키 → (거리, 축이름, 축id). 사다리에서 몇 칸 떨어졌는지.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    reasons: dict[str, float] = {}

    key = f"{other.lawd_cd}|{other.emd_name or ''}"
    key_any = f"{other.lawd_cd}|"
    hit = ladder_hits.get(key) or ladder_hits.get(key_any)
    axis_id = axis_name = None
    if hit:
        distance, axis_name, axis_id = hit
        # 바로 위아래(1칸)가 가장 강하고, 멀어질수록 약해진다.
        reasons["사다리인접"] = max(0.0, 1.0 - (distance - 1) / LADDER_SPAN) if distance else 1.0
    else:
        reasons["사다리인접"] = 0.0

    reasons["가격대"] = _closeness(target.price, other.price, tolerance=0.60)
    reasons["단지규모"] = _closeness(target.apt_households, other.apt_households,
                                    tolerance=1.20)
    reasons["연식"] = _year_closeness(target.approval_year, other.approval_year)
    # 같은 구 안의 비슷한 단지는 실재하는 비교대상이다. 사다리 축이 없을 때
    # 문턱을 넘게 해주는 것이 이 항목이고, 시군구가 다르면 0 이라 아무 단지나
    # 묶이지 않는다.
    reasons["같은시군구"] = 1.0 if target.lawd_cd == other.lawd_cd else 0.0
    reasons["같은시도"] = 1.0 if (target.sido and target.sido == other.sido) else 0.0

    similarity = sum(reasons[k] * w[k] for k in w)
    note = (f"{axis_name} 축에서 {'같은 칸' if hit and hit[0] == 0 else f'{hit[0]}칸 차이'}"
            if hit else "사다리 축에 없음 — 근거가 약함")
    return Pick(other, round(min(similarity, 1.0), 4), reasons, axis_id, axis_name, note)


def select(conn: sqlite3.Connection, target: Candidate, candidates: list[Candidate], *,
           top_n: int = DEFAULT_TOP_N, min_similarity: float = MIN_SIMILARITY,
           weights: dict[str, float] | None = None) -> list[Pick]:
    """비교단지 3~5개. 근거가 부족하면 그보다 적게, 또는 0개를 돌려준다."""
    ladder_hits = _ladder_hits(conn, target)

    picks = [score(target, c, ladder_hits=ladder_hits, weights=weights)
             for c in candidates if c.complex_id != target.complex_id]
    picks = [p for p in picks if p.has_ground and p.similarity >= min_similarity]
    picks.sort(key=lambda p: -p.similarity)
    return picks[:top_n]


def _ladder_hits(conn: sqlite3.Connection, target: Candidate) -> dict:
    """대상 단지가 속한 축들의 이웃 지역 → (거리, 축이름, 축id)."""
    hits: dict[str, tuple[int, str, int]] = {}
    for node in ladder.nodes_for_region(conn, target.lawd_cd, target.emd_name):
        for nb in ladder.neighbours(conn, node, span=LADDER_SPAN):
            if not nb.lawd_cd:
                continue          # 코드가 안 채워진 노드는 매칭에 쓰지 않는다
            key = f"{nb.lawd_cd}|{nb.emd_name or ''}"
            distance = abs(nb.rank - node.rank)
            if key not in hits or distance < hits[key][0]:
                hits[key] = (distance, nb.axis_name, nb.axis_id)
    return hits


def to_calc(target: Candidate, pick: Pick, *, engine_weights: dict | None = None) -> Calc:
    """선정 근거를 Calc 로. 이게 그대로 selection_reason_json 과 calc_trace 가 된다."""
    w = {**DEFAULT_WEIGHTS, **(engine_weights or {})}
    return Calc(
        value=pick.similarity, unit="ratio",
        formula="Σ(항목점수 × 가중치)",
        inputs={
            "대상": f"{target.name} ({target.lawd_cd})",
            "비교대상": f"{pick.candidate.name} ({pick.candidate.lawd_cd})",
            "선정근거": pick.note,
        },
        intermediates={
            "항목점수": {k: round(v, 3) for k, v in pick.reasons.items()},
            "가중치": w,
            "기여도": {k: round(v * w[k], 4) for k, v in pick.reasons.items()},
            "가격": {
                "대상": units.fmt_eok(target.price) if target.price else "확인 불가",
                "비교대상": (units.fmt_eok(pick.candidate.price)
                          if pick.candidate.price else "확인 불가"),
            },
        },
        evidence=(LADDER_EVIDENCE,) if pick.axis_id else (),
        # 유사도 가중치는 우리가 정한 가정이다. 관측된 사실이 아니다.
        grade="ESTIMATED",
    )
