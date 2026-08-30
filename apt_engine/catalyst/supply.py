"""공급량 분석 (요구사항 19).

구 단위 총량만 보면 틀린다. 같은 구라도 반대편 끝에 5,000세대가 들어오는 것과
바로 옆 블록에 들어오는 것은 다른 이야기다. 그래서 좌표가 있으면 **반경별**로,
없으면 시군구 단위로 떨어뜨려 계산하고 **어느 기준으로 셌는지 밝힌다.**

그리고 요구사항 19대로 1~2년과 3~5년을 나눈다. 당장의 전세 압력과
중기 매매 압력은 다른 문제다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from apt_engine import geo, units
from apt_engine.trace import Calc, Evidence

# 요구사항 19의 반경들
RADII_M = (1000, 3000, 5000)

# 아직 안 지어진 것과 지어질 게 확실한 것은 무게가 다르다.
STAGE_WEIGHT = {"계획": 0.4, "분양": 0.8, "착공": 1.0, "입주예정": 1.0, "입주완료": 0.0}

SUPPLY_EVIDENCE = Evidence(
    source="입주물량 (수기 입력)",
    note="단지 단위 입주물량은 공공 API 가 없다. 분양·착공 단계는 일정이 밀릴 수 있다.")


@dataclass(frozen=True)
class Bucket:
    label: str
    households: int
    weighted: int
    projects: int
    by_stage: dict[str, int] = field(default_factory=dict)


def _year_of(ym: str) -> int:
    return int(str(ym)[:4])


def near_supply(conn: sqlite3.Connection, *, lawd_cd: str, as_of_ym: str,
                lat: float | None = None, lon: float | None = None,
                radius_m: int | None = None) -> tuple[list[sqlite3.Row], str]:
    """공급 목록과 **어떤 기준으로 골랐는지**.

    좌표가 있으면 반경, 없으면 시군구 전체. 기준을 숨기면 "3km 이내 2,000세대"와
    "구 전체 2,000세대"가 같은 숫자로 보인다.
    """
    rows = conn.execute(
        "SELECT * FROM supply_plan WHERE lawd_cd = ? AND move_in_ym >= ? "
        "AND stage != '입주완료' ORDER BY move_in_ym",
        (lawd_cd, as_of_ym)).fetchall()

    if radius_m is None or not geo.has_coords(lat, lon):
        return list(rows), f"{lawd_cd} 시군구 전체 (단지 좌표 없음 — 반경 계산 불가)"

    kept = []
    no_coord = 0
    for r in rows:
        if not geo.has_coords(r["lat"], r["lon"]):
            no_coord += 1
            continue
        if geo.haversine_m(lat, lon, r["lat"], r["lon"]) <= radius_m:
            kept.append(r)
    basis = f"직선 {radius_m:,}m 이내"
    if no_coord:
        basis += f" (좌표 없는 공급 {no_coord}건 제외 — 실제 물량은 더 클 수 있음)"
    return kept, basis


def bucketize(rows: list[sqlite3.Row], *, as_of_ym: str) -> dict[str, Bucket]:
    """1~2년 / 3~5년으로 나눈다."""
    base_year = _year_of(as_of_ym)
    windows = {"1~2년": (base_year, base_year + 2), "3~5년": (base_year + 3, base_year + 5)}

    out: dict[str, Bucket] = {}
    for label, (lo, hi) in windows.items():
        picked = [r for r in rows if lo <= _year_of(r["move_in_ym"]) <= hi]
        households = sum(r["households"] for r in picked)
        weighted = sum(int(r["households"] * STAGE_WEIGHT.get(r["stage"], 0.5))
                       for r in picked)
        by_stage: dict[str, int] = {}
        for r in picked:
            by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + r["households"]
        out[label] = Bucket(label, households, weighted, len(picked), by_stage)
    return out


def analyze(conn: sqlite3.Connection, *, lawd_cd: str, as_of_ym: str,
            lat: float | None = None, lon: float | None = None,
            radius_m: int | None = None,
            stock_households: int | None = None) -> Calc:
    """향후 공급. 데이터가 없으면 "0세대"가 아니라 "확인 불가"다."""
    total = conn.execute("SELECT COUNT(*) FROM supply_plan").fetchone()[0]
    if total == 0:
        return Calc(
            value=None, unit="세대",
            formula="향후 입주물량 합계",
            inputs={"지역": lawd_cd, "기준월": as_of_ym},
            intermediates={"주의": "입주물량 데이터가 입력되지 않았습니다 — 확인 불가. "
                                  "`cli supply template` 로 서식을 받아 채워 넣으세요."},
            evidence=(SUPPLY_EVIDENCE,), grade="ESTIMATED")

    rows, basis = near_supply(conn, lawd_cd=lawd_cd, as_of_ym=as_of_ym,
                              lat=lat, lon=lon, radius_m=radius_m)
    buckets = bucketize(rows, as_of_ym=as_of_ym)
    near_term = buckets["1~2년"]

    intermediates = {
        "집계 기준": basis,
        "1~2년": f"{near_term.households:,}세대 ({near_term.projects}개 단지)"
                 + (f" · 단계보정 {near_term.weighted:,}세대"
                    if near_term.weighted != near_term.households else ""),
        "3~5년": f"{buckets['3~5년'].households:,}세대 "
                 f"({buckets['3~5년'].projects}개 단지)",
        "1~2년 단계별": near_term.by_stage or "없음",
    }
    if stock_households:
        pressure = near_term.households / stock_households
        intermediates["기존 재고 대비"] = (
            f"{units.fmt_pct(pressure)} — 이 단지 세대수 {stock_households:,} 기준")
    if not rows:
        intermediates["해석"] = "이 기준 안에 예정된 입주물량이 없습니다."

    return Calc(
        value=near_term.households, unit="세대",
        formula="향후 1~2년 입주 예정 세대수 합계",
        inputs={"지역": lawd_cd, "기준월": as_of_ym,
                "반경": f"{radius_m:,}m" if radius_m else "시군구 전체"},
        intermediates=intermediates,
        evidence=(SUPPLY_EVIDENCE,),
        # 분양·착공 단계는 일정이 밀린다. 확정으로 표시하지 않는다.
        grade="ESTIMATED",
    )
