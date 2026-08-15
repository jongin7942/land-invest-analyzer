"""개발용 vs 토지보상용 — 두 관점의 가치평가.

김종률 노트의 수용보상 강의 내용을 그대로 규칙화한다:
  대지군(공장·주택지·창고 등) = 필지 작고 소유자 많음, 이용도 높음 → 보상 후함
  농지군(농지·임야·과수원 등) = 필지 크고 소유자 적음, 신도시 내 면적 비중 큼
                              → 보상은 짜다(공시지가의 1.1배 안팎), 대신 총액은 큼

개발용 관점(이미 구현: zoning_rules+road_access+price_baseline)은 "지어서 판다",
토지보상용 관점은 "짓지 않고 수용될 때까지 들고 있는다" — 완전히 다른 논리라서
같은 필지도 두 관점의 점수가 정반대로 나올 수 있다(개발엔 나쁜 개발제한구역이
보상용으로는 오히려 그린벨트 해제 이슈가 있으면 매력적일 수 있는 식).
"""
from __future__ import annotations

PYEONG_M2 = 3.3058

LAND_GROUP = {
    "대": "대지군", "공장용지": "대지군", "창고용지": "대지군", "주차장": "대지군",
    "주유소용지": "대지군", "학교용지": "대지군", "잡종지": "대지군", "종교용지": "대지군",
    "전": "농지군", "답": "농지군", "과수원": "농지군", "임야": "농지군", "목장용지": "농지군",
}

# 노트: "농지군이 보상을 적게 해준다. 거의 공시지가의 1.1배 정도로 해주는 경우가 많다."
# 대지군은 실거래 시세에 더 가깝게(통상 표준지 감정평가 반영률이 높음) 보상되는 경향.
COMPENSATION_MULTIPLIER = {"농지군": 1.1, "대지군": 1.4, "미분류": 1.2}


def land_group(jimok: str | None) -> str:
    return LAND_GROUP.get((jimok or "").strip(), "미분류")


def price_per_pyeong_won(price_won_m2: float) -> float:
    """개별공시지가(원/㎡) → 만원/평."""
    return price_won_m2 * PYEONG_M2 / 10000


def compensation_view(r: dict) -> dict | None:
    """토지보상용 관점 평가. r 에 land_price_won_m2, jimok, ppp_min_bid 필요.
    필수값 없으면 None."""
    price_m2 = r.get("land_price_m2")
    ppp_bid = r.get("ppp_min_bid")
    if not price_m2 or not ppp_bid:
        return None

    group = land_group(r.get("jimok"))
    mult = COMPENSATION_MULTIPLIER.get(group, 1.2)

    price_ppp = price_per_pyeong_won(price_m2)  # 공시지가 평당(만원)
    est_comp_ppp = price_ppp * mult             # 노트의 배율로 추정한 보상단가(평당 만원)

    discount_vs_official = (price_ppp - ppp_bid) / price_ppp * 100 if price_ppp else None
    discount_vs_est_comp = (est_comp_ppp - ppp_bid) / est_comp_ppp * 100 if est_comp_ppp else None

    return {
        "land_group": group,
        "official_price_ppp": price_ppp,       # 공시지가 평당
        "est_compensation_ppp": est_comp_ppp,   # 배율 적용 추정 보상단가 평당
        "multiplier": mult,
        "discount_vs_official": discount_vs_official,
        "discount_vs_est_comp": discount_vs_est_comp,
    }


def compensation_score(cv: dict | None, has_dev_news: bool = False) -> float:
    """토지보상용 관점 점수. 공시지가 대비 저평가 + (뉴스로 확인된) 개발압력 가점.
    뉴스 신호가 없으면 '수용 가능성 자체가 불확실'이라 크게 깎는다 —
    수용보상은 '거기 사업이 실제로 지정되는가'가 전제이기 때문."""
    if cv is None:
        return None
    score = cv.get("discount_vs_official") or 0
    if has_dev_news:
        score += 25  # 인근 개발계획 뉴스가 있으면 수용 가능성이 실질적
    else:
        score -= 15  # 뉴스 신호 없으면 순수 추정 — 과신 방지
    return score
