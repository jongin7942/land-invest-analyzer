"""국토교통부 아파트 매매 실거래가 상세 수집기.

데이터셋: 국토교통부_아파트 매매 실거래가 상세 자료 (data.go.kr)
  Base : apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev
  Op   : GET /getRTMSDataSvcAptTradeDev
  파라미터: serviceKey, LAWD_CD(시군구 5자리), DEAL_YMD(YYYYMM), pageNo, numOfRows
  응답 : XML

이 데이터셋을 쓰는 이유는 단순 실거래가가 아니라 **상세** 자료이기 때문이다.
`거래유형(중개거래/직거래)`·`해제여부`·`해제사유발생일`·`등기일자`가 들어 있어,
요구사항 26-5(취소거래)와 26-6(직거래)을 별도 API 없이 만족할 수 있다.

⚠ 필드명은 개발 환경에서 라이브 검증하지 못했다(프록시가 data.go.kr 을 차단).
2023년 개편 전후의 한글·영문 이름을 모두 후보로 두었고, 다르면
`python -m apt_engine.cli probe trade --lawd 11680 --ym 202607` 로 확인해
FIELDS 에 한 줄 추가하면 된다.
"""
from __future__ import annotations

from apt_engine import area, units
from apt_engine.collectors import molit

API_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"

SOURCE_KEY = "molit_apt_trade"

# 필드 후보: (영문 신규, 한글 구버전, …) 순서대로 시도한다.
FIELDS = {
    "apt_name":   ("aptNm", "아파트"),
    "apt_dong":   ("aptDong", "동"),
    "emd_name":   ("umdNm", "법정동"),
    "jibun":      ("jibun", "지번"),
    "sgg_cd":     ("sggCd", "지역코드"),
    "area":       ("excluUseAr", "전용면적"),
    "amount":     ("dealAmount", "거래금액"),
    "year":       ("dealYear", "년"),
    "month":      ("dealMonth", "월"),
    "day":        ("dealDay", "일"),
    "floor":      ("floor", "층"),
    "build_year": ("buildYear", "건축년도"),
    "deal_type":  ("dealingGbn", "거래유형"),
    "agent":      ("estateAgentSggNm", "중개사소재지"),
    "cancel":     ("cdealType", "해제여부"),
    "cancel_day": ("cdealDay", "해제사유발생일"),
    "regist":     ("rgstDate", "등기일자"),
    "seller":     ("slerGbn", "매도자"),
    "buyer":      ("buyerGbn", "매수자"),
}


def _is_cancelled(value: str | None) -> bool:
    """해제여부는 해제된 거래에만 'O' 가 들어오고 보통은 빈 값이다."""
    return bool(value) and value.strip().upper() in ("O", "Y", "1", "해제")


def parse_item(raw: dict, lawd_cd: str) -> dict | None:
    """<item> dict → trade 행. 필수값이 없으면 None(호출부가 건너뛴다)."""
    def f(name: str) -> str | None:
        return molit.pick(raw, FIELDS[name])

    exclusive = molit.to_float(f("area"))
    amount_manwon = molit.to_int(f("amount"))
    deal_ymd = molit.ymd(f("year"), f("month"), f("day"))

    if not exclusive or exclusive <= 0 or not amount_manwon or amount_manwon <= 0 or not deal_ymd:
        return None
    apt_name = f("apt_name")
    if not apt_name:
        return None

    return {
        "lawd_cd": f("sgg_cd") or lawd_cd,
        "emd_name": f("emd_name"),
        "jibun": f("jibun"),
        "apt_name": apt_name,
        "apt_dong": f("apt_dong"),
        "exclusive_area_m2": exclusive,
        "area_band": area.band_of(exclusive),
        # API 는 만원 단위. 저장은 원(int) — 엔진 전체가 원 하나로만 돈다.
        "deal_amount": int(units.from_manwon(amount_manwon)),
        "deal_ymd": deal_ymd,
        "floor": molit.to_int(f("floor")),
        "build_year": molit.to_int(f("build_year")),
        "deal_type": f("deal_type"),
        "agent_region": f("agent"),
        "cancel_yn": 1 if _is_cancelled(f("cancel")) else 0,
        "cancel_ymd": f("cancel_day"),
        "registration_ymd": f("regist"),
        "seller_type": f("seller"),
        "buyer_type": f("buyer"),
        "raw": raw,
    }


def fetch_month(lawd_cd: str, deal_ymd: str, *, num_of_rows: int = 1000) -> list[dict]:
    """한 시군구·한 달(YYYYMM)의 매매 실거래 전부."""
    items = molit.fetch_all_pages(
        API_URL, {"LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd}, num_of_rows=num_of_rows
    )
    rows = []
    for raw in items:
        try:
            row = parse_item(raw, lawd_cd)
        except area.AreaBandError:
            continue  # 전용면적이 깨진 행. 저장하지 않는다
        if row:
            rows.append(row)
    return rows


def probe(lawd_cd: str = "11680", deal_ymd: str = "202607") -> str:
    return molit.probe(API_URL, {"LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd})
