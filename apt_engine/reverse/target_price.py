"""목표수익률 역산 매수가 — "얼마 이하에서 사야 하는가".

종인님이 정한 최종 목표의 **나머지 절반**이다.

> "…위험 대비 기대수익이 가장 높은 아파트는 무엇이며, **얼마 이하에서
>  사야 하는가?**"

앞의 절반(무엇을)은 랭킹이 답한다. 뒤의 절반은 이 모듈이 답한다.

---

## 왜 단순 나눗셈이 안 되나

"세후 연 7% 를 원하니 기대 매도가를 1.07^5 로 나누면 되지" 가 틀린 이유:

**매수가가 바뀌면 비용이 전부 따라 바뀐다.**

    매수가 ↓  →  취득세 ↓ (구간이 바뀌면 세율 자체가 바뀜)
              →  중개보수 ↓
              →  대출 가능액 ↓ (LTV 는 비율이라)
              →  실투자금 ↓
              →  보유세 ↓ (공시가격이 따라감)
              →  양도차익 ↑  →  양도세 ↑

취득세만 해도 6~9억 구간은 세율이 구간 안에서 연속으로 변한다. 그래서
"수익률 = f(매수가)" 는 닫힌 식으로 안 풀린다.

**이분법으로 푼다.** 매수가를 넣어 수익률을 계산하는 함수가 이미 있으니,
그 함수를 거꾸로 탐색한다. IRR 을 이분법으로 푸는 것과 같은 이유다
(`cashflow/irr.py`).

---

## 답이 없을 수 있다

목표가 너무 높으면 **어떤 가격에도 도달하지 않는다.** 0원에 사도 안 되는
경우가 있다 — 보유비용이 기대 상승을 넘을 때다. 그때 "매우 싸게 사면 됩니다"
같은 답을 만들지 않고 **"이 목표로는 살 수 없습니다"** 라고 말한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from apt_engine import units

# 탐색 범위. 현재가의 이 배율 사이에서 찾는다.
MIN_RATIO = 0.30
MAX_RATIO = 1.50

# 이분법 반복. 30회면 범위가 10억이어도 1원 단위까지 좁혀진다.
MAX_ITERATIONS = 40
# 수익률이 이만큼 안에 들어오면 찾은 것으로 본다.
TOLERANCE = 0.0005

NOTE = ("매수가가 바뀌면 취득세·대출·보유세·양도세가 전부 따라 바뀝니다. "
        "닫힌 식으로 안 풀려서 이분법으로 찾습니다")


@dataclass(frozen=True)
class TargetPrice:
    target_return: float
    price: int | None
    achieved_return: float | None = None
    current_price: int | None = None
    iterations: int = 0
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.price is not None

    @property
    def discount_needed(self) -> float | None:
        """현재가 대비 얼마나 깎아야 하나."""
        if not self.known or not self.current_price:
            return None
        return (self.price - self.current_price) / self.current_price

    @property
    def buyable_now(self) -> bool | None:
        if not self.known or not self.current_price:
            return None
        return self.current_price <= self.price

    @property
    def label(self) -> str:
        if not self.known:
            return f"목표 {self.target_return:.1%}: 확인 불가 — {self.reason}"
        head = (f"목표 {self.target_return:.1%} → "
                f"{units.fmt_eok(self.price)} 이하에서 사야 합니다")
        if self.current_price:
            gap = self.discount_needed
            if self.buyable_now:
                head += f"  (현재 {units.fmt_eok(self.current_price)} — 지금 가능)"
            else:
                head += (f"  (현재 {units.fmt_eok(self.current_price)} — "
                         f"{abs(gap):.1%} 더 싸야 함)")
        return head


def solve(*, target_return: float, current_price: int,
          return_at: Callable[[int], float | None],
          min_ratio: float = MIN_RATIO,
          max_ratio: float = MAX_RATIO) -> TargetPrice:
    """목표 수익률을 만드는 최대 매수가 (이분법).

    `return_at(price) -> 수익률 | None` 을 호출한다. 파이프라인이 세금·대출·
    보유비용을 다 반영한 함수를 넘긴다. 이 모듈은 탐색만 한다.

    **수익률이 가격에 대해 단조감소**라고 가정한다(비싸게 살수록 수익률이
    낮아진다). 이 가정이 깨지는 구간이 있으면 그 사실을 notes 에 남긴다.
    """
    if current_price <= 0:
        return TargetPrice(target_return, None, reason="현재가가 0 이하입니다")

    lo = int(current_price * min_ratio)
    hi = int(current_price * max_ratio)

    r_lo = return_at(lo)
    r_hi = return_at(hi)
    if r_lo is None or r_hi is None:
        return TargetPrice(
            target_return, None, current_price=current_price,
            reason=("탐색 범위 끝에서 수익률을 계산하지 못했습니다 — "
                    "세금·대출 규칙이 모자랄 수 있습니다"))

    notes: list[str] = []
    if r_lo < r_hi:
        notes.append(
            f"싸게 살수록 수익률이 낮게 나옵니다({r_lo:.1%} < {r_hi:.1%}). "
            f"단조성 가정이 깨졌으니 결과를 그대로 믿지 마세요")

    # 가장 싸게 사도 목표에 못 미치면 답이 없다.
    if r_lo < target_return:
        return TargetPrice(
            target_return, None, r_lo, current_price, 0,
            (f"탐색 범위에서 가장 싼 {units.fmt_eok(lo)} 에 사도 "
             f"{r_lo:.1%} 뿐입니다. 이 목표로는 살 수 없습니다 — "
             f"보유비용이 기대 상승을 넘습니다"), notes)

    # 가장 비싸게 사도 목표를 넘으면 상한이 답이다.
    if r_hi >= target_return:
        return TargetPrice(target_return, hi, r_hi, current_price, 0,
                           notes=notes + [
                               f"탐색 상한 {units.fmt_eok(hi)} 에서도 목표를 "
                               f"넘습니다. 상한을 올려 보세요"])

    # 이분법 — r(lo) >= target > r(hi)
    iterations = 0
    for iterations in range(1, MAX_ITERATIONS + 1):
        mid = (lo + hi) // 2
        if mid == lo:
            break
        r_mid = return_at(mid)
        if r_mid is None:
            return TargetPrice(
                target_return, None, current_price=current_price,
                iterations=iterations,
                reason=f"{units.fmt_eok(mid)} 에서 수익률을 계산하지 못했습니다",
                notes=notes)
        if abs(r_mid - target_return) <= TOLERANCE:
            return TargetPrice(target_return, mid, r_mid, current_price,
                               iterations, notes=notes)
        if r_mid >= target_return:
            lo = mid
        else:
            hi = mid

    return TargetPrice(target_return, lo, return_at(lo), current_price,
                       iterations, notes=notes)


def ladder(*, targets: tuple[float, ...], current_price: int,
           return_at: Callable[[int], float | None]) -> list[TargetPrice]:
    """목표 수익률 여러 개를 한 번에 (§39 매수가 구간과 이어진다).

        연 5% → 8.2억 이하
        연 7% → 7.1억 이하
        연 10% → 5.8억 이하

    이 사다리가 §39 의 STRONG BUY / FAIR / WAIT 선과 다른 점: 저 선들은
    **후보 집단 안에서의 상대 위치**로 만들고, 이건 **내가 원하는 수익률**로
    만든다. 둘이 크게 어긋나면 그 자체가 정보다 — 시장이 내 요구수익률을
    주지 않는다는 뜻이다.
    """
    return [solve(target_return=t, current_price=current_price,
                  return_at=return_at)
            for t in sorted(targets)]


DEFAULT_TARGETS = (0.05, 0.07, 0.10, 0.15)


def compare_with_bands(prices: list[TargetPrice], bands) -> list[str]:
    """요구수익률 사다리와 §39 매수가 구간이 어긋나는지."""
    out: list[str] = []
    fair = getattr(bands, "fair", None)
    if fair is None:
        return ["§39 매수가 구간이 없어 비교할 수 없습니다"]
    for tp in prices:
        if not tp.known:
            continue
        if tp.price < fair * 0.7:
            out.append(
                f"목표 {tp.target_return:.0%} 를 만족하는 가격"
                f"({units.fmt_eok(tp.price)})이 시장의 FAIR 선"
                f"({units.fmt_eok(fair)})보다 한참 아래입니다 — "
                f"시장이 이 요구수익률을 주지 않습니다")
    return out or ["요구수익률과 시장 매수가 구간이 크게 어긋나지 않습니다"]
