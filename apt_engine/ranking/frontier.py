"""Capital Frontier · Alternative Purchase Test (지시서 §30·§31·§27).

§27 이 현금 버킷 9종을 요구한 이유는 표를 예쁘게 만들려는 게 아니다.

    3억이면 A, 3.5억이면 B

이 사실 자체가 정보다. 5천만원을 더 모으는 것이 의미가 있는지 없는지를
그 비교가 알려준다. 버킷 하나만 돌리면 그걸 볼 수 없다.

§30 Capital Frontier 는 그 계단을 그린다.

    2.0억 → 2.5억   1위 점수 +0.3   신규 후보 1개   →  거의 의미 없음
    2.5억 → 3.0억   1위 점수 +12.4  신규 후보 9개   →  여기가 문턱이다
    3.0억 → 3.5억   1위 점수 +1.1   신규 후보 2개

문턱이 어디인지 알면 "얼마를 더 모아야 하는가" 에 답할 수 있다.

§31 Alternative Purchase Test 는 반대 방향이다.

> 돈을 더 넣어서 얻는 것이 실제로 더 나은가, 아니면 그냥 더 비싼 걸 사는 것인가.

자기자본을 1억 더 넣었는데 기대수익률이 그대로면, 더 비싼 걸 샀을 뿐 더 나은
투자를 한 게 아니다. **금액이 아니라 자기자본 대비 수익률로 비교한다.**
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine import units
from apt_engine.invest import buckets as bucket_mod

# 이 정도 점수 차이는 잡음으로 본다. **판정 기준이지 관측이 아니다.**
MEANINGFUL_SCORE_GAIN = 3.0
# 자기자본 대비 수익률이 이만큼은 올라야 "더 나은 투자" 로 본다.
MEANINGFUL_ROE_GAIN = 0.02

NOTE = ("버킷 간 비교는 금액이 아니라 **자기자본 대비**로 합니다(§31). "
        "더 비싼 걸 사는 것과 더 나은 투자를 하는 것은 다릅니다")


@dataclass(frozen=True)
class Rung:
    """사다리 한 칸 — 버킷 하나의 결과."""
    cash: int
    best_score: float | None
    best_id: int | None
    candidate_ids: list[int] = field(default_factory=list)
    best_roe: float | None = None          # 자기자본 대비 기대수익률
    executable_n: int = 0
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.best_score is not None

    @property
    def label(self) -> str:
        if not self.known:
            return f"{units.fmt_eok(self.cash)}: 확인 불가 — {self.reason}"
        roe = f" · 자기자본 대비 {self.best_roe:+.1%}" if self.best_roe is not None else ""
        return (f"{units.fmt_eok(self.cash)}: 1위 {self.best_score:.1f}점"
                f"{roe} · 매수가능 {self.executable_n}개")


@dataclass(frozen=True)
class Step:
    """칸에서 칸으로 — 무엇이 달라지는가 (§30)."""
    frm: Rung
    to: Rung
    score_gain: float | None
    roe_gain: float | None
    gained: list[int] = field(default_factory=list)
    lost: list[int] = field(default_factory=list)

    @property
    def extra_cash(self) -> int:
        return self.to.cash - self.frm.cash

    @property
    def threshold(self) -> bool | None:
        """여기가 문턱인가 — 돈을 더 넣을 가치가 있는 구간인가."""
        if self.score_gain is None:
            return None
        return self.score_gain >= MEANINGFUL_SCORE_GAIN

    @property
    def better_investment(self) -> bool | None:
        """§31 — 더 나은 투자인가, 그냥 더 비싼 걸 사는 것인가."""
        if self.roe_gain is None:
            return None
        return self.roe_gain >= MEANINGFUL_ROE_GAIN

    @property
    def label(self) -> str:
        head = (f"{units.fmt_eok(self.frm.cash)} → {units.fmt_eok(self.to.cash)} "
                f"(+{units.fmt_eok(self.extra_cash)})")
        if self.score_gain is None:
            return f"{head}: 확인 불가"
        mark = "★ 문턱" if self.threshold else "  "
        roe = ("자기자본 대비 확인 불가" if self.roe_gain is None else
               f"자기자본 대비 {self.roe_gain:+.1%}")
        verdict = ""
        if self.better_investment is False:
            verdict = " — 더 비싼 걸 살 뿐 더 나은 투자가 아닙니다(§31)"
        elif self.better_investment:
            verdict = " — 실제로 더 나은 투자입니다"
        return (f"{mark} {head}: 1위 점수 {self.score_gain:+.1f} · {roe} · "
                f"신규 {len(self.gained)}개{verdict}")


@dataclass
class Frontier:
    rungs: list[Rung] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    @property
    def thresholds(self) -> list[Step]:
        return [s for s in self.steps if s.threshold]

    @property
    def summary(self) -> str:
        lines = ["Capital Frontier (§30)"]
        for r in self.rungs:
            lines.append(f"  {r.label}")
        lines.append("")
        lines.append("  ── 칸을 올릴 때 ──")
        for s in self.steps:
            lines.append(f"  {s.label}")
        if self.thresholds:
            names = " · ".join(units.fmt_eok(s.to.cash) for s in self.thresholds)
            lines.append(f"\n  문턱: {names} — 여기까지 모으면 답이 바뀝니다")
        else:
            lines.append("\n  뚜렷한 문턱이 없습니다 — 돈을 더 모아도 "
                         "답이 크게 바뀌지 않습니다")
        lines.append(f"  {NOTE}")
        return "\n".join(lines)


def build(results: dict[int, object]) -> Frontier:
    """버킷별 결과 → 사다리.

    `results` 는 현금 → DeltaResult. 각 버킷을 따로 돌린 결과를 넣는다.
    """
    rungs: list[Rung] = []
    for cash in sorted(results):
        rungs.append(_rung(cash, results[cash]))

    steps: list[Step] = []
    for lo, hi in zip(rungs, rungs[1:]):
        score_gain = (None if (lo.best_score is None or hi.best_score is None)
                      else hi.best_score - lo.best_score)
        roe_gain = (None if (lo.best_roe is None or hi.best_roe is None)
                    else hi.best_roe - lo.best_roe)
        a, b = set(lo.candidate_ids), set(hi.candidate_ids)
        steps.append(Step(lo, hi, score_gain, roe_gain,
                          sorted(b - a), sorted(a - b)))
    return Frontier(rungs, steps)


def _rung(cash: int, result) -> Rung:
    split = getattr(result, "split", None)
    executable = list(getattr(split, "executable", []) or [])
    if not executable:
        return Rung(cash, None, None, [], None, 0,
                    "매수 가능한 후보가 없습니다")
    best = executable[0]
    score = best.alpha.alpha if best.alpha.known else None
    roe = _roe(best, cash)
    return Rung(cash, score, best.complex_id,
                [c.complex_id for c in executable], roe, len(executable))


def _roe(candidate, cash: int) -> float | None:
    """§31 — 자기자본 대비 기대수익률.

    **실투자금이 아니라 자기자본(cash) 으로 나눈다.** 실투자금으로 나누면
    레버리지를 크게 쓴 후보가 항상 이기고, 남는 현금이 노는 것을 못 본다(§2).
    """
    if not candidate.alpha.known or not cash:
        return None
    # Alpha 0~100 을 기대수익률로 읽는다(가중치 학습 전이라 근사).
    expected = candidate.alpha.alpha / 100.0
    required = candidate.required_equity
    if required is None:
        return None
    deployed = min(required, cash)
    return expected * deployed / cash


def alternative_purchase(cheaper, pricier, *, cash: int
                         ) -> tuple[bool | None, str]:
    """§31 — 더 비싼 걸 사는 것이 실제로 더 나은가.

    두 후보의 **자기자본 대비** 수익률을 비교한다. 금액으로 비교하면
    비싼 게 항상 이긴다.
    """
    a, b = _roe(cheaper, cash), _roe(pricier, cash)
    if a is None or b is None:
        return None, ("실투자금이나 기대수익을 몰라 비교할 수 없습니다 — "
                      "금액으로 비교하면 비싼 것이 항상 이깁니다")
    if b - a >= MEANINGFUL_ROE_GAIN:
        return True, (f"자기자본 대비 {a:+.1%} → {b:+.1%} — "
                      f"더 나은 투자입니다")
    return False, (f"자기자본 대비 {a:+.1%} → {b:+.1%} — "
                   f"더 비싼 걸 살 뿐 더 나은 투자가 아닙니다")


def default_buckets(cash: int | None = None) -> tuple[int, ...]:
    """§27 의 9종. 현금을 주면 그 아래위만 본다 (전부 돌리면 느리다)."""
    if cash is None:
        return bucket_mod.BUCKETS
    near = [b for b in bucket_mod.BUCKETS
            if 0.5 * cash <= b <= 2.0 * cash]
    return tuple(near) or bucket_mod.BUCKETS
