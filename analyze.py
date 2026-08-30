"""Phase 2 분석 CLI: 시세 기준선 + 건축규모.

예:
  python analyze.py --baseline --zoning 계획관리지역        # 계획관리 동별 평당 시세
  python analyze.py --baseline --sgg 안성시                 # 안성시 용도지역별 시세
  python analyze.py --parcel --sgg 안성시 --umd 양성면 \
        --zoning 계획관리지역 --area 660 --price 9000        # 후보 물건 평가
"""
from __future__ import annotations

import argparse

from analysis import price_baseline as pb
from analysis import road_access as ra
from analysis import zoning_rules as zr
from collectors import land_characteristics as lc


def cmd_baseline(args):
    b = pb.build_baselines(min_samples=args.min_samples)
    rows = []
    if args.zoning:
        # 특정 용도지역의 동별(L1) 시세
        for (umd, z), s in b["L1"].items():
            if z == args.zoning:
                rows.append((umd, z, s))
        rows.sort(key=lambda r: r[2]["median"])
        print(f"[{args.zoning}] 동별 평당 시세 (중앙값 오름차순, n>={args.min_samples})\n")
        print(f"{'법정동':16s} {'중앙값':>8s} {'하위25%':>8s} {'표본':>5s}")
        for umd, z, s in rows:
            print(f"{umd:16s} {s['median']:8.0f} {s['p25']:8.0f} {s['n']:5d}")
    elif args.sgg:
        for (sgg, z), s in b["L2"].items():
            if sgg == args.sgg:
                rows.append((z, s))
        rows.sort(key=lambda r: r[1]["median"])
        print(f"[{args.sgg}] 용도지역별 평당 시세 (중앙값 오름차순)\n")
        print(f"{'용도지역':18s} {'중앙값':>8s} {'하위25%':>8s} {'표본':>5s}")
        for z, s in rows:
            print(f"{z:18s} {s['median']:8.0f} {s['p25']:8.0f} {s['n']:5d}")
    else:
        # 전체 용도지역(L3) 시세
        items = sorted(b["L3"].items(), key=lambda kv: kv[1]["median"])
        print("경기도 전체 용도지역별 평당 시세(만원)\n")
        print(f"{'용도지역':18s} {'중앙값':>8s} {'하위25%':>8s} {'표본':>6s}")
        for z, s in items:
            print(f"{z:18s} {s['median']:8.0f} {s['p25']:8.0f} {s['n']:6d}")


def cmd_parcel(args):
    ppp = pb.price_per_pyeong(args.price, args.area)
    print(f"■ 후보: {args.sgg} {args.umd} / {args.zoning} / "
          f"{args.area}㎡({args.area/zr.PYEONG_M2:.0f}평) / {args.price:,}만원")
    print(f"  → 평당 {ppp:,.0f}만원\n")

    # 1) 저평가 판정
    b = pb.build_baselines(min_samples=args.min_samples)
    uv = pb.undervaluation(b, args.sgg, args.umd, args.zoning, ppp, area_m2=args.area)
    print("● 시세 대비")
    if uv is None:
        print("  기준선 표본 부족 — 판정 불가 (다른 월/지역 데이터 더 수집 권장)")
    else:
        sign = "싸다" if uv["pct_below_median"] and uv["pct_below_median"] > 0 else "비싸다"
        print(f"  기준선: {uv['level']} 중앙값 평당 {uv['median']:,.0f}만 (표본 {uv['n']})")
        print(f"  판정  : 중앙값 대비 {uv['pct_below_median']:+.1f}% ({sign})")
        if uv["pct_below_median"] and uv["pct_below_median"] >= args.threshold:
            print(f"  ★ 저평가 {args.threshold}%↑ — 급매 후보")

    # 2) 건축가능 규모
    print("\n● 건축가능 규모(법정 상한 기준)")
    bd = zr.buildable(args.zoning, args.area)
    if bd is None:
        print(f"  용도지역 '{args.zoning}' 규제표 미등록 — zoning_rules.py 확인")
    else:
        print(f"  건폐율 {bd['bcr']}% / 용적률 {bd['far']}%")
        print(f"  건축면적(바닥) {bd['footprint_pyeong']:.0f}평, "
              f"연면적 {bd['gross_pyeong']:.0f}평, 대략 {bd['approx_floors']:.1f}층")
        if bd["restricted"]:
            print("  ⚠ 신축이 원칙적으로 제한되는 용도 — 건축가능성 별도 확인 필요")


def cmd_address(args):
    """주소 한 건 → 용도지역·도로접면(맹지)·건축규모·(가격 주면)저평가 종합 판정."""
    prof = lc.profile_address(args.address, road=args.road)
    print(f"■ 주소: {args.address}")
    if not prof["ok"]:
        print(f"  ✗ 조회 실패: {prof.get('error')}")
        return
    print(f"  필지: {prof.get('addr')}  (PNU {prof.get('pnu')})")
    zoning = prof.get("zoning")
    area = prof.get("area_m2")
    print(f"  지목={prof.get('jimok')}  용도지역={zoning}"
          + (f"/{prof.get('zoning2')}" if prof.get('zoning2') else "")
          + (f"  면적={area:.0f}㎡({area/zr.PYEONG_M2:.0f}평)" if area else "  면적=미상"))

    # 1) 맹지/도로접면 판정
    r = ra.classify(prof.get("road_side"))
    print(f"\n● 도로접면: {prof.get('road_side')} → [{r['grade']}] {r['note']}")

    # 2) 건축가능 규모
    if zoning and area:
        bd = zr.buildable(zoning, area)
        if bd:
            print(f"\n● 건축가능 규모(법정 상한): 건폐율 {bd['bcr']}% / 용적률 {bd['far']}%")
            print(f"  건축면적 {bd['footprint_pyeong']:.0f}평, "
                  f"연면적 {bd['gross_pyeong']:.0f}평, 약 {bd['approx_floors']:.1f}층")
            if bd["restricted"]:
                print("  ⚠ 신축 원칙 제한 용도 — 건축가능성 별도 확인")

    # 3) 가격을 주면 저평가 판정
    if args.price and area:
        ppp = pb.price_per_pyeong(args.price, area)
        b = pb.build_baselines(min_samples=args.min_samples)
        # 시군구명은 필지 addr 앞부분에서 추정하기 어려워, 사용자가 --sgg/--umd 주면 사용
        uv = None
        if args.sgg and args.umd:
            uv = pb.undervaluation(b, args.sgg, args.umd, zoning, ppp, area_m2=area)
        print(f"\n● 시세: 매물 평당 {ppp:,.0f}만원")
        if uv:
            print(f"  기준선 {uv['level']} 중앙값 {uv['median']:,.0f}만 → "
                  f"{uv['pct_below_median']:+.1f}%"
                  + (f"  ★급매후보" if (uv['pct_below_median'] or 0) >= args.threshold else ""))
        else:
            print("  (저평가 판정하려면 --sgg, --umd 를 함께 주세요)")


def main(argv=None):
    p = argparse.ArgumentParser(description="토지 시세 기준선 + 건축규모 분석 (Phase 2)")
    p.add_argument("--baseline", action="store_true", help="시세 기준선 출력")
    p.add_argument("--parcel", action="store_true", help="후보 물건 평가(수동입력)")
    p.add_argument("--address", help="주소로 필지 종합판정(V-World)")
    p.add_argument("--road", action="store_true", help="도로명주소로 조회")
    p.add_argument("--zoning", help="용도지역 (예: 계획관리지역)")
    p.add_argument("--sgg", help="시군구명 (예: 안성시)")
    p.add_argument("--umd", help="법정동/읍면 (예: 양성면)")
    p.add_argument("--area", type=float, help="대지면적 ㎡")
    p.add_argument("--price", type=float, help="매물가(만원)")
    p.add_argument("--min-samples", type=int, default=5, help="기준선 최소 표본수")
    p.add_argument("--threshold", type=float, default=15.0, help="급매 판정 저평가율(%%)")  # argparse가 help를 %-포맷하므로 %는 %%로 escape
    args = p.parse_args(argv)

    if args.address:
        cmd_address(args)
    elif args.parcel:
        cmd_parcel(args)
    elif args.baseline:
        cmd_baseline(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
