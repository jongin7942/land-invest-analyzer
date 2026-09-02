"""랭킹 파이프라인 — 순서가 곧 규칙이다 (지시서 §1·§26·§45·§60).

    ① 수도권 전체 (Blind Universe — 이름 없이)
    ② Capital Feasibility Gate — 실제로 살 수 있는가 (§26)
    ③ Feature 계산 → Consensus 9모델 (§49)
    ④ Kill Score 로 배제 (§45) — 지우지 않고 이유와 함께 남긴다
    ⑤ TOP100 → TOP30 → TOP10
    ⑥ CASH 옵션 — 아무것도 좋지 않으면 억지로 추천하지 않는다 (§60)

**②가 ③보다 먼저인 것이 중요하다.** 살 수 없는 집을 점수 매기는 건 낭비이고,
더 나쁘게는 "좋은데 못 산다" 는 후보가 상위에 남아 판단을 흐린다.

이 모듈은 단지명을 **한 번도 읽지 않는다.** 이름은 결과를 표시할 때 붙인다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from apt_engine import area as area_mod, units
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.blind import universe as universe_mod
from apt_engine.cash import self_capital as capital_mod
from apt_engine.features import assemble as features_mod
from apt_engine.features.base import FeatureSet
from apt_engine.invest.budget import Profile
from apt_engine.scoring import consensus as consensus_mod
from apt_engine.scoring import kill as kill_mod
from apt_engine.scoring import models as models_mod
from apt_engine.scoring import regime_bridge  # noqa: F401  (아래에서 정의)
from apt_engine.scoring import thesis as thesis_mod
from apt_engine.scoring import weights as weights_mod

# 단계별 통과 수. 지시서 §1 이 정한 숫자다.
TOP_WIDE = 100
TOP_DEEP = 30
TOP_FINAL = 10

# 이 점수 아래면 아무것도 추천하지 않는다(§60). **판정 기준**이라 백테스트가 대체한다.
CASH_THRESHOLD = 45.0

# 자본 게이트 방식.
#   STRICT      실투자금(대출·세금·비용 전부)을 계산해서 거른다. 기본값이고 §26 이 요구하는 것.
#   PRICE_ONLY  매매가 ≤ 현금. 대출 규칙이 없는 DB(과거 시점 백테스트 등)에서
#               쓰는 대체 경로다. **대출이 나온다고 가정하지 않는다** — 전액 현금
#               매수만 가능하다고 보는 것이라 실제보다 후보가 좁게 잡힌다.
#               이 모드로 돈 결과에는 그 사실이 항상 붙어 다닌다.
#   NO_LOAN     세금과 부대비용은 다 세고 **대출만 0** 으로 본다. loan_rule 이
#               2025-10-16 부터만 있어 과거 창에서 STRICT 가 성립하지 않을 때
#               쓴다. PRICE_ONLY 와 달리 취득세·중개보수·법무비를 빠뜨리지 않고,
#               STRICT 와 달리 레버리지를 기대하지 않는다. 필요현금을 크게 잡는
#               방향이라 못 사는 집이 올라오지는 않는다.
GATE_STRICT = "STRICT"
GATE_PRICE_ONLY = "PRICE_ONLY"
GATE_NO_LOAN = "NO_LOAN"
GATE_NOTE = {
    GATE_STRICT: "실투자금 기준(§26)",
    GATE_PRICE_ONLY: ("매매가 기준 — 대출 규칙이 없어 전액 현금 매수만 가능하다고 "
                      "봤습니다. 실제보다 후보가 좁습니다"),
    GATE_NO_LOAN: ("매수가 + 취득세 + 부대비용 기준 — 대출 규칙이 없어 대출을 0 으로 "
                   "봤습니다. 필요현금을 실제보다 크게 잡습니다"),
}


@dataclass(frozen=True)
class Candidate:
    complex_id: int
    area_band: str
    price: int
    features: FeatureSet
    consensus: consensus_mod.Consensus
    kill: kill_mod.KillScore
    survival: thesis_mod.Survival
    capital: capital_mod.SelfCapital | None = None

    @property
    def score(self) -> float:
        return self.consensus.score

    @property
    def confidence(self) -> float:
        return self.consensus.confidence

    @property
    def required_equity(self) -> int | None:
        return self.capital.required if self.capital else None


@dataclass(frozen=True)
class Dropped:
    complex_id: int
    stage: str                         # 어느 단계에서 빠졌나
    reason: str


@dataclass(frozen=True)
class Result:
    as_of: str
    cash: int
    horizon_years: int
    profile_name: str
    area_band: str
    regime: str | None
    weights: weights_mod.Weights
    universe_size: int
    gate: str = GATE_STRICT
    feasible: list[Candidate] = field(default_factory=list)
    top100: list[Candidate] = field(default_factory=list)
    top30: list[Candidate] = field(default_factory=list)
    top10: list[Candidate] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    cash_recommended: bool = False
    cash_reason: str = ""

    @property
    def summary(self) -> str:
        head = (f"{self.as_of} · 현금 {units.fmt_eok(self.cash)} · "
                f"{self.horizon_years}년 · {self.profile_name}")
        if self.gate != GATE_STRICT:
            head += f"\n  자본 게이트: {GATE_NOTE[self.gate]}"
        return (f"{head}\n  후보 {self.universe_size} → 매수가능 {len(self.feasible)} "
                f"→ TOP100 {len(self.top100)} → TOP30 {len(self.top30)} "
                f"→ TOP10 {len(self.top10)}")


def run(conn: sqlite3.Connection, *, as_of: cutoff_mod.AsOf, profile: Profile,
        horizon_years: int = 5, area_band: str | None = None,
        lawd_cd: str | None = None, scan_limit: int = 2000,
        groups: list[str] | None = None,
        weights_source: str = weights_mod.HEURISTIC,
        gate: str = GATE_STRICT,
        cache: dict | None = None) -> Result:
    """전체 파이프라인 한 번.

    cache — 같은 as_of·같은 band 로 현금만 바꿔 여러 번 부를 때(백테스트의 자본
    버킷) 창 단위로 넘긴다. universe·실투자금·feature 는 현금과 무관해서 단지별로
    답이 같은데, 캐시가 없으면 버킷 수만큼 다시 계산한다. **as_of 나 band 가
    달라지면 새 dict 를 줘야 한다** — 키가 complex_id 뿐이라 섞이면 틀린다.
    """
    if not profile.available_cash:
        raise ValueError("가용 현금이 없으면 매수 가능 판정을 할 수 없습니다")
    if gate not in GATE_NOTE:
        raise ValueError(f"모르는 자본 게이트: {gate} (가능: {', '.join(GATE_NOTE)})")

    band = area_band or area_mod.DEFAULT_BAND

    cap_cache = None if cache is None else cache.setdefault("capital", {})
    feat_cache = None if cache is None else cache.setdefault("features", {})

    # ① Blind Universe — 이름 없이
    if cache is not None and "universe" in cache:
        universe = cache["universe"]
    else:
        universe = universe_mod.build(conn, as_of=as_of, area_band=band,
                                      lawd_cd=lawd_cd)
        if cache is not None:
            cache["universe"] = universe
    rows = universe.rows[:scan_limit]
    dropped: list[Dropped] = []

    # ② Capital Feasibility Gate (§26) — 점수 매기기 전에 거른다
    feasible_rows = []
    capitals: dict[int, capital_mod.SelfCapital | None] = {}
    for row in rows:
        if gate == GATE_PRICE_ONLY:
            # 대출을 가정하지 않는다. 매매가가 현금을 넘으면 못 산다.
            if row.representative_price > profile.available_cash:
                dropped.append(Dropped(
                    row.complex_id, "feasibility",
                    f"매매가 {units.fmt_eok(row.representative_price)} > "
                    f"현금 {units.fmt_eok(profile.available_cash)} "
                    f"(대출 규칙이 없어 전액 현금 기준으로 봤습니다)"))
                continue
            capitals[row.complex_id] = None
            feasible_rows.append(row)
            continue
        if cap_cache is not None and row.complex_id in cap_cache:
            capital = cap_cache[row.complex_id]
        else:
            capital = _capital_of(conn, row, profile=profile, as_of=as_of, band=band,
                                  use_mortgage=(gate != GATE_NO_LOAN))
            if cap_cache is not None:
                cap_cache[row.complex_id] = capital
        if capital is None:
            dropped.append(Dropped(row.complex_id, "feasibility",
                                   "실투자금을 계산하지 못했습니다"))
            continue
        verdict = capital.affordable(profile.available_cash)
        if verdict is None:
            dropped.append(Dropped(row.complex_id, "feasibility",
                                   f"실투자금 확인 불가: {', '.join(capital.unknown[:2])}"))
            continue
        if not verdict:
            dropped.append(Dropped(
                row.complex_id, "feasibility",
                f"실투자금 {units.fmt_eok(capital.required)} > "
                f"현금 {units.fmt_eok(profile.available_cash)}"))
            continue
        capitals[row.complex_id] = capital
        feasible_rows.append(row)

    # ③ Feature → Consensus
    feature_sets: dict[int, FeatureSet] = {}
    for row in feasible_rows:
        cid = row.complex_id
        if feat_cache is not None and cid in feat_cache:
            feature_sets[cid] = feat_cache[cid]
            continue
        built = features_mod.build(
            conn, cid, band, as_of=as_of, lawd_cd=row.lawd_cd,
            horizon_years=horizon_years, groups=groups)
        if feat_cache is not None:
            feat_cache[cid] = built
        feature_sets[cid] = built

    # Capital Efficiency 는 후보 집단이 있어야 계산된다(상대적 개념).
    for row in feasible_rows:
        capital = capitals[row.complex_id]
        if capital is None:
            from apt_engine.features.base import Feature
            feature_sets[row.complex_id] = feature_sets[row.complex_id].add(
                Feature.missing("capital_efficiency",
                                "대출 규칙이 없어 실투자금을 계산하지 못했습니다 "
                                "— 레버리지 효율을 0 으로 두지 않습니다"))
            continue
        feature_sets[row.complex_id] = _add_capital_efficiency(
            feature_sets[row.complex_id], capital)

    regime_name = _regime_name(feature_sets)
    weights = weights_mod.for_regime(regime_name, source=weights_source)
    ranks = models_mod.build_ranks(feature_sets)

    candidates: list[Candidate] = []
    for row in feasible_rows:
        fs = feature_sets[row.complex_id]
        scores = models_mod.score_all(row.complex_id, fs, ranks)
        cons = consensus_mod.combine(row.complex_id, scores, weights)
        kill = kill_mod.evaluate(fs)
        survival = thesis_mod.evaluate(cons, weights)
        candidates.append(Candidate(row.complex_id, band, row.representative_price,
                                    fs, cons, kill, survival,
                                    capitals[row.complex_id]))

    # ④·⑤ 단계별 좁히기. 정렬 키에 이름이 들어가지 않는다 — id 로 동점을 깬다.
    ordered = sorted(candidates, key=lambda c: (-c.score, c.complex_id))
    top100 = ordered[:TOP_WIDE]
    for c in ordered[TOP_WIDE:]:
        dropped.append(Dropped(c.complex_id, "top100", f"점수 {c.score:.0f} — 100위 밖"))

    top30 = top100[:TOP_DEEP]
    for c in top100[TOP_DEEP:]:
        dropped.append(Dropped(c.complex_id, "top30", f"점수 {c.score:.0f} — 30위 밖"))

    # Kill 은 TOP10 문턱에서 적용한다. 지우지 않고 이유를 남긴다(§65).
    survivors = []
    for c in top30:
        if c.kill.killed:
            dropped.append(Dropped(
                c.complex_id, "kill",
                f"Kill {c.kill.value:.2f} — {'; '.join(h.reason for h in c.kill.hits)}"))
            continue
        survivors.append(c)
    top10 = survivors[:TOP_FINAL]

    # ⑥ CASH 옵션 (§60)
    cash_reco, cash_reason = _cash_option(top10)

    return Result(as_of.day, profile.available_cash, horizon_years, profile.name,
                  band, regime_name, weights, len(universe), gate, candidates,
                  top100, top30, top10, dropped, cash_reco, cash_reason)


def _capital_of(conn, row, *, profile: Profile, as_of: cutoff_mod.AsOf,
                band: str,
                use_mortgage: bool = True) -> capital_mod.SelfCapital | None:
    try:
        exclusive = float(band)
    except ValueError:
        exclusive = None
    try:
        return capital_mod.compute(
            conn, price=row.representative_price, as_of=as_of.day,
            lawd_cd=row.lawd_cd, current_home_count=profile.current_home_count,
            exclusive_area_m2=exclusive, first_home_buyer=profile.first_home_buyer,
            annual_income=profile.annual_income,
            existing_annual_payment=profile.existing_annual_payment,
            interest_rate=profile.interest_rate,
            mortgage_term_years=profile.mortgage_term_years,
            repayment_type=profile.repayment_type, lender_type=profile.lender_type,
            region=profile.region,
            agent_vat_registered=profile.agent_vat_registered,
            use_mortgage=use_mortgage, allow_unverified=True)
    except Exception:                      # noqa: BLE001 — 후보 하나 때문에 멈추지 않는다
        return None


def _add_capital_efficiency(fs: FeatureSet,
                            capital: capital_mod.SelfCapital) -> FeatureSet:
    """같은 돈으로 얼마나 큰 자산을 잡는가 (§28·§29).

    레버리지가 크다는 뜻이기도 해서, 이 값 하나로 좋다고 하지 않는다 —
    risk 모델과 함께 봐야 한다.
    """
    from apt_engine.features.base import Feature, Status

    if capital.required is None or capital.required <= 0:
        return fs.add(Feature.missing("capital_efficiency",
                                      "실투자금을 확정하지 못했습니다"))
    value = capital.purchase_price / capital.required
    return fs.add(Feature("capital_efficiency", value, "배", 0.7, Status.OK,
                          {"매수가": units.fmt_eok(capital.purchase_price),
                           "실투자금": units.fmt_eok(capital.required),
                           "주의": "레버리지가 크다는 뜻이기도 하다. "
                                 "risk 모델과 함께 봐야 한다"}))


def _regime_name(feature_sets: dict[int, FeatureSet]) -> str | None:
    """후보들이 본 국면. 같은 시점·지역이면 모두 같아야 한다."""
    for fs in feature_sets.values():
        f = fs["regime"]
        if f.known and f.detail.get("국면"):
            return f.detail["국면"]
    return None


def _cash_option(top10: list[Candidate]) -> tuple[bool, str]:
    """억지로 아파트를 추천하지 않는다 (§60)."""
    if not top10:
        return True, "매수 가능하면서 Kill 을 통과한 후보가 없습니다"
    best = top10[0]
    if best.score < CASH_THRESHOLD:
        return True, (f"1위 점수 {best.score:.0f} 가 기준 {CASH_THRESHOLD:.0f} 미만입니다. "
                      f"지금은 현금 보유가 낫습니다(§60)")
    if best.confidence < 30:
        return True, (f"1위 신뢰도 {best.confidence:.0f} 가 낮습니다. "
                      f"데이터를 더 채운 뒤 판단하세요")
    return False, ""
