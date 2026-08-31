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

import json
import time
import xml.etree.ElementTree as ET

import requests

import config


class MolitError(RuntimeError):
    pass


class MolitAuthError(MolitError):
    """인증키 문제 — 미등록/미승인/오타. 재시도해도 소용없다."""


class MolitQuotaError(MolitError):
    """일일 트래픽 한도 소진. 재시도해도 소용없고, 자정에 리셋된다.

    인증 오류와 달리 '내일 다시 하면 되는' 문제라, 실패로 쌓지 말고 즉시 멈춘다.
    남은 달을 계속 두드리면 실패 기록만 수백 건 쌓이고 시간만 버린다.
    """


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


def _parse_items_json(text: str) -> list[dict]:
    """JSON 응답(K-apt AptListService4 계열)을 XML 경로와 같은 규약으로 파싱.

    실거래가 API 는 XML 만 주지만 K-apt 단지목록은 JSON 만 준다(`_type=xml` 무시).
    두 계열을 한 함수로 받기 위해, JSON 도 `[{필드: 값}, …]` 로 평탄화해서
    돌려준다. resultCode 해석 규칙은 XML 쪽과 동일하게 맞춘다.
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise MolitError(f"JSON 파싱 실패: {e}\n앞부분: {text[:200]}") from e

    resp = doc.get("response", doc)
    header = resp.get("header") or {}
    rc = str(header.get("resultCode", "")).strip()
    msg = header.get("resultMsg") or "?"
    if rc and rc not in ("00", "000"):
        if rc in ("03",):
            return []
        if rc in ("30", "31", "20", "22"):
            raise MolitAuthError(f"resultCode={rc} msg={msg}")
        raise MolitError(f"resultCode={rc} msg={msg}")

    body = resp.get("body") or {}
    # 응답 모양이 세 가지다:
    #   단지목록(AptListService4)      → body.items = […]
    #   기본정보(AptBasisInfoServiceV5) → body.item  = {…}   (단수 dict)
    #   일부 구형 API                  → body.items = {"item": […]}
    items = body.get("items")
    if isinstance(items, dict):          # {"item": …} 래핑
        items = items.get("item")
    if not items:
        items = body.get("item")
    if not items:                        # None / "" / [] / {}
        return []
    if isinstance(items, dict):          # 단수 레코드
        items = [items]

    # None 값은 빈 문자열로 — pick() 이 XML 경로와 동일하게 동작하도록
    return [{k: ("" if v is None else v) for k, v in row.items()} for row in items]


def parse_items(xml_text: str) -> list[dict]:
    """<item> 하위 태그를 통째로 dict 로. 에러 응답이면 예외.

    JSON 응답이면 `_parse_items_json` 으로 넘긴다. 인증 오류는 JSON API 라도
    XML(<OpenAPI_ServiceResponse>)로 오기 때문에 본문 첫 글자로 갈라야 한다.
    """
    if xml_text.lstrip()[:1] in ("{", "["):
        return _parse_items_json(xml_text)
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
            if r.status_code == 429 or "LIMITED_NUMBER_OF_SERVICE_REQUESTS" in r.text:
                raise MolitQuotaError(
                    f"일일 트래픽 한도를 다 썼습니다 (HTTP {r.status_code}). "
                    f"자정에 리셋되면 다시 돌리세요 — 이미 받은 달은 건너뜁니다."
                ) from e
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
