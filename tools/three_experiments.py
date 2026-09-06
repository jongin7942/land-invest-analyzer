"""세 가지 독립 실험 (spec/EXPERIMENT_PROTOCOL_20260907.md 를 그대로 구현 — 규칙·가중치 탐색 없음).

    .venv/Scripts/python.exe tools/three_experiments.py [--horizon 60|36] [--no-cache]
5년(60) 은 logs/_exit_panel_jongin.pkl(E+모든 변수) 재사용, 3년(36) 은 HORIZON=36 으로 새로 만든다(진입 2007~2023).
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from apt_engine.exitprice import panel as panel_mod  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--horizon", type=int, default=60); ap.add_argument("--no-cache", action="store_true")
ARGS = ap.parse_args()
panel_mod.HORIZON = ARGS.horizon
import expert_theories as et  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import boost as boost_mod, jobs as jobs_mod, model as model_mod  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import haversine_m, median  # noqa: E402

H = ARGS.horizon
LAG = H // 12
CACHE = ROOT / "logs" / ("_exit_panel_jongin.pkl" if H == 60 else f"_exit_panel_3exp_{H}.pkl")
TEST_YEARS = list(range(2016, 2022)) if H == 60 else [2022, 2023]
ENTRY_YEARS = list(range(2007, 2022 if H == 60 else 2024))
E = panel_mod.FEATURE_SETS["E_+theory2"]
MOM_CHEAP = ["own_pct", "own_pct_sq", "mom1", "mom3", "rel_gu", "rel_gu_x_tier", "emd_rel_mom3", "rel_emd"]
EQUITY = 3e8; MAX_PRICE = 10e8; COST_IN, COST_OUT, SPREAD = 0.015, 0.004, 0.015
SEEDS = (7, 11, 13)


def log(m): et.log(m)


# ── 패널 ──
def build():
    if CACHE.exists() and not ARGS.no_cache:
        rows = pickle.loads(CACHE.read_bytes()); log(f"패널 캐시 {CACHE.name} {len(rows)}행"); return rows
    bands = ("84", "59", "74")
    with get_conn() as conn:
        cx = store.load_complexes(conn); prices = store.load_prices(conn, cx, bands); store.attach_academies(cx)
        jeonse = store.load_jeonse(conn, set(prices)); stations = panel_mod.load_stations(conn); jobs = jobs_mod.Jobs(cx, conn)
        cx_all = store.load_complexes(conn, min_households=0); prices_all = store.load_prices(conn, cx_all, bands)
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs if jobs.available else None, tier_complexes=cx_all, tier_prices=prices_all)
    rows = []
    for y in ENTRY_YEARS:
        rows += pb.build([y]); log(f"패널 {y} 누적 {len(rows)}행")
    CACHE.write_bytes(pickle.dumps(rows))
    return rows


# ── 실험 변수 계산 (패널 행에서, 미래 정보 없음) ──
def add_hedonic(rows):
    """진입월 횡단면 ridge → hedonic_resid. 정상가격 CV 성적도 반환."""
    feats = ["age", "age_sq", "log_hh", "tier", "dist_tier1_km", "dist_center_km", "log_academy", "jobs_3km", "station_km", "far", "far_missing", "b59", "b74"]
    by_ym = defaultdict(list)
    for r in rows:
        by_ym[r.entry_ym].append(r)
    cv = {}
    for ym, rs in by_ym.items():
        far_vals = [r.x["far"] for r in rs if r.x.get("far") is not None]
        far_med = median(far_vals) if far_vals else 200.0
        class S: __slots__ = ("x", "target", "complex_id", "band", "entry_ym", "price")
        objs = []
        for r in rs:
            o = S(); o.complex_id, o.band, o.entry_ym, o.price = r.complex_id, r.band, r.entry_ym, r.price
            a = r.x.get("age")
            o.x = {"age": a, "age_sq": (a * a) if a is not None else None, "log_hh": r.x.get("log_hh"), "tier": r.x.get("tier"), "dist_tier1_km": r.x.get("dist_tier1_km"),
                   "dist_center_km": r.x.get("dist_center_km"), "log_academy": r.x.get("log_academy"), "jobs_3km": r.x.get("jobs_3km"), "station_km": r.x.get("station_km"),
                   "far": r.x["far"] if r.x.get("far") is not None else far_med, "far_missing": 0.0 if r.x.get("far") is not None else 1.0,
                   "b59": 1.0 if r.band == "59" else 0.0, "b74": 1.0 if r.band == "74" else 0.0}
            o.target = math.log(r.price / store.BAND_M2[r.band]); objs.append((r, o))
        usable = [(r, o) for r, o in objs if all(o.x.get(f) is not None for f in feats)]
        if len(usable) < 60:
            continue
        # 5겹 CV
        k = 5; errs = []; preds = {}
        for i in range(k):
            tr = [o for j, (_, o) in enumerate(usable) if j % k != i]; te = [(r, o) for j, (r, o) in enumerate(usable) if j % k == i]
            f = model_mod.fit(tr, feats, 1.0)
            if not f: continue
            for r, o in te:
                p = f.predict(o.x)
                if p is not None:
                    errs.append(o.target - p); preds[id(r)] = p
        if errs:
            m = sum(o.target for _, o in usable) / len(usable); ss = sum((o.target - m) ** 2 for _, o in usable)
            cv[ym] = {"n": len(errs), "cv_r2": round(1 - sum(e * e for e in errs) / ss, 4), "cv_mae_log": round(sum(abs(e) for e in errs) / len(errs), 4)}
        # 전체 적합 잔차(정상가격 대비 할인) — 각 행의 잔차는 CV 예측 기준(자기 자신을 뺀 적합)
        for r, o in usable:
            p = preds.get(id(r))
            r.x["hedonic_resid"] = (o.target - p) if p is not None else None
        for r, _ in objs:
            r.x.setdefault("hedonic_resid", None)
    return cv


def add_substitutes(rows):
    """같은 시점·같은 면적대의 우월 대체재 수. 우월 = 15km 안 & dist_tier1 ≤ & tier ≤ & age ≤ & price ≤ 나×(1+k)."""
    with get_conn() as conn:
        cx = store.load_complexes(conn)
    by = defaultdict(list)
    for r in rows:
        by[(r.entry_ym, r.band)].append(r)
    for key, rs in by.items():
        n = len(rs)
        for r in rs:
            c = cx.get(r.complex_id)
            if c is None or r.x.get("dist_tier1_km") is None or r.x.get("tier") is None or r.x.get("age") is None:
                r.x.update({"subst_n0": None, "subst_n10": None, "subst_n20": None, "subst_share20": None}); continue
            cnt = [0, 0, 0]
            for o in rs:
                if o is r: continue
                oc = cx.get(o.complex_id)
                if oc is None or o.x.get("dist_tier1_km") is None or o.x.get("tier") is None or o.x.get("age") is None: continue
                if o.x["dist_tier1_km"] > r.x["dist_tier1_km"] or o.x["tier"] > r.x["tier"] or o.x["age"] > r.x["age"]: continue
                if haversine_m(c.lat, c.lon, oc.lat, oc.lon) > 15000: continue
                for i, k in enumerate((0.0, 0.1, 0.2)):
                    if o.price <= r.price * (1 + k): cnt[i] += 1
            r.x.update({"subst_n0": math.log1p(cnt[0]), "subst_n10": math.log1p(cnt[1]), "subst_n20": math.log1p(cnt[2]), "subst_share20": cnt[2] / max(1, n - 1)})


def add_employment_growth(rows):
    """스냅샷 Y 는 Y년 12월 공개로 간주 → 진입 T 는 연도 ≤ T−1 인 최신 두 스냅샷. 3km·10km 연증가율 − 수도권 연증가율."""
    with get_conn() as conn:
        cx = store.load_complexes(conn); jobs = jobs_mod.Jobs(cx, conn)
    yms = jobs.yms                                        # '2016-0' 형식
    year_of = lambda s: int(s[:4])
    # 법정동 중심점 격자
    centers = jobs.center
    codes = list(centers)
    def ring_sum(c, snap, radius):
        tot = 0
        for code in codes:
            la, lo = centers[code]
            if abs(la - c.lat) > radius / 111000 * 1.2 or abs(lo - c.lon) > radius / 88000 * 1.2: continue
            if haversine_m(c.lat, c.lon, la, lo) <= radius:
                tot += jobs.snap[snap].get(code, 0)
        return tot
    metro = {s: sum(jobs.snap[s].values()) for s in yms}
    cache = {}
    for r in rows:
        T = int(r.entry_ym[:4])
        avail = [s for s in yms if year_of(s) <= T - 1]
        if len(avail) < 2:
            r.x.update({"emp_g3km": None, "emp_g10km": None}); continue
        s1, s0 = avail[-1], avail[-2]; yrs = year_of(s1) - year_of(s0)
        key = (r.complex_id, s0, s1)
        if key not in cache:
            c = cx.get(r.complex_id)
            if c is None:
                cache[key] = (None, None)
            else:
                a3, b3 = ring_sum(c, s0, 3000), ring_sum(c, s1, 3000); a10, b10 = ring_sum(c, s0, 10000), ring_sum(c, s1, 10000)
                mg = (math.log(metro[s1] / metro[s0])) / yrs
                cache[key] = ((math.log((b3 + 1) / (a3 + 1)) / yrs - mg) if a3 > 0 else None, (math.log((b10 + 1) / (a10 + 1)) / yrs - mg) if a10 > 0 else None)
        r.x["emp_g3km"], r.x["emp_g10km"] = cache[key]


def add_income_growth(rows):
    inc, inc_sd = panel_mod.INCOME, panel_mod.INCOME_SIDO
    with get_conn() as conn:
        cx = store.load_complexes(conn)
    # 수도권 중앙 성장(시군구 단위 중앙값)
    def metro_g(y, k):
        v = [math.log(inc[(l, y)] / inc[(l, y - k)]) for (l, yy) in inc if yy == y and (l, y - k) in inc]
        return median(v) if len(v) >= 10 else None
    for r in rows:
        T = int(r.entry_ym[:4]); y = T - 2; l = cx[r.complex_id].lawd_cd if r.complex_id in cx else None
        out = {}
        for k, nm in ((1, "inc_g1"), (3, "inc_g3")):
            if l and (l, y) in inc and (l, y - k) in inc and metro_g(y, k) is not None:
                out[nm] = math.log(inc[(l, y)] / inc[(l, y - k)]) - metro_g(y, k)
            else:
                out[nm] = None
        r.x.update(out)


# ── 평가 ──
def bok(year):
    return panel_mod.BOK.get(year, 2.5)


def top10_metrics(picks, cohort, year):
    """picks: 선택된 10행. cohort: 그 해 전체(target 있음). 초과수익=demeaned target(log). 순자산 배율=세금 제외."""
    n = len(cohort); top = set(id(r) for r in sorted(cohort, key=lambda r: -r.target)[: max(1, n // 10)])
    hit = sum(1 for r in picks if id(r) in top) / len(picks)
    exc = sum(r.target for r in picks) / len(picks)
    tws = []
    for r in picks:
        p0 = r.price; p1 = p0 * math.exp(r.raw)
        loan = max(0.0, min(p0 - EQUITY, 0.7 * p0)); interest = loan * (bok(year) / 100 + SPREAD) * (H / 12)
        net = p1 - p0 - COST_IN * p0 - COST_OUT * p1 - interest
        tws.append((EQUITY + net) / EQUITY)
    return {"hit": round(hit, 3), "excess": round(exc, 4), "tw_mult": round(sum(tws) / len(tws), 4), "n_picks": len(picks)}


def boost_pred(train, test, feats):
    ms = [m for m in (boost_mod.fit_boost(train, feats, rounds=150, seed=s) for s in SEEDS) if m]
    if not ms: return None
    return {id(t): sum(m.predict(t.x) for m in ms) / len(ms) for t in test}


def eligible(rs):
    return [r for r in rs if r.target is not None and r.price <= MAX_PRICE]


def run_model(rows, feats, label, subset_key=None):
    """walk-forward: 학습 ≤ T−LAG. subset_key 가 있으면 테스트를 그 변수들이 있는 행으로 제한(기준 B/C 비교용)."""
    res = {}
    for T in TEST_YEARS:
        train = [r for r in rows if int(r.entry_ym[:4]) <= T - LAG]
        test = eligible([r for r in rows if int(r.entry_ym[:4]) == T])
        if subset_key:
            test = [r for r in test if all(r.x.get(k) is not None for k in subset_key)]
        if len(test) < 30: continue
        pred = boost_pred(train, test, feats)
        if pred is None: continue
        picks = sorted(test, key=lambda r: -pred[id(r)])[:10]
        m = top10_metrics(picks, test, T); m["n_test"] = len(test)
        m["recall20"] = et.eval_pred([(pred[id(r)], r.target) for r in test])["recall"]
        m["picks"] = [(r.complex_id, r.band) for r in picks]
        res[T] = m
        log(f"  {label:34s} T={T} n={len(test)} 적중 {m['hit']:.2f} 초과 {m['excess']:+.3f} TW {m['tw_mult']:.3f} recall20 {m['recall20']:.3f}")
    return res


def rank_avg_model(rows, base_feats, new_var, label):
    """실험 1: 기준 예측 순위 + 새 변수 순위(동일 가중, 사전 고정). 테스트는 새 변수가 있는 행."""
    res = {}
    for T in TEST_YEARS:
        train = [r for r in rows if int(r.entry_ym[:4]) <= T - LAG]
        test = [r for r in eligible([r for r in rows if int(r.entry_ym[:4]) == T]) if r.x.get(new_var) is not None]
        if len(test) < 30: continue
        pred = boost_pred(train, test, base_feats)
        if pred is None: continue
        n = len(test)
        rk_b = {id(r): i for i, r in enumerate(sorted(test, key=lambda r: pred[id(r)]))}
        rk_n = {id(r): i for i, r in enumerate(sorted(test, key=lambda r: r.x[new_var]))}
        score = {id(r): rk_b[id(r)] + rk_n[id(r)] for r in test}
        picks = sorted(test, key=lambda r: -score[id(r)])[:10]
        m = top10_metrics(picks, test, T); m["n_test"] = n
        # 보조: 새 변수 vs 기준 잔차 Spearman
        resid = [(r.x[new_var], r.target - pred[id(r)]) for r in test]
        m["spearman_var_vs_resid"] = round(model_mod.spearman([a for a, _ in resid], [b for _, b in resid]) or 0, 3)
        m["picks"] = [(r.complex_id, r.band) for r in picks]
        res[T] = m
        log(f"  {label:34s} T={T} n={n} 적중 {m['hit']:.2f} 초과 {m['excess']:+.3f} TW {m['tw_mult']:.3f} | 변수↔잔차 ρ {m['spearman_var_vs_resid']:+.3f}")
    return res


def avg(res):
    ks = [k for k in ("hit", "excess", "tw_mult", "recall20", "spearman_var_vs_resid") if any(k in v for v in res.values())]
    return {k: round(sum(v[k] for v in res.values() if k in v) / max(1, sum(1 for v in res.values() if k in v)), 4) for k in ks} | {"n_years": len(res)}


def main() -> int:
    rows = build()
    for r in rows:
        r.raw = r.target                       # 세전 원 수익(비용후 순자산용)
    rows = et.demean(rows)
    log(f"지평 {H}개월 · 테스트연도 {TEST_YEARS} · 행 {len(rows)}")
    out = {"horizon": H, "test_years": TEST_YEARS, "protocol": "spec/EXPERIMENT_PROTOCOL_20260907.md"}
    # 변수 계산
    cv = add_hedonic(rows); add_substitutes(rows); add_employment_growth(rows); add_income_growth(rows)
    out["hedonic_fair_price_cv"] = {y: v for y, v in cv.items() if int(y[:4]) in TEST_YEARS}
    for f in ("hedonic_resid", "subst_n20", "emp_g3km", "emp_g10km", "inc_g1", "inc_g3"):
        log(f"커버리지 {f}: " + json.dumps({T: round(sum(1 for r in rows if int(r.entry_ym[:4]) == T and r.x.get(f) is not None) / max(1, sum(1 for r in rows if int(r.entry_ym[:4]) == T)), 2) for T in TEST_YEARS}))
    # 기준 A: 전체 표본
    out["A_baseline_full"] = run_model(rows, E, "A 기준·전체표본")
    # 실험 2
    out["exp2"] = {"B_baseline_sub": run_model(rows, E, "2B 기준·헤도닉표본", ["hedonic_resid"]),
                   "C_replace": run_model(rows, [f for f in E if f not in MOM_CHEAP] + ["hedonic_resid"], "2C 대체(모멘텀→헤도닉)", ["hedonic_resid"]),
                   "C_parallel": run_model(rows, E + ["hedonic_resid"], "2C 병행(E+헤도닉)", ["hedonic_resid"])}
    # 실험 3
    S = ["subst_n0", "subst_n10", "subst_n20", "subst_share20"]
    out["exp3"] = {"B_baseline_sub": run_model(rows, E, "3B 기준·대체재표본", S), "C_subst": run_model(rows, E + S, "3C E+대체재", S)}
    # 실험 1 (사전 고정 rank-average)
    out["exp1"] = {}
    for v in ("emp_g3km", "emp_g10km", "inc_g1", "inc_g3"):
        out["exp1"][f"B_baseline_{v}"] = run_model(rows, E, f"1B 기준·{v}표본", [v])
        out["exp1"][f"C_rankavg_{v}"] = rank_avg_model(rows, E, v, f"1C 기준순위+{v}")
    # 요약
    out["summary"] = {"A_baseline_full": avg(out["A_baseline_full"])}
    for ex in ("exp1", "exp2", "exp3"):
        out["summary"][ex] = {k: avg(v) for k, v in out[ex].items()}
    (ROOT / "reports" / f"three_experiments_{H}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log("=== 요약 (연평균) ===")
    log(f"A 기준·전체: {out['summary']['A_baseline_full']}")
    for ex in ("exp2", "exp3", "exp1"):
        for k, v in out["summary"][ex].items():
            log(f"{ex} {k:22s} {v}")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
