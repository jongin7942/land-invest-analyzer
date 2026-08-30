"""재건축 사업성 데이터 입출력 (PHASE 6).

이 계층의 입력은 전부 사람 손으로 들어온다. 그래서 서식에는 **값을 채워 넣지
않는다** — 용적률이나 공사비를 우리가 적어 두면 그게 곧 하드코딩이고, 사용자가
원문 확인 없이 그대로 쓰게 된다(요구사항 62-7·62-10).
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from apt_engine import ENGINE_VERSION


class RedevImportError(ValueError):
    pass


# 표 이름 → (테이블, 컬럼)
TABLES = {
    "far": ("far_standard",
            ("sido", "lawd_cd", "zoning", "kind", "max_far", "conditions_json",
             "public_contribution_rate", "effective_from", "effective_to",
             "source_name", "source_url", "last_verified", "note")),
    "duration": ("stage_duration_ref",
                 ("project_type", "from_stage", "to_stage", "region", "median_months",
                  "p25_months", "p75_months", "sample_n",
                  "source_name", "source_url", "last_verified", "note")),
    "cost": ("construction_cost_ref",
             ("region", "grade", "base_year", "cost_per_py", "other_cost_rate",
              "source_name", "source_url", "last_verified", "note")),
}

# 비율로 들어오는 칸 — '25%' 도 0.25 로 받는다.
RATE_COLUMNS = {"public_contribution_rate", "other_cost_rate", "rental_ratio"}
INT_COLUMNS = {"max_far", "median_months", "p25_months", "p75_months", "sample_n",
               "base_year", "cost_per_py", "planned_units", "member_count",
               "prior_asset_total"}


def _rows_of(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    return [r for r in csv.DictReader(lines)
            if any(str(v or "").strip() for v in r.values())]


def _coerce(column: str, value):
    text = str(value or "").strip()
    if not text:
        return None
    if column in RATE_COLUMNS:
        try:
            return float(text[:-1]) / 100 if text.endswith("%") else float(text)
        except ValueError as e:
            raise RedevImportError(f"{column}: 비율이어야 합니다 — {value!r}") from e
    if column in INT_COLUMNS or column.endswith("_m2") or column.endswith("_far"):
        try:
            number = float(text.replace(",", ""))
        except ValueError as e:
            raise RedevImportError(f"{column}: 숫자여야 합니다 — {value!r}") from e
        return number if (column.endswith("_far") or column.endswith("_m2")
                          or column == "max_far") else int(number)
    return text


def import_csv(conn: sqlite3.Connection, kind: str, path: str | Path) -> dict:
    """참고표 CSV 를 넣는다. 한 줄이라도 잘못되면 전부 넣지 않는다."""
    if kind not in TABLES:
        raise RedevImportError(
            f"알 수 없는 표: {kind} (가능: {', '.join(TABLES)}, project, landarea)")
    table, columns = TABLES[kind]
    raw_rows = _rows_of(path)
    if not raw_rows:
        return {"read": 0, "inserted": 0, "unverified": 0}

    prepared, errors = [], []
    for i, raw in enumerate(raw_rows, start=2):
        try:
            prepared.append([_coerce(c, raw.get(c)) for c in columns])
        except RedevImportError as e:
            errors.append(f"  {i}행: {e}")
    if errors:
        raise RedevImportError(f"{path} 에서 {len(errors)}개 줄을 읽지 못했습니다:\n"
                               + "\n".join(errors[:20]))

    placeholders = ",".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})", prepared)
    vi = columns.index("last_verified")
    return {"read": len(raw_rows), "inserted": len(prepared),
            "unverified": sum(1 for r in prepared if not r[vi])}


# ── 단지 찾기 ─────────────────────────────────────────────────────────

def _complex_id(conn: sqlite3.Connection, row: dict, line: int) -> int:
    """CSV 한 줄이 가리키는 단지. 애매하면 붙이지 않고 에러를 낸다."""
    raw_id = str(row.get("complex_id") or "").strip()
    if raw_id:
        found = conn.execute("SELECT id FROM complex WHERE id = ?",
                             (int(raw_id),)).fetchone()
        if not found:
            raise RedevImportError(f"{line}행: complex_id {raw_id} 인 단지가 없습니다")
        return int(raw_id)

    name = str(row.get("complex_name") or "").strip()
    lawd = str(row.get("lawd_cd") or "").strip()
    if not name:
        raise RedevImportError(f"{line}행: complex_id 또는 complex_name 이 필요합니다")
    sql = "SELECT id, name FROM complex WHERE name = ?"
    params: list = [name]
    if lawd:
        sql += " AND lawd_cd = ?"
        params.append(lawd)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        raise RedevImportError(
            f"{line}행: '{name}' 단지를 찾지 못했습니다"
            + ("" if lawd else " (lawd_cd 를 함께 적으면 정확해집니다)"))
    if len(rows) > 1:
        raise RedevImportError(
            f"{line}행: '{name}' 이 {len(rows)}개 있습니다. lawd_cd 나 complex_id 로 "
            f"구분하세요 — 아무거나 골라 붙이지 않습니다")
    return int(rows[0]["id"])


PROJECT_COLUMNS = ("project_type", "name", "stage", "stage_date", "safety_grade",
                   "expected_approval_ym", "expected_move_ym", "expected_done_ym",
                   "planned_far", "planned_units", "rental_ratio",
                   "public_contribution_rate", "member_count", "prior_asset_total",
                   "source_name", "source_url", "last_verified", "data_grade", "note")


def import_projects(conn: sqlite3.Connection, path: str | Path) -> dict:
    """정비사업 단계 CSV. 단지를 못 찾거나 애매하면 통째로 거부한다."""
    raw_rows = _rows_of(path)
    if not raw_rows:
        return {"read": 0, "inserted": 0, "unverified": 0}

    prepared = []
    for i, raw in enumerate(raw_rows, start=2):
        cid = _complex_id(conn, raw, i)
        values = [_coerce(c, raw.get(c)) for c in PROJECT_COLUMNS]
        record = dict(zip(PROJECT_COLUMNS, values))
        if not record["project_type"] or not record["stage"]:
            raise RedevImportError(f"{i}행: project_type 과 stage 는 필수입니다")
        if record["planned_far"] and record["stage"] in ("미지정", "예비안전진단",
                                                         "정밀안전진단"):
            raise RedevImportError(
                f"{i}행: '{record['stage']}' 단계에는 정비계획 용적률이 존재할 수 없습니다. "
                f"조례 상한을 planned_far 로 적지 마세요 — far 표에 kind=조례 로 넣으세요")
        if record["stage"] != "미지정" and not record["stage_date"]:
            raise RedevImportError(
                f"{i}행: stage_date(그 단계가 된 날)가 필요합니다. "
                f"모르면 stage 를 '미지정'으로 두세요")
        record["data_grade"] = record["data_grade"] or "ESTIMATED"
        prepared.append((cid, record))

    inserted = 0
    for cid, record in prepared:
        cols = ("complex_id",) + PROJECT_COLUMNS
        values = (cid,) + tuple(record[c] for c in PROJECT_COLUMNS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in PROJECT_COLUMNS)
        conn.execute(
            f"INSERT INTO redevelopment_project ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)}) "
            f"ON CONFLICT(complex_id, project_type) DO UPDATE SET {updates}, "
            f"updated_at = datetime('now','localtime')", values)
        inserted += 1
    return {"read": len(raw_rows), "inserted": inserted,
            "unverified": sum(1 for _, r in prepared if not r["last_verified"])}


def import_land_area(conn: sqlite3.Connection, path: str | Path) -> dict:
    """대지면적·평형별 대지지분 CSV. 출처를 함께 남긴다."""
    raw_rows = _rows_of(path)
    updated = shares = 0
    for i, raw in enumerate(raw_rows, start=2):
        cid = _complex_id(conn, raw, i)
        land_area = _coerce("land_area_m2", raw.get("land_area_m2"))
        source = str(raw.get("source_name") or "").strip() or None
        verified = str(raw.get("last_verified") or "").strip() or None
        if land_area is not None:
            if not source:
                raise RedevImportError(
                    f"{i}행: 대지면적에는 source_name 이 필요합니다 "
                    f"(예: 건축물대장 총괄표제부)")
            conn.execute(
                "UPDATE complex SET land_area_m2 = ?, land_area_source = ?, "
                "land_area_verified = ?, updated_at = datetime('now','localtime') "
                "WHERE id = ?", (land_area, source, verified, cid))
            updated += 1

        area = _coerce("exclusive_area_m2", raw.get("exclusive_area_m2"))
        share = _coerce("land_share_m2", raw.get("land_share_m2"))
        if share is not None:
            if area is None:
                raise RedevImportError(
                    f"{i}행: land_share_m2 를 적으려면 exclusive_area_m2 도 필요합니다")
            n = conn.execute(
                "UPDATE unit_type SET land_share_m2 = ?, land_share_source = ? "
                " WHERE complex_id = ? AND abs(exclusive_area_m2 - ?) < 0.05",
                (share, source, cid, area)).rowcount
            if not n:
                raise RedevImportError(
                    f"{i}행: {area}㎡ 타입이 그 단지에 없습니다. "
                    f"unit_type 이 먼저 수집돼 있어야 합니다")
            shares += n
    return {"read": len(raw_rows), "complexes": updated, "unit_types": shares}


# ── 시나리오 저장 ─────────────────────────────────────────────────────

def save_scenario(conn: sqlite3.Connection, *, complex_id: int, area_band: str,
                  as_of: str, scenario_key: str, assumptions, result,
                  calc_json: str) -> int:
    conn.execute(
        "INSERT INTO redevelopment_scenario (complex_id, area_band, as_of, scenario_key, "
        " far, far_kind, cost_per_py, cost_base_year, new_price_per_m2, "
        " new_units, general_units, sale_revenue, total_cost, proportion_rate, "
        " right_value, member_price, extra_charge, engine_version, calc_trace) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(complex_id, area_band, as_of, scenario_key) DO UPDATE SET "
        " far=excluded.far, far_kind=excluded.far_kind, cost_per_py=excluded.cost_per_py, "
        " cost_base_year=excluded.cost_base_year, "
        " new_price_per_m2=excluded.new_price_per_m2, new_units=excluded.new_units, "
        " general_units=excluded.general_units, sale_revenue=excluded.sale_revenue, "
        " total_cost=excluded.total_cost, proportion_rate=excluded.proportion_rate, "
        " right_value=excluded.right_value, member_price=excluded.member_price, "
        " extra_charge=excluded.extra_charge, engine_version=excluded.engine_version, "
        " calc_trace=excluded.calc_trace, "
        " calculated_at=datetime('now','localtime')",
        (complex_id, area_band, as_of, scenario_key,
         assumptions.far, assumptions.far_kind, assumptions.cost_per_py,
         assumptions.cost_base_year, assumptions.new_price_per_m2,
         result.new_units, result.general_units, result.revenue, result.total_cost,
         result.proportion_rate, result.right_value, result.member_price,
         result.extra_charge, ENGINE_VERSION, calc_json))
    return 1


def latest_scenarios(conn: sqlite3.Connection, complex_id: int,
                     area_band: str | None = None) -> list[sqlite3.Row]:
    sql = ("SELECT * FROM redevelopment_scenario WHERE complex_id = ?")
    params: list = [complex_id]
    if area_band:
        sql += " AND area_band = ?"
        params.append(area_band)
    sql += " ORDER BY as_of DESC, scenario_key"
    return conn.execute(sql, params).fetchall()


def set_manual_status(conn: sqlite3.Connection, complex_id: int, *, status: str,
                      note: str | None = None) -> int:
    return conn.execute(
        "UPDATE redev_candidate SET manual_status = ?, manual_note = COALESCE(?, manual_note) "
        " WHERE complex_id = ?", (status, note, complex_id)).rowcount


def candidates(conn: sqlite3.Connection, *, lawd_cd: str | None = None,
               status: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    sql = ("SELECT rc.*, c.name, c.lawd_cd, c.apt_households "
           "  FROM redev_candidate rc JOIN complex c ON c.id = rc.complex_id WHERE 1=1")
    params: list = []
    if lawd_cd:
        sql += " AND c.lawd_cd = ?"
        params.append(lawd_cd)
    if status:
        sql += " AND rc.manual_status = ?"
        params.append(status)
    sql += " ORDER BY rc.score DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def coverage(conn: sqlite3.Connection) -> dict:
    """PHASE 6 입력이 얼마나 채워졌나."""
    out = {}
    for kind, (table, _) in TABLES.items():
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        verified = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE last_verified IS NOT NULL "
            f"AND trim(last_verified) != ''").fetchone()[0]
        out[kind] = {"total": total, "verified": verified}
    out["project"] = {
        "total": conn.execute("SELECT COUNT(*) FROM redevelopment_project").fetchone()[0],
        "verified": conn.execute(
            "SELECT COUNT(*) FROM redevelopment_project "
            "WHERE last_verified IS NOT NULL AND trim(last_verified) != ''").fetchone()[0]}
    out["land_area"] = {
        "total": conn.execute(
            "SELECT COUNT(*) FROM complex WHERE land_area_m2 IS NOT NULL").fetchone()[0],
        "verified": conn.execute(
            "SELECT COUNT(*) FROM complex WHERE land_area_verified IS NOT NULL "
            "AND trim(land_area_verified) != ''").fetchone()[0]}
    return out


# ── 입력 서식 ─────────────────────────────────────────────────────────
# 값은 비워 둔다. 예시 줄은 '#' 주석이라 읽히지 않는다.

TEMPLATES = {
    "far": (
        "sido,lawd_cd,zoning,kind,max_far,conditions_json,public_contribution_rate,"
        "effective_from,effective_to,source_name,source_url,last_verified,note\n"
        "# 예) 인천,,제2종일반주거지역,조례,,,,2024-01-01,,인천광역시 도시계획조례 제OO조,"
        "https://www.law.go.kr/...,,\n"
        "# kind: 법정상한 / 조례 / 정비계획 / 역세권특례  ← 절대 한 칸에 섞지 말 것\n"
        "#   법정상한  국토계획법 시행령의 최대치. 이 값을 사업 용적률로 쓰지 않는다\n"
        "#   조례      지자체 도시계획조례 상한. 실무 출발점\n"
        "#   정비계획  실제 고시된 구역 용적률. 이건 redev project 표의 planned_far 로 넣는다\n"
        "#   역세권특례 조건부 상향 한도. public_contribution_rate 를 함께 적는다\n"
        "# sido/lawd_cd 를 비우면 상위 범위(전국/시도 전체)로 본다\n"
        "# last_verified 를 비우면 엔진이 이 값으로 계산하지 않는다\n"),
    "project": (
        "complex_id,complex_name,lawd_cd,project_type,name,stage,stage_date,safety_grade,"
        "expected_approval_ym,expected_move_ym,expected_done_ym,planned_far,planned_units,"
        "rental_ratio,public_contribution_rate,member_count,prior_asset_total,"
        "source_name,source_url,last_verified,data_grade,note\n"
        "# 예) ,동아1단지,28237,재건축,,정밀안전진단,2025-11-20,D,,,,,,,,,,"
        "인천 부평구 고시 제2025-XX호,https://...,,ESTIMATED,\n"
        "# stage: 미지정/예비안전진단/정밀안전진단/정비구역지정/추진위원회/조합설립/\n"
        "#        사업시행인가/관리처분인가/이주철거/착공/준공\n"
        "# stage_date 는 '그 단계가 된 날'(확정 사실). expected_* 는 '예정'(추정)\n"
        "# planned_far 는 정비계획 고시가 난 뒤에만 적는다. 조례 상한을 여기 적지 말 것\n"
        "# prior_asset_total 은 종전자산 감정평가 총액(원). 관리처분 전에는 비워 둔다\n"),
    "duration": (
        "project_type,from_stage,to_stage,region,median_months,p25_months,p75_months,"
        "sample_n,source_name,source_url,last_verified,note\n"
        "# 예) 재건축,조합설립,사업시행인가,서울,,,,,서울시 정비사업 통계,https://...,,\n"
        "# 이 표가 비어 있으면 엔진은 사업기간을 '확인 불가'로 답한다.\n"
        "# 그럴듯한 평균 연수를 코드에 넣지 않기 위한 표다\n"),
    "cost": (
        "region,grade,base_year,cost_per_py,other_cost_rate,source_name,source_url,"
        "last_verified,note\n"
        "# 예) 서울,보통,2025,,0.25,조합 공사도급계약 공고,https://...,,\n"
        "# cost_per_py 는 공사연면적(지하 포함) 기준 원/평. base_year 없이 쓰지 않는다\n"
        "# other_cost_rate 는 기타사업비/공사비 비율 (예: 0.25 또는 25%)\n"),
    "landarea": (
        "complex_id,complex_name,lawd_cd,land_area_m2,exclusive_area_m2,land_share_m2,"
        "source_name,last_verified,note\n"
        "# 예) ,동아1단지,28237,,84.9,,건축물대장 총괄표제부,,\n"
        "# land_area_m2 는 단지 전체 대지면적. 평균 대지지분 = 이것 / 아파트 세대수\n"
        "# 평형별 대지권 비율을 등기부에서 확인했으면 exclusive_area_m2 + land_share_m2 로 넣는다\n"
        "# source_name 은 필수다 — 대지면적은 이 엔진에서 가장 민감한 입력이다\n"),
}


def write_template(kind: str, path: str | Path) -> Path:
    if kind not in TEMPLATES:
        raise RedevImportError(
            f"알 수 없는 서식: {kind} (가능: {', '.join(TEMPLATES)})")
    p = Path(path)
    p.write_text(TEMPLATES[kind], encoding="utf-8")
    return p
