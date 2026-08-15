"""V-World 필지 프로파일 수집기 (Phase 2-C).

주소(경매/공매 물건 주소 등) 한 건을 넣으면:
  주소 → 좌표(지오코딩) → 필지 PNU(연속지적) → 토지특성(용도지역·도로접면) → 토지임야(지목·면적)
를 순서대로 조회해 하나의 필지 프로파일 dict 로 돌려준다.

이 프로파일이 Phase 4(경매/공매 후보)의 맹지/용도/건축규모 판정 입력이 된다.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import requests

import config

GEOCODE_URL = "https://api.vworld.kr/req/address"
DATA_URL = "https://api.vworld.kr/req/data"
NED_LANDCHAR_URL = "https://api.vworld.kr/ned/data/getLandCharacteristics"
NED_LADFRL_URL = "https://api.vworld.kr/ned/data/ladfrlList"
NED_LANDPRICE_URL = "https://api.vworld.kr/ned/data/getIndvdLandPriceAttr"

DEFAULT_DOMAIN = "http://localhost"


class VWorldError(RuntimeError):
    pass


def _key() -> str:
    if not config.VWORLD_API_KEY:
        raise VWorldError("VWORLD_API_KEY 가 비어 있습니다. .env 확인.")
    return config.VWORLD_API_KEY


def _get_with_retry(url: str, params: dict, timeout: int, retries: int = 3):
    """대량 배치 처리 중 V-World 서버가 간헐적으로 연결을 끊는 경우가 있어
    지수 백오프로 재시도한다(경험적으로 200여 건 연속 호출 후 발생)."""
    last_exc = None
    for attempt in range(retries):
        try:
            return requests.get(url, params=params, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
    raise VWorldError(f"V-World 연결 실패(재시도 {retries}회 소진): {last_exc}")


def geocode(address: str, road: bool = False, timeout: int = 20):
    """주소 → (lon, lat). PARCEL(지번) 기본, road=True면 도로명.
    실패 시 None 반환(주소 오타/미존재)."""
    g = _get_with_retry(GEOCODE_URL, {
        "service": "address", "version": "2.0", "request": "GetCoord",
        "format": "json", "crs": "epsg:4326",
        "type": "ROAD" if road else "PARCEL",
        "address": address, "key": _key(),
    }, timeout).json()
    if g["response"]["status"] != "OK":
        return None
    pt = g["response"]["result"]["point"]
    return float(pt["x"]), float(pt["y"])


def parcel_at(lon: float, lat: float, timeout: int = 20):
    """좌표의 연속지적 필지 → {pnu, addr, jibun, bonbun, bubun}. 없으면 None."""
    d = _get_with_retry(DATA_URL, {
        "service": "data", "request": "GetFeature", "data": "LP_PA_CBND_BUBUN",
        "version": "2.0", "format": "json", "geomFilter": f"POINT({lon} {lat})",
        "size": "1", "key": _key(), "domain": DEFAULT_DOMAIN,
    }, timeout).json()
    if d["response"]["status"] != "OK":
        return None
    feats = d["response"]["result"]["featureCollection"]["features"]
    if not feats:
        return None
    p = feats[0]["properties"]
    return {
        "pnu": p.get("pnu"),
        "addr": p.get("addr"),
        "jibun": p.get("jibun"),
        "bonbun": p.get("bonbun"),
        "bubun": p.get("bubun"),
    }


def _ned_first_item(xml_text: str):
    root = ET.fromstring(xml_text)
    err = root.findtext(".//error/text") or root.findtext(".//error/code")
    if err:
        raise VWorldError(f"NED 에러: {err}")
    tc = root.findtext(".//totalCount")
    if tc in (None, "0"):
        return None
    # NED 응답의 개별 레코드 태그명은 서비스마다 달라 첫 leaf 컨테이너를 찾는다.
    for tag in ("field", "item", "landCharacteristics", "ladfrl"):
        el = root.find(f".//{tag}")
        if el is not None:
            return el
    # 못 찾으면 response 바로 아래 첫 복합 요소
    for child in root:
        if len(list(child)):
            return child
    return None


def land_characteristics(pnu: str, years=("2024", "2023", "2025"), timeout: int = 20):
    """토지특성: {용도지역, 도로접면, 토지이용상황, 지형형상}. 최신 연도부터 시도."""
    for yr in years:
        r = _get_with_retry(NED_LANDCHAR_URL, {
            "key": _key(), "format": "xml", "pnu": pnu,
            "stdrYear": yr, "numOfRows": "5",
        }, timeout)
        el = _ned_first_item(r.text)
        if el is not None:
            return {
                "zoning": el.findtext("prposArea1Nm"),
                "zoning2": el.findtext("prposArea2Nm"),
                "road_side": el.findtext("roadSideCodeNm"),
                "land_use": el.findtext("ladUseSittnNm"),
                "shape": el.findtext("tpgrphFrmCodeNm"),
                "stdr_year": yr,
            }
    return None


def ladfrl(pnu: str, timeout: int = 20):
    """토지임야정보: {지목, 면적㎡, 대장구분}. 없으면 None."""
    r = _get_with_retry(NED_LADFRL_URL, {
        "key": _key(), "format": "xml", "pnu": pnu, "numOfRows": "5",
    }, timeout)
    el = _ned_first_item(r.text)
    if el is None:
        return None
    area = el.findtext("lndpclAr")
    try:
        area = float(area) if area else None
    except ValueError:
        area = None
    return {
        "jimok": el.findtext("lndcgrCodeNm"),
        "area_m2": area,
        "register_type": el.findtext("regstrSeCodeNm"),
    }


def land_price(pnu: str, years=("2025", "2024", "2023"), timeout: int = 20):
    """개별공시지가(㎡당 원). 토지보상용 가치평가의 기준가로 쓴다 —
    수용보상은 실거래가가 아니라 표준지/개별공시지가를 기준으로 산정되기 때문.
    최신 연도부터 시도, 없으면 None."""
    for yr in years:
        r = _get_with_retry(NED_LANDPRICE_URL, {
            "key": _key(), "format": "xml", "pnu": pnu,
            "stdrYear": yr, "numOfRows": "5",
        }, timeout)
        el = _ned_first_item(r.text)
        if el is not None:
            price = el.findtext("pblntfPclnd")
            try:
                price = int(price) if price else None
            except ValueError:
                price = None
            if price:
                return {"price_won_m2": price, "stdr_year": yr,
                        "announced_date": el.findtext("pblntfDe")}
    return None


def profile_address(address: str, road: bool = False) -> dict:
    """주소 한 건의 전체 필지 프로파일. 단계별 실패는 dict 안에 기록한다."""
    prof = {"input_address": address, "ok": False, "error": None}
    coord = geocode(address, road=road)
    if coord is None and not road:
        coord = geocode(address, road=True)  # 지번 실패 시 도로명 재시도
    if coord is None:
        prof["error"] = "지오코딩 실패(주소 확인 필요)"
        return prof
    prof["lon"], prof["lat"] = coord

    parcel = parcel_at(*coord)
    if not parcel or not parcel.get("pnu"):
        prof["error"] = "해당 좌표의 필지 조회 실패"
        return prof
    prof.update(parcel)

    lc = land_characteristics(parcel["pnu"]) or {}
    prof.update(lc)
    ld = ladfrl(parcel["pnu"]) or {}
    prof.update(ld)
    lp = land_price(parcel["pnu"]) or {}
    if lp:
        prof["land_price_won_m2"] = lp["price_won_m2"]
        prof["land_price_year"] = lp["stdr_year"]
    prof["ok"] = True
    return prof
