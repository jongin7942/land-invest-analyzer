"""가격 스냅샷 — 필터 + 대표가격 + 근거를 하나의 Calc 로 묶는다.

월별로 쌓으면 요구사항 4(Historical Price Ratio)가 저절로 따라온다.
별도 수집 없이 같은 실거래를 다른 창(window)으로 다시 집계하기만 하면 된다.

전세가율은 같은 시점·같은 면적밴드의 매매 스냅샷과 전세 스냅샷에서 나온다.
서로 다른 면적이나 다른 시점을 섞지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass

from apt_engine import units
from apt_engine.price import outlier, representative
from apt_engine.trace import Calc, Evidence

MOLIT_TRADE = Evidence(source="국토교통부 아파트 매매 실거래가 상세",
                       url="https://www.data.go.kr")
MOLIT_RENT = Evidence(source="국토교통부 아파트 전월세 실거래가",
                      url="https://www.data.go.kr")

DEFAULT_WINDOW_MONTHS = 6


def ym_window(as_of_ym: str, months: int) -> tuple[str, str]:
    """as_of 기준 최근 N개월 창. (시작 YYYYMM, 끝 YYYYMM) — 양끝 포함."""
    if len(as_of_ym) != 6 or not as_of_ym.isdigit():
        raise ValueError(f"기준월은 YYYYMM 형식이어야 합니다: {as_of_ym!r}")
    if months < 1:
        raise ValueError(f"창 길이는 1개월 이상이어야 합니다: {months}")
    y, m = int(as_of_ym[:4]), int(as_of_ym[4:])
    total = y * 12 + (m - 1) - (months - 1)
    return f"{total // 12:04d}{total % 12 + 1:02d}", as_of_ym


def in_window(rows: list[dict], ymd_key: str, start_ym: str, end_ym: str) -> list[dict]:
    return [r for r in rows if start_ym <= str(r[ymd_key])[:6] <= end_ym]


@dataclass(frozen=True)
class Snapshot:
    """한 단지·한 면적밴드·한 시점의 대표가격."""
    value: int | None                  # 원. 표본이 없으면 None
    method: str
    sample_n: int
    excluded_n: int
    exclusions: dict[str, int]
    relaxed: list[str]
    confidence: str | None
    quartiles: dict[str, int]
    as_of_ym: str
    window_months: int
    calc: Calc

    @property
    def usable(self) -> bool:
        return self.value is not None


def _build(rows: list[dict], *, as_of_ym: str, window_months: int,
           price_key: str, ymd_key: str, jeonse: bool, label: str,
           evidence: Evidence, preferred: str) -> Snapshot:
    start_ym, end_ym = ym_window(as_of_ym, window_months)
    windowed = in_window(rows, ymd_key, start_ym, end_ym)
    filtered = outlier.filter_normal(windowed, jeonse=jeonse, price_key=price_key)

    values = [int(r[price_key]) for r in filtered.kept]
    excl = filtered.exclusion_counts
    excluded_n = len(filtered.excluded)

    common_inputs = {
        "기준월": as_of_ym,
        "집계창": f"{start_ym}~{end_ym} ({window_months}개월)",
        "창 안 거래": len(windowed),
        "정상거래": len(values),
        "제외": excluded_n,
    }

    if not values:
        calc = Calc(
            value=None, unit="원",
            formula=f"{label} 정상거래 0건 — 대표가격을 내지 않는다",
            inputs=common_inputs,
            intermediates={"제외사유": excl},
            evidence=(evidence,),
            grade="CONFIRMED",
        )
        return Snapshot(None, representative.MEDIAN, 0, excluded_n, excl,
                        filtered.relaxed, None, {}, as_of_ym, window_months, calc)

    value, method = representative.compute(values, preferred=preferred)
    q = representative.quartiles(values)
    confidence = representative.confidence_of(len(values))

    method_ko = "중앙값" if method == representative.MEDIAN else "절사평균(양끝 10%)"
    intermediates = {
        "제외사유": excl,
        "분포": {k: units.fmt_eok(v) for k, v in q.items()},
        "신뢰도근거": f"정상거래 {len(values)}건 → {confidence}",
    }
    if filtered.relaxed:
        # 되살렸다는 사실을 반드시 남긴다 — 조용히 포함하는 것과 다르다.
        intermediates["표본부족 완화"] = filtered.relaxed_labels

    calc = Calc(
        value=value, unit="원",
        formula=f"{label} 정상거래 {len(values)}건의 {method_ko}",
        inputs=common_inputs,
        intermediates=intermediates,
        evidence=(evidence,),
        # 실거래에서 직접 나온 값이다. 표본이 적은 건 confidence 가 말한다.
        grade="CONFIRMED",
    )
    return Snapshot(value, method, len(values), excluded_n, excl, filtered.relaxed,
                    confidence, q, as_of_ym, window_months, calc)


def build_price(trades: list[dict], *, as_of_ym: str,
                window_months: int = DEFAULT_WINDOW_MONTHS,
                preferred: str = representative.MEDIAN) -> Snapshot:
    """매매 대표가격. trades 는 **한 단지·한 면적밴드**의 거래여야 한다."""
    return _build(trades, as_of_ym=as_of_ym, window_months=window_months,
                  price_key="deal_amount", ymd_key="deal_ymd", jeonse=False,
                  label="매매", evidence=MOLIT_TRADE, preferred=preferred)


def build_jeonse(contracts: list[dict], *, as_of_ym: str,
                 window_months: int = DEFAULT_WINDOW_MONTHS,
                 preferred: str = representative.MEDIAN) -> Snapshot:
    """전세 대표 보증금. 월세 낀 계약은 앞단에서 빠진다."""
    return _build(contracts, as_of_ym=as_of_ym, window_months=window_months,
                  price_key="deposit", ymd_key="contract_ymd", jeonse=True,
                  label="전세", evidence=MOLIT_RENT, preferred=preferred)


def jeonse_ratio(price: Snapshot, jeonse: Snapshot) -> Calc | None:
    """전세가율 = 대표 전세보증금 / 대표 매매가.

    같은 시점·같은 면적밴드끼리만 계산한다. 호출부가 짝을 맞춰 넘긴다.
    """
    if not price.usable or not jeonse.usable or price.value <= 0:
        return None
    if price.as_of_ym != jeonse.as_of_ym:
        raise ValueError(
            f"기준월이 다른 스냅샷으로 전세가율을 계산할 수 없습니다: "
            f"매매 {price.as_of_ym} vs 전세 {jeonse.as_of_ym}")

    ratio = jeonse.value / price.value
    return Calc.derive(
        ratio,
        unit="ratio",
        formula="대표 전세보증금 ÷ 대표 매매가",
        sources={"대표매매가": price.calc, "대표전세가": jeonse.calc},
        intermediates={
            "매매": units.fmt_eok(price.value),
            "전세": units.fmt_eok(jeonse.value),
            "전세가율": units.fmt_pct(ratio),
            "표본": f"매매 {price.sample_n}건({price.confidence}) · "
                    f"전세 {jeonse.sample_n}건({jeonse.confidence})",
        },
    )
