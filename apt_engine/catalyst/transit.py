"""교통호재 — 계획과 개통을 절대 섞지 않는다 (요구사항 21·26-10·62-8).

역 하나에 대해 두 가지를 분리해서 본다:

    확정된 사실   지금 어느 단계인가(status), 언제 그 단계가 됐나(status_date),
                 개통했다면 언제 개통했나(opened_ym)
    추정          언제 개통할 것 같은가(expected_open_ym)

"GTX-B 착공"은 사실이고 "2030년 개통 예정"은 추정이다. 화면에서도 둘을 다른 등급으로
표시한다. 기사 제목만 보고 확정 호재처럼 쓰지 않는다.

그리고 요구사항 55: 호재를 **투자기간과 연결**한다. 2035년 개통 예정인데 투자기간이
5년이면, 개통 자체는 투자기간 밖이고 기대감만 기간 안에 들어온다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from apt_engine import geo, rules
from apt_engine.trace import Calc, Evidence

# 뒤로 갈수록 확실하다. '개통'만 사실이고 나머지는 예정이다.
STAGES = ("계획", "예비타당성", "기본계획", "착공", "공사중", "개통예정", "개통")
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}

# 단계별 실현 신뢰도. 착공 전은 밀리거나 무산되는 일이 흔하다.
STAGE_CONFIDENCE = {
    "계획": "LOW", "예비타당성": "LOW", "기본계획": "MEDIUM",
    "착공": "HIGH", "공사중": "HIGH", "개통예정": "HIGH", "개통": "HIGH",
}

# 역세권으로 볼 직선거리. 직선이라 도보거리는 이보다 길다.
NEAR_RADIUS_M = 800
FAR_RADIUS_M = 2000


@dataclass(frozen=True)
class NearbyStation:
    station_id: int
    name: str
    project_name: str
    kind: str
    status: str
    status_date: str | None
    expected_open_ym: str | None
    opened_ym: str | None
    meters: float
    method: str
    verified: bool

    @property
    def opened(self) -> bool:
        return self.status == "개통" and bool(self.opened_ym)

    @property
    def confidence(self) -> str:
        return STAGE_CONFIDENCE.get(self.status, "LOW")

    @property
    def walk_minutes(self) -> int:
        return geo.rough_walk_minutes(self.meters)

    def horizon_label(self, *, as_of: str, years: int) -> tuple[bool | None, str]:
        """투자기간 안에 개통이 들어오는가. (여부, 설명)."""
        if self.opened:
            return True, f"{self.opened_ym} 이미 개통 — 가격에 반영됐을 가능성이 높다"
        if not self.expected_open_ym:
            return None, "개통 시점 미상 — 투자기간과 연결할 수 없다"
        end_year = int(as_of[:4]) + years
        open_year = int(self.expected_open_ym[:4])
        if open_year <= end_year:
            return True, (f"{self.expected_open_ym} 개통 예정 — 투자기간({years}년) 안. "
                          f"단 '{self.status}' 단계라 일정이 밀릴 수 있다")
        return False, (f"{self.expected_open_ym} 개통 예정 — 투자기간({years}년) 밖. "
                       f"개통 자체가 아니라 기대감만 기간 안에 들어온다")


def compute_distances(conn: sqlite3.Connection, *, complex_id: int | None = None,
                      max_m: int = FAR_RADIUS_M) -> int:
    """좌표가 있는 단지·역 쌍의 직선거리를 계산해 저장한다.

    좌표가 없는 단지는 건너뛴다 — 거리를 추측하지 않는다.
    """
    sql = "SELECT id, lat, lon FROM complex WHERE lat IS NOT NULL AND lon IS NOT NULL"
    params: list = []
    if complex_id is not None:
        sql += " AND id = ?"
        params.append(complex_id)
    complexes = conn.execute(sql, params).fetchall()
    stations = conn.execute(
        "SELECT id, lat, lon FROM transit_station "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL").fetchall()
    if not complexes or not stations:
        return 0

    n = 0
    for c in complexes:
        for s in stations:
            meters = geo.haversine_m(c["lat"], c["lon"], s["lat"], s["lon"])
            if meters > max_m:
                continue
            conn.execute(
                "INSERT INTO station_distance (complex_id, station_id, meters, "
                "walk_minutes, method) VALUES (?,?,?,?,'직선') "
                "ON CONFLICT(complex_id, station_id) DO UPDATE SET "
                "meters=excluded.meters, walk_minutes=excluded.walk_minutes, "
                "method=excluded.method, calculated_at=datetime('now','localtime')",
                (c["id"], s["id"], meters, geo.rough_walk_minutes(meters)))
            n += 1
    return n


def nearby(conn: sqlite3.Connection, complex_id: int, *,
           max_m: int = FAR_RADIUS_M) -> list[NearbyStation]:
    rows = conn.execute("""
        SELECT d.meters, d.method, s.id AS station_id, s.name, s.status, s.status_date,
               s.expected_open_ym, s.opened_ym, s.last_verified,
               p.name AS project_name, p.kind
        FROM station_distance d
        JOIN transit_station s ON s.id = d.station_id
        JOIN transit_project p ON p.id = s.project_id
        WHERE d.complex_id = ? AND d.meters <= ?
        ORDER BY d.meters""", (complex_id, max_m)).fetchall()
    return [NearbyStation(
        r["station_id"], r["name"], r["project_name"], r["kind"], r["status"],
        r["status_date"], r["expected_open_ym"], r["opened_ym"],
        r["meters"], r["method"], bool(r["last_verified"])) for r in rows]


def to_calc(station: NearbyStation, *, as_of: str, years: int) -> Calc:
    """역 하나를 촉매 Calc 로. 사실과 추정을 항목으로 갈라 놓는다."""
    within, horizon_note = station.horizon_label(as_of=as_of, years=years)

    facts = {
        "현재 단계": station.status,
        "단계 확정일": station.status_date or "미상",
        "실제 개통": station.opened_ym or "아직 개통 안 함",
    }
    estimates = {
        "개통 예정": station.expected_open_ym or "미상 — 확인 불가",
        "직선거리": f"{station.meters:,.0f}m",
        "도보(추정)": f"약 {station.walk_minutes}분 "
                    f"(직선거리 × {geo.DETOUR_FACTOR} 기준. 실측 아님)",
        "투자기간 연결": horizon_note,
    }

    return Calc(
        value=within, unit="bool",
        formula="개통(예정) 시점이 투자기간 안에 들어오는가",
        inputs={"역": f"{station.project_name} {station.name}",
                "기준일": as_of, "투자기간": f"{years}년"},
        intermediates={
            "확정된 사실": facts,
            "추정": estimates,
            "실현 신뢰도": f"{station.confidence} ('{station.status}' 단계 기준)",
            **({} if station.verified else
               {"미검증": "이 역 정보는 사람이 확인하지 않았습니다"}),
        },
        evidence=(Evidence(
            source=f"{station.project_name} 사업 단계 (수기 입력)",
            effective_date=station.status_date,
            note=f"'{station.status}' 는 확정 사실, '개통 예정'은 추정이다"),),
        # 개통한 역만 확정이고, 나머지는 전부 추정이다.
        grade="CONFIRMED" if station.opened else "ESTIMATED",
    )


def stage_at_least(status: str, minimum: str) -> bool:
    """단계 비교. '착공 이상만 호재로 친다' 같은 필터에 쓴다."""
    return STAGE_ORDER.get(status, -1) >= STAGE_ORDER.get(minimum, 99)
