"""Leader 망 · 전달 실패 · 회복가능 할인 · Next Node
   (신규 지시서 §10·§11·§12·§13·§14·§15·§16).

이 모듈이 막으려는 실수는 지시서가 §49-7 로 못박은 것이다.

> Leader 가 올랐다는 이유만으로 Follower 추천 금지.

전형적인 오판은 이렇게 생긴다.

    잠실 84 가 1년에 +25% 올랐다.
    이 단지는 잠실 대비 40% 싸다.
    → 따라 오를 것이다. 매수.

여기 빠진 질문이 둘이다.

    ① 이 단지를 사는 사람과 잠실을 사는 사람이 같은 사람인가? (Buyer Overlap)
    ② 지난 다섯 번의 잠실 상승 때 이 단지는 따라 올랐나? (Transmission)

②가 계속 "아니오" 였다면 그 40% 는 **닫힐 격차가 아니라 구조적 할인**이다.
같은 값이 앞으로도 유지될 이유가 있다는 뜻이고, 그걸 저평가로 읽으면 안 된다.

    관측 할인 = 구조적 할인 + 회복가능 할인
    Alpha 에는 **회복가능 할인만** 쓴다.                       (§13)

§14 는 한 걸음 더 간다. 오래 싼 채로 격차가 안 닫혔으면 **감점**한다.
그리고 왜 안 닫혔는지를 12개 항목으로 진단한다 — 이유가 설명되면 그만큼을
구조적 할인으로 옮긴다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features import bands as bands_mod
from apt_engine.features.base import (Feature, Status, combine,
                                      sample_confidence)
from apt_engine.trace import Calc

# §11 Leader 다섯 종류
LOCAL = "LOCAL"                  # 같은 생활권에서 가장 비싼 곳
PRICE = "PRICE"                  # 같은 가격대 상위
FLOW = "FLOW"                    # 거래가 먼저 붙는 곳
CAPITAL_COHORT = "CAPITAL_COHORT"  # 같은 자기자본으로 살 수 있는 상위
METRO = "METRO"                  # 수도권 전체 선도
LEADER_KINDS = (LOCAL, PRICE, FLOW, CAPITAL_COHORT, METRO)

LEADER_LABEL = {
    LOCAL: "같은 생활권 선도단지",
    PRICE: "같은 가격대 상위",
    FLOW: "거래가 먼저 붙는 단지",
    CAPITAL_COHORT: "같은 자기자본 코호트 상위",
    METRO: "수도권 선도",
}

# Buyer Overlap 을 이만큼도 못 보면 Leader 로 인정하지 않는다(§11).
MIN_BUYER_OVERLAP = 0.30

# §12 전달 실패 판정. **관측이 아니라 기준이다** — 백테스트가 대체한다.
LEADER_RISE_MEANINGFUL = 0.08     # Leader 가 이만큼 올랐는데
FOLLOWER_RESPONSE = 0.02          # Follower 가 이만큼도 안 움직였으면 무반응
FAILURE_MONTHS = 12               # 그 상태가 이만큼 이어지면 전달 실패

# §14 오래 싼 것에 대한 감점 시작점
PERSISTENT_MONTHS = 24

# §15 Money Arrival Depth
DEPTH_LABEL = {1: "선도만 움직임", 2: "상위 추종", 3: "중위 추종", 4: "하위 꼬리"}
CHASE_DEPTH = 4

THRESHOLD_NOTE = ("전달 실패·회복가능 비율의 경계는 판정 기준이지 관측된 분포가 "
                  "아닙니다. 백테스트(§21)가 대체합니다")

# §14 "Why Not Yet?" 진단 12항목. 이 중 하나라도 설명되면 구조적 할인으로 옮긴다.
WHY_NOT_YET = (
    "교통", "직장 접근성", "학군", "생활권", "상품성", "주차",
    "공급", "Buyer Pool", "Replacement Availability", "전세 약세",
    "가격대 수요 Ceiling", "Relevant Leader mismatch",
)


@dataclass(frozen=True)
class Leader:
    leader_id: int
    kind: str
    buyer_overlap: float | None
    basis: str

    @property
    def relevant(self) -> bool:
        """§11 — 가깝다고 Leader 가 아니다. 겹침이 확인돼야 한다."""
        return (self.buyer_overlap is not None
                and self.buyer_overlap >= MIN_BUYER_OVERLAP)

    @property
    def label(self) -> str:
        overlap = ("확인 불가" if self.buyer_overlap is None
                   else f"{self.buyer_overlap:.0%}")
        return f"{LEADER_LABEL[self.kind]} #{self.leader_id} (겹침 {overlap})"


def load_leaders(conn: sqlite3.Connection, complex_id: int, area_band: str, *,
                 as_of: cutoff_mod.AsOf) -> list[Leader]:
    """그 시점에 알 수 있었던 Leader 관계.

    **59㎡ 와 84㎡ 의 Leader 망은 분리한다**(§11). 면적이 다르면 사는 사람이
    다르고, 그래서 가격이 같이 움직일 이유도 다르다.
    """
    with cutoff_mod.guard(conn, as_of.observable) as g:
        rows = g.execute(
            "SELECT leader_id, leader_kind, buyer_overlap, overlap_basis "
            "  FROM leader_link "
            " WHERE follower_id = ? AND area_band = ? AND as_of <= ? "
            " ORDER BY as_of DESC",
            (complex_id, area_band, as_of.observable.day)).fetchall()
    seen: set[tuple[int, str]] = set()
    out: list[Leader] = []
    for r in rows:
        key = (int(r["leader_id"]), r["leader_kind"])
        if key in seen:
            continue
        seen.add(key)
        out.append(Leader(int(r["leader_id"]), r["leader_kind"],
                          r["buyer_overlap"], r["overlap_basis"] or ""))
    return out


def relevant_leaders(leaders: list[Leader]) -> list[Leader]:
    return [l for l in leaders if l.relevant]


# ── §12 Leader Transmission Failure ──────────────────────────────────

@dataclass(frozen=True)
class Transmission:
    leader_rise: float | None
    follower_rise: float | None
    months_no_response: int | None
    buyer_overlap: float | None
    failure: float | None
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.failure is not None

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        return (f"Leader {self.leader_rise:+.1%} vs Follower "
                f"{self.follower_rise:+.1%} · 무반응 {self.months_no_response}개월 "
                f"→ 전달실패 {self.failure:.2f}")


def transmission(follower: bands_mod.BandSeries, leader: bands_mod.BandSeries,
                 *, buyer_overlap: float | None, months: int = 12
                 ) -> Transmission:
    """Leader 는 올랐는데 Follower 는 안 움직였는가 (§12).

        TransmissionFailure = LeaderPriceRise
                            × DurationWithoutFollowerResponse
                            × BuyerOverlapAdjustment

    겹침이 클수록 "그런데도 안 따라왔다" 가 더 무겁다. 겹침이 작으면 애초에
    따라올 이유가 없었으므로 실패로 세지 않는다.
    """
    lead_rise, why_l = bands_mod._band_change(leader, months, lambda p: p.p50)
    foll_rise, why_f = bands_mod._band_change(follower, months, lambda p: p.p50)
    if lead_rise is None:
        return Transmission(None, None, None, buyer_overlap, None,
                            f"Leader 가격변화를 못 구했습니다: {why_l}")
    if foll_rise is None:
        return Transmission(lead_rise, None, None, buyer_overlap, None,
                            f"Follower 가격변화를 못 구했습니다: {why_f}")
    if buyer_overlap is None:
        return Transmission(lead_rise, foll_rise, None, None, None,
                            "Buyer Overlap 을 몰라 전달 실패를 판정하지 않습니다 — "
                            "겹침 없이 '안 따라왔다' 는 의미가 없습니다")

    if lead_rise < LEADER_RISE_MEANINGFUL:
        return Transmission(lead_rise, foll_rise, 0, buyer_overlap, 0.0,
                            "Leader 가 크게 오르지 않아 전달을 논할 상황이 아닙니다")

    responded = foll_rise >= FOLLOWER_RESPONSE
    months_no = 0 if responded else _months_without_response(follower, months)

    if responded:
        return Transmission(lead_rise, foll_rise, 0, buyer_overlap, 0.0,
                            "Follower 가 반응했습니다")

    duration = min(1.0, months_no / FAILURE_MONTHS)
    failure = min(1.0, lead_rise * duration * buyer_overlap * 3)
    return Transmission(lead_rise, foll_rise, months_no, buyer_overlap, failure)


def _months_without_response(series: bands_mod.BandSeries, window: int) -> int:
    """중앙값이 마지막으로 의미 있게 오른 뒤 몇 달이 지났나."""
    usable = series.usable
    if len(usable) < 2:
        return window
    latest = usable[0].p50
    for i, p in enumerate(usable):
        if p.p50 and latest and (latest - p.p50) / p.p50 >= FOLLOWER_RESPONSE:
            return i
    return len(usable)


# ── §13 Recoverable Discount Ratio ───────────────────────────────────

@dataclass(frozen=True)
class Discount:
    observed: float | None
    structural: float | None
    recoverable: float | None
    ratio: float | None
    why_not_yet: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.ratio is not None

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        return (f"관측 할인 {self.observed:.1%} = 구조적 {self.structural:.1%} "
                f"+ 회복가능 {self.recoverable:.1%} (RDR {self.ratio:.0%})")


def decompose(observed_discount: float | None, *,
              transmission_failure: float | None,
              why_not_yet: list[str] | None = None,
              neighbour_confirmation: float | None = None) -> Discount:
    """관측 할인을 구조적 / 회복가능으로 나눈다 (§13·§14).

    구조적 비중을 키우는 것:
      * 전달 실패가 반복됐다 (§12)
      * "왜 아직?" 진단에서 구조적 이유가 확인됐다 (§14)

    회복가능 비중을 키우는 것:
      * 같은 생활권 비교단지가 함께 움직인다 (§10)

    **전달 실패를 모르면 분해하지 않는다.** 모르는 상태에서 전부 회복가능으로
    두면 "Spread 가 크다 = 기회가 크다" 라는 §12 가 금지한 결론이 나온다.
    """
    reasons = list(why_not_yet or [])
    if observed_discount is None:
        return Discount(None, None, None, None, reasons,
                        "관측 할인을 구하지 못했습니다")
    if observed_discount <= 0:
        return Discount(observed_discount, 0.0, 0.0, 0.0, reasons,
                        "Leader 보다 싸지 않아 할인이 없습니다")
    if transmission_failure is None:
        return Discount(observed_discount, None, None, None, reasons,
                        "전달 실패를 판정하지 못해 할인을 분해하지 않습니다 — "
                        "분해 없이 전부 회복가능으로 보면 §12 가 금지한 "
                        "'Spread 가 크다 = 기회가 크다' 가 됩니다")

    structural_share = min(1.0, transmission_failure)
    # 구조적 이유가 하나씩 확인될 때마다 구조적 비중을 올린다(§14).
    if reasons:
        structural_share = min(1.0, structural_share + 0.10 * len(reasons))
    # 이웃이 함께 움직이면 회복 쪽에 무게를 준다(§10). **Alpha 가 아니라 배분이다.**
    if neighbour_confirmation is not None:
        structural_share = max(0.0, structural_share
                               - 0.20 * neighbour_confirmation)

    structural = observed_discount * structural_share
    recoverable = observed_discount - structural
    ratio = recoverable / observed_discount if observed_discount else 0.0
    return Discount(observed_discount, structural, recoverable, ratio, reasons)


def recoverable_feature(d: Discount) -> Feature:
    if not d.known:
        return Feature.missing("recoverable_discount_ratio", d.reason)
    detail = {
        "관측 할인": f"{d.observed:.1%}",
        "구조적": f"{d.structural:.1%}",
        "회복가능": f"{d.recoverable:.1%}",
        "주의": "Alpha 에는 회복가능 부분만 씁니다(§13)",
    }
    if d.why_not_yet:
        detail["구조적 이유"] = ", ".join(d.why_not_yet)
    return Feature(
        "recoverable_discount_ratio", d.ratio, "0~1", 0.6, Status.OK, detail,
        Calc(value=d.ratio, unit="0~1",
             formula="회복가능 할인 ÷ 관측 할인",
             intermediates={"관측": d.observed, "구조적": d.structural,
                            "회복가능": d.recoverable},
             grade="ESTIMATED"))


def transmission_feature(t: Transmission) -> Feature:
    if not t.known:
        return Feature.missing("transmission_failure", t.reason)
    return Feature(
        "transmission_failure", t.failure, "0~1",
        combine(0.6, 1.0 if t.buyer_overlap else 0.3), Status.OK,
        {"Leader 상승": f"{t.leader_rise:+.1%}",
         "Follower 상승": f"{t.follower_rise:+.1%}",
         "무반응": f"{t.months_no_response}개월",
         "Buyer Overlap": f"{t.buyer_overlap:.0%}" if t.buyer_overlap else "확인 불가",
         "해석": ("Leader 가 올랐는데 안 따라왔습니다 — 구조적 할인 가능성"
                if t.failure > 0.3 else "전달이 되고 있습니다"),
         "기준": THRESHOLD_NOTE},
        Calc(value=t.failure, unit="0~1",
             formula="Leader상승 × 무반응기간 × BuyerOverlap",
             intermediates={"leader": t.leader_rise, "follower": t.follower_rise,
                            "무반응개월": t.months_no_response,
                            "겹침": t.buyer_overlap},
             grade="ESTIMATED"))


def persistent_cheapness(*, months_cheap: int | None,
                         gap_closed: float | None) -> Feature:
    """§14 — 오래 싼 것 자체는 저평가 증거가 아니다.

    24개월 넘게 싼데 격차가 안 닫혔으면 감점한다. 닫히고 있으면 감점하지 않는다.
    """
    if months_cheap is None:
        return Feature.missing(
            "persistent_cheapness",
            "얼마나 오래 쌌는지 몰라 판정하지 않습니다")
    if months_cheap < PERSISTENT_MONTHS:
        return Feature("persistent_cheapness", 0.0, "0~1", 0.6, Status.OK,
                       {"기간": f"{months_cheap}개월",
                        "해석": f"{PERSISTENT_MONTHS}개월 미만이라 감점하지 않습니다"})
    over = (months_cheap - PERSISTENT_MONTHS) / PERSISTENT_MONTHS
    value = min(1.0, 0.3 + 0.7 * min(1.0, over))
    if gap_closed is not None and gap_closed > 0:
        # 격차가 닫히는 중이면 감점을 줄인다.
        value *= max(0.0, 1.0 - min(1.0, gap_closed * 5))
    return Feature(
        "persistent_cheapness", value, "0~1", 0.6, Status.OK,
        {"기간": f"{months_cheap}개월째 쌈",
         "격차 축소": (f"{gap_closed:+.1%}" if gap_closed is not None
                   else "확인 불가"),
         "해석": "오래 싸다는 사실 자체는 저평가 증거가 아닙니다(§14)",
         "다음": f"왜 안 닫혔는지 진단하세요: {', '.join(WHY_NOT_YET[:4])} 등"},
        Calc(value=value, unit="0~1",
             formula="싼 기간이 길수록 감점, 격차가 닫히는 중이면 감면",
             intermediates={"개월": months_cheap, "격차축소": gap_closed},
             grade="ESTIMATED"))


# ── §10 Neighbour Confirmation ───────────────────────────────────────

def neighbour_confirmation(*, moving: int, valid: int) -> Feature:
    """같은 생활권 비교단지가 함께 움직이는가 (§10).

    **이 값을 Alpha 로 크게 가산하지 않는다.** Movement 의 신뢰도를 올리는
    용도다 — 단일 단지의 이상 거래를 시장 확산으로 오인하지 않기 위해서다.
    등록부에서도 role 이 CONFIDENCE 다.
    """
    if valid <= 0:
        return Feature.missing(
            "neighbour_confirmation",
            "비교 가능한 같은 생활권 단지가 없습니다")
    value = moving / valid
    return Feature(
        "neighbour_confirmation", value, "0~1",
        sample_confidence(valid, full_at=5), Status.OK,
        {"움직이는 단지": f"{moving}/{valid}",
         "주의": "Alpha 가 아니라 신뢰도 조정용입니다(§10). "
               "단일 단지의 이상 거래를 시장 확산으로 오인하지 않기 위해서입니다"},
        Calc(value=value, unit="0~1",
             formula="움직인 비교단지 수 ÷ 유효 비교단지 수",
             intermediates={"moving": moving, "valid": valid},
             grade="CONFIRMED"))


# ── §15 Money Arrival Depth · §16 Next Node ──────────────────────────

@dataclass(frozen=True)
class Ladder:
    """생활권 가격 사다리 한 줄. rank 가 작을수록 위(비쌈)."""
    nodes: list[tuple[int, int, float | None]] = field(default_factory=list)
    # (rank, complex_id, 최근 상승률)

    def moved(self, threshold: float = 0.05) -> list[int]:
        return [rank for rank, _, rise in self.nodes
                if rise is not None and rise >= threshold]


def money_arrival_depth(ladder: Ladder, *, self_rank: int | None) -> Feature:
    """돈이 사다리 몇 번째 칸까지 왔나 (§15).

    Depth 4(하위 꼬리)까지 왔으면 Chase Risk 다 — 남은 칸이 없다.
    **가장 싼 칸을 자동으로 추천하지 않는다**(§16).
    """
    if not ladder.nodes:
        return Feature.missing("money_arrival_depth",
                               "생활권 가격 사다리가 없습니다")
    moved = ladder.moved()
    if not moved:
        return Feature("money_arrival_depth", 0.0, "칸", 0.5, Status.OK,
                       {"해석": "아직 사다리에서 움직인 칸이 없습니다"})
    depth = max(moved)
    detail = {"도달 칸": f"{depth} ({DEPTH_LABEL.get(depth, '더 아래')})",
              "내 칸": self_rank if self_rank is not None else "확인 불가",
              "주의": "Depth 4 이후는 Chase Risk 입니다(§15)"}
    if depth >= CHASE_DEPTH:
        detail["경고"] = "돈이 꼬리까지 왔습니다 — 남은 칸이 거의 없습니다"
    return Feature(
        "money_arrival_depth", float(depth), "칸",
        sample_confidence(len(ladder.nodes), full_at=5), Status.OK, detail,
        Calc(value=float(depth), unit="칸",
             formula="움직인 칸 중 가장 아래",
             intermediates={"움직인 칸": moved}, grade="CONFIRMED"))


def next_node(ladder: Ladder, *, self_rank: int | None,
              buyer_overlap: float | None, remaining_gap: float | None,
              early_band_migration: float | None,
              transmission_probability: float | None) -> Feature:
    """이미 오른 칸 **바로 아래**이면서 아직 초기인가 (§16).

        NextNodeScore = Leader/Lifezone movement
                      × BuyerOverlap × RemainingGap
                      × TransmissionProbability × EarlyBandMigration

    다섯 중 하나라도 없으면 점수를 만들지 않는다. **가장 싼 꼬리를 자동으로
    추천하지 않는다** — 싸다는 것은 다섯 항목 중 하나(RemainingGap)일 뿐이다.
    """
    parts = {
        "BuyerOverlap": buyer_overlap,
        "RemainingGap": remaining_gap,
        "EarlyBandMigration": early_band_migration,
        "TransmissionProbability": transmission_probability,
    }
    missing = [k for k, v in parts.items() if v is None]
    if missing:
        return Feature.missing(
            "next_node_score",
            f"구성요소가 없습니다: {', '.join(missing)}. "
            f"싸다는 것만으로 Next Node 로 보지 않습니다(§16)")
    if self_rank is None or not ladder.nodes:
        return Feature.missing("next_node_score",
                               "사다리에서 내 위치를 몰라 판정하지 않습니다")

    moved = ladder.moved()
    if not moved:
        return Feature("next_node_score", 0.0, "0~1", 0.4, Status.OK,
                       {"해석": "위 칸이 아직 안 움직였습니다 — Next Node 가 아닙니다"})

    deepest = max(moved)
    # 바로 아래 칸이어야 한다. 두 칸 이상 아래는 아직 순서가 아니다.
    adjacency = 1.0 if self_rank == deepest + 1 else (
        0.5 if self_rank == deepest + 2 else 0.0)
    value = (adjacency * buyer_overlap * max(0.0, remaining_gap)
             * transmission_probability * early_band_migration)
    return Feature(
        "next_node_score", min(1.0, value), "0~1",
        combine(0.6, sample_confidence(len(ladder.nodes), full_at=5)),
        Status.OK,
        {"움직인 가장 아래 칸": deepest, "내 칸": self_rank,
         "인접도": adjacency,
         "구성": {k: f"{v:.2f}" for k, v in parts.items()},
         "주의": "가장 싼 칸을 자동으로 추천하지 않습니다(§16)"},
        Calc(value=min(1.0, value), unit="0~1",
             formula="인접도 × BuyerOverlap × RemainingGap "
                     "× TransmissionProbability × EarlyBandMigration",
             intermediates=parts, grade="ESTIMATED"))


def save_transmission(conn: sqlite3.Connection, complex_id: int, area_band: str,
                      *, as_of: str, t: Transmission, d: Discount) -> None:
    conn.execute(
        "INSERT INTO transmission_state (follower_id, area_band, as_of, "
        " leader_rise_12m, follower_rise_12m, months_no_response, buyer_overlap, "
        " observed_discount, structural_discount, recoverable_discount, "
        " recoverable_ratio, transmission_failure, why_not_yet_json, "
        " unknown_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(follower_id, area_band, as_of) DO UPDATE SET "
        " leader_rise_12m=excluded.leader_rise_12m, "
        " follower_rise_12m=excluded.follower_rise_12m, "
        " recoverable_ratio=excluded.recoverable_ratio, "
        " transmission_failure=excluded.transmission_failure, "
        " unknown_reason=excluded.unknown_reason",
        (complex_id, area_band, as_of, t.leader_rise, t.follower_rise,
         t.months_no_response, t.buyer_overlap, d.observed, d.structural,
         d.recoverable, d.ratio, t.failure,
         json.dumps(d.why_not_yet, ensure_ascii=False) if d.why_not_yet else None,
         None if d.known else (d.reason or t.reason or "사유 미기록")))


def leader_exhaustion(leader_series, *, months: int = 24) -> Feature:
    """Relevant Leader 가 이미 소진됐는가 (§4-D).

    Follower 를 사는 논리는 "Leader 가 올랐으니 이것도 따라 오른다" 인데,
    **Leader 자신이 이미 꼭대기면 따라갈 자리가 없다.** 그러면 Follower 의
    상승 여지도 같이 사라진다.

    Leader 의 역사적 가격 위치로 본다. 100% 에 가까우면 소진이다.
    """
    from apt_engine.features import bands as bands_mod

    usable = leader_series.usable if leader_series else []
    if len(usable) < 12:
        return Feature.missing(
            "leader_exhaustion",
            f"Leader 관측이 {len(usable)}개월뿐입니다(최소 12개월)")

    prices = [p.p50 for p in usable[:months] if p.p50]
    if len(prices) < 12:
        return Feature.missing("leader_exhaustion",
                               "Leader 중앙값이 있는 달이 모자랍니다")
    now = prices[0]
    below = sum(1 for p in prices if p < now)
    value = below / len(prices)
    return Feature(
        "leader_exhaustion", value, "0~1",
        sample_confidence(len(prices), full_at=months), Status.OK,
        {"Leader 가격 위치": f"과거 {len(prices)}개월 중 상위 "
                          f"{(1 - value):.0%}",
         "해석": ("Leader 가 이미 꼭대기입니다 — 따라갈 자리가 없습니다"
                if value > 0.85 else "Leader 에게 아직 여지가 있습니다"),
         "주의": "높을수록 감점입니다. Follower 논리의 전제가 무너집니다"},
        Calc(value=value, unit="0~1",
             formula="Leader 현재가보다 쌌던 달의 비율",
             intermediates={"관측 개월": len(prices)}, grade="CONFIRMED"))
