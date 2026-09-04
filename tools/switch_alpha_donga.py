"""부평 동아1단지 76.7㎡ 4.6억 — 보유 vs 갈아타기 계산 서식.

확정된 값은 채워져 있고, 종인님이 아직 주지 않은 값은 USER_INPUT_REQUIRED 로 남아
있다. 그 값이 다 들어오기 전에는 SWITCH_ALPHA 를 계산하지 않는다(임의 가정 금지).

    실행:  .venv/Scripts/python.exe tools/switch_alpha_donga.py
    입력:  아래 HELD 의 USER_INPUT_REQUIRED 를 실제 값으로 바꾸고 다시 실행
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.invest import switch_alpha as sa  # noqa: E402

R = sa.USER_INPUT_REQUIRED

# ── 확정값 (종인님 2026-09-04) ─────────────────────────────────────
HELD = sa.HeldAsset(
    complex_id=482, name="부평 동아1단지 아파트", lawd_cd="28237", exclusive_area_m2=76.74,
    purchase_price=460_000_000,          # 실제 매수가
    house_count_at_purchase=2,           # 기존 1주택 + 추가 취득 = 일시적 2주택
    temporary_two_home=True,
    # ── 아직 못 받은 값 — 임의 가정 금지 ──────────────────────────
    purchase_date=R,                     # 계약일/잔금일
    mortgage_amount=R,                   # 주택담보대출 금액 (없으면 0)
    interest_rate=R,                     # 대출금리 (예: 0.042)
    repayment_type=R,                    # 원리금균등 / 원금균등 / 만기일시
    assumed_deposit=R,                   # 전세 승계 보증금 (없으면 0)
    occupancy=R,                         # 실거주 / 임대 / 전세승계 (기본 시나리오는 비거주)
    other_home_disposal_date=R,          # 기존 1주택 처분 시점
    current_market_price=R,              # 지금 팔면 받을 가격 (저층 실거래 기준)
)
HOLDING_YEARS = 5
HELD_SALE_PRICE_5Y = None                # 5년 뒤 매도가 시나리오 — 엔진 Base/Bear/Bull 에서 가져올 것
MOVING_COST = R                          # 수리/이사비
CASH_YIELD = R                           # 남는 현금의 연 수익률 (예금·MMF 등)

# 대안은 DISCOVERY_REGISTRY_642 의 Settlement 통과 후보에서 '지금 살 수 있는 가격' 으로 채운다.
ALTERNATIVES: list[sa.Alternative] = []


def main() -> int:
    missing = HELD.missing()
    print("부평 동아1단지 76.7㎡ · 보유 vs 갈아타기")
    print(f"  확정값: 매수가 4.60억 · 일시적 2주택 · 비교기간 {HOLDING_YEARS}년 · 기본 비거주")
    if missing or HELD_SALE_PRICE_5Y is None or MOVING_COST == R or CASH_YIELD == R or not ALTERNATIVES:
        print("\n  아직 계산하지 않습니다 — 아래 값이 비어 있습니다 (임의 가정 금지):")
        for m in missing:
            print(f"    · HELD.{m}")
        if HELD_SALE_PRICE_5Y is None:
            print("    · HELD_SALE_PRICE_5Y (5년 뒤 매도가 시나리오)")
        if MOVING_COST == R:
            print("    · MOVING_COST (수리/이사비)")
        if CASH_YIELD == R:
            print("    · CASH_YIELD (남는 현금 연 수익률)")
        if not ALTERNATIVES:
            print("    · ALTERNATIVES (Settlement 통과 대안 + 지금 살 수 있는 가격)")
        print("\n  갈아타기에서 세는 비용:", " · ".join(sa.SWITCH_COST_ITEMS))
        return 0
    with get_conn() as conn:
        for alt in ALTERNATIVES:
            r = sa.compare(conn, HELD, alt, as_of="2026-09-04", holding_years=HOLDING_YEARS,
                           held_sale_price=HELD_SALE_PRICE_5Y, moving_cost=MOVING_COST,
                           cash_yield=CASH_YIELD, allow_unverified=False)
            print(f"\n  vs {alt.name}: {r.label}")
            for k, v in r.cost_items.items():
                print(f"      {k:16s} {v:,}" if isinstance(v, int) else f"      {k:16s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
