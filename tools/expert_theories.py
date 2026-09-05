"""'전문가 이론' 전부 대입 → 승자 포착률(Recall@20) 이 지금(E 모델) 보다 높은 모델 찾기 (종인님 지시 2026-09-05).

절차
  1) 패널 재생성(EXPERT 변수 24개 추가) → logs/_exit_panel_expert.pkl
  2) 전월세 실거래가 2011년부터라 E 의 학습표본(진입 ≤ T−5)은 T=2016 부터 생긴다 → 테스트연도 2016~2021 (6개).
     선택 구간 2016~2018 에서 고르고 확인 구간 2019~2021 에서 한 번만 평가(선택 편향 최소화). 연도별 결과도 모두 기록.
     a. 기준 E(ridge, 완전행만)      b. E + 이론 그룹 1개씩      c. 그룹 전진 선택
     d. 목표 백분위화·변수 분위변환   e. 부스팅(결측 인지) E / E+전문가 — 완전행 부분집합과 전체행 둘 다 평가
     f. ridge 폴백 사다리(운영 방식, 전체행)   g. 앙상블(ridge 순위 + 부스팅 순위)
  3) reports/expert_theories.json + 콘솔 요약. 채택 판단은 확인 구간 Recall 이 E 보다 ≥2pp 높고 선택 구간도 낮지 않을 때.
    .venv/Scripts/python.exe tools/expert_theories.py [--no-cache] [--quick]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import boost as boost_mod, jobs as jobs_mod, model as model_mod, panel as panel_mod  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import median, percentile  # noqa: E402

ENTRY_YEARS = list(range(2007, 2022))
SELECT_YEARS = [2016, 2017, 2018]
HOLDOUT_YEARS = [2019, 2020, 2021]
ALL_YEARS = SELECT_YEARS + HOLDOUT_YEARS
LAM = 3.0
CACHE = ROOT / "logs" / "_exit_panel_expert.pkl"
OUT = ROOT / "reports" / "expert_theories.json"
T0 = time.time()


def log(msg):
    print(f"[{time.time()-T0:6.0f}s] {msg}", flush=True)


def build_panel(no_cache: bool):
    if CACHE.exists() and not no_cache:
        rows = pickle.loads(CACHE.read_bytes()); log(f"패널 캐시 {len(rows)}행"); return rows
    bands = ("84", "59", "74")
    with get_conn() as conn:
        cx = store.load_complexes(conn)
        prices = store.load_prices(conn, cx, bands)
        store.attach_academies(cx)
        jeonse = store.load_jeonse(conn, set(prices))
        stations = panel_mod.load_stations(conn)
        jobs = jobs_mod.Jobs(cx, conn)
        cx_all = store.load_complexes(conn, min_households=0)
        prices_all = store.load_prices(conn, cx_all, bands)
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs if jobs.available else None,
                                tier_complexes=cx_all, tier_prices=prices_all)
    rows = []
    for y in ENTRY_YEARS:
        rows += pb.build([y]); log(f"패널 {y} 누적 {len(rows)}행")
    CACHE.write_bytes(pickle.dumps(rows))
    return rows


def demean(rows):
    by = {}
    for r in rows:
        if r.target is not None:
            by.setdefault(r.entry_ym, []).append(r.target)
    lvl = {k: median(v) for k, v in by.items()}
    for r in rows:
        if r.target is not None:
            r.target -= lvl[r.entry_ym]
    return rows


class Shim:
    __slots__ = ("complex_id", "band", "entry_ym", "price", "target", "x")
    def __init__(self, r, target=None, x=None):
        self.complex_id, self.band, self.entry_ym, self.price = r.complex_id, r.band, r.entry_ym, r.price
        self.target = r.target if target is None else target
        self.x = r.x if x is None else x


def rank_target(rows):
    by = {}
    for r in rows:
        if r.target is not None:
            by.setdefault(r.entry_ym, []).append(r.target)
    srt = {k: sorted(v) for k, v in by.items()}
    out = []
    for r in rows:
        if r.target is None:
            out.append(Shim(r)); continue
        v = srt[r.entry_ym]; lo, hi = 0, len(v)
        while lo < hi:
            m = (lo + hi) // 2
            if v[m] < r.target: lo = m + 1
            else: hi = m
        out.append(Shim(r, target=lo / max(1, len(v) - 1) - 0.5))
    return out


def quantile_x(train, test, features, nq=20):
    cuts = {}
    for f in features:
        vals = sorted(r.x.get(f) for r in train if r.x.get(f) is not None)
        cuts[f] = [percentile(vals, q / nq) for q in range(1, nq)] if len(vals) >= nq else None
    def tx(r):
        x = dict(r.x)
        for f in features:
            v = x.get(f); c = cuts[f]
            if v is None or c is None:
                continue
            lo, hi = 0, len(c)
            while lo < hi:
                m = (lo + hi) // 2
                if v <= c[m]: hi = m
                else: lo = m + 1
            x[f] = lo / nq
        return Shim(r, x=x)
    return [tx(r) for r in train], [tx(r) for r in test]


def eval_pred(pairs):
    pairs = [(p, a) for p, a in pairs if p is not None and a is not None]
    n = len(pairs)
    if n < 10:
        return None
    pred = [p for p, _ in pairs]; act = [a for _, a in pairs]
    top_act = set(sorted(range(n), key=lambda i: -act[i])[: max(1, n // 10)])
    top_pred = set(sorted(range(n), key=lambda i: -pred[i])[: max(1, n // 5)])
    med = median(act)
    return {"n": n, "ic": model_mod.spearman(pred, act), "recall": len(top_act & top_pred) / len(top_act),
            "above_median": sum(1 for i in top_pred if act[i] >= med) / len(top_pred)}


def split(rows, T):
    return [r for r in rows if int(r.entry_ym[:4]) <= T - 5], [r for r in rows if int(r.entry_ym[:4]) == T]


def complete(rows, feats):
    return [r for r in rows if all(r.x.get(f) is not None for f in feats)]


E = panel_mod.FEATURE_SETS["E_+theory2"]
LADDER = [
    ("D", E),
    ("D-jm", [f for f in E if f != "jeonse_mom1"]),
    ("D-jm-m3", [f for f in E if f not in ("jeonse_mom1", "mom3")]),
    ("D-jm-m3-jr-st", [f for f in E if f not in ("jeonse_mom1", "mom3", "jeonse_ratio", "station_km")]),
    ("D-min", [f for f in E if f not in ("jeonse_mom1", "mom3", "jeonse_ratio", "station_km", "own_pct", "mom1", "jobs_emd", "log_academy", "jeonse_gap_closing", "emd_rel_mom3", "own_pct_sq")]),
]


def run_ridge(rows, feats, years, *, rank=False, qx=False, subset=None):
    """subset: 평가 행을 이 변수들이 완전한 행으로 제한(기본 feats 완전행 = ridge 가 예측 가능한 행)."""
    res = {}
    for T in years:
        train, test = split(rows, T)
        if subset is not None:
            test = complete(test, subset)
        if rank:
            train = rank_target(train)
        if qx:
            train, test_x = quantile_x(train, test, feats)
        else:
            test_x = test
        f = model_mod.fit(train, feats, LAM)
        if f is None:
            continue
        res[T] = eval_pred([(f.predict(tx.x), t.target) for tx, t in zip(test_x, test)])
    return res


def run_ladder(rows, years, ladder=LADDER, *, rank=False):
    """운영 방식: 완전행은 D, 아니면 차례로 축소 모델 → 전체행 예측."""
    res = {}
    for T in years:
        train, test = split(rows, T)
        tr = rank_target(train) if rank else train
        fits = [(model_mod.fit(tr, fs, LAM), fs) for _, fs in ladder]
        pairs = []
        for t in test:
            p = None
            for f, fs in fits:
                if f is None:
                    continue
                p = f.predict(t.x)
                if p is not None:
                    break
            pairs.append((p, t.target))
        res[T] = eval_pred(pairs)
    return res


def run_boost(rows, feats, years, *, rank=False, rounds=150, lr=0.08, depth=2, min_leaf=25, keep=None, subset=None, tag=""):
    res = {}
    for T in years:
        train, test = split(rows, T)
        if rank:
            train = rank_target(train)
        if keep is not None and T in keep:
            m = keep[T]
        else:
            m = boost_mod.fit_boost(train, feats, rounds=rounds, lr=lr, depth=depth, min_leaf=min_leaf)
            if m is None:
                continue
            if keep is not None:
                keep[T] = m
        ev_rows = complete(test, subset) if subset is not None else test
        res[T] = eval_pred([(m.predict(t.x), t.target) for t in ev_rows])
        if res[T]:
            log(f"    boost{tag} T={T} n_train={m.n} n_eval={res[T]['n']} recall={res[T]['recall']:.3f} ic={res[T]['ic']:.3f}")
    return res


def run_ens(rows, feats_r, years, boosts, *, subset=None):
    res = {}
    for T in years:
        train, test = split(rows, T)
        if subset is not None:
            test = complete(test, subset)
        f = model_mod.fit(train, feats_r, LAM); m = boosts.get(T)
        if f is None or m is None:
            continue
        pr = [f.predict(t.x) for t in test]; pb = [m.predict(t.x) for t in test]
        def ranks(v):
            idx = [i for i in range(len(v)) if v[i] is not None]
            order = sorted(idx, key=lambda i: v[i]); r = [None] * len(v)
            for pos, i in enumerate(order): r[i] = pos / max(1, len(idx) - 1)
            return r
        rr, rb = ranks(pr), ranks(pb)
        res[T] = eval_pred([(((rr[i] + rb[i]) / 2 if rr[i] is not None else rb[i]), test[i].target) for i in range(len(test))])
    return res


def summ(res, years):
    v = [res[T] for T in years if res.get(T)]
    if not v:
        return None
    return {"recall": round(sum(r["recall"] for r in v) / len(v), 4), "ic": round(sum((r["ic"] or 0) for r in v) / len(v), 4),
            "above_median": round(sum(r["above_median"] for r in v) / len(v), 4), "n_avg": round(sum(r["n"] for r in v) / len(v)), "n_years": len(v)}


def pack(res):
    return {"select": summ(res, SELECT_YEARS), "holdout": summ(res, HOLDOUT_YEARS), "all": summ(res, ALL_YEARS),
            "years": {T: {k: (round(x, 3) if isinstance(x, float) else x) for k, x in r.items()} for T, r in res.items() if r}}


def fmt(p):
    g = lambda k: (p[k]["recall"] if p.get(k) else None)
    return f"선택 {g('select')} 확인 {g('holdout')} 전체 {g('all')} n={p['all']['n_avg'] if p.get('all') else '-'}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    rows = demean(build_panel(args.no_cache))
    X = panel_mod.FEATURE_SETS["X_expert"]
    G = panel_mod.EXPERT_GROUPS
    out = {"n_rows": len(rows), "years": {"select": SELECT_YEARS, "holdout": HOLDOUT_YEARS}, "experiments": {}}
    ex = out["experiments"]
    out["expert_coverage"] = {f: round(sum(1 for r in rows if r.x.get(f) is not None) / len(rows), 3) for f in panel_mod.EXPERT}

    def rec(name, res):
        ex[name] = pack(res); log(f"  {name:28s} {fmt(ex[name])}")

    # a. 기준: E ridge (평가 = E 완전행)
    rec("E_ridge", run_ridge(rows, E, ALL_YEARS))
    # f. 운영 방식 사다리(전체행)
    rec("E_ladder_all", run_ladder(rows, ALL_YEARS))
    # b. 그룹별 추가 (평가는 E 완전행으로 고정 → 표본 차이 제거)
    for g, fs in G.items():
        rec(f"E+{g}", run_ridge(rows, E + fs, ALL_YEARS, subset=E))
    rec("X_ridge", run_ridge(rows, X, ALL_YEARS, subset=E))
    # c. 그룹 전진 선택(선택 구간만 봄)
    cur, chosen = list(E), []
    best = ex["E_ridge"]["select"]["recall"] if ex["E_ridge"]["select"] else 0
    for step in range(5):
        cand = []
        for g, fs in G.items():
            if g in chosen: continue
            s = summ(run_ridge(rows, cur + fs, SELECT_YEARS, subset=E), SELECT_YEARS)
            if s: cand.append((s["recall"], s["ic"], g))
        cand.sort(reverse=True)
        if not cand or cand[0][0] <= best + 0.003:
            break
        best = cand[0][0]; chosen.append(cand[0][2]); cur += G[cand[0][2]]
        log(f"  전진 {step}: +{cand[0][2]} → 선택 {best}")
    rec("fwd_groups_ridge", run_ridge(rows, cur, ALL_YEARS, subset=E)); ex["fwd_groups_ridge"]["groups"] = chosen
    # d. 변환
    rec("E_ridge_rank", run_ridge(rows, E, ALL_YEARS, rank=True))
    rec("E_ridge_qx", run_ridge(rows, E, ALL_YEARS, qx=True))
    rec("E_ridge_rank_qx", run_ridge(rows, E, ALL_YEARS, rank=True, qx=True))
    rec("X_ridge_rank_qx", run_ridge(rows, X, ALL_YEARS, rank=True, qx=True, subset=E))
    # e. 부스팅 — 완전행(E 와 동일 표본) / 전체행
    rounds = 60 if args.quick else 150
    kE, kX, kXr, kX3 = {}, {}, {}, {}
    rec("E_boost_sub", run_boost(rows, E, ALL_YEARS, rounds=rounds, keep=kE, subset=E, tag="E"))
    rec("E_boost_all", run_boost(rows, E, ALL_YEARS, rounds=rounds, keep=kE))
    rec("X_boost_sub", run_boost(rows, X, ALL_YEARS, rounds=rounds, keep=kX, subset=E, tag="X"))
    rec("X_boost_all", run_boost(rows, X, ALL_YEARS, rounds=rounds, keep=kX))
    rec("X_boost_rank_sub", run_boost(rows, X, ALL_YEARS, rounds=rounds, rank=True, keep=kXr, subset=E, tag="Xr"))
    rec("X_boost_rank_all", run_boost(rows, X, ALL_YEARS, rounds=rounds, rank=True, keep=kXr))
    rec("X_boost_d3_sub", run_boost(rows, X, ALL_YEARS, rounds=rounds, depth=3, min_leaf=40, keep=kX3, subset=E, tag="X3"))
    rec("X_boost_d3_all", run_boost(rows, X, ALL_YEARS, rounds=rounds, depth=3, min_leaf=40, keep=kX3))
    out["boost_importance_X"] = {str(T): dict(list(boost_mod.importance(m).items())[:15]) for T, m in kX.items()}
    # g. 앙상블
    rec("ens_Eridge+Xboost_sub", run_ens(rows, E, ALL_YEARS, kX, subset=E))
    rec("ens_Eridge+Xboost_all", run_ens(rows, E, ALL_YEARS, kX))

    base = ex["E_ridge"]
    bs = base["select"]["recall"] if base["select"] else 0; bh = base["holdout"]["recall"] if base["holdout"] else 0
    table = []
    for k, v in ex.items():
        s = v["select"]["recall"] if v.get("select") else None; h = v["holdout"]["recall"] if v.get("holdout") else None; a = v["all"]["recall"] if v.get("all") else None
        table.append((k, s, h, a))
    table.sort(key=lambda t: -(t[2] or 0))
    out["ranking_by_holdout"] = table
    out["baseline"] = {"select": bs, "holdout": bh}
    out["adopt_candidates"] = [k for k, s, h, a in table if s is not None and h is not None and h >= bh + 0.02 and s >= bs - 0.01 and k.endswith("_sub") or (k.startswith("E+") and s is not None and h is not None and h >= bh + 0.02 and s >= bs - 0.01)]
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log("=== 확인 구간(2019~21) Recall@20 순 ===")
    for k, s, h, a in table:
        log(f"  {k:28s} 선택 {s}  확인 {h}  전체 {a}")
    log(f"채택 후보(같은 표본 기준, 확인 ≥ E+2pp & 선택 ≥ E−1pp): {out['adopt_candidates']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
