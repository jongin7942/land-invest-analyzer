"""서울 재건축 단계 인가일 전후 — 그 단지의 시군구 대비 상대가격이 움직였나.

경기도(68건)로는 사례가 13~21건이라 결론을 못 냈다. 서울은 정보몽땅 추진경과에서
단계별 인가일을 긁었고(fetch_seoul_stages.py), 사업장을 우리 단지에 붙인 표
(seoul_redev_matched.csv, measure_seoul_stage_level.py 가 만든 것)와 사업장명으로
합친다. 자는 measure_redev_stages.py 와 같다:

    상대가격 = 그 단지 대표가격 ÷ 같은 시군구·같은 면적대 중앙값
    효과     = 상대가격(인가 +12개월) − 상대가격(인가 −12개월)

면적대는 그 단지에서 스냅샷이 가장 많은 것을 쓴다(84㎡ 고정이면 1980년대 단지가
빠진다). `_approx`(변경인가일만 있는 사업)는 따로 센다 — 실제 인가보다 늦은 날이라
'후' 창이 이미 반영된 뒤일 수 있다.
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_redev_stages import (DATA_START_YM, WINDOW_MONTHS, main_band,  # noqa: E402
                                  relative_at, shift_ym)

from apt_engine.db.connection import get_conn  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MATCHED = ROOT / "rules" / "seoul_redev_matched.csv"
STAGES = ROOT / "rules" / "seoul_redev_stages.csv"
OUT = ROOT / "rules" / "redev_stage_effect_seoul.csv"
ORDER = ["안전진단", "정비구역지정", "추진위", "조합설립", "사업시행인가", "관리처분", "착공", "준공"]


def main() -> int:
    matched = {r["project"]: r for r in csv.DictReader(MATCHED.open(encoding="utf-8"))}
    stages = list(csv.DictReader(STAGES.open(encoding="utf-8")))
    joined = [(matched[s["project"]], s) for s in stages if s["project"] in matched]
    print(f"단계 표 {len(stages)}건 · 단지에 붙은 사업장 {len(matched)}건 · 합쳐진 {len(joined)}건\n")

    results: dict[str, list[tuple[float, str, str, bool]]] = defaultdict(list)
    skipped: dict[str, int] = defaultdict(int)
    with get_conn() as conn:
        for m, s in joined:
            cid, lawd = int(m["complex_id"]), m["lawd_cd"]
            band = main_band(conn, cid)
            if not band:
                skipped["가격 스냅샷 없음"] += 1
                continue
            for stage in ORDER:
                day, approx = s.get(stage, ""), False
                if not day and s.get(stage + "_approx"):
                    day, approx = s[stage + "_approx"], True
                if len(day) != 8:
                    continue
                ym = day[:6]
                before, after = shift_ym(ym, -WINDOW_MONTHS), shift_ym(ym, WINDOW_MONTHS)
                if before < DATA_START_YM:
                    skipped[f"{stage}·데이터이전"] += 1
                    continue
                a = relative_at(conn, cid, lawd, band, before)
                b = relative_at(conn, cid, lawd, band, after)
                if a is None or b is None:
                    skipped[f"{stage}·가격없음"] += 1
                    continue
                results[stage].append((b - a, m["complex_name"], day, approx))

    print("═" * 76)
    print(f"서울 · 단계 인가 전후 {WINDOW_MONTHS}개월 · 자치구 대비 상대가격 변화")
    print("═" * 76)
    print(f"  {'단계':12s} {'전체':>4s} {'중앙값':>8s} {'상승':>5s}   {'최초인가만':>6s} {'중앙값':>8s}   {'변경인가만':>6s} {'중앙값':>8s}")
    print("  " + "─" * 72)
    table = []
    for stage in ORDER:
        v = results[stage]
        if len(v) < 5:
            print(f"  {stage:12s} {len(v):4d}  표본 5건 미만")
            continue
        allv = sorted(x[0] for x in v)
        strict = sorted(x[0] for x in v if not x[3])
        loose = sorted(x[0] for x in v if x[3])
        med = statistics.median(allv)
        pos = sum(1 for x in allv if x > 0) / len(allv)
        ms = f"{statistics.median(strict):+8.2%}" if len(strict) >= 3 else "       —"
        ml = f"{statistics.median(loose):+8.2%}" if len(loose) >= 3 else "       —"
        print(f"  {stage:12s} {len(allv):4d} {med:+8.2%} {pos:5.0%}   {len(strict):6d} {ms}   {len(loose):6d} {ml}")
        table.append({"stage": stage, "samples": len(allv), "median_delta": round(med, 6),
                      "positive_ratio": round(pos, 4), "strict_n": len(strict),
                      "strict_median": round(statistics.median(strict), 6) if strict else "",
                      "approx_n": len(loose),
                      "approx_median": round(statistics.median(loose), 6) if loose else "",
                      "window_months": WINDOW_MONTHS})
    if table:
        with OUT.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
            w.writeheader()
            w.writerows(table)
        print(f"\n→ {OUT}")
    print("\n못 잰 사유:")
    for k, n in sorted(skipped.items(), key=lambda kv: -kv[1])[:8]:
        print(f"   {k:22s} {n:4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
