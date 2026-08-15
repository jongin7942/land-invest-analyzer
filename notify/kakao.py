# -*- coding: utf-8 -*-
"""카카오톡 '나에게 보내기(메모)' API. stock-alert(src/kakao.py)와 동일한 방식을 재사용."""
import json

import requests

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


def refresh_access_token(rest_api_key: str, refresh_token: str):
    """refresh_token으로 access_token 재발급. (access_token, new_refresh_token_or_None) 반환."""
    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token,
    }, timeout=15)
    r.raise_for_status()
    j = r.json()
    return j["access_token"], j.get("refresh_token")


def send_memo(access_token: str, text: str, link_url: str = "https://www.onbid.co.kr"):
    """나에게 텍스트 메시지 전송."""
    template = {
        "object_type": "text",
        "text": text[:2000],
        "link": {"web_url": link_url, "mobile_web_url": link_url},
    }
    r = requests.post(
        MEMO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()
