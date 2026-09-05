"""KOSIS 오픈API 수집 (종인님 키 2026-09-06) — 건설공사비지수 · 시군구 인구이동 · 시군구 근로소득.

  397 / DT_39701_A003  건설공사비지수(2020=100), 월, 업종별(건설·건축·주거용건물…)  → rules/kosis_construction_cost_index.csv
  101 / DT_1B26001_A01 시군구별 이동자수(총전입·총전출·순이동…), 월                   → rules/kosis_migration_sigungu_monthly.csv
  133 / DT_133001N_4215 시군구별 근로소득 연말정산 신고현황(주소지), 연               → rules/kosis_income_sigungu_yearly.csv
호출은 연 단위로 나누고(셀 4만 개 제한) 3초 간격. 응답 필드: PRD_DE, C1(코드) C1_NM, ITM_ID ITM_NM, DT, UNIT_NM.
    .venv/Scripts/python.exe tools/collect_kosis.py [--only cost|move|income]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {}
for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1); ENV[k.strip()] = v.strip()
KEY = os.getenv("KOSIS_API_KEY") or ENV.get("KOSIS_API_KEY")
BASE = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
FIELDS = ["PRD_DE", "C1", "C1_NM", "C2", "C2_NM", "ITM_ID", "ITM_NM", "DT", "UNIT_NM"]


def call(org: str, tbl: str, prd_se: str, start: str, end: str, retries: int = 4) -> list[dict]:
    q = {"method": "getList", "apiKey": KEY, "itmId": "ALL", "objL1": "ALL", "objL2": "ALL", "format": "json", "jsonVD": "Y",
         "prdSe": prd_se, "startPrdDe": start, "endPrdDe": end, "orgId": org, "tblId": tbl}
    url = BASE + "?" + urllib.parse.urlencode(q)
    for a in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                body = r.read().decode("utf-8")
            j = json.loads(body)
            if isinstance(j, dict) and j.get("err"):
                # objL2 가 없는 표는 objL2=ALL 이 오류 → 빼고 재시도
                if a == 0 and "objL2" in q:
                    q.pop("objL2"); url = BASE + "?" + urllib.parse.urlencode(q); continue
                if j.get("err") in ("30", "31"):      # 자료 없음
                    return []
                raise RuntimeError(f"KOSIS err {j}")
            return j
        except Exception as e:  # noqa: BLE001
            if a == retries - 1:
                raise
            time.sleep(10 * (a + 1))
    return []


def collect(org, tbl, prd_se, years, out, log):
    rows, seen = [], set()
    for y in years:
        s, e = (f"{y}01", f"{y}12") if prd_se == "M" else (str(y), str(y))
        try:
            data = call(org, tbl, prd_se, s, e)
        except Exception as ex:  # noqa: BLE001
            print(f"  {tbl} {y} 실패: {str(ex)[:120]}", flush=True); time.sleep(5); continue
        n = 0
        for d in data:
            key = (d.get("PRD_DE"), d.get("C1"), d.get("C2"), d.get("ITM_ID"))
            if key in seen: continue
            seen.add(key); rows.append({f: d.get(f, "") for f in FIELDS}); n += 1
        print(f"  {tbl} {y}: {n}행 (누적 {len(rows)})", flush=True)
        time.sleep(3)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    print(f"[{log}] {out.name} {len(rows)}행", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--only", default=""); a = ap.parse_args()
    R = ROOT / "rules"
    if not a.only or a.only == "cost":
        collect("397", "DT_39701_A003", "M", range(2000, 2027), R / "kosis_construction_cost_index.csv", "공사비지수")
    if not a.only or a.only == "move":
        collect("101", "DT_1B26001_A01", "M", range(2006, 2027), R / "kosis_migration_sigungu_monthly.csv", "인구이동")
    if not a.only or a.only == "income":
        collect("133", "DT_133001N_4215", "Y", range(2010, 2026), R / "kosis_income_sigungu_yearly.csv", "근로소득")
    return 0


if __name__ == "__main__":
    sys.exit(main())
