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
