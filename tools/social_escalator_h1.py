"""사회적 승강장 H1 — 다음 선택 집중도 (사전등록: spec/SOCIAL_ESCALATOR_SPEC_v0.1.md).

자료: rules/kosis_od_migration_sigungu.csv (등급 B, 거주 이동 집계 OD, 2012~2025 연간, 개인 수, 매수/임차 구분 없음).
지표(모두 진입연도 T 의 T−1 자료로만 계산):
  bpc_flow, bpc_excess, eff_origins, top_origin_share, retention10  ← 예산 적합 가중은 등급 C(모형 가정)
비교: B0 기준 / B1 대조(총유입·규모거리 기대치) / B2 (+집중량) / B3 (+다양성) / B4 (+선택유지)
    .venv/Scripts/python.exe tools/social_escalator_h1.py [--horizon 60|36]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from apt_engine.exitprice import panel as panel_mod  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--horizon", type=int, default=60); ARGS = ap.parse_args()
panel_mod.HORIZON = ARGS.horizon
import expert_theories as et  # noqa: E402
import three_experiments as tx  # noqa: E402  (공통 평가 규약 재사용)
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import haversine_m, median  # noqa: E402
from apt_engine.exitprice import model as model_mod  # noqa: E402

OD = ROOT / "rules" / "kosis_od_migration_sigungu.csv"
# 우리 시군구 코드 → 이동자료(KOSIS) 코드. 경기 구 단위는 시 단위로만 제공되고, 2026 개편 인천 신설구는 옛 구로 근사(PROXY).
OD_CODE = {**{c: c[:4] + "0" for c in ("41111","41113","41115","41117","41131","41133","41135","41171","41173","41192","41194","41196",
                                       "41271","41273","41281","41285","41287","41461","41463","41465","41591","41593","41595","41597")},
           "28125": "28110", "28155": "28110", "28275": "28260", "28290": "28260"}
od_cd = lambda c: OD_CODE.get(c, c)
H1 = ["bpc_flow", "bpc_excess"]
H1_DIV = ["eff_origins", "top_origin_share"]
H1_RET = ["retention10"]
CTRL = ["od_total_in", "grav_expected"]


def load_od():
    flows = defaultdict(dict)          # year → {(o,d): movers}
    for r in csv.DictReader(OD.open(encoding="utf-8")):
        o, d = r["origin_cd"], r["dest_cd"]
        if len(o) != 5 or len(d) != 5 or o == d:      # 시도 합계·자기 내부 이동 제외
            continue
        flows[r["year"]][(o, d)] = flows[r["year"]].get((o, d), 0) + int(r["movers"])
    return flows


def build_features(rows, flows):
    with get_conn() as conn:
        cx = store.load_complexes(conn)
    # 시군구 중심점·세대 재고
    pts = defaultdict(list); stock = defaultdict(int)
    for c in cx.values():
        pts[od_cd(c.lawd_cd)].append((c.lat, c.lon)); stock[od_cd(c.lawd_cd)] += c.households or 0
    cent = {k: (sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v)) for k, v in pts.items()}
    # 연도별 시군구 ㎡단가 중앙값(진입연도 6월)
    by_ym_lawd = defaultdict(list)
    for r in rows:
        by_ym_lawd[(r.entry_ym, od_cd(cx[r.complex_id].lawd_cd))].append(r.price / store.BAND_M2[r.band])
    lawd_price = {k: median(v) for k, v in by_ym_lawd.items()}
    stats = {"years": {}, "flow_coverage": {}}
    for ym in sorted({r.entry_ym for r in rows}):
        T = int(ym[:4]); yr = str(T - 1)
        F = flows.get(yr)
        if not F:
            for r in rows:
                if r.entry_ym == ym:
                    r.x.update({k: None for k in H1 + H1_DIV + H1_RET + CTRL})
            continue
        # 기대 이동량(중력): 관측 가능한 쌍(양쪽 중심점·재고 있음)만
        pairs, X, Y = [], [], []
        outflow = defaultdict(int)
        for (o, d), v in F.items():
            outflow[o] += v
        for (o, d), v in F.items():
            if o not in cent or d not in cent or stock[d] <= 0 or outflow[o] <= 0 or v <= 0:
                continue
            dist = haversine_m(*cent[o], *cent[d]) / 1000.0
            pairs.append((o, d, v)); X.append({"lo": math.log(outflow[o]), "ls": math.log(stock[d]), "ld": math.log(dist + 1)}); Y.append(math.log(v))
        if len(pairs) < 100:
            continue
        class R2: __slots__ = ("x", "target")
        objs = []
        for xx, yy in zip(X, Y):
            o2 = R2(); o2.x = xx; o2.target = yy; objs.append(o2)
        fit = model_mod.fit(objs, ["lo", "ls", "ld"], 1.0)
        excess = {}
        for (o, d, v), xx in zip(pairs, X):
            p = fit.predict(xx) if fit else None
            if p is not None:
                excess[(o, d)] = math.log(v) - p
        in_by_dest = defaultdict(list)
        for (o, d), v in F.items():
            if o in cent and d in cent:
                in_by_dest[d].append((o, v))
        cover_num = cover_den = 0
        for r in [r for r in rows if r.entry_ym == ym]:
            d = od_cd(cx[r.complex_id].lawd_cd)
            mine = r.price / store.BAND_M2[r.band]
            src = in_by_dest.get(d, [])
            tot = sum(v for _, v in src)
            cover_den += sum(v for _, v in src)
            if not src:
                r.x.update({k: None for k in H1 + H1_DIV + H1_RET + CTRL}); continue
            def compat_sum(price):
                num, ws, exs, cnt = 0.0, [], [], 0
                for o, v in src:
                    op = lawd_price.get((ym, o))
                    if op is None:
                        continue
                    cnt += 1
                    if op >= 0.8 * price:
                        num += v; ws.append((o, v))
                        if (o, d) in excess: exs.append(excess[(o, d)])
                return num, ws, exs, cnt
            num, ws, exs, cnt = compat_sum(mine)
            num10, _, _, _ = compat_sum(mine * 1.1)
            cover_num += num
            a = [v / num for _, v in ws] if num > 0 else []
            r.x.update({
                "bpc_flow": math.log1p(num),
                "bpc_excess": (sum(exs) / len(exs)) if exs else None,
                "eff_origins": math.exp(-sum(p * math.log(p) for p in a if p > 0)) if a else None,
                "top_origin_share": max(a) if a else None,
                "retention10": (num10 / num) if num > 0 else None,
                "od_total_in": math.log1p(tot),
                "grav_expected": math.log1p(sum(math.exp(fit.predict(xx)) for (o, dd, _), xx in zip(pairs, X) if dd == d and fit.predict(xx) is not None)) if fit else None,
            })
        stats["flow_coverage"][ym] = round(cover_num / max(1, cover_den), 3)
    return stats


def main() -> int:
    rows = tx.build()
    for r in rows:
        r.raw = r.target
    rows = et.demean(rows)
    flows = load_od()
    et.log(f"OD 연도 {sorted(flows)[:3]}~{sorted(flows)[-1]} · 쌍 {sum(len(v) for v in flows.values())}")
    stats = build_features(rows, flows)
    et.log("예산적합 유입 비중: " + json.dumps(stats["flow_coverage"]))
    for f in H1 + H1_DIV + H1_RET + CTRL:
        et.log(f"커버리지 {f}: " + json.dumps({T: round(sum(1 for r in rows if int(r.entry_ym[:4]) == T and r.x.get(f) is not None) / max(1, sum(1 for r in rows if int(r.entry_ym[:4]) == T)), 2) for T in tx.TEST_YEARS}))
    E = tx.E
    out = {"horizon": tx.H, "test_years": tx.TEST_YEARS, "flow_coverage": stats["flow_coverage"], "spec": "spec/SOCIAL_ESCALATOR_SPEC_v0.1.md"}
    sub = H1
    out["B0_full"] = tx.run_model(rows, E, "B0 기준·전체표본")
    out["B0_sub"] = tx.run_model(rows, E, "B0 기준·H1표본", sub)
    out["B1_control"] = tx.run_model(rows, E + CTRL, "B1 대조(총유입·규모거리)", sub)
    out["B2_bpc"] = tx.run_model(rows, E + H1, "B2 E+선택집중량", sub)
    b0 = tx.avg(out["B0_sub"]); b2 = tx.avg(out["B2_bpc"])
    if b2.get("hit", 0) >= b0.get("hit", 0) + 0.10:
        out["B3_diversity"] = tx.run_model(rows, E + H1 + H1_DIV, "B3 +출발지 다양성", sub)
        out["B4_retention"] = tx.run_model(rows, E + H1 + H1_DIV + H1_RET, "B4 +선택유지", sub)
    else:
        out["B3_B4"] = "SKIPPED — B2 가 사전등록 기준(+0.10)을 넘지 못해 확장하지 않음(규격 §5)"
    out["summary"] = {k: tx.avg(v) for k, v in out.items() if isinstance(v, dict) and k.startswith("B")}
    (ROOT / "reports" / f"social_escalator_h1_{tx.H}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    et.log("=== 요약 ===")
    for k, v in out["summary"].items():
        et.log(f"{k:16s} {v}")
    et.log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
