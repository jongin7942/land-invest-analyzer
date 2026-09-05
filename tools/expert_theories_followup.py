"""§24 후속: 상위 이론을 같은 표본으로 재비교 + 부스팅(전체행)에 넣어 확인. → reports/expert_theories_followup.json"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import expert_theories as et  # noqa: E402
from apt_engine.exitprice import boost as boost_mod, model as model_mod, panel as panel_mod  # noqa: E402

rows = et.demean(pickle.loads(et.CACHE.read_bytes()))
E = et.E; G = panel_mod.EXPERT_GROUPS
V = G["거래량선행"]; B = G["브랜드·재건축사업성"]; L = G["대장갭메우기"]
B2 = ["brand", "hh_x_age_old"]            # 결측 없는 브랜드·구축대단지만(용적률 제외)
out = {}


def same_sample(feats_a, feats_b, years):
    """두 모델을 feats_b(넓은 쪽) 완전행에서만 평가."""
    res = {}
    for T in years:
        tr, te = et.split(rows, T)
        te = et.complete(te, feats_b)
        fa = model_mod.fit(tr, feats_a, et.LAM); fb = model_mod.fit(tr, feats_b, et.LAM)
        if not fa or not fb: continue
        ra = et.eval_pred([(fa.predict(t.x), t.target) for t in te]); rb = et.eval_pred([(fb.predict(t.x), t.target) for t in te])
        if ra and rb:
            res[T] = {"n": ra["n"], "E": round(ra["recall"], 3), "E+": round(rb["recall"], 3), "icE": round(ra["ic"], 3), "icE+": round(rb["ic"], 3)}
    m = lambda k: round(sum(v[k] for v in res.values()) / max(1, len(res)), 4)
    return {"years": res, "mean_E": m("E"), "mean_E+": m("E+"), "wins": sum(1 for v in res.values() if v["E+"] > v["E"]), "n_years": len(res)}


for name, fs in [("거래량선행", V), ("브랜드·재건축사업성", B), ("브랜드·구축대단지(용적률 제외)", B2), ("거래량+브랜드", V + B), ("거래량+브랜드2", V + B2), ("대장갭+거래량", L + V)]:
    out[f"ridge_same_sample/{name}"] = same_sample(E, E + fs, et.ALL_YEARS)
    r = out[f"ridge_same_sample/{name}"]
    et.log(f"ridge 같은표본 E vs E+{name}: {r['mean_E']} → {r['mean_E+']}  ({r['wins']}/{r['n_years']} 연도 우세)")


def boost_all(feats, tag, rounds=150, seeds=(7, 11, 13)):
    """시드 3개 평균 예측(안정화) — 전체행 평가 + E 완전행 평가."""
    res_all, res_sub = {}, {}
    for T in et.ALL_YEARS:
        tr, te = et.split(rows, T)
        ms = [boost_mod.fit_boost(tr, feats, rounds=rounds, seed=s) for s in seeds]
        ms = [m for m in ms if m]
        if not ms: continue
        pred = lambda x: sum(m.predict(x) for m in ms) / len(ms)
        res_all[T] = et.eval_pred([(pred(t.x), t.target) for t in te])
        res_sub[T] = et.eval_pred([(pred(t.x), t.target) for t in et.complete(te, E)])
        et.log(f"  boost {tag} T={T} all={res_all[T]['recall']:.3f} sub={res_sub[T]['recall']:.3f}")
    return {"all": et.pack(res_all), "sub": et.pack(res_sub)}


out["ladder_all"] = et.pack(et.run_ladder(rows, et.ALL_YEARS))
et.log(f"ladder(운영) 전체행 {et.fmt(out['ladder_all'])}")
for name, fs in [("E", E), ("E+거래량", E + V), ("E+브랜드재건축", E + B), ("E+거래량+브랜드재건축", E + V + B), ("E+거래량+대장갭", E + V + L)]:
    out[f"boost3/{name}"] = boost_all(fs, name)
    a, s = out[f"boost3/{name}"]["all"], out[f"boost3/{name}"]["sub"]
    et.log(f"boost×3 {name}: 전체행 {et.fmt(a)} | E완전행 {et.fmt(s)}")

(ROOT / "reports" / "expert_theories_followup.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
et.log("done")
