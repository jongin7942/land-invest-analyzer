"""단위계 테스트.

요구사항 34의 테스트 목록에는 없지만, 그 목록 전부(취득세·LTV·Initial Equity·IRR·
분담금)가 금액 위에서 돌아간다. 단위가 흔들리면 그 테스트들이 전부 무의미해진다.
"""
import pytest

from apt_engine import units as u


class TestMoneyParsing:
    def test_억_원_변환은_부동소수_오차가_없다(self):
        # 11.2 * 1e8 을 float 로 하면 1119999999.9999999 가 된다.
        assert u.from_eok(11.2) == 1_120_000_000
        assert u.from_eok("11.2") == 1_120_000_000
        assert u.from_eok(0.7) == 70_000_000

    def test_만원_원_변환(self):
        # 국토부 실거래가 API 의 거래금액 단위가 만원이다.
        assert u.from_manwon(112_000) == 1_120_000_000
        assert u.from_manwon(1) == 10_000

    def test_변환_결과는_항상_int(self):
        for v in (u.from_eok(11.25), u.from_manwon(1234.5), u.won_round(1.4)):
            assert isinstance(v, int)

    def test_반올림은_절반_올림(self):
        assert u.won_round(1.5) == 2
        assert u.won_round(1.4) == 1
        assert u.won_round(-1.5) == -2  # 0에서 멀어지는 방향(대칭)


class TestWonGate:
    """as_won 은 엔진 경계에서 float 금액이 흘러드는 걸 막는 게이트다."""

    def test_int_는_통과(self):
        assert u.as_won(1_120_000_000) == 1_120_000_000

    def test_float_는_거부(self):
        with pytest.raises(TypeError, match="원 단위 int"):
            u.as_won(1_120_000_000.0)

    def test_bool_은_거부된다(self):
        # bool 은 int 의 서브클래스라 그냥 두면 True 가 1원으로 통과한다.
        with pytest.raises(TypeError):
            u.as_won(True)

    def test_문자열도_거부(self):
        with pytest.raises(TypeError):
            u.as_won("1120000000")


class TestFormatting:
    def test_억_표기는_불필요한_0을_안_붙인다(self):
        assert u.fmt_eok(1_120_000_000) == "11.2억"
        assert u.fmt_eok(1_100_000_000) == "11억"
        assert u.fmt_eok(1_125_000_000) == "11.25억"

    def test_큰_금액은_천단위_구분(self):
        assert u.fmt_eok(123_400_000_000) == "1,234억"

    def test_0원(self):
        assert u.fmt_eok(0) == "0억"
        assert u.fmt_won(0) == "0원"

    def test_만원_원_표기(self):
        assert u.fmt_manwon(112_000_000) == "11,200만원"
        assert u.fmt_won(1_120_000_000) == "1,120,000,000원"


class TestArea:
    def test_평_변환은_기존_analysis_모듈과_같은_상수를_쓴다(self):
        from analysis.price_baseline import PYEONG_M2 as LAND_PYEONG
        assert u.PYEONG_M2 == LAND_PYEONG

    def test_평_왕복(self):
        assert u.from_pyeong(u.to_pyeong(84.97)) == pytest.approx(84.97)

    def test_전용_84_는_약_25평(self):
        assert u.to_pyeong(84.97) == pytest.approx(25.7, abs=0.1)

    def test_면적_표기(self):
        assert u.fmt_m2(84.97) == "84.97㎡"
        assert u.fmt_m2(84.0) == "84㎡"
        assert u.fmt_pyeong(84.97) == "26평"


class TestRatio:
    def test_비율은_0에서_1_사이로_저장한다(self):
        assert u.from_pct(7.1) == pytest.approx(0.071)
        assert u.to_pct(0.071) == pytest.approx(7.1)

    def test_퍼센트_표기(self):
        assert u.fmt_pct(0.071) == "7.1%"
        assert u.fmt_pct(0.6612, digits=1) == "66.1%"
        assert u.fmt_pct(-0.033, sign=True) == "-3.3%"
        assert u.fmt_pct(0.033, sign=True) == "+3.3%"
