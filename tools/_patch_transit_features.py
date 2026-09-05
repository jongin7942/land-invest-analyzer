"""panel.py 에 교통 목적지 등급 변수(TRANSIT_GROUPS) 추가 — 종인님 2026-09-06 "교통 선점효과를 이론에 대입".

역 목록에 노선 목적지 등급(transit_project.destination_tier)을 붙이고, 진입 시점(ym) 기준으로
  stn_t1_km / stn_t2_km : 개통된 1급·2급 노선 역까지 최단 거리(km, 5km 안 없으면 5.0)
  access_score           : Σ_tier w/(1+km)  (w: 1급 1.0 · 2급 0.5 · 3급 0.25) — 직장중심 접근성 점수
  planned_t1 / planned_t2: 진입 전에 공표(착공·계획)된 미개통 1급/2급 역이 1.5km 안
  t1_new5y               : 1km 안 1급 역이 최근 5년 안에 개통(이미 가격에 반영된 선점)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "apt_engine" / "exitprice" / "panel.py"
s = p.read_text(encoding="utf-8")
assert "TRANSIT_GROUPS" not in s

old = "KOSIS = [f for fs in KOSIS_GROUPS.values() for f in fs]"
new = '''KOSIS = [f for fs in KOSIS_GROUPS.values() for f in fs]
TRANSIT_GROUPS = {
    # 2026-09-06 종인님: 지하철·GTX 의 '어디와 이어지느냐'(목적지 등급) + 선점효과를 가격 이론에 대입
    "교통접근": ["stn_t1_km", "stn_t2_km", "access_score"],
    "교통계획": ["planned_t1", "planned_t2"],
    "교통선점": ["t1_new5y"],
}
TRANSIT = [f for fs in TRANSIT_GROUPS.values() for f in fs]
TIER_W = {1: 1.0, 2: 0.5, 3: 0.25}'''
assert old in s; s = s.replace(old, new, 1)
old = '    "K_kosis": FEATURES + JOB_FEATURES + THEORY2 + KOSIS,\n}'
new = '    "K_kosis": FEATURES + JOB_FEATURES + THEORY2 + KOSIS,\n    "T_transit": FEATURES + JOB_FEATURES + THEORY2 + TRANSIT,\n}'
assert old in s; s = s.replace(old, new, 1)

# load_stations: tier 포함 5-tuple
old = '''    rows = conn.execute("SELECT lat, lon, opened_ym, status_date, status FROM transit_station WHERE lat IS NOT NULL").fetchall()
    out = []
    for r in rows:
        opened = str(r["opened_ym"]) if r["opened_ym"] else None
        if opened is None and r["status"] == "운영중":
            opened = "200001"          # 자료 시작 전부터 운영 중
        out.append((float(r["lat"]), float(r["lon"]), opened, r["status_date"]))
    return out'''
new = '''    rows = conn.execute("SELECT s.lat, s.lon, s.opened_ym, s.status_date, s.status, p.destination_tier FROM transit_station s "
                        "LEFT JOIN transit_project p ON p.id = s.project_id WHERE s.lat IS NOT NULL").fetchall()
    out = []
    for r in rows:
        opened = str(r["opened_ym"]) if r["opened_ym"] else None
        if opened is None and r["status"] == "운영중":
            opened = "200001"          # 자료 시작 전부터 운영 중
        out.append((float(r["lat"]), float(r["lon"]), opened, r["status_date"], int(r["destination_tier"]) if r["destination_tier"] else None))
    return out'''
assert old in s; s = s.replace(old, new, 1)
# station_feats 언패킹 호환
old = "            la, lo, opened, sdate = self.stations[i]\n"
new = "            la, lo, opened, sdate = self.stations[i][:4]\n"
assert old in s; s = s.replace(old, new, 1)

# 교통 등급 변수 계산 메서드 추가 (station_feats 뒤)
old = "    # ── 행 ──\n"
new = '''    def transit_feats(self, c: Complex, ym: str) -> dict:
        """목적지 등급별 접근성(진입 시점 기준). 역 목록이 4-tuple(등급 없음)이면 None."""
        yidx = store._ym_index(ym)
        best = {1: 5.0, 2: 5.0, 3: 5.0}; planned = {1: 0.0, 2: 0.0}; new5 = 0.0
        for i in self._near(self._sgrid, c.lat, c.lon, r=3):
            st = self.stations[i]
            if len(st) < 5 or st[4] is None:
                continue
            la, lo, opened, sdate, tier = st
            d = haversine_m(c.lat, c.lon, la, lo) / 1000.0
            if opened and store._ym_index(opened) <= yidx:
                if d < best[tier]:
                    best[tier] = d
                if tier == 1 and d <= 1.0 and yidx - store._ym_index(opened) <= 60 and opened != "200001":
                    new5 = 1.0
            elif tier in planned and d <= 1.5 and sdate and sdate[:7].replace("-", "") <= ym:
                planned[tier] = 1.0
        access = sum(TIER_W[t] / (1.0 + best[t]) for t in (1, 2, 3))
        return {"stn_t1_km": best[1], "stn_t2_km": best[2], "access_score": access, "planned_t1": planned[1], "planned_t2": planned[2], "t1_new5y": new5}

    # ── 행 ──
'''
assert old in s; s = s.replace(old, new, 1)
old = "        x.update(self.cycle_feats(t, year))\n        t1 = t + HORIZON\n"
new = "        x.update(self.transit_feats(c, entry_ym))\n        x.update(self.cycle_feats(t, year))\n        t1 = t + HORIZON\n"
assert old in s; s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("patched")
