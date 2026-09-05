"""모델 선정 — 안정형·공격형 (종인님 지시 2026-09-05 밤: "변수 다 들어가면 다시 시뮬레이션해서 최적 이론, 안정형·공격형 뽑고 사용자가 선택").

패널: logs/_exit_panel_policy.pkl (E + 전문가 24 + 정책 10 변수 전부 포함).
격자: 변수집합 × 모델(ridge / boost / boost-rank) — 선택 구간(2016~18)에서 목표별 최고를 고르고 확인 구간(2019~21)에서 한 번 평가.
목표
  공격형(AGGRESSIVE): 승자 포착률 Recall@20 (실제 상위 10% 를 예측 상위 20% 가 잡는 비율)
  안정형(STABLE)    : 예측 상위 20% 의 '중앙값 이상' 비율 + '하위 20% 회피' (1 − 예측 상위20% 중 실제 하위20% 비율) 의 평균
결과: reports/model_select.json (+ 콘솔). 선택 편향을 줄이기 위해 선택 구간에서 고른 뒤 확인 구간 성적을 그대로 적는다.
    .venv/Scripts/python.exe tools/model_select.py [--quick]
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
from apt_engine.exitprice import boost as boost_mod, model as model_mod, panel as panel_mod  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

CACHE = ROOT / "logs" / "_exit_panel_policy.pkl"
E = et.E; G = panel_mod.EXPERT_GROUPS; PG = panel_mod.POLICY_GROUPS
FEATURE_SETS = {
    "E": E,
    "E+거래량": E + G["거래량선행"],
    "E+대장갭+거래량": E + G["대장갭메우기"] + G["거래량선행"],
    "E+브랜드재건축": E + G["브랜드·재건축사업성"],
    "E+갭투자전세": E + G["갭투자·전세압력"],
    "E+고점대비": E + G["고점대비·가격대"],
    "E+미분양": E + PG["미분양"],
    "E+규제": E + PG["규제지역"] + PG["분양가상한제"],
    "E+거래량+미분양+규제": E + G["거래량선행"] + PG["미분양"] + PG["규제지역"],
    "X(전문가전부)": panel_mod.FEATURE_SETS["X_expert"],
    "ALL": panel_mod.FEATURE_SETS["X_expert"] + panel_mod.POLICY,
}


def metrics(pairs):
    pairs = [(p, a) for p, a in pairs if p is not None and a is not None]
    n = len(pairs)
    if n < 10:
        return None
    pred = [p for p, _ in pairs]; act = [a for _, a in pairs]
    order_act = sorted(range(n), key=lambda i: -act[i])
    top_act = set(order_act[: max(1, n // 10)]); bot_act = set(order_act[-max(1, n // 5):])
    top_pred = set(sorted(range(n), key=lambda i: -pred[i])[: max(1, n // 5)])
    med = median(act)
    above = sum(1 for i in top_pred if act[i] >= med) / len(top_pred)
    loser = sum(1 for i in top_pred if i in bot_act) / len(top_pred)
    return {"n": n, "recall": len(top_act & top_pred) / len(top_act), "above_median": above, "loser_share": loser,
            "stable": (above + (1 - loser)) / 2, "ic": model_mod.spearman(pred, act)}


def run(rows, feats, kind, years, seeds):
    res = {}
    for T in years:
        tr, te = et.split(rows, T)
        if kind == "ridge":
            f = model_mod.fit(tr, feats, et.LAM)
            if not f: continue
            pairs = [(f.predict(t.x), t.target) for t in te]
        else:
            trr = et.rank_target(tr) if kind == "boost-rank" else tr
            ms = [m for m in (boost_mod.fit_boost(trr, feats, rounds=150, seed=s) for s in seeds) if m]
            if not ms: continue
            pairs = [(sum(m.predict(t.x) for m in ms) / len(ms), t.target) for t in te]
        res[T] = metrics(pairs)
    return res


def summ(res, years, key):
    v = [res[T][key] for T in years if res.get(T)]
    return round(sum(v) / len(v), 4) if v else None


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--quick", action="store_true"); a = ap.parse_args()
    rows = et.demean(pickle.loads(CACHE.read_bytes()))
    seeds = (7,) if a.quick else (7, 11)
    grid = []
    for fname, feats in FEATURE_SETS.items():
        for kind in ("ridge", "boost", "boost-rank"):
            if kind == "ridge" and len(feats) > 60:      # ridge 는 완전행만 → 대형 집합은 표본이 무너짐
                continue
            res = run(rows, feats, kind, et.ALL_YEARS, seeds)
            row = {"features": fname, "n_features": len(feats), "model": kind,
                   "select": {k: summ(res, et.SELECT_YEARS, k) for k in ("recall", "stable", "above_median", "loser_share", "ic")},
                   "holdout": {k: summ(res, et.HOLDOUT_YEARS, k) for k in ("recall", "stable", "above_median", "loser_share", "ic")},
                   "all": {k: summ(res, et.ALL_YEARS, k) for k in ("recall", "stable", "above_median", "loser_share", "ic")},
                   "years": {T: {k: round(v, 3) for k, v in r.items()} for T, r in res.items() if r}}
            grid.append(row)
            et.log(f"{fname:20s} {kind:10s} 공격(recall) 선택 {row['select']['recall']} 확인 {row['holdout']['recall']} | 안정 선택 {row['select']['stable']} 확인 {row['holdout']['stable']} (n={row['all']['n'] if 'n' in row['all'] else '-'})")
    def pick(objective):
        cand = [g for g in grid if g["select"][objective] is not None and g["holdout"][objective] is not None]
        best_sel = max(cand, key=lambda g: (g["select"][objective], g["holdout"][objective]))
        best_hold = max(cand, key=lambda g: g["holdout"][objective])
        return {"chosen_by_select": {k: best_sel[k] for k in ("features", "model", "select", "holdout")},
                "best_by_holdout": {k: best_hold[k] for k in ("features", "model", "select", "holdout")}}
    out = {"grid": grid, "AGGRESSIVE": pick("recall"), "STABLE": pick("stable"), "baseline_E_boost": next((g for g in grid if g["features"] == "E" and g["model"] == "boost"), None)}
    (ROOT / "reports" / "model_select.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for obj in ("AGGRESSIVE", "STABLE"):
        c = out[obj]["chosen_by_select"]; h = out[obj]["best_by_holdout"]
        et.log(f"[{obj}] 선택구간 최고: {c['features']}/{c['model']} → 확인 {c['holdout']} | 확인구간 최고: {h['features']}/{h['model']} {h['holdout']}")
    et.log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
