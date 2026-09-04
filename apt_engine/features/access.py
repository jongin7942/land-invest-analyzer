"""역세권 접근성 — 수준이 아니라 **벌어지는 속도**를 센다.

── 왜 '수준' 을 세면 안 되는가 ─────────────────────────────────────
역 가까운 집이 비싼 건 사실이다. 우리 데이터에서도 500m 안은 시군구 중앙값의
1.048배, 2km 밖은 0.914배다. 하지만 그 차이는 **이미 값에 들어 있다.**
역세권이라고 가점을 주면, 그 프리미엄을 이미 다 치르고 사는 사람에게
"좋은 매물" 이라고 말하는 것이 된다. 그건 알파가 아니라 영수증이다.

── 그럼 무엇이 알파인가 ────────────────────────────────────────────
격차가 **앞으로 더 벌어진다면** 그건 아직 반영되지 않은 것이다. 다 반영됐다면
한 번 뛰고 멈춰야 하는데, 실제로는 18년 내내 단조롭게 벌어졌다.

    같은 단지 1,856개 · 시군구 중앙값 대비 · 2008 -> 2025
        ~500m      1.024 -> 1.048     연 +0.131%p
        500m~1km   0.999 -> 0.997     연 +0.006%p
        1~1.5km    0.982 -> 0.965     연 -0.126%p
        1.5~2km    0.965 -> 0.943     연 -0.159%p
        2km 밖     0.950 -> 0.914     연 -0.150%p

밴드 다섯 개가 순서대로 늘어선다. 우연이면 이렇게 안 나온다.

방향도 중요하다. 가까운 쪽이 크게 오른 게 아니라(+0.024) **먼 쪽이 뒤처졌다**
(-0.036). 역세권이 특별해지는 게 아니라 역에서 먼 곳의 할인이 깊어지는 중이다.
그래서 이 feature 는 사실상 '역에서 멀어서 뒤처질 위험' 을 재는 쪽에 가깝다.

── 새 역 개통은 왜 안 쓰는가 ───────────────────────────────────────
써봤고, 측정해보니 0 이었다. 개통 사례 117건의 중앙값이 +0.15%, 오른 사례가
절반(51%)이다. 개통 -60개월부터 +24개월까지 7년을 펼쳐도 +1%p 를 못 넘고
매 구간이 동전 던지기였다(tools/event_study.py).

그래서 **미개통 역은 이 feature 에 넣지 않는다.** 계획·착공 중인 역 옆이라고
가점을 주면, 측정으로 부정된 가정에 돈을 거는 것이 된다. 여기서 세는 역은
'운영중' 과 '개통' 뿐이다.

── 값의 뜻 ─────────────────────────────────────────────────────────
`station_access_drift` = 그 밴드의 연 추세 × 투자기간.

5년이면 ~500m 는 +0.66%p, 2km 밖은 -0.75%p 다. 작다. 작은 게 맞고, 크게
보이도록 부풀리지 않는다. 순위를 가르는 데 쓰이는 것은 절대 크기가 아니라
후보들 사이의 백분위이므로(normalize.percentile_rank), 작아도 순서는 판다.
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from apt_engine.features.base import Feature, Status
from apt_engine.trace import Calc, Evidence

RULES = Path(__file__).resolve().parents[2] / "rules" / "station_access_drift.csv"

KEY = "station_access_drift"

# 이 feature 가 세는 역. 미개통 역은 알파를 만들지 않는다고 측정됐다.
OPEN_STATUSES = ("운영중", "개통")

# 밴드의 단지 수가 이보다 적으면 신뢰도를 깎는다. 1.5~2km(84개)와
# 2km 밖(79개)이 여기 걸린다 - 값은 쓰되 얇다는 사실을 같이 들고 다닌다.
THIN_PANEL = 150

MEASURE_NOTE = ("자체 측정값입니다. 2008~2025년 수도권 한 표본에서 나온 추세이고, "
                "앞으로도 이어진다는 보장은 없습니다")


@dataclass(frozen=True)
class Band:
    name: str
    max_meters: float | None      # None 이면 그 밖 전부
    annual_drift: float
    complexes: int
    years: int
    first_year: int
    last_year: int
    note: str


@lru_cache(maxsize=1)
def bands(path: str | None = None) -> tuple[Band, ...]:
    """측정된 밴드표. tools/measure_access_drift.py 가 만든다."""
    p = Path(path) if path else RULES
    if not p.exists():
        return ()
    out = []
    with p.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            upper = (r.get("max_meters") or "").strip()
            out.append(Band(
                name=r["band"],
                max_meters=float(upper) if upper else None,
                annual_drift=float(r["annual_drift"]),
                complexes=int(r["complexes"]),
                years=int(r["years"]),
                first_year=int(r["first_year"]),
                last_year=int(r["last_year"]),
                note=r.get("note") or ""))
    # 상한이 없는(가장 먼) 밴드는 언제나 맨 뒤에 온다.
    out.sort(key=lambda b: (b.max_meters is None, b.max_meters or 0))
    return tuple(out)


def band_for(meters: float | None, table: tuple[Band, ...]) -> Band | None:
    for b in table:
        if b.max_meters is None or (meters is not None and meters <= b.max_meters):
            return b
    return None


def nearest_open_station(conn: sqlite3.Connection, complex_id: int
                         ) -> tuple[float, str, str] | None:
    """가장 가까운 **다니고 있는** 역. (거리, 역 이름, 노선) 또는 None."""
    marks = ",".join("?" for _ in OPEN_STATUSES)
    row = conn.execute(
        f"SELECT d.meters, s.name, p.name AS project FROM station_distance d "
        f"  JOIN transit_station s ON s.id = d.station_id "
        f"  JOIN transit_project p ON p.id = s.project_id "
        f" WHERE d.complex_id = ? AND s.status IN ({marks}) "
        f" ORDER BY d.meters LIMIT 1",
        (complex_id, *OPEN_STATUSES)).fetchone()
    return (row["meters"], row["name"], row["project"]) if row else None


def has_coordinates(conn: sqlite3.Connection, complex_id: int) -> bool:
    row = conn.execute("SELECT lat FROM complex WHERE id = ?",
                       (complex_id,)).fetchone()
    return bool(row and row["lat"] is not None)


def drift(conn: sqlite3.Connection, complex_id: int, *,
          horizon_years: int | None = None,
          rules_path: str | None = None) -> Feature:
    """투자기간 동안 시군구 대비 벌어질(뒤처질) 폭. 측정된 추세 × 기간."""
    table = bands(rules_path)
    if not table:
        return Feature.missing(
            KEY, "밴드표(rules/station_access_drift.csv)가 없습니다 — "
                 "tools/measure_access_drift.py 로 만드세요")

    years = horizon_years or 3
    near = nearest_open_station(conn, complex_id)

    if near is None:
        # 좌표가 없으면 '멀다' 가 아니라 '모른다' 다. 이 둘을 섞으면
        # 좌표 없는 단지가 전부 최하위로 몰린다.
        if not has_coordinates(conn, complex_id):
            return Feature.missing(KEY, "단지 좌표가 없어 역까지 거리를 모릅니다")
        meters = None       # 좌표는 있는데 2km 안에 다니는 역이 없다
    else:
        meters = near[0]

    band = band_for(meters, table)
    if band is None:
        return Feature.missing(KEY, "거리에 맞는 밴드가 없습니다")

    value = band.annual_drift * years

    where = (f"{near[1]}({near[2]}) {meters:,.0f}m" if near
             else "2km 안에 다니는 역 없음")
    calc = Calc(
        value=value, unit="시군구 대비 %p",
        formula="밴드의 연 추세 × 투자기간",
        inputs={"가장 가까운 역": where, "밴드": band.name,
                "투자기간": f"{years}년"},
        intermediates={
            "연 추세": f"{band.annual_drift:+.3%}p",
            "기간 누적": f"{value:+.2%}p",
            "측정 표본": f"단지 {band.complexes}개 · "
                      f"{band.first_year}~{band.last_year}년 {band.years}개 해",
            "뜻": ("역세권이라 비싼 것은 이미 값에 있습니다. 이 값은 그 격차가 "
                  "앞으로 더 벌어질(또는 좁혀질) 속도만 셉니다"),
            "주의": MEASURE_NOTE,
        },
        evidence=(Evidence(
            source="자체 측정 — 같은 단지 balanced panel, 시군구 중앙값 정규화",
            note=band.note),),
        grade="ESTIMATED")

    # 얇은 밴드는 값을 쓰되 신뢰도를 깎는다. 측정에 쓴 해가 적어도 마찬가지다.
    confidence = 0.75
    if band.complexes < THIN_PANEL:
        confidence = 0.45
    if band.years < 10:
        confidence = min(confidence, 0.35)
    if near is None:
        # 2km 밖은 거리를 정확히 모른다 - station_distance 가 2km 에서 잘린다.
        confidence = min(confidence, 0.40)

    return Feature(key=KEY, value=value, unit="", confidence=confidence,
                   status=Status.OK, calc=calc,
                   detail={"밴드": band.name, "역": where,
                           "연 추세": f"{band.annual_drift:+.3%}p"}
                   ).with_confidence(confidence)


def all_features(conn: sqlite3.Connection, complex_id: int, *,
                 horizon_years: int | None = None) -> list[Feature]:
    return [drift(conn, complex_id, horizon_years=horizon_years)]
