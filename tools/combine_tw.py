"""§14·§35 → Terminal Wealth 결합 실행 (MASTER_SPEC §13).

기존 TOP100(점수 기반) 후보 + 회귀 예시(부평 동아1단지 74㎡ 4.6억)에 대해
  Bear/Base/Bull 매도가(exit_price.build) → cashflow.scenario.band → 순이익 → EXPECTED_TW / Wealth Floor
를 계산하고 EXPECTED_TW 순 목록을 만든다.

    .venv/Scripts/python.exe tools/combine_tw.py [--cash 3] [--pool reports/top100_before_combine_2026-09-04.json]

주의: 공시가격은 자료가 없어 매매가×0.65 로 추정한다(ESTIMATED, 국민주택채권 계산과 같은 가정).
세법 규칙 32건은 원문 미확인이라 allow_unverified=True 로 돈다 → 결과는 SCENARIO 등급.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.cashflow import scenario as scenario_mod  # noqa: E402
from apt_engine.invest import budget as budget_mod, exit_price  # noqa: E402
from apt_engine.invest.budget import Profile  # noqa: E402
from apt_engine import regions  # noqa: E402

AS_OF = "2026-09-04"
OFFICIAL_RATIO = 0.65
PROBE = {"complex_id": 482, "band": "74", "price": 460_000_000, "label": "회귀 예시 · 부평 동아1단지 74㎡ 저층 4.6억"}


def one(conn, cid: int, band: str, *, profile: Profile, rel, opt, pred, price_override=None):
    cand = budget_mod.evaluate(conn, cid, profile=profile, as_of=AS_OF, area_band=band,
                               price_override=price_override, allow_unverified=True)
    if cand is None:
        return None
    price = cand.capital.purchase_price
    es = exit_price.build(price, relative=rel.get((cid, band)), option=opt.get(cid), prediction=pred.get((cid, band)))
    region = profile.region or regions.sido_of(cand.lawd_cd)
    band_res = scenario_mod.band(
        conn, capital=cand.capital, as_of=AS_OF, holding_years=5, base_sale_price=price,
        scenario_prices=es.prices, occupancy="임대", official_price=int(price * OFFICIAL_RATIO),
        interest_rate=profile.interest_rate, mortgage_term_years=profile.mortgage_term_years,
        repayment_type=profile.repayment_type, house_count=profile.current_home_count + 1,
        agent_vat_registered=profile.agent_vat_registered if hasattr(profile, "agent_vat_registered") else True,
        region=region, lawd_cd=cand.lawd_cd, allow_unverified=True)
    nps = {k: t.net_profit for k, t in band_res.results.items()}
    etw, floor = exit_price.expected_tw(nps)
    r = rel.get((cid, band))
    o = opt.get(cid)
    return {
        "complex_id": cid, "band": band, "name": cand.name if hasattr(cand, "name") else "",
        "lawd_cd": cand.lawd_cd, "price": price, "self_capital": cand.capital.required,
        "exit_bear": es.prices["Bear"], "exit_base": es.prices["Base"], "exit_bull": es.prices["Bull"],
        "relative_uplift": round(es.relative_uplift, 4), "relative_status": es.relative_status,
        "exit_model": pred[(cid, band)].model if (cid, band) in pred else "NONE(무성장)",
        "relative_label": r.label if r else "N/A", "consensus": r.consensus if r else "N/A",
        "option_stage": o.option_stage if o else None, "option_applied": es.option_applied,
        "np_bear": nps.get("Bear"), "np_base": nps.get("Base"), "np_bull": nps.get("Bull"),
        "expected_tw": etw, "wealth_floor": floor,
        "irr_base": band_res.results["Base"].irr,
        "unknown": "|".join(sorted({u for t in band_res.results.values() for u in t.unknown}))[:200],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cash", default="3")
    ap.add_argument("--pool", default=str(ROOT / "reports" / "top100_before_combine_2026-09-04.json"))
    ap.add_argument("--rate", type=float, default=0.04,
                    help="후보 비교용 표준 대출금리(HEURISTIC). 종인님 실제 조건이 아니다 — 보유 vs 갈아타기에는 쓰지 않는다")
    args = ap.parse_args()
    t0 = time.time()
    profile = replace(Profile(name="balanced"), available_cash=int(float(args.cash) * 1e8),
                      interest_rate=args.rate)
    pool = json.loads(Path(args.pool).read_text(encoding="utf-8"))["rows"]
    rel, opt, pred = exit_price.load_relative(), exit_price.load_options(), exit_price.load_predictions()
    print(f"Exit Price 예측 {len(pred)}건 적재")
    rows = []
    with get_conn() as conn:
        for it in pool:
            got = one(conn, int(it["complex_id"]), str(it["area_band"]), profile=profile, rel=rel, opt=opt, pred=pred)
            if got:
                got["name"] = it["name"]; got["score_rank"] = it["rank"]; got["score"] = it["score"]
                rows.append(got)
        probe = one(conn, PROBE["complex_id"], PROBE["band"], profile=profile, rel=rel, opt=opt, pred=pred,
                    price_override=PROBE["price"])
        if probe:
            probe["name"] = PROBE["label"]; probe["score_rank"] = None; probe["score"] = None
    ranked = sorted([r for r in rows if r["expected_tw"] is not None], key=lambda r: -r["expected_tw"])
    for i, r in enumerate(ranked, 1):
        r["tw_rank"] = i
    out_rows = ranked + [r for r in rows if r["expected_tw"] is None]
    if probe:
        out_rows.append(probe)
    (ROOT / "reports").mkdir(exist_ok=True)
    with (ROOT / "reports" / "tw_combined_2026-09-04.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader(); w.writerows(out_rows)
    summary = {
        "pool": len(pool), "computed": len(ranked), "not_computed": len(rows) - len(ranked),
        "prob_note": exit_price.PROB_NOTE,
        "top20_by_tw": [{k: r[k] for k in ("tw_rank", "score_rank", "name", "band", "price", "self_capital", "relative_uplift", "relative_status", "relative_label", "option_stage", "expected_tw", "wealth_floor", "exit_model", "exit_base")} for r in ranked[:20]],
        "model_priced": sum(1 for r in ranked if not r["exit_model"].startswith("NONE")),
        "positive_tw": sum(1 for r in ranked if (r["expected_tw"] or 0) > 0),
        "biggest_movers": sorted([{"name": r["name"], "band": r["band"], "score_rank": r["score_rank"], "tw_rank": r["tw_rank"], "move": r["score_rank"] - r["tw_rank"], "relative_uplift": r["relative_uplift"]} for r in ranked], key=lambda x: -abs(x["move"]))[:15],
        "uplift_applied": sum(1 for r in ranked if r["relative_uplift"] > 0),
        "option_applied": sum(1 for r in ranked if r["option_applied"]),
        "probe": probe,
        "unknown_common": sorted({u for r in rows for u in r["unknown"].split("|") if u})[:12],
        "seconds": round(time.time() - t0),
    }
    (ROOT / "reports" / "tw_combined_2026-09-04.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("top20_by_tw", "biggest_movers")}, ensure_ascii=False, indent=1))
    for r in summary["top20_by_tw"]:
        print(" ", r)
    print("movers:", summary["biggest_movers"][:8])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
