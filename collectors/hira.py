"""건강보험심사평가원(심평원) 병의원·약국 정보 수집기 (data.go.kr, 공식·무료).

경쟁·보완 시설 밀도 계산의 재료. 인증키는 실거래가와 같은 DATA_GO_KR_SERVICE_KEY 를 쓰되
data.go.kr 에서 아래 두 데이터셋을 각각 활용신청(자동승인)해야 한다:
  - 건강보험심사평가원_병원정보서비스   (getHospBasisList)
  - 건강보험심사평가원_약국정보서비스   (getParmacyBasisList  ← 철자 'Parmacy' 가 실제 경로)

지원 조회 방식
  by_sido(sido_cd, cl_cds)          시도 전체를 종별코드별로 페이징 수집(초기 적재용)
  by_radius(lon, lat, radius_m)     좌표 반경 조회(매물 한 건 주변 확인용)
  by_emdong(sido_cd, emdong_nm)     읍면동명 조회

시도코드(심평원): 서울 110000 / 인천 220000 / 경기 310000
종별코드: 01 상급종합 11 종합병원 21 병원 28 요양병원 29 정신병원 31 의원 41 치과병원 51 치과의원
          71 보건소 81 약국 91 한방병원 92 한의원
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET

import requests

import config

HOSP_URL = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
PHARM_URL = "https://apis.data.go.kr/B551182/pharmacyInfoService/getParmacyBasisList"

SIDO_CD = {"서울특별시": "110000", "인천광역시": "220000", "경기도": "310000",
           "서울": "110000", "인천": "220000", "경기": "310000"}
CL_NAMES = {"01": "상급종합병원", "11": "종합병원", "21": "병원", "28": "요양병원", "29": "정신병원",
            "31": "의원", "41": "치과병원", "51": "치과의원", "71": "보건소", "81": "약국",
            "91": "한방병원", "92": "한의원"}
DEFAULT_CL_CDS = ("31", "51", "92", "21", "11", "41", "91")


class HiraError(RuntimeError):
    pass


def _rows_from_response(text: str) -> tuple[list[dict], int]:
    """XML 또는 JSON 응답 → (items, totalCount)."""
    text = text.strip()
    if text.startswith("{"):
        data = json.loads(text)
        body = (data.get("response") or {}).get("body") or {}
        items = body.get("items") or {}
        items = items.get("item") if isinstance(items, dict) else items
        if isinstance(items, dict):
            items = [items]
        return list(items or []), int(body.get("totalCount") or 0)
    root = ET.fromstring(text)
    code = root.findtext(".//resultCode")
    if code not in (None, "00", "0"):
        msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or ""
        raise HiraError(f"심평원 API 오류 resultCode={code} {msg}")
    items = []
    for it in root.iter("item"):
        items.append({c.tag: (c.text or "").strip() for c in it})
    total = int(root.findtext(".//totalCount") or 0)
    return items, total


def _normalize(it: dict, is_pharmacy: bool = False) -> dict:
    def f(x):
        try:
            return float(x) if x not in (None, "") else None
        except ValueError:
            return None
    cl_cd = str(it.get("clCd") or ("81" if is_pharmacy else ""))
    return {
        "ykiho": it.get("ykiho"),
        "name": it.get("yadmNm"),
        "cl_cd": cl_cd,
        "cl_name": it.get("clCdNm") or CL_NAMES.get(cl_cd),
        "sido_cd": it.get("sidoCd"),
        "sggu_cd": it.get("sgguCd"),
        "sggu_name": it.get("sgguCdNm"),
        "emdong_name": it.get("emdongNm"),
        "addr": it.get("addr"),
        "lat": f(it.get("YPos")),
        "lon": f(it.get("XPos")),
        "open_ymd": it.get("estbDd"),
        "dgsbjt": None,
    }


def _fetch_all(url: str, params: dict, is_pharmacy: bool, num_rows: int = 1000,
               sleep: float = 0.3, timeout: int = 30, progress=None) -> list[dict]:
    key = config.require_data_go_kr_key()
    out = []
    page = 1
    while True:
        p = {"serviceKey": key, "pageNo": page, "numOfRows": num_rows, **params}
        r = requests.get(url, params=p, timeout=timeout)
        r.raise_for_status()
        items, total = _rows_from_response(r.text)
        out.extend(_normalize(it, is_pharmacy) for it in items)
        if progress:
            progress(f"    p{page}: {len(items)}건 (누적 {len(out)}/{total})")
        if not items or len(out) >= total:
            break
        page += 1
        time.sleep(sleep)
    return out


def by_sido(sido: str, cl_cds=DEFAULT_CL_CDS, include_pharmacy: bool = True, progress=print) -> list[dict]:
    """시도 전체 병의원(+약국) 목록. sido: '서울'/'경기'/'인천' 또는 심평원 코드."""
    sido_cd = SIDO_CD.get(sido, sido)
    rows = []
    for cl in cl_cds:
        progress(f"  {CL_NAMES.get(cl, cl)}({cl}) 수집")
        rows.extend(_fetch_all(HOSP_URL, {"sidoCd": sido_cd, "clCd": cl}, False, progress=progress))
    if include_pharmacy:
        progress("  약국(81) 수집")
        rows.extend(_fetch_all(PHARM_URL, {"sidoCd": sido_cd}, True, progress=progress))
    return rows


def by_radius(lon: float, lat: float, radius_m: int = 500, include_pharmacy: bool = True) -> list[dict]:
    rows = _fetch_all(HOSP_URL, {"xPos": lon, "yPos": lat, "radius": radius_m}, False)
    if include_pharmacy:
        rows.extend(_fetch_all(PHARM_URL, {"xPos": lon, "yPos": lat, "radius": radius_m}, True))
    return rows


def by_emdong(sido: str, emdong_nm: str, include_pharmacy: bool = True) -> list[dict]:
    sido_cd = SIDO_CD.get(sido, sido)
    rows = _fetch_all(HOSP_URL, {"sidoCd": sido_cd, "emdongNm": emdong_nm}, False)
    if include_pharmacy:
        rows.extend(_fetch_all(PHARM_URL, {"sidoCd": sido_cd, "emdongNm": emdong_nm}, True))
    return rows


def probe(sido: str = "서울", cl_cd: str = "31") -> str:
    key = config.require_data_go_kr_key()
    r = requests.get(HOSP_URL, params={"serviceKey": key, "pageNo": 1, "numOfRows": 2,
                                       "sidoCd": SIDO_CD.get(sido, sido), "clCd": cl_cd}, timeout=30)
    return f"HTTP {r.status_code}\n{r.url}\n\n{r.text[:3000]}"
