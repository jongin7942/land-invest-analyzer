"""상대가치 저장·조회 (PHASE 4)."""
from __future__ import annotations

import json
import sqlite3

from apt_engine.relative.benchmark import Candidate


def candidates(conn: sqlite3.Connection, area_band: str, *,
               min_households: int | None = None,
               sido: str | None = None) -> list[Candidate]:
    """비교 후보 풀 — 그 면적밴드의 대표가격이 있는 단지들.

    가격이 없는 단지는 후보에서 뺀다. 비교할 값이 없으면 비교가 아니다.
    """
    sql = """
        SELECT c.id, c.name, c.lawd_cd, c.emd_name, c.apt_households, c.approval_year,
               r.sido,
               (SELECT p.representative_price FROM price_snapshot p
                 WHERE p.complex_id = c.id AND p.area_band = ?
                 ORDER BY p.as_of_ym DESC LIMIT 1) AS price
        FROM complex c LEFT JOIN region r ON r.lawd_cd = c.lawd_cd
        WHERE 1=1
    """
    params: list = [area_band]
    if min_households is not None:
        sql += " AND c.apt_households >= ?"
        params.append(min_households)
    if sido:
        sql += " AND r.sido = ?"
        params.append(sido)
    rows = conn.execute(sql, params).fetchall()
    return [Candidate(r["id"], r["name"], r["lawd_cd"], r["emd_name"],
                      r["apt_households"], r["approval_year"], r["price"], r["sido"])
            for r in rows if r["price"]]


def candidate_of(conn: sqlite3.Connection, complex_id: int,
                 area_band: str) -> Candidate | None:
    row = conn.execute("""
        SELECT c.id, c.name, c.lawd_cd, c.emd_name, c.apt_households, c.approval_year,
               r.sido,
               (SELECT p.representative_price FROM price_snapshot p
                 WHERE p.complex_id = c.id AND p.area_band = ?
                 ORDER BY p.as_of_ym DESC LIMIT 1) AS price
        FROM complex c LEFT JOIN region r ON r.lawd_cd = c.lawd_cd
        WHERE c.id = ?""", (area_band, complex_id)).fetchone()
    if row is None:
        return None
    return Candidate(row["id"], row["name"], row["lawd_cd"], row["emd_name"],
                     row["apt_households"], row["approval_year"], row["price"],
                     row["sido"])


def replace_benchmarks(conn: sqlite3.Connection, complex_id: int, area_band: str,
                       picks_with_calc: list[tuple], *, is_manual: bool = False) -> int:
    """자동선정 결과를 통째로 교체한다. 사람이 지정한 것(is_manual=1)은 건드리지 않는다."""
    conn.execute(
        "DELETE FROM benchmark_relation WHERE complex_id = ? AND area_band = ? "
        "AND is_manual = 0", (complex_id, area_band))
    for rank, (pick, calc) in enumerate(picks_with_calc, start=1):
        conn.execute(
            "INSERT INTO benchmark_relation (complex_id, benchmark_complex_id, area_band, "
            "axis_id, rank, similarity, selection_reason_json, is_manual, "
            "engine_version, calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(complex_id, benchmark_complex_id, area_band) DO UPDATE SET "
            "axis_id=excluded.axis_id, rank=excluded.rank, similarity=excluded.similarity, "
            "selection_reason_json=excluded.selection_reason_json, "
            "engine_version=excluded.engine_version, calc_trace=excluded.calc_trace",
            (complex_id, pick.candidate.complex_id, area_band, pick.axis_id, rank,
             pick.similarity,
             json.dumps({"항목점수": pick.reasons, "근거": pick.note,
                         "축": pick.axis_name}, ensure_ascii=False),
             1 if is_manual else 0, calc.engine_version, calc.to_json()))
    return len(picks_with_calc)


def benchmarks_of(conn: sqlite3.Connection, complex_id: int,
                  area_band: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT b.*, c.name AS benchmark_name, c.lawd_cd AS benchmark_lawd, "
        "a.name AS axis_name FROM benchmark_relation b "
        "JOIN complex c ON c.id = b.benchmark_complex_id "
        "LEFT JOIN ladder_axis a ON a.id = b.axis_id "
        "WHERE b.complex_id = ? AND b.area_band = ? ORDER BY b.rank",
        (complex_id, area_band)).fetchall()


def snapshots_by_ym(conn: sqlite3.Connection, complex_id: int,
                    area_band: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM price_snapshot WHERE complex_id = ? AND area_band = ? "
        "ORDER BY as_of_ym", (complex_id, area_band)).fetchall()
    return {r["as_of_ym"]: r for r in rows}


def save_ratio(conn: sqlite3.Connection, *, complex_id: int, benchmark_id: int,
               area_band: str, as_of_ym: str, ratio: float,
               price_snapshot_id: int | None, benchmark_snapshot_id: int | None,
               market_phase: str | None, confidence: str, calc) -> None:
    conn.execute(
        "INSERT INTO price_ratio_history (complex_id, benchmark_complex_id, area_band, "
        "as_of_ym, ratio, price_snapshot_id, benchmark_snapshot_id, market_phase, "
        "confidence, engine_version, calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(complex_id, benchmark_complex_id, area_band, as_of_ym) DO UPDATE SET "
        "ratio=excluded.ratio, price_snapshot_id=excluded.price_snapshot_id, "
        "benchmark_snapshot_id=excluded.benchmark_snapshot_id, "
        "market_phase=excluded.market_phase, confidence=excluded.confidence, "
        "engine_version=excluded.engine_version, calc_trace=excluded.calc_trace, "
        "calculated_at=datetime('now','localtime')",
        (complex_id, benchmark_id, area_band, as_of_ym, ratio, price_snapshot_id,
         benchmark_snapshot_id, market_phase, confidence,
         calc.engine_version, calc.to_json()))


def ratio_history(conn: sqlite3.Connection, complex_id: int, benchmark_id: int,
                  area_band: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM price_ratio_history WHERE complex_id = ? "
        "AND benchmark_complex_id = ? AND area_band = ? ORDER BY as_of_ym",
        (complex_id, benchmark_id, area_band)).fetchall()]


def save_norm(conn: sqlite3.Connection, *, complex_id: int, benchmark_id: int,
              area_band: str, norm) -> None:
    conn.execute(
        "INSERT INTO ratio_norm (complex_id, benchmark_complex_id, area_band, window_key, "
        "median_ratio, mean_ratio, p25_ratio, p75_ratio, sample_n, from_ym, to_ym, "
        "engine_version, calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(complex_id, benchmark_complex_id, area_band, window_key) "
        "DO UPDATE SET median_ratio=excluded.median_ratio, mean_ratio=excluded.mean_ratio, "
        "p25_ratio=excluded.p25_ratio, p75_ratio=excluded.p75_ratio, "
        "sample_n=excluded.sample_n, from_ym=excluded.from_ym, to_ym=excluded.to_ym, "
        "engine_version=excluded.engine_version, calc_trace=excluded.calc_trace, "
        "calculated_at=datetime('now','localtime')",
        (complex_id, benchmark_id, area_band, norm.window_key, norm.median, norm.mean,
         norm.p25, norm.p75, norm.sample_n, norm.from_ym, norm.to_ym,
         norm.calc.engine_version, norm.calc.to_json()))


def norms_of(conn: sqlite3.Connection, complex_id: int, benchmark_id: int,
             area_band: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ratio_norm WHERE complex_id = ? AND benchmark_complex_id = ? "
        "AND area_band = ? ORDER BY window_key", (complex_id, benchmark_id, area_band)
    ).fetchall()


def latest_ratio(conn: sqlite3.Connection, complex_id: int, benchmark_id: int,
                 area_band: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM price_ratio_history WHERE complex_id = ? "
        "AND benchmark_complex_id = ? AND area_band = ? ORDER BY as_of_ym DESC LIMIT 1",
        (complex_id, benchmark_id, area_band)).fetchone()
