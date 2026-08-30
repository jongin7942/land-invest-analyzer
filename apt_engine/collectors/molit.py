"""국토교통부 실거래가 API 공통 계층 (data.go.kr).

토지 쪽 `collectors/land_trade.py` 에서 검증된 패턴을 그대로 가져왔다:
  * XML 고정 응답 파싱
  * 에러 응답(<errMsg>, resultCode)을 정상 응답과 구분
  * **필드 후보 배열** — API 개편으로 필드명이 바뀌어도 견딘다

아파트 실거래가 API 는 2023년 개편으로 필드명이 한글에서 영문으로 바뀌었고,
데이터셋에 따라 아직 한글 필드가 남아 있기도 하다. 어느 쪽이 오든 파싱되게
후보를 둘 다 적어 둔다. 실제 응답은 `python -m apt_engine.cli probe` 로 확인한다.

인증키는 토지 프로그램과 같은 `DATA_GO_KR_SERVICE_KEY` 를 쓴다
(계정당 인증키 공유. 데이터셋별 활용신청만 따로 하면 된다).
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import requests

import config


class MolitError(RuntimeError):
    pass


class MolitAuthError(MolitError):
    """인증키 문제 — 미등록/미승인/오타. 재시도해도 소용없다."""


def pick(raw: dict, keys: tuple[str, ...]) -> str | None:
    """필드 후보를 순서대로 시도해 처음 발견된 비어 있지 않은 값을 반환."""
    for k in keys:
        v = raw.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def to_float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def to_int(s: str | None) -> int | None:
    if s is None:
        return None
    try:
        return int(float(str(s).replace(",", "")))
    except ValueError:
        return None


def parse_items(xml_text: str) -> list[dict]:
    """<item> 하위 태그를 통째로 dict 로. 에러 응답이면 예외."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise MolitError(f"XML 파싱 실패(응답이 XML이 아님): {e}\n앞부분: {xml_text[:200]}") from e

    # 인증 실패 등은 다른 스키마로 온다: <OpenAPI_ServiceResponse>…<errMsg>
    err = root.find(".//errMsg")
    if err is not None:
        auth = root.findtext(".//returnAuthMsg")
        code = root.findtext(".//returnReasonCode")
        raise MolitAuthError(f"API 에러 응답: {auth or err.text} (reasonCode={code})")

    rc = root.findtext(".//resultCode")
    if rc is not None and rc not in ("00", "000"):
        msg = root.findtext(".//resultMsg") or "?"
        if rc in ("03",):  # NODATA — 그 달에 거래가 없음. 정상이다
            return []
        if rc in ("30", "31", "20", "22"):  # 인증키/사용량 관련
            raise MolitAuthError(f"resultCode={rc} msg={msg}")
        raise MolitError(f"resultCode={rc} msg={msg}")

    return [{child.tag: (child.text or "") for child in item} for item in root.iter("item")]


def request(url: str, params: dict, *, timeout: int = 25, retries: int = 3) -> str:
    """GET + 지수 백오프 재시도. 인증 오류는 재시도하지 않는다."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.text
        except requests.HTTPError as e:
            last = e
            if r.status_code < 500:  # 4xx 는 재시도해도 같다
                raise MolitError(f"HTTP {r.status_code}: {r.text[:200]}") from e
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
        if attempt < retries - 1:
            time.sleep(0.5 * (2 ** attempt))
    raise MolitError(f"요청 실패(재시도 {retries}회 소진): {last}")


def fetch_all_pages(url: str, base_params: dict, *, num_of_rows: int = 1000,
                    max_pages: int = 50, pause: float = 0.1) -> list[dict]:
    """페이징을 끝까지 돌며 <item> 을 모은다."""
    key = config.require_data_go_kr_key()
    items: list[dict] = []
    page = 1
    while page <= max_pages:
        text = request(url, {**base_params, "serviceKey": key,
                             "pageNo": page, "numOfRows": num_of_rows})
        page_items = parse_items(text)
        items.extend(page_items)
        if len(page_items) < num_of_rows:
            break
        page += 1
        time.sleep(pause)
    return items


def probe(url: str, params: dict, *, timeout: int = 25) -> str:
    """라이브 원본 응답을 그대로 보여준다 — 필드명 확인용.

    개발 컨테이너에서는 data.go.kr 이 막혀 있어 실제 필드명을 검증할 수 없었다.
    사용자 PC에서 이 명령 한 번으로 확인하고, 다르면 각 수집기의 FIELDS 후보에
    추가하면 된다.
    """
    key = config.require_data_go_kr_key()
    r = requests.get(url, params={**params, "serviceKey": key,
                                  "pageNo": 1, "numOfRows": 1}, timeout=timeout)
    masked = r.url.replace(key, "***KEY***")
    return f"HTTP {r.status_code}\n{masked}\n\n{r.text[:4000]}"


def ymd(year: str | None, month: str | None, day: str | None) -> str | None:
    """(년, 월, 일) → 'YYYYMMDD'. 하나라도 없으면 None."""
    if not (year and month and day):
        return None
    try:
        return f"{int(year):04d}{int(month):02d}{int(day):02d}"
    except ValueError:
        return None
