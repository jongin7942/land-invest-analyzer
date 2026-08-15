"""Phase 4 파이프라인: 온비드 공매 물건 → V-World 판정 → 시세 기준선 → 종합 스코어.

사용:
  python pipeline.py --run                          # 온비드 실제 수집+판정(활용신청 필요)
  python pipeline.py --demo "경기도 이천시 율면 고당리 1" --min-bid 30000000
                                                      # 온비드 없이 단일 주소로 스코어링 테스트
  python pipeline.py --top 20                        # 저장된 후보 중 상위 N 출력
"""
from __future__ import annotations

import argparse
import re

import json

from analysis import category as cat
from analysis import price_baseline as pb
from analysis import road_access as ra
from analysis import zoning_rules as zr
from collectors import land_characteristics as lc
from collectors import news as news_mod
from collectors import onbid
from db import schema

# 도로접면 등급별 가점/감점
ROAD_SCORE = {ra.OK: 10, ra.NARROW: 0, ra.BLOCKED: -20, ra.MENGJI: -40, ra.UNKNOWN: -5}
# 김종률식 관심 용도지역(저가 관리지역) 가점 / 사실상 신축제한 용도 감점
FAVORED_ZONING = {"계획관리지역", "생산관리지역", "자연녹지지역"}
RESTRICTED_PENALTY = -10


def parse_sgg_umd(addr: str | None):
    """V-World 정규화 주소('경기도 안성시 양성면 노곡리 1')에서
    실거래가 기준선 키(sgg_nm, umd_nm)를 추정 파싱. 예: ('안성시','양성면 노곡리')."""
    if not addr:
        return None, None
    t = addr.replace("경기도", "").split()
    t = [x for x in t if x]
    if not t:
        return None, None
    i = 0
    sgg = t[i]; i += 1
    if i < len(t) and t[i].endswith("구"):
        sgg = f"{sgg} {t[i]}"; i += 1
    umd = t[i] if i < len(t) else None
    if umd:
        i += 1
        if umd.endswith(("읍", "면")) and i < len(t) and t[i].endswith("리"):
            umd = f"{umd} {t[i]}"
    return sgg, umd


def evaluate(address: str, min_bid_won: int | None, baselines: dict, road: bool = False) -> dict:
    """주소+최저입찰가(원) 한 건을 통째로 판정. store 용 dict 반환(raw 제외 필드 채움)."""
    prof = lc.profile_address(address, road=road)
    out = {"address": address, "min_bid": min_bid_won}
    if not prof.get("ok"):
        out["tags"] = f"판정실패:{prof.get('error')}"
        return out

    zoning = prof.get("zoning")
    area = prof.get("area_m2")
    out.update({
        "pnu": prof.get("pnu"), "zoning": zoning, "jimok": prof.get("jimok"),
        "area_m2": area, "road_side": prof.get("road_side"),
    })

    rgrade = ra.classify(prof.get("road_side"))
    out["road_grade"] = rgrade["grade"]
    score = ROAD_SCORE.get(rgrade["grade"], -5)

    tags = [rgrade["grade"]]
    if zoning:
        if zoning in FAVORED_ZONING:
            tags.append(zoning)
            score += 5
        lim = zr.limits_for(zoning)
        if lim and lim[2]:  # restricted
            tags.append("신축제한용도")
            score += RESTRICTED_PENALTY

    if min_bid_won and area:
        ppp = pb.price_per_pyeong(min_bid_won / 10000.0, area)  # 원→만원
        out["ppp_min_bid"] = ppp
        sgg, umd = parse_sgg_umd(prof.get("addr") or address)
        uv = pb.undervaluation(baselines, sgg, umd, zoning, ppp, area_m2=area) if (sgg and umd) else None
        if uv:
            out["baseline_med"] = uv["median"]
            out["pct_below"] = uv["pct_below_median"]
            out["baseline_lvl"] = uv["level"]
            score += (uv["pct_below_median"] or 0)
            if (uv["pct_below_median"] or 0) >= 15:
                tags.append("★저평가")

    out["score"] = score
    out["tags"] = ",".join(tags)
    return out


def evaluate_onbid_item(item: dict, baselines: dict) -> dict:
    """온비드 물건(dict, PNU·면적·소재지 이미 포함)을 판정. 지오코딩 불필요."""
    out = dict(item)  # mgmt_no, plnm_no, name, appraisal, min_bid, disposal, bid_begin/end 등 유지
    out["address"] = f"{item.get('sido','')} {item.get('sgg','')} {item.get('emd','')}".strip()
    pnu = item.get("pnu")
    area = item.get("area_m2")
    if not pnu:
        out["tags"] = "PNU없음"
        return out

    lc_data = lc.land_characteristics(pnu) or {}
    zoning = lc_data.get("zoning")
    road_side = lc_data.get("road_side")
    out.update({"zoning": zoning, "road_side": road_side})
    if not out.get("jimok"):
        out["jimok"] = item.get("use_name")

    rgrade = ra.classify(road_side)
    out["road_grade"] = rgrade["grade"]
    score = ROAD_SCORE.get(rgrade["grade"], -5)

    tags = [rgrade["grade"]]
    if zoning:
        if zoning in FAVORED_ZONING:
            tags.append(zoning)
            score += 5
        lim = zr.limits_for(zoning)
        if lim and lim[2]:
            tags.append("신축제한용도")
            score += RESTRICTED_PENALTY

    min_bid = item.get("min_bid")
    if min_bid and area:
        ppp = pb.price_per_pyeong(min_bid / 10000.0, area)  # 원→만원
        out["ppp_min_bid"] = ppp
        uv = pb.undervaluation(baselines, item.get("sgg"), item.get("emd"), zoning, ppp, area_m2=area)
        if uv:
            out["baseline_med"] = uv["median"]
            out["pct_below"] = uv["pct_below_median"]
            out["baseline_lvl"] = uv["level"]
            score += (uv["pct_below_median"] or 0)
            if (uv["pct_below_median"] or 0) >= 15:
                tags.append("★저평가")

    out["score"] = score
    out["tags"] = ",".join(tags)

    # --- 개발호재 뉴스 (개발용·토지보상용 둘 다에 참고자료로 붙는다) ---
    hits = news_mod.development_news(item.get("sgg"), item.get("emd"))
    out["news_count"] = len(hits)
    out["news_json"] = json.dumps(hits, ensure_ascii=False) if hits else None

    # --- 토지보상용 관점 ---
    lp = lc.land_price(pnu)
    if lp:
        out["land_price_m2"] = lp["price_won_m2"]
        out["land_price_yr"] = lp["stdr_year"]
    out["land_group"] = cat.land_group(out.get("jimok"))
    cv = cat.compensation_view(out)
    if cv:
        out["comp_discount"] = cv["discount_vs_official"]
        out["comp_score"] = cat.compensation_score(cv, has_dev_news=bool(hits))

    return out


def cmd_run(args):
    print("온비드 부동산 물건목록(경기도·토지) 수집 중...")
    if args.prpt_div:
        rows = onbid.fetch_land_items(prpt_div_cds=[args.prpt_div], pvct_trgt_yn=args.pvct,
                                       max_matches=args.limit)
    else:
        # 카테고리별 개별 한도 적용 — 안 그러면 작은 카테고리(공유재산 등)만
        # 먼저 소진되고 압류재산처럼 큰 카테고리 표본이 거의 안 들어간다.
        rows = []
        for code, name in onbid.PRPT_DIV.items():
            part = onbid.fetch_land_items(prpt_div_cds=[code], pvct_trgt_yn=args.pvct,
                                           max_matches=args.limit)
            print(f"  {name}({code}): {len(part)}건")
            rows.extend(part)
    print(f"대상 {len(rows)}건 — V-World 판정 중...\n")
    if not rows:
        print("0건입니다. python -c \"from collectors import onbid; print(onbid.probe())\" 로 원본 응답 확인 권장.")
        return

    baselines = pb.build_baselines(min_samples=args.min_samples)
    schema.init_db()
    results = []
    failed = 0
    for i, r in enumerate(rows, 1):
        try:
            results.append(evaluate_onbid_item(r, baselines))
        except Exception as e:  # noqa: BLE001 - 개별 물건 실패로 전체 배치가 죽으면 안 됨
            failed += 1
            r["tags"] = f"판정오류:{e}"
            results.append(r)
        if i % 20 == 0:
            print(f"  {i}/{len(rows)} 판정 완료")

    n = schema.upsert_auction_candidates(results)
    print(f"\n저장: {n}건 신규 (총 판정 {len(results)}건, 실패 {failed}건)\n")
    cmd_top(args)


def cmd_demo(args):
    baselines = pb.build_baselines(min_samples=args.min_samples)
    ev = evaluate(args.demo, int(args.min_bid) if args.min_bid else None, baselines, road=args.road)
    print(f"■ {args.demo}")
    if "tags" in ev and ev["tags"].startswith("판정실패"):
        print(f"  {ev['tags']}")
        return
    print(f"  용도={ev.get('zoning')}  지목={ev.get('jimok')}  "
          f"면적={ev.get('area_m2')}㎡  도로접면={ev.get('road_side')}({ev.get('road_grade')})")
    if ev.get("ppp_min_bid"):
        print(f"  최저입찰가 평당 {ev['ppp_min_bid']:,.0f}만원"
              + (f"  기준선({ev.get('baseline_lvl')}) {ev.get('baseline_med'):,.0f}만 "
                 f"→ {ev.get('pct_below'):+.1f}%" if ev.get("baseline_med") else "  (기준선 표본 부족)"))
    print(f"  태그: {ev.get('tags')}")
    print(f"  ★ 종합점수: {ev.get('score'):.1f}")


def cmd_top(args):
    schema.init_db()
    rows = schema.top_candidates(args.top)
    if not rows:
        print("저장된 후보가 없습니다.")
        return
    print(f"{'점수':>6s} {'등급':8s} {'저평가%':>7s} {'회차':>4s} {'상태':10s} {'용도':14s} 주소")
    for r in rows:
        pb_ = f"{r['pct_below']:+.0f}%" if r["pct_below"] is not None else "-"
        rnd = str(r["round"]) if r["round"] is not None else "-"
        st = r["status"] or "-"
        print(f"{r['score']:6.1f} {r['road_grade'] or '-':8s} {pb_:>7s} {rnd:>4s} {st:10s} "
              f"{(r['zoning'] or '-'):14s} {r['address']}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Phase4: 온비드 → 판정 → 스코어링")
    p.add_argument("--run", action="store_true", help="온비드 실제 수집+판정")
    p.add_argument("--demo", help="온비드 없이 주소 하나로 스코어링 테스트")
    p.add_argument("--min-bid", help="--demo 용 최저입찰가(원)")
    p.add_argument("--road", action="store_true", help="--demo 주소가 도로명주소")
    p.add_argument("--pvct", default="N", help="수의계약가능여부(Y/N), 기본 N")
    p.add_argument("--prpt-div", help="재산유형코드 하나만(예: 0007=압류재산). 기본은 전체 순회")
    p.add_argument("--limit", type=int, default=150,
                    help="카테고리별 수집 상한(기본 150, 전국스캔 시간절약). 0=무제한")
    p.add_argument("--min-samples", type=int, default=5)
    p.add_argument("--top", type=int, default=20, help="상위 N건 출력")
    args = p.parse_args(argv)

    if args.demo:
        cmd_demo(args)
    elif args.run:
        cmd_run(args)
    else:
        cmd_top(args)


if __name__ == "__main__":
    main()
