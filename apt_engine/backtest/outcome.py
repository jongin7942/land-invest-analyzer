"""정답 계산 (§33·§34·§36·§40·§41).

⚠ 이 모듈은 **미래를 본다.** 그게 목적이다 — 채점하려면 답을 봐야 한다.
그래서 컷오프 guard 를 쓰지 않고 원래 커넥션으로 조회한다. 대신 반대편을 막았다:
`blind/cutoff.py` 의 `ANSWER_KEY_TABLES` 때문에, Feature 코드는 여기서 만든
결과 테이블을 **어떤 조건을 붙여도** 읽을 수 없다.

정답은 우리가 고른 것뿐 아니라 **후보 전체** 에 대해 계산한다.
고른 것만 채점하면 Regret(§35)도 Missed Winner(§41)도 계산할 수 없다.
놓친 것을 봐야 놓친 걸 안다.

값을 못 내면 0 이나 평균으로 채우지 않고 `unknown_reason` 을 남긴다(§67).
스키마의 CHECK 가 그걸 강제한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from apt_engine.backtest import windows as windows_mod

# Winner 4상태 (§41)
WINNER_FOUND = "WINNER_FOUND"
MISSED_WINNER = "MISSED_WINNER"
FALSE_POSITIVE = "FALSE_POSITIVE"
CORRECT_REJECT = "CORRECT_REJECT"

# 상위 몇 %를 "실제로 좋았던 것" 으로 볼 것인가.
# **판정 기준**이다 — 관측이 아니다. 바꾸면 Recall 도 Precision 도 같이 바뀐다.
WINNER_TOP_FRACTION = 0.20
WINNER_NOTE = ("Winner 기준은 동시점 후보 상위 20% 입니다. 절대 수익률 기준이 "
               "아니라 상대 기준이라 하락장에서도 정의됩니다(§56)")

# 상승 시작으로 볼 최소 상승폭 (§40 Discovery Lag)
RISE_THRESHOLD = 0.05
RISE_SUSTAIN_MONTHS = 3


@dataclass(frozen=True)
class Outcome:
    complex_id: int
    area_band: str
    entry_price: int | None = None
    exit_price: int | None = None
    forward_return: float | None = None
    annualized: float | None = None
    max_drawdown: float | None = None
    trough_ym: str | None = None
    recovery_months: int | None = None
    recovered: bool | None = None
    rise_start_ym: str | None = None
    months_late: int | None = None
    sample_n: int = 0
    unknown_reason: str | None = None
    # 아래 둘은 후보 집단이 있어야 정해진다 — 개별 계산에서는 비어 있다.
    ex_post_rank: int | None = None
    winner_class: str | None = None
    picked: bool = False

    @property
    def known(self) -> bool:
        return self.forward_return is not None

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.unknown_reason}"
        parts = [f"{self.forward_return:+.1%}"]
        if self.max_drawdown is not None:
            parts.append(f"MDD {self.max_drawdown:.1%}")
        if self.recovery_months is not None:
            parts.append(f"회복 {self.recovery_months}개월")
        elif self.recovered is False:
            parts.append("미회복")
        return " · ".join(parts)


def price_series(conn: sqlite3.Connection, complex_id: int, area_band: str,
                 *, start_ym: str, end_ym: str) -> list[tuple[str, int]]:
    """월별 대표가격. 채점용이라 컷오프를 걸지 않는다(그게 정답지의 정의다)."""
    rows = conn.execute(
        "SELECT as_of_ym, representative_price, sample_n FROM price_snapshot "
        " WHERE complex_id=? AND area_band=? AND as_of_ym>=? AND as_of_ym<=? "
        "   AND representative_price IS NOT NULL "
        " ORDER BY as_of_ym",
        (complex_id, area_band, start_ym, end_ym)).fetchall()
    return [(r["as_of_ym"], int(r["representative_price"])) for r in rows]


def compute(conn: sqlite3.Connection, complex_id: int, area_band: str, *,
            window: windows_mod.Window,
            entry_tolerance_months: int = 3,
            exit_tolerance_months: int = 3) -> Outcome:
    """단지 하나의 정답.

    진입가·청산가를 **찾지 못하면 추정하지 않는다.** 앞뒤 몇 달까지는 허용하되
    (스냅샷이 매달 있는 게 보장되지 않으므로), 그 밖이면 확인 불가로 남긴다.
    없는 가격을 보간해서 만들면 그 백테스트 성적은 우리가 만든 숫자를 채점한 것이
    된다.
    """
    entry_ym, exit_ym = window.as_of_ym, window.eval_ym
    lo = _shift_ym(entry_ym, -entry_tolerance_months)
    hi = _shift_ym(exit_ym, exit_tolerance_months)
    series = price_series(conn, complex_id, area_band, start_ym=lo, end_ym=hi)
    if not series:
        return Outcome(complex_id, area_band,
                       unknown_reason=f"{lo}~{hi} 구간에 가격 스냅샷이 없습니다")

    entry = _nearest(series, entry_ym, entry_tolerance_months)
    if entry is None:
        return Outcome(complex_id, area_band, sample_n=len(series),
                       unknown_reason=(f"진입 시점 {entry_ym} 근처"
                                       f"(±{entry_tolerance_months}개월)에 가격이 "
                                       f"없습니다 — 보간하지 않습니다"))
    exit_ = _nearest(series, exit_ym, exit_tolerance_months)
    if exit_ is None:
        return Outcome(complex_id, area_band, entry_price=entry[1],
                       sample_n=len(series),
                       unknown_reason=(f"채점 시점 {exit_ym} 근처"
                                       f"(±{exit_tolerance_months}개월)에 가격이 "
                                       f"없습니다"))

    entry_price, exit_price = entry[1], exit_[1]
    if entry_price <= 0:
        return Outcome(complex_id, area_band, sample_n=len(series),
                       unknown_reason="진입가가 0 이하입니다")

    fwd = (exit_price - entry_price) / entry_price
    years = window.horizon_years
    annualized = (1 + fwd) ** (1 / years) - 1 if fwd > -1 else None

    held = [p for p in series if entry[0] <= p[0] <= exit_[0]]
    mdd, trough = _drawdown(held)
    rec_months, recovered = _recovery(held, trough)
    rise_ym = _rise_start(series, before=entry[0])
    late = _months_between(rise_ym, entry[0]) if rise_ym else None

    return Outcome(complex_id, area_band, entry_price, exit_price, fwd,
                   annualized, mdd, trough, rec_months, recovered,
                   rise_ym, late, len(held))


def classify(outcomes: list[Outcome], picked_ids: set[int], *,
             top_fraction: float = WINNER_TOP_FRACTION) -> list[Outcome]:
    """후보 집단을 놓고 사후 순위와 Winner 4상태를 매긴다 (§34·§41).

    확인 불가인 후보는 순위에서 **제외**한다. 0% 수익으로 세면 그 후보가
    "평범했다" 는 사실을 만들어낸 것이 된다.
    """
    known = [o for o in outcomes if o.known]
    if not known:
        return [o for o in outcomes]

    ordered = sorted(known, key=lambda o: (-o.forward_return, o.complex_id))
    rank_of = {o.complex_id: i + 1 for i, o in enumerate(ordered)}
    cut = max(1, int(round(len(ordered) * top_fraction)))
    winners = {o.complex_id for o in ordered[:cut]}

    out: list[Outcome] = []
    for o in outcomes:
        picked = o.complex_id in picked_ids
        if not o.known:
            # 정답을 모르면 Winner 판정도 못 한다. picked 만 기록한다.
            out.append(_with(o, picked=picked))
            continue
        is_winner = o.complex_id in winners
        cls = (WINNER_FOUND if (picked and is_winner) else
               FALSE_POSITIVE if (picked and not is_winner) else
               MISSED_WINNER if (not picked and is_winner) else
               CORRECT_REJECT)
        out.append(_with(o, picked=picked, winner_class=cls,
                         ex_post_rank=rank_of[o.complex_id]))
    return out


def save(conn: sqlite3.Connection, window_id: int,
         outcomes: list[Outcome]) -> int:
    conn.execute("DELETE FROM backtest_outcome WHERE window_id=?", (window_id,))
    for o in outcomes:
        conn.execute(
            "INSERT INTO backtest_outcome (window_id, complex_id, area_band, "
            " entry_price, exit_price, forward_return, annualized, max_drawdown, "
            " trough_ym, recovery_months, recovered, rise_start_ym, months_late, "
            " winner_class, picked, ex_post_rank, sample_n, unknown_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (window_id, o.complex_id, o.area_band, o.entry_price, o.exit_price,
             o.forward_return, o.annualized, o.max_drawdown, o.trough_ym,
             o.recovery_months,
             None if o.recovered is None else int(o.recovered),
             o.rise_start_ym, o.months_late, o.winner_class, int(o.picked),
             o.ex_post_rank, o.sample_n, o.unknown_reason))
    return len(outcomes)


# ── 내부 계산 ────────────────────────────────────────────────────────

def _with(o: Outcome, **kw) -> Outcome:
    from dataclasses import replace
    return replace(o, **kw)


def _nearest(series: list[tuple[str, int]], target_ym: str,
             tolerance: int) -> tuple[str, int] | None:
    """목표 월에 가장 가까운 관측. 허용 범위를 벗어나면 None(보간하지 않는다)."""
    best = None
    best_gap = tolerance + 1
    for ym, price in series:
        gap = abs(_months_between(ym, target_ym))
        if gap < best_gap or (gap == best_gap and best and ym < best[0]):
            best, best_gap = (ym, price), gap
    return best if best_gap <= tolerance else None


def _drawdown(series: list[tuple[str, int]]) -> tuple[float | None, str | None]:
    """보유기간 중 최대 낙폭 (§36). 고점 대비 저점이지 시작 대비가 아니다."""
    if len(series) < 2:
        return None, None
    peak = series[0][1]
    worst, trough = 0.0, None
    for ym, price in series:
        peak = max(peak, price)
        if peak <= 0:
            continue
        drop = (price - peak) / peak
        if drop < worst:
            worst, trough = drop, ym
    return (worst, trough) if trough else (0.0, series[0][0])


def _recovery(series: list[tuple[str, int]],
              trough_ym: str | None) -> tuple[int | None, bool | None]:
    """저점에서 직전 고점을 되찾기까지 몇 달인가 (§36).

    구간 안에서 회복하지 못했으면 개월수를 만들어내지 않는다 —
    `(None, False)` 는 "아직 회복 못 했다" 이고, `(None, None)` 은 "모른다" 다.
    """
    if not trough_ym or len(series) < 2:
        return None, None
    idx = next((i for i, (ym, _) in enumerate(series) if ym == trough_ym), None)
    if idx is None:
        return None, None
    prior_peak = max(p for _, p in series[:idx + 1])
    for j in range(idx + 1, len(series)):
        if series[j][1] >= prior_peak:
            return _months_between(trough_ym, series[j][0]), True
    return None, False


def _rise_start(series: list[tuple[str, int]], *, before: str) -> str | None:
    """실제 상승이 시작된 달 (§40 Discovery Lag 의 기준점).

    저점 이후 `RISE_SUSTAIN_MONTHS` 동안 `RISE_THRESHOLD` 이상 올랐고 그 뒤로
    저점 아래로 다시 내려가지 않은 첫 달을 상승 시작으로 본다.
    조건에 맞는 달이 없으면 None — "상승이 없었다" 와 "못 찾았다" 를 구분하지
    않는 대신, 호출부에서 months_late 를 None 으로 남긴다.
    """
    hist = [p for p in series if p[0] <= before]
    if len(hist) < RISE_SUSTAIN_MONTHS + 1:
        return None
    for i in range(len(hist) - RISE_SUSTAIN_MONTHS):
        base_ym, base = hist[i]
        if base <= 0:
            continue
        later = hist[i + 1:i + 1 + RISE_SUSTAIN_MONTHS]
        if not later:
            continue
        if all(p >= base for _, p in later) and \
           (later[-1][1] - base) / base >= RISE_THRESHOLD:
            return base_ym
    return None


def _shift_ym(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:6]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def _months_between(a: str, b: str) -> int:
    return ((int(b[:4]) * 12 + int(b[4:6])) - (int(a[:4]) * 12 + int(a[4:6])))
