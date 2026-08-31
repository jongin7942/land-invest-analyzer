"""사이클 — Excess Reset Completion · Path-Dependent Valuation
   (신규 지시서 §18·§19).

§18 이 잡으려는 것은 2021 → 2023 → 현재 같은 한 사이클이다.

    과열 → 조정 → 거래소멸 → 가격밴드 안정 → 전세 유지 → 바닥 회복 → 거래 회복

일곱 단계를 **순서대로** 확인한다. 순서가 중요한 이유는 §49-6 때문이다.

> 단순 고점 대비 -30% 라는 이유로 가산하지 않는다.

고점 대비 -30% 는 두 가지를 뜻할 수 있다.

    ① 조정이 끝나고 회복이 시작됐다      → 좋다
    ② 아직 떨어지는 중이다                → 나쁘다

둘은 하락률이 같다. 구분하려면 **그 뒤에 무슨 일이 있었는지**를 봐야 한다.
거래가 마르고, 밴드가 멈추고, 전세가 버티고, 바닥이 올라온 순서가 있어야
①이다. 그 순서가 없으면 그냥 떨어지는 중이다.

§19 는 한 걸음 더 간다.

> 같은 현재가격이라도 도달 경로가 다르면 동일한 투자상품으로 평가하지 않는다.

    A  3.0 → 4.0 → 5.5   급등해서 도달한 5.5억
    B  6.5 → 4.5 → 5.5   조정 후 회복해서 도달한 5.5억

가격은 같지만 A 는 위에 아무 저항이 없고 아래는 비어 있다. B 는 6.5억까지
거래된 이력이 있어 위쪽에 실제 수요가 있었다는 증거가 있고, 4.5억에서
멈췄으므로 아래쪽 지지선도 확인됐다. **같은 점수를 주면 안 된다.**
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine.features import bands as bands_mod
from apt_engine.features.base import Feature, Status, sample_confidence
from apt_engine.trace import Calc

# §18 일곱 단계
OVERHEAT = "과열"
CORRECTION = "조정"
DRY_UP = "거래소멸"
STABILIZE = "밴드안정"
JEONSE_HOLD = "전세유지"
FLOOR_RECOVERY = "바닥회복"
TXN_RECOVERY = "거래회복"
RESET_SEQUENCE = (OVERHEAT, CORRECTION, DRY_UP, STABILIZE, JEONSE_HOLD,
                  FLOOR_RECOVERY, TXN_RECOVERY)

# 판정 기준. **관측이 아니라 기준이다** — 백테스트가 대체한다.
OVERHEAT_RISE = 0.25          # 이만큼 오른 뒤라야 '과열' 이었다고 본다
CORRECTION_DROP = -0.10       # 이만큼 빠져야 '조정'
DRY_UP_RATIO = 0.6            # 거래건수가 과열기 대비 이 아래로
STABLE_BAND = 0.03            # 밴드가 이 안에서만 움직이면 '안정'
FLOOR_RISE = 0.01             # P25 가 이만큼 오르면 '바닥 회복'
MIN_MONTHS = 30               # 사이클을 보려면 최소 이만큼
THRESHOLD_NOTE = ("Reset 단계 경계는 판정 기준이지 관측된 분포가 아닙니다. "
                  "백테스트(§21)가 대체합니다")

# §19 경로 유형
PATH_SPIKE = "SPIKE"              # 급등해서 도달
PATH_RESET_RECOVERY = "RESET_RECOVERY"   # 조정 후 회복해서 도달
PATH_STEADY = "STEADY"            # 꾸준히 올라서 도달
PATH_DECLINING = "DECLINING"      # 떨어지는 중에 통과
PATH_UNKNOWN = "UNKNOWN"

PATH_LABEL = {
    PATH_SPIKE: "급등해서 이 가격에 왔습니다 — 위에 저항이 없고 아래가 비었습니다",
    PATH_RESET_RECOVERY: "조정 후 회복해서 왔습니다 — 위쪽 수요와 아래쪽 지지선이 "
                         "둘 다 확인됐습니다",
    PATH_STEADY: "꾸준히 올라왔습니다",
    PATH_DECLINING: "떨어지는 중에 이 가격을 지나고 있습니다",
    PATH_UNKNOWN: "경로를 판정할 만큼 이력이 없습니다",
}


@dataclass(frozen=True)
class Reset:
    completed: list[str] = field(default_factory=list)
    current_step: str | None = None
    peak_ym: str | None = None
    trough_ym: str | None = None
    drop_from_peak: float | None = None
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.current_step is not None

    @property
    def ratio(self) -> float | None:
        """일곱 단계 중 몇 개까지 왔나."""
        if not self.known:
            return None
        return len(self.completed) / len(RESET_SEQUENCE)

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        drop = f"{self.drop_from_peak:+.1%}" if self.drop_from_peak is not None else "?"
        return (f"{self.current_step} (고점 대비 {drop}) · "
                f"{len(self.completed)}/{len(RESET_SEQUENCE)}단계")


def excess_reset(series: bands_mod.BandSeries, *,
                 jeonse_held: bool | None = None) -> Reset:
    """과열 → … → 거래회복 중 어디까지 왔나 (§18).

    **고점 대비 하락률만으로 판정하지 않는다.** 순서를 확인한다.
    """
    usable = series.usable
    if len(usable) < MIN_MONTHS:
        return Reset(reason=(
            f"관측이 {len(usable)}개월뿐입니다(최소 {MIN_MONTHS}개월). "
            f"사이클을 판정하지 않습니다"))

    prices = [(p.ym, p.p50, p.sample_n) for p in usable if p.p50]
    if len(prices) < MIN_MONTHS:
        return Reset(reason="중앙값이 있는 달이 모자랍니다")

    # prices 는 최신순. 과거→현재로 뒤집어 순서를 본다.
    chrono = list(reversed(prices))
    peak_i = max(range(len(chrono)), key=lambda i: chrono[i][1])
    peak_ym, peak_price, peak_n = chrono[peak_i]
    now_ym, now_price, now_n = chrono[-1]

    completed: list[str] = []

    # ① 과열이 있었는가 — 고점 전에 충분히 올랐어야 한다
    before = chrono[:peak_i + 1]
    if len(before) >= 6:
        base = min(p for _, p, _ in before)
        if base > 0 and (peak_price - base) / base >= OVERHEAT_RISE:
            completed.append(OVERHEAT)
    if OVERHEAT not in completed:
        return Reset([], None, peak_ym, None, None,
                     "고점 전에 충분한 상승이 없어 '과열 후 조정' 사이클이 "
                     "아닙니다. 고점 대비 하락률만으로 회복을 논하지 않습니다(§49-6)")

    after = chrono[peak_i + 1:]
    if not after:
        return Reset(completed, OVERHEAT, peak_ym, None, 0.0,
                     "아직 고점입니다")

    trough_i = min(range(len(after)), key=lambda i: after[i][1])
    trough_ym, trough_price, _ = after[trough_i]
    drop = (trough_price - peak_price) / peak_price

    # ② 조정
    if drop > CORRECTION_DROP:
        return Reset(completed, OVERHEAT, peak_ym, trough_ym, drop,
                     f"고점 대비 {drop:+.1%} — 아직 조정이라 할 만큼 빠지지 "
                     f"않았습니다")
    completed.append(CORRECTION)

    # ③ 거래 소멸 — 조정 구간의 거래건수가 과열기보다 줄었는가
    peak_window = [n for _, _, n in before[-6:]] or [peak_n]
    corr_window = [n for _, _, n in after[:trough_i + 1][-6:]] or [0]
    peak_avg = sum(peak_window) / len(peak_window)
    corr_avg = sum(corr_window) / len(corr_window)
    if peak_avg > 0 and corr_avg / peak_avg <= DRY_UP_RATIO:
        completed.append(DRY_UP)
    else:
        return Reset(completed, CORRECTION, peak_ym, trough_ym, drop,
                     "거래가 마르지 않았습니다 — 아직 조정 중일 수 있습니다")

    # ④ 밴드 안정 — 저점 이후 가격이 좁은 범위에서만 움직이는가
    post = after[trough_i:]
    if len(post) < 3:
        return Reset(completed, DRY_UP, peak_ym, trough_ym, drop,
                     "저점 이후 관측이 3개월 미만입니다")
    lo = min(p for _, p, _ in post)
    hi = max(p for _, p, _ in post)
    if lo > 0 and (hi - lo) / lo <= STABLE_BAND * 3:
        completed.append(STABILIZE)
    else:
        return Reset(completed, DRY_UP, peak_ym, trough_ym, drop,
                     "저점 이후 가격이 아직 흔들립니다")

    # ⑤ 전세 유지 — 모르면 **가정하지 않고** 여기서 멈춘다
    if jeonse_held is None:
        return Reset(completed, STABILIZE, peak_ym, trough_ym, drop,
                     "전세가 버텼는지 몰라 그 다음 단계를 판정하지 않습니다")
    if not jeonse_held:
        return Reset(completed, STABILIZE, peak_ym, trough_ym, drop,
                     "전세가 함께 빠졌습니다 — 하방이 확인되지 않았습니다")
    completed.append(JEONSE_HOLD)

    # ⑥ 바닥 회복 — P25 가 올라오는가
    p25_now = usable[0].p25
    p25_trough = next((p.p25 for p in usable if p.ym == trough_ym), None)
    if p25_now and p25_trough and p25_trough > 0:
        if (p25_now - p25_trough) / p25_trough >= FLOOR_RISE:
            completed.append(FLOOR_RECOVERY)
        else:
            return Reset(completed, JEONSE_HOLD, peak_ym, trough_ym, drop,
                         "바닥(P25)이 아직 올라오지 않았습니다")
    else:
        return Reset(completed, JEONSE_HOLD, peak_ym, trough_ym, drop,
                     "P25 를 몰라 바닥 회복을 판정하지 않습니다")

    # ⑦ 거래 회복
    recent = [n for _, _, n in chrono[-6:]]
    recent_avg = sum(recent) / len(recent) if recent else 0
    if corr_avg > 0 and recent_avg / corr_avg >= 1.2:
        completed.append(TXN_RECOVERY)
        step = TXN_RECOVERY
    else:
        step = FLOOR_RECOVERY

    return Reset(completed, step, peak_ym, trough_ym, drop)


def reset_feature(r: Reset) -> Feature:
    if not r.known:
        return Feature.missing("reset_completion", r.reason)
    detail = {
        "현재 단계": r.current_step,
        "완료": " → ".join(r.completed),
        "고점": r.peak_ym or "확인 불가",
        "저점": r.trough_ym or "확인 불가",
        "고점 대비": (f"{r.drop_from_peak:+.1%}" if r.drop_from_peak is not None
                  else "확인 불가"),
        "주의": "고점 대비 하락률만으로 가산하지 않습니다. 순서를 봅니다(§18·§49-6)",
        "기준": THRESHOLD_NOTE,
    }
    if r.reason:
        detail["멈춘 이유"] = r.reason
    return Feature(
        "reset_completion", r.ratio, "0~1", 0.6, Status.OK, detail,
        Calc(value=r.ratio, unit="0~1",
             formula="완료한 단계 수 ÷ 7 (과열→조정→거래소멸→밴드안정→"
                     "전세유지→바닥회복→거래회복)",
             intermediates={"완료": r.completed}, grade="ESTIMATED"))


# ── §19 Path-Dependent Valuation ─────────────────────────────────────

@dataclass(frozen=True)
class Path:
    kind: str
    highest: int | None = None
    lowest: int | None = None
    current: int | None = None
    months: int = 0
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.kind != PATH_UNKNOWN

    @property
    def overhead_proof(self) -> float | None:
        """위쪽에 실제 수요가 있었다는 증거 (§19).

        과거에 지금보다 비싸게 거래된 적이 있으면, 그 가격을 낼 사람이
        존재한다는 사실이 확인된 것이다. 급등해서 처음 온 가격에는 그 증거가 없다.
        """
        if self.highest is None or self.current is None or self.current <= 0:
            return None
        return max(0.0, (self.highest - self.current) / self.current)

    @property
    def support_proof(self) -> float | None:
        """아래쪽 지지선이 확인됐는가."""
        if self.lowest is None or self.current is None or self.current <= 0:
            return None
        return max(0.0, (self.current - self.lowest) / self.current)

    @property
    def label(self) -> str:
        return PATH_LABEL[self.kind]


def price_path(series: bands_mod.BandSeries, *, months: int = 60) -> Path:
    """같은 가격이라도 어떻게 왔는가 (§19)."""
    usable = [p for p in series.usable[:months] if p.p50]
    if len(usable) < 12:
        return Path(PATH_UNKNOWN, reason=(
            f"관측이 {len(usable)}개월뿐입니다(최소 12개월). "
            f"도달 경로를 판정하지 않습니다"))

    chrono = list(reversed(usable))
    prices = [p.p50 for p in chrono]
    current = prices[-1]
    highest, lowest = max(prices), min(prices)
    peak_i = prices.index(highest)

    # 고점이 최근이면 아직 조정 전이다.
    after_peak = len(prices) - 1 - peak_i
    drop_from_peak = (current - highest) / highest if highest else 0.0

    if drop_from_peak <= -0.08 and after_peak >= 6:
        # 고점 뒤로 충분히 지났고 충분히 빠졌다. 회복 중인지 계속 빠지는지 본다.
        trough = min(prices[peak_i:])
        recovering = current > trough * 1.02
        kind = PATH_RESET_RECOVERY if recovering else PATH_DECLINING
    else:
        base = prices[0]
        rise = (current - base) / base if base else 0.0
        # 절반 이상의 상승이 최근 12개월에 몰렸으면 급등이다.
        recent_base = prices[-13] if len(prices) >= 13 else base
        recent_rise = (current - recent_base) / recent_base if recent_base else 0.0
        kind = (PATH_SPIKE if (rise > 0.15 and recent_rise >= rise * 0.5)
                else PATH_STEADY)

    return Path(kind, highest, lowest, current, len(usable))


def path_feature(p: Path) -> Feature:
    """경로를 점수로. **같은 가격에 같은 점수를 주지 않는다**(§19).

    RESET_RECOVERY 가 가장 높다 — 위쪽 수요와 아래쪽 지지선이 둘 다 확인됐다.
    SPIKE 가 가장 낮다 — 둘 다 확인되지 않았다.
    """
    if not p.known:
        return Feature.missing("path_quality", p.reason)

    base = {PATH_RESET_RECOVERY: 1.00, PATH_STEADY: 0.65,
            PATH_SPIKE: 0.20, PATH_DECLINING: 0.10}[p.kind]

    detail = {
        "경로": p.kind,
        "해석": p.label,
        "과거 최고": f"{p.highest:,}원" if p.highest else "확인 불가",
        "과거 최저": f"{p.lowest:,}원" if p.lowest else "확인 불가",
        "위쪽 증거": (f"{p.overhead_proof:.1%} 위까지 거래된 적 있음"
                  if p.overhead_proof else "없음 — 이 위 가격은 미검증"),
        "아래쪽 지지": (f"{p.support_proof:.1%} 아래에서 멈춘 적 있음"
                   if p.support_proof else "확인 불가"),
        "주의": "가격이 같아도 도달 경로가 다르면 다른 상품입니다(§19)",
    }
    return Feature(
        "path_quality", base, "0~1",
        sample_confidence(p.months, full_at=36), Status.OK, detail,
        Calc(value=base, unit="0~1",
             formula="도달 경로 유형별 점수 (RESET_RECOVERY > STEADY > SPIKE > DECLINING)",
             intermediates={"경로": p.kind, "최고": p.highest, "최저": p.lowest},
             grade="ESTIMATED"))
