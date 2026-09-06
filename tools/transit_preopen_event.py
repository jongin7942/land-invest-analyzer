"""교통 선점효과 직접 검증 — 착공~개통 구간 상대수익 (종인님 2026-09-06).

연혁표(rules/transit_project_milestones.csv)에 착공·개통이 있는 노선의 역(2015~2024 개통)에 대해
  근접군: 역 1km 안 1,000세대 이상 단지×면적    대조군: 같은 시군구 2~6km
창: [발표(announce)~착공], [착공~개통], [개통~+24개월] 세 구간의 log 가격변화(월 p50) 차이(근접 − 대조) 중앙값.
목적지 등급(1/2/3)·급행 정차 여부로 나눠 본다.
출력: reports/transit_preopen_event.json
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import panel as pm  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import haversine_m, median  # noqa: E402


def price_at(s, t, w=2):
    vals = [v for v in s.p50[max(0, t - w):t + w + 1] if v]
    return median(vals) if vals else None


def main() -> int:
    with get_conn() as conn:
        cx = store.load_complexes(conn)
        prices = store.load_prices(conn, cx, ("84", "59", "74"))
        stations = pm.load_stations(conn)
    by_lawd = defaultdict(list)
    for (cid, band), s in prices.items():
        by_lawd[cx[cid].lawd_cd].append((cid, band, s))
    events = []
    for la, lo, opened, sdate, tier, pname, sname, line in stations:
        m = pm.milestone_of(pname)
        if not m or not opened or opened == "200001" or not m.get("construct_ym") or tier is None:
            continue
        if not ("201501" <= opened <= "202412"):
            continue
        t_open = pm.ym_idx(opened); t_con = pm.ym_idx(m["construct_ym"]); t_ann = pm.ym_idx(m["announce_ym"]) if m.get("announce_ym") else None
        if t_con < 0 or t_con >= t_open:
            continue
        near, ctrl = [], []
        # 역이 속한 시군구: 가장 가까운 단지의 lawd
        for (cid, band), s in prices.items():
            c = cx[cid]; d = haversine_m(la, lo, c.lat, c.lon)
            if d <= 1000:
                near.append((c.lawd_cd, s))
        if len(near) < 2:
            continue
        lawds = {l for l, _ in near}
        for l in lawds:
            for cid, band, s in by_lawd[l]:
                c = cx[cid]; d = haversine_m(la, lo, c.lat, c.lon)
                if 2000 <= d <= 6000:
                    ctrl.append(s)
        if len(ctrl) < 3:
            continue
        def window(t0, t1):
            if t0 is None or t0 < 0 or t1 >= store.N_MONTHS or t1 <= t0:
                return None
            def chg(s):
                a, b = price_at(s, t0), price_at(s, t1)
                return math.log(b / a) if a and b else None
            n = [v for v in (chg(s) for _, s in near) if v is not None]; k = [v for v in (chg(s) for s in ctrl) if v is not None]
            if len(n) < 2 or len(k) < 3:
                return None
            return {"near": round(median(n), 4), "ctrl": round(median(k), 4), "diff": round(median(n) - median(k), 4), "n_near": len(n), "n_ctrl": len(k)}
        ev = {"station": sname, "line": line, "tier": tier, "express": pm.is_express(line, sname), "announce": m.get("announce_ym"), "construct": m["construct_ym"], "opened": opened,
              "w_ann_con": window(t_ann, t_con) if t_ann is not None else None, "w_con_open": window(t_con, t_open), "w_open_24": window(t_open, t_open + 24)}
        events.append(ev)
    out = {"n_events": len(events), "events": events, "summary": {}}
    def summ(key, flt):
        vals = [e[key]["diff"] for e in events if e.get(key) and flt(e)]
        return {"n": len(vals), "diff_median": round(median(vals), 4) if vals else None, "share_positive": round(sum(1 for v in vals if v > 0) / len(vals), 3) if vals else None}
    for label, flt in (("all", lambda e: True), ("tier1", lambda e: e["tier"] == 1), ("tier2", lambda e: e["tier"] == 2), ("tier3", lambda e: e["tier"] == 3),
                       ("tier1_express", lambda e: e["tier"] == 1 and e["express"]), ("tier1_local", lambda e: e["tier"] == 1 and not e["express"])):
        out["summary"][label] = {k: summ(k, flt) for k in ("w_ann_con", "w_con_open", "w_open_24")}
    (ROOT / "reports" / "transit_preopen_event.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("events", len(events))
    for k, v in out["summary"].items():
        print(k, {w: (x["n"], x["diff_median"], x["share_positive"]) for w, x in v.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
