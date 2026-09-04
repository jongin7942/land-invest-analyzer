"""서울 정비사업 1,158건의 단계별 인가일을 정보몽땅에서 수집한다.

── 왜 ─────────────────────────────────────────────────────────────
서울 사업장 목록(fetch_seoul_redev.py)에는 현재 단계만 있고 날짜가 없다. 날짜는
사업장마다 있는 조합 카페의 '추진경과' 페이지에 있다. 거기엔 조합설립인가·
사업시행인가·관리처분인가·착공신고·준공인가가 날짜와 함께 시간순으로 적혀 있다.

경기도 68건으로는 재건축 단계 효과를 못 읽었다(사례 13~21건). 서울 1,158건이
붙으면 처음으로 전후 비교가 가능한 표본이 된다.

── 경로 ───────────────────────────────────────────────────────────
  1. 사업장 목록 (POST lscrMainIndx.do, 페이지마다)  → cafeOpenPopup('슬러그')
  2. 카페 첫 화면 (/cafe/mainIndx.do?cafeUrl=슬러그)   → cafeId, bsnsPk
  3. 추진경과 (/cafe/mainIndx/cleanup-prtnelapse/vscr.do?cafeId=..&bsnsPk=..)
     → 섹션(조합설립인가 …)별 날짜 목록

── 어떤 날짜를 '그 단계의 날' 로 보나 ──────────────────────────────
한 섹션에 [인가신청]·[인가]·[(변경)인가]·[인가고시] 가 여러 줄 있다.
**'인가' 또는 '승인' 이 들어간 줄 중 가장 이른 날짜**를 그 단계의 날로 본다.
신청일은 아직 확정이 아니고, 변경인가는 이미 확정된 뒤의 일이다.
착공은 [착공신고], 준공은 [인가] 줄을 쓴다.

수집은 예의를 지킨다 — 요청 사이 0.3초, 결과는 JSON 캐시에 쌓아 중단 후 이어서
돌릴 수 있다.
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
BASE = "https://cleanup.seoul.go.kr"
LIST = f"{BASE}/cleanup/bsnssttus/lscrMainIndx.do"
CACHE = ROOT / "logs" / "_seoul_stages_cache.json"
OUT = ROOT / "rules" / "seoul_redev_stages.csv"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

FORM = [
    *[("bsnsSeCodeList", c) for c in ("100", "101", "102", "103", "104", "105", "106", "107")],
    *[("bsnsEfctMthdList", c) for c in ("1", "2", "3", "4")],
    *[("operSeCodeList", c) for c in ("100", "101", "102", "103")],
    *[("cafeSttusCodeList", c) for c in ("100", "110")],
    ("scupBsnsSttus.bsnsProgrsSttusCode", ""), ("scupBsnsSttus.asscNm", ""),
    ("scupBsnsSttus.signguCode", ""), ("scupBsnsSttus.legaldongCode", ""),
    ("sortColumn", ""), ("orderValue", ""),
]

# 추진경과 섹션 이름 → 우리 단계 이름. 목록의 '사업단계' 와 같은 어휘를 쓴다.
SECTIONS = {
    "안전진단": "안전진단",
    "정비구역지정": "정비구역지정",
    "조합설립추진위원회승인": "추진위",
    "조합설립인가": "조합설립",
    "사업시행인가": "사업시행인가",
    "관리처분인가": "관리처분",
    "착공신고": "착공",
    "준공인가": "준공",
}
# 그 단계가 '확정' 된 줄로 인정하는 표시. 신청·고시·변경은 뺀다.
CONFIRM = {
    "안전진단": ("안전진단",),
    "정비구역지정": ("지정", "고시"),
    "추진위": ("승인",),
    "조합설립": ("인가",),
    "사업시행인가": ("인가",),
    "관리처분": ("인가",),
    "착공": ("착공신고", "착공"),
    "준공": ("인가",),
}
EXCLUDE = ("신청", "변경", "고시")     # '[인가고시]' 는 인가 뒤의 고시라 뺀다


def opener():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    op.addheaders = [("User-Agent", UA), ("Referer", LIST)]
    return op


def get(op, url: str) -> str:
    with op.open(url, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def post(op, url: str, fields) -> str:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with op.open(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


ROW_RE = re.compile(
    r"<tr[^>]*>(?P<row>.*?)</tr>", re.S)
SLUG_RE = re.compile(r"cafeOpenPopup\('([^']+)'\)")
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def strip(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


PAGE_SIZE = 10


def list_rows(op) -> list[dict]:
    """전체 목록. 페이저 링크가 cpage=N&pageSize=10 을 쓰므로 그 인자로 넘긴다."""
    rows: list[dict] = []
    seen: set[str] = set()
    page = 1
    while True:
        html = post(op, LIST, FORM + [("cpage", str(page)), ("pageSize", str(PAGE_SIZE))])
        got = [r for r in parse_rows(html) if r["no"] not in seen]
        if not got:
            break
        rows.extend(got)
        seen.update(r["no"] for r in got)
        # 서버는 시작 위치를 (cpage-1)*10 으로 잡으면서도 cpage 상한은 pageSize 기준으로
        # 검사한다(pageSize=100 이면 cpage 21 부터 빈 응답). 10행씩 끝까지 넘기는 게 안전하다.
        page += 1
        time.sleep(0.2)
    if not rows:
        raise SystemExit("목록을 못 읽었습니다 — 폼 값이 바뀐 것 같습니다.")
    return rows


def parse_rows(html: str) -> list[dict]:
    out = []
    for m in ROW_RE.finditer(html):
        row = m.group("row")
        slug = SLUG_RE.search(row)
        if not slug:
            continue
        cells = [strip(c) for c in CELL_RE.findall(row)]
        if len(cells) < 6:
            continue
        out.append({"no": cells[0], "gu": cells[1], "biz_type": cells[2],
                    "project": cells[3], "jibun": cells[4], "stage": cells[5],
                    "slug": slug.group(1)})
    return out


# 카페 첫 화면의 링크는 HTML 이라 & 가 &amp; 로 적혀 있다. 둘 다 받는다.
CAFE_ID_RE = re.compile(r"cafeId=([A-Za-z0-9]+)(?:&amp;|&)bsnsPk=([0-9\-]+)")
SECTION_RE = re.compile(r"<h[2-5][^>]*>\s*([가-힣 ()]+?)\s*</h[2-5]>", re.S)
DATE_RE = re.compile(r"(\d{4})-\s*(\d{2})-\s*(\d{2})\s*\[([^\]]+)\]")


# 추진경과 페이지의 섹션 순서. 제목이 <span> 안의 아코디언 라벨이라 태그로는 못
# 자르고, 텍스트에서 이 순서대로 라벨 위치를 찾아 구간을 나눈다.
ORDER = ["기본계획수립", "안전진단", "정비구역지정", "조합설립추진위원회승인",
         "정비사업전문관리업자선정", "설계자선정", "조합설립인가", "사업시행인가",
         "시공자선정", "철거업자선정", "관리처분인가", "이주", "철거신고", "착공신고",
         "일반분양승인", "준공인가", "이전고시", "조합해산", "조합청산"]


def plain_text(html: str) -> str:
    return strip(re.sub(r"<script.*?</script>", " ", html, flags=re.S))


def stage_dates(html_or_plain: str) -> dict[str, str]:
    """추진경과 → {단계: YYYYMMDD, 단계+'_approx': YYYYMMDD}.

    같은 단계 안에 [인가]·[(변경)인가]·[인가신청]·[인가고시] 가 섞여 있다.
    최초 [인가] 가 있으면 그 날, 없고 [(변경)인가] 만 있으면(오래된 사업은
    최초 인가가 시스템 이전이라 그렇다) 가장 이른 변경인가일을 `_approx` 로 둔다 —
    실제 인가일보다 늦은 상한이므로 전후 비교에서는 따로 표시한다.
    """
    plain = html_or_plain if "<" not in html_or_plain[:200] else plain_text(html_or_plain)
    # 라벨 위치. 라벨은 '2020- 01- 01 [' 같은 날짜 앞에 홀로 나온다.
    marks: list[tuple[int, str]] = []
    cursor = 0
    for name in ORDER:
        i = plain.find(name, cursor)
        if i >= 0:
            marks.append((i, name))
            cursor = i + len(name)
    out: dict[str, str] = {}
    for idx, (pos, name) in enumerate(marks):
        stage = SECTIONS.get(name)
        if not stage:
            continue
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(plain)
        chunk = plain[pos:end]
        # 정보몽땅은 최초 인가도 '(변경)인가' 로 표시한다(200개 카페의 조합설립인가
        # 구간에서 '(변경)인가' 1,248건, 그냥 '인가' 0건). 그래서 '변경' 을 가르지
        # 않고, 신청·고시를 뺀 확정 줄 중 가장 이른 날을 그 단계의 날로 본다.
        dates = []
        for y, mo, d, tag in DATE_RE.findall(chunk):
            tag_ = tag.replace(" ", "")
            if "신청" in tag_ or "고시" in tag_:
                continue
            if any(x in tag_ for x in CONFIRM[stage]):
                dates.append(f"{y}{mo}{d}")
        if dates:
            out[stage] = min(dates)
    return out


def main() -> int:
    op = opener()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    CACHE.parent.mkdir(parents=True, exist_ok=True)

    print("사업장 목록 읽는 중...")
    rows = list_rows(op)
    print(f"  {len(rows):,}건")

    done = 0
    for i, r in enumerate(rows, 1):
        slug = r["slug"]
        if slug in cache and "stages" in cache[slug]:
            continue
        try:
            main_html = get(op, f"{BASE}/cafe/mainIndx.do?cafeUrl={urllib.parse.quote(slug)}")
            m = CAFE_ID_RE.search(main_html)
            if not m:
                cache[slug] = {"error": "cafeId 없음"}
            else:
                cafe_id, bsns_pk = m.group(1), m.group(2)
                prog = get(op, f"{BASE}/cafe/mainIndx/cleanup-prtnelapse/vscr.do"
                               f"?cafeId={cafe_id}&bsnsPk={bsns_pk}")
                plain = plain_text(prog)
                # 본문 텍스트를 캐시에 남긴다 — 파서를 고쳐도 다시 긁지 않게.
                cache[slug] = {"cafe_id": cafe_id, "bsns_pk": bsns_pk,
                               "plain": plain, "stages": stage_dates(plain)}
                done += 1
        except Exception as e:  # noqa: BLE001 — 한 건 실패로 전체를 멈추지 않는다
            cache[slug] = {"error": str(e)[:200]}
        time.sleep(0.3)
        if i % 25 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(rows)} · 이번에 수집 {done}")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    # 최초 인가일과, 그것이 없을 때의 변경인가일(_approx)을 나란히 적는다.
    stages = [s for base in SECTIONS.values() for s in (base, base + "_approx")]
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["no", "gu", "biz_type", "project", "jibun", "stage_now", "slug",
                    "cafe_id", "bsns_pk", *stages])
        n_any = 0
        for r in rows:
            c = cache.get(r["slug"], {})
            st = c.get("stages", {})
            if st:
                n_any += 1
            w.writerow([r["no"], r["gu"], r["biz_type"], r["project"], r["jibun"],
                        r["stage"], r["slug"], c.get("cafe_id", ""), c.get("bsns_pk", ""),
                        *[st.get(s, "") for s in stages]])
    print(f"\n단계 날짜가 하나라도 있는 사업장 {n_any:,}건 → {OUT}")
    errs = sum(1 for v in cache.values() if "error" in v)
    if errs:
        print(f"  실패 {errs}건 (캐시에 사유 있음, 다시 돌리면 재시도)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
