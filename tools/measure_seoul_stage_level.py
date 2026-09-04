"""서울 정비사업 1,158건 — 단계가 앞선 단지가 실제로 더 비싼가.

── 이건 전후 비교가 아니다 ─────────────────────────────────────────
경기도 자료에는 단계별 인가일이 있어서 '인가 전후 12개월' 을 비교할 수 있었다.
서울 목록에는 **현재 단계만** 있고 날짜가 없다(날짜는 조합 정보공개 카페 안이라
사업장마다 따로 들어가야 한다). 그래서 여기서는 다른 질문을 던진다.

    전후 비교(경기)  같은 단지가 단계를 밟으면 올랐나          — 인과에 가깝다
    수준 비교(서울)  단계가 앞선 단지가 지금 더 비싼가        — 상관이다

수준 비교는 인과가 아니다. 비싼 동네가 사업이 잘 굴러가기도 하고(역인과),
애초에 사업성 좋은 단지가 먼저 단계를 밟기도 한다(선택 편향). **이 값을
'단계를 밟으면 이만큼 오른다' 로 읽으면 안 된다.**

그런데도 재는 이유는, 경기 전후 비교의 표본이 13~21건으로 너무 얇아서
어느 방향으로도 못 읽기 때문이다. 표본이 큰 수준 비교와 방향이 맞으면
그만큼 덜 의심스럽고, 어긋나면 그 자체가 볼 만한 신호다.

── 재는 방법 ────────────────────────────────────────────────────────
    그 단지의 대표가격 ÷ 같은 자치구 같은 면적대 중앙값 = 상대가격

단계별로 이 상대가격의 중앙값을 낸다. 자치구로 나누므로 강남/도봉 같은
지역 차이는 지워지고, 같은 동네 안에서의 위치만 남는다.
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

import xlrd  # noqa: E402

from apt_engine.collectors import geocode as geo_mod  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "logs" / "_seoul_redev_list.xls"
CACHE = Path(__file__).resolve().parents[1] / "logs" / "_seoul_redev_geocode.json"
OUT = Path(__file__).resolve().parents[1] / "rules" / "seoul_redev_matched.csv"

MATCH_RADIUS_M = 300
MIN_PEERS = 5
RECENT_FROM = "202501"

# 앞으로 갈수록 사업이 확정에 가깝다. 순서가 곧 진행도다.
STAGE_ORDER = [
    "정비계획 수립", "안전진단", "정비구역지정", "추진위원회승인",
    "조합설립인가", "사업시행인가", "관리처분인가", "철거", "착공",
    "분양", "준공인가", "이전고시", "조합해산", "조합청산",
]

NOISE = ("아파트", "주공", "단지", "구역", "지구", "재건축", "재개발",
         "정비사업", "조합", "주택", "일원", "및", "통합", "소규모")


def hav(a, b, c, d) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = p2 - p1, math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def tokens(name: str) -> set[str]:
    s = re.sub(r"[^0-9가-힣A-Za-z]", " ", name or "")
    for w in NOISE:
        s = s.replace(w, " ")
    return {t for t in s.split() if len(t) >= 2}


def read_rows() -> list[dict]:
    ws = xlrd.open_workbook(SRC).sheet_by_index(0)
    head = [str(ws.cell_value(1, c)).strip() for c in range(ws.ncols)]
    out = []
    for r in range(2, ws.nrows):
        row = {head[c]: str(ws.cell_value(r, c)).strip() for c in range(ws.ncols)}
        if row.get("사업장명"):
            out.append(row)
    return out


def main() -> int:
    rows = read_rows()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    print(f"서울 정비사업 {len(rows):,}건\n")

    with get_conn() as conn:
        complexes = [(r["id"], r["name"], r["lat"], r["lon"], r["lawd_cd"])
                     for r in conn.execute(
            "SELECT id, name, lat, lon, lawd_cd FROM complex "
            " WHERE lat IS NOT NULL AND lawd_cd LIKE '11%'")]
    print(f"서울 단지 {len(complexes):,}개\n")

    matched, stats = [], defaultdict(int)
    for i, r in enumerate(rows, 1):
        gu, jibun = r.get("자치구", ""), r.get("대표지번", "")
        if not jibun or jibun in ("-", "nan"):
            stats["지번없음"] += 1
            continue
        addr = f"서울특별시 {gu} {jibun}".strip()

        if addr in cache:
            pt = cache[addr]
        else:
            try:
                pt = geo_mod.geocode(addr)
            except geo_mod.GeocodeError as e:
                print(f"  [{i}] {e}")
                return 1
            cache[addr] = pt
            time.sleep(0.05)
        if i % 100 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(rows)} · 붙음 {len(matched)}")
        if not pt:
            stats["지오코딩실패"] += 1
            continue

        lat, lon = pt
        near = [(cid, nm, hav(lat, lon, cl, co), lawd)
                for cid, nm, cl, co, lawd in complexes
                if hav(lat, lon, cl, co) <= MATCH_RADIUS_M]
        if not near:
            stats["근처단지없음"] += 1
            continue
        want = tokens(r.get("사업장명", ""))
        hits = [x for x in near if want & tokens(x[1])]
        if not hits:
            stats["이름불일치"] += 1
            continue
        cid, nm, dist, lawd = min(hits, key=lambda x: x[2])
        matched.append({"complex_id": cid, "complex_name": nm, "lawd_cd": lawd,
                        "gu": gu, "project": r.get("사업장명", ""),
                        "biz_type": r.get("사업구분", ""),
                        "stage": r.get("진행단계", ""),
                        "jibun": jibun, "meters": round(dist, 1)})

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    if matched:
        with OUT.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(matched[0].keys()))
            w.writeheader()
            w.writerows(matched)
    print(f"\n붙은 사업 {len(matched)}건 → {OUT}")
    for k, v in stats.items():
        print(f"   {k:14s} {v:4d}")

    # ── 단계별 상대가격 수준 ─────────────────────────────────────
    by_stage: dict[str, list[float]] = defaultdict(list)
    with get_conn() as conn:
        for m in matched:
            cid, lawd = m["complex_id"], m["lawd_cd"]
            band = conn.execute(
                "SELECT area_band FROM price_snapshot WHERE complex_id = ? "
                "  AND as_of_ym >= ? GROUP BY area_band ORDER BY COUNT(*) DESC LIMIT 1",
                (cid, RECENT_FROM)).fetchone()
            if not band:
                continue
            band = band["area_band"]
            mine = conn.execute(
                "SELECT MAX(representative_price) p FROM price_snapshot "
                " WHERE complex_id = ? AND area_band = ? AND as_of_ym >= ?",
                (cid, band, RECENT_FROM)).fetchone()["p"]
            peers = [r["p"] for r in conn.execute(
                "SELECT MAX(ps.representative_price) p FROM price_snapshot ps "
                "  JOIN complex c ON c.id = ps.complex_id "
                " WHERE c.lawd_cd = ? AND ps.area_band = ? AND ps.as_of_ym >= ? "
                "   AND ps.complex_id <> ? GROUP BY ps.complex_id",
                (lawd, band, RECENT_FROM, cid))]
            if not mine or len(peers) < MIN_PEERS:
                continue
            by_stage[m["stage"]].append(mine / statistics.median(peers))

    print("\n" + "═" * 66)
    print("현재 단계별 · 같은 자치구 같은 면적대 중앙값 대비 상대가격")
    print("  ※ 상관입니다. '단계를 밟으면 오른다' 로 읽으면 안 됩니다.")
    print("═" * 66)
    print(f"  {'단계':16s} {'단지':>4s}  {'중앙값':>8s}")
    print("  " + "─" * 40)
    for st in STAGE_ORDER:
        v = by_stage.get(st) or []
        if len(v) < 5:
            continue
        print(f"  {st:16s} {len(v):4d}  {statistics.median(v):8.3f}")
    other = {k: v for k, v in by_stage.items() if k not in STAGE_ORDER and len(v) >= 5}
    for k, v in other.items():
        print(f"  {k:16s} {len(v):4d}  {statistics.median(v):8.3f}  (순서 미상)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
