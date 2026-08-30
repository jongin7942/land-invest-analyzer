"""전용면적 밴드 — "서로 다른 면적의 가격을 섞어서 비교하지 말 것"의 구현.

요구사항 26-4: 84㎡ 가격 분석에 59/74/101㎡ 거래를 섞지 않는다.
요구사항 1: 기본 비교면적은 **전용 80~85㎡**(국민평형)이다.

같은 '84타입'도 단지마다 84.92 / 84.97 / 84.99 로 제각각이라 실수(實數)로는 묶이지
않는다. 그래서 전용면적을 밴드로 정규화하고, **가격 분석의 모든 조회는 밴드 단위로**
한다. 단지 전체 평균가는 아예 만들지 않는다.

밴드 경계는 실제 평형 군집을 따른다. 특히 `84` 밴드는 사용자 지정대로 80~85 이고
85~90(35평형대)은 `88` 로 분리한다 — 이 둘을 합치면 국민평형 비교가 오염된다.
"""
from __future__ import annotations

# (하한 포함, 상한 미포함, 밴드코드, 표시명)
BANDS: tuple[tuple[float, float, str, str], ...] = (
    (0.0,   40.0,  "u40", "40㎡ 미만"),
    (40.0,  50.0,  "45",  "40~50㎡"),
    (50.0,  60.0,  "59",  "50~60㎡ (전용 59)"),
    (60.0,  70.0,  "66",  "60~70㎡"),
    (70.0,  80.0,  "74",  "70~80㎡ (전용 74)"),
    (80.0,  85.0,  "84",  "80~85㎡ (국민평형)"),
    (85.0,  90.0,  "88",  "85~90㎡"),
    (90.0,  100.0, "97",  "90~100㎡"),
    (100.0, 110.0, "101", "100~110㎡"),
    (110.0, 120.0, "114", "110~120㎡"),
    (120.0, 135.0, "129", "120~135㎡"),
    (135.0, 165.0, "145", "135~165㎡"),
    (165.0, float("inf"), "o165", "165㎡ 이상"),
)

# 기본 비교면적. 사용자가 필터로 바꿀 수 있다(요구사항 1).
DEFAULT_BAND = "84"

_BY_CODE = {code: (lo, hi, label) for lo, hi, code, label in BANDS}


class AreaBandError(ValueError):
    pass


def band_of(exclusive_area_m2: float) -> str:
    """전용면적(㎡) → 밴드 코드. 0 이하는 에러 — 조용히 넘기면 엉뚱한 밴드에 섞인다."""
    if exclusive_area_m2 is None or exclusive_area_m2 <= 0:
        raise AreaBandError(f"전용면적이 유효하지 않습니다: {exclusive_area_m2!r}")
    for lo, hi, code, _ in BANDS:
        if lo <= exclusive_area_m2 < hi:
            return code
    raise AreaBandError(f"밴드를 찾지 못했습니다: {exclusive_area_m2}")  # pragma: no cover


def label_of(band: str) -> str:
    if band not in _BY_CODE:
        raise AreaBandError(f"알 수 없는 밴드: {band!r}")
    return _BY_CODE[band][2]


def range_of(band: str) -> tuple[float, float]:
    if band not in _BY_CODE:
        raise AreaBandError(f"알 수 없는 밴드: {band!r}")
    lo, hi, _ = _BY_CODE[band]
    return lo, hi


def all_bands() -> list[str]:
    return [code for _, _, code, _ in BANDS]


def same_band(*areas: float) -> bool:
    """가격 비교 전 방어. 여러 면적이 한 밴드에 속하는지 확인한다."""
    if not areas:
        return True
    bands = {band_of(a) for a in areas}
    return len(bands) == 1
