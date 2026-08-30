"""중복 매물 추정 (요구사항 5).

같은 집이 여러 중개업소에 올라온다. 매물 28건이 실제로는 19~22개 물건일 수 있고,
그걸 28건으로 세면 "매물이 많다 = 매수자 우위"라는 판단이 통째로 틀린다.

그런데 **정확한 중복 제거는 원리적으로 불가능하다.** 동·호수를 알 수 없고 층과 면적이
같은 다른 집이 실제로 존재하기 때문이다. 그래서 이 모듈은 확정하지 않고 범위를 낸다:

    원본 매물     28건
    중복 제거 추정 19~22건

하한은 "확실한 중복까지 다 묶었을 때", 상한은 "확실한 것만 묶었을 때"다.
"""
from __future__ import annotations

from dataclasses import dataclass

# 가격이 이 비율 안에서 다르면 같은 물건일 수 있다고 본다(중개업소마다 호가를 조금씩 다르게 올린다).
PRICE_TOLERANCE = 0.03


def _key_strict(row: dict) -> tuple | None:
    """동·층·면적·거래유형이 모두 같으면 거의 확실히 같은 집이다.

    동이 없으면 판정하지 않는다(None) — 동을 모르면 같은 층 다른 집일 수 있다.
    """
    if not row.get("dong") or row.get("floor") is None:
        return None
    return (row["trade_type"], row["dong"], row["floor"],
            round(float(row["exclusive_area_m2"]), 2))


def _key_loose(row: dict) -> tuple:
    """동을 몰라도 층·면적·거래유형·방향이 같으면 같은 물건 후보로 본다."""
    return (row["trade_type"], row.get("floor"),
            round(float(row["exclusive_area_m2"]), 2), row.get("direction") or "")


def _prices_close(a: int, b: int) -> bool:
    hi = max(a, b)
    return hi > 0 and abs(a - b) / hi <= PRICE_TOLERANCE


@dataclass(frozen=True)
class DedupeResult:
    raw_count: int
    unique_min: int          # 느슨하게 묶었을 때(중복을 최대한 제거)
    unique_max: int          # 확실한 것만 묶었을 때
    groups: dict[str, str]   # listing_key → 그룹키
    certain_duplicates: int  # 동·층·면적까지 같아 거의 확실한 중복 건수

    @property
    def range_label(self) -> str:
        if self.unique_min == self.unique_max:
            return f"{self.unique_min}건"
        return f"{self.unique_min}~{self.unique_max}건"

    @property
    def has_uncertainty(self) -> bool:
        return self.unique_min != self.unique_max


def estimate(rows: list[dict]) -> DedupeResult:
    """중복을 추정한다. 확정하지 않고 범위를 돌려준다."""
    if not rows:
        return DedupeResult(0, 0, 0, {}, 0)

    # ── 확실한 중복: 동·층·면적이 모두 같다 ──
    strict_groups: dict[tuple, list[dict]] = {}
    ungrouped: list[dict] = []
    for row in rows:
        key = _key_strict(row)
        if key is None:
            ungrouped.append(row)
        else:
            strict_groups.setdefault(key, []).append(row)

    group_of: dict[str, str] = {}
    for key, members in strict_groups.items():
        gid = f"S{abs(hash(key)) % 10**8}"
        for m in members:
            group_of[m["listing_key"]] = gid
    certain_dups = sum(len(v) - 1 for v in strict_groups.values())
    unique_max = len(strict_groups) + len(ungrouped)

    # ── 느슨한 중복: 동을 몰라도 층·면적·방향·가격대가 맞으면 같은 물건 후보 ──
    loose_groups: dict[tuple, list[dict]] = {}
    for row in rows:
        loose_groups.setdefault(_key_loose(row), []).append(row)

    unique_min = 0
    for members in loose_groups.values():
        unique_min += _count_price_clusters(members)

    # 느슨한 쪽이 더 많이 묶으므로 min <= max 여야 한다. 뒤집히면 보수적으로 맞춘다.
    unique_min = min(unique_min, unique_max)
    return DedupeResult(len(rows), unique_min, unique_max, group_of, certain_dups)


def _count_price_clusters(members: list[dict]) -> int:
    """가격이 서로 가까운 것끼리 하나로 묶었을 때 남는 개수."""
    if len(members) <= 1:
        return len(members)
    prices = sorted(int(m["price"]) for m in members)
    clusters = 1
    anchor = prices[0]
    for p in prices[1:]:
        if not _prices_close(anchor, p):
            clusters += 1
            anchor = p
    return clusters
