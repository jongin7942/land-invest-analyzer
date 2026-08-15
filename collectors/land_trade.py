"""국토교통부 토지 매매 실거래가 수집기 (data.go.kr).

엔드포인트: https://apis.data.go.kr/1613000/RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade
요청변수: serviceKey, LAWD_CD(시군구 5자리), DEAL_YMD(YYYYMM), pageNo, numOfRows
응답: XML

주의: 2024~2025 API 개편으로 응답 필드명이 바뀔 수 있어, <item> 하위 태그를
      통째로 dict 로 담고(raw) 후보 키 목록으로 유연하게 매핑한다.
"""
from __future__ import annotations  # Python 3.9 에서 str | None 표기 허용

import time
import xml.etree.ElementTree as ET

import requests

from data.gyeonggi_sigungu import name_of

API_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade"

# API 필드명이 개편돼도 견디도록 후보 키를 순서대로 시도한다.
FIELD_CANDIDATES = {
    "umd_nm": ["umdNm", "법정동"],
    "jibun": ["jibun", "지번"],
    "jimok": ["jimok", "지목"],
    "zoning": ["landUse", "용도지역", "zoning"],
    "deal_area": ["dealArea", "거래면적"],
    "deal_amount": ["dealAmount", "거래금액"],
    "share_type": ["shareDealingType", "지분구분", "구분"],
    "year": ["dealYear", "년"],
    "month": ["dealMonth", "월"],
    "day": ["dealDay", "일"],
}


class LandTradeError(RuntimeError):
    pass


def _pick(raw: dict, keys: list[str]) -> str | None:
    for k in keys:
        v = raw.get(k)
        if v is not None and v.strip() != "":
            return v.strip()
    return None


def _to_float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _to_int_amount(s: str | None) -> int | None:
    """거래금액(만원). '1,200' 같은 문자열 → 1200."""
    if s is None:
        return None
    try:
        return int(s.replace(",", "").strip())
    except ValueError:
        return None


def _parse_items(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)

    # 에러 응답(잘못된 키 등)은 다른 스키마로 온다: <OpenAPI_ServiceResponse>...<errMsg>
    err = root.find(".//errMsg")
    if err is not None:
        reason = root.find(".//returnAuthMsg")
        detail = reason.text if reason is not None else err.text
        raise LandTradeError(f"API 에러 응답: {detail}")

    result_code = root.find(".//resultCode")
    if result_code is not None and result_code.text not in ("00", "000"):
        msg = root.find(".//resultMsg")
        raise LandTradeError(
            f"resultCode={result_code.text} msg={msg.text if msg is not None else '?'}"
        )

    items = []
    for item in root.iter("item"):
        raw = {child.tag: (child.text or "") for child in item}
        items.append(raw)
    return items


def fetch_month(service_key: str, lawd_cd: str, deal_ymd: str,
                num_of_rows: int = 500, max_pages: int = 20,
                timeout: int = 20) -> list[dict]:
    """한 시군구(lawd_cd)·한 달(deal_ymd=YYYYMM)의 토지 실거래를 전부 수집해
    DB 컬럼에 맞춘 dict 목록으로 반환."""
    all_rows: list[dict] = []
    page = 1
    while page <= max_pages:
        params = {
            "serviceKey": service_key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "pageNo": page,
            "numOfRows": num_of_rows,
        }
        resp = requests.get(API_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        raws = _parse_items(resp.text)
        if not raws:
            break

        for raw in raws:
            y = _pick(raw, FIELD_CANDIDATES["year"])
            m = _pick(raw, FIELD_CANDIDATES["month"])
            d = _pick(raw, FIELD_CANDIDATES["day"])
            deal_ymd_full = None
            if y and m and d:
                deal_ymd_full = f"{int(y):04d}{int(m):02d}{int(d):02d}"

            all_rows.append({
                "sgg_cd": lawd_cd,
                "sgg_nm": name_of(lawd_cd),
                "umd_nm": _pick(raw, FIELD_CANDIDATES["umd_nm"]),
                "jibun": _pick(raw, FIELD_CANDIDATES["jibun"]),
                "jimok": _pick(raw, FIELD_CANDIDATES["jimok"]),
                "zoning": _pick(raw, FIELD_CANDIDATES["zoning"]),
                "deal_area": _to_float(_pick(raw, FIELD_CANDIDATES["deal_area"])),
                "deal_amount": _to_int_amount(_pick(raw, FIELD_CANDIDATES["deal_amount"])),
                "deal_ymd": deal_ymd_full,
                "share_type": _pick(raw, FIELD_CANDIDATES["share_type"]),
                "raw": raw,
            })

        if len(raws) < num_of_rows:
            break
        page += 1
        time.sleep(0.2)  # 과도한 호출 방지

    return all_rows
