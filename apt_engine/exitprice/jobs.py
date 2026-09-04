"""직장 근접 변수 — 국민연금 가입 사업장 내역(법정동별 가입자 수)에서 만든다.

  jobs_emd        : 단지가 속한 법정동의 가입자 수(log1p). 진입 시점 이전의 가장 가까운 스냅샷.
                    2016년 이전 진입은 2016 스냅샷을 쓴다(PROXY — 일자리 수준은 느리게 변한다는 가정).
  jobs_3km        : 반경 3km 안 법정동 가입자 수 합(log1p) — '직장과 가까운 곳' 의 실제 크기.
  jobs_growth5    : 5년 전 스냅샷 대비 3km 가입자 증감(log 비) — 자료가 있는 진입연도만.
법정동 코드는 단지 pnu 앞 10자리, pnu 가 없으면 같은 시군구·같은 읍면동 이름의 다른 단지 코드로 맞춘다.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

from apt_engine.relative.store import Complex, haversine_m

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "rules" / "nps_jobs_by_emd.csv"


class Jobs:
    def __init__(self, complexes: dict[int, Complex], conn=None):
        self.snap: dict[str, dict[str, int]] = defaultdict(dict)     # ym → emd10 → insured
        if CSV.exists():
            with CSV.open(encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    self.snap[r["ym"]][r["emd_cd10"]] = self.snap[r["ym"]].get(r["emd_cd10"], 0) + int(r["insured"])
        self.yms = sorted(self.snap)
        # 단지 → 법정동 코드
        self.code_of: dict[int, str] = {}
        name_to_code: dict[tuple[str, str], str] = {}
        if conn is not None:
            for r in conn.execute("SELECT id, pnu, lawd_cd, emd_name FROM complex WHERE pnu IS NOT NULL"):
                self.code_of[int(r["id"])] = r["pnu"][:10]
                name_to_code[(r["lawd_cd"], r["emd_name"])] = r["pnu"][:10]
        for c in complexes.values():
            if c.id not in self.code_of:
                code = name_to_code.get((c.lawd_cd, c.emd))
                if code:
                    self.code_of[c.id] = code
        # 법정동 중심점(단지 좌표 평균)
        pts: dict[str, list] = defaultdict(list)
        for c in complexes.values():
            code = self.code_of.get(c.id)
            if code:
                pts[code].append((c.lat, c.lon))
        self.center = {k: (sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v)) for k, v in pts.items()}
        self._grid: dict[tuple[int, int], list[str]] = defaultdict(list)
        for k, (la, lo) in self.center.items():
            self._grid[(int(la / 0.03), int(lo / 0.03))].append(k)

    @property
    def available(self) -> bool:
        return bool(self.yms)

    def snapshot_for(self, entry_ym: str) -> str | None:
        """진입 시점 이전(같은 달 포함)의 가장 최근 스냅샷. 없으면 가장 오래된 것(PROXY)."""
        prior = [y for y in self.yms if y <= entry_ym]
        return prior[-1] if prior else (self.yms[0] if self.yms else None)

    def _within(self, c: Complex, radius_m: float, ym: str) -> int:
        g = (int(c.lat / 0.03), int(c.lon / 0.03))
        tot = 0
        table = self.snap.get(ym, {})
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for code in self._grid.get((g[0] + dx, g[1] + dy), ()):
                    la, lo = self.center[code]
                    if haversine_m(c.lat, c.lon, la, lo) <= radius_m:
                        tot += table.get(code, 0)
        return tot

    def features(self, c: Complex, entry_ym: str) -> dict:
        if not self.available:
            return {"jobs_emd": None, "jobs_3km": None, "jobs_growth5": None, "jobs_status": "NO_DATA"}
        ym = self.snapshot_for(entry_ym)
        code = self.code_of.get(c.id)
        own = self.snap[ym].get(code) if code else None
        near = self._within(c, 3000, ym)
        # 5년 전 스냅샷(±12개월 허용)
        target = f"{int(entry_ym[:4]) - 5}{entry_ym[4:6]}"
        older = [y for y in self.yms if abs(int(y[:4]) * 12 + int(y[4:6]) - (int(target[:4]) * 12 + int(target[4:6]))) <= 12]
        growth = None
        if older:
            near0 = self._within(c, 3000, older[0])
            if near0 > 0 and near > 0:
                growth = math.log(near / near0)
        status = "VERIFIED" if ym <= entry_ym else "PROXY_LATER_SNAPSHOT"
        return {"jobs_emd": math.log1p(own) if own is not None else None,
                "jobs_3km": math.log1p(near) if near else None,
                "jobs_growth5": growth, "jobs_status": status}
