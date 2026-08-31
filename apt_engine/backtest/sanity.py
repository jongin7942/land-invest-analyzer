"""시점별 Sanity Test (신규 지시서 §28·§29·§31·§30).

세 개의 서로 다른 질문이다. **하나의 가중치로 셋 다 통과해야 한다** — §29 가
"2021용 Weight 와 2019용 Weight 를 따로 만들지 않는다" 고 못박았다.

    2021  과열기에 BUY 를 남발하지 않는가        (Reverse Sanity)
    2017/2019  좋은 기회를 CASH 로 흘리지 않는가  (Opportunity)
    2023  Reset 후 회복 초기를 잡는가            (Recovery)

2021 만 통과하는 모델은 만들기 쉽다 — 전부 CASH 라고 하면 된다. 그래서 §29 가
반대 방향 검사를 같이 요구한다. 둘을 **동시에** 걸어야 의미가 있다.

⚠ §28 이 명시한 대로, 이건 **Reverse Sanity Test 이지 True Blind Test 가
아니다.** 우리는 이미 2021 이 고점이었다는 것을 알고 검사를 설계했다.
결과를 "모델이 2021 을 예측했다" 로 읽으면 안 된다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from apt_engine.backtest import windows as windows_mod
from apt_engine.blind import cutoff as cutoff_mod

# §30 이 지정한 스냅샷 시점
SNAPSHOTS = ("2015-01-01", "2017-01-01", "2019-01-01", "2021-01-01",
             "2023-01-01")

# 검사 종류
REVERSE = "REVERSE"          # 사면 안 되는데 샀는가
OPPORTUNITY = "OPPORTUNITY"  # 사야 하는데 안 샀는가
RECOVERY = "RECOVERY"        # 회복 초기를 잡았는가

# 2021 검사 — BUY 비율이 이보다 높으면 실패로 본다.
# **판정 기준이지 관측이 아니다.**
REVERSE_MAX_BUY_SHARE = 0.20
# 2017/2019 검사 — BUY 비율이 이보다 낮으면 지나치게 보수적이다.
OPPORTUNITY_MIN_BUY_SHARE = 0.10

THRESHOLD_NOTE = ("BUY 비율 경계는 판정 기준입니다. 백테스트가 대체합니다")
BLIND_NOTE = ("§28: 이것은 Reverse Sanity Test 이지 True Blind Test 가 "
              "아닙니다. 2021 이 고점이었다는 것을 알고 설계한 검사입니다")


@dataclass(frozen=True)
class Check:
    as_of: str
    kind: str
    passed: bool | None
    buy_share: float | None
    candidates: int
    detail: str
    unknown_reason: str | None = None

    @property
    def label(self) -> str:
        if self.passed is None:
            return f"{self.as_of} [{self.kind}] 판정 불가 — {self.unknown_reason}"
        mark = "통과" if self.passed else "실패"
        share = f"{self.buy_share:.0%}" if self.buy_share is not None else "?"
        return (f"{self.as_of} [{self.kind}] {mark} · "
                f"BUY {share} ({self.candidates}개 중) · {self.detail}")


@dataclass
class SanityReport:
    checks: list[Check] = field(default_factory=list)
    weights_source: str = "HEURISTIC"

    @property
    def all_passed(self) -> bool | None:
        """**하나의 가중치로 전부 통과했는가** (§29).

        하나라도 판정 불가면 전체가 판정 불가다 — 통과한 것만 세면
        "2021 은 통과했다" 로 읽히고, 그건 반쪽이다.
        """
        if any(c.passed is None for c in self.checks):
            return None
        return all(c.passed for c in self.checks) if self.checks else None

    @property
    def summary(self) -> str:
        lines = ["시점별 Sanity Test", f"  가중치 출처: {self.weights_source}"]
        for c in self.checks:
            lines.append(f"  {c.label}")
        verdict = self.all_passed
        if verdict is None:
            lines.append("  → 판정 불가 (데이터가 없는 시점이 있습니다)")
        elif verdict:
            lines.append("  → 하나의 가중치로 전부 통과했습니다(§29)")
        else:
            failed = [c.as_of for c in self.checks if c.passed is False]
            lines.append(f"  → 실패: {', '.join(failed)}. "
                         f"시점별로 가중치를 따로 만들지 않습니다(§29·§49-14)")
        lines.append(f"  {BLIND_NOTE}")
        return "\n".join(lines)


def data_available(conn: sqlite3.Connection, as_of: str) -> tuple[bool, str]:
    """그 시점을 검사할 수 있는가.

    실거래와 **그 시점 정책** 이 둘 다 있어야 한다. 정책이 없으면 실투자금이
    확인 불가가 되어 Capital Gate 를 아무도 통과하지 못한다 — 그 상태로 돌리면
    "후보 0개" 가 나오고, 그걸 "보수적이라 통과" 로 읽으면 안 된다.
    """
    ym = as_of[:4] + as_of[5:7]
    trades = conn.execute(
        "SELECT COUNT(*) FROM trade WHERE deal_ymd <= ? AND deal_ymd >= ?",
        (as_of.replace("-", ""), f"{int(as_of[:4]) - 1}0101")).fetchone()[0]
    if not trades:
        return False, f"{as_of} 시점 전후의 실거래가 없습니다"

    loans = conn.execute(
        "SELECT COUNT(*) FROM loan_rule WHERE effective_from <= ? "
        "  AND (effective_to IS NULL OR effective_to >= ?)",
        (as_of, as_of)).fetchone()[0]
    if not loans:
        return False, (
            f"{as_of} 시점에 유효한 대출 규칙이 없습니다. 그 상태로 돌리면 "
            f"실투자금이 확인 불가가 되어 후보 0개가 나오고, 그걸 '보수적이라 "
            f"통과' 로 읽게 됩니다")
    return True, ""


def check(conn: sqlite3.Connection, as_of: str, kind: str, *, run_fn
          ) -> Check:
    """시점 하나를 검사한다.

    `run_fn(conn, as_of) -> (buy_count, total_count)` 를 호출한다.
    파이프라인을 부르는 책임은 호출부에 있고, 이 함수는 판정만 한다.
    """
    ok, why = data_available(conn, as_of)
    if not ok:
        return Check(as_of, kind, None, None, 0, "", why)

    try:
        buys, total = run_fn(conn, as_of)
    except Exception as exc:                       # noqa: BLE001
        return Check(as_of, kind, None, None, 0, "",
                     f"랭킹이 실패했습니다: {type(exc).__name__}: {exc}")

    if not total:
        return Check(as_of, kind, None, None, 0, "",
                     "자본 게이트를 통과한 후보가 없습니다 — "
                     "모델이 보수적인 것과 구분되지 않습니다")

    share = buys / total
    if kind == REVERSE:
        passed = share <= REVERSE_MAX_BUY_SHARE
        detail = ("과열기에 매수를 자제했습니다" if passed else
                  f"과열기에 BUY 가 {share:.0%} 입니다 — MoneyArrival 만 보고 "
                  f"판단하고 있을 수 있습니다(§28)")
    elif kind == OPPORTUNITY:
        passed = share >= OPPORTUNITY_MIN_BUY_SHARE
        detail = ("기회를 잡았습니다" if passed else
                  f"BUY 가 {share:.0%} 뿐입니다 — 2021 을 피하려다 좋은 "
                  f"기회까지 CASH 로 흘렸습니다(§29)")
    else:                                          # RECOVERY
        passed = share >= OPPORTUNITY_MIN_BUY_SHARE
        detail = ("회복 초기를 잡았습니다" if passed else
                  f"BUY 가 {share:.0%} 뿐입니다 — Reset 후 회복을 못 잡습니다(§31)")

    return Check(as_of, kind, passed, share, total, detail)


PLAN: tuple[tuple[str, str], ...] = (
    ("2017-01-01", OPPORTUNITY),
    ("2019-01-01", OPPORTUNITY),
    ("2021-01-01", REVERSE),
    ("2023-01-01", RECOVERY),
)


def run_all(conn: sqlite3.Connection, *, run_fn,
            weights_source: str = "HEURISTIC") -> SanityReport:
    """§28·§29·§31 을 **같은 가중치로** 한 번에 돌린다."""
    report = SanityReport(weights_source=weights_source)
    for as_of, kind in PLAN:
        report.checks.append(check(conn, as_of, kind, run_fn=run_fn))
    return report


def buy_counter(*, gate: str, limit: int = 10):
    """`run_fn` 을 만든다 — 그 시점 파이프라인을 돌려 BUY 개수를 센다."""
    def run(conn: sqlite3.Connection, as_of: str) -> tuple[int, int]:
        from apt_engine.invest.budget import Profile
        from apt_engine.ranking import delta_pipeline as delta

        profile = Profile.load(conn, "backtest") or Profile(
            name="backtest", available_cash=300_000_000)
        result = delta.run(conn, as_of=cutoff_mod.AsOf(as_of), profile=profile,
                           gate=gate, limit=limit)
        total = len(result.candidates)
        buys = len(result.split.executable) if result.split else 0
        return buys, total
    return run
