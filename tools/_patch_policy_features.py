"""panel.py 에 정책·공급 이론 변수(POLICY_GROUPS) 추가: 규제지역·분양가상한제·미분양·분양가."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "apt_engine" / "exitprice" / "panel.py"
s = p.read_text(encoding="utf-8")
assert "POLICY_GROUPS" not in s

# 1) 그룹 정의
old = 'EXPERT = [f for fs in EXPERT_GROUPS.values() for f in fs]'
new = '''EXPERT = [f for fs in EXPERT_GROUPS.values() for f in fs]
POLICY_GROUPS = {
    # 2026-09-05 종인님 지시: 규제·공사비·분양가상한제·분양가·청약을 변수로. 자료가 있는 것부터.
    "규제지역": ["reg_adj", "reg_hot", "reg_months", "reg_balloon"],          # 조정대상/투기과열 여부, 지정 후 경과월, 풍선효과(비규제인데 같은 시도 규제 비중 높음)
    "분양가상한제": ["cap_zone"],
    "미분양": ["gu_unsold_per_1k", "gu_unsold_chg12", "metro_unsold_chg12"],  # 시군구 미분양/재고 1천세대, 12개월 변화, 수도권 변화
    "분양가": ["sido_bunyang_ratio", "sido_bunyang_mom12"],                    # 시도 신규 분양가 ÷ 기존 ㎡단가, 분양가 12개월 상승률
}
POLICY = [f for fs in POLICY_GROUPS.values() for f in fs]'''
s = s.replace(old, new, 1)
old = '    "X_expert": FEATURES + JOB_FEATURES + THEORY2 + EXPERT,\n}'
new = '    "X_expert": FEATURES + JOB_FEATURES + THEORY2 + EXPERT,\n    "P_policy": FEATURES + JOB_FEATURES + THEORY2 + POLICY,\n}'
s = s.replace(old, new, 1)

# 2) 자료 로더 (모듈 수준, 실패해도 조용히)
old = "BOK: dict[int, float] = {}\n"
new = '''BOK: dict[int, float] = {}
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
'''
s = s.replace(old, new, 1)

# 3) row() 안: 정책 변수 계산 (cycle_feats 직전)
old = "        x.update(self.cycle_feats(t, year))\n        t1 = t + HORIZON\n"
new = '''        # ── 정책·공급 변수 (POLICY_GROUPS) ──
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
'''
assert old in s
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("patched")
