"""'전문가 이론' 전부 대입 → 승자 포착률(Recall@20) 이 지금(E 모델) 보다 높은 모델 찾기 (종인님 지시 2026-09-05).

절차
  1) 패널 재생성(EXPERT 변수 24개 추가) → logs/_exit_panel_expert.pkl
  2) 선택 구간(테스트 2013~2017) 에서 고르고 확인 구간(2018~2021) 에서 한 번만 평가 — 선택 편향 방지
     a. 기준 E(ridge)                          b. E + 이론 그룹 1개씩(ridge)
     c. 이론 그룹 전진 선택(ridge)             d. 목표 백분위화 / 변수 분위변환(ridge)
     e. 부스팅(결측 인지 트리) E / E+전문가    f. 앙상블(ridge 순위 + 부스팅 순위)
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
SELECT_YEARS = list(range(2013, 2018))
HOLDOUT_YEARS = list(range(2018, 2022))
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


# ── 변환 ──
class Shim:
    """target 을 바꾼 Row 대체(원 Row 는 평가용으로 보존)."""
    __slots__ = ("complex_id", "band", "entry_ym", "price", "target", "x")
    def __init__(self, r, target=None, x=None):
        self.complex_id, self.band, self.entry_ym, self.price = r.complex_id, r.band, r.entry_ym, r.price
        self.target = r.target if target is None else target
        self.x = r.x if x is None else x


def rank_target(rows):
    """연도 내 백분위(−0.5~0.5) 로 목표 변환 — 극단값 영향 제거."""
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
    """학습 분포 기준 분위변환(0~1) — 비선형·극단값 완화. 결측은 그대로 None."""
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


# ── 평가 ──
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
    train = [r for r in rows if int(r.entry_ym[:4]) <= T - 5]
    test = [r for r in rows if int(r.entry_ym[:4]) == T]
    return train, test


def run_ridge(rows, feats, years, *, rank=False, qx=False):
    res = {}
    for T in years:
        train, test = split(rows, T)
        if rank:
            train = rank_target(train)
        if qx:
            train, test_x = quantile_x(train, test, feats)
        else:
            test_x = test
        f = model_mod.fit(train, feats, LAM)
        if f is None:
            continue
        pairs = [(f.predict(tx.x), t.target) for tx, t in zip(test_x, test)]
        res[T] = eval_pred(pairs)
    return res


def run_boost(rows, feats, years, *, rank=False, rounds=150, lr=0.08, depth=2, min_leaf=25, keep=None):
    res = {}
    for T in years:
        train, test = split(rows, T)
        if rank:
            train = rank_target(train)
        m = boost_mod.fit_boost(train, feats, rounds=rounds, lr=lr, depth=depth, min_leaf=min_leaf)
        if m is None:
            continue
        pairs = [(m.predict(t.x), t.target) for t in test]
        res[T] = eval_pred(pairs)
        if keep is not None:
            keep[T] = (m, pairs)
        log(f"    boost T={T} n_train={m.n} recall={res[T]['recall']:.3f} ic={res[T]['ic']:.3f}")
    return res


def run_ens(rows, feats_r, feats_b, years, *, rank=False, boost_cache=None):
    res = {}
    for T in years:
        train, test = split(rows, T)
        tr = rank_target(train) if rank else train
        f = model_mod.fit(tr, feats_r, LAM)
        if boost_cache and T in boost_cache:
            m = boost_cache[T][0]
        else:
            m = boost_mod.fit_boost(tr, feats_b)
        if f is None or m is None:
            continue
        pr = [f.predict(t.x) for t in test]; pb = [m.predict(t.x) for t in test]
        # ridge 예측이 결측이면 부스팅만, 둘 다 있으면 순위 평균
        def ranks(v):
            idx = [i for i in range(len(v)) if v[i] is not None]
            order = sorted(idx, key=lambda i: v[i]); r = [None] * len(v)
            for pos, i in enumerate(order): r[i] = pos / max(1, len(idx) - 1)
            return r
        rr, rb = ranks(pr), ranks(pb)
        pairs = [(((rr[i] + rb[i]) / 2 if rr[i] is not None else rb[i]), test[i].target) for i in range(len(test))]
        res[T] = eval_pred(pairs)
    return res


def summ(res):
    v = [r for r in res.values() if r]
    if not v:
        return None
    return {"recall": round(sum(r["recall"] for r in v) / len(v), 4), "ic": round(sum(r["ic"] for r in v if r["ic"] is not None) / len(v), 4),
            "above_median": round(sum(r["above_median"] for r in v) / len(v), 4), "n_avg": round(sum(r["n"] for r in v) / len(v)),
            "years": {T: {k: (round(x, 3) if isinstance(x, float) else x) for k, x in r.items()} for T, r in res.items() if r}}


def both(fn, *a, **k):
    return {"select": summ(fn(*a, SELECT_YEARS, **k)), "holdout": summ(fn(*a, HOLDOUT_YEARS, **k))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--quick", action="store_true", help="부스팅 라운드 축소")
    args = ap.parse_args()
    rows = demean(build_panel(args.no_cache))
    E = panel_mod.FEATURE_SETS["E_+theory2"]
    X = panel_mod.FEATURE_SETS["X_expert"]
    G = panel_mod.EXPERT_GROUPS
    out = {"n_rows": len(rows), "experiments": {}}
    ex = out["experiments"]

    # 변수 커버리지
    cov = {f: round(sum(1 for r in rows if r.x.get(f) is not None) / len(rows), 3) for f in panel_mod.EXPERT}
    out["expert_coverage"] = cov
    log("커버리지 " + ", ".join(f"{k}={v}" for k, v in cov.items()))

    ex["E_ridge"] = both(run_ridge, rows, E); log(f"기준 E ridge  {ex['E_ridge']['select']['recall']} / {ex['E_ridge']['holdout']['recall']}")
    # b. 그룹별 추가
    for g, fs in G.items():
        ex[f"E+{g}"] = both(run_ridge, rows, E + fs)
        log(f"  E+{g:12s} {ex[f'E+{g}']['select']['recall']} / {ex[f'E+{g}']['holdout']['recall']}  n={ex[f'E+{g}']['select']['n_avg']}")
    ex["X_all_ridge"] = both(run_ridge, rows, X); log(f"  E+전부(X) ridge {ex['X_all_ridge']['select']['recall']} / {ex['X_all_ridge']['holdout']['recall']} n={ex['X_all_ridge']['select']['n_avg']}")
    # c. 그룹 전진 선택 (선택 구간만 보고)
    cur, chosen = list(E), []
    best = ex["E_ridge"]["select"]["recall"]
    for step in range(5):
        cand = []
        for g, fs in G.items():
            if g in chosen: continue
            s = summ(run_ridge(rows, cur + fs, SELECT_YEARS))
            if s: cand.append((s["recall"], s["ic"], g))
        cand.sort(reverse=True)
        if not cand or cand[0][0] <= best + 0.003:
            break
        best = cand[0][0]; chosen.append(cand[0][2]); cur += G[cand[0][2]]
        log(f"  전진 {step}: +{cand[0][2]} → 선택 {best}")
    ex["fwd_groups_ridge"] = {"groups": chosen, **both(run_ridge, rows, cur)}
    log(f"  전진선택 결과 {chosen} → {ex['fwd_groups_ridge']['select']['recall']} / {ex['fwd_groups_ridge']['holdout']['recall']}")
    # d. 변환
    ex["E_ridge_rank"] = both(run_ridge, rows, E, rank=True)
    ex["E_ridge_qx"] = both(run_ridge, rows, E, qx=True)
    ex["E_ridge_rank_qx"] = both(run_ridge, rows, E, rank=True, qx=True)
    ex["X_ridge_rank_qx"] = both(run_ridge, rows, X, rank=True, qx=True)
    for k in ("E_ridge_rank", "E_ridge_qx", "E_ridge_rank_qx", "X_ridge_rank_qx"):
        log(f"  {k:16s} {ex[k]['select']['recall']} / {ex[k]['holdout']['recall']}")
    # e. 부스팅
    rounds = 60 if args.quick else 150
    keepE, keepX = {}, {}
    ex["E_boost"] = {"select": summ(run_boost(rows, E, SELECT_YEARS, rounds=rounds, keep=keepE)), "holdout": summ(run_boost(rows, E, HOLDOUT_YEARS, rounds=rounds, keep=keepE))}
    log(f"  E boost {ex['E_boost']['select']['recall']} / {ex['E_boost']['holdout']['recall']} n={ex['E_boost']['select']['n_avg']}")
    ex["X_boost"] = {"select": summ(run_boost(rows, X, SELECT_YEARS, rounds=rounds, keep=keepX)), "holdout": summ(run_boost(rows, X, HOLDOUT_YEARS, rounds=rounds, keep=keepX))}
    log(f"  X boost {ex['X_boost']['select']['recall']} / {ex['X_boost']['holdout']['recall']}")
    ex["X_boost_rank"] = {"select": summ(run_boost(rows, X, SELECT_YEARS, rounds=rounds, rank=True)), "holdout": summ(run_boost(rows, X, HOLDOUT_YEARS, rounds=rounds, rank=True))}
    log(f"  X boost rank {ex['X_boost_rank']['select']['recall']} / {ex['X_boost_rank']['holdout']['recall']}")
    ex["X_boost_d3"] = {"select": summ(run_boost(rows, X, SELECT_YEARS, rounds=rounds, depth=3, min_leaf=40)), "holdout": summ(run_boost(rows, X, HOLDOUT_YEARS, rounds=rounds, depth=3, min_leaf=40))}
    log(f"  X boost depth3 {ex['X_boost_d3']['select']['recall']} / {ex['X_boost_d3']['holdout']['recall']}")
    imp = {T: boost_mod.importance(m) for T, (m, _) in keepX.items()}
    out["boost_importance_X"] = {str(T): dict(list(v.items())[:15]) for T, v in imp.items()}
    # f. 앙상블
    ex["ens_E_ridge+X_boost"] = {"select": summ(run_ens(rows, E, X, SELECT_YEARS, boost_cache=keepX)), "holdout": summ(run_ens(rows, E, X, HOLDOUT_YEARS, boost_cache=keepX))}
    log(f"  ens ridgeE+boostX {ex['ens_E_ridge+X_boost']['select']['recall']} / {ex['ens_E_ridge+X_boost']['holdout']['recall']}")

    # 판정
    base_h = ex["E_ridge"]["holdout"]["recall"]; base_s = ex["E_ridge"]["select"]["recall"]
    table = sorted(((k, v["select"]["recall"] if v.get("select") else None, v["holdout"]["recall"] if v.get("holdout") else None) for k, v in ex.items()), key=lambda t: -(t[2] or 0))
    out["ranking_by_holdout"] = table
    adopt = [k for k, s, h in table if s is not None and h is not None and h >= base_h + 0.02 and s >= base_s - 0.01]
    out["adopt_candidates"] = adopt
    out["baseline"] = {"select": base_s, "holdout": base_h}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log("=== 확인 구간(2018~21) Recall@20 순 ===")
    for k, s, h in table:
        log(f"  {k:26s} 선택 {s}  확인 {h}")
    log(f"채택 후보(확인 ≥ 기준+2pp, 선택 ≥ 기준−1pp): {adopt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
