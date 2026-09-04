"""Relative Price Gap Engine — Leader Set · 역사적 비율 밴드 · 국면별 정상비율 ·
구조적/회복가능 분해 · Leader Transmission Probability · Multi-Leader Consensus ·
Relative Mispricing · RELATIVE_LAG / FALSE_CHEAP 목록 · 과거 전달 백테스트 (MASTER_SPEC §35).

원칙(§35 절대 규칙):
  * 절대가격 차이가 아니라 Follower/Leader 비율. 2021 고점 비율을 정상값으로 쓰지 않는다
    (과열 국면 월은 정상비율 계산에서 뺀다).
  * 과거 평균으로 무조건 회귀한다고 보지 않는다 — 회복가능 비중은 그 Pair 의 실제 과거
    전달 실적으로만 정하고, 구조 변화 플래그(§35.15)가 있으면 신뢰도를 낮춘다.
  * Mispricing 은 점수가 아니라 Liquid Exit Price / Terminal Wealth 입력이다.
  * 특정 단지를 위한 보정은 없다. 이름은 표시용이다.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field, asdict

from apt_engine.features import regime as regime_mod
from apt_engine.relative import store, zones as zones_mod
from apt_engine.relative.store import (N_MONTHS, MONTHS, Complex, Series, change, haversine_m,
                                       median, percentile)

MIN_PAIR_MONTHS = 60
CURRENT_WINDOW = 6
LEADER_RALLY = 0.08            # Leader 12개월 상승이 이만큼이면 '대장 상승' 에피소드
EPISODE_GAP = 24               # 에피소드 사이 최소 간격(개월)
CATCHUP_HORIZON = 24
CATCHUP_SUCCESS = 0.5          # Follower 가 Leader 상승의 절반 이상 따라가면 전달 성공
MIN_EPISODES_VERIFIED = 3
NORMAL_REGIMES = ("바닥형성", "회복초기", "상승초기", "상승확산", "침체", "하락전환")   # 과열 제외
OVERHEAT = ("과열",)
STRUCT_FLAG_SHARE = 0.10       # 구조 변화 플래그 하나당 구조적 비중 +10%p (HEURISTIC)
ACADEMY_GAP_RATIO = 2.0        # 학원 수가 2배 이상 차이 → 학군 구조 플래그
AGE_GAP_YEARS = 12
HOUSEHOLD_GAP_RATIO = 3.0
FALSE_CHEAP_MIN_GAP = 0.12

KIND_LOCAL, KIND_GRADE, KIND_UPPER, KIND_BUYER = "LOCAL", "GRADE", "UPPER_GRADE", "BUYER_CHOICE"


@dataclass
class PairResult:
    follower_id: int
    follower_band: str
    leader_id: int
    leader_band: str
    kind: str
    months: int
    current_ratio: float | None
    hist_p10: float | None
    hist_p25: float | None
    hist_median: float | None
    hist_p75: float | None
    hist_p90: float | None
    regime_now: str
    regime_normal: float | None      # 현 국면과 같은 과거 국면의 중앙값
    normal_used: float | None
    normal_basis: str
    observed_gap: float | None       # (normal − current)/normal, + = 덜 따라감
    structural_gap: float | None
    recoverable_gap: float | None
    structural_flags: list = field(default_factory=list)
    episodes: int = 0
    successes: int = 0
    transmission_p: float | None = None
    transmission_status: str = "UNKNOWN"
    catchup_median: float | None = None
    leader_move: str = "NONE"        # CONFIRMED / PARTIAL / NONE / UNKNOWN
    follower_start: list = field(default_factory=list)
    mispricing: float | None = None
    mispricing_status: str = "NOT_CALCULATED"
    size_premium: float | None = None   # 다른 면적 Pair 일 때 ㎡단가 비율(구조적 면적 프리미엄)


# ── 시군구 국면 (월별) ──────────────────────────────────────────────────

def region_regimes(complexes: dict[int, Complex], prices: dict[tuple[int, str], Series]) -> dict[str, list]:
    """lawd_cd → 길이 N_MONTHS 의 국면 이름. 기존 regime.classify 를 그대로 쓴다."""
    by_lawd: dict[str, list] = {}
    for (cid, band), s in prices.items():
        by_lawd.setdefault(complexes[cid].lawd_cd, []).append(s)
    out: dict[str, list] = {}
    for lawd, series in by_lawd.items():
        names: list = ["확인 불가"] * N_MONTHS
        for t in range(15, N_MONTHS):
            c12 = [change(s.p50, t, 12) for s in series]
            c3 = [change(s.p50, t, 3) for s in series]
            c12 = [v for v in c12 if v is not None]
            c3 = [v for v in c3 if v is not None]
            if len(c12) < 3:
                continue
            v_recent = sum(sum(s.n[t - 2:t + 1]) for s in series)
            v_prior = sum(sum(s.n[t - 14:t - 2]) for s in series) / 4.0
            vol = v_recent / v_prior if v_prior > 0 else None
            name, _ = regime_mod.classify(price_12m=median(c12), price_3m=median(c3) if len(c3) >= 3 else None,
                                          volume_ratio=vol)
            names[t] = name
        out[lawd] = names
    return out


# ── Leader Set ────────────────────────────────────────────────────────

class LeaderBook:
    def __init__(self, units: dict[str, zones_mod.Unit], zones: dict[str, list[str]],
                 leaders: list[zones_mod.Leader], complexes: dict[int, Complex],
                 prices: dict[tuple[int, str], Series]):
        self.units, self.zones, self.complexes, self.prices = units, zones, complexes, prices
        self.by_zone_band: dict[tuple[str, str], list[zones_mod.Leader]] = {}
        for l in leaders:
            self.by_zone_band.setdefault((l.zone, l.band), []).append(l)
        for v in self.by_zone_band.values():
            v.sort(key=lambda l: l.rank)
        self.zone_tier: dict[str, int | None] = {}
        self.zone_center: dict[str, tuple[float, float]] = {}
        for z, ks in zones.items():
            tiers = [units[k].tier for k in ks if units[k].tier is not None]
            self.zone_tier[z] = round(sum(tiers) / len(tiers)) if tiers else None
            self.zone_center[z] = (sum(units[k].lat for k in ks) / len(ks),
                                   sum(units[k].lon for k in ks) / len(ks))
        self.leader_ids: set[int] = {l.complex_id for l in leaders if l.rank <= 2}
        # Buyer-Choice 탐색용: 대장(1·2위)의 (단지, 면적, 현재가, 좌표) 만 미리 모아 둔다
        self.leader_entries: list[tuple[int, str, float, float, float]] = []
        for (lid, lband), s in prices.items():
            if lid in self.leader_ids:
                lp = s.last_median()
                if lp:
                    c = complexes[lid]
                    self.leader_entries.append((lid, lband, lp, c.lat, c.lon))

    def zone_of(self, cid: int) -> str | None:
        u = self.units.get(self.complexes[cid].emd_key)
        return u.zone if u else None

    def _nearest_zone(self, z: str, *, tier: int, exclude: set[str], band: str) -> str | None:
        lat, lon = self.zone_center[z]
        best, best_d = None, None
        for other, t in self.zone_tier.items():
            if other in exclude or t != tier or (other, band) not in self.by_zone_band:
                continue
            la, lo = self.zone_center[other]
            d = haversine_m(lat, lon, la, lo)
            if best_d is None or d < best_d:
                best, best_d = other, d
        return best

    def leader_set(self, cid: int, band: str) -> list[tuple[str, int, str]]:
        """[(kind, leader_id, leader_band)] — 같은 단지는 제외."""
        z = self.zone_of(cid)
        if z is None:
            return []
        out: list[tuple[str, int, str]] = []
        local = [l for l in self.by_zone_band.get((z, band), []) if l.complex_id != cid]
        if local:
            out.append((KIND_LOCAL, local[0].complex_id, band))
        tier = self.zone_tier.get(z)
        if tier is not None:
            g = self._nearest_zone(z, tier=tier, exclude={z}, band=band)
            if g:
                out.append((KIND_GRADE, self.by_zone_band[(g, band)][0].complex_id, band))
            if tier > 1:
                u = self._nearest_zone(z, tier=tier - 1, exclude={z}, band=band)
                if u:
                    out.append((KIND_UPPER, self.by_zone_band[(u, band)][0].complex_id, band))
        # Buyer-Choice: 같은 총액대(1.10~1.35배)에서 실제 비교되는 상위 상품 — 면적 달라도 됨, 10km 안
        my = self.prices[(cid, band)].last_median()
        me = self.complexes[cid]
        if my:
            best, best_d = None, None
            for lid, lband, lp, la, lo in self.leader_entries:
                if lid == cid or not (1.10 <= lp / my <= 1.35):
                    continue
                if abs(la - me.lat) > 0.095 or abs(lo - me.lon) > 0.12:
                    continue
                d = haversine_m(me.lat, me.lon, la, lo)
                if d > 10000:
                    continue
                if best_d is None or d < best_d:
                    best, best_d = (lid, lband), d
            if best and all(best[0] != o[1] for o in out):
                out.append((KIND_BUYER, best[0], best[1]))
        return out


# ── Pair 계산 ─────────────────────────────────────────────────────────

def _band_rise_all(s: Series, t: int, months: int = 12) -> tuple[bool | None, bool | None, bool | None]:
    a = change(s.p25, t, months)
    b = change(s.p50, t, months)
    c = change(s.p75, t, months)
    return (a > 0.01 if a is not None else None, b > 0.01 if b is not None else None,
            c > 0.01 if c is not None else None)


def _volume_up(s: Series, t: int) -> bool | None:
    recent = sum(s.n[t - 5:t + 1])
    prior = sum(s.n[t - 17:t - 5]) / 2.0
    if prior <= 0:
        return None
    return recent >= prior


def episodes_of(leader: Series, follower: Series, ratio: list) -> tuple[list[dict], int]:
    """Leader 12개월 상승 ≥ 8% 에피소드마다 Follower 의 24개월 추종률과 Gap 축소율."""
    eps: list[dict] = []
    last = -EPISODE_GAP
    for t in range(12, N_MONTHS):
        lr = change(leader.p50, t, 12)
        if lr is None or lr < LEADER_RALLY or t - last < EPISODE_GAP:
            continue
        last = t
        fr = change(follower.p50, min(t + CATCHUP_HORIZON, N_MONTHS - 1), 12 + CATCHUP_HORIZON) \
            if t + CATCHUP_HORIZON < N_MONTHS else None
        # Follower 상승은 Leader 상승 시작(t−12)부터 t+24 까지
        f0, f1 = follower.p50[t - 12], follower.p50[t + CATCHUP_HORIZON] if t + CATCHUP_HORIZON < N_MONTHS else None
        catch = (f1 / f0 - 1.0) / lr if f0 and f1 else None
        rec = {"t": MONTHS[t], "leader_rise": round(lr, 4),
               "catchup": round(min(catch, 2.0), 4) if catch is not None else None,
               "complete": t + CATCHUP_HORIZON < N_MONTHS}
        for h, lab in ((12, "gap_12m"), (36, "gap_36m"), (60, "gap_60m")):
            r0 = ratio[t]
            r1 = ratio[t + h] if t + h < N_MONTHS else None
            rec[lab] = round(r1 / r0 - 1.0, 4) if r0 and r1 else None
        eps.append(rec)
    complete = [e for e in eps if e["complete"] and e["catchup"] is not None]
    return complete, len(eps)


def structural_flags(f: Complex, l: Complex) -> list[str]:
    flags: list[str] = []
    if f.academies_500m is not None and l.academies_500m is not None and l.academies_500m >= 10:
        if l.academies_500m >= ACADEMY_GAP_RATIO * max(1, f.academies_500m):
            flags.append("학군(학원가 밀도 격차)")
    if f.approval_year and l.approval_year and l.approval_year - f.approval_year >= AGE_GAP_YEARS:
        flags.append("연식·상품성 격차")
    if f.households and l.households and l.households >= HOUSEHOLD_GAP_RATIO * f.households:
        flags.append("세대수(대단지) 격차")
    if f.station_m is not None and l.station_m is not None and f.station_m > 1000 and l.station_m <= 500:
        flags.append("역 접근 격차")
    if f.station_opened_recent or l.station_opened_recent:
        flags.append("최근 철도 개통(과거 비율 신뢰도 하향)")
    return flags


def compute_pair(fid: int, fband: str, lid: int, lband: str, kind: str, *,
                 prices: dict[tuple[int, str], Series], complexes: dict[int, Complex],
                 regimes: dict[str, list]) -> PairResult | None:
    fs, ls = prices[(fid, fband)], prices[(lid, lband)]
    ratio: list = [None] * N_MONTHS
    for t in range(N_MONTHS):
        if fs.p50[t] and ls.p50[t]:
            ratio[t] = fs.p50[t] / ls.p50[t]
    months = sum(1 for r in ratio if r)
    if months < MIN_PAIR_MONTHS:
        return None
    reg = regimes.get(complexes[fid].lawd_cd, ["확인 불가"] * N_MONTHS)
    regime_now = reg[-1] if reg[-1] != "확인 불가" else next((r for r in reversed(reg) if r != "확인 불가"), "확인 불가")
    hist = [r for t, r in enumerate(ratio) if r and reg[t] not in OVERHEAT]
    same_regime = [r for t, r in enumerate(ratio) if r and reg[t] == regime_now]
    cur = median([r for r in ratio[-CURRENT_WINDOW:] if r])
    p = {q: percentile(hist, q) for q in (0.10, 0.25, 0.50, 0.75, 0.90)}
    regime_normal = median(same_regime) if len(same_regime) >= 12 else None
    if regime_normal is not None and regime_now not in OVERHEAT:
        normal, basis = regime_normal, f"현 국면({regime_now}) 과거 중앙값"
    else:
        normal, basis = p[0.50], "장기 중앙값(과열 월 제외)"
    observed = (normal - cur) / normal if normal and cur else None

    complete, n_all = episodes_of(ls, fs, ratio)
    succ = sum(1 for e in complete if e["catchup"] >= CATCHUP_SUCCESS)
    n = len(complete)
    if n >= MIN_EPISODES_VERIFIED:
        p_trans, status = succ / n, "VERIFIED"
    elif n >= 1:
        p_trans, status = succ / n, "PROXY"
    else:
        p_trans, status = None, "UNKNOWN"
    catch_med = median([e["catchup"] for e in complete]) if complete else None

    flags = structural_flags(complexes[fid], complexes[lid])
    size_premium = None
    if fband != lband:
        fm, lm = store.BAND_M2[fband], store.BAND_M2[lband]
        if cur:
            size_premium = (ls.last_median() / lm) / (fs.last_median() / fm) if fs.last_median() else None

    structural = recoverable = None
    if observed is not None:
        if observed <= 0:
            structural, recoverable = 0.0, 0.0
        elif catch_med is None:
            structural = recoverable = None       # 전달 실적 없이는 분해하지 않는다
        else:
            rec_share = max(0.0, min(1.0, catch_med))
            rec_share = max(0.0, rec_share - STRUCT_FLAG_SHARE * len(flags))
            recoverable = observed * rec_share
            structural = observed - recoverable

    t = N_MONTHS - 1
    a, b, c = _band_rise_all(ls, t)
    vol = _volume_up(ls, t)
    if a is None and b is None:
        leader_move = "UNKNOWN"
    elif a and b and c and (vol is None or vol):
        leader_move = "CONFIRMED"
    elif b:
        leader_move = "PARTIAL"
    else:
        leader_move = "NONE"

    starts: list[str] = []
    fa, fb, _ = _band_rise_all(fs, t)
    if fa:
        starts.append("P25↑")
    if fb:
        starts.append("Median↑")
    if _volume_up(fs, t):
        starts.append("거래량↑")
    r0, r1 = ratio[t], ratio[t - 12] if t - 12 >= 0 else None
    if r0 and r1 and r0 > r1 * 1.02:
        starts.append("Gap 축소")

    mis, mstatus = None, "NOT_CALCULATED"
    if recoverable is not None and p_trans is not None:
        settle = 1.0 if starts else 0.5
        heat = 0.5 if regime_now in ("과열", "하락전환") else 1.0
        lead_f = {"CONFIRMED": 1.0, "PARTIAL": 0.7, "NONE": 0.4, "UNKNOWN": 0.5}[leader_move]
        mis = recoverable * p_trans * settle * heat * lead_f
        mstatus = status if not flags else "PROXY(구조 플래그)"
    return PairResult(fid, fband, lid, lband, kind, months, cur, p[0.10], p[0.25], p[0.50], p[0.75], p[0.90],
                      regime_now, regime_normal, normal, basis, observed, structural, recoverable, flags,
                      n, succ, p_trans, status, catch_med, leader_move, starts, mis, mstatus, size_premium)


# ── Follower 집계 ─────────────────────────────────────────────────────

@dataclass
class FollowerResult:
    complex_id: int
    band: str
    zone: str | None
    tier: int | None
    price_now: float | None
    pairs: list
    consensus: str
    consensus_gap: float | None
    mispricing: float | None
    mispricing_status: str
    label: str            # LAG_CANDIDATE / FALSE_CHEAP / NEUTRAL / UNKNOWN
    reason: str


def aggregate(fid: int, band: str, pairs: list[PairResult], *, zone: str | None, tier: int | None,
              price_now: float | None) -> FollowerResult:
    gaps = [p.observed_gap for p in pairs if p.observed_gap is not None]
    if len(gaps) >= 3:
        if all(g > 0.05 for g in gaps) and max(gaps) - min(gaps) <= 0.12:
            cons = "STRONG"
        elif all(g > 0 for g in gaps):
            cons = "OK"
        elif any(g > 0.10 for g in gaps) and any(g < 0.02 for g in gaps):
            cons = "DISTORTED"     # 한 Leader 에만 싸 보임
        else:
            cons = "WEAK"
    elif gaps:
        cons = "THIN"              # Leader 3개 미만
    else:
        cons = "UNKNOWN"
    cgap = median(gaps) if gaps else None
    mis = [p.mispricing for p in pairs if p.mispricing is not None]
    m = median(mis) if mis else None
    statuses = {p.mispricing_status for p in pairs if p.mispricing is not None}
    mstatus = "VERIFIED" if statuses == {"VERIFIED"} else ("PROXY" if statuses else "NOT_CALCULATED")
    struct_share = None
    obs = [p.observed_gap for p in pairs if p.structural_gap is not None and p.observed_gap]
    if obs:
        struct_share = sum(p.structural_gap for p in pairs if p.structural_gap is not None) / sum(obs)
    moving = any(p.follower_start for p in pairs)
    leader_ok = any(p.leader_move in ("CONFIRMED", "PARTIAL") for p in pairs)
    ptrans = [p.transmission_p for p in pairs if p.transmission_p is not None]
    label, reason = "NEUTRAL", ""
    if cgap is None:
        label, reason = "UNKNOWN", "비율 역사 60개월 미만 또는 Leader 없음"
    elif cgap >= FALSE_CHEAP_MIN_GAP and (
            (struct_share is not None and struct_share >= 0.6) or (ptrans and median(ptrans) < 0.3) or not moving):
        label = "FALSE_CHEAP"
        why = []
        if struct_share is not None and struct_share >= 0.6:
            why.append(f"구조적 비중 {struct_share:.0%}")
        if ptrans and median(ptrans) < 0.3:
            why.append(f"과거 전달확률 {median(ptrans):.0%}")
        if not moving:
            why.append("Follower 무반응(Persistent Cheapness)")
        reason = " · ".join(why)
    elif m is not None and m > 0.03 and cons in ("STRONG", "OK") and leader_ok and moving:
        label, reason = "LAG_CANDIDATE", f"합의 {cons} · Leader 이동 확인 · 후행 움직임 {'/'.join(sorted({s for p in pairs for s in p.follower_start}))}"
    elif m is not None and m > 0.03:
        label, reason = "LAG_WATCH", f"Mispricing 은 있으나 합의 {cons} / Leader {'확인' if leader_ok else '미확인'} / 후행 {'움직임' if moving else '무반응'}"
    return FollowerResult(fid, band, zone, tier, price_now, pairs, cons, cgap, m, mstatus, label, reason)
