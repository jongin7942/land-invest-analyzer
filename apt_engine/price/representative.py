"""Representative Price — 이 단지·이 면적의 "지금 가격".

요구사항 2의 핵심: **단일 최고가 거래를 현재가격으로 사용하지 않는다.**

    최근 84㎡ 정상거래  10.8  11.0  11.1  11.2  11.3  14.0 (억)
    → 14억 하나 때문에 현재가격을 14억으로 잡으면 안 된다.

중앙값을 기본으로 쓴다. 위 예에서 중앙값은 11.05억이고, 14억은 앞단
(`outlier.py`)에서 이상치로 이미 빠진다. 두 겹으로 막는 셈이다.

신뢰도는 표본 수로 정한다. 요구사항 2가 준 기준 그대로:
    HIGH   정상거래 10건 이상
    MEDIUM 3~9건
    LOW    1~2건

신뢰도와 등급(grade)은 다른 축이다. 1~2건으로 낸 가격도 **실거래에서 나온 값**이라
등급은 CONFIRMED 이고, 다만 신뢰도가 LOW 다. 추정으로 만든 값(ESTIMATED)과
표본이 적은 실측값(CONFIRMED·LOW)을 섞으면 안 된다.
"""
from __future__ import annotations

import statistics

from apt_engine import units

MEDIAN = "median"
TRIMMED_MEAN = "trimmed_mean"

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"

# 요구사항 2의 신뢰도 기준
HIGH_MIN_SAMPLES = 10
MEDIUM_MIN_SAMPLES = 3

# 절사평균을 쓸 때 양끝에서 덜어내는 비율
DEFAULT_TRIM = 0.1
# 절사평균은 표본이 이만큼은 돼야 의미가 있다(10건에 10%면 양끝 1건씩).
MIN_FOR_TRIM = 10


def confidence_of(sample_n: int) -> str | None:
    """표본 수 → HIGH / MEDIUM / LOW. 0건이면 None(가격을 내지 않는다)."""
    if sample_n <= 0:
        return None
    if sample_n >= HIGH_MIN_SAMPLES:
        return HIGH
    if sample_n >= MEDIUM_MIN_SAMPLES:
        return MEDIUM
    return LOW


def median_price(values: list[int]) -> int:
    """중앙값. 짝수 개면 가운데 두 값의 평균이라 원 단위로 반올림한다.

    파이썬 내장 round() 대신 units.won_round() 를 쓴다 — 내장 round 는 은행가 반올림이라
    5.5 를 6으로, 2.5 를 2로 만든다. 금액 반올림 규칙이 모듈마다 다르면 안 된다.
    """
    if not values:
        raise ValueError("표본이 비어 있습니다")
    return int(units.won_round(statistics.median(values)))


def trimmed_mean(values: list[int], trim: float = DEFAULT_TRIM) -> int:
    """양끝을 trim 비율만큼 덜어낸 평균.

    표본이 MIN_FOR_TRIM 미만이면 덜어낼 게 없어 그냥 평균이 된다 —
    그건 이상치에 취약하므로, 그 경우 호출부가 중앙값을 쓰도록 `pick_method` 가 막는다.
    """
    if not values:
        raise ValueError("표본이 비어 있습니다")
    ordered = sorted(values)
    k = int(len(ordered) * trim)
    core = ordered[k:len(ordered) - k] if k else ordered
    return int(units.won_round(statistics.fmean(core)))


def pick_method(sample_n: int, preferred: str = MEDIAN) -> str:
    """표본 수에 맞는 산출 방식. 절사평균은 표본이 충분할 때만 허용한다."""
    if preferred == TRIMMED_MEAN and sample_n >= MIN_FOR_TRIM:
        return TRIMMED_MEAN
    return MEDIAN


def compute(values: list[int], *, preferred: str = MEDIAN) -> tuple[int, str]:
    """(대표가격, 사용한 방식)."""
    method = pick_method(len(values), preferred)
    value = trimmed_mean(values) if method == TRIMMED_MEAN else median_price(values)
    return value, method


def quartiles(values: list[int]) -> dict[str, int]:
    """분포 요약. 대표가격 하나만 보여주면 그 값이 얼마나 흔들리는지 알 수 없다."""
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        only = ordered[0]
        return {"p25": only, "p50": only, "p75": only, "min": only, "max": only}
    return {
        "p25": int(units.won_round(_percentile(ordered, 0.25))),
        "p50": int(units.won_round(statistics.median(ordered))),
        "p75": int(units.won_round(_percentile(ordered, 0.75))),
        "min": ordered[0],
        "max": ordered[-1],
    }


def _percentile(ordered: list[int], q: float) -> float:
    """선형보간 백분위수."""
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac
