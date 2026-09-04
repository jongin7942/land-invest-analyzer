"""급지(tier)·생활권(life_zone)·대장(leader) 자동 산출 (MASTER_SPEC §35.3·§35.4).

행정구역을 급지로 쓰지 않는다. 법정동 단위로
  1. 가격수준(㎡단가, 최근 24개월)  → 급지 tier (Jenks 자연분류, 데이터가 정함)
  2. 가격 동조성(12개월 변화율 상관) + 거리 + 가격수준 근접  → 생활권 (union-find)
  3. 생활권×면적별 대장 1~3  (상위가격 지속·거래량·하락기 상대강도·선행성·설명력의 순위 평균)
을 만든다. 통근축·학군·외부 매수자 이동은 이번 v0.1 에 넣지 못했다 → method 에 PROXY 표시.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from apt_engine.relative import store
from apt_engine.relative.store import (BAND_M2, N_MONTHS, Complex, Series, change,
                                       haversine_m, median, pearson, percentile)

N_TIERS = 8
LEVEL_WINDOW = 24            # 급지용 가격수준 창(개월)
ZONE_MAX_DIST_M = 2500       # 인접 법정동 병합 최대 거리(중심점)
ZONE_MAX_DIAMETER_M = 5000   # 생활권 지름 상한 — 2.5km 씩 사슬로 이어져 10km 생활권이 되는 것을 막는다
ZONE_MIN_CORR = 0.75         # 12개월 변화율 상관 하한
ZONE_MAX_LEVEL_GAP = 0.35    # log ㎡단가 차이 상한 (≈ 42%)
LEADER_LOOKBACK = 60         # 대장 판정 창(개월)
LEADER_MIN_MONTHS = 36
CRASH_PEAK = ("202107", "202212")
CRASH_TROUGH = ("202301", "202312")
METHOD_NOTE = ("급지=㎡단가 Jenks 8단계(데이터), 생활권=거리≤2.5km·12개월 변화율 상관≥0.75·"
               "가격수준 근접(데이터). 통근축·학군·외부 유입은 미반영 → PROXY. "
               "대장 가중치는 동일가중 순위평균(HEURISTIC).")


@dataclass
class Unit:                      # 법정동
    key: str
    lawd_cd: str
    emd: str
    complex_ids: list = field(default_factory=list)
    lat: float = 0.0
    lon: float = 0.0
    level: float | None = None   # log ㎡단가
    n_level: int = 0
    index: list = field(default_factory=lambda: [None] * N_MONTHS)   # 12개월 log 변화율 중앙값
    tier: int | None = None
    zone: str | None = None


@dataclass
class Leader:
    zone: str
    band: str
    rank: int
    complex_id: int
    composite: float
    parts: dict


def _idx(ym: str) -> int:
    return store._ym_index(ym) - store.MONTH0


# ── 1. 법정동 단위 ──────────────────────────────────────────────────────

def build_units(complexes: dict[int, Complex], prices: dict[tuple[int, str], Series]) -> dict[str, Unit]:
    units: dict[str, Unit] = {}
    for c in complexes.values():
        u = units.get(c.emd_key)
        if u is None:
            u = units[c.emd_key] = Unit(c.emd_key, c.lawd_cd, c.emd)
        u.complex_ids.append(c.id)
    for u in units.values():
        u.lat = sum(complexes[i].lat for i in u.complex_ids) / len(u.complex_ids)
        u.lon = sum(complexes[i].lon for i in u.complex_ids) / len(u.complex_ids)
        levels: list[float] = []
        # 월별 12개월 변화율의 법정동 중앙값 (지수)
        per_month: list[list[float]] = [[] for _ in range(N_MONTHS)]
        for cid in u.complex_ids:
            for band in store.CORE_BANDS:
                s = prices.get((cid, band))
                if s is None:
                    continue
                recent = [v for v in s.p50[-LEVEL_WINDOW:] if v]
                if len(recent) >= 6:
                    levels.append(math.log(median(recent) / BAND_M2[band]))
                for t in range(12, N_MONTHS):
                    ch = change(s.p50, t, 12)
                    if ch is not None and ch > -0.6 and ch < 1.5:
                        per_month[t].append(math.log1p(ch))
        u.level = median(levels) if levels else None
        u.n_level = len(levels)
        u.index = [median(m) if len(m) >= 2 else None for m in per_month]
    return units


# ── 2. 급지 — Jenks 자연분류 (1차원) ──────────────────────────────────

def jenks_breaks(values: list[float], k: int) -> list[float]:
    """1차원 k-means(자연분류 근사). 경계 k−1 개(오름차순)를 돌려준다.
    분위수 고정 컷이 아니라 데이터의 빈 구간에서 급지가 갈리게 한다."""
    data = sorted(values)
    n = len(data)
    if n <= k:
        return data[1:]
    centers = [data[int((i + 0.5) * n / k)] for i in range(k)]
    for _ in range(100):
        groups: list[list[float]] = [[] for _ in range(k)]
        for v in data:
            j = min(range(k), key=lambda i: abs(v - centers[i]))
            groups[j].append(v)
        new = [sum(g) / len(g) if g else centers[i] for i, g in enumerate(groups)]
        if all(abs(a - b) < 1e-9 for a, b in zip(new, centers)):
            break
        centers = new
    centers.sort()
    return [(centers[i] + centers[i + 1]) / 2 for i in range(k - 1)]


def assign_tiers(units: dict[str, Unit], k: int = N_TIERS) -> list[float]:
    levels = [u.level for u in units.values() if u.level is not None and u.n_level >= 2]
    breaks = sorted(jenks_breaks(levels, k))          # 오름차순 경계 k-1 개
    for u in units.values():
        if u.level is None or u.n_level < 2:
            continue
        below = sum(1 for b in breaks if u.level >= b)  # 넘어선 경계 수
        u.tier = k - below                              # 1 = 최고 급지
    return breaks


# ── 3. 생활권 — 제약 union-find ────────────────────────────────────────

def assign_zones(units: dict[str, Unit]) -> dict[str, list[str]]:
    keys = [k for k, u in units.items() if u.level is not None]
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    cands: list[tuple[float, str, str]] = []
    for i, a in enumerate(keys):
        ua = units[a]
        for b in keys[i + 1:]:
            ub = units[b]
            if abs(ua.lat - ub.lat) > 0.03 or abs(ua.lon - ub.lon) > 0.035:
                continue
            d = haversine_m(ua.lat, ua.lon, ub.lat, ub.lon)
            if d > ZONE_MAX_DIST_M or abs(ua.level - ub.level) > ZONE_MAX_LEVEL_GAP:
                continue
            corr, n = pearson(ua.index, ub.index)
            if corr is None or n < 36 or corr < ZONE_MIN_CORR:
                continue
            cands.append((-corr, a, b))
    cands.sort()
    members: dict[str, list[str]] = {k: [k] for k in keys}
    for _, a, b in cands:
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        merged = members[ra] + members[rb]
        lv = [units[k].level for k in merged]
        if max(lv) - min(lv) > ZONE_MAX_LEVEL_GAP * 1.5:
            continue
        # 사슬 병합 방지: 생활권 지름(법정동 중심점 간 최대 거리) 상한
        too_wide = False
        for k1 in members[ra]:
            for k2 in members[rb]:
                if haversine_m(units[k1].lat, units[k1].lon, units[k2].lat, units[k2].lon) > ZONE_MAX_DIAMETER_M:
                    too_wide = True
                    break
            if too_wide:
                break
        if too_wide:
            continue
        parent[rb] = ra
        members[ra] = merged
        del members[rb]
    zones: dict[str, list[str]] = {}
    for root, ks in members.items():
        # 이름 = 단지 수가 가장 많은 법정동 + 시군구코드 (표시용)
        head = max(ks, key=lambda k: len(units[k].complex_ids))
        name = f"Z{units[head].lawd_cd}-{units[head].emd}"
        zones[name] = ks
        for k in ks:
            units[k].zone = name
    for u in units.values():          # 가격 자료 없는 법정동 → 시군구 PROXY 생활권
        if u.zone is None:
            u.zone = f"P{u.lawd_cd}"
    return zones


# ── 4. 대장 자동 선정 ────────────────────────────────────────────────

def _crash_dd(s: Series) -> float | None:
    a, b = _idx(CRASH_PEAK[0]), _idx(CRASH_PEAK[1])
    c, d = _idx(CRASH_TROUGH[0]), _idx(CRASH_TROUGH[1])
    peak = [v for v in s.p50[a:b + 1] if v]
    trough = [v for v in s.p50[c:d + 1] if v]
    if len(peak) < 4 or len(trough) < 4:
        return None
    return median(trough) / median(peak) - 1.0


def pick_leaders(units: dict[str, Unit], zones: dict[str, list[str]],
                 prices: dict[tuple[int, str], Series], band: str) -> list[Leader]:
    out: list[Leader] = []
    t_end = N_MONTHS
    t_start = N_MONTHS - LEADER_LOOKBACK
    for zone, keys in zones.items():
        cids = [cid for k in keys for cid in units[k].complex_ids if (cid, band) in prices]
        cands = [cid for cid in cids
                 if sum(1 for v in prices[(cid, band)].p50[t_start:t_end] if v) >= LEADER_MIN_MONTHS]
        if len(cands) < 3:
            continue
        # 생활권 지수(12개월 변화율 중앙값) — 선행성·설명력 계산용
        zone_ch: list = [None] * N_MONTHS
        for t in range(12, N_MONTHS):
            vals = [change(prices[(cid, band)].p50, t, 12) for cid in cands]
            vals = [v for v in vals if v is not None]
            zone_ch[t] = median(vals) if len(vals) >= 2 else None
        # 월별 상위 3 위 안에 든 비율
        top_hits = {cid: 0 for cid in cands}
        months_ranked = 0
        for t in range(t_start, t_end):
            month = [(prices[(cid, band)].p50[t], cid) for cid in cands if prices[(cid, band)].p50[t]]
            if len(month) < 3:
                continue
            months_ranked += 1
            for _, cid in sorted(month, reverse=True)[:3]:
                top_hits[cid] += 1
        dds = {cid: _crash_dd(prices[(cid, band)]) for cid in cands}
        dd_med = median([v for v in dds.values() if v is not None])
        metrics: dict[int, dict] = {}
        for cid in cands:
            s = prices[(cid, band)]
            own = [change(s.p50, t, 12) for t in range(N_MONTHS)]
            # 선행성: 내 변화율 vs 3개월 뒤 생활권 변화율 상관 − 3개월 앞 상관
            lead_c, _ = pearson(own[:-3], zone_ch[3:])
            lag_c, _ = pearson(own[3:], zone_ch[:-3])
            lead = (lead_c - lag_c) if lead_c is not None and lag_c is not None else None
            expl, _ = pearson(own, zone_ch)
            metrics[cid] = {
                "top3_share": top_hits[cid] / months_ranked if months_ranked else None,
                "volume": sum(s.n[-24:]) / 24.0,
                "crash_rel": (dds[cid] - dd_med) if dds[cid] is not None and dd_med is not None else None,
                "lead": lead,
                "explain": expl,
            }
        # 항목별 백분위 순위(생활권 안) → 평균. 없는 항목은 평균에서 뺀다(중립값 금지).
        comp: dict[int, float] = {}
        for key in ("top3_share", "volume", "crash_rel", "lead", "explain"):
            vals = sorted((m[key], cid) for cid, m in metrics.items() if m[key] is not None)
            n = len(vals)
            for i, (_, cid) in enumerate(vals):
                metrics[cid][key + "_rank"] = i / (n - 1) if n > 1 else 0.5
        for cid, m in metrics.items():
            ranks = [m[k + "_rank"] for k in ("top3_share", "volume", "crash_rel", "lead", "explain")
                     if k + "_rank" in m]
            comp[cid] = sum(ranks) / len(ranks) if ranks else 0.0
        ordered = sorted(comp.items(), key=lambda kv: -kv[1])[:3]
        for rank, (cid, score) in enumerate(ordered, start=1):
            out.append(Leader(zone, band, rank, cid, round(score, 4),
                              {k: (round(v, 4) if isinstance(v, float) else v)
                               for k, v in metrics[cid].items() if not k.endswith("_rank")}))
    return out


# ── 저장 ──────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS relative_zone (
    emd_key TEXT PRIMARY KEY, lawd_cd TEXT, emd_name TEXT, life_zone TEXT, tier INTEGER,
    price_level_log_m2 REAL, n_level INTEGER, method TEXT, as_of TEXT
);
CREATE TABLE IF NOT EXISTS zone_leader (
    life_zone TEXT, area_band TEXT, rank INTEGER, complex_id INTEGER, composite REAL,
    parts_json TEXT, method TEXT, as_of TEXT,
    PRIMARY KEY (life_zone, area_band, rank, as_of)
);
"""


def save(conn: sqlite3.Connection, units: dict[str, Unit], zones: dict[str, list[str]],
         leaders: list[Leader], *, as_of: str) -> None:
    import json
    conn.executescript(DDL)
    conn.execute("DELETE FROM relative_zone")
    conn.executemany(
        "INSERT INTO relative_zone VALUES (?,?,?,?,?,?,?,?,?)",
        [(u.key, u.lawd_cd, u.emd, u.zone, u.tier, u.level, u.n_level,
          "DATA" if u.level is not None else "PROXY_SIGUNGU", as_of) for u in units.values()])
    conn.execute("DELETE FROM zone_leader WHERE as_of = ?", (as_of,))
    conn.executemany(
        "INSERT INTO zone_leader VALUES (?,?,?,?,?,?,?,?)",
        [(l.zone, l.band, l.rank, l.complex_id, l.composite, json.dumps(l.parts, ensure_ascii=False),
          "RANK_AVG_HEURISTIC", as_of) for l in leaders])
    # complex.life_zone / life_zone 테이블 — 기존 leader_link 빌더가 읽는 자리
    conn.execute("DELETE FROM life_zone WHERE curated_by = 'relative.zones v0.1'")
    conn.executemany(
        "INSERT OR REPLACE INTO life_zone (key, name, sido, rationale, curated_by) VALUES (?,?,?,?,?)",
        [(z, z, z[1:3] if z[0] in "ZP" else None,
          f"법정동 {len(ks)}개 · " + METHOD_NOTE, "relative.zones v0.1") for z, ks in zones.items()])
    conn.executemany("UPDATE complex SET life_zone = ? WHERE lawd_cd = ? AND emd_name = ?",
                     [(u.zone, u.lawd_cd, u.emd) for u in units.values()])
    conn.commit()
