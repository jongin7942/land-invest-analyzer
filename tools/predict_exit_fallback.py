"""Exit Price 현재 예측 커버리지 확대 — 결측이 잦은 변수를 뺀 폴백 모델 사다리 (§12).

주 모델 D(+일자리)로 예측이 안 되는 단지(전세 변화·3년 모멘텀·전세가율·역 거리 결측)에 대해
변수를 하나씩 뺀 모델로 예측한다. 어느 모델을 썼는지 `model`, 상태 `SCENARIO_FALLBACK_v0.1` 로 남긴다.
학습 패널은 tools/run_exit_price.py 의 캐시(logs/_exit_panel_*.pkl)를 그대로 쓴다(상대 목표).
"""
from __future__ import annotations

import csv
import json
import math
import pickle
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import jobs as jobs_mod, model as model_mod, panel as panel_mod  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import median, percentile  # noqa: E402

NOW_YM = "202606"
LAM = 0.3
D = panel_mod.FEATURE_SETS["E_+theory2"]   # v0.3 채택 변수군(E). 이름은 D 로 유지
LADDER = [
    ("D", D),
    ("D-jm", [f for f in D if f != "jeonse_mom1"]),
    ("D-jm-m3", [f for f in D if f not in ("jeonse_mom1", "mom3")]),
    ("D-jm-m3-jr-st", [f for f in D if f not in ("jeonse_mom1", "mom3", "jeonse_ratio", "station_km")]),
    ("D-min", [f for f in D if f not in ("jeonse_mom1", "mom3", "jeonse_ratio", "station_km", "own_pct", "mom1", "jobs_emd", "log_academy", "jeonse_gap_closing", "emd_rel_mom3", "own_pct_sq")]),
]


def main() -> int:
    t0 = time.time()
    with get_conn() as conn:
        cx = store.load_complexes(conn)
        prices = store.load_prices(conn, cx, ("84", "59", "74"))
        store.attach_academies(cx)
        jeonse = store.load_jeonse(conn, set(prices))
        stations = panel_mod.load_stations(conn)
        jobs = jobs_mod.Jobs(cx, conn)
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs)
    rows = pickle.loads((ROOT / "logs" / "_exit_panel_84_59_74.pkl").read_bytes())
    by_y: dict = {}
    for r in rows:
        if r.target is not None:
            by_y.setdefault(r.entry_ym, []).append(r.target)
    lvl = {ym: median(v) for ym, v in by_y.items()}
    for r in rows:
        if r.target is not None:
            r.target -= lvl[r.entry_ym]
    mk = sorted(lvl.values())
    bear_mk, base_mk, bull_mk = min(mk), percentile(mk, 0.5), percentile(mk, 0.8)
    print(f"[적재] {len(rows)}행 · 시장 시나리오 log {bear_mk:.3f}/{base_mk:.3f}/{bull_mk:.3f} ({time.time()-t0:.0f}s)", flush=True)

    now = [r for k in prices for r in [pb.row(k[0], k[1], NOW_YM)] if r]
    miss = Counter(f for r in now for f in D if r.x.get(f) is None)
    print(f"[NOW] {len(now)}행 · 결측 {miss.most_common(6)} ({time.time()-t0:.0f}s)", flush=True)

    fits = {}
    for name, feats in LADDER:
        fits[name] = model_mod.fit(rows, feats, LAM)
        print(f"  적합 {name:14s} n={fits[name].n} 변수 {len(feats)} ({time.time()-t0:.0f}s)", flush=True)

    out, used = [], Counter()
    for r in now:
        for name, feats in LADDER:
            f = fits[name]
            p = f.predict(r.x)
            if p is None:
                continue
            used[name] += 1
            out.append({
                "complex_id": r.complex_id, "band": r.band, "name": cx[r.complex_id].name, "lawd_cd": cx[r.complex_id].lawd_cd,
                "price_now": round(r.price), "pred_log5y": round(p, 4),
                "base_factor": round(math.exp(p + base_mk + f.resid_q[0.5]), 4),
                "bear_factor": round(math.exp(p + bear_mk + f.resid_q[0.2]), 4),
                "bull_factor": round(math.exp(p + bull_mk + f.resid_q[0.8]), 4),
                "market_scenario_log": json.dumps({"bear": round(bear_mk, 4), "base": round(base_mk, 4), "bull": round(bull_mk, 4)}),
                "tier": r.x.get("tier"),
                "dist_center_km": round(r.x["dist_center_km"], 2) if r.x.get("dist_center_km") is not None else None,
                "model": f"{name}|lam={LAM}|relative",
                "status": "SCENARIO_WALKFORWARD_v0.1" if name == "D" else "SCENARIO_FALLBACK_v0.1",
            })
            break
    with (ROOT / "rules" / "exit_price_2026.csv").open("w", encoding="utf-8", newline="") as fo:
        w = csv.DictWriter(fo, fieldnames=list(out[0].keys()))
        w.writeheader(); w.writerows(out)
    print(f"[예측] 커버리지 {len(out)}/{len(now)} · 모델별 {dict(used)} ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
