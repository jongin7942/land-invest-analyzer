"""Rotation Engine · 순위변경 설명 · TOP10 전체 컬럼
   (지시서 §61·§62·§64·§65).

§64 가 요구하는 것은 단순하다.

> 순위가 왜 바뀌었는지 설명한다.

어제 3위였던 게 오늘 11위가 됐다면 이유가 셋 중 하나다.

    ① 이 후보가 나빠졌다        가격이 올랐거나 공급이 잡혔거나
    ② 다른 후보가 좋아졌다      나는 그대로인데 밀렸다
    ③ 우리가 더 알게 됐다       데이터가 채워져 판단이 바뀌었다

셋은 완전히 다른 사건인데, 순위만 보면 구분이 안 된다. ②는 사실 아무 일도
안 일어난 것이고, ③은 후보가 아니라 우리가 바뀐 것이다. 그래서 점수 변화와
순위 변화를 **따로** 보고 어느 쪽인지 말한다.

§61 Rotation 은 그 위에 있다. "지금 들고 있는 것을 팔고 다른 걸 사는 게
나은가" 는 순위 비교가 아니라 **거래비용을 넘는 차이인가** 의 문제다.
취득세·중개보수·양도세를 내고도 남으면 회전이고, 아니면 그냥 순위 차이다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from apt_engine import units

# 순위가 이만큼 움직여야 '변화' 로 본다. 1~2위 흔들림은 잡음이다.
MEANINGFUL_RANK_MOVE = 3
# 점수가 이만큼 움직여야 후보 자체가 변한 것으로 본다.
MEANINGFUL_SCORE_MOVE = 3.0

# §61 회전 판정 — 거래비용을 넘는 기대 차이가 이만큼은 돼야 한다.
# **판정 기준이지 관측이 아니다.**
ROTATION_MARGIN = 0.05

THRESHOLD_NOTE = ("순위·회전 경계는 판정 기준입니다. 백테스트가 대체합니다")

# 변화 원인
SELF_WORSE = "SELF_WORSE"
SELF_BETTER = "SELF_BETTER"
OTHERS_MOVED = "OTHERS_MOVED"
MORE_KNOWN = "MORE_KNOWN"
NEW_ENTRY = "NEW_ENTRY"
DROPPED = "DROPPED"
UNCHANGED = "UNCHANGED"

CAUSE_LABEL = {
    SELF_WORSE: "이 후보 자체가 나빠졌습니다",
    SELF_BETTER: "이 후보 자체가 좋아졌습니다",
    OTHERS_MOVED: "이 후보는 그대로인데 다른 후보들이 움직였습니다",
    MORE_KNOWN: "데이터가 채워져 판단이 바뀌었습니다 — 후보가 변한 게 아닙니다",
    NEW_ENTRY: "새로 들어왔습니다",
    DROPPED: "빠졌습니다",
    UNCHANGED: "변화 없음",
}


@dataclass(frozen=True)
class Change:
    complex_id: int
    previous_rank: int | None
    current_rank: int | None
    previous_score: float | None
    current_score: float | None
    previous_confidence: float | None = None
    current_confidence: float | None = None
    cause: str = UNCHANGED
    detail: str = ""

    @property
    def rank_move(self) -> int | None:
        if self.previous_rank is None or self.current_rank is None:
            return None
        return self.previous_rank - self.current_rank      # 양수 = 올라감

    @property
    def score_move(self) -> float | None:
        if self.previous_score is None or self.current_score is None:
            return None
        return self.current_score - self.previous_score

    @property
    def label(self) -> str:
        if self.cause == NEW_ENTRY:
            return f"#{self.complex_id} 신규 {self.current_rank}위"
        if self.cause == DROPPED:
            return f"#{self.complex_id} {self.previous_rank}위 → 탈락 · {self.detail}"
        arrow = ("↑" if (self.rank_move or 0) > 0 else
                 "↓" if (self.rank_move or 0) < 0 else "→")
        score = (f"{self.score_move:+.1f}" if self.score_move is not None
                 else "?")
        return (f"#{self.complex_id} {self.previous_rank}위 {arrow} "
                f"{self.current_rank}위 (점수 {score}) — "
                f"{CAUSE_LABEL[self.cause]}")


def explain(previous: dict[int, tuple[int, float, float | None]],
            current: dict[int, tuple[int, float, float | None]],
            *, dropped_reasons: dict[int, str] | None = None) -> list[Change]:
    """순위 변화의 **원인**까지 말한다 (§64·§65).

    `previous`/`current` 는 complex_id → (순위, 점수, 신뢰도).
    """
    reasons = dropped_reasons or {}
    out: list[Change] = []

    for cid, (rank, score, conf) in sorted(current.items(),
                                           key=lambda kv: kv[1][0]):
        if cid not in previous:
            out.append(Change(cid, None, rank, None, score, None, conf,
                              NEW_ENTRY))
            continue
        prev_rank, prev_score, prev_conf = previous[cid]
        change = Change(cid, prev_rank, rank, prev_score, score, prev_conf, conf)
        cause, detail = _cause_of(change)
        out.append(Change(cid, prev_rank, rank, prev_score, score, prev_conf,
                          conf, cause, detail))

    for cid, (prev_rank, prev_score, prev_conf) in sorted(
            previous.items(), key=lambda kv: kv[1][0]):
        if cid in current:
            continue
        out.append(Change(cid, prev_rank, None, prev_score, None, prev_conf,
                          None, DROPPED,
                          reasons.get(cid, "탈락 사유가 기록되지 않았습니다")))
    return out


def _cause_of(c: Change) -> tuple[str, str]:
    """순위가 움직인 이유를 셋 중 하나로."""
    rank_move = c.rank_move or 0
    score_move = c.score_move

    if abs(rank_move) < MEANINGFUL_RANK_MOVE:
        return UNCHANGED, ""

    if score_move is None:
        return MORE_KNOWN, "이전 점수가 없어 원인을 특정하지 못했습니다"

    # 신뢰도가 크게 움직였으면 '더 알게 된 것' 이다 — 후보가 변한 게 아니다.
    if (c.previous_confidence is not None and c.current_confidence is not None
            and abs(c.current_confidence - c.previous_confidence) >= 15
            and abs(score_move) < MEANINGFUL_SCORE_MOVE):
        return MORE_KNOWN, (
            f"신뢰도 {c.previous_confidence:.0f} → {c.current_confidence:.0f}, "
            f"점수는 {score_move:+.1f} 밖에 안 움직였습니다")

    if abs(score_move) < MEANINGFUL_SCORE_MOVE:
        return OTHERS_MOVED, (
            f"점수는 {score_move:+.1f} 로 사실상 그대로인데 순위가 "
            f"{abs(rank_move)}칸 움직였습니다 — 다른 후보들이 변한 것입니다")

    return (SELF_BETTER if score_move > 0 else SELF_WORSE,
            f"점수가 {score_move:+.1f} 움직였습니다")


def load_previous(conn: sqlite3.Connection, *, run_key: str, as_of: str,
                  cash: int, horizon_years: int, profile: str,
                  list_kind: str) -> dict[int, tuple[int, float, float | None]]:
    """직전 실행의 순위·점수·신뢰도 (§64)."""
    row = conn.execute(
        "SELECT id FROM ranking_run WHERE run_key=? AND cash=? "
        "  AND horizon_years=? AND profile=? AND list_kind=? AND as_of < ? "
        " ORDER BY as_of DESC LIMIT 1",
        (run_key, cash, horizon_years, profile, list_kind, as_of)).fetchone()
    if row is None:
        return {}
    rows = conn.execute(
        "SELECT complex_id, rank, score, confidence FROM ranking_entry "
        " WHERE run_id = ?", (row[0],)).fetchall()
    return {int(r["complex_id"]): (int(r["rank"]), float(r["score"]),
                                   r["confidence"]) for r in rows}


# ── §61 Rotation ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Rotation:
    from_id: int
    to_id: int
    gain: float | None                # 기대 차이 (비용 차감 전)
    cost: float | None                # 회전 비용 (매도+매수)
    net: float | None
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def worth_it(self) -> bool | None:
        if self.net is None:
            return None
        return self.net > ROTATION_MARGIN

    @property
    def label(self) -> str:
        if self.net is None:
            return f"#{self.from_id} → #{self.to_id}: 확인 불가 — {self.reason}"
        verdict = ("회전할 만합니다" if self.worth_it else
                   "회전 비용을 넘지 못합니다 — 그냥 순위 차이입니다")
        return (f"#{self.from_id} → #{self.to_id}: "
                f"기대차 {self.gain:+.1%} − 비용 {self.cost:.1%} "
                f"= {self.net:+.1%}  {verdict}")


def rotation(*, holding_id: int, holding_return: float | None,
             candidate_id: int, candidate_return: float | None,
             sell_cost_ratio: float | None,
             buy_cost_ratio: float | None) -> Rotation:
    """지금 들고 있는 것을 팔고 저것을 사는 게 나은가 (§61).

    **거래비용을 모르면 판정하지 않는다.** 0 으로 두면 순위가 한 칸만 높아도
    회전하라는 답이 나오고, 실제로는 취득세·중개보수·양도세를 내고 나면
    거의 항상 손해다.
    """
    if holding_return is None or candidate_return is None:
        return Rotation(holding_id, candidate_id, None, None, None,
                        "두 후보의 기대수익을 모두 알아야 비교할 수 있습니다")
    if sell_cost_ratio is None or buy_cost_ratio is None:
        missing = []
        if sell_cost_ratio is None:
            missing.append("매도비용(양도세·중개보수)")
        if buy_cost_ratio is None:
            missing.append("매수비용(취득세·중개보수·법무)")
        return Rotation(
            holding_id, candidate_id, candidate_return - holding_return, None,
            None,
            f"{' · '.join(missing)}을 몰라 회전 여부를 판정하지 않습니다 — "
            f"0 으로 두면 순위가 한 칸만 높아도 회전하라는 답이 나옵니다")

    gain = candidate_return - holding_return
    cost = sell_cost_ratio + buy_cost_ratio
    return Rotation(holding_id, candidate_id, gain, cost, gain - cost,
                    notes=[THRESHOLD_NOTE])


# ── §62 TOP10 전체 컬럼 ──────────────────────────────────────────────

COLUMNS: tuple[tuple[str, str], ...] = (
    ("rank", "순위"),
    ("complex_id", "단지"),
    ("area_band", "면적"),
    ("type_key", "타입"),
    ("stage", "단계"),
    ("price", "현재가"),
    ("strong_buy", "STRONG BUY"),
    ("fair_buy", "FAIR"),
    ("do_not_buy", "DO NOT BUY"),
    ("verdict", "판정"),
    ("alpha", "Alpha"),
    ("risk", "Risk"),
    ("confidence", "Confidence"),
    ("required_equity", "실투자금"),
    ("unused_cash", "남는 현금"),
    ("recoverable_gap", "회복가능 격차"),
    ("price_stretch", "Stretch"),
    ("money_depth", "돈 도달 칸"),
    ("latent", "Latent"),
    ("visible", "Visible"),
    ("band_shift", "밴드 이동"),
    ("supply_risk", "공급위험"),
    ("leader", "Relevant Leader"),
    ("transmission", "전달실패"),
    ("rank_change", "순위변화"),
    ("coverage", "커버리지"),
)


def row_of(candidate, *, rank: int, change: Change | None = None,
           unused_cash: int | None = None,
           coverage: str | None = None) -> dict:
    """§62 가 요구한 컬럼을 한 줄로. 없는 값은 '확인 불가' 다."""
    def f(key, fmt="{:.2f}"):
        item = candidate.features.items.get(key)
        if item is None or not item.usable or item.value is None:
            return "확인 불가"
        return fmt.format(item.value)

    a = candidate.alpha
    return {
        "rank": rank,
        "complex_id": candidate.complex_id,
        "area_band": candidate.area_band,
        "type_key": "-",
        "stage": candidate.stage.stage,
        "price": units.fmt_eok(candidate.price),
        "strong_buy": (units.fmt_eok(candidate.bands.strong_buy)
                       if candidate.bands.strong_buy else "확인 불가"),
        "fair_buy": (units.fmt_eok(candidate.bands.fair)
                     if candidate.bands.fair else "확인 불가"),
        "do_not_buy": (units.fmt_eok(candidate.bands.do_not_buy)
                       if candidate.bands.do_not_buy else "확인 불가"),
        "verdict": candidate.bands.verdict(),
        "alpha": f"{a.alpha:.0f}" if a.known else "확인 불가",
        "risk": f"{a.risk:.0f}" if a.risk is not None else "확인 불가",
        "confidence": f"{a.confidence:.0f}",
        "required_equity": (units.fmt_eok(candidate.required_equity)
                            if candidate.required_equity else "확인 불가"),
        "unused_cash": (units.fmt_eok(unused_cash) if unused_cash is not None
                        else "확인 불가"),
        "recoverable_gap": f("recoverable_discount_ratio"),
        "price_stretch": f("price_stretch", "{:+.1%}"),
        "money_depth": f("money_arrival_depth", "{:.0f}"),
        "latent": f("latent_movement"),
        "visible": f("visible_movement"),
        "band_shift": f("band_shift_strength"),
        "supply_risk": f("effective_supply_risk"),
        "leader": getattr(candidate, "relevant_leader", None) or "확인 불가",
        "transmission": f("transmission_failure"),
        "rank_change": (change.label.split("—")[0].strip() if change
                        else "직전 실행 없음"),
        "coverage": coverage or "확인 불가",
    }
