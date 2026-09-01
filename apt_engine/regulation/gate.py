"""토지거래허가 실행 Gate — 감점이 아니라 문이다 (지시서 §5·§6·§49-9).

**왜 감점으로 하면 안 되는가**

토허를 `-10점` 으로 처리하면, 기대수익이 큰 후보는 그 감점을 이기고
1위로 올라온다. 그런데 비거주 투자자에게 실거주 의무가 있는 물건은
**점수가 아무리 높아도 살 수가 없다.** 못 사는 것을 1위로 보여주면
그 화면 전체가 거짓말이 된다.

그래서 점수가 아니라 Gate 다. 통과 못 하면 `executable = False` 이고
실행 목록에서 빠진다. 다만 **연구 데이터에서는 지우지 않는다** —
왜 빠졌는지 남아 있어야 나중에 규제가 풀렸을 때 다시 볼 수 있고,
Gate 가 너무 빡빡한지(§46 Winner Recall)도 그걸로 측정한다.

**두 개의 순위를 나눈다 (§5)**

    Pure Alpha Ranking   순수 투자매력. Gate 를 보지 않는다. 연구용.
    Executable Ranking   실제로 살 수 있는 것만. 사용자 기본 화면.

**내국인 토허와 외국인 토허를 절대 혼동하지 않는다 (§6)**

수도권 외국인 대상 토허는 내국인 매수를 막지 않는다. `zone.py` 가
scope 를 필수로 받는 이유가 이것이고, 여기서도 사용자의 국적 구분을
그대로 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine.regulation import zone as zone_mod

# 매수 목적. 실거주 의무는 '비거주 투자' 에서만 문제가 된다.
LIVE_IN = "실거주"
INVEST = "비거주"
PURPOSES = (LIVE_IN, INVEST)

# Gate 판정
PASS = "PASS"                    # 살 수 있다
BLOCKED = "BLOCKED"              # 살 수 없다 — 실행 목록에서 뺀다
NEEDS_CHECK = "NEEDS_CHECK"      # 확인해야 안다 — 뺀다. '아마 될 것' 으로 두지 않는다


@dataclass(frozen=True)
class Decision:
    """실행 가능 여부 판정.

    `executable` 이 True 가 아니면 실행 순위에 올리지 않는다. NEEDS_CHECK 도
    마찬가지다 — 확인 못 한 것을 통과시키면 Gate 가 아니다.
    """
    verdict: str
    purpose: str
    reason: str
    designated: bool | None = None
    residence_duty_months: int | None = None
    grace_allowed: bool | None = None
    parcel_recheck_required: bool = True
    evidence: list = field(default_factory=list)

    @property
    def executable(self) -> bool:
        return self.verdict == PASS

    @property
    def label(self) -> str:
        return {PASS: "매수 가능", BLOCKED: "매수 불가",
                NEEDS_CHECK: "확인 필요"}.get(self.verdict, self.verdict)


def decide(permit: zone_mod.PermitStatus, *, purpose: str,
           grace_allowed: bool | None = None,
           parcel_recheck_required: bool = True) -> Decision:
    """토허 상태 + 매수 목적 → 실행 가능 여부.

    판정 순서가 중요하다.

      1. 데이터를 못 봤나            → NEEDS_CHECK (통과시키지 않는다)
      2. 지정 구역이 아닌가          → PASS
      3. 실거주 목적인가             → PASS (의무가 있어도 어차피 산다)
      4. 실거주 의무가 있나          → BLOCKED
      5. 의무 기간을 모르나          → NEEDS_CHECK

    4번에서 유예가 확인된 경우만 PASS 로 되돌린다. **유예가 '가능할 수도
    있다' 는 것으로는 통과시키지 않는다** — grace_allowed 가 True 여야 한다.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"매수 목적은 {PURPOSES} 중 하나여야 합니다: {purpose!r}")

    ev = list(permit.evidence)

    if not permit.checked:
        return Decision(NEEDS_CHECK, purpose,
                        "토지거래허가 데이터가 없어 판정하지 못했습니다. "
                        "확인 못 한 것을 '가능' 으로 두지 않습니다.",
                        evidence=ev)

    if not permit.designated:
        return Decision(PASS, purpose, "토지거래허가구역이 아닙니다",
                        designated=False, evidence=ev,
                        parcel_recheck_required=parcel_recheck_required)

    duty = permit.residence_duty_months

    if purpose == LIVE_IN:
        return Decision(
            PASS, purpose,
            "실거주 목적이라 실거주 의무가 매수를 막지 않습니다"
            + (f" (의무 {duty}개월)" if duty else ""),
            designated=True, residence_duty_months=duty, evidence=ev,
            parcel_recheck_required=parcel_recheck_required)

    if duty is None:
        return Decision(
            NEEDS_CHECK, purpose,
            "토지거래허가구역인데 실거주 의무 기간이 입력돼 있지 않습니다. "
            "비거주 투자가 가능한지 확인해야 합니다.",
            designated=True, evidence=ev,
            parcel_recheck_required=parcel_recheck_required)

    if duty <= 0:
        return Decision(PASS, purpose,
                        "토지거래허가구역이지만 실거주 의무가 없습니다",
                        designated=True, residence_duty_months=duty, evidence=ev,
                        parcel_recheck_required=parcel_recheck_required)

    if grace_allowed is True:
        return Decision(
            PASS, purpose,
            f"실거주 의무 {duty}개월이지만 유예가 확인됐습니다",
            designated=True, residence_duty_months=duty, grace_allowed=True,
            evidence=ev, parcel_recheck_required=parcel_recheck_required)

    return Decision(
        BLOCKED, purpose,
        f"토지거래허가구역이고 실거주 의무 {duty}개월이 있습니다. "
        f"비거주 투자로는 매수할 수 없습니다.",
        designated=True, residence_duty_months=duty, grace_allowed=grace_allowed,
        evidence=ev, parcel_recheck_required=parcel_recheck_required)


def decide_at(conn, *, lawd_cd: str, as_of: str, purpose: str,
              scope: str = zone_mod.DOMESTIC) -> Decision:
    """DB 에서 토허 상태를 읽어 바로 판정한다.

    scope 기본값이 내국인인 이유: 수도권 외국인 토허를 내국인에게 적용하면
    살 수 있는 것을 못 산다고 말하게 된다(§6).
    """
    permit = zone_mod.permit_zone_at(conn, lawd_cd, as_of=as_of, scope=scope)
    grace, recheck = _grace_of(conn, lawd_cd, as_of, scope)
    return decide(permit, purpose=purpose, grace_allowed=grace,
                  parcel_recheck_required=recheck)


def _grace_of(conn, lawd_cd: str, as_of: str, scope: str):
    """017 에서 추가한 유예·재확인 컬럼을 읽는다."""
    row = conn.execute(
        "SELECT residence_grace_allowed, parcel_recheck_required "
        "FROM land_permit_zone "
        "WHERE lawd_cd = ? AND target_scope = ? "
        "AND effective_from <= ? AND effective_to >= ? "
        "ORDER BY effective_from DESC LIMIT 1",
        (lawd_cd, scope, as_of, as_of)).fetchone()
    if row is None:
        return None, True
    grace = row["residence_grace_allowed"]
    return (None if grace is None else bool(grace),
            bool(row["parcel_recheck_required"]))


__all__ = ["Decision", "decide", "decide_at", "PASS", "BLOCKED", "NEEDS_CHECK",
           "LIVE_IN", "INVEST", "PURPOSES"]
