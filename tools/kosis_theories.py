"""KOSIS 변수(인구이동·공사비지수·시군구 근로소득) 검증 — §24 와 같은 절차 (2026-09-06).
패널을 새로 만들어(logs/_exit_panel_kosis.pkl) E vs E+그룹(같은 표본 ridge), 부스팅(E+POLICY, 전체행)을 비교한다.
규제 변수는 학습구간(≤2016 진입)에 변동이 없어 walk-forward 로는 계수가 0 → 별도 사건연구(regulation_event_study.py).
    .venv/Scripts/python.exe tools/policy_theories.py [--no-cache]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import expert_theories as et  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import boost as boost_mod, jobs as jobs_mod, model as model_mod, panel as panel_mod  # noqa: E402
from apt_engine.relative import store  # noqa: E402

CACHE = ROOT / "logs" / "_exit_panel_kosis.pkl"


def build(no_cache: bool):
    if CACHE.exists() and not no_cache:
        rows = pickle.loads(CACHE.read_bytes()); et.log(f"패널 캐시 {len(rows)}행"); return rows
    bands = ("84", "59", "74")
    with get_conn() as conn:
        cx = store.load_complexes(conn); prices = store.load_prices(conn, cx, bands); store.attach_academies(cx)
        jeonse = store.load_jeonse(conn, set(prices)); stations = panel_mod.load_stations(conn); jobs = jobs_mod.Jobs(cx, conn)
        cx_all = store.load_complexes(conn, min_households=0); prices_all = store.load_prices(conn, cx_all, bands)
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs if jobs.available else None, tier_complexes=cx_all, tier_prices=prices_all)
    rows = []
    for y in et.ENTRY_YEARS:
        rows += pb.build([y]); et.log(f"패널 {y} 누적 {len(rows)}행")
    CACHE.write_bytes(pickle.dumps(rows))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--no-cache", action="store_true"); a = ap.parse_args()
    rows = et.demean(build(a.no_cache))
    E = et.E; G = panel_mod.KOSIS_GROUPS; P = panel_mod.FEATURE_SETS["K_kosis"]
    out = {"coverage": {f: round(sum(1 for r in rows if r.x.get(f) is not None) / len(rows), 3) for f in panel_mod.KOSIS}}
    et.log("커버리지 " + json.dumps(out["coverage"], ensure_ascii=False))
    def same_sample(fa, fb):
        res = {}
        for T in et.ALL_YEARS:
            tr, te = et.split(rows, T); te = et.complete(te, fb)
            ma = model_mod.fit(tr, fa, et.LAM); mb = model_mod.fit(tr, fb, et.LAM)
            if not ma or not mb: continue
            ra = et.eval_pred([(ma.predict(t.x), t.target) for t in te]); rb = et.eval_pred([(mb.predict(t.x), t.target) for t in te])
            if ra and rb: res[T] = {"n": ra["n"], "E": round(ra["recall"], 3), "E+": round(rb["recall"], 3), "coef": {f: round(mb.beta[1 + fb.index(f)], 4) for f in fb if f not in fa}}
        m = lambda k: round(sum(v[k] for v in res.values()) / max(1, len(res)), 4)
        return {"years": res, "mean_E": m("E"), "mean_E+": m("E+"), "wins": sum(1 for v in res.values() if v["E+"] > v["E"]), "n_years": len(res)}

    out["ridge_same_sample"] = {}
    for g, fs in G.items():
        r = same_sample(E, E + fs); out["ridge_same_sample"][g] = r
        et.log(f"ridge 같은표본 E vs E+{g}: {r['mean_E']} → {r['mean_E+']} ({r['wins']}/{r['n_years']} 우세)")
    r = same_sample(E, E + G["인구이동"] + G["소득"]); out["ridge_same_sample"]["인구이동+소득"] = r
    et.log(f"ridge 같은표본 E vs E+인구이동+소득: {r['mean_E']} → {r['mean_E+']} ({r['wins']}/{r['n_years']} 우세)")

    def boost3(feats, tag):
        res = {}
        for T in et.ALL_YEARS:
            tr, te = et.split(rows, T)
            ms = [m for m in (boost_mod.fit_boost(tr, feats, rounds=150, seed=s) for s in (7, 11, 13)) if m]
            if not ms: continue
            res[T] = et.eval_pred([(sum(m.predict(t.x) for m in ms) / len(ms), t.target) for t in te])
        return et.pack(res)
    out["boost3"] = {}
    for name, fs in [("E", E), ("E+인구이동", E + G["인구이동"]), ("E+소득", E + G["소득"]), ("E+공사비", E + G["공사비"]), ("E+KOSIS전부", P)]:
        out["boost3"][name] = boost3(fs, name); et.log(f"boost×3 {name}: {et.fmt(out['boost3'][name])}")
    (ROOT / "reports" / "kosis_theories.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    et.log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
