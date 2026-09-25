"""소상공인시장진흥공단 상가(상권)정보 API — 좌표 반경 내 업소 목록 (data.go.kr, 공식·무료).

data.go.kr 에서 "소상공인시장진흥공단_상가(상권)정보" 활용신청(자동승인) 후
DATA_GO_KR_SERVICE_KEY 로 그대로 호출. 매물 좌표 반경 안에 업소가 몇 개, 어떤 업종이
있는지로 상권 밀도와 성격(먹자골목/오피스/주거 근린/의료 밀집)을 읽는다.
결과는 poi_cache 에 30일 캐시.
"""
from __future__ import annotations

import collections

import requests

import config
from db import shop as shopdb

RADIUS_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInRadius"

MEDICAL_MCLS_WORDS = ("병원", "의원", "의료", "약국", "치과", "한의원")


class SbizError(RuntimeError):
    pass


def stores_in_radius(lon: float, lat: float, radius_m: int = 300, max_pages: int = 10,
                     use_cache: bool = True) -> list[dict]:
    key_cache = f"sbiz:{lon:.5f}:{lat:.5f}:{radius_m}"
    if use_cache:
        hit = shopdb.cache_get(key_cache)
        if hit is not None:
            return hit
    key = config.require_data_go_kr_key()
    out = []
    for page in range(1, max_pages + 1):
        r = requests.get(RADIUS_URL, params={
            "serviceKey": key, "pageNo": page, "numOfRows": 1000, "radius": radius_m,
            "cx": lon, "cy": lat, "type": "json",
        }, timeout=30)
        try:
            data = r.json()
        except ValueError as e:
            raise SbizError(f"JSON 아님(HTTP {r.status_code}): {r.text[:200]}") from e
        hdr = (data.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader")
        if hdr:  # 라이브 확인: 미등록 키면 HTTP 403 + returnReasonCode 30
            raise SbizError(f"data.go.kr 오류 {hdr.get('returnReasonCode')}: {hdr.get('returnAuthMsg') or hdr.get('errMsg')}"
                            " — '소상공인시장진흥공단_상가(상권)정보' 활용신청 여부 확인")
        r.raise_for_status()
        body = data.get("body") or {}
        items = body.get("items") or []
        for it in items:
            out.append({
                "name": it.get("bizesNm"), "lcls": it.get("indsLclsNm"),
                "mcls": it.get("indsMclsNm"), "scls": it.get("indsSclsNm"),
                "lon": it.get("lon"), "lat": it.get("lat"),
            })
        total = int(body.get("totalCount") or 0)
        if not items or len(out) >= total:
            break
    if use_cache:
        shopdb.cache_put(key_cache, out)
    return out


def summarize(stores: list[dict]) -> dict:
    """대분류별 카운트 + 의료 업소 수 + 밀도 라벨."""
    by_l = collections.Counter(s.get("lcls") or "(미분류)" for s in stores)
    medical = sum(1 for s in stores
                  if any(w in (str(s.get("mcls") or "") + str(s.get("scls") or "")) for w in MEDICAL_MCLS_WORDS))
    n = len(stores)
    food = sum(v for k, v in by_l.items() if "음식" in k)
    retail = sum(v for k, v in by_l.items() if "소매" in k)
    label = "고밀도" if n >= 400 else ("중밀도" if n >= 150 else "저밀도")
    character = []
    if n and food / n >= 0.4:
        character.append("먹자·유흥 성격")
    if n and retail / n >= 0.35:
        character.append("소매 근린 성격")
    if medical >= 15:
        character.append("의료 밀집")
    return {"n": n, "by_lcls": dict(by_l.most_common(8)), "medical": medical, "food": food,
            "retail": retail, "density_label": label, "character": character}
