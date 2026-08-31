"""현금흐름 결과 저장·조회 (PHASE 7)."""
from __future__ import annotations

import json
import sqlite3

from apt_engine import ENGINE_VERSION


def save(conn: sqlite3.Connection, *, complex_id: int | None, area_band: str | None,
         as_of: str, scenario_key: str, timeline) -> int:
    """시나리오 하나. data_grade 는 스키마가 SCENARIO 로 고정한다."""
    conn.execute(
        "INSERT INTO cashflow_snapshot (complex_id, area_band, as_of, scenario_key, "
        " holding_years, occupancy, purchase_price, sale_price, initial_equity, "
        " peak_equity, net_profit, irr, profit_per_100m, unknown_json, "
        " engine_version, calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(complex_id, area_band, as_of, scenario_key, holding_years, "
        " occupancy) DO UPDATE SET "
        " purchase_price=excluded.purchase_price, sale_price=excluded.sale_price, "
        " initial_equity=excluded.initial_equity, peak_equity=excluded.peak_equity, "
        " net_profit=excluded.net_profit, irr=excluded.irr, "
        " profit_per_100m=excluded.profit_per_100m, unknown_json=excluded.unknown_json, "
        " engine_version=excluded.engine_version, calc_trace=excluded.calc_trace, "
        " calculated_at=datetime('now','localtime')",
        (complex_id, area_band, as_of, scenario_key, timeline.holding_years,
         timeline.occupancy, timeline.capital.purchase_price, timeline.sale_price,
         timeline.initial_equity, timeline.peak_equity, timeline.net_profit,
         timeline.irr, timeline.profit_per_100m,
         json.dumps(timeline.unknown, ensure_ascii=False), ENGINE_VERSION,
         timeline.calc.to_json() if timeline.calc else "{}"))
    return 1


def latest(conn: sqlite3.Connection, complex_id: int,
           area_band: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM cashflow_snapshot WHERE complex_id = ?"
    params: list = [complex_id]
    if area_band:
        sql += " AND area_band = ?"
        params.append(area_band)
    sql += " ORDER BY as_of DESC, holding_years, scenario_key"
    return conn.execute(sql, params).fetchall()
