"""가격비율 — Current 와 Historical Normal 을 절대 섞지 않는다 (요구사항 4).

    Current Ratio           지금 이 단지가 비교단지 대비 몇 %인가
    Historical Normal Ratio 과거에는 보통 몇 %였나

이 둘이 벌어져 있으면 "상대적으로 싸졌다"는 신호지만, 그것만으로 저평가라고
단정하지 않는다(요구사항 3). 비율이 벌어진 데는 이유가 있을 수 있고, 그 이유를
찾는 건 PHASE 5(촉매)의 일이다.

시장 국면(상승기/하락기)은 한국부동산원 지수가 없어 **벤치마크 단지 가격의 12개월
변화율로 자체 판정**한다. 자체 판정이라는 사실을 계산근거에 남긴다 —
공식 지수로 오해하면 안 된다.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from apt_engine import units
from apt_engine.trace import Calc, Evidence

# 12개월 변화율이 이 이상이면 상승기, 이하면 하락기, 사이면 횡보기.
PHASE_THRESHOLD = 0.03
RISING, FALLING, FLAT = "상승", "하락", "횡보"

WINDOW_ALL, WINDOW_5Y, WINDOW_10Y = "all", "5y", "10y"
WINDOW_RISING, WINDOW_FALLING, WINDOW_FLAT = "상승기", "하락기", "횡보기"

SELF_JUDGED = Evidence(
    source="자체 시장국면 판정",
    note="한국부동산원 지수가 없어 벤치마크 단지 대표가격의 12개월 변화율로 판정한다. "
         "공식 지수가 아니다.")


def market_phase(current: int | None, year_ago: int | None) -> str | None:
    """12개월 전 대비 변화로 국면 판정. 비교할 값이 없으면 None(확인 불가)."""
    if not current or not year_ago or year_ago <= 0:
        return None
    change = (current - year_ago) / year_ago
    if change >= PHASE_THRESHOLD:
        return RISING
    if change <= -PHASE_THRESHOLD:
        return FALLING
    return FLAT


def current_ratio(target_snapshot, benchmark_snapshot, *, area_band: str) -> Calc | None:
    """지금 시점의 가격비율. 같은 면적밴드·같은 기준월끼리만."""
    if target_snapshot is None or benchmark_snapshot is None:
        return None
    a, b = target_snapshot["representative_price"], benchmark_snapshot["representative_price"]
    if not a or not b or b <= 0:
        return None
    if target_snapshot["as_of_ym"] != benchmark_snapshot["as_of_ym"]:
        raise ValueError(
            f"기준월이 다른 스냅샷으로 가격비율을 계산할 수 없습니다: "
            f"{target_snapshot['as_of_ym']} vs {benchmark_snapshot['as_of_ym']}")
    if target_snapshot["area_band"] != benchmark_snapshot["area_band"]:
        raise ValueError(
            f"면적밴드가 다른 스냅샷을 비교할 수 없습니다: "
            f"{target_snapshot['area_band']} vs {benchmark_snapshot['area_band']}")

    ratio = a / b
    # 표본이 적은 쪽이 비율의 신뢰도를 결정한다.
    confidence = _weaker(target_snapshot["confidence"], benchmark_snapshot["confidence"])
    return Calc(
        value=ratio, unit="ratio",
        formula="대상 대표가격 ÷ 비교단지 대표가격",
        inputs={
            "대상": units.fmt_eok(a), "비교단지": units.fmt_eok(b),
            "기준월": target_snapshot["as_of_ym"], "면적": area_band,
        },
        intermediates={
            "가격비율": units.fmt_pct(ratio),
            "표본": f"대상 {target_snapshot['sample_n']}건({target_snapshot['confidence']}) · "
                   f"비교 {benchmark_snapshot['sample_n']}건({benchmark_snapshot['confidence']})",
            "신뢰도": confidence,
        },
        grade="CONFIRMED",
    )


_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _weaker(a: str, b: str) -> str:
    return max((a, b), key=lambda c: _ORDER.get(c, 2))


@dataclass(frozen=True)
class Norm:
    window_key: str
    median: float
    mean: float
    p25: float | None
    p75: float | None
    sample_n: int
    from_ym: str | None
    to_ym: str | None
    calc: Calc


def _percentile(ordered: list[float], q: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] * (1 - (pos - lo)) + ordered[hi] * (pos - lo)


def _summarize(rows: list[dict], window_key: str, note: str) -> Norm | None:
    if not rows:
        return None
    values = sorted(float(r["ratio"]) for r in rows)
    yms = sorted(r["as_of_ym"] for r in rows)
    calc = Calc(
        value=statistics.median(values), unit="ratio",
        formula=f"{window_key} 구간 가격비율 {len(values)}개의 중앙값",
        inputs={"구간": note, "표본": len(values),
                "기간": f"{yms[0]} ~ {yms[-1]}"},
        intermediates={
            "중앙값": units.fmt_pct(statistics.median(values)),
            "평균": units.fmt_pct(statistics.fmean(values)),
            "25%": units.fmt_pct(_percentile(values, 0.25)),
            "75%": units.fmt_pct(_percentile(values, 0.75)),
            "최소~최대": f"{units.fmt_pct(values[0])} ~ {units.fmt_pct(values[-1])}",
        },
        evidence=(SELF_JUDGED,) if window_key in (WINDOW_RISING, WINDOW_FALLING,
                                                   WINDOW_FLAT) else (),
        grade="CONFIRMED",
    )
    return Norm(window_key, statistics.median(values), statistics.fmean(values),
                _percentile(values, 0.25), _percentile(values, 0.75),
                len(values), yms[0], yms[-1], calc)


def normals(history: list[dict], *, as_of_ym: str) -> list[Norm]:
    """구간별 Historical Normal Ratio.

    history 는 `price_ratio_history` 행들(dict). ratio · as_of_ym · market_phase 필요.
    표본이 없는 구간은 아예 만들지 않는다 — 0건짜리 '평균'은 숫자가 아니다.
    """
    if not history:
        return []
    out: list[Norm] = []

    def cutoff(years: int) -> str:
        y, m = int(as_of_ym[:4]), int(as_of_ym[4:])
        return f"{y - years:04d}{m:02d}"

    windows = [
        (WINDOW_ALL, "전체 기간", lambda r: True),
        (WINDOW_5Y, "최근 5년", lambda r: r["as_of_ym"] >= cutoff(5)),
        (WINDOW_10Y, "최근 10년", lambda r: r["as_of_ym"] >= cutoff(10)),
        (WINDOW_RISING, "시장 상승기(자체 판정)", lambda r: r.get("market_phase") == RISING),
        (WINDOW_FALLING, "시장 하락기(자체 판정)", lambda r: r.get("market_phase") == FALLING),
        (WINDOW_FLAT, "시장 횡보기(자체 판정)", lambda r: r.get("market_phase") == FLAT),
    ]
    for key, note, keep in windows:
        norm = _summarize([r for r in history if keep(r)], key, note)
        if norm:
            out.append(norm)
    return out


def gap_vs_normal(current: Calc | None, norm: Norm | None) -> Calc | None:
    """지금 비율이 과거 정상 비율에서 얼마나 벌어졌나.

    벌어졌다는 사실만 말하고 "저평가"라고 부르지 않는다 — 벌어진 데는 이유가 있을 수
    있고, 그 이유를 찾는 건 PHASE 5(촉매)의 일이다.
    """
    if current is None or norm is None or not norm.median:
        return None
    delta = current.value - norm.median
    return Calc.derive(
        delta, unit="%p",
        formula="현재 가격비율 − 과거 정상 가격비율(중앙값)",
        sources={"현재비율": current, "정상비율": norm.calc},
        intermediates={
            "현재": units.fmt_pct(current.value),
            f"정상({norm.window_key}, {norm.sample_n}개월)": units.fmt_pct(norm.median),
            "차이": units.fmt_pct(delta, sign=True),
            "해석": ("현재 비율이 과거보다 낮습니다 — 상대적으로 가격 격차가 벌어진 상태입니다. "
                    "격차가 벌어진 이유(공급·호재·규제 변화)를 확인해야 저평가인지 알 수 있습니다."
                    if delta < 0 else
                    "현재 비율이 과거보다 높습니다 — 비교단지 대비 이미 많이 좁혀진 상태입니다."),
        },
    )
