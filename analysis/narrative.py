"""김종률(옥탑방보보스)식 해설 생성기.

auction_candidate 한 건의 계산된 속성(용도지역·도로접면·규모·유찰회차 등)을
김종률 아카데미 노트의 원칙과 매칭해, "왜 급매인가"/"그라면 어떻게 볼 것인가"/
"직접 확인할 것"을 한국어 문장으로 만든다. 점수만으론 안 보이는 '왜'를 설명하는 게 목적.

전부 규칙 기반(결정적)이며, DB에 저장된 필드 외 새 API 호출은 하지 않는다.
"""
from __future__ import annotations

import json

from analysis import category
from analysis import due_diligence

PYEONG_M2 = 3.3058

LAND_GROUP_NOTE = {
    "대지군": "공장·주택지·창고 등 — 필지가 작고 소유자가 많아 노트 기준 보상이 상대적으로 후한 편입니다.",
    "농지군": "농지·임야 등 — 필지가 크고 소유자가 적어 노트 기준 보상단가는 짜지만(공시지가의 약 1.1배 안팎) 필지가 커서 총액은 클 수 있습니다.",
    "미분류": "지목이 분류표에 없어 대지군/농지군 판단을 보류합니다.",
}

# 각 용도지역에 대한 김종률식 코멘트
ZONING_COMMENTS = {
    "계획관리지역": ("그가 강조하는 핵심 사냥터입니다. \"토지 가격이 올라가는 이유는 "
                    "개발 가능성 증가·규제 완화·용도 변경\"이라는 원리상, 관리지역은 "
                    "지구단위계획·성장관리권역 지정 등으로 규제가 풀릴 여지가 가장 큰 용도입니다."),
    "생산관리지역": ("계획관리지역과 함께 관리지역군으로, 향후 계획관리로 상향될 잠재력이 있는 "
                    "용도입니다. 다만 생산관리는 농업 관련 용도 보호 성격이 있어 용도변경 난이도는 "
                    "계획관리보다 높습니다."),
    "보전관리지역": ("관리지역 중 규제가 가장 센 편입니다. 신축 자체가 어려운 경우가 많아, "
                    "\"이 땅에 어떤 건물을 지을 수 있냐\"는 질문에 답이 궁색할 수 있습니다 — "
                    "단순히 싸다고 접근하면 안 되는 용도."),
    "농림지역": ("원칙적으로 농업진흥구역 등 개발이 크게 제한됩니다. 그의 노트 기준으로도 "
                "'지목이 임야·전답이라도 개발 가능 여부'를 먼저 따지라고 했는데, 농림지역은 "
                "그 가능성이 가장 낮은 축에 속합니다."),
    "자연녹지지역": ("도시지역 안에 있으면서도 개발이 제한된 녹지라, 지구단위계획이나 도시계획 "
                    "변경 이슈가 있는지에 따라 가치가 크게 갈립니다."),
    "생산녹지지역": ("녹지지역 중 농업생산 보호 목적이 강해 신축 여지가 제한적입니다."),
    "보전녹지지역": ("녹지지역 중 규제가 가장 강한 편으로, 신축 가능성부터 확인이 필요합니다."),
    "개발제한구역": ("그린벨트입니다. 노트에서 언급한 '미집행 도시계획시설 내 가설건축물'처럼 "
                    "특수한 틈새 전략이 아니라면 일반적인 건축 목적 접근은 어렵습니다. 다만 "
                    "그린벨트 해제 이슈가 있는 지역이면 얘기가 달라집니다 — 해제 논의 여부 확인 필요."),
    "제1종일반주거지역": ("주택 건축이 명확한 용도입니다. 다가구(50평~)나 다중주택(역세권이면 "
                        "40평도 가능) 등 필지 크기에 맞는 주택 유형을 그의 기준으로 따져볼 만합니다."),
    "제2종일반주거지역": ("제1종보다 용적률이 높아 다세대·소형 공동주택 개발 여지가 더 큽니다."),
    "제3종일반주거지역": ("일반주거 중 용적률이 가장 높아, 아파트급 개발도 검토 대상입니다."),
    "준주거지역": ("주거+상업 혼합 개발이 가능해 활용도가 넓습니다."),
    "일반상업지역": ("용적률이 매우 높아(법정 최대 1300%) 상가·오피스 개발 시 역산 매수가 "
                    "계산(시세→건축비·마진 차감→적정 토지가)이 그대로 적용됩니다."),
    "근린상업지역": ("근린생활시설 중심의 상업 개발이 가능합니다."),
}

ROAD_COMMENTS = {
    "맹지": ("맹지 판정입니다. 그의 노트: \"맹지는 진입도로 확보 전 건축 불가.\" 곧바로 포기하기보다 "
            "인접 사도의 굴착동의를 받을 수 있는지, 지자체 도로계획(도시계획도로 지정 여부)이 있는지부터 "
            "확인하는 게 그의 접근법입니다. 안 되면 특수물건으로 분류하고 넘어가는 게 맞습니다."),
    "건축애로": ("도로접면이 '세로(불)' — 자동차 통행이 안 되는 좁은 길입니다. 그의 노트: \"건축법상 "
                "4m 이상 도로에 2m 이상 접해야 허가.\" 과거엔 3m 도로에도 허가해준 사례가 있어 "
                "주변에 건축물이 있다고 착각하기 쉬운 지점이라고 그가 특히 경고한 부분입니다. "
                "현장 실측과 지자체 문의가 필수입니다."),
    "확인필요": ("도로접면이 '세로(가)' — 차량 통행은 가능하지만 폭이 좁습니다. 4m 이상 도로에 "
                "2m 이상 접했는지는 서류가 아니라 실측으로 확인해야 한다는 게 그의 반복된 강조점입니다."),
    "건축양호": ("도로 조건은 양호합니다. 그가 말하는 건축허가 3요건(용도지역·도로 4m+2m 접함·대지) "
                "중 도로 조건은 통과한 셈입니다."),
    "미상": ("도로접면 정보가 조회되지 않았습니다. 지적도·스카이뷰를 함께 보라는 그의 조언대로, "
            "직접 지도에서 진입로 여부부터 확인해야 합니다."),
}


def _pyeong(area_m2):
    return (area_m2 / PYEONG_M2) if area_m2 else None


def _fmt_manwon(v) -> str:
    """평당가(만원) 표시. 소액(대형 필지 등으로 평당 10만원 미만)은 소수점을 살려
    '1만원'처럼 뭉개져 보이지 않게 한다."""
    if v is None:
        return "-"
    if v < 10:
        return f"{v:.2f}"
    return f"{v:,.0f}"


def why_undervalued(r: dict) -> list[str]:
    """'왜 급매인가' — 가격 근거 섹션."""
    lines = []
    if r.get("pct_below") is not None and r.get("baseline_med") is not None:
        lines.append(
            f"최저입찰가 평당 {_fmt_manwon(r.get('ppp_min_bid'))}만원 vs 기준선({r.get('baseline_lvl')}) "
            f"중앙값 평당 {_fmt_manwon(r['baseline_med'])}만원 → **{r['pct_below']:+.1f}% "
            f"{'저평가' if r['pct_below'] > 0 else '고평가'}**"
        )
    else:
        lines.append("이 지역·용도의 실거래가 표본이 부족해 정량적 저평가율은 계산되지 않았습니다.")

    if r.get("appraisal") and r.get("min_bid"):
        ratio = r["min_bid"] / r["appraisal"] * 100
        lines.append(f"감정가 {r['appraisal']:,}원 대비 최저입찰가는 **{ratio:.0f}%** 수준입니다.")

    if r.get("round"):
        rnd = r["round"]
        if rnd >= 10:
            lines.append(
                f"**{rnd}회 유찰**된 물건입니다. 반복 유찰은 (a) 수요가 없어서일 수도, "
                f"(b) 아직 아무도 눈치채지 못한 저평가일 수도 있습니다 — 유찰 사유(도로·형상·용도 문제 "
                f"등)를 구분해서 봐야 합니다."
            )
        elif rnd > 1:
            lines.append(f"{rnd}회 유찰돼 최저입찰가가 낮아진 상태입니다.")

    pyeong = _pyeong(r.get("area_m2"))
    if pyeong and pyeong > 1000:
        lines.append(
            f"면적이 {pyeong:,.0f}평으로 **대형 필지**입니다. 소규모 거래 위주 기준선과 비교하면 "
            f"저평가율이 과장되는 경향이 있어(같은 규모끼리 비교하도록 보정은 했지만) 참고용으로만 "
            f"보시는 게 안전합니다."
        )
    return lines


def zongyul_view(r: dict) -> list[str]:
    """'옥탑방보보스라면 어떻게 볼 것인가' — 원칙 매칭 섹션."""
    lines = []
    zoning = r.get("zoning")
    if zoning and zoning in ZONING_COMMENTS:
        lines.append(f"**용도지역({zoning})**: {ZONING_COMMENTS[zoning]}")
    elif zoning:
        lines.append(f"**용도지역({zoning})**: 별도 코멘트가 준비되지 않은 용도입니다. "
                     "국가법령정보센터에서 해당 시군구 조례상 건폐율·용적률을 직접 확인하세요.")

    grade = r.get("road_grade")
    if grade and grade in ROAD_COMMENTS:
        lines.append(f"**도로접면({r.get('road_side') or grade})**: {ROAD_COMMENTS[grade]}")

    area = r.get("area_m2")
    if zoning and area:
        # 건폐율/용적률은 pipeline에서 별도 계산하지 않고 여기서 지역 룰을 다시 참조
        from analysis.zoning_rules import buildable
        bd = buildable(zoning, area)
        if bd:
            lines.append(
                f"**건축 규모(법정 상한 기준)**: 건폐율 {bd['bcr']}%·용적률 {bd['far']}%로 "
                f"바닥 {bd['footprint_pyeong']:.0f}평, 연면적 {bd['gross_pyeong']:.0f}평(약 "
                f"{bd['approx_floors']:.1f}층) 규모 건축이 가능합니다. "
                f"\"이 땅에 어떤 건물을 지을 수 있냐가 돈을 번다\"는 그의 원칙대로, "
                f"이 규모를 기준으로 건축비·마진을 역산해 적정 매수가를 다시 검산해볼 만합니다."
            )

    jimok = r.get("jimok")
    pyeong = _pyeong(area)
    if jimok == "임야" and pyeong and pyeong > 1000:
        lines.append(
            "**지목(임야)+대형 필지**: 그의 수용보상 강의 내용 중 \"농지군(임야 포함)은 필지가 크고 "
            "소유자가 적어 상대적으로 보상단가가 낮다\"는 패턴과 유사합니다. 개발압력이 아직 낮은 "
            "초기 단계의 저가매수 논리와 맞아떨어질 수 있습니다 — 단, 개발 호재가 실제 있는지가 전제입니다."
        )
    return lines


def checklist(r: dict) -> list[str]:
    """'직접 확인할 것' — 프로그램이 대신 못 하는 부분."""
    items = [
        "지적도와 스카이뷰(위성지도)를 같이 보고 실제 진입로가 있는지 확인 "
        "(그의 노트: \"토지 지적도상으로도 보고, 스카이뷰로도 보기. 현황상 도로가 있을 수도 있다\")",
        f"{r.get('address', '')} 관할 지자체 도시계획조례에서 정확한 건폐율·용적률 확인 "
        "(법정 상한이 아니라 조례가 실제 기준)",
        "인근 개발계획(도로 신설·확장, 택지지구, 산업단지) 여부 — 도시기본계획·지구단위계획 확인",
        f"온비드(www.onbid.co.kr)에서 물건관리번호 {r.get('mgmt_no', '')} 로 검색해 공고 원문·"
        "유치권/법정지상권 신고 여부 확인",
    ]
    if r.get("road_grade") == "맹지":
        items.append("인접 필지 소유자와 도로 사용승낙(굴착동의) 협상 가능성 타진")
    return items


def compensation_narrative(r: dict) -> list[str]:
    """'토지보상용 관점' — 개발하지 않고 수용될 때까지 들고 있는다는 논리의 평가.
    개별공시지가가 없으면 판단 불가를 알려준다."""
    cv = category.compensation_view(r)
    if cv is None:
        return ["개별공시지가 데이터가 없어(또는 최저입찰가 정보 부족) 토지보상용 평가를 계산할 수 없습니다."]

    lines = [
        f"**{cv['land_group']}**: {LAND_GROUP_NOTE.get(cv['land_group'], '')}",
        f"개별공시지가 평당 {cv['official_price_ppp']:.2f}만원 대비 최저입찰가는 "
        f"**{cv['discount_vs_official']:+.1f}%** 수준입니다.",
        f"노트의 보상배율(대지군×1.4 / 농지군×1.1 추정)을 적용하면 추정 보상단가는 평당 "
        f"{cv['est_compensation_ppp']:.2f}만원 — 최저입찰가 대비 {cv['discount_vs_est_comp']:+.1f}% 차이입니다.",
    ]
    news_count = r.get("news_count") or 0
    if news_count > 0:
        lines.append(
            f"인근 개발호재 뉴스가 **{news_count}건** 확인됩니다 — 수용 가능성을 뒷받침하는 실질적 신호입니다."
        )
    else:
        lines.append(
            "인근 개발호재 뉴스가 확인되지 않았습니다. 수용보상은 \"실제 사업지구 지정\"이 전제라, "
            "뉴스 신호가 없으면 이 계산은 순수 추정치일 뿐입니다 — 아래 고시 링크로 직접 확인하기 전엔 "
            "과신하지 마세요."
        )
    return lines


def news_items(r: dict) -> list[dict]:
    raw = r.get("news_json")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return []


def build_narrative(r: dict) -> dict:
    """섹션별 리스트를 담은 dict로 반환. 웹앱 템플릿에서 그대로 렌더링."""
    return {
        "why": why_undervalued(r),
        "view": zongyul_view(r),
        "checklist": checklist(r),
        "compensation": compensation_narrative(r),
        "news": news_items(r),
        "post_award": due_diligence.build_checklist(r),
    }
