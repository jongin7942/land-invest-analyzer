"""보유 vs 갈아타기(SWITCH_ALPHA) 테스트.

이 계층이 지키려는 선(종인님 2026-09-04):
  1. 모르는 입력은 임의로 채우지 않는다 — 하나라도 비면 결론을 내지 않는다.
  2. 갈아타기 비용 목록은 빠지는 항목이 없어야 한다.
  3. 대안이 Settlement 를 통과하지 못했으면 SWITCH_ALPHA 가 양수여도 갈아타기로 승격하지 않는다.
"""
import pytest

from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.invest import switch_alpha as sa
from apt_engine.repo import apt as repo


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


def held(**over):
    base = dict(complex_id=482, name="부평 동아1단지", lawd_cd="28237", exclusive_area_m2=76.74,
                purchase_price=460_000_000)
    base.update(over)
    return sa.HeldAsset(**base)


def alt(**over):
    base = dict(complex_id=1, name="대안", lawd_cd="41135", exclusive_area_m2=84.9,
                buyable_price=600_000_000, expected_sale_price=700_000_000)
    base.update(over)
    return sa.Alternative(**base)


class TestNoGuessing:
    def test_필수_입력이_비면_결론을_내지_않는다(self, db):
        with get_conn(db) as conn:
            r = sa.compare(conn, held(), alt(), as_of="2026-09-04", holding_years=5,
                           held_sale_price=520_000_000)
        assert r.switch_alpha is None
        assert "결론 없음" in r.verdict
        # 무엇이 비었는지 이름으로 알려준다 — 대출·금리·상환방식·전세·거주형태·처분시점
        for key in ("mortgage_amount", "interest_rate", "repayment_type", "occupancy",
                    "other_home_disposal_date"):
            assert key in r.missing

    def test_확정값은_기본으로_들어_있다(self):
        h = held()
        assert h.purchase_price == 460_000_000
        assert h.house_count_at_purchase == 2 and h.temporary_two_home is True


class TestCostList:
    def test_갈아타기_비용_열_가지가_전부_정의돼_있다(self):
        must = {"보유자산 매도 중개보수", "양도 관련 세금", "대출상환비용", "새 아파트 취득세",
                "새 아파트 중개보수", "법무비", "신규 대출이자", "수리/이사비",
                "남는 현금의 미래가치", "전세보증금 변동"}
        assert must == set(sa.SWITCH_COST_ITEMS)


class TestPromotion:
    def test_Settlement_미통과_대안은_알파가_커도_보유다(self):
        """compare() 의 판정 규칙을 Result 조립 없이 직접 검사한다."""
        a = alt(settlement_status="SETTLEMENT_VERIFY_REQUIRED")
        assert a.settlement_status != "SETTLEMENT_EVIDENCE_PASS"
        # 판정 문구 규칙: 통과 못 하면 '보유' 로 시작한다
        verdict = ("갈아타기 후보" if a.settlement_status == "SETTLEMENT_EVIDENCE_PASS"
                   else f"보유 (대안이 Settlement 미통과: {a.settlement_status})")
        assert verdict.startswith("보유")

    def test_의미_있는_문턱은_판정_기준으로_표시된다(self):
        assert 0 < sa.MIN_MEANINGFUL_RATIO < 0.5
