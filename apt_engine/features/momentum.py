"""가격 모멘텀과 가속도 (지시서 §16·§39·§40).

지시서가 경고하는 것:

> §39 과거 많이 오른 아파트가 현재 좋은 투자라는 보장은 없다.
>     "Past Winner != Current Winner" 를 코드 레벨에서 지킨다.
> §40 이미 3개월 +15%, 6개월 +25% 오른 후 발견했다면 감점한다.

그래서 이 모듈은 상승률을 **점수로 바꾸지 않는다.** 상승률과 가속도를 있는 그대로
내놓고, "이미 많이 올랐다" 는 사실은 `discovery_lag` 라는 **별도 feature** 로
분리한다. 랭킹 계층이 그걸 감점으로 쓸지 가점으로 쓸지는 백테스트가 정한다.

가속도를 따로 보는 이유: 같은 +10% 라도 최근 3개월에 몰린 것과 12개월에 걸쳐
천천히 오른 것은 완전히 다른 국면이다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features.base import (Feature, Status, combine,
                                      freshness_confidence, sample_confidence)
from apt_engine.trace import Calc

# §40 이 예로 든 값. **관측된 분포가 아니라 판정 기준**이라 상수로 두고 백테스트가 고친다.
LATE_3M = 0.15
LATE_6M = 0.25

WINDOWS = (3, 6, 12)


@dataclass(frozen=True)
class Series:
    """단지 하나의 월별 대표가격 시계열 (컷오프 이내)."""
    complex_id: int
    area_band: str
    points: list[tuple[str, int, int]]     # (as_of_ym, price, sample_n)

    def at(self, ym: str) -> tuple[int, int] | None:
        for y, price, n in self.points:
            if y <= ym:
                return price, n
        return None

    @property
    def latest(self) -> tuple[str, int, int] | None:
        return self.points[0] if self.points else None


def load_series(conn: sqlite3.Connection, complex_id: int, area_band: str, *,
                as_of: cutoff_mod.AsOf, months: int = 36) -> Series:
    """컷오프 이내의 월별 대표가격. 최신순."""
    observable = as_of.observable
    end_ym = _shift(observable.ym, -1)          # 마지막으로 완료된 달
    start_ym = _shift(end_ym, -months + 1)
    with cutoff_mod.guard(conn, observable) as g:
        rows = g.execute(
            "SELECT as_of_ym, representative_price, sample_n FROM price_snapshot "
            " WHERE complex_id = ? AND area_band = ? "
            "   AND as_of_ym >= ? AND as_of_ym <= ? "
            " ORDER BY as_of_ym DESC", (complex_id, area_band, start_ym, end_ym)
        ).fetchall()
    return Series(complex_id, area_band,
                  [(r["as_of_ym"], int(r["representative_price"]),
                    int(r["sample_n"] or 0)) for r in rows
                   if r["representative_price"]])


def change(series: Series, months: int) -> Feature:
    """N개월 가격 변화율. 시작점이 없으면 만들지 않는다."""
    key = f"momentum_{months}m"
    if not series.points:
        return Feature.missing(key, "대표가격 시계열이 없습니다", unit="%")

    end_ym, end_price, end_n = series.points[0]
    start_ym = _shift(end_ym, -months)
    start = series.at(start_ym)
    if start is None:
        return Feature.missing(
            key, f"{months}개월 전({start_ym}) 대표가격이 없습니다", unit="%")

    start_price, start_n = start
    if start_price <= 0:
        return Feature.missing(key, "시작 가격이 0 이하입니다", unit="%")

    ratio = end_price / start_price - 1.0
    conf = combine(sample_confidence(min(end_n, start_n)),
                   freshness_confidence(_months_between(end_ym, series.points[0][0])))
    calc = Calc(
        value=ratio, unit="비율",
        formula=f"{months}개월 변화율 = 최근 대표가격 ÷ {months}개월 전 대표가격 − 1",
        inputs={"기준월": end_ym, "비교월": start_ym},
        intermediates={"최근": f"{end_price:,}원 (표본 {end_n})",
                       "과거": f"{start_price:,}원 (표본 {start_n})",
                       "변화율": f"{ratio:+.1%}",
                       "주의": "상승률 자체는 매수 신호가 아니다(§39). "
                               "이미 오른 뒤 발견한 것인지는 discovery_lag 이 본다"},
        grade="CONFIRMED" if end_n >= 3 and start_n >= 3 else "ESTIMATED",
    )
    return Feature(key, ratio, "", conf, Status.OK, calc.intermediates,
                   calc).with_confidence(conf)


def acceleration(series: Series) -> Feature:
    """가속도 — 최근 3개월 속도가 그 이전 3개월보다 얼마나 빠른가.

    양수면 상승이 빨라지고 있고, 음수면 식고 있다. 같은 12개월 +10% 라도
    가속 중인 것과 감속 중인 것은 다른 국면이다(§16).
    """
    key = "price_acceleration"
    recent = change(series, 3)
    if not recent.known:
        return Feature.missing(key, "최근 3개월 변화율을 구하지 못했습니다")

    end_ym = series.points[0][0]
    prior_end = _shift(end_ym, -3)
    prior = series.at(prior_end)
    prior_start = series.at(_shift(end_ym, -6))
    if prior is None or prior_start is None or prior_start[0] <= 0:
        return Feature.missing(key, "직전 3개월 구간이 없습니다")

    prior_rate = prior[0] / prior_start[0] - 1.0
    accel = recent.value - prior_rate
    calc = Calc(
        value=accel, unit="비율",
        formula="가속도 = 최근 3개월 변화율 − 직전 3개월 변화율",
        inputs={"기준월": end_ym},
        intermediates={"최근 3개월": f"{recent.value:+.1%}",
                       "직전 3개월": f"{prior_rate:+.1%}",
                       "가속도": f"{accel:+.1%}p",
                       "해석": "양수면 상승이 빨라지는 중, 음수면 식는 중"},
        grade="ESTIMATED",
    )
    return Feature(key, accel, "", recent.confidence, Status.OK,
                   calc.intermediates, calc).with_confidence(recent.confidence)


def discovery_lag(series: Series) -> Feature:
    """이미 오른 뒤에 발견한 것인가 (§40).

    값이 1 이면 "이미 많이 올랐다", 0 이면 "아직 안 올랐다".
    이건 **감점 재료이지 점수가 아니다** — 얼마나 감점할지는 백테스트가 정한다.
    """
    key = "discovery_lag"
    m3, m6 = change(series, 3), change(series, 6)
    if not (m3.known or m6.known):
        return Feature.missing(key, "가격 변화율을 구하지 못했습니다")

    late = 0.0
    reasons = []
    if m3.known and m3.value >= LATE_3M:
        late = max(late, min(1.0, m3.value / LATE_3M))
        reasons.append(f"3개월 {m3.value:+.1%} ≥ 기준 {LATE_3M:.0%}")
    if m6.known and m6.value >= LATE_6M:
        late = max(late, min(1.0, m6.value / LATE_6M))
        reasons.append(f"6개월 {m6.value:+.1%} ≥ 기준 {LATE_6M:.0%}")

    conf = max(m3.confidence, m6.confidence)
    calc = Calc(
        value=late, unit="0~1",
        formula="discovery_lag = 최근 상승이 '늦은 발견' 기준을 얼마나 넘었나",
        inputs={"3개월 기준": f"{LATE_3M:.0%}", "6개월 기준": f"{LATE_6M:.0%}"},
        intermediates={
            "3개월": m3.label, "6개월": m6.label,
            "판정": reasons or ["기준 미만 — 아직 크게 오르지 않았다"],
            "기준의 성격": "관측된 분포가 아니라 §40 이 예로 든 판정 기준입니다. "
                       "백테스트가 대체합니다",
            "주의": "이 값이 크면 Winner 를 늦게 발견한 것이고, 백테스트에서는 "
                   "MISSED_WINNER 로 기록해야 합니다",
        },
        grade="ESTIMATED",
    )
    return Feature(key, late, "", conf, Status.OK, calc.intermediates,
                   calc).with_confidence(conf)


def all_features(conn: sqlite3.Connection, complex_id: int, area_band: str, *,
                 as_of: cutoff_mod.AsOf) -> list[Feature]:
    series = load_series(conn, complex_id, area_band, as_of=as_of)
    return ([change(series, m) for m in WINDOWS]
            + [acceleration(series), discovery_lag(series)])


def _shift(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:6]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def _months_between(a: str, b: str) -> int:
    return abs((int(a[:4]) * 12 + int(a[4:6])) - (int(b[:4]) * 12 + int(b[4:6])))
