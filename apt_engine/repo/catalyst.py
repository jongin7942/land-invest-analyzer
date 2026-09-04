"""교통·공급·촉매 저장·조회 + 수기 입력 (PHASE 5)."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path


class CatalystImportError(ValueError):
    pass


# ── 교통 ──────────────────────────────────────────────────────────────

TRANSIT_COLUMNS = ("project_name", "kind", "station_name", "lawd_cd", "lat", "lon",
                   "status", "status_date", "expected_open_ym", "opened_ym",
                   "source_name", "source_url", "last_verified", "note")


def _rows_of(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    return [r for r in csv.DictReader(lines)
            if any(str(v or "").strip() for v in r.values())]


def _num(value, *, kind=float):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return kind(text.replace(",", ""))
    except ValueError as e:
        raise CatalystImportError(f"숫자여야 합니다: {value!r}") from e


def import_transit(conn: sqlite3.Connection, path: str | Path) -> dict:
    rows = _rows_of(path)
    if not rows:
        return {"projects": 0, "stations": 0, "unverified": 0}

    projects: dict[str, int] = {}
    stations = unverified = 0
    for i, r in enumerate(rows, start=2):
        pname = (r.get("project_name") or "").strip()
        sname = (r.get("station_name") or "").strip()
        status = (r.get("status") or "").strip()
        if not pname or not sname or not status:
            raise CatalystImportError(
                f"{i}행: project_name · station_name · status 는 필수입니다")
        if status == "개통" and not (r.get("opened_ym") or "").strip():
            raise CatalystImportError(
                f"{i}행: status 가 '개통'이면 opened_ym 이 있어야 합니다. "
                f"개통월을 모르면 '운영중' 으로 적으세요 — 역세권 거리에는 쓰이고, "
                f"개통 전후를 가르는 유사사례 측정에서만 빠집니다")

        if pname not in projects:
            conn.execute(
                "INSERT INTO transit_project (name, kind, source_name, source_url, "
                "last_verified, note) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind",
                (pname, (r.get("kind") or "기타").strip(), r.get("source_name"),
                 r.get("source_url"), r.get("last_verified") or None, r.get("note")))
            projects[pname] = conn.execute(
                "SELECT id FROM transit_project WHERE name = ?", (pname,)).fetchone()[0]

        try:
            lat, lon = _num(r.get("lat")), _num(r.get("lon"))
        except CatalystImportError as e:
            raise CatalystImportError(f"{i}행: 좌표 — {e}") from e

        conn.execute(
            "INSERT INTO transit_station (project_id, name, lawd_cd, lat, lon, status, "
            "status_date, expected_open_ym, opened_ym, source_name, source_url, "
            "last_verified, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id, name) DO UPDATE SET "
            "lawd_cd=excluded.lawd_cd, lat=excluded.lat, lon=excluded.lon, "
            "status=excluded.status, status_date=excluded.status_date, "
            "expected_open_ym=excluded.expected_open_ym, opened_ym=excluded.opened_ym, "
            "source_name=excluded.source_name, source_url=excluded.source_url, "
            "last_verified=excluded.last_verified, note=excluded.note",
            (projects[pname], sname, (r.get("lawd_cd") or "").strip() or None, lat, lon,
             status, r.get("status_date") or None, r.get("expected_open_ym") or None,
             r.get("opened_ym") or None, r.get("source_name"), r.get("source_url"),
             r.get("last_verified") or None, r.get("note")))
        stations += 1
        if not (r.get("last_verified") or "").strip():
            unverified += 1
    return {"projects": len(projects), "stations": stations, "unverified": unverified}


def _shift_ym(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:6]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def import_supply(conn: sqlite3.Connection, path: str | Path) -> dict:
    """§18 look-ahead 방지 — announced_ym(언제 알았나) 없이는 feature 가 이 행을
    보지 않는다(features/supply.py). 예전 임포터는 이 칸을 아예 안 썼다 - 마이그레이션
    014 가 칸을 만들었는데 임포터가 못 따라가서, 데이터를 넣어도 공급 feature 가
    영원히 빈손이었다. 비어 있으면 "분양은 준공 30개월 전" 관행으로 역산해서 채운다.
    """
    rows = _rows_of(path)
    inserted = unverified = estimated_announce = 0
    for i, r in enumerate(rows, start=2):
        lawd = (r.get("lawd_cd") or "").strip()
        name = (r.get("complex_name") or "").strip()
        ym = (r.get("move_in_ym") or "").strip()
        stage = (r.get("stage") or "").strip()
        households = _num(r.get("households"), kind=int)
        if not (lawd and name and ym and stage and households):
            raise CatalystImportError(
                f"{i}행: lawd_cd · complex_name · households · move_in_ym · stage 는 필수입니다")

        announced = (r.get("announced_ym") or "").strip()
        note = r.get("note") or ""
        if not announced:
            announced = _shift_ym(ym, -30)
            estimated_announce += 1
            note = (note + " · " if note else "") +                 "announced_ym 미입력 — 분양 관행(준공 30개월 전)으로 역산한 추정값"

        conn.execute(
            "INSERT INTO supply_plan (lawd_cd, emd_name, complex_name, households, "
            "move_in_ym, announced_ym, stage, kind, lat, lon, source_name, source_url, "
            "last_verified, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(lawd_cd, complex_name, move_in_ym) DO UPDATE SET "
            "announced_ym=excluded.announced_ym, households=excluded.households, "
            "stage=excluded.stage, kind=excluded.kind, lat=excluded.lat, lon=excluded.lon, "
            "last_verified=excluded.last_verified, note=excluded.note",
            (lawd, r.get("emd_name") or None, name, households, ym, announced, stage,
             (r.get("kind") or "").strip() or None, _num(r.get("lat")), _num(r.get("lon")),
             r.get("source_name"), r.get("source_url"),
             r.get("last_verified") or None, note))
        inserted += 1
        if not (r.get("last_verified") or "").strip():
            unverified += 1
    return {"inserted": inserted, "unverified": unverified, "estimated_announce": estimated_announce}


def opened_stations(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """선행사례를 만들 수 있는 역 — 실제 개통한 것만."""
    return conn.execute(
        "SELECT s.*, p.name AS project_name FROM transit_station s "
        "JOIN transit_project p ON p.id = s.project_id "
        "WHERE s.status = '개통' AND s.opened_ym IS NOT NULL "
        "ORDER BY s.opened_ym").fetchall()


def save_analogue(conn: sqlite3.Connection, a) -> None:
    conn.execute(
        "INSERT INTO transit_analogue (station_id, station_name, project_name, opened_ym, "
        "area_band, radius_m, before_ym, after_ym, near_n, far_n, ratio_before, "
        "ratio_after, delta, engine_version, calc_trace) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(station_name, area_band, before_ym, after_ym, radius_m) "
        "DO UPDATE SET ratio_before=excluded.ratio_before, "
        "ratio_after=excluded.ratio_after, delta=excluded.delta, "
        "near_n=excluded.near_n, far_n=excluded.far_n, "
        "engine_version=excluded.engine_version, calc_trace=excluded.calc_trace",
        (a.station_id, a.station_name, a.project_name, a.opened_ym, a.area_band,
         a.radius_m, a.before_ym, a.after_ym, a.near_n, a.far_n,
         a.ratio_before, a.ratio_after, a.delta,
         a.calc.engine_version, a.calc.to_json()))


def analogues(conn: sqlite3.Connection, *, area_band: str | None = None,
              project_name: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM transit_analogue WHERE 1=1"
    params: list = []
    if area_band:
        sql += " AND area_band = ?"
        params.append(area_band)
    if project_name:
        sql += " AND project_name = ?"
        params.append(project_name)
    return conn.execute(sql + " ORDER BY opened_ym DESC", params).fetchall()


def save_catalyst(conn: sqlite3.Connection, *, complex_id: int, kind: str, label: str,
                  station_id: int | None, expected_year: int | None,
                  within_horizon: bool | None, direction: str, evidence: dict,
                  confidence: str, calc) -> None:
    """근거(evidence)가 비면 스키마가 거부한다 — 요구사항 5."""
    conn.execute(
        "INSERT INTO future_catalyst (complex_id, kind, label, station_id, expected_year, "
        "within_horizon, direction, evidence_json, confidence, data_grade, "
        "engine_version, calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(complex_id, kind, label) DO UPDATE SET "
        "station_id=excluded.station_id, expected_year=excluded.expected_year, "
        "within_horizon=excluded.within_horizon, direction=excluded.direction, "
        "evidence_json=excluded.evidence_json, confidence=excluded.confidence, "
        "data_grade=excluded.data_grade, engine_version=excluded.engine_version, "
        "calc_trace=excluded.calc_trace, calculated_at=datetime('now','localtime')",
        (complex_id, kind, label, station_id, expected_year,
         None if within_horizon is None else int(within_horizon), direction,
         json.dumps(evidence, ensure_ascii=False), confidence,
         calc.grade, calc.engine_version, calc.to_json()))


def catalysts_of(conn: sqlite3.Connection, complex_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM future_catalyst WHERE complex_id = ? "
        "ORDER BY kind, expected_year", (complex_id,)).fetchall()


def complexes_missing_coords(conn: sqlite3.Connection,
                             limit: int | None = None) -> list[sqlite3.Row]:
    # 칸 이름은 jibun 이다(jibun_addr 아님). 그리고 주소가 하나도 없는 단지는
    # 부르지 않는다 — 호출만 버리고 실패 로그만 쌓인다. 주소는 K-apt 기본정보와
    # 함께 채워지므로 수집이 진행되면 대상도 같이 늘어난다.
    sql = ("SELECT id, name, road_addr, jibun FROM complex "
           " WHERE (lat IS NULL OR lon IS NULL) "
           "   AND (COALESCE(road_addr, '') != '' OR COALESCE(jibun, '') != '') "
           " ORDER BY id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def set_coords(conn: sqlite3.Connection, complex_id: int, lat: float, lon: float) -> None:
    conn.execute("UPDATE complex SET lat = ?, lon = ?, "
                 "updated_at = datetime('now','localtime') WHERE id = ?",
                 (lat, lon, complex_id))


# ── 입력 서식 ──────────────────────────────────────────────────────────
# 노선·역 이름과 단계는 사람이 공식 발표를 보고 채운다.
# 우리가 "GTX-B 2030년 개통" 같은 값을 미리 적어 두면 그게 곧 확정 호재처럼 쓰인다.

TRANSIT_TEMPLATE = """project_name,kind,station_name,lawd_cd,lat,lon,status,status_date,expected_open_ym,opened_ym,source_name,source_url,last_verified,note
# status: 계획 / 예비타당성 / 기본계획 / 착공 / 공사중 / 개통예정 / 개통
#   '개통' 으로 적으려면 opened_ym 이 반드시 있어야 합니다.
#   status_date 는 '그 단계가 된 날'(확정 사실), expected_open_ym 은 '개통 예정'(추정)입니다.
#   둘을 같은 칸에 넣지 마세요 — 계획을 확정 호재로 만드는 가장 흔한 경로입니다.
# lat/lon 은 역 좌표(위도,경도). 비우면 역세권 거리 계산에서 제외됩니다.
# 예) GTX-A,GTX,동탄,41597,37.2001,127.0985,개통,2024-03-30,,202403,국토교통부 보도자료,https://...,2026-08-31,
"""

SUPPLY_TEMPLATE = """lawd_cd,emd_name,complex_name,households,move_in_ym,announced_ym,stage,kind,lat,lon,source_name,source_url,last_verified,note
# stage: 계획 / 분양 / 착공 / 입주예정 / 입주완료
# kind:  신규분양 / 재건축 / 재개발 / 공공 / 기타
# announced_ym: 이 공급을 **언제 알 수 있었나**(분양공고일 등). move_in_ym(입주월)과
#   다르다 - 과거 시점 모델이 나중에 발표된 계획을 알면 look-ahead 다(§18).
#   비우면 "분양은 관행적으로 준공 30개월 전" 이라는 통념으로 move_in_ym 에서
#   역산한다(추정 — note 에 남는다). 실제 분양공고일을 알면 직접 적으세요.
# lat/lon 을 채우면 반경별(1/3/5km) 공급 분석이 됩니다. 비우면 시군구 단위로만 셉니다.
# 예) 28237,산곡동,○○자이,1200,202803,202601,착공,재개발,37.4870,126.7210,입주자모집공고,https://...,2026-08-31,
"""


def write_transit_template(path: str | Path) -> Path:
    p = Path(path)
    p.write_text(TRANSIT_TEMPLATE, encoding="utf-8")
    return p


def write_supply_template(path: str | Path) -> Path:
    p = Path(path)
    p.write_text(SUPPLY_TEMPLATE, encoding="utf-8")
    return p
