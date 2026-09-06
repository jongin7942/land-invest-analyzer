"""교통 목적지 등급이 '현재 가격대 형성'을 얼마나 설명하나 — 횡단면 회귀 (종인님 2026-09-06).

각 기준 시점(2012·2016·2021·2026 6월)에 단지×면적의 log ㎡단가를
  (A) E 수준 변수: tier, dist_tier1_km, log_academy, jobs_3km, age, log_hh, jeonse_ratio 없이(수준 변수만)
  (B) A + 교통 접근(stn_t1_km, stn_t2_km, access_score, planned_t1, planned_t2, t1_new5y)
로 ridge 적합(λ=1)해 R² 증가와 교통 변수 계수(표준화, log 가격 단위)를 본다.
→ "1급 노선 역이 1km 가까울 때 가격 프리미엄이 몇 %인지", "계획 노선이 이미 가격에 얼마나 들어가 있는지(선점·priced-in)" 를 연도별로 비교.
입력: logs/_exit_panel_transit.pkl (tools/transit_theories.py 가 만든 패널) + NOW 행은 직접 생성.
출력: reports/transit_price_formation.json
"""
from __future__ import annotations

import json
import math
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.exitprice import model as mm  # noqa: E402
from apt_engine.relative import store  # noqa: E402

CACHE = ROOT / "logs" / "_exit_panel_transit.pkl"
LEVEL = ["tier", "dist_tier1_km", "dist_center_km", "log_academy", "jobs_3km", "age", "log_hh"]
TRANSIT = ["stn_t1_km", "stn_t2_km", "access_score", "planned_t1_c", "planned_t2_c", "planned_t1_a", "t1_new5y", "express_t1_km"]


class R:
    __slots__ = ("complex_id", "band", "entry_ym", "price", "target", "x")


def r2(fit, rows):
    pairs = [(fit.predict(r.x), r.target) for r in rows]
    pairs = [(p, a) for p, a in pairs if p is not None]
    if len(pairs) < 20:
        return None
    m = sum(a for _, a in pairs) / len(pairs)
    ss = sum((a - m) ** 2 for _, a in pairs); se = sum((a - p) ** 2 for p, a in pairs)
    return round(1 - se / ss, 4), len(pairs)


def main() -> int:
    rows = pickle.loads(CACHE.read_bytes())
    out = {"years": {}}
    for y in ("2012", "2016", "2019", "2021"):
        sub = []
        for r in rows:
            if not r.entry_ym.startswith(y):
                continue
            o = R(); o.complex_id, o.band, o.entry_ym, o.price, o.x = r.complex_id, r.band, r.entry_ym, r.price, r.x
            o.target = math.log(r.price / store.BAND_M2[r.band])       # log ㎡단가(원)
            sub.append(o)
        fa = mm.fit(sub, LEVEL, 1.0); fb = mm.fit(sub, LEVEL + TRANSIT, 1.0)
        if not fa or not fb:
            continue
        ra, rb = r2(fa, sub), r2(fb, sub)
        # 표준화 계수 → 1SD 변화 시 log 가격 변화. stn_t1_km 는 부호 반대(가까울수록 비쌈).
        coef = {f: round(fb.beta[1 + (LEVEL + TRANSIT).index(f)], 4) for f in TRANSIT}
        sd = {f: round(fb.std[f], 3) for f in TRANSIT}
        # 1급 역 1km 접근 프리미엄(%) ≈ exp(−beta_std/sd × 1km) − 1
        prem_1km = round((math.exp(-coef["stn_t1_km"] / sd["stn_t1_km"] * 1.0) - 1) * 100, 1) if sd["stn_t1_km"] else None
        planned_prem = round((math.exp(coef["planned_t1_c"] / sd["planned_t1_c"]) - 1) * 100, 1) if sd["planned_t1_c"] else None
        planned_prem_a = round((math.exp(coef["planned_t1_a"] / sd["planned_t1_a"]) - 1) * 100, 1) if sd["planned_t1_a"] else None
        express_prem = round((math.exp(-coef["express_t1_km"] / sd["express_t1_km"]) - 1) * 100, 1) if sd["express_t1_km"] else None
        out["years"][y] = {"n": ra[1], "r2_level": ra[0], "r2_level+transit": rb[0], "r2_gain": round(rb[0] - ra[0], 4),
                           "coef_std": coef, "premium_t1_per_1km_pct": prem_1km, "planned_t1_construct_premium_pct": planned_prem, "planned_t1_announce_premium_pct": planned_prem_a, "express_t1_per_1km_pct": express_prem,
                           "share_planned_t1_c": round(sum(1 for r in sub if r.x.get("planned_t1_c")) / len(sub), 3), "share_planned_t1_a": round(sum(1 for r in sub if r.x.get("planned_t1_a")) / len(sub), 3), "share_t1_new5y": round(sum(1 for r in sub if r.x.get("t1_new5y")) / len(sub), 3)}
        print(y, out["years"][y], flush=True)
    (ROOT / "reports" / "transit_price_formation.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
