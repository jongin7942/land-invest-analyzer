"""거래량 Flow Stage 와 Transaction Quality (지시서 §15·§16).

> §15 거래량 증가 자체를 BUY 신호로 사용하지 않는다.
>     거래량은 "Investigation Priority" 를 높이는 신호다.

그래서 이 모듈의 출력 이름은 `buy_signal` 이 아니라 `investigation_priority` 다.
이름이 곧 계약이다 — 랭킹 계층이 이걸 매수 점수로 쓰려면 그 결정을 코드에
명시적으로 써야 하고, 그러면 리뷰에서 보인다.

Flow Stage 6단계(§15):
    0 거래 없음 → 1 급매 소진 → 2 거래 증가 → 3 정상가격 상승
    → 4 지역 확산 → 5 과열/고점 회전

**가장 좋은 구간이 어디인지는 정하지 않는다.** 지시서가 "백테스트로 학습한다"고
못 박았다. 여기서는 단계만 판정하고, 어느 단계가 좋은지는 Lessons DB 가 정한다.

Transaction Quality(§16): 거래량뿐 아니라 거래의 **질**.
저층만 팔린 것과 중층 이상이 여러 건 팔린 것은 같은 '거래 3건' 이 아니다.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field

from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features.base import Feature, Status, sample_confidence
from apt_engine.trace import Calc

STAGES = ("거래없음", "급매소진", "거래증가", "정상가격상승", "지역확산", "과열회전")
STAGE_INDEX = {s: i for i, s in enumerate(STAGES)}

STAGE_NOTE = ("어느 단계가 매수에 가장 좋은지는 여기서 정하지 않습니다. "
              "지시서 §15 에 따라 백테스트가 학습합니다")

# 저층으로 볼 층수. 저층 거래는 같은 단지라도 가격이 다르게 형성된다.
LOW_FLOOR = 3


@dataclass(frozen=True)
class Window:
    """한 구간의 거래 묶음."""
    trades: list[tuple[int, int, str]] = field(default_factory=list)  # (금액, 층, 일자)

    def __len__(self) -> int:
        return len(self.trades)

    @property
    def prices(self) -> list[int]:
        return [p for p, _, _ in self.trades]

    @property
    def mid_high(self) -> list[int]:
        """중층 이상 거래만. 저층은 가격대가 달라 섞으면 중앙값이 내려간다."""
        return [p for p, f, _ in self.trades if f is not None and f > LOW_FLOOR]

    @property
    def median(self) -> float | None:
        return statistics.median(self.prices) if self.prices else None


def _window(conn: sqlite3.Connection, complex_id: int, band: str,
            start_ymd: str, end_ymd: str, *, as_of: cutoff_mod.AsOf) -> Window:
    with cutoff_mod.guard(conn, as_of) as g:
        rows = g.execute(
            "SELECT deal_amount, floor, deal_ymd FROM trade "
            " WHERE complex_id = ? AND area_band = ? AND cancel_yn = 0 "
            "   AND deal_ymd >= ? AND deal_ymd <= ?",
            (complex_id, band, start_ymd, end_ymd)).fetchall()
    return Window([(int(r["deal_amount"]), r["floor"], r["deal_ymd"]) for r in rows])


def classify_stage(recent: Window, prior: Window) -> tuple[str, list[str]]:
    """거래량과 가격 방향으로 단계 하나."""
    reasons: list[str] = []
    if len(recent) == 0:
        return "거래없음", ["최근 구간에 정상 거래가 없습니다"]

    reasons.append(f"최근 {len(recent)}건 · 직전 {len(prior)}건")
    volume_up = len(recent) > len(prior) if len(prior) else len(recent) >= 3

    r_med, p_med = recent.median, prior.median
    if r_med is None or p_med is None:
        reasons.append("가격 비교 구간이 없어 거래량만으로 판정")
        return ("거래증가" if volume_up else "급매소진"), reasons

    move = r_med / p_med - 1.0
    reasons.append(f"중앙가 {move:+.1%}")

    if move < -0.03:
        reasons.append("가격이 내리며 거래가 늘었다면 급매 소진 국면")
        return "급매소진", reasons
    if not volume_up:
        reasons.append("거래가 늘지 않았다")
        return "급매소진" if move < 0 else "거래증가", reasons
    if move < 0.03:
        return "거래증가", reasons
    if move < 0.10:
        reasons.append("거래 증가 + 가격 상승")
        return "정상가격상승", reasons
    if move < 0.20:
        return "지역확산", reasons
    reasons.append("단기 급등 — 고점 회전 가능성")
    return "과열회전", reasons


def flow_stage(conn: sqlite3.Connection, complex_id: int, band: str, *,
               as_of: cutoff_mod.AsOf, months: int = 3) -> Feature:
    key = "flow_stage"
    observable = as_of.observable
    end = observable.ymd
    mid = _shift_ymd(end, -months)
    start = _shift_ymd(end, -months * 2)

    recent = _window(conn, complex_id, band, mid, end, as_of=observable)
    prior = _window(conn, complex_id, band, start, mid, as_of=observable)
    stage, reasons = classify_stage(recent, prior)

    conf = sample_confidence(len(recent) + len(prior), full_at=12)
    calc = Calc(
        value=stage, unit="단계",
        formula="Flow Stage = f(거래량 변화, 중앙가 변화)",
        inputs={"최근 구간": f"{mid}~{end}", "직전 구간": f"{start}~{mid}"},
        intermediates={"단계": stage, "근거": reasons, "단계 목록": list(STAGES),
                       "주의": STAGE_NOTE},
        grade="ESTIMATED",
    )
    return Feature(key, float(STAGE_INDEX[stage]), "", conf, Status.OK,
                   {"단계": stage, "근거": reasons}, calc).with_confidence(conf)


def investigation_priority(stage_feature: Feature, recent_n: int,
                           prior_n: int) -> Feature:
    """거래량이 말해 주는 것은 '더 볼 만하다' 이지 '사라' 가 아니다 (§15)."""
    key = "investigation_priority"
    if not stage_feature.known:
        return Feature.missing(key, "Flow Stage 를 구하지 못했습니다")
    if prior_n == 0 and recent_n == 0:
        return Feature.missing(key, "거래가 없습니다")
    ratio = (recent_n / prior_n) if prior_n else float(recent_n)
    value = max(0.0, min(1.0, (ratio - 1.0) / 2.0 + 0.5))
    calc = Calc(
        value=value, unit="0~1",
        formula="거래량 비율(최근÷직전)을 0~1 로",
        inputs={"최근": recent_n, "직전": prior_n},
        intermediates={"비율": f"{ratio:.2f}배",
                       "이름이 곧 계약": "이 값은 조사 우선순위다. 매수 신호가 아니다(§15)"},
        grade="ESTIMATED",
    )
    return Feature(key, value, "", stage_feature.confidence, Status.OK,
                   calc.intermediates, calc).with_confidence(stage_feature.confidence)


def transaction_quality(conn: sqlite3.Connection, complex_id: int, band: str, *,
                        as_of: cutoff_mod.AsOf, months: int = 6) -> Feature:
    """거래의 질 (§16).

    중층 이상 정상거래가 여러 건 값을 끌어올릴 때 신뢰도가 높다.
    저층 한 건이나 극단적으로 흩어진 가격은 신호가 아니다.
    """
    key = "transaction_quality"
    observable = as_of.observable
    end = observable.ymd
    start = _shift_ymd(end, -months)
    window = _window(conn, complex_id, band, start, end, as_of=observable)
    if len(window) == 0:
        return Feature.missing(key, f"최근 {months}개월 정상 거래가 없습니다")

    mid_high = window.mid_high
    known_floor = [f for _, f, _ in window.trades if f is not None]
    mid_high_share = (len(mid_high) / len(known_floor)) if known_floor else None

    prices = window.prices
    dispersion = None
    if len(prices) >= 3 and statistics.median(prices):
        dispersion = statistics.pstdev(prices) / statistics.median(prices)

    parts = {}
    parts["표본"] = sample_confidence(len(window), full_at=8)
    if mid_high_share is not None:
        parts["중층이상비중"] = mid_high_share
    if dispersion is not None:
        # 흩어짐이 작을수록 좋다. 20% 이상 흩어지면 신호로 보지 않는다.
        parts["가격응집"] = max(0.0, 1.0 - dispersion / 0.20)

    value = sum(parts.values()) / len(parts)
    calc = Calc(
        value=value, unit="0~1",
        formula="거래 질 = 평균(표본충분도, 중층이상 비중, 가격 응집도)",
        inputs={"구간": f"{start}~{end}", "거래 수": len(window)},
        intermediates={
            "항목별": {k: round(v, 3) for k, v in parts.items()},
            "중층이상": (f"{len(mid_high)}/{len(known_floor)}건"
                    if known_floor else "층 정보 없음"),
            "가격 분산": f"{dispersion:.1%}" if dispersion is not None else "표본 부족",
            "해석": "정상 중층 이상 여러 건이 값을 끌어올릴 때 신뢰도가 높다(§16). "
                  "저층 한 건은 신호가 아니다",
        },
        grade="ESTIMATED",
    )
    conf = sample_confidence(len(window), full_at=8)
    return Feature(key, value, "", conf, Status.OK, calc.intermediates,
                   calc).with_confidence(conf)


def all_features(conn: sqlite3.Connection, complex_id: int, band: str, *,
                 as_of: cutoff_mod.AsOf) -> list[Feature]:
    observable = as_of.observable
    end, mid = observable.ymd, _shift_ymd(observable.ymd, -3)
    start = _shift_ymd(end, -6)
    recent = _window(conn, complex_id, band, mid, end, as_of=observable)
    prior = _window(conn, complex_id, band, start, mid, as_of=observable)
    stage = flow_stage(conn, complex_id, band, as_of=as_of)
    return [stage, investigation_priority(stage, len(recent), len(prior)),
            transaction_quality(conn, complex_id, band, as_of=as_of)]


def _shift_ymd(ymd: str, months: int) -> str:
    year, month, day = int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])
    total = year * 12 + (month - 1) + months
    y, m = total // 12, total % 12 + 1
    return f"{y:04d}{m:02d}{min(day, 28):02d}"
