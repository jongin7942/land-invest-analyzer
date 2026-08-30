"""단지 하나의 촉매 모으기 (요구사항 5·55).

**근거 없는 촉매는 만들지 않는다.** 각 촉매는 무엇을 보고 만들었는지(evidence)를
반드시 갖고, 그게 없으면 저장 단계에서 스키마가 거부한다.

그리고 호재를 투자기간과 연결한다. "GTX 옵니다"로 끝내지 않고,
2035년 개통 예정인데 투자기간이 5년이면 "개통 자체는 기간 밖, 기대감만 기간 안"이라고
말한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from apt_engine import units
from apt_engine.catalyst import supply as supply_mod
from apt_engine.catalyst import transit as transit_mod
from apt_engine.trace import Calc


@dataclass(frozen=True)
class CatalystItem:
    kind: str
    label: str
    direction: str
    expected_year: int | None
    within_horizon: bool | None
    confidence: str
    evidence: dict
    calc: Calc
    station_id: int | None = None


def from_transit(conn: sqlite3.Connection, complex_id: int, *, as_of: str,
                 years: int, min_stage: str = "계획") -> list[CatalystItem]:
    """가까운 역들을 촉매로. 이미 개통한 역도 남긴다 — 가격에 반영됐다는 사실이 정보다."""
    items = []
    for st in transit_mod.nearby(conn, complex_id):
        if not transit_mod.stage_at_least(st.status, min_stage):
            continue
        calc = transit_mod.to_calc(st, as_of=as_of, years=years)
        within, note = st.horizon_label(as_of=as_of, years=years)
        expected = None
        if st.opened_ym:
            expected = int(st.opened_ym[:4])
        elif st.expected_open_ym:
            expected = int(st.expected_open_ym[:4])

        items.append(CatalystItem(
            kind="교통",
            label=f"{st.project_name} {st.name} ({st.meters:,.0f}m)",
            # 이미 개통한 역은 앞으로의 촉매가 아니다 — 이미 반영됐다.
            direction="중립" if st.opened else "상승",
            expected_year=expected,
            within_horizon=within,
            confidence=st.confidence,
            evidence={
                "단계": st.status,
                "단계확정일": st.status_date,
                "개통": st.opened_ym or f"예정 {st.expected_open_ym or '미상'}",
                "거리": f"직선 {st.meters:,.0f}m (도보 추정 {st.walk_minutes}분)",
                "투자기간": note,
                "검증": "확인됨" if st.verified else "미검증",
            },
            calc=calc,
            station_id=st.station_id,
        ))
    return items


def from_supply(conn: sqlite3.Connection, complex_id: int, *, as_of_ym: str,
                radius_m: int = 3000) -> CatalystItem | None:
    """향후 공급은 대개 하방 촉매다. 데이터가 없으면 만들지 않는다."""
    row = conn.execute(
        "SELECT id, lawd_cd, lat, lon, apt_households FROM complex WHERE id = ?",
        (complex_id,)).fetchone()
    if row is None:
        return None

    calc = supply_mod.analyze(conn, lawd_cd=row["lawd_cd"], as_of_ym=as_of_ym,
                              lat=row["lat"], lon=row["lon"], radius_m=radius_m,
                              stock_households=row["apt_households"])
    if calc.value is None:
        return None      # 확인 불가 — 촉매로 만들지 않는다

    households = int(calc.value)
    return CatalystItem(
        kind="공급",
        label=f"향후 1~2년 입주 {households:,}세대",
        direction="하락" if households > 0 else "중립",
        expected_year=int(as_of_ym[:4]) + 2,
        within_horizon=True,
        confidence="MEDIUM",
        evidence={
            "집계기준": calc.intermediates.get("집계 기준"),
            "1~2년": calc.intermediates.get("1~2년"),
            "3~5년": calc.intermediates.get("3~5년"),
        },
        calc=calc,
    )


def summarize(items: list[CatalystItem], *, years: int) -> Calc:
    """촉매 전체 요약. 방향별로 세되 점수로 뭉개지 않는다."""
    if not items:
        return Calc(
            value=None, unit="건",
            formula="투자기간 안의 촉매 수",
            inputs={"투자기간": f"{years}년"},
            intermediates={"주의": "촉매 데이터가 없습니다 — 확인 불가. "
                                  "`cli transit import` / `cli supply import` 로 "
                                  "교통·공급을 채워 넣으세요."},
            grade="ESTIMATED")

    inside = [i for i in items if i.within_horizon is True]
    outside = [i for i in items if i.within_horizon is False]
    unknown = [i for i in items if i.within_horizon is None]

    return Calc(
        value=len(inside), unit="건",
        formula="투자기간 안에 일어나는 촉매 수",
        inputs={"투자기간": f"{years}년", "전체 촉매": len(items)},
        intermediates={
            "기간 안": [i.label for i in inside] or "없음",
            "기간 밖": [i.label for i in outside] or "없음",
            "시점 미상": [i.label for i in unknown] or "없음",
            "상승 방향": len([i for i in items if i.direction == "상승"]),
            "하락 방향": len([i for i in items if i.direction == "하락"]),
            "주의": "촉매의 수가 곧 상승폭은 아니다. 이미 개통한 역은 가격에 반영됐을 "
                   "가능성이 높아 '중립'으로 둔다.",
        },
        grade="ESTIMATED",
    )
