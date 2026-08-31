"""시장 국면 Regime Engine (지시서 §8).

> 시장국면마다 factor weight 가 달라져야 한다.
> 고정 weight 하나로 모든 시장을 평가하지 않는다.

국면은 **단지가 아니라 시장(시군구·시도)** 의 성질이다. 그래서 이 모듈은
단지가 아니라 지역 단위로 계산하고, 같은 시점의 모든 후보가 같은 국면을 본다.

7국면을 무엇으로 가르나: 가격 변화율(장·단기) · 가속도 · 거래량 변화 세 가지다.
경계값은 **관측된 분포가 아니라 판정 기준**이라 `THRESHOLDS` 에 모아 두고,
Feature 의 근거에 "백테스트가 대체한다" 고 적는다. 지시서 §74 가 허용하는
heuristic 의 범위(= discovery 용)를 넘지 않기 위해서다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features.base import Feature, Status, sample_confidence
from apt_engine.trace import Calc

# 뒤로 갈수록 뜨겁다. 숫자는 순서일 뿐 크기가 아니다.
REGIMES = ("침체", "바닥형성", "회복초기", "상승초기", "상승확산", "과열", "하락전환")
REGIME_INDEX = {r: i for i, r in enumerate(REGIMES)}

# 판정 기준. **관측치가 아니라 가정**이다. 백테스트가 이 값을 대체한다.
THRESHOLDS = {
    "flat": 0.02,          # ±2% 안이면 보합으로 본다
    "strong": 0.08,        # 12개월 8% 이상이면 강한 상승
    "hot": 0.20,           # 12개월 20% 이상이면 과열 의심
    "volume_up": 1.30,     # 거래량이 직전 동기 대비 1.3배면 증가
    "volume_down": 0.70,
}

THRESHOLD_NOTE = ("국면 경계값은 관측된 분포가 아니라 판정 기준입니다. "
                  "백테스트(§55)가 이 값을 대체합니다")


@dataclass(frozen=True)
class Regime:
    name: str
    price_12m: float | None
    price_3m: float | None
    volume_ratio: float | None
    sample_n: int
    reasons: list[str]

    @property
    def index(self) -> int:
        return REGIME_INDEX.get(self.name, -1)

    @property
    def rising(self) -> bool:
        return self.name in ("회복초기", "상승초기", "상승확산", "과열")


def classify(*, price_12m: float | None, price_3m: float | None,
             volume_ratio: float | None) -> tuple[str, list[str]]:
    """세 지표로 국면 하나. 모르는 게 있으면 아는 것만으로 판정하고 그 사실을 남긴다."""
    t = THRESHOLDS
    reasons: list[str] = []

    if price_12m is None:
        return "확인 불가", ["12개월 가격 변화율이 없어 국면을 정할 수 없습니다"]

    flat, strong, hot = t["flat"], t["strong"], t["hot"]
    up_3m = price_3m is not None and price_3m > flat
    down_3m = price_3m is not None and price_3m < -flat
    vol_up = volume_ratio is not None and volume_ratio >= t["volume_up"]
    vol_down = volume_ratio is not None and volume_ratio <= t["volume_down"]

    reasons.append(f"12개월 {price_12m:+.1%}")
    if price_3m is not None:
        reasons.append(f"3개월 {price_3m:+.1%}")
    if volume_ratio is not None:
        reasons.append(f"거래량 {volume_ratio:.2f}배")

    # 하락 중
    if price_12m < -flat:
        if up_3m:
            reasons.append("장기 하락 · 단기 반등 → 바닥형성")
            return "바닥형성", reasons
        reasons.append("장기·단기 모두 하락 → 침체")
        return "침체", reasons

    # 보합
    if abs(price_12m) <= flat:
        if up_3m and vol_up:
            reasons.append("보합 · 단기 상승 · 거래 증가 → 회복초기")
            return "회복초기", reasons
        if down_3m:
            reasons.append("보합인데 단기 하락 → 하락전환")
            return "하락전환", reasons
        reasons.append("장기 보합 · 뚜렷한 방향 없음 → 바닥형성")
        return "바닥형성", reasons

    # 상승 중
    if price_12m >= hot:
        if down_3m or vol_down:
            reasons.append("장기 급등 후 단기 둔화 → 하락전환")
            return "하락전환", reasons
        reasons.append(f"12개월 {price_12m:+.1%} ≥ {hot:.0%} → 과열")
        return "과열", reasons
    if price_12m >= strong:
        reasons.append(f"12개월 {price_12m:+.1%} ≥ {strong:.0%} → 상승확산")
        return "상승확산", reasons
    if down_3m:
        reasons.append("완만한 상승인데 단기 하락 → 하락전환")
        return "하락전환", reasons
    reasons.append("완만한 상승 → 상승초기")
    return "상승초기", reasons


def region_regime(conn: sqlite3.Connection, lawd_cd: str, *,
                  as_of: cutoff_mod.AsOf, area_band: str = "84") -> Regime:
    """시군구 하나의 국면. 그 지역 모든 단지의 중앙값 가격으로 본다."""
    observable = as_of.observable
    end_ym = _shift(observable.ym, -1)
    ym_12 = _shift(end_ym, -12)
    ym_3 = _shift(end_ym, -3)

    with cutoff_mod.guard(conn, observable) as g:
        rows = g.execute(
            "SELECT s.as_of_ym, s.representative_price, s.sample_n "
            "  FROM price_snapshot s JOIN complex c ON c.id = s.complex_id "
            " WHERE c.lawd_cd = ? AND s.area_band = ? "
            "   AND s.as_of_ym >= ? AND s.as_of_ym <= ?",
            (lawd_cd, area_band, ym_12, end_ym)).fetchall()

    by_ym: dict[str, list[int]] = {}
    total_n = 0
    for r in rows:
        if not r["representative_price"]:
            continue
        by_ym.setdefault(r["as_of_ym"], []).append(int(r["representative_price"]))
        total_n += int(r["sample_n"] or 0)

    def median_at(ym: str) -> float | None:
        prices = by_ym.get(ym)
        if not prices:
            return None
        s = sorted(prices)
        mid = len(s) // 2
        return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0

    now, then12, then3 = median_at(end_ym), median_at(ym_12), median_at(ym_3)
    p12 = (now / then12 - 1.0) if now and then12 else None
    p3 = (now / then3 - 1.0) if now and then3 else None

    # 거래량 비율 — 최근 3개월 대비 그 이전 3개월
    with cutoff_mod.guard(conn, observable) as g:
        counts = g.execute(
            "SELECT COUNT(*) FROM trade t JOIN complex c ON c.id = t.complex_id "
            " WHERE c.lawd_cd = ? AND t.deal_ymd <= ? AND t.deal_ymd >= ? "
            "   AND t.cancel_yn = 0",
            (lawd_cd, observable.ymd, _shift(end_ym, -3) + "01")).fetchone()[0]
        prior = g.execute(
            "SELECT COUNT(*) FROM trade t JOIN complex c ON c.id = t.complex_id "
            " WHERE c.lawd_cd = ? AND t.deal_ymd <= ? AND t.deal_ymd >= ? "
            "   AND t.cancel_yn = 0",
            (lawd_cd, _shift(end_ym, -3) + "01", _shift(end_ym, -6) + "01")).fetchone()[0]
    volume_ratio = (counts / prior) if prior else None

    name, reasons = classify(price_12m=p12, price_3m=p3, volume_ratio=volume_ratio)
    return Regime(name, p12, p3, volume_ratio, total_n, reasons)


def feature(regime: Regime) -> Feature:
    """국면을 Feature 로. 값은 국면 인덱스(0~6)이고, 이름이 detail 에 있다."""
    key = "regime"
    if regime.name == "확인 불가":
        return Feature.missing(key, "; ".join(regime.reasons))
    conf = sample_confidence(regime.sample_n, full_at=30)
    calc = Calc(
        value=regime.name, unit="국면",
        formula="7국면 분류 = f(12개월 변화율, 3개월 변화율, 거래량 비율)",
        inputs={"12개월": f"{regime.price_12m:+.1%}" if regime.price_12m is not None
                else "확인 불가",
                "3개월": f"{regime.price_3m:+.1%}" if regime.price_3m is not None
                else "확인 불가",
                "거래량 비율": f"{regime.volume_ratio:.2f}배"
                if regime.volume_ratio is not None else "확인 불가"},
        intermediates={"국면": regime.name, "판정 근거": regime.reasons,
                       "경계값": THRESHOLDS, "경계값 성격": THRESHOLD_NOTE,
                       "쓰임": "국면마다 factor 가중치가 달라야 한다(§8). "
                             "이 값 자체는 점수가 아니다"},
        grade="ESTIMATED",
    )
    return Feature(key, float(regime.index), "", conf, Status.OK,
                   {"국면": regime.name, "근거": regime.reasons},
                   calc).with_confidence(conf)


def _shift(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:6]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"
