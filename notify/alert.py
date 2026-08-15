# -*- coding: utf-8 -*-
"""Phase 5: 저평가 급매 후보를 카카오톡 '나에게 보내기'로 발송.

사용:
  python notify/alert.py --test              # 연동 테스트 메시지
  python notify/alert.py --send --top 5       # 상위 5건 발송
  python notify/alert.py --send --top 10 --min-score 60   # 점수 60 이상만
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

import config
from db import schema
from notify import kakao


def _load_refresh_token() -> str:
    if not config.KAKAO_TOKEN_PATH.exists():
        raise RuntimeError(
            "kakao_token.json 이 없습니다. stock-alert 카카오 계정을 복사하는 절차를 먼저 진행하세요."
        )
    data = json.loads(config.KAKAO_TOKEN_PATH.read_text(encoding="utf-8-sig"))
    rt = data.get("refresh_token")
    if not rt:
        raise RuntimeError("kakao_token.json 에 refresh_token이 비어 있습니다.")
    return rt


def _save_refresh_token(rt: str):
    config.KAKAO_TOKEN_PATH.write_text(
        json.dumps({"refresh_token": rt}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _access_token() -> str:
    if not config.KAKAO_REST_API_KEY:
        raise RuntimeError("KAKAO_REST_API_KEY 가 비어 있습니다. .env 확인.")
    rt = _load_refresh_token()
    access, new_rt = kakao.refresh_access_token(config.KAKAO_REST_API_KEY, rt)
    if new_rt:  # 카카오가 가끔 refresh_token을 회전시킴 — 갱신되면 저장
        _save_refresh_token(new_rt)
    return access


def _use_public() -> bool:
    return bool(config.PUBLIC_BASE_URL)


def list_url() -> str:
    """전체 목록 링크. PUBLIC_BASE_URL(GitHub Pages) 있으면 그걸 우선(PC 꺼져있어도 됨)."""
    if _use_public():
        return f"{config.PUBLIC_BASE_URL}/index.html"
    return f"{config.BASE_URL}/"


def candidate_url(cid: int) -> str:
    """정적 사이트는 candidate/<id>.html, 라이브 Flask는 /candidate/<id>."""
    if _use_public():
        return f"{config.PUBLIC_BASE_URL}/candidate/{cid}.html"
    return f"{config.BASE_URL}/candidate/{cid}"


def format_message(rows, header: str | None = None) -> str:
    """카톡은 내용이 길면 읽기 힘드니, 후보마다 한줄 요약 + 상세는 링크로 뺀다.
    폰에서 링크를 누르면 모바일 웹으로 왜 급매인지/체크리스트까지 다 볼 수 있다."""
    today = dt.date.today().isoformat()
    lines = [header or f"[토지 급매 알림] {today}", f"전체 목록: {list_url()}"]
    for i, r in enumerate(rows, 1):
        dev = f"개발{r['score']:.0f}" if r["score"] is not None else "개발-"
        comp = f"보상{r['comp_score']:.0f}" if r["comp_score"] is not None else "보상-"
        flag = " ⚠지분" if r["name"] and "지분" in r["name"] else ""
        lines.append(f"\n{i}. {r['address']} ({r['zoning'] or '용도미상'}){flag}")
        lines.append(f"   {dev} · {comp} · {r['road_grade'] or '?'}")
        lines.append(f"   {candidate_url(r['id'])}")
    return "\n".join(lines)


def send_top_candidates(limit: int = 5, min_score: float | None = None):
    rows = schema.top_candidates(limit)
    if min_score is not None:
        rows = [r for r in rows if (r["score"] if r["score"] is not None else -999) >= min_score]
    if not rows:
        return None
    msg = format_message(rows)
    access = _access_token()
    return kakao.send_memo(access, msg, link_url=list_url())


def send_test():
    access = _access_token()
    return kakao.send_memo(
        access,
        f"[토지급매탐지기] 연동 테스트 메시지\n{dt.datetime.now():%Y-%m-%d %H:%M}\n"
        f"이 메시지가 왔다면 카카오 연동 성공입니다.\n웹앱: {list_url()}",
        link_url=list_url(),
    )


def main(argv=None):
    p = argparse.ArgumentParser(description="Phase5: 카카오톡 급매 알림 발송")
    p.add_argument("--test", action="store_true", help="연동 테스트 메시지 발송")
    p.add_argument("--send", action="store_true", help="상위 후보 발송")
    p.add_argument("--top", type=int, default=5, help="발송할 상위 건수")
    p.add_argument("--min-score", type=float, help="이 점수 이상만 발송(미지정시 상위 N건 무조건 발송)")
    args = p.parse_args(argv)

    if args.test:
        send_test()
        print("전송 완료. 카카오톡 '나에게 보내기'를 확인하세요.")
    elif args.send:
        schema.init_db()
        r = send_top_candidates(args.top, args.min_score)
        print("전송 완료" if r else "조건을 만족하는 후보가 없어 전송하지 않았습니다.")
    else:
        p.print_help()


if __name__ == "__main__":
    main()
