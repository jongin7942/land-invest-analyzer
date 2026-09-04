"""Catalyst Alpha — 호재를 단순 가점하지 않는다 (지시서 §17·§18).

    Catalyst Alpha = Economic Impact
                   × Realization Probability
                   × Time Relevance
                   × Complex Exposure
                   × (1 − Priced In Fraction)

다섯 항목을 곱하는 이유는 **하나만 0이어도 알파가 0** 이어야 하기 때문이다.
GTX 가 아무리 크게 좋아도(Economic Impact) 이미 가격에 다 반영돼 있으면
(Priced In = 1) 지금 사서 얻을 것은 없다. 발표만 됐고 실현확률이 낮으면
(Realization Probability ≈ 0) 마찬가지다.

**다섯 항목 중 하나라도 모르면 알파를 만들지 않는다.** 모르는 걸 1.0 으로 두면
가장 데이터가 부실한 호재가 가장 큰 알파를 받는다. 그건 정확히 반대다.

§18 Look-ahead 방지: `catalyst_state` 에서 **컷오프 이전 마지막 상태**만 읽는다.
2024년에 확정된 노선을 2023년 모델이 알 수 없다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from apt_engine import units
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features.base import Feature, Status, combine
from apt_engine.trace import Calc, Evidence

# 단계별 실현확률의 **하한**. state 에 값이 없을 때 쓰는 게 아니라,
# 적힌 값이 단계에 비해 지나치게 낙관적인지 확인하는 용도다.
STAGE_CEILING = {
    "발표": 0.30, "계획": 0.40, "예비타당성": 0.50, "기본계획": 0.65,
    "착공": 0.90, "공사중": 0.95, "완공예정": 0.98, "완공": 1.00, "무산": 0.0,
}

STAGE_NOTE = ("단계별 상한은 관측된 실현율이 아니라 판정 기준입니다. "
              "적힌 실현확률이 단계에 비해 낙관적이면 상한으로 깎습니다")


@dataclass(frozen=True)
class CatalystView:
    catalyst_id: int
    key: str
    kind: str
    name: str
    stage: str
    as_of: str
    realization: float | None
    economic_impact: int | None
    priced_in: float | None
    expected_completion: str | None
    exposure: float | None
    exposure_method: str | None
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing


def _time_relevance(expected_completion: str | None, *, as_of: str,
                    horizon_years: int) -> tuple[float | None, str]:
    """투자기간 안에 일어나는가 (§55 '호재를 투자기간과 연결').

    기간 밖이면 0 이 아니라 **작은 값**이다 — 기대감은 먼저 움직이기 때문이다.
    다만 그 기대감을 얼마로 볼지는 우리가 정할 수 없어서, 기간 안이면 1,
    밖이면 남은 거리에 반비례하게 두고 그 사실을 근거에 적는다.
    """
    if not expected_completion:
        return None, "완공 예정시점이 없어 투자기간과 연결할 수 없습니다"
    try:
        done_year = int(str(expected_completion)[:4])
    except ValueError:
        return None, f"완공 예정시점을 읽을 수 없습니다: {expected_completion!r}"
    now_year = int(as_of[:4])
    end_year = now_year + horizon_years
    if done_year <= end_year:
        return 1.0, f"{done_year}년 완공 예정 — 투자기간({horizon_years}년) 안"
    over = done_year - end_year
    value = max(0.1, 1.0 / (1.0 + over))
    return value, (f"{done_year}년 완공 예정 — 투자기간 밖 {over}년. "
                   f"개통 자체가 아니라 기대감만 기간 안에 들어온다")


def load(conn: sqlite3.Connection, complex_id: int, *,
         as_of: cutoff_mod.AsOf, price: int | None = None) -> list[CatalystView]:
    """이 단지에 걸린 호재들의 **그 시점 상태**.

    `price` 를 주면 `impact_ratio` 가 적힌 호재의 경제효과를 그 단지 가격에
    맞춰 만든다(마이그레이션 025). 같은 역 앞이라도 3억짜리와 15억짜리가 같은
    금액을 받으면 안 되기 때문이다. 측정되는 것이 애초에 비율이라(역세권/비역세권
    가격비율의 변화) 비율로 적고 금액은 읽을 때 만드는 편이 원본에 가깝다.
    """
    observable = as_of.observable
    with cutoff_mod.guard(conn, observable) as g:
        rows = g.execute(
            "SELECT e.catalyst_id, e.exposure, e.method, e.as_of AS exp_as_of, "
            "       c.catalyst_key, c.catalyst_type, c.name "
            "  FROM catalyst_exposure e JOIN catalyst c ON c.id = e.catalyst_id "
            " WHERE e.complex_id = ? "
            "   AND (e.as_of IS NULL OR e.as_of <= ?)",
            (complex_id, observable.day)).fetchall()

        out: list[CatalystView] = []
        for r in rows:
            state = g.execute(
                "SELECT * FROM catalyst_state WHERE catalyst_id = ? AND as_of <= ? "
                " ORDER BY as_of DESC LIMIT 1",
                (r["catalyst_id"], observable.day)).fetchone()
            if state is None:
                # 그 시점에 이 호재의 상태를 아직 몰랐다. 노출도만 있고 상태가 없으면
                # 호재로 세지 않는다.
                continue

            missing = []
            realization = state["realization_probability"]
            if realization is None:
                missing.append("실현확률")
            # 비율이 적혀 있으면 단지 가격에 맞춰 금액을 만든다. 금액이 직접
            # 적힌 경우(확정 보상금 등)보다 비율 쪽을 우선한다 - 단지 가격에
            # 따라 움직이는 편이 실제에 가깝다.
            ratio = state["impact_ratio"] if "impact_ratio" in state.keys() else None
            impact = state["economic_impact"]
            if ratio is not None and price:
                impact = int(price * float(ratio))
            elif ratio is not None and not price:
                impact = None
                missing.append("경제효과(비율은 있으나 단지 대표가격을 못 구함)")
            if impact is None and "경제효과(비율은 있으나 단지 대표가격을 못 구함)" not in missing:
                missing.append("경제효과")
            priced = state["priced_in_fraction"]
            if priced is None:
                missing.append("선반영률")
            if r["exposure"] is None:
                missing.append("노출도")

            out.append(CatalystView(
                catalyst_id=int(r["catalyst_id"]), key=r["catalyst_key"],
                kind=r["catalyst_type"], name=r["name"], stage=state["stage"],
                as_of=state["as_of"],
                realization=float(realization) if realization is not None else None,
                economic_impact=int(impact) if impact is not None else None,
                priced_in=float(priced) if priced is not None else None,
                expected_completion=state["expected_completion"],
                exposure=float(r["exposure"]) if r["exposure"] is not None else None,
                exposure_method=r["method"], missing=missing))
    return out


def alpha_of(view: CatalystView, *, as_of: str,
             horizon_years: int) -> tuple[int | None, dict]:
    """호재 하나의 남은 알파(원). 항목이 하나라도 없으면 None."""
    detail: dict = {
        "호재": f"{view.name} ({view.kind})", "단계": view.stage,
        "상태 기준일": view.as_of,
    }
    relevance, relevance_note = _time_relevance(
        view.expected_completion, as_of=as_of, horizon_years=horizon_years)
    detail["시간 적합성"] = relevance_note

    missing = list(view.missing)
    if relevance is None:
        missing.append("시간 적합성")
    if missing:
        detail["확인 불가"] = missing
        detail["주의"] = ("모르는 항목을 1.0 으로 두지 않았습니다. "
                        "그러면 데이터가 부실한 호재가 가장 큰 알파를 받습니다")
        return None, detail

    ceiling = STAGE_CEILING.get(view.stage, 0.3)
    realization = min(view.realization, ceiling)
    if realization < view.realization:
        detail["실현확률 조정"] = (f"{view.realization:.0%} → {realization:.0%} "
                             f"('{view.stage}' 단계 상한)")

    remaining = 1.0 - view.priced_in
    alpha = int(view.economic_impact * realization * relevance
                * view.exposure * remaining)

    detail.update({
        "경제효과": units.fmt_eok(view.economic_impact),
        "실현확률": f"{realization:.0%}",
        "시간 적합성 값": f"{relevance:.2f}",
        "노출도": f"{view.exposure:.0%} ({view.exposure_method})",
        "선반영률": f"{view.priced_in:.0%}",
        "남은 알파": units.fmt_eok(alpha),
        "산식": "경제효과 × 실현확률 × 시간적합성 × 노출도 × (1 − 선반영률)",
    })
    if view.priced_in >= 0.8:
        detail["선반영 경고"] = ("이미 대부분 가격에 반영됐습니다. "
                            "호재가 커도 지금 사서 얻을 것은 적습니다(§17)")
    return alpha, detail


def feature(conn: sqlite3.Connection, complex_id: int, *,
            as_of: cutoff_mod.AsOf, horizon_years: int = 5,
            price: int | None = None) -> Feature:
    """남은 호재 알파 합계. 가격 대비 비율로 낸다(단지 크기에 무관하게 비교하려고)."""
    key = "catalyst_alpha"
    views = load(conn, complex_id, as_of=as_of, price=price)
    if not views:
        return Feature.missing(key, "그 시점에 알려진 호재가 없습니다")

    total = 0
    details: list[dict] = []
    incomplete = 0
    for v in views:
        alpha, detail = alpha_of(v, as_of=as_of.day, horizon_years=horizon_years)
        details.append(detail)
        if alpha is None:
            incomplete += 1
        else:
            total += alpha

    if incomplete == len(views):
        return Feature.missing(
            key, f"호재 {len(views)}개 모두 항목이 부족합니다 "
                 f"(실현확률·경제효과·선반영률·노출도 중 누락)")

    if price:
        value = total / price
        unit = ""
    else:
        value = float(total)
        unit = "원"

    # 항목이 빠진 호재가 많을수록 덜 믿는다.
    conf = combine(1.0 - incomplete / len(views), 0.7)
    calc = Calc(
        value=value, unit=unit,
        formula="Catalyst Alpha = Σ(경제효과 × 실현확률 × 시간적합성 × 노출도 "
                "× (1 − 선반영률))",
        inputs={"호재 수": len(views), "투자기간": f"{horizon_years}년",
                "기준가": units.fmt_eok(price) if price else "미입력"},
        intermediates={
            "호재별": details,
            "합계": units.fmt_eok(total),
            "계산 못한 호재": f"{incomplete}/{len(views)}개",
            "단계 상한": STAGE_CEILING,
            "상한 성격": STAGE_NOTE,
            "원칙": "다섯 항목 중 하나라도 0 이면 알파는 0 이다. "
                  "이미 반영된 호재는 지금 사서 얻을 것이 없다(§17)",
        },
        evidence=(Evidence(source="호재 원장 (catalyst_state)",
                           note=f"{as_of.observable.day} 이전 상태만 사용(§18)"),),
        grade="ESTIMATED",
    )
    return Feature(key, value, unit, conf, Status.OK, calc.intermediates,
                   calc).with_confidence(conf)


def all_features(conn: sqlite3.Connection, complex_id: int, *,
                 as_of: cutoff_mod.AsOf, horizon_years: int = 5,
                 price: int | None = None) -> list[Feature]:
    return [feature(conn, complex_id, as_of=as_of, horizon_years=horizon_years,
                    price=price)]
