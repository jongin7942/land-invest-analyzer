"""랭킹 실행 저장 (지시서 §64·§66).

과거 snapshot 을 **덮어쓰지 않는다.** 매 실행이 새 run 이고, 이전 run 과 비교해
순위 변화와 탈락 이유를 낼 수 있다.
"""
from __future__ import annotations

import json
import sqlite3

from apt_engine import ENGINE_VERSION


def save_run(conn: sqlite3.Connection, *, run_key: str, result, list_kind: str,
             entries) -> int:
    """한 리스트를 저장하고 run_id 를 돌려준다."""
    conn.execute(
        "INSERT INTO ranking_run (run_key, as_of, cash, horizon_years, profile, "
        " list_kind, universe_size, feasible_size, engine_version, weights_json, "
        " weights_source, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(run_key, as_of, cash, horizon_years, profile, list_kind) "
        "DO UPDATE SET executed_at=datetime('now','localtime'), "
        " universe_size=excluded.universe_size, feasible_size=excluded.feasible_size, "
        " weights_json=excluded.weights_json, weights_source=excluded.weights_source",
        (run_key, result.as_of, result.cash, result.horizon_years,
         result.profile_name, list_kind, result.universe_size, len(result.feasible),
         ENGINE_VERSION,
         json.dumps(result.weights.values, ensure_ascii=False),
         result.weights.source,
         f"국면 {result.regime or '확인 불가'}"))
    run_id = conn.execute(
        "SELECT id FROM ranking_run WHERE run_key=? AND as_of=? AND cash=? "
        " AND horizon_years=? AND profile=? AND list_kind=?",
        (run_key, result.as_of, result.cash, result.horizon_years,
         result.profile_name, list_kind)).fetchone()[0]

    conn.execute("DELETE FROM ranking_entry WHERE run_id = ?", (run_id,))
    for e in entries:
        c = e.candidate
        conn.execute(
            "INSERT INTO ranking_entry (run_id, rank, complex_id, area_band, score, "
            " confidence, kill_score, thesis_survival, required_equity, "
            " buyable_price, factors_json, reasons_json, calc_trace) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, e.rank, c.complex_id, c.area_band, c.score, c.confidence,
             c.kill.value, c.survival.value, c.required_equity, c.price,
             json.dumps(c.consensus.attribution, ensure_ascii=False),
             json.dumps([h.reason for h in c.kill.hits], ensure_ascii=False),
             c.consensus.calc.to_json() if c.consensus.calc else "{}"))
    return run_id


def previous_rank(conn: sqlite3.Connection, *, run_key: str, as_of: str, cash: int,
                  horizon_years: int, profile: str, list_kind: str,
                  complex_id: int) -> int | None:
    """직전 실행에서의 순위 (§64 previous_rank → current_rank)."""
    row = conn.execute(
        "SELECT e.rank FROM ranking_entry e JOIN ranking_run r ON r.id = e.run_id "
        " WHERE r.run_key=? AND r.cash=? AND r.horizon_years=? AND r.profile=? "
        "   AND r.list_kind=? AND r.as_of < ? AND e.complex_id=? "
        " ORDER BY r.as_of DESC LIMIT 1",
        (run_key, cash, horizon_years, profile, list_kind, as_of,
         complex_id)).fetchone()
    return int(row["rank"]) if row else None


def dropped_from_top(conn: sqlite3.Connection, *, run_key: str, as_of: str,
                     cash: int, horizon_years: int, profile: str,
                     list_kind: str, current_ids: set[int]) -> list[sqlite3.Row]:
    """직전에는 있었는데 이번에 빠진 후보 (§65)."""
    rows = conn.execute(
        "SELECT e.complex_id, e.rank FROM ranking_entry e "
        "  JOIN ranking_run r ON r.id = e.run_id "
        " WHERE r.run_key=? AND r.cash=? AND r.horizon_years=? AND r.profile=? "
        "   AND r.list_kind=? AND r.as_of < ? "
        " ORDER BY r.as_of DESC, e.rank",
        (run_key, cash, horizon_years, profile, list_kind, as_of)).fetchall()
    seen: set[int] = set()
    out = []
    for r in rows:
        cid = int(r["complex_id"])
        if cid in seen:
            continue
        seen.add(cid)
        if cid not in current_ids:
            out.append(r)
    return out
