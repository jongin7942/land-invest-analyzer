"""확산 계급(얼리어답터 → 빠른 추종 → 대중 → 후행) 검증 — 종인님 이론 (2026-09-05).

가설: 먼저 오르는 곳(선행)과 나중에 오르는 곳(후행)이 정해져 있고, 대중+후행이 70% 이상이다.
검증: 진입 시점까지의 자료로 잰 법정동 선행성(emd_lead_months)을 로저스 분포 비율로 4계급으로 나누고
      ① 계급별 비중 ② 계급이 해마다 얼마나 유지되는가(선행지는 계속 선행지인가)
      ③ 계급별 이후 5년 상대수익(시장 대비) — 사이클 국면(수도권 1년 모멘텀 상/하)별로 나눠 본다.
입력: logs/_exit_panel_84_59_74.pkl (tools/run_exit_price.py --no-cache 로 v0.3 변수가 들어간 캐시)
출력: reports/diffusion_classes.json
"""
from __future__ import annotations

import json
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.relative.store import median, percentile  # noqa: E402

CLASSES = [("얼리어답터", 0.84), ("빠른추종", 0.50), ("대중", 0.16), ("후행", 0.0)]   # 상위 16% / 다음 34% / 다음 34% / 하위 16%


def classify(lead: float, q84: float, q50: float, q16: float) -> str:
    if lead >= q84:
        return "얼리어답터"
    if lead >= q50:
        return "빠른추종"
    if lead >= q16:
        return "대중"
    return "후행"


def main() -> int:
    rows = pickle.loads((ROOT / "logs" / "_exit_panel_84_59_74.pkl").read_bytes())
    rows = [r for r in rows if r.x.get("emd_lead_months") is not None]
    if not rows:
        print("emd_lead_months 가 없는 캐시 — v0.3 패널로 다시 만들어야 합니다"); return 1
    # 시장 수준 제거(상대 목표)
    by_y: dict = defaultdict(list)
    for r in rows:
        if r.target is not None:
            by_y[r.entry_ym].append(r.target)
    lvl = {y: median(v) for y, v in by_y.items()}
    out = {"years": {}, "persistence": {}, "class_returns_by_cycle": {}, "note": "계급 경계는 해마다 그 해 법정동 선행성 분포의 16/50/84 백분위(로저스 비율)"}
    cls_of: dict[tuple[str, str], str] = {}
    for y in sorted(by_y):
        yr = [r for r in rows if r.entry_ym == y]
        # 법정동 단위 선행성(단지 행의 중앙값)
        by_emd: dict = defaultdict(list)
        for r in yr:
            by_emd[r.x.get("_emd", r.complex_id)].append(r.x["emd_lead_months"])
        leads = [r.x["emd_lead_months"] for r in yr]
        q84, q50, q16 = percentile(leads, 0.84), percentile(leads, 0.50), percentile(leads, 0.16)
        cnt = Counter(); ret: dict = defaultdict(list)
        for r in yr:
            c = classify(r.x["emd_lead_months"], q84, q50, q16)
            cnt[c] += 1
            cls_of[(str(r.complex_id) + r.band, y)] = c
            if r.target is not None:
                ret[c].append(r.target - lvl[y])
        n = len(yr)
        out["years"][y] = {"n": n, "q16_50_84": [q16, q50, q84], "share": {c: round(cnt[c] / n, 3) for c in cnt},
                           "metro_mom1": yr[0].x.get("metro_mom1"),
                           "rel_return_median": {c: round(median(v), 4) for c, v in ret.items() if len(v) >= 30}}
    # 계급 지속성: t 년 계급 vs t+1 년 계급 (같은 단지×면적)
    years = sorted(by_y)
    stay = Counter(); tot = Counter()
    for (key, y), c in cls_of.items():
        ny = f"{int(y[:4]) + 1}06"
        c2 = cls_of.get((key, ny))
        if c2:
            tot[c] += 1
            if c2 == c:
                stay[c] += 1
    out["persistence"] = {c: {"n": tot[c], "stay_next_year": round(stay[c] / tot[c], 3)} for c in tot}
    # 사이클 국면별 계급 수익: 수도권 1년 모멘텀 중앙값 기준 상/하
    mm = {y: v["metro_mom1"] for y, v in out["years"].items() if v.get("metro_mom1") is not None}
    if mm:
        split = median(list(mm.values()))
        for phase, cond in (("상승국면(수도권 1y 모멘텀 상위 절반)", lambda v: v >= split), ("정체·하락국면(하위 절반)", lambda v: v < split)):
            agg: dict = defaultdict(list)
            for y, v in out["years"].items():
                if y in mm and cond(mm[y]):
                    for c, rr in v["rel_return_median"].items():
                        agg[c].append(rr)
            out["class_returns_by_cycle"][phase] = {c: round(median(v), 4) for c, v in agg.items()}
        out["class_returns_by_cycle"]["split_metro_mom1"] = round(split, 4)
    (ROOT / "reports" / "diffusion_classes.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("persistence", "class_returns_by_cycle")}, ensure_ascii=False, indent=1))
    for y, v in out["years"].items():
        print(y, "비중", v["share"], "상대수익", v["rel_return_median"], "mm1", v["metro_mom1"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
