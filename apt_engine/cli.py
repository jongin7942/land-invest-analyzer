"""아파트 엔진 CLI.

    # 준비
    python -m apt_engine.cli init                     # DB 생성 + 마이그레이션
    python -m apt_engine.cli probe trade              # API 원본 응답 확인(필드명 검증)

    # 수집 — 순서가 중요하다. 단지가 먼저 있어야 실거래를 붙일 수 있다.
    python -m apt_engine.cli collect complexes                  # K-apt 단지 (수도권 전체)
    python -m apt_engine.cli collect trades --months 60         # 매매 5년치
    python -m apt_engine.cli collect rents  --months 60         # 전월세 5년치
    python -m apt_engine.cli match                              # 단지 매칭
    python -m apt_engine.cli snapshot                           # 대표가격·전세가·전세가율

    # 호가 — 외부 API 없이 수기 입력만으로 돌아간다
    python -m apt_engine.cli listing template 매물.csv           # 입력 서식 생성
    python -m apt_engine.cli listing import 매물.csv             # 매물 저장 + 오늘 스냅샷
    python -m apt_engine.cli listing note --complex-id 1 ...     # 중개사 협상가 기록

    # 규칙 — 공식 API 가 없는 세법·규제·토허·대출을 사람이 넣는다
    python -m apt_engine.cli rule template tax 세법.csv          # 서식 생성
    python -m apt_engine.cli rule import tax 세법.csv            # 입력
    python -m apt_engine.cli rule status                        # 채워진 정도

    # 조회
    python -m apt_engine.cli price "동아1단지"                    # 근거까지 펼쳐서 보기
    python -m apt_engine.cli cash "동아1단지" --price 6.2 --house-count 1   # 실투자금

    # 상대가치 — 가격사다리를 먼저 채워야 비교단지가 잡힌다
    python -m apt_engine.cli ladder template 사다리.csv
    python -m apt_engine.cli ladder import 사다리.csv
    python -m apt_engine.cli relative build --band 84
    python -m apt_engine.cli relative show "동아1단지" --band 84

    # 촉매 — 교통호재·공급. 계획과 개통을 섞지 않는다
    python -m apt_engine.cli transit template 교통.csv
    python -m apt_engine.cli transit import 교통.csv
    python -m apt_engine.cli supply  template 공급.csv
    python -m apt_engine.cli geocode --limit 200          # 단지 좌표 채우기(V-World)
    python -m apt_engine.cli catalyst build --as-of 2026-08-31 --years 5
    python -m apt_engine.cli catalyst show "동아1단지"
    python -m apt_engine.cli market "동아1단지" --band 84         # 호가·괴리·변화·시장압력

    # 재건축 — 1차는 전국 자동, 2차는 상위 후보만 수기
    python -m apt_engine.cli redev screen --lawd 28237          # 1차 스크리닝
    python -m apt_engine.cli redev template project 정비사업.csv  # 2차 입력 서식
    python -m apt_engine.cli redev import  project 정비사업.csv
    python -m apt_engine.cli redev show "동아1단지" --price 6.2  # 분담금 3구간·전환원가

    # 점검
    python -m apt_engine.cli status
    python -m apt_engine.cli validate
    python -m apt_engine.cli report unmatched
    python -m apt_engine.cli report gaps

토지 파이프라인(main.py / analyze.py / pipeline.py)과 완전히 별개이며,
이 명령들은 land_invest.db 를 열지 않는다.
"""
from __future__ import annotations

import argparse
import sys

import config
from apt_engine import ENGINE_VERSION, ingest, regions
from apt_engine.collectors import apt_rent, apt_trade, kapt
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.repo import apt as repo
from apt_engine.price import snapshot as snap_mod
from apt_engine.redev import far as redev_far
from apt_engine.redev import feasibility as redev_feas
from apt_engine.redev import screening as redev_screening
from apt_engine.validation import rules as validation


# ── 준비 ──────────────────────────────────────────────────────────────

def cmd_init(args):
    applied = mig.migrate(args.db)
    print(f"마이그레이션 적용: {', '.join(f'{v:03d}' for v in applied)}" if applied
          else "이미 최신 스키마입니다.")
    with get_conn(args.db) as conn:
        n = repo.sync_regions(conn)
    print(f"시군구 코드 {n}개 동기화")
    cmd_status(args)


def cmd_status(args):
    s = mig.status(args.db)
    print(f"\nDB       : {s['db_path']}")
    print(f"엔진버전 : {ENGINE_VERSION}")
    print(f"스키마   : {s['version']:03d} / 최신 {s['latest']:03d}"
          + (f"  (미적용 {len(s['pending'])}건 — `init` 필요)" if s["pending"] else ""))
    if not s["tables"]:
        print("테이블   : 없음 (python -m apt_engine.cli init)")
        return

    print("테이블   :")
    with get_conn(s["db_path"]) as conn:
        for t in s["tables"]:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<22s} {n:>12,d} 행")

        if conn.execute("SELECT COUNT(*) FROM trade").fetchone()[0]:
            print("\n매칭 현황:")
            for table, label in (("trade", "매매"), ("jeonse_contract", "전월세")):
                st = repo.match_stats(conn, table)
                if not st["total"]:
                    continue
                detail = " · ".join(f"{k} {v:,}" for k, v in sorted(st["by_confidence"].items()))
                print(f"  {label}: 전체 {st['total']:,} · 미매칭 {st['unmatched']:,}"
                      f" ({st['unmatched_pct']:.1f}%)\n    {detail}")


# ── 진단 ──────────────────────────────────────────────────────────────

def cmd_probe(args):
    """라이브 원본 응답을 그대로 출력한다.

    개발 환경에서 data.go.kr 에 접근할 수 없어 필드명을 라이브 검증하지 못했다.
    이 명령으로 확인하고, 다르면 각 수집기의 FIELDS 후보에 추가하면 된다.
    """
    what = args.what
    if what == "trade":
        print(apt_trade.probe(args.lawd, args.ym))
    elif what == "rent":
        print(apt_rent.probe(args.lawd, args.ym))
    elif what == "kapt-list":
        print(kapt.probe_list(args.lawd))
    elif what == "kapt-basis":
        if not args.kapt_code:
            sys.exit("--kapt-code 가 필요합니다. 먼저 `probe kapt-list` 로 단지코드를 확인하세요.")
        print(kapt.probe_basis(args.kapt_code))


def cmd_validate(args):
    with get_conn(args.db) as conn:
        results = validation.run_all(conn)
        summary = validation.summarize(results)

    for rule, violations in results:
        mark = "✓" if not violations else ("✗" if rule.severity == "ERROR" else "!")
        print(f"{mark} [{rule.rule_id}] {rule.title}")
        for v in violations:
            print(f"    - {v}")

    print(f"\n{summary['total']}개 규칙 중 통과 {summary['passed']} · "
          f"위반 {summary['errors']} · 경고 {summary['warnings']}")
    if summary["errors"]:
        sys.exit(1)


def cmd_report(args):
    with get_conn(args.db) as conn:
        if args.kind == "unmatched":
            _report_unmatched(conn, args.limit)
        elif args.kind == "gaps":
            _report_gaps(conn)


def _report_unmatched(conn, limit: int):
    """어느 단지명이 안 붙는지 — 매칭 규칙을 고칠 근거."""
    any_rows = False
    for table, label in (("trade", "매매"), ("jeonse_contract", "전월세")):
        rows = repo.distinct_unmatched_names(conn, table)[:limit]
        if not rows:
            continue
        any_rows = True
        print(f"\n■ {label} 미매칭 상위 {len(rows)}건")
        print(f"{'건수':>6s}  {'시군구':16s} {'법정동':10s} {'건축':>4s}  단지명")
        for r in rows:
            print(f"{r['cnt']:6,d}  {regions.name_of(r['lawd_cd']):16s} "
                  f"{(r['emd_name'] or '-'):10s} {(r['build_year'] or '-'):>4}  {r['apt_name']}")
    if not any_rows:
        print("미매칭 건이 없습니다.")


def _report_gaps(conn):
    """시군구별로 거래가 0건인 달 — 코드 개편 시점을 잘못 잡으면 여기가 빈다."""
    rows = repo.monthly_coverage(conn, "trade", "deal_ymd")
    if not rows:
        print("수집된 매매 실거래가 없습니다.")
        return
    by_region: dict[str, set] = {}
    all_yms = set()
    for r in rows:
        by_region.setdefault(r["lawd_cd"], set()).add(r["ym"])
        all_yms.add(r["ym"])

    lo, hi = min(all_yms), max(all_yms)
    print(f"수집 구간 {lo} ~ {hi}, 시군구 {len(by_region)}개\n")
    found = False
    for code, yms in sorted(by_region.items()):
        missing = sorted(y for y in all_yms if y not in yms)
        if not missing:
            continue
        found = True
        head = ", ".join(missing[:8]) + (f" … 총 {len(missing)}개월" if len(missing) > 8 else "")
        print(f"  {regions.name_of(code):16s} 공백: {head}")
    if not found:
        print("  공백 없음.")
    else:
        print("\n※ 구 개편 전후 구간이 통째로 비었다면 apt_engine/regions.py 의 "
              "LEGACY until_ym 을 확인하세요.")


# ── 수집 ──────────────────────────────────────────────────────────────

def cmd_collect(args):
    if args.what == "complexes":
        print(f"K-apt 단지 수집 ({args.sido or '수도권 전체'})...")
        s = ingest.collect_complexes(args.sido, with_basis=not args.no_basis, db_path=args.db)
        print(f"\n시군구 {s['regions']}개 · 단지 {s['listed']}개 · "
              f"기본정보 {s['basis']}개 · 실패 {s['failed']}건")
    elif args.what == "trades":
        print(f"매매 실거래 수집 (최근 {args.months}개월, {args.sido or '수도권 전체'})...")
        s = ingest.collect_trades(args.months, args.sido, db_path=args.db)
        _print_deal_stats(s)
    elif args.what == "rents":
        print(f"전월세 실거래 수집 (최근 {args.months}개월, {args.sido or '수도권 전체'})...")
        s = ingest.collect_rents(args.months, args.sido, db_path=args.db)
        _print_deal_stats(s)


def _print_deal_stats(s: dict):
    print(f"\n{s['months']}개월 · 조회 {s['fetched']:,}건 · 신규 {s['inserted']:,}건 · "
          f"데이터없음 {s['empty']}건 · 실패 {s['failed']}건")
    if s["inserted"]:
        print("다음: python -m apt_engine.cli match")


def cmd_match(args):
    print("단지 매칭 중..." + (" (전체 재계산)" if args.rebuild else ""))
    ingest.run_matching(rebuild=args.rebuild, db_path=args.db)
    print("\n다음: python -m apt_engine.cli validate")


def cmd_snapshot(args):
    print(f"대표가격 스냅샷 생성 (집계창 {args.window}개월, 기준월 {args.months}개)...")
    s = ingest.build_snapshots(
        as_of_ym=args.as_of, months=args.months, window_months=args.window,
        min_households=args.min_households, sido=args.sido, db_path=args.db)
    print(f"\n(단지×면적) {s['pairs']}쌍 · 매매 스냅샷 {s['price']:,} · "
          f"전세 {s['jeonse']:,} · 전세가율 {s['ratio']:,} · 표본없음 {s['skipped']:,}")
    if s["price"]:
        print('다음: python -m apt_engine.cli price "<단지명>"')


def cmd_price(args):
    """요구사항 51 — 모든 핵심 숫자는 출처·원본·계산법을 펼쳐 볼 수 있어야 한다."""
    from apt_engine.trace import Calc

    with get_conn(args.db) as conn:
        matches = repo.find_complexes(conn, args.query)
        if not matches:
            print(f"'{args.query}' 로 찾은 단지가 없습니다. "
                  f"`collect complexes` 를 먼저 실행했는지 확인하세요.")
            return
        if len(matches) > 1 and not args.complex_id:
            print(f"'{args.query}' 로 {len(matches)}개 단지가 검색됐습니다. "
                  f"--complex-id 로 지정하세요.\n")
            for m in matches[:20]:
                print(f"  [{m['id']:>6}] {m['name']:<28s} {regions.name_of(m['lawd_cd']):16s} "
                      f"{(m['emd_name'] or '-'):10s} "
                      f"{m['apt_households'] or '?'}세대 {m['approval_year'] or '?'}년")
            return

        row = (conn.execute("SELECT * FROM complex WHERE id=?", (args.complex_id,)).fetchone()
               if args.complex_id else matches[0])
        if row is None:
            print(f"단지 #{args.complex_id} 를 찾을 수 없습니다.")
            return

        _print_complex_header(row)
        bands = [args.band] if args.band else [
            r[0] for r in conn.execute(
                "SELECT DISTINCT area_band FROM trade WHERE complex_id=? ORDER BY 1",
                (row["id"],)).fetchall()]
        if not bands:
            print("\n  이 단지에 매칭된 실거래가 없습니다 → 대표가격 확인 불가")
            return

        for band in bands:
            _print_band_price(conn, row["id"], band, verbose=args.verbose)


def _print_complex_header(row):
    from apt_engine import units
    print(f"\n■ {row['name']}  ({regions.name_of(row['lawd_cd'])} {row['emd_name'] or ''})")
    facts = []
    facts.append(f"세대수 {row['apt_households']:,}" if row["apt_households"]
                 else "세대수 확인 불가(K-apt 미수집)")
    facts.append(f"사용승인 {row['approval_year']}년" if row["approval_year"]
                 else "사용승인 확인 불가")
    facts.append(f"대지면적 {units.fmt_m2(row['land_area_m2'])}" if row["land_area_m2"]
                 else "대지면적 확인 불가(건축물대장 필요)")
    facts.append(f"용적률 {row['current_far']}%" if row["current_far"]
                 else "현재 용적률 확인 불가")
    print("  " + " · ".join(facts))


def _print_band_price(conn, complex_id, band, *, verbose=False):
    from apt_engine import area, units
    from apt_engine.trace import Calc

    ps = repo.latest_price_snapshot(conn, complex_id, band)
    js = repo.latest_jeonse_snapshot(conn, complex_id, band)
    print(f"\n  ── 전용 {area.label_of(band)} ──")

    if ps is None:
        print("    매매 대표가격: 확인 불가 (스냅샷 미생성 — `cli snapshot` 실행)")
    else:
        print(f"    매매 대표가격  {units.fmt_eok(ps['representative_price']):>10s}"
              f"   [{ps['confidence']}] 정상거래 {ps['sample_n']}건 · 제외 {ps['excluded_n']}건"
              f"  ({ps['as_of_ym']} 기준 {ps['window_months']}개월)")
        print(f"    분포           {units.fmt_eok(ps['price_min'])} ~ "
              f"{units.fmt_eok(ps['price_p25'])} ~ {units.fmt_eok(ps['price_p75'])} ~ "
              f"{units.fmt_eok(ps['price_max'])}")

    if js is None:
        print("    전세 대표가격: 확인 불가")
    else:
        print(f"    전세 대표가격  {units.fmt_eok(js['representative_deposit']):>10s}"
              f"   [{js['confidence']}] 정상계약 {js['sample_n']}건")
        if js["jeonse_ratio"] is not None:
            print(f"    전세가율       {units.fmt_pct(js['jeonse_ratio']):>10s}")
        else:
            print("    전세가율: 확인 불가 (매매 또는 전세 표본 없음)")

    if verbose and ps is not None:
        print("\n    ── 계산 근거 ──")
        for line in Calc.from_json(ps["calc_trace"]).explain().splitlines():
            print(f"    {line}")


# ── 호가 ──────────────────────────────────────────────────────────────

def cmd_listing(args):
    from apt_engine.listing.provider import ListingError, write_template
    from apt_engine.repo import listing as listing_repo

    if args.action == "template":
        path = write_template(args.path)
        print(f"입력 서식을 만들었습니다: {path}\n"
              f"이 파일의 컬럼에 맞춰 매물을 적은 뒤 `listing import` 하세요.\n"
              f"가격은 6.2(억) 또는 620000000(원) 둘 다 됩니다.")
        return

    if args.action == "import":
        try:
            s = ingest.import_listings(args.path, fmt=args.format, seen_on=args.date,
                                       db_path=args.db)
        except ListingError as e:
            sys.exit(f"입력 파일에 문제가 있습니다:\n{e}")
        print(f"\n읽음 {s['read']}건 · 신규 {s['new']} · 갱신 {s['updated']} · "
              f"스냅샷 {s['snapshot']} · 단지매칭 {s['matched']}")
        if s["matched"] < s["read"]:
            print("※ 단지에 못 붙은 매물이 있습니다. CSV 에 lawd_cd 열을 넣으면 정확해집니다.")
        return

    if args.action == "note":
        with get_conn(args.db) as conn:
            nid = listing_repo.add_field_note(
                conn, complex_id=args.complex_id, area_band=args.band,
                noted_on=args.date or _today(), kind=args.kind,
                note=args.note, source=args.source,
                price=_parse_price_arg(args.price))
        print(f"현장 확인값 #{nid} 저장 — 이 값은 호가가 아니라 '{args.kind}' 로 "
              f"별도 보관됩니다(요구사항 46).")


def _today():
    from datetime import date
    return date.today().isoformat()


def _parse_price_arg(raw):
    if raw is None:
        return None
    from apt_engine.listing.provider import parse_price
    return parse_price(raw)


def cmd_market(args):
    """호가 분포 · 실거래 괴리 · 매물 변화 · 시장압력 (요구사항 4·5·8·9·10)."""
    from apt_engine import area, units
    from apt_engine.repo import listing as listing_repo

    with get_conn(args.db) as conn:
        matches = repo.find_complexes(conn, args.query)
        if not matches:
            print(f"'{args.query}' 로 찾은 단지가 없습니다.")
            return
        row = (conn.execute("SELECT * FROM complex WHERE id=?", (args.complex_id,)).fetchone()
               if args.complex_id else matches[0])
        if len(matches) > 1 and not args.complex_id:
            print(f"'{args.query}' 로 {len(matches)}개 검색됨 — 첫 번째를 씁니다. "
                  f"--complex-id 로 지정하세요.")
        _print_complex_header(row)

    band = args.band or area.DEFAULT_BAND
    res = ingest.analyze_listings(complex_id=row["id"], area_band=band,
                                  trade_type=args.trade_type,
                                  window_days=args.window, db_path=args.db)

    print(f"\n  ── 전용 {area.label_of(band)} · {args.trade_type} ──")
    dist = res["distribution"]
    if dist is None:
        print("    현재 호가: 확인 불가 (매물 미입력 — `listing import` 필요)")
    else:
        print(f"    매물 {dist.count}건 (중복 제거 추정 {dist.dedupe.range_label})"
              + (f" · 특수매물 {dist.special_count}건" if dist.special_count else ""))
        print(f"    최저호가        {units.fmt_eok(dist.low):>10s}"
              + ("  ⚠ 특수매물" if dist.low_is_special else ""))
        if dist.low_normal is not None:
            print(f"    정상매물 최저호가 {units.fmt_eok(dist.low_normal):>9s}")
        print(f"    중위호가        {units.fmt_eok(dist.median):>10s}"
              f"   (25% {units.fmt_eok(dist.p25)} · 75% {units.fmt_eok(dist.p75)} · "
              f"최고 {units.fmt_eok(dist.high)})")
        for group, d in dist.by_floor_group.items():
            print(f"      {group} {d['n']}건 · 중앙 {units.fmt_eok(d['median'])}")
        if dist.special_flags:
            print(f"    특수조건: " + " · ".join(f"{k} {v}건"
                                                for k, v in dist.special_flags.items()))

    ps = res["price_snapshot_row"]
    if ps is None:
        print("\n    대표 실거래가: 확인 불가 (`cli snapshot` 필요)")
    else:
        print(f"\n    대표 실거래가   {units.fmt_eok(ps['representative_price']):>10s}"
              f"   [{ps['confidence']}] {ps['sample_n']}건")
        if res["recent_trade"]:
            print(f"    최근 실거래 1건 {units.fmt_eok(res['recent_trade']):>10s}")

    gap = res["gap"]
    if gap is not None and gap.value is not None:
        print(f"\n    호가·실거래 괴리")
        for k, v in gap.intermediates.items():
            print(f"      {k}: {v}")

    chg = res["change"]
    print()
    if chg is None:
        print(f"    매물 변화: 확인 불가 (스냅샷이 2일 이상 쌓여야 계산됩니다)")
    else:
        for line in chg.summary_lines():
            print(f"    {line}")

    press = res["pressure"]
    if not press.available_components:
        # 근거가 하나도 없는데 50점 중립이라고 쓰면 판단한 것처럼 보인다.
        print("\n    시장압력: 확인 불가 (매물 스냅샷을 며칠 쌓아야 계산됩니다)")
    else:
        print(f"\n    시장압력 {press.score}/100 → {press.direction}"
              f"   (근거 확보율 {press.coverage*100:.0f}%)")
    for c in press.components:
        mark = "·" if c.available else "?"
        print(f"      {mark} {c.key:<8s} {c.note}")

    if args.verbose:
        print("\n    ── 계산 근거 ──")
        for line in press.calc.explain().splitlines():
            print(f"    {line}")


# ── 규칙 (PHASE 3) ────────────────────────────────────────────────────

def cmd_rule(args):
    from apt_engine.repo import rules as rule_repo

    if args.action == "template":
        path = rule_repo.write_template(args.kind, args.path)
        print(f"서식을 만들었습니다: {path}\n"
              f"'#' 로 시작하는 줄은 설명이라 무시됩니다. 값을 채운 뒤 import 하세요.\n"
              f"★ last_verified(확인일)를 비우면 엔진이 그 규칙으로 계산하지 않습니다 — "
              f"원문을 직접 확인한 날짜를 넣으세요.")
        return

    if args.action == "import":
        with get_conn(args.db) as conn:
            try:
                s = rule_repo.import_csv(conn, args.kind, args.path)
            except rule_repo.RuleImportError as e:
                sys.exit(f"입력 파일에 문제가 있습니다:\n{e}")
        print(f"{args.kind}: {s['inserted']}건 입력"
              + (f" · 미검증 {s['unverified']}건 (계산에 쓰이지 않음)"
                 if s["unverified"] else ""))
        return

    if args.action == "list":
        with get_conn(args.db) as conn:
            rows = rule_repo.list_rules(conn, args.kind)
        if not rows:
            print(f"{args.kind} 규칙이 없습니다. `rule template {args.kind}` 로 시작하세요.")
            return
        for r in rows:
            mark = "✓" if r["last_verified"] else "✗"
            key = (r["rule_key"] if "rule_key" in r.keys()
                   else r["zone_type"] if "zone_type" in r.keys() else r["target_scope"])
            print(f"  {mark} [{r['id']:>4}] {key:<28s} "
                  f"{r['effective_from']}~{r['effective_to'] or '현재'}"
                  + (f"  확인 {r['last_verified']}" if r["last_verified"] else "  ⚠ 미검증"))
        return

    if args.action == "verify":
        with get_conn(args.db) as conn:
            n = rule_repo.mark_verified(conn, args.kind, rule_id=args.id,
                                        verified_on=args.date or _today())
        print(f"{n}건 확인 표시" if n else "해당 규칙을 찾지 못했습니다.")
        return

    if args.action == "status":
        with get_conn(args.db) as conn:
            cov = rule_repo.coverage(conn)
        print("규칙 입력 현황 (검증/전체)\n")
        labels = {"regulation": "규제지역", "permit": "토지거래허가구역",
                  "tax": "세법", "loan": "대출규제", "cost": "취득 부대비용"}
        for kind, c in cov.items():
            state = ("미입력" if c["total"] == 0
                     else "사용 가능" if c["verified"] == c["total"]
                     else f"일부 미검증")
            print(f"  {labels[kind]:<16s} {c['verified']:>3}/{c['total']:<3}  {state}")
        if all(c["total"] == 0 for c in cov.values()):
            print("\n전부 비어 있습니다. 세금·대출·실투자금 계산이 '확인 불가'로 나옵니다.\n"
                  "`rule template <종류> <파일>` 로 서식을 받아 채워 넣으세요.")


def cmd_regulation(args):
    """규제지역·토허 판정 (요구사항 22). 매수 판단 전에 가장 먼저 봐야 한다."""
    from apt_engine.regulation import zone as zone_mod

    with get_conn(args.db) as conn:
        row = _resolve_complex(conn, args)
        if row is None:
            return
        _print_complex_header(row)
        day = args.as_of or _today()
        z = zone_mod.zone_at(conn, row["lawd_cd"], as_of=day, emd_name=row["emd_name"])
        p = zone_mod.permit_zone_at(conn, row["lawd_cd"], as_of=day, scope=args.scope,
                                    emd_name=row["emd_name"])
        calc = zone_mod.summarize(z, p)

    print(f"\n  기준일 {day} · 대상 {args.scope}")
    print(f"    규제지역          {z.label}")
    print(f"    토지거래허가구역   {p.label}")
    print(f"    전세 활용         {calc.intermediates['전세 활용']}")
    if "주의" in calc.intermediates:
        print(f"\n    ⚠ {calc.intermediates['주의']}")


def cmd_loan(args):
    from apt_engine.listing.provider import parse_price
    from apt_engine.regulation import loan as loan_mod
    from apt_engine.regulation import zone as zone_mod

    price = parse_price(args.price)
    with get_conn(args.db) as conn:
        row = _resolve_complex(conn, args) if args.query else None
        lawd = row["lawd_cd"] if row is not None else args.lawd
        if not lawd:
            sys.exit("단지명 또는 --lawd 가 필요합니다.")
        day = args.as_of or _today()
        z = zone_mod.zone_at(conn, lawd, as_of=day)
        cap = loan_mod.capacity(
            conn, price=price, as_of=day, house_count=args.house_count,
            zone_types=z.types,
            annual_income=parse_price(args.income) if args.income else None,
            existing_annual_payment=parse_price(args.existing) if args.existing else 0,
            rate=args.rate / 100, years=args.years,
            requested=parse_price(args.requested) if args.requested else None)

    from apt_engine import units
    print(f"\n  집값 {units.fmt_eok(price)} · {z.label} · 주택수 {args.house_count}")
    if not cap.checked:
        print(f"    대출 가능액: 확인 불가")
        print(f"    {cap.calc.intermediates.get('주의', '')}")
        return
    print(f"    이론상 LTV 한도   "
          f"{units.fmt_eok(cap.ltv_limit) if cap.ltv_limit else '확인 불가':>10s}")
    print(f"    DSR 한도          "
          f"{units.fmt_eok(cap.dsr_limit) if cap.dsr_limit else '확인 불가':>10s}")
    print(f"    실제 가능액       {units.fmt_eok(cap.available):>10s}   "
          f"← {cap.binding} 이 결정")
    if args.verbose:
        print("\n    ── 계산 근거 ──")
        for line in cap.calc.explain().splitlines():
            print(f"    {line}")


def cmd_cash(args):
    """실제 필요한 현금 (요구사항 27)."""
    from apt_engine import units
    from apt_engine.cash import equity as equity_mod
    from apt_engine.listing.provider import parse_price

    price = parse_price(args.price)
    with get_conn(args.db) as conn:
        row = _resolve_complex(conn, args)
        if row is None:
            return
        _print_complex_header(row)

        jeonse = parse_price(args.jeonse) if args.jeonse else None
        if jeonse is None:
            js = repo.latest_jeonse_snapshot(conn, row["id"], args.band or "84")
            jeonse = js["representative_deposit"] if js else None

        eq = equity_mod.compute(
            conn, price=price, as_of=args.as_of or _today(),
            house_count=args.house_count, lawd_cd=row["lawd_cd"],
            emd_name=row["emd_name"], exclusive_area_m2=args.area,
            jeonse_deposit=jeonse,
            loan_amount=parse_price(args.loan) if args.loan else None,
            repair_cost=parse_price(args.repair) if args.repair else 0,
            buffer_cost=parse_price(args.buffer) if args.buffer else 0)

    print(f"\n  ── 실제 필요한 현금 ──")
    for item in eq.items:
        sign = "+" if item.sign > 0 else "−"
        value = units.fmt_eok(item.amount) if item.known else "확인 불가"
        print(f"    {sign} {item.name:<18s} {value:>12s}"
              + (f"   {item.note}" if item.note else ""))
    print(f"    {'=':>2} {'실투자금':<18s} {eq.label:>12s}")
    if not eq.complete:
        print(f"\n    ⚠ {eq.calc.intermediates['주의']}")
    if args.verbose:
        print("\n    ── 계산 근거 ──")
        for line in eq.calc.explain().splitlines():
            print(f"    {line}")


def _resolve_complex(conn, args):
    matches = repo.find_complexes(conn, args.query)
    if not matches:
        print(f"'{args.query}' 로 찾은 단지가 없습니다.")
        return None
    if getattr(args, "complex_id", None):
        return conn.execute("SELECT * FROM complex WHERE id=?",
                            (args.complex_id,)).fetchone()
    if len(matches) > 1:
        print(f"'{args.query}' 로 {len(matches)}개 검색됨 — 첫 번째를 씁니다. "
              f"--complex-id 로 지정하세요.")
    return matches[0]


# ── 상대가치 (PHASE 4) ────────────────────────────────────────────────

def cmd_ladder(args):
    from apt_engine.relative import ladder as ladder_mod

    if args.action == "template":
        path = ladder_mod.write_template(args.path)
        print(f"가격사다리 서식을 만들었습니다: {path}\n"
              f"요구사항에 적어주신 축들을 넣어 뒀습니다. **lawd_cd 가 비어 있는 노드는 "
              f"비교단지 매칭에 쓰이지 않으니** 시군구코드를 채워 넣으세요.\n"
              f"축 순서·구성은 자유롭게 고치시면 됩니다 — 이건 데이터가 아니라 도메인 지식입니다.")
        return

    if args.action == "import":
        with get_conn(args.db) as conn:
            try:
                s = ladder_mod.import_csv(conn, args.path)
            except ladder_mod.LadderError as e:
                sys.exit(f"사다리 파일에 문제가 있습니다:\n  {e}")
        print(f"축 {s['axes']}개 · 노드 {s['nodes']}개 등록")
        return

    if args.action == "list":
        with get_conn(args.db) as conn:
            axes = ladder_mod.list_axes(conn)
            if not axes:
                print("등록된 축이 없습니다. `ladder template` 로 시작하세요.")
                return
            for a in axes:
                labels = ladder_mod.axis_labels(conn, a["id"])
                print(f"\n■ {a['name']}  ({a['node_count']}개 · {a['curated_by']})")
                print(f"  {' → '.join(labels)}")
                print(f"  근거: {a['rationale']}")


def cmd_relative(args):
    from apt_engine import area, units

    if args.action == "build":
        band = args.band or area.DEFAULT_BAND
        print(f"비교단지 선정 (전용 {area.label_of(band)})...")
        b = ingest.build_benchmarks(area_band=band, min_households=args.min_households,
                                    sido=args.sido, db_path=args.db)
        print(f"  대상 {b['targets']}개 · 비교단지 붙은 단지 {b['with_benchmarks']}개 · "
              f"관계 {b['relations']}개 · 근거부족 {b['no_ground']}개")
        if b["no_ground"] and b["no_ground"] >= b["targets"] * 0.5:
            print("  ※ 근거부족이 많습니다 — 가격사다리(`ladder import`)의 lawd_cd 가 "
                  "채워졌는지 확인하세요.")
        if not b["relations"]:
            return
        print("\n가격비율 시계열 계산...")
        r = ingest.build_ratios(area_band=band, db_path=args.db)
        print(f"  쌍 {r['pairs']}개 · 월별 비율 {r['ratios']}건 · "
              f"구간별 정상비율 {r['norms']}건 · 공통기간없음 {r['skipped']}개")
        return

    if args.action == "show":
        band = args.band or area.DEFAULT_BAND
        with get_conn(args.db) as conn:
            row = _resolve_complex(conn, args)
            if row is None:
                return
            _print_complex_header(row)
        view = ingest.relative_view(complex_id=row["id"], area_band=band, db_path=args.db)

        print(f"\n  ── 전용 {area.label_of(band)} 상대가치 ──")
        if not view["benchmarks"]:
            print("    비교단지: 확인 불가 (`relative build` 미실행 또는 근거 부족)")
            print("    가격사다리에 이 지역이 등록돼 있는지 확인하세요 — 사다리 없이는")
            print("    '비슷해 보여서' 골라주지 않습니다.")
            return

        for b in view["benchmarks"]:
            r, latest, norms = b["row"], b["latest"], b["norms"]
            print(f"\n    [{r['rank']}] {r['benchmark_name']} "
                  f"({regions.name_of(r['benchmark_lawd'])})"
                  f"  유사도 {r['similarity']:.2f}"
                  + (f" · {r['axis_name']} 축" if r["axis_name"] else ""))
            print(f"        선정근거: {b['reasons'].get('근거', '')}")
            top = sorted(b["reasons"].get("항목점수", {}).items(),
                         key=lambda kv: -kv[1])[:3]
            print(f"        항목: " + " · ".join(f"{k} {v:.2f}" for k, v in top))

            if latest is None:
                print("        가격비율: 확인 불가 (공통 기준월의 대표가격 없음)")
                continue
            print(f"        현재비율  {units.fmt_pct(latest['ratio']):>7s}"
                  f"   ({latest['as_of_ym']} · {latest['confidence']})")
            for key in ("all", "5y", "상승기", "하락기"):
                n = norms.get(key)
                if n:
                    print(f"        정상비율  {units.fmt_pct(n['median_ratio']):>7s}"
                          f"   ({key} · {n['sample_n']}개월 · "
                          f"{n['from_ym']}~{n['to_ym']})")
            base = norms.get("5y") or norms.get("all")
            if base:
                delta = latest["ratio"] - base["median_ratio"]
                verdict = ("가격격차가 벌어진 상태" if delta < -0.02
                           else "과거보다 좁혀진 상태" if delta > 0.02 else "과거 수준")
                print(f"        차이      {units.fmt_pct(delta, sign=True):>7s}"
                      f"   → {verdict}")

        if args.verbose and view["benchmarks"]:
            from apt_engine.trace import Calc
            print("\n    ── 선정 계산 근거 (1순위) ──")
            for line in Calc.from_json(
                    view["benchmarks"][0]["row"]["calc_trace"]).explain().splitlines():
                print(f"    {line}")


# ── 촉매 (PHASE 5) ────────────────────────────────────────────────────

def cmd_transit(args):
    from apt_engine.repo import catalyst as cat_repo

    if args.action == "template":
        path = cat_repo.write_transit_template(args.path)
        print(f"교통사업 서식을 만들었습니다: {path}\n"
              f"★ status 는 계획/예비타당성/기본계획/착공/공사중/개통예정/개통 중 하나입니다.\n"
              f"  '개통'으로 적으려면 opened_ym 이 반드시 있어야 합니다.\n"
              f"  status_date(단계가 된 날, 사실)와 expected_open_ym(개통 예정, 추정)을\n"
              f"  같은 칸에 넣지 마세요 — 계획이 확정 호재로 둔갑하는 가장 흔한 경로입니다.")
        return

    if args.action == "import":
        with get_conn(args.db) as conn:
            try:
                s = cat_repo.import_transit(conn, args.path)
            except cat_repo.CatalystImportError as e:
                sys.exit(f"교통 파일에 문제가 있습니다:\n  {e}")
        print(f"노선 {s['projects']}개 · 역 {s['stations']}개 등록"
              + (f" · 미검증 {s['unverified']}개" if s["unverified"] else ""))
        return

    if args.action == "list":
        with get_conn(args.db) as conn:
            rows = conn.execute(
                "SELECT p.name AS project, p.kind, s.name, s.status, s.status_date, "
                "s.expected_open_ym, s.opened_ym, s.lat, s.last_verified "
                "FROM transit_station s JOIN transit_project p ON p.id = s.project_id "
                "ORDER BY p.name, s.name").fetchall()
        if not rows:
            print("등록된 역이 없습니다. `transit template` 로 시작하세요.")
            return
        print(f"{'노선':10s} {'역':12s} {'단계':8s} {'개통':10s} 좌표 검증")
        for r in rows:
            opened = r["opened_ym"] or (f"예정 {r['expected_open_ym']}"
                                        if r["expected_open_ym"] else "미상")
            print(f"{r['project']:10s} {r['name']:12s} {r['status']:8s} {opened:10s} "
                  f"{'O' if r['lat'] else 'X'}    {'O' if r['last_verified'] else 'X'}")


def cmd_supply(args):
    from apt_engine.repo import catalyst as cat_repo

    if args.action == "template":
        path = cat_repo.write_supply_template(args.path)
        print(f"입주물량 서식을 만들었습니다: {path}\n"
              f"lat/lon 을 채우면 반경별(1/3/5km) 분석이 됩니다. 비우면 시군구 단위입니다.")
        return

    if args.action == "import":
        with get_conn(args.db) as conn:
            try:
                s = cat_repo.import_supply(conn, args.path)
            except cat_repo.CatalystImportError as e:
                sys.exit(f"공급 파일에 문제가 있습니다:\n  {e}")
        print(f"입주물량 {s['inserted']}건 등록"
              + (f" · 미검증 {s['unverified']}건" if s["unverified"] else ""))


def cmd_geocode(args):
    print("단지 좌표 조회 (V-World)...")
    s = ingest.geocode_complexes(limit=args.limit, db_path=args.db)
    print(f"\n대상 {s['targets']}개 · 채움 {s['filled']}개 · 실패 {s['failed']}개")
    if s["failed"]:
        print("실패한 건은 collection_log 에 사유가 남습니다 — 좌표를 추측하지 않습니다.")


def cmd_catalyst(args):
    from apt_engine import area, units

    if args.action == "build":
        as_of = args.as_of or _today()
        print(f"촉매 생성 (기준일 {as_of} · 투자기간 {args.years}년)...")
        s = ingest.build_catalysts(as_of=as_of, years=args.years,
                                   area_band=args.band or area.DEFAULT_BAND,
                                   db_path=args.db)
        print(f"  역세권 거리 {s['distances']:,}쌍 · 단지 {s['complexes']}개 · "
              f"촉매 {s['catalysts']}개 · 개통 선행사례 {s['analogues']}건")
        if not s["distances"]:
            print("  ※ 단지 좌표나 역 좌표가 없습니다 — `cli geocode` 와 "
                  "`transit import`(lat/lon 포함)를 먼저 하세요.")
        return

    if args.action == "show":
        as_of = args.as_of or _today()
        with get_conn(args.db) as conn:
            row = _resolve_complex(conn, args)
            if row is None:
                return
            _print_complex_header(row)
        view = ingest.catalyst_view(complex_id=row["id"], as_of=as_of, years=args.years,
                                    area_band=args.band or area.DEFAULT_BAND,
                                    db_path=args.db)

        print(f"\n  기준일 {as_of} · 투자기간 {args.years}년")
        if not view["has_coords"]:
            print("    단지 좌표: 확인 불가 → 역세권 거리를 계산할 수 없습니다 "
                  "(`cli geocode`)")

        print(f"\n  ── 교통 ──")
        if not view["stations"]:
            print("    가까운 역: 확인 불가 (역 데이터 미입력 또는 좌표 없음)")
        for st in view["stations"]:
            within, note = st.horizon_label(as_of=as_of, years=args.years)
            mark = "●" if st.opened else ("◐" if within else "△")
            print(f"    {mark} {st.project_name} {st.name}  "
                  f"직선 {st.meters:,.0f}m (도보추정 {st.walk_minutes}분)")
            print(f"        단계 {st.status}"
                  + (f" ({st.status_date})" if st.status_date else "")
                  + f" · 실현신뢰도 {st.confidence}"
                  + ("" if st.verified else " · ⚠ 미검증"))
            print(f"        {note}")

        print(f"\n  ── 공급 ──")
        sc = view["supply"]
        if sc is None or sc.value is None:
            print("    향후 공급: 확인 불가 (입주물량 데이터 미입력)")
        else:
            print(f"    집계기준  {sc.intermediates['집계 기준']}")
            print(f"    1~2년     {sc.intermediates['1~2년']}")
            print(f"    3~5년     {sc.intermediates['3~5년']}")
            if "기존 재고 대비" in sc.intermediates:
                print(f"    재고대비  {sc.intermediates['기존 재고 대비']}")

        print(f"\n  ── 요약 ──")
        summary = view["summary"]
        if summary.value is None:
            print(f"    {summary.intermediates.get('주의', '확인 불가')}")
        else:
            for key in ("기간 안", "기간 밖", "시점 미상"):
                value = summary.intermediates[key]
                text = " · ".join(value) if isinstance(value, list) else value
                print(f"    {key:8s} {text}")

        if view["analogues"]:
            print(f"\n  ── 개통 선행사례 (참고 범위) ──")
            for a in view["analogues"][:5]:
                print(f"    {a['project_name']} {a['station_name']} "
                      f"({a['opened_ym']} 개통) "
                      f"역세권/비역세권 {units.fmt_pct(a['ratio_before'])} → "
                      f"{units.fmt_pct(a['ratio_after'])} "
                      f"({units.fmt_pct(a['delta'], sign=True)})")
            print("    ※ 절대 상승률이 아니라 상대 비율 변화입니다. "
                  "미개통 노선에는 참고 범위로만 쓰세요.")

        if args.verbose and view["items"]:
            print("\n    ── 계산 근거 (1순위 촉매) ──")
            for line in view["items"][0].calc.explain().splitlines():
                print(f"    {line}")


# ── 파서 ──────────────────────────────────────────────────────────────

def _resolve_complex(conn, query, complex_id):
    """CLI 공통 — 이름으로 단지 하나를 고른다. 애매하면 목록만 보여준다."""
    if complex_id:
        row = conn.execute("SELECT * FROM complex WHERE id=?", (complex_id,)).fetchone()
        if row is None:
            print(f"단지 #{complex_id} 를 찾을 수 없습니다.")
        return row
    matches = repo.find_complexes(conn, query)
    if not matches:
        print(f"'{query}' 로 찾은 단지가 없습니다.")
        return None
    if len(matches) > 1:
        print(f"'{query}' 로 {len(matches)}개 단지가 검색됐습니다. --complex-id 로 지정하세요.\n")
        for m in matches[:20]:
            print(f"  [{m['id']:>6}] {m['name']:<28s} {regions.name_of(m['lawd_cd']):16s} "
                  f"{m['apt_households'] or '?'}세대 {m['approval_year'] or '?'}년")
        return None
    return conn.execute("SELECT * FROM complex WHERE id=?", (matches[0]["id"],)).fetchone()


def _redev_assumptions(conn, args, row, far_basis):
    """CLI 인자 + DB 참고치로 가정 한 벌을 만든다. 빠진 게 있으면 (None, 사유들)."""
    from apt_engine import area as area_mod, units
    from apt_engine.redev import feasibility as feas

    missing, notes = [], []

    # ── 평당 공사비 ──
    cost_per_py, cost_year, other_rate = args.cost_per_py, args.cost_base_year, None
    if cost_per_py:
        cost_per_py = int(units.from_manwon(cost_per_py))
        if not cost_year:
            missing.append("--cost-base-year (공사비 기준연도 없이 쓰지 않습니다)")
    else:
        ref = feas.cost_reference(conn, region=regions.sido_of(row["lawd_cd"]),
                                  allow_unverified=args.allow_unverified)
        if ref is None:
            missing.append("평당 공사비 — `redev template cost` 로 넣거나 --cost-per-py(만원/평)")
        else:
            cost_per_py, cost_year, other_rate, ev = ref
            notes.append(f"공사비 {cost_per_py:,}원/평 ({cost_year}년 기준, {ev.source})")
    if args.other_cost_rate is not None:
        other_rate = args.other_cost_rate
    if other_rate is None and cost_per_py:
        missing.append("기타사업비율 — cost 표의 other_cost_rate 또는 --other-cost-rate")

    # ── 일반분양가 ──
    new_price = None
    if args.new_price_py:
        new_price = int(round(units.from_manwon(args.new_price_py) / units.PYEONG_M2))
        notes.append(f"일반분양가 {args.new_price_py:g}만원/평 → {new_price:,}원/㎡")
    elif args.new_price_m2:
        new_price = int(args.new_price_m2)
    else:
        missing.append("일반분양가 — --new-price-py(만원/평) 또는 --new-price-m2(원/㎡)")

    # ── 세대당 분양면적 ──
    unit_area = args.new_unit_area
    if not unit_area:
        band = args.band or area_mod.DEFAULT_BAND
        try:
            exclusive = float(band)
        except ValueError:
            exclusive = None
        if exclusive:
            unit_area = round(exclusive * feas.SUPPLY_AREA_RATIO, 1)
            notes.append(f"세대당 분양면적 {unit_area:g}㎡ = 전용 {exclusive:g}㎡ × "
                         f"{feas.SUPPLY_AREA_RATIO} (가정)")
        else:
            missing.append("--new-unit-area (신축 세대당 분양면적 ㎡)")

    # ── 조합원 종전자산 ──
    prior = None
    if args.prior_asset:
        prior = int(units.from_eok(args.prior_asset))
    else:
        band = args.band or area_mod.DEFAULT_BAND
        snap = repo.latest_price_snapshot(conn, row["id"], band)
        if snap and snap["representative_price"]:
            prior = int(snap["representative_price"])
            notes.append(
                f"종전자산을 대표가격 {units.fmt_eok(prior)} 로 갈음 "
                f"({snap['as_of_ym']} {band}㎡) — 감정평가액은 통상 이보다 낮아 "
                f"실제 추가분담금은 더 클 수 있습니다")
        else:
            missing.append("조합원 종전자산 — --prior-asset(억) 또는 대표가격 스냅샷")

    member_count = args.member_count or row["apt_households"]
    if not member_count:
        missing.append("조합원수 — --member-count 또는 단지 세대수")

    project = None
    rental_ratio = args.rental_ratio
    from apt_engine.redev import stage as stage_mod
    project = stage_mod.load(conn, row["id"])
    if project:
        if rental_ratio is None and project.rental_ratio is not None:
            rental_ratio = project.rental_ratio
        if not args.member_count and project.member_count:
            member_count = project.member_count

    if missing:
        return None, missing, notes, project

    a = feas.Assumptions(
        far=far_basis.far, far_kind=far_basis.kind,
        cost_per_py=cost_per_py, cost_base_year=cost_year,
        new_price_per_m2=new_price, avg_new_unit_area_m2=unit_area,
        construction_area_factor=args.construction_factor,
        other_cost_rate=other_rate, member_discount=args.member_discount,
        prior_asset_per_member=prior, member_count=int(member_count),
        rental_ratio=rental_ratio or 0.0,
        prior_asset_total=(project.prior_asset_total if project else None))
    return a, [], notes, project


def cmd_redev(args):
    """재건축 사업성 — 1차 스크리닝과 2차 정밀계산 (PHASE 6)."""
    import json
    from apt_engine import area as area_mod, units
    from apt_engine.redev import (conversion, far as far_mod, feasibility as feas,
                                  scenario as scen, screening, stage as stage_mod)
    from apt_engine.repo import redev as redev_repo

    if args.action == "template":
        if not args.kind or not args.path:
            sys.exit("사용법: redev template <far|project|duration|cost|landarea> <파일>")
        path = redev_repo.write_template(args.kind, args.path)
        print(f"서식을 만들었습니다: {path}")
        if args.kind == "far":
            print("★ kind 칸을 반드시 구분해 적으세요 — 법정상한/조례/정비계획/역세권특례.\n"
                  "  법정 최대 용적률을 사업 용적률로 쓰면 사업성이 실제보다 크게 나옵니다.")
        if args.kind == "landarea":
            print("★ 대지면적은 이 엔진에서 가장 민감한 입력입니다. 건축물대장 총괄표제부\n"
                  "  (정부24 · 세움터)의 대지면적을 그대로 적고 출처를 남기세요.")
        return

    if args.action == "import":
        if not args.kind or not args.path:
            sys.exit("사용법: redev import <far|project|duration|cost|landarea> <파일>")
        with get_conn(args.db) as conn:
            try:
                if args.kind == "project":
                    s = redev_repo.import_projects(conn, args.path)
                elif args.kind == "landarea":
                    s = redev_repo.import_land_area(conn, args.path)
                else:
                    s = redev_repo.import_csv(conn, args.kind, args.path)
            except redev_repo.RedevImportError as e:
                sys.exit(f"파일에 문제가 있습니다:\n  {e}")
        print("  ".join(f"{k} {v}" for k, v in s.items()))
        return

    if args.action == "status":
        with get_conn(args.db) as conn:
            cov = redev_repo.coverage(conn)
        print("PHASE 6 입력 현황 (전체 / 검증됨)")
        for kind, c in cov.items():
            mark = "OK" if c["verified"] else ("미검증" if c["total"] else "비어 있음")
            print(f"  {kind:12s} {c['total']:>6,} / {c['verified']:>6,}   {mark}")
        print("\n검증되지 않은 값으로는 계산하지 않습니다. `--allow-unverified` 로 강제할 수는\n"
              "있지만 그 결과에는 '미검증' 표시가 붙습니다.")
        return

    if args.action == "screen":
        as_of = args.as_of or _today()
        with get_conn(args.db) as conn:
            found = screening.screen(conn, as_of=as_of, lawd_cd=args.lawd,
                                     min_age=args.min_age, max_far=args.max_far)
            saved = screening.save(conn, found, as_of=as_of)
        print(f"1차 스크리닝 (기준일 {as_of}) — 통과 {saved}개")
        print(f"  조건: 사용승인 {args.min_age}년 이상 · 현재 용적률 {args.max_far:g}% 이하 · "
              f"아파트 {screening.MIN_HOUSEHOLDS}세대 이상")
        no_land = sum(1 for c in found if c.land_share_m2 is None)
        if no_land:
            print(f"  ※ {no_land}개 단지는 대지면적이 없어 대지지분을 뺀 점수입니다 — "
                  f"낮은 게 아니라 자료가 없는 것입니다.")
        print(f"\n{'순위':>4} {'점수':>6}  {'단지':<24s} {'지역':<14s} {'연식':>4} "
              f"{'용적률':>7} {'대지지분':>9}  상태")
        for i, c in enumerate(found[:args.limit], start=1):
            share = f"{c.land_share_m2:.1f}㎡" if c.land_share_m2 else "확인 불가"
            far = f"{c.current_far:g}%" if c.current_far else "미상"
            print(f"{i:>4} {c.score:>6.3f}  {c.name[:24]:<24s} "
                  f"{regions.name_of(c.lawd_cd)[:14]:<14s} {c.age_years:>3}년 "
                  f"{far:>7} {share:>9}  {c.manual_status}")
        print("\n여기까지가 1차입니다. 사업성 금액은 만들지 않았습니다 —\n"
              "상위 후보에 대해 `redev template project/landarea` 로 정비계획·대지면적을\n"
              "넣은 뒤 `redev show` 를 실행하세요(2차).")
        return

    # `redev show 동아1단지` 처럼 위치인자를 하나만 준 경우, argparse 는 그것을
    # kind 로 받는다. show/mark 에서는 단지명으로 읽는다.
    query = args.query or args.path or args.kind

    if args.action == "mark":
        with get_conn(args.db) as conn:
            row = _resolve_complex(conn, query, args.complex_id)
            if row is None:
                return
            n = redev_repo.set_manual_status(conn, row["id"], status=args.status,
                                             note=args.note)
        print(f"{row['name']}: {args.status}" if n else
              "스크리닝 결과가 없습니다. `redev screen` 을 먼저 실행하세요.")
        return

    # ── show — 2차 정밀 ──
    as_of = args.as_of or _today()
    band = args.band or area_mod.DEFAULT_BAND
    with get_conn(args.db) as conn:
        row = _resolve_complex(conn, query, args.complex_id)
        if row is None:
            return
        _print_complex_header(row)

        # 1) 정비사업 단계 — 사실
        project = stage_mod.load(conn, row["id"])
        print("\n[정비사업 단계]")
        if project is None:
            print("  등록된 정비사업이 없습니다 — 확인 불가.")
            print("  오래된 단지라는 이유만으로 재건축 가능성을 숫자로 만들지 않습니다.")
            print("  `redev template project` 로 단계를 넣으세요.")
        else:
            print(f"  {project.project_type} · {project.stage}"
                  f"  (단계 변경일 {project.stage_date or '확인 불가'})"
                  + ("" if project.verified else "  ※ 미검증"))
            dur = stage_mod.remaining(conn, project,
                                      region=regions.sido_of(row["lawd_cd"]),
                                      allow_unverified=args.allow_unverified)
            print(f"  준공까지 남은 기간: {dur.label}")
            risk = stage_mod.delay_risk(project, as_of=as_of)
            print(f"  지연위험: {risk.level}")
            for r in risk.reasons:
                print(f"    · {r}")

        # 2) 용적률 — 종류를 절대 섞지 않는다
        print("\n[용적률]")
        print(f"  현재 용적률: {row['current_far']:g}%" if row["current_far"]
              else "  현재 용적률: 확인 불가")
        options = far_mod.available(conn, zoning=row["zoning"] or "", as_of=as_of,
                                    lawd_cd=row["lawd_cd"],
                                    sido=regions.sido_of(row["lawd_cd"]),
                                    allow_unverified=args.allow_unverified)
        planned = far_mod.planned(conn, row["id"])
        for b in ([planned] if planned else []) + options:
            print(f"  {b.kind:8s} {b.far:>6g}%   {b.caveat}")
        if not options and not planned:
            print(f"  기준 미입력 — 확인 불가 (용도지역: {row['zoning'] or '미입력'})")

        basis, why = far_mod.resolve(conn, row["id"], as_of=as_of, prefer=args.far_kind,
                                     allow_unverified=args.allow_unverified)
        if args.far:
            basis = far_mod.FarBasis(far=args.far, kind=args.far_kind or "사용자입력",
                                     zoning=row["zoning"] or "확인 불가", scope="사용자 지정",
                                     public_contribution_rate=None, verified=True,
                                     source_name="사용자 입력 용적률", source_url=None)
            why = "사용자가 --far 로 지정"
        print(f"  → 사업 용적률로 사용: "
              f"{basis.label if basis else '확인 불가'}  ({why})")

        # 3) 사업성 3구간
        print("\n[사업성 시나리오]")
        if basis is None:
            print("  용적률 기준이 없어 계산하지 않았습니다.")
            return
        a, missing, notes, project = _redev_assumptions(conn, args, row, basis)
        for n in notes:
            print(f"  · {n}")
        if a is None:
            print("  계산하지 않았습니다 — 다음 값이 없습니다:")
            for m in missing:
                print(f"    · {m}")
            print("\n  빠진 값을 그럴듯한 기본값으로 채우지 않습니다. "
                  "재건축에서 근거 없는 분담금이 가장 위험합니다.")
            return

        land_area = row["land_area_m2"]
        if not land_area:
            print("  대지면적 미입력 — 사업성을 계산할 수 없습니다.")
            print("  `redev template landarea` 로 건축물대장 대지면적을 넣으세요.")
            return

        band_result = scen.band(land_area_m2=land_area, base=a,
                                evidence=(basis.evidence,))
        print(f"  {band_result.label}")
        if band_result.calc:
            for key, detail in band_result.calc.intermediates["시나리오별"].items():
                print(f"    {key:4s} 용적률 {detail['용적률']:>7s} · "
                      f"공사비 {detail['평당공사비']:>12s}/평 · "
                      f"분양가 {detail['일반분양가']:>13s} → "
                      f"비례율 {detail['비례율']:>7s} · 분담금 {detail['추가분담금']:>9s}")
        # 단서는 시나리오마다 다르다. 기준 시나리오에서만 안 보이는 경고
        # (예: 보수 시나리오에서 조합원을 다 담지 못함)를 놓치지 않는다.
        seen = []
        for result in band_result.results.values():
            for c in result.caveats:
                if c not in seen:
                    seen.append(c)
        for c in seen:
            print(f"    ※ {c}")
        print(f"    ※ {scen.ADJUST_NOTE}")
        print(f"    ※ {feas.ASSUMPTION_NOTE}")

        # 4) 민감도
        sens = scen.sensitivity_calc(land_area_m2=land_area, base=a)
        print("\n[민감도 — 가정 하나를 ±20% 흔들면]")
        for factor, swing in sens.intermediates["변동폭"].items():
            print(f"  {factor:8s} {swing:>12s}")
        print(f"  가장 민감한 항목: {sens.intermediates['가장 민감한 항목']} "
              f"— 이 값부터 실제 자료로 확인하세요.")

        # 5) 신축전환원가
        base_result = band_result.results.get("기준")
        print("\n[신축전환원가와 재건축 마진]")
        if args.price is None:
            print("  --price(매수가, 억) 를 주면 신축전환원가를 계산합니다.")
        else:
            price = int(units.from_eok(args.price))
            years = None
            if project:
                dur = stage_mod.remaining(conn, project,
                                          region=regions.sido_of(row["lawd_cd"]),
                                          allow_unverified=args.allow_unverified)
                years = None if dur.months is None else round(dur.months / 12)
            future, future_note = conversion.future_value_of(a, per_m2=args.future_price_m2)
            conv = conversion.compute(
                conn, price=price, as_of=as_of, lawd_cd=row["lawd_cd"],
                emd_name=row["emd_name"],
                extra_charge=None if base_result is None else base_result.extra_charge,
                house_count=args.house_count, exclusive_area_m2=float(band)
                if band.isdigit() else None,
                years=years, loan_amount=int(units.from_eok(args.loan or 0)),
                loan_rate=args.loan_rate,
                annual_holding_cost=(int(units.from_manwon(args.holding_cost))
                                     if args.holding_cost else None),
                future_value=future, allow_unverified=args.allow_unverified,
                evidence=(basis.evidence,))
            for item in conv.items:
                amount = units.fmt_eok(item.amount) if item.known else "확인 불가"
                print(f"    {'+' if item.sign > 0 else '−'} {item.name:<18s} {amount:>10s}"
                      + (f"   {item.note}" if item.note else ""))
            print(f"    = 신축전환원가        {conv.label}")
            print(f"      준공 후 예상 가치    {units.fmt_eok(future)}  ({future_note})")
            print(f"      재건축 마진          {conv.margin_label}")

        # 6) 저장
        if args.save:
            for key, result in band_result.results.items():
                redev_repo.save_scenario(
                    conn, complex_id=row["id"], area_band=band, as_of=as_of,
                    scenario_key=key, assumptions=scen.variant(a, key), result=result,
                    calc_json=result.calc.to_json())
            print(f"\n시나리오 {len(band_result.results)}건을 저장했습니다 "
                  f"(등급 SCENARIO — 확정 금액이 아닙니다).")

        if args.verbose and band_result.calc:
            print("\n[계산 근거]")
            print(band_result.calc.explain())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m apt_engine.cli",
        description="수도권 아파트 상대가치 투자 분석 엔진 (PHASE 1)",
    )
    p.add_argument("--db", help=f"DB 경로 (기본: {config.APT_DB_PATH})")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init", help="DB 생성 + 마이그레이션 + 시군구 동기화")
    sub.add_parser("status", help="스키마·데이터·매칭 현황")

    pr = sub.add_parser("probe", help="API 원본 응답 확인 (필드명 검증)")
    pr.add_argument("what", choices=["trade", "rent", "kapt-list", "kapt-basis"])
    pr.add_argument("--lawd", default="11680", help="시군구코드 (기본 11680 강남구)")
    pr.add_argument("--ym", default="202607", help="거래월 YYYYMM")
    pr.add_argument("--kapt-code", help="kapt-basis 용 단지코드")

    co = sub.add_parser("collect", help="데이터 수집")
    co.add_argument("what", choices=["complexes", "trades", "rents"])
    co.add_argument("--sido", choices=["서울", "경기", "인천"], help="미지정시 수도권 전체")
    co.add_argument("--months", type=int, default=60, help="최근 N개월 (기본 60 = 5년)")
    co.add_argument("--no-basis", action="store_true",
                    help="complexes: 단지 목록만 받고 기본정보는 건너뜀(빠름)")

    ma = sub.add_parser("match", help="실거래를 단지에 붙인다")
    ma.add_argument("--rebuild", action="store_true", help="기존 매칭을 지우고 전부 다시")

    sn = sub.add_parser("snapshot", help="대표가격·전세가·전세가율 계산")
    sn.add_argument("--as-of", help="기준월 YYYYMM (기본: 이번 달)")
    sn.add_argument("--months", type=int, default=1,
                    help="기준월을 몇 개 만들지 (과거 시계열용, 기본 1)")
    sn.add_argument("--window", type=int, default=snap_mod.DEFAULT_WINDOW_MONTHS,
                    help="집계창 개월 수 (요구사항 2: 3~6개월, 기본 6)")
    sn.add_argument("--min-households", type=int, help="세대수 하한으로 대상 좁히기")
    sn.add_argument("--sido", choices=["서울", "경기", "인천"])

    pc = sub.add_parser("price", help="단지의 대표가격을 근거와 함께 보기")
    pc.add_argument("query", help="단지명 일부 (예: 동아1단지)")
    pc.add_argument("--complex-id", type=int, help="동명 단지가 여럿일 때 지정")
    pc.add_argument("--band", help="전용면적 밴드 (예: 84). 미지정시 전부")
    pc.add_argument("--verbose", action="store_true", help="계산 근거 전문 출력")

    li = sub.add_parser("listing", help="호가(매물) 입력·기록")
    li.add_argument("action", choices=["template", "import", "note"])
    li.add_argument("path", nargs="?", help="CSV/JSON 파일 경로")
    li.add_argument("--format", choices=["csv", "json"], default="csv")
    li.add_argument("--date", help="관측일 YYYY-MM-DD (기본: 오늘)")
    li.add_argument("--complex-id", type=int, help="note: 단지 ID")
    li.add_argument("--band", help="note: 전용면적 밴드")
    li.add_argument("--kind", default="협상가",
                    choices=["협상가", "임장관찰", "중개사확인", "기타"])
    li.add_argument("--price", help="note: 금액 (6.05 = 6.05억)")
    li.add_argument("--note", default="", help="note: 내용")
    li.add_argument("--source", default="", help="note: 누구에게 들었나(필수)")

    mk = sub.add_parser("market", help="호가 분포·괴리·변화·시장압력")
    mk.add_argument("query", help="단지명 일부")
    mk.add_argument("--complex-id", type=int)
    mk.add_argument("--band", help="전용면적 밴드 (기본 84)")
    mk.add_argument("--trade-type", default="매매", choices=["매매", "전세", "월세"])
    mk.add_argument("--window", type=int, default=30, help="변화 비교 기간(일)")
    mk.add_argument("--verbose", action="store_true")

    ru = sub.add_parser("rule", help="세법·규제·토허·대출 규칙 수기 입력")
    ru.add_argument("action", choices=["template", "import", "list", "verify", "status"])
    ru.add_argument("kind", nargs="?",
                    choices=["regulation", "permit", "tax", "loan", "cost"])
    ru.add_argument("path", nargs="?", help="CSV 파일 경로")
    ru.add_argument("--id", type=int, help="verify: 규칙 ID")
    ru.add_argument("--date", help="verify: 확인일 YYYY-MM-DD (기본 오늘)")

    rg = sub.add_parser("regulation", help="규제지역·토허 판정")
    rg.add_argument("query", help="단지명 일부")
    rg.add_argument("--complex-id", type=int)
    rg.add_argument("--as-of", help="기준일 YYYY-MM-DD (기본 오늘)")
    rg.add_argument("--scope", default="내국인", choices=["내국인", "외국인", "전체"])

    ln = sub.add_parser("loan", help="대출 가능액 (LTV·DSR 중 제한적인 값)")
    ln.add_argument("query", nargs="?", help="단지명 일부")
    ln.add_argument("--lawd", help="단지 대신 시군구코드로")
    ln.add_argument("--complex-id", type=int)
    ln.add_argument("--price", required=True, help="집값 (6.2 = 6.2억)")
    ln.add_argument("--house-count", type=int, default=0, help="취득 후 주택 수")
    ln.add_argument("--income", help="연소득 (1.0 = 1억)")
    ln.add_argument("--existing", help="기존 대출 연간 원리금")
    ln.add_argument("--requested", help="실제 받으려는 금액")
    ln.add_argument("--rate", type=float, default=4.0, help="금리 %% (기본 4.0)")
    ln.add_argument("--years", type=int, default=30)
    ln.add_argument("--as-of", help="기준일 YYYY-MM-DD")
    ln.add_argument("--verbose", action="store_true")

    ca = sub.add_parser("cash", help="실제 필요한 현금")
    ca.add_argument("query", help="단지명 일부")
    ca.add_argument("--complex-id", type=int)
    ca.add_argument("--price", required=True, help="매수가 (6.2 = 6.2억)")
    ca.add_argument("--house-count", type=int, default=1, help="취득 후 주택 수")
    ca.add_argument("--band", help="전용면적 밴드 (전세 대표가 조회용, 기본 84)")
    ca.add_argument("--area", type=float, help="전용면적 ㎡ (농특세 판정)")
    ca.add_argument("--jeonse", help="승계 전세보증금. 미지정시 대표 전세가 사용")
    ca.add_argument("--loan", help="주택담보대출액")
    ca.add_argument("--repair", help="수리비")
    ca.add_argument("--buffer", help="안전자금")
    ca.add_argument("--as-of", help="기준일 YYYY-MM-DD")
    ca.add_argument("--verbose", action="store_true")

    la = sub.add_parser("ladder", help="가격사다리 축 정의 (도메인 지식 수기 입력)")
    la.add_argument("action", choices=["template", "import", "list"])
    la.add_argument("path", nargs="?", help="CSV 파일 경로")

    rl = sub.add_parser("relative", help="비교단지 선정 · 가격비율")
    rl.add_argument("action", choices=["build", "show"])
    rl.add_argument("query", nargs="?", help="show: 단지명 일부")
    rl.add_argument("--complex-id", type=int)
    rl.add_argument("--band", help="전용면적 밴드 (기본 84)")
    rl.add_argument("--min-households", type=int, help="build: 세대수 하한")
    rl.add_argument("--sido", choices=["서울", "경기", "인천"])
    rl.add_argument("--verbose", action="store_true")

    tr = sub.add_parser("transit", help="교통사업 단계 입력 (계획/착공/개통 구분)")
    tr.add_argument("action", choices=["template", "import", "list"])
    tr.add_argument("path", nargs="?")

    sp = sub.add_parser("supply", help="입주물량 입력")
    sp.add_argument("action", choices=["template", "import"])
    sp.add_argument("path", nargs="?")

    gc = sub.add_parser("geocode", help="단지 좌표 채우기 (V-World)")
    gc.add_argument("--limit", type=int, help="한 번에 처리할 단지 수")

    ct = sub.add_parser("catalyst", help="촉매 생성·조회")
    ct.add_argument("action", choices=["build", "show"])
    ct.add_argument("query", nargs="?", help="show: 단지명 일부")
    ct.add_argument("--complex-id", type=int)
    ct.add_argument("--as-of", help="기준일 YYYY-MM-DD (기본 오늘)")
    ct.add_argument("--years", type=int, default=5, help="투자기간(년, 기본 5)")
    ct.add_argument("--band", help="전용면적 밴드 (기본 84)")
    ct.add_argument("--verbose", action="store_true")

    rd = sub.add_parser("redev", help="재건축 사업성 (1차 스크리닝 · 2차 정밀)")
    rd.add_argument("action",
                    choices=["template", "import", "status", "screen", "mark", "show"])
    rd.add_argument("kind", nargs="?",
                    help="template/import 대상: far|project|duration|cost|landarea")
    rd.add_argument("path", nargs="?", help="template/import 파일 경로")
    rd.add_argument("query", nargs="?", help="show/mark: 단지명 일부")
    rd.add_argument("--complex-id", type=int)
    rd.add_argument("--as-of", help="기준일 YYYY-MM-DD (기본 오늘)")
    rd.add_argument("--band", help="전용면적 밴드 (기본 84)")
    rd.add_argument("--lawd", help="screen: 시군구 코드로 한정")
    rd.add_argument("--limit", type=int, default=30, help="screen: 표시 개수")
    rd.add_argument("--min-age", type=int, default=redev_screening.MIN_AGE_YEARS,
                    help="screen: 최소 연식(년)")
    rd.add_argument("--max-far", type=float, default=redev_screening.MAX_CURRENT_FAR,
                    help="screen: 현재 용적률 상한(%%)")
    rd.add_argument("--status", default="조사중",
                    choices=["미조사", "조사중", "완료", "제외"], help="mark: 조사 상태")
    rd.add_argument("--note", help="mark: 메모")
    # 사업성 가정 — 전부 사용자가 바꿀 수 있다
    rd.add_argument("--far", type=float, help="사업 용적률(%%)을 직접 지정")
    rd.add_argument("--far-kind", choices=list(redev_far.KINDS),
                    help="어느 기준의 용적률을 쓸지")
    rd.add_argument("--cost-per-py", type=float, help="평당 공사비(만원/평)")
    rd.add_argument("--cost-base-year", type=int, help="공사비 기준연도")
    rd.add_argument("--other-cost-rate", type=float, help="기타사업비/공사비 비율")
    rd.add_argument("--new-price-py", type=float, help="일반분양가(만원/평)")
    rd.add_argument("--new-price-m2", type=int, help="일반분양가(원/㎡)")
    rd.add_argument("--new-unit-area", type=float, help="신축 세대당 분양면적(㎡)")
    rd.add_argument("--construction-factor", type=float,
                    default=redev_feas.CONSTRUCTION_AREA_RATIO,
                    help="공사연면적/용적률연면적 비율 (가정)")
    rd.add_argument("--member-discount", type=float, default=1.0,
                    help="조합원분양가/일반분양가. 기본 1.0 = 할인 미반영(보수적)")
    rd.add_argument("--rental-ratio", type=float, help="임대 세대 비율")
    rd.add_argument("--prior-asset", type=float, help="조합원 종전자산 평가액(억)")
    rd.add_argument("--member-count", type=int, help="조합원 세대수")
    # 신축전환원가
    rd.add_argument("--price", type=float, help="매수가(억)")
    rd.add_argument("--house-count", type=int, default=1)
    rd.add_argument("--loan", type=float, help="대출액(억)")
    rd.add_argument("--loan-rate", type=float, help="대출금리(예: 0.045)")
    rd.add_argument("--holding-cost", type=float, help="연간 보유비용(만원)")
    rd.add_argument("--future-price-m2", type=int, help="준공 후 예상 시세(원/㎡)")
    rd.add_argument("--save", action="store_true", help="시나리오를 DB에 저장")
    rd.add_argument("--allow-unverified", action="store_true",
                    help="미검증 규칙도 사용(결과에 표시됨)")
    rd.add_argument("--verbose", action="store_true")

    sub.add_parser("validate", help="요구사항 26 검증 규칙 실행")

    re_ = sub.add_parser("report", help="진단 리포트")
    re_.add_argument("kind", choices=["unmatched", "gaps"])
    re_.add_argument("--limit", type=int, default=30)
    return p


HANDLERS = {
    "init": cmd_init, "status": cmd_status, "probe": cmd_probe,
    "collect": cmd_collect, "match": cmd_match,
    "snapshot": cmd_snapshot, "price": cmd_price,
    "listing": cmd_listing, "market": cmd_market,
    "rule": cmd_rule, "regulation": cmd_regulation, "loan": cmd_loan, "cash": cmd_cash,
    "ladder": cmd_ladder, "relative": cmd_relative,
    "transit": cmd_transit, "supply": cmd_supply, "geocode": cmd_geocode,
    "catalyst": cmd_catalyst, "redev": cmd_redev,
    "validate": cmd_validate, "report": cmd_report,
}


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    handler = HANDLERS.get(args.cmd)
    if handler is None:
        p.print_help()
        return
    handler(args)


if __name__ == "__main__":
    main()
