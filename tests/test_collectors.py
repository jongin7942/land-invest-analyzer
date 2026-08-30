"""수집기 파싱 테스트.

data.go.kr 이 개발 환경에서 차단돼 라이브 검증을 못 했으므로, **응답 형태 두 가지
(2023 개편 후 영문 필드 / 개편 전 한글 필드)** 를 모두 고정해 둔다. 실제 응답이
둘 중 하나면 그대로 돌아가고, 셋째 형태면 이 테스트에 케이스를 추가하면 된다.
"""
import pytest

from apt_engine import units
from apt_engine.collectors import apt_rent, apt_trade, molit

# ── 응답 픽스처 ────────────────────────────────────────────────────────

TRADE_XML_EN = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header>
<body><items>
  <item>
    <aptNm>은마</aptNm><aptDong>101</aptDong><umdNm>대치동</umdNm><jibun>316</jibun>
    <sggCd>11680</sggCd><excluUseAr>84.43</excluUseAr><dealAmount>  280,000</dealAmount>
    <dealYear>2026</dealYear><dealMonth>7</dealMonth><dealDay>3</dealDay>
    <floor>7</floor><buildYear>1979</buildYear>
    <dealingGbn>중개거래</dealingGbn><estateAgentSggNm>서울 강남구</estateAgentSggNm>
    <cdealType></cdealType><cdealDay></cdealDay><rgstDate>26.07.20</rgstDate>
    <slerGbn>개인</slerGbn><buyerGbn>개인</buyerGbn>
  </item>
  <item>
    <aptNm>래미안대치팰리스</aptNm><umdNm>대치동</umdNm><jibun>1</jibun>
    <sggCd>11680</sggCd><excluUseAr>59.98</excluUseAr><dealAmount>350,000</dealAmount>
    <dealYear>2026</dealYear><dealMonth>7</dealMonth><dealDay>15</dealDay>
    <floor>12</floor><buildYear>2015</buildYear>
    <dealingGbn>직거래</dealingGbn><cdealType>O</cdealType><cdealDay>26.08.01</cdealDay>
  </item>
</items><numOfRows>10</numOfRows><totalCount>2</totalCount></body></response>
"""

TRADE_XML_KO = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>00</resultCode></header><body><items>
  <item>
    <아파트>은마</아파트><법정동>대치동</법정동><지번>316</지번><지역코드>11680</지역코드>
    <전용면적>84.43</전용면적><거래금액>280,000</거래금액>
    <년>2026</년><월>7</월><일>3</일><층>7</층><건축년도>1979</건축년도>
    <거래유형>중개거래</거래유형><해제여부> </해제여부>
  </item>
</items></body></response>
"""

RENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>000</resultCode></header><body><items>
  <item>
    <aptNm>은마</aptNm><umdNm>대치동</umdNm><jibun>316</jibun><sggCd>11680</sggCd>
    <excluUseAr>84.43</excluUseAr><deposit>66,000</deposit><monthlyRent>0</monthlyRent>
    <dealYear>2026</dealYear><dealMonth>7</dealMonth><dealDay>10</dealDay>
    <floor>5</floor><buildYear>1979</buildYear>
    <contractType>갱신</contractType><useRRRight>사용</useRRRight>
    <preDeposit>60,000</preDeposit><preMonthlyRent>0</preMonthlyRent>
    <contractTerm>26.09~28.09</contractTerm>
  </item>
</items></body></response>
"""

NODATA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>03</resultCode><resultMsg>NODATA_ERROR</resultMsg></header>
<body><items/><totalCount>0</totalCount></body></response>
"""

AUTH_ERROR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<OpenAPI_ServiceResponse><cmmMsgHeader>
<errMsg>SERVICE ERROR</errMsg>
<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
<returnReasonCode>30</returnReasonCode>
</cmmMsgHeader></OpenAPI_ServiceResponse>
"""


class TestMolitCommon:
    def test_정상_응답에서_item_을_뽑는다(self):
        assert len(molit.parse_items(TRADE_XML_EN)) == 2

    def test_데이터_없음은_빈_목록이지_에러가_아니다(self):
        # 그 달에 거래가 없는 시군구는 흔하다. 실패로 처리하면 로그가 오염된다.
        assert molit.parse_items(NODATA_XML) == []

    def test_인증_실패는_전용_예외로_구분된다(self):
        # 재시도해도 소용없는 오류라 배치를 즉시 멈춰야 한다.
        with pytest.raises(molit.MolitAuthError, match="SERVICE_KEY_IS_NOT_REGISTERED"):
            molit.parse_items(AUTH_ERROR_XML)

    def test_XML이_아니면_앞부분을_보여주며_실패한다(self):
        # 게이트웨이가 HTML 오류 페이지나 잘린 응답을 주는 경우.
        with pytest.raises(molit.MolitError, match="XML 파싱 실패"):
            molit.parse_items("<html><body>503 Service Unavailable")

    def test_날짜_조립(self):
        assert molit.ymd("2026", "7", "3") == "20260703"
        assert molit.ymd("2026", "7", None) is None


class TestTradeParsing:
    def test_영문_필드_응답(self):
        rows = [apt_trade.parse_item(r, "11680") for r in molit.parse_items(TRADE_XML_EN)]
        first = rows[0]
        assert first["apt_name"] == "은마"
        assert first["emd_name"] == "대치동"
        assert first["exclusive_area_m2"] == 84.43
        assert first["area_band"] == "84"
        assert first["deal_ymd"] == "20260703"
        assert first["floor"] == 7
        assert first["build_year"] == 1979
        assert first["deal_type"] == "중개거래"
        assert first["cancel_yn"] == 0

    def test_한글_필드_응답도_같은_결과(self):
        en = apt_trade.parse_item(molit.parse_items(TRADE_XML_EN)[0], "11680")
        ko = apt_trade.parse_item(molit.parse_items(TRADE_XML_KO)[0], "11680")
        for key in ("apt_name", "emd_name", "exclusive_area_m2", "area_band",
                    "deal_amount", "deal_ymd", "floor", "deal_type", "cancel_yn"):
            assert en[key] == ko[key], key

    def test_거래금액은_만원에서_원으로_바뀐다(self):
        # '  280,000' (만원) → 28억. 공백과 콤마가 섞여 온다.
        row = apt_trade.parse_item(molit.parse_items(TRADE_XML_EN)[0], "11680")
        assert row["deal_amount"] == 2_800_000_000
        assert isinstance(row["deal_amount"], int)
        assert units.fmt_eok(row["deal_amount"]) == "28억"

    def test_해제거래와_직거래가_구분돼_저장된다(self):
        # 요구사항 26-5·26-6: 이 두 필드가 없으면 정상거래를 걸러낼 수 없다.
        second = apt_trade.parse_item(molit.parse_items(TRADE_XML_EN)[1], "11680")
        assert second["cancel_yn"] == 1
        assert second["cancel_ymd"] == "26.08.01"
        assert second["deal_type"] == "직거래"
        assert second["area_band"] == "59"   # 84 밴드에 섞이지 않는다

    def test_해제여부가_공백이면_취소가_아니다(self):
        row = apt_trade.parse_item(molit.parse_items(TRADE_XML_KO)[0], "11680")
        assert row["cancel_yn"] == 0

    @pytest.mark.parametrize("broken", [
        {"aptNm": "은마", "excluUseAr": "", "dealAmount": "280000",
         "dealYear": "2026", "dealMonth": "7", "dealDay": "3"},
        {"aptNm": "은마", "excluUseAr": "84.43", "dealAmount": "0",
         "dealYear": "2026", "dealMonth": "7", "dealDay": "3"},
        {"aptNm": "", "excluUseAr": "84.43", "dealAmount": "280000",
         "dealYear": "2026", "dealMonth": "7", "dealDay": "3"},
        {"aptNm": "은마", "excluUseAr": "84.43", "dealAmount": "280000"},  # 날짜 없음
    ])
    def test_필수값이_없으면_저장하지_않는다(self, broken):
        assert apt_trade.parse_item(broken, "11680") is None


class TestRentParsing:
    def test_전세_파싱(self):
        row = apt_rent.parse_item(molit.parse_items(RENT_XML)[0], "11680")
        assert row["deposit"] == 660_000_000     # 66,000만원 → 6.6억
        assert row["monthly_rent"] == 0          # 순수 전세
        assert row["contract_ymd"] == "20260710"
        assert row["area_band"] == "84"

    def test_갱신계약과_갱신요구권이_구분돼_저장된다(self):
        # 갱신계약은 시세가 아니라 기존 계약의 연장이다. PHASE 2에서 걸러낸다.
        row = apt_rent.parse_item(molit.parse_items(RENT_XML)[0], "11680")
        assert row["contract_type"] == "갱신"
        assert row["use_renewal_right"] == 1
        assert row["prev_deposit"] == 600_000_000

    def test_보증금_0은_유효하다(self):
        # 순수 월세 계약. 전세가 아닐 뿐 유효한 데이터다.
        raw = {"aptNm": "X", "excluUseAr": "84.0", "deposit": "0", "monthlyRent": "100",
               "dealYear": "2026", "dealMonth": "7", "dealDay": "1"}
        row = apt_rent.parse_item(raw, "11680")
        assert row["deposit"] == 0
        assert row["monthly_rent"] == 1_000_000
