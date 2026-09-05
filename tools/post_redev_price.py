"""정비사업 완료 후 가격(POST_REDEV_LIQUID_EXIT_PRICE) 1차 — §14.6 · §10 대체재 집합 중 '동일 생활권 신축'.

옵션 등록부의 Stage ≥ 3 · 1,000세대 이상 단지에 대해, 2km 안 최근 5년 준공 단지(84㎡ 밴드)의 최근 6개월 ㎡단가 중앙값을
'완료 후 ㎡단가'로 삼고, 현재 ㎡단가와의 배율(gross uplift)을 낸다. 분담금·기간·확률을 빼지 않은 **총 상승 배율**이므로
순증가치(NET)가 아니다 — 등록부에는 project_case_liquid_exit(㎡ 기준 배율)과 상태 PROXY 로만 적는다.
    .venv/Scripts/python.exe tools/post_redev_price.py → rules/post_redev_price.csv (+ 등록부 갱신)
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import haversine_m, median  # noqa: E402

RULES = ROOT / "rules"
NEW_SINCE = 2021
RADIUS_M = 2000
MIN_NEW = 2


def main() -> int:
    with get_conn() as conn:
        cx_all = store.load_complexes(conn, min_households=0)
        prices_all = store.load_prices(conn, cx_all, ("84",))
    def m2_now(cid):
        s = prices_all.get((cid, "84"))
        if not s:
            return None
        v = s.last_median(6)
        return v / 84.0 if v else None
    new_ids = [c.id for c in cx_all.values() if c.approval_year and c.approval_year >= NEW_SINCE and m2_now(c.id)]
    reg = list(csv.DictReader((RULES / "option_stage_registry.csv").open(encoding="utf-8", newline="")))
    out = []
    for r in reg:
        cid = int(r["complex_id"]); st = int(r["option_stage"])
        c = cx_all.get(cid)
        if c is None or st < 3 or (c.households or 0) < 1000:
            continue
        cur = m2_now(cid)
        near = [m2_now(nid) for nid in new_ids if nid != cid and haversine_m(c.lat, c.lon, cx_all[nid].lat, cx_all[nid].lon) <= RADIUS_M]
        near = [v for v in near if v]
        if cur and len(near) >= MIN_NEW:
            post = median(near)
            row = {"complex_id": cid, "name": c.name, "lawd_cd": c.lawd_cd, "option_stage": st, "households": c.households,
                   "current_m2_won": round(cur), "post_redev_m2_won": round(post), "gross_uplift": round(post / cur, 3),
                   "n_new_within_2km": len(near), "status": "PROXY(동일 생활권 신축 ㎡단가, 분담금·기간 미차감)"}
            r["project_case_liquid_exit"] = f"{post/cur:.3f}x(㎡)"
            r["option_research_status"] = (r.get("option_research_status") or "") + "+POST_PRICE_PROXY"
        else:
            row = {"complex_id": cid, "name": c.name, "lawd_cd": c.lawd_cd, "option_stage": st, "households": c.households,
                   "current_m2_won": round(cur) if cur else None, "post_redev_m2_won": None, "gross_uplift": None,
                   "n_new_within_2km": len(near), "status": "N/A(2km 안 최근 5년 신축 부족)"}
        out.append(row)
    with (RULES / "post_redev_price.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    with (RULES / "option_stage_registry.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(reg[0].keys())); w.writeheader(); w.writerows(reg)
    got = [o for o in out if o["gross_uplift"]]
    print(f"Stage≥3 · 1,000세대↑ 후보 {len(out)} · 신축 비교 가능 {len(got)}")
    for o in sorted(got, key=lambda o: -o["gross_uplift"])[:12]:
        print(f"  S{o['option_stage']} {o['name'][:14]:14s} {o['households']}세대 현재 {o['current_m2_won']/1e4:,.0f}만/㎡ → 신축 {o['post_redev_m2_won']/1e4:,.0f}만/㎡ ×{o['gross_uplift']} (신축 {o['n_new_within_2km']}개)")
    if got:
        print("배율 중앙값", median([o["gross_uplift"] for o in got]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
