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
from apt_engine.features import bands as bands_mod
from apt_engine.features import demand as demand_mod
from apt_engine.features import leader as leader_mod
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
                "catalyst", "access", "bands", "stretch", "cycle")


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
    # §62 화면에 "누구를 따라가는 후보인가" 를 보여주기 위한 것.
    # **점수 계산에는 쓰이지 않는다** — 표시용이다.
    relevant_leader: str | None = None

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
        nationality: str | None = None,
        occupancy_plan: str | None = None,
        limit: int = 10) -> DeltaResult:
    """새 층까지 포함한 한 번의 랭킹.

    `nationality` · `occupancy_plan` 을 주면 토지거래허가 Hard Gate 가
    켜진다(§5). 안 주면 Gate 를 적용하지 않는다 — 국적과 실거주 계획을
    모르는 채로 판정하면 전부 NEEDS_CHECK 가 되어 화면이 빈다.
    """
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

    # ③-2 수요 쪽 세 Feature 는 **후보군이 있어야** 계산된다(§4-C·§33).
    # buyer_pool · effective_supply_risk · replacement_availability 가
    # EarlyAlpha 의 곱셈 항이라, 이게 없으면 점수가 아예 안 나온다.
    #
    # 단지 속성은 **한 번에** 읽는다. 후보마다 조회하면 2000개 후보에
    # 4000번 쿼리가 나간다.
    meta = _complex_meta(conn, [c.complex_id for c in base.feasible])
    market = demand_mod.Market(
        [demand_mod.Cohort(c.complex_id, c.price,
                           meta.get(c.complex_id, (None, ""))[0],
                           _sample_of(c.features),
                           meta.get(c.complex_id, (None, ""))[1])
         for c in base.feasible],
        as_of.day)

    # ③④⑤
    candidates: list[DeltaCandidate] = []
    stages: dict[int, stage_mod.Verdict] = {}
    for c in base.feasible:
        supply_values = {
            k: (c.features.items[k].value
                if k in c.features.items and c.features.items[k].usable
                else None)
            for k in ("supply_ratio_1y", "supply_ratio_2y",
                      "supply_ratio_3y", "supply_ratio_5y")}
        cliff = (c.features.items["supply_cliff"].value
                 if ("supply_cliff" in c.features.items
                     and c.features.items["supply_cliff"].usable) else None)
        households, region = meta.get(c.complex_id, (None, ""))
        features = c.features
        leader_feats, leader_label = _leader_features(
            conn, c.complex_id, band, as_of=as_of)
        for f in leader_feats:
            features = features.add(f)
        for f in demand_mod.all_features(
                market, price=c.price, lawd_cd=region or None,
                households=households, sample_n=_sample_of(c.features),
                required_equity=c.required_equity,
                supply_values=supply_values, cliff=cliff):
            features = features.add(f)
        features = features.add(demand_mod.same_capital_value(
            market, price=c.price, required_equity=c.required_equity,
            capital=profile.available_cash))
        c = _with_features(c, features)

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
            price_bands, c.required_equity, leader_label))

    # ⑤-1 Neighbour Confirmation (§10) — **모든 후보의 Feature 가 나온 뒤**
    # 계산한다. 이웃마다 시계열을 다시 로드하면 후보 수의 제곱만큼 쿼리가 나간다.
    # 이미 계산된 band_shift_strength 를 읽어서 센다.
    all_sets = {c.complex_id: c.features for c in candidates}
    regions = {cid: meta.get(cid, (None, ""))[1] for cid in all_sets}
    candidates = [
        DeltaCandidate(
            c.complex_id, c.area_band, c.price,
            c.features.add(demand_mod.neighbour_confirmation_from(
                all_sets, complex_id=c.complex_id, regions=regions)),
            c.alpha, c.stage, c.bands, c.required_equity, c.relevant_leader)
        for c in candidates]

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
                c.required_equity, c.relevant_leader)
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
    # ⑦-b 토지거래허가 Hard Gate (§5). 점수가 아니라 문이라 여기서 건다.
    gates = _permit_gates(conn, ordered, as_of=as_of.day, band=band,
                          nationality=nationality,
                          occupancy_plan=occupancy_plan, notes=notes)

    split = exec_mod.split(ordered, stages, cash=cash,
                           expected_returns=returns,
                           risk_penalties=penalties, gates=gates, limit=limit)

    # ⑧
    coverage = exec_mod.measure(conn, as_of=as_of.day, area_band=band,
                               scanned_ids={c.complex_id for c in candidates})

    if not cash.known:
        notes.append("CASH 기준선이 없어 §46 최종 질문에 답하지 못했습니다 — "
                     "`profile set --cash-hurdle` 로 넣으세요")

    return DeltaResult(as_of.day, profile.available_cash or 0, horizon_years,
                       band, base.universe_size, len(base.feasible),
                       ordered, split, coverage, cash, weights_source, notes)


def _leader_features(conn, complex_id: int, band: str, *,
                     as_of: cutoff_mod.AsOf) -> tuple[list, str | None]:
    """Leader 망 → 전달 실패 → 회복가능 할인 (§11·§12·§13).

    Leader 가 없으면 **세 Feature 모두 '확인 불가'** 다. 0 이 아니다 —
    Leader 를 못 찾은 것과 Leader 가 있는데 안 따라온 것은 다른 상태다.
    """
    leaders = leader_mod.load_leaders(conn, complex_id, band, as_of=as_of)
    relevant = leader_mod.relevant_leaders(leaders)
    if not relevant:
        why = (f"겹침이 확인된 Leader 가 없습니다 "
               f"(후보 {len(leaders)}개 중 0개). "
               f"Leader 를 못 찾은 것과 안 따라온 것은 다릅니다")
        from apt_engine.features.base import Feature
        return ([Feature.missing("transmission_failure", why),
                 Feature.missing("recoverable_discount_ratio", why),
                 Feature.missing("leader_exhaustion", why),
                 Feature.missing("next_node_score", why)], None)

    # 겹침이 가장 큰 Leader 하나로 본다. 여러 Leader 를 평균하면
    # "누구를 따라가야 하는지" 가 흐려진다.
    best = max(relevant, key=lambda l: l.buyer_overlap or 0.0)
    follower_series = bands_mod.load_bands(conn, complex_id, band, as_of=as_of)
    leader_series = bands_mod.load_bands(conn, best.leader_id, band,
                                         as_of=as_of)

    t = leader_mod.transmission(follower_series, leader_series,
                                buyer_overlap=best.buyer_overlap)
    discount = _observed_discount(follower_series, leader_series)
    d = leader_mod.decompose(discount, transmission_failure=t.failure)

    from apt_engine.features.base import Feature
    return ([leader_mod.transmission_feature(t),
             leader_mod.recoverable_feature(d),
             leader_mod.leader_exhaustion(leader_series),
             Feature.missing("next_node_score",
                             "생활권 가격 사다리가 아직 입력되지 않았습니다 "
                             "(`ladder import`)")],
            best.label)


def _observed_discount(follower, leader) -> float | None:
    """Leader 대비 관측 할인. 둘 중 하나라도 없으면 만들지 않는다."""
    f, l = follower.latest, leader.latest
    if f is None or l is None or not f.p50 or not l.p50 or l.p50 <= 0:
        return None
    return max(0.0, (l.p50 - f.p50) / l.p50)


def _complex_meta(conn, ids: list[int]) -> dict[int, tuple[int | None, str]]:
    """단지 → (세대수, 시군구). 한 번에 읽는다.

    **이름은 읽지 않는다**(§1). 여기서 name 을 가져오면 그 순간부터 이름이
    스코어링 경로 안에 있게 된다.
    """
    if not ids:
        return {}
    out: dict[int, tuple[int | None, str]] = {}
    chunk = 500
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        marks = ",".join("?" * len(part))
        for r in conn.execute(
                f"SELECT id, apt_households, lawd_cd FROM complex "
                f" WHERE id IN ({marks})", part):
            out[int(r["id"])] = (r["apt_households"], r["lawd_cd"] or "")
    return out


def _with_features(candidate, features):
    from dataclasses import replace
    return replace(candidate, features=features)


def _sample_of(features) -> int:
    """대표가격 표본 수. Feature 에 실려 있으면 쓰고, 없으면 0."""
    for key in ("transaction_quality", "investigation_priority"):
        item = features.items.get(key)
        if item is not None and item.detail.get("표본"):
            try:
                return int(str(item.detail["표본"]).split("건")[0])
            except (ValueError, IndexError):
                continue
    return 0


def _permit_gates(conn, candidates, *, as_of: str, band: str,
                  nationality: str | None, occupancy_plan: str | None,
                  notes: list[str]) -> dict:
    """후보별 토허 판정 (§5).

    국적이나 실거주 계획을 안 받았으면 **Gate 를 걸지 않는다.** 걸면
    전 후보가 NEEDS_CHECK 로 막혀 화면이 비는데, 그건 "확인이 필요하다" 를
    "아무것도 못 산다" 로 바꿔 말하는 것이다. 대신 그 사실을 메모로 남긴다.
    """
    from apt_engine.regulation import gate as gate_mod
    from apt_engine.regulation import land_share as ls_mod

    if nationality is None or occupancy_plan is None:
        notes.append(
            "토지거래허가 Gate 를 적용하지 않았습니다 — 매수자 국적과 "
            "실거주 계획을 받아야 판정할 수 있습니다(§5).")
        return {}

    metas = {r["id"]: r for r in conn.execute(
        "SELECT c.id, c.lawd_cd, r.sido FROM complex c "
        "LEFT JOIN region r ON r.lawd_cd = c.lawd_cd "
        f"WHERE c.id IN ({','.join('?' * len(candidates))})",
        [c.complex_id for c in candidates])} if candidates else {}

    SIDO = {"서울": "서울특별시", "경기": "경기도", "인천": "인천광역시"}
    out, estimated = {}, 0
    for c in candidates:
        meta = metas.get(c.complex_id)
        if meta is None:
            continue
        sido = SIDO.get((meta["sido"] or "")[:2], meta["sido"] or "")
        share = ls_mod.load(conn, complex_id=c.complex_id, area_band=band)
        if share.known and share.verification != ls_mod.VERIFIED:
            estimated += 1
        rules = gate_mod.load_rules(conn, lawd_cd=meta["lawd_cd"], as_of=as_of)
        out[c.complex_id] = gate_mod.evaluate_candidate(
            rules,
            gate_mod.Candidate(
                lawd_cd=meta["lawd_cd"], property_type=gate_mod.APARTMENT,
                land_share_sqm=share.value,
                land_share_verification=share.verification),
            nationality=nationality, occupancy_plan=occupancy_plan,
            contract_date=as_of,
            coverage_status=gate_mod.coverage_of(
                conn, sido=sido, target_scope=gate_mod.BROAD_APARTMENT),
            parcel_coverage=gate_mod.coverage_of(
                conn, sido=sido, target_scope=gate_mod.PARCEL_SPECIFIC))

    if estimated:
        notes.append(
            f"대지권이 추정값인 후보 {estimated}개는 토허 허가대상 판정을 "
            f"하지 못했습니다 — 공동주택 공시가격의 전유부 대지권이 필요합니다 "
            f"(`cli landshare import`).")
    return out


def detail(c: DeltaCandidate) -> str:
    """§48-J 가 요구한 후보 상세."""
    def f(key):
        item = c.features.items.get(key)
        if item is None or not item.usable or item.value is None:
            return "확인 불가"
        return f"{item.value:+.1%}" if abs(item.value) < 10 else f"{item.value:.2f}"

    lines = [
        f"■ 후보 #{c.complex_id} [{c.area_band}㎡]",
        f"  Relevant Leader          {c.relevant_leader or '확인 불가'}",
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
