"""아파트 엔진 CLI (PHASE 0).

    python -m apt_engine.cli init      # 마이그레이션 적용 (없으면 apt_invest.db 생성)
    python -m apt_engine.cli status    # DB 경로 · 스키마 버전 · 테이블 현황

토지 파이프라인(main.py / analyze.py / pipeline.py)과 완전히 별개다.
이 명령은 land_invest.db 를 열지 않는다.
"""
from __future__ import annotations

import argparse

import config
from apt_engine import ENGINE_VERSION
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn


def cmd_init(args):
    applied = mig.migrate(args.db)
    if applied:
        print(f"마이그레이션 적용: {', '.join(f'{v:03d}' for v in applied)}")
    else:
        print("이미 최신 스키마입니다.")
    cmd_status(args)


def cmd_status(args):
    s = mig.status(args.db)
    print(f"\nDB       : {s['db_path']}")
    print(f"엔진버전 : {ENGINE_VERSION}")
    print(f"스키마   : {s['version']:03d} / 최신 {s['latest']:03d}"
          + (f"  (미적용 {len(s['pending'])}건 — `init` 실행 필요)" if s["pending"] else ""))

    if not s["tables"]:
        print("테이블   : 없음 (python -m apt_engine.cli init)")
        return
    print("테이블   :")
    with get_conn(s["db_path"]) as conn:
        for t in s["tables"]:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<20s} {n:>10,d} 행")


def main(argv=None):
    p = argparse.ArgumentParser(description="수도권 아파트 투자분석 엔진 (PHASE 0)")
    p.add_argument("--db", help=f"DB 경로 (기본: {config.APT_DB_PATH})")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("init", help="마이그레이션 적용")
    sub.add_parser("status", help="현황 출력")
    args = p.parse_args(argv)

    if args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "status":
        cmd_status(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
