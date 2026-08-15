"""낙찰 후 확인·해결 체크리스트 — 이주왕(부동산 공법 경매)식 관점 + 경공매 공통 법률.

이주왕의 핵심 메시지: "등기부에 보이는 민법상 권리뿐 아니라, 등기부에 안 보이는
공법상 제약(농지취득자격증명, 산지전용허가 등)까지 확인해야 낙찰 후 낭패를 안 본다."

여기서 다루는 건 전부 "낙찰 이후" 단계 — 지금까지의 엔진(용도지역/도로/시세)이
"살 만한가"를 판정했다면, 이 모듈은 "낙찰되면 뭘 확인하고 어떻게 풀어야 하는가"를 다룬다.
전부 규칙 기반, 물건별 raw 데이터만으로 판단(추가 API 호출 없음).
"""
from __future__ import annotations

import json
import urllib.parse

# 지목별 취득 요건(공법상 제약 — 이주왕식 핵심 포인트)
FARMLAND_JIMOK = ("전", "답", "과수원")
FOREST_JIMOK = ("임야",)

ONBID_DETAIL_URL = (
    "https://www.onbid.co.kr/op/cltrpbancinf/pbanc/pbancdtlinf/"
    "PbancDtlInqController/mvmnPbancDtl.do"
)
IROS_URL = "https://www.iros.go.kr"  # 인터넷등기소 (등기부등본 열람, 유료)


def is_co_ownership(name: str | None) -> bool:
    """물건명에 '지분' 표기가 있으면 공유지분 경매/공매 물건."""
    return bool(name) and "지분" in name


def onbid_detail_url(raw_json: str | None) -> str | None:
    """실제 온비드 공고 상세페이지 링크(라이브 검증됨: 공고번호까지 정확히 일치).
    유치권·법정지상권·특별매각조건 같은 실제 유의사항은 여기(자바스크립트 렌더링이라
    이 프로그램이 자동으로 읽어올 수 없음 — 사람이 직접 열어봐야 함)에 있다."""
    if not raw_json:
        return None
    try:
        d = json.loads(raw_json)
    except (ValueError, TypeError):
        return None
    prpt_div = (d.get("prptDivCd") or "").lstrip("0")
    params = {
        "cltrScrnGrpCd": "0",
        "cltrPrptDivCd": prpt_div,
        "onbidCltrno": d.get("onbidCltrno"),
        "onbidPbancNo": d.get("onbidPbancNo"),
        "pbctNo": d.get("pbctNo"),
        "pbctCdtnNo": d.get("pbctCdtnNo"),
    }
    if not all(params.values()):
        return None
    return ONBID_DETAIL_URL + "?" + urllib.parse.urlencode(params)


def verification_links(r: dict) -> list[dict]:
    """이 프로그램이 자동판정 못 하는 항목(유치권/저당권/대항력 등)을 사람이
    직접 확인할 수 있는 실제 링크. 등기부등본은 열람 수수료(약 700~1,000원)가 든다."""
    links = []
    onbid_url = onbid_detail_url(r.get("raw_json"))
    if onbid_url:
        links.append({
            "label": "온비드 공고 원문 열기 (유치권·특별매각조건·감정평가서)",
            "url": onbid_url,
            "note": "이 물건의 실제 공고 상세페이지입니다. 유치권 신고, 법정지상권 성립 여지, "
                    "명도책임 등 특별매각조건이 여기 명시됩니다.",
        })
    links.append({
        "label": "인터넷등기소에서 등기부등본 열람 (근저당권·대항력 있는 점유자 확인)",
        "url": IROS_URL,
        "note": "건당 열람 수수료(약 700~1,000원)가 듭니다. 근저당권·가압류·가처분 등 "
                "말소기준권리와 전입일자 대조는 등기부등본에서만 확인할 수 있습니다.",
    })
    return links


def common_checklist(r: dict) -> list[str]:
    """경매·공매 공통 + 경매/공매 차이 — 모든 물건에 적용.
    ⚠ 중요: 이 프로그램은 온비드 물건목록 API 데이터만 가지고 있어, 유치권·저당권·
    법정지상권·대항력 있는 점유자의 실제 유무를 자동으로 판정할 수 없다(그 정보는
    공고 상세문서·등기부등본에만 있음). 그래서 "있다/없다"를 단정하지 않고,
    무엇을 어디서 확인해야 하는지와 발견됐을 때의 대처법을 알려준다."""
    items = [
        ("**공매는 인도명령 제도가 없습니다.** 법원경매는 잔금납부 후 6개월 이내 인도명령을 "
         "신청하면 2~3주 만에 점유자를 내보낼 수 있지만, 온비드 공매(이 프로그램이 다루는 물건 "
         "전부)는 국세징수법 적용이라 인도명령 규정 자체가 없습니다. 점유자가 안 나가면 "
         "**명도소송(5~6개월 이상)** 밖에 방법이 없다는 걸 낙찰가에 미리 반영해야 합니다."),
        ("**유치권**: 이 프로그램은 유치권 신고 유무를 자동으로 알 수 없습니다 — 아래 "
         "'온비드 공고 원문' 링크를 열어 특별매각조건·감정평가서에 유치권 신고가 명시돼 "
         "있는지 직접 확인하세요. 있다면 공사계약서·세금계산서·대금지급내역 같은 객관적 "
         "증거가 있는 진짜 채권인지, 낙찰가를 깎으려는 허위 신고인지부터 구분해야 합니다."),
        ("**법정지상권**: 토지만 낙찰받는 경우, 그 위에 건물이 있고 토지·건물 소유자가 "
         "달랐던 이력이 있으면 건물 소유자에게 법정지상권이 성립해 철거시키지 못할 수 "
         "있습니다. 지적도·스카이뷰로 지상 건축물 유무부터 확인하고, 확실한 건 등기부 "
         "이력(아래 등기부등본 링크)으로 대조하세요."),
        ("**저당권·대항력 있는 점유자**: 근저당권(말소기준권리)과 그보다 먼저 전입·인도받은 "
         "임차인이 있는지는 **등기부등본에만** 나옵니다. 이 프로그램의 어떤 데이터로도 "
         "대체 확인이 안 되니, 입찰 전 반드시 아래 등기부등본 링크에서 열람하세요(유료)."),
    ]
    return items


def ownership_checklist(r: dict) -> list[str]:
    """공유지분 물건이면 별도 경고."""
    if not is_co_ownership(r.get("name")):
        return []
    return [
        ("**이 물건은 공유지분 물건입니다.** 다른 공유자가 '공유자 우선매수신고권'을 행사하면 "
         "당신이 최고가로 입찰해도 같은 가격에 공유자가 채갈 수 있습니다 — 입찰 자체가 "
         "헛수고가 될 위험이 있습니다. 또 지분만 매입하면 활용도가 낮아, 나머지 지분을 "
         "매입하거나 공유물분할청구소송을 해야 온전한 소유권 행사가 가능한 경우가 많습니다. "
         "입찰 전 다른 공유자의 매수 의사·자금력을 가늠해보는 게 이주왕식 접근입니다."),
    ]


def purpose_requirements(r: dict) -> dict:
    """목적별(건축/토지보상) 취득 요건 — 지목 기반으로 공법상 필수 서류를 알려준다."""
    jimok = (r.get("jimok") or "").strip()
    build_reqs = []
    comp_reqs = []

    if jimok in FARMLAND_JIMOK:
        build_reqs.append(
            "**농지취득자격증명(농취증) 필수**: 농지(전·답·과수원)는 낙찰 후 **매각결정기일"
            "(통상 낙찰일로부터 1주일)까지** 농지 소재지 읍면동 주민센터에서 농취증을 발급받아 "
            "제출해야 합니다. 기한 내 미제출 시 매각불허가 처리되고, 법원/기관에 따라 "
            "**입찰보증금이 몰수될 수 있습니다.** 농업경영계획서(자경 목적) 또는 "
            "주말체험영농계획서(체험영농 목적)를 함께 준비하세요. 건축(농지전용) 목적이면 "
            "농지전용허가·신고 가능 여부를 사전에 지자체에 확인해야 농취증 발급 자체가 됩니다."
        )
        comp_reqs.append(
            "농지는 수용보상 시 '농지군'으로 분류돼 공시지가 대비 보상배율이 낮은 편(노트: "
            "공시지가의 약 1.1배)입니다. 농취증 요건은 보상 목적 취득에도 동일하게 적용됩니다."
        )
    elif jimok in FOREST_JIMOK:
        build_reqs.append(
            "**산지전용허가/산지일시사용허가 검토**: 임야에 건축하려면 산지관리법상 산지전용허가"
            "(또는 신고)가 필요합니다. 경사도·입목축적·표고 등 산지관리법 기준에 걸리면 허가 "
            "자체가 안 날 수 있어, 이 조건은 건폐율·용적률보다 먼저 확인해야 하는 선행 관문입니다. "
            "관할 지자체 산림부서에 사전 문의를 권장합니다."
        )

    return {"build": build_reqs, "compensation": comp_reqs}


def build_checklist(r: dict) -> dict:
    """narrative.py 에서 그대로 렌더링할 dict."""
    purpose = purpose_requirements(r)
    return {
        "common": common_checklist(r),
        "ownership": ownership_checklist(r),
        "build_purpose": purpose["build"],
        "compensation_purpose": purpose["compensation"],
        "links": verification_links(r),
    }
