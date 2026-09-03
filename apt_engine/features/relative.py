"""비교단지 대비 격차 (§10).

`relative` 모델이 유일하게 쓰는 feature 인데 그동안 아무도 만들지 않았다.
scoring/models.py 의 SPEC 은 `("relative_gap", True)` 를 가리키고 있었고,
그 키를 내놓는 코드가 없어서 백테스트에서 이 모델은 계속 '미평가' 였다 —
heuristic 가중치로 15% 를 차지하는 모델이 통째로 비어 있었던 것이다.

── 무엇을 재는가 ──────────────────────────────────────────────────
비교단지와의 **가격비율**이 과거 정상 수준에서 얼마나 벌어졌는가.

    relative_gap = 정상비율(중앙값) − 현재비율

양수면 비교단지 대비 예전보다 싸졌다는 뜻이다. 이 값이 클수록 좋다고 보는
이유는 §10 이 말하는 '따라 올라갈 자리' 이기 때문이고, 그래서 SPEC 의
higher_is_better 가 True 다.

**벌어졌다는 사실만 말하고 저평가라고 부르지 않는다.** 벌어진 데는 이유가
있을 수 있다(공급·규제·상품성 변화). 그 이유를 찾는 것은 촉매(PHASE 5)의
일이고, 여기서는 관측된 격차만 낸다.

── 왜 단순 가격비교가 아닌가 ────────────────────────────────────
옆 단지보다 싸다는 것만으로는 아무 뜻이 없다. 상품성이 달라 **원래** 10%
싼 것이 정상이면 10% 싼 상태는 제자리다. 그래서 각 쌍마다 과거 비율의
중앙값(ratio_norm)을 기준으로 두고, 거기서 벗어난 만큼만 격차로 센다.
"""
from __future__ import annotations

import sqlite3
import statistics

from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features.base import Feature, Status
from apt_engine.trace import Calc

# 비교단지가 이보다 적으면 격차를 믿지 않는다. 한 곳과의 비율은 그 한 곳의
# 사정(리모델링·급매)에 통째로 휘둘린다.
MIN_PEERS = 3

# 정상비율을 만든 표본이 이보다 짧으면 '정상' 이라 부를 수 없다.
MIN_NORM_MONTHS = 12


def relative_gap(conn: sqlite3.Connection, complex_id: int, area_band: str, *,
                 as_of: cutoff_mod.AsOf) -> Feature:
    """비교단지 대비 가격비율이 정상에서 벌어진 정도.

    비교단지·비율·정상비율 중 하나라도 없으면 만들지 않는다. 0 으로 두면
    '격차가 없다' 는 뜻이 되어 버리는데, 그건 모른다는 것과 다르다.
    """
    peers = conn.execute(
        "SELECT benchmark_complex_id FROM benchmark_relation "
        " WHERE complex_id = ? AND area_band = ?",
        (complex_id, area_band)).fetchall()
    if not peers:
        return Feature.missing(
            "relative_gap",
            "비교단지가 없습니다 — `cli relative build` 로 만들 수 있습니다")

    observable = as_of.observable
    gaps: list[float] = []
    detail_rows: list[str] = []
    skipped_short = 0

    with cutoff_mod.guard(conn, observable) as g:
        for p in peers:
            bid = p["benchmark_complex_id"]
            # 컷오프 시점에 관측 가능한 가장 최근 비율.
            cur = g.execute(
                "SELECT ratio, as_of_ym FROM price_ratio_history "
                " WHERE complex_id = ? AND benchmark_complex_id = ? "
                "   AND area_band = ? AND as_of_ym <= ? "
                " ORDER BY as_of_ym DESC LIMIT 1",
                (complex_id, bid, area_band, observable.ym)).fetchone()
            if cur is None or cur["ratio"] is None:
                continue
            # 정상비율은 그 시점까지의 표본으로 만든 것만 쓴다. 나중 구간까지
            # 포함해 만든 정상비율을 과거 시점이 쓰면 look-ahead 다(§18).
            norm = g.execute(
                "SELECT median_ratio, sample_n, window_key, to_ym FROM ratio_norm "
                " WHERE complex_id = ? AND benchmark_complex_id = ? "
                "   AND area_band = ? AND to_ym <= ? "
                " ORDER BY sample_n DESC LIMIT 1",
                (complex_id, bid, area_band, observable.ym)).fetchone()
            if norm is None or not norm["median_ratio"]:
                continue
            if (norm["sample_n"] or 0) < MIN_NORM_MONTHS:
                skipped_short += 1
                continue
            gap = float(norm["median_ratio"]) - float(cur["ratio"])
            gaps.append(gap)
            detail_rows.append(
                f"현재 {float(cur['ratio']):.3f} / 정상 {float(norm['median_ratio']):.3f} "
                f"({norm['window_key']}, {norm['sample_n']}개월) → {gap:+.3f}")

    if len(gaps) < MIN_PEERS:
        why = f"쓸 수 있는 비교단지가 {len(gaps)}개뿐입니다 (최소 {MIN_PEERS}개)"
        if skipped_short:
            why += (f" — 정상비율 표본이 {MIN_NORM_MONTHS}개월 미만이라 "
                    f"{skipped_short}개를 뺐습니다")
        return Feature.missing("relative_gap", why)

    value = statistics.median(gaps)

    # 비교단지가 많고 서로 같은 방향을 가리킬수록 믿을 만하다. 방향이 갈리면
    # 그 격차는 이 단지의 사정이 아니라 비교단지 쪽 사정일 수 있다.
    same_way = sum(1 for x in gaps if (x > 0) == (value > 0))
    agreement = same_way / len(gaps)
    confidence = min(1.0, 0.30 + 0.10 * len(gaps)) * agreement

    return Feature(
        key="relative_gap", value=value, unit="",
        confidence=round(confidence, 3), status=Status.OK,
        detail={
            "비교단지": len(gaps),
            "방향 일치": f"{agreement:.0%}",
            "쌍별": detail_rows[:5],
            "뜻": ("양수면 비교단지 대비 예전보다 싸진 상태입니다. "
                  "벌어진 이유(공급·규제·상품성)를 확인해야 저평가인지 알 수 있습니다"),
        },
        calc=Calc("relative_gap",
                  "median(정상 가격비율 − 현재 가격비율)",
                  {"비교단지 수": len(gaps), "방향 일치": f"{agreement:.0%}"}))
