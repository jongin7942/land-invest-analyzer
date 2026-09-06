"""panel.py 에 '종인 이론' 변수(JONGIN_GROUPS) 추가 — 동아1단지 사고 과정의 일반화 (2026-09-06).

  ① 1급 노선 착공~개통 선점            → planned_t1_c, express_t1_km (이미 있음)
  ② 상급지 순유출 → 접근 쉽고 싼 곳 수혜  → gu_interin_12m_per_1k(시도간 전입/재고), cheap_x_access(시도 대비 저가 × 1급 역 접근)
  ④ 재건축 연한 + 저용적률 + 대단지 + 역세권 → redev_ready, redev_ready_station
  ⑥ 학군                                 → log_academy (이미 있음)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "apt_engine" / "exitprice" / "panel.py"
s = p.read_text(encoding="utf-8")
assert "JONGIN_GROUPS" not in s

old = "TRANSIT = [f for fs in TRANSIT_GROUPS.values() for f in fs]"
new = '''TRANSIT = [f for fs in TRANSIT_GROUPS.values() for f in fs]
JONGIN_GROUPS = {
    "선점(1급착공·급행)": ["planned_t1_c", "express_t1_km"],
    "밀려나는수요": ["gu_interin_12m_per_1k", "cheap_x_access", "rel_sido_price"],
    "재건축레이더": ["redev_ready", "redev_ready_station"],
}
JONGIN = [f for fs in JONGIN_GROUPS.values() for f in fs]'''
assert old in s; s = s.replace(old, new, 1)
old = '    "T_transit": FEATURES + JOB_FEATURES + THEORY2 + TRANSIT,\n}'
new = '    "T_transit": FEATURES + JOB_FEATURES + THEORY2 + TRANSIT,\n    "J_jongin": FEATURES + JOB_FEATURES + THEORY2 + JONGIN,\n}'
assert old in s; s = s.replace(old, new, 1)
# MIG 로더: 시도간전입 추가
old = '''            if _r["ITM_NM"] not in ("총전입", "총전출", "순이동") or not _r["DT"]:
                continue
            d = MIG.setdefault((_r["C1"], _r["PRD_DE"]), {})
            d[{"총전입": "in", "총전출": "out", "순이동": "net"}[_r["ITM_NM"]]] = float(_r["DT"])'''
new = '''            if _r["ITM_NM"] not in ("총전입", "총전출", "순이동", "시도간전입") or not _r["DT"]:
                continue
            d = MIG.setdefault((_r["C1"], _r["PRD_DE"]), {})
            d[{"총전입": "in", "총전출": "out", "순이동": "net", "시도간전입": "inter_in"}[_r["ITM_NM"]]] = float(_r["DT"])'''
assert old in s; s = s.replace(old, new, 1)
# row(): 종인 변수 (transit_feats 뒤, cycle 전)
old = "        x.update(self.transit_feats(c, entry_ym))\n        x.update(self.cycle_feats(t, year))\n"
new = '''        x.update(self.transit_feats(c, entry_ym))
        # ── 종인 이론 변수 ──
        inter12 = _mig_sum(mcode, entry_ym, 12, "inter_in")
        sido_lv2 = self._cache.get(("sidolv", c.lawd_cd[:2], t))
        relsd = (math.log(p0 / store.BAND_M2[band]) - math.log(sido_lv2)) if sido_lv2 else None
        rr = 1.0 if (age is not None and age >= 30 and far is not None and far < 200 and (c.households or 0) >= 1000) else (0.0 if (age is not None and far is not None) else None)
        x.update({
            "gu_interin_12m_per_1k": (inter12 / stock * 1000.0) if (inter12 is not None and stock > 0) else None,
            "rel_sido_price": relsd,
            "cheap_x_access": ((-relsd) / (1.0 + x["stn_t1_km"])) if (relsd is not None and x.get("stn_t1_km") is not None) else None,
            "redev_ready": rr,
            "redev_ready_station": (rr * (1.0 if x.get("stn_t1_km", 9) <= 0.5 else 0.0)) if rr is not None else None,
        })
        x.update(self.cycle_feats(t, year))
'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("patched jongin")
