"""Exit Price Engine v0.1 — 패널 구축 · Walk-Forward · 현재 시점 예측 · 계급도 (MASTER_SPEC §12).

    .venv/Scripts/python.exe tools/run_exit_price.py [--bands 84,59,74] [--lams 0.3,1,3,10]

산출:
  reports/exit_price_backtest.json     feature set × λ × 테스트연도 성적(MAE·IC·Winner Recall·상위10% lift)
  rules/exit_price_2026.csv            단지×면적별 5년 뒤 Bear/Base/Bull 배율(잔차 P20/P50/P80)
  reports/hierarchy_2026.json          급지 계급도: 급지 간 ㎡단가 격차(%), 급지 상승 조건별 lift
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import jobs as jobs_mod, model as model_mod, panel as panel_mod  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import median, percentile  # noqa: E402

ENTRY_YEARS = list(range(2007, 2022))          # 2007~2021 진입(결과 2012~2026) — 하락기(2008~2013) 포함
TEST_YEARS = list(range(2013, 2022))           # 학습 = 진입 ≤ T−5
NOW_YM = "202606"                              # 현재 예측 진입 시점(스냅샷 최신 202609 − 스무딩 여유)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bands", default="84,59,74")
    ap.add_argument("--lams", default="0.3,1,3,10")
    ap.add_argument("--relative", action="store_true", help="목표를 진입연도 내 중앙값 대비 편차로(시장 수준 분리)")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    bands = tuple(args.bands.split(","))
    lams = [float(x) for x in args.lams.split(",")]
    t0 = time.time()

    with get_conn() as conn:
        cx = store.load_complexes(conn)
        prices = store.load_prices(conn, cx, bands)
        store.attach_academies(cx)
        jeonse = store.load_jeonse(conn, set(prices))
        stations = panel_mod.load_stations(conn)
        jobs = jobs_mod.Jobs(cx, conn)
    print(f"[일자리] 스냅샷 {jobs.yms} · 법정동 코드 매핑 {len(jobs.code_of)}")
    cx_all = store.load_complexes(conn, min_households=0)
    prices_all = store.load_prices(conn, cx_all, bands)
    print(f"[급지 지도] 전체 단지 {len(cx_all)} · 단지×면적 {len(prices_all)} (행은 1,000세대 이상 {len(cx)} 만)", flush=True)
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs if jobs.available else None,
                                tier_complexes=cx_all, tier_prices=prices_all)
    cache = ROOT / "logs" / f"_exit_panel_{'_'.join(bands)}.pkl"
    if cache.exists() and not args.no_cache:
        rows = pickle.loads(cache.read_bytes()); print(f"[패널] 캐시 {cache.name} {len(rows)}행")
    else:
        rows = pb.build(ENTRY_YEARS); cache.write_bytes(pickle.dumps(rows))
    market_level = {}
    if args.relative:
        by_y = {}
        for r in rows:
            if r.target is not None: by_y.setdefault(r.entry_ym, []).append(r.target)
        market_level = {ym: median(v) for ym, v in by_y.items()}
        for r in rows:
            if r.target is not None: r.target = r.target - market_level[r.entry_ym]
        print(f"[상대화] 진입연도별 시장 중앙값 5년 log 수익: { {k: round(v,3) for k, v in sorted(market_level.items())} }")
    with_t = [r for r in rows if r.target is not None]
    print(f"[패널] 행 {len(rows)} · 목표 있음 {len(with_t)} · 진입연도 {ENTRY_YEARS[0]}~{ENTRY_YEARS[-1]} ({time.time()-t0:.0f}s)", flush=True)
    miss = Counter(f for r in rows for f in panel_mod.FEATURES if r.x.get(f) is None)
    print("  결측 상위:", miss.most_common(6))

    # ── Walk-Forward ──
    bt = {}
    best = (None, None, -9)
    for name, feats in panel_mod.FEATURE_SETS.items():
        res_by_lam = model_mod.walk_forward(rows, feats, TEST_YEARS, lams)
        for lam, res in res_by_lam.items():
            ics = [v["ic"] for v in res.values() if v.get("ic") is not None]
            recs = [v["winner_recall"] for v in res.values() if v.get("winner_recall") is not None]
            recs30 = [v["winner_recall30"] for v in res.values() if v.get("winner_recall30") is not None]
            precs = [v["precision_above_median"] for v in res.values() if v.get("precision_above_median") is not None]
            maes = [v["mae"] for v in res.values() if v.get("mae") is not None]
            mkt = [v["mae_market_only"] for v in res.values() if v.get("mae_market_only") is not None]
            summ = {"ic_mean": round(sum(ics) / len(ics), 3) if ics else None,
                    "recall_mean": round(sum(recs) / len(recs), 3) if recs else None,
                    "recall30_mean": round(sum(recs30) / len(recs30), 3) if recs30 else None,
                    "precision_mean": round(sum(precs) / len(precs), 3) if precs else None,
                    "mae_mean": round(sum(maes) / len(maes), 4) if maes else None,
                    "mae_market_only_mean": round(sum(mkt) / len(mkt), 4) if mkt else None,
                    "by_year": res}
            bt[f"{name}|lam={lam}"] = summ
            score = (summ["ic_mean"] or -9)
            if score > best[2]:
                best = (name, lam, score)
            print(f"  {name:11s} λ={lam:<4} IC {summ['ic_mean']} · Recall@20 {summ['recall_mean']} · Recall@30 {summ['recall30_mean']} · 중앙값이상 {summ['precision_mean']} · MAE {summ['mae_mean']}", flush=True)
    print(f"[선택] {best[0]} λ={best[1]} (IC {best[2]})  ({time.time()-t0:.0f}s)")

    # ── 최종 적합(전 창) → 현재 예측 ──
    feats = panel_mod.FEATURE_SETS[best[0]]
    final = model_mod.fit(with_t, feats, best[1])
    now_rows = []
    for (cid, band) in prices:
        r = pb.row(cid, band, NOW_YM)
        if r is not None:
            now_rows.append(r)
    preds = []
    mk = sorted(market_level.values()) if market_level else []
    mkt_q = {q: percentile(mk, q) for q in (0.2, 0.5, 0.8)} if mk else {0.2: 0.0, 0.5: 0.0, 0.8: 0.0}
    if market_level:
        print(f"[시장 수준 시나리오] 과거 5년 log 수익 P20/P50/P80 = {mkt_q[0.2]:.3f}/{mkt_q[0.5]:.3f}/{mkt_q[0.8]:.3f} (진입연도 {len(mk)}개)")
    for r in now_rows:
        p = final.predict(r.x)
        if p is None:
            continue
        preds.append({
            "complex_id": r.complex_id, "band": r.band, "name": cx[r.complex_id].name, "lawd_cd": cx[r.complex_id].lawd_cd,
            "price_now": round(r.price), "pred_log5y": round(p, 4),
            "base_factor": round(math.exp(p + mkt_q[0.5] + final.resid_q[0.5]), 4),
            "bear_factor": round(math.exp(p + mkt_q[0.2] + final.resid_q[0.2]), 4),
            "bull_factor": round(math.exp(p + mkt_q[0.8] + final.resid_q[0.8]), 4),
            "market_scenario_log": {"bear": round(mkt_q[0.2], 4), "base": round(mkt_q[0.5], 4), "bull": round(mkt_q[0.8], 4)},
            "tier": r.x.get("tier"), "dist_center_km": round(r.x["dist_center_km"], 2) if r.x.get("dist_center_km") is not None else None,
            "model": f"{best[0]}|lam={best[1]}" + ("|relative" if args.relative else ""), "status": "SCENARIO_WALKFORWARD_v0.1",
        })
    with (ROOT / "rules" / "exit_price_2026.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(preds[0].keys())); w.writeheader(); w.writerows(preds)
    pf = [p["pred_log5y"] for p in preds]
    print(f"[현재 예측] {len(preds)}건 · 5년 log 수익 분포 P10 {percentile(pf,0.1):.3f} / P50 {percentile(pf,0.5):.3f} / P90 {percentile(pf,0.9):.3f} · 잔차 P20/P80 {final.resid_q[0.2]:.3f}/{final.resid_q[0.8]:.3f}")

    # ── 계급도 ──
    hier = hierarchy(pb, rows, cx, prices)
    donga = [p for p in preds if p["complex_id"] == 482]
    report = {
        "as_of": NOW_YM, "entry_years": ENTRY_YEARS, "test_years": TEST_YEARS, "n_rows": len(rows), "n_with_target": len(with_t),
        "missing_top": miss.most_common(8), "backtest": bt, "selected": {"set": best[0], "lam": best[1], "ic": best[2]},
        "final_coef": {fe: round(b, 4) for fe, b in zip(feats, final.beta[1:])}, "final_n": final.n,
        "resid_q": {str(k): round(v, 4) for k, v in final.resid_q.items()},
        "now_pred_dist": {q: round(percentile(pf, q), 4) for q in (0.1, 0.25, 0.5, 0.75, 0.9)},
        "donga_482": donga, "relative_mode": args.relative, "market_level_by_entry": {k: round(v, 4) for k, v in sorted(market_level.items())}, "market_scenario_log": {str(k): round(v, 4) for k, v in mkt_q.items()}, "seconds": round(time.time() - t0),
    }
    (ROOT / "reports" / ("exit_price_backtest_relative.json" if args.relative else "exit_price_backtest.json")).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "reports" / "hierarchy_2026.json").write_text(json.dumps(hier, ensure_ascii=False, indent=1), encoding="utf-8")
    print("동아 482:", donga)
    print("계급도 요약:", json.dumps({k: hier[k] for k in ("tier_gap_pct_now", "promotion_base_rate", "conditions")}, ensure_ascii=False)[:1500])
    return 0


def hierarchy(pb: panel_mod.PanelBuilder, rows, cx, prices) -> dict:
    """급지 간 격차(%)와 '급지 상승' 조건별 lift. 진입 Y 의 법정동 tier vs Y+5 의 tier."""
    out = {}
    t_now = panel_mod.ym_idx(NOW_YM)
    tiers_now, info_now = pb.tiers_at(t_now)
    # 급지별 ㎡단가 수준 중앙값 → 인접 급지 격차
    levels: dict[int, list[float]] = defaultdict(list)
    for (cid, band), s in prices.items():
        vals = [v for v in s.p50[t_now - 23:t_now + 1] if v]
        tr = tiers_now.get(cx[cid].emd_key)
        if len(vals) >= 6 and tr:
            levels[tr].append(math.log(median(vals) / store.BAND_M2[band]))
    med = {tr: median(v) for tr, v in levels.items()}
    out["tier_level_m2_won"] = {tr: round(math.exp(m)) for tr, m in sorted(med.items())}
    out["tier_gap_pct_now"] = {f"{tr}→{tr-1}": round((math.exp(med[tr - 1] - med[tr]) - 1) * 100, 1)
                              for tr in sorted(med) if tr - 1 in med}
    out["tier_counts_emd"] = dict(sorted(Counter(tiers_now.values()).items()))
    # 승급 분석: 각 진입연도 Y 의 법정동 tier vs Y+5
    promo, base_n = Counter(), Counter()
    cond_hit: dict[str, list[int]] = defaultdict(lambda: [0, 0])   # cond → [승급 수, 전체 수]
    seen = set()
    for r in rows:
        if r.target is None:
            continue
        y = int(r.entry_ym[:4]); key = (cx[r.complex_id].emd_key, y)
        if key in seen:
            continue
        seen.add(key)
        t0 = panel_mod.ym_idx(r.entry_ym); t1 = t0 + panel_mod.HORIZON
        tiers0, _ = pb.tiers_at(t0); tiers1, _ = pb.tiers_at(t1)
        a, b = tiers0.get(key[0]), tiers1.get(key[0])
        if a is None or b is None:
            continue
        up = b < a
        base_n[a] += 1; promo[a] += int(up)
        conds = {
            "역_계획공표(진입시)": r.x.get("station_planned") == 1.0,
            "역_개통_이후5년내": _station_opened_between(pb, cx[r.complex_id], r.entry_ym),
            "신축입주_2km(이후2년)": r.x.get("supply_planned", 0) > math.log1p(1000),
            "학원가_상위(≥30개)": (cx[r.complex_id].academies_500m or 0) >= 30,
            "상위급지_중심_3km내": (r.x.get("dist_center_km") or 99) <= 3,
            "상위급지_중심_10km밖": (r.x.get("dist_center_km") or 0) > 10,
            "시군구_5년모멘텀_상위": (r.x.get("gu_mom5") or 0) > 0.3,
            "전세가율_≥0.7": (r.x.get("jeonse_ratio") or 0) >= 0.7,
        }
        for c, hit in conds.items():
            if hit:
                cond_hit[c][1] += 1; cond_hit[c][0] += int(up)
    total_up, total = sum(promo.values()), sum(base_n.values())
    base_rate = total_up / total if total else None
    out["promotion_base_rate"] = round(base_rate, 3) if base_rate else None
    out["promotion_by_tier"] = {tr: {"n": base_n[tr], "up_rate": round(promo[tr] / base_n[tr], 3)} for tr in sorted(base_n) if base_n[tr]}
    out["conditions"] = {c: {"n": v[1], "up_rate": round(v[0] / v[1], 3) if v[1] else None,
                             "lift": round((v[0] / v[1]) / base_rate, 2) if v[1] and base_rate else None}
                         for c, v in cond_hit.items()}
    out["note"] = ("승급 = 진입연도 법정동 급지가 5년 뒤 한 단계 이상 올라감(급지는 매 시점 재계산). "
                   "조건은 진입 시점 정보(역 개통은 이후 5년 사건). lift = 조건 있을 때 승급률 ÷ 기본 승급률. HEURISTIC 임계값")
    return out


def _station_opened_between(pb, c, entry_ym: str) -> bool:
    y0 = store._ym_index(entry_ym); y1 = y0 + panel_mod.HORIZON
    for i in pb._near(pb._sgrid, c.lat, c.lon):
        la, lo, opened, _ = pb.stations[i]
        if opened and y0 < store._ym_index(opened) <= y1 and store.haversine_m(c.lat, c.lon, la, lo) <= 1500:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
