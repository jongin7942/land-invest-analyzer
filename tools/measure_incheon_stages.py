"""인천 재건축 단계 인가일 전후 — 자치구 대비 상대가격 변화. 서울·경기와 같은 자.

fetch_incheon_stages.py 가 만든 표(대표지번 + 단계별 날짜)를 우리 단지에 붙인다.
붙이는 규칙은 서울과 같다: 대표지번을 지오코딩해 300m 안 단지 중 이름이 겹치는 것,
재건축이면 150m 안 가장 가까운 아파트도 허용(재개발은 빌라촌이라 안 함).
"""
from __future__ import annotations

import csv
import json
import math
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_redev_stages import (DATA_START_YM, WINDOW_MONTHS, main_band,  # noqa: E402
                                  relative_at, shift_ym)

from apt_engine.collectors import geocode as geo_mod  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "rules" / "incheon_redev_stages.csv"
GEO = ROOT / "logs" / "_incheon_geocode.json"
OUT_MATCH = ROOT / "rules" / "incheon_redev_matched.csv"
OUT = ROOT / "rules" / "redev_stage_effect_incheon.csv"
STAGES = ["정비구역지정", "추진위", "조합설립", "사업시행인가", "관리처분", "착공", "준공"]
NOISE = ("아파트", "주공", "단지", "구역", "지구", "재건축", "재개발", "주택", "일원", "번지", "및")


def hav(a, b, c, d):
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def tokens(s):
    s = re.sub(r"[^0-9가-힣A-Za-z]", " ", s or "")
    for w in NOISE:
        s = s.replace(w, " ")
    return {t for t in s.split() if len(t) >= 2}


def clean_addr(jibun: str, gu: str) -> str:
    s = re.sub(r"\(.*?\)", " ", jibun or "")
    s = re.sub(r"(일원|일대|번지|외\s*\d+\s*필지|외\s*\d+)", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return f"인천광역시 {gu} {s}".strip() if s else ""


def main() -> int:
    rows = list(csv.DictReader(SRC.open(encoding="utf-8")))
    geo = json.loads(GEO.read_text(encoding="utf-8")) if GEO.exists() else {}
    with get_conn() as conn:
        cx = [(r["id"], r["name"], r["lat"], r["lon"], r["lawd_cd"]) for r in conn.execute(
            "SELECT id, name, lat, lon, lawd_cd FROM complex WHERE lat IS NOT NULL AND lawd_cd LIKE '28%'")]
        matched, stats = [], defaultdict(int)
        for r in rows:
            addr = clean_addr(r["jibun"], r["gu"])
            if not addr:
                stats["주소없음"] += 1
                continue
            if addr not in geo:
                try:
                    geo[addr] = geo_mod.geocode(addr)
                except geo_mod.GeocodeError as e:
                    print("지오코딩 중단:", e)
                    break
                time.sleep(0.05)
            pt = geo[addr]
            if not pt:
                stats["지오코딩실패"] += 1
                continue
            lat, lon = pt
            near = [(cid, nm, hav(lat, lon, la, lo), lawd) for cid, nm, la, lo, lawd in cx
                    if hav(lat, lon, la, lo) <= 300]
            if not near:
                stats["근처단지없음"] += 1
                continue
            want = tokens(r["zone"])
            hits = [x for x in near if want & tokens(x[1])]
            if not hits and "재건축" in r["biz_type"]:
                hits = [x for x in near if x[2] <= 150]
            if not hits:
                stats["이름불일치"] += 1
                continue
            cid, nm, dist, lawd = min(hits, key=lambda x: x[2])
            matched.append({**r, "complex_id": cid, "complex_name": nm, "lawd_cd": lawd,
                            "meters": round(dist, 1)})
        GEO.parent.mkdir(parents=True, exist_ok=True)
        GEO.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
        if matched:
            with OUT_MATCH.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(matched[0].keys()))
                w.writeheader()
                w.writerows(matched)
        print(f"인천 사업장 {len(rows)}건 · 단지에 붙음 {len(matched)}건 · " +
              " · ".join(f"{k} {v}" for k, v in stats.items()))

        results = defaultdict(list)
        skipped = defaultdict(int)
        for m in matched:
            cid, lawd = m["complex_id"], m["lawd_cd"]
            band = main_band(conn, cid)
            if not band:
                skipped["가격 스냅샷 없음"] += 1
                continue
            for stage in STAGES:
                day = m.get(stage, "")
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
                results[stage].append((b - a, m["complex_name"], day))

    print("\n인천 · 단계 인가 전후 12개월 · 자치구 대비 상대가격 변화")
    print(f"  {'단계':12s} {'사례':>4s} {'중앙값':>8s} {'상승':>5s}")
    table = []
    for stage in STAGES:
        v = sorted(x[0] for x in results[stage])
        if len(v) < 5:
            print(f"  {stage:12s} {len(v):4d}  표본 5건 미만")
            continue
        med = statistics.median(v)
        pos = sum(1 for x in v if x > 0) / len(v)
        print(f"  {stage:12s} {len(v):4d} {med:+8.2%} {pos:5.0%}")
        table.append({"stage": stage, "samples": len(v), "median_delta": round(med, 6),
                      "positive_ratio": round(pos, 4), "window_months": WINDOW_MONTHS})
    if table:
        with OUT.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
            w.writeheader()
            w.writerows(table)
    print("\n못 잰 사유:", dict(sorted(skipped.items(), key=lambda kv: -kv[1])[:6]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
