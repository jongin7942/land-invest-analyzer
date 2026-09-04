"""청약홈 분양정보 — 공급 계획(§13)의 진짜 데이터 출처.

`supply_plan` 이 요구하는 것과 이 API 가 주는 것이 정확히 맞아떨어진다.

    supply_plan 칸       청약홈 필드                뜻
    complex_name         HOUSE_NM                  단지명
    households           TOT_SUPLY_HSHLDCO          총 공급 세대수
    move_in_ym           MVN_PREARNGE_YM            입주예정월 (YYYYMM)
    announced_ym         RCRIT_PBLANC_DE            모집공고일 — **실제** 분양공고일이다.
                                                     "분양은 준공 30개월 전" 이라는 추정을
                                                     쓸 필요가 없다.
    lawd_cd/emd_name/lat/lon   HSSPLY_ADRES 를 지오코딩해서 얻는다(주소만 준다).

한국부동산원(REB) 이 운영하는 청약홈의 공식 데이터라 종인님이 말한 "민간 집계보다
신뢰도 있는 정부 출처" 조건을 만족한다. 아실·부동산지인 같은 민간 집계는 이 출처가
있는 한 안 쓴다 — 같은 정보를 정부 원천에서 구할 수 있는데 굳이 재가공된 값을
쓸 이유가 없다.

`HOUSE_SECD_NM` 으로 아파트만 거른다(오피스텔·도시형생활주택 제외 — supply_plan 은
아파트 재고 대비 공급이라 다른 상품을 섞으면 분모·분자가 어긋난다).
"""
from __future__ import annotations

import time

import requests

import config

URL = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
SOURCE_KEY = "applyhome_supply"
SOURCE_NAME = "한국부동산원 청약홈 분양정보 조회서비스"
SOURCE_URL = "https://www.applyhome.co.kr"

METRO_NAMES = {"서울", "경기", "인천"}

# 시군구·읍면동 접미사. 주소 앞부분만 남기고 자를 때 쓴다.
_SGG_SUFFIX = ("시", "군", "구")
_EMD_SUFFIX = ("동", "읍", "면", "리", "가")


class ApplyhomeError(RuntimeError):
    pass


def admin_prefix(address: str) -> str | None:
    """공공택지지구 블록 분양처럼 지번이 없는 주소를 시군구·읍면동까지만 자른다.

    "인천광역시 미추홀구 학익동 인천 용현·학익 1블록 도시개발구역 공동3BL" 같은
    주소는 통째로 지오코딩이 안 된다(지번이 지구·블록명이라 필지 검색에 안 걸림).
    앞의 "인천광역시 미추홀구 학익동"만 남기면 실제 행정구역 이름이라 지오코딩이
    된다 - supply_plan 은 lawd_cd(시군구)만 맞으면 좌표 없이도 집계에 들어간다.

    토큰을 하나씩 보며 시군구(시/군/구로 끝남) 하나 + 읍면동(동/읍/면/리/가로
    끝남) 하나를 만나면 거기서 멈춘다. 못 찾으면 None.
    """
    tokens = address.replace(",", " ").split()
    seen_sgg = False
    out: list[str] = []
    for tok in tokens:
        out.append(tok)
        if not seen_sgg:
            if tok.endswith(_SGG_SUFFIX) and len(tok) >= 2:
                seen_sgg = True
            continue
        if tok.endswith(_EMD_SUFFIX) and len(tok) >= 2:
            return " ".join(out)
    return None


def fetch_all(*, page_size: int = 1000, retries: int = 3) -> list[dict]:
    """전국 분양정보 전체. 페이지를 넘겨가며 다 받는다."""
    if not config.DATA_GO_KR_SERVICE_KEY:
        raise ApplyhomeError("DATA_GO_KR_SERVICE_KEY 가 비어 있습니다.")

    out: list[dict] = []
    page = 1
    total = None
    while total is None or len(out) < total:
        last: Exception | None = None
        body = None
        for attempt in range(retries):
            try:
                r = requests.get(URL, params={
                    "serviceKey": config.DATA_GO_KR_SERVICE_KEY,
                    "page": page, "perPage": page_size}, timeout=30)
                r.raise_for_status()
                body = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                last = e
                if attempt < retries - 1:
                    time.sleep(0.5 * (attempt + 1))
        if body is None:
            raise ApplyhomeError(f"청약홈 연결 실패(페이지 {page}): {last}")
        rows = body.get("data") or []
        if not rows:
            break
        out.extend(rows)
        total = body.get("totalCount")
        page += 1
    return out


def metro_apartments(rows: list[dict]) -> list[dict]:
    """수도권 아파트 분양만. 오피스텔 등은 뺀다."""
    return [r for r in rows
            if r.get("SUBSCRPT_AREA_CODE_NM") in METRO_NAMES
            and r.get("HOUSE_SECD_NM") == "APT"
            and r.get("HOUSE_NM")
            and r.get("TOT_SUPLY_HSHLDCO")]
