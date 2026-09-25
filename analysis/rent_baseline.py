"""상가 임대료 기준선 엔진 (개원노트용).

임대 매물(shop_listing, 월세)로 '동 × 층대별 전용평당 환산월세 중앙값'을 만든다.
병원은 보통 2~5층 메디컬/근생 건물에 들어가므로 층대를 반드시 분리한다
(1층 임대료는 상층의 1.5~3배가 흔해 섞으면 기준이 무의미해진다).

환산월세(만원) = 월세 + 보증금 × (전환율/12)       전환율 기본 6%(config.RENT_CONVERSION_RATE)
평당 환산월세 = 환산월세 / 전용평                    전용면적 없으면 계약면적×0.55 로 추정(태그 표시)

기준선 계층(정밀→포괄): (동, 층대, 면적구간) > (동, 층대) > (시군구, 층대) > (시도, 층대)
"""
from __future__ import annotations

import statistics

import config

PYEONG_M2 = 3.3058
EXCLUSIVE_RATIO_GUESS = 0.55  # 상가 전용률 통상 50~60%

# 병원 개원 면적대. 30평 미만은 소형(치과 1인 등), 30~80평이 의원 주력, 80평 이상은 대형/메디컬.
AREA_BUCKETS = (
    (30, "소형(~30평)"),
    (80, "중형(30~80평)"),
    (float("inf"), "대형(80평~)"),
)


def area_bucket(pyeong: float) -> str:
    for limit, label in AREA_BUCKETS:
        if pyeong <= limit:
            return label
    return AREA_BUCKETS[-1][1]


def converted_rent(deposit_manwon, rent_manwon, rate: float | None = None) -> float | None:
    """환산월세(만원). 월세 정보가 아예 없으면 None."""
    rate = config.RENT_CONVERSION_RATE if rate is None else rate
    if rent_manwon is None and deposit_manwon is None:
        return None
    return float(rent_manwon or 0) + float(deposit_manwon or 0) * rate / 12.0


def exclusive_pyeong(listing: dict) -> tuple[float | None, bool]:
    """(전용평, 추정여부). 전용면적 없으면 계약면적으로 추정."""
    ex = listing.get("area_exclusive")
    if ex and ex > 0:
        return ex / PYEONG_M2, False
    ct = listing.get("area_contract")
    if ct and ct > 0:
        return ct * EXCLUSIVE_RATIO_GUESS / PYEONG_M2, True
    return None, False


def enrich(listing: dict) -> dict:
    """conv_rent, rent_per_py 채움(in-place). 매매 매물은 건너뜀."""
    if (listing.get("trade_type") or "") not in ("월세", "전세"):
        listing["conv_rent"] = None
        listing["rent_per_py"] = None
        return listing
    cr = converted_rent(listing.get("deposit"), listing.get("rent"))
    py, _ = exclusive_pyeong(listing)
    listing["conv_rent"] = round(cr, 1) if cr is not None else None
    listing["rent_per_py"] = round(cr / py, 2) if (cr is not None and py and py > 0) else None
    return listing


def _iqr_trim(values: list[float]) -> list[float]:
    if len(values) < 8:
        return values
    s = sorted(values); n = len(s)
    q1, q3 = s[n // 4], s[(3 * n) // 4]
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [v for v in s if lo <= v <= hi]


def _summ(values: list[float]) -> dict:
    s = sorted(_iqr_trim(values)); n = len(s)
    return {"n": n, "median": statistics.median(s), "p25": s[n // 4],
            "p75": s[(3 * n) // 4] if n > 1 else s[-1], "min": s[0], "max": s[-1]}


def build_baselines(listings: list[dict], min_samples: int = 4) -> dict:
    """활성 임대 매물 목록으로 기준선 dict 생성.
    키: L0 (umd, band, area_bucket) / L1 (umd, band) / L2 (sgg, band) / L3 (sido, band)
    값: 평당 환산월세 요약 + 보증금·월세 중앙값(참고)."""
    buckets = {"L0": {}, "L1": {}, "L2": {}, "L3": {}}
    extra = {}  # 같은 키의 보증금/월세/면적 원값(참고 통계)

    for l in listings:
        if (l.get("trade_type") or "") != "월세" or l.get("status", "active") != "active":
            continue
        rpp = l.get("rent_per_py")
        if not rpp or rpp <= 0:
            continue
        band = l.get("floor_band") or "미상"
        py, _ = exclusive_pyeong(l)
        keys = {
            "L0": (l.get("umd"), band, area_bucket(py)) if py else None,
            "L1": (l.get("umd"), band),
            "L2": (l.get("sgg"), band),
            "L3": (l.get("sido"), band),
        }
        for lvl, k in keys.items():
            if k is None or None in k:
                continue
            buckets[lvl].setdefault(k, []).append(rpp)
            e = extra.setdefault((lvl, k), {"deposit": [], "rent": [], "py": []})
            if l.get("deposit") is not None:
                e["deposit"].append(float(l["deposit"]))
            if l.get("rent") is not None:
                e["rent"].append(float(l["rent"]))
            if py:
                e["py"].append(py)

    out = {}
    for lvl, d in buckets.items():
        out[lvl] = {}
        for k, vals in d.items():
            if len(vals) < min_samples:
                continue
            s = _summ(vals)
            e = extra[(lvl, k)]
            s["deposit_med"] = statistics.median(e["deposit"]) if e["deposit"] else None
            s["rent_med"] = statistics.median(e["rent"]) if e["rent"] else None
            s["py_med"] = statistics.median(e["py"]) if e["py"] else None
            out[lvl][k] = s
    return out


def lookup(baselines: dict, sido, sgg, umd, band, pyeong: float | None = None):
    """정밀→포괄 순으로 (요약, 레벨설명) 반환. 없으면 (None, None)."""
    band = band or "미상"
    if pyeong:
        hit = baselines["L0"].get((umd, band, area_bucket(pyeong)))
        if hit:
            return hit, f"동·층·면적({umd}·{band}·{area_bucket(pyeong)})"
    for lvl, key, desc in (("L1", (umd, band), f"동·층({umd}·{band})"),
                           ("L2", (sgg, band), f"시군구·층({sgg}·{band})"),
                           ("L3", (sido, band), f"시도·층({sido}·{band})")):
        hit = baselines[lvl].get(key)
        if hit:
            return hit, desc
    return None, None


def assess(listing: dict, baselines: dict) -> dict | None:
    """매물 한 건의 평당 환산월세를 기준선과 비교. 양수 pct = 기준선보다 비쌈.
    label: 저렴(-15% 이하) / 적정 / 비쌈(+15% 이상)."""
    rpp = listing.get("rent_per_py")
    if not rpp:
        return None
    py, _ = exclusive_pyeong(listing)
    summ, level = lookup(baselines, listing.get("sido"), listing.get("sgg"), listing.get("umd"),
                         listing.get("floor_band"), py)
    if not summ:
        return None
    med = summ["median"]
    pct = (rpp - med) / med * 100 if med else None
    label = "적정"
    if pct is not None:
        if pct <= -15:
            label = "저렴"
        elif pct >= 15:
            label = "비쌈"
    return {"median": med, "p25": summ["p25"], "p75": summ["p75"], "n": summ["n"],
            "level": level, "input": rpp, "pct_vs_median": pct, "label": label}


def estimate_monthly_cost(baselines: dict, sido, sgg, umd, band: str, pyeong: float,
                          deposit_months: float = 10.0) -> dict | None:
    """개원노트용: '이 동네 이 층 N평이면 월세·보증금이 대략 얼마'를 기준선에서 역산.
    보증금은 관행상 월세의 약 10개월치(deposit_months)로 가정해 환산월세를 월세+보증금으로 분해."""
    summ, level = lookup(baselines, sido, sgg, umd, band, pyeong)
    if not summ:
        return None
    rate = config.RENT_CONVERSION_RATE
    conv = summ["median"] * pyeong          # 환산월세 총액(만원)
    # conv = rent + rent*deposit_months*rate/12  →  rent = conv / (1 + deposit_months*rate/12)
    rent = conv / (1 + deposit_months * rate / 12)
    deposit = rent * deposit_months
    return {
        "level": level, "n": summ["n"], "rent_per_py_med": summ["median"],
        "rent_per_py_p25": summ["p25"], "rent_per_py_p75": summ["p75"],
        "conv_rent": conv, "rent_est": rent, "deposit_est": deposit,
        "rent_low": summ["p25"] * pyeong / (1 + deposit_months * rate / 12),
        "rent_high": summ["p75"] * pyeong / (1 + deposit_months * rate / 12),
        "market_deposit_med": summ.get("deposit_med"), "market_rent_med": summ.get("rent_med"),
    }
