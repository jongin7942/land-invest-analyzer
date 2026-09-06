"""'종인 이론'(동아1단지 사고 과정의 일반화) 백테스트 — 2026-09-06.

A. 변수 모델: v0.8(E 부스팅) vs E+종인 변수 — 5년 상대수익 Recall@20 (선택 2016~18 / 확인 2019~21, 전체행 부스팅×3)
B. 규칙 포트폴리오: 사고 과정을 그대로 규칙으로 만들어 매년 6월 진입 시 고른 단지의 5년 상대수익(연도 중앙값 차감)과
   실제 상위 10% 적중률을 전체·E 상위 20% 와 비교.
   R1 선점:   1급 역 1km 안 착공(planned_t1_c) 또는 1급 역 ≤ 0.7km
   R2 저가·접근: 시도 대비 ㎡단가 하위 절반 & 1급 역 ≤ 1km
   R3 재건축:  준공 30년↑ & 용적률<200 & 1,000세대↑ (redev_ready)
   R4 유입:    시도간 전입/재고 상위 절반
   종인 규칙 = R1 & R2 & R3 (엄격) / R1 & (R2 or R3) (완화) / E 상위 20% ∩ 종인 완화 (결합)
출력: reports/jongin_theory.json
    .venv/Scripts/python.exe tools/jongin_theory.py [--no-cache]
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
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import boost as boost_mod, jobs as jobs_mod, panel as panel_mod  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

CACHE = ROOT / "logs" / "_exit_panel_jongin.pkl"


def build(no_cache):
    if CACHE.exists() and not no_cache:
        rows = pickle.loads(CACHE.read_bytes()); et.log(f"패널 캐시 {len(rows)}행"); return rows
    bands = ("84", "59", "74")
    with get_conn() as conn:
        cx = store.load_complexes(conn); prices = store.load_prices(conn, cx, bands); store.attach_academies(cx)
        jeonse = store.load_jeonse(conn, set(prices)); stations = panel_mod.load_stations(conn); jobs = jobs_mod.Jobs(cx, conn)
        cx_all = store.load_complexes(conn, min_households=0); prices_all = store.load_prices(conn, cx_all, bands)
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs if jobs.available else None, tier_complexes=cx_all, tier_prices=prices_all)
    rows = []
    for y in et.ENTRY_YEARS:
        rows += pb.build([y]); et.log(f"패널 {y} 누적 {len(rows)}행")
    CACHE.write_bytes(pickle.dumps(rows))
    return rows


def med_of(rs, f):
    v = sorted(r.x[f] for r in rs if r.x.get(f) is not None)
    return v[len(v) // 2] if v else None


def rules(te):
    m_rel, m_in = med_of(te, "rel_sido_price"), med_of(te, "gu_interin_12m_per_1k")
    g = lambda r, f: r.x.get(f)
    R1 = lambda r: (g(r, "planned_t1_c") or 0) > 0 or (g(r, "stn_t1_km") is not None and g(r, "stn_t1_km") <= 0.7)
    R2 = lambda r: g(r, "rel_sido_price") is not None and g(r, "rel_sido_price") <= m_rel and g(r, "stn_t1_km") is not None and g(r, "stn_t1_km") <= 1.0
    R3 = lambda r: (g(r, "redev_ready") or 0) > 0
    R4 = lambda r: g(r, "gu_interin_12m_per_1k") is not None and g(r, "gu_interin_12m_per_1k") >= m_in
    return {"R1 선점": R1, "R2 저가·접근": R2, "R3 재건축": R3, "R4 유입": R4,
            "종인 엄격(R1&R2&R3)": lambda r: R1(r) and R2(r) and R3(r),
            "종인 완화(R1&(R2|R3))": lambda r: R1(r) and (R2(r) or R3(r)),
            "종인 완화+유입": lambda r: R1(r) and (R2(r) or R3(r)) and R4(r)}


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--no-cache", action="store_true"); a = ap.parse_args()
    rows = et.demean(build(a.no_cache))
    E = et.E; J = panel_mod.JONGIN; G = panel_mod.JONGIN_GROUPS
    out = {"coverage": {f: round(sum(1 for r in rows if r.x.get(f) is not None) / len(rows), 3) for f in J}}
    et.log("커버리지 " + json.dumps(out["coverage"], ensure_ascii=False))
    # B. 규칙 포트폴리오 (E 예측도 함께)
    port = {}
    boosts = {}
    for T in et.ALL_YEARS:
        tr, te = et.split(rows, T); te = [t for t in te if t.target is not None]
        ms = [m for m in (boost_mod.fit_boost(tr, E, rounds=150, seed=s) for s in (7, 11)) if m]
        boosts[T] = ms
        pred = {id(t): sum(m.predict(t.x) for m in ms) / len(ms) for t in te}
        n = len(te); top_act = set(id(t) for t in sorted(te, key=lambda t: -t.target)[: max(1, n // 10)])
        e_top = set(id(t) for t in sorted(te, key=lambda t: -pred[id(t)])[: max(1, n // 5)])
        rs = rules(te); rs["E 상위20%"] = lambda r, s=e_top: id(r) in s
        for name, f in list(rs.items()) + [("E∩종인완화", lambda r, s=e_top, f2=rs["종인 완화(R1&(R2|R3))"]: id(r) in s and f2(r))]:
            picks = [t for t in te if f(t)]
            if len(picks) < 5:
                continue
            port.setdefault(name, {})[T] = {"n": len(picks), "share": round(len(picks) / n, 3), "rel_median": round(median([t.target for t in picks]), 4),
                                            "hit_top10": round(sum(1 for t in picks if id(t) in top_act) / len(picks), 3), "recall_top10": round(sum(1 for t in picks if id(t) in top_act) / len(top_act), 3)}
        et.log(f"T={T} 규칙 포트폴리오 완료 (n={n})")
    summ = {}
    for name, yrs in port.items():
        def m(keys, k):
            v = [yrs[T][k] for T in keys if T in yrs]; return round(sum(v) / len(v), 4) if v else None
        summ[name] = {"n_avg": m(et.ALL_YEARS, "n"), "rel_median_all": m(et.ALL_YEARS, "rel_median"), "rel_median_holdout": m(et.HOLDOUT_YEARS, "rel_median"),
                      "hit_top10_all": m(et.ALL_YEARS, "hit_top10"), "hit_top10_holdout": m(et.HOLDOUT_YEARS, "hit_top10"), "years": yrs}
        et.log(f"  {name:22s} n≈{summ[name]['n_avg']} 5년 상대수익 중앙 전체 {summ[name]['rel_median_all']} 확인 {summ[name]['rel_median_holdout']} | 상위10% 적중 전체 {summ[name]['hit_top10_all']} 확인 {summ[name]['hit_top10_holdout']}")
    out["portfolio"] = summ
    # A. 변수 모델
    def boost3(feats, tag):
        res = {}
        for T in et.ALL_YEARS:
            tr, te = et.split(rows, T)
            ms = [m for m in (boost_mod.fit_boost(tr, feats, rounds=150, seed=s) for s in (7, 11, 13)) if m]
            res[T] = et.eval_pred([(sum(m.predict(t.x) for m in ms) / len(ms), t.target) for t in te])
        return et.pack(res)
    out["model"] = {}
    for name, fs in [("E", E), ("E+종인전부", E + J), ("E+밀려나는수요", E + G["밀려나는수요"]), ("E+재건축레이더", E + G["재건축레이더"])]:
        out["model"][name] = boost3(fs, name); et.log(f"boost×3 {name}: {et.fmt(out['model'][name])}")
    (ROOT / "reports" / "jongin_theory.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    et.log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
