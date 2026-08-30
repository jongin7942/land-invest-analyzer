"""전용면적 밴드 테스트 — 요구사항 26-4("84㎡ 분석에 59/74/101 섞지 말 것").

경계값이 이 테스트의 전부다. 84 밴드가 한 뼘만 넓어져도 국민평형 비교가 오염된다.
"""
import pytest

from apt_engine import area


class TestKookminBand:
    """기본 비교면적 = 전용 80~85㎡ (사용자 지정)."""

    @pytest.mark.parametrize("m2", [80.0, 82.5, 84.92, 84.97, 84.99, 84.9999])
    def test_80에서_85미만은_84밴드(self, m2):
        assert area.band_of(m2) == "84"

    @pytest.mark.parametrize("m2", [79.99, 85.0, 85.01, 88.9])
    def test_경계_밖은_84밴드가_아니다(self, m2):
        assert area.band_of(m2) != "84"

    def test_85_이상은_별도_밴드로_분리된다(self):
        # 35평형(85~90)을 국민평형에 합치면 비교가 오염된다.
        assert area.band_of(84.99) == "84"
        assert area.band_of(85.00) == "88"

    def test_기본_밴드는_84(self):
        assert area.DEFAULT_BAND == "84"

    def test_84밴드_범위(self):
        assert area.range_of("84") == (80.0, 85.0)


class TestOtherBands:
    @pytest.mark.parametrize("m2,band", [
        (39.9, "u40"), (40.0, "45"), (49.9, "45"),
        (50.0, "59"), (59.97, "59"), (59.99, "59"),
        (60.0, "66"), (74.9, "74"), (79.99, "74"),
        (90.0, "97"), (101.9, "101"), (114.5, "114"),
        (129.0, "129"), (145.0, "145"), (200.0, "o165"),
    ])
    def test_밴드_경계(self, m2, band):
        assert area.band_of(m2) == band

    def test_밴드는_겹치지_않고_빈틈도_없다(self):
        prev_hi = 0.0
        for lo, hi, _, _ in area.BANDS:
            assert lo == prev_hi, f"{lo} 앞에 빈틈/겹침"
            prev_hi = hi
        assert prev_hi == float("inf")


class TestGuards:
    @pytest.mark.parametrize("bad", [0, -1.0, None])
    def test_유효하지_않은_면적은_에러(self, bad):
        # 조용히 넘기면 엉뚱한 밴드에 섞인다.
        with pytest.raises(area.AreaBandError):
            area.band_of(bad)

    def test_모르는_밴드코드는_에러(self):
        with pytest.raises(area.AreaBandError):
            area.range_of("999")

    def test_same_band_는_섞임을_잡는다(self):
        assert area.same_band(84.92, 84.97, 84.99) is True
        assert area.same_band(84.97, 59.97) is False   # 요구사항 26-4 위반 상황
        assert area.same_band(84.99, 85.01) is False   # 국민평형 경계
        assert area.same_band() is True
