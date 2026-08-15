"""온비드(캠코) 공매 부동산 물건목록 수집기 (Phase 4) — 라이브 검증 완료.

데이터셋: 한국자산관리공사_차세대 온비드 부동산 물건목록 조회서비스 (data.go.kr 15157207)
  Base URL : apis.data.go.kr/B010003/OnbidRlstListSrvc2
  Operation: GET /getRlstCltrList2
  응답     : XML 고정(type=json 파라미터 무시됨. 확인됨)
  인증키   : 실거래가와 동일한 DATA_GO_KR_SERVICE_KEY(계정 공유 키)

재산유형코드(prptDivCd, 라이브로 전수 확인함 2026-08-15):
  0002 공유재산(409) / 0004 불용품(8, 부동산 아님) / 0005 기타일반재산(16,382)
  0007 압류재산(50,591, 세금체납 등 강제매각 — 김종률식 핵심 사냥터)
  0008 수탁재산(42) / 0010 국유재산(2,566)
  (0001/0003/0006/0009 은 NODATA — 미사용 코드)

응답 필드의 좋은 소식: ltnoPnu(19자리 PNU), lctnSdnm/Sggnm/Emdnm(소재지 구조화),
landSqms(면적㎡) 가 이미 포함돼 있어 V-World 지오코딩 단계 없이 바로
land_characteristics(pnu) 호출이 가능하다(collectors.land_characteristics 참고).
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import requests

import config

BASE_URL = "https://apis.data.go.kr/B010003/OnbidRlstListSrvc2/getRlstCltrList2"

PRPT_DIV = {
    "0002": "공유재산",
    "0005": "기타일반재산",
    "0007": "압류재산",
    "0008": "수탁재산",
    "0010": "국유재산",
}
DEFAULT_PRPT_DIV_CODES = list(PRPT_DIV.keys())


class OnbidError(RuntimeError):
    pass


def _to_int(s):
    if s is None or s == "":
        return None
    try:
        return int(float(str(s).replace(",", "")))
    except ValueError:
        return None


def _to_float(s):
    if s is None or s == "":
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def probe(prpt_div_cd: str = "0007", pvct_trgt_yn: str = "N", timeout: int = 20) -> str:
    """라이브 원본 응답(1건) 문자열 반환. 필드명 재확인용."""
    key = config.require_data_go_kr_key()
    r = requests.get(BASE_URL, params={
        "serviceKey": key, "pageNo": 1, "numOfRows": 1,
        "prptDivCd": prpt_div_cd, "pvctTrgtYn": pvct_trgt_yn,
    }, timeout=timeout)
    return f"HTTP {r.status_code}\n{r.url}\n\n{r.text[:3000]}"


def _fetch_page(key, prpt_div_cd, pvct_trgt_yn, page_no, num_of_rows, timeout):
    r = requests.get(BASE_URL, params={
        "serviceKey": key, "pageNo": page_no, "numOfRows": num_of_rows,
        "prptDivCd": prpt_div_cd, "pvctTrgtYn": pvct_trgt_yn,
    }, timeout=timeout)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    rc = root.findtext(".//resultCode")
    if rc == "03":  # NODATA_ERROR — 이 코드/조건엔 물건 없음(정상)
        return [], 0
    if rc not in ("00", "000"):
        raise OnbidError(f"resultCode={rc} msg={root.findtext('.//resultMsg')}")
    total = _to_int(root.findtext(".//totalCount")) or 0
    items = [{c.tag: (c.text or "") for c in item} for item in root.iter("item")]
    return items, total


def fetch_land_items(prpt_div_cds=None, pvct_trgt_yn: str = "N", sido: str = "경기도",
                     num_of_rows: int = 500, max_pages_per_code: int = 200, timeout: int = 25,
                     max_matches: int | None = None):
    """공매 물건목록에서 소재지=sido, 용도중분류=토지 인 것만 수집해 dict 목록으로 반환.

    응답에 이미 PNU·면적·소재지가 구조화돼 있어 별도 지오코딩이 필요 없다.
    max_matches 지정 시 그 개수만큼 찾으면 스캔을 조기 종료(전국 스캔 시간 절약).
    """
    key = config.require_data_go_kr_key()
    codes = prpt_div_cds or DEFAULT_PRPT_DIV_CODES
    rows = []
    seen = set()
    for code in codes:
        if max_matches and len(rows) >= max_matches:
            break
        page = 1
        while page <= max_pages_per_code:
            if max_matches and len(rows) >= max_matches:
                break
            items, total = _fetch_page(key, code, pvct_trgt_yn, page, num_of_rows, timeout)
            if not items:
                break
            for raw in items:
                if sido and raw.get("lctnSdnm") != sido:
                    continue
                if raw.get("cltrUsgMclsCtgrNm") != "토지":
                    continue
                dkey = (raw.get("cltrMngNo"), raw.get("pbctCdtnNo"))
                if dkey in seen:
                    continue
                seen.add(dkey)
                rows.append({
                    "mgmt_no": raw.get("cltrMngNo"),
                    "plnm_no": raw.get("pbctCdtnNo"),
                    "name": raw.get("onbidCltrNm"),
                    "prpt_div": raw.get("prptDivNm"),
                    "use_name": raw.get("cltrUsgSclsCtgrNm"),  # 소분류(대지/전/답/임야 등)
                    "sido": raw.get("lctnSdnm"),
                    "sgg": raw.get("lctnSggnm"),
                    "emd": raw.get("lctnEmdNm"),
                    "pnu": raw.get("ltnoPnu") or None,
                    "area_m2": _to_float(raw.get("landSqms")),
                    "appraisal": _to_int(raw.get("apslEvlAmt")),
                    "min_bid": _to_int(raw.get("lowstBidPrcIndctCont")),
                    "disposal": raw.get("dspsMthodNm"),
                    "bid_begin": raw.get("cltrBidBgngDt"),
                    "bid_end": raw.get("cltrBidEndDt"),
                    "status": raw.get("pbctStatNm"),
                    "round": _to_int(raw.get("pbctNsq")),  # 입찰회차(유찰 누적 횟수 참고용)
                    "raw": raw,
                })
            if len(items) < num_of_rows or page * num_of_rows >= total:
                break
            page += 1
            time.sleep(0.15)
    return rows
