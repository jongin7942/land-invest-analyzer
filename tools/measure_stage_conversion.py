"""정비사업 단계 전환율·체류기간 — §14.3 PROJECT_PROBABILITY 의 historical_stage_conversion_rate (2026-09-05).

서울 정보몽땅(733, 근사 일자 포함) · 경기데이터드림 추진현황(533) · 인천 renewal(148) 의 단계별 일자로
  P(다음 단계 이상 도달 | 현재 단계, 5년 안)  와  단계 체류기간 분포(중앙값·P25·P75)
를 지역별로 잰다. 관측 종료(2026-09) 전에 5년이 안 지난 사례는 '미확정' 으로 빼고(우측 절단), 지역 간 전이 금지(§14.7).
출력: rules/stage_conversion.csv, reports/stage_conversion.json
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules"
LADDER = [("안전진단", 2), ("정비구역지정", 3), ("추진위", 3), ("조합설립", 4), ("사업시행인가", 5), ("관리처분", 6), ("착공", 7), ("준공", 8)]
LABEL = {2: "안전진단(2)", 3: "구역지정/추진위(3)", 4: "조합설립(4)", 5: "사업시행인가(5)", 6: "관리처분(6)", 7: "착공(7)", 8: "준공(8)"}
OBS_END = 202609
HORIZON_M = 60


def ym_int(v: str | None) -> int | None:
    v = (v or "").strip().replace("-", "").replace(".", "")
    return int(v[:6]) if len(v) >= 6 and v[:6].isdigit() else None


def months(a: int, b: int) -> int:
    return (b // 100 - a // 100) * 12 + (b % 100 - a % 100)


def stages_of(row: dict, approx_ok: bool) -> dict[int, int]:
    """stage → 최초 도달 ym."""
    out: dict[int, int] = {}
    for col, st in LADDER:
        v = ym_int(row.get(col)) or (ym_int(row.get(col + "_approx")) if approx_ok else None)
        # 경기 파일은 열 이름이 다르다
        if v is None:
            for alt in (col + "인가일자", col + "일자", col + "승인일자", col + "지정일자(최초지정)"):
                v = ym_int(row.get(alt))
                if v:
                    break
        if v and (st not in out or v < out[st]):
            out[st] = v
    return out


def load(name: str, approx_ok: bool) -> list[dict[int, int]]:
    p = RULES / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        return [s for s in (stages_of(r, approx_ok) for r in csv.DictReader(f)) if s]


def main() -> int:
    regions = {"서울": load("seoul_redev_stages.csv", True), "경기": load("gg_redev_progress.csv", False), "인천": load("incheon_redev_stages.csv", False)}
    rows_out, report = [], {}
    for region, projects in regions.items():
        conv = defaultdict(lambda: {"n": 0, "reached": 0, "censored": 0, "dwell": []})
        for st_map in projects:
            for st, ym in st_map.items():
                if st >= 8:
                    continue
                later = [(s2, y2) for s2, y2 in st_map.items() if s2 > st and y2 >= ym]
                nxt = min(later, key=lambda x: x[1]) if later else None
                cell = conv[st]
                if nxt and months(ym, nxt[1]) <= HORIZON_M:
                    cell["n"] += 1; cell["reached"] += 1; cell["dwell"].append(months(ym, nxt[1]))
                elif months(ym, OBS_END) >= HORIZON_M:
                    cell["n"] += 1          # 5년 지났는데 다음 단계 없음 = 미전환
                else:
                    cell["censored"] += 1   # 아직 5년 안 됨 → 제외
        rep = {}
        for st in sorted(conv):
            c = conv[st]
            d = sorted(c["dwell"])
            q = lambda p: d[min(len(d) - 1, int(len(d) * p))] if d else None
            rate = round(c["reached"] / c["n"], 3) if c["n"] else None
            status = "VERIFIED" if c["n"] >= 30 else ("PROXY" if c["n"] >= 10 else "UNKNOWN")
            rep[LABEL[st]] = {"n": c["n"], "p_next_within_5y": rate, "censored": c["censored"], "dwell_median_m": q(0.5), "dwell_p25_m": q(0.25), "dwell_p75_m": q(0.75), "status": status}
            rows_out.append({"region": region, "from_stage": st, "from_label": LABEL[st], "n": c["n"], "p_next_within_5y": rate, "censored": c["censored"],
                             "dwell_median_months": q(0.5), "dwell_p25_months": q(0.25), "dwell_p75_months": q(0.75), "status": status,
                             "source": {"서울": "서울 정비사업 정보몽땅(근사 일자 포함)", "경기": "경기데이터드림 정비사업 추진현황", "인천": "인천 renewal 정비사업 현황"}[region]})
        report[region] = {"projects": len(projects), "by_stage": rep}
        print(f"[{region}] 사업 {len(projects)}")
        for k, v in rep.items():
            print(f"   {k:16s} n={v['n']:4d} 5년내 다음단계 {v['p_next_within_5y']}  체류 중앙 {v['dwell_median_m']}개월 (P25 {v['dwell_p25_m']} / P75 {v['dwell_p75_m']})  절단 {v['censored']}  {v['status']}")
    with (RULES / "stage_conversion.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys())); w.writeheader(); w.writerows(rows_out)
    (ROOT / "reports" / "stage_conversion.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
