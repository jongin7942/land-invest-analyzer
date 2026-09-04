"""역까지의 거리별로, 같은 시군구 안에서 매년 몇 %p 씩 벌어지나.

── 이 값이 왜 필요한가 ─────────────────────────────────────────────
개통 사례 117건을 재보니 **새 역이 생기는 것** 은 알파를 만들지 않았다
(tools/event_study.py · 개통 -60~+24개월 내내 중앙값 +1%p 미만, 매 구간이
동전 던지기). 그런데 같은 데이터에서 **역까지의 거리 자체**는 아주 강했다.

    같은 단지 1,777개를 2008~2025년 추적한 실제 상승률
        역 ~500m  +107.1%      1~1.5km  +65.0%
        500m~1km   +86.7%      1.5~2km  +59.5%

이 차이의 대부분은 이미 값에 들어 있다(역세권은 원래 비싸다). 그래서 수준은
알파가 아니다. 알파가 되려면 격차가 **앞으로 더 벌어져야** 하는데, 실제로
17년간 단조롭게 벌어졌다. 계속 벌어진다는 것은 아직 다 반영되지 않았다는
뜻이다 — 다 반영됐으면 한 번 뛰고 멈춰야 한다.

여기서 재는 것은 그 **벌어지는 속도**다.

── 어떻게 재는가 ────────────────────────────────────────────────────
1. 시군구 중앙값으로 나눈다. 시장 전체 등락과 지역 간 가격차가 지워지고,
   "같은 동네 안에서 상대적으로 어디쯤인가" 만 남는다.
2. **2008년과 2025년에 둘 다 값이 있는 단지만** 쓴다(balanced panel).
   신축이 들어와 집단이 바뀌면 그 변화가 '거리의 효과' 로 둔갑한다.
   analogue.py 에서 정확히 이 함정에 빠졌던 적이 있다(킨텍스역 -29.2% -> +1.5%).
3. 18개 해 전체에 최소제곱 직선을 맞춘다. 끝점 두 개만 쓰면 그 해의 잡음이
   그대로 값이 된다.

── 이 값의 한계 ─────────────────────────────────────────────────────
· 2008~2025년 수도권 한 표본에서 나온 추세다. 앞으로도 이어진다는 보장은 없다.
  다만 18년 내내 방향이 바뀌지 않았고 5개 밴드가 순서대로 늘어선다.
· 거리는 역 하나까지의 직선거리다. 그 역이 어느 노선인지, 강남까지 몇 분인지는
  안 본다. 노선의 질을 넣으면 더 정확해지겠지만 지금 데이터로는 못 한다.
· D(1.5~2km)와 E(2km 밖)는 단지가 84개·79개로 얇다. feature 쪽에서 신뢰도를
  낮게 준다.
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apt_engine.db.connection import get_conn  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "rules" / "station_access_drift.csv"

# (밴드 이름, 상한 미터). 마지막은 그 밖 전부.
BANDS: tuple[tuple[str, float | None], ...] = (
    ("~500m", 500), ("500m~1km", 1000), ("1~1.5km", 1500),
    ("1.5~2km", 2000), ("2km 밖", None),
)

FIRST_YEAR, LAST_YEAR = 2008, 2025
AREA_BAND = "84"
MIN_PER_BAND = 20          # 한 해 한 밴드에 이보다 적으면 그 해는 안 쓴다


def band_of(meters: float | None) -> str:
    for name, upper in BANDS:
        if upper is None or (meters is not None and meters <= upper):
            return name
    return BANDS[-1][0]


def slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0


def main() -> int:
    with get_conn() as conn:
        dist = {r["complex_id"]: r["m"] for r in conn.execute(
            "SELECT d.complex_id, MIN(d.meters) m FROM station_distance d "
            "  JOIN transit_station s ON s.id = d.station_id "
            "   AND s.status IN ('운영중','개통') GROUP BY d.complex_id")}
        has_coord = {r["id"] for r in conn.execute(
            "SELECT id FROM complex WHERE lat IS NOT NULL")}

        years = list(range(FIRST_YEAR, LAST_YEAR + 1))
        per_year: dict[int, dict[int, tuple[str, int]]] = {}
        for y in years:
            per_year[y] = {
                r["complex_id"]: (r["lawd_cd"], r["p"]) for r in conn.execute(
                    "SELECT c.lawd_cd, ps.complex_id, "
                    "       MAX(ps.representative_price) p "
                    "  FROM price_snapshot ps JOIN complex c ON c.id = ps.complex_id "
                    " WHERE ps.area_band = ? AND ps.as_of_ym BETWEEN ? AND ? "
                    " GROUP BY ps.complex_id", (AREA_BAND, f"{y}01", f"{y}12"))}

    panel = set(per_year[FIRST_YEAR]) & set(per_year[LAST_YEAR]) & has_coord

    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    counts: dict[str, int] = {}
    for y in years:
        avail = [c for c in panel if c in per_year[y]]
        by_lawd = defaultdict(list)
        for c in avail:
            by_lawd[per_year[y][c][0]].append(per_year[y][c][1])
        med = {k: statistics.median(v) for k, v in by_lawd.items()}
        buckets = defaultdict(list)
        for c in avail:
            lawd, price = per_year[y][c]
            buckets[band_of(dist.get(c))].append(price / med[lawd])
        for name, _ in BANDS:
            if len(buckets[name]) >= MIN_PER_BAND:
                series[name].append((y, statistics.median(buckets[name])))
                counts[name] = max(counts.get(name, 0), len(buckets[name]))

    rows = []
    for name, upper in BANDS:
        s = series.get(name)
        if not s or len(s) < 10:
            print(f"  {name}: 해가 {len(s or [])}개뿐 — 추세를 내지 않습니다")
            continue
        xs = [float(y) for y, _ in s]
        ys = [v for _, v in s]
        rows.append({
            "band": name,
            "max_meters": "" if upper is None else int(upper),
            "annual_drift": round(slope(xs, ys), 6),
            "level_first": round(ys[0], 4),
            "level_last": round(ys[-1], 4),
            "complexes": counts[name],
            "years": len(s),
            "first_year": FIRST_YEAR,
            "last_year": LAST_YEAR,
            "source_name": "자체 측정 — 같은 단지 balanced panel, 시군구 중앙값 정규화",
            "note": (f"{FIRST_YEAR}~{LAST_YEAR}년 {len(s)}개 해에 맞춘 최소제곱 직선의 "
                     f"기울기. 단지 {counts[name]}개"),
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\n같은 단지 {len(panel):,}개 · {FIRST_YEAR}~{LAST_YEAR}년\n")
    print(f"{'밴드':12s} {'단지':>6s} {'첫해':>7s} {'끝해':>7s} {'연 추세':>10s}  5년")
    print("─" * 60)
    for r in rows:
        print(f"{r['band']:12s} {r['complexes']:6,} {r['level_first']:7.3f} "
              f"{r['level_last']:7.3f} {r['annual_drift']:+9.3%}p "
              f"{r['annual_drift']*5:+7.2%}p")
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
