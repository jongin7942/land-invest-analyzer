"""TW 순위 안정성 (MASTER_SPEC §13 순위 안정성 출력): 평균순위 · TOP10 생존율 · P90 순위.

시장 수준·예측 오차·시나리오 확률·금리를 흔들어 300회 반복한다. 순이익은 매도가의 1차식으로 근사한다
(각 후보의 Bear/Base/Bull 세 점을 선형 보간 — 세금 누진 구간을 넘으면 약간 어긋나므로 근사임을 표시).
    .venv/Scripts/python.exe tools/tw_stability.py [--tag 3eok] → reports/tw_stability_<tag>.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
N_DRAWS = 300
MARKET_SD = 0.08        # 시장 수준 log 흔들림(전세가율 조건부 분포의 폭 ≈ ±0.08)
PRED_SD = 0.085         # 잔차 P20/P80 ≈ ±0.085
RATE_EXTRA_SD = 0.01    # 금리 ±1%p → 이자비용 근사: 대출 × Δ금리 × 5년 × 0.6(원리금균등 평균잔액)
PROB_SETS = [(0.25, 0.5, 0.25), (0.35, 0.45, 0.20), (0.20, 0.5, 0.30), (0.4, 0.4, 0.2)]


def interp(xs, ys, x):
    (x0, y0), (x1, y1), (x2, y2) = sorted(zip(xs, ys))
    if x <= x1:
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 != x0 else y1
    return y1 + (y2 - y1) * (x - x1) / (x2 - x1) if x2 != x1 else y1


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", default="3eok"); args = ap.parse_args()
    rows = [r for r in csv.DictReader((ROOT / "reports" / f"tw_combined_{args.tag}.csv").open(encoding="utf-8")) if r["tw_rank"]]
    cands = []
    for r in rows:
        try:
            ex = [float(r["exit_bear"]), float(r["exit_base"]), float(r["exit_bull"])]
            np_ = [float(r["np_bear"]), float(r["np_base"]), float(r["np_bull"])]
        except (ValueError, TypeError):
            continue
        loan = max(0.0, float(r["price"]) - float(r["self_capital"] or 0))
        cands.append({"name": r["name"], "band": r["band"], "tw_rank": int(r["tw_rank"]), "ex": ex, "np": np_, "loan": loan,
                      "model": r["exit_model"], "base": float(r["price"])})
    rng = random.Random(42)
    ranks = {i: [] for i in range(len(cands))}
    for _ in range(N_DRAWS):
        m = rng.gauss(0, MARKET_SD); dr = rng.gauss(0, RATE_EXTRA_SD); pb, pm, pu = rng.choice(PROB_SETS)
        tws = []
        for i, c in enumerate(cands):
            e = rng.gauss(0, PRED_SD) if not c["model"].startswith("NONE") else 0.0
            f = math.exp(m + e)
            vals = [interp(c["ex"], c["np"], x * f) for x in c["ex"]]
            tw = pb * vals[0] + pm * vals[1] + pu * vals[2] - c["loan"] * dr * 5 * 0.6
            tws.append((tw, i))
        tws.sort(key=lambda t: -t[0])
        for pos, (_, i) in enumerate(tws, 1):
            ranks[i].append(pos)
    out = []
    for i, c in enumerate(cands):
        rs = sorted(ranks[i])
        out.append({"name": c["name"], "band": c["band"], "tw_rank": c["tw_rank"], "mean_rank": round(sum(rs) / len(rs), 1),
                    "top10_survival": round(sum(1 for x in rs if x <= 10) / len(rs), 3),
                    "top5_entry": round(sum(1 for x in rs if x <= 5) / len(rs), 3),
                    "p90_rank": rs[int(len(rs) * 0.9) - 1], "model": c["model"][:14]})
    out.sort(key=lambda x: x["mean_rank"])
    res = {"tag": args.tag, "n": len(cands), "draws": N_DRAWS, "note": "순이익은 Bear/Base/Bull 세 점의 선형 보간 근사 · 시장 log ±0.08 · 예측 오차 ±0.085 · 금리 ±1%p · 확률 4벌",
           "rows": out}
    (ROOT / "reports" / f"tw_stability_{args.tag}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for x in out[:12]:
        print(f"{x['mean_rank']:5.1f} (TW{x['tw_rank']:3d}) 생존 {x['top10_survival']:.0%} P90 {x['p90_rank']:3d}  {x['name']} {x['band']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
