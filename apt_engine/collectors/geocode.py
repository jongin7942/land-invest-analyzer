"""V-World 지오코딩 — 단지 주소를 좌표로.

역세권 거리를 계산하려면 단지 좌표가 필요한데, K-apt 기본정보에는 좌표가 없다.

`VWORLD_API_KEY` 는 이 저장소(land-invest-analyzer) 전용이다. 예전에는 같은
저장소 안의 옛 토지투자 모듈(collectors/land_characteristics.py, main.py,
pipeline.py …)과 나눠 쓰는 것처럼 적혀 있었는데, 그 모듈은 8월 중순 이후
손대지 않은 죽은 코드다(2026-09-04 종인님 확인) - 실제로 경쟁하는 다른
프로그램은 없다. 아파트 엔진은 그 모듈을 import 하지 않는다는 원칙이라 호출
코드는 별도로 두되, 호출 패턴은 그 모듈에서 검증된 것을 따랐다.
"""
from __future__ import annotations

import time

import requests

import config

GEOCODE_URL = "https://api.vworld.kr/req/address"
SOURCE_KEY = "vworld_geocode"


class GeocodeError(RuntimeError):
    pass


def _key() -> str:
    if not config.VWORLD_API_KEY:
        raise GeocodeError(
            "VWORLD_API_KEY 가 비어 있습니다. .env 를 확인하세요.")
    return config.VWORLD_API_KEY


def geocode(address: str, *, road: bool = False, timeout: int = 20,
            retries: int = 3) -> tuple[float, float] | None:
    """주소 → (lat, lon). 못 찾으면 None — 추측하지 않는다."""
    if not address or not address.strip():
        return None
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(GEOCODE_URL, params={
                "service": "address", "version": "2.0", "request": "GetCoord",
                "format": "json", "crs": "epsg:4326",
                "type": "ROAD" if road else "PARCEL",
                "address": address.strip(), "key": _key(),
            }, timeout=timeout)
            r.raise_for_status()
            body = r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
            continue
        if body.get("response", {}).get("status") != "OK":
            return None
        point = body["response"]["result"]["point"]
        return float(point["y"]), float(point["x"])     # (위도, 경도)
    raise GeocodeError(f"V-World 연결 실패(재시도 {retries}회 소진): {last}")


def geocode_complex(road_addr: str | None, jibun_addr: str | None):
    """도로명 → 지번 순으로 시도. 둘 다 실패하면 None."""
    for addr, road in ((road_addr, True), (jibun_addr, False)):
        if not addr:
            continue
        coords = geocode(addr, road=road)
        if coords:
            return coords
    return None
