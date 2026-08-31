"""Stage 분류 · 4분면 · Quiet Compounder (신규 지시서 §17·§22·§23·§38).

지시서가 가장 강하게 경고한 것:

> §22 Cheap + Dormant 를 Pre-Breakout 으로 잘못 분류하지 않는 것이 매우 중요하다.
> §49-8 싸다는 이유만으로 Pre-Breakout 분류 금지.

이 둘은 겉으로 똑같이 생겼다. 둘 다 싸고, 둘 다 아직 안 올랐다. 차이는 하나뿐이다.

    PRE_BREAKOUT   싸고 + **바닥이 이미 조용히 움직이고 있다**
    VALUE_TRAP     싸고 + 아무것도 안 움직인다 + 오래 그랬다

그래서 이 모듈은 **Cheapness 만으로는 어떤 Stage 도 내리지 않는다.**
Movement 증거(P25 이동 · Slope 지속 · 거래 회복)가 없으면 PRE_BREAKOUT 이 될 수 없다.

§38: Stage 와 Investment Score 를 혼동하지 않는다. 좋은 아파트라도 EXHAUSTED 면
신규매수 순위는 낮을 수 있다. 그래서 Stage 는 점수가 아니라 **라벨**이고,
랭킹은 Stage 를 필터로 쓴다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from apt_engine.features.base import Feature, FeatureSet

# §38 여덟 상태
DORMANT = "DORMANT"
PRE_BREAKOUT = "PRE_BREAKOUT"
EMERGING = "EMERGING"
CONFIRMED = "CONFIRMED"
MATURE = "MATURE"
EXHAUSTED = "EXHAUSTED"
VALUE_TRAP = "VALUE_TRAP"
CHASE = "CHASE"
UNKNOWN = "UNKNOWN"

STAGES = (DORMANT, PRE_BREAKOUT, EMERGING, CONFIRMED, MATURE, EXHAUSTED,
          VALUE_TRAP, CHASE, UNKNOWN)

STAGE_LABEL = {
    DORMANT: "아무 움직임이 없습니다. 쌀 수도 있지만 언제 오를지 모릅니다",
    PRE_BREAKOUT: "싸고, 바닥이 조용히 움직이기 시작했습니다",
    EMERGING: "가격대 이동이 시작됐습니다. 아직 눈에 크게 띄지 않습니다",
    CONFIRMED: "이동이 확인됐습니다. 일부는 이미 가격에 들어갔습니다",
    MATURE: "충분히 올랐습니다. 남은 알파가 작습니다",
    EXHAUSTED: "더 밀어올릴 여지가 없어 보입니다",
    VALUE_TRAP: "오래 쌌는데 아무것도 안 움직입니다. 싼 데는 이유가 있습니다",
    CHASE: "이미 많이 올랐는데 지금 들어가는 것입니다",
    UNKNOWN: "판정에 필요한 값을 구하지 못했습니다",
}

# §22 4분면
TARGET = "TARGET"
VALUE_TRAP_CANDIDATE = "VALUE_TRAP_CANDIDATE"
QUADRANT_CHASE = "CHASE"
OVERPRICED_DEAD = "OVERPRICED_DEAD"

# 판정 기준. **관측이 아니라 기준이다** — 백테스트가 대체한다.
CHEAP_STRETCH = -0.03          # price_stretch 가 이보다 낮으면 '싸다'
EXPENSIVE_STRETCH = 0.10
MOVING_SHIFT = 0.5             # band_shift_strength 가 이보다 크면 '움직인다'
LATENT_HIGH = 0.6
VISIBLE_EARLY = 0.45
VISIBLE_CLEAR = 0.70
LONG_CHEAP_MONTHS = 24
THRESHOLD_NOTE = ("Stage 경계는 판정 기준이지 관측된 분포가 아닙니다. "
                  "백테스트(§21)가 대체합니다")

# §23 Quiet Compounder 조건
QUIET_MIN_SLOPE_PERSISTENCE = 0.75
QUIET_MAX_STRETCH = 0.05
QUIET_MAX_VISIBLE = 0.5


@dataclass(frozen=True)
class Verdict:
    stage: str
    quadrant: str | None
    quiet_compounder: bool
    reasons: list[str] = field(default_factory=list)
    unknown_reason: str | None = None
    inputs: dict = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return self.stage != UNKNOWN

    @property
    def label(self) -> str:
        head = f"{self.stage} — {STAGE_LABEL[self.stage]}"
        if self.quiet_compounder:
            head += "\n    ★ QUIET_COMPOUNDER — 조용히 가격대가 위로 이동 중"
        return head


def _v(fs: FeatureSet, key: str) -> float | None:
    f = fs.items.get(key)
    if f is None or not f.usable:
        return None
    return f.value


def classify(fs: FeatureSet, *, persistent_cheap_months: int | None = None
             ) -> Verdict:
    """Stage 하나. 값을 못 구하면 **DORMANT 로 떨어뜨리지 않고** UNKNOWN 이다.

    모르는 것을 '움직임 없음' 으로 세면 데이터가 부족한 단지가 전부
    VALUE_TRAP 이 된다 — 그건 판정이 아니라 데이터 공백이다.
    """
    stretch = _v(fs, "price_stretch")
    shift = _v(fs, "band_shift_strength")
    latent = _v(fs, "latent_movement")
    visible = _v(fs, "visible_movement")
    slope = _v(fs, "slope_persistence")
    percentile = _v(fs, "price_percentile")

    inputs = {"price_stretch": stretch, "band_shift_strength": shift,
              "latent_movement": latent, "visible_movement": visible,
              "slope_persistence": slope, "price_percentile": percentile}

    # Cheapness 와 Movement 중 **하나라도** 모르면 4분면을 만들 수 없다.
    if stretch is None:
        return Verdict(UNKNOWN, None, False, [],
                       "price_stretch 를 구하지 못했습니다 — 싼지 비싼지 모릅니다",
                       inputs)
    movement_signals = [x for x in (shift, latent, visible) if x is not None]
    if not movement_signals:
        return Verdict(UNKNOWN, None, False, [],
                       "Movement 신호를 하나도 구하지 못했습니다 — "
                       "움직이는지 아닌지 모릅니다", inputs)

    cheap = stretch <= CHEAP_STRETCH
    expensive = stretch >= EXPENSIVE_STRETCH
    moving = max(movement_signals) >= MOVING_SHIFT

    quadrant = (TARGET if (cheap and moving) else
                VALUE_TRAP_CANDIDATE if (cheap and not moving) else
                QUADRANT_CHASE if (expensive and moving) else
                OVERPRICED_DEAD if expensive else None)

    reasons: list[str] = [
        f"price_stretch {stretch:+.1%} → " +
        ("쌈" if cheap else "비쌈" if expensive else "중간"),
        f"Movement 최대 {max(movement_signals):.2f} → " +
        ("움직임" if moving else "정지"),
    ]

    stage = _stage_of(cheap=cheap, expensive=expensive, moving=moving,
                      latent=latent, visible=visible, slope=slope,
                      percentile=percentile, stretch=stretch,
                      persistent_cheap_months=persistent_cheap_months,
                      reasons=reasons)

    quiet = _quiet_compounder(slope=slope, stretch=stretch, visible=visible,
                              latent=latent)
    if quiet:
        reasons.append("QUIET_COMPOUNDER — 기울기는 꾸준한데 아직 눈에 안 띕니다(§23)")

    return Verdict(stage, quadrant, quiet, reasons, None, inputs)


def _stage_of(*, cheap, expensive, moving, latent, visible, slope, percentile,
              stretch, persistent_cheap_months, reasons) -> str:
    """8단계 판정.

    순서가 규칙이다. **VALUE_TRAP 검사를 PRE_BREAKOUT 보다 먼저** 한다 —
    싸고 안 움직이는 것을 먼저 걸러내야 §22 의 오분류가 안 난다.
    """
    # ① 싸고 안 움직인다 → 오래 그랬으면 VALUE_TRAP, 아니면 DORMANT
    if cheap and not moving:
        if (persistent_cheap_months is not None
                and persistent_cheap_months >= LONG_CHEAP_MONTHS):
            reasons.append(
                f"{persistent_cheap_months}개월째 싼데 격차가 안 닫혔습니다 "
                f"→ VALUE_TRAP (§14·§22)")
            return VALUE_TRAP
        reasons.append("싸지만 움직임이 없습니다 → DORMANT. "
                       "싸다는 이유만으로 PRE_BREAKOUT 으로 올리지 않습니다(§49-8)")
        return DORMANT

    # ② 비싸고 안 움직인다
    if expensive and not moving:
        reasons.append("비싸고 움직임도 없습니다 → EXHAUSTED")
        return EXHAUSTED

    # ③ 비싼데 움직인다 → 쫓아가는 것
    if expensive and moving:
        reasons.append("이미 비싼데 움직입니다 → CHASE (§22)")
        return CHASE

    # ④ 여기부터는 '움직인다'. 얼마나 보이는가로 나눈다.
    if percentile is not None and percentile >= 0.95 and not cheap:
        reasons.append("역사상 최고가 근처입니다 → MATURE")
        return MATURE

    if visible is not None and visible >= VISIBLE_CLEAR:
        reasons.append("이미 눈에 띄게 움직였습니다 → CONFIRMED (남은 알파 감소·§20)")
        return CONFIRMED

    if visible is not None and visible >= VISIBLE_EARLY:
        reasons.append("이동이 시작됐고 아직 초기입니다 → EMERGING")
        return EMERGING

    # ⑤ Latent 는 높은데 아직 안 보인다 = 지시서가 찾는 자리
    if latent is not None and latent >= LATENT_HIGH and cheap:
        reasons.append(
            "싸고, 바닥이 오래 조용히 올라왔고, 아직 눈에 안 띕니다 "
            "→ PRE_BREAKOUT (§7 의 Latent HIGH · Visible EARLY 가설)")
        return PRE_BREAKOUT

    if slope is not None and slope >= 0.75 and cheap:
        reasons.append("싸고 기울기가 꾸준합니다 → PRE_BREAKOUT")
        return PRE_BREAKOUT

    reasons.append("움직이지만 근거가 약합니다 → EMERGING")
    return EMERGING


def _quiet_compounder(*, slope, stretch, visible, latent) -> bool:
    """§23 — 눈에 안 띄는데 오래 위로 이동 중인 후보."""
    if slope is None or stretch is None:
        return False
    if slope < QUIET_MIN_SLOPE_PERSISTENCE:
        return False
    if stretch > QUIET_MAX_STRETCH:
        return False
    if visible is not None and visible > QUIET_MAX_VISIBLE:
        return False
    if latent is not None and latent < 0.5:
        return False
    return True


# ── 저장 ─────────────────────────────────────────────────────────────

def save(conn: sqlite3.Connection, complex_id: int, area_band: str, *,
         as_of: str, verdict: Verdict) -> None:
    conn.execute(
        "INSERT INTO stage_state (complex_id, area_band, as_of, stage, quadrant, "
        " quiet_compounder, reasons_json, unknown_reason) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(complex_id, area_band, as_of) DO UPDATE SET "
        " stage=excluded.stage, quadrant=excluded.quadrant, "
        " quiet_compounder=excluded.quiet_compounder, "
        " reasons_json=excluded.reasons_json, "
        " unknown_reason=excluded.unknown_reason",
        (complex_id, area_band, as_of, verdict.stage, verdict.quadrant,
         int(verdict.quiet_compounder),
         json.dumps({"이유": verdict.reasons, "입력": verdict.inputs},
                    ensure_ascii=False, default=str),
         verdict.unknown_reason))


# 신규매수 순위에서 어떻게 다룰 것인가 (§38).
# EXHAUSTED·CHASE·VALUE_TRAP 은 좋은 아파트여도 **지금 사는 것**이 아니다.
EXECUTABLE_STAGES = (EMERGING, CONFIRMED, PRE_BREAKOUT)
WATCH_STAGES = (PRE_BREAKOUT, DORMANT)
EXCLUDED_FROM_EXECUTABLE = (EXHAUSTED, CHASE, VALUE_TRAP, UNKNOWN)


def executable(verdict: Verdict) -> bool:
    return verdict.stage in EXECUTABLE_STAGES


def watchable(verdict: Verdict) -> bool:
    """Pre-Breakout Watch 목록 (§37).

    > 아직 확신은 낮지만 Remaining Alpha 가 크고 초기 조건이 형성되는 후보.

    두 부류를 받는다.
      * PRE_BREAKOUT — 싸고 바닥이 조용히 움직이는 것
      * QUIET_COMPOUNDER — Stage 와 무관하게, 눈에 안 띄는데 꾸준히 오르는 것(§23)

    그냥 조용한 것(DORMANT · quiet 아님)은 받지 않는다. 조용한 것과 조용히
    오르는 것은 다르고, 그 구분이 §22 오분류를 막는 자리다.
    """
    if verdict.stage == PRE_BREAKOUT:
        return True
    if verdict.stage in (EXHAUSTED, CHASE, VALUE_TRAP, UNKNOWN, MATURE):
        return False
    return verdict.quiet_compounder
