"""Relative Price Gap Engine 전체 실행 (MASTER_SPEC §35).

    .venv/Scripts/python.exe tools/run_relative_gap.py [--bands 84,59,74] [--limit N]

산출:
  rules/relative_zones.csv           법정동 → 생활권·급지
  rules/zone_leaders.csv             생활권×면적 대장 1~3
  rules/relative_pairs.csv           Follower–Leader Pair 전부
  rules/relative_followers.csv       Follower 집계(합의·Mispricing·라벨)
  rules/RELATIVE_LAG_TOP50.csv, rules/FALSE_CHEAP_TOP50.csv
  rules/relative_backtest_episodes.csv  대장 상승 → 후행 추종 에피소드(§35.17·18)
  reports/relative_gap_report.json   §35.27 보고 항목
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.relative import gap as gap_mod, store, zones as zones_mod  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

RULES = ROOT / "rules"
AS_OF = "2026-09-04"


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bands", default="84,59,74")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    bands = tuple(args.bands.split(","))
    t0 = time.time()

    with get_conn() as conn:
        complexes = store.load_complexes(conn)
        prices = store.load_prices(conn, complexes, bands)
        n_acad = store.attach_academies(complexes)
        n_st = store.attach_stations(conn, complexes)
        print(f"[적재] 단지 {len(complexes)} · 단지×면적 {len(prices)} · 학원 {n_acad} · 역 {n_st} ({time.time()-t0:.0f}s)")

        units = zones_mod.build_units(complexes, prices)
        breaks = zones_mod.assign_tiers(units)
        zones = zones_mod.assign_zones(units)
        leaders: list[zones_mod.Leader] = []
        for b in bands:
            leaders += zones_mod.pick_leaders(units, zones, prices, b)
        zones_mod.save(conn, units, zones, leaders, as_of=AS_OF)
        print(f"[급지·생활권] 법정동 {len(units)} · 생활권 {len(zones)} · 대장 {len(leaders)} ({time.time()-t0:.0f}s)")

        regimes = gap_mod.region_regimes(complexes, prices)
        book = gap_mod.LeaderBook(units, zones, leaders, complexes, prices)
        pair_rows, follower_rows, episodes = [], [], []
        followers: list[gap_mod.FollowerResult] = []
        keys = [k for k in prices if prices[k].months_with_price() >= gap_mod.MIN_PAIR_MONTHS]
        if args.limit:
            keys = keys[:args.limit]
        for i, (cid, band) in enumerate(keys):
            pairs = []
            for kind, lid, lband in book.leader_set(cid, band):
                pr = gap_mod.compute_pair(cid, band, lid, lband, kind, prices=prices, complexes=complexes, regimes=regimes)
                if pr:
                    pairs.append(pr)
                    # 백테스트 에피소드 원장
                    comp, _ = gap_mod.episodes_of(prices[(lid, lband)], prices[(cid, band)],
                                                  [ (prices[(cid, band)].p50[t] / prices[(lid, lband)].p50[t])
                                                    if prices[(cid, band)].p50[t] and prices[(lid, lband)].p50[t] else None
                                                    for t in range(store.N_MONTHS)])
                    for e in comp:
                        episodes.append({"follower_id": cid, "band": band, "leader_id": lid, "leader_band": lband,
                                         "kind": kind, **e, "flags": "|".join(pr.structural_flags),
                                         "tier_gap": (book.zone_tier.get(book.zone_of(cid)) or 0) - (book.zone_tier.get(book.zone_of(lid)) or 0)})
            z = book.zone_of(cid)
            fr = gap_mod.aggregate(cid, band, pairs, zone=z, tier=book.zone_tier.get(z) if z else None,
                                   price_now=prices[(cid, band)].last_median())
            followers.append(fr)
            for p in pairs:
                d = {k: v for k, v in p.__dict__.items()}
                d["structural_flags"] = "|".join(p.structural_flags)
                d["follower_start"] = "|".join(p.follower_start)
                d["follower_name"] = complexes[cid].name
                d["leader_name"] = complexes[p.leader_id].name
                pair_rows.append(d)
            if (i + 1) % 2000 == 0:
                print(f"  … {i+1}/{len(keys)} ({time.time()-t0:.0f}s)")

    # ── 출력 ──
    for fr in followers:
        c = complexes[fr.complex_id]
        follower_rows.append({
            "complex_id": fr.complex_id, "name": c.name, "lawd_cd": c.lawd_cd, "emd": c.emd, "band": fr.band,
            "zone": fr.zone, "tier": fr.tier, "price_now": fr.price_now,
            "n_leaders": len(fr.pairs), "consensus": fr.consensus, "consensus_gap": fr.consensus_gap,
            "mispricing": fr.mispricing, "mispricing_status": fr.mispricing_status,
            "label": fr.label, "reason": fr.reason,
            "leaders": "|".join(f"{p.kind}:{p.leader_id}:{p.leader_band}" for p in fr.pairs),
        })
    write_csv(RULES / "relative_zones.csv", [
        {"emd_key": u.key, "lawd_cd": u.lawd_cd, "emd": u.emd, "life_zone": u.zone, "tier": u.tier,
         "price_level_log_m2": u.level, "n_level": u.n_level, "n_complex": len(u.complex_ids)} for u in units.values()])
    write_csv(RULES / "zone_leaders.csv", [
        {"life_zone": l.zone, "band": l.band, "rank": l.rank, "complex_id": l.complex_id,
         "name": complexes[l.complex_id].name, "composite": l.composite, **{k: v for k, v in l.parts.items()}}
        for l in leaders])
    write_csv(RULES / "relative_pairs.csv", pair_rows)
    write_csv(RULES / "relative_followers.csv", follower_rows)
    lag = sorted([r for r in follower_rows if r["label"] == "LAG_CANDIDATE"], key=lambda r: -(r["mispricing"] or 0))[:50]
    false_cheap = sorted([r for r in follower_rows if r["label"] == "FALSE_CHEAP"], key=lambda r: -(r["consensus_gap"] or 0))[:50]
    write_csv(RULES / "RELATIVE_LAG_TOP50.csv", lag)
    write_csv(RULES / "FALSE_CHEAP_TOP50.csv", false_cheap)
    write_csv(RULES / "relative_backtest_episodes.csv", episodes)

    # ── §35.27 보고 ──
    by_h = {}
    for lab in ("gap_12m", "gap_36m", "gap_60m"):
        vals = [e[lab] for e in episodes if e[lab] is not None]
        by_h[lab] = {"n": len(vals), "median_gap_change": median(vals)}
    succ = [e for e in episodes if e["catchup"] >= gap_mod.CATCHUP_SUCCESS]
    fail = [e for e in episodes if e["catchup"] < gap_mod.CATCHUP_SUCCESS]
    flag_fail = Counter(f for e in fail for f in e["flags"].split("|") if f)
    flag_succ = Counter(f for e in succ for f in e["flags"].split("|") if f)
    failure_causes = {f: {"실패 중 비율": round(flag_fail[f] / len(fail), 3) if fail else None,
                          "성공 중 비율": round(flag_succ[f] / len(succ), 3) if succ else None}
                      for f in set(flag_fail) | set(flag_succ)}
    tier_fail = defaultdict(lambda: [0, 0])
    for e in episodes:
        tier_fail[e["tier_gap"]][0 if e["catchup"] >= gap_mod.CATCHUP_SUCCESS else 1] += 1
    widest = sorted([r for r in pair_rows if r["observed_gap"] is not None], key=lambda r: -r["observed_gap"])[:50]
    top_recoverable = sorted([r for r in follower_rows if r["mispricing"] is not None], key=lambda r: -r["mispricing"])[:50]
    hist_ratio = [r["current_ratio"] for r in pair_rows if r["current_ratio"]]
    donga = [r for r in follower_rows if r["complex_id"] == 482]
    donga_pairs = [r for r in pair_rows if r["follower_id"] == 482]
    # 진입가 민감도(Pair Test 예시, §35.22): 밴드 중앙값이 아니라 실제 진입가로 현재비율을 다시 본다.
    # 순위·점수에는 쓰지 않는다 — 표시 전용.
    probe = {"complex_id": 482, "band": "74", "entry_price": 460_000_000}
    probe_rows = []
    for r in donga_pairs:
        if r["follower_band"] != probe["band"] or not r["current_ratio"] or not r["normal_used"]:
            continue
        band_med = prices[(482, "74")].last_median()
        ratio_entry = r["current_ratio"] * probe["entry_price"] / band_med if band_med else None
        probe_rows.append({"kind": r["kind"], "leader_name": r["leader_name"], "normal_used": r["normal_used"],
                           "ratio_band_median": r["current_ratio"], "ratio_at_entry": ratio_entry,
                           "gap_at_entry": (r["normal_used"] - ratio_entry) / r["normal_used"] if ratio_entry else None,
                           "transmission_p": r["transmission_p"], "structural_flags": r["structural_flags"]})
    report = {
        "as_of": AS_OF, "bands": bands,
        "1_급지_분류방식": zones_mod.METHOD_NOTE + f" · Jenks 경계(log ㎡단가) {[round(b, 3) for b in breaks]}",
        "2_생활권_수": len(zones), "법정동_수": len(units),
        "급지별_법정동수": dict(sorted(Counter(u.tier for u in units.values() if u.tier).items())),
        "3_생활권_대장": len(leaders),
        "4_Pair_수": len(pair_rows),
        "5_60개월이상_Pair_수": sum(1 for r in pair_rows if r["months"] >= 60),
        "6_Relative_Ratio_역사분포(현재비율)": {q: percentile_safe(hist_ratio, q) for q in (0.10, 0.25, 0.5, 0.75, 0.9)},
        "7_현재_가장_벌어진_Pair_50": [{k: r[k] for k in ("follower_name", "follower_band", "leader_name", "kind", "current_ratio", "normal_used", "observed_gap", "structural_flags")} for r in widest],
        "8_구조적_Gap_제거후_저평가_50": [{k: r[k] for k in ("name", "band", "zone", "tier", "consensus", "mispricing", "mispricing_status", "label")} for r in top_recoverable],
        "9_과거_전달_성공률": {"에피소드": len(episodes), "성공": len(succ), "성공률": round(len(succ) / len(episodes), 3) if episodes else None,
                            "Gap_축소_추이": by_h, "Leader_Transmission_Failure_Rate": round(len(fail) / len(episodes), 3) if episodes else None,
                            "급지차별(성공,실패)": {str(k): v for k, v in sorted(tier_fail.items())}},
        "10_실패사례_주요원인(플래그 비중)": failure_causes,
        "11_RELATIVE_LAG_TOP50": [{k: r[k] for k in ("name", "band", "zone", "tier", "price_now", "consensus", "mispricing", "mispricing_status", "reason")} for r in lag],
        "12_FALSE_CHEAP_TOP50": [{k: r[k] for k in ("name", "band", "zone", "tier", "consensus_gap", "reason")} for r in false_cheap],
        "14_부평동아_Leader_Set": [{k: r[k] for k in ("follower_band", "kind", "leader_name", "leader_band", "months")} for r in donga_pairs],
        "15_부평동아_비율": [{k: r[k] for k in ("follower_band", "kind", "hist_median", "regime_now", "regime_normal", "normal_used", "current_ratio", "observed_gap", "structural_gap", "recoverable_gap", "structural_flags", "episodes", "transmission_p", "transmission_status", "leader_move", "follower_start", "mispricing", "mispricing_status")} for r in donga_pairs],
        "부평동아_집계": donga,
        "15b_부평동아_진입가_4.6억_민감도(표시전용)": probe_rows,
        "라벨_분포": dict(Counter(r["label"] for r in follower_rows)),
        "합의_분포": dict(Counter(r["consensus"] for r in follower_rows)),
        "전달확률_상태": dict(Counter(r["transmission_status"] for r in pair_rows)),
        "소요시간_초": round(time.time() - t0),
    }
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "relative_gap_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if not isinstance(v, list)}, ensure_ascii=False, indent=1))
    return 0


def percentile_safe(vals, q):
    from apt_engine.relative.store import percentile
    return round(percentile(vals, q), 4) if vals else None


if __name__ == "__main__":
    raise SystemExit(main())
