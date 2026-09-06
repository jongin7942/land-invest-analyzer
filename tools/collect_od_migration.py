"""시군구 출발지→도착지 이동자수 수집 (KOSIS 101/DT_1B26003_A02) — 사회적 승강장 가설 H1 자료.

도착지(수도권 시군구)마다 한 번 호출해 전 연도·전 출발지를 받는다. 성별은 '계'(objL3=0).
등급: B AGGREGATE_RESIDENTIAL_OD_PROXY — 거주 이동(전입신고) 집계이며 매수/임차·가구/개인 구분 없음.
공개시점: 연간 통계는 다음해 초 공표 → 진입연도 T 는 T−1 자료까지만 쓴다(사용하는 쪽에서 강제).
출력: rules/kosis_od_migration_sigungu.csv (dest_cd, dest_nm, origin_cd, origin_nm, year, movers)
    .venv/Scripts/python.exe tools/collect_od_migration.py
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402

ENV = {l.split("=", 1)[0]: l.split("=", 1)[1].strip() for l in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines() if "=" in l and not l.startswith("#")}
KEY = ENV["KOSIS_API_KEY"]
B = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
OUT = ROOT / "rules" / "kosis_od_migration_sigungu.csv"


def fetch(dest: str, retries: int = 3):
    p = {"method": "getList", "apiKey": KEY, "itmId": "T70", "format": "json", "jsonVD": "Y", "orgId": "101", "tblId": "DT_1B26003_A02",
         "objL1": "ALL", "objL2": dest, "objL3": "0", "prdSe": "Y", "startPrdDe": "2006", "endPrdDe": "2025"}
    for a in range(retries):
        try:
            j = requests.get(B, params=p, timeout=300).json()
            if isinstance(j, list):
                return j
            if isinstance(j, dict) and j.get("err") in ("30", "31"):
                return []
        except Exception:  # noqa: BLE001
            pass
        time.sleep(10 * (a + 1))
    return None


def main() -> int:
    with get_conn() as conn:
        dests = [(r["lawd_cd"], r["name"]) for r in conn.execute("SELECT lawd_cd, name FROM region ORDER BY lawd_cd")]
    dests += [("41110","수원시"),("41130","성남시"),("41170","안양시"),("41270","안산시"),("41280","고양시"),("41460","용인시")]
    rows, miss, t0 = [], [], time.time()
    for i, (cd, nm) in enumerate(dests, 1):
        j = fetch(cd)
        if not j:
            miss.append((cd, nm)); continue
        for d in j:
            if d.get("C1") in (None, "") or not d.get("DT"):
                continue
            rows.append({"dest_cd": cd, "dest_nm": nm, "origin_cd": d["C1"], "origin_nm": d["C1_NM"], "year": d["PRD_DE"], "movers": int(float(d["DT"]))})
        print(f"  {i}/{len(dests)} {nm} 누적 {len(rows)}행 ({time.time()-t0:.0f}s)", flush=True)
        time.sleep(1)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["dest_cd", "dest_nm", "origin_cd", "origin_nm", "year", "movers"]); w.writeheader(); w.writerows(rows)
    yrs = sorted({r["year"] for r in rows})
    print(f"완료 {len(rows)}행 · 도착지 {len(dests)-len(miss)}/{len(dests)} · 연도 {yrs[0]}~{yrs[-1]} · 실패 {miss}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
