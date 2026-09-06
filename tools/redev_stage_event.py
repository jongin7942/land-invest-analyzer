"""정비사업 단계 도달 사건연구 — "조합설립 때 팔면 얼마나 올라 있나" (종인님 동아1단지 목표 검증, 2026-09-06).

정비사업 단계 일자(exitprice/redev.load: 구역지정/추진위 3 · 조합설립 4 · 사업시행인가 5 · 관리처분 6 · 착공 7)를 가진 단지에서,
각 단계 도달월 D 기준 [D−60개월 → D] 와 [D−36 → D], [D → D+24] 의 log 가격변화(월 p50, ±2개월 평활)에서
같은 시군구 중앙값 변화를 뺀 상대수익을 지역(서울/경기/인천)·단계별로 집계한다.
출력: reports/redev_stage_event.json
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
from apt_engine.exitprice import redev  # noqa: E402
from apt_engine.exitprice.panel import ym_idx  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

LABEL = {3: "구역지정/추진위", 4: "조합설립", 5: "사업시행인가", 6: "관리처분", 7: "착공"}


def price_at(s, t, w=2):
    vals = [v for v in s.p50[max(0, t - w):t + w + 1] if v]
    return median(vals) if vals else None


def main() -> int:
    with get_conn() as conn:
        cx = store.load_complexes(conn, min_households=0)
        prices = store.load_prices(conn, cx, ("84", "59", "74"))
    table = redev.load()
    by_lawd = defaultdict(list)
    for (cid, band), s in prices.items():
        by_lawd[cx[cid].lawd_cd].append(s)
    def gu_change(lawd, t0, t1, exclude_cid):
        vals = []
        for (cid, band), s in prices.items():
            if cx[cid].lawd_cd != lawd or cid == exclude_cid:
                continue
            a, b = price_at(s, t0), price_at(s, t1)
            if a and b:
                vals.append(math.log(b / a))
        return median(vals) if len(vals) >= 5 else None
    events = []
    for cid, stages in table.items():
        if cid not in cx:
            continue
        c = cx[cid]
        for stage, ym in stages:
            if stage not in LABEL:
                continue
            t = ym_idx(ym)
            if t < 0 or t >= store.N_MONTHS:
                continue
            for band in ("84", "59", "74"):
                s = prices.get((cid, band))
                if not s:
                    continue
                ev = {"cid": cid, "name": c.name, "region": {"11": "서울", "41": "경기", "28": "인천"}.get(c.lawd_cd[:2], "?"), "band": band, "stage": stage, "label": LABEL[stage], "ym": ym, "hh": c.households}
                ok = False
                for key, (a, b) in {"pre60": (t - 60, t), "pre36": (t - 36, t), "post24": (t, t + 24)}.items():
                    if a < 0 or b >= store.N_MONTHS:
                        continue
                    p0, p1 = price_at(s, a), price_at(s, b); g = gu_change(c.lawd_cd, a, b, cid)
                    if p0 and p1 and g is not None:
                        ev[key] = {"own": round(math.log(p1 / p0), 4), "gu": round(g, 4), "rel": round(math.log(p1 / p0) - g, 4)}; ok = True
                if ok:
                    events.append(ev)
    out = {"n_events": len(events), "summary": {}, "events": events}
    for key in ("pre60", "pre36", "post24"):
        for region in ("전체", "서울", "경기", "인천"):
            for stage in (3, 4, 5, 6, 7):
                vals = [e[key]["rel"] for e in events if key in e and e["stage"] == stage and (region == "전체" or e["region"] == region)]
                owns = [e[key]["own"] for e in events if key in e and e["stage"] == stage and (region == "전체" or e["region"] == region)]
                if len(vals) >= 5:
                    out["summary"].setdefault(key, {}).setdefault(region, {})[LABEL[stage]] = {"n": len(vals), "rel_median": round(median(vals), 4), "rel_p25": round(sorted(vals)[len(vals) // 4], 4), "rel_p75": round(sorted(vals)[len(vals) * 3 // 4], 4), "own_median": round(median(owns), 4), "share_positive": round(sum(1 for v in vals if v > 0) / len(vals), 3)}
    (ROOT / "reports" / "redev_stage_event.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("events", len(events))
    for key, regs in out["summary"].items():
        for region, st in regs.items():
            print(key, region, {k: (v["n"], v["rel_median"], v["own_median"], v["share_positive"]) for k, v in st.items()})
    # 인천·1,000세대 이상·조합설립 사례 나열
    big = [e for e in events if e["stage"] == 4 and e["region"] == "인천" and "pre60" in e]
    for e in sorted(big, key=lambda e: -e["pre60"]["rel"])[:12]:
        print("  인천 조합설립", e["name"], e["band"], e["ym"], e["hh"], e["pre60"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
