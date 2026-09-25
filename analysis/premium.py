"""권리금·공실 힌트 파서 + 동별 상권 활성 지표.

네이버 상가 매물은 권리금을 정형 필드로 주지 않는 경우가 많아, 태그와 매물 특징
설명 문장에서 규칙으로 읽는다. 세 가지 상태:
  없음  '무권리', '권리금 없음', '노권리', '권리금 X' 등
  있음  '권리금 3,000', '권리금 있음', '권리금 협의', '권리 협의' 등
  미상  아무 언급 없음

개원 컨설팅 관점의 해석(개원노트용):
  권리금이 붙은 매물 비율이 높다 = 그 자리에서 장사가 되고 있어 나가는 사람이 값을
  받는다 → 상권 활성. 무권리·공실·즉시입주 비율이 높다 = 빈 자리가 많다 → 침체
  또는 신축 공급 과잉. 둘 다 '힌트'지 결론은 아니다(현장 확인 필수).
"""
from __future__ import annotations

import re

NONE_PATTERNS = (
    r"무\s*권리", r"권리금\s*(없|무|x|X|0원|없음|無)", r"노\s*권리", r"권리\s*없",
    r"no\s*premium", r"권리금\s*x",
)
AMOUNT_PATTERN = r"권리(?:금)?\s*[:：]?\s*(\d+(?:\.\d+)?\s*억)?\s*(\d[\d,]*)?\s*(만|만원)?"
HAS_PATTERNS = (
    r"권리금\s*(있|유|협의|별도|문의|포함)", r"권리\s*(협의|별도|있)", r"시설\s*권리", r"바닥\s*권리",
    r"영업\s*권리",
)
VACANCY_PATTERNS = (r"공실", r"즉시\s*입주", r"바로\s*입주", r"빈\s*상가", r"신축\s*(첫|최초)\s*입주",
                    r"공실\s*상태", r"입주\s*가능")


def _text(listing: dict) -> str:
    return " ".join(str(listing.get(k) or "") for k in ("feature_desc", "tags", "name"))


def parse_premium(listing: dict) -> tuple[str, int | None]:
    """(권리금 상태, 금액 만원 또는 None)."""
    t = _text(listing)
    for p in NONE_PATTERNS:
        if re.search(p, t, flags=re.I):
            return "없음", 0
    # 금액이 명시된 경우
    for m in re.finditer(r"권리(?:금)?\s*[:：]?\s*((?:\d+(?:\.\d+)?\s*억)?\s*(?:\d[\d,]*)?\s*(?:만|만원)?)", t):
        amt_s = m.group(1).replace(" ", "")
        if not re.search(r"\d", amt_s):
            continue
        total = 0
        mm = re.search(r"(\d+(?:\.\d+)?)억", amt_s)
        if mm:
            total += int(float(mm.group(1)) * 10000)
            amt_s = amt_s[mm.end():]
        mm = re.search(r"(\d[\d,]*)", amt_s)
        if mm:
            total += int(mm.group(1).replace(",", ""))
        if total > 0:
            return "있음", total
    for p in HAS_PATTERNS:
        if re.search(p, t, flags=re.I):
            return "있음", None
    return "미상", None


def vacancy_hint(listing: dict) -> int:
    t = _text(listing)
    return 1 if any(re.search(p, t) for p in VACANCY_PATTERNS) else 0


def enrich(listing: dict) -> dict:
    """listing dict 에 premium / premium_amount / vacancy_hint 채워 반환(in-place)."""
    st, amt = parse_premium(listing)
    listing["premium"] = st
    listing["premium_amount"] = amt
    listing["vacancy_hint"] = vacancy_hint(listing)
    return listing


# ------------------------------------------------------- 동별 활성 지표 ----

def _days_between(a: str | None, b: str | None) -> float | None:
    import datetime as dt
    try:
        da = dt.datetime.fromisoformat(a[:19]); db = dt.datetime.fromisoformat(b[:19])
        return (db - da).total_seconds() / 86400
    except (TypeError, ValueError):
        return None


def activity_profile(listings: list[dict], now_iso: str | None = None) -> dict:
    """한 지역(동 또는 시군구)의 임대 매물 묶음으로 상권 활성 지표를 계산.

    반환 필드:
      n, n_known(권리금 언급 있는 매물 수), premium_ratio(있음/(있음+없음)),
      none_ratio, unknown_ratio, vacancy_ratio, stale_ratio(30일 이상 안 나간 매물 비율),
      median_days_listed, index(0~100 활성 지수), label(활성/보통/침체/판단보류), notes[]
    """
    import datetime as dt
    import statistics

    now_iso = now_iso or dt.datetime.now().isoformat(timespec="seconds")
    rent_only = [l for l in listings if (l.get("trade_type") or "") in ("월세", "전세")]
    n = len(rent_only)
    if n == 0:
        return {"n": 0, "label": "판단보류", "index": None, "notes": ["임대 매물 표본 없음"]}
    has = sum(1 for l in rent_only if l.get("premium") == "있음")
    none = sum(1 for l in rent_only if l.get("premium") == "없음")
    unk = n - has - none
    known = has + none
    vac = sum(1 for l in rent_only if l.get("vacancy_hint"))
    days = [d for d in (_days_between(l.get("first_seen"), now_iso) for l in rent_only) if d is not None]
    stale = sum(1 for d in days if d >= 30)

    premium_ratio = has / known if known else None
    vacancy_ratio = vac / n
    stale_ratio = stale / len(days) if days else None
    med_days = statistics.median(days) if days else None

    # 활성 지수: 권리금 비율(60) + 비공실(25) + 회전(15). 정보 없는 항목은 중립(절반)으로.
    idx = 0.0
    idx += 60 * (premium_ratio if premium_ratio is not None else 0.5)
    idx += 25 * (1 - vacancy_ratio)
    idx += 15 * ((1 - stale_ratio) if stale_ratio is not None else 0.5)
    idx = round(idx, 1)

    notes = []
    if known < 5:
        notes.append(f"권리금 언급 매물이 {known}건뿐이라 활성 판단 신뢰도 낮음(미상 {unk}건)")
    if premium_ratio is not None:
        notes.append(f"권리금 있음 {has}건 / 없음 {none}건 → 권리금 비율 {premium_ratio*100:.0f}%")
    if vacancy_ratio >= 0.3:
        notes.append(f"공실·즉시입주 매물 비율 {vacancy_ratio*100:.0f}% — 빈 자리가 많음(공급과잉/침체 의심)")
    if stale_ratio is not None and stale_ratio >= 0.5 and len(days) >= 5:
        notes.append(f"30일 넘게 안 나간 매물 {stale_ratio*100:.0f}% — 임대 회전 느림")

    if known < 5 and (stale_ratio is None or len(days) < 5):
        label = "판단보류"
    elif idx >= 65:
        label = "활성"
    elif idx >= 45:
        label = "보통"
    else:
        label = "침체"
    return {
        "n": n, "n_known": known, "has": has, "none": none, "unknown": unk,
        "premium_ratio": premium_ratio, "none_ratio": (none / known) if known else None,
        "unknown_ratio": unk / n, "vacancy_ratio": vacancy_ratio, "stale_ratio": stale_ratio,
        "median_days_listed": med_days, "index": idx, "label": label, "notes": notes,
    }
