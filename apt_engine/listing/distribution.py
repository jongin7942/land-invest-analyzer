"""호가 분포와 실거래 괴리 (요구사항 4·8).

**최저호가 하나를 현재 시장가격이라고 단정하지 않는다.** 최저호가는 대개
급매·1층·수리필요 같은 이유가 있고, 그걸 시세로 삼으면 "지금 사면 싸다"는
결론이 자동으로 나온다.

그래서 항상 두 줄로 낸다:

    최저호가         6.05억  (급매 · 저층)
    정상매물 최저호가  6.20억

그리고 호가는 실거래가 아니다. 파는 사람의 희망가일 뿐이라, 실거래와의 괴리를
따로 계산해 "지금 호가가 실거래를 얼마나 앞서 있는지"를 본다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine import units
from apt_engine.listing import dedupe
from apt_engine.price import representative
from apt_engine.trace import Calc, Evidence

LISTING_EVIDENCE = Evidence(
    source="매물 호가(수기 입력)",
    note="호가는 매도 희망가이며 체결가가 아니다. 실거래와 섞어 계산하지 않는다.")


@dataclass(frozen=True)
class Distribution:
    """한 단지·한 면적밴드·한 거래유형의 호가 분포."""
    count: int
    normal_count: int
    special_count: int
    dedupe: dedupe.DedupeResult

    low: int | None                 # 최저호가 (특수매물 포함)
    low_normal: int | None          # 정상매물 최저호가
    p10: int | None
    p25: int | None
    median: int | None
    mean: int | None
    p75: int | None
    high: int | None

    by_floor_group: dict[str, dict]
    special_flags: dict[str, int]
    calc: Calc

    @property
    def low_is_special(self) -> bool:
        """최저호가가 특수매물인가 — 그렇다면 그 값을 시세로 쓰면 안 된다."""
        return (self.low is not None and self.low_normal is not None
                and self.low < self.low_normal)


def _stats(prices: list[int]) -> dict:
    if not prices:
        return {}
    ordered = sorted(prices)
    q = representative.quartiles(ordered)
    return {
        "low": ordered[0], "high": ordered[-1],
        "p10": int(units.won_round(representative._percentile(ordered, 0.10))),
        "p25": q["p25"], "median": q["p50"], "p75": q["p75"],
        "mean": int(units.won_round(sum(ordered) / len(ordered))),
    }


def analyze(listings: list[dict], *, trade_type: str = "매매") -> Distribution:
    """호가 분포. listings 는 **한 단지·한 면적밴드·한 거래유형**이어야 한다."""
    rows = [r for r in listings if r.get("trade_type") == trade_type]
    dd = dedupe.estimate(rows)

    prices = [int(r["price"]) for r in rows]
    normal = [int(r["price"]) for r in rows if not r.get("is_special")]
    stats = _stats(prices)
    normal_stats = _stats(normal)

    flags: dict[str, int] = {}
    for r in rows:
        for f in (r.get("special_flags") or []):
            flags[f] = flags.get(f, 0) + 1

    by_floor: dict[str, dict] = {}
    for group in ("저층", "중층", "고층"):
        group_prices = [int(r["price"]) for r in rows if r.get("floor_group") == group]
        if group_prices:
            by_floor[group] = {"n": len(group_prices), **_stats(group_prices)}

    low = stats.get("low")
    low_normal = normal_stats.get("low")

    inputs = {
        "매물 수": len(rows),
        "정상매물": len(normal),
        "특수매물": len(rows) - len(normal),
        "중복 제거 추정": dd.range_label,
    }
    intermediates = {
        "분포": {k: units.fmt_eok(v) for k, v in stats.items()},
        "정상매물 분포": {k: units.fmt_eok(v) for k, v in normal_stats.items()},
        "특수조건": flags,
        "층별": {g: f"{d['n']}건 · 중앙 {units.fmt_eok(d['median'])}"
                for g, d in by_floor.items()},
    }
    if low is not None and low_normal is not None and low < low_normal:
        intermediates["주의"] = (
            f"최저호가 {units.fmt_eok(low)} 는 특수매물입니다. "
            f"정상매물 최저호가는 {units.fmt_eok(low_normal)} 입니다.")

    calc = Calc(
        value=normal_stats.get("median") or stats.get("median"),
        unit="원",
        formula=f"{trade_type} 호가 {len(rows)}건의 분포 "
                f"(정상매물 {len(normal)}건 기준 중앙값)",
        inputs=inputs,
        intermediates=intermediates,
        evidence=(LISTING_EVIDENCE,),
        # 호가는 관측된 사실이다(체결가가 아닐 뿐). 수집된 그대로라 CONFIRMED.
        grade="CONFIRMED",
    )

    return Distribution(
        count=len(rows), normal_count=len(normal), special_count=len(rows) - len(normal),
        dedupe=dd,
        low=low, low_normal=low_normal,
        p10=stats.get("p10"), p25=stats.get("p25"), median=stats.get("median"),
        mean=stats.get("mean"), p75=stats.get("p75"), high=stats.get("high"),
        by_floor_group=by_floor, special_flags=flags, calc=calc,
    )
