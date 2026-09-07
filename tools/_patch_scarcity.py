"""panel.py 에 '이중 희소성' 변수(SCARCITY_GROUPS) 추가 — 종인님 가설 2026-09-07.

가설: 좋은 입지의 40년 가까운 노후 단지가 재건축되면 **입지 희소성 + 신축 희소성**이 겹쳐 상승폭이 매우 커진다.
기존 E 에 없던 새 정보는 "그 지역에서 신축이 실제로 얼마나 비싼가(신축 프리미엄)"와 "신축이 얼마나 드문가(신축 비중)".
모두 진입 시점 t 의 횡단면으로만 계산(미래 정보 없음).
  new_prem_gu     시군구 신축(≤7년) ㎡단가 ÷ 구축(≥20년) ㎡단가, log. 표본 부족 시 시도, 그래도 부족하면 None
  new_share10     최근 10년 준공 세대 ÷ 시군구 총세대 (낮을수록 신축 희소)
  newtown         법정동 계획개발 대리: 준공연도 중앙 ≥1995 이고 사분위폭 ≤8년 → 1
  loc_old         입지 상위(급지 ≤3) × 노후(35년↑)
  scarcity_combo  loc_old × (new_prem_gu 가 그 시점 중앙 이상)   ← 핵심 3중 결합
  scarcity_far    scarcity_combo × (용적률 <200)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "apt_engine" / "exitprice" / "panel.py"
s = p.read_text(encoding="utf-8")
assert "SCARCITY_GROUPS" not in s

old = "JONGIN = [f for fs in JONGIN_GROUPS.values() for f in fs]"
new = '''JONGIN = [f for fs in JONGIN_GROUPS.values() for f in fs]
SCARCITY_GROUPS = {
    "신축희소성": ["new_prem_gu", "new_share10"],
    "입지×노후": ["loc_old", "newtown"],
    "이중희소성 결합": ["scarcity_combo", "scarcity_far"],
}
SCARCITY = [f for fs in SCARCITY_GROUPS.values() for f in fs]'''
assert old in s; s = s.replace(old, new, 1)
old = '    "J_jongin": FEATURES + JOB_FEATURES + THEORY2 + JONGIN,\n}'
new = '    "J_jongin": FEATURES + JOB_FEATURES + THEORY2 + JONGIN,\n    "S_scarcity": FEATURES + JOB_FEATURES + THEORY2 + SCARCITY,\n}'
assert old in s; s = s.replace(old, new, 1)

# 헬퍼: 시군구/시도 신축 프리미엄, 신축 비중, 법정동 계획개발 여부
old = "    # ── 계급(급지) at Y ──\n"
new = '''    def new_premium(self, key: str, t: int, year: int, level: str) -> float | None:
        """신축(≤7년) ÷ 구축(≥20년) ㎡단가 중앙값의 log. level='gu'(시군구) 또는 'sido'."""
        ck = ("newprem", level, key, t)
        if ck not in self._cache:
            new_, old_ = [], []
            for (cid, band), s_ in self.tier_prices.items():
                c2 = self.tier_cx[cid]
                k2 = c2.lawd_cd if level == "gu" else c2.lawd_cd[:2]
                if k2 != key or not c2.approval_year or not s_.p50[t]:
                    continue
                a = year - c2.approval_year
                v = math.log(s_.p50[t] / store.BAND_M2[band])
                if a <= 7:
                    new_.append(v)
                elif a >= 20:
                    old_.append(v)
            self._cache[ck] = (median(new_) - median(old_)) if (len(new_) >= 3 and len(old_) >= 5) else None
        return self._cache[ck]

    def new_share10(self, lawd: str, year: int) -> float | None:
        ck = ("newshare", lawd, year)
        if ck not in self._cache:
            tot = rec = 0
            for c2 in self.tier_cx.values():
                if c2.lawd_cd != lawd or not c2.households:
                    continue
                tot += c2.households
                if c2.approval_year and year - c2.approval_year <= 10:
                    rec += c2.households
            self._cache[ck] = (rec / tot) if tot > 0 else None
        return self._cache[ck]

    def newtown_flag(self, emd_key: str) -> float | None:
        """법정동 계획개발 대리 — 준공연도 중앙 ≥1995 이고 사분위폭 ≤8년(동시 대량 개발)."""
        ck = ("newtown", emd_key)
        if ck not in self._cache:
            ys = sorted(c2.approval_year for c2 in self.tier_cx.values() if c2.emd_key == emd_key and c2.approval_year)
            if len(ys) < 4:
                self._cache[ck] = None
            else:
                q1, q3, med = ys[len(ys) // 4], ys[len(ys) * 3 // 4], ys[len(ys) // 2]
                self._cache[ck] = 1.0 if (med >= 1995 and (q3 - q1) <= 8) else 0.0
        return self._cache[ck]

    # ── 계급(급지) at Y ──
'''
assert old in s; s = s.replace(old, new, 1)

# row(): 희소성 변수 (종인 변수 뒤)
old = "        x.update(self.cycle_feats(t, year))\n        t1 = t + HORIZON\n"
new = '''        # ── 이중 희소성 변수 (SCARCITY_GROUPS) ──
        npg = self.new_premium(c.lawd_cd, t, year, "gu")
        if npg is None:
            npg = self.new_premium(c.lawd_cd[:2], t, year, "sido")
        ck_med = ("npmed", t)
        if ck_med not in self._cache:
            vals = []
            for lw in {c2.lawd_cd for c2 in self.tier_cx.values()}:
                v = self.new_premium(lw, t, year, "gu")
                if v is not None:
                    vals.append(v)
            self._cache[ck_med] = median(vals) if len(vals) >= 5 else None
        np_med = self._cache[ck_med]
        loc_old = (1.0 if (tier is not None and tier <= 3 and age is not None and age >= 35) else 0.0) if age is not None else None
        combo = (loc_old * (1.0 if (npg is not None and np_med is not None and npg >= np_med) else 0.0)) if loc_old is not None else None
        x.update({
            "new_prem_gu": npg,
            "new_share10": self.new_share10(c.lawd_cd, year),
            "loc_old": loc_old,
            "newtown": self.newtown_flag(c.emd_key),
            "scarcity_combo": combo,
            "scarcity_far": (combo * (1.0 if (far is not None and far < 200) else 0.0)) if combo is not None else None,
        })
        x.update(self.cycle_feats(t, year))
        t1 = t + HORIZON
'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("patched scarcity")
