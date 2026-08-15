"""개발호재 뉴스 수집 + 지자체 고시 바로가기 링크 생성.

네이버 뉴스 검색 API로 후보 물건 지역의 개발 관련 기사를 찾는다.
지자체 고시공고는 시군구마다 사이트가 제각각이라 단일 API가 없어,
검색 딥링크(클릭하면 바로 검색결과로 이동)로 대신한다.
"""
from __future__ import annotations

import html
import re
import urllib.parse

import requests

import config

NEWS_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"

# 토지 가치에 영향을 주는 개발호재 키워드 (김종률 노트: 도로/철도/산단/택지가 핵심 호재 +
# 수용보상 판단에 쓰이는 그린벨트해제/지정고시류)
DEV_KEYWORDS = ("개발계획", "도로 신설", "도로 확장", "IC", "택지지구", "택지개발", "산업단지",
                "테크노밸리", "지구단위계획", "신도시", "역세권", "고속도로", "그린벨트",
                "국공유지", "물류단지", "지정고시", "수용")


def _clean(text: str) -> str:
    """네이버 검색 응답의 <b> 태그·HTML엔티티 제거."""
    return html.unescape(re.sub(r"</?b>", "", text or ""))


class NaverNewsError(RuntimeError):
    pass


def search_news(query: str, display: int = 10, sort: str = "date", timeout: int = 15):
    """뉴스 검색. 결과 없거나 키 미설정이면 빈 리스트(에러로 전체 파이프라인을 막지 않음)."""
    if not config.NAVER_CLIENT_ID or not config.NAVER_CLIENT_SECRET:
        return []
    try:
        r = requests.get(NEWS_URL, params={"query": query, "display": display, "sort": sort},
                         headers={"X-NCP-APIGW-API-KEY-ID": config.NAVER_CLIENT_ID,
                                  "X-NCP-APIGW-API-KEY": config.NAVER_CLIENT_SECRET},
                         timeout=timeout)
        r.raise_for_status()
        items = r.json().get("items", [])
    except (requests.RequestException, ValueError):
        return []
    return [{
        "title": _clean(it.get("title")),
        "description": _clean(it.get("description")),
        "link": it.get("originallink") or it.get("link"),
        "pub_date": it.get("pubDate"),
    } for it in items]


def development_news(sgg: str, emd: str, limit: int = 5):
    """'{시군구} {읍면동} 개발계획' 류 검색으로 호재 뉴스를 모은다.
    1차 시도: 시군구·읍면동을 '둘 다' 언급 + 개발 키워드 포함 기사만(정밀).
    표본이 0건이면: 시군구만 언급 + 개발 키워드 포함으로 완화해 재시도(참고용으로 표시).
    (읍면동명이 흔한 지명이라도 '시군구+읍면동+개발키워드' 3중 매칭이면 노이즈가 크게 줄어든다.
    실측 결과 시군구명만으로 거르면 시장 인터뷰·관광 기사 등이 80%대로 섞여 들어와 무의미했다.)"""
    if not sgg:
        return []
    region = f"{sgg} {emd}".strip()
    raw = search_news(f"{region} 개발계획", display=15, sort="date")
    raw += search_news(f"{region} 지구단위계획", display=10, sort="date")
    raw += search_news(f"{region} 도로 신설", display=10, sort="date")

    sgg_short = sgg.replace("시", "").replace("군", "").replace("구", "")
    emd_short = re.sub(r"(읍|면|동)$", "", emd or "")

    def _filter(require_emd: bool):
        seen, hits = set(), []
        for item in raw:
            if item["link"] in seen:
                continue
            text = item["title"] + item["description"]
            if sgg_short not in text:
                continue
            if require_emd and emd_short and emd_short not in text:
                continue
            if not any(k in text for k in DEV_KEYWORDS):
                continue
            seen.add(item["link"])
            hits.append(item)
        return hits

    hits = _filter(require_emd=True)
    if not hits:
        hits = _filter(require_emd=False)
        for h in hits:
            h["approximate"] = True  # 읍면동까지는 못 좁힌 시군구 단위 참고 매칭
    return hits[:limit]


def gosi_search_links(sgg: str, emd: str) -> list[dict]:
    """지자체 고시공고를 API 없이 바로 확인할 수 있는 검색 딥링크.
    시군구 사이트 도메인이 제각각이라 통합 API가 없어, 클릭 즉시 검색결과로
    이동하는 링크로 대체한다."""
    region = f"{sgg} {emd}".strip()
    links = []
    for label, q in (
        ("고시공고 검색", f"{region} 고시공고"),
        ("도시계획 확인", f"{region} 도시계획 지구단위계획"),
        ("개발계획 뉴스 더보기", f"{region} 개발계획"),
    ):
        links.append({"label": label, "url": "https://search.naver.com/search.naver?query="
                     + urllib.parse.quote(q)})
    return links
