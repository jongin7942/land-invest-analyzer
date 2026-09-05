"""청약홈 청약접수 경쟁률(ApplyhomeInfoCmpetRtSvc) 전량 수집 + 분양정보(HOUSE_MANAGE_NO) 조인 → 시군구·월 집계.

  경쟁률: getAPTLttotPblancCmpet — HOUSE_MANAGE_NO, HOUSE_TY, RESIDE_SECD(01 해당지역/02 기타), SUBSCRPT_RANK_CODE(1·2순위), SUPLY_HSHLDCO, REQ_CNT, CMPET_RATE
  분양정보: 기존 collectors/applyhome.fetch_all() — HSSPLY_ADRES(공급위치), RCRIT_PBLANC_DE(모집공고일), TOT_SUPLY_HSHLDCO 등
출력: rules/applyhome_competition_raw.csv, rules/applyhome_competition_sigungu_monthly.csv (1순위 해당지역, 공급세대 가중 경쟁률·접수건수·공급세대)
    .venv/Scripts/python.exe tools/collect_competition.py
"""
from __future__ import annotations

import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402  (프로젝트 루트 config.py)
from apt_engine.collectors import applyhome  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
from load_regulation_history import codes_for  # noqa: E402

_REG = None
def lawd_of(address: str):
    """'경기도 성남시 분당구 ...' → lawd_cd. 시 단위만 있으면(구 정보 없음) 그 시의 첫 코드가 아니라 4자리+'0' 형태 시 코드로."""
    global _REG
    if _REG is None:
        with get_conn() as conn:
            _REG = [(r["lawd_cd"], r["sido"], r["name"]) for r in conn.execute("SELECT lawd_cd, sido, name FROM region")]
    toks = address.replace(",", " ").split()
    if not toks:
        return None
    sd = {"서울특별시": "서울", "서울시": "서울", "서울": "서울", "경기도": "경기", "경기": "경기", "인천광역시": "인천", "인천시": "인천", "인천": "인천"}.get(toks[0])
    if not sd:
        return None
    sgg = [t for t in toks[1:4] if t.endswith(("시", "군", "구"))]
    if not sgg:
        return None
    name = " ".join(sgg[:2]) if len(sgg) >= 2 and sgg[0].endswith("시") and sgg[1].endswith("구") else sgg[0]
    codes = codes_for(_REG, sd, name)
    if len(codes) == 1:
        return codes[0]
    if codes:                      # 시 단위(구 미상) → 시 코드
        return codes[0][:4] + "0"
    return None

URL = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet"


def fetch_all_cmpet() -> list[dict]:
    out, page = [], 1
    while True:
        for a in range(3):
            try:
                r = requests.get(URL, params={"serviceKey": config.DATA_GO_KR_SERVICE_KEY, "page": page, "perPage": 1000}, timeout=90)
                j = r.json(); break
            except Exception:  # noqa: BLE001
                time.sleep(5)
        else:
            break
        data = j.get("data") or []
        out += data
        if page == 1:
            print(f"  경쟁률 총 {j.get('totalCount')}건", flush=True)
        if len(data) < 1000:
            break
        page += 1
        time.sleep(0.3)
    return out


def main() -> int:
    t0 = time.time()
    cm = fetch_all_cmpet(); print(f"  경쟁률 {len(cm)}행 ({time.time()-t0:.0f}s)", flush=True)
    det = applyhome.fetch_all(); print(f"  분양정보 {len(det)}행 ({time.time()-t0:.0f}s)", flush=True)
    info = {d["HOUSE_MANAGE_NO"]: d for d in det}
    raw_fields = ["HOUSE_MANAGE_NO", "PBLANC_NO", "HOUSE_TY", "MODEL_NO", "RESIDE_SECD", "RESIDE_SENM", "SUBSCRPT_RANK_CODE", "SUPLY_HSHLDCO", "REQ_CNT", "CMPET_RATE", "HOUSE_NM", "HSSPLY_ADRES", "RCRIT_PBLANC_DE", "SUBSCRPT_AREA_CODE_NM", "TOT_SUPLY_HSHLDCO"]
    rows = []
    for c in cm:
        d = info.get(c["HOUSE_MANAGE_NO"], {})
        rows.append({**{k: c.get(k) for k in raw_fields[:10]}, "HOUSE_NM": d.get("HOUSE_NM"), "HSSPLY_ADRES": d.get("HSSPLY_ADRES"), "RCRIT_PBLANC_DE": d.get("RCRIT_PBLANC_DE"),
                     "SUBSCRPT_AREA_CODE_NM": d.get("SUBSCRPT_AREA_CODE_NM"), "TOT_SUPLY_HSHLDCO": d.get("TOT_SUPLY_HSHLDCO")})
    with (ROOT / "rules" / "applyhome_competition_raw.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=raw_fields); w.writeheader(); w.writerows(rows)
    # 시군구·월 집계: 1순위 해당지역, 공급세대 가중
    agg = defaultdict(lambda: {"supply": 0, "req": 0, "n_types": 0})
    for r in rows:
        if str(r["SUBSCRPT_RANK_CODE"]) != "1" or r["RESIDE_SECD"] != "01" or not r["HSSPLY_ADRES"] or not r["RCRIT_PBLANC_DE"]:
            continue
        pref = lawd_of(r["HSSPLY_ADRES"])
        if not pref:
            continue
        ym = r["RCRIT_PBLANC_DE"].replace("-", "")[:6]
        try:
            sup = int(float(r["SUPLY_HSHLDCO"] or 0)); req = int(float(r["REQ_CNT"] or 0))
        except ValueError:
            continue
        a = agg[(pref, ym)]; a["supply"] += sup; a["req"] += req; a["n_types"] += 1
    with (ROOT / "rules" / "applyhome_competition_sigungu_monthly.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["lawd_cd", "ym", "supply_hh", "req_cnt", "cmpet_rate", "n_types"])
        for (pref, ym), a in sorted(agg.items()):
            w.writerow([pref, ym, a["supply"], a["req"], round(a["req"] / a["supply"], 2) if a["supply"] else "", a["n_types"]])
    print(f"완료: raw {len(rows)} · 시군구·월 {len(agg)} ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
