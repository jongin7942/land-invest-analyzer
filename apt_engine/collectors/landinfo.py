"""단지 대지면적 — V-World 토지정보.

재건축 판정의 출발점은 **용적률**이다. 지금 얼마나 지어져 있는지를 알아야 더
지을 여지가 있는지 말할 수 있다. 용적률 = 연면적 ÷ 대지면적인데, 연면적은
K-apt 기본정보(`kaptTarea`)로 99% 확보돼 있고 대지면적이 비어 있었다.

── 왜 건축물대장이 아닌가 ────────────────────────────────────────
건축물대장(BldRgstHubService)은 접근이 되고 동별 표제부·총괄표제부도 나오는데,
**대지면적(platArea)이 일관되게 0 이다.** 실측:

    은마(대치동 316)      총괄표제부 1건 · platArea 0 · vlRat 0 · 4,424세대
    잠실주공5(잠실동 27)   총괄표제부 1건 · platArea 0 · vlRat 0 · 3,930세대
    래미안대치팰리스        총괄표제부 1건 · platArea 0 · vlRat 0

연면적·세대수·동수·사용승인일은 다 채워져 있는데 대지면적 칸만 비어 있다.

── 그래서 토지 쪽에서 가져온다 ──────────────────────────────────
V-World 의 `LT_C_LANDINFOBASEMAP` 레이어가 필지의 `parea`(면적)와 `jimok`(지목)을
준다. 검증:

    은마       대지 239,226㎡ · 연면적 528,772㎡ → 용적률 221.0%
    잠실주공5   대지 296,846㎡ · 연면적 451,776㎡ → 용적률 152.2%

둘 다 알려진 값과 맞는다.

── 주의: 여러 필지에 걸친 단지 ─────────────────────────────────
아파트는 대지권이 한 필지로 통합된 경우가 많지만(은마 = 대치동 316 하나),
여러 필지에 걸친 단지도 있다. 그런 단지는 **대표 필지 하나만 잡히므로 대지면적이
실제보다 작게 나오고, 용적률은 크게 나온다.** 그 방향은 '재건축 여지가 적다' 로
기울어 후보를 놓치는 쪽이라 안전하지만, 정확하지는 않다.

그래서 `land_area_source` 에 어느 필지에서 왔는지를 남기고 `land_area_verified`
는 세우지 않는다 — 연속지적도로 필지를 다 합산하기 전에는 '확인됨' 이 아니다.
"""
from __future__ import annotations

import time

import requests

import config

REVERSE_URL = "https://api.vworld.kr/req/address"
DATA_URL = "https://api.vworld.kr/req/data"
LAND_LAYER = "LT_C_LANDINFOBASEMAP"
SOURCE_KEY = "vworld_landinfo"

# 지목이 '대'(대지)가 아니면 아파트 부지로 보기 어렵다. 도로·구거 같은 필지를
# 대지면적으로 잡으면 용적률이 터무니없어진다.
EXPECTED_JIMOK = {"대"}

# ── 말이 안 되는 값을 거르는 문턱 ────────────────────────────────────
# 단일 필지만 잡힌 단지는 대지면적이 실제의 몇십 분의 일로 나온다. 실측으로
# 용적률 36,607% 같은 값이 나왔다. 그런 값은 재건축 판정에 넣으면 안 된다.
MAX_PLAUSIBLE_FAR = 12.0        # 1,200% — 초고층 주상복합도 이보다 낮다
MIN_LAND_PER_HOUSEHOLD = 5.0    # ㎡/세대 — 아파트는 이보다 좁을 수 없다


class LandInfoError(RuntimeError):
    pass


def _key() -> str:
    if not config.VWORLD_API_KEY:
        raise LandInfoError("VWORLD_API_KEY 가 비어 있습니다. .env 를 확인하세요.")
    return config.VWORLD_API_KEY


def _get(url: str, params: dict, *, timeout: int = 25, retries: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params={**params, "key": _key()}, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
    raise LandInfoError(f"V-World 연결 실패(재시도 {retries}회 소진): {last}")


def pnu_at(lat: float, lon: float) -> tuple[str, str] | None:
    """좌표 → (PNU, 지번주소). 못 찾으면 None — 추측하지 않는다.

    PNU 는 법정동코드(10) + 산여부(1) + 본번(4) + 부번(4) = 19자리다.
    역지오코딩이 주는 조각으로 직접 만든다 — 필지 조회의 열쇠가 이것이다.
    """
    body = _get(REVERSE_URL, {
        "service": "address", "version": "2.0", "request": "GetAddress",
        "format": "json", "crs": "epsg:4326", "type": "PARCEL",
        "point": f"{lon},{lat}",
    })
    res = body.get("response", {})
    if res.get("status") != "OK":
        return None
    items = res.get("result") or []
    if not items:
        return None
    item = items[0]
    st = item.get("structure") or {}
    dong_code = (st.get("level4LC") or "").strip()
    parcel = (st.get("level5") or "").strip()
    if not dong_code or len(dong_code) != 10 or not parcel:
        return None

    # level5 는 '20-75', '316', '산 12-3' 처럼 온다. 뒤에 붙는 글자(도/대 등)는 뗀다.
    #
    # PNU 11번째 자리는 산여부다. **일반 토지가 1, 산이 2** 다(0 이 아니다).
    # 0 으로 만들면 그런 필지가 없어서 조회가 전부 빈손으로 돌아온다.
    mountain = "2" if parcel.startswith("산") else "1"
    digits = parcel.lstrip("산").strip()
    head, _, tail = digits.partition("-")
    bon = "".join(ch for ch in head if ch.isdigit())
    bu = "".join(ch for ch in tail if ch.isdigit())
    if not bon:
        return None
    pnu = f"{dong_code}{mountain}{int(bon):04d}{int(bu or 0):04d}"
    return pnu, item.get("text") or ""


def parcel_area(pnu: str) -> dict | None:
    """PNU → {'area_m2', 'jimok', 'addr'}. 없으면 None."""
    body = _get(DATA_URL, {
        "service": "data", "version": "2.0", "request": "GetFeature",
        "format": "json", "size": "1", "page": "1", "crs": "EPSG:4326",
        "data": LAND_LAYER, "attrFilter": f"pnu:=:{pnu}",
    })
    res = body.get("response", {})
    if res.get("status") != "OK":
        return None
    feats = ((res.get("result") or {}).get("featureCollection") or {}).get("features") or []
    if not feats:
        return None
    p = feats[0].get("properties") or {}
    try:
        area = float(p.get("parea"))
    except (TypeError, ValueError):
        return None
    if area <= 0:
        return None
    return {
        "area_m2": area,
        "jimok": (p.get("jimok") or "").strip(),
        "addr": " ".join(x for x in (p.get("sido_nm"), p.get("sgg_nm"),
                                     p.get("emd_nm"), p.get("jibun")) if x),
    }


def land_of(lat: float, lon: float, *, gross_floor_area_m2: float | None = None,
            households: int | None = None) -> dict | None:
    """좌표 하나로 대지면적까지. 못 믿을 값이면 `skipped` 에 이유를 담아 돌려준다.

    연면적·세대수를 주면 **말이 되는 값인지** 함께 본다. 여러 필지에 걸친 단지는
    대표 필지 하나만 잡혀서 대지면적이 실제의 몇십 분의 일로 나오는데, 그대로
    두면 용적률이 36,000% 같은 값이 된다. 거른 단지는 연속지적도로 필지를
    합산해야 채울 수 있다 — 그때까지는 '확인 불가' 다.
    """
    found = pnu_at(lat, lon)
    if not found:
        return None
    pnu, jibun_text = found
    info = parcel_area(pnu)
    if not info:
        return None
    base = {"pnu": pnu, "jibun": jibun_text}

    if info["jimok"] and info["jimok"] not in EXPECTED_JIMOK:
        # 도로·구거 같은 필지를 대지면적으로 잡으면 용적률이 터무니없어진다.
        return {**base, "skipped": f"지목이 '{info['jimok']}' 입니다"}

    area = info["area_m2"]
    if gross_floor_area_m2 and area > 0:
        far = gross_floor_area_m2 / area
        if far > MAX_PLAUSIBLE_FAR:
            return {**base, "skipped": (
                f"용적률이 {far * 100:,.0f}% 로 계산됩니다 — 대지권이 여러 필지에 "
                f"걸쳐 있어 대표 필지({area:,.0f}㎡)만 잡힌 것으로 봅니다")}
    if households and area / households < MIN_LAND_PER_HOUSEHOLD:
        return {**base, "skipped": (
            f"세대당 대지가 {area / households:.1f}㎡ 뿐입니다 — 필지 일부만 "
            f"잡힌 것으로 봅니다")}

    return {**base, **info}
