"""Price Band Migration · Latent/Visible Movement · Slope Persistence
   (신규 지시서 §7·§8·§9).

§9 가 금지한 것부터: **단일 최고가나 평균가격 상승을 Repricing 으로 보지 않는다.**

    최근 84㎡ 거래  10.8  11.0  11.1  11.2  11.3  14.0 (억)

평균은 11.6억이지만 14억 한 건이 만든 숫자다. 그래서 P25 / Median / P75 를
각각 추적하고, **셋이 함께 올라야** 진짜 가격대 이동으로 본다.

    Strong Shift   P25 ↑  Median ↑  P75 ↑     실제 구매력이 올라왔다
    Weak Shift     P75 만 ↑                    비싼 물건 몇 건이 팔린 것

저층·직거래·취소·이상치는 `price/outlier.py` 가 이미 걸러낸 뒤의 분위수를 쓴다
(`price_snapshot.price_p25/p50/p75`). 정규화 없이 분위수를 보면 저층 거래 비중이
달라진 것을 가격대 이동으로 오독한다.

§7 은 Movement 를 둘로 나눈다.

    Latent   시장이 조용한데 바닥이 오래 조금씩 올라옴 — 아직 안 알려짐
    Visible  거래량이 돌아오고 밴드가 눈에 띄게 이동 — 이미 보임

지시서의 가설: `LatentMovement = HIGH` 이고 `VisibleMovement = EARLY` 인 조합이
가장 좋다. **이 가설은 검증 대상이지 결론이 아니다** — 백테스트가 판정한다.

§8 은 최근 3개월 하나로 판단하지 말라고 한다. 3M·6M·12M·24M 기울기를 다 보고,
   3M+ 6M+ 12M+ 24M 미세+   → 일관된 상승 (Latent 높음)
   24M 0 · 12M 0 · 6M 0 · 3M +20%  → Spike 경고
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field

from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features.base import Feature, Status, combine, sample_confidence
from apt_engine.trace import Calc

# §8 이 요구한 네 구간
SLOPE_WINDOWS = (3, 6, 12, 24)

# 밴드 이동을 '올랐다' 로 볼 최소 변화율. **판정 기준이라 백테스트가 대체한다.**
BAND_RISE = 0.01

# Spike 경고 — 24M·12M 이 사실상 정지인데 3M 만 크게 뛴 경우 (§8)
SPIKE_SHORT = 0.10
SPIKE_LONG = 0.02

# Latent 로 인정할 최소 관측 개월. 짧으면 '조용히 오래' 가 성립하지 않는다.
LATENT_MIN_MONTHS = 18

# 거래가 완전히 소멸하면 Latent 가 아니다(§7: "거래 완전 소멸 없음")
MIN_SAMPLE_FOR_LATENT = 2

THRESHOLD_NOTE = ("밴드 이동·Spike 기준은 관측된 분포가 아니라 판정 기준입니다. "
                  "백테스트(§21)가 이 값을 대체합니다")


@dataclass(frozen=True)
class BandPoint:
    ym: str
    p25: int | None
    p50: int | None
    p75: int | None
    sample_n: int

    @property
    def complete(self) -> bool:
        return None not in (self.p25, self.p50, self.p75)


@dataclass(frozen=True)
class BandSeries:
    complex_id: int
    area_band: str
    points: list[BandPoint] = field(default_factory=list)   # 최신순

    def at(self, ym: str) -> BandPoint | None:
        for p in self.points:
            if p.ym <= ym:
                return p
        return None

    @property
    def latest(self) -> BandPoint | None:
        return self.points[0] if self.points else None

    @property
    def usable(self) -> list[BandPoint]:
        return [p for p in self.points if p.complete]


def load_bands(conn: sqlite3.Connection, complex_id: int, area_band: str, *,
               as_of: cutoff_mod.AsOf, months: int = 36) -> BandSeries:
    """컷오프 이내의 월별 가격 분위수. 최신순.

    `price_snapshot` 의 p25/p50/p75 는 이상치·직거래·취소를 제외한 뒤의 값이라
    여기서 다시 거를 필요가 없다.
    """
    observable = as_of.observable
    end_ym = _shift(observable.ym, -1)
    start_ym = _shift(end_ym, -months + 1)
    with cutoff_mod.guard(conn, observable) as g:
        rows = g.execute(
            "SELECT as_of_ym, price_p25, price_p50, price_p75, sample_n, "
            "       representative_price "
            "  FROM price_snapshot "
            " WHERE complex_id = ? AND area_band = ? "
            "   AND as_of_ym >= ? AND as_of_ym <= ? "
            " ORDER BY as_of_ym DESC",
            (complex_id, area_band, start_ym, end_ym)).fetchall()
    points = []
    for r in rows:
        # p50 이 비어 있으면 대표가격으로 대신한다(같은 정의다). p25/p75 는 대체하지 않는다 —
        # 없는 분위수를 대표가격으로 채우면 분포가 한 점으로 눌려서 Weak/Strong 구분이 사라진다.
        p50 = r["price_p50"] if r["price_p50"] is not None else r["representative_price"]
        points.append(BandPoint(r["as_of_ym"], r["price_p25"], p50, r["price_p75"],
                                int(r["sample_n"] or 0)))
    return BandSeries(complex_id, area_band, points)


# ── §9 Price Band Migration ──────────────────────────────────────────

def _band_change(series: BandSeries, months: int,
                 pick) -> tuple[float | None, str]:
    """분위수 하나의 기간 변화율."""
    usable = series.usable
    if not usable:
        return None, "분위수가 있는 스냅샷이 없습니다"
    latest = usable[0]
    past_ym = _shift(latest.ym, -months)
    past = next((p for p in usable if p.ym <= past_ym), None)
    if past is None:
        return None, f"{months}개월 전 분위수가 없습니다(최장 {usable[-1].ym})"
    base, now = pick(past), pick(latest)
    if not base or base <= 0:
        return None, f"{months}개월 전 값이 0 이하입니다"
    return (now - base) / base, ""


def migration_features(series: BandSeries, *, months: int = 12) -> list[Feature]:
    """P25 / Median / P75 각각의 이동 + 셋을 합친 Shift 강도 (§9)."""
    picks = (("p25_migration", lambda p: p.p25, "하위 25% 가격대"),
             ("median_migration", lambda p: p.p50, "중앙값 가격대"),
             ("p75_migration", lambda p: p.p75, "상위 25% 가격대"))
    out: list[Feature] = []
    changes: dict[str, float] = {}
    n = len(series.usable)
    conf = sample_confidence(n, full_at=12)

    for key, pick, label in picks:
        value, why = _band_change(series, months, pick)
        if value is None:
            out.append(Feature.missing(key, why))
            continue
        changes[key] = value
        out.append(Feature(
            key, value, "비율", conf, Status.OK,
            {"기간": f"{months}개월", "무엇": label,
             "주의": "이 값 하나로 매수 판단하지 않습니다(§9)"},
            Calc(value=value, unit="비율",
                 formula=f"({label} 현재 − {months}개월 전) ÷ {months}개월 전",
                 intermediates={"관측 개월": n}, grade="CONFIRMED")))

    out.append(_shift_strength(changes, months, conf))
    return out


def _shift_strength(changes: dict[str, float], months: int,
                    conf: float) -> Feature:
    """Strong / Weak Distribution Shift (§9).

    셋 다 오르면 1.0, P75 만 오르면 낮게. **P75 만 오른 것을 상승으로 세지 않는다** —
    그건 비싼 물건 몇 건이 팔린 것이지 가격대가 올라온 게 아니다.
    """
    need = ("p25_migration", "median_migration", "p75_migration")
    missing = [k for k in need if k not in changes]
    if missing:
        return Feature.missing(
            "band_shift_strength",
            f"분위수 이동을 다 구하지 못했습니다: {', '.join(missing)}")

    up = {k: changes[k] > BAND_RISE for k in need}
    risen = sum(up.values())
    if up["p25_migration"] and up["median_migration"] and up["p75_migration"]:
        value, kind = 1.0, "Strong — P25·중앙값·P75 가 함께 올랐습니다"
    elif up["p25_migration"] and up["median_migration"]:
        value, kind = 0.75, "바닥과 중앙이 올랐습니다 (P75 는 아직)"
    elif up["p75_migration"] and not up["p25_migration"]:
        value, kind = 0.25, "Weak — P75 만 올랐습니다. 가격대 이동이 아닙니다(§9)"
    elif risen:
        value, kind = 0.5, "일부만 올랐습니다"
    else:
        value, kind = 0.0, "가격대가 이동하지 않았습니다"

    return Feature(
        "band_shift_strength", value, "0~1", conf, Status.OK,
        {"판정": kind, "기간": f"{months}개월",
         "P25": f"{changes['p25_migration']:+.1%}",
         "중앙값": f"{changes['median_migration']:+.1%}",
         "P75": f"{changes['p75_migration']:+.1%}",
         "기준": THRESHOLD_NOTE},
        Calc(value=value, unit="0~1",
             formula="P25·중앙값·P75 가 함께 올랐는가",
             intermediates={k: f"{v:+.1%}" for k, v in changes.items()},
             grade="CONFIRMED"))


# ── §8 Slope Persistence ─────────────────────────────────────────────

@dataclass(frozen=True)
class Slopes:
    values: dict[int, float]           # 개월 → 변화율
    missing: list[int] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return len(self.values) >= 2


def slopes(series: BandSeries, *,
           windows: tuple[int, ...] = SLOPE_WINDOWS) -> Slopes:
    """3M·6M·12M·24M 중앙값 기울기 (§8)."""
    out: dict[int, float] = {}
    missing: list[int] = []
    for months in windows:
        value, _ = _band_change(series, months, lambda p: p.p50)
        if value is None:
            missing.append(months)
        else:
            out[months] = value
    return Slopes(out, missing)


def slope_persistence(s: Slopes) -> Feature:
    """기울기가 얼마나 일관되게 양수인가 (§8).

    긴 구간에 가중치를 더 준다. 3개월만 튄 것을 '지속 상승' 으로 세지 않는다.
    """
    if not s.known:
        return Feature.missing(
            "slope_persistence",
            f"기울기를 2개 이상 구하지 못했습니다 (없는 구간: "
            f"{', '.join(f'{m}M' for m in s.missing)})")

    weights = {3: 1.0, 6: 1.5, 12: 2.0, 24: 2.5}
    total = sum(weights.get(m, 1.0) for m in s.values)
    positive = sum(weights.get(m, 1.0) for m, v in s.values.items() if v > 0)
    value = positive / total if total else 0.0
    conf = sample_confidence(len(s.values), full_at=len(SLOPE_WINDOWS))

    return Feature(
        "slope_persistence", value, "0~1", conf, Status.OK,
        {"구간": {f"{m}M": f"{v:+.1%}" for m, v in sorted(s.values.items())},
         "해석": ("길고 짧은 구간이 모두 양수" if value >= 0.99 else
                "일부 구간만 양수" if value > 0 else "상승 구간 없음"),
         "주의": "긴 구간에 더 큰 가중치를 줍니다 — 3개월만 튄 것은 지속이 아닙니다"},
        Calc(value=value, unit="0~1",
             formula="양수인 구간의 가중합 ÷ 전체 가중합",
             intermediates={f"{m}M": v for m, v in sorted(s.values.items())},
             grade="CONFIRMED"))


def spike_warning(s: Slopes) -> tuple[bool, str]:
    """24M·12M 은 정지인데 3M 만 크게 뛴 경우 (§8).

    이건 Movement 가 아니라 단발 이벤트일 가능성이 높다. Feature 로 만들지 않고
    경고 문장으로 돌려준다 — 점수를 깎는 것은 STRETCH 쪽 일이다.
    """
    short = s.values.get(3)
    if short is None or short < SPIKE_SHORT:
        return False, ""
    longs = [s.values.get(m) for m in (12, 24)]
    longs = [v for v in longs if v is not None]
    if not longs:
        return False, ""
    if all(abs(v) <= SPIKE_LONG for v in longs):
        return True, (f"3개월 {short:+.1%} 인데 12·24개월은 사실상 정지입니다. "
                      f"가격대 이동이 아니라 단발 거래일 수 있습니다(§8)")
    return False, ""


# ── §7 Latent / Visible Movement ─────────────────────────────────────

def latent_movement(series: BandSeries, s: Slopes, *,
                    jeonse_floor_ok: bool | None = None) -> Feature:
    """조용히 오래 바닥이 이동해 왔는가 (§7).

    네 가지를 본다.
        24M P25 상승 · 24M 중앙값 상승 · 전세 바닥 유지/상승 · 거래 완전소멸 없음

    전세 정보를 못 구하면 **그 항목을 0 으로 세지 않고** 분모에서 뺀다.
    """
    usable = series.usable
    if len(usable) < LATENT_MIN_MONTHS:
        return Feature.missing(
            "latent_movement",
            f"관측이 {len(usable)}개월뿐입니다(최소 {LATENT_MIN_MONTHS}개월). "
            f"'조용히 오래' 를 확인할 수 없습니다")

    checks: dict[str, bool] = {}
    p25_24, _ = _band_change(series, 24, lambda p: p.p25)
    p50_24, _ = _band_change(series, 24, lambda p: p.p50)
    if p25_24 is not None:
        checks["24개월 P25 상승"] = p25_24 > 0
    if p50_24 is not None:
        checks["24개월 중앙값 상승"] = p50_24 > 0
    if jeonse_floor_ok is not None:
        checks["전세 바닥 유지/상승"] = jeonse_floor_ok
    recent = usable[:6]
    checks["거래 완전소멸 없음"] = all(p.sample_n >= MIN_SAMPLE_FOR_LATENT
                                for p in recent) if recent else False

    if len(checks) < 2:
        return Feature.missing(
            "latent_movement", "확인할 수 있는 조건이 2개 미만입니다")

    value = sum(checks.values()) / len(checks)
    conf = combine(sample_confidence(len(usable), full_at=24),
                   sample_confidence(len(checks), full_at=4))
    return Feature(
        "latent_movement", value, "0~1", conf, Status.OK,
        {"조건": {k: ("O" if v else "X") for k, v in checks.items()},
         "확인한 조건": f"{len(checks)}/4",
         "주의": "못 구한 조건은 X 가 아니라 분모에서 뺐습니다"},
        Calc(value=value, unit="0~1", formula="충족 조건 ÷ 확인 가능 조건",
             intermediates={k: v for k, v in checks.items()}, grade="CONFIRMED"))


def visible_movement(series: BandSeries, band_shift: Feature, *,
                     volume_recovery: float | None = None) -> Feature:
    """거래량 회복과 밴드 이동이 눈에 보이는가 (§7).

    Visible 은 **높다고 좋은 게 아니다.** 이미 다 보이면 늦은 것이다(§20).
    그래서 값만 내고, 좋고 나쁨은 Stage 분류(§38)가 정한다.
    """
    usable = series.usable
    if not usable:
        return Feature.missing("visible_movement", "분위수 스냅샷이 없습니다")

    parts: dict[str, float] = {}
    if band_shift.usable and band_shift.value is not None:
        parts["가격대 이동"] = band_shift.value
    if volume_recovery is not None:
        parts["거래량 회복"] = max(0.0, min(1.0, volume_recovery))
    recent6 = usable[:6]
    if len(recent6) >= 3:
        older = usable[6:12]
        if older:
            now = statistics.mean(p.sample_n for p in recent6)
            before = statistics.mean(p.sample_n for p in older)
            if before > 0:
                parts["거래건수 증가"] = max(0.0, min(1.0, (now - before) / before))

    if not parts:
        return Feature.missing(
            "visible_movement", "가격대 이동도 거래량도 확인하지 못했습니다")

    value = sum(parts.values()) / len(parts)
    return Feature(
        "visible_movement", value, "0~1",
        sample_confidence(len(usable), full_at=12), Status.OK,
        {"구성": {k: f"{v:.2f}" for k, v in parts.items()},
         "주의": "높다고 좋은 것이 아닙니다 — 이미 다 보이면 늦은 것입니다(§20)"},
        Calc(value=value, unit="0~1", formula="구성 항목 평균",
             intermediates=parts, grade="CONFIRMED"))


def _shift(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:6]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"
