"""교통 호재 목적지 등급 태깅 (spec/RULE_TRANSIT_BY_DESTINATION_20260906.md, 종인님 2026-09-06).

rules/transit_line_destination_tier.csv 의 pattern 을 transit_project.name / catalyst.catalyst_key 에 맞춰
line_id · destination_tier · destination 을 붙인다(컬럼 없으면 ALTER TABLE). 이어서 transit_analogue(117 사례)의
개통 효과(delta = 역세권/비역세권 가격비 변화)를 등급별로 다시 집계해 reports/transit_by_destination.json 에 쓴다.
    .venv/Scripts/python.exe tools/tag_transit_destination.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402


def ensure_cols(conn, table, cols):
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for c, typ in cols:
        if c not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} {typ}")


def match(name: str, rules: list[dict]):
    # 긴 패턴 우선(예: '8호선 별내선' 이 '8호선' 보다 먼저)
    for r in sorted(rules, key=lambda r: -len(r["pattern"])):
        if r["pattern"] in name:
            return r
    return None


def main() -> int:
    rules = list(csv.DictReader((ROOT / "rules" / "transit_line_destination_tier.csv").open(encoding="utf-8")))
    with get_conn() as conn:
        ensure_cols(conn, "transit_project", [("line_id", "TEXT"), ("destination_tier", "INTEGER"), ("destination", "TEXT"), ("tier_confidence", "TEXT")])
        ensure_cols(conn, "catalyst", [("line_id", "TEXT"), ("destination_tier", "INTEGER"), ("destination", "TEXT"), ("tier_confidence", "TEXT")])
        unmatched = []
        for r in conn.execute("SELECT id, name FROM transit_project").fetchall():
            m = match(r["name"], rules)
            if m:
                conn.execute("UPDATE transit_project SET line_id=?, destination_tier=?, destination=?, tier_confidence=? WHERE id=?", (m["line_id"], int(m["destination_tier"]), m["destination"], m["confidence"], r["id"]))
            else:
                unmatched.append(r["name"])
        for r in conn.execute("SELECT id, catalyst_key FROM catalyst WHERE catalyst_type IN ('GTX','지하철','철도','transit')").fetchall():
            m = match(r["catalyst_key"], rules)
            if m:
                conn.execute("UPDATE catalyst SET line_id=?, destination_tier=?, destination=?, tier_confidence=? WHERE id=?", (m["line_id"], int(m["destination_tier"]), m["destination"], m["confidence"], r["id"]))
            else:
                conn.execute("UPDATE catalyst SET destination_tier=NULL, tier_confidence='DESTINATION_TIER_UNKNOWN' WHERE id=?", (r["id"],))
                unmatched.append(r["catalyst_key"])
        conn.commit()
        # 등급별 개통 효과
        rows = conn.execute("SELECT a.delta, a.ratio_before, a.ratio_after, a.project_name, a.station_name, a.opened_ym, p.destination_tier, p.line_id "
                            "FROM transit_analogue a LEFT JOIN transit_station s ON s.id = a.station_id LEFT JOIN transit_project p ON p.id = s.project_id WHERE a.delta IS NOT NULL").fetchall()
        by = {}
        for r in rows:
            by.setdefault(r["destination_tier"], []).append(r)
        out = {"rule": "spec/RULE_TRANSIT_BY_DESTINATION_20260906.md", "n": len(rows), "by_tier": {}, "unmatched": unmatched}
        for tier, rs in sorted(by.items(), key=lambda kv: (kv[0] is None, kv[0])):
            d = [x["delta"] for x in rs]
            out["by_tier"][str(tier)] = {"n": len(d), "delta_median": round(median(d), 4), "delta_p25": round(sorted(d)[len(d) // 4], 4), "delta_p75": round(sorted(d)[len(d) * 3 // 4], 4),
                                        "share_positive": round(sum(1 for v in d if v > 0) / len(d), 3), "lines": sorted({x["line_id"] or x["project_name"] for x in rs})}
        by_line = {}
        for r in rows:
            by_line.setdefault(r["line_id"] or r["project_name"], []).append(r["delta"])
        out["by_line"] = {k: {"n": len(v), "delta_median": round(median(v), 4)} for k, v in sorted(by_line.items(), key=lambda kv: -len(kv[1]))}
        tp = conn.execute("SELECT destination_tier, COUNT(*) FROM transit_project GROUP BY destination_tier").fetchall()
        out["projects_by_tier"] = {str(r[0]): r[1] for r in tp}
    (ROOT / "reports" / "transit_by_destination.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("n", "by_tier", "projects_by_tier", "unmatched")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
