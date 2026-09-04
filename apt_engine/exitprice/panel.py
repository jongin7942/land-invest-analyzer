"""Walk-Forward 패널 — 진입 시점 Y(매년 6월)에 알 수 있었던 변수로 5년 뒤 가격을 설명한다.

변수는 종인님의 가격 이론(memory: price-theory-hierarchy)을 그대로 옮긴 것이다.
  수요의 뿌리   : 직장 근접(job — 자료 없음, 대리값 역·중심 거리) · 끼리끼리(급지 tier, 시군구 대비 상대가격)
               · 교육·환경(학원 밀도, 연식, 세대수)
  차선책       : 중심(선점지)까지의 거리 — 멀어질수록 차선책이 되고, 일정 거리 밖이면 쏠림에서 빠진다
  선점·쏠림    : 상위 급지 중심까지 거리, 상위 급지 상승의 전파(시군구·수도권 5년 모멘텀)
  시장 상태     : 자기 가격 백분위(과열/저점), 전세가율(실수요 하한), 공급(입주), 거래량, 시군구 국면
미래 정보는 쓰지 않는다. 진입 뒤 승인된 단지(planned supply)는 '입주예정' 대리값이라 leakage 위험을 표시한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from apt_engine.features import regime as regime_mod
from apt_engine.relative import store, zones as zones_mod
from apt_engine.relative.store import N_MONTHS, MONTHS, Complex, Series, change, haversine_m, median

HORIZON = 60
ENTRY_MONTH = "06"
SMOOTH = 2          # 목표·기준 가격은 ±2개월 평균(단일월 튐 방지)

FEATURES = [
    # 시장
    "metro_mom5", "gu_mom5", "gu_mom1", "regime",
    # 자기 상태
    "mom1", "mom3", "own_pct", "rel_gu", "log_vol", "jeonse_ratio", "jeonse_mom1",
    # 끼리끼리·계급
    "tier", "dist_center_km", "dist_tier1_km",
    # 교육·환경·상품
    "log_academy", "age", "log_hh",
    # 직장·교통(대리)
    "station_km", "station_planned",
    # 공급
    "supply_recent", "supply_planned",
]
FEATURE_SETS = {
    "A_market": ["metro_mom5", "gu_mom5", "gu_mom1", "regime"],
    "B_+own": ["metro_mom5", "gu_mom5", "gu_mom1", "regime", "mom1", "mom3", "own_pct", "rel_gu", "log_vol", "jeonse_ratio", "jeonse_mom1"],
    "C_+theory": FEATURES,
}


def ym_idx(ym: str) -> int:
    return store._ym_index(ym) - store.MONTH0


def smooth_price(s: Series, t: int) -> float | None:
    vals = [s.p50[i] for i in range(max(0, t - SMOOTH), min(N_MONTHS, t + SMOOTH + 1)) if s.p50[i]]
    return sum(vals) / len(vals) if len(vals) >= 2 else None


@dataclass
class Row:
    complex_id: int
    band: str
    entry_ym: str
    price: float
    target: float | None            # log(P_{Y+5}/P_Y)
    x: dict


class PanelBuilder:
    def __init__(self, complexes: dict[int, Complex], prices: dict[tuple[int, str], Series],
                 jeonse: dict[tuple[int, str], list], stations: list[tuple[float, float, str, str | None]]):
        """stations: (lat, lon, opened_ym or None, status_date 'YYYY-MM-DD' or None)"""
        self.cx, self.prices, self.jeonse, self.stations = complexes, prices, jeonse, stations
        self.by_gu: dict[str, list[tuple[int, str]]] = {}
        for (cid, band) in prices:
            self.by_gu.setdefault(complexes[cid].lawd_cd, []).append((cid, band))
        self._grid: dict[tuple[int, int], list[int]] = {}
        for c in complexes.values():
            self._grid.setdefault(self._gk(c.lat, c.lon), []).append(c.id)
        self._sgrid: dict[tuple[int, int], list[int]] = {}
        for i, (la, lo, _, _) in enumerate(stations):
            self._sgrid.setdefault(self._gk(la, lo), []).append(i)
        self._cache: dict = {}

    @staticmethod
    def _gk(lat: float, lon: float, cell: float = 0.02) -> tuple[int, int]:
        return int(lat / cell), int(lon / cell)

    def _near(self, grid, lat, lon, r=1):
        gk = self._gk(lat, lon)
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                yield from grid.get((gk[0] + dx, gk[1] + dy), ())

    # ── 지역 지수 ──
    def gu_mom(self, lawd: str, t: int, months: int) -> float | None:
        key = ("gu", lawd, t, months)
        if key not in self._cache:
            vals = [change(self.prices[k].p50, t, months) for k in self.by_gu.get(lawd, [])]
            vals = [math.log1p(v) for v in vals if v is not None and -0.7 < v < 2.0]
            self._cache[key] = median(vals) if len(vals) >= 5 else None
        return self._cache[key]

    def metro_mom(self, t: int, months: int) -> float | None:
        key = ("metro", t, months)
        if key not in self._cache:
            vals = [self.gu_mom(g, t, months) for g in self.by_gu]
            vals = [v for v in vals if v is not None]
            self._cache[key] = median(vals) if vals else None
        return self._cache[key]

    def gu_regime(self, lawd: str, t: int) -> float | None:
        key = ("regime", lawd, t)
        if key not in self._cache:
            series = [self.prices[k] for k in self.by_gu.get(lawd, [])]
            c12 = [change(s.p50, t, 12) for s in series]; c12 = [v for v in c12 if v is not None]
            c3 = [change(s.p50, t, 3) for s in series]; c3 = [v for v in c3 if v is not None]
            if len(c12) < 3:
                self._cache[key] = None
            else:
                v_recent = sum(sum(s.n[t - 2:t + 1]) for s in series)
                v_prior = sum(sum(s.n[t - 14:t - 2]) for s in series) / 4.0
                name, _ = regime_mod.classify(price_12m=median(c12), price_3m=median(c3) if len(c3) >= 3 else None,
                                              volume_ratio=(v_recent / v_prior) if v_prior > 0 else None)
                self._cache[key] = float(regime_mod.REGIME_INDEX.get(name, -1)) if name in regime_mod.REGIME_INDEX else None
        return self._cache[key]

    def gu_median_price(self, lawd: str, band: str, t: int) -> float | None:
        key = ("gumed", lawd, band, t)
        if key not in self._cache:
            vals = [self.prices[k].p50[t] for k in self.by_gu.get(lawd, []) if k[1] == band and self.prices[k].p50[t]]
            self._cache[key] = median(vals) if len(vals) >= 3 else None
        return self._cache[key]

    # ── 계급(급지) at Y ──
    def tiers_at(self, t: int) -> tuple[dict[str, int], dict[str, tuple[float, float, int]]]:
        """법정동 → tier, 법정동 → (lat, lon, tier). 24개월 창의 ㎡단가 log 중앙값을 8단계 자연분류."""
        key = ("tiers", t)
        if key not in self._cache:
            levels: dict[str, list[float]] = {}
            for (cid, band), s in self.prices.items():
                vals = [v for v in s.p50[max(0, t - 23):t + 1] if v]
                if len(vals) >= 6:
                    levels.setdefault(self.cx[cid].emd_key, []).append(math.log(median(vals) / store.BAND_M2[band]))
            lv = {k: median(v) for k, v in levels.items() if len(v) >= 2}
            breaks = sorted(zones_mod.jenks_breaks(list(lv.values()), zones_mod.N_TIERS)) if len(lv) > 8 else []
            tiers = {k: zones_mod.N_TIERS - sum(1 for b in breaks if v >= b) for k, v in lv.items()}
            cent: dict[str, list] = {}
            for c in self.cx.values():
                if c.emd_key in tiers:
                    cent.setdefault(c.emd_key, []).append((c.lat, c.lon))
            info = {k: (sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v), tiers[k]) for k, v in cent.items()}
            self._cache[key] = (tiers, info)
        return self._cache[key]

    def dist_to_tier(self, lat: float, lon: float, info: dict, *, max_tier: int, own_key: str) -> float | None:
        best = None
        for k, (la, lo, tr) in info.items():
            if tr > max_tier or k == own_key:
                continue
            d = haversine_m(lat, lon, la, lo)
            if best is None or d < best:
                best = d
        return best / 1000.0 if best is not None else None

    # ── 근처 공급 · 역 · 학원 ──
    def supply_near(self, c: Complex, y0: int, y1: int, radius=2000) -> float:
        tot = 0
        for oid in self._near(self._grid, c.lat, c.lon):
            o = self.cx[oid]
            if o.id != c.id and o.approval_year and y0 < o.approval_year <= y1 and haversine_m(c.lat, c.lon, o.lat, o.lon) <= radius:
                tot += o.households or 0
        return math.log1p(tot)

    def station_feats(self, c: Complex, ym: str) -> tuple[float | None, float]:
        best, planned = None, 0.0
        yidx = store._ym_index(ym)
        for i in self._near(self._sgrid, c.lat, c.lon):
            la, lo, opened, sdate = self.stations[i]
            d = haversine_m(c.lat, c.lon, la, lo)
            if opened and store._ym_index(opened) <= yidx:
                if best is None or d < best:
                    best = d
            elif d <= 1500 and sdate and sdate[:7].replace("-", "") <= ym:
                planned = 1.0      # 진입 시점에 이미 공표(착공·계획)된 미개통 역
        return (best / 1000.0 if best is not None else None), planned

    # ── 행 ──
    def row(self, cid: int, band: str, entry_ym: str) -> Row | None:
        s = self.prices[(cid, band)]
        t = ym_idx(entry_ym)
        if t < 60 or t >= N_MONTHS:
            return None
        p0 = smooth_price(s, t)
        if not p0:
            return None
        c = self.cx[cid]
        tiers, info = self.tiers_at(t)
        tier = tiers.get(c.emd_key)
        if tier is None:
            return None
        hist = [v for v in s.p50[:t + 1] if v]
        own_pct = sum(1 for v in hist if v <= p0) / len(hist) if len(hist) >= 24 else None
        gumed = self.gu_median_price(c.lawd_cd, band, t)
        jr = self.jeonse.get((cid, band))
        j0 = jr[t] if jr else None
        j1 = jr[t - 12] if jr and t >= 12 else None
        st_km, planned = self.station_feats(c, entry_ym)
        year = int(entry_ym[:4])
        x = {
            "metro_mom5": self.metro_mom(t, 60), "gu_mom5": self.gu_mom(c.lawd_cd, t, 60),
            "gu_mom1": self.gu_mom(c.lawd_cd, t, 12), "regime": self.gu_regime(c.lawd_cd, t),
            "mom1": math.log(p0 / smooth_price(s, t - 12)) if smooth_price(s, t - 12) else None,
            "mom3": math.log(p0 / smooth_price(s, t - 36)) if smooth_price(s, t - 36) else None,
            "own_pct": own_pct,
            "rel_gu": math.log(p0 / gumed) if gumed else None,
            "log_vol": math.log1p(sum(s.n[t - 11:t + 1])),
            "jeonse_ratio": (j0 / p0) if j0 else None,
            "jeonse_mom1": math.log(j0 / j1) if j0 and j1 else None,
            "tier": float(tier),
            "dist_center_km": self.dist_to_tier(c.lat, c.lon, info, max_tier=2, own_key="") ,
            "dist_tier1_km": self.dist_to_tier(c.lat, c.lon, info, max_tier=1, own_key=""),
            "log_academy": math.log1p(c.academies_500m) if c.academies_500m is not None else None,
            "age": float(year - c.approval_year) if c.approval_year else None,
            "log_hh": math.log1p(c.households) if c.households else None,
            "station_km": st_km, "station_planned": planned,
            "supply_recent": self.supply_near(c, year - 3, year),
            "supply_planned": self.supply_near(c, year, year + 2),     # leakage 위험(입주예정 대리)
        }
        t1 = t + HORIZON
        p1 = smooth_price(s, t1) if t1 < N_MONTHS else None
        target = math.log(p1 / p0) if p1 else None
        return Row(cid, band, entry_ym, p0, target, x)

    def build(self, entry_years: list[int]) -> list[Row]:
        out: list[Row] = []
        for y in entry_years:
            ym = f"{y}{ENTRY_MONTH}"
            for (cid, band) in self.prices:
                r = self.row(cid, band, ym)
                if r is not None:
                    out.append(r)
        return out


def load_stations(conn) -> list[tuple[float, float, str | None, str | None]]:
    rows = conn.execute("SELECT lat, lon, opened_ym, status_date, status FROM transit_station WHERE lat IS NOT NULL").fetchall()
    out = []
    for r in rows:
        opened = str(r["opened_ym"]) if r["opened_ym"] else None
        if opened is None and r["status"] == "운영중":
            opened = "200001"          # 자료 시작 전부터 운영 중
        out.append((float(r["lat"]), float(r["lon"]), opened, r["status_date"]))
    return out
