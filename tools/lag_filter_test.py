"""후행지 제외·빠른추종 우선 규칙이 승자 포착률을 올리는가 — 규칙 검증 (§35.6 · 확산 계급, 2026-09-05).

E 모델의 walk-forward 예측(테스트연도별)을 그대로 두고, 예측 상위 20% 를 고를 때
  R0 기본  · R1 후행지(선행성 ≤ −3개월) 제외 · R2 빠른추종(0~+3개월) 우선 · R3 R1+R2
로 바꿔 Recall@20(실제 상위 10% 포착)·중앙값이상 비율을 비교한다. 제외한 만큼 다음 순위로 채워 후보 수는 같게 둔다.
    .venv/Scripts/python.exe tools/lag_filter_test.py → reports/lag_filter_test.json
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.exitprice import model as model_mod, panel as panel_mod  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

TEST_YEARS = list(range(2013, 2022))
LAM = 3.0
FEATS = panel_mod.FEATURE_SETS["E_+theory2"]


def pick(rows_scored, rule):
    """rows_scored: [(pred, act, lead)] → 선택된 인덱스 집합(상위 20%)."""
    n = len(rows_scored); k = max(1, n // 5)
    order = sorted(range(n), key=lambda i: -rows_scored[i][0])
    if rule == "R0":
        return set(order[:k])
    def ok(i):
        lead = rows_scored[i][2]
        if rule in ("R1", "R3") and lead is not None and lead <= -3:
            return False
        return True
    chosen = [i for i in order if ok(i)]
    if rule in ("R2", "R3"):
        fast = [i for i in chosen if rows_scored[i][2] is not None and 0 <= rows_scored[i][2] <= 3]
        rest = [i for i in chosen if i not in set(fast)]
        # 빠른추종을 먼저 채우되 예측 상위 40% 안에서만(예측을 무시하지 않도록)
        top40 = set(order[: max(1, int(n * 0.4))])
        fast = [i for i in fast if i in top40]
        chosen = fast + [i for i in rest if i not in set(fast)]
    return set(chosen[:k])


def main() -> int:
    rows = pickle.loads((ROOT / "logs" / "_exit_panel_84_59_74.pkl").read_bytes())
    by_y: dict = {}
    for r in rows:
        if r.target is not None:
            by_y.setdefault(r.entry_ym, []).append(r.target)
    lvl = {y: median(v) for y, v in by_y.items()}
    for r in rows:
        if r.target is not None:
            r.target -= lvl[r.entry_ym]
    res = {rule: {"recall": [], "above_median": [], "n": 0} for rule in ("R0", "R1", "R2", "R3")}
    per_year = {}
    for T in TEST_YEARS:
        train = [r for r in rows if r.target is not None and int(r.entry_ym[:4]) <= T - 5]
        test = [r for r in rows if r.target is not None and int(r.entry_ym[:4]) == T]
        fit = model_mod.fit(train, FEATS, LAM)
        if fit is None:
            continue
        scored = []
        for r in test:
            p = fit.predict(r.x)
            if p is not None:
                scored.append((p, r.target, r.x.get("emd_lead_months")))
        if len(scored) < 30:
            continue
        acts = [a for _, a, _ in scored]
        top_act = set(sorted(range(len(scored)), key=lambda i: -acts[i])[: max(1, len(scored) // 10)])
        med = median(acts)
        per_year[T] = {}
        for rule in res:
            sel = pick(scored, rule)
            rec = len(sel & top_act) / len(top_act)
            ab = sum(1 for i in sel if acts[i] >= med) / len(sel)
            res[rule]["recall"].append(rec); res[rule]["above_median"].append(ab); res[rule]["n"] += 1
            per_year[T][rule] = round(rec, 3)
    out = {rule: {"recall_mean": round(sum(v["recall"]) / len(v["recall"]), 4), "above_median_mean": round(sum(v["above_median"]) / len(v["above_median"]), 4),
                  "recall_2018_21": round(sum(per_year[T][rule] for T in per_year if T >= 2018) / max(1, sum(1 for T in per_year if T >= 2018)), 4), "years": v["n"]}
           for rule, v in res.items() if v["recall"]}
    out["per_year"] = per_year
    out["note"] = "R0 기본 · R1 후행지(선행성≤−3개월) 제외 · R2 빠른추종(0~3개월, 예측 상위 40% 안) 우선 · R3 둘 다. 후보 수는 동일(상위 20%)."
    (ROOT / "reports" / "lag_filter_test.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for rule in ("R0", "R1", "R2", "R3"):
        if rule in out:
            print(rule, out[rule])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
