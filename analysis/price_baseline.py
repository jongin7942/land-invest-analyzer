"""시세 기준선 엔진.

실거래가(land_trade)로 '동·용도지역별 평당가 중앙값'을 만든다.
김종률식 핵심: "이 동네 이 용도지역 땅은 평당 얼마가 정상인가"를 알아야
싸게 나온 물건(급매)을 판정할 수 있다.

실거래가는 지번이 마스킹돼 개별 필지 매칭은 불가하므로, 이 데이터는
'기준선(baseline)' 산출 전용으로 쓰고, 실제 급매 후보는 경매/공매에서 온다.
"""
from __future__ import annotations

import statistics

from db.schema import get_conn

PYEONG_M2 = 3.3058  # 1평 = 3.3058㎡


def price_per_pyeong(deal_amount_manwon, deal_area_m2):
    """평당가(만원/평). 값이 없거나 0이면 None."""
    if not deal_amount_manwon or not deal_area_m2:
        return None
    pyeong = deal_area_m2 / PYEONG_M2
    if pyeong <= 0:
        return None
    return deal_amount_manwon / pyeong


def _iqr_trim(values: list[float]) -> list[float]:
    """IQR 밖(1.5배) 이상치 제거. 표본이 적으면 그대로 반환."""
    if len(values) < 8:
        return values
    s = sorted(values)
    n = len(s)
    q1 = s[n // 4]
    q3 = s[(3 * n) // 4]
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [v for v in s if lo <= v <= hi]


def _summ(values: list[float]) -> dict:
    trimmed = _iqr_trim(values)
    s = sorted(trimmed)
    n = len(s)
    return {
        "n": n,
        "median": statistics.median(s),
        "p25": s[n // 4],
        "p75": s[(3 * n) // 4] if n > 1 else s[-1],
    }


# 시세와 무관하거나 왜곡을 주는 것 제외
EXCLUDE_JIMOK = ("도로", "구거", "제방", "하천", "수도용지")

# 면적 구간(평). 대형 필지는 소규모 거래 위주 기준선과 비교하면 평당가가
# 구조적으로 낮게 나와(임야·전답 대필지 등) 저평가율이 과장된다.
# 예: 여주 계획관리 7,021평 임야를 소형 필지 시세와 비교하면 98% 저평가로 나오지만
# 실제로는 '대형 필지치고 정상'인 경우가 많다 → 같은 규모끼리 비교해 보정한다.
SIZE_BUCKETS = (
    (200, "소형(~200평)"),
    (1000, "중형(200~1000평)"),
    (float("inf"), "대형(1000평~)"),
)


def size_bucket(pyeong: float) -> str:
    for limit, label in SIZE_BUCKETS:
        if pyeong <= limit:
            return label
    return SIZE_BUCKETS[-1][1]


def _fetch_rows():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT sgg_nm, umd_nm, zoning, jimok, deal_area, deal_amount, share_type
            FROM land_trade
            WHERE deal_area > 0 AND deal_amount > 0
            """
        ).fetchall()


def build_baselines(min_samples: int = 5):
    """기준선을 두 트랙으로 만든다:
      규모보정 트랙(면적 구간 포함, 우선 사용): BL1(umd,zoning,bucket)
        > BL2(sgg,zoning,bucket) > BL3(zoning,bucket)
      기존 트랙(면적 무시, 표본 부족시 최종 폴백): L1(umd,zoning) > L2(sgg,zoning) > L3(zoning)
    각 키에 평당가 요약통계(median/p25/p75/n)를 담아 반환.
    """
    l1, l2, l3 = {}, {}, {}
    bl1, bl2, bl3 = {}, {}, {}

    for r in _fetch_rows():
        if r["jimok"] in EXCLUDE_JIMOK:
            continue
        if r["share_type"] and "지분" in r["share_type"]:
            continue  # 지분거래는 평당가 왜곡
        ppp = price_per_pyeong(r["deal_amount"], r["deal_area"])
        if ppp is None:
            continue
        z = r["zoning"] or "(미상)"
        l1.setdefault((r["umd_nm"], z), []).append(ppp)
        l2.setdefault((r["sgg_nm"], z), []).append(ppp)
        l3.setdefault(z, []).append(ppp)

        bucket = size_bucket(r["deal_area"] / PYEONG_M2)
        bl1.setdefault((r["umd_nm"], z, bucket), []).append(ppp)
        bl2.setdefault((r["sgg_nm"], z, bucket), []).append(ppp)
        bl3.setdefault((z, bucket), []).append(ppp)

    def summarize(d):
        return {k: _summ(v) for k, v in d.items() if len(v) >= min_samples}

    return {
        "L1": summarize(l1), "L2": summarize(l2), "L3": summarize(l3),
        "BL1": summarize(bl1), "BL2": summarize(bl2), "BL3": summarize(bl3),
    }


def lookup(baselines: dict, sgg_nm: str, umd_nm: str, zoning: str, area_m2: float | None = None):
    """정밀→포괄 순으로 기준선을 찾아 (요약, 사용레벨) 반환. 없으면 (None, None).
    area_m2 를 주면 같은 면적 구간(규모) 내에서 먼저 찾고, 표본이 없을 때만
    면적을 무시한 기존 기준선으로 폴백한다."""
    z = zoning or "(미상)"
    if area_m2:
        bucket = size_bucket(area_m2 / PYEONG_M2)
        hit = baselines["BL1"].get((umd_nm, z, bucket))
        if hit:
            return hit, f"동·용도·규모({umd_nm}·{z}·{bucket})"
        hit = baselines["BL2"].get((sgg_nm, z, bucket))
        if hit:
            return hit, f"시군구·용도·규모({sgg_nm}·{z}·{bucket})"
        hit = baselines["BL3"].get((z, bucket))
        if hit:
            return hit, f"용도·규모({z}·{bucket})"
    hit = baselines["L1"].get((umd_nm, z))
    if hit:
        return hit, f"동·용도({umd_nm}·{z})"
    hit = baselines["L2"].get((sgg_nm, z))
    if hit:
        return hit, f"시군구·용도({sgg_nm}·{z})"
    hit = baselines["L3"].get(z)
    if hit:
        return hit, f"용도({z})"
    return None, None


def undervaluation(baselines, sgg_nm, umd_nm, zoning, price_per_pyeong_value, area_m2=None):
    """해당 평당가가 기준선(중앙값) 대비 몇 % 싼지 반환.
    양수 = 저평가(싸다). dict 또는 None."""
    summ, level = lookup(baselines, sgg_nm, umd_nm, zoning, area_m2)
    if not summ:
        return None
    med = summ["median"]
    pct_below = (med - price_per_pyeong_value) / med * 100 if med else None
    return {
        "median": med,
        "p25": summ["p25"],
        "n": summ["n"],
        "level": level,
        "input_ppp": price_per_pyeong_value,
        "pct_below_median": pct_below,
    }
