"""DELTA 파이프라인 — 새 층을 하나로 (신규 지시서 §2·§3·§37·§43·§46).

기존 `ranking/pipeline.py` 를 **대체하지 않는다.** 그 파이프라인은 Consensus
9모델 기준으로 계속 돌고, 이 모듈은 그 위에 새 지시서의 층을 얹는다.
둘을 한 파일에 합치지 않은 이유는 §49-15 다 — 정상 작동하는 기능을 이유 없이
재작성하지 않는다.

    ① Blind Universe          기존 그대로
    ② Capital Feasibility     기존 그대로 (§2)
    ③ Feature (4 State 포함)   bands · stretch · cycle 그룹 추가
    ④ Stage 분류              §17·§38
    ⑤ EarlyAlpha              §21 — Alpha / Risk / Confidence 셋으로
    ⑥ CASH 와 비교            §3·§46 — 못 이기면 TOP 에 안 넣는다
    ⑦ Executable / Watch      §37
    ⑧ Coverage · 시장온도      §43·§40

⑥이 마지막에서 두 번째인 것이 중요하다. 점수를 다 낸 **뒤에** CASH 와 비교해야
"이 후보가 몇 등이고 CASH 는 그 사이 어디" 를 말할 수 있다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from apt_engine import area as area_mod, units
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features import assemble as features_mod
from apt_engine.features import stage as stage_mod
from apt_engine.features.base import FeatureSet
from apt_engine.invest import cash_candidate as cash_mod
from apt_engine.invest.budget import Profile
from apt_engine.ranking import executable as exec_mod
from apt_engine.ranking import pipeline as base_pipeline
from apt_engine.scoring import early_alpha as alpha_mod
from apt_engine.scoring import weights as weights_mod

# 4 State 를 계산하려면 이 그룹들이 필요하다.
DELTA_GROUPS = ("momentum", "regime", "flow", "supply", "jeonse", "entry",
                "catalyst", "bands", "stretch", "cycle")


@dataclass(frozen=True)
class DeltaCandidate:
    complex_id: int
    area_band: str
    price: int
    features: FeatureSet
    alpha: alpha_mod.Alpha
    stage: stage_mod.Verdict
    bands: exec_mod.PriceBands
    required_equity: int | None = None

    @property
    def score(self) -> float | None:
        return self.alpha.alpha

    @property
    def line(self) -> str:
        score = f"{self.alpha.alpha:5.1f}" if self.alpha.known else "  ―  "
        risk = f"{self.alpha.risk:4.0f}" if self.alpha.risk is not None else "  ― "
        return (f"{score} / R{risk} / C{self.alpha.confidence:4.0f}  "
                f"{self.stage.stage:<13} {units.fmt_eok(self.price):>8}  "
                f"{self.bands.verdict()}")


@dataclass(frozen=True)
class DeltaResult:
    as_of: str
    cash: int
    horizon_years: int
    area_band: str
    universe_size: int
    feasible_size: int
    candidates: list[DeltaCandidate] = field(default_factory=list)
    split: exec_mod.Split | None = None
    coverage: exec_mod.Coverage | None = None
    cash_option: cash_mod.CashOption | None = None
    weights_source: str = weights_mod.HEURISTIC
    notes: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        """§43 — 다 못 봤으면 '전체' 라고 쓰지 않는다."""
        return self.coverage.title if self.coverage else "UNIVERSE 미측정"

    @property
    def summary(self) -> str:
        lines = [
            f"{self.as_of} · 현금 {units.fmt_eok(self.cash)} · "
            f"{self.horizon_years}년 · {self.area_band}㎡",
            f"  {self.title}",
            f"  후보 {self.universe_size} → 매수가능 {self.feasible_size} "
            f"→ 점수산출 {sum(1 for c in self.candidates if c.alpha.known)}",
        ]
        if self.weights_source != weights_mod.BACKTESTED:
            lines.append("  ⚠ 가중치 임시(heuristic) — 백테스트 학습 전입니다(§21)")
        if self.cash_option and not self.cash_option.known:
            lines.append(f"  ⚠ {self.cash_option.unknown_reason}")
        if self.split:
            lines.append("  " + self.split.summary.replace("\n", "\n  "))
        for note in self.notes:
            lines.append(f"  · {note}")
        return "\n".join(lines)

    @property
    def report(self) -> str:
        lines = [self.summary, ""]
        if self.split and self.split.executable:
            lines.append("  ── TODAY / EXECUTABLE ──")
            lines.append("   Alpha /Risk/Conf   Stage          가격     판정")
            for i, c in enumerate(self.split.executable, 1):
                lines.append(f"  {i:2}. {c.line}")
        else:
            lines.append("  ── TODAY / EXECUTABLE ──")
            lines.append("    지금 매수할 만한 후보가 없습니다(§46).")
        if self.split and self.split.watch:
            lines.append("")
            lines.append("  ── PRE-BREAKOUT WATCH ──")
            for i, c in enumerate(self.split.watch, 1):
                lines.append(f"  {i:2}. {c.line}")
        return "\n".join(lines)


def run(conn: sqlite3.Connection, *, as_of: cutoff_mod.AsOf, profile: Profile,
        horizon_years: int = 5, area_band: str | None = None,
        lawd_cd: str | None = None, scan_limit: int = 2000,
        gate: str = base_pipeline.GATE_STRICT,
        weights: dict[str, float] | None = None,
        weights_source: str = weights_mod.HEURISTIC,
        limit: int = 10) -> DeltaResult:
    """새 층까지 포함한 한 번의 랭킹."""
    band = area_band or area_mod.DEFAULT_BAND
    notes: list[str] = []

    # ①② 기존 파이프라인이 Universe 와 Capital Gate 를 담당한다.
    base = base_pipeline.run(
        conn, as_of=as_of, profile=profile, horizon_years=horizon_years,
        area_band=band, lawd_cd=lawd_cd, scan_limit=scan_limit,
        groups=list(DELTA_GROUPS), weights_source=weights_source, gate=gate)

    cash = cash_mod.load(conn, profile_name=profile.name,
                         capital=profile.available_cash or 0,
                         horizon_years=horizon_years)

    # ③④⑤
    candidates: list[DeltaCandidate] = []
    stages: dict[int, stage_mod.Verdict] = {}
    for c in base.feasible:
        verdict = stage_mod.classify(c.features)
        stages[c.complex_id] = verdict
        alpha = alpha_mod.compute(c.complex_id, c.features, weights=weights,
                                  weights_source=weights_source)
        entry_pos = c.features.items.get("entry_position")
        price_bands = exec_mod.price_bands(
            c.price,
            entry_position=(entry_pos.value if entry_pos and entry_pos.usable
                            else None),
            alternatives_quality=None)   # 아래에서 후보군이 정해진 뒤 다시 계산
        candidates.append(DeltaCandidate(
            c.complex_id, band, c.price, c.features, alpha, verdict,
            price_bands, c.required_equity))

    # ⑤-2 Competitive Buy Price — 대안의 질이 정해진 뒤에야 계산된다(§39).
    scored = [c for c in candidates if c.alpha.known]
    if scored:
        quality = min(1.0, len(scored) / 20.0)
        candidates = [
            DeltaCandidate(
                c.complex_id, c.area_band, c.price, c.features, c.alpha,
                c.stage,
                exec_mod.price_bands(
                    c.price,
                    entry_position=(c.features.items["entry_position"].value
                                    if ("entry_position" in c.features.items
                                        and c.features.items["entry_position"].usable)
                                    else None),
                    alternatives_quality=quality),
                c.required_equity)
            for c in candidates]
        notes.append(
            f"같은 자기자본 대안 {len(scored)}개를 반영해 매수가를 조정했습니다(§39)")

    # ⑥⑦ 정렬 키에 이름이 없다. 동점은 id 로 깬다.
    ordered = sorted(candidates,
                     key=lambda c: (-(c.alpha.alpha or -1.0), c.complex_id))
    returns = {c.complex_id: (c.alpha.alpha / 100.0)
               for c in ordered if c.alpha.known}
    penalties = {c.complex_id: ((c.alpha.risk or 0.0) / 100.0)
                 for c in ordered if c.alpha.known}
    split = exec_mod.split(ordered, stages, cash=cash,
                           expected_returns=returns,
                           risk_penalties=penalties, limit=limit)

    # ⑧
    coverage = exec_mod.measure(conn, as_of=as_of.day, area_band=band,
                               scanned_ids={c.complex_id for c in candidates})

    if not cash.known:
        notes.append("CASH 기준선이 없어 §46 최종 질문에 답하지 못했습니다 — "
                     "`profile set --cash-hurdle` 로 넣으세요")

    return DeltaResult(as_of.day, profile.available_cash or 0, horizon_years,
                       band, base.universe_size, len(base.feasible),
                       ordered, split, coverage, cash, weights_source, notes)


def detail(c: DeltaCandidate) -> str:
    """§48-J 가 요구한 후보 상세."""
    def f(key):
        item = c.features.items.get(key)
        if item is None or not item.usable or item.value is None:
            return "확인 불가"
        return f"{item.value:+.1%}" if abs(item.value) < 10 else f"{item.value:.2f}"

    lines = [
        f"■ 후보 #{c.complex_id} [{c.area_band}㎡]",
        f"  Normal Executable Price  {units.fmt_eok(c.price)}",
        f"  매수가 구간              {c.bands.label}",
    ]
    if c.bands.competitive_note:
        lines.append(f"                           {c.bands.competitive_note}")
    lines += [
        f"  {c.alpha.label}",
        f"  Stage                    {c.stage.label}",
        f"  실투자금                 " + (units.fmt_eok(c.required_equity)
                                     if c.required_equity else "확인 불가"),
        "",
        "  ── 핵심 지표 ──",
        f"  Remaining Recoverable Gap  {f('recoverable_discount_ratio')}",
        f"  Price Stretch              {f('price_stretch')}",
        f"  Money Arrival Depth        {f('money_arrival_depth')}",
        f"  Latent / Visible           {f('latent_movement')} / {f('visible_movement')}",
        f"  Band Shift                 {f('band_shift_strength')}",
        f"  Reset Completion           {f('reset_completion')}",
        f"  Path Quality               {f('path_quality')}",
        "",
        "  ── 왜 이 후보인가 ──",
    ]
    for reason in c.stage.reasons:
        lines.append(f"    · {reason}")
    if c.alpha.penalties:
        lines.append("  ── 반대 근거 ──")
        for k, v in sorted(c.alpha.penalties.items(), key=lambda kv: -kv[1]):
            lines.append(f"    · {k} −{v:.2f}")
    if c.alpha.missing:
        lines.append(f"  ── 못 구한 것 ── {', '.join(c.alpha.missing)}")
    return "\n".join(lines)
