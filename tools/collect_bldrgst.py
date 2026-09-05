"""건축물대장 총괄표제부(건축HUB API) → 용적률 확정 (2026-09-06, 종인님 활용신청 승인).

총괄표제부의 `vlRat`·`platArea` 는 0 으로 오지만 **`vlRatEstmTotArea`(용적률 산정용 연면적)** 는 채워져 있다.
  부평 동아1단지: 208,914.65㎡ ÷ V-World 대지 114,992㎡ = 181.7% (외부 표기 181% 와 일치)
따라서 용적률 = vlRatEstmTotArea ÷ V-World 대지면적. PNU(19자리) = 시군구5 + 법정동5 + 산1 + 본번4 + 부번4.
저장: rules/bldrgst_recap.csv, complex_attribute(vlrat_est_tot_area, far_bldrgst), complex.current_far(VERIFIED) 갱신.
    .venv/Scripts/python.exe tools/collect_bldrgst.py [--limit N] [--dry]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402

ENV = {l.split("=", 1)[0]: l.split("=", 1)[1].strip() for l in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines() if "=" in l and not l.startswith("#")}
KEY = ENV["DATA_GO_KR_SERVICE_KEY"]
URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrRecapTitleInfo"
FIELDS = ["complex_id", "pnu", "bldNm", "platArea", "archArea", "totArea", "vlRatEstmTotArea", "vlRat", "bcRat", "hhldCnt", "mainBldCnt", "useAprDay", "n_items"]
SOURCE_TIER = 1


def fetch(pnu: str) -> list[dict]:
    p = {"serviceKey": KEY, "sigunguCd": pnu[:5], "bjdongCd": pnu[5:10], "platGbCd": {"1": "0", "2": "1"}.get(pnu[10], "0"), "bun": pnu[11:15], "ji": pnu[15:19], "numOfRows": 20, "pageNo": 1, "_type": "json"}
    for a in range(3):
        try:
            r = requests.get(URL, params=p, timeout=60)
            j = r.json()
            body = j.get("response", {}).get("body", {})
            items = body.get("items") or {}
            it = items.get("item") if isinstance(items, dict) else None
            if it is None:
                return []
            return it if isinstance(it, list) else [it]
        except Exception:  # noqa: BLE001
            time.sleep(3 * (a + 1))
    return []


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0); ap.add_argument("--dry", action="store_true"); a = ap.parse_args()
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name, pnu, land_area_m2, current_far, apt_households FROM complex WHERE canonical_id IS NULL AND apt_households >= 1000 AND pnu IS NOT NULL AND length(pnu) = 19 ORDER BY id").fetchall()
    if a.limit:
        rows = rows[: a.limit]
    out = []; t0 = time.time(); hit = 0
    for i, r in enumerate(rows, 1):
        items = fetch(r["pnu"])
        # 총괄표제부가 여러 건이면 용적률 산정 연면적 합(같은 필지의 단지 분할)
        if items:
            vtot = sum(float(x.get("vlRatEstmTotArea") or 0) for x in items)
            first = max(items, key=lambda x: float(x.get("vlRatEstmTotArea") or 0))
            out.append({"complex_id": r["id"], "pnu": r["pnu"], "bldNm": first.get("bldNm"), "platArea": first.get("platArea"), "archArea": sum(float(x.get("archArea") or 0) for x in items),
                        "totArea": sum(float(x.get("totArea") or 0) for x in items), "vlRatEstmTotArea": vtot, "vlRat": first.get("vlRat"), "bcRat": first.get("bcRat"),
                        "hhldCnt": sum(int(float(x.get("hhldCnt") or 0)) for x in items), "mainBldCnt": sum(int(float(x.get("mainBldCnt") or 0)) for x in items), "useAprDay": first.get("useAprDay"), "n_items": len(items)})
            if vtot > 0:
                hit += 1
        else:
            out.append({"complex_id": r["id"], "pnu": r["pnu"], "n_items": 0})
        if i % 100 == 0 or i == len(rows):
            print(f"  {i}/{len(rows)} 총괄표제부 있음 {sum(1 for o in out if o['n_items'])} · 산정연면적>0 {hit} ({time.time()-t0:.0f}s)", flush=True)
        time.sleep(0.15)
    with (ROOT / "rules" / "bldrgst_recap.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(out)
    if a.dry:
        return 0
    land = {r["id"]: r["land_area_m2"] for r in rows}; hh = {r["id"]: r["apt_households"] or 0 for r in rows}
    n_upd = 0; ratios = []
    with get_conn() as conn:
        conn.execute("DELETE FROM complex_attribute WHERE attr_key IN ('vlrat_est_tot_area','far_bldrgst')")
        for o in out:
            v = o.get("vlRatEstmTotArea")
            plat = float(o.get("platArea") or 0)
            base = plat if plat > 0 else (land.get(o["complex_id"]) or 0)
            if not v or base <= 0:
                continue
            far = float(v) / base * 100.0
            # 검증: 대장 세대수가 K-apt 세대수의 절반 이상(다른 건물 매칭 방지), 용적률 50~700%
            if (o.get("hhldCnt") or 0) < 0.5 * hh.get(o["complex_id"], 0) or not (50 <= far <= 700):
                continue
            prev = conn.execute("SELECT current_far FROM complex WHERE id=?", (o["complex_id"],)).fetchone()[0]
            if prev:
                ratios.append(far / prev)
            conn.execute("INSERT OR REPLACE INTO complex_attribute(complex_id, attr_key, value_num, unit, as_of, source_name, source_tier, confidence, verification, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (o["complex_id"], "vlrat_est_tot_area", float(v), "㎡", "2026-09-06", "건축물대장 총괄표제부(건축HUB)", SOURCE_TIER, "HIGH", "VERIFIED", f"n_items={o['n_items']}"))
            conn.execute("INSERT OR REPLACE INTO complex_attribute(complex_id, attr_key, value_num, unit, as_of, source_name, source_tier, confidence, verification, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (o["complex_id"], "far_bldrgst", far, "%", "2026-09-06", "건축물대장 용적률산정연면적 ÷ V-World 대지", SOURCE_TIER, "HIGH", "VERIFIED", ("대지=총괄표제부 platArea" if plat > 0 else "대지=V-World 대표 필지(여러 필지 단지는 과대 가능)")))
            conn.execute("UPDATE complex SET current_far=?, land_area_source = COALESCE(land_area_source,'') || ' | FAR=건축물대장 산정연면적/대지 (VERIFIED)', updated_at=datetime('now','localtime') WHERE id=?", (far, o["complex_id"]))
            n_upd += 1
        conn.commit()
    ratios.sort()
    print(f"완료: 총괄표제부 {sum(1 for o in out if o['n_items'])}/{len(out)} · 용적률 갱신 {n_upd} · 이전(추정)/확정 비율 중앙 {ratios[len(ratios)//2]:.3f} (p10 {ratios[len(ratios)//10]:.3f} p90 {ratios[len(ratios)*9//10]:.3f})" if ratios else f"완료: 갱신 {n_upd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
