"""좌표 거리 계산.

**직선거리와 도보거리는 다른 값이다.** 하천·철로·언덕이 있으면 직선 400m 가
도보 15분이 되기도 한다. 그래서 계산 결과에 항상 방식을 붙여 저장하고,
직선거리를 "도보 N분 역세권"이라고 부르지 않는다(요구사항 14).
"""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0

# 직선거리를 도보시간으로 바꿀 때 쓰는 보수적 계수.
# 실제 보행경로는 직선보다 길다 — 도시부에서 통상 1.2~1.4배.
DETOUR_FACTOR = 1.3
WALK_M_PER_MIN = 67.0        # 성인 도보 약 4km/h


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 사이 직선거리(m)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def rough_walk_minutes(straight_m: float) -> int:
    """직선거리에서 **대략적인** 도보시간. 실측이 아니라 추정이다."""
    return max(1, round(straight_m * DETOUR_FACTOR / WALK_M_PER_MIN))


def has_coords(*values) -> bool:
    return all(v is not None for v in values)
