"""가격사다리 축 — 어떤 지역들이 어떤 순서로 가격이 이어지는가 (요구사항 3).

    강남 → 잠실 → 성동 → 마포 → 목동/신도림 → 광명 → 부천 → 부평 → 검단
    강남 → 과천 → 분당 → 수지 → 광교 → 동탄
    과천 → 평촌 → 산본
    마포 → 은평 → 고양 → 일산

**이건 데이터가 아니라 도메인 지식이다.** 공공 API 로 받을 수 있는 게 아니고,
LLM 이 만들어낼 것도 아니다. 사람이 판단해 적고 그 근거를 남긴다.

축이 있으면 비교단지 선정이 근거를 갖는다 — "산본은 평촌의 아래 칸이라 평촌 단지와
비교한다"는 말이 되지만, "비슷해 보여서"는 말이 안 된다.
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class LadderError(ValueError):
    pass


@dataclass(frozen=True)
class Node:
    axis_id: int
    axis_name: str
    rank: int
    label: str
    lawd_cd: str | None
    emd_name: str | None


def upsert_axis(conn: sqlite3.Connection, *, name: str, rationale: str,
                curated_by: str) -> int:
    """축 하나를 만들거나 갱신하고 id 를 돌려준다."""
    conn.execute(
        "INSERT INTO ladder_axis (name, rationale, curated_by) VALUES (?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET rationale=excluded.rationale, "
        "curated_by=excluded.curated_by",
        (name, rationale, curated_by))
    return conn.execute("SELECT id FROM ladder_axis WHERE name = ?", (name,)).fetchone()[0]


def set_nodes(conn: sqlite3.Connection, axis_id: int, nodes: list[dict]) -> int:
    """축의 노드를 통째로 교체한다. 순서를 바꾸려면 다시 넣는 게 안전하다."""
    conn.execute("DELETE FROM ladder_node WHERE axis_id = ?", (axis_id,))
    conn.executemany(
        "INSERT INTO ladder_node (axis_id, rank, label, lawd_cd, emd_name, note) "
        "VALUES (?,?,?,?,?,?)",
        [(axis_id, i, n["label"], n.get("lawd_cd") or None,
          n.get("emd_name") or None, n.get("note") or None)
         for i, n in enumerate(nodes)])
    return len(nodes)


def import_csv(conn: sqlite3.Connection, path: str | Path) -> dict:
    """축 정의 CSV 를 읽는다.

    같은 axis_name 의 줄들이 파일에 적힌 순서대로 한 축이 된다.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    rows = [r for r in csv.DictReader(lines)
            if any(str(v or "").strip() for v in r.values())]
    if not rows:
        return {"axes": 0, "nodes": 0}

    grouped: dict[str, list[dict]] = {}
    meta: dict[str, tuple[str, str]] = {}
    for i, r in enumerate(rows, start=2):
        name = (r.get("axis_name") or "").strip()
        label = (r.get("label") or "").strip()
        if not name or not label:
            raise LadderError(f"{i}행: axis_name 과 label 은 필수입니다")
        rationale = (r.get("rationale") or "").strip()
        curated_by = (r.get("curated_by") or "").strip()
        if name not in meta:
            if not rationale or not curated_by:
                raise LadderError(
                    f"{i}행: 축 '{name}' 의 첫 줄에는 rationale(왜 이 순서인가)과 "
                    f"curated_by(누가 정했나)가 있어야 합니다")
            meta[name] = (rationale, curated_by)
        grouped.setdefault(name, []).append(r)

    for name, node_rows in grouped.items():
        rationale, curated_by = meta[name]
        axis_id = upsert_axis(conn, name=name, rationale=rationale, curated_by=curated_by)
        set_nodes(conn, axis_id, node_rows)

    return {"axes": len(grouped), "nodes": len(rows)}


def nodes_for_region(conn: sqlite3.Connection, lawd_cd: str,
                     emd_name: str | None = None) -> list[Node]:
    """이 지역이 속한 사다리 노드들. 한 지역이 여러 축에 있을 수 있다."""
    rows = conn.execute(
        "SELECT n.axis_id, a.name AS axis_name, n.rank, n.label, n.lawd_cd, n.emd_name "
        "FROM ladder_node n JOIN ladder_axis a ON a.id = n.axis_id "
        "WHERE n.lawd_cd = ? AND (n.emd_name IS NULL OR n.emd_name = ?)",
        (lawd_cd, emd_name)).fetchall()
    return [Node(*r) for r in rows]


def neighbours(conn: sqlite3.Connection, node: Node, *, span: int = 1) -> list[Node]:
    """같은 축에서 위아래 span 칸 안의 노드들. 자기 자신은 뺀다."""
    rows = conn.execute(
        "SELECT n.axis_id, a.name, n.rank, n.label, n.lawd_cd, n.emd_name "
        "FROM ladder_node n JOIN ladder_axis a ON a.id = n.axis_id "
        "WHERE n.axis_id = ? AND n.rank BETWEEN ? AND ? AND n.rank != ? "
        "ORDER BY n.rank",
        (node.axis_id, node.rank - span, node.rank + span, node.rank)).fetchall()
    return [Node(*r) for r in rows]


def list_axes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT a.*, COUNT(n.id) AS node_count FROM ladder_axis a "
        "LEFT JOIN ladder_node n ON n.axis_id = a.id GROUP BY a.id ORDER BY a.name"
    ).fetchall()


def axis_labels(conn: sqlite3.Connection, axis_id: int) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT label FROM ladder_node WHERE axis_id = ? ORDER BY rank", (axis_id,))]


# ── 입력 서식 ──────────────────────────────────────────────────────────
# 요구사항 3이 예로 든 축들을 그대로 담았다. 사용자가 적은 도메인 지식이라
# 우리가 만들어낸 값이 아니지만, **시군구코드는 비워 뒀다** — 어느 구를 가리키는지는
# 사람이 확정해야 하고, 비워 두면 그 노드는 매칭에 쓰이지 않는다.

TEMPLATE_CSV = """axis_name,rationale,curated_by,label,lawd_cd,emd_name,note
# 같은 axis_name 줄들이 파일에 적힌 순서대로 한 축이 됩니다(위가 상위 = 비싼 쪽).
# rationale 과 curated_by 는 각 축의 첫 줄에만 적으면 됩니다.
# lawd_cd 를 비우면 그 노드는 비교단지 매칭에 쓰이지 않습니다 — 채워 넣으세요.
서남권,서울 도심 접근성과 서부 축선을 따라 이어지는 가격 계단,jongin,강남,11680,,
서남권,,,잠실,11710,,
서남권,,,성동,11200,,
서남권,,,마포,11440,,
서남권,,,목동,11470,,
서남권,,,광명,41210,,
서남권,,,부천,,,부천시 3개 구 — 어느 구인지 확정 필요
서남권,,,부평,28237,,
서남권,,,검단,28260,,
경부축,강남 접근성을 따라 남하하는 경부라인,jongin,강남,11680,,
경부축,,,과천,41290,,
경부축,,,분당,41135,,
경부축,,,수지,41465,,
경부축,,,광교,,,수원 영통 일부 — 동 단위 확정 필요
경부축,,,동탄,,,화성 동탄구 — 코드 확인 필요
안양권,과천을 정점으로 한 안양·군포 계단,jongin,과천,41290,,
안양권,,,평촌,41173,,
안양권,,,산본,41410,,
서북권,마포에서 서북으로 이어지는 축,jongin,마포,11440,,
서북권,,,은평,11380,,
서북권,,,고양,41281,,
서북권,,,일산,41285,,
"""


def write_template(path: str | Path) -> Path:
    p = Path(path)
    p.write_text(TEMPLATE_CSV, encoding="utf-8")
    return p
