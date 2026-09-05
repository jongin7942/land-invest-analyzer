"""변수 조합 탐색 — 승자 포착률(Recall@20)을 올리는 조합 찾기 (MASTER_SPEC §12 · 종인님 요청 2026-09-05).

선택 편향을 막기 위해 두 구간으로 나눈다:
  선택 구간  테스트연도 2013~2017 (진입 ≤ 2012 학습) — 여기서 조합을 고른다
  확인 구간  테스트연도 2018~2021 — 고른 조합을 한 번만 평가한다(여기 성적이 진짜)
탐색: ① C(이론 21변수)에서 전진 선택(한 번에 1개 추가, 최대 6회) ② E(33변수)에서 후진 제거 ③ 무작위 부분집합 60개.
목표: Recall@20 평균 (동률이면 IC). 결과: reports/feature_search.json
    .venv/Scripts/python.exe tools/feature_search.py
"""
from __future__ import annotations

import json
import pickle
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apt_engine.exitprice import model as model_mod, panel as panel_mod  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

SELECT_YEARS = list(range(2013, 2018))
HOLDOUT_YEARS = list(range(2018, 2022))
LAM = 3.0
ALL = sorted({f for fs in panel_mod.FEATURE_SETS.values() for f in fs})


def score(rows, feats, years):
    res = model_mod.walk_forward(rows, feats, years, [LAM])[LAM]
    recs = [v["winner_recall"] for v in res.values() if v.get("winner_recall") is not None]
    ics = [v["ic"] for v in res.values() if v.get("ic") is not None]
    precs = [v["precision_above_median"] for v in res.values() if v.get("precision_above_median") is not None]
    if not recs:
        return None
    return {"recall": round(sum(recs) / len(recs), 4), "ic": round(sum(ics) / len(ics), 4) if ics else None,
            "above_median": round(sum(precs) / len(precs), 4) if precs else None, "n_years": len(recs)}


def main() -> int:
    t0 = time.time()
    rows = pickle.loads((ROOT / "logs" / "_exit_panel_84_59_74.pkl").read_bytes())
    by_y: dict = {}
    for r in rows:
        if r.target is not None:
            by_y.setdefault(r.entry_ym, []).append(r.target)
    lvl = {y: median(v) for y, v in by_y.items()}
    for r in rows:
        if r.target is not None:
            r.target -= lvl[r.entry_ym]
    print(f"[패널] {len(rows)}행 · 후보 변수 {len(ALL)} ({time.time()-t0:.0f}s)", flush=True)

    log: list[dict] = []
    def ev(name, feats):
        s = score(rows, feats, SELECT_YEARS)
        if s:
            log.append({"name": name, "features": list(feats), **s})
        return s

    base_sets = {k: v for k, v in panel_mod.FEATURE_SETS.items() if k in ("C_+theory", "E_+theory2", "E2_+relmom", "E3_+redev", "F_+cycle")}
    for k, v in base_sets.items():
        s = ev(k, v); print(f"  기준 {k:12s} 선택구간 Recall {s['recall']} IC {s['ic']}", flush=True)

    # ① 전진 선택 (C 에서 시작)
    cur = list(panel_mod.FEATURE_SETS["C_+theory"])
    best = ev("C_+theory", cur)["recall"]
    for step in range(6):
        pool = [f for f in ALL if f not in cur]
        cand = []
        for f in pool:
            s = ev(f"fwd{step}+{f}", cur + [f])
            if s:
                cand.append((s["recall"], s["ic"] or 0, f))
        cand.sort(reverse=True)
        if not cand or cand[0][0] <= best + 0.003:
            print(f"  전진 {step}: 개선 없음 (최고 {cand[0][2] if cand else '-'} {cand[0][0] if cand else '-'})", flush=True); break
        best = cand[0][0]; cur.append(cand[0][2])
        print(f"  전진 {step}: +{cand[0][2]} → Recall {best} ({time.time()-t0:.0f}s)", flush=True)
    forward = list(cur)

    # ② 후진 제거 (E 에서 시작)
    cur = list(panel_mod.FEATURE_SETS["E_+theory2"])
    best = ev("E_+theory2", cur)["recall"]
    for step in range(6):
        cand = []
        for f in cur:
            s = ev(f"bwd{step}-{f}", [x for x in cur if x != f])
            if s:
                cand.append((s["recall"], s["ic"] or 0, f))
        cand.sort(reverse=True)
        if not cand or cand[0][0] <= best + 0.003:
            print(f"  후진 {step}: 개선 없음", flush=True); break
        best = cand[0][0]; cur.remove(cand[0][2])
        print(f"  후진 {step}: −{cand[0][2]} → Recall {best} ({time.time()-t0:.0f}s)", flush=True)
    backward = list(cur)

    # ③ 무작위 부분집합
    rng = random.Random(7)
    for i in range(60):
        k = rng.randint(12, 30)
        feats = rng.sample(ALL, k)
        ev(f"rand{i}", feats)
    print(f"  무작위 60개 완료 ({time.time()-t0:.0f}s)", flush=True)

    # 선택구간 상위 6개 → 확인구간 평가
    top = sorted(log, key=lambda x: (-x["recall"], -(x["ic"] or 0)))[:6]
    confirm = []
    for x in top:
        h = score(rows, x["features"], HOLDOUT_YEARS)
        confirm.append({**x, "holdout": h})
        print(f"  확인 {x['name'][:28]:28s} 선택 {x['recall']} → 확인구간 Recall {h['recall'] if h else None} IC {h['ic'] if h else None}", flush=True)
    # 기준(E) 의 확인구간
    ref = score(rows, panel_mod.FEATURE_SETS["E_+theory2"], HOLDOUT_YEARS)
    refc = score(rows, panel_mod.FEATURE_SETS["C_+theory"], HOLDOUT_YEARS)
    out = {"select_years": SELECT_YEARS, "holdout_years": HOLDOUT_YEARS, "lam": LAM, "n_eval": len(log),
           "forward_result": forward, "backward_result": backward,
           "reference_holdout": {"E_+theory2": ref, "C_+theory": refc},
           "top_confirmed": confirm, "all": sorted(log, key=lambda x: -x["recall"])[:40], "seconds": round(time.time() - t0)}
    (ROOT / "reports" / "feature_search.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("기준 E 확인구간:", ref, "· C:", refc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
