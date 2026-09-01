"""수도권(서울·경기·인천) 시군구 법정동코드 — 실거래가 API 의 LAWD_CD 5자리.

출처: 행정표준코드관리시스템(code.go.kr) 법정동코드.

토지 프로그램의 `data/gyeonggi_sigungu.py` 와 같은 성격이지만, 아파트 엔진은
토지 모듈을 import 하지 않는다는 원칙(회귀 테스트가 강제한다)에 따라 별도로 둔다.
경기도 코드는 그쪽과 같은 값이며, 여기에 서울 25개 구와 인천 10개 군구를 더했다.

── 코드 개편 처리 ───────────────────────────────────────────────────────
시군구 코드는 구 신설·폐지로 바뀐다. 과거 5년치를 수집하려면 **그 시점에 유효했던
코드**로 요청해야 한다 — 2021년 화성시 거래는 신설된 4개 구 코드로는 안 나온다.

그래서 `codes_for_ym(YYYYMM)` 이 그 달에 유효한 코드 집합을 돌려준다.
개편 시행월은 아래 LEGACY 표에 있고, 값이 틀리면 그 구간이 통째로 비게 되므로
`python -m apt_engine.cli report gaps` 가 월별 공백을 찾아 알려준다.
"""
from __future__ import annotations

SEOUL = {
    "11110": "서울 종로구", "11140": "서울 중구", "11170": "서울 용산구",
    "11200": "서울 성동구", "11215": "서울 광진구", "11230": "서울 동대문구",
    "11260": "서울 중랑구", "11290": "서울 성북구", "11305": "서울 강북구",
    "11320": "서울 도봉구", "11350": "서울 노원구", "11380": "서울 은평구",
    "11410": "서울 서대문구", "11440": "서울 마포구", "11470": "서울 양천구",
    "11500": "서울 강서구", "11530": "서울 구로구", "11545": "서울 금천구",
    "11560": "서울 영등포구", "11590": "서울 동작구", "11620": "서울 관악구",
    "11650": "서울 서초구", "11680": "서울 강남구", "11710": "서울 송파구",
    "11740": "서울 강동구",
}

# 2026-07-01 인천형 행정체제 개편 반영.
#   중구(28110) 내륙 + 동구(28140)  → 제물포구(28125)
#   중구(28110) 영종도             → 영종구(28155)
#   서구(28260) 아라뱃길 이남      → 서해구(28275)   ← 개칭·존속이지만 코드가 바뀐다
#   서구(28260) 아라뱃길 이북      → 검단구(28290)
# 2026-08-31 K-apt 전국 단지목록(22,288건)의 bjdCode 앞 5자리로 대조해 확정했고,
# 국토교통부 실거래가 API totalCount 로도 재확인했다(아래 LEGACY 주석).
INCHEON = {
    "28125": "인천 제물포구", "28155": "인천 영종구", "28177": "인천 미추홀구",
    "28185": "인천 연수구", "28200": "인천 남동구", "28237": "인천 부평구",
    "28245": "인천 계양구", "28275": "인천 서해구", "28290": "인천 검단구",
    "28710": "인천 강화군", "28720": "인천 옹진군",
}

GYEONGGI = {
    "41111": "수원시 장안구", "41113": "수원시 권선구", "41115": "수원시 팔달구",
    "41117": "수원시 영통구",
    "41131": "성남시 수정구", "41133": "성남시 중원구", "41135": "성남시 분당구",
    "41150": "의정부시",
    "41171": "안양시 만안구", "41173": "안양시 동안구",
    "41192": "부천시 원미구", "41194": "부천시 소사구", "41196": "부천시 오정구",
    "41210": "광명시", "41220": "평택시", "41250": "동두천시",
    "41271": "안산시 상록구", "41273": "안산시 단원구",
    "41281": "고양시 덕양구", "41285": "고양시 일산동구", "41287": "고양시 일산서구",
    "41290": "과천시", "41310": "구리시", "41360": "남양주시", "41370": "오산시",
    "41390": "시흥시", "41410": "군포시", "41430": "의왕시", "41450": "하남시",
    "41461": "용인시 처인구", "41463": "용인시 기흥구", "41465": "용인시 수지구",
    "41480": "파주시", "41500": "이천시", "41550": "안성시", "41570": "김포시",
    "41591": "화성시 만세구", "41593": "화성시 효행구", "41595": "화성시 병점구",
    "41597": "화성시 동탄구",
    "41610": "광주시", "41630": "양주시", "41650": "포천시", "41670": "여주시",
    "41800": "연천군", "41820": "가평군", "41830": "양평군",
}

SIDO = {"서울": SEOUL, "인천": INCHEON, "경기": GYEONGGI}

# 현재 유효한 전체 수도권 코드
SIGUNGU: dict[str, str] = {**SEOUL, **INCHEON, **GYEONGGI}

# ── 폐지된 코드 ───────────────────────────────────
# 비어 둔 것이 의도된 상태다.
#
# 당초 "과거 거래는 그 시점의 옆 코드로만 조회된다"고 가정했으나,
# 2026-08-31 라이브 확인 결과 그 가정은 틀렸다. 국토교통부 실거래가 API 는
# 과거 데이터까지 **현재 행정구역 코드로 소급 재편**해서 돌려준다:
#
#   부천  202301: 41190(통합)=0  │ 41192=97  41194=83  41196=16
#   화성  202401: 41590(통합)=0  │ 41591=175 41593=75  41595=129 41597=292
#   인천  202108: 28260(옆 서구)=0 │ 28275=304 28290=306
#
# 즉 폐지 코드는 전 기간 0건이다. 여기에 항목을 다시 넣으면 codes_for_ym() 이
# 그 달에 후속 구 코드를 빼고 폐지 코드를 쓰기 때문에, 해당 구간이 통째로 비게 된다.
# 추가하기 전에 반드시 해당 코드·월로 totalCount 가 0 이 아님을 먼저 확인할 것.
LEGACY: dict[str, dict] = {}


# ── 개편으로 사라진 코드 · 승계 관계 ──────────────────────────────────
#
# **LEGACY 와 완전히 다른 목적이다. 절대 섞지 않는다.**
#
#   LEGACY   수집할 때 어느 코드로 API 를 때릴지 정한다 → 비어 있어야 한다
#   RETIRED  옛 코드가 무엇이었는지 이름을 남긴다        → 조회·이력용
#   LINEAGE  어느 코드가 어느 코드를 승계했는지 남긴다   → 규칙 판정용
#
# RETIRED 항목을 LEGACY 에 옮겨 적으면 codes_for_ym() 이 그 구간을 폐지
# 코드로 요청해 수집이 통째로 0건이 된다(위 주석 참조).
#
# 2026-07-01 인천형 행정체제 개편 — 1995년 이후 31년 만.
#   중구 내륙 + 동구 → 제물포구 / 중구 영종도 → 영종구
#   서구 아라뱃길 이남 → 서해구(개칭) / 이북 → 검단구(신설)
RETIRED: dict[str, dict] = {
    "28110": {"name": "인천 중구", "sido": "인천", "until_ym": "202606"},
    "28140": {"name": "인천 동구", "sido": "인천", "until_ym": "202606"},
    "28260": {"name": "인천 서구", "sido": "인천", "until_ym": "202606"},
}

# (옛 코드, 새 코드, 관계, 승계범위)
#   FULL     옛 구역 전체가 그 새 구로 넘어갔다
#   PARTIAL  옛 구역의 일부만 넘어갔다 — 옛 구에 걸린 규칙을 그대로
#            물려주면 지정된 적 없는 땅까지 규제 대상이 될 수 있다
LINEAGE: tuple[tuple[str, str, str, str], ...] = (
    ("28110", "28125", "SPLIT", "PARTIAL"),   # 중구 내륙 → 제물포구
    ("28110", "28155", "SPLIT", "PARTIAL"),   # 중구 영종도 → 영종구
    ("28140", "28125", "ABSORB", "FULL"),     # 동구 전역 → 제물포구
    ("28260", "28275", "RENAME", "PARTIAL"),  # 서구 이남 → 서해구
    ("28260", "28290", "SPLIT", "PARTIAL"),   # 서구 이북 → 검단구
)

LINEAGE_EFFECTIVE_FROM = "2026-07-01"
LINEAGE_SOURCE = "행정표준코드관리시스템 · 인천광역시 행정체제 개편"


def successors_of(code: str) -> list[str]:
    """이 코드를 승계한 현재 코드들. 개편이 없었으면 빈 목록."""
    return [new for old, new, _, _ in LINEAGE if old == code]


def predecessors_of(code: str) -> list[str]:
    """이 코드가 승계한 옛 코드들."""
    return [old for old, new, _, _ in LINEAGE if new == code]


def _walk(code: str, step) -> set[str]:
    seen: set[str] = set()
    queue = [code]
    while queue:
        for nxt in step(queue.pop()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def ancestors_of(code: str) -> list[str]:
    """이 코드가 승계한 옛 코드 전부 (개편을 여러 번 거쳐도 따라간다)."""
    return sorted(_walk(code, predecessors_of))


def descendants_of(code: str) -> list[str]:
    """이 코드를 승계한 새 코드 전부."""
    return sorted(_walk(code, successors_of))


def related_codes(code: str) -> list[str]:
    """같은 땅을 가리키는 코드 — 자기 자신 + 조상 + 후손.

    **위아래를 섞어 걷지 않는다.** 조상을 타고 올라간 뒤 다시 내려오면
    형제 구까지 딸려온다. 중구(28110)에서 올라간 제물포구가 다시
    영종구(28155)를 끌고 오는 식인데, 영종구는 제물포구와 같은 땅이
    아니다. 지금 데이터에서는 둘 다 같은 지정을 물려받아 결과가 같지만,
    한쪽에만 걸린 규칙이 생기는 순간 조용히 틀린다.

    규칙 판정은 이 집합으로 넓혀 읽되 **유효기간으로 다시 거른다** —
    넓히는 것만으로는 옛 규칙이 되살아나지 않는다.
    """
    return sorted({code} | set(ancestors_of(code)) | set(descendants_of(code)))


def name_of(code: str) -> str:
    if code in SIGUNGU:
        return SIGUNGU[code]
    if code in LEGACY:
        return LEGACY[code]["name"]
    if code in RETIRED:
        return RETIRED[code]["name"]
    return code


def sido_of(code: str) -> str | None:
    for sido, table in SIDO.items():
        if code in table:
            return sido
    if code in LEGACY:
        # 폐지 코드는 후속 코드의 시도를 따른다.
        return sido_of(LEGACY[code]["successors"][0])
    if code in RETIRED:
        return RETIRED[code]["sido"]
    return None


def all_codes(sido: str | None = None) -> list[str]:
    """현재 유효한 코드 목록. sido 를 주면 그 시도만 ('서울'/'경기'/'인천')."""
    if sido is None:
        return list(SIGUNGU)
    if sido not in SIDO:
        raise ValueError(f"알 수 없는 시도: {sido!r} (가능: {', '.join(SIDO)})")
    return list(SIDO[sido])


def codes_for_ym(ym: str, sido: str | None = None) -> list[str]:
    """거래월 YYYYMM 시점에 **유효했던** 코드 목록.

    개편 이전 달이면 후속 구 코드 대신 폐지된 통합 코드를 쓴다 —
    그래야 과거 거래가 조회된다.
    """
    if len(ym) != 6 or not ym.isdigit():
        raise ValueError(f"거래월은 YYYYMM 형식이어야 합니다: {ym!r}")

    codes = all_codes(sido)
    for legacy_code, info in LEGACY.items():
        if ym > info["until_ym"]:
            continue
        successors = [c for c in info["successors"] if c in codes]
        if not successors:
            continue  # 이 시도에 해당하지 않음
        codes = [c for c in codes if c not in successors]
        codes.append(legacy_code)
    return codes
