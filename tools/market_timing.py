"""투자 시점(사이클) 분석 — 수도권 전체가 5년 뒤 얼마나 올랐나를 진입 시점의 사이클 변수로 설명한다 (§12 시장 수준).

매년 6월 진입(2007~2021) 15개 점 + 최신(2026-06). 표본이 15개라 모델이 아니라 **표와 단순 규칙**으로 본다.
  변수: 고점 대비 낙폭(metro_dd_peak) · 5년 이동평균 대비(metro_vs_ma5) · 전세가율 중앙값 · 거래량 비율(12m/이전 36m 평균)
        · 기준금리·1년 변화 · 1년/3년 모멘텀
  결과: 이후 5년 수도권 log 수익 중앙값(패널의 진입연도별 시장 중앙값과 동일 정의)
    .venv/Scripts/python.exe tools/market_timing.py  → reports/market_timing.json, spec 로그용 표 출력
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import panel as panel_mod  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

YEARS = list(range(2007, 2027))
QUARTERS = ("03", "06", "09", "12")   # 분기 진입으로 표본 15 → ~60 (겹치는 5년 창이라 독립 표본은 아님)


def main() -> int:
    t0 = time.time()
    with get_conn() as conn:
        cx = store.load_complexes(conn)
        prices = store.load_prices(conn, cx, ("84", "59", "74"))
        jeonse = store.load_jeonse(conn, set(prices))
        stations = panel_mod.load_stations(conn)
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations)
    rows = []
    for y in YEARS:
      for q in QUARTERS:
        ym = f"{y}{q}"
        t = panel_mod.ym_idx(ym)
        if t < 0 or t >= store.N_MONTHS or ym > "202606":
            continue
        cyc = pb.cycle_feats(t, y)
        # 이후 5년 시장 수익 = 단지×면적별 log(P+5/P0) 의 중앙값
        fwd = []
        t1 = t + panel_mod.HORIZON
        if t1 < store.N_MONTHS:
            for k, s in prices.items():
                p0 = panel_mod.smooth_price(s, t); p1 = panel_mod.smooth_price(s, t1)
                if p0 and p1:
                    fwd.append(math.log(p1 / p0))
        rows.append({"year": y, "ym": ym, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in cyc.items()},
                     "fwd5_log": round(median(fwd), 4) if len(fwd) >= 100 else None, "n": len(fwd)})
    # 단순 규칙 평가: 각 변수 상위/하위 절반의 이후 5년 수익 중앙값
    hist = [r for r in rows if r["fwd5_log"] is not None]
    rules = {}
    for k in ("metro_dd_peak", "metro_vs_ma5", "metro_jeonse_ratio", "metro_vol_ratio", "bok_rate", "bok_rate_chg1", "metro_mom1", "metro_mom3"):
        vals = [r for r in hist if r.get(k) is not None]
        if len(vals) < 6:
            continue
        m = median([r[k] for r in vals])
        hi = [r["fwd5_log"] for r in vals if r[k] >= m]; lo = [r["fwd5_log"] for r in vals if r[k] < m]
        # 순위상관
        n = len(vals)
        rk = lambda xs: {i: s for s, i in enumerate(sorted(range(n), key=lambda i: xs[i]))}
        a = rk([r[k] for r in vals]); b = rk([r["fwd5_log"] for r in vals])
        d2 = sum((a[i] - b[i]) ** 2 for i in range(n)); rho = 1 - 6 * d2 / (n * (n * n - 1))
        rules[k] = {"n": n, "split": round(m, 4), "fwd_when_high": round(median(hi), 3), "fwd_when_low": round(median(lo), 3), "spearman": round(rho, 3)}
    out = {"rows": rows, "rules": rules, "seconds": round(time.time() - t0)}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "market_timing.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("시점 | 고점대비 | MA5대비 | 전세가율 | 거래량비 | 금리 | 금리Δ | 1y | 3y | → 5년 수익(log)")
    for r in rows:
        print(f"{r['ym']} | {r.get('metro_dd_peak')} | {r.get('metro_vs_ma5')} | {r.get('metro_jeonse_ratio')} | {r.get('metro_vol_ratio')} | {r.get('bok_rate')} | {r.get('bok_rate_chg1')} | {r.get('metro_mom1')} | {r.get('metro_mom3')} | {r['fwd5_log']}")
    print(json.dumps(rules, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
