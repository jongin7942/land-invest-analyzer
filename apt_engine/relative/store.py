"""Relative Price Gap Engine — 공용 데이터 적재 (MASTER_SPEC §35).

월별 가격 스냅샷을 (단지, 면적밴드) → 길이 240 의 배열로 올린다. numpy 가 없는
환경이라 순수 파이썬 리스트를 쓴다. 이름은 계산에 쓰지 않는다(§3) — 표시용으로만 싣는다.
"""
from __future__ import annotations

import csv
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "rules"

FIRST_YM = "200610"
LAST_YM = "202609"


def _ym_index(ym: str) -> int:
    return int(ym[:4]) * 12 + int(ym[4:6]) - 1


MONTH0 = _ym_index(FIRST_YM)
N_MONTHS = _ym_index(LAST_YM) - MONTH0 + 1
MONTHS = [f"{(MONTH0 + i) // 12:04d}{(MONTH0 + i) % 12 + 1:02d}" for i in range(N_MONTHS)]

BAND_M2 = {"84": 84.0, "59": 59.0, "74": 74.0, "114": 114.0, "129": 129.0, "101": 101.0,
           "66": 66.0, "45": 45.0, "145": 145.0, "u40": 35.0, "97": 97.0, "o165": 180.0, "88": 88.0}
CORE_BANDS = ("84", "59", "74")


@dataclass
class Series:
    p50: list = field(default_factory=lambda: [None] * N_MONTHS)
    p25: list = field(default_factory=lambda: [None] * N_MONTHS)
    p75: list = field(default_factory=lambda: [None] * N_MONTHS)
    n: list = field(default_factory=lambda: [0] * N_MONTHS)

    def months_with_price(self) -> int:
        return sum(1 for v in self.p50 if v)

    def last_median(self, k: int = 6) -> float | None:
        vals = [v for v in self.p50[-k:] if v]
        return median(vals) if vals else None


@dataclass
class Complex:
    id: int
    name: str
    lawd_cd: str
    emd: str
    lat: float
    lon: float
    households: int | None
    approval_year: int | None
    academies_500m: int | None = None
    station_m: float | None = None
    station_opened_recent: bool = False   # 최근 5년 내 1.5km 안 개통 (§15 평균회귀 함정 플래그)

    @property
    def emd_key(self) -> str:
        return f"{self.lawd_cd}|{self.emd}"


def median(vals: list) -> float | None:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return float(v[m]) if len(v) % 2 else (v[m - 1] + v[m]) / 2.0


def percentile(vals: list, q: float) -> float | None:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    pos = (len(v) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (pos - lo)


def pearson(xs: list, ys: list) -> tuple[float | None, int]:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 12:
        return None, len(pairs)
    n = len(pairs)
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx <= 0 or syy <= 0:
        return None, n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    return sxy / math.sqrt(sxx * syy), n


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def change(series: list, t: int, months: int) -> float | None:
    """t 시점 값 / (t−months) 시점 값 − 1. 둘 중 하나라도 없으면 None."""
    if t - months < 0:
        return None
    a, b = series[t], series[t - months]
    if not a or not b:
        return None
    return a / b - 1.0


# ── 적재 ──────────────────────────────────────────────────────────────

def load_complexes(conn: sqlite3.Connection) -> dict[int, Complex]:
    out: dict[int, Complex] = {}
    for r in conn.execute(
            "SELECT id, name, lawd_cd, emd_name, lat, lon, apt_households, approval_year "
            "  FROM complex WHERE canonical_id IS NULL AND lat IS NOT NULL AND lawd_cd IS NOT NULL"):
        out[int(r["id"])] = Complex(int(r["id"]), r["name"] or "", r["lawd_cd"], r["emd_name"] or "",
                                    float(r["lat"]), float(r["lon"]),
                                    int(r["apt_households"]) if r["apt_households"] else None,
                                    int(r["approval_year"]) if r["approval_year"] else None)
    return out


def load_prices(conn: sqlite3.Connection, complexes: dict[int, Complex],
                bands: tuple[str, ...] = CORE_BANDS) -> dict[tuple[int, str], Series]:
    out: dict[tuple[int, str], Series] = {}
    q = ("SELECT complex_id, area_band, as_of_ym, price_p50, price_p25, price_p75, sample_n "
         f"  FROM price_snapshot WHERE area_band IN ({','.join('?' * len(bands))}) "
         "   AND price_p50 IS NOT NULL")
    for cid, band, ym, p50, p25, p75, n in conn.execute(q, bands):
        if cid not in complexes:
            continue
        i = _ym_index(ym) - MONTH0
        if i < 0 or i >= N_MONTHS:
            continue
        s = out.get((cid, band))
        if s is None:
            s = out[(cid, band)] = Series()
        s.p50[i] = int(p50)
        s.p25[i] = int(p25) if p25 else None
        s.p75[i] = int(p75) if p75 else None
        s.n[i] = int(n or 0)
    return out


def load_jeonse(conn: sqlite3.Connection, keys: set[tuple[int, str]]) -> dict[tuple[int, str], list]:
    out: dict[tuple[int, str], list] = {}
    bands = tuple(sorted({b for _, b in keys}))
    if not bands:
        return out
    for cid, band, ym, d in conn.execute(
            "SELECT complex_id, area_band, as_of_ym, deposit_p50 FROM jeonse_snapshot "
            f" WHERE area_band IN ({','.join('?' * len(bands))}) AND deposit_p50 IS NOT NULL", bands):
        if (cid, band) not in keys:
            continue
        i = _ym_index(ym) - MONTH0
        if 0 <= i < N_MONTHS:
            out.setdefault((cid, band), [None] * N_MONTHS)[i] = int(d)
    return out


def _grid_key(lat: float, lon: float, cell: float = 0.005) -> tuple[int, int]:
    return int(lat / cell), int(lon / cell)


def attach_academies(complexes: dict[int, Complex], radius_m: float = 500.0) -> int:
    """학원 좌표(경기 + NEIS 서울·인천 있으면)로 500m 안 학원 수. 학군 구조 프리미엄의 대리값(§35.9)."""
    pts: list[tuple[float, float]] = []
    for name in ("gg_academies.csv", "academies_neis.csv"):
        p = RULES / name
        if not p.exists():
            continue
        with p.open(encoding="utf-8", newline="") as f:
            rd = csv.DictReader(f)
            cols = rd.fieldnames or []
            lat_c = next((c for c in cols if "위도" in c or c.lower() in ("lat", "latitude")), None)
            lon_c = next((c for c in cols if "경도" in c or c.lower() in ("lon", "lng", "longitude")), None)
            if not lat_c or not lon_c:
                continue
            for r in rd:
                try:
                    pts.append((float(r[lat_c]), float(r[lon_c])))
                except (TypeError, ValueError):
                    pass
    if not pts:
        return 0
    grid: dict[tuple[int, int], list] = {}
    for la, lo in pts:
        grid.setdefault(_grid_key(la, lo), []).append((la, lo))
    for c in complexes.values():
        gk = _grid_key(c.lat, c.lon)
        cnt = 0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for la, lo in grid.get((gk[0] + dx, gk[1] + dy), ()):
                    if haversine_m(c.lat, c.lon, la, lo) <= radius_m:
                        cnt += 1
        c.academies_500m = cnt
    return len(pts)


def attach_stations(conn: sqlite3.Connection, complexes: dict[int, Complex], *,
                    as_of_ym: str = LAST_YM, recent_months: int = 60) -> int:
    rows = conn.execute(
        "SELECT lat, lon, opened_ym FROM transit_station "
        " WHERE lat IS NOT NULL AND status IN ('개통','운영중') AND opened_ym IS NOT NULL").fetchall()
    st = [(float(r["lat"]), float(r["lon"]), str(r["opened_ym"])) for r in rows]
    if not st:
        return 0
    grid: dict[tuple[int, int], list] = {}
    for la, lo, ym in st:
        grid.setdefault(_grid_key(la, lo, 0.02), []).append((la, lo, ym))
    cut = _ym_index(as_of_ym) - recent_months
    for c in complexes.values():
        gk = _grid_key(c.lat, c.lon, 0.02)
        best = None
        recent = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for la, lo, ym in grid.get((gk[0] + dx, gk[1] + dy), ()):
                    d = haversine_m(c.lat, c.lon, la, lo)
                    if _ym_index(ym) > _ym_index(as_of_ym):
                        continue
                    if best is None or d < best:
                        best = d
                    if d <= 1500 and _ym_index(ym) >= cut:
                        recent = True
        c.station_m = best
        c.station_opened_recent = recent
    return len(st)
