"""안정형·공격형 = v0.8(E 부스팅) 예측 위에 게이트 — 검증 (연구로그 §26).

변수집합 탐색(model_select)에서는 어떤 조합도 선택·확인 구간을 동시에 이기지 못했다(선택 편향·잡음).
그래서 예측 모델은 하나(E 부스팅 ×2시드)로 두고, 후보 풀만 다르게 자른다:
  BASE       예측 상위
  STABLE_*   방어력 게이트: 전세가율 상위 절반 / 거래량 상위 절반 / 시군구 대비 고평가 아님(rel_gu ≤ 0)
  AGGR_*     선행성 게이트: 확산 선행(emd_lead ≥ 0) / 대장 덜 따라감(lag_catchup_gap > 0 or rel_gu_mom3 < 0) / 최근 3년 덜 오름(own_pct 하위 절반)
평가: 각 테스트연도에서 게이트를 통과한 행 가운데 예측 상위 k(=전체 n 의 20%)개를 고르고
  recall(전체 실제 상위 10% 중 포착), above_median(전체 중앙값 이상 비율), loser_share(전체 하위 20% 비율).
    .venv/Scripts/python.exe tools/model_variants_gate.py → reports/model_variants_gate.json
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import expert_theories as et  # noqa: E402
from apt_engine.exitprice import boost as boost_mod  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

rows = et.demean(pickle.loads(et.CACHE.read_bytes()))
E = et.E


def med_of(rs, f):
    v = [r.x.get(f) for r in rs if r.x.get(f) is not None]
    return median(v) if v else None


def gates(te):
    mj, mv, mo = med_of(te, "jeonse_ratio"), med_of(te, "log_vol"), med_of(te, "own_pct")
    g = {
        "BASE": lambda r: True,
        "STABLE_jr": lambda r: r.x.get("jeonse_ratio") is not None and r.x["jeonse_ratio"] >= mj,
        "STABLE_vol": lambda r: r.x.get("log_vol") is not None and r.x["log_vol"] >= mv,
        "STABLE_relgu": lambda r: r.x.get("rel_gu") is not None and r.x["rel_gu"] <= 0,
        "STABLE_jr+vol": lambda r: (r.x.get("jeonse_ratio") is not None and r.x["jeonse_ratio"] >= mj) and (r.x.get("log_vol") is not None and r.x["log_vol"] >= mv),
        "STABLE_jr+relgu": lambda r: (r.x.get("jeonse_ratio") is not None and r.x["jeonse_ratio"] >= mj) and (r.x.get("rel_gu") is not None and r.x["rel_gu"] <= 0),
        "AGGR_lead": lambda r: r.x.get("emd_lead_months") is not None and r.x["emd_lead_months"] >= 0,
        "AGGR_lag": lambda r: (r.x.get("lag_catchup_gap") or 0) > 0 or (r.x.get("rel_gu_mom3") is not None and r.x["rel_gu_mom3"] < 0),
        "AGGR_underpriced": lambda r: r.x.get("own_pct") is not None and r.x["own_pct"] <= mo,
        "AGGR_lead+lag": lambda r: (r.x.get("emd_lead_months") is not None and r.x["emd_lead_months"] >= 0) and ((r.x.get("lag_catchup_gap") or 0) > 0 or (r.x.get("rel_gu_mom3") is not None and r.x["rel_gu_mom3"] < 0)),
        "AGGR_lead+under": lambda r: (r.x.get("emd_lead_months") is not None and r.x["emd_lead_months"] >= 0) and (r.x.get("own_pct") is not None and r.x["own_pct"] <= mo),
    }
    return g


def main() -> int:
    res = {}
    for T in et.ALL_YEARS:
        tr, te = et.split(rows, T)
        te = [t for t in te if t.target is not None]
        ms = [m for m in (boost_mod.fit_boost(tr, E, rounds=150, seed=s) for s in (7, 11)) if m]
        pred = {id(t): sum(m.predict(t.x) for m in ms) / len(ms) for t in te}
        n = len(te); k = max(1, n // 5)
        act = sorted(te, key=lambda t: -t.target)
        top_act = set(id(t) for t in act[: max(1, n // 10)]); bot_act = set(id(t) for t in act[-max(1, n // 5):])
        med = median([t.target for t in te])
        for name, g in gates(te).items():
            pool = [t for t in te if g(t)]
            picks = sorted(pool, key=lambda t: -pred[id(t)])[:k]
            if len(picks) < k // 2:
                continue
            r = {"n_pool": len(pool), "picked": len(picks),
                 "recall": sum(1 for t in picks if id(t) in top_act) / len(top_act),
                 "above_median": sum(1 for t in picks if t.target >= med) / len(picks),
                 "loser_share": sum(1 for t in picks if id(t) in bot_act) / len(picks),
                 "median_rel_return": median([t.target for t in picks])}
            r["stable"] = (r["above_median"] + 1 - r["loser_share"]) / 2
            res.setdefault(name, {})[T] = r
        et.log(f"T={T} 완료 (n={n})")
    out = {}
    for name, yrs in res.items():
        def m(keys, k):
            v = [yrs[T][k] for T in keys if T in yrs]
            return round(sum(v) / len(v), 4) if v else None
        out[name] = {"select": {k: m(et.SELECT_YEARS, k) for k in ("recall", "stable", "above_median", "loser_share", "median_rel_return")},
                     "holdout": {k: m(et.HOLDOUT_YEARS, k) for k in ("recall", "stable", "above_median", "loser_share", "median_rel_return")},
                     "all": {k: m(et.ALL_YEARS, k) for k in ("recall", "stable", "above_median", "loser_share", "median_rel_return")},
                     "years": {T: {k: round(v, 3) for k, v in r.items()} for T, r in yrs.items()}}
        et.log(f"{name:18s} 공격 recall 선택 {out[name]['select']['recall']} 확인 {out[name]['holdout']['recall']} | 안정 선택 {out[name]['select']['stable']} 확인 {out[name]['holdout']['stable']} | 하위20% 비율 확인 {out[name]['holdout']['loser_share']} | 풀 {sum(yrs[T]['n_pool'] for T in yrs)//len(yrs)}")
    (ROOT / "reports" / "model_variants_gate.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    et.log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
