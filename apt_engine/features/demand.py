"""수요 쪽 세 가지 — Buyer Pool · 실질 경쟁공급 · 대체 가용성
   (신규 지시서 §4-C·§13·§33).

셋 다 **후보 하나만 봐서는 계산되지 않는다.** "이 가격대를 살 사람이 두꺼운가"
는 다른 후보들과 비교해야 나오는 값이고, "대체할 물건이 많은가" 는 정의상
집단 개념이다. 그래서 `capital_efficiency` 와 같이 파이프라인이 후보군을
확보한 뒤에 붙인다.

§33 이 이 모듈의 존재 이유다.

> 서울 / 노원 / 강남 / 분당 … 이름 자체에 점수를 주지 않는다.
> 실제 효과를 BuyerPool · JobAccessibility · Liquidity · Supply ·
> ReplacementGap 등 **측정 가능한 Feature 로 분해한다.**

"강남이니까 좋다" 를 "이 가격대를 살 수 있는 사람이 많고, 대체재가 적고,
경쟁 공급이 없다" 로 바꾸는 것이 목적이다.

⚠ **Buyer Pool 은 지금 대리지표(proxy)다.** 진짜로 재려면 소득 분포와 대출
가능액 분포가 필요한데 둘 다 없다. 그래서 거래 활성도와 세대수로 근사하고,
그 사실을 신뢰도와 detail 에 그대로 적는다. 근사값을 실측처럼 보여주지 않는다.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field

from apt_engine import units
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features.base import Feature, Status, sample_confidence
from apt_engine.trace import Calc

# 같은 가격대로 볼 범위 (±). 좁으면 표본이 없고 넓으면 다른 상품이 섞인다.
COHORT_BAND = 0.15

# Buyer Pool 을 낼 최소 동급 후보 수
MIN_COHORT = 4

# 대체재가 이만큼 있으면 "많다" 로 본다. **판정 기준이지 관측이 아니다.**
REPLACEMENT_MANY = 12

# 대리지표라서 신뢰도에 상한을 건다. 실측이 아니다.
PROXY_CONFIDENCE_CAP = 0.45

PROXY_NOTE = ("Buyer Pool 은 소득·대출가능액 분포가 없어 거래 활성도와 "
              "세대수로 근사한 값입니다. 실측이 아니라서 신뢰도에 상한을 둡니다")
THRESHOLD_NOTE = ("대체 가용성·경쟁공급 경계는 판정 기준입니다. "
                  "백테스트(§21)가 대체합니다")


@dataclass(frozen=True)
class Cohort:
    """같은 가격대·같은 생활권의 후보들. 이름은 들어오지 않는다(§1)."""
    complex_id: int
    price: int
    households: int | None
    sample_n: int
    lawd_cd: str

    @property
    def liquidity(self) -> float | None:
        """세대수 대비 거래 활성도. 세대수를 모르면 만들지 않는다."""
        if not self.households or self.households <= 0:
            return None
        return self.sample_n / self.households


@dataclass(frozen=True)
class Market:
    """후보군 전체. 파이프라인이 한 번 만들어 모든 후보에 재사용한다."""
    rows: list[Cohort] = field(default_factory=list)
    as_of: str = ""

    def peers(self, price: int, *, same_region: str | None = None
              ) -> list[Cohort]:
        """같은 가격대(±15%)의 다른 후보들."""
        lo, hi = price * (1 - COHORT_BAND), price * (1 + COHORT_BAND)
        return [r for r in self.rows
                if lo <= r.price <= hi
                and (same_region is None or r.lawd_cd == same_region)]

    @property
    def median_liquidity(self) -> float | None:
        values = [r.liquidity for r in self.rows if r.liquidity is not None]
        return statistics.median(values) if values else None


def load_market(conn: sqlite3.Connection, rows, *, as_of: str) -> Market:
    """Universe 행에서 Market 을 만든다. 추가 조회 없이 이미 읽은 것만 쓴다."""
    return Market([Cohort(r.complex_id, r.representative_price,
                          r.apt_households, r.sample_n, r.lawd_cd)
                   for r in rows], as_of)


# ── Buyer Pool ───────────────────────────────────────────────────────

def buyer_pool(market: Market, *, price: int, lawd_cd: str | None,
               households: int | None, sample_n: int) -> Feature:
    """이 가격대를 살 수 있는 수요층의 두께 (§4-C·§33).

    **대리지표다.** 소득 분포가 없으니 두 가지로 근사한다.

        ① 같은 가격대에서 실제로 거래가 얼마나 일어나는가 (활성도)
        ② 같은 가격대의 물건이 몇 개나 있는가 (시장의 두께)

    ②가 클수록 좋다는 게 직관과 반대로 보일 수 있는데, 여기서는
    "이 가격대에 시장이 형성돼 있다" 는 뜻이다. 대체재가 많다는 뜻이기도
    해서 그건 `replacement_availability` 가 따로 감점한다 — 한 신호를 두 번
    쓰지 않기 위해 축을 나눴다(§45).
    """
    peers = market.peers(price, same_region=lawd_cd)
    if len(peers) < MIN_COHORT:
        # 지역을 넓혀 다시 본다. 그래도 모자라면 만들지 않는다.
        peers = market.peers(price)
    if len(peers) < MIN_COHORT:
        return Feature.missing(
            "buyer_pool",
            f"같은 가격대 후보가 {len(peers)}개뿐입니다(최소 {MIN_COHORT}개). "
            f"수요층 두께를 근사할 표본이 없습니다")

    liquidities = [p.liquidity for p in peers if p.liquidity is not None]
    own_liquidity = (sample_n / households) if (households and households > 0) else None

    parts: dict[str, float] = {}
    if own_liquidity is not None and liquidities:
        median = statistics.median(liquidities)
        if median > 0:
            # 동급 대비 거래 활성도. 1.0 이면 평균.
            parts["거래 활성도"] = max(0.0, min(1.0, own_liquidity / median / 2))
    if liquidities:
        parts["가격대 시장 형성"] = max(0.0, min(1.0, len(peers) / 20.0))

    if not parts:
        return Feature.missing(
            "buyer_pool",
            "세대수를 몰라 거래 활성도를 낼 수 없습니다 — "
            "거래건수만으로는 큰 단지가 무조건 유리해집니다")

    value = sum(parts.values()) / len(parts)
    confidence = min(PROXY_CONFIDENCE_CAP,
                     sample_confidence(len(peers), full_at=15))
    return Feature(
        "buyer_pool", value, "0~1", confidence, Status.OK,
        {"동급 후보": f"{len(peers)}개 (±{COHORT_BAND:.0%})",
         "구성": {k: f"{v:.2f}" for k, v in parts.items()},
         "주의": PROXY_NOTE,
         "지역명": "지역 이름은 쓰지 않습니다 — 측정값으로만 봅니다(§33)"},
        Calc(value=value, unit="0~1",
             formula="동급 대비 거래 활성도 · 가격대 시장 형성 정도의 평균 (대리지표)",
             intermediates={"동급 수": len(peers), "구성": parts},
             grade="ESTIMATED"))


# ── 실질 경쟁 공급 ───────────────────────────────────────────────────

def effective_supply_risk(fs_values: dict[str, float | None], *,
                          cliff: float | None = None) -> Feature:
    """경쟁 공급 위험을 **한 곳으로 모은다** (§13·§45).

    전에는 `supply_ratio_1y/2y/3y/5y` 가 각각 ALPHA 모델과 Kill 규칙 양쪽에
    흩어져 있었다. 같은 공급 신호가 여러 번 세어졌다. 이제 공급 관련 감점은
    이 Feature **하나**로만 나가고, 원시 비율들은 CONTEXT 로 내렸다.

    가까운 공급에 더 큰 무게를 준다 — 5년 뒤 입주는 지금 가격에 거의 영향이 없다.
    """
    weights = {"supply_ratio_1y": 1.0, "supply_ratio_2y": 0.7,
               "supply_ratio_3y": 0.4, "supply_ratio_5y": 0.15}
    known = {k: v for k, v in fs_values.items()
             if k in weights and v is not None}
    if not known:
        return Feature.missing(
            "effective_supply_risk",
            "공급 비율을 하나도 구하지 못했습니다 — 공급 위험을 0 으로 "
            "두지 않습니다")

    # **가장 임박한 위험**을 쓴다. 알려진 항만으로 가중평균하면 5년치 하나만
    # 아는 후보가 1년치 하나만 아는 후보와 같은 위험이 된다 — 분모가 같이
    # 줄기 때문이다. 그렇다고 모르는 항을 0 으로 채우면 위험이 과소평가된다.
    # 최댓값을 쓰면 둘 다 피한다.
    weighted = max(known[k] * weights[k] for k in known)
    # 비율을 0~1 위험으로. 10% 를 최대 위험으로 본다.
    value = max(0.0, min(1.0, weighted / 0.10))

    detail = {
        "구성": {k: f"{v:.1%}" for k, v in sorted(known.items())},
        "가중": "가장 임박한 위험을 씁니다 (1년 ×1.0 · 5년 ×0.15)",
        "주의": "공급 관련 감점은 이 값 하나로만 나갑니다(§45 중복 금지)",
        "기준": THRESHOLD_NOTE,
    }
    if cliff is not None and cliff > 0.5:
        # §13 공급 절벽 — 물량이 끊기면 그 뒤는 위험이 아니라 기회다.
        value *= 0.6
        detail["공급 절벽"] = f"{cliff:.2f} — 물량이 끊겨 위험을 낮췄습니다"

    missing = [k for k in weights if k not in known]
    if missing:
        detail["못 구한 구간"] = ", ".join(missing)

    return Feature(
        "effective_supply_risk", value, "0~1",
        sample_confidence(len(known), full_at=4), Status.OK, detail,
        Calc(value=value, unit="0~1",
             formula="기간별 공급비율의 가중평균 ÷ 10% (상한 1.0)",
             intermediates=known, grade="ESTIMATED"))


# ── 대체 가용성 ──────────────────────────────────────────────────────

def replacement_availability(market: Market, *, price: int,
                             required_equity: int | None,
                             lawd_cd: str | None) -> Feature:
    """같은 돈으로 살 수 있는 다른 물건이 얼마나 있나 (§4-C·§24).

    많으면 **가격이 안 오른다.** 굳이 이걸 살 이유가 없기 때문이다.
    등록부에서 `higher_is_better=False` 인 이유다.

    실투자금 기준이 있으면 그걸 쓰고, 없으면 매매가로 근사한다.
    근사했다는 사실을 남긴다.
    """
    basis = "실투자금"
    if required_equity is None:
        basis = "매매가(실투자금 미상)"

    peers = market.peers(price, same_region=lawd_cd)
    wider = market.peers(price)
    if not wider:
        return Feature.missing(
            "replacement_availability",
            "같은 가격대 후보를 찾지 못했습니다")

    # 자기 자신은 대체재가 아니다.
    local = max(0, len(peers) - 1)
    total = max(0, len(wider) - 1)

    value = min(1.0, total / REPLACEMENT_MANY)
    return Feature(
        "replacement_availability", value, "0~1",
        sample_confidence(len(wider), full_at=REPLACEMENT_MANY), Status.OK,
        {"같은 생활권 대체재": f"{local}개",
         "전체 대체재": f"{total}개 (±{COHORT_BAND:.0%})",
         "기준": basis,
         "해석": ("대체재가 많아 굳이 이걸 살 이유가 적습니다"
                if value > 0.6 else "대체재가 적습니다"),
         "주의": "많을수록 감점입니다 — 대체재가 많으면 가격이 안 오릅니다"},
        Calc(value=value, unit="0~1",
             formula=f"같은 가격대 후보 수 ÷ {REPLACEMENT_MANY} (상한 1.0)",
             intermediates={"같은 생활권": local, "전체": total, "기준": basis},
             grade="ESTIMATED"))


def all_features(market: Market, *, price: int, lawd_cd: str | None,
                 households: int | None, sample_n: int,
                 required_equity: int | None,
                 supply_values: dict[str, float | None],
                 cliff: float | None = None) -> list[Feature]:
    """세 개를 한 번에. 파이프라인이 후보마다 부른다."""
    return [
        buyer_pool(market, price=price, lawd_cd=lawd_cd,
                   households=households, sample_n=sample_n),
        effective_supply_risk(supply_values, cliff=cliff),
        replacement_availability(market, price=price,
                                 required_equity=required_equity,
                                 lawd_cd=lawd_cd),
    ]
