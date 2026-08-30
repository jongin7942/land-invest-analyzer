"""매물 스냅샷 변화 감지 (요구사항 9·53).

매일 매물을 찍어 두면 두 날짜를 비교해 무엇이 변했는지 알 수 있다.

    30일 변화
      매물      32 → 21건
      최저호가  5.9 → 6.2억
      가격인하  3건 · 가격인상 5건

한 가지 조심할 게 있다. **사라진 매물을 "거래완료"라고 부르지 않는다.**
팔려서 내린 것인지 안 팔려서 거둔 것인지 매물 데이터만으로는 알 수 없다.
그래서 "시장 이탈(거래 또는 철회)"로만 표시한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine import units


@dataclass(frozen=True)
class PriceMove:
    listing_key: str
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before

    @property
    def ratio(self) -> float:
        return self.delta / self.before if self.before else 0.0


@dataclass(frozen=True)
class Change:
    from_date: str
    to_date: str
    count_before: int
    count_after: int
    new_keys: list[str] = field(default_factory=list)
    gone_keys: list[str] = field(default_factory=list)
    cuts: list[PriceMove] = field(default_factory=list)
    raises: list[PriceMove] = field(default_factory=list)
    low_before: int | None = None
    low_after: int | None = None
    median_before: int | None = None
    median_after: int | None = None

    @property
    def count_delta(self) -> int:
        return self.count_after - self.count_before

    @property
    def low_delta_ratio(self) -> float | None:
        if not self.low_before or not self.low_after:
            return None
        return (self.low_after - self.low_before) / self.low_before

    @property
    def median_delta_ratio(self) -> float | None:
        if not self.median_before or not self.median_after:
            return None
        return (self.median_after - self.median_before) / self.median_before

    def summary_lines(self) -> list[str]:
        out = [f"{self.from_date} → {self.to_date}",
               f"매물 {self.count_before} → {self.count_after}건 "
               f"({self.count_delta:+d})"]
        if self.low_before and self.low_after:
            out.append(f"최저호가 {units.fmt_eok(self.low_before)} → "
                       f"{units.fmt_eok(self.low_after)} "
                       f"({units.fmt_pct(self.low_delta_ratio, sign=True)})")
        if self.median_before and self.median_after:
            out.append(f"중위호가 {units.fmt_eok(self.median_before)} → "
                       f"{units.fmt_eok(self.median_after)} "
                       f"({units.fmt_pct(self.median_delta_ratio, sign=True)})")
        out.append(f"신규 {len(self.new_keys)}건 · "
                   f"시장 이탈 {len(self.gone_keys)}건(거래 또는 철회 — 확정 불가)")
        out.append(f"가격인하 {len(self.cuts)}건 · 가격인상 {len(self.raises)}건")
        return out


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return int(units.won_round((ordered[mid - 1] + ordered[mid]) / 2))


def compare(before_rows: list[dict], after_rows: list[dict], *,
            from_date: str, to_date: str) -> Change:
    """두 스냅샷 비교. 각 행은 최소 listing_key 와 price 를 가져야 한다."""
    before = {r["listing_key"]: int(r["price"]) for r in before_rows}
    after = {r["listing_key"]: int(r["price"]) for r in after_rows}

    cuts, raises = [], []
    for key in before.keys() & after.keys():
        if after[key] < before[key]:
            cuts.append(PriceMove(key, before[key], after[key]))
        elif after[key] > before[key]:
            raises.append(PriceMove(key, before[key], after[key]))

    before_prices = list(before.values())
    after_prices = list(after.values())

    return Change(
        from_date=from_date, to_date=to_date,
        count_before=len(before), count_after=len(after),
        new_keys=sorted(after.keys() - before.keys()),
        gone_keys=sorted(before.keys() - after.keys()),
        cuts=sorted(cuts, key=lambda m: m.ratio),
        raises=sorted(raises, key=lambda m: -m.ratio),
        low_before=min(before_prices) if before_prices else None,
        low_after=min(after_prices) if after_prices else None,
        median_before=_median(before_prices),
        median_after=_median(after_prices),
    )
