"""일회성 패치 v0.4:
  1) 급지·중심거리 지도는 전체 단지로 만들고(참조 지리), 행(학습·예측)은 1,000세대 이상만 — 대단지만으로 급지를 만들면
     법정동 대부분이 단지 1개라 tier 가 비어 행이 사라지는 문제(pool 45/100 예측 없음) 해결.
  2) 새 변수: rel_gu_mom3(시군구 내 상대 위치의 3년 변화 속도), lead 계급 출력.
  3) 변수군 E2 = E + rel_gu_mom3 + vol_lead + emd_lead_months (확산 중 살아남을 후보만).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "apt_engine/exitprice/panel.py"
s = p.read_text(encoding="utf-8")

# 1) tier 지도용 별도 가격 사전
s = s.replace('''                 jobs=None):
        """stations: (lat, lon, opened_ym or None, status_date 'YYYY-MM-DD' or None); jobs: exitprice.jobs.Jobs"""
        self.cx, self.prices, self.jeonse, self.stations = complexes, prices, jeonse, stations
        self.jobs = jobs''', '''                 jobs=None, tier_complexes=None, tier_prices=None):
        """stations: (lat, lon, opened_ym or None, status_date 'YYYY-MM-DD' or None); jobs: exitprice.jobs.Jobs
        tier_complexes/tier_prices: 급지·중심거리 지도를 만들 때 쓸 전체 단지(없으면 complexes/prices). 행은 complexes 만."""
        self.cx, self.prices, self.jeonse, self.stations = complexes, prices, jeonse, stations
        self.jobs = jobs
        self.tier_cx = tier_complexes or complexes
        self.tier_prices = tier_prices or prices''')
s = s.replace('''            levels: dict[str, list[float]] = {}
            for (cid, band), s in self.prices.items():
                vals = [v for v in s.p50[max(0, t - 23):t + 1] if v]
                if len(vals) >= 6:
                    levels.setdefault(self.cx[cid].emd_key, []).append(math.log(median(vals) / store.BAND_M2[band]))
            lv = {k: median(v) for k, v in levels.items() if len(v) >= 2}''', '''            levels: dict[str, list[float]] = {}
            for (cid, band), s in self.tier_prices.items():
                vals = [v for v in s.p50[max(0, t - 23):t + 1] if v]
                if len(vals) >= 6:
                    levels.setdefault(self.tier_cx[cid].emd_key, []).append(math.log(median(vals) / store.BAND_M2[band]))
            lv = {k: median(v) for k, v in levels.items() if len(v) >= 2}''')
s = s.replace('''            cent: dict[str, list] = {}
            for c in self.cx.values():
                if c.emd_key in tiers:
                    cent.setdefault(c.emd_key, []).append((c.lat, c.lon))''', '''            cent: dict[str, list] = {}
            for c in self.tier_cx.values():
                if c.emd_key in tiers:
                    cent.setdefault(c.emd_key, []).append((c.lat, c.lon))''')

# 2) 새 변수 rel_gu_mom3
s = s.replace('''            "rel_gu": math.log(p0 / gumed) if gumed else None,''', '''            "rel_gu": math.log(p0 / gumed) if gumed else None,
            "rel_gu_mom3": (math.log(p0 / gumed) - math.log(smooth_price(s, t - 36) / self.gu_median_price(c.lawd_cd, band, t - 36)))
                           if gumed and t >= 36 and smooth_price(s, t - 36) and self.gu_median_price(c.lawd_cd, band, t - 36) else None,''')

# 3) 변수군 E2
s = s.replace('''    "G_+diffusion": FEATURES + JOB_FEATURES + THEORY2 + DIFFUSION,''', '''    "E2_+relmom": FEATURES + JOB_FEATURES + THEORY2 + ["rel_gu_mom3", "vol_lead", "emd_lead_months"],
    "G_+diffusion": FEATURES + JOB_FEATURES + THEORY2 + DIFFUSION,''')
p.write_text(s, encoding="utf-8")

# runner: 전체 단지로 tier 지도
q = ROOT / "tools/run_exit_price.py"
r = q.read_text(encoding="utf-8")
r = r.replace('''    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs if jobs.available else None)''',
'''    cx_all = store.load_complexes(conn, min_households=0)
    prices_all = store.load_prices(conn, cx_all, bands)
    print(f"[급지 지도] 전체 단지 {len(cx_all)} · 단지×면적 {len(prices_all)} (행은 1,000세대 이상 {len(cx)} 만)", flush=True)
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs if jobs.available else None,
                                tier_complexes=cx_all, tier_prices=prices_all)''')
q.write_text(r, encoding="utf-8")

# fallback: 같은 처리 + lead 계급 출력 + 전세가율 조건부 시장 시나리오
f = ROOT / "tools/predict_exit_fallback.py"
u = f.read_text(encoding="utf-8")
u = u.replace('''    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs)''',
'''        cx_all = store.load_complexes(conn, min_households=0)
        prices_all = store.load_prices(conn, cx_all, ("84", "59", "74"))
    pb = panel_mod.PanelBuilder(cx, prices, jeonse, stations, jobs=jobs, tier_complexes=cx_all, tier_prices=prices_all)''')
u = u.replace('''    mk = sorted(lvl.values())
    bear_mk, base_mk, bull_mk = min(mk), percentile(mk, 0.5), percentile(mk, 0.8)''',
'''    mk = sorted(lvl.values())
    # 시장 수준 시나리오: 전세가율 조건부(§6) — 지금(2026-06) 전세가율과 ±0.08 안의 과거 진입연도만 쓴다(최소 3개, 없으면 전체)
    mt_path = ROOT / "reports" / "market_timing.json"
    cond_years, jr_now = [], None
    if mt_path.exists():
        mt = json.loads(mt_path.read_text(encoding="utf-8"))
        now_row = next((r for r in mt["rows"] if r["year"] == 2026), None)
        jr_now = now_row.get("metro_jeonse_ratio") if now_row else None
        if jr_now is not None:
            cond_years = [f"{r['year']}06" for r in mt["rows"] if r.get("metro_jeonse_ratio") is not None
                          and abs(r["metro_jeonse_ratio"] - jr_now) <= 0.08 and f"{r['year']}06" in lvl]
    if len(cond_years) >= 3:
        mk_c = sorted(lvl[y] for y in cond_years)
        bear_mk, base_mk, bull_mk = min(mk_c), percentile(mk_c, 0.5), max(mk_c)
        scen_note = f"전세가율 조건부(지금 {jr_now:.2f}, 유사 진입연도 {sorted(cond_years)})"
    else:
        bear_mk, base_mk, bull_mk = min(mk), percentile(mk, 0.5), percentile(mk, 0.8)
        scen_note = "전체 진입연도 분포(조건부 표본 부족)"
    print("[시장 시나리오]", scen_note, f"log {bear_mk:.3f}/{base_mk:.3f}/{bull_mk:.3f}", flush=True)''')
u = u.replace('''                "tier": r.x.get("tier"),''', '''                "tier": r.x.get("tier"),
                "emd_lead_months": r.x.get("emd_lead_months"),
                "market_scenario_note": scen_note,''')
f.write_text(u, encoding="utf-8")
print("v0.4 patch applied")
