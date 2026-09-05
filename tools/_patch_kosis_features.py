"""panel.py 에 KOSIS 변수 추가: 시군구 인구 순이동(12개월 합/재고), 전입 변화, 건설공사비지수 YoY(시장 수준)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "apt_engine" / "exitprice" / "panel.py"
s = p.read_text(encoding="utf-8")
assert "KOSIS_GROUPS" not in s

old = "POLICY = [f for fs in POLICY_GROUPS.values() for f in fs]"
new = '''POLICY = [f for fs in POLICY_GROUPS.values() for f in fs]
KOSIS_GROUPS = {
    # 2026-09-06 KOSIS 수집: 인구이동(101/DT_1B26001_A01) · 건설공사비지수(397/DT_39701_A003)
    "인구이동": ["gu_netmig_12m_per_1k", "gu_inmig_chg12", "sido_netmig_12m_per_1k"],   # 시군구 순이동 12개월 합 / 시군구 세대(1천), 전입 12개월 변화, 시도 순이동
    "공사비": ["cost_idx_yoy", "cost_idx_yoy_chg"],                                     # 주거용건물 공사비지수 전년동월비, 그 변화(시장 수준)
    "소득": ["gu_income_pc_log", "gu_income_growth3", "gu_income_rel_sido"],           # 시군구 1인당 총급여(log, 2년 전 연도), 3년 성장, 시도 대비
}
KOSIS = [f for fs in KOSIS_GROUPS.values() for f in fs]'''
assert old in s; s = s.replace(old, new, 1)
old = '    "P_policy": FEATURES + JOB_FEATURES + THEORY2 + POLICY,\n}'
new = '    "P_policy": FEATURES + JOB_FEATURES + THEORY2 + POLICY,\n    "K_kosis": FEATURES + JOB_FEATURES + THEORY2 + KOSIS,\n}'
assert old in s; s = s.replace(old, new, 1)

# 로더
old = "def load_regulation(conn=None) -> None:"
new = '''MIG: dict[tuple[str, str], dict] = {}        # (kosis 시군구코드 5자리 or 시도 2자리, ym) → {"in":총전입,"out":총전출,"net":순이동}
COST: dict[str, float] = {}                   # ym → 주거용건물 공사비지수
INCOME: dict[tuple[str, int], float] = {}     # (lawd_cd, year) → 1인당 총급여(백만원)
INCOME_SIDO: dict[tuple[str, int], float] = {}
try:
    with (_R / "kosis_migration_sigungu_monthly.csv").open(encoding="utf-8") as _f:
        for _r in _csv2.DictReader(_f):
            if _r["ITM_NM"] not in ("총전입", "총전출", "순이동") or not _r["DT"]:
                continue
            d = MIG.setdefault((_r["C1"], _r["PRD_DE"]), {})
            d[{"총전입": "in", "총전출": "out", "순이동": "net"}[_r["ITM_NM"]]] = float(_r["DT"])
    with (_R / "kosis_construction_cost_index.csv").open(encoding="utf-8") as _f:
        for _r in _csv2.DictReader(_f):
            if _r["C1_NM"] == "주거용건물" and _r["DT"]:
                COST[_r["PRD_DE"]] = float(_r["DT"])
    with (_R / "kosis_income_sigungu_mapped.csv").open(encoding="utf-8") as _f:
        _acc = {}
        for _r in _csv2.DictReader(_f):
            INCOME[(_r["lawd_cd"], int(_r["year"]))] = float(_r["salary_per_person_mw"])
            _a = _acc.setdefault((_r["sido"], int(_r["year"])), [0.0, 0.0]); _a[0] += float(_r["total_salary_mw"]); _a[1] += float(_r["persons"])
        for (_sd, _y), (_t, _n) in _acc.items():
            if _n > 0:
                INCOME_SIDO[({"서울": "11", "인천": "28", "경기": "41"}.get(_sd, _sd), _y)] = _t / _n
except Exception:
    pass


def load_regulation(conn=None) -> None:'''
assert old in s; s = s.replace(old, new, 1)

# row(): KOSIS 변수 (cycle_feats 직전, 정책 변수 뒤)
old = "        x.update(self.cycle_feats(t, year))\n        t1 = t + HORIZON\n"
new = '''        # ── KOSIS 변수 ──
        def _mig_sum(code, ym_, months, key):
            tot = 0.0; n = 0
            for k_ in range(months):
                d = MIG.get((code, _ym_shift(ym_, k_)))
                if d and key in d:
                    tot += d[key]; n += 1
            return tot if n >= months // 2 else None
        mcode = c.lawd_cd if c.lawd_cd[:2] in ("11", "28") else c.lawd_cd[:4] + "0"
        if (mcode, entry_ym) not in MIG and c.lawd_cd[:2] not in ("11", "28"):
            mcode = c.lawd_cd
        net12 = _mig_sum(mcode, entry_ym, 12, "net"); in12 = _mig_sum(mcode, entry_ym, 12, "in"); in12p = _mig_sum(mcode, _ym_shift(entry_ym, 12), 12, "in")
        # 재고: 시 단위 코드면 그 시의 모든 구 세대 합
        key_st = ("migstock", mcode)
        if key_st not in self._cache:
            pref = mcode[:4] if mcode.endswith("0") and mcode[:2] not in ("11", "28") else mcode
            self._cache[key_st] = sum((o.households or 0) for o in self.tier_cx.values() if o.lawd_cd.startswith(pref))
        stock = self._cache[key_st]
        snet = _mig_sum(c.lawd_cd[:2], entry_ym, 12, "net")
        key_ss = ("sidostock", c.lawd_cd[:2])
        if key_ss not in self._cache:
            self._cache[key_ss] = sum((o.households or 0) for o in self.tier_cx.values() if o.lawd_cd[:2] == c.lawd_cd[:2])
        c0 = COST.get(entry_ym); c12 = COST.get(_ym_shift(entry_ym, 12)); c24 = COST.get(_ym_shift(entry_ym, 24))
        yoy = math.log(c0 / c12) if c0 and c12 else None
        yoy_p = math.log(c12 / c24) if c12 and c24 else None
        x.update({
            "gu_netmig_12m_per_1k": (net12 / stock * 1000.0) if (net12 is not None and stock > 0) else None,
            "gu_inmig_chg12": math.log((in12 + 1) / (in12p + 1)) if (in12 is not None and in12p is not None) else None,
            "sido_netmig_12m_per_1k": (snet / self._cache[key_ss] * 1000.0) if (snet is not None and self._cache[key_ss] > 0) else None,
            "cost_idx_yoy": yoy, "cost_idx_yoy_chg": (yoy - yoy_p) if (yoy is not None and yoy_p is not None) else None,
        })
        iy = year - 2                       # 연말정산 통계는 다음해 말 공표 → 진입연도−2 가 진입 시점에 알 수 있는 최신
        inc = INCOME.get((c.lawd_cd, iy)); inc3 = INCOME.get((c.lawd_cd, iy - 3)); inc_sd = INCOME_SIDO.get((c.lawd_cd[:2], iy))
        x.update({
            "gu_income_pc_log": math.log(inc) if inc else None,
            "gu_income_growth3": math.log(inc / inc3) if (inc and inc3) else None,
            "gu_income_rel_sido": math.log(inc / inc_sd) if (inc and inc_sd) else None,
        })
        x.update(self.cycle_feats(t, year))
        t1 = t + HORIZON
'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("patched")
