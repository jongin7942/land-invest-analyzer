"""대출 상환 스케줄 — 연도별 이자·원금·잔액 (요구사항 30·31).

상환방식마다 현금흐름이 완전히 다르다.

    원리금균등  매달 같은 금액. 초반엔 이자 비중이 크다
    원금균등    원금은 매달 같고 이자가 줄어든다. **첫 해 부담이 가장 크다**
    만기일시    보유기간 내내 이자만. 원금은 매도 시점에 한꺼번에

이걸 뭉뚱그려 "이자 = 원금 × 금리 × 년수" 로 계산하면, 원리금균등에서 실제보다
이자를 크게 잡고(원금이 줄어드는 걸 무시), 만기일시에서는 원금 상환 시점을 놓친다.

**스트레스 금리를 여기 쓰지 않는다.** 스트레스 금리는 DSR 한도를 구할 때만 쓰는
규제 장치이고, 실제로 나가는 이자는 계약 금리로 계산해야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from apt_engine import units

MONTHS_PER_YEAR = 12
REPAYMENT_TYPES = ("원리금균등", "원금균등", "만기일시")


@dataclass(frozen=True)
class YearRow:
    year: int                  # 1부터
    interest: int
    principal: int
    balance_end: int

    @property
    def payment(self) -> int:
        return self.interest + self.principal


@dataclass(frozen=True)
class Schedule:
    principal: int
    annual_rate: float
    term_years: int
    repayment_type: str
    rows: list[YearRow]

    def take(self, years: int) -> list[YearRow]:
        """보유기간만큼만 잘라 쓴다."""
        return self.rows[:years]

    def balance_after(self, years: int) -> int:
        """그 시점의 잔액. 매도할 때 한꺼번에 갚아야 하는 돈이다."""
        if years <= 0:
            return self.principal
        if years >= len(self.rows):
            return self.rows[-1].balance_end if self.rows else 0
        return self.rows[years - 1].balance_end

    def interest_through(self, years: int) -> int:
        return sum(r.interest for r in self.take(years))

    @property
    def label(self) -> str:
        return (f"{units.fmt_eok(self.principal)} · "
                f"{units.fmt_pct(self.annual_rate, digits=2)} · "
                f"{self.term_years}년 {self.repayment_type}")


def build(principal: int, *, annual_rate: float, term_years: int,
          repayment_type: str = "원리금균등") -> Schedule:
    """연 단위 상환 스케줄. 월 단위로 굴린 뒤 해마다 묶는다."""
    if repayment_type not in REPAYMENT_TYPES:
        raise ValueError(f"상환방식은 {', '.join(REPAYMENT_TYPES)} 중 하나입니다: "
                         f"{repayment_type!r}")
    principal = int(principal)
    if principal <= 0 or term_years <= 0:
        return Schedule(max(principal, 0), annual_rate, term_years, repayment_type, [])

    n = term_years * MONTHS_PER_YEAR
    i = annual_rate / MONTHS_PER_YEAR
    balance = float(principal)
    rows: list[YearRow] = []

    if repayment_type == "원리금균등":
        payment = (principal * i / (1 - (1 + i) ** -n)) if i > 0 else principal / n
    elif repayment_type == "원금균등":
        monthly_principal = principal / n
    # 만기일시는 매달 이자만 낸다

    for year in range(1, term_years + 1):
        year_interest = 0.0
        year_principal = 0.0
        for _ in range(MONTHS_PER_YEAR):
            interest = balance * i
            if repayment_type == "원리금균등":
                pay_principal = min(payment - interest, balance)
            elif repayment_type == "원금균등":
                pay_principal = min(monthly_principal, balance)
            else:
                pay_principal = 0.0
            year_interest += interest
            year_principal += pay_principal
            balance -= pay_principal
        rows.append(YearRow(year, int(units.won_round(year_interest)),
                            int(units.won_round(year_principal)),
                            int(units.won_round(balance))))

    if repayment_type == "만기일시" and rows:
        # 만기에 원금을 한꺼번에 갚는다.
        last = rows[-1]
        rows[-1] = YearRow(last.year, last.interest, principal, 0)

    return Schedule(principal, annual_rate, term_years, repayment_type, rows)
