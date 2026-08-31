"""현금 버킷 (지시서 §27).

> 투자 가능 금액을 2억 / 2.5억 / 3억 / 3.5억 / 4억 / 5억 / 6억 / 8억 / 10억
> 으로 나눠서 각각 TOP10 을 만든다.

한 금액으로만 돌리면 **자본 구간이 바뀔 때 답이 어떻게 달라지는지**를 못 본다.
"3억이면 A, 3.5억이면 B" 라는 사실 자체가 정보다 — 5천만원을 더 모으는 것이
의미가 있는지 없는지를 그 비교가 알려준다(§30 Capital Frontier).

숫자는 지시서가 준 것이고, 관측이나 추정이 아니다.
"""
from __future__ import annotations

from dataclasses import dataclass

from apt_engine import units

# §27 이 명시한 9종. 원 단위.
BUCKETS: tuple[int, ...] = tuple(int(v * 100_000_000) for v in
                                 (2, 2.5, 3, 3.5, 4, 5, 6, 8, 10))

BUCKET_NOTE = "§27 이 지정한 9개 구간입니다. 관측값이 아니라 분석 격자입니다"


def nearest(cash: int) -> int:
    """주어진 현금이 속하는 버킷(그 이하 중 가장 큰 것).

    버킷보다 적으면 가장 작은 버킷을 돌려주지 않는다 — 2억 미만은 이 격자가
    다루는 구간이 아니고, 없는 답을 만들어 주는 것보다 없다고 하는 게 낫다.
    """
    below = [b for b in BUCKETS if b <= cash]
    return max(below) if below else 0


@dataclass(frozen=True)
class Step:
    """한 버킷에서 다음 버킷으로 갈 때 무엇이 달라지는가 (§30)."""
    frm: int
    to: int
    extra_cash: int
    gained: list[int]                  # 새로 살 수 있게 된 단지
    lost: list[int]                    # 더 이상 상위에 없는 단지
    best_score_delta: float | None     # 1위 점수 변화. 모르면 None

    @property
    def label(self) -> str:
        if self.best_score_delta is None:
            return (f"{units.fmt_eok(self.frm)} → {units.fmt_eok(self.to)}: "
                    f"점수 변화 확인 불가")
        arrow = "↑" if self.best_score_delta > 0 else (
            "↓" if self.best_score_delta < 0 else "→")
        return (f"{units.fmt_eok(self.frm)} → {units.fmt_eok(self.to)} "
                f"(+{units.fmt_eok(self.extra_cash)}): "
                f"1위 점수 {arrow}{abs(self.best_score_delta):.1f} · "
                f"신규 {len(self.gained)}개")


def frontier(by_bucket: dict[int, list[int]],
             scores: dict[int, float | None]) -> list[Step]:
    """버킷을 올릴 때마다 무엇을 얻는가.

    `by_bucket`  버킷 → 그 버킷의 TOP 후보 id 목록 (순위 순)
    `scores`     버킷 → 그 버킷 1위 점수 (없으면 None)

    §31 Alternative Purchase Test 가 이 위에 올라간다: "돈을 더 넣어서 얻는 것이
    실제로 더 나은가, 아니면 그냥 더 비싼 걸 사는 것인가."
    """
    keys = sorted(by_bucket)
    out: list[Step] = []
    for lo, hi in zip(keys, keys[1:]):
        a, b = by_bucket[lo], by_bucket[hi]
        sa, sb = scores.get(lo), scores.get(hi)
        delta = None if (sa is None or sb is None) else sb - sa
        out.append(Step(lo, hi, hi - lo,
                        [c for c in b if c not in set(a)],
                        [c for c in a if c not in set(b)], delta))
    return out
