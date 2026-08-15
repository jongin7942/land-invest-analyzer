"""Phase 1 진입점: 경기도 토지 실거래가 수집.

사용 예:
    python main.py --months 3                 # 최근 3개월, 경기도 전 시군구
    python main.py --sigungu 41590 --ym 202607  # 화성시, 2026년 7월
    python main.py --stats                     # 수집 현황
"""
import argparse
import sys
import time
from datetime import date

import config
from collectors.land_trade import LandTradeError, fetch_month
from data.gyeonggi_sigungu import GYEONGGI_SIGUNGU, all_codes, name_of
from db import schema


def recent_yms(n: int) -> list[str]:
    """오늘 기준 최근 n개월의 YYYYMM 목록(과거→현재)."""
    today = date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def collect(sigungu_codes: list[str], yms: list[str]):
    key = config.require_data_go_kr_key()
    schema.init_db()

    total_new = 0
    total_fetched = 0
    for code in sigungu_codes:
        for ym in yms:
            try:
                rows = fetch_month(key, code, ym)
            except LandTradeError as e:
                print(f"  [!] {name_of(code)} {ym} 실패: {e}")
                continue
            except Exception as e:  # noqa: BLE001 - 네트워크/파싱 등 계속 진행
                print(f"  [!] {name_of(code)} {ym} 예외: {e}")
                continue

            new = schema.upsert_land_trades(rows)
            total_new += new
            total_fetched += len(rows)
            print(f"  {name_of(code):14s} {ym}  조회 {len(rows):4d}건 / 신규 {new:4d}건")
            time.sleep(0.1)

    print(f"\n완료: 총 조회 {total_fetched}건, 신규 저장 {total_new}건 → {config.DB_PATH}")


def show_stats():
    schema.init_db()
    s = schema.stats()
    print(f"총 {s['total']}건  (거래일 {s['ymd_range'][0]} ~ {s['ymd_range'][1]})\n")
    print("시군구별:")
    for name, c in s["by_sgg"]:
        print(f"  {name or '(미상)':16s} {c}")


def main(argv=None):
    p = argparse.ArgumentParser(description="경기도 토지 실거래가 수집 (Phase 1)")
    p.add_argument("--months", type=int, help="최근 N개월 수집 (경기도 전 시군구)")
    p.add_argument("--sigungu", help="특정 시군구 LAWD_CD (예: 41590)")
    p.add_argument("--ym", help="특정 거래월 YYYYMM (예: 202607)")
    p.add_argument("--stats", action="store_true", help="수집 현황만 출력")
    args = p.parse_args(argv)

    if args.stats:
        show_stats()
        return

    # 대상 시군구 결정
    if args.sigungu:
        if args.sigungu not in GYEONGGI_SIGUNGU:
            print(f"경고: {args.sigungu} 는 경기도 시군구 목록에 없습니다. 그래도 시도합니다.")
        codes = [args.sigungu]
    else:
        codes = all_codes()

    # 대상 월 결정
    if args.ym:
        yms = [args.ym]
    elif args.months:
        yms = recent_yms(args.months)
    else:
        yms = recent_yms(1)

    print(f"대상: 시군구 {len(codes)}개 × 월 {len(yms)}개 ({yms[0]}~{yms[-1]})\n")
    collect(codes, yms)


if __name__ == "__main__":
    sys.exit(main())
