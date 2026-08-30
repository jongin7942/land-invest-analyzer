"""규칙 조회 공통 계층 — `as_of` 없이는 아무것도 못 찾는다.

세법·규제·대출은 전부 "언제 기준인가"가 답을 바꾼다. 그래서 조회 함수는 `as_of` 를
**키워드 필수 인자**로 받고 기본값을 `date.today()` 로 두지 않는다. 호출부가 매번
명시하게 강제해서, 작년 세법으로 올해를 계산하거나(요구사항 62-10) 지정기간이 끝난
토허를 현재로 표시하는(요구사항 26-7) 사고를 호출 시점에 막는다.

그리고 `last_verified` 가 비어 있는 규칙은 **미검증**으로 본다. 미검증 규칙으로
계산하면 `UnverifiedRuleError` 가 난다. 그럴듯한 기본값으로 조용히 계산하는 것보다
"확인 불가"가 낫다 — 세금이 틀리면 그 위에 쌓은 수익률·적정매수가가 전부 틀린다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from apt_engine.trace import Evidence


class RuleError(RuntimeError):
    pass


class UnverifiedRuleError(RuleError):
    """규칙은 있는데 사람이 확인한 적이 없다. 계산을 거부한다."""


class NoRuleError(RuleError):
    """그 시점에 적용할 규칙이 아예 없다."""


class NotEnactedError(RuleError):
    """규칙은 있는데 아직 시행 전이다(발표·입법예고). 계산에 쓰지 않는다."""


# ── 정책 생애주기 ─────────────────────────────────────────────────────
ENACTED = "ENACTED"        # 시행 중. 계산에 쓰는 유일한 상태
ANNOUNCED = "ANNOUNCED"    # 발표됐으나 시행 전
PROPOSED = "PROPOSED"      # 입법예고·정책발표 단계
EXPIRED = "EXPIRED"        # 시행이 끝남. 백테스트에는 여전히 필요하다
STATUSES = (ENACTED, ANNOUNCED, PROPOSED, EXPIRED)

# ── 데이터 신뢰도 (요구사항 17) ────────────────────────────────────────
VERIFIED = "VERIFIED"                    # 사람이 원문을 확인함
ESTIMATED = "ESTIMATED"                  # 추정치. 사전 확정이 불가능한 실비 등
UNKNOWN = "UNKNOWN"                      # 값을 모른다
NEEDS_VERIFICATION = "NEEDS_VERIFICATION"  # 값은 있으나 원문 확인 전
VERIFICATIONS = (VERIFIED, ESTIMATED, UNKNOWN, NEEDS_VERIFICATION)

# 신뢰도 합성 — 가장 약한 것이 이긴다(Calc.grade 와 같은 원칙).
_VERIFICATION_ORDER = {VERIFIED: 0, ESTIMATED: 1, NEEDS_VERIFICATION: 2, UNKNOWN: 3}


def weakest_verification(*levels: str) -> str:
    """여러 항목을 합쳤을 때의 신뢰도. 하나라도 UNKNOWN 이면 결과는 UNKNOWN."""
    known = [l for l in levels if l in _VERIFICATION_ORDER]
    if not known:
        return UNKNOWN
    return max(known, key=_VERIFICATION_ORDER.__getitem__)


def as_ymd(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise ValueError(f"날짜는 YYYY-MM-DD 형식이어야 합니다: {value!r}")
    return text


def effective_clause(alias: str = "") -> str:
    """as_of 시점에 유효한 행만 고르는 SQL 조각. 파라미터 2개(as_of, as_of)를 받는다."""
    p = f"{alias}." if alias else ""
    return (f"{p}effective_from <= ? "
            f"AND ({p}effective_to IS NULL OR {p}effective_to >= ?)")


def matches(conditions_json: str | None, context: dict) -> tuple[bool, int]:
    """조건이 맞는지, 그리고 몇 개나 맞았는지(구체성).

    지원하는 연산자 — 키 접미사로 표현한다:
        "house_count": 1          같다
        "house_count_gte": 2      이상
        "house_count_lte": 3      이하
        "exclusive_area_gt": 85   초과
        "price_lt": 900000000     미만
        "regulated": true         불리언
        "zone_in": ["조정대상지역","투기과열지구"]  목록에 포함
    """
    if not conditions_json:
        return True, 0
    try:
        conditions = json.loads(conditions_json)
    except (ValueError, TypeError) as e:
        raise RuleError(f"conditions_json 을 읽을 수 없습니다: {conditions_json!r}") from e
    if not conditions:
        return True, 0

    score = 0
    for key, expected in conditions.items():
        field, _, op = key.rpartition("_")
        if op in ("gte", "lte", "gt", "lt", "in"):
            actual = context.get(field)
        else:
            field, op, actual = key, "eq", context.get(key)

        if actual is None:
            return False, 0
        ok = (
            actual >= expected if op == "gte" else
            actual <= expected if op == "lte" else
            actual > expected if op == "gt" else
            actual < expected if op == "lt" else
            actual in expected if op == "in" else
            actual == expected
        )
        if not ok:
            return False, 0
        score += 1
    return True, score


@dataclass(frozen=True)
class Rule:
    """규칙 한 행 + 검증 상태."""
    row: sqlite3.Row
    specificity: int

    def __getitem__(self, key):
        return self.row[key]

    def get(self, key, default=None):
        try:
            value = self.row[key]
        except (IndexError, KeyError):
            return default
        return default if value is None else value

    @property
    def verified(self) -> bool:
        return bool(self.get("last_verified"))

    @property
    def status(self) -> str:
        """정책 생애주기. 계산에 쓰는 건 ENACTED 뿐이다."""
        return str(self.get("status") or ENACTED)

    @property
    def enacted(self) -> bool:
        return self.status == ENACTED

    @property
    def verification(self) -> str:
        """데이터 신뢰도. VERIFIED / ESTIMATED / UNKNOWN / NEEDS_VERIFICATION."""
        got = self.get("verification")
        if got:
            return str(got)
        return VERIFIED if self.verified else NEEDS_VERIFICATION

    @property
    def evidence(self) -> Evidence:
        return Evidence(
            source=self.get("source_name") or "수기 입력 규칙",
            url=self.get("source_url"),
            effective_date=self.get("effective_from"),
            retrieved_at=self.get("last_verified"),
            note=(f"{self.get('effective_from')} ~ {self.get('effective_to') or '현재'}"
                  + ("" if self.verified else "  ⚠ 미검증")),
        )

    def require_enacted(self, what: str) -> "Rule":
        """시행 전 정책으로 실투자금을 계산하지 않는다.

        발표만 된 정책(ANNOUNCED/PROPOSED)을 지금 계산에 넣으면, 아직 오지 않은
        세제를 전제로 매수 판단을 하게 된다. 화면에는 '향후 정책 변경 가능'으로만
        보여주고 금액에는 넣지 않는다.
        """
        if not self.enacted:
            raise NotEnactedError(
                f"{what} 규칙 '{self.get('rule_key')}' 은 status={self.status} 입니다. "
                f"시행 중인 법령(ENACTED)만 계산에 씁니다 — "
                f"발표·예정 정책은 '향후 정책 변경 가능'으로만 표시합니다.")
        return self

    def require_verified(self, what: str) -> "Rule":
        if not self.verified:
            raise UnverifiedRuleError(
                f"{what} 규칙 '{self.get('rule_key') or self.get('zone_type')}' 은 "
                f"아직 사람이 확인하지 않았습니다(last_verified 비어 있음). "
                f"원문을 확인한 뒤 `cli rule verify` 로 표시하거나, "
                f"확인 없이 진행하려면 allow_unverified=True 를 명시하세요."
            )
        return self


def pick(rows: list[sqlite3.Row], context: dict, *, amount: int | None = None,
         min_col: str = "bracket_min", max_col: str = "bracket_max",
         statuses: tuple[str, ...] = (ENACTED,)) -> list[Rule]:
    """조건과 금액구간이 맞는 규칙만 남기고, 구체적인 것부터 정렬한다.

    기본적으로 **시행 중인 규칙만** 돌려준다. 발표·예정 정책을 보려면
    statuses 를 명시한다 — 그 경우 호출부가 그것을 금액에 넣지 않을 책임을 진다.
    """
    out: list[Rule] = []
    for row in rows:
        status = _maybe(row, "status") or ENACTED
        if statuses and status not in statuses:
            continue
        ok, score = matches(_maybe(row, "conditions_json"), context)
        if not ok:
            continue
        if amount is not None:
            lo = _maybe(row, min_col) or 0
            hi = _maybe(row, max_col)
            if amount < lo or (hi is not None and amount >= hi):
                continue
        out.append(Rule(row, score))
    # 조건이 더 많이 맞는 것 먼저, 같으면 더 최근에 시행된 규칙 먼저.
    out.sort(key=lambda r: (-r.specificity, _desc(r.get("effective_from") or "")))
    return out


def _desc(text: str):
    """문자열 내림차순 정렬용 키."""
    return tuple(-ord(c) for c in text)


def _maybe(row: sqlite3.Row, key: str):
    try:
        return row[key]
    except (IndexError, KeyError):
        return None
