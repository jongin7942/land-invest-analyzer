"""한국부동산원 공동주택 단지정보(K-apt) 수집기.

**이 수집기가 없으면 "1,000세대 이상" 필터 자체가 불가능하다.** 실거래가 API 는
단지명 문자열만 줄 뿐 세대수·동수·사용승인일·대지면적을 주지 않는다.

두 단계로 쓴다:
  1) 시군구별 단지 목록  → 단지코드(kaptCode) 확보
  2) 단지코드별 기본정보 → 세대수·동수·사용승인일·연면적·시공사 등

⚠ 이 API 는 버전 접미사(V2/V3, Service2/Service3)가 자주 바뀌고, 개발 환경에서
라이브 검증을 하지 못했다(프록시가 data.go.kr 차단). 그래서 엔드포인트를 **후보
목록**으로 두고 순서대로 시도한다. 전부 실패하면 어느 URL 이 어떤 이유로 실패했는지
그대로 보여준다 — 활용신청 화면의 '엔드포인트' 를 보고 URL_* 상수에 한 줄 추가하면 된다.

`python -m apt_engine.cli probe kapt-list --lawd 11680` 으로 확인할 수 있다.
"""
from __future__ import annotations

import requests

import config
from apt_engine.collectors import molit

SOURCE_KEY = "kapt_complex"

# 시군구 단지목록 — 앞에서부터 시도한다.
URL_LIST = (
    "https://apis.data.go.kr/1613000/AptListService3/getSigunguAptList3",
    "https://apis.data.go.kr/1613000/AptListService2/getSigunguAptList2",
    "https://apis.data.go.kr/1611000/AptListService2/getSigunguAptList",
)

# 단지 기본정보
URL_BASIS = (
    "https://apis.data.go.kr/1613000/AptBasisInfoServiceV3/getAphusBassInfoV3",
    "https://apis.data.go.kr/1613000/AptBasisInfoServiceV2/getAphusBassInfoV2",
    "https://apis.data.go.kr/1611000/AptBasisInfoService1/getAphusBassInfo",
)

LIST_FIELDS = {
    "kapt_code": ("kaptCode", "단지코드"),
    "name":      ("kaptName", "단지명"),
    "bjd_cd":    ("bjdCode", "법정동코드"),
    "sido":      ("as1",),
    "sgg":       ("as2",),
    "emd":       ("as3",),
    "ri":        ("as4",),
}

BASIS_FIELDS = {
    "kapt_code":   ("kaptCode",),
    "name":        ("kaptName", "단지명"),
    "addr":        ("kaptAddr", "법정동주소"),
    "road_addr":   ("doroJuso", "도로명주소"),
    "bjd_cd":      ("bjdCode", "법정동코드"),
    "kind":        ("codeAptNm", "단지분류"),          # 아파트 / 연립주택 / 도시형생활주택 등
    "households":  ("kaptdaCnt", "세대수"),
    "buildings":   ("kaptDongCnt", "동수"),
    "use_date":    ("kaptUsedate", "사용승인일"),
    "gross_area":  ("kaptTarea", "연면적"),
    "priv_area":   ("privArea", "전용면적합"),
    "builder":     ("kaptBcompany", "시공사"),
    "developer":   ("kaptAcompany", "시행사"),
    "heat":        ("codeHeatNm", "난방방식"),
    "parking_g":   ("kaptdPcntu", "지하주차대수"),
    "parking_o":   ("kaptdPcnt", "지상주차대수"),
    "ho_cnt":      ("hoCnt", "호수"),
}


class KaptError(molit.MolitError):
    pass


def _try_endpoints(urls: tuple[str, ...], params: dict, *, num_of_rows: int,
                   max_pages: int) -> tuple[str, list[dict]]:
    """후보 URL 을 순서대로 시도. 성공한 (url, items) 반환."""
    failures = []
    for url in urls:
        try:
            items = molit.fetch_all_pages(url, params, num_of_rows=num_of_rows,
                                          max_pages=max_pages)
            return url, items
        except molit.MolitAuthError as e:
            # 활용신청이 안 된 것일 수도, URL 이 틀린 것일 수도 있다. 다음 후보로.
            failures.append(f"  {url}\n    → 인증/승인 문제: {e}")
        except (molit.MolitError, requests.RequestException) as e:
            failures.append(f"  {url}\n    → {e}")
    raise KaptError(
        "K-apt 엔드포인트 후보를 전부 시도했지만 실패했습니다.\n"
        + "\n".join(failures)
        + "\n\ndata.go.kr 활용신청 화면의 '엔드포인트' 를 확인해"
          " apt_engine/collectors/kapt.py 의 URL 상수에 추가하세요."
    )


def fetch_complex_list(lawd_cd: str, *, num_of_rows: int = 1000,
                       max_pages: int = 20) -> list[dict]:
    """시군구의 단지 목록. `{kapt_code, name, bjd_cd, emd, …}` 목록."""
    _, items = _try_endpoints(URL_LIST, {"sigunguCode": lawd_cd},
                              num_of_rows=num_of_rows, max_pages=max_pages)
    rows = []
    for raw in items:
        code = molit.pick(raw, LIST_FIELDS["kapt_code"])
        name = molit.pick(raw, LIST_FIELDS["name"])
        if not code or not name:
            continue
        rows.append({
            "kapt_code": code,
            "name": name,
            "lawd_cd": lawd_cd,
            "bjd_cd": molit.pick(raw, LIST_FIELDS["bjd_cd"]),
            "emd_name": molit.pick(raw, LIST_FIELDS["emd"]),
            "raw": raw,
        })
    return rows


def parse_basis(raw: dict) -> dict:
    """기본정보 <item> → complex 행 조각."""
    def f(name: str) -> str | None:
        return molit.pick(raw, BASIS_FIELDS[name])

    use_date = f("use_date")
    approval_year = None
    if use_date and len(use_date) >= 4 and use_date[:4].isdigit():
        approval_year = int(use_date[:4])

    parking = (molit.to_int(f("parking_g")) or 0) + (molit.to_int(f("parking_o")) or 0)

    return {
        "kapt_code": f("kapt_code"),
        "name": f("name"),
        "road_addr": f("road_addr"),
        "jibun_addr": f("addr"),
        "kind": f("kind"),
        # ⚠ kaptdaCnt 는 그 단지의 공동주택 세대수다. 주상복합에서 오피스텔이
        # 섞여 들어올 여지가 있어, 확인되기 전까지 officetel_households 는 건드리지 않는다
        # (합산 컬럼이 아예 없으므로 잘못 더해질 일은 없다).
        "apt_households": molit.to_int(f("households")),
        "building_count": molit.to_int(f("buildings")),
        "approval_date": use_date,
        "approval_year": approval_year,
        "gross_floor_area_m2": molit.to_float(f("gross_area")),
        "builder": f("builder") or f("developer"),
        "heat_type": f("heat"),
        "parking_count": parking or None,
        "raw": raw,
    }


def fetch_basis(kapt_code: str) -> dict | None:
    """단지 하나의 기본정보. 없으면 None."""
    _, items = _try_endpoints(URL_BASIS, {"kaptCode": kapt_code},
                              num_of_rows=10, max_pages=1)
    if not items:
        return None
    return parse_basis(items[0])


def probe_list(lawd_cd: str = "11680") -> str:
    return _probe(URL_LIST, {"sigunguCode": lawd_cd})


def probe_basis(kapt_code: str) -> str:
    return _probe(URL_BASIS, {"kaptCode": kapt_code})


def _probe(urls: tuple[str, ...], params: dict) -> str:
    """후보 URL 을 전부 호출해 각각의 원본 응답을 보여준다."""
    key = config.require_data_go_kr_key()
    out = []
    for url in urls:
        try:
            r = requests.get(url, params={**params, "serviceKey": key,
                                          "pageNo": 1, "numOfRows": 1}, timeout=25)
            out.append(f"── {url}\nHTTP {r.status_code}\n{r.text[:1500]}")
        except requests.RequestException as e:
            out.append(f"── {url}\n요청 실패: {e}")
    return "\n\n".join(out)
