"""교통 선점 변수 — 2년 지평 검증 (§30.5). 5년 순위에는 희석되는 착공~개통 효과가 2년 상대수익 순위에는 잡히는지.

패널: HORIZON=24 로 다시 생성(logs/_exit_panel_transit_2y.pkl), 학습 = 진입 ≤ T−2, 테스트 T = 2016~2024.
비교: E / E+교통계획2(착공) / E+급행 / E+계획2+급행 — 전체행 부스팅×2 (시간 절약), Recall@20·above_median.
    .venv/Scripts/python.exe tools/transit_theories_2y.py [--no-cache]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from apt_engine.exitprice import panel as panel_mod  # noqa: E402
panel_mod.HORIZON = 24            # 2년 지평 — 패널 생성 전에 바꿔야 한다
import expert_theories as et  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import boost as boost_mod, jobs as jobs_mod, model as model_mod  # noqa: E402
from apt_engine.relative import store  # noqa: E402

CACHE = ROOT / "logs" / "_exit_panel_transit_2y.pkl"
YEARS = list(range(2016, 2025))
SEL, HOLD = [2016, 2017, 2018, 2019], [2020, 2021, 2022, 2023, 2024]


def build(no_cache):
    if CACHE.exists() and not no_cache:
        rows = pickle.loads(CACHE.read_bytes()); et.log(f"패널 캐시 {len(rows)}행"); return rows
    bands = ("84", "59", "74")
    with get_conn() as conn:
        cx = store.load_complexes(conn); prices = store.load_prices(conn, cx, bands); store.attach_academies(cx)
        jeonse = store.load_jeonse(conn, set(prices)); stations = panel_mod.load_stations(conn); jobs = jobs_mod.Jobs(cx, conn)
        cx_all = store.load_complexes(conn, min_households=0); prices_all = store.load_prices(conn, cx_all, bands)
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs if jobs.available else None, tier_complexes=cx_all, tier_prices=prices_all)
    rows = []
    for y in range(2007, 2025):
        rows += pb.build([y]); et.log(f"패널 {y} 누적 {len(rows)}행")
    CACHE.write_bytes(pickle.dumps(rows))
    return rows


def split2(rows, T):
    return [r for r in rows if int(r.entry_ym[:4]) <= T - 2], [r for r in rows if int(r.entry_ym[:4]) == T]


def run(rows, feats, tag):
    res = {}
    for T in YEARS:
        tr, te = split2(rows, T)
        ms = [m for m in (boost_mod.fit_boost(tr, feats, rounds=150, seed=s) for s in (7, 11)) if m]
        if not ms: continue
        res[T] = et.eval_pred([(sum(m.predict(t.x) for m in ms) / len(ms), t.target) for t in te])
    def m(keys, k):
        v = [res[T][k] for T in keys if res.get(T)]
        return round(sum(v) / len(v), 4) if v else None
    out = {"select": {k: m(SEL, k) for k in ("recall", "above_median", "ic")}, "holdout": {k: m(HOLD, k) for k in ("recall", "above_median", "ic")}, "all": {k: m(YEARS, k) for k in ("recall", "above_median", "ic")},
           "years": {T: {k: round(v, 3) for k, v in r.items()} for T, r in res.items() if r}}
    et.log(f"{tag:22s} 2y Recall 선택(16~19) {out['select']['recall']} 확인(20~24) {out['holdout']['recall']} 전체 {out['all']['recall']} | 중앙값이상 확인 {out['holdout']['above_median']}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--no-cache", action="store_true"); a = ap.parse_args()
    rows = et.demean(build(a.no_cache))
    E = et.E; G = panel_mod.TRANSIT_GROUPS
    out = {"horizon_months": 24, "n_rows": len(rows)}
    for name, fs in [("E", E), ("E+교통계획2(착공)", E + G["교통계획2(착공기준 복원)"]), ("E+급행", E + G["급행"]), ("E+계획2+급행", E + G["교통계획2(착공기준 복원)"] + G["급행"]), ("E+접근+계획2+급행", E + G["교통접근"] + G["교통계획2(착공기준 복원)"] + G["급행"])]:
        out[name] = run(rows, fs, name)
    (ROOT / "reports" / "transit_theories_2y.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    et.log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
