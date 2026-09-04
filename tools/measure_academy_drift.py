"""학원가 밀도별 — 같은 시군구 안에서 상대가격이 벌어지는 속도.

종인님(2026-09-04): "학원가도 영향을 미치던데 이것도 고려해줘", "초품아보다
학업성취도가 중요한 것 같아". 학교 거리 자체는 재보니 약했고(먼 꼬리만 뒤처짐),
학원가는 첫 측정(경기 1,092개 단지)에서 역세권보다 강하게 나왔다:

    500m 안 입시·보습 학원 수     시군구 대비 연 추세    같은 단지 17년 상승
    하위 50%  (18개 미만)              -0.08%p                +47%
    중간                               +0.06%p                +66%
    상위 20%  (38개↑)                  +0.01%p                +60%
    상위 5%   (78개↑, 학원가)          +0.38%p                +91%

역세권 ~500m 가 +0.13%p 였다. 학원가 상위 5% 는 그 세 배다.

── 자 ───────────────────────────────────────────────────────────────
station_access·병원·학교와 같은 자. 같은 단지(2008·2025 둘 다 값 있음)만, 시군구
중앙값으로 나눈 상대가격, 18개 해에 직선을 맞춘 기울기. '학원가' 는 거리가 아니라
**반경 500m 안 입시·보습·교과 계열 학원 수**다(예능·외국어·직업기술은 뺀다).
밴드 경계는 그 시도 단지들의 분위수(50/80/95%)로 잡는다 — 절대 개수는 지역마다
다르다.

── 데이터 ───────────────────────────────────────────────────────────
    rules/gg_academies.csv     경기데이터드림(시군명, 교습과정명, WGS84위도/경도)
    rules/academies_all.csv    전국학원및교습소표준데이터(있으면) — 열 이름이 다르므로
                               아래 COLS 에 매핑을 둔다
서울·인천 좌표가 없으면 그 시도는 재지 않는다.
"""
from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apt_engine.db.connection import get_conn  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rules" / "academy_density_drift.csv"
RADIUS_M = 500
FIRST_YEAR, LAST_YEAR = 2008, 2025
EXAM_KEYWORDS = ("입시", "보습", "교과", "논술", "종합")

# 파일별 열 이름. (위도, 경도, 과정/분야, 주소)
SOURCES = [
    (ROOT / "rules" / "gg_academies.csv", ("WGS84위도", "WGS84경도", "교습과정명", "소재지지번주소")),
    (ROOT / "rules" / "academies_all.csv", ("위도", "경도", "교습과정명", "소재지지번주소")),
]


def hav(a, b, c, d):
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def load_points() -> dict[str, list[tuple[float, float]]]:
    """시도(서울/경기/인천) → 입시계열 학원 좌표."""
    by = defaultdict(list)
    seen = set()
    for path, (klat, klon, kcourse, kaddr) in SOURCES:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    la, lo = float(r[klat]), float(r[klon])
                except (KeyError, ValueError, TypeError):
                    continue
                course = r.get(kcourse) or ""
                if not any(k in course for k in EXAM_KEYWORDS):
                    continue
                addr = r.get(kaddr) or ""
                sido = ("서울" if addr.startswith("서울") else "경기" if addr.startswith("경기") or "시군명" in r
                        else "인천" if addr.startswith("인천") else None)
                if not sido:
                    continue
                key = (round(la, 5), round(lo, 5))
                if key in seen:
                    continue
                seen.add(key)
                by[sido].append((la, lo))
    return by


def counter(points):
    cell = 0.01
    grid = defaultdict(list)
    for la, lo in points:
        grid[(int(la / cell), int(lo / cell))].append((la, lo))

    def count(la, lo):
        n, ci, cj = 0, int(la / cell), int(lo / cell)
        for i in range(ci - 1, ci + 2):
            for j in range(cj - 1, cj + 2):
                for hla, hlo in grid.get((i, j), []):
                    if hav(la, lo, hla, hlo) <= RADIUS_M:
                        n += 1
        return n
    return count


def slope(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0


def main() -> int:
    pts = load_points()
    prefix = {"서울": "11", "경기": "41", "인천": "28"}
    years = list(range(FIRST_YEAR, LAST_YEAR + 1))
    out_rows = []
    with get_conn() as conn:
        for sido, points in pts.items():
            if len(points) < 500:
                print(f"[{sido}] 학원 {len(points)}개뿐 — 건너뜀")
                continue
            count = counter(points)
            cx = {r["id"]: (r["lat"], r["lon"]) for r in conn.execute(
                "SELECT id, lat, lon FROM complex WHERE lat IS NOT NULL AND lawd_cd LIKE ?",
                (prefix[sido] + "%",))}
            per = {}
            for y in years:
                per[y] = {r["complex_id"]: (r["lawd_cd"], r["p"]) for r in conn.execute(
                    "SELECT c.lawd_cd, ps.complex_id, MAX(ps.representative_price) p "
                    "  FROM price_snapshot ps JOIN complex c ON c.id = ps.complex_id "
                    " WHERE c.lawd_cd LIKE ? AND ps.area_band = '84' AND ps.as_of_ym BETWEEN ? AND ? "
                    " GROUP BY ps.complex_id", (prefix[sido] + "%", f"{y}01", f"{y}12"))}
            panel = set(per[FIRST_YEAR]) & set(per[LAST_YEAR]) & set(cx)
            if len(panel) < 200:
                print(f"[{sido}] 패널 {len(panel)}개 — 건너뜀")
                continue
            dens = {c: count(*cx[c]) for c in panel}
            vals = sorted(dens.values())
            q50, q80, q95 = (vals[int(len(vals) * k)] for k in (0.5, 0.8, 0.95))

            def band(n):
                return ("D 상위5%" if n >= q95 else "C 상위20%" if n >= q80
                        else "B 중간" if n >= q50 else "A 하위50%")
            names = ["A 하위50%", "B 중간", "C 상위20%", "D 상위5%"]
            series, cnt = defaultdict(list), {}
            for y in years:
                avail = [c for c in panel if c in per[y]]
                gm = defaultdict(list)
                for c in avail:
                    gm[per[y][c][0]].append(per[y][c][1])
                med = {k: statistics.median(v) for k, v in gm.items()}
                b = defaultdict(list)
                for c in avail:
                    b[band(dens[c])].append(per[y][c][1] / med[per[y][c][0]])
                for nm in names:
                    if len(b[nm]) >= 20:
                        series[nm].append((y, statistics.median(b[nm])))
                        cnt[nm] = len(b[nm])
            gain = defaultdict(list)
            for c in panel:
                gain[band(dens[c])].append(per[LAST_YEAR][c][1] / per[FIRST_YEAR][c][1] - 1)
            print(f"\n[{sido}] 학원 {len(points):,}개 · 단지 {len(panel):,}개 · 500m 안 학원 수 분위 50/80/95% = {q50}/{q80}/{q95}")
            for nm in names:
                s = series.get(nm)
                if not s or len(s) < 10:
                    continue
                xs = [y for y, _ in s]
                ys = [v for _, v in s]
                sl = slope(xs, ys)
                g = statistics.median(gain[nm]) if gain[nm] else float("nan")
                print(f"   {nm:10s} 단지{cnt[nm]:5d}  {ys[0]:.3f} → {ys[-1]:.3f}  연 {sl:+.3%}p  5년 {sl*5:+.2%}p  17년 상승 {g:+.0%}")
                out_rows.append({"sido": sido, "band": nm, "complexes": cnt[nm],
                                 "threshold_count": {"A 하위50%": 0, "B 중간": q50, "C 상위20%": q80, "D 상위5%": q95}[nm],
                                 "annual_drift": round(sl, 6), "level_first": round(ys[0], 4),
                                 "level_last": round(ys[-1], 4), "gain_17y_median": round(g, 4),
                                 "radius_m": RADIUS_M, "first_year": FIRST_YEAR, "last_year": LAST_YEAR})
    if out_rows:
        with OUT.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)
        print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
