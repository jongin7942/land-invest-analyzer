"""호가 저장·조회.

매물은 실거래와 달리 **같은 물건이 계속 갱신된다.** 어제 6.2억이던 매물이 오늘
6.05억이면 새 매물이 아니라 같은 매물의 가격 인하다. 그래서 listing 은 UPSERT 로
현재 상태를 유지하고, listing_snapshot 에 날짜별로 따로 찍는다.
"""
from __future__ import annotations

import json
import sqlite3

_LISTING_COLS = (
    "provider", "listing_key", "external_id", "complex_id", "match_confidence",
    "match_reason", "lawd_cd", "apt_name", "trade_type", "price", "monthly_rent",
    "exclusive_area_m2", "area_band", "dong", "floor", "top_floor", "floor_group",
    "direction", "features", "move_in_date", "tenant_status", "agency",
    "special_flags_json", "is_special", "source_url",
    "first_seen_at", "last_seen_at", "is_active", "raw_json", "source_id", "retrieved_at",
)


def _payload(row: dict, src_id: int | None) -> list:
    out = []
    for c in _LISTING_COLS:
        if c == "raw_json":
            raw = row.get("raw")
            out.append(json.dumps(raw, ensure_ascii=False) if raw else None)
        elif c == "special_flags_json":
            flags = row.get("special_flags")
            out.append(json.dumps(flags, ensure_ascii=False) if flags else None)
        elif c == "source_id":
            out.append(row.get("source_id", src_id))
        elif c == "is_active":
            out.append(row.get("is_active", 1))
        else:
            out.append(row.get(c))
    return out


def upsert_listings(conn: sqlite3.Connection, rows: list[dict], *,
                    src_id: int | None = None) -> dict:
    """현재 매물 목록을 반영한다.

    같은 (provider, listing_key) 가 있으면 가격·특징을 갱신하고 last_seen_at 을 민다.
    first_seen_at 은 유지한다 — 매물이 시장에 나온 지 얼마나 됐는지가 협상 재료다.
    """
    if not rows:
        return {"new": 0, "updated": 0}
    placeholders = ",".join("?" for _ in _LISTING_COLS)
    updates = ",".join(
        f"{c}=excluded.{c}" for c in _LISTING_COLS
        if c not in ("provider", "listing_key", "first_seen_at"))
    sql = (f"INSERT INTO listing ({','.join(_LISTING_COLS)}) VALUES ({placeholders}) "
           f"ON CONFLICT(provider, listing_key) DO UPDATE SET {updates}")

    new = updated = 0
    for row in rows:
        exists = conn.execute(
            "SELECT 1 FROM listing WHERE provider=? AND listing_key=?",
            (row["provider"], row["listing_key"])).fetchone()
        conn.execute(sql, _payload(row, src_id))
        updated += 1 if exists else 0
        new += 0 if exists else 1
    return {"new": new, "updated": updated}


def deactivate_missing(conn: sqlite3.Connection, provider: str, seen_on: str,
                       *, complex_id: int | None = None) -> int:
    """이번 수집에서 안 보인 매물을 비활성으로 돌린다.

    **"거래완료"라고 부르지 않는다.** 팔려서 내린 것인지 안 팔려서 거둔 것인지
    매물 데이터만으로는 알 수 없다(요구사항 9).
    """
    sql = ("UPDATE listing SET is_active=0 "
           "WHERE provider=? AND is_active=1 AND last_seen_at < ?")
    params: list = [provider, seen_on]
    if complex_id is not None:
        sql += " AND complex_id = ?"
        params.append(complex_id)
    return conn.execute(sql, params).rowcount


def save_daily_snapshot(conn: sqlite3.Connection, rows: list[dict],
                        snapshot_date: str) -> int:
    """그날의 매물을 그대로 찍는다. 같은 날 두 번 넣어도 중복되지 않는다."""
    if not rows:
        return 0
    n = 0
    for r in rows:
        n += conn.execute(
            """INSERT INTO listing_snapshot
               (snapshot_date, provider, listing_key, complex_id, area_band, trade_type,
                price, monthly_rent, dong, floor, floor_group, is_special, features)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(snapshot_date, provider, listing_key) DO UPDATE SET
                 price=excluded.price, monthly_rent=excluded.monthly_rent,
                 is_special=excluded.is_special, features=excluded.features""",
            (snapshot_date, r["provider"], r["listing_key"], r.get("complex_id"),
             r["area_band"], r["trade_type"], r["price"], r.get("monthly_rent", 0),
             r.get("dong"), r.get("floor"), r.get("floor_group"),
             r.get("is_special", 0), r.get("features"))).rowcount
    return n


def active_listings(conn: sqlite3.Connection, *, complex_id: int | None = None,
                    area_band: str | None = None, trade_type: str | None = None,
                    apt_name: str | None = None) -> list[dict]:
    sql = "SELECT * FROM listing WHERE is_active=1"
    params: list = []
    if complex_id is not None:
        sql += " AND complex_id = ?"
        params.append(complex_id)
    if area_band:
        sql += " AND area_band = ?"
        params.append(area_band)
    if trade_type:
        sql += " AND trade_type = ?"
        params.append(trade_type)
    if apt_name:
        sql += " AND apt_name = ?"
        params.append(apt_name)
    rows = []
    for r in conn.execute(sql + " ORDER BY price", params).fetchall():
        d = dict(r)
        d["special_flags"] = json.loads(d["special_flags_json"]) if d["special_flags_json"] else []
        rows.append(d)
    return rows


def snapshot_dates(conn: sqlite3.Connection, *, complex_id: int | None = None,
                   area_band: str | None = None, trade_type: str | None = None) -> list[str]:
    sql = "SELECT DISTINCT snapshot_date FROM listing_snapshot WHERE 1=1"
    params: list = []
    for col, val in (("complex_id", complex_id), ("area_band", area_band),
                     ("trade_type", trade_type)):
        if val is not None:
            sql += f" AND {col} = ?"
            params.append(val)
    return [r[0] for r in conn.execute(sql + " ORDER BY snapshot_date", params).fetchall()]


def snapshot_rows(conn: sqlite3.Connection, snapshot_date: str, *,
                  complex_id: int | None = None, area_band: str | None = None,
                  trade_type: str | None = None) -> list[dict]:
    sql = "SELECT * FROM listing_snapshot WHERE snapshot_date = ?"
    params: list = [snapshot_date]
    for col, val in (("complex_id", complex_id), ("area_band", area_band),
                     ("trade_type", trade_type)):
        if val is not None:
            sql += f" AND {col} = ?"
            params.append(val)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def save_pressure(conn: sqlite3.Connection, *, complex_id: int, area_band: str,
                  as_of_date: str, window_days: int, pressure) -> None:
    conn.execute(
        """INSERT INTO market_pressure
           (complex_id, area_band, as_of_date, window_days, score, direction,
            components_json, engine_version, data_grade, calc_trace)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(complex_id, area_band, as_of_date, window_days) DO UPDATE SET
             score=excluded.score, direction=excluded.direction,
             components_json=excluded.components_json,
             engine_version=excluded.engine_version, data_grade=excluded.data_grade,
             calc_trace=excluded.calc_trace,
             calculated_at=datetime('now','localtime')""",
        (complex_id, area_band, as_of_date, window_days, pressure.score, pressure.direction,
         json.dumps([{"key": c.key, "raw": c.raw, "normalized": c.normalized,
                      "weight": c.weight, "note": c.note} for c in pressure.components],
                    ensure_ascii=False),
         pressure.calc.engine_version, pressure.calc.grade, pressure.calc.to_json()))


def add_field_note(conn: sqlite3.Connection, *, complex_id: int | None, area_band: str | None,
                   noted_on: str, kind: str, note: str, source: str,
                   price: int | None = None, listing_key: str | None = None) -> int:
    """현장·중개사 확인값 (요구사항 46).

    호가와 절대 같은 테이블에 넣지 않는다 — '6.05억이면 된다더라'는 호가가 아니라
    협상 가능가이고, 둘을 섞으면 시세 통계가 오염된다.
    """
    cur = conn.execute(
        "INSERT INTO field_note (complex_id, area_band, noted_on, kind, listing_key, "
        "price, note, source) VALUES (?,?,?,?,?,?,?,?)",
        (complex_id, area_band, noted_on, kind, listing_key, price, note, source))
    return cur.lastrowid


def field_notes(conn: sqlite3.Connection, complex_id: int,
                area_band: str | None = None) -> list[dict]:
    sql = "SELECT * FROM field_note WHERE complex_id = ?"
    params: list = [complex_id]
    if area_band:
        sql += " AND area_band = ?"
        params.append(area_band)
    return [dict(r) for r in conn.execute(sql + " ORDER BY noted_on DESC", params).fetchall()]
