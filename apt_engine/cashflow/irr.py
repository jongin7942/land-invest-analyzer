"""NPV 와 IRR (요구사항 30).

총 수익률만 보면 기간이 사라진다. 5천만원을 2년에 벌었는지 10년에 벌었는지가
투자에서는 전부인데, "수익률 30%" 라는 말에는 그게 안 들어 있다.

IRR 은 미분 없이 **이분법**으로 푼다. 부동산 현금흐름은 부호가 여러 번 바뀔 수
있어(중간에 큰 지출) 뉴턴법이 발산하는 경우가 있고, 그때 조용히 이상한 값을
내놓는 것보다 "구할 수 없다" 고 말하는 편이 낫다.
"""
from __future__ import annotations

from typing import Sequence

# 탐색 범위. 연 −99% ~ +1000% 밖의 IRR 은 실무적으로 의미가 없다.
LOW, HIGH = -0.99, 10.0
TOLERANCE = 1e-7
MAX_ITER = 200


def npv(rate: float, flows: Sequence[float]) -> float:
    """flows[0] 은 t=0 (보통 음수), flows[i] 는 i년 말 현금흐름."""
    total = 0.0
    for t, cf in enumerate(flows):
        total += cf / ((1 + rate) ** t)
    return total


def irr(flows: Sequence[float]) -> float | None:
    """내부수익률. 못 구하면 None — 억지로 숫자를 만들지 않는다."""
    flows = list(flows)
    if len(flows) < 2:
        return None
    if all(cf >= 0 for cf in flows) or all(cf <= 0 for cf in flows):
        # 부호가 한 번도 안 바뀌면 IRR 이 존재하지 않는다.
        return None

    low, high = LOW, HIGH
    f_low, f_high = npv(low, flows), npv(high, flows)
    if f_low * f_high > 0:
        return None                      # 이 구간 안에 해가 없다

    for _ in range(MAX_ITER):
        mid = (low + high) / 2
        f_mid = npv(mid, flows)
        if abs(f_mid) < TOLERANCE or (high - low) < TOLERANCE:
            return mid
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


def payback_year(flows: Sequence[float]) -> int | None:
    """누적 현금흐름이 처음 0 이상이 되는 해. 원금 회수 시점."""
    total = 0.0
    for t, cf in enumerate(flows):
        total += cf
        if t > 0 and total >= 0:
            return t
    return None
