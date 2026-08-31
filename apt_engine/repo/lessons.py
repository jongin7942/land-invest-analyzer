"""Investment Lessons DB (지시서 §58·§59).

백테스트에서 얻은 규칙을 **코드에 하드코딩하지 않기 위한 그릇**이다.

    HYPOTHESIS   가설. 아직 검증 안 됨. 계산에 쓰지 않는다
    PROVISIONAL  일부 구간에서 확인됨. 가중치를 조금 줄 수 있다
    CONFIRMED    충분한 표본·구간에서 확인됨. 규칙으로 쓴다
    REJECTED     검증했더니 아니었다. **지우지 않고 남긴다** — 왜 아닌지가 자산이다

몇 개 사례로 CONFIRMED 로 올리지 못하게 스키마가 근거와 표본수를 요구한다.
그리고 여기 있는 lesson 은 그 자체로 계산에 끼어들지 않는다 —
가중치를 바꾸려면 `ranking_run.weights_json` 에 반영되고 그 실행이 기록된다.
"""
from __future__ import annotations

import sqlite3

STATUSES = ("HYPOTHESIS", "PROVISIONAL", "CONFIRMED", "REJECTED")

# CONFIRMED 로 올리기 위한 최소 조건. 지시서 §58 "몇 개 사례만으로 올리지 않는다".
MIN_SAMPLE_FOR_CONFIRMED = 200
MIN_REGIMES_FOR_CONFIRMED = 2


class LessonError(ValueError):
    pass


# §59 — 지금 시점에 seed 해야 할 20개. **전부 HYPOTHESIS 로 넣는다.**
# 백테스트로 검증하기 전에는 확정규칙으로 쓰지 않는다.
SEED_LESSONS: tuple[tuple[str, str], ...] = (
    ("jeonse_is_downside_defense",
     "전세가율은 Upside 보다 Downside Defense 성격이 강하다"),
    ("volume_is_not_buy_signal",
     "거래량 증가는 자동 BUY 신호가 아니다 — 조사 우선순위를 높이는 신호다"),
    ("good_tier_old_build_not_enough",
     "좋은 급지 + 구축이라는 조합만으로 상승을 보장하지 않는다"),
    ("entry_price_over_location",
     "Entry Price 가 좋은 입지보다 수익률에 더 크게 작용할 수 있다"),
    ("peak_discount_not_sufficient",
     "과거 고점 대비 할인은 저평가의 충분조건이 아니다"),
    ("redev_pricing_over_feasibility",
     "재건축은 사업성 자체보다 사업성 대비 가격 선반영 정도가 중요하다"),
    ("supply_ratio_over_volume",
     "공급은 절대물량보다 기존 stock 대비 공급비율이 중요할 수 있다"),
    ("leader_follower_needs_proof",
     "Leader/Follower 관계는 실제 과거 가격전이로 검증돼야 인정한다"),
    ("entry_timing_matters",
     "같은 단지라도 Entry Timing 차이가 수익률을 크게 바꿀 수 있다"),
    ("recovery_speed_has_signal",
     "하락 후 회복속도가 향후 성과에 설명력을 가질 수 있다"),
    ("split_new_and_renewed_jeonse",
     "신규전세와 갱신전세를 분리해야 한다 — 갱신은 시세를 반영하지 않는다"),
    ("newish_high_jeonse_not_alpha",
     "준신축 + 높은 전세가율 조합만으로 큰 Alpha 가 발생하지 않을 수 있다"),
    ("past_winner_is_not_current_winner",
     "과거 많이 오른 아파트가 현재 좋은 투자라는 보장은 없다"),
    ("late_discovery_is_missed",
     "모델이 상승 후에 발견한 Winner 는 성공이 아니라 Missed Winner 다"),
    ("false_follower_removal_matters",
     "가격전이가 검증되지 않은 Follower 제거가 성과에 크게 기여한다"),
    ("no_user_interest_in_generation",
     "사용자 관심단지는 candidate generation 에 사용하지 않는다"),
    ("catalyst_alpha_formula",
     "호재는 실현확률 × 미반영비율로 평가해야 한다 — 단순 가점은 틀린다"),
    ("redev_profit_needs_irr",
     "재건축 예상이익은 총액이 아니라 시간가치(IRR)로 평가해야 한다"),
    ("alternative_purchase_test",
     "모든 TOP 후보는 같은 현금으로 살 수 있는 대체 후보와 비교돼야 한다"),
    ("kill_score_over_base_score",
     "높은 Kill Score 는 높은 Base Score 보다 결정적일 수 있다"),
)


def seed(conn: sqlite3.Connection) -> int:
    """20개 가설을 넣는다. 이미 있으면 건드리지 않는다(사람이 올린 status 보존)."""
    added = 0
    for key, hypothesis in SEED_LESSONS:
        cur = conn.execute(
            "INSERT INTO investment_lesson (lesson_key, original_hypothesis, status) "
            "VALUES (?,?,'HYPOTHESIS') ON CONFLICT(lesson_key) DO NOTHING",
            (key, hypothesis))
        added += cur.rowcount
    return added


def promote(conn: sqlite3.Connection, lesson_key: str, *, status: str,
            evidence: str | None = None, sample_size: int | None = None,
            tested_regions: str | None = None, tested_regimes: str | None = None,
            result: str | None = None, modified_rule: str | None = None) -> None:
    """상태를 올린다. CONFIRMED 는 표본과 구간을 확인한 뒤에만."""
    if status not in STATUSES:
        raise LessonError(f"status 는 {', '.join(STATUSES)} 중 하나입니다: {status!r}")

    row = conn.execute("SELECT * FROM investment_lesson WHERE lesson_key = ?",
                       (lesson_key,)).fetchone()
    if row is None:
        raise LessonError(f"'{lesson_key}' lesson 이 없습니다")

    size = sample_size if sample_size is not None else row["sample_size"]
    regimes = tested_regimes or row["tested_regimes"] or ""

    if status == "CONFIRMED":
        if not (size and size >= MIN_SAMPLE_FOR_CONFIRMED):
            raise LessonError(
                f"CONFIRMED 로 올리려면 표본이 {MIN_SAMPLE_FOR_CONFIRMED}건 이상이어야 "
                f"합니다 (현재 {size}). 사례 몇 개로 규칙을 확정하지 않습니다.")
        if len([r for r in regimes.split(",") if r.strip()]) < MIN_REGIMES_FOR_CONFIRMED:
            raise LessonError(
                f"CONFIRMED 로 올리려면 서로 다른 시장국면 "
                f"{MIN_REGIMES_FOR_CONFIRMED}개 이상에서 확인돼야 합니다 "
                f"(현재 '{regimes}'). 상승장에서만 맞는 규칙은 규칙이 아닙니다.")
        if not (evidence or row["evidence"]):
            raise LessonError("CONFIRMED 에는 근거(evidence)가 필요합니다")

    conn.execute(
        "UPDATE investment_lesson SET status = ?, "
        " evidence = COALESCE(?, evidence), sample_size = COALESCE(?, sample_size), "
        " tested_regions = COALESCE(?, tested_regions), "
        " tested_regimes = COALESCE(?, tested_regimes), "
        " result = COALESCE(?, result), modified_rule = COALESCE(?, modified_rule), "
        " updated_at = datetime('now','localtime') WHERE lesson_key = ?",
        (status, evidence, sample_size, tested_regions, tested_regimes, result,
         modified_rule, lesson_key))


def by_status(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM investment_lesson"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    return conn.execute(sql + " ORDER BY status, lesson_key", params).fetchall()


def confirmed_rules(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """계산에 쓸 수 있는 규칙만. PROVISIONAL 은 여기 안 들어온다."""
    return conn.execute(
        "SELECT * FROM investment_lesson WHERE status = 'CONFIRMED' "
        " AND modified_rule IS NOT NULL ORDER BY lesson_key").fetchall()
