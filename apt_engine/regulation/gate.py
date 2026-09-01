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

# ── 매수자 국적 ───────────────────────────────────────────────────────
#
# **이 축을 빼면 최악의 오류가 난다.**
#
# 2025-08-26 시행 수도권 외국인 주택 토허는 대한민국 국적이 없는
# 개인·외국법인·외국정부에만 적용된다. 내국인 매수와 아무 상관이 없다.
# 그런데 국적을 안 받으면 "부평구는 토허구역" 이라는 이유로 내국인이
# 부평 아파트를 사는 것까지 막게 된다.
KOREAN = "KOR"
FOREIGN = "FOREIGN"
NATIONALITY_UNKNOWN = None

# 규칙이 누구에게 적용되는가 (지시서 §2)
ALL_BUYERS = "ALL_BUYERS"
FOREIGN_ONLY = "FOREIGN_ONLY"
CORPORATE_ONLY = "CORPORATE_ONLY"
SPECIFIC_BUYER_TYPE = "SPECIFIC_BUYER_TYPE"
SCOPE_UNKNOWN = "UNKNOWN"

# ── 부동산 유형 (지시서 §2) ───────────────────────────────────────────
APARTMENT = "APARTMENT"
ROW_HOUSE = "ROW_HOUSE"              # 연립주택
MULTI_FAMILY = "MULTI_FAMILY_HOUSE"  # 다세대주택

# 규칙이 어떤 물건에 걸리는가
APARTMENT_ONLY = "APARTMENT_ONLY"
APT_AND_SAME_COMPLEX = "APARTMENT_AND_SAME_COMPLEX_MULTI_HOUSING"

# ── 실거주 계획 ───────────────────────────────────────────────────────
NON_OCCUPANCY = "NON_OCCUPANCY"
OCCUPANCY = "OCCUPANCY"

# 주거지역 허가대상 면적. **초과** 다 — 6㎡ 와 같으면 대상이 아니다.
# 이 한 글자('이상' vs '초과')로 6㎡ 짜리가 막히거나 안 막힌다.
DEFAULT_RESIDENTIAL_THRESHOLD = 6.0

# Gate 판정
PASS = "PASS"                    # 살 수 있다
PASS_WITH_PERMIT = "PASS_WITH_PERMIT"   # 허가를 받으면 살 수 있다
BLOCKED = "BLOCKED"              # 살 수 없다 — 실행 목록에서 뺀다
NEEDS_CHECK = "NEEDS_CHECK"      # 확인해야 안다 — 뺀다. '아마 될 것' 으로 두지 않는다

# ── 커버리지 (지시서 §4) ──────────────────────────────────────────────
#
# **두 겹으로 나눈다.** 한 겹으로 두면 둘 중 하나가 손해를 본다.
#
#   광역(시·구) 아파트 지정   BROAD_APARTMENT    — 다 넣었으면 COMPLETE
#   필지 단위 지정            PARCEL_SPECIFIC    — 재건축·모아타운 등
#
# 광역을 다 넣었는데도 필지 미수집 때문에 전부 NEEDS_CHECK 로 두면
# 군포·안양 만안구처럼 **지정된 적 없는 곳까지 확정 추천이 막힌다.**
# 반대로 필지 미수집을 무시하면 재건축 구역을 놓친다.
#
# 그래서 광역으로 PASS/BLOCK 을 정하고, 필지 미수집은 **경고로 덧붙인다.**
COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
INCOMPLETE = "INCOMPLETE"

BROAD_APARTMENT = "BROAD_APARTMENT"
PARCEL_SPECIFIC = "PARCEL_SPECIFIC"
PARCEL_WARNING = "PARCEL_SPECIFIC_LAND_PERMIT_COVERAGE_INCOMPLETE"


def applies_to_buyer(target_scope: str | None,
                     nationality: str | None) -> bool | None:
    """이 규칙이 이 매수자에게 적용되는가.

    True  적용된다        False 적용 안 된다        None 국적을 몰라 판정 불가

    핵심은 **FOREIGN_ONLY × 내국인 → False** 다. 여기서 True 를 돌려주면
    내국인이 인천 7개 구 아파트를 하나도 못 사게 된다.
    """
    if target_scope == ALL_BUYERS:
        return True
    if target_scope == FOREIGN_ONLY:
        if nationality == KOREAN:
            return False              # ← 내국인에게는 적용되지 않는다
        if nationality == FOREIGN:
            return True
        return None                   # 국적 미입력 → 판정 불가
    if target_scope in (CORPORATE_ONLY, SPECIFIC_BUYER_TYPE, SCOPE_UNKNOWN):
        # 법인 여부·세부 유형을 우리가 안 받고 있다. 모르면 통과시키지 않는다.
        return None
    return None


@dataclass(frozen=True)
class Decision:
    """실행 가능 여부 판정.

    `executable` 이 True 가 아니면 실행 순위에 올리지 않는다. NEEDS_CHECK 도
    마찬가지다 — 확인 못 한 것을 통과시키면 Gate 가 아니다.
    """
    verdict: str
    purpose: str
    reason: str
    nationality: str | None = None
    target_scope: str | None = None
    coverage_status: str | None = None
    rule_id: str | None = None
    # 나에게 **적용되지 않은** 규칙들. 비었다고 규칙이 없는 게 아니다.
    # 화면에서 "외국인 토허구역이지만 내국인이라 해당 없음" 을 말하려면
    # 이게 있어야 한다 — 없으면 그냥 '규칙 없음' 으로 보인다.
    not_applicable: tuple = ()
    # NEEDS_CHECK 인 이유를 코드로 구분한다. 사유가 다르면 할 일도 다르다.
    #   RULE_INCOMPLETE      규칙은 찾았는데 실거주 의무가 비었다
    #   NATIONALITY_UNKNOWN  매수자 국적을 안 받았다
    #   COVERAGE_INCOMPLETE  그 지역 규칙 자체를 아직 수집 못 했다
    check_code: str | None = None
    designated: bool | None = None
    residence_duty_months: int | None = None
    grace_allowed: bool | None = None
    parcel_recheck_required: bool = True
    evidence: list = field(default_factory=list)

    @property
    def executable(self) -> bool:
        """실행 목록에 올릴 수 있는가.

        PASS_WITH_PERMIT 도 True 다 — 허가를 받으면 살 수 있는 것이지
        못 사는 것이 아니다. 다만 화면에 '허가 필요' 를 표시해야 한다.
        """
        return self.verdict in (PASS, PASS_WITH_PERMIT)

    @property
    def permit_required(self) -> bool:
        return self.verdict == PASS_WITH_PERMIT

    @property
    def label(self) -> str:
        return {PASS: "매수 가능", PASS_WITH_PERMIT: "허가 받으면 가능",
                BLOCKED: "매수 불가",
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


# ── 규칙 집합 전체를 놓고 판정한다 (지시서 §2) ────────────────────────

@dataclass(frozen=True)
class Rule:
    """토허 규칙 한 줄. DB 행을 그대로 담는다."""
    rule_id: str | None
    target_scope: str | None
    nationality_scope: str | None
    residence_duty_months: int | None
    status: str | None
    effective_from: str | None = None
    effective_to: str | None = None
    property_scope: str | None = None
    parcel_recheck_required: bool = True
    source_url: str | None = None
    residential_threshold_sqm: float | None = None


def matches_property(rule: Rule, *, property_type: str,
                     same_complex_has_apartment: bool | None) -> bool | None:
    """이 규칙이 이 물건에 걸리는가 (지시서 §2·§3).

    True 걸린다 · False 안 걸린다 · None 확인해야 안다

    **행정구역 전체의 모든 부동산에 적용하지 않는다.** 광역 지정이라도
    대상은 아파트(와 같은 단지의 연립·다세대)뿐이다. 일반 연립·다세대
    전체로 확대하면 대상이 아닌 물건을 막게 된다.
    """
    scope = (rule.property_scope or "").strip()
    if not scope:
        return None      # 대상이 뭔지 모르면 통과시키지도 막지도 않는다

    # 고시문을 그대로 옮긴 한글 서술도 받는다.
    # 외국인 수도권 지정은 '단독/다가구/아파트/연립/다세대' 처럼 들어온다.
    korean = "아파트" in scope
    apt_covered = scope in (APARTMENT_ONLY, APT_AND_SAME_COMPLEX) or korean

    if property_type == APARTMENT:
        return apt_covered

    if property_type in (ROW_HOUSE, MULTI_FAMILY):
        word = "연립" if property_type == ROW_HOUSE else "다세대"
        if korean:
            # 한글 고시가 그 유형을 직접 적어 두면 같은 단지 조건 없이 걸린다
            return word in scope
        if scope != APT_AND_SAME_COMPLEX:
            return False
        if same_complex_has_apartment is True:
            return True
        if same_complex_has_apartment is False:
            return False
        return None      # 같은 단지에 아파트가 있는지 모른다

    return False


def evaluate(rules: list[Rule], *, nationality: str | None, purpose: str,
             coverage_status: str = INCOMPLETE) -> Decision:
    """이 매수자·이 목적에 대해 토허가 어떻게 걸리는가.

    판정 순서 (지시서 §2 의 evaluate_land_permit 을 그대로 옮긴 것)

      1. 나에게 적용되는 규칙만 남긴다   ← FOREIGN_ONLY × 내국인은 여기서 빠진다
      2. 국적을 몰라 판정 못 한 규칙이 있으면  NEEDS_CHECK
      3. 적용 규칙이 없다
           · 커버리지가 COMPLETE 면        PASS  (확인했고 해당 없음)
           · 아니면                        NEEDS_CHECK (확인을 못 한 것)
      4. 불완전한 규칙이 있으면            NEEDS_CHECK
      5. 비거주 + 실거주의무 > 0           BLOCKED
      6. 그 외                             PASS_WITH_PERMIT (허가는 받아야 한다)

    3번이 이 함수의 핵심이다. **규칙이 0건인 것과 '해당 없음' 은 다르다.**
    0건을 PASS 로 처리하면 실거주 의무 구역을 비거주 투자자에게 추천하게
    되고, BLOCK 으로 처리하면 화면이 텅 빈다.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"매수 목적은 {PURPOSES} 중 하나여야 합니다: {purpose!r}")

    applicable, undecidable = [], []
    for r in rules:
        hit = applies_to_buyer(r.target_scope, nationality)
        if hit is True:
            applicable.append(r)
        elif hit is None:
            undecidable.append(r)

    not_applied = tuple(r for r in rules
                        if applies_to_buyer(r.target_scope, nationality) is False)

    if undecidable:
        why = ("매수자 국적을 몰라 판정할 수 없습니다"
               if nationality is None
               else "적용 대상을 판정할 수 없는 규칙이 있습니다")
        return Decision(NEEDS_CHECK, purpose, why, nationality=nationality,
                        coverage_status=coverage_status,
                        target_scope=undecidable[0].target_scope,
                        rule_id=undecidable[0].rule_id,
                        not_applicable=not_applied,
                        check_code="NATIONALITY_UNKNOWN")

    if not applicable:
        # 나에게 적용되는 규칙이 없다. 여기서 갈린다.
        #
        #   커버리지 COMPLETE  → 확인했고 해당 없음 → PASS
        #   그 외             → 확인을 못 한 것   → NEEDS_CHECK
        #
        # 둘을 같게 처리하면 안 된다. "찾아봤는데 없더라" 와 "안 찾아봤다"
        # 는 다른 말이고, 뒤엣것을 PASS 로 두면 실거주 의무 구역을
        # 비거주 투자자에게 추천하게 된다.
        skipped = (f" (외국인 대상 규칙 {len(not_applied)}건은 "
                   f"내국인에게 적용되지 않아 제외했습니다)"
                   if not_applied and nationality == KOREAN else "")
        if coverage_status == COMPLETE:
            return Decision(PASS, purpose,
                            "이 매수자에게 적용되는 토지거래허가 규칙이 없습니다"
                            + skipped,
                            nationality=nationality, designated=False,
                            coverage_status=coverage_status,
                            not_applicable=not_applied)
        return Decision(
            NEEDS_CHECK, purpose,
            "토지거래허가구역 데이터 미확인 — 추천 확정 전 확인 필요" + skipped,
            nationality=nationality, coverage_status=coverage_status,
            not_applicable=not_applied, check_code="COVERAGE_INCOMPLETE")

    # 규칙이 '확정' 인가. 어휘가 두 벌이라 둘 다 받는다 —
    # 기존 규칙표는 ENACTED, 작업지시서는 CONFIRMED 를 쓴다.
    confirmed = {"ENACTED", "CONFIRMED"}
    incomplete = [r for r in applicable
                  if (r.status or "").upper() not in confirmed
                  or r.residence_duty_months is None]
    if incomplete:
        return Decision(
            NEEDS_CHECK, purpose,
            "토지거래허가 규칙이 불완전합니다 (실거주 의무 기간 미확인). "
            "비거주 가능이라고 판정하지 않습니다.",
            nationality=nationality, coverage_status=coverage_status,
            target_scope=incomplete[0].target_scope,
            rule_id=incomplete[0].rule_id, designated=True,
            not_applicable=not_applied, check_code="RULE_INCOMPLETE")

    recheck = any(r.parcel_recheck_required for r in applicable)

    if purpose == INVEST:
        duty = [r for r in applicable if (r.residence_duty_months or 0) > 0]
        if duty:
            r = duty[0]
            return Decision(
                BLOCKED, purpose,
                f"토지거래허가구역이고 실거주 의무 {r.residence_duty_months}개월이 "
                f"있습니다. 비거주 투자로는 매수할 수 없습니다.",
                nationality=nationality, target_scope=r.target_scope,
                coverage_status=coverage_status, rule_id=r.rule_id,
                designated=True, residence_duty_months=r.residence_duty_months,
                parcel_recheck_required=recheck, not_applicable=not_applied)

    r = applicable[0]
    return Decision(
        PASS_WITH_PERMIT, purpose,
        "토지거래허가를 받으면 매수할 수 있습니다"
        + (f" (실거주 의무 {r.residence_duty_months}개월)"
           if r.residence_duty_months else ""),
        nationality=nationality, target_scope=r.target_scope,
        coverage_status=coverage_status, rule_id=r.rule_id, designated=True,
        residence_duty_months=r.residence_duty_months,
        parcel_recheck_required=recheck, not_applicable=not_applied)


# 기존 어휘(내국인/외국인/전체) → 작업지시서 어휘. 기존 데이터를 안 깨고
# 읽기 위한 변환이다. `buyer_scope` 가 채워져 있으면 그것이 우선한다.
LEGACY_SCOPE = {
    "외국인": FOREIGN_ONLY,
    "내국인": ALL_BUYERS,     # 내국인 대상 지정은 내국인 매수자 전부에 걸린다
    "전체": ALL_BUYERS,
}


def buyer_scope_of(row) -> str:
    """이 규칙이 누구에게 걸리는가. 모르면 UNKNOWN — 통과시키지 않는다."""
    explicit = row["buyer_scope"] if "buyer_scope" in row.keys() else None
    if explicit:
        return explicit
    return LEGACY_SCOPE.get(row["target_scope"], SCOPE_UNKNOWN)


# ── 물건 단위 판정 (지시서 §3) ────────────────────────────────────────

@dataclass(frozen=True)
class Candidate:
    """판정 대상 물건."""
    lawd_cd: str
    property_type: str = APARTMENT
    land_share_sqm: float | None = None          # 주거지역 토지지분
    same_complex_has_apartment: bool | None = None
    # 대지권이 어디서 왔나. 추정값이면 **통과 방향으로 쓰지 않는다**.
    # (regulation.land_share 의 VERIFIED / ESTIMATED)
    land_share_verification: str = "VERIFIED"


def evaluate_candidate(rules: list[Rule], candidate: Candidate, *,
                       nationality: str | None, occupancy_plan: str | None,
                       contract_date: str,
                       coverage_status: str = INCOMPLETE,
                       parcel_coverage: str = INCOMPLETE) -> Decision:
    """주소·물건·계약일·매수자를 모두 놓고 판정한다.

    순서가 판정을 좌우한다.

      1. 계약일에 유효한 규칙만 남긴다   ← 만료된 지정을 적용하지 않는다
      2. 매수자에게 적용되는 것만        ← FOREIGN_ONLY × 내국인은 여기서 빠진다
      3. 이 물건 유형에 걸리는 것만      ← 일반 연립·다세대는 여기서 빠진다
      4. 토지지분 6㎡ **초과** 인가       ← 6㎡ 는 대상이 아니다
      5. 비거주 투자면 BLOCK
      6. 실거주 계획 미입력이면 NEEDS_CHECK
      7. 실거주 예정이면 PASS_WITH_PERMIT

    중복 규칙은 **지우지 않고 가장 엄격한 것**을 적용한다(§5).
    서울에는 광역 아파트 지정과 재건축 구역 지정이 겹칠 수 있다.
    """
    if occupancy_plan not in (NON_OCCUPANCY, OCCUPANCY, None):
        raise ValueError(f"실거주 계획 값이 이상합니다: {occupancy_plan!r}")

    # 1. 계약일 기준 유효
    live = [r for r in rules
            if (not r.effective_from or r.effective_from <= contract_date)
            and (not r.effective_to or r.effective_to >= contract_date)]
    expired = [r for r in rules if r not in live]

    # 2. 매수자
    applicable, undecidable = [], []
    for r in live:
        hit = applies_to_buyer(r.target_scope, nationality)
        if hit is True:
            applicable.append(r)
        elif hit is None:
            undecidable.append(r)
    not_applied = tuple(r for r in live
                        if applies_to_buyer(r.target_scope, nationality) is False)

    if undecidable:
        return Decision(
            NEEDS_CHECK, INVEST if occupancy_plan == NON_OCCUPANCY else LIVE_IN,
            "매수자 국적을 몰라 판정할 수 없습니다" if nationality is None
            else "적용 대상을 판정할 수 없는 규칙이 있습니다",
            nationality=nationality, coverage_status=coverage_status,
            not_applicable=not_applied, check_code="NATIONALITY_UNKNOWN",
            rule_id=undecidable[0].rule_id)

    purpose = INVEST if occupancy_plan == NON_OCCUPANCY else LIVE_IN

    # 3. 물건 유형
    matched, unsure = [], []
    for r in applicable:
        hit = matches_property(
            r, property_type=candidate.property_type,
            same_complex_has_apartment=candidate.same_complex_has_apartment)
        if hit is True:
            matched.append(r)
        elif hit is None:
            unsure.append(r)

    if unsure:
        return Decision(
            NEEDS_CHECK, purpose,
            "같은 단지에 아파트가 있는지 확인해야 이 규칙이 걸리는지 알 수 있습니다",
            nationality=nationality, coverage_status=coverage_status,
            not_applicable=not_applied, rule_id=unsure[0].rule_id,
            check_code="SAME_COMPLEX_APARTMENT_STATUS_UNKNOWN")

    if not matched:
        # 규칙이 아예 없었나, 아니면 나/내 물건에 안 걸렸나.
        why = ("이 매수자·이 물건에 걸리는 토지거래허가 규칙이 없습니다"
               if live else "이 지역·이 시점에 유효한 토지거래허가 규칙이 없습니다")
        if expired and not live:
            # 만료된 지정이 있었다. **자동 통과시키지 않는다** — 연장·재지정
            # 여부를 확인해야 한다(§6 테스트 13).
            return Decision(
                NEEDS_CHECK, purpose,
                f"직전 지정이 {expired[0].effective_to} 로 끝났습니다. "
                f"연장·재지정 여부를 확인해야 합니다.",
                nationality=nationality, coverage_status=coverage_status,
                not_applicable=not_applied, rule_id=expired[0].rule_id,
                check_code="DESIGNATION_EXPIRED_RECHECK")
        if coverage_status == COMPLETE:
            # 광역 지정을 다 확인했고 여기엔 없다 → PASS.
            # 다만 필지 단위를 아직 못 봤으면 그 사실을 **경고로 붙인다** —
            # 판정을 뒤집지는 않는다(§4).
            note = ""
            code = None
            if parcel_coverage != COMPLETE:
                note = (" 다만 재건축·모아타운 같은 필지 단위 지정은 "
                        "아직 확인하지 못했습니다.")
                code = PARCEL_WARNING
            return Decision(PASS, purpose, why + note, nationality=nationality,
                            designated=False, coverage_status=coverage_status,
                            not_applicable=not_applied, check_code=code)
        return Decision(NEEDS_CHECK, purpose,
                        why + " (광역 지정 목록을 아직 다 확인하지 못했습니다)",
                        nationality=nationality, coverage_status=coverage_status,
                        not_applicable=not_applied,
                        check_code="COVERAGE_INCOMPLETE")

    # 4. 토지지분 — **초과** 여야 대상이다
    if candidate.land_share_sqm is None:
        return Decision(
            NEEDS_CHECK, purpose,
            "주거지역 토지지분을 확인하지 못했습니다. "
            "6㎡ 초과라야 허가 대상이라 지분 없이는 판정할 수 없습니다.",
            nationality=nationality, coverage_status=coverage_status,
            not_applicable=not_applied, rule_id=matched[0].rule_id,
            designated=True, check_code="LAND_SHARE_AREA_MISSING")

    threshold = next((r.residential_threshold_sqm for r in matched
                      if r.residential_threshold_sqm is not None),
                     DEFAULT_RESIDENTIAL_THRESHOLD)

    # 추정 대지권으로는 **문을 열 수 없다**. 아래로 틀리면 허가 없이
    # 계약해서 무효가 되기 때문이다(regulation/land_share.py 참고).
    from apt_engine.regulation import land_share as ls_mod
    share = ls_mod.LandShare(candidate.land_share_sqm, "-",
                             candidate.land_share_verification)
    over = share.exceeds(threshold)

    if over is None:
        return Decision(
            NEEDS_CHECK, purpose,
            f"대지권 {candidate.land_share_sqm:.1f}㎡ 는 추정값이라 "
            f"허가대상 기준({threshold}㎡ 초과) 판정에 쓸 수 없습니다. "
            f"공동주택 공시가격의 전유부 대지권을 확인해야 합니다.",
            nationality=nationality, coverage_status=coverage_status,
            not_applicable=not_applied, designated=True,
            rule_id=matched[0].rule_id, check_code="LAND_SHARE_ESTIMATED_ONLY")

    if over is False:
        return Decision(
            PASS, purpose,
            f"토지지분 {candidate.land_share_sqm}㎡ 로 허가대상 기준"
            f"({threshold}㎡ 초과)에 못 미칩니다",
            nationality=nationality, coverage_status=coverage_status,
            not_applicable=not_applied, designated=True,
            rule_id=matched[0].rule_id)

    # 5~7. 가장 엄격한 실거주 의무를 적용한다 (중복 규칙, §5)
    duties = [r.residence_duty_months for r in matched
              if r.residence_duty_months is not None]
    if len(duties) < len(matched):
        return Decision(
            NEEDS_CHECK, purpose,
            "실거주 의무 기간이 확인되지 않은 규칙이 있습니다. "
            "비거주 가능이라고 판정하지 않습니다.",
            nationality=nationality, coverage_status=coverage_status,
            not_applicable=not_applied, designated=True,
            rule_id=matched[0].rule_id, check_code="RULE_INCOMPLETE")
    duty = max(duties) if duties else 0
    strictest = next(r for r in matched if r.residence_duty_months == duty)
    recheck = any(r.parcel_recheck_required for r in matched)

    if duty > 0 and occupancy_plan == NON_OCCUPANCY:
        return Decision(
            BLOCKED, INVEST,
            f"토지거래허가구역이고 실거주 의무 {duty}개월이 있습니다. "
            f"비거주 투자로는 매수할 수 없습니다.",
            nationality=nationality, target_scope=strictest.target_scope,
            coverage_status=coverage_status, rule_id=strictest.rule_id,
            designated=True, residence_duty_months=duty,
            parcel_recheck_required=recheck, not_applicable=not_applied)

    if occupancy_plan is None:
        return Decision(
            NEEDS_CHECK, purpose,
            f"토지거래허가구역입니다(실거주 의무 {duty}개월). "
            f"실거주 계획을 입력해야 매수 가능 여부를 판정할 수 있습니다.",
            nationality=nationality, coverage_status=coverage_status,
            rule_id=strictest.rule_id, designated=True,
            residence_duty_months=duty, not_applicable=not_applied,
            parcel_recheck_required=recheck, check_code="OCCUPANCY_PLAN_MISSING")

    return Decision(
        PASS_WITH_PERMIT, purpose,
        f"토지거래허가를 받으면 매수할 수 있습니다 (실거주 의무 {duty}개월)",
        nationality=nationality, target_scope=strictest.target_scope,
        coverage_status=coverage_status, rule_id=strictest.rule_id,
        designated=True, residence_duty_months=duty,
        parcel_recheck_required=recheck, not_applicable=not_applied)


def load_rules(conn, *, lawd_cd: str, as_of: str) -> list[Rule]:
    """그 시점에 유효한 규칙만 읽는다. 만료된 지정은 적용하지 않는다."""
    rows = conn.execute(
        "SELECT rule_id, target_scope, buyer_scope, nationality_scope,"
        "       residence_duty_months, status, effective_from, effective_to,"
        "       property_scope, parcel_recheck_required, source_url,"
        "       residential_threshold_sqm "
        "FROM land_permit_zone "
        "WHERE lawd_cd = ? AND effective_from <= ? AND effective_to >= ?",
        (lawd_cd, as_of, as_of)).fetchall()
    return [Rule(rule_id=r["rule_id"], target_scope=buyer_scope_of(r),
                 nationality_scope=r["nationality_scope"],
                 residence_duty_months=r["residence_duty_months"],
                 status=r["status"], effective_from=r["effective_from"],
                 effective_to=r["effective_to"],
                 property_scope=r["property_scope"],
                 parcel_recheck_required=bool(r["parcel_recheck_required"]),
                 source_url=r["source_url"],
                 residential_threshold_sqm=r["residential_threshold_sqm"])
            for r in rows]


def coverage_of(conn, *, sido: str, target_scope: str = ALL_BUYERS) -> str:
    """그 지역·그 대상의 수집 상태. 없으면 INCOMPLETE — 안 넣은 것이다."""
    row = conn.execute(
        "SELECT coverage_status FROM permit_coverage "
        "WHERE sido = ? AND target_scope = ?", (sido, target_scope)).fetchone()
    return row["coverage_status"] if row else INCOMPLETE


def evaluate_at(conn, *, lawd_cd: str, sido: str, as_of: str,
                nationality: str | None, purpose: str) -> Decision:
    """DB 에서 규칙과 커버리지를 읽어 바로 판정한다."""
    rules = load_rules(conn, lawd_cd=lawd_cd, as_of=as_of)
    # 커버리지는 **적용 대상별로** 본다. 외국인 토허를 다 넣었어도
    # 내국인 일반 토허를 안 넣었으면 내국인에게는 여전히 INCOMPLETE 다.
    scope = FOREIGN_ONLY if nationality == FOREIGN else ALL_BUYERS
    return evaluate(rules, nationality=nationality, purpose=purpose,
                    coverage_status=coverage_of(conn, sido=sido,
                                                target_scope=scope))


__all__ = ["Decision", "Rule", "decide", "decide_at", "evaluate", "evaluate_at",
           "load_rules", "coverage_of", "applies_to_buyer", "buyer_scope_of",
           "Candidate", "evaluate_candidate", "matches_property",
           "APARTMENT", "ROW_HOUSE", "MULTI_FAMILY",
           "APARTMENT_ONLY", "APT_AND_SAME_COMPLEX",
           "NON_OCCUPANCY", "OCCUPANCY",
           "PASS", "PASS_WITH_PERMIT", "BLOCKED", "NEEDS_CHECK",
           "LIVE_IN", "INVEST", "PURPOSES",
           "KOREAN", "FOREIGN", "ALL_BUYERS", "FOREIGN_ONLY", "CORPORATE_ONLY",
           "SPECIFIC_BUYER_TYPE", "SCOPE_UNKNOWN",
           "COMPLETE", "PARTIAL", "INCOMPLETE",
           "BROAD_APARTMENT", "PARCEL_SPECIFIC", "PARCEL_WARNING"]
