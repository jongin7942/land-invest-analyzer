"""병원 개원 관점의 매물·상권 판정.

1) medical_flags(listing): 매물 문구에서 의료 관련 신호를 태그로 뽑는다.
     병원양도   현재 병·의원이 영업 중이고 양도/매도/인수 문구가 있음 (= "매도 나온 병원")
     전병원자리 병·의원이 있던 자리(철수/이전) — 인테리어·배관·전기 승계 여지
     메디컬빌딩 메디컬/의료 전문 건물 문구 또는 약국 입점 문구
     의료가능   업종 제한 없음 + 의료 가능 문구(정화조/전기 용량 언급 등)
2) competition(listing, clinics, radius_m): 반경 내 심평원 병의원·약국 카운트.
3) fit_hint(...): 상권 활성·임대료 적정성·경쟁·매물 특성을 묶어 개원노트에 넣을
   짧은 판단 문장을 만든다. 점수는 참고용(0~100)이고, 문장이 본체다.
"""
from __future__ import annotations

import math
import re

# 의료기관 자체를 가리키는 말(양도/전 자리 판정에 씀). '메디컬'·'약국' 같은 건물 성격 단어는 여기서 뺀다 —
# 안 그러면 "메디컬타워 1층 현 편의점 영업중" 이 병원 양도로 잡힌다.
CLINIC_WORDS = r"(병원|의원|치과|한의원|클리닉|피부과|정형외과|소아과|소아청소년과|내과|이비인후과|안과|산부인과|" \
               r"정신건강의학과|비뇨의학과|성형외과|재활의학과|가정의학과|통증의학과|한방병원|치과의원)"
MEDICAL_WORDS = r"(" + CLINIC_WORDS[1:-1] + r"|메디컬|메디칼|진료|약국|한방|통증)"
# '현 ○○과 운영중', '○○의원 양도' 처럼 의료기관 단어와 인접(20자 이내)한 양도 신호만 인정
TRANSFER_RE = re.compile(
    CLINIC_WORDS + r".{0,20}?(양도|매도|매매|인수|승계|통째|시설\s*포함|인테리어\s*포함|장비\s*포함|운영\s*중|영업\s*중)"
    r"|(현\s*|운영\s*중인\s*)" + CLINIC_WORDS)
FORMER_RE = re.compile(
    r"(전|구|옛|기존)\s*" + CLINIC_WORDS + r"|" + CLINIC_WORDS + r"\s*(자리|있던|하던|운영했던|철수|이전|폐업|나간|였던)")
MEDICAL_BUILDING = r"(메디컬|메디칼|의료\s*전문|병원\s*전문|약국\s*(입점|운영|있)|병원\s*(입점|다수|밀집))"
MEDICAL_OK = r"(병원\s*(가능|추천|적합)|의원\s*(가능|추천|적합)|의료\s*(가능|업종)|정화조|전기\s*(용량|증설)|업종\s*(제한\s*없|무관))"

# 심평원 종별코드
CL_CLINIC = ("31",)            # 의원
CL_DENTAL = ("41", "51")       # 치과병원/치과의원
CL_ORIENTAL = ("91", "92")     # 한방병원/한의원
CL_HOSPITAL = ("01", "11", "21", "28", "29")  # 상급종합/종합/병원/요양/정신
CL_PHARMACY = ("81",)


def _text(listing: dict) -> str:
    return " ".join(str(listing.get(k) or "") for k in ("name", "building_name", "feature_desc", "tags"))


def medical_flags(listing: dict) -> list[str]:
    t = _text(listing)
    flags = []
    has_med = re.search(MEDICAL_WORDS, t) is not None
    # '전 치과 자리 … 승계' 처럼 둘 다 걸리면 '전 자리'가 우선(현재 영업 중이 아님)
    if FORMER_RE.search(t):
        flags.append("전병원자리")
    elif TRANSFER_RE.search(t):
        flags.append("병원양도")
    if re.search(MEDICAL_BUILDING, t):
        flags.append("메디컬빌딩")
    if re.search(MEDICAL_OK, t):
        flags.append("의료가능")
    if has_med and not flags:
        flags.append("의료언급")
    return flags


def enrich(listing: dict) -> dict:
    listing["medical_flag"] = ",".join(medical_flags(listing))
    return listing


# ------------------------------------------------------------ 경쟁 밀도 ----

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def competition(lat: float | None, lon: float | None, clinics: list[dict], radius_m: int = 500,
                dept_keyword: str | None = None) -> dict | None:
    """반경 내 병의원·약국 수. dept_keyword(예: '피부과')를 주면 이름/진료과목에 그 단어가 있는
    기관을 '동일과' 로 따로 센다."""
    if lat is None or lon is None:
        return None
    out = {"radius_m": radius_m, "clinic": 0, "dental": 0, "oriental": 0, "hospital": 0,
           "pharmacy": 0, "same_dept": 0, "same_dept_names": [], "nearest_pharmacy_m": None}
    nearest_ph = None
    for c in clinics:
        if c.get("lat") is None or c.get("lon") is None:
            continue
        d = haversine_m(lat, lon, c["lat"], c["lon"])
        if d > radius_m:
            continue
        cl = str(c.get("cl_cd") or "")
        if cl in CL_CLINIC:
            out["clinic"] += 1
        elif cl in CL_DENTAL:
            out["dental"] += 1
        elif cl in CL_ORIENTAL:
            out["oriental"] += 1
        elif cl in CL_HOSPITAL:
            out["hospital"] += 1
        elif cl in CL_PHARMACY:
            out["pharmacy"] += 1
            if nearest_ph is None or d < nearest_ph:
                nearest_ph = d
        if dept_keyword and (dept_keyword in str(c.get("name") or "")
                             or dept_keyword in str(c.get("dgsbjt") or "")):
            out["same_dept"] += 1
            out["same_dept_names"].append(f"{c.get('name')}({d:.0f}m)")
    out["nearest_pharmacy_m"] = round(nearest_ph) if nearest_ph is not None else None
    return out


# ------------------------------------------------------------- 종합 힌트 ----

def fit_hint(listing: dict, rent_assess: dict | None, activity: dict | None,
             comp: dict | None, dept_keyword: str | None = None) -> dict:
    """매물 한 건에 대한 개원 적합 힌트. {'score': 0~100, 'lines': [...], 'verdict': str}"""
    score = 50.0
    lines = []

    band = listing.get("floor_band") or "미상"
    if band == "1층":
        lines.append("1층 — 가시성·접근성은 최고지만 임대료가 상층의 2배 안팎. 약국·소아과·내과처럼 유동 고객이 중요한 과가 아니면 상층이 효율적.")
        score -= 5
    elif band in ("2층", "3층이상"):
        lines.append(f"{band} — 의원 개원의 주력 층대. 엘리베이터 유무와 1층 안내판(사이니지) 확보 여부를 확인.")
        score += 5
    elif band == "지하":
        lines.append("지하 — 의료기관은 환기·채광·접근성 때문에 비추천(재활·검사 부속시설 정도).")
        score -= 20

    from analysis.rent_baseline import exclusive_pyeong
    py, guessed = exclusive_pyeong(listing)
    if py:
        if py < 25:
            lines.append(f"전용 약 {py:.0f}평{'(추정)' if guessed else ''} — 진료실 1·대기실·처치실 구성이 빠듯. 1인 치과·피부 시술 중심이면 가능.")
            score -= 5
        elif py <= 80:
            lines.append(f"전용 약 {py:.0f}평{'(추정)' if guessed else ''} — 의원급 개원 표준 면적대.")
            score += 5
        else:
            lines.append(f"전용 약 {py:.0f}평{'(추정)' if guessed else ''} — 대형. 공동개원·검진센터·통증/재활 장비 배치에 적합, 고정비 부담 큼.")

    if rent_assess:
        pct = rent_assess["pct_vs_median"]
        lines.append(f"임대료 {rent_assess['label']} — 전용평당 환산월세 {rent_assess['input']:.1f}만원, "
                     f"기준선 중앙값 {rent_assess['median']:.1f}만원({rent_assess['level']}, n={rent_assess['n']}) 대비 {pct:+.0f}%.")
        score += max(-20, min(20, -pct / 2))
    else:
        lines.append("임대료 기준선 없음(표본 부족) — 같은 층대 매물을 더 모아야 판단 가능.")

    if listing.get("premium") == "있음":
        amt = listing.get("premium_amount")
        lines.append(f"권리금 있음{f'({amt:,}만원)' if amt else ''} — 현재 영업 중인 자리. 상권은 살아있다는 신호지만 초기비용 증가.")
        score += 3
    elif listing.get("premium") == "없음":
        lines.append("무권리 — 초기비용 절감. 다만 공실 자리라면 왜 비었는지(전 임차인 폐업 사유) 확인.")

    flags = [f for f in (listing.get("medical_flag") or "").split(",") if f]
    if "병원양도" in flags:
        lines.append("★ 병원 양도 매물 — 현 의료기관이 시설·장비·환자층째로 넘기는 건. 진료과·매출·양도 조건을 중개사에 직접 확인.")
        score += 10
    if "전병원자리" in flags:
        lines.append("전 병원 자리 — 급배수·전기·방사선 차폐 등 기존 설비 승계로 인테리어 비용 절감 가능. 전 병원의 폐업 사유가 상권 문제인지 개인 사유인지 확인.")
        score += 5
    if "메디컬빌딩" in flags:
        lines.append("메디컬 빌딩 — 약국·타과와 시너지. 같은 과 입점 여부(경쟁 제한 조항)를 확인.")
        score += 5

    if activity and activity.get("label") != "판단보류":
        lines.append(f"동 상권 활성도 '{activity['label']}'(지수 {activity['index']}) — " + "; ".join(activity.get("notes", [])[:2]))
        score += {"활성": 10, "보통": 0, "침체": -10}.get(activity["label"], 0)

    if comp:
        lines.append(f"반경 {comp['radius_m']}m 내 의원 {comp['clinic']}·치과 {comp['dental']}·한의원 {comp['oriental']}·"
                     f"병원급 {comp['hospital']}·약국 {comp['pharmacy']}"
                     + (f", 동일과({dept_keyword}) {comp['same_dept']}곳" if dept_keyword else "") + ".")
        if dept_keyword:
            if comp["same_dept"] == 0:
                lines.append(f"반경 내 {dept_keyword} 없음 — 선점 여지. 다만 수요 자체가 없는지(배후 인구·연령) 확인.")
                score += 8
            elif comp["same_dept"] >= 3:
                lines.append(f"{dept_keyword} {comp['same_dept']}곳 밀집 — 차별화 없이는 어려움. 밀집 자체가 '의료 상권'을 뜻하기도 하니 대기 환자 수로 판단.")
                score -= 8
        if comp["pharmacy"] == 0:
            lines.append("반경 내 약국 없음 — 처방 위주 과라면 약국 유치 가능 여부가 중요.")
            score -= 3
        elif comp["nearest_pharmacy_m"] is not None and comp["nearest_pharmacy_m"] <= 100:
            lines.append(f"약국 {comp['nearest_pharmacy_m']}m — 처방 동선 양호.")
            score += 3

    score = max(0.0, min(100.0, score))
    verdict = "검토 권장" if score >= 65 else ("보통" if score >= 45 else "신중")
    return {"score": round(score, 1), "verdict": verdict, "lines": lines}
