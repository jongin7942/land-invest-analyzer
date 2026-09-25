"""개원노트 생성기 — 한 지역(동 또는 시군구)의 상가 임대 시장을 '병원 개원' 관점으로 요약한 마크다운.

구성
  1. 한 줄 요약(활성도·기준선·경쟁)
  2. 임대료 기준선 표(층대별) + "N평 개원 시 예상 월세·보증금"
  3. 권리금·공실로 본 상권 활성도
  4. 병원 관련 매물(양도/전 병원 자리/메디컬빌딩)
  5. 경쟁·보완 시설(심평원 데이터가 있을 때)
  6. 개원 후보 매물 Top N (층·면적·임대료 적정성으로 필터)
  7. 직접 확인할 것

전부 규칙 기반이고 DB에 있는 것만 쓴다(리포트 생성 시 새 API 호출 없음).
"""
from __future__ import annotations

import datetime as dt

from analysis import medical_fit as mf
from analysis import premium as pm
from analysis import rent_baseline as rb
from db import shop as shopdb

BANDS = ("1층", "2층", "3층이상", "지하", "미상")


def naver_link(article_no: str) -> str:
    return f"https://m.land.naver.com/article/info/{article_no}"


def _fmt(v, nd=0, suffix=""):
    if v is None:
        return "-"
    return f"{v:,.{nd}f}{suffix}"


def _listing_line(l: dict, assess: dict | None = None) -> str:
    py, guessed = rb.exclusive_pyeong(l)
    price = (f"보증금 {_fmt(l.get('deposit'))}/월세 {_fmt(l.get('rent'))}만"
             if l.get("trade_type") in ("월세", "전세") else f"매매 {_fmt(l.get('sale_price'))}만")
    parts = [
        f"[{l.get('article_no')}]({naver_link(l.get('article_no'))})",
        f"{l.get('re_type') or ''} {l.get('floor_band') or ''}",
        f"전용 {py:.0f}평{'(추정)' if guessed else ''}" if py else "면적 미상",
        price,
    ]
    if l.get("rent_per_py"):
        parts.append(f"평당 {l['rent_per_py']:.1f}만")
    if assess:
        parts.append(f"기준선 대비 {assess['pct_vs_median']:+.0f}% ({assess['label']})")
    if l.get("premium") and l.get("premium") != "미상":
        amt = l.get("premium_amount")
        parts.append(f"권리금 {l['premium']}{f' {amt:,}만' if amt else ''}")
    if l.get("medical_flag"):
        parts.append(f"🏥 {l['medical_flag']}")
    if l.get("building_name"):
        parts.append(str(l["building_name"]))
    desc = (l.get("feature_desc") or "").strip()
    if desc:
        parts.append(f"“{desc[:60]}{'…' if len(desc) > 60 else ''}”")
    return " · ".join(parts)


def build_note(sgg: str, umd: str | None = None, dept: str | None = None, pyeong: float = 50.0,
               band: str = "2층", radius_m: int = 500, top_n: int = 10,
               all_listings: list[dict] | None = None, clinics: list[dict] | None = None) -> str:
    """마크다운 문자열 반환. all_listings 를 주면 기준선을 그걸로 만든다(없으면 DB 전체 활성 매물)."""
    all_listings = all_listings if all_listings is not None else shopdb.listings(active_only=True)
    region_listings = [l for l in all_listings
                       if (sgg in (l.get("sgg") or "")) and (not umd or umd in (l.get("umd") or ""))]
    clinics = clinics if clinics is not None else shopdb.all_clinics_with_coords()
    baselines = rb.build_baselines(all_listings)
    sido = next((l.get("sido") for l in region_listings if l.get("sido")), None)
    umd_name = umd or "(시군구 전체)"
    title = f"{sgg} {umd_name}"
    today = dt.date.today().isoformat()

    rent_listings = [l for l in region_listings if l.get("trade_type") == "월세"]
    act = pm.activity_profile(rent_listings)

    out = [f"# 개원노트 · {title}", "", f"- 작성일: {today}",
           f"- 표본: 활성 상가 매물 {len(region_listings)}건(임대 {len(rent_listings)}건)"
           + (f", 관심과: {dept}" if dept else ""),
           f"- 전제: 보증금→월세 환산율 연 {rb.config.RENT_CONVERSION_RATE*100:.0f}%, 전용면적 없는 매물은 계약면적×{rb.EXCLUSIVE_RATIO_GUESS} 로 추정",
           ""]

    # 1. 한 줄 요약 --------------------------------------------------------
    est = rb.estimate_monthly_cost(baselines, sido, sgg, umd, band, pyeong)
    summary = []
    summary.append(f"상권 활성도 **{act.get('label')}**" + (f"(지수 {act['index']})" if act.get("index") is not None else ""))
    if est:
        summary.append(f"{band} 전용 {pyeong:.0f}평 개원 시 예상 월세 약 **{est['rent_est']:,.0f}만원**"
                       f"(보증금 약 {est['deposit_est']:,.0f}만원, 범위 {est['rent_low']:,.0f}~{est['rent_high']:,.0f}만원)")
    else:
        summary.append(f"{band} 임대료 기준선 없음(표본 부족)")
    med_list = [l for l in region_listings if l.get("medical_flag")]
    summary.append(f"병원 관련 매물 {len(med_list)}건")
    out += ["## 1. 한 줄 요약", "", " / ".join(summary), ""]

    # 2. 임대료 기준선 ------------------------------------------------------
    out += ["## 2. 임대료 기준선 (전용평당 환산월세, 만원/평)", "",
            "| 층대 | 표본 | 중앙값 | p25~p75 | 시장 보증금 중앙값 | 시장 월세 중앙값 | 전용평 중앙값 | 기준 |",
            "|---|---:|---:|---|---:|---:|---:|---|"]
    for b in BANDS:
        summ, level = rb.lookup(baselines, sido, sgg, umd, b)
        if not summ:
            continue
        out.append(f"| {b} | {summ['n']} | {summ['median']:.1f} | {summ['p25']:.1f}~{summ['p75']:.1f} | "
                   f"{_fmt(summ.get('deposit_med'))} | {_fmt(summ.get('rent_med'))} | "
                   f"{_fmt(summ.get('py_med'), 0)} | {level} |")
    if est:
        out += ["", f"**{band} 전용 {pyeong:.0f}평 기준 예상 임대 조건** ({est['level']}, n={est['n']})", "",
                f"- 환산월세 총액 약 {est['conv_rent']:,.0f}만원/월",
                f"- 월세 약 {est['rent_est']:,.0f}만원 + 보증금 약 {est['deposit_est']:,.0f}만원 (보증금=월세 10개월 관행 가정)",
                f"- 협상 여지 범위(p25~p75): 월세 {est['rent_low']:,.0f}~{est['rent_high']:,.0f}만원",
                "- 관리비·부가세 별도. 실제 계약은 렌트프리(무상임대) 개월 수로 조정되는 경우가 많으니 명목 월세만 보지 말 것."]
    out.append("")

    # 3. 권리금·활성도 ----------------------------------------------------
    out += ["## 3. 권리금·공실로 본 상권 활성도", ""]
    if act.get("n"):
        out += [f"- 임대 매물 {act['n']}건 중 권리금 있음 {act['has']}건 / 없음 {act['none']}건 / 언급 없음 {act['unknown']}건",
                f"- 권리금 비율(언급 매물 기준): {_fmt((act['premium_ratio'] or 0)*100 if act['premium_ratio'] is not None else None, 0, '%')}",
                f"- 공실·즉시입주 매물 비율: {act['vacancy_ratio']*100:.0f}%",
                f"- 30일 이상 미거래 매물 비율: {_fmt((act['stale_ratio'] or 0)*100 if act['stale_ratio'] is not None else None, 0, '%')}"
                f" (매물 잔존일 중앙값 {_fmt(act['median_days_listed'], 0, '일')}; 수집을 반복해야 의미 있음)",
                f"- 판정: **{act['label']}** (활성 지수 {act['index']})"]
        for n in act.get("notes", []):
            out.append(f"  - {n}")
        # 층대별 권리금 분포
        by_band = {}
        for l in rent_listings:
            by_band.setdefault(l.get("floor_band") or "미상", []).append(l)
        out += ["", "| 층대 | 임대 매물 | 권리금 있음 | 무권리 | 공실 힌트 |", "|---|---:|---:|---:|---:|"]
        for b in BANDS:
            ls = by_band.get(b)
            if not ls:
                continue
            out.append(f"| {b} | {len(ls)} | {sum(1 for x in ls if x.get('premium')=='있음')} | "
                       f"{sum(1 for x in ls if x.get('premium')=='없음')} | {sum(1 for x in ls if x.get('vacancy_hint'))} |")
    else:
        out.append("- 임대 매물 표본 없음")
    out.append("")

    # 4. 병원 관련 매물 ----------------------------------------------------
    out += ["## 4. 병원 관련 매물 (양도 · 전 병원 자리 · 메디컬빌딩)", ""]
    if med_list:
        order = {"병원양도": 0, "전병원자리": 1, "메디컬빌딩": 2, "의료가능": 3, "의료언급": 4}
        med_list.sort(key=lambda l: min(order.get(f, 9) for f in l["medical_flag"].split(",")))
        for l in med_list[:30]:
            out.append(f"- {_listing_line(l, rb.assess(l, baselines))}")
    else:
        out.append("- 해당 없음 (매물 문구에 병원/의원/치과/한의원 등의 언급이 없음)")
    out.append("")

    # 5. 경쟁·보완 시설 ----------------------------------------------------
    out += ["## 5. 경쟁·보완 시설 (심평원 등록 기준)", ""]
    local_clinics = [c for c in clinics
                     if (sgg.replace(" ", "") in str(c.get("sggu_name") or "").replace(" ", ""))
                     and (not umd or umd in str(c.get("emdong_name") or ""))]
    if local_clinics:
        cnt = {}
        for c in local_clinics:
            cnt[c.get("cl_name") or c.get("cl_cd")] = cnt.get(c.get("cl_name") or c.get("cl_cd"), 0) + 1
        out.append("- " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items(), key=lambda x: -x[1])))
        if dept:
            same = [c for c in local_clinics if dept in str(c.get("name") or "") or dept in str(c.get("dgsbjt") or "")]
            out.append(f"- 동일과({dept}) 추정 {len(same)}곳: " + ", ".join(str(c.get("name")) for c in same[:20]))
        n_med_listings = len(rent_listings)
        if n_med_listings:
            out.append(f"- 의원급(의원+치과의원+한의원) {sum(cnt.get(k,0) for k in ('의원','치과의원','한의원'))}곳 대비 임대 매물 {n_med_listings}건")
    else:
        out.append("- 심평원 병의원 데이터 없음. `python shop_pipeline.py --clinics 서울` 로 먼저 적재하세요.")
    out.append("")

    # 6. 개원 후보 매물 ----------------------------------------------------
    out += [f"## 6. 개원 후보 매물 Top {top_n} (상층 우선 · 전용 25~100평 · 임대료 적정 이하 우선)", ""]
    cands = []
    for l in rent_listings:
        py, _ = rb.exclusive_pyeong(l)
        if not py or py < 25 or py > 120:
            continue
        if (l.get("floor_band") or "") == "지하":
            continue
        assess = rb.assess(l, baselines)
        comp = mf.competition(l.get("lat"), l.get("lon"), clinics, radius_m, dept) if clinics else None
        hint = mf.fit_hint(l, assess, act, comp, dept)
        cands.append((hint["score"], l, assess, hint))
    cands.sort(key=lambda x: -x[0])
    if cands:
        for score, l, assess, hint in cands[:top_n]:
            out.append(f"### {hint['verdict']} · 점수 {score} · {_listing_line(l, assess)}")
            for line in hint["lines"]:
                out.append(f"- {line}")
            out.append("")
    else:
        out.append("- 조건에 맞는 매물 없음")
    out.append("")

    # 7. 직접 확인할 것 ----------------------------------------------------
    out += ["## 7. 직접 확인할 것 (프로그램이 못 하는 부분)", "",
            "- 건축물대장 용도: 의료기관은 1·2종 근린생활시설(의원) 또는 의료시설이어야 하고, 층별 용도가 맞는지 확인. 용도변경 필요 시 비용·기간·건물주 동의.",
            "- 정화조 용량·전기 용량·급배수 위치: 의원(특히 진료실 다수·장비)은 증설이 필요한 경우가 흔함.",
            "- 방사선(X-ray) 설치 시 차폐 공사 가능 여부와 아래층 용도(주거·유치원 등 제한).",
            "- 엘리베이터·장애인 접근성(경사로, 화장실): 2층 이상 개원 필수 체크.",
            "- 임대차 조건: 렌트프리, 관리비 실비, 원상복구 범위, 동일업종 입점 제한 조항(메디컬빌딩이면 특히).",
            "- 권리금은 상가임대차보호법상 회수 기회 보호 대상이지만, 병원 양도는 '의료기관 개설 신고'와 별개이므로 사업 양수도 계약서를 따로 검토.",
            "- 배후 인구·연령 구조·유동 인구(출근/주거/학교)는 현장 시간대별 방문으로 확인. 여기 지표는 매물 문구 기반 힌트일 뿐.",
            "", f"_자동 생성 · 데이터: 네이버 부동산 매물 스냅샷{', 심평원 병의원 정보' if local_clinics else ''} · {today}_"]
    return "\n".join(out)
