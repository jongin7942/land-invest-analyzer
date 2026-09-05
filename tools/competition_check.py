"""청약경쟁률(시군구·월, 2020-02~)이 이후 시군구 가격에 앞서는가 — 짧은 자료라 12개월 상대수익으로 본다.

각 (시군구, 월) 의 1순위 해당지역 경쟁률(공급 가중, 직전 6개월 합산)과 그 뒤 12개월 시군구 중앙가 변화 − 수도권 변화 의 Spearman.
또 같은 시점 시군구의 직전 12개월 상대수익과의 상관(경쟁률이 이미 오른 곳을 따라가는지) 도 같이 본다.
출력: reports/competition_check.json
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.exitprice.model import spearman  # noqa: E402
from apt_engine.exitprice.panel import ym_idx  # noqa: E402
from apt_engine.relative import store  # noqa: E402
from apt_engine.relative.store import median  # noqa: E402


def ym_shift(ym, k):
    y, m = int(ym[:4]), int(ym[4:6]); m -= k
    while m <= 0:
        y -= 1; m += 12
    while m > 12:
        y += 1; m -= 12
    return f"{y}{m:02d}"


def main() -> int:
    with get_conn() as conn:
        cx = store.load_complexes(conn)
        prices = store.load_prices(conn, cx, ("84", "59", "74"))
    # 시군구 월 지수(log ㎡단가 중앙값), 시 코드(4자리+0)도 함께
    by_code = defaultdict(list)
    for (cid, band), s in prices.items():
        c = cx[cid]
        by_code[c.lawd_cd].append(s); by_code[c.lawd_cd[:4] + "0"].append(s)
    def level(code, t):
        vals = [math.log(s.p50[t] / store.BAND_M2[b]) for s, b in ((s, k[1]) for k, s in prices.items() if cx[k[0]].lawd_cd == code or cx[k[0]].lawd_cd[:4] + "0" == code) if s.p50[t]]
        return median(vals) if len(vals) >= 3 else None
    def metro(t):
        vals = [math.log(s.p50[t] / store.BAND_M2[k[1]]) for k, s in prices.items() if s.p50[t]]
        return median(vals) if len(vals) >= 50 else None
    cm = defaultdict(dict)
    for r in csv.DictReader((ROOT / "rules" / "applyhome_competition_sigungu_monthly.csv").open(encoding="utf-8")):
        cm[r["lawd_cd"]][r["ym"]] = (int(r["supply_hh"]), int(r["req_cnt"]))
    pairs_fwd, pairs_back = [], []
    rows = []
    for code, months in cm.items():
        for ym in months:
            sup = req = 0
            for k in range(6):
                v = months.get(ym_shift(ym, k))
                if v:
                    sup += v[0]; req += v[1]
            if sup < 50:
                continue
            rate = req / sup
            t = ym_idx(ym)
            if t + 12 >= store.N_MONTHS or t - 12 < 0:
                continue
            l0, l1, lb = level(code, t), level(code, t + 12), level(code, t - 12)
            m0, m1, mb = metro(t), metro(t + 12), metro(t - 12)
            if None in (l0, l1, lb, m0, m1, mb):
                continue
            fwd = (l1 - l0) - (m1 - m0); back = (l0 - lb) - (m0 - mb)
            pairs_fwd.append((math.log1p(rate), fwd)); pairs_back.append((math.log1p(rate), back))
            rows.append({"code": code, "ym": ym, "rate6m": round(rate, 2), "fwd12_rel": round(fwd, 4), "back12_rel": round(back, 4)})
    out = {"n": len(rows),
           "spearman_rate_vs_fwd12_rel": round(spearman([a for a, _ in pairs_fwd], [b for _, b in pairs_fwd]), 3) if len(rows) >= 10 else None,
           "spearman_rate_vs_back12_rel": round(spearman([a for a, _ in pairs_back], [b for _, b in pairs_back]), 3) if len(rows) >= 10 else None}
    srt = sorted(rows, key=lambda r: r["rate6m"])
    if len(srt) >= 12:
        q = len(srt) // 4
        out["quartiles"] = [{"rate_range": [srt[i * q]["rate6m"], srt[min(len(srt) - 1, (i + 1) * q - 1)]["rate6m"]],
                             "fwd12_rel_median": round(median([r["fwd12_rel"] for r in srt[i * q:(i + 1) * q if i < 3 else len(srt)]]), 4),
                             "back12_rel_median": round(median([r["back12_rel"] for r in srt[i * q:(i + 1) * q if i < 3 else len(srt)]]), 4)} for i in range(4)]
    out["sample"] = rows[:5]
    (ROOT / "reports" / "competition_check.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
