"""경기도 일반 정비사업 추진현황을 내려받는다 (경기데이터드림).

── 왜 이 자료인가 ──────────────────────────────────────────────────
"재건축이 진행되면 값이 오른다" 는 다들 당연하게 말하지만, 우리는 오늘
교통에서 정확히 그런 통념이 측정으로 부정되는 것을 봤다(개통 사례 117건,
중앙값 +0.15%). 그래서 재건축 단계도 **재보기 전에는 믿지 않는다.**

재려면 '언제 어느 단계를 밟았나' 가 단지별로 필요하다. 이 자료에 그게 있다.

    정비구역지정 → 추진위 → 안전진단 → 조합설립 → 사업시행인가
    → 관리처분인가 → 착공 → 일반분양 → 준공

각 단계의 **인가일자**가 칸으로 들어 있고, 소재지 주소가 있어 좌표를 붙일 수
있다. 기존/신축 용적률, 기존 세대수, 조합원수도 함께 온다.

── 왜 API 가 아니라 이 경로인가 ────────────────────────────────────
openapi.gg.go.kr/GenrlimprvBizpropls 가 같은 자료를 주지만 인증키가 필요하고
sample 키는 거부된다. 포털의 시트 다운로드는 키 없이 열려 있어서 그쪽을 쓴다.
다만 CSRF 토큰과 세션 쿠키가 필요하므로, 페이지를 한 번 열어 둘 다 받은 뒤
같은 세션으로 내려받는다.

인증키를 받게 되면 API 로 바꾸는 편이 낫다 — 이 경로는 포털 개편에 약하다.
"""
from __future__ import annotations

import re
import sys
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

INF_ID = "S62GFEEN7JMLMA0PH6CF19108891"
BASE = "https://data.gg.go.kr"
PAGE = f"{BASE}/portal/data/service/selectServicePage.do?infId={INF_ID}&infSeq=1"
SAVE_PURPOSE = f"{BASE}/portal/data/sheet/saveInfUsePurp.do"
DOWNLOAD = f"{BASE}/portal/data/sheet/downloadSheetData.do"

OUT = Path(__file__).resolve().parents[1] / "rules" / "gg_redev_progress.csv"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


def opener() -> urllib.request.OpenerDirector:
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))
    op.addheaders = [("User-Agent", UA), ("Referer", PAGE)]
    return op


def csrf_of(html: str) -> str | None:
    """페이지에 심긴 CSRF 토큰. 이름이 두 가지라 둘 다 본다."""
    for pat in (r'name=["\'](?:_csrf|CSRFToken)["\']\s+value=["\']([0-9a-f-]{20,})',
                r'value=["\']([0-9a-f-]{20,})["\']\s+name=["\'](?:_csrf|CSRFToken)',
                r'["\'](?:_csrf|CSRFToken)["\']\s*[:=]\s*["\']([0-9a-f-]{20,})'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def fetch() -> bytes:
    op = opener()
    with op.open(PAGE, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    token = csrf_of(html)
    if not token:
        raise SystemExit("CSRF 토큰을 못 찾았습니다 — 포털 구조가 바뀐 것 같습니다.\n"
                         "  브라우저로 페이지를 열어 확인하거나, 인증키를 받아\n"
                         "  openapi.gg.go.kr/GenrlimprvBizpropls 로 바꾸세요.")

    # 포털이 활용목적을 요구한다. U03 = 연구·조사.
    body = urllib.parse.urlencode(
        {"infId": INF_ID, "dsUsePurpsCd": "U03", "dsUsePurps": ""}).encode()
    op.open(urllib.request.Request(
        SAVE_PURPOSE, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"}),
        timeout=30).read()

    q = urllib.parse.urlencode({
        "infId": INF_ID, "infSeq": "1", "SIGUN_NM": "", "BIZ_TYPE_NM": "",
        "loc": "", "rows": "100", "downloadType": "C",
        "CSRFToken": token, "_csrf": token})
    with op.open(f"{DOWNLOAD}?{q}", timeout=120) as r:
        return r.read()


def main() -> int:
    raw = fetch()
    # 포털은 MS949(EUC-KR 확장)로 내려준다. 저장은 UTF-8 로 통일한다.
    text = raw.decode("cp949", "replace")
    if "시군명" not in text.split("\n", 1)[0]:
        print("받은 내용이 CSV 가 아닙니다:", text[:300], file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    rows = [l for l in text.splitlines() if l.strip()]
    print(f"정비사업 {len(rows) - 1:,}건 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
