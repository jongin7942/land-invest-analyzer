"""GitHub Pages용 정적 사이트 생성기.

DB의 모든 후보를 정적 HTML로 구워 docs/ 에 쌓는다(GitHub 저장소 설정에서
Pages 소스를 main 브랜치의 /docs 폴더로 지정하면 그대로 배포됨).
검색엔진에는 안 걸리지만(robots.txt + noindex 메타), 링크를 아는 사람은
누구나 열람 가능 — 카톡으로 링크를 보내면 다른 사람도 모바일 웹으로 볼 수 있다.

사용:
  python build_static.py
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from app import app
from analysis.narrative import build_narrative
from collectors.news import gosi_search_links
from db import schema

DOCS_DIR = Path(__file__).resolve().parent / "docs"

# 정적 목록 페이지(JS 클라이언트 필터링)에 실을 최소 필드만 추림 — 용량 절약
LIST_FIELDS = (
    "id", "address", "name", "zoning", "road_grade", "land_group",
    "score", "comp_score", "pct_below", "comp_discount", "news_count",
    "area_m2", "min_bid",
)


def build():
    schema.init_db()
    with schema.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM auction_candidate WHERE score IS NOT NULL OR comp_score IS NOT NULL"
        ).fetchall()
    rows = [dict(r) for r in rows]
    print(f"대상 {len(rows)}건")

    # docs/ 는 GitHub Pages 산출물이면서 **엔지니어링 문서(docs/*.md)도 들어 있다.**
    # 예전에는 통째로 rmtree 해서, 여기 있던 문서가 빌드 한 번에 사라졌다(실제로 겪음).
    # 그래서 이 스크립트가 만드는 것만 지운다.
    GENERATED = {"index.html", "robots.txt", ".nojekyll"}
    if DOCS_DIR.exists():
        shutil.rmtree(DOCS_DIR / "candidate", ignore_errors=True)
        for name in GENERATED:
            (DOCS_DIR / name).unlink(missing_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "candidate").mkdir(exist_ok=True)

    # robots.txt — 검색엔진 수집 차단(링크를 아는 사람만 접근 가능하게)
    (DOCS_DIR / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    # GitHub Pages가 _로 시작하는 폴더/파일을 무시하지 않도록(Jekyll 처리 비활성화)
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")

    with app.test_request_context():
        list_rows = [{k: r.get(k) for k in LIST_FIELDS} for r in rows]
        index_html = app.jinja_env.get_template("static_index.html").render(
            rows_json=json.dumps(list_rows, ensure_ascii=False), noindex=True,
        )
        (DOCS_DIR / "index.html").write_text(index_html, encoding="utf-8")

        for r in rows:
            narrative = build_narrative(r)
            try:
                raw = json.loads(r.get("raw_json") or "{}")
            except ValueError:
                raw = {}
            gosi_links = gosi_search_links(raw.get("lctnSggnm") or "", raw.get("lctnEmdNm") or "")
            html = app.jinja_env.get_template("detail.html").render(
                r=r, narrative=narrative, gosi_links=gosi_links,
                link_prefix="", link_suffix=".html", list_link="../index.html", noindex=True,
            )
            (DOCS_DIR / "candidate" / f"{r['id']}.html").write_text(html, encoding="utf-8")

    print(f"완료: {DOCS_DIR} 에 index.html + candidate/*.html {len(rows)}개 생성됨")


if __name__ == "__main__":
    build()
