"""아파트 엔진 앱 v2 — 컨설팅 등급 단일 HTML (Artifact). 서버 없이 미리 계산한 값으로 동작한다.

투자금(1·2·3·4·5·7·10억) × 수도권 전역(1,000세대 이상, E 이론 예측) TW 순위 + 단지별 리포트(시나리오·가격 추이·대장 대비·
정비사업·위험·안정성) + 비교함 + 즐겨찾기 + 인쇄. 모든 컨트롤은 addEventListener 로 연결(버튼 미동작 버그 수정).
    .venv/Scripts/python.exe tools/build_app.py  → reports/apt_app.html
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.exitprice import panel as panel_mod  # noqa: E402  (규제지역 상태 reg_status)

R = ROOT / "reports"
RULES = ROOT / "rules"
CASHES = ["1", "2", "3", "4", "5", "7", "10"]
# 모델 3종 — base(v0.8 채택) / stable(안정형) / aggr(공격형). 변형은 reports/tw_all_<model>_<cash>eok.csv 가 있을 때만 앱에 실린다.
MODELS = {
    # 예측 모델은 하나(v0.8 E-부스팅). §26: 변수집합 11종×모델 3종, 게이트 11종 어느 것도 선택·확인 구간을 동시에 이기지 못했다.
    # 그래서 안정형·공격형은 '같은 예측을 어떤 결과로 줄 세우나' 의 차이다 — 새 예측이 아니라 의사결정 규칙.
    "base": {"name": "기본", "key": "tw", "desc": "기대 순이익(운 나쁨 25%·기준 50%·운 좋음 25% 가중) 순. 승자 포착률 44%(2016~21 전체행), 최근 3년 61%."},
    "stable": {"name": "안정형", "key": "fl", "desc": "'운 나쁘면 남는 돈'(Bear 시나리오·walk-forward 잔차 하위 20%) 순. 방어력(Downside Floor)을 먼저 보는 사람용 — 예측 상위 20%의 하위 20% 비율은 기본과 같은 6.8%."},
    "aggr": {"name": "공격형", "key": "nu", "desc": "'운 좋으면 남는 돈'(Bull 시나리오·잔차 상위 20%) 순. 상승폭이 큰 후보를 앞에 두되, 운 나쁠 때 손실도 같은 화면에서 확인할 것."},
}
YEARS = list(range(2007, 2027))


def read_csv(p: Path):
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fnum(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d




_REG_SHARE: dict = {}


def _regz(c) -> dict:
    """2026-09 기준 규제 상태 + 시도 규제 비중(풍선효과 후보 판정은 앱에서: 비규제 & 비중 ≥0.7 & 상승국면, §25.2)."""
    st = panel_mod.reg_status(c.lawd_cd, c.emd, "20260905")
    return {"adj": st["adj"], "hot": st["hot"], "cap": st["cap"], "from": st["adj_from"], "share": _REG_SHARE.get(c.lawd_cd[:2])}

def _model_card(e: dict) -> dict:
    """모델 성적표 — v0.8(E 변수 + 부스팅 ×3시드, §24) 가 있으면 그 walk-forward 성적(전체행·2016~2021), 없으면 ridge 백테스트."""
    fu = R / "expert_theories_followup.json"
    if fu.exists():
        try:
            b = json.loads(fu.read_text(encoding="utf-8"))["boost3/E"]["all"]
            return {"name": "E-boost v0.8", "ic": b["all"]["ic"], "recall": b["all"]["recall"], "recall_recent": b["holdout"]["recall"],
                    "above": b["all"]["above_median"], "years": "2016~2021"}
        except Exception:
            pass
    return {"name": "E", "ic": e.get("ic_mean"), "recall": e.get("recall_mean"), "recall30": e.get("recall30_mean"), "above": e.get("precision_mean"), "recall_recent": 0.52}

def main() -> int:
    with get_conn() as conn:
        region = {r["lawd_cd"]: r["name"] for r in conn.execute("SELECT lawd_cd, name FROM region")}
        cxrows = {int(r["id"]): dict(r) for r in conn.execute(
            "SELECT id, name, apt_households, approval_year, builder, heat_type, parking_count, land_area_m2, building_count FROM complex")}
        cx = store.load_complexes(conn, min_households=1000)
        for _sd in ('11', '41', '28'):
            _ids = [o for o in cx.values() if o.lawd_cd[:2] == _sd]
            _REG_SHARE[_sd] = round(sum(1 for o in _ids if panel_mod.reg_status(o.lawd_cd, o.emd, '20260905')['adj']) / len(_ids), 2) if _ids else 0.0
        store.attach_academies(cx)
        store.attach_stations(conn, cx)
        # 연도별(6월) 가격·전세 이력 + 하락기 낙폭
        hist = defaultdict(dict)
        for r in conn.execute("SELECT complex_id, area_band, as_of_ym, price_p50 FROM price_snapshot WHERE substr(as_of_ym,5,2)='06' AND price_p50 IS NOT NULL AND area_band IN ('59','74','84')"):
            if int(r["complex_id"]) in cx:
                hist[(int(r["complex_id"]), r["area_band"])][int(r["as_of_ym"][:4])] = int(r["price_p50"])
        latest = {}
        for r in conn.execute("SELECT complex_id, area_band, price_p25, price_p50, price_p75, sample_n, as_of_ym FROM price_snapshot WHERE as_of_ym IN ('202608','202609','202607','202606') AND area_band IN ('59','74','84') ORDER BY as_of_ym"):
            if int(r["complex_id"]) in cx:
                latest[(int(r["complex_id"]), r["area_band"])] = dict(r)
        jeonse = {}
        for r in conn.execute("SELECT complex_id, area_band, deposit_p50, as_of_ym FROM jeonse_snapshot WHERE as_of_ym >= '202601' AND deposit_p50 IS NOT NULL AND area_band IN ('59','74','84') ORDER BY as_of_ym"):
            if int(r["complex_id"]) in cx:
                jeonse[(int(r["complex_id"]), r["area_band"])] = int(r["deposit_p50"])
        crash = {}
        for r in conn.execute("""SELECT complex_id, area_band,
                 (SELECT MAX(price_p50) FROM price_snapshot p2 WHERE p2.complex_id=p.complex_id AND p2.area_band=p.area_band AND p2.as_of_ym BETWEEN '202107' AND '202212') AS pk,
                 (SELECT MIN(price_p50) FROM price_snapshot p3 WHERE p3.complex_id=p.complex_id AND p3.area_band=p.area_band AND p3.as_of_ym BETWEEN '202301' AND '202312') AS tr
                 FROM (SELECT DISTINCT complex_id, area_band FROM price_snapshot WHERE area_band IN ('59','74','84')) p"""):
            if int(r["complex_id"]) in cx and r["pk"] and r["tr"]:
                crash[(int(r["complex_id"]), r["area_band"])] = round(r["tr"] / r["pk"] - 1, 3)
    names = {cid: v["name"] for cid, v in cxrows.items()}
    preds = {(int(r["complex_id"]), r["band"]): r for r in read_csv(RULES / "exit_price_2026.csv")}
    rel = {(int(r["complex_id"]), r["band"]): r for r in read_csv(RULES / "relative_followers.csv")}
    pairs = defaultdict(list)
    for r in read_csv(RULES / "relative_pairs.csv"):
        pairs[(int(r["follower_id"]), r["follower_band"])].append({
            "kind": r["kind"], "leader": names.get(int(r["leader_id"]), r.get("leader_name", "")), "lband": r["leader_band"],
            "cur": fnum(r["current_ratio"]), "normal": fnum(r["normal_used"]), "gap": fnum(r["observed_gap"]),
            "p": fnum(r["transmission_p"]), "flags": r.get("structural_flags", ""), "move": r.get("leader_move", "")})
    opt = {int(r["complex_id"]): r for r in read_csv(RULES / "option_stage_registry.csv")}
    post = {int(r["complex_id"]): r for r in read_csv(RULES / "post_redev_price.csv")}
    conv = {(r["region"], int(r["from_stage"])): r for r in read_csv(RULES / "stage_conversion.csv")}
    lag_q = {}
    leads = [fnum(p.get("emd_lead_months")) for p in preds.values() if fnum(p.get("emd_lead_months")) is not None]
    if leads:
        ls = sorted(leads); lag_q = {"q16": ls[int(len(ls) * 0.16)], "q50": ls[len(ls) // 2], "q84": ls[int(len(ls) * 0.84)]}

    def diffusion(lead):
        if lead is None or not lag_q:
            return None
        if lead >= lag_q["q84"]: return "얼리어답터"
        if lead >= lag_q["q50"]: return "빠른추종"
        if lead >= lag_q["q16"]: return "대중"
        return "후행"

    profiles = {}
    data = {}
    meta = {}
    for mkey in MODELS:
        data[mkey] = {}; meta[mkey] = {}
    for c in CASHES:
        if not (R / f"tw_all_{c}eok.csv").exists():
            continue
        rows = [r for r in read_csv(R / f"tw_all_{c}eok.csv") if r.get("tw_rank")]
        if not rows:
            continue
        st = {}
        stp = R / f"tw_stability_all_{c}eok.json"
        if stp.exists():
            for s in json.loads(stp.read_text(encoding="utf-8"))["rows"]:
                st[s["name"] + s["band"]] = s
        out = []
        for r in rows:
            cid = int(r["complex_id"]); band = r["band"]; key = (cid, band)
            price = fnum(r["price"]); tw = fnum(r["expected_tw"]); fl = fnum(r["wealth_floor"]); sc = fnum(r["self_capital"], 0)
            s = st.get(r["name"] + band, {})
            grade = "D"
            if not r["exit_model"].startswith("NONE"):
                # A: 양수 · 최악이 실투자금의 −30% 안 · (TOP10 생존 ≥ 50% 또는 평균순위 ≤ 20 또는 불리할 때(P90) ≤ 60위) — 큰 풀(600+)에서도 A 가 나오도록 안정성 기준을 셋 중 하나로
                stable = (s.get("top10_survival") or 0) >= 0.5 or (s.get("mean_rank") or 1e9) <= 20 or (s.get("p90_rank") or 1e9) <= 60
                if tw is not None and tw > 0 and fl is not None and fl > -0.3 * max(sc, 1) and stable:
                    grade = "A"
                elif tw is not None and tw > 0:
                    grade = "B"
                else:
                    grade = "C"
            out.append({
                "id": cid, "b": band, "p": round(price / 1e8, 2), "sc": round(sc / 1e8, 2),
                "eb": round(fnum(r["exit_bear"], 0) / 1e8, 2), "ex": round(fnum(r["exit_base"], 0) / 1e8, 2), "eu": round(fnum(r["exit_bull"], 0) / 1e8, 2),
                "nb": round(fnum(r["np_bear"], 0) / 1e8, 2), "nx": round(fnum(r["np_base"], 0) / 1e8, 2), "nu": round(fnum(r["np_bull"], 0) / 1e8, 2),
                "tw": round(tw / 1e8, 2) if tw is not None else None, "fl": round(fl / 1e8, 2) if fl is not None else None,
                "rk": int(r["tw_rank"]), "pred": not r["exit_model"].startswith("NONE"), "g": grade,
                "mr": s.get("mean_rank"), "sv": s.get("top10_survival"), "p90": s.get("p90_rank"), "irr": round(fnum(r["irr_base"], 0) * 100, 1) if fnum(r["irr_base"]) is not None else None,
            })
        for mkey, mdef in MODELS.items():
            k = mdef["key"]
            ranked = sorted(out, key=lambda x: -((x.get(k) if x.get(k) is not None else -1e9)))
            rows_m = []
            for i2, x in enumerate(ranked, 1):
                y = dict(x); y["rk"] = i2
                if mkey == "stable":      # 안정형 등급: 운 나쁠 때도 플러스면 A, 기대 플러스면 B
                    y["g"] = "D" if not y["pred"] else ("A" if (y["fl"] or 0) > 0 else ("B" if (y["tw"] or 0) > 0 else "C"))
                elif mkey == "aggr":      # 공격형 등급: 운 좋을 때 실투자금의 30% 이상 남고 기대 플러스면 A
                    y["g"] = "D" if not y["pred"] else ("A" if ((y["nu"] or 0) >= 0.3 * max(y["sc"], 0.01) and (y["tw"] or 0) > 0) else ("B" if (y["tw"] or 0) > 0 else "C"))
                rows_m.append(y)
            data[mkey][c] = rows_m
            meta[mkey][c] = {"n": len(rows_m), "pos": sum(1 for x in rows_m if (x["tw"] or 0) > 0), "A": sum(1 for x in rows_m if x["g"] == "A")}
    # 단지×면적 공통 정보(투자금 무관)
    keys = {(x["id"], x["b"]) for md in data.values() for rows in md.values() for x in rows}
    info = {}
    for cid, band in keys:
        c = cx.get(cid); base = cxrows.get(cid, {}); p = preds.get((cid, band), {}); rl = rel.get((cid, band), {}); o = opt.get(cid, {}); po = post.get(cid, {})
        lt = latest.get((cid, band), {}); h = hist.get((cid, band), {})
        stage = int(o["option_stage"]) if o.get("option_stage") not in (None, "") else None
        region_name = region.get(base.get("lawd_cd") or (c.lawd_cd if c else ""), "")
        reg_key = "서울" if (c and c.lawd_cd.startswith("11")) else ("인천" if (c and c.lawd_cd.startswith("28")) else "경기")
        cv = conv.get((reg_key, stage)) if stage else None
        land_share = round(base["land_area_m2"] / base["apt_households"], 1) if base.get("land_area_m2") and base.get("apt_households") else None
        info[f"{cid}|{band}"] = {
            "n": base.get("name", ""), "reg": region.get(c.lawd_cd, c.lawd_cd) if c else "", "emd": c.emd if c else "", "lawd": c.lawd_cd if c else "",
            "hh": base.get("apt_households"), "yr": base.get("approval_year"), "builder": base.get("builder"), "heat": base.get("heat_type"),
            "park": base.get("parking_count"), "bld": base.get("building_count"), "ls": land_share,
            "acad": c.academies_500m if c else None, "stn": round(c.station_m) if (c and c.station_m is not None) else None,
            "tier": fnum(p.get("tier")), "dc": fnum(p.get("dist_center_km")), "relm": fnum(p.get("pred_log5y")), "model": (p.get("model") or "")[:20],
            "lead": fnum(p.get("emd_lead_months")), "diff": diffusion(fnum(p.get("emd_lead_months"))),
            "rel": rl.get("label", ""), "cons": rl.get("consensus", ""), "mis": fnum(rl.get("mispricing")), "pairs": pairs.get((cid, band), [])[:4],
            "ost": stage, "op": fnum(o.get("project_probability")), "ody": fnum(o.get("years_to_next_stage")),
            "far": fnum(o.get("existing_far")), "post": fnum(po.get("gross_uplift")),
            "hist": [h.get(y) and round(h[y] / 1e8, 2) for y in YEARS],
            "p25": round(lt["price_p25"] / 1e8, 2) if lt.get("price_p25") else None, "p75": round(lt["price_p75"] / 1e8, 2) if lt.get("price_p75") else None,
            "vol": lt.get("sample_n"), "asof": lt.get("as_of_ym"),
            "jr": round(jeonse[(cid, band)] / lt["price_p50"], 3) if (cid, band) in jeonse and lt.get("price_p50") else None,
            "jd": round(jeonse[(cid, band)] / 1e8, 2) if (cid, band) in jeonse else None,
            "crash": crash.get((cid, band)),
            "regz": _regz(c) if c else None,
        }
    scen = next(iter(preds.values()), {}).get("market_scenario_note", "")
    mt = json.loads((R / "market_timing.json").read_text(encoding="utf-8")) if (R / "market_timing.json").exists() else {}
    now = next((r for r in mt.get("rows", []) if r.get("ym") == "202606"), {})
    bt = json.loads((R / "exit_price_backtest_relative.json").read_text(encoding="utf-8")) if (R / "exit_price_backtest_relative.json").exists() else {}
    e = (bt.get("backtest") or {}).get("E_+theory2|lam=3.0") or {}
    payload = {"data": data, "meta": meta, "models": {k: {"name": v["name"], "desc": v["desc"], "key": v["key"], "cashes": sorted(data.get(k, {}).keys(), key=int)} for k, v in MODELS.items() if data.get(k)}, "info": info, "years": YEARS, "scenario": scen, "asof": "2026-09-05",
               "market": {"jr": now.get("metro_jeonse_ratio"), "rate": now.get("bok_rate"), "vol": now.get("metro_vol_ratio"), "dd": now.get("metro_dd_peak"), "mom12": now.get("metro_mom1")},
               "model": _model_card(e),
               "conv": {f"{k[0]}|{k[1]}": {"p": fnum(v["p_next_within_5y"]), "m": fnum(v["dwell_median_months"])} for k, v in conv.items()}}
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tpl = (ROOT / "tools" / "app_template.html").read_text(encoding="utf-8")
    (R / "apt_app.html").write_text(tpl.replace("__DATA__", js), encoding="utf-8")
    print(R / "apt_app.html", f"{(R / 'apt_app.html').stat().st_size/1024:.0f} KB", meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
