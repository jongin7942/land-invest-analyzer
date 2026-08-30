"""국토교통부 아파트 전월세 실거래가 수집기.

데이터셋: 국토교통부_아파트 전월세 실거래가 자료 (data.go.kr)
  Base : apis.data.go.kr/1613000/RTMSDataSvcAptRent
  Op   : GET /getRTMSDataSvcAptRent
  파라미터: serviceKey, LAWD_CD, DEAL_YMD(YYYYMM), pageNo, numOfRows

전세는 Initial Equity 의 승계 보증금이자 전세가율의 분자다. 다만 **모든 계약이
시세는 아니다** — 갱신계약, 특히 갱신요구권을 쓴 계약은 기존 보증금의 연장이라
시세보다 낮게 찍힌다. 이 API 가 `계약구분(신규/갱신)`과 `갱신요구권사용`,
`종전계약보증금`을 주므로 저장해 두고 PHASE 2에서 걸러낸다.

⚠ 필드명은 라이브 검증하지 못했다. `cli probe rent` 로 확인할 것.
"""
from __future__ import annotations

from apt_engine import area, units
from apt_engine.collectors import molit

API_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"

SOURCE_KEY = "molit_apt_rent"

FIELDS = {
    "apt_name":    ("aptNm", "아파트"),
    "emd_name":    ("umdNm", "법정동"),
    "jibun":       ("jibun", "지번"),
    "sgg_cd":      ("sggCd", "지역코드"),
    "area":        ("excluUseAr", "전용면적"),
    "deposit":     ("deposit", "보증금액", "보증금"),
    "rent":        ("monthlyRent", "월세금액", "월세"),
    "year":        ("dealYear", "년"),
    "month":       ("dealMonth", "월"),
    "day":         ("dealDay", "일"),
    "floor":       ("floor", "층"),
    "build_year":  ("buildYear", "건축년도"),
    "term":        ("contractTerm", "계약기간"),
    "ctype":       ("contractType", "계약구분"),
    "renewal":     ("useRRRight", "갱신요구권사용"),
    "pre_deposit": ("preDeposit", "종전계약보증금"),
    "pre_rent":    ("preMonthlyRent", "종전계약월세"),
}


def _renewal_flag(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return 1 if value.strip().upper() in ("사용", "Y", "O", "1") else 0


def parse_item(raw: dict, lawd_cd: str) -> dict | None:
    def f(name: str) -> str | None:
        return molit.pick(raw, FIELDS[name])

    exclusive = molit.to_float(f("area"))
    deposit_manwon = molit.to_int(f("deposit"))
    contract_ymd = molit.ymd(f("year"), f("month"), f("day"))
    apt_name = f("apt_name")

    if not exclusive or exclusive <= 0 or deposit_manwon is None or not contract_ymd:
        return None
    if deposit_manwon < 0 or not apt_name:
        return None

    rent_manwon = molit.to_int(f("rent")) or 0
    pre_deposit = molit.to_int(f("pre_deposit"))
    pre_rent = molit.to_int(f("pre_rent"))

    return {
        "lawd_cd": f("sgg_cd") or lawd_cd,
        "emd_name": f("emd_name"),
        "jibun": f("jibun"),
        "apt_name": apt_name,
        "exclusive_area_m2": exclusive,
        "area_band": area.band_of(exclusive),
        "deposit": int(units.from_manwon(deposit_manwon)),
        "monthly_rent": int(units.from_manwon(rent_manwon)),
        "contract_ymd": contract_ymd,
        "floor": molit.to_int(f("floor")),
        "build_year": molit.to_int(f("build_year")),
        "contract_type": f("ctype"),
        "use_renewal_right": _renewal_flag(f("renewal")),
        "prev_deposit": int(units.from_manwon(pre_deposit)) if pre_deposit is not None else None,
        "prev_monthly_rent": int(units.from_manwon(pre_rent)) if pre_rent is not None else None,
        "contract_term": f("term"),
        "raw": raw,
    }


def fetch_month(lawd_cd: str, deal_ymd: str, *, num_of_rows: int = 1000) -> list[dict]:
    items = molit.fetch_all_pages(
        API_URL, {"LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd}, num_of_rows=num_of_rows
    )
    rows = []
    for raw in items:
        try:
            row = parse_item(raw, lawd_cd)
        except area.AreaBandError:
            continue
        if row:
            rows.append(row)
    return rows


def probe(lawd_cd: str = "11680", deal_ymd: str = "202607") -> str:
    return molit.probe(API_URL, {"LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd})
