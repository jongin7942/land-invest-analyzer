"""predict_exit_fallback.py 에 --boost (E-boost3, §24 채택) 경로 추가."""
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "tools" / "predict_exit_fallback.py"
s = p.read_text(encoding="utf-8")
assert "--boost" not in s

s = s.replace("def main() -> int:\n    t0 = time.time()\n",
              "SEEDS = (7, 11, 13)\n\n\ndef main() -> int:\n    import argparse\n    ap = argparse.ArgumentParser(); ap.add_argument(\"--boost\", action=\"store_true\", help=\"E-boost3(결측 인지 부스팅 ×3시드, §24 채택) 로 예측\")\n    args = ap.parse_args()\n    t0 = time.time()\n", 1)

anchor = "    fits = {}\n    for name, feats in LADDER:\n"
new = '''    if args.boost:
        from apt_engine.exitprice import boost as boost_mod
        # 정직한 오차폭: 2016~2021 walk-forward 테스트 예측의 잔차 분위(학습 내 잔차는 과소)
        resid = []
        for T in range(2016, 2022):
            tr = [r for r in rows if int(r.entry_ym[:4]) <= T - 5]
            te = [r for r in rows if int(r.entry_ym[:4]) == T and r.target is not None]
            ms = [m for m in (boost_mod.fit_boost(tr, D, rounds=150, seed=sd) for sd in SEEDS) if m]
            if not ms:
                continue
            resid += [t.target - sum(m.predict(t.x) for m in ms) / len(ms) for t in te]
            print(f"  잔차 walk-forward T={T} 누적 {len(resid)} ({time.time()-t0:.0f}s)", flush=True)
        rq = {q: percentile(resid, q) for q in (0.2, 0.5, 0.8)}
        ms = [m for m in (boost_mod.fit_boost(rows, D, rounds=150, seed=sd) for sd in SEEDS) if m]
        print(f"  최종 적합 E-boost3 n={ms[0].n} · 잔차 분위 p20 {rq[0.2]:.3f} p50 {rq[0.5]:.3f} p80 {rq[0.8]:.3f} ({time.time()-t0:.0f}s)", flush=True)
        out = []
        for r in now:
            p = sum(m.predict(r.x) for m in ms) / len(ms)
            out.append({
                "complex_id": r.complex_id, "band": r.band, "name": cx[r.complex_id].name, "lawd_cd": cx[r.complex_id].lawd_cd,
                "price_now": round(r.price), "pred_log5y": round(p, 4),
                "base_factor": round(math.exp(p + base_mk + rq[0.5]), 4),
                "bear_factor": round(math.exp(p + bear_mk + rq[0.2]), 4),
                "bull_factor": round(math.exp(p + bull_mk + rq[0.8]), 4),
                "market_scenario_log": json.dumps({"bear": round(bear_mk, 4), "base": round(base_mk, 4), "bull": round(bull_mk, 4)}),
                "tier": r.x.get("tier"),
                "emd_lead_months": r.x.get("emd_lead_months"),
                "market_scenario_note": scen_note,
                "dist_center_km": round(r.x["dist_center_km"], 2) if r.x.get("dist_center_km") is not None else None,
                "model": "E-boost3|depth2|r150|relative",
                "status": "SCENARIO_WALKFORWARD_v0.8",
            })
        with (ROOT / "rules" / "exit_price_2026.csv").open("w", encoding="utf-8", newline="") as fo:
            w = csv.DictWriter(fo, fieldnames=list(out[0].keys()))
            w.writeheader(); w.writerows(out)
        print(f"[예측] E-boost3 커버리지 {len(out)}/{len(now)} (결측 행 포함, 폴백 사다리 불필요) ({time.time()-t0:.0f}s)")
        return 0

'''
assert anchor in s
s = s.replace(anchor, new + anchor, 1)
p.write_text(s, encoding="utf-8")
print("patched")
