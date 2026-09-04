"""서울 정비사업 사업장 목록을 내려받는다 (정비사업 정보몽땅).

── 왜 서울이 필요한가 ──────────────────────────────────────────────
경기도 자료로 재건축 단계 효과를 재봤는데, 단계별 사례가 13~21건뿐이라
결론을 낼 수 없었다. 실제로 면적대 고르는 방식 하나만 바꿨는데 정비구역지정이
+4.43% 에서 -4.17% 로 뒤집혔다. 그 정도로 얇다.

서울에는 사업장이 1,158개 있고(경기 533개), 우리 가격 데이터도 서울이 가장
촘촘하다. 표본을 늘리지 않으면 이 측정은 어느 방향으로도 못 읽는다.

── 무엇을 받는가, 무엇이 부족한가 ──────────────────────────────────
이 목록에는 자치구·사업구분·사업장명·**대표지번**·진행단계가 있다. 대표지번이
있어 좌표를 붙일 수 있고, 진행단계로 지금 어디까지 왔는지 알 수 있다.

**다만 단계별 날짜가 없다.** 경기도 자료에는 조합설립인가일·사업시행인가일이
칸으로 있었는데 여기는 '현재 단계' 만 있다. 날짜는 사업장 상세 페이지에
들어가야 나온다. 그래서 이 파일만으로는 전후 비교를 못 하고, 상세 페이지를
따로 긁어야 한다 — 다음 단계 작업이다.

날짜 없이도 쓸 데가 있다. 지금 단계가 어디인지는 알 수 있으므로,
'단계가 앞선 단지가 시군구 대비 더 비싼가' 라는 **수준 비교**는 할 수 있다.
그건 인과가 아니라 상관이지만(비싼 동네가 사업이 잘 굴러가기도 한다),
전후 비교와 방향이 어긋나면 그 자체가 신호다.

── 접근 방법 ────────────────────────────────────────────────────────
검색 폼을 그대로 POST 하면 엑셀(.xls)을 준다. 인증키가 필요 없다.
세션 쿠키가 필요하므로 검색 페이지를 먼저 열고 같은 세션으로 받는다.
"""
from __future__ import annotations

import re
import sys
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = "https://cleanup.seoul.go.kr"
SEARCH = f"{BASE}/cleanup/bsnssttus/lscrMainIndx.do"
EXCEL = f"{BASE}/cleanup/bsnssttus/lsubBsnsSttusExcel.do"

OUT = Path(__file__).resolve().parents[1] / "logs" / "_seoul_redev_list.xls"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

# 검색 폼의 기본 선택값 그대로. 사업구분 8종 · 시행방식 4종 · 운영단계 4종 ·
# 운영구분 2종을 전부 켠 상태가 '전체' 다.
FORM = [
    *[("bsnsSeCodeList", c) for c in
      ("100", "101", "102", "103", "104", "105", "106", "107")],
    *[("bsnsEfctMthdList", c) for c in ("1", "2", "3", "4")],
    *[("operSeCodeList", c) for c in ("100", "101", "102", "103")],
    *[("cafeSttusCodeList", c) for c in ("100", "110")],
    ("scupBsnsSttus.bsnsProgrsSttusCode", ""),
    ("scupBsnsSttus.asscNm", ""),
    ("scupBsnsSttus.signguCode", ""),
    ("scupBsnsSttus.legaldongCode", ""),
    ("sortColumn", ""),
    ("orderValue", ""),
]


def main() -> int:
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))
    op.addheaders = [("User-Agent", UA), ("Referer", SEARCH)]

    with op.open(SEARCH, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")

    fields = list(FORM)
    # CSRF 토큰이 있으면 같이 보낸다. 지금은 없지만 생겨도 깨지지 않게.
    m = re.search(r'name=["\'](_csrf|CSRFToken)["\']\s+value=["\']([^"\']+)', html)
    if m:
        fields.append((m.group(1), m.group(2)))

    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        EXCEL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with op.open(req, timeout=180) as r:
        raw = r.read()

    if len(raw) < 10_000:
        print("받은 파일이 너무 작습니다 — 폼 값이 바뀐 것 같습니다.",
              file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(raw)
    print(f"서울 사업장 목록 {len(raw):,} bytes → {OUT}")
    print("  ※ 이 파일에는 단계별 날짜가 없습니다. 날짜는 사업장 상세에 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
