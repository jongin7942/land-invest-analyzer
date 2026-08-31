"""CASH 를 후보로 (신규 지시서 §3·§24·§46).

> CASH 는 Benchmark 가 아니라 Candidate 다.
> 최종 Ranking 이 1. CASH  2. Apartment A  3. Apartment B 가 될 수 있어야 한다.

전에는 `pipeline._cash_option()` 이 boolean 하나를 돌려줬다. "아무것도 좋지
않으면 현금" 이라는 판단은 있었지만, **CASH 가 순위표의 행이 아니었다.**
그 차이가 중요한 이유:

* 행이 아니면 "3위 후보보다는 낫고 2위보다는 못하다" 를 말할 수 없다.
* 행이 아니면 백테스트에서 "그때 현금이 정답이었나" 를 채점할 수 없다(§26 CashAccuracy).

그래서 CASH 를 같은 축(세후·이자후·비용후 위험조정 기대수익)에서 점수화한다.

**남는 현금도 버리지 않는다**(§2). 5억 자기자본으로 실투자금 3억짜리를 사면
2억이 남는다. 그 2억의 수익을 0 으로 세면 비싼 물건이 부당하게 유리해진다.

    총자본수익 = 아파트 기대수익 + 남는현금 × 현금수익률

현금수익률(Cash Hurdle)은 **추정하지 않는다.** 사용자가 프로필에 넣어야 하고,
없으면 CASH 점수는 '확인 불가' 다 — 0% 로 가정하면 현금이 항상 최악이 된다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from apt_engine import units
from apt_engine.trace import Calc

CASH_ID = 0                      # complex_id 와 겹치지 않는 자리표시자
CASH_LABEL = "CASH / 매수하지 않음"


@dataclass(frozen=True)
class CashOption:
    """현금 보유라는 투자상품."""
    capital: int
    hurdle_rate: float | None            # 세후 연 수익률
    horizon_years: int
    unknown_reason: str | None = None

    @property
    def known(self) -> bool:
        return self.hurdle_rate is not None

    @property
    def expected_return(self) -> float | None:
        """보유기간 전체 기대수익률(복리)."""
        if self.hurdle_rate is None:
            return None
        return (1 + self.hurdle_rate) ** self.horizon_years - 1

    @property
    def terminal_value(self) -> int | None:
        r = self.expected_return
        return None if r is None else int(self.capital * (1 + r))

    @property
    def label(self) -> str:
        if not self.known:
            return f"CASH — 확인 불가 ({self.unknown_reason})"
        return (f"CASH {units.fmt_eok(self.capital)} · "
                f"연 {self.hurdle_rate:.2%} · "
                f"{self.horizon_years}년 {self.expected_return:+.1%}")

    @property
    def calc(self) -> Calc:
        if not self.known:
            return Calc(value=None, unit="비율",
                        formula="현금 수익률(Cash Hurdle)이 없어 계산하지 않았습니다",
                        intermediates={"사유": self.unknown_reason},
                        grade="SCENARIO")
        return Calc(value=self.expected_return, unit="비율",
                    formula="(1 + 세후 현금수익률) ^ 보유연수 − 1",
                    intermediates={"자본": self.capital,
                                   "연 수익률": self.hurdle_rate,
                                   "보유연수": self.horizon_years},
                    grade="ESTIMATED")


def load(conn: sqlite3.Connection, *, profile_name: str | None,
         capital: int, horizon_years: int) -> CashOption:
    """프로필에서 Cash Hurdle 을 읽는다. 없으면 **추정하지 않는다.**"""
    rate = None
    if profile_name:
        row = conn.execute(
            "SELECT cash_hurdle_rate FROM user_profile WHERE name = ?",
            (profile_name,)).fetchone()
        if row is not None:
            rate = row["cash_hurdle_rate"]
    if rate is None:
        return CashOption(
            capital, None, horizon_years,
            "현금 수익률이 프로필에 없습니다. `profile set --cash-hurdle 0.03` 처럼 "
            "세후 기준으로 넣으세요 — 0% 로 가정하면 현금이 항상 최악이 됩니다")
    return CashOption(capital, float(rate), horizon_years)


def unused_cash_return(cash: CashOption, *, required_equity: int | None
                       ) -> tuple[int | None, float | None, str]:
    """남는 현금과 그 수익 (§2 UnusedCashReturn).

    반환: (남는 현금, 보유기간 수익금, 설명)
    """
    if required_equity is None:
        return None, None, "실투자금을 몰라 남는 현금을 계산할 수 없습니다"
    leftover = max(0, cash.capital - required_equity)
    if leftover == 0:
        return 0, 0.0, "자기자본을 전부 씁니다"
    r = cash.expected_return
    if r is None:
        return leftover, None, (
            f"남는 현금 {units.fmt_eok(leftover)} 의 수익률을 모릅니다 "
            f"— 0 으로 세지 않습니다")
    return leftover, leftover * r, (
        f"남는 현금 {units.fmt_eok(leftover)} × {r:+.1%} = "
        f"{units.fmt_eok(int(leftover * r))}")


def total_capital_return(*, property_gain: float | None, purchase_price: int,
                         required_equity: int | None, cash: CashOption
                         ) -> tuple[float | None, dict]:
    """자기자본 전체 기준 수익률 (§2·§24).

    아파트만 보면 "실투자금 3억으로 1억 벌었다 = 33%" 지만, 자기자본이 5억이면
    2억이 놀고 있었다. 그 2억까지 포함해야 **같은 자기자본끼리** 비교가 된다.
    """
    detail: dict = {}
    if property_gain is None or required_equity is None:
        detail["사유"] = "아파트 기대수익 또는 실투자금을 몰라 계산하지 않았습니다"
        return None, detail

    gain = property_gain * purchase_price
    leftover, leftover_gain, why = unused_cash_return(
        cash, required_equity=required_equity)
    detail["아파트 수익"] = units.fmt_eok(int(gain))
    detail["남는 현금"] = why
    if leftover_gain is None:
        detail["주의"] = ("남는 현금의 수익을 몰라 총자본수익률을 내지 않습니다 "
                        "— 0 으로 세면 비싼 물건이 부당하게 유리해집니다")
        return None, detail
    total = (gain + leftover_gain) / cash.capital if cash.capital else None
    detail["총자본수익률"] = f"{total:+.1%}" if total is not None else "확인 불가"
    return total, detail


def beats(cash: CashOption, *, candidate_return: float | None,
          risk_penalty: float = 0.0) -> tuple[bool | None, str]:
    """이 후보가 CASH 보다 나은가 (§46 최종 질문).

    `risk_penalty` 위험조정 감점(0~1). 현금은 위험이 0 이라 감점이 없다.

    셋 중 하나를 돌려준다: True(낫다) / False(못하다) / None(모른다).
    **모르는 것을 '낫다' 로 세지 않는다.**
    """
    if candidate_return is None:
        return None, "후보의 기대수익을 몰라 CASH 와 비교할 수 없습니다"
    if not cash.known:
        return None, f"CASH 기준선이 없습니다 — {cash.unknown_reason}"
    adjusted = candidate_return * (1 - risk_penalty)
    hurdle = cash.expected_return
    if adjusted > hurdle:
        return True, (f"위험조정 기대수익 {adjusted:+.1%} > "
                      f"현금 {hurdle:+.1%}")
    return False, (f"위험조정 기대수익 {adjusted:+.1%} ≤ 현금 {hurdle:+.1%} "
                   f"— 지금은 매수하지 않는 것이 우위입니다(§3)")
