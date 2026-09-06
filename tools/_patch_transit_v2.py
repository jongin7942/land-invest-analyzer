"""교통 변수 v2: 착공/발표 기준 계획역 복원(연혁표) + 급행 정차역 — 종인님 2026-09-06 "검증하도록 해 · 급행도 확인".

load_stations → (lat, lon, opened, sdate, tier, project_name, station_name, line_id) 8-tuple.
transit_feats 추가:
  planned_t1_c / planned_t2_c : 착공(construct_ym) ≤ ym < 개통 인 1급/2급 역이 1.5km (연혁표 기반, 과거 복원)
  planned_t1_a               : 발표(announce_ym: 예타·기본계획) ≤ ym < 개통 인 1급 역 1.5km
  yrs_to_open_t1             : 가장 가까운 계획 1급 역의 (개통 − ym) 년수(개통 안 됐으면 예정 open_ym, 없으면 8)
  express_t1_km              : 급행 정차 1급 역까지 거리(km, 5km 넘으면 5)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "apt_engine" / "exitprice" / "panel.py"
s = p.read_text(encoding="utf-8")
assert "planned_t1_c" not in s

old = '''TRANSIT_GROUPS = {
    # 2026-09-06 종인님: 지하철·GTX 의 '어디와 이어지느냐'(목적지 등급) + 선점효과를 가격 이론에 대입
    "교통접근": ["stn_t1_km", "stn_t2_km", "access_score"],
    "교통계획": ["planned_t1", "planned_t2"],
    "교통선점": ["t1_new5y"],
}'''
new = '''TRANSIT_GROUPS = {
    # 2026-09-06 종인님: 지하철·GTX 의 '어디와 이어지느냐'(목적지 등급) + 선점효과를 가격 이론에 대입
    "교통접근": ["stn_t1_km", "stn_t2_km", "access_score"],
    "교통계획": ["planned_t1", "planned_t2"],
    "교통선점": ["t1_new5y"],
    "교통계획2(착공기준 복원)": ["planned_t1_c", "planned_t2_c", "yrs_to_open_t1"],
    "교통계획3(발표기준 복원)": ["planned_t1_a"],
    "급행": ["express_t1_km"],
}
MILESTONES: list[dict] = []     # rules/transit_project_milestones.csv
EXPRESS: dict[str, tuple[bool, set]] = {}   # line_id → (all_express, {station names})
try:
    import csv as _csv3
    from pathlib import Path as _P3
    _R3 = _P3(__file__).resolve().parents[2] / "rules"
    with (_R3 / "transit_project_milestones.csv").open(encoding="utf-8") as _f:
        MILESTONES = list(_csv3.DictReader(_f))
    with (_R3 / "transit_express_stations.csv").open(encoding="utf-8") as _f:
        for _r in _csv3.DictReader(_f):
            EXPRESS[_r["line_id"]] = (_r["all_express"] == "1", set(x.strip() for x in (_r["stations"] or "").split("|") if x.strip()))
except Exception:
    pass


def milestone_of(project_name: str):
    for m in sorted(MILESTONES, key=lambda r: -len(r["pattern"])):
        if m["pattern"] in (project_name or ""):
            return m
    return None


def is_express(line_id: str, station_name: str) -> bool:
    e = EXPRESS.get(line_id or "")
    if not e:
        return False
    if e[0]:
        return True
    base = (station_name or "").split("(")[0].replace("역", "").strip()
    return any(base == n or base.startswith(n) for n in e[1])'''
assert old in s; s = s.replace(old, new, 1)

old = '''    rows = conn.execute("SELECT s.lat, s.lon, s.opened_ym, s.status_date, s.status, p.destination_tier FROM transit_station s "
                        "LEFT JOIN transit_project p ON p.id = s.project_id WHERE s.lat IS NOT NULL").fetchall()
    out = []
    for r in rows:
        opened = str(r["opened_ym"]) if r["opened_ym"] else None
        if opened is None and r["status"] == "운영중":
            opened = "200001"          # 자료 시작 전부터 운영 중
        out.append((float(r["lat"]), float(r["lon"]), opened, r["status_date"], int(r["destination_tier"]) if r["destination_tier"] else None))
    return out'''
new = '''    rows = conn.execute("SELECT s.lat, s.lon, s.opened_ym, s.status_date, s.status, p.destination_tier, p.name AS pname, s.name AS sname, p.line_id FROM transit_station s "
                        "LEFT JOIN transit_project p ON p.id = s.project_id WHERE s.lat IS NOT NULL").fetchall()
    out = []
    for r in rows:
        opened = str(r["opened_ym"]) if r["opened_ym"] else None
        if opened is None and r["status"] == "운영중":
            opened = "200001"          # 자료 시작 전부터 운영 중
        out.append((float(r["lat"]), float(r["lon"]), opened, r["status_date"], int(r["destination_tier"]) if r["destination_tier"] else None, r["pname"], r["sname"], r["line_id"]))
    return out'''
assert old in s; s = s.replace(old, new, 1)

old = '''        access = sum(TIER_W[t] / (1.0 + best[t]) for t in (1, 2, 3))
        return {"stn_t1_km": best[1], "stn_t2_km": best[2], "access_score": access, "planned_t1": planned[1], "planned_t2": planned[2], "t1_new5y": new5}'''
new = '''        access = sum(TIER_W[t] / (1.0 + best[t]) for t in (1, 2, 3))
        # v2: 연혁표로 과거 계획 상태 복원 + 급행
        pc = {1: 0.0, 2: 0.0}; pa1 = 0.0; yrs = 8.0; ex_best = 5.0
        for i in self._near(self._sgrid, c.lat, c.lon, r=3):
            st = self.stations[i]
            if len(st) < 8 or st[4] is None:
                continue
            la, lo, opened, sdate, tier, pname, sname, line = st
            d = haversine_m(c.lat, c.lon, la, lo) / 1000.0
            if d > 5.0:
                continue
            opened_i = store._ym_index(opened) if opened else None
            if tier == 1 and opened_i is not None and opened_i <= yidx and is_express(line, sname) and d < ex_best:
                ex_best = d
            m = milestone_of(pname)
            if m and d <= 1.5 and (opened_i is None or opened_i > yidx):
                open_ym = opened or (m.get("open_ym") or "")
                if m.get("construct_ym") and store._ym_index(m["construct_ym"]) <= yidx and tier in pc:
                    pc[tier] = 1.0
                    if tier == 1 and open_ym:
                        yrs = min(yrs, max(0.0, (store._ym_index(open_ym) - yidx) / 12.0))
                if tier == 1 and m.get("announce_ym") and store._ym_index(m["announce_ym"]) <= yidx:
                    pa1 = 1.0
        return {"stn_t1_km": best[1], "stn_t2_km": best[2], "access_score": access, "planned_t1": planned[1], "planned_t2": planned[2], "t1_new5y": new5,
                "planned_t1_c": pc[1], "planned_t2_c": pc[2], "yrs_to_open_t1": yrs, "planned_t1_a": pa1, "express_t1_km": ex_best}'''
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("patched v2")
