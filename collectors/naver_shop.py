"""네이버 부동산 상가 매물 수집기 (개원 입지 모듈).

⚠ 비공식 API. 네이버 부동산 웹/모바일 페이지가 내부적으로 호출하는 엔드포인트를
그대로 쓴다(아파트 매물 수집 프로그램들이 쓰는 것과 같은 방식). 공식 개방 API가
아니므로 응답 필드명이 예고 없이 바뀔 수 있다. 그래서:
  - 원본 응답을 raw_json 에 통째로 보관한다(나중에 재파싱 가능).
  - 필드 추출은 후보 키를 여러 개 두고 있는 것을 쓴다(_pick).
  - `python shop_pipeline.py --probe --cortar <동코드>` 로 원본을 눈으로 확인할 수 있다.
  - 호출 간격(NAVER_LAND_SLEEP)을 지켜 차단을 피한다. 개인용·저빈도 사용 전제.

엔드포인트 두 종류(config.NAVER_LAND_ENDPOINT):
  mobile(기본): m.land.naver.com/cluster/ajax/articleList — 토큰 없이 응답하는 경우가 많음
  new         : new.land.naver.com/api/articles         — Bearer 토큰(NAVER_LAND_AUTH) 필요할 수 있음

매물 종류 코드(rletTpCd / realEstateType): SG 상가, SMS 사무실, GM 건물, SGJT 상가주택,
  GJCG 공장·창고, TJ 토지, APTHGJ 지식산업센터
거래 종류 코드(tradTpCd / tradeType): A1 매매, B1 전세, B2 월세
"""
from __future__ import annotations

import json
import re
import time

import requests

import config

MOBILE_REGION_URL = "https://m.land.naver.com/map/getRegionList"
MOBILE_ARTICLE_URL = "https://m.land.naver.com/cluster/ajax/articleList"
NEW_REGION_URL = "https://new.land.naver.com/api/regions/list"
NEW_ARTICLE_URL = "https://new.land.naver.com/api/articles"

# 수도권 시도 코드(법정동코드 앞 2자리 → 네이버 cortarNo 10자리)
CAPITAL_SIDO = {
    "1100000000": "서울특별시",
    "4100000000": "경기도",
    "2800000000": "인천광역시",
}

RE_TYPES = {"SG": "상가", "SMS": "사무실", "GM": "건물", "SGJT": "상가주택",
            "GJCG": "공장/창고", "TJ": "토지", "APTHGJ": "지식산업센터"}
TRADE_TYPES = {"A1": "매매", "B1": "전세", "B2": "월세"}

HEADERS_MOBILE = {
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
    "Referer": "https://m.land.naver.com/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}
HEADERS_NEW = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://new.land.naver.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


class NaverLandError(RuntimeError):
    pass


# ------------------------------------------------------------- 공통 유틸 ----

def _pick(d: dict, *keys, default=None):
    """후보 키 중 처음으로 값이 있는 것을 반환(대소문자 무시)."""
    if not isinstance(d, dict):
        return default
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v not in (None, ""):
            return v
    return default


def parse_korean_money(v) -> int | None:
    """'1억 5,000' / '5,000' / 15000 / '3억' → 만원 정수. 못 읽으면 None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", "").replace(" ", "")
    total = 0
    m = re.search(r"(\d+(?:\.\d+)?)억", s)
    if m:
        total += int(float(m.group(1)) * 10000)
        s = s[m.end():]
    m = re.search(r"(\d+(?:\.\d+)?)(만)?", s)
    if m and m.group(1):
        total += int(float(m.group(1)))
    return total if total or re.search(r"\d", str(v)) else None


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def parse_floor(info) -> tuple[int | None, int | None]:
    """'2/5' → (2,5), 'B1/5' → (-1,5), '고/15' → (None,15), '1' → (1,None)."""
    if info in (None, ""):
        return None, None
    s = str(info).strip()
    parts = s.split("/")
    cur = parts[0].strip()
    tot = parts[1].strip() if len(parts) > 1 else ""

    def _one(x):
        if not x:
            return None
        x = x.upper().replace("층", "")
        if x.startswith("B") and x[1:].isdigit():
            return -int(x[1:])
        if x.lstrip("-").isdigit():
            return int(x)
        return None  # 고/중/저 등

    return _one(cur), _one(tot)


def floor_band(floor: int | None, raw_info=None) -> str:
    if floor is None:
        s = str(raw_info or "")
        if s.startswith(("고", "중")):
            return "3층이상"
        if s.startswith("저"):
            return "2층"
        return "미상"
    if floor <= 0:
        return "지하"
    if floor == 1:
        return "1층"
    if floor == 2:
        return "2층"
    return "3층이상"


def _norm_confirm_ymd(v) -> str | None:
    """'25.09.20.' / '20250920' / '2025-09-20' → '20250920'."""
    if not v:
        return None
    digits = re.sub(r"\D", "", str(v))
    if len(digits) == 6:  # YYMMDD
        return "20" + digits
    if len(digits) == 8:
        return digits
    return None


def _session(endpoint: str) -> requests.Session:
    s = requests.Session()
    if endpoint == "new":
        s.headers.update(HEADERS_NEW)
        if config.NAVER_LAND_AUTH:
            tok = config.NAVER_LAND_AUTH
            s.headers["Authorization"] = tok if tok.lower().startswith("bearer ") else f"Bearer {tok}"
    else:
        s.headers.update(HEADERS_MOBILE)
    return s


def _get_json(sess: requests.Session, url: str, params: dict, timeout: int = 20, retries: int = 3):
    last = None
    for i in range(retries):
        try:
            r = sess.get(url, params=params, timeout=timeout)
            if r.status_code in (401, 403):
                raise NaverLandError(
                    f"HTTP {r.status_code} — 접근 거부. 엔드포인트를 바꾸거나(NAVER_LAND_ENDPOINT) "
                    f"브라우저 토큰(NAVER_LAND_AUTH)을 넣어보세요. url={r.url}")
            if r.status_code == 429:
                time.sleep(5 * (i + 1)); continue
            r.raise_for_status()
            try:
                return r.json()
            except ValueError as e:
                raise NaverLandError(f"JSON 아님(차단/리다이렉트 가능성): {r.text[:200]}") from e
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            time.sleep(2 ** i)
    raise NaverLandError(f"네이버 호출 실패: {last}")


# ------------------------------------------------------------- 지역 트리 ----

def _normalize_region(item: dict, parent_no: str | None, level: int) -> dict | None:
    no = _pick(item, "cortarNo", "CortarNo", "cortar_no")
    if not no:
        return None
    return {
        "cortar_no": str(no),
        "name": _pick(item, "cortarNm", "CortarNm", "cortarName", "name"),
        "parent_no": parent_no,
        "level": level,
        "center_lat": _to_float(_pick(item, "centerLat", "CenterLat", "lat")),
        "center_lon": _to_float(_pick(item, "centerLon", "CenterLon", "lon", "lng")),
    }


def fetch_children(cortar_no: str, level: int, endpoint: str | None = None) -> list[dict]:
    """cortar_no 바로 아래 하위 지역 목록(시도→시군구, 시군구→동)."""
    endpoint = endpoint or config.NAVER_LAND_ENDPOINT
    sess = _session(endpoint)
    if endpoint == "new":
        data = _get_json(sess, NEW_REGION_URL, {"cortarNo": cortar_no})
        items = _pick(data, "regionList", "list", default=[]) or []
    else:
        data = _get_json(sess, MOBILE_REGION_URL, {"cortarNo": cortar_no, "mycortarNo": ""})
        result = _pick(data, "result", default=data)
        items = _pick(result, "list", "regionList", default=[]) or []
    out = []
    for it in items:
        n = _normalize_region(it, cortar_no, level)
        if n:
            out.append(n)
    return out


def fetch_capital_region_tree(sidos: dict | None = None, sleep: float | None = None,
                              progress=print) -> list[dict]:
    """수도권 시도→시군구→동 전체 트리를 내려받아 region 행 목록으로 반환."""
    sidos = sidos or CAPITAL_SIDO
    sleep = config.NAVER_LAND_SLEEP if sleep is None else sleep
    rows: list[dict] = []
    for sido_no, sido_nm in sidos.items():
        rows.append({"cortar_no": sido_no, "name": sido_nm, "parent_no": None, "level": 1,
                     "sido": sido_nm, "sgg": None, "umd": None,
                     "center_lat": None, "center_lon": None})
        sggs = fetch_children(sido_no, 2)
        progress(f"  {sido_nm}: 시군구 {len(sggs)}개")
        time.sleep(sleep)
        for sgg in sggs:
            sgg.update({"sido": sido_nm, "sgg": sgg["name"], "umd": None})
            rows.append(sgg)
            umds = fetch_children(sgg["cortar_no"], 3)
            for u in umds:
                u.update({"sido": sido_nm, "sgg": sgg["name"], "umd": u["name"]})
                rows.append(u)
            progress(f"    {sgg['name']}: 동 {len(umds)}개")
            time.sleep(sleep)
    return rows


# ------------------------------------------------------------- 매물 목록 ----

def _bounds(lat: float | None, lon: float | None, delta: float = 0.03) -> dict:
    """모바일 엔드포인트는 지도 영역 파라미터를 함께 받는다. 동 중심 ± delta(약 3km)."""
    if lat is None or lon is None:
        return {}
    return {"z": 14, "lat": lat, "lon": lon,
            "btm": lat - delta, "top": lat + delta, "lft": lon - delta, "rgt": lon + delta}


def fetch_articles_page(cortar_no: str, re_type: str = "SG", trade_type: str = "B2",
                        page: int = 1, center: tuple[float | None, float | None] = (None, None),
                        endpoint: str | None = None, sess: requests.Session | None = None):
    """한 페이지 원본 응답 dict 반환."""
    endpoint = endpoint or config.NAVER_LAND_ENDPOINT
    sess = sess or _session(endpoint)
    if endpoint == "new":
        params = {"cortarNo": cortar_no, "realEstateType": re_type, "tradeType": trade_type,
                  "page": page, "order": "dateDesc", "priceType": "RETAIL"}
        return _get_json(sess, NEW_ARTICLE_URL, params)
    params = {"view": "atcl", "rletTpCd": re_type, "tradTpCd": trade_type,
              "cortarNo": cortar_no, "sort": "date", "page": page}
    params.update(_bounds(*center))
    return _get_json(sess, MOBILE_ARTICLE_URL, params)


def _items_of(data: dict) -> tuple[list, bool]:
    """응답에서 (매물 목록, 다음 페이지 있음) 추출. mobile/new 두 형태 모두 처리."""
    items = _pick(data, "body", "articleList", "list", default=[]) or []
    more = _pick(data, "more", "isMoreData", default=False)
    if isinstance(more, str):
        more = more.lower() == "true"
    return list(items), bool(more)


def normalize_article(item: dict, region: dict | None = None, source: str = "naver") -> dict | None:
    """네이버 매물 한 건 → shop_listing 행 dict(분석 필드 제외). 매물번호 없으면 None."""
    no = _pick(item, "atclNo", "articleNo", "article_no")
    if not no:
        return None
    region = region or {}
    trade_cd = _pick(item, "tradTpCd", "tradeTypeCode")
    trade_nm = _pick(item, "tradTpNm", "tradeTypeName") or TRADE_TYPES.get(str(trade_cd), trade_cd)
    re_cd = _pick(item, "rletTpCd", "realEstateTypeCode")
    re_nm = _pick(item, "rletTpNm", "realEstateTypeName") or RE_TYPES.get(str(re_cd), re_cd)

    # 가격: mobile 은 prc(만원 정수)+rentPrc, new 는 dealOrWarrantPrc('1억 5,000')+rentPrc('300')
    main_price = parse_korean_money(_pick(item, "prc", "dealOrWarrantPrc", "price"))
    rent = parse_korean_money(_pick(item, "rentPrc", "rentPrice"))
    if trade_nm == "매매":
        deposit, sale = None, main_price
    elif trade_nm == "전세":
        deposit, sale = main_price, None
        rent = rent or 0
    else:
        deposit, sale = main_price, None

    floor_info = _pick(item, "flrInfo", "floorInfo")
    fl, fl_tot = parse_floor(floor_info)
    tags = _pick(item, "tagList", "tags", default=[]) or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,\s]+", tags) if t]
    feature = _pick(item, "atclFetrDesc", "articleFeatureDesc", "featureDesc", default="") or ""

    return {
        "article_no": str(no),
        "source": source,
        "cortar_no": str(_pick(item, "cortarNo", default=region.get("cortar_no")) or ""),
        "sido": region.get("sido"),
        "sgg": region.get("sgg"),
        "umd": region.get("umd"),
        "name": _pick(item, "atclNm", "articleName"),
        "building_name": _pick(item, "bildNm", "buildingName"),
        "re_type": re_nm,
        "trade_type": trade_nm,
        "deposit": deposit,
        "rent": rent,
        "sale_price": sale,
        "area_contract": _to_float(_pick(item, "spc1", "area1", "supplyArea")),
        "area_exclusive": _to_float(_pick(item, "spc2", "area2", "exclusiveArea")),
        "floor": fl,
        "floor_total": fl_tot,
        "floor_band": floor_band(fl, floor_info),
        "direction": _pick(item, "direction"),
        "feature_desc": feature,
        "tags": ",".join(str(t) for t in tags),
        "lat": _to_float(_pick(item, "lat", "latitude")),
        "lon": _to_float(_pick(item, "lng", "lon", "longitude")),
        "realtor": _pick(item, "rltrNm", "realtorName", "cpNm", "cpName"),
        "confirm_ymd": _norm_confirm_ymd(_pick(item, "atclCfmYmd", "articleConfirmYmd")),
        "raw": item,
    }


def fetch_region_articles(region: dict, re_types=("SG",), trade_types=("B2",),
                          max_pages: int = 30, sleep: float | None = None,
                          endpoint: str | None = None, progress=None) -> list[dict]:
    """동 하나의 매물을 종류×거래유형 조합별로 전 페이지 수집해 정규화된 목록 반환."""
    endpoint = endpoint or config.NAVER_LAND_ENDPOINT
    sleep = config.NAVER_LAND_SLEEP if sleep is None else sleep
    sess = _session(endpoint)
    center = (region.get("center_lat"), region.get("center_lon"))
    src = f"naver_{endpoint}"
    out: dict[str, dict] = {}
    for re_type in re_types:
        for trade_type in trade_types:
            for page in range(1, max_pages + 1):
                data = fetch_articles_page(region["cortar_no"], re_type, trade_type, page,
                                           center=center, endpoint=endpoint, sess=sess)
                items, more = _items_of(data)
                for it in items:
                    n = normalize_article(it, region, source=src)
                    if n:
                        out[n["article_no"]] = n
                if progress:
                    progress(f"      {region.get('umd')} {RE_TYPES.get(re_type, re_type)}/"
                             f"{TRADE_TYPES.get(trade_type, trade_type)} p{page}: {len(items)}건")
                time.sleep(sleep)
                if not items or not more:
                    break
    return list(out.values())


def probe(cortar_no: str, re_type: str = "SG", trade_type: str = "B2",
          center=(None, None), endpoint: str | None = None) -> str:
    """원본 응답 1페이지를 문자열로(필드명 확인용)."""
    data = fetch_articles_page(cortar_no, re_type, trade_type, 1, center=center, endpoint=endpoint)
    items, more = _items_of(data)
    head = json.dumps(items[:2], ensure_ascii=False, indent=2) if items else "(매물 없음)"
    keys = sorted({k for it in items[:20] for k in it.keys()}) if items else []
    return (f"endpoint={endpoint or config.NAVER_LAND_ENDPOINT} cortar={cortar_no} "
            f"type={re_type}/{trade_type} items={len(items)} more={more}\n"
            f"keys={keys}\n\n{head}")


def load_fixture(path: str, region: dict | None = None) -> list[dict]:
    """저장해 둔 원본 JSON(응답 그대로 또는 매물 배열)을 정규화. 오프라인 파서 테스트용."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = data if isinstance(data, list) else _items_of(data)[0]
    rows = []
    for it in items:
        n = normalize_article(it, region, source="fixture")
        if n:
            rows.append(n)
    return rows
