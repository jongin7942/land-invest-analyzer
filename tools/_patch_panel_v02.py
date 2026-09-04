"""일회성 패치: exitprice/panel.py 에 v0.2 이론·사이클 변수 추가, runner 진입연도 확장."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "apt_engine/exitprice/panel.py"
s = p.read_text(encoding="utf-8")

s = s.replace('''JOB_FEATURES = ["jobs_emd", "jobs_3km"]            # 국민연금 사업장(2016~) — 그 전 진입은 PROXY 스냅샷''',
'''THEORY2 = [
    # 비선형·상호작용 (v0.2): 신축 프리미엄 소멸 구간, 급지×국면, 자기 백분위 제곱
    "age_new", "age_mid", "age_old", "tier_x_regime", "own_pct_sq", "rel_gu_x_tier",
    # 덜 오른 곳: 법정동 3년 상대 모멘텀(법정동 − 수도권), 전세-매매 갭 축소 속도
    "emd_rel_mom3", "jeonse_gap_closing",
    # 시군구 공급: 진입 후 2년 승인 세대 / 시군구 재고 (leakage 위험 — 입주예정 대리)
    "gu_supply_ratio",
    # 급지 내 위치: 법정동 ㎡단가 대비 자기 ㎡단가
    "rel_emd",
]
CYCLE = ["metro_dd_peak", "metro_vs_ma5", "metro_jeonse_ratio", "metro_vol_ratio", "bok_rate", "bok_rate_chg1", "metro_mom1", "metro_mom3"]
JOB_FEATURES = ["jobs_emd", "jobs_3km"]            # 국민연금 사업장(2016~) — 그 전 진입은 PROXY 스냅샷''')

s = s.replace('''    "D_+jobs": FEATURES + JOB_FEATURES,
}''', '''    "D_+jobs": FEATURES + JOB_FEATURES,
    "E_+theory2": FEATURES + JOB_FEATURES + THEORY2,
    "F_+cycle": FEATURES + JOB_FEATURES + THEORY2 + CYCLE,
}
BOK: dict[int, float] = {}
try:
    import csv as _csv
    from pathlib import Path as _P
    with (_P(__file__).resolve().parents[2] / "rules" / "bok_base_rate.csv").open(encoding="utf-8") as _f:
        for _r in _csv.DictReader(_f):
            BOK[int(_r["year"])] = float(_r["rate_june"])
except Exception:
    pass''')

s = s.replace('''    def gu_regime(self, lawd: str, t: int) -> float | None:''', '''    def _ensure_emd(self):
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
            v_recent = sum(sum(s.n[t - 11:t + 1]) for s in self.prices.values())
            v_prior = sum(sum(s.n[t - 47:t - 11]) for s in self.prices.values()) / 3.0
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

    def gu_supply_ratio(self, lawd: str, y0: int, y1: int) -> float | None:
        key = ("gusup", lawd, y0, y1)
        if key not in self._cache:
            ids = {cid for cid, _ in self.by_gu.get(lawd, [])}
            stock = sum((self.cx[cid].households or 0) for cid in ids if self.cx[cid].approval_year and self.cx[cid].approval_year <= y0)
            new = sum((self.cx[cid].households or 0) for cid in ids if self.cx[cid].approval_year and y0 < self.cx[cid].approval_year <= y1)
            self._cache[key] = (new / stock) if stock > 0 else None
        return self._cache[key]

    def gu_regime(self, lawd: str, t: int) -> float | None:''')

s = s.replace('''        if self.jobs is not None:
            jf = self.jobs.features(c, entry_ym)
            x.update({k: jf.get(k) for k in ("jobs_emd", "jobs_3km", "jobs_growth5")})''',
'''        if self.jobs is not None:
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
        x.update(self.cycle_feats(t, year))''')
p.write_text(s, encoding="utf-8")

q = ROOT / "tools/run_exit_price.py"
r = q.read_text(encoding="utf-8")
r = r.replace("ENTRY_YEARS = list(range(2011, 2022))          # 2011~2021 진입(결과 2016~2026)",
              "ENTRY_YEARS = list(range(2007, 2022))          # 2007~2021 진입(결과 2012~2026) — 하락기(2008~2013) 포함")
r = r.replace("TEST_YEARS = list(range(2016, 2022))           # 학습 = 진입 ≤ T−5",
              "TEST_YEARS = list(range(2013, 2022))           # 학습 = 진입 ≤ T−5")
q.write_text(r, encoding="utf-8")
print("panel v0.2 + runner years patched")
