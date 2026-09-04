"""경기도 정비사업 533건을 우리 단지에 붙인다.

── 왜 붙여야 하나 ──────────────────────────────────────────────────
"재건축이 진행되면 값이 오른다" 를 재려면, 사업 하나하나가 **어느 단지**인지
알아야 한다. 그래야 그 단지의 가격 흐름을 단계 날짜와 맞대볼 수 있다.

경기도 자료에는 정비구역명(예: 원당주공2단지)과 소재지 주소가 있다. 주소를
좌표로 바꾸고, 그 근처에 있는 우리 단지 중 이름이 겹치는 것을 고른다.

── 붙이는 규칙과, 붙이지 않는 경우 ─────────────────────────────────
1. 주소를 지오코딩한다. 실패하면 그 사업은 버린다 — 좌표를 지어내지 않는다.
2. 반경 400m 안의 단지만 후보로 본다. 정비구역은 넓지만, 주소는 구역의 대표
   지번이라 대상 단지가 그 근처에 있다.
3. 후보 중 **이름이 겹치거나 세대수가 맞는** 것을 고른다.

거리만으로 가장 가까운 단지를 고르지 않는다. 재개발 구역은 빌라촌이라 근처
아파트가 대상이 아닌 경우가 흔하고, 그걸 붙이면 '재건축 안 하는 단지' 를
재건축 사례로 세는 셈이 된다. 측정을 망치는 가장 확실한 길이다.

── 세대수를 같이 보는 이유 ─────────────────────────────────────────
정비구역명은 행정 이름이고(주공7-2단지, 금곡2, 오남1) 단지 이름은 상품
이름이라(과천래미안센트럴스위트) 글자가 안 겹치는 일이 흔하다. 그런데
**(기존주택)세대수는 그 단지의 세대수 그대로**다. 400세대 구역 옆에 400세대
단지가 있으면 같은 것으로 보아도 좋다. 이름과 세대수 중 하나만 맞아도 붙이되,
어느 쪽으로 붙었는지 match_by 에 남긴다.

── 준공된 사업은 대개 안 붙는다. 그게 맞다 ────────────────────────
준공되면 대상 단지가 헐리고 그 자리에 이름이 다른 새 아파트가 선다. 우리
complex 표는 현재 존재하는 단지만 담으므로 옛 단지는 아예 없다. 그래서 이
매칭은 아직 안 헐린 사업(예정구역~착공)에서 주로 붙는다.

그게 우리가 재려는 것과 정확히 맞다 — **지금 낡은 단지를 샀을 때, 재건축이
진행되면 값이 오르는가**. 헐린 뒤의 신축 값은 다른 질문이다.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apt_engine.collectors import geocode as geo_mod  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "rules" / "gg_redev_progress.csv"
OUT = Path(__file__).resolve().parents[1] / "rules" / "gg_redev_matched.csv"
CACHE = Path(__file__).resolve().parents[1] / "logs" / "_gg_redev_geocode.json"

MATCH_RADIUS_M = 400

# 이름 비교에서 뺀다 — 다 붙어 있어서 겹쳐도 아무 뜻이 없다.
NOISE = ("아파트", "주공", "단지", "구역", "지구", "재건축", "재개발", "일원",
         "차", "동", "번지", "외", "및")

STAGE_COLS = [
    ("정비구역지정일자(최초지정)", "정비구역지정"),
    ("추진위승인일자", "추진위"),
    ("안전진단일자", "안전진단"),
    ("조합설립인가일자", "조합설립"),
    ("사업시행인가일자", "사업시행인가"),
    ("관리처분인가일자", "관리처분"),
    ("착공일자", "착공"),
    ("준공일자", "준공"),
]


def hav(a, b, c, d) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = p2 - p1, math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def tokens(name: str) -> set[str]:
    """이름에서 비교에 쓸 조각. 흔한 말과 한 글자는 뺀다."""
    s = re.sub(r"[^0-9가-힣A-Za-z]", " ", name or "")
    for w in NOISE:
        s = s.replace(w, " ")
    return {t for t in s.split() if len(t) >= 2}


def addr_variants(addr: str) -> list[str]:
    """지오코딩에 넣어볼 주소 후보들.

    한 번 넣어보고 실패하면 버리기엔 아까운 주소가 많았다(533건 중 129건 실패).
    실패한 것들을 보니 패턴이 있었다.

        경기도 고양시 성사동 715번지        고양시는 구가 있어야 한다
        경기도 광명시 철산2동 235           행정동이라 법정동으로 안 잡힌다
        경기도 부천시 중동 884외3 (현, ...)  괄호와 '외3' 이 붙어 있다

    그래서 원문 → 괄호 제거 → '번지' 제거 → 행정동 숫자 제거 순으로 넓혀가며
    시도한다. 넓힌 주소가 엉뚱한 곳을 가리킬 위험은 뒤의 400m·이름·세대수
    확인이 막는다.
    """
    raw = (addr or "").strip()
    if not raw:
        return []
    out, seen = [], set()

    def add(s: str) -> None:
        s = re.sub(r"\s+", " ", s).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    add(raw)
    no_paren = re.sub(r"\(.*?\)", " ", raw)
    add(no_paren)
    base = re.sub(r"(일원|일대|번지|외\s*\d+\s*필지|외\s*\d+)", " ", no_paren)
    add(base)
    # '철산2동' 처럼 행정동에 붙은 숫자를 떼면 법정동이 되는 경우가 많다.
    add(re.sub(r'([가-힣]{2,})\d+(동)', r'\g<1>\g<2>', base))
    # 지번 뒤의 부번(-12)까지 떼고 본번만 남긴다.
    add(re.sub(r'(\d+)-\d+\s*$', r'\g<1>', base))
    return out[:5]


def clean_addr(addr: str) -> str:
    v = addr_variants(addr)
    return v[0] if v else ""


def households_of(text: str) -> int | None:
    s = re.sub(r"[^0-9]", "", text or "")
    return int(s) if s else None


def load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def main() -> int:
    rows = list(csv.DictReader(SRC.open(encoding="utf-8")))
    cache = load_cache()
    CACHE.parent.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        complexes = [(r["id"], r["name"], r["lat"], r["lon"], r["lawd_cd"],
                      r["apt_households"])
                     for r in conn.execute(
            "SELECT id, name, lat, lon, lawd_cd, apt_households FROM complex "
            " WHERE lat IS NOT NULL AND lawd_cd LIKE '41%'")]
    print(f"경기도 단지 {len(complexes):,}개 · 정비사업 {len(rows)}건\n")

    out, stats = [], {"주소없음": 0, "지오코딩실패": 0, "근처없음": 0,
                      "이름·세대수 불일치": 0, "붙음": 0, "단계없음": 0}
    by_reason = {"이름": 0, "세대수": 0, "이름+세대수": 0}

    for i, r in enumerate(rows, 1):
        variants = [v for v in addr_variants(r.get("위치") or "")
                    if "nan" not in v]
        if not variants:
            stats["주소없음"] += 1
            continue

        pt, used = None, None
        for v in variants:
            if v in cache:
                pt = cache[v]
            else:
                try:
                    pt = geo_mod.geocode(v)
                except geo_mod.GeocodeError as e:
                    print(f"  [{i}] {e}")
                    return 1
                cache[v] = pt
                time.sleep(0.05)
            if pt:
                used = v
                break
        if i % 40 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(rows)} · 붙음 {stats['붙음']}")
        if not pt:
            stats["지오코딩실패"] += 1
            continue

        lat, lon = pt
        near = [(cid, nm, hav(lat, lon, clat, clon), lawd, hh)
                for cid, nm, clat, clon, lawd, hh in complexes
                if hav(lat, lon, clat, clon) <= MATCH_RADIUS_M]
        if not near:
            stats["근처없음"] += 1
            continue

        # 이름이 겹치거나(행정 이름 ↔ 상품 이름), 세대수가 맞거나.
        want = tokens(r.get("정비구역명") or "")
        zone_hh = households_of(r.get("(기존주택)세대수", ""))
        hits = []
        for cid, nm, d, lawd, hh in near:
            by_name = bool(want & tokens(nm))
            by_hh = bool(zone_hh and hh and abs(hh - zone_hh) <= max(5, zone_hh * 0.02))
            if by_name or by_hh:
                why = "이름+세대수" if (by_name and by_hh) else ("이름" if by_name else "세대수")
                hits.append((cid, nm, d, lawd, why))
        if not hits:
            stats["이름·세대수 불일치"] += 1
            continue
        # 둘 다 맞는 것 > 이름 > 세대수 순으로, 같으면 가까운 쪽.
        order = {"이름+세대수": 0, "이름": 1, "세대수": 2}
        cid, nm, dist, lawd, why = min(hits, key=lambda x: (order[x[4]], x[2]))
        by_reason[why] += 1

        stages = {label: r[col].strip() for col, label in STAGE_COLS
                  if (r.get(col) or "").strip()
                  and len(r[col].strip()) == 8 and r[col].strip().isdigit()}
        if not stages:
            stats["단계없음"] += 1
            continue

        stats["붙음"] += 1
        out.append({
            "complex_id": cid, "complex_name": nm, "lawd_cd": lawd,
            "sigun": r["시군명"], "zone_name": r["정비구역명"],
            "biz_type": r["사업유형"], "biz_step": r["사업단계"],
            "addr": used, "lat": lat, "lon": lon,
            "match_meters": round(dist, 1), "match_by": why,
            "existing_far": r.get("(기존)용적률", ""),
            "planned_far": r.get("(신축)용적률", ""),
            "existing_households": r.get("(기존주택)세대수", ""),
            **{label: stages.get(label, "") for _, label in STAGE_COLS},
        })

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    if out:
        with OUT.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)

    print(f"\n붙은 사업 {len(out)}건 → {OUT}")
    print("사유별:")
    for k, v in stats.items():
        print(f"   {k:18s} {v:4d}")
    print("무엇으로 붙었나:")
    for k, v in by_reason.items():
        print(f"   {k:18s} {v:4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
