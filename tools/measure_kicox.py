"""산업단지 지정 전후 — 근처 아파트가 같은 시군구의 먼 아파트보다 움직였나.

교통(개통 117건)·재건축과 같은 자다. 산업단지는 역과 달리 '한 점' 이 아니라
수십만 ㎡ 면적이므로 반경을 넓게 잡는다(근처 = 3km 안, 대조군 = 같은 시군구
3km 밖). 좌표는 카카오 키워드 검색(단지명)으로 얻는다 — V-World 지오코더는
주소만 받아서 '○○일반산업단지' 를 못 찾는다.

표본은 KICOX 분기별 파일(2018~2025)의 신규지정 부록에서 나온 수도권 52건이고,
그중 지정 후 12개월이 지난 것만 잴 수 있다. 얇다. 이 결과로 순위를 매기지
말고, 방향만 본다.
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_redev_stages import DATA_START_YM, WINDOW_MONTHS, shift_ym  # noqa: E402

from apt_engine.db.connection import get_conn  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "rules" / "kicox_designations.csv"
CACHE = ROOT / "logs" / "_kicox_geocode.json"
NEAR_M, FAR_MIN_M = 3000, 3000
MIN_SAMPLES, TOL = 5, 3
AREA_BAND = "84"


def kakao_key() -> str:
    """이 저장소의 카카오 키는 Local API 에 403 이 난다(앱 설정 문제로 보임).
    약국 앱(눈뜬개국)의 키는 되므로 그쪽을 먼저 쓴다 — 종인님이 두 프로젝트 키를
    같이 써도 된다고 했다."""
    candidates = [
        (ROOT.parent / "projects" / "pharmacy-hogang" / ".env", ("GEOCODING_API_KEY", "KAKAO_REST_API_KEY")),
        (ROOT / ".env", ("KAKAO_REST_API_KEY",)),
    ]
    for path, names in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            for n in names:
                if line.startswith(n + "="):
                    return line.split("=", 1)[1].strip()
    raise SystemExit("카카오 REST 키를 못 찾았습니다")


def kakao_search(q: str, key: str):
    url = "https://dapi.kakao.com/v2/local/search/keyword.json?" + urllib.parse.urlencode({"query": q, "size": 3})
    req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        docs = json.load(r).get("documents", [])
    return (float(docs[0]["y"]), float(docs[0]["x"]), docs[0].get("address_name", "")) if docs else None


def hav(a, b, c, d):
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def prices(conn, ids, ym):
    lo, hi = shift_ym(ym, -TOL), shift_ym(ym, TOL)
    out = {}
    for cid in ids:
        row = conn.execute(
            "SELECT representative_price p FROM price_snapshot WHERE complex_id=? AND area_band=? "
            "  AND as_of_ym BETWEEN ? AND ? ORDER BY ABS(CAST(as_of_ym AS INTEGER)-?) LIMIT 1",
            (cid, AREA_BAND, lo, hi, int(ym))).fetchone()
        if row:
            out[cid] = row["p"]
    return out


def ratio(conn, near, far, ym):
    n, f = prices(conn, near, ym), prices(conn, far, ym)
    return (n, f)


def main() -> int:
    rows = [r for r in csv.DictReader(SRC.open(encoding="utf-8")) if r["sido"] in ("서울", "경기", "인천")]
    key = kakao_key()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    sido_full = {"서울": "서울", "경기": "경기", "인천": "인천"}
    results, skipped = [], defaultdict(int)
    with get_conn() as conn:
        cx = conn.execute("SELECT id, lat, lon, lawd_cd FROM complex WHERE lat IS NOT NULL").fetchall()
        for r in rows:
            q = f"{sido_full[r['sido']]} {r['sigungu']} {r['name']}"
            if q not in cache:
                cache[q] = kakao_search(q, key) or kakao_search(r["name"], key)
                time.sleep(0.1)
            pt = cache[q]
            if not pt:
                skipped["좌표 못 찾음"] += 1
                continue
            lat, lon = pt[0], pt[1]
            near = [c for c in cx if hav(lat, lon, c["lat"], c["lon"]) <= NEAR_M]
            if len(near) < MIN_SAMPLES:
                skipped["근처 단지 부족"] += 1
                continue
            lawds = {c["lawd_cd"] for c in near}
            far = [c for c in cx if c["lawd_cd"] in lawds and hav(lat, lon, c["lat"], c["lon"]) > FAR_MIN_M]
            if len(far) < MIN_SAMPLES:
                skipped["대조군 부족"] += 1
                continue
            ym = r["designated"][:7].replace("-", "")
            before, after = shift_ym(ym, -WINDOW_MONTHS), shift_ym(ym, WINDOW_MONTHS)
            if before < DATA_START_YM:
                skipped["데이터 이전"] += 1
                continue
            nb, fb = ratio(conn, [c["id"] for c in near], [c["id"] for c in far], before)
            na, fa = ratio(conn, [c["id"] for c in near], [c["id"] for c in far], after)
            nn = set(nb) & set(na)
            ff = set(fb) & set(fa)
            if len(nn) < MIN_SAMPLES or len(ff) < MIN_SAMPLES:
                skipped["전후 둘 다 있는 단지 부족(아직 12개월 안 지났거나)"] += 1
                continue
            rb = statistics.median(nb[c] for c in nn) / statistics.median(fb[c] for c in ff)
            ra = statistics.median(na[c] for c in nn) / statistics.median(fa[c] for c in ff)
            results.append((ra - rb, r["name"], r["designated"], r["kind"], len(nn), len(ff)))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    print(f"수도권 신규지정 {len(rows)}건 · 측정된 사례 {len(results)}건\n")
    if results:
        v = sorted(x[0] for x in results)
        pos = sum(1 for x in v if x > 0)
        print(f"  지정 ±{WINDOW_MONTHS}개월 · 근처(3km)/먼곳 가격비율 변화")
        print(f"  중앙값 {statistics.median(v):+.2%} · 상승 {pos}/{len(v)} ({pos/len(v):.0%}) · 범위 {v[0]:+.2%} ~ {v[-1]:+.2%}\n")
        for d, nm, day, kind, n1, n2 in sorted(results, key=lambda x: -x[0]):
            print(f"   {d:+7.2%}  {day}  {kind:4s} {nm[:26]:28s} 근처{n1:3d} 먼곳{n2:3d}")
    print("\n못 잰 사유:")
    for k, n in sorted(skipped.items(), key=lambda kv: -kv[1]):
        print(f"   {k:44s} {n:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
