"""호가 vs 실거래 괴리 (요구사항 8).

호가가 실거래보다 높은 건 정상이다(매도자는 늘 더 받고 싶다). 문제는 **얼마나**
높은가다. 괴리가 평소보다 크게 벌어졌다면 둘 중 하나다:

    실거래 추격구간   시장이 오르는 중이고 실거래가 호가를 따라잡는 중
    호가 과열        매도자만 기대가 높고 매수자가 안 따라오는 중

이 모듈은 그 둘을 구분해 단정하지 않는다. 괴리율을 계산해 보여주고,
판단 재료(매물 증감·가격인하 건수)는 pressure.py 가 따로 낸다.
"""
from __future__ import annotations

from dataclasses import dataclass

from apt_engine import units
from apt_engine.trace import Calc

# 이 이상 벌어지면 "확인 필요" 신호로 본다. 절대 기준이 아니라 눈에 띄게 하는 문턱값이다.
WIDE_GAP = 0.10


@dataclass(frozen=True)
class Gap:
    listing_price: int
    trade_price: int
    ratio: float             # (호가 - 실거래) / 실거래
    label: str

    @property
    def wide(self) -> bool:
        return abs(self.ratio) >= WIDE_GAP


def _gap(listing_price: int | None, trade_price: int | None, label: str) -> Gap | None:
    if not listing_price or not trade_price or trade_price <= 0:
        return None
    return Gap(listing_price, trade_price,
               (listing_price - trade_price) / trade_price, label)


def analyze(distribution, price_snapshot, *, recent_trade_price: int | None = None) -> Calc:
    """호가 분포와 실거래 대표가격의 괴리.

    price_snapshot 은 PHASE 2의 Snapshot(대표가격), recent_trade_price 는
    가장 최근 1건의 실거래가다. 둘은 다른 질문에 답한다 —
    대표가격은 "정상 시세", 최근 1건은 "제일 마지막에 찍힌 값".
    """
    base = price_snapshot.value if (price_snapshot and price_snapshot.usable) else None

    gaps = [
        _gap(distribution.low, base, "최저호가 vs 대표실거래"),
        _gap(distribution.low_normal, base, "정상매물 최저호가 vs 대표실거래"),
        _gap(distribution.median, base, "중위호가 vs 대표실거래"),
        _gap(distribution.low_normal, recent_trade_price, "정상매물 최저호가 vs 최근실거래"),
    ]
    gaps = [g for g in gaps if g is not None]

    if not gaps:
        return Calc(
            value=None, unit="ratio",
            formula="호가 또는 실거래 표본이 없어 괴리율을 계산할 수 없음",
            inputs={"호가 매물": distribution.count,
                    "대표실거래": "확인 불가" if base is None else units.fmt_eok(base)},
            grade="CONFIRMED",
        )

    primary = next((g for g in gaps if g.label.startswith("정상매물 최저호가 vs 대표")), gaps[0])

    intermediates = {g.label: units.fmt_pct(g.ratio, sign=True) for g in gaps}
    if distribution.low_is_special:
        intermediates["주의"] = (
            "최저호가가 특수매물이라 '정상매물 최저호가' 기준 괴리율을 우선 봐야 합니다.")
    if primary.wide:
        intermediates["신호"] = (
            "괴리가 큽니다 — 실거래가 호가를 따라가는 중일 수도, 호가만 앞서간 것일 수도 "
            "있습니다. 매물 증감과 가격인하 건수를 함께 보세요(market pressure).")

    sources = {}
    if price_snapshot is not None and price_snapshot.usable:
        sources["대표실거래"] = price_snapshot.calc
    sources["호가분포"] = distribution.calc

    return Calc.derive(
        primary.ratio,
        unit="ratio",
        formula="(정상매물 최저호가 − 대표 실거래가) ÷ 대표 실거래가",
        sources=sources,
        inputs={
            "정상매물 최저호가": units.fmt_eok(primary.listing_price),
            "대표 실거래가": units.fmt_eok(primary.trade_price),
        },
        intermediates=intermediates,
    )
