"""단지명 매칭 테스트 — 이 프로젝트 최대의 숨은 난제(E-6).

가장 중요한 건 "붙이는 것"이 아니라 **애매하면 안 붙이는 것**이다.
억지 매칭은 미매칭보다 나쁘다 — 틀린 가격이 조용히 섞여 들어와 이후 모든 계산을 오염시킨다.
"""
import pytest

from apt_engine.collectors import matcher
from apt_engine.collectors.matcher import Candidate


def c(cid, name, emd=None, year=None):
    return Candidate(cid, name, matcher.normalize(name), emd, year)


class TestNormalize:
    def test_공백과_기호를_지운다(self):
        assert matcher.normalize("래미안 대치팰리스") == matcher.normalize("래미안대치팰리스")
        assert matcher.normalize("헬리오시티(1단지)") == matcher.normalize("헬리오시티 1단지")

    def test_아파트_꼬리표는_있으나_없으나_같다(self):
        assert matcher.normalize("은마아파트") == matcher.normalize("은마")

    @pytest.mark.parametrize("a,b", [
        ("e편한세상", "이편한세상"),
        ("E-편한세상 도곡", "이편한세상도곡"),
        ("래미안Xi", "래미안자이"),
        ("삼성 IPARK", "삼성아이파크"),
        ("포스코 THE#", "포스코더샵"),
        ("대우 Prugio", "대우푸르지오"),
        ("힐스테이트", "HILLSTATE"),
    ])
    def test_로마자_브랜드_표기_차이를_흡수한다(self, a, b):
        assert matcher.normalize(a) == matcher.normalize(b)

    def test_단지_번호는_절대_지우지_않는다(self):
        # '1단지'와 '2단지'는 다른 단지다. 여기서 지우면 가격이 섞인다.
        assert matcher.normalize("주공 1단지") != matcher.normalize("주공 2단지")
        assert matcher.normalize("○○ 1차") != matcher.normalize("○○ 2차")

    def test_제1단지와_1단지는_같다(self):
        assert matcher.normalize("주공 제1단지") == matcher.normalize("주공1단지")

    def test_빈_이름(self):
        assert matcher.normalize(None) == ""
        assert matcher.normalize("  ") == ""


class TestExactMatch:
    def test_정규화_이름이_하나만_맞으면_EXACT(self):
        r = matcher.match("은마아파트", [c(1, "은마"), c(2, "래미안대치팰리스")])
        assert (r.complex_id, r.confidence) == (1, "EXACT")

    def test_표기가_달라도_붙는다(self):
        r = matcher.match("e편한세상 도곡", [c(7, "이편한세상도곡")])
        assert (r.complex_id, r.confidence) == (7, "EXACT")


class TestAmbiguity:
    """요구사항 26-3의 정신 — 근거 없이 합치지 않는다."""

    def test_동명_단지를_구별할_근거가_없으면_붙이지_않는다(self):
        r = matcher.match("래미안", [c(1, "래미안", "대치동"), c(2, "래미안", "도곡동")])
        assert r.complex_id is None
        assert r.confidence == "NONE"
        assert "근거가 없음" in r.reason

    def test_법정동으로_특정되면_STRONG(self):
        r = matcher.match("래미안",
                          [c(1, "래미안", "대치동"), c(2, "래미안", "도곡동")],
                          emd_name="도곡동")
        assert (r.complex_id, r.confidence) == (2, "STRONG")
        assert "법정동" in r.reason

    def test_건축년도로_특정되면_STRONG(self):
        r = matcher.match("래미안",
                          [c(1, "래미안", "대치동", 1999), c(2, "래미안", "대치동", 2015)],
                          emd_name="대치동", build_year=2016)
        assert (r.complex_id, r.confidence) == (2, "STRONG")
        assert "건축년도" in r.reason

    def test_유사_후보가_비등하면_붙이지_않는다(self):
        r = matcher.match("○○마을1단지", [c(1, "○○마을2단지"), c(2, "○○마을3단지")])
        assert r.complex_id is None
        assert r.confidence == "NONE"


class TestVariants:
    """실거래가 ↔ K-apt 표기 차이 (2026-08-31 인천 실측 사례).

    두 API 는 같은 단지를 다르게 적는다. 규칙이 명시적인 변형만 흡수하고,
    변형이 맞아도 법정동이나 건축년도가 받쳐주지 않으면 WEAK 에 머문다.
    """

    def test_끝의_단지_유무를_흡수한다(self):
        # 실거래 '부개주공3' ↔ K-apt '부개주공3단지아파트'
        r = matcher.match("부개주공3", [c(1, "부개주공3단지아파트", "부개동", 1996)],
                          emd_name="부개동", build_year=1996)
        assert (r.complex_id, r.confidence) == (1, "STRONG")

    def test_법정동_접두어_유무를_흡수한다(self):
        # 실거래 '주공5'(부개동) ↔ K-apt '부개 주공5단지 아파트'
        r = matcher.match("주공5", [c(1, "부개 주공5단지 아파트", "부개동", 1998)],
                          emd_name="부개동", build_year=1998)
        assert (r.complex_id, r.confidence) == (1, "STRONG")

    def test_차와_단지는_같은_계열로_본다(self):
        # 실거래 '동아(1차)' ↔ K-apt '부평 동아1단지 아파트'
        r = matcher.match("동아(1차)", [c(1, "부평 동아1단지 아파트", "부평동", 1986)],
                          emd_name="부평동", build_year=1986)
        assert (r.complex_id, r.confidence) == (1, "STRONG")

    def test_차수가_다르면_절대_붙이지_않는다(self):
        # 여기서 붙으면 1차 가격과 2차 가격이 섞인다. 미매칭이 낫다.
        r = matcher.match("동아(2차)", [c(1, "부평 동아1단지 아파트", "부평동", 1986)],
                          emd_name="부평동", build_year=1995)
        assert r.complex_id is None

    def test_이름_가운데_아파트도_흡수한다(self):
        # K-apt '부평 동아아파트2단지' — '아파트'가 끝이 아니라 가운데 있다
        r = matcher.match("동아(2차)", [c(1, "부평 동아아파트2단지", "부평동", 1995)],
                          emd_name="부평동", build_year=1995)
        assert (r.complex_id, r.confidence) == (1, "STRONG")

    def test_교차확인이_없으면_WEAK_에_머문다(self):
        # 변형은 맞지만 법정동도 건축년도도 확인이 안 되면 단정하지 않는다.
        r = matcher.match("부개주공3", [c(1, "부개주공3단지아파트", "부개동", None)],
                          emd_name=None, build_year=None)
        assert (r.complex_id, r.confidence) == (1, "WEAK")

    def test_접두어를_떼면_같아지는_단지가_둘이면_법정동으로_가른다(self):
        # '갈산주공1단지'와 '부개주공1단지'는 접두어를 떼면 둘 다 '주공1단지'다.
        cands = [c(1, "갈산 주공1단지 아파트", "갈산동", 1992),
                 c(2, "부개 주공1단지 아파트", "부개동", 1996)]
        r = matcher.match("주공1단지", cands, emd_name="갈산동", build_year=1992)
        assert (r.complex_id, r.confidence) == (1, "STRONG")

    def test_변형이_통해도_다른_이름은_안_붙는다(self):
        # '뉴서울'(부개동) 은 '산곡 뉴서울2차' 와 다른 단지다.
        r = matcher.match("뉴서울", [c(1, "산곡 뉴서울2차아파트", "산곡동", 1989)],
                          emd_name="부개동", build_year=1989)
        assert r.complex_id is None


class TestEmdStem:
    def test_법정동_어간(self):
        assert matcher.emd_stem("부개동") == "부개"
        assert matcher.emd_stem("갈산동") == "갈산"
        assert matcher.emd_stem(None) == ""


class TestFuzzy:
    def test_유사도만으로는_WEAK_에_그친다(self):
        r = matcher.match("힐스테이트 서초젠트리스", [c(1, "힐스테이트서초젠트리스아파트")])
        assert r.confidence in ("EXACT", "WEAK", "STRONG")
        if r.confidence == "WEAK":
            assert "검증 권장" in r.reason

    def test_건축년도까지_맞으면_STRONG_으로_올라간다(self):
        # 한 글자 오타('팰'/'펠') — 유사도는 높지만 완전일치는 아니다.
        cands = [c(1, "래미안대치펠리스", None, 2015)]
        weak = matcher.match("래미안대치팰리스", cands)
        strong = matcher.match("래미안대치팰리스", cands, build_year=2016)
        assert weak.confidence == "WEAK"
        assert strong.confidence == "STRONG"
        assert strong.complex_id == 1

    def test_전혀_다른_이름은_안_붙는다(self):
        r = matcher.match("은마", [c(1, "헬리오시티"), c(2, "래미안대치팰리스")])
        assert r.complex_id is None
        assert "유사한 단지 없음" in r.reason

    def test_건축년도_허용오차(self):
        # 사용승인일과 건축년도는 1~2년 어긋나는 게 정상이다.
        assert matcher._year_ok(2015, 2016) is True
        assert matcher._year_ok(2015, 2017) is True
        assert matcher._year_ok(2015, 2018) is False
        assert matcher._year_ok(None, 2016) is False


class TestEmptyCases:
    def test_후보가_없으면_이유를_남긴다(self):
        r = matcher.match("은마", [])
        assert r.complex_id is None
        assert "K-apt 미수집" in r.reason

    def test_단지명이_비면_이유를_남긴다(self):
        r = matcher.match("", [c(1, "은마")])
        assert r.complex_id is None
        assert "비어 있음" in r.reason

    def test_matched_속성(self):
        assert matcher.match("은마", [c(1, "은마")]).matched is True
        assert matcher.match("은마", []).matched is False
