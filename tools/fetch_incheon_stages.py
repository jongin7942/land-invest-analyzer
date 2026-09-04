"""인천 정비사업 148건의 단계별 날짜를 추정분담금 정보시스템에서 수집한다.

공공데이터포털의 인천 '정비사업 추진현황' CSV 와 시청의 월별 엑셀에는 현재 단계
(○ 표시)만 있고 날짜가 없다. 날짜는 renewal.incheon.go.kr 의 '정비사업 검색' 에서
사업장마다 여는 사업개요 팝업(pop_overview.do?busi_ara_id=…)의 '추진과정' 에 있다.

    정비구역지정 [2024-01-22] · 추진위승인 [0000-00-00] 해당없음 · 조합설립인가 [...]
    · 사업시행인가 [...] · 관리처분 [...] · 착공 [...] · 준공 [...]

[0000-00-00] 은 아직 안 온 단계다. 위치(대표지번)도 같은 팝업에 있어 좌표를 붙일 수
있다. 목록은 검색 페이지(search.do)를 페이지 넘겨 읽고, 행마다 붙은
openPopup_schMap('BARA_…','BABS_…') 의 첫 인자가 사업장 id 다.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://renewal.incheon.go.kr"
SEARCH = f"{BASE}/ires/program/0000-0011-0025/program/business/search.do"
POPUP = f"{BASE}/html/pop/pop_overview.do?busi_ara_id="
CACHE = ROOT / "logs" / "_incheon_stages_cache.json"
OUT = ROOT / "rules" / "incheon_redev_stages.csv"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

STAGES = ["정비구역지정", "추진위", "조합설립", "사업시행인가", "관리처분", "착공", "준공"]
# 팝업의 라벨 → 우리 단계 이름
LABELS = {"정비구역지정": "정비구역지정", "추진위승인": "추진위", "조합설립인가": "조합설립",
          "사업시행인가": "사업시행인가", "관리처분": "관리처분", "착공": "착공", "준공": "준공"}


def opener():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    op.addheaders = [("User-Agent", UA), ("Referer", SEARCH)]
    return op


def fetch(op, url, fields=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode() if fields else None)
    with op.open(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", " ", html, flags=re.S))).strip()


ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
ID_RE = re.compile(r"openPopup_schMap\('([A-Z_0-9]+)'")
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def list_projects(op) -> list[dict]:
    out, seen, page = [], set(), 1
    while page < 60:
        html = fetch(op, SEARCH, {"page": str(page)})
        got = 0
        for m in ROW_RE.finditer(html):
            row = m.group(1)
            idm = ID_RE.search(row)
            if not idm or idm.group(1) in seen:
                continue
            cells = [strip(c) for c in CELL_RE.findall(row)]
            if len(cells) < 6:
                continue
            seen.add(idm.group(1))
            out.append({"no": cells[0], "gu": cells[1], "biz_type": cells[2], "stage_now": cells[3],
                        "zone": cells[4], "jibun": cells[5], "ara_id": idm.group(1)})
            got += 1
        if not got:
            break
        page += 1
        time.sleep(0.2)
    return out


DATE_RE = re.compile(r"(정비구역지정|추진위승인|조합설립인가|사업시행인가|관리처분|착공|준공)\s*\[(\d{4})-(\d{2})-(\d{2})\]")


def parse_popup(html: str) -> dict:
    plain = strip(html)
    out = {}
    for label, y, mo, d in DATE_RE.findall(plain):
        if y == "0000":
            continue
        out[LABELS[label]] = f"{y}{mo}{d}"
    loc = re.search(r"위치\s+(.+?)\s+구역면적", plain)
    hh = re.search(r"세대수\s+([\d,]+)", plain)
    return {"stages": out, "location": loc.group(1).strip() if loc else "",
            "households": hh.group(1).replace(",", "") if hh else ""}


def main() -> int:
    op = opener()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    rows = list_projects(op)
    print(f"인천 정비사업 목록 {len(rows)}건")
    for i, r in enumerate(rows, 1):
        if r["ara_id"] in cache:
            continue
        try:
            cache[r["ara_id"]] = parse_popup(fetch(op, POPUP + r["ara_id"]))
        except Exception as e:  # noqa: BLE001
            cache[r["ara_id"]] = {"error": str(e)[:200]}
        time.sleep(0.25)
        if i % 30 == 0:
            print(f"  {i}/{len(rows)}")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    n_any = 0
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["no", "gu", "biz_type", "stage_now", "zone", "jibun", "ara_id",
                    "location", "households", *STAGES])
        for r in rows:
            c = cache.get(r["ara_id"], {})
            st = c.get("stages", {})
            n_any += bool(st)
            w.writerow([r["no"], r["gu"], r["biz_type"], r["stage_now"], r["zone"], r["jibun"],
                        r["ara_id"], c.get("location", ""), c.get("households", ""),
                        *[st.get(s, "") for s in STAGES]])
    print(f"단계 날짜가 하나라도 있는 사업장 {n_any}건 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
