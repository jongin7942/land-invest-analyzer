"""전세 — Downside Defense 이지 Upside 가 아니다 (지시서 §14).

> 전세는 기본적으로 Downside Defense factor 로 사용한다.
> 전세가율이 높다는 이유만으로 Upside Score 를 직접 높이지 않는다.

전세가율이 높으면 두 가지가 참이다.
  1. 실투자금이 적게 든다 (Capital Efficiency)
  2. 매매가가 전세가에 받쳐져 있어 하방이 얕다 (Downside Defense)

**둘 다 "얼마나 오를까" 와는 다른 이야기다.** 그래서 이 모듈은 upside 점수를
내놓지 않고, `downside_defense` 와 `capital_efficiency` 두 갈래로만 값을 낸다.

Jeonse Lead: 전세가 먼저 움직이고 매매가 따라오는 관계. 전세는 실수요라
투기적 기대가 덜 섞여 있어서, 전세가 오르는데 매매가 안 오르면 그 격차가
나중에 좁혀지는 경우가 있다. **있는 경우가 있다** 는 것이지 항상은 아니라서,
이건 Lessons DB 의 가설(jeonse_is_downside_defense)로 남아 있고 백테스트가 검증한다.
"""
from __future__ import annotations

import sqlite3

from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features.base import (Feature, Status, combine, sample_confidence)
from apt_engine.trace import Calc

WINDOWS = (3, 6, 12)

# 전세가율이 이 정도면 하방이 상당히 받쳐진다고 본다. **판정 기준**이다.
DEFENSE_FLOOR = 0.50
DEFENSE_FULL = 0.80


def _series(conn: sqlite3.Connection, complex_id: int, band: str, table: str,
            column: str, *, as_of: cutoff_mod.AsOf, months: int = 24):
    observable = as_of.observable
    end_ym = _shift(observable.ym, -1)
    start_ym = _shift(end_ym, -months + 1)
    with cutoff_mod.guard(conn, observable) as g:
        rows = g.execute(
            f"SELECT as_of_ym, {column} AS v, sample_n FROM {table} "
            " WHERE complex_id = ? AND area_band = ? "
            "   AND as_of_ym >= ? AND as_of_ym <= ? ORDER BY as_of_ym DESC",
            (complex_id, band, start_ym, end_ym)).fetchall()
    return [(r["as_of_ym"], int(r["v"]), int(r["sample_n"] or 0))
            for r in rows if r["v"]]


def _at(points, ym: str):
    for y, v, n in points:
        if y <= ym:
            return v, n
    return None


def ratio_feature(conn: sqlite3.Connection, complex_id: int, band: str, *,
                  as_of: cutoff_mod.AsOf) -> Feature:
    """전세가율. **같은 기준월끼리만** 나눈다 — 다른 달을 섞으면 비율이 거짓이 된다."""
    key = "jeonse_ratio"
    sale = _series(conn, complex_id, band, "price_snapshot",
                   "representative_price", as_of=as_of)
    rent = _series(conn, complex_id, band, "jeonse_snapshot",
                   "representative_deposit", as_of=as_of)
    if not sale or not rent:
        return Feature.missing(key, "매매 또는 전세 대표가가 없습니다")

    sale_ym = {y: (v, n) for y, v, n in sale}
    common = [y for y, _, _ in rent if y in sale_ym]
    if not common:
        return Feature.missing(
            key, "매매와 전세의 기준월이 겹치지 않습니다. 다른 달을 섞어 나누지 않습니다")

    ym = max(common)
    deposit, rent_n = next((v, n) for y, v, n in rent if y == ym)
    price, sale_n = sale_ym[ym]
    if price <= 0:
        return Feature.missing(key, "매매 대표가가 0 이하입니다")

    value = deposit / price
    conf = combine(sample_confidence(sale_n), sample_confidence(rent_n))
    calc = Calc(
        value=value, unit="비율",
        formula="전세가율 = 같은 달 전세 대표보증금 ÷ 매매 대표가격",
        inputs={"기준월": ym},
        intermediates={"매매": f"{price:,}원 (표본 {sale_n})",
                       "전세": f"{deposit:,}원 (표본 {rent_n})",
                       "전세가율": f"{value:.1%}",
                       "주의": "전세가율이 높다는 이유만으로 상승 점수를 높이지 않는다(§14)"},
        grade="CONFIRMED" if min(sale_n, rent_n) >= 3 else "ESTIMATED",
    )
    return Feature(key, value, "", conf, Status.OK, calc.intermediates,
                   calc).with_confidence(conf)


def downside_defense(ratio: Feature) -> Feature:
    """전세가율 → 하방 방어력 (0~1). 상승 점수가 아니다."""
    key = "downside_defense"
    if not ratio.known:
        return Feature.missing(key, "전세가율을 구하지 못했습니다")
    span = DEFENSE_FULL - DEFENSE_FLOOR
    value = max(0.0, min(1.0, (ratio.value - DEFENSE_FLOOR) / span))
    calc = Calc(
        value=value, unit="0~1",
        formula=f"전세가율을 {DEFENSE_FLOOR:.0%}~{DEFENSE_FULL:.0%} 구간에서 0~1 로",
        inputs={"전세가율": f"{ratio.value:.1%}"},
        intermediates={
            "해석": "매매가가 전세가에 받쳐져 하락 시 덜 빠질 가능성",
            "기준 성격": "경계값은 관측된 분포가 아니라 판정 기준입니다. 백테스트가 대체합니다",
            "쓰임": "Downside 항목에만 쓴다. Upside 에 더하지 않는다(§14)",
        },
        grade="ESTIMATED",
    )
    return Feature(key, value, "", ratio.confidence, Status.OK, calc.intermediates,
                   calc).with_confidence(ratio.confidence)


def jeonse_lead(conn: sqlite3.Connection, complex_id: int, band: str, *,
                as_of: cutoff_mod.AsOf, months: int = 12) -> Feature:
    """전세가 매매보다 얼마나 앞서 움직였나.

    양수면 전세가 더 올랐다(매매가 아직 안 따라왔다). 이걸 **자동으로 상승 신호로
    쓰지 않는다** — 구조적 이유(공급·규제)로 벌어진 것일 수도 있다.
    """
    key = "jeonse_lead"
    sale = _series(conn, complex_id, band, "price_snapshot",
                   "representative_price", as_of=as_of, months=months + 12)
    rent = _series(conn, complex_id, band, "jeonse_snapshot",
                   "representative_deposit", as_of=as_of, months=months + 12)
    if not sale or not rent:
        return Feature.missing(key, "매매 또는 전세 시계열이 없습니다")

    end_ym = min(sale[0][0], rent[0][0])
    past_ym = _shift(end_ym, -months)
    s_now, s_then = _at(sale, end_ym), _at(sale, past_ym)
    r_now, r_then = _at(rent, end_ym), _at(rent, past_ym)
    if not all((s_now, s_then, r_now, r_then)) or s_then[0] <= 0 or r_then[0] <= 0:
        return Feature.missing(key, f"{months}개월 전 값이 없습니다")

    sale_change = s_now[0] / s_then[0] - 1.0
    rent_change = r_now[0] / r_then[0] - 1.0
    value = rent_change - sale_change
    conf = combine(sample_confidence(min(s_now[1], s_then[1])),
                   sample_confidence(min(r_now[1], r_then[1])))
    calc = Calc(
        value=value, unit="비율",
        formula=f"Jeonse Lead = 전세 {months}개월 변화율 − 매매 {months}개월 변화율",
        inputs={"기준월": end_ym, "비교월": past_ym},
        intermediates={
            "매매 변화": f"{sale_change:+.1%}", "전세 변화": f"{rent_change:+.1%}",
            "격차": f"{value:+.1%}p",
            "해석": ("전세가 앞섰다 — 매매가 따라올 수도, 구조적 이유일 수도 있다"
                   if value > 0 else "매매가 앞섰다 — 기대가 먼저 반영된 상태"),
            "주의": "자동 매수 신호가 아니다. Lessons DB 의 가설이며 백테스트가 검증한다",
        },
        grade="ESTIMATED",
    )
    return Feature(key, value, "", conf, Status.OK, calc.intermediates,
                   calc).with_confidence(conf)


def all_features(conn: sqlite3.Connection, complex_id: int, band: str, *,
                 as_of: cutoff_mod.AsOf) -> list[Feature]:
    ratio = ratio_feature(conn, complex_id, band, as_of=as_of)
    return [ratio, downside_defense(ratio),
            jeonse_lead(conn, complex_id, band, as_of=as_of)]


def _shift(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:6]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"
