"""시점 창 — walk-forward 는 "과거에 서서 미래를 모르는 척" 하는 것이다 (§55·§72).

한 번 학습하고 전 구간을 채점하는 방식(in-sample)은 언제나 좋은 성적이 나온다.
walk-forward 는 그걸 막는다.

    as_of 2018-01  ─ 결정 ─▶            2020-01 채점  (2년)
    as_of 2018-07  ─ 결정 ─▶            2020-07 채점
    as_of 2019-01  ─ 결정 ─▶            2021-01 채점
      …

각 창은 **자기보다 앞선 데이터만** 본다. 채점 결과는 그 창의 결정에 영향을 주지
않는다. 창끼리 기간이 겹치는 것은 정상이다 — 겹친다고 표본 수를 그만큼 곱해서
세면 안 되고, KPI 는 그래서 `sample_n` 을 창 단위로 따로 기록한다.

§72 분할은 **시간으로** 한다:

    ├──────── TRAIN 60% ────────┤── VALIDATION 20% ──┤──── OOT 20% ────┤

무작위 분할을 쓰면 2021년 데이터로 학습해서 2019년을 맞히게 된다. 성적은 좋지만
실전에서는 불가능한 일이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from apt_engine import rules

TRAIN, VALIDATION, OOT = "TRAIN", "VALIDATION", "OOT"

# §72 분할 비율. 관측이 아니라 실험 설계 값이다.
TRAIN_FRACTION = 0.60
VALIDATION_FRACTION = 0.20
SPLIT_NOTE = ("분할은 시간 순서로 합니다. 무작위 분할은 미래로 과거를 맞히는 "
              "것과 같아서 성적이 부풀려집니다(§72)")

# §47 2Y / 5Y / 10Y
DEFAULT_HORIZONS = (2, 5, 10)
DEFAULT_STEP_MONTHS = 6


@dataclass(frozen=True)
class Window:
    as_of: str                  # 결정 시점 = 데이터 컷오프
    horizon_years: int
    eval_day: str               # 채점 시점
    split: str
    scorable: bool              # 채점 시점의 데이터가 있는가
    skip_reason: str | None = None

    @property
    def as_of_ym(self) -> str:
        return self.as_of[:4] + self.as_of[5:7]

    @property
    def eval_ym(self) -> str:
        return self.eval_day[:4] + self.eval_day[5:7]

    @property
    def label(self) -> str:
        tail = "" if self.scorable else f"  (채점 불가: {self.skip_reason})"
        return (f"{self.as_of} +{self.horizon_years}년 → {self.eval_day} "
                f"[{self.split}]{tail}")


def add_years(day: str, years: int) -> str:
    d = date.fromisoformat(rules.as_ymd(day))
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:                      # 2/29
        return d.replace(year=d.year + years, day=28).isoformat()


def add_months(day: str, months: int) -> str:
    d = date.fromisoformat(rules.as_ymd(day))
    total = d.year * 12 + (d.month - 1) + months
    year, month = total // 12, total % 12 + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, last)).isoformat()


def generate(data_start: str, data_end: str, *,
             horizons: tuple[int, ...] = DEFAULT_HORIZONS,
             step_months: int = DEFAULT_STEP_MONTHS,
             warmup_months: int = 12,
             train_fraction: float = TRAIN_FRACTION,
             validation_fraction: float = VALIDATION_FRACTION) -> list[Window]:
    """데이터 구간 안에서 만들 수 있는 창 전부.

    `warmup_months` 만큼은 창을 만들지 않는다. 데이터 시작 직후 시점은
    momentum·regime 같은 과거 의존 feature 를 계산할 재료가 없어서, 창을
    만들어 봐야 "데이터 부족" 만 잔뜩 나온다.

    채점 시점이 데이터 끝을 넘는 창도 **버리지 않고** `scorable=False` 로 남긴다.
    몇 개가 왜 채점되지 않았는지 말할 수 있어야 하기 때문이다.
    """
    start = rules.as_ymd(data_start)
    end = rules.as_ymd(data_end)
    if end <= start:
        raise ValueError(f"데이터 구간이 뒤집혔습니다: {start} ~ {end}")

    first = add_months(start, warmup_months)
    stamps: list[str] = []
    cursor = first
    while cursor <= end:
        stamps.append(cursor)
        cursor = add_months(cursor, step_months)
    if not stamps:
        return []

    splits = assign_splits(stamps, train_fraction=train_fraction,
                           validation_fraction=validation_fraction)

    out: list[Window] = []
    for stamp in stamps:
        for horizon in horizons:
            eval_day = add_years(stamp, horizon)
            scorable = eval_day <= end
            reason = None if scorable else (
                f"채점 시점 {eval_day} 이 데이터 끝 {end} 을 넘습니다 — "
                f"아직 정답이 존재하지 않습니다")
            out.append(Window(stamp, horizon, eval_day, splits[stamp],
                              scorable, reason))
    return out


def assign_splits(stamps: list[str], *,
                  train_fraction: float = TRAIN_FRACTION,
                  validation_fraction: float = VALIDATION_FRACTION
                  ) -> dict[str, str]:
    """시간 순서로 분할 (§72). 기본은 60/20/20.

    경계는 **개수** 가 아니라 순서로 정한다. 같은 날짜가 두 분할에 걸치는 일이
    없어야 하고, TRAIN 의 마지막 날짜가 VALIDATION 의 첫 날짜보다 뒤면 안 된다.

    비율을 조정할 수 있게 둔 이유는 보유기간이 길면 60/20/20 으로는 검증이
    불가능하기 때문이다. 2년 보유를 검증하려면 겹치지 않는 창 3개, 즉 **6년**
    짜리 VALIDATION 구간이 필요하다. 20년 데이터의 20% 는 4년이라 모자란다.
    학습 구간을 줄여서 검증 구간을 늘릴지는 실험 설계자가 정한다.
    """
    ordered = sorted(set(stamps))
    n = len(ordered)
    if n == 0:
        return {}
    if n < 3:
        # 창이 셋도 안 되면 분할이 의미가 없다. 전부 OOT 로 두어
        # "학습에 쓸 수 있는 구간이 없다" 는 사실이 드러나게 한다.
        return {s: OOT for s in ordered}
    train_end = max(1, int(n * train_fraction))
    val_end = max(train_end + 1,
                  int(n * (train_fraction + validation_fraction)))
    val_end = min(val_end, n - 1)          # OOT 는 최소 하나 남긴다
    out = {}
    for i, s in enumerate(ordered):
        out[s] = TRAIN if i < train_end else (VALIDATION if i < val_end else OOT)
    return out


def boundary(windows: list[Window]) -> dict[str, tuple[str, str] | None]:
    """분할별 실제 기간. 리포트에 찍어서 겹치지 않는지 눈으로 확인한다."""
    out: dict[str, tuple[str, str] | None] = {}
    for split in (TRAIN, VALIDATION, OOT):
        days = sorted(w.as_of for w in windows if w.split == split)
        out[split] = (days[0], days[-1]) if days else None
    return out


def overlaps(windows: list[Window]) -> list[str]:
    """분할이 시간상 겹치면 그 사실을 문장으로 돌려준다. 겹치면 §72 위반이다."""
    bounds = boundary(windows)
    problems = []
    order = [(TRAIN, VALIDATION), (VALIDATION, OOT), (TRAIN, OOT)]
    for a, b in order:
        ba, bb = bounds.get(a), bounds.get(b)
        if ba and bb and ba[1] >= bb[0]:
            problems.append(f"{a} 의 끝({ba[1]})이 {b} 의 시작({bb[0]})보다 뒤입니다")
    return problems


def embargo_conflicts(windows: list[Window]) -> list[str]:
    """정답 기간이 다음 분할의 결정 시점을 덮는 창 (§72 의 숨은 함정).

    분할을 시간으로 잘라도 이 문제가 남는다.

        TRAIN  as_of 2021-07 ─ 5년 정답 ─▶ 2026-07
        VALID  as_of 2022-01  ← 이 시점의 시장을 TRAIN 의 정답이 이미 알고 있다

    학습 표본의 **정답 구간**이 검증 구간과 겹치면, 검증은 더 이상 독립이 아니다.
    성적이 좋게 나오지만 그건 같은 시장을 두 번 본 것이다.

    이걸 자동으로 잘라내지 않는다 — horizon 이 길면 겹침은 불가피하고, 무엇을
    버릴지는 실험 설계다. 대신 **몇 개가 겹치는지 반드시 보고한다.**
    """
    bounds = boundary(windows)
    out: list[str] = []
    for train_split, later in ((TRAIN, VALIDATION), (TRAIN, OOT),
                               (VALIDATION, OOT)):
        later_bound = bounds.get(later)
        if not later_bound:
            continue
        later_start = later_bound[0]
        bad = [w for w in windows
               if w.split == train_split and w.scorable and w.eval_day > later_start]
        if bad:
            out.append(
                f"{train_split} 창 {len(bad)}개의 정답 구간이 {later} 시작"
                f"({later_start}) 이후까지 이어집니다 "
                f"(최장 {max(w.eval_day for w in bad)}). "
                f"{later} 성적을 완전히 독립적인 것으로 읽으면 안 됩니다")
    return out


def purge(windows: list[Window], *, keep_split: str = TRAIN) -> list[Window]:
    """정답 구간이 다음 분할을 침범하는 창을 뺀다 (embargo).

    표본이 줄어드는 대신 검증이 독립이 된다. 어느 쪽을 택할지는 호출부가 정하고,
    어느 쪽이든 `embargo_conflicts()` 결과를 리포트에 남긴다.
    """
    bounds = boundary(windows)
    later_start = None
    for later in (VALIDATION, OOT):
        b = bounds.get(later)
        if b:
            later_start = b[0]
            break
    if later_start is None:
        return list(windows)
    return [w for w in windows
            if w.split != keep_split or not w.scorable or w.eval_day <= later_start]


def independent_count(windows: list[Window]) -> int:
    """서로 겹치지 않는 창의 최대 개수 = 실제 독립 관측 수.

    겹친 창은 같은 기간을 두 번 채점한 것이라 표본이 늘어난 게 아니다.
    """
    spans = sorted({(w.as_of, w.eval_day) for w in windows if w.scorable})
    end = ""
    n = 0
    for start, stop in spans:
        if start >= end:
            n += 1
            end = stop
    return n


def power_report(windows: list[Window], *, min_independent: int = 3) -> list[str]:
    """이 설계로 학습이 **가능하기는 한가** 를 미리 말해 준다.

    돌리고 나서 "VALIDATION 이 부족해서 판정 못 함" 을 보는 것보다,
    돌리기 전에 "이 구간·이 horizon 으로는 독립 관측이 2개뿐이라 확인이
    불가능하다" 를 아는 게 낫다. 보유기간이 길수록 이 문제가 커진다 —
    2년 보유면 6년짜리 검증 구간이 있어야 독립 관측 3개가 나온다.
    """
    out: list[str] = []
    short: list[str] = []
    for split in (TRAIN, VALIDATION, OOT):
        subset = [w for w in windows if w.split == split]
        if not subset:
            out.append(f"{split}: 창이 없습니다")
            continue
        for horizon in sorted({w.horizon_years for w in subset}):
            same = [w for w in subset if w.horizon_years == horizon]
            n = independent_count(same)
            verdict = ("충분" if n >= min_independent else
                       f"부족 — {min_independent}개 필요")
            out.append(
                f"{split} {horizon}년: 창 {len(same)}개 · 독립 관측 {n}개 · {verdict}")
            if n < min_independent:
                short.append(
                    f"{split} 에서 {horizon}년 보유를 검증하려면 "
                    f"{min_independent * horizon}년짜리 구간이 필요합니다")
    for line in sorted(set(short)):
        out.append(f"→ {line}")
    return out
