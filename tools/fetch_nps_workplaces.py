"""국민연금 가입 사업장 내역(공공데이터포털 15083277) → 법정동별 일자리(가입자 수) 집계.

'직장과 가까운 곳에 살고 싶어한다' 는 가격 이론의 첫 변수를 위한 자료다. 월별 파일이 과거 이력으로
남아 있어 스냅샷 몇 개(최신·5년 전·가장 오래된 것)를 받아 법정동(10자리 코드) 단위 가입자 수와
증감을 만든다. 사업장 좌표는 쓰지 않는다 — 법정동 코드로 단지(pnu 앞 10자리)와 붙인다.

    .venv/Scripts/python.exe tools/fetch_nps_workplaces.py [--want 202608,202106,201606]

산출: rules/nps_jobs_by_emd.csv  (emd_cd10, sido_cd, sgg_cd, ym, workplaces, insured, notice_amount)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://www.data.go.kr"
PK = "15083277"
DETAIL_PK = "uddi:b5ac0771-a9e3-4ce0-9505-ad0439d97b79"
PAGE = f"{BASE}/data/{PK}/fileData.do"
DIR = ROOT / "logs" / "nps"
OUT = ROOT / "rules" / "nps_jobs_by_emd.csv"  # 실행마다 전체 재작성(캐시된 파일은 재집계)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36")
CAPITAL_SIDO = {"11", "28", "41"}


def opener():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    op.addheaders = [("User-Agent", UA), ("Referer", PAGE), ("X-Requested-With", "XMLHttpRequest")]
    op.open(PAGE, timeout=30).read()
    return op


def post(op, url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
    with op.open(req, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def history(op) -> list[tuple[str, str]]:
    """[(파일명, detailPk)] 최신 포함."""
    html = post(op, f"{BASE}/tcs/dss/selectHistAndCsvData.do", {"publicDataPk": PK, "publicDataDetailPk": DETAIL_PK})
    items = re.findall(r'data-public-pk="([^"]+)"\s+data-public-detail-sn="(\d+)">\s*([^<]+?)\s*</a>', html)
    out = [(name.strip(), pk) for pk, sn, name in items]
    out.insert(0, ("국민연금공단_국민연금 가입 사업장 내역_20260825", DETAIL_PK))
    return out


def download(op, name: str, detail_pk: str) -> Path | None:
    DIR.mkdir(parents=True, exist_ok=True)
    dest = DIR / f"{name}.bin"
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest
    j = json.loads(post(op, f"{BASE}/tcs/dss/selectFileDataDownload.do", {
        "publicDataDetailPk": detail_pk, "publicDataPk": PK, "atchFileId": "", "fileDetailSn": "1",
        "publicDataTyCode": "PR0051"}))
    if not j.get("status") or not j.get("atchFileId"):
        print(f"  건너뜀 {name}: {j.get('error') or 'atchFileId 없음'}"); return None
    chk = json.loads(post(op, f"{BASE}/cmm/cmm/check-limit.json", {"atchFileId": j["atchFileId"], "fileDetailSn": j["fileDetailSn"]}))
    if chk.get("needCaptcha"):
        print(f"  건너뜀 {name}: 캡차 요구"); return None
    url = f"{BASE}/cmm/cmm/fileDownload.do?atchFileId={j['atchFileId']}&fileDetailSn={j['fileDetailSn']}&dataNm={urllib.parse.quote(name)}"
    with op.open(url, timeout=600) as r:
        data = r.read()
    if len(data) < 100_000:
        print(f"  건너뜀 {name}: 응답이 작음 {len(data)}B {data[:60]!r}"); return None
    dest.write_bytes(data)
    print(f"  받음 {name} {len(data)/1e6:.1f}MB")
    return dest


def iter_rows(path: Path):
    data = path.read_bytes()
    blobs = []
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.lower().endswith(".csv"):
                    blobs.append(z.read(n))
    else:
        blobs.append(data)
    for b in blobs:
        for enc in ("cp949", "utf-8-sig", "utf-8"):
            try:
                text = b.decode(enc); break
            except UnicodeDecodeError:
                continue
        rd = csv.reader(io.StringIO(text))
        header = next(rd)
        yield header, rd


def find_col(header: list[str], *keys: str) -> int | None:
    for i, h in enumerate(header):
        hh = h.replace(" ", "")
        if all(k in hh for k in keys):
            return i
    return None


def aggregate(path: Path) -> dict[tuple[str, str], list]:
    agg: dict[tuple[str, str], list] = defaultdict(lambda: [0, 0, 0])
    for header, rd in iter_rows(path):
        c_ym = find_col(header, "자료생성년월") or 0
        c_sido = find_col(header, "광역시도코드")
        c_sgg = find_col(header, "시군구코드") if find_col(header, "시군구코드") != find_col(header, "읍면동코드") else None
        c_emd = find_col(header, "읍면동코드")
        c_ldong = find_col(header, "법정동주소코드")
        c_cnt = find_col(header, "가입자수")
        c_amt = find_col(header, "당월고지금액")
        c_state = find_col(header, "가입상태")
        if c_cnt is None or (c_ldong is None and c_emd is None):
            raise SystemExit(f"컬럼을 못 찾음: {header[:25]}")
        for row in rd:
            try:
                if c_state is not None and row[c_state].strip() == "2":
                    continue
                if c_ldong is not None and row[c_ldong].strip():
                    code = row[c_ldong].strip()[:10]
                else:
                    code = (row[c_sido].strip().zfill(2) + row[c_sgg].strip().zfill(3) + row[c_emd].strip().zfill(5))[:10]
                if code[:2] not in CAPITAL_SIDO:
                    continue
                ym = row[c_ym].strip()[:6]
                a = agg[(code, ym)]
                a[0] += 1
                a[1] += int(float(row[c_cnt] or 0))
                a[2] += int(float(row[c_amt] or 0)) if c_amt is not None else 0
            except (IndexError, ValueError):
                continue
    return agg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--want", default="202608,202106,201606,201806,202306")
    args = ap.parse_args()
    want = [w for w in args.want.split(",") if w]
    op = opener()
    hist = history(op)
    print(f"이력 {len(hist)}개 (가장 오래된: {hist[-1][0] if hist else '-'})")
    picked = []
    for w in want:
        # 파일명 뒤 8자리 날짜 → 그 달 파일. 없으면 가장 가까운 달
        cands = []
        for n, pk in hist:
            m = re.search(r"_(\d{6})\d{2}$", n) or re.search(r"(\d{4})년\s*(\d{1,2})월", n)
            if not m:
                continue
            ym = m.group(1) if len(m.groups()) == 1 else f"{m.group(1)}{int(m.group(2)):02d}"
            cands.append((abs(int(ym) - int(w)), n, pk))
        if cands:
            d, n, pk = min(cands)
            if d <= 2:
                picked.append((n, pk))
    seen = set()
    rows_out = []
    for n, pk in picked:
        if n in seen:
            continue
        seen.add(n)
        p = download(op, n, pk)
        if not p:
            continue
        agg = aggregate(p)
        for (code, ym), (wp, ins, amt) in agg.items():
            rows_out.append({"emd_cd10": code, "sido_cd": code[:2], "sgg_cd": code[:5], "ym": ym,
                             "workplaces": wp, "insured": ins, "notice_amount": amt, "file": n[-12:]})
        print(f"  집계 {n[-8:]}: 법정동 {len({c for (c, _) in agg})} · 가입자 {sum(v[1] for v in agg.values()):,}")
    if rows_out:
        with OUT.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys())); w.writeheader(); w.writerows(rows_out)
        print(f"→ {OUT} ({len(rows_out)}행)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
