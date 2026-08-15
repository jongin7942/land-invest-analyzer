"""도로접면(roadSideCodeNm) → 건축가능성 판정.

김종률식 핵심 필터: "건축법상 4m 도로에 2m 이상 접하지 않으면 맹지 = 건축 불가."
개별공시지가 조사의 '도로접면' 코드로 1차 스크리닝한다(정밀 확인은 현황·지적 병행).

도로접면 등급(넓음→좁음):
  광대(로한면/소각/세각) > 중로(한면/각지) > 소로(한면/각지)
  > 세로(가)=자동차통행 가능 세로 > 세로(불)=자동차통행 불가 > 맹지
"""
from __future__ import annotations

# 판정 등급
OK = "건축양호"        # 4m 이상 도로 접함으로 볼 수 있음
NARROW = "확인필요"    # 세로(가): 좁은 도로, 실측/현황 확인 권장
BLOCKED = "건축애로"   # 세로(불): 자동차 통행 불가 → 4m 미달 가능성 큼
MENGJI = "맹지"        # 도로 미접 → 건축 불가(진입도로 확보 전)
UNKNOWN = "미상"


def classify(road_side: str | None) -> dict:
    """도로접면 코드 → {grade, buildable, note}."""
    s = (road_side or "").strip()
    if not s:
        return {"grade": UNKNOWN, "buildable": None,
                "note": "도로접면 정보 없음 — 지적도/현황 직접 확인"}
    if s == "맹지":
        return {"grade": MENGJI, "buildable": False,
                "note": "맹지 — 진입도로 확보 전 건축 불가. 김종률식 회피/특수전략 대상"}
    if "불" in s:  # 세로(불), 세각(불)
        return {"grade": BLOCKED, "buildable": False,
                "note": f"{s} — 자동차 통행 불가(4m 미달 가능성). 건축 애로"}
    if s.startswith("세로") or s.startswith("세각"):  # 세로(가), 세각(가)
        return {"grade": NARROW, "buildable": True,
                "note": f"{s} — 좁은 도로. 4m/2m 접도 요건 실측 확인 권장"}
    if s.startswith(("광대", "중로", "소로")):
        return {"grade": OK, "buildable": True,
                "note": f"{s} — 도로 접함 양호"}
    return {"grade": UNKNOWN, "buildable": None, "note": f"미분류 코드: {s}"}
