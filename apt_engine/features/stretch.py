"""STRETCH — 기대가 이미 가격에 반영되었는가 (신규 지시서 §4-D·§5·§6).

§5 가 금지한 것이 이 모듈의 출발점이다.

> 단순 전고점 대비 하락률을 저평가로 사용하지 않는다.

전고점이 높았다는 것은 **그때 과열이었다는 뜻**일 수 있다. 2021년 고점 대비
-30% 라는 사실만으로 싸다고 하면, 2021년의 과열을 정상가격으로 인정하는 것이다.

그래서 기준점을 전고점이 아니라 **장기 정상가격**으로 잡는다.

    PriceStretch = (현재 정규화가격 − 장기 정상가격) / 장기 정상가격

장기 정상가격은 단순 과거 평균이 아니라, 가능한 범위에서 보정한다.

    ① 장기 추세      — 그 단지가 원래 오르던 속도
    ② 지역 Beta      — 시군구 전체가 오른 만큼은 그 단지의 실력이 아니다
    ③ 생활권 Beta    — 더 좁은 범위에서 같은 보정
    ④ 상품 Quality   — 연식이 드는 것은 정상가격을 낮춘다

보정에 쓸 데이터가 없으면 **보정하지 않고 그 사실을 남긴다.** 없는 Beta 를
1.0 으로 가정하면 "보정했다" 는 거짓말이 된다.

§6 은 가속도를 선형으로 쓰지 말라고 한다.

    Dormant → Emerging → Confirmation → Overheated

가장 높은 Alpha 는 Emerging~초기 Confirmation 에 있다는 것이 **가설**이고,
Extreme Acceleration 은 오히려 Stretch 를 높인다. 그래서 원시 가속도를 그대로
가산하지 않고 **구간(zone)** 으로 바꾼다.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from apt_engine.features import bands as bands_mod
from apt_engine.features.base import (Feature, Status, combine,
                                      sample_confidence)
from apt_engine.trace import Calc

# 장기 정상가격을 낼 최소 관측 개월. 짧으면 '장기' 가 성립하지 않는다.
MIN_MONTHS_FOR_NORMAL = 24

# §6 가속도 구간. **관측이 아니라 판정 기준**이라 백테스트가 대체한다.
ZONE_DORMANT = "DORMANT"
ZONE_EMERGING = "EMERGING"
ZONE_CONFIRMATION = "CONFIRMATION"
ZONE_OVERHEATED = "OVERHEATED"
ZONES = (ZONE_DORMANT, ZONE_EMERGING, ZONE_CONFIRMATION, ZONE_OVERHEATED)

# 구간 경계 — 6개월 변화율 기준
ZONE_EDGES = ((0.02, ZONE_DORMANT), (0.08, ZONE_EMERGING),
              (0.20, ZONE_CONFIRMATION))

# 구간별 '남은 알파' 가설. 역U 다 — 양 끝이 낮고 가운데가 높다.
# 이 숫자는 **가설이지 관측이 아니다.** §21 백테스트가 학습으로 대체한다.
ZONE_ALPHA = {
    ZONE_DORMANT: 0.35,        # 아직 아무 일도 안 일어남 — 언제 올지 모른다
    ZONE_EMERGING: 1.00,       # 막 움직이기 시작 — 가설상 최고
    ZONE_CONFIRMATION: 0.70,   # 확인됐지만 이미 일부 반영
    ZONE_OVERHEATED: 0.10,     # 남은 게 거의 없다
}
ZONE_NOTE = ("구간별 알파는 §6 의 가설이지 관측이 아닙니다. "
             "Walk-Forward 백테스트가 이 값을 대체합니다")

RUNUP_WINDOWS = {"runup_1y": 12, "runup_2y": 24, "runup_3y": 36}


@dataclass(frozen=True)
class NormalPrice:
    """장기 정상가격과 그것을 어떻게 냈는지."""
    value: float | None
    months: int
    adjustments: dict[str, float] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        tail = (f" (보정 {len(self.adjustments)}개"
                + (f", 못한 보정 {len(self.skipped)}개" if self.skipped else "")
                + ")")
        return f"{self.value:,.0f}원{tail}"


def historical_normal(series: bands_mod.BandSeries, *,
                      region_beta: float | None = None,
                      lifezone_beta: float | None = None,
                      quality_decay: float | None = None,
                      months: int = 60) -> NormalPrice:
    """장기 정상가격 (§5).

    `region_beta` 지역 전체가 그 기간 오른 배율. 주면 그만큼 나눠서
                  "지역이 올라서 오른 부분" 을 뺀다.
    `quality_decay` 연식이 들면서 정상가격이 낮아지는 배율.

    셋 다 선택이다. **주지 않으면 보정하지 않고 그 사실을 `skipped` 에 남긴다.**
    """
    usable = series.usable
    if len(usable) < MIN_MONTHS_FOR_NORMAL:
        return NormalPrice(None, len(usable), reason=(
            f"관측이 {len(usable)}개월뿐입니다(최소 {MIN_MONTHS_FOR_NORMAL}개월). "
            f"장기 정상가격을 만들지 않습니다"))

    window = usable[:months]
    prices = [p.p50 for p in window if p.p50]
    if len(prices) < MIN_MONTHS_FOR_NORMAL:
        return NormalPrice(None, len(prices), reason="중앙값이 있는 달이 모자랍니다")

    # 추세 보정: 단순 평균이 아니라 **추세선의 현재 위치**를 정상가로 본다.
    # 오래 우상향한 단지의 과거 평균을 정상가로 쓰면 항상 '고평가' 로 나온다.
    base = _trend_now(prices)

    adjustments: dict[str, float] = {}
    skipped: list[str] = []
    value = base

    if region_beta is not None and region_beta > 0:
        value /= region_beta
        adjustments["지역 Beta"] = region_beta
    else:
        skipped.append("지역 Beta")

    if lifezone_beta is not None and lifezone_beta > 0:
        value /= lifezone_beta
        adjustments["생활권 Beta"] = lifezone_beta
    else:
        skipped.append("생활권 Beta")

    if quality_decay is not None and quality_decay > 0:
        value *= quality_decay
        adjustments["상품 Quality"] = quality_decay
    else:
        skipped.append("상품 Quality(연식)")

    return NormalPrice(value, len(prices), adjustments, skipped)


def _trend_now(prices: list[int]) -> float:
    """시계열의 추세선이 지금 가리키는 값. `prices` 는 최신순.

    최소제곱 직선을 파이썬만으로 낸다. 표본이 적으면 평균으로 물러난다.
    """
    n = len(prices)
    if n < 3:
        return statistics.mean(prices)
    ordered = list(reversed(prices))          # 과거 → 현재
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ordered) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return my
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ordered)) / denom
    return my + slope * (n - 1 - mx)


def price_stretch(series: bands_mod.BandSeries, normal: NormalPrice) -> Feature:
    """현재가가 장기 정상가에서 얼마나 벗어났나 (§5).

    양수 = 정상보다 비싸다(감점), 음수 = 정상보다 싸다.
    **전고점 대비가 아니다.**
    """
    if not normal.known:
        return Feature.missing("price_stretch", normal.reason)
    latest = series.latest
    if latest is None or not latest.p50:
        return Feature.missing("price_stretch", "현재 중앙값이 없습니다")

    value = (latest.p50 - normal.value) / normal.value
    conf = combine(sample_confidence(normal.months, full_at=36),
                   # 보정을 많이 못 했으면 덜 믿는다
                   sample_confidence(len(normal.adjustments), full_at=3) * 0.5 + 0.5)
    detail = {
        "현재 중앙값": f"{latest.p50:,}원",
        "장기 정상가": normal.label,
        "해석": ("정상가보다 비쌉니다" if value > 0 else "정상가보다 쌉니다"),
        "주의": "전고점 대비 하락률이 아닙니다. 전고점은 과열이었을 수 있습니다(§5)",
    }
    if normal.skipped:
        detail["못한 보정"] = ", ".join(normal.skipped)
    return Feature(
        "price_stretch", value, "비율", conf, Status.OK, detail,
        Calc(value=value, unit="비율",
             formula="(현재 정규화가격 − 장기 정상가격) ÷ 장기 정상가격",
             intermediates={"현재": latest.p50, "정상가": normal.value,
                            "보정": normal.adjustments},
             grade="ESTIMATED" if normal.skipped else "CONFIRMED"))


def runup_features(series: bands_mod.BandSeries) -> list[Feature]:
    """1Y·2Y·3Y 상승폭 (§4-D). 높을수록 STRETCH 가 크다 = 감점."""
    out: list[Feature] = []
    for key, months in RUNUP_WINDOWS.items():
        value, why = bands_mod._band_change(series, months, lambda p: p.p50)
        if value is None:
            out.append(Feature.missing(key, why))
            continue
        out.append(Feature(
            key, value, "비율", sample_confidence(len(series.usable), full_at=months),
            Status.OK,
            {"기간": f"{months}개월",
             "주의": "많이 오른 것은 가점이 아니라 감점입니다(§4-D·§39)"},
            Calc(value=value, unit="비율",
                 formula=f"({months}개월 상승폭)", grade="CONFIRMED")))
    return out


def acceleration_zone(series: bands_mod.BandSeries,
                      slopes: bands_mod.Slopes) -> Feature:
    """가속도를 역U 로 (§6).

    선형 가산이면 "많이 오를수록 좋다" 가 되어 상투를 잡는다. 그래서 구간으로
    바꾸고, 구간마다 **남은 알파**를 다르게 본다. 가장 높은 자리는 Emerging 이다.

    반환값은 '남은 알파' 가 아니라 **STRETCH 값**이다 — `1 − 남은알파` 로 두어
    등록부의 `higher_is_better=False` 와 방향을 맞춘다.
    """
    six = slopes.values.get(6)
    if six is None:
        return Feature.missing(
            "acceleration_zone",
            "6개월 기울기가 없어 가속 구간을 판정할 수 없습니다")

    zone = ZONE_OVERHEATED
    for edge, name in ZONE_EDGES:
        if six < edge:
            zone = name
            break

    remaining = ZONE_ALPHA[zone]
    value = 1.0 - remaining          # STRETCH: 높을수록 나쁘다

    three = slopes.values.get(3)
    extreme = three is not None and three >= 0.15
    if extreme and zone != ZONE_OVERHEATED:
        # §6 "Extreme Acceleration 은 오히려 Stretch/Entry Risk 를 높인다"
        value = min(1.0, value + 0.25)

    return Feature(
        "acceleration_zone", value, "0~1",
        sample_confidence(len(slopes.values), full_at=4), Status.OK,
        {"구간": zone,
         "6개월 기울기": f"{six:+.1%}",
         "남은 알파(가설)": f"{remaining:.2f}",
         "Extreme 가속": ("예 — Stretch 를 더 올렸습니다" if extreme else "아니오"),
         "주의": ZONE_NOTE},
        Calc(value=value, unit="0~1",
             formula="1 − 구간별 남은알파 (역U). 선형 가산이 아닙니다(§6)",
             intermediates={"구간": zone, "6M": six, "3M": three},
             grade="SCENARIO"))


def price_percentile(series: bands_mod.BandSeries) -> Feature:
    """자기 역사 안에서 지금 가격이 몇 번째인가 (§4-D).

    100% 에 가까우면 역사상 최고가 근처다. 이건 저평가의 반대다.
    """
    usable = series.usable
    if len(usable) < MIN_MONTHS_FOR_NORMAL:
        return Feature.missing(
            "price_percentile",
            f"관측이 {len(usable)}개월뿐이라 역사적 위치를 낼 수 없습니다")
    prices = [p.p50 for p in usable if p.p50]
    now = prices[0]
    below = sum(1 for p in prices if p < now)
    value = below / len(prices)
    return Feature(
        "price_percentile", value, "0~1",
        sample_confidence(len(prices), full_at=36), Status.OK,
        {"해석": f"과거 {len(prices)}개월 중 {below}개월보다 비쌉니다",
         "주의": "높을수록 감점입니다 — 역사상 최고가 근처라는 뜻입니다"},
        Calc(value=value, unit="0~1",
             formula="현재보다 싼 달의 수 ÷ 관측 개월",
             intermediates={"관측 개월": len(prices)}, grade="CONFIRMED"))
