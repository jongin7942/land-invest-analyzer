"""용도지역별 건폐율/용적률 + 건축가능 규모 계산.

값은 국토계획법 시행령의 '최대 한도'다. 실제 적용치는 시군구 도시계획조례로
정하며 대개 같거나 더 낮다(김종률 노트: 국가법령정보센터 > 해당 시군구 도시계획조례
확인). Phase 2에서는 법정 상한으로 1차 계산하고, 조례 보정은 후속으로 붙인다.
"""
from __future__ import annotations

PYEONG_M2 = 3.3058

# 용도지역명(실거래가 zoning 표기 기준) -> (건폐율%, 용적률%)  ※법정 상한
ZONING_LIMITS = {
    "제1종전용주거지역": (50, 100),
    "제2종전용주거지역": (50, 150),
    "제1종일반주거지역": (60, 200),
    "제2종일반주거지역": (60, 250),
    "제3종일반주거지역": (50, 300),
    "준주거지역": (70, 500),
    "중심상업지역": (90, 1500),
    "일반상업지역": (80, 1300),
    "근린상업지역": (70, 900),
    "유통상업지역": (80, 1100),
    "전용공업지역": (70, 300),
    "일반공업지역": (70, 350),
    "준공업지역": (70, 400),
    "보전녹지지역": (20, 80),
    "생산녹지지역": (20, 100),
    "자연녹지지역": (20, 100),
    "보전관리지역": (20, 80),
    "생산관리지역": (20, 80),
    "계획관리지역": (40, 100),
    "농림지역": (20, 80),
    "자연환경보전": (20, 80),
    "자연환경보전지역": (20, 80),
    "개발제한구역": (20, 80),  # 원칙상 신축 제한. 규모계산은 참고용일 뿐 건축가능성은 별도.
}

# 신축 자체가 원칙적으로 제한/불가에 가까운 용도 → 규모계산보다 '주의' 플래그
RESTRICTED = {"농림지역", "자연환경보전", "자연환경보전지역", "개발제한구역",
              "보전녹지지역", "보전관리지역"}


def limits_for(zoning: str):
    """(건폐율, 용적률, 주의여부). 미등록 용도면 None."""
    key = (zoning or "").strip()
    if key not in ZONING_LIMITS:
        return None
    bcr, far = ZONING_LIMITS[key]
    return bcr, far, key in RESTRICTED


def buildable(zoning: str, land_area_m2: float):
    """대지면적 기준 '지을 수 있는 규모' 1차 계산(법정 상한).
    반환: dict 또는 None(미등록 용도)."""
    lim = limits_for(zoning)
    if lim is None or not land_area_m2:
        return None
    bcr, far, restricted = lim
    footprint = land_area_m2 * bcr / 100.0      # 건축면적(1층 바닥)
    gross = land_area_m2 * far / 100.0          # 연면적 상한
    approx_floors = far / bcr if bcr else None  # 대략 층수
    return {
        "zoning": zoning,
        "bcr": bcr,
        "far": far,
        "land_pyeong": land_area_m2 / PYEONG_M2,
        "footprint_m2": footprint,
        "footprint_pyeong": footprint / PYEONG_M2,
        "gross_m2": gross,
        "gross_pyeong": gross / PYEONG_M2,
        "approx_floors": approx_floors,
        "restricted": restricted,
    }
