"""아파트 엔진 CLI.

    # 준비
    python -m apt_engine.cli init                     # DB 생성 + 마이그레이션
    python -m apt_engine.cli probe trade              # API 원본 응답 확인(필드명 검증)

    # 수집 — 순서가 중요하다. 단지가 먼저 있어야 실거래를 붙일 수 있다.
    python -m apt_engine.cli collect complexes                  # K-apt 단지 (수도권 전체)
    python -m apt_engine.cli collect trades --months 60         # 매매 5년치
    python -m apt_engine.cli collect rents  --months 60         # 전월세 5년치
    python -m apt_engine.cli match                              # 단지 매칭

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


# ── 파서 ──────────────────────────────────────────────────────────────

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

    sub.add_parser("validate", help="요구사항 26 검증 규칙 실행")

    re_ = sub.add_parser("report", help="진단 리포트")
    re_.add_argument("kind", choices=["unmatched", "gaps"])
    re_.add_argument("--limit", type=int, default=30)
    return p


HANDLERS = {
    "init": cmd_init, "status": cmd_status, "probe": cmd_probe,
    "collect": cmd_collect, "match": cmd_match,
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
