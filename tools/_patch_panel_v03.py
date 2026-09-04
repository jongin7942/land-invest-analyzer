"""일회성 패치 v0.3: 확산(얼리어답터 → 빠른 추종 → 대중 → 후행) 변수 추가.

종인님 이론: 먼저 오르는 곳과 나중에 오르는 곳, 먼저 사는 수요와 나중에 사는 수요가 정해져 있다.
변수로 옮기면
  emd_lead_months   법정동 12개월 변화율이 수도권 변화율보다 몇 달 앞서/뒤서 움직였나(진입 전 60개월 교차상관, −12~+12)
                    양수 = 얼리어답터(선행), 음수 = 후행. 미래 정보 없이 진입 시점까지의 자료만 사용.
  lead_x_cycle      선행성 × 수도권 1년 모멘텀 — 사이클 초기(수도권이 막 오르기 시작)에는 선행지가, 중후반에는 후행지가 유리하다는 가설
  lag_catchup_gap   (수도권 3년 모멘텀 − 법정동 3년 모멘텀) × 후행 여부 — 후행지인데 아직 못 따라간 폭
  vol_lead          거래량 확산: 자기 12개월 거래량 / 이전 36개월 평균 ÷ 수도권 같은 비율 — 수요가 먼저 붙었는지
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "apt_engine/exitprice/panel.py"
s = p.read_text(encoding="utf-8")

s = s.replace('''CYCLE = ["metro_dd_peak",''', '''DIFFUSION = ["emd_lead_months", "lead_x_cycle", "lag_catchup_gap", "vol_lead"]
CYCLE = ["metro_dd_peak",''')
s = s.replace('''    "F_+cycle": FEATURES + JOB_FEATURES + THEORY2 + CYCLE,
}''', '''    "F_+cycle": FEATURES + JOB_FEATURES + THEORY2 + CYCLE,
    "G_+diffusion": FEATURES + JOB_FEATURES + THEORY2 + DIFFUSION,
    "H_all": FEATURES + JOB_FEATURES + THEORY2 + DIFFUSION + CYCLE,
}''')

s = s.replace('''    def gu_supply_ratio(self, lawd: str, y0: int, y1: int) -> float | None:''', '''    def emd_lead_months(self, emd_key: str, t: int, window: int = 60, max_lag: int = 12) -> float | None:
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
            rec = sum(sum(s.n[t - 11:t + 1]) for s in self.prices.values())
            pri = sum(sum(s.n[t - 47:t - 11]) for s in self.prices.values()) / 3.0
            self._cache[key] = (rec / pri) if pri > 0 else None
        return self._cache[key]

    def gu_supply_ratio(self, lawd: str, y0: int, y1: int) -> float | None:''')

s = s.replace('''        x.update(self.cycle_feats(t, year))''', '''        # ── v0.3 확산(얼리어답터/후행) 변수 ──
        lead = self.emd_lead_months(c.emd_key, t)
        mm1 = x["metro_mom1"] if "metro_mom1" in x else self.metro_mom(t, 12)
        own_vol = sum(s.n[t - 11:t + 1]); own_pri = sum(s.n[t - 47:t - 11]) / 3.0
        mv = self.metro_vol_ratio12(t)
        x.update({
            "emd_lead_months": lead,
            "lead_x_cycle": (lead * mm1) if lead is not None and mm1 is not None else None,
            "lag_catchup_gap": ((m3 - emd3) if (m3 is not None and emd3 is not None) else None) if (lead is not None and lead < 0) else (0.0 if lead is not None else None),
            "vol_lead": ((own_vol / own_pri) / mv) if own_pri > 0 and mv else None,
        })
        x.update(self.cycle_feats(t, year))''')
p.write_text(s, encoding="utf-8")
print("panel v0.3 diffusion patched")
