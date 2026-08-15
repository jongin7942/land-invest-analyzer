"""로컬 웹 대시보드: 급매 후보를 "왜 급매인지" + "김종률이라면 어떻게 볼지"와 함께 열람.

실행:
  python app.py
  브라우저에서 http://localhost:5000 접속
"""
from __future__ import annotations

import json
import re

from flask import Flask, render_template, request, abort

from analysis.narrative import build_narrative
from collectors.news import gosi_search_links
from db import schema

app = Flask(__name__)
app.jinja_env.filters["mdbold"] = lambda text: re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
# 라이브 Flask 앱에서의 링크 형태(정적 사이트 빌드시엔 build_static.py 가 다른 값으로 렌더링)
app.jinja_env.globals["link_prefix"] = "/candidate/"
app.jinja_env.globals["link_suffix"] = ""
app.jinja_env.globals["list_link"] = "/"
app.jinja_env.globals["noindex"] = False


def _query_candidates(min_score, zoning, exclude_mengji, sort, view):
    schema.init_db()
    score_col = "score" if view != "comp" else "comp_score"
    sql = f"SELECT * FROM auction_candidate WHERE {score_col} IS NOT NULL"
    params = []
    if min_score not in (None, ""):
        sql += f" AND {score_col} >= ?"
        params.append(float(min_score))
    if zoning:
        sql += " AND zoning = ?"
        params.append(zoning)
    if exclude_mengji:
        sql += " AND (road_grade IS NULL OR road_grade != '맹지')"
    order = f"{score_col} DESC" if sort != "asc" else f"{score_col} ASC"
    sql += f" ORDER BY {order} LIMIT 300"
    with schema.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        zonings = [r[0] for r in conn.execute(
            "SELECT DISTINCT zoning FROM auction_candidate WHERE zoning IS NOT NULL ORDER BY zoning"
        ).fetchall()]
        total = conn.execute("SELECT COUNT(*) FROM auction_candidate").fetchone()[0]
    return rows, zonings, total


@app.route("/")
def index():
    min_score = request.args.get("min_score", "")
    zoning = request.args.get("zoning", "")
    exclude_mengji = request.args.get("exclude_mengji") == "on"
    sort = request.args.get("sort", "desc")
    view = request.args.get("view", "dev")  # dev=개발용, comp=토지보상용
    rows, zonings, total = _query_candidates(min_score, zoning, exclude_mengji, sort, view)
    return render_template(
        "list.html", rows=rows, zonings=zonings, total=total, shown=len(rows),
        min_score=min_score, zoning=zoning, exclude_mengji=exclude_mengji, sort=sort, view=view,
    )


@app.route("/candidate/<int:cid>")
def detail(cid):
    schema.init_db()
    with schema.get_conn() as conn:
        row = conn.execute("SELECT * FROM auction_candidate WHERE id = ?", (cid,)).fetchone()
    if row is None:
        abort(404)
    r = dict(row)
    narrative = build_narrative(r)
    try:
        raw = json.loads(r.get("raw_json") or "{}")
    except ValueError:
        raw = {}
    gosi_links = gosi_search_links(raw.get("lctnSggnm") or "", raw.get("lctnEmdNm") or "")
    return render_template("detail.html", r=r, narrative=narrative, gosi_links=gosi_links)


if __name__ == "__main__":
    import config
    print(f"로컬: http://localhost:5000")
    print(f"같은 와이파이의 폰에서: {config.BASE_URL}")
    # host="0.0.0.0" 로 열어야 같은 와이파이의 폰에서 카톡 링크로 접속 가능
    app.run(debug=True, port=5000, host="0.0.0.0")
