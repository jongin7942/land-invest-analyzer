"""역세권 프리미엄은 **언제** 벌어지나 — 개통 전후 시간축으로 펼쳐 본다.

── 왜 이걸 재는가 ──────────────────────────────────────────────────
개통 ±12개월로 재니 지하철 117건의 중앙값이 +0.15% 였다. 사실상 0 이다.
그런데 노선별로 갈라 보면 이야기가 다르다.

    신림선(2022)          +4.83%
    인천2호선(2016)        +2.69%
    서울 9호선 1단계(2009)  -3.67%
    7호선 부천연장(2012)    -3.50%

9호선 1단계는 2002년에 착공해서 2009년에 열렸다. 개통할 무렵에는 이미
7년 동안 반영될 시간이 있었다. 반면 신림선은 착공(2015)에서 개통(2022)까지
기간이 짧고 노선 확정도 늦었다.

즉 "개통 때 오른다" 가 아니라 **"확정될 때부터 개통까지 나눠서 오른다"** 일
가능성이 크다. 그렇다면 개통 시점만 보는 창은 남은 부스러기만 보는 것이다.

── 어떻게 재는가 ────────────────────────────────────────────────────
개통을 0 으로 놓고 시간축을 펼친다. 각 시점마다 역세권/비역세권 가격비율을
내고, 가장 이른 시점을 기준(0)으로 삼아 그 뒤로 얼마나 벌어졌는지를 본다.

    t=-60개월  비율 1.000  (기준)
    t=-36개월  비율 1.012  → +1.2%p
    t=  0개월  비율 1.031  → +3.1%p
    t=+24개월  비율 1.033  → +3.3%p

이 모양이 "언제 오르나" 의 답이다. 계단이 개통 직전에 있으면 개통이 이벤트고,
훨씬 앞에 있으면 발표·착공이 이벤트다.

**모든 시점에 값이 있는 단지만** 쓴다(balanced panel). 신축이 들어와 집단이
바뀌면 그 변화가 '역의 효과' 로 둔갑하기 때문이다 — analogue.py 에서 이미
한 번 데인 자리다. 시점이 8개면 조건이 그만큼 빡세지므로 표본이 줄어든다.
그래도 구성이 바뀐 수를 보는 것보다는 낫다.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apt_engine.catalyst import analogue as an  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402

# 개통을 0 으로 놓은 시간축(개월).
OFFSETS = (-60, -48, -36, -24, -12, 0, +12, +24)

# 가격 데이터가 시작하는 달. 이보다 앞은 잴 수 없다.
DATA_START_YM = "200610"

MIN_PANEL = 5          # 모든 시점에 값이 있는 단지가 이보다 적으면 버린다


def prices_all(conn, complex_ids, area_band, yms, tol):
    """모든 시점에 값이 있는 단지만 남긴 {ym: {complex_id: price}}."""
    per_ym = {ym: an._prices_at(conn, complex_ids, area_band, ym, tol) for ym in yms}
    common = None
    for m in per_ym.values():
        common = set(m) if common is None else (common & set(m))
    if not common:
        return None, 0
    return ({ym: {c: m[c] for c in common} for ym, m in per_ym.items()},
            len(common))


def profile(conn, station, area_band="84"):
    """한 역의 시간축 프로파일. (offset → 비율) 또는 None."""
    opened = station["opened_ym"]
    yms = [an.shift_ym(opened, o) for o in OFFSETS]
    if min(yms) < DATA_START_YM:
        return None, "가격 데이터 시작(2006-10) 이전 구간이 있음"

    near, far = an._split_by_distance(conn, station["id"], station["lawd_cd"],
                                      an.MEASURE_RADIUS_M)
    if len(near) < an.MIN_SAMPLES or len(far) < an.MIN_SAMPLES:
        return None, f"역세권 {len(near)} · 비역세권 {len(far)} — 표본 부족"

    n_all, n_panel = prices_all(conn, near, area_band, yms, an.SNAPSHOT_TOLERANCE_MONTHS)
    f_all, f_panel = prices_all(conn, far, area_band, yms, an.SNAPSHOT_TOLERANCE_MONTHS)
    if not n_all or not f_all or n_panel < MIN_PANEL or f_panel < MIN_PANEL:
        return None, (f"모든 시점에 값이 있는 단지가 부족 "
                      f"(역세권 {n_panel} · 비역세권 {f_panel})")

    out = {}
    for off, ym in zip(OFFSETS, yms):
        n_med = statistics.median(n_all[ym].values())
        f_med = statistics.median(f_all[ym].values())
        if not f_med:
            return None, "비역세권 중앙값 0"
        out[off] = n_med / f_med
    return out, f"패널 역세권 {n_panel} · 비역세권 {f_panel}"


def main() -> int:
    with get_conn() as conn:
        stations = conn.execute(
            "SELECT s.*, p.kind, p.name AS project FROM transit_station s "
            "  JOIN transit_project p ON p.id = s.project_id "
            " WHERE s.status = '개통' AND s.opened_ym IS NOT NULL "
            "   AND s.lawd_cd IS NOT NULL ORDER BY s.opened_ym").fetchall()

        rows = []
        reasons: dict[str, int] = {}
        for st in stations:
            prof, why = profile(conn, st)
            if prof is None:
                reasons[why.split("(")[0].strip()] = reasons.get(
                    why.split("(")[0].strip(), 0) + 1
                continue
            rows.append((st, prof))

    print(f"대상 개통 역 {len(stations)}개 · 시간축을 낸 역 {len(rows)}개\n")
    if reasons:
        print("잰 수 없던 사유:")
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}건  {why}")
        print()

    if not rows:
        return 0

    print("═" * 72)
    print("역세권/비역세권 가격비율 — 개통을 0 으로 놓은 시간축")
    print("  (가장 이른 시점 대비 몇 %p 벌어졌나 · 중앙값)")
    print("═" * 72)

    def show(label, subset):
        if len(subset) < 3:
            return
        print(f"\n  {label}  ({len(subset)}건)")
        base_off = OFFSETS[0]
        line = []
        for off in OFFSETS:
            moves = [p[off] - p[base_off] for _, p in subset]
            med = statistics.median(moves)
            line.append((off, med))
        for off, med in line:
            tag = "개통" if off == 0 else f"{off:+d}개월"
            bar_n = int(abs(med) * 400)
            bar = ("█" * min(bar_n, 40)) if med >= 0 else ("░" * min(bar_n, 40))
            print(f"    {tag:>8s}  {med:+7.2%}  {bar}")

    show("전체", rows)
    by_kind: dict[str, list] = {}
    for st, p in rows:
        by_kind.setdefault(st["kind"], []).append((st, p))
    for kind, sub in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        show(f"종류: {kind}", sub)

    # 구간별 증가분 — 어느 구간에서 벌어지나
    print("\n" + "═" * 72)
    print("구간별 증가분 (앞 시점 대비) — 계단이 어디에 있나")
    print("═" * 72)
    for a, b in zip(OFFSETS, OFFSETS[1:]):
        steps = [p[b] - p[a] for _, p in rows]
        med = statistics.median(steps)
        pos = sum(1 for s in steps if s > 0)
        print(f"  {a:+4d} → {b:+4d}개월   {med:+7.2%}   상승 {pos}/{len(steps)} "
              f"({pos/len(steps):.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
