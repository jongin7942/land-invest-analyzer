"""정규화 — 절대 임계값을 쓰지 않는다.

"상승률 10% 이상이면 만점" 같은 기준은 두 가지로 틀린다.
  1. 시장 전체가 20% 오른 해에는 모두가 만점이 된다
  2. 그 임계값이 어디서 왔는지 설명할 수 없다

그래서 **후보 집단 안에서의 상대 위치**(백분위)를 쓴다. 같은 시점 같은 후보군
안에서 몇 등인가만 본다. 이러면 시장 전체가 오르든 내리든 비교가 유지되고,
임계값을 지어낼 필요도 없다.

값이 없는 후보는 **0점이 아니라 제외**된다. 0점을 주면 데이터가 없다는 이유로
꼴찌가 되고, 그건 "모른다" 와 "나쁘다" 를 섞는 것이다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ranked:
    """한 지표에 대한 후보들의 상대 위치."""
    key: str
    positions: dict[int, float]        # complex_id → 0~1 백분위
    missing: list[int]                 # 값이 없어 순위를 못 매긴 후보
    n: int

    def get(self, complex_id: int) -> float | None:
        return self.positions.get(complex_id)


def percentile_rank(values: dict[int, float | None], *,
                    higher_is_better: bool = True) -> Ranked:
    """값 → 0~1 백분위. 같은 값은 같은 위치(평균 순위)를 받는다."""
    known = {cid: v for cid, v in values.items() if v is not None}
    missing = sorted(cid for cid, v in values.items() if v is None)
    if not known:
        return Ranked("", {}, missing, 0)
    if len(known) == 1:
        # 후보가 하나면 비교가 성립하지 않는다. 중립(0.5)으로 둔다.
        return Ranked("", {next(iter(known)): 0.5}, missing, 1)

    ordered = sorted(known.items(), key=lambda kv: kv[1])
    positions: dict[int, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        # 동점 구간의 평균 위치
        rank = (i + j) / 2.0
        pos = rank / (len(ordered) - 1)
        for k in range(i, j + 1):
            positions[ordered[k][0]] = pos if higher_is_better else 1.0 - pos
        i = j + 1
    return Ranked("", positions, missing, len(known))


def winsorize(values: dict[int, float | None], *, low: float = 0.02,
              high: float = 0.98) -> dict[int, float | None]:
    """극단값을 분위수로 잘라낸다.

    한 단지의 이상한 값 하나가 나머지 전부의 상대 위치를 밀어내는 걸 막는다.
    **값을 버리지 않고 경계로 옮긴다** — 버리면 그 후보가 사라진다.
    """
    known = sorted(v for v in values.values() if v is not None)
    if len(known) < 5:
        return dict(values)
    lo = known[max(0, int(len(known) * low))]
    hi = known[min(len(known) - 1, int(len(known) * high))]
    return {cid: (None if v is None else min(max(v, lo), hi))
            for cid, v in values.items()}
