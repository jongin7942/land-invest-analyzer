"""Entry Price Engine — 좋은 아파트와 좋은 매수가격을 분리한다 (지시서 §7).

> 이 가격은 임의 퍼센트로 만들지 않는다.

"MNTP × 0.9 = Strong Buy" 같은 계산을 하지 않는다는 뜻이다. 그런 값은 어느
단지에나 같은 답을 주고, 시장 국면과 무관하며, 왜 그 숫자인지 설명할 수 없다.

대신 **서로 다른 근거에서 나온 가격 앵커 여러 개**를 모은다.

    long_term      이 단지 자신의 장기 중앙값 (자기 역사)
    jeonse_floor   전세가 받쳐 주는 하한 (실수요 바닥)
    distribution   최근 실제로 거래된 가격 분포 (진짜 살 수 있었던 가격)
    peer           같은 생활권 비교단지 대비 정상 비율
    supply_adjust  향후 공급이 많으면 하향, 절벽이면 상향

앵커가 **둘 미만이면 매수가를 만들지 않는다.** 하나짜리 근거로 "이 가격에 사라"
고 말하는 건 근거 없는 숫자와 다르지 않다.

§7 이 못 박은 것 하나: **과거 고점 대비 하락률 자체를 저평가 점수로 쓰지 않는다.**
고점은 참고로만 보여주고 앵커에 넣지 않는다 — 고점이 거품이었다면 그 대비 할인은
저평가가 아니라 정상화다.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field

from apt_engine import units
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features import jeonse as jeonse_mod
from apt_engine.features import momentum as momentum_mod
from apt_engine.features.base import Feature, Status, combine, sample_confidence
from apt_engine.trace import Calc

# 앵커가 이보다 적으면 매수가를 만들지 않는다.
MIN_ANCHORS = 2

# 전세가 받쳐 주는 하한을 볼 때 쓰는 전세가율 상한.
# **판정 기준**이지 관측된 상한이 아니다 — 백테스트가 대체한다.
JEONSE_SUPPORT_RATIO = 0.80

# 앵커들을 어떻게 네 구간으로 나누나. 분위수라 임의 퍼센트가 아니다.
BANDS = {"strong": 0.15, "fair": 0.50, "wait": 0.85}


@dataclass(frozen=True)
class Anchor:
    key: str
    price: int
    confidence: float
    basis: str

    @property
    def label(self) -> str:
        return f"{units.fmt_eok(self.price)}  ({self.basis}, 신뢰도 {self.confidence:.0%})"


@dataclass(frozen=True)
class EntryPrice:
    complex_id: int
    area_band: str
    current: int | None
    anchors: list[Anchor] = field(default_factory=list)
    strong_buy: int | None = None
    fair_buy: int | None = None
    wait: int | None = None
    overpriced: int | None = None
    past_peak: int | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.fair_buy is not None

    @property
    def verdict(self) -> str:
        """지금 가격이 어느 구간인가. 매수 지시가 아니라 위치 표시다."""
        if not self.known or self.current is None:
            return "확인 불가"
        if self.current <= self.strong_buy:
            return "Strong Buy 구간"
        if self.current <= self.fair_buy:
            return "Fair Buy 구간"
        if self.current <= self.wait:
            return "Wait 구간"
        return "Overpriced 구간"

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — 앵커 {len(self.anchors)}개 (최소 {MIN_ANCHORS}개 필요)"
        return (f"Strong {units.fmt_eok(self.strong_buy)} / "
                f"Fair {units.fmt_eok(self.fair_buy)} / "
                f"Wait {units.fmt_eok(self.wait)} → 현재 {self.verdict}")


def _long_term_anchor(series: momentum_mod.Series) -> Anchor | None:
    """이 단지 자신의 장기 중앙값. 자기 역사가 가장 오염이 적은 기준이다."""
    if len(series.points) < 12:
        return None
    prices = [p for _, p, _ in series.points]
    median = int(statistics.median(prices))
    n = sum(n for _, _, n in series.points)
    return Anchor("long_term", median, sample_confidence(len(prices), full_at=24),
                  f"최근 {len(prices)}개월 대표가격 중앙값")


def _jeonse_anchor(conn: sqlite3.Connection, complex_id: int, band: str, *,
                   as_of: cutoff_mod.AsOf) -> Anchor | None:
    """전세가 받쳐 주는 하한. 전세는 실수요라 기대가 덜 섞여 있다."""
    ratio = jeonse_mod.ratio_feature(conn, complex_id, band, as_of=as_of)
    if not ratio.known:
        return None
    deposit_text = ratio.calc.intermediates.get("전세", "")
    try:
        deposit = int(deposit_text.split("원")[0].replace(",", ""))
    except (ValueError, IndexError):
        return None
    price = int(deposit / JEONSE_SUPPORT_RATIO)
    return Anchor("jeonse_floor", price, ratio.confidence * 0.8,
                  f"전세 {units.fmt_eok(deposit)} ÷ 전세가율 상한 "
                  f"{JEONSE_SUPPORT_RATIO:.0%}")


def _distribution_anchor(conn: sqlite3.Connection, complex_id: int, band: str, *,
                         as_of: cutoff_mod.AsOf, months: int = 12) -> Anchor | None:
    """최근 실제 거래 분포의 하위 25%. **실제로 그 가격에 거래된 적이 있다.**"""
    observable = as_of.observable
    start = _shift_ymd(observable.ymd, -months)
    with cutoff_mod.guard(conn, observable) as g:
        rows = g.execute(
            "SELECT deal_amount FROM trade "
            " WHERE complex_id = ? AND area_band = ? AND cancel_yn = 0 "
            "   AND deal_ymd >= ? AND deal_ymd <= ?",
            (complex_id, band, start, observable.ymd)).fetchall()
    prices = sorted(int(r["deal_amount"]) for r in rows)
    if len(prices) < 4:
        return None
    idx = max(0, int(len(prices) * 0.25) - 1)
    return Anchor("distribution", prices[idx], sample_confidence(len(prices), full_at=12),
                  f"최근 {months}개월 정상거래 {len(prices)}건의 하위 25%")


def _peer_anchor(conn: sqlite3.Connection, complex_id: int, band: str, *,
                 as_of: cutoff_mod.AsOf) -> Anchor | None:
    """비교단지 대비 정상 비율에서 역산한 가격.

    `benchmark_relation` 이 있어야 한다. 없으면 만들지 않는다 —
    "비슷해 보이는 단지" 를 코드가 임의로 고르면 그게 곧 지어낸 근거다.
    """
    rows = conn.execute(
        "SELECT benchmark_complex_id FROM benchmark_relation WHERE complex_id = ?",
        (complex_id,)).fetchall()
    if not rows:
        return None

    observable = as_of.observable
    end_ym = _shift_ym(observable.ym, -1)
    ratios: list[float] = []
    peer_prices: list[int] = []
    with cutoff_mod.guard(conn, observable) as g:
        for r in rows:
            peer = g.execute(
                "SELECT representative_price FROM price_snapshot "
                " WHERE complex_id = ? AND area_band = ? AND as_of_ym <= ? "
                " ORDER BY as_of_ym DESC LIMIT 1",
                (r["benchmark_complex_id"], band, end_ym)).fetchone()
            if peer and peer["representative_price"]:
                peer_prices.append(int(peer["representative_price"]))
        hist = g.execute(
            "SELECT ratio FROM price_ratio_history "
            " WHERE complex_id = ? AND area_band = ? AND as_of_ym <= ?",
            (complex_id, band, end_ym)).fetchall()
    ratios = [float(h["ratio"]) for h in hist if h["ratio"]]
    if not peer_prices or len(ratios) < 6:
        return None

    normal = statistics.median(ratios)
    price = int(statistics.median(peer_prices) * normal)
    return Anchor("peer", price, sample_confidence(len(ratios), full_at=24),
                  f"비교단지 중앙값 × 정상비율 {normal:.2f} (표본 {len(ratios)}개월)")


def _past_peak(series: momentum_mod.Series) -> int | None:
    """과거 고점. **앵커가 아니다** — §7 이 저평가 근거로 쓰지 말라고 못 박았다."""
    prices = [p for _, p, _ in series.points]
    return max(prices) if prices else None


def build(conn: sqlite3.Connection, complex_id: int, area_band: str, *,
          as_of: cutoff_mod.AsOf,
          supply_ratio: float | None = None) -> EntryPrice:
    """매수가 구간. 앵커가 부족하면 만들지 않는다."""
    series = momentum_mod.load_series(conn, complex_id, area_band, as_of=as_of,
                                      months=60)
    current = series.points[0][1] if series.points else None

    candidates = [
        _long_term_anchor(series),
        _jeonse_anchor(conn, complex_id, area_band, as_of=as_of),
        _distribution_anchor(conn, complex_id, area_band, as_of=as_of),
        _peer_anchor(conn, complex_id, area_band, as_of=as_of),
    ]
    anchors = [a for a in candidates if a is not None]
    missing = [k for k, a in zip(("long_term", "jeonse_floor", "distribution", "peer"),
                                 candidates) if a is None]

    peak = _past_peak(series)
    if len(anchors) < MIN_ANCHORS:
        return EntryPrice(complex_id, area_band, current, anchors,
                          past_peak=peak, missing=missing)

    # 앵커를 신뢰도로 가중해 분위수를 만든다. 임의 퍼센트가 아니라
    # "서로 다른 근거들이 말하는 가격의 분포" 다.
    weighted: list[int] = []
    for a in anchors:
        weight = max(1, int(round(a.confidence * 10)))
        weighted.extend([a.price] * weight)
    weighted.sort()

    def q(p: float) -> int:
        idx = min(len(weighted) - 1, max(0, int(len(weighted) * p)))
        return weighted[idx]

    strong, fair, wait = q(BANDS["strong"]), q(BANDS["fair"]), q(BANDS["wait"])

    # 공급이 많으면 매수가를 낮춰야 한다. 공급 비율을 **그대로** 반영한다
    # (임의 계수를 곱하지 않는다 — 비율 자체가 이미 stock 대비 크기다).
    if supply_ratio is not None and supply_ratio > 0:
        adjust = 1.0 - min(0.20, supply_ratio)
        strong = int(strong * adjust)
        fair = int(fair * adjust)
        wait = int(wait * adjust)

    return EntryPrice(complex_id, area_band, current, anchors, strong, fair, wait,
                      int(wait * 1.0) + 1, peak, missing)


def feature(entry: EntryPrice) -> Feature:
    """현재 가격이 매수가 구간의 어디인가. 0=아주 쌈, 1=아주 비쌈."""
    key = "entry_position"
    if not entry.known or entry.current is None:
        return Feature.missing(
            key, f"매수가 앵커가 {len(entry.anchors)}개뿐입니다 "
                 f"(최소 {MIN_ANCHORS}개). 없는 것: {', '.join(entry.missing)}")

    lo, hi = entry.strong_buy, entry.wait
    span = max(hi - lo, 1)
    # 아래쪽을 0 에서 자르지 않는다. 인천 74㎡ 204개를 채점했더니 Strong Buy 아래
    # 단지가 전부 0.0 에 묶여 같은 백분위(83)를 받았다 — "얼마나 더 싼가" 가 지워졌다.
    # 구간 한 폭(-1.0)까지는 그대로 두고, 그 아래는 데이터 오류일 가능성이 커 자른다.
    value = max(-1.0, min(1.5, (entry.current - lo) / span))
    conf = combine(*[a.confidence for a in entry.anchors])

    calc = Calc(
        value=value, unit="0~1.5",
        formula="entry_position = (현재가 − Strong Buy) ÷ (Wait − Strong Buy)",
        inputs={"현재가": units.fmt_eok(entry.current)},
        intermediates={
            "앵커": {a.key: a.label for a in entry.anchors},
            "없는 앵커": entry.missing or "없음",
            "구간": {"Strong Buy": units.fmt_eok(entry.strong_buy),
                   "Fair Buy": units.fmt_eok(entry.fair_buy),
                   "Wait": units.fmt_eok(entry.wait)},
            "판정": entry.verdict,
            "과거 고점": (units.fmt_eok(entry.past_peak) if entry.past_peak
                     else "확인 불가"),
            "고점 대비": ("참고용입니다. §7 에 따라 고점 대비 하락률 자체를 "
                      "저평가 근거로 쓰지 않습니다 — 고점이 거품이었다면 그 대비 "
                      "할인은 저평가가 아니라 정상화입니다"),
            "산출 방식": "임의 퍼센트가 아니라 서로 다른 근거에서 나온 앵커들의 분위수",
        },
        grade="ESTIMATED",
    )
    return Feature(key, value, "", conf, Status.OK, calc.intermediates,
                   calc).with_confidence(conf)


def all_features(conn: sqlite3.Connection, complex_id: int, area_band: str, *,
                 as_of: cutoff_mod.AsOf,
                 supply_ratio: float | None = None) -> list[Feature]:
    return [feature(build(conn, complex_id, area_band, as_of=as_of,
                          supply_ratio=supply_ratio))]


def _shift_ym(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:6]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def _shift_ymd(ymd: str, months: int) -> str:
    total = int(ymd[:4]) * 12 + (int(ymd[4:6]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}{min(int(ymd[6:8]), 28):02d}"
