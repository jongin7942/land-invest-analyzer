"""특수조건 매물 감지 (요구사항 4).

최저호가가 특수매물이면 그건 시장가격이 아니다. 그래서 항상 두 값을 따로 낸다:

    최저호가          6.05억  (급매 · 1층)
    정상매물 최저호가  6.20억

키워드 매칭이라 완벽하지 않다. 그래서 "특수매물이 아니다"라고 단정하지 않고,
감지된 근거(어떤 키워드가 걸렸는지)를 함께 남겨 사람이 확인할 수 있게 한다.
"""
from __future__ import annotations

import re

# (플래그, 키워드들) — 매물특징 원문에서 찾는다.
KEYWORD_FLAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("급매",       ("급매", "급급매", "급처", "시세이하", "싸게")),
    ("수리필요",   ("수리필요", "올수리필요", "내부수리", "상태불량", "노후",
                   "리모델링필요", "손봐야")),
    ("세입자승계", ("세입자승계", "임차인승계", "전세끼고", "전세안고", "월세승계",
                   "세안고", "갭투자")),
    ("입주제한",   ("입주불가", "입주협의", "만기후", "명도", "입주어려움")),
    ("조건부",     ("조건부", "협의", "잔금조건", "대출승계", "특약")),
    ("저층",       ("저층", "1층", "일층", "반지하")),
    ("최상층",     ("최상층", "탑층")),
    ("확장안됨",   ("비확장", "확장안", "미확장")),
)

# 층 자체로 판정하는 것 — 문구가 없어도 저층이면 저층이다.
LOW_FLOOR_MAX = 2


def detect(features: str | None, *, floor: int | None = None,
           top_floor: int | None = None, tenant_status: str | None = None) -> list[str]:
    """특수조건 플래그 목록. 없으면 빈 리스트."""
    text = " ".join(filter(None, [features or "", tenant_status or ""]))
    normalized = re.sub(r"[\s,./·]+", "", text)

    flags: list[str] = []
    for flag, keywords in KEYWORD_FLAGS:
        if any(k in normalized for k in keywords):
            flags.append(flag)

    if floor is not None and floor <= LOW_FLOOR_MAX and "저층" not in flags:
        flags.append("저층")
    if floor is not None and top_floor is not None and floor == top_floor \
            and top_floor > 1 and "최상층" not in flags:
        flags.append("최상층")

    return flags


def floor_group(floor: int | None, top_floor: int | None) -> str | None:
    """저층 / 중층 / 고층 (요구사항 4).

    전체 층수를 모르면 판정하지 않는다 — 15층 건물의 10층과 30층 건물의 10층은 다르다.
    """
    if floor is None:
        return None
    if top_floor is None:
        return "저층" if floor <= LOW_FLOOR_MAX else None
    if top_floor <= 0:
        return None
    ratio = floor / top_floor
    if floor <= LOW_FLOOR_MAX or ratio <= 1 / 3:
        return "저층"
    if ratio <= 2 / 3:
        return "중층"
    return "고층"


def is_special(flags: list[str]) -> bool:
    """정상매물 통계에서 뺄 것인가.

    '최상층'과 '확장안됨'은 선호도 차이일 뿐 특수조건은 아니라고 본다 —
    시세 자체를 왜곡하지는 않는다.
    """
    hard = {"급매", "수리필요", "세입자승계", "입주제한", "조건부", "저층"}
    return bool(set(flags) & hard)
