"""이중 희소성 가설 검증 — 좋은 입지 × 노후 × 신축 희소성 (종인님 2026-09-07).

사전등록(실행 전 고정):
  주가설  기존 가격 형성 모델(v0.8)에 '지역 신축 프리미엄·신축 희소성'과 '입지×노후 결합'을 더하면
          5년 뒤 상위 10% 후보를 더 잘 고른다.
  귀무가설 기존 점수를 통제하면 추가 예측력이 없다. 기각 못 하면 채택하지 않는다.
  비교모델 S0 기준(같은 표본) / S1 신축 희소성만 / S2 입지×노후만 / S3 결합(핵심) / S4 전부
  규칙     "입지 상위(급지≤3) & 35년 이상 & 그 지역 신축 프리미엄 중앙 이상" 단독 포트폴리오도 함께 본다
  판정     적중 +0.10 이상이 5년 전 연도 평균과 미사용 3년 구간에서 모두 유지될 때만 채택
    .venv/Scripts/python.exe tools/scarcity_theory.py [--horizon 60|36] [--no-cache]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from apt_engine.exitprice import panel as panel_mod  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--horizon", type=int, default=60); ap.add_argument("--no-cache", action="store_true"); ARGS = ap.parse_args()
panel_mod.HORIZON = ARGS.horizon
import expert_theories as et  # noqa: E402
import three_experiments as tx  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import jobs as jobs_mod  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

CACHE = ROOT / "logs" / f"_exit_panel_scarcity_{ARGS.horizon}.pkl"
G = panel_mod.SCARCITY_GROUPS
NEW = G["신축희소성"]; LOC = G["입지×노후"]; COMBO = G["이중희소성 결합"]


def build():
    if CACHE.exists() and not ARGS.no_cache:
        rows = pickle.loads(CACHE.read_bytes()); et.log(f"패널 캐시 {len(rows)}행"); return rows
    bands = ("84", "59", "74")
    with get_conn() as conn:
        cx = store.load_complexes(conn); prices = store.load_prices(conn, cx, bands); store.attach_academies(cx)
        jeonse = store.load_jeonse(conn, set(prices)); stations = panel_mod.load_stations(conn); jobs = jobs_mod.Jobs(cx, conn)
        cx_all = store.load_complexes(conn, min_households=0); prices_all = store.load_prices(conn, cx_all, bands)
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs if jobs.available else None, tier_complexes=cx_all, tier_prices=prices_all)
    rows = []
    for y in range(2007, 2007 + (18 if ARGS.horizon == 36 else 15)):
        rows += pb.build([y]); et.log(f"패널 {y} 누적 {len(rows)}행")
    CACHE.write_bytes(pickle.dumps(rows))
    return rows


def rule_portfolio(rows):
    """규칙 단독: 입지 상위 & 35년↑ & 지역 신축 프리미엄 중앙 이상."""
    out = {}
    for T in tx.TEST_YEARS:
        ym = f"{T}06"
        te = [r for r in rows if r.entry_ym == ym and r.target is not None and r.price <= tx.MAX_PRICE]
        if len(te) < 50:
            continue
        n = len(te)
        top = set(id(r) for r in sorted(te, key=lambda r: -r.target)[: max(1, n // 10)])
        med = median([r.target for r in te])
        for name, f in (("규칙 입지×노후", lambda r: (r.x.get("loc_old") or 0) > 0),
                        ("규칙 이중희소성", lambda r: (r.x.get("scarcity_combo") or 0) > 0),
                        ("규칙 이중희소성+저용적", lambda r: (r.x.get("scarcity_far") or 0) > 0)):
            picks = [r for r in te if f(r)]
            if len(picks) < 5:
                continue
            out.setdefault(name, {})[T] = {"n": len(picks), "share": round(len(picks) / n, 3),
                                           "rel_median": round(median([r.target for r in picks]), 4),
                                           "hit_top10": round(sum(1 for r in picks if id(r) in top) / len(picks), 3),
                                           "above_median": round(sum(1 for r in picks if r.target >= med) / len(picks), 3)}
    return out


def main() -> int:
    rows = build()
    for r in rows:
        r.raw = r.target
    rows = et.demean(rows)
    E = tx.E
    out = {"horizon": tx.H, "test_years": tx.TEST_YEARS,
           "coverage": {f: {T: round(sum(1 for r in rows if int(r.entry_ym[:4]) == T and r.x.get(f) is not None) / max(1, sum(1 for r in rows if int(r.entry_ym[:4]) == T)), 2) for T in tx.TEST_YEARS} for f in panel_mod.SCARCITY}}
    et.log("커버리지 " + json.dumps(out["coverage"], ensure_ascii=False))
    # 규칙 단독
    out["rules"] = rule_portfolio(rows)
    for name, yrs in out["rules"].items():
        m = lambda k: round(sum(v[k] for v in yrs.values()) / len(yrs), 4)
        et.log(f"  {name:22s} 연평균 {m('n'):.0f}개({m('share'):.1%}) 5년 상대수익 {m('rel_median'):+.3f} 상위10% 적중 {m('hit_top10'):.3f} 중앙값이상 {m('above_median'):.3f}")
    # 모델 비교 (같은 표본 = 결합변수가 있는 행)
    sub = COMBO
    out["S0_full"] = tx.run_model(rows, E, "S0 기준·전체표본")
    out["S0_sub"] = tx.run_model(rows, E, "S0 기준·희소성표본", sub)
    out["S1_newscarcity"] = tx.run_model(rows, E + NEW, "S1 +신축 희소성", sub)
    out["S2_locold"] = tx.run_model(rows, E + LOC, "S2 +입지×노후", sub)
    out["S3_combo"] = tx.run_model(rows, E + COMBO, "S3 +이중희소성 결합", sub)
    out["S4_all"] = tx.run_model(rows, E + panel_mod.SCARCITY, "S4 +전부", sub)
    out["summary"] = {k: tx.avg(v) for k, v in out.items() if isinstance(v, dict) and k.startswith("S")}
    (ROOT / "reports" / f"scarcity_theory_{tx.H}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    et.log("=== 요약 ===")
    for k, v in out["summary"].items():
        et.log(f"{k:18s} {v}")
    et.log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
