"""거시 변수(통화량 M2·원/달러 환율·건설공사비지수·기준금리·전세가율)와 이후 5년 수도권 시장 수익의 관계 (종인님 지시 2026-09-06 "환율·통화량도 고려").

시장 수준 변수는 단지 간 순위(연도 내 상대수익)에는 기여할 수 없으므로(§24·§25.4), §6 투자시점/시장 시나리오 자리에서 검증한다.
입력: reports/market_timing.json (분기 진입 행: ym, fwd5_log, metro_jeonse_ratio, bok_rate ...), rules/kosis_m2_monthly.csv, rules/kosis_fx_krw_monthly.csv, rules/kosis_construction_cost_index.csv
출력: reports/macro_timing.json — 변수별 Spearman(전 구간 / 2011~ ), 전세가율을 통제한 뒤의 잔차 상관, 4분위별 평균 5년 수익.
    .venv/Scripts/python.exe tools/macro_timing.py
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.exitprice.model import spearman  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

R, RULES = ROOT / "reports", ROOT / "rules"


def ym_shift(ym, k):
    y, m = int(ym[:4]), int(ym[4:6]); m -= k
    while m <= 0:
        y -= 1; m += 12
    return f"{y}{m:02d}"


def load_series():
    m2 = {}
    for r in csv.DictReader((RULES / "kosis_m2_monthly.csv").open(encoding="utf-8")):
        if r["C1_NM"].startswith("M2(") and r["DT"]:
            m2[r["PRD_DE"]] = float(r["DT"])
    fx = {}
    for r in csv.DictReader((RULES / "kosis_fx_krw_monthly.csv").open(encoding="utf-8")):
        if r["ITM_NM"] == "USD당 국내 통화" and r["C2_NM"] == "기간평균" and r["DT"]:
            fx[r["PRD_DE"]] = float(r["DT"])
    cost = {}
    for r in csv.DictReader((RULES / "kosis_construction_cost_index.csv").open(encoding="utf-8")):
        if r["C1_NM"] == "주거용건물" and r["DT"]:
            cost[r["PRD_DE"]] = float(r["DT"])
    return m2, fx, cost


def yoy(s, ym):
    a, b = s.get(ym), s.get(ym_shift(ym, 12))
    return math.log(a / b) if a and b else None


def main() -> int:
    mt = json.loads((R / "market_timing.json").read_text(encoding="utf-8"))
    rows = [r for r in mt["rows"] if r.get("fwd5_log") is not None]
    m2, fx, cost = load_series()
    for r in rows:
        ym = r.get("ym") or f"{r['year']}06"
        r["ym"] = ym
        r["m2_yoy"] = yoy(m2, ym)
        r["m2_yoy_chg"] = (yoy(m2, ym) - yoy(m2, ym_shift(ym, 12))) if yoy(m2, ym) is not None and yoy(m2, ym_shift(ym, 12)) is not None else None
        r["fx_usd"] = fx.get(ym)
        r["fx_yoy"] = yoy(fx, ym)
        r["fx_3y"] = (math.log(fx[ym] / fx[ym_shift(ym, 36)]) if fx.get(ym) and fx.get(ym_shift(ym, 36)) else None)
        r["cost_yoy"] = yoy(cost, ym)
    vars_ = ["metro_jeonse_ratio", "bok_rate", "m2_yoy", "m2_yoy_chg", "fx_usd", "fx_yoy", "fx_3y", "cost_yoy", "metro_vol_ratio", "metro_dd_peak"]
    out = {"n_rows": len(rows), "period": [rows[0]["ym"], rows[-1]["ym"]], "spearman": {}, "quartiles": {}, "partial_on_jeonse": {}}
    def corr(sub, v):
        pairs = [(x[v], x["fwd5_log"]) for x in sub if x.get(v) is not None]
        return (round(spearman([a for a, _ in pairs], [b for _, b in pairs]), 3), len(pairs)) if len(pairs) >= 10 else (None, len(pairs))
    for v in vars_:
        out["spearman"][v] = {"all": corr(rows, v), "2011~": corr([x for x in rows if x["ym"] >= "201101"], v)}
        vals = sorted([x for x in rows if x.get(v) is not None], key=lambda x: x[v])
        if len(vals) >= 12:
            q = len(vals) // 4
            out["quartiles"][v] = [{"range": [round(vals[i * q][v], 3), round(vals[min(len(vals) - 1, (i + 1) * q - 1)][v], 3)],
                                    "fwd5_median": round(median([x["fwd5_log"] for x in vals[i * q:(i + 1) * q if i < 3 else len(vals)]]), 3)} for i in range(4)]
    # 전세가율 통제: fwd5 를 전세가율 순위로 1차 근사한 잔차와 변수의 상관
    base = [x for x in rows if x.get("metro_jeonse_ratio") is not None]
    if len(base) >= 12:
        xs = [x["metro_jeonse_ratio"] for x in base]; ys = [x["fwd5_log"] for x in base]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        b = sum((a - mx) * (c - my) for a, c in zip(xs, ys)) / max(1e-9, sum((a - mx) ** 2 for a in xs)); a0 = my - b * mx
        for x in base:
            x["_resid"] = x["fwd5_log"] - (a0 + b * x["metro_jeonse_ratio"])
        for v in vars_:
            if v == "metro_jeonse_ratio":
                continue
            pairs = [(x[v], x["_resid"]) for x in base if x.get(v) is not None]
            out["partial_on_jeonse"][v] = (round(spearman([p for p, _ in pairs], [q for _, q in pairs]), 3), len(pairs)) if len(pairs) >= 10 else None
    now = next((x for x in mt["rows"] if x.get("ym") == "202606" or x.get("year") == 2026), None)
    if now:
        ym = "202606"
        out["now_202606"] = {"m2_yoy": yoy(m2, ym) or yoy(m2, ym_shift(ym, 1)), "fx_usd": fx.get(ym) or fx.get(ym_shift(ym, 1)), "fx_yoy": yoy(fx, ym) or yoy(fx, ym_shift(ym, 1)), "cost_yoy": yoy(cost, ym) or yoy(cost, ym_shift(ym, 1))}
    (R / "macro_timing.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("n_rows", "period", "spearman", "partial_on_jeonse", "now_202606") if k in out}, ensure_ascii=False, indent=1))
    for v, qs in out["quartiles"].items():
        print(v, [(q["range"], q["fwd5_median"]) for q in qs])
    return 0


if __name__ == "__main__":
    sys.exit(main())
