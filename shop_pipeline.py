"""상가 매물 → 임대료 기준선 → 권리금·상권 활성 → 병원 개원 힌트 (개원 입지 모듈).

사용 순서
  python shop_pipeline.py --regions                       # 1) 수도권 시도→시군구→동 목록 내려받기(최초 1회)
  python shop_pipeline.py --probe --cortar 1168010100     #    (선택) 네이버 원본 응답 확인(역삼동)
  python shop_pipeline.py --collect --sgg 강남구           # 2) 상가 임대 매물 수집(시군구 단위 권장)
  python shop_pipeline.py --collect --sido 서울 --trade all   #    서울 전체, 매매+임대
  python shop_pipeline.py --clinics 서울                   # 3) 심평원 병의원·약국 적재(활용신청 필요, 최초 1회)
  python shop_pipeline.py --baseline --sgg 강남구           # 4) 층대별 임대료 기준선 표
  python shop_pipeline.py --note 강남구 역삼동 --dept 피부과 --pyeong 50   # 5) 개원노트 생성 → notes/
  python shop_pipeline.py --medical                        #    병원 양도/전 병원 자리 매물만
  python shop_pipeline.py --stats
  python shop_pipeline.py --fixture tests/fixtures/naver_shop_mobile_sample.json  # 오프라인 파서 테스트

수집을 반복(작업 스케줄러로 매일 1회 등)하면 first_seen/last_seen 으로 매물 잔존일·소멸이
쌓여 상권 회전 속도 지표가 살아난다. 한 번만 돌리면 그 지표는 비어 있다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

import config
from analysis import clinic_note
from analysis import medical_fit as mf
from analysis import premium as pm
from analysis import rent_baseline as rb
from collectors import naver_shop
from db import schema
from db import shop as shopdb

NOTES_DIR = config.BASE_DIR / "notes"

TRADE_SETS = {"rent": ("B2",), "sale": ("A1",), "all": ("B2", "A1", "B1")}


def _analyze_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        pm.enrich(r)
        mf.enrich(r)
        rb.enrich(r)
    return rows


def _region_filter(args) -> list[dict]:
    regs = shopdb.regions(level=3, sido=args.sido, sgg=args.sgg)
    if args.umd:
        regs = [r for r in regs if args.umd in (r.get("umd") or "")]
    if args.cortar:
        regs = [r for r in regs if r["cortar_no"] == args.cortar] or [
            {"cortar_no": args.cortar, "sido": None, "sgg": None, "umd": None,
             "center_lat": None, "center_lon": None}]
    if args.max_regions:
        regs = regs[: args.max_regions]
    return regs


# --------------------------------------------------------------- 명령들 ----

def cmd_regions(args):
    print("네이버 지역 트리(수도권) 내려받는 중... (서울·경기·인천 시군구→동, 수 분 소요)")
    rows = naver_shop.fetch_capital_region_tree()
    n = shopdb.upsert_regions(rows)
    print(f"region 테이블 갱신: {n}건 (동 단위 {sum(1 for r in rows if r['level']==3)}개)")


def cmd_probe(args):
    center = (None, None)
    if args.cortar:
        reg = next((r for r in shopdb.regions() if r["cortar_no"] == args.cortar), None)
        if reg:
            center = (reg.get("center_lat"), reg.get("center_lon"))
    print(naver_shop.probe(args.cortar, args.re_type, TRADE_SETS.get(args.trade, ("B2",))[0], center=center))


def cmd_collect(args):
    regs = _region_filter(args)
    if not regs:
        raise SystemExit("대상 동이 없습니다. 먼저 `--regions` 로 지역 목록을 받거나 --sgg/--sido 를 확인하세요.")
    print(f"수집 대상 동: {len(regs)}개 / 매물종류 {args.re_type} / 거래 {args.trade} / 엔드포인트 {config.NAVER_LAND_ENDPOINT}")
    total = {"new": 0, "updated": 0, "price_changed": 0, "gone": 0}
    for i, reg in enumerate(regs, 1):
        label = f"{reg.get('sido') or ''} {reg.get('sgg') or ''} {reg.get('umd') or reg['cortar_no']}".strip()
        try:
            rows = naver_shop.fetch_region_articles(
                reg, re_types=tuple(args.re_type.split(",")), trade_types=TRADE_SETS[args.trade],
                max_pages=args.max_pages, progress=print if args.verbose else None)
        except naver_shop.NaverLandError as e:
            print(f"  [{i}/{len(regs)}] {label}: 실패 — {e}")
            if "접근 거부" in str(e):
                raise SystemExit("차단/인증 문제로 중단합니다. 잠시 후 재시도하거나 엔드포인트를 바꿔보세요.")
            continue
        _analyze_rows(rows)
        st = shopdb.upsert_listings(rows)
        gone = shopdb.mark_gone(reg["cortar_no"], {r["article_no"] for r in rows}) if rows else 0
        for k in ("new", "updated", "price_changed"):
            total[k] += st[k]
        total["gone"] += gone
        print(f"  [{i}/{len(regs)}] {label}: {len(rows)}건 (신규 {st['new']}, 갱신 {st['updated']}, "
              f"가격변동 {st['price_changed']}, 소멸 {gone})")
    print(f"완료: 신규 {total['new']} / 갱신 {total['updated']} / 가격변동 {total['price_changed']} / 소멸 {total['gone']}")


def cmd_fixture(args):
    reg = {"cortar_no": args.cortar or "0000000000", "sido": args.sido or "테스트시",
           "sgg": args.sgg or "테스트구", "umd": args.umd or "테스트동"}
    rows = naver_shop.load_fixture(args.fixture, reg)
    _analyze_rows(rows)
    st = shopdb.upsert_listings(rows)
    print(f"fixture {args.fixture}: {len(rows)}건 파싱 → {st}")
    for r in rows[:5]:
        print(f"  {r['article_no']} {r['re_type']}/{r['trade_type']} {r['floor_band']} "
              f"보증금 {r['deposit']} 월세 {r['rent']} 전용 {r['area_exclusive']} 권리금 {r['premium']} "
              f"의료 {r['medical_flag'] or '-'} 평당 {r['rent_per_py']}")


def cmd_reanalyze(args):
    """DB에 있는 매물을 현재 규칙으로 다시 분석(파서 개선 후 재적용용)."""
    import json
    rows = shopdb.listings(active_only=False)
    for r in rows:
        try:
            r["raw"] = json.loads(r.get("raw_json") or "{}")
        except ValueError:
            r["raw"] = {}
    _analyze_rows(rows)
    st = shopdb.upsert_listings(rows)
    print(f"재분석 {len(rows)}건: {st}")


def cmd_clinics(args):
    from collectors import hira
    for sido in args.clinics:
        print(f"심평원 {sido} 병의원·약국 수집...")
        rows = hira.by_sido(sido)
        n = shopdb.upsert_clinics(rows)
        print(f"  clinic_poi 갱신 {n}건")


def cmd_baseline(args):
    ls = shopdb.listings(active_only=True)
    bl = rb.build_baselines(ls)
    umd_to_sgg = {l.get("umd"): l.get("sgg") for l in ls}
    sel = [(k, v) for k, v in bl["L1"].items()
           if not args.sgg or args.sgg in (umd_to_sgg.get(k[0]) or "")]
    print(f"층대별 전용평당 환산월세 기준선 (만원/평, 환산율 {config.RENT_CONVERSION_RATE*100:.0f}%)")
    print(f"{'동':<12}{'층대':<8}{'n':>4}{'중앙값':>8}{'p25':>8}{'p75':>8}{'보증금중앙':>10}{'월세중앙':>8}{'전용평중앙':>8}")
    for (umd, band), s in sorted(sel, key=lambda x: (x[0][0], x[0][1])):
        print(f"{umd:<12}{band:<8}{s['n']:>4}{s['median']:>8.1f}{s['p25']:>8.1f}{s['p75']:>8.1f}"
              f"{(s.get('deposit_med') or 0):>10.0f}{(s.get('rent_med') or 0):>8.0f}{(s.get('py_med') or 0):>8.0f}")
    print("\n시군구 단위(L2):")
    for (sgg, band), s in sorted(bl["L2"].items()):
        if args.sgg and args.sgg not in (sgg or ""):
            continue
        print(f"  {sgg} {band}: n={s['n']} 중앙값 {s['median']:.1f} (p25 {s['p25']:.1f} ~ p75 {s['p75']:.1f})")


def cmd_medical(args):
    ls = shopdb.listings(sgg=args.sgg, umd=args.umd, active_only=True, medical_only=True)
    bl = rb.build_baselines(shopdb.listings(active_only=True))
    print(f"병원 관련 매물 {len(ls)}건")
    for l in ls:
        print(f"- {l.get('sgg')} {l.get('umd')} · {clinic_note._listing_line(l, rb.assess(l, bl))}")


def cmd_note(args):
    sgg = args.note[0]
    umd = args.note[1] if len(args.note) > 1 else None
    md = clinic_note.build_note(sgg, umd, dept=args.dept, pyeong=args.pyeong, band=args.band,
                                radius_m=args.radius, top_n=args.top)
    NOTES_DIR.mkdir(exist_ok=True)
    safe = re.sub(r"[^\w가-힣]+", "_", f"{sgg}_{umd or '전체'}_{args.dept or ''}").strip("_")
    path = NOTES_DIR / f"{safe}_{dt.date.today().isoformat()}.md"
    path.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n→ 저장: {path}")


def cmd_stats(args):
    s = shopdb.listing_stats()
    print(f"매물 총 {s['total']}건 (활성 {s['active']}, 병원관련 {s['medical']}) / 지역 {s['regions']} / 병의원 {s['clinics']}")
    print(f"권리금 분포(활성): {s['premium']}")
    for sido, sgg, c in s["by_sgg"][:30]:
        print(f"  {sido} {sgg}: {c}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--regions", action="store_true", help="수도권 지역 트리 내려받기")
    g.add_argument("--probe", action="store_true", help="네이버 원본 응답 1페이지 출력")
    g.add_argument("--collect", action="store_true", help="상가 매물 수집")
    g.add_argument("--fixture", help="저장된 원본 JSON 파일로 파서 테스트")
    g.add_argument("--reanalyze", action="store_true", help="DB 매물을 현재 규칙으로 재분석")
    g.add_argument("--clinics", nargs="+", metavar="시도", help="심평원 병의원·약국 적재(서울/경기/인천)")
    g.add_argument("--baseline", action="store_true", help="임대료 기준선 표")
    g.add_argument("--medical", action="store_true", help="병원 관련 매물 목록")
    g.add_argument("--note", nargs="+", metavar=("시군구", "동"), help="개원노트 생성")
    g.add_argument("--stats", action="store_true")

    ap.add_argument("--sido", help="서울/경기/인천 (부분일치)")
    ap.add_argument("--sgg", help="시군구명 (부분일치)")
    ap.add_argument("--umd", help="읍면동명 (부분일치)")
    ap.add_argument("--cortar", help="법정동코드 10자리 직접 지정")
    ap.add_argument("--re-type", default="SG", help="매물종류 코드(콤마): SG 상가, SMS 사무실, GM 건물, SGJT 상가주택")
    ap.add_argument("--trade", default="rent", choices=list(TRADE_SETS), help="rent(월세) / sale(매매) / all")
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--max-regions", type=int, help="동 개수 상한(테스트용)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--dept", help="관심 진료과 키워드(예: 피부과)")
    ap.add_argument("--pyeong", type=float, default=50.0, help="개원 예정 전용 평수(기본 50)")
    ap.add_argument("--band", default="2층", choices=["1층", "2층", "3층이상", "지하"], help="개원 예정 층대")
    ap.add_argument("--radius", type=int, default=500, help="경쟁 시설 반경(m)")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    schema.init_db()
    if args.regions:
        cmd_regions(args)
    elif args.probe:
        if not args.cortar:
            raise SystemExit("--probe 에는 --cortar <법정동코드 10자리> 가 필요합니다.")
        cmd_probe(args)
    elif args.collect:
        cmd_collect(args)
    elif args.fixture:
        cmd_fixture(args)
    elif args.reanalyze:
        cmd_reanalyze(args)
    elif args.clinics:
        cmd_clinics(args)
    elif args.baseline:
        cmd_baseline(args)
    elif args.medical:
        cmd_medical(args)
    elif args.note:
        cmd_note(args)
    elif args.stats:
        cmd_stats(args)


if __name__ == "__main__":
    main()
