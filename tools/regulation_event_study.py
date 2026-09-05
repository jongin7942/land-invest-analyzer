"""규제지역 사건연구 — 지정·해제 뒤 상대수익과 풍선효과 (종인님 지시 2026-09-05).

walk-forward 로는 검증이 안 되는(학습구간에 규제 변동 없음) 규제 변수를, 사건 시점 기준으로 본다.
사건: 조정대상지역·투기과열지구 지정/해제 일자(rules/regulation_zone_expanded.csv 의 effective_from/to).
각 사건 D 에서 단지를 네 집단으로 나눈다:
  NEW      이번에 새로 지정된 시군구
  ALREADY  이전부터 규제 중
  BALLOON  같은 시도 안의 비규제 시군구(풍선효과 후보)
  OTHER    그 외 비규제
각 집단의 D 이후 6/12/24/36개월 log 수익률(단지 6월 대표가 → 월 p50 사용)에서 수도권 중앙값을 뺀 상대수익의 중앙값을 낸다.
결과: reports/regulation_event_study.json
    .venv/Scripts/python.exe tools/regulation_event_study.py
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice import panel as panel_mod  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402

EVENTS = [  # (날짜, 설명)
    ("20161103", "11.3 조정대상지역 최초(서울·과천·성남·하남·고양·남양주·동탄2)"),
    ("20170803", "8.2 투기과열지구(서울·과천)·투기지역 11구"),
    ("20180828", "8.27 구리·안양동안·광교 조정 / 광명·하남 투기과열"),
    ("20181231", "12.28 수원팔달·용인수지·기흥 조정"),
    ("20200221", "2.20 수원 영통·권선·장안, 안양만안, 의왕 조정"),
    ("20200619", "6.17 경기·인천 대부분 조정 / 투기과열 확대"),
    ("20201120", "11.19 김포 조정"),
    ("20220926", "9.21 해제(안성·평택·동두천·양주·파주 조정 / 인천 3구 투기과열)"),
    ("20221114", "11.10 해제(서울·과천·성남·하남·광명 외 전부)"),
    ("20230105", "1.3 해제(강남3구·용산 외 서울 전부)"),
]
H = [6, 12, 24, 36]


def ym_of(ymd: str) -> str:
    return ymd[:6]


def main() -> int:
    with get_conn() as conn:
        cx = store.load_complexes(conn)
        prices = store.load_prices(conn, cx, ("84", "59", "74"))
    panel_mod.load_regulation()
    # 월별 수도권 지수(log ㎡단가 중앙값) — 상대수익 기준
    def metro_level(t):
        vals = [math.log(s.p50[t] / store.BAND_M2[k[1]]) for k, s in prices.items() if s.p50[t]]
        return median(vals) if len(vals) >= 50 else None
    out = {"events": []}
    for ymd, desc in EVENTS:
        t0 = panel_mod.ym_idx(ym_of(ymd))
        if t0 < 6 or t0 >= store.N_MONTHS:
            continue
        before = f"{ymd[:6]}01"; after = f"{ymd[:6]}28"
        # 사건 전/후 상태로 집단 분류 (시군구 단위)
        groups = {"NEW": [], "ALREADY": [], "RELEASED": [], "BALLOON": [], "OTHER": []}
        st_after = {c.id: panel_mod.reg_status(c.lawd_cd, c.emd, after) for c in cx.values()}
        st_before = {c.id: panel_mod.reg_status(c.lawd_cd, c.emd, before) for c in cx.values()}
        # 시도별 규제 비중(after)
        share = {}
        for sd in ("11", "41", "28"):
            ids = [c for c in cx.values() if c.lawd_cd[:2] == sd]
            share[sd] = sum(1 for c in ids if st_after[c.id]["adj"] or st_after[c.id]["hot"]) / max(1, len(ids))
        for c in cx.values():
            b = st_before[c.id]["adj"] or st_before[c.id]["hot"]; a = st_after[c.id]["adj"] or st_after[c.id]["hot"]
            if a and not b: g = "NEW"
            elif a and b: g = "ALREADY"
            elif b and not a: g = "RELEASED"
            elif share[c.lawd_cd[:2]] >= 0.3: g = "BALLOON"
            else: g = "OTHER"
            groups[g].append(c.id)
        res = {"date": ymd, "desc": desc, "n": {g: len(v) for g, v in groups.items()}, "rel_return_median": {}}
        for h in H:
            t1 = t0 + h
            if t1 >= store.N_MONTHS:
                continue
            m0, m1 = metro_level(t0), metro_level(t1)
            if m0 is None or m1 is None:
                continue
            mk = m1 - m0
            for g, ids in groups.items():
                vals = []
                for (cid, band), s in prices.items():
                    if cid in set(ids) and s.p50[t0] and s.p50[t1]:
                        vals.append(math.log(s.p50[t1] / s.p50[t0]) - mk)
                if len(vals) >= 10:
                    res["rel_return_median"].setdefault(str(h), {})[g] = {"median": round(median(vals), 4), "n": len(vals)}
            res["rel_return_median"].setdefault(str(h), {})["market_log"] = round(mk, 4)
        out["events"].append(res)
        print(ymd, desc, res["n"], {h: {g: v["median"] for g, v in d.items() if isinstance(v, dict)} for h, d in res["rel_return_median"].items()}, flush=True)
    (ROOT / "reports" / "regulation_event_study.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
