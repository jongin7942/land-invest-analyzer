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

from apt_engine.exitprice import redev as redev_mod
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
THEORY2 = [
    # 비선형·상호작용 (v0.2): 신축 프리미엄 소멸 구간, 급지×국면, 자기 백분위 제곱
    "age_new", "age_mid", "age_old", "tier_x_regime", "own_pct_sq", "rel_gu_x_tier",
    # 덜 오른 곳: 법정동 3년 상대 모멘텀(법정동 − 수도권), 전세-매매 갭 축소 속도
    "emd_rel_mom3", "jeonse_gap_closing",
    # 시군구 공급: 진입 후 2년 승인 세대 / 시군구 재고 (leakage 위험 — 입주예정 대리)
    "gu_supply_ratio",
    # 급지 내 위치: 법정동 ㎡단가 대비 자기 ㎡단가
    "rel_emd",
]
DIFFUSION = ["emd_lead_months", "lead_x_cycle", "lag_catchup_gap", "vol_lead"]
CYCLE = ["metro_dd_peak", "metro_vs_ma5", "metro_jeonse_ratio", "metro_vol_ratio", "bok_rate", "bok_rate_chg1", "metro_mom1", "metro_mom3"]
JOB_FEATURES = ["jobs_emd", "jobs_3km"]            # 국민연금 사업장(2016~) — 그 전 진입은 PROXY 스냅샷
EXPERT_GROUPS = {
    # 소위 '전문가 이론'을 검증 가능한 변수로 옮긴 것 (2026-09-05, 종인님 지시). 이름에 점수를 주지 않고 실측으로만 판정.
    "갭투자·전세압력": ["gap_abs", "jeonse_mom3", "jeonse_ratio_x_regime"],          # 갭 작은 곳에 투자수요 / 전세 상승이 매매를 밀어올림
    "고점대비·가격대": ["own_dd_peak", "price_level", "price_x_regime"],              # 전고점 대비 낙폭 평균회귀 / 저가 순환매
    "거래량선행": ["own_vol_ratio", "gu_vol_ratio"],                                   # 거래량이 가격에 선행
    "급지사다리갭": ["upper_tier_gap", "upper_tier_gap_dev"],                          # 상급지와 벌어진 갭은 메워진다
    "대장갭메우기": ["leader_gap", "leader_gap_dev"],                                  # 같은 구 대장(상위10%)과의 갭 축소
    "브랜드·재건축사업성": ["brand", "far", "land_per_hh", "far_low_old", "hh_x_age_old"],  # 1군 브랜드 / 저용적률 구축 대단지
    "평형순환매": ["band_59", "band_84", "band84_x_regime"],                           # 소형 먼저, 국평 나중
    "상호작용": ["acad_x_84", "age_x_tier", "dist_center_x_regime"],                   # 학군은 국평에서 / 외곽은 상승 후반에
    "수도권공급": ["metro_supply_ratio"],                                              # 입주물량(승인 대리) 이론
}
EXPERT = [f for fs in EXPERT_GROUPS.values() for f in fs]
POLICY_GROUPS = {
    # 2026-09-05 종인님 지시: 규제·공사비·분양가상한제·분양가·청약을 변수로. 자료가 있는 것부터.
    "규제지역": ["reg_adj", "reg_hot", "reg_months", "reg_balloon"],          # 조정대상/투기과열 여부, 지정 후 경과월, 풍선효과(비규제인데 같은 시도 규제 비중 높음)
    "분양가상한제": ["cap_zone"],
    "미분양": ["gu_unsold_per_1k", "gu_unsold_chg12", "metro_unsold_chg12"],  # 시군구 미분양/재고 1천세대, 12개월 변화, 수도권 변화
    "분양가": ["sido_bunyang_ratio", "sido_bunyang_mom12"],                    # 시도 신규 분양가 ÷ 기존 ㎡단가, 분양가 12개월 상승률
}
POLICY = [f for fs in POLICY_GROUPS.values() for f in fs]
BRAND_BUILDERS = ("삼성물산", "현대건설", "GS건설", "대우건설", "대림산업", "DL이앤씨", "포스코", "롯데건설", "현대산업개발", "HDC", "SK에코", "SK건설", "한화건설", "현대엔지니어링")
JOB_GROWTH = ["jobs_growth5"]                       # 5년 전 스냅샷이 있는 진입연도(2021~)만
FEATURE_SETS = {
    "A_market": ["metro_mom5", "gu_mom5", "gu_mom1", "regime"],
    "B_+own": ["metro_mom5", "gu_mom5", "gu_mom1", "regime", "mom1", "mom3", "own_pct", "rel_gu", "log_vol", "jeonse_ratio", "jeonse_mom1"],
    "C_+theory": FEATURES,
    "D_+jobs": FEATURES + JOB_FEATURES,
    "E_+theory2": FEATURES + JOB_FEATURES + THEORY2,
    "F_+cycle": FEATURES + JOB_FEATURES + THEORY2 + CYCLE,
    "E2_+relmom": FEATURES + JOB_FEATURES + THEORY2 + ["rel_gu_mom3", "vol_lead", "emd_lead_months"],
    "E3_+redev": FEATURES + JOB_FEATURES + THEORY2 + ["redev_stage", "redev_active"],
    "G_+diffusion": FEATURES + JOB_FEATURES + THEORY2 + DIFFUSION,
    "H_all": FEATURES + JOB_FEATURES + THEORY2 + DIFFUSION + CYCLE,
    "X_expert": FEATURES + JOB_FEATURES + THEORY2 + EXPERT,
    "P_policy": FEATURES + JOB_FEATURES + THEORY2 + POLICY,
}
BOK: dict[int, float] = {}
# ── 정책·공급 자료 (rules/) ──
REG_ROWS: list[tuple] = []          # (lawd_cd, emd or None, zone_type, from 'YYYYMMDD', to 'YYYYMMDD' or None, exclude set)
UNSOLD: dict[tuple[str, str], int] = {}     # (lawd_cd, ym) → 미분양
UNSOLD_SIDO: dict[tuple[str, str], int] = {}   # (sido, ym) → 시도 계
BUNYANG: dict[tuple[str, str], float] = {}  # (sido, ym) → ㎡당 분양가(천원, 모든면적)
try:
    import csv as _csv2
    from pathlib import Path as _P2
    _R = _P2(__file__).resolve().parents[2] / "rules"
    with (_R / "regulation_zone_history.csv").open(encoding="utf-8") as _f:
        pass  # DB 적재본을 쓰지 않고 CSV 를 직접 읽지는 않음(코드 매핑은 DB region 필요) → 아래 load_regulation()
    with (_R / "unsold_sigungu_monthly.csv").open(encoding="utf-8") as _f:
        for _r in _csv2.DictReader(_f):
            if _r["unsold"] in ("", None):
                continue
            if _r["sigungu"] == "계":
                UNSOLD_SIDO[(_r["sido"], _r["ym"])] = int(_r["unsold"])
            elif _r["lawd_cd"]:
                UNSOLD[(_r["lawd_cd"], _r["ym"])] = int(_r["unsold"])
    with (_R / "hug_bunyang_sido_monthly.csv").open(encoding="utf-8") as _f:
        for _r in _csv2.DictReader(_f):
            if _r["size"] == "모든면적" and _r["price_per_m2_kwon"]:
                BUNYANG[(_r["sido"], f"{_r['year']}{int(_r['month']):02d}")] = float(_r["price_per_m2_kwon"])
except Exception:
    pass


def load_regulation(conn=None) -> None:
    """rules/regulation_zone_expanded.csv → REG_ROWS (조정대상·투기과열·투기지역·분양가상한제)."""
    REG_ROWS.clear()
    import csv as _c
    from pathlib import Path as _P
    fp = _P(__file__).resolve().parents[2] / "rules" / "regulation_zone_expanded.csv"
    if not fp.exists():
        return
    with fp.open(encoding="utf-8") as f:
        for r in _c.DictReader(f):
            excl = set(x for x in (r["exclude"] or "").split("|") if x)
            REG_ROWS.append((r["lawd_cd"], r["emd_name"] or None, r["zone_type"], r["effective_from"].replace("-", ""),
                             (r["effective_to"] or "").replace("-", "") or None, excl))


load_regulation()


def reg_status(lawd: str, emd: str, ymd: str) -> dict:
    """ymd 'YYYYMMDD' 시점의 규제 상태. 조정대상/투기과열/분양가상한제 여부와 조정대상 지정 후 경과월."""
    out = {"adj": 0.0, "hot": 0.0, "cap": 0.0, "adj_from": None}
    for lc, e, z, f, t, excl in REG_ROWS:
        if lc != lawd or f > ymd or (t and t <= ymd):
            continue
        if e and e != emd:
            continue
        if emd in excl:
            continue
        if z == "조정대상지역":
            out["adj"] = 1.0
            out["adj_from"] = f if out["adj_from"] is None or f < out["adj_from"] else out["adj_from"]
        elif z == "투기과열지구":
            out["hot"] = 1.0
        elif z == "분양가상한제":
            out["cap"] = 1.0
    return out
try:
    import csv as _csv
    from pathlib import Path as _P
    with (_P(__file__).resolve().parents[2] / "rules" / "bok_base_rate.csv").open(encoding="utf-8") as _f:
        for _r in _csv.DictReader(_f):
            BOK[int(_r["year"])] = float(_r["rate_june"])
except Exception:
    pass


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
                 jeonse: dict[tuple[int, str], list], stations: list[tuple[float, float, str, str | None]],
                 jobs=None, tier_complexes=None, tier_prices=None):
        """stations: (lat, lon, opened_ym or None, status_date 'YYYY-MM-DD' or None); jobs: exitprice.jobs.Jobs
        tier_complexes/tier_prices: 급지·중심거리 지도를 만들 때 쓸 전체 단지(없으면 complexes/prices). 행은 complexes 만."""
        self.cx, self.prices, self.jeonse, self.stations = complexes, prices, jeonse, stations
        self.jobs = jobs
        self.tier_cx = tier_complexes or complexes
        self.tier_prices = tier_prices or prices
        self.redev = redev_mod.load()          # 정비사업 단계 일자(그 시점 이하만 사용)
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

    def _ensure_emd(self):
        if not hasattr(self, "by_emd"):
            self.by_emd = {}
            for (cid, band) in self.prices:
                self.by_emd.setdefault(self.cx[cid].emd_key, []).append((cid, band))

    def emd_mom(self, emd_key: str, t: int, months: int) -> float | None:
        key = ("emd", emd_key, t, months)
        if key not in self._cache:
            self._ensure_emd()
            vals = [change(self.prices[k].p50, t, months) for k in self.by_emd.get(emd_key, [])]
            vals = [math.log1p(v) for v in vals if v is not None and -0.7 < v < 2.0]
            self._cache[key] = median(vals) if len(vals) >= 3 else None
        return self._cache[key]

    def emd_level(self, emd_key: str, t: int) -> float | None:
        key = ("emdlv", emd_key, t)
        if key not in self._cache:
            self._ensure_emd()
            vals = [math.log(self.prices[k].p50[t] / store.BAND_M2[k[1]]) for k in self.by_emd.get(emd_key, []) if self.prices[k].p50[t]]
            self._cache[key] = median(vals) if len(vals) >= 3 else None
        return self._cache[key]

    def metro_level(self, t: int) -> float | None:
        """수도권 가격 수준 지수(log ㎡단가 중앙값). 구성 고정이 아니라 PROXY."""
        key = ("mlevel", t)
        if key not in self._cache:
            vals = [math.log(s.p50[t] / store.BAND_M2[k[1]]) for k, s in self.prices.items() if s.p50[t]]
            self._cache[key] = median(vals) if len(vals) >= 50 else None
        return self._cache[key]

    def cycle_feats(self, t: int, year: int) -> dict:
        key = ("cycle", t)
        if key not in self._cache:
            lv = self.metro_level(t)
            hist = [self.metro_level(i) for i in range(max(0, t - 120), t + 1, 3)]
            hist = [h for h in hist if h is not None]
            peak = max(hist) if hist else None
            ma5 = [self.metro_level(i) for i in range(max(0, t - 59), t + 1, 3)]
            ma5 = [h for h in ma5 if h is not None]
            jr = []
            for k, s in self.prices.items():
                j = self.jeonse.get(k)
                if j and j[t] and s.p50[t]:
                    jr.append(j[t] / s.p50[t])
            v_recent = sum(sum(s.n[max(0, t - 11):t + 1]) for s in self.prices.values())
            v_prior = (sum(sum(s.n[max(0, t - 47):t - 11]) for s in self.prices.values()) / 3.0) if t >= 47 else 0.0
            self._cache[key] = {
                "metro_dd_peak": (lv - peak) if lv is not None and peak is not None else None,
                "metro_vs_ma5": (lv - sum(ma5) / len(ma5)) if lv is not None and len(ma5) >= 10 else None,
                "metro_jeonse_ratio": median(jr) if len(jr) >= 50 else None,
                "metro_vol_ratio": (v_recent / v_prior) if v_prior > 0 else None,
                "metro_mom1": self.metro_mom(t, 12), "metro_mom3": self.metro_mom(t, 36),
                "bok_rate": BOK.get(year),
                "bok_rate_chg1": (BOK[year] - BOK[year - 1]) if year in BOK and (year - 1) in BOK else None,
            }
        return self._cache[key]

    def emd_lead_months(self, emd_key: str, t: int, window: int = 60, max_lag: int = 12) -> float | None:
        """진입 전 window 개월 동안 법정동 12개월 변화율과 수도권 변화율의 교차상관이 최대인 시차.
        양수 = 법정동이 수도권보다 먼저 움직임(얼리어답터). 자료가 얇으면 None."""
        key = ("lead", emd_key, t)
        if key not in self._cache:
            e = [self.emd_mom(emd_key, i, 12) for i in range(t - window, t + 1)]
            m = [self.metro_mom(i, 12) for i in range(t - window, t + 1)]
            best, best_c = None, None
            for lag in range(-max_lag, max_lag + 1):
                pairs = []
                for i in range(len(e)):
                    j = i + lag           # 법정동 i 시점 vs 수도권 i+lag 시점: lag>0 이면 법정동이 앞선다
                    if 0 <= j < len(m) and e[i] is not None and m[j] is not None:
                        pairs.append((e[i], m[j]))
                if len(pairs) < 24:
                    continue
                n = len(pairs)
                mx = sum(a for a, _ in pairs) / n; my = sum(b for _, b in pairs) / n
                sxx = sum((a - mx) ** 2 for a, _ in pairs); syy = sum((b - my) ** 2 for _, b in pairs)
                if sxx <= 0 or syy <= 0:
                    continue
                c = sum((a - mx) * (b - my) for a, b in pairs) / math.sqrt(sxx * syy)
                if best_c is None or c > best_c:
                    best, best_c = lag, c
            self._cache[key] = float(best) if best is not None and best_c is not None and best_c > 0.3 else None
        return self._cache[key]

    def metro_vol_ratio12(self, t: int) -> float | None:
        key = ("mvol12", t)
        if key not in self._cache:
            rec = sum(sum(s.n[max(0, t - 11):t + 1]) for s in self.prices.values())
            pri = (sum(sum(s.n[max(0, t - 47):t - 11]) for s in self.prices.values()) / 3.0) if t >= 47 else 0.0
            self._cache[key] = (rec / pri) if pri > 0 else None
        return self._cache[key]

    def gu_supply_ratio(self, lawd: str, y0: int, y1: int) -> float | None:
        key = ("gusup", lawd, y0, y1)
        if key not in self._cache:
            ids = {cid for cid, _ in self.by_gu.get(lawd, [])}
            stock = sum((self.cx[cid].households or 0) for cid in ids if self.cx[cid].approval_year and self.cx[cid].approval_year <= y0)
            new = sum((self.cx[cid].households or 0) for cid in ids if self.cx[cid].approval_year and y0 < self.cx[cid].approval_year <= y1)
            self._cache[key] = (new / stock) if stock > 0 else None
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
                v_recent = sum(sum(s.n[max(0, t - 2):t + 1]) for s in series)
                v_prior = (sum(sum(s.n[max(0, t - 14):t - 2]) for s in series) / 4.0) if t >= 14 else 0.0
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

    def gu_pct_price(self, lawd: str, band: str, t: int, q: float) -> float | None:
        key = ("gupct", lawd, band, t, q)
        if key not in self._cache:
            vals = [self.prices[k].p50[t] for k in self.by_gu.get(lawd, []) if k[1] == band and self.prices[k].p50[t]]
            self._cache[key] = store.percentile(vals, q) if len(vals) >= 5 else None
        return self._cache[key]

    def gu_vol_ratio(self, lawd: str, t: int) -> float | None:
        key = ("guvol", lawd, t)
        if key not in self._cache:
            ss = [self.prices[k] for k in self.by_gu.get(lawd, [])]
            rec = sum(sum(s.n[max(0, t - 11):t + 1]) for s in ss)
            pri = (sum(sum(s.n[max(0, t - 47):t - 11]) for s in ss) / 3.0) if t >= 47 else 0.0
            self._cache[key] = (rec / pri) if pri > 0 else None
        return self._cache[key]

    def tier_level(self, tiers: dict, tier: int, t: int, t_ref: int) -> float | None:
        """t_ref 시점 급지 구성(tiers)을 고정하고, 그 급지 법정동들의 t 시점 log ㎡단가 중앙값."""
        key = ("tierlv", t_ref, tier, t)
        if key not in self._cache:
            vals = [self.emd_level(k, t) for k, tr in tiers.items() if tr == tier]
            vals = [v for v in vals if v is not None]
            self._cache[key] = median(vals) if len(vals) >= 3 else None
        return self._cache[key]

    def metro_supply_ratio(self, y0: int, y1: int) -> float | None:
        key = ("msup", y0, y1)
        if key not in self._cache:
            stock = sum((c.households or 0) for c in self.cx.values() if c.approval_year and c.approval_year <= y0)
            new = sum((c.households or 0) for c in self.cx.values() if c.approval_year and y0 < c.approval_year <= y1)
            self._cache[key] = (new / stock) if stock > 0 else None
        return self._cache[key]

    # ── 계급(급지) at Y ──
    def tiers_at(self, t: int) -> tuple[dict[str, int], dict[str, tuple[float, float, int]]]:
        """법정동 → tier, 법정동 → (lat, lon, tier). 24개월 창의 ㎡단가 log 중앙값을 8단계 자연분류."""
        key = ("tiers", t)
        if key not in self._cache:
            levels: dict[str, list[float]] = {}
            for (cid, band), s in self.tier_prices.items():
                vals = [v for v in s.p50[max(0, t - 23):t + 1] if v]
                if len(vals) >= 6:
                    levels.setdefault(self.tier_cx[cid].emd_key, []).append(math.log(median(vals) / store.BAND_M2[band]))
            lv = {k: median(v) for k, v in levels.items() if len(v) >= 2}
            breaks = sorted(zones_mod.jenks_breaks(list(lv.values()), zones_mod.N_TIERS)) if len(lv) > 8 else []
            tiers = {k: zones_mod.N_TIERS - sum(1 for b in breaks if v >= b) for k, v in lv.items()}
            cent: dict[str, list] = {}
            for c in self.tier_cx.values():
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
        if t < 12 or t >= N_MONTHS:      # 12개월 이력만 요구 — 5년 모멘텀 등은 없으면 None(폴백 모델이 처리). 2007~2010 하락기 진입 포함
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
        # 전세는 거래가 드문 달이 많다 → 진입 시점 이전 6개월 안의 마지막 값을 쓴다(미래 정보 아님). 결측 절반이 이걸로 채워진다.
        def _last(arr, at, back=6):
            if not arr:
                return None
            for i in range(at, max(-1, at - back - 1), -1):
                if i >= 0 and arr[i]:
                    return arr[i]
            return None
        j0 = _last(jr, t)
        j1 = _last(jr, t - 12) if t >= 12 else None
        st_km, planned = self.station_feats(c, entry_ym)
        year = int(entry_ym[:4])
        x = {
            "metro_mom5": self.metro_mom(t, 60), "gu_mom5": self.gu_mom(c.lawd_cd, t, 60),
            "gu_mom1": self.gu_mom(c.lawd_cd, t, 12), "regime": self.gu_regime(c.lawd_cd, t),
            "mom1": math.log(p0 / smooth_price(s, t - 12)) if smooth_price(s, t - 12) else None,
            "mom3": math.log(p0 / smooth_price(s, t - 36)) if smooth_price(s, t - 36) else None,
            "own_pct": own_pct,
            "rel_gu": math.log(p0 / gumed) if gumed else None,
            "rel_gu_mom3": (math.log(p0 / gumed) - math.log(smooth_price(s, t - 36) / self.gu_median_price(c.lawd_cd, band, t - 36)))
                           if gumed and t >= 36 and smooth_price(s, t - 36) and self.gu_median_price(c.lawd_cd, band, t - 36) else None,
            "log_vol": math.log1p(sum(s.n[max(0, t - 11):t + 1])),
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
        if self.jobs is not None:
            jf = self.jobs.features(c, entry_ym)
            x.update({k: jf.get(k) for k in ("jobs_emd", "jobs_3km", "jobs_growth5")})
        # ── v0.2 이론 변수 ──
        age = x["age"]
        reg = x["regime"]
        emd3 = self.emd_mom(c.emd_key, t, 36)
        m3 = self.metro_mom(t, 36)
        emd_lv = self.emd_level(c.emd_key, t)
        x.update({
            "age_new": (1.0 if age < 5 else 0.0) if age is not None else None,
            "age_mid": (1.0 if 5 <= age < 15 else 0.0) if age is not None else None,
            "age_old": (1.0 if age >= 25 else 0.0) if age is not None else None,
            "tier_x_regime": (float(tier) * reg) if reg is not None else None,
            "own_pct_sq": (own_pct ** 2) if own_pct is not None else None,
            "rel_gu_x_tier": (x["rel_gu"] * float(tier)) if x["rel_gu"] is not None else None,
            "emd_rel_mom3": (emd3 - m3) if emd3 is not None and m3 is not None else None,
            "jeonse_gap_closing": (x["jeonse_mom1"] - x["mom1"]) if x["jeonse_mom1"] is not None and x["mom1"] is not None else None,
            "gu_supply_ratio": self.gu_supply_ratio(c.lawd_cd, year, year + 2),
            "rel_emd": (math.log(p0 / store.BAND_M2[band]) - emd_lv) if emd_lv is not None else None,
        })
        # ── v0.5 정비사업 단계(진입 시점 이하 최고 단계, §14 사다리) ──
        rs = redev_mod.stage_at(self.redev, cid, entry_ym)
        x["redev_stage"] = float(rs)
        x["redev_active"] = 1.0 if rs >= 3 else 0.0
        # ── v0.3 확산(얼리어답터/후행) 변수 ──
        lead = self.emd_lead_months(c.emd_key, t)
        mm1 = x["metro_mom1"] if "metro_mom1" in x else self.metro_mom(t, 12)
        own_vol = sum(s.n[max(0, t - 11):t + 1]); own_pri = (sum(s.n[max(0, t - 47):t - 11]) / 3.0) if t >= 47 else 0.0
        mv = self.metro_vol_ratio12(t)
        x.update({
            "emd_lead_months": lead,
            "lead_x_cycle": (lead * mm1) if lead is not None and mm1 is not None else None,
            "lag_catchup_gap": ((m3 - emd3) if (m3 is not None and emd3 is not None) else None) if (lead is not None and lead < 0) else (0.0 if lead is not None else None),
            "vol_lead": ((own_vol / own_pri) / mv) if own_pri > 0 and mv else None,
        })
        # ── 전문가 이론 변수 (EXPERT_GROUPS) ──
        j3 = _last(jr, t - 36) if t >= 36 else None
        peak = max(hist) if len(hist) >= 24 else None
        own_pri36 = (sum(s.n[max(0, t - 47):t - 11]) / 3.0) if t >= 47 else 0.0
        far = c.far if (c.far and c.far > 30) else None
        up_gap = up_dev = None
        if tier is not None and tier > 1 and emd_lv is not None:
            up_now = self.tier_level(tiers, tier - 1, t, t)
            if up_now is not None:
                up_gap = up_now - emd_lv
                hist_gap = []
                for k in range(6, 61, 6):
                    if t - k < 0:
                        break
                    a = self.tier_level(tiers, tier - 1, t - k, t); b = self.emd_level(c.emd_key, t - k)
                    if a is not None and b is not None:
                        hist_gap.append(a - b)
                if len(hist_gap) >= 5:
                    up_dev = up_gap - sum(hist_gap) / len(hist_gap)
        ld_gap = ld_dev = None
        top = self.gu_pct_price(c.lawd_cd, band, t, 0.9)
        if top:
            ld_gap = math.log(top / p0)
            hg = []
            for k in range(6, 61, 6):
                if t - k < 0:
                    break
                tp = self.gu_pct_price(c.lawd_cd, band, t - k, 0.9); pk = smooth_price(s, t - k)
                if tp and pk:
                    hg.append(math.log(tp / pk))
            if len(hg) >= 5:
                ld_dev = ld_gap - sum(hg) / len(hg)
        b84 = 1.0 if band == "84" else 0.0
        x.update({
            "gap_abs": ((p0 - j0) / 1e8) if j0 else None,
            "jeonse_mom3": math.log(j0 / j3) if j0 and j3 else None,
            "jeonse_ratio_x_regime": (x["jeonse_ratio"] * reg) if x["jeonse_ratio"] is not None and reg is not None else None,
            "own_dd_peak": math.log(p0 / peak) if peak else None,
            "price_level": math.log(p0 / 1e8),
            "price_x_regime": (math.log(p0 / 1e8) * reg) if reg is not None else None,
            "own_vol_ratio": (own_vol / own_pri36) if own_pri36 > 0 else None,
            "gu_vol_ratio": self.gu_vol_ratio(c.lawd_cd, t),
            "upper_tier_gap": up_gap if tier > 1 else 0.0,
            "upper_tier_gap_dev": up_dev if tier > 1 else 0.0,
            "leader_gap": ld_gap, "leader_gap_dev": ld_dev,
            "brand": 1.0 if c.builder and any(b in c.builder for b in BRAND_BUILDERS) else 0.0,
            "far": far,
            "land_per_hh": (c.land_area / c.households) if c.land_area and c.households else None,
            "far_low_old": (1.0 if (far is not None and far < 200 and age is not None and age >= 25) else 0.0) if far is not None and age is not None else None,
            "hh_x_age_old": (x["log_hh"] * x["age_old"]) if x["log_hh"] is not None and x["age_old"] is not None else None,
            "band_59": 1.0 if band == "59" else 0.0, "band_84": b84,
            "band84_x_regime": (b84 * reg) if reg is not None else None,
            "acad_x_84": (x["log_academy"] * b84) if x["log_academy"] is not None else None,
            "age_x_tier": (age * float(tier)) if age is not None else None,
            "dist_center_x_regime": (x["dist_center_km"] * reg) if x["dist_center_km"] is not None and reg is not None else None,
            "metro_supply_ratio": self.metro_supply_ratio(year, year + 2),
        })
        # ── 정책·공급 변수 (POLICY_GROUPS) ──
        ymd = entry_ym + "15"
        rs_ = reg_status(c.lawd_cd, c.emd, ymd)
        sido_nm = {"11": "서울", "41": "경기", "28": "인천"}.get(c.lawd_cd[:2])
        # 풍선효과: 자기 시군구는 비규제인데 같은 시도의 규제 단지 비중이 30% 이상
        key_b = ("regshare", sido_nm, ymd)
        if key_b not in self._cache:
            ids = [o for o in self.cx.values() if o.lawd_cd[:2] == c.lawd_cd[:2]]
            self._cache[key_b] = (sum(1 for o in ids if reg_status(o.lawd_cd, o.emd, ymd)["adj"]) / len(ids)) if ids else 0.0
        share = self._cache[key_b]
        adj_from = rs_["adj_from"]
        reg_months = ((int(ymd[:4]) - int(adj_from[:4])) * 12 + int(ymd[4:6]) - int(adj_from[4:6])) if adj_from else 0.0
        def _uns(code, ym_):
            v = UNSOLD.get((code, ym_))
            return v
        def _ym_shift(ym_, k):
            yy, mm = int(ym_[:4]), int(ym_[4:6]); mm -= k
            while mm <= 0: yy -= 1; mm += 12
            return f"{yy}{mm:02d}"
        gu_stock = self._cache.setdefault(("gustock", c.lawd_cd), sum((o.households or 0) for o in self.tier_cx.values() if o.lawd_cd == c.lawd_cd))
        u0 = _uns(c.lawd_cd, entry_ym); u12 = _uns(c.lawd_cd, _ym_shift(entry_ym, 12))
        m0 = UNSOLD_SIDO.get((sido_nm, entry_ym)); m12 = UNSOLD_SIDO.get((sido_nm, _ym_shift(entry_ym, 12)))
        # 수도권 합
        mk0 = [UNSOLD_SIDO.get((s_, entry_ym)) for s_ in ("서울", "경기", "인천")]; mk12 = [UNSOLD_SIDO.get((s_, _ym_shift(entry_ym, 12))) for s_ in ("서울", "경기", "인천")]
        b0 = [BUNYANG.get((sido_nm, _ym_shift(entry_ym, k))) for k in range(0, 3)]; b0 = [v for v in b0 if v]
        b12 = [BUNYANG.get((sido_nm, _ym_shift(entry_ym, k))) for k in range(12, 15)]; b12 = [v for v in b12 if v]
        # 시도 기존 ㎡단가(원) 중앙값 at t
        key_s = ("sidolv", c.lawd_cd[:2], t)
        if key_s not in self._cache:
            vals = [s_.p50[t] / store.BAND_M2[k_[1]] for k_, s_ in self.prices.items() if s_.p50[t] and self.cx[k_[0]].lawd_cd[:2] == c.lawd_cd[:2]]
            self._cache[key_s] = median(vals) if len(vals) >= 20 else None
        sido_lv = self._cache[key_s]
        x.update({
            "reg_adj": rs_["adj"], "reg_hot": rs_["hot"], "cap_zone": rs_["cap"],
            "reg_months": float(reg_months),
            "reg_balloon": 1.0 if (rs_["adj"] == 0.0 and share >= 0.3) else 0.0,
            "gu_unsold_per_1k": (u0 / gu_stock * 1000.0) if (u0 is not None and gu_stock > 0) else None,
            "gu_unsold_chg12": math.log((u0 + 10) / (u12 + 10)) if (u0 is not None and u12 is not None) else None,
            "metro_unsold_chg12": math.log((sum(mk0) + 10) / (sum(mk12) + 10)) if all(v is not None for v in mk0 + mk12) else None,
            "sido_bunyang_ratio": (sum(b0) / len(b0) * 1000.0 / sido_lv) if (b0 and sido_lv) else None,
            "sido_bunyang_mom12": math.log((sum(b0) / len(b0)) / (sum(b12) / len(b12))) if (b0 and b12) else None,
        })
        x.update(self.cycle_feats(t, year))
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
