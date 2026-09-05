"""전문가 이론 변수 추가 패치 (store.Complex 필드, panel.py EXPERT 변수)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── store.py: Complex 에 시공사·용적률·대지면적 ──
p = ROOT / "apt_engine" / "relative" / "store.py"
s = p.read_text(encoding="utf-8")
old = "    station_opened_recent: bool = False   # 최근 5년 내 1.5km 안 개통 (§15 평균회귀 함정 플래그)\n"
new = old + ("    builder: str | None = None            # K-apt 시공사(브랜드 이론)\n"
             "    far: float | None = None              # 현 용적률(재건축 사업성 이론)\n"
             "    land_area: float | None = None        # 대지면적 ㎡(대지지분 이론)\n")
assert old in s and "builder: str | None" not in s
s = s.replace(old, new)
old_q = ('"SELECT id, name, lawd_cd, emd_name, lat, lon, apt_households, approval_year "\n'
         '            "  FROM complex WHERE canonical_id IS NULL')
new_q = ('"SELECT id, name, lawd_cd, emd_name, lat, lon, apt_households, approval_year, builder, current_far, land_area_m2 "\n'
         '            "  FROM complex WHERE canonical_id IS NULL')
assert old_q in s
s = s.replace(old_q, new_q)
old_c = ("                                    int(r[\"approval_year\"]) if r[\"approval_year\"] else None)\n    return out\n")
new_c = ("                                    int(r[\"approval_year\"]) if r[\"approval_year\"] else None,\n"
         "                                    builder=(r[\"builder\"] or None), far=(float(r[\"current_far\"]) if r[\"current_far\"] else None),\n"
         "                                    land_area=(float(r[\"land_area_m2\"]) if r[\"land_area_m2\"] else None))\n    return out\n")
assert old_c in s
s = s.replace(old_c, new_c)
p.write_text(s, encoding="utf-8")

# ── panel.py ──
p = ROOT / "apt_engine" / "exitprice" / "panel.py"
s = p.read_text(encoding="utf-8")
old = 'JOB_GROWTH = ["jobs_growth5"]'
new = '''EXPERT_GROUPS = {
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
BRAND_BUILDERS = ("삼성물산", "현대건설", "GS건설", "대우건설", "대림산업", "DL이앤씨", "포스코", "롯데건설", "현대산업개발", "HDC", "SK에코", "SK건설", "한화건설", "현대엔지니어링")
JOB_GROWTH = ["jobs_growth5"]'''
assert old in s and "EXPERT_GROUPS" not in s
s = s.replace(old, new, 1)
old = '    "H_all": FEATURES + JOB_FEATURES + THEORY2 + DIFFUSION + CYCLE,\n}'
new = ('    "H_all": FEATURES + JOB_FEATURES + THEORY2 + DIFFUSION + CYCLE,\n'
       '    "X_expert": FEATURES + JOB_FEATURES + THEORY2 + EXPERT,\n}')
assert old in s
s = s.replace(old, new, 1)

# helpers: gu percentile price, tier level, metro supply
old = "    # ── 계급(급지) at Y ──\n"
new = '''    def gu_pct_price(self, lawd: str, band: str, t: int, q: float) -> float | None:
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
'''
assert old in s
s = s.replace(old, new, 1)

# row(): expert features before cycle_feats
old = "        x.update(self.cycle_feats(t, year))\n        t1 = t + HORIZON\n"
new = '''        # ── 전문가 이론 변수 (EXPERT_GROUPS) ──
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
        x.update(self.cycle_feats(t, year))
        t1 = t + HORIZON
'''
assert old in s
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("patched")
