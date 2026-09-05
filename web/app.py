"""아파트 투자 후보 — 링크로 여는 웹 버전.

약국 앱(눈뜬개국)과 같은 뼈대다: Flask 를 127.0.0.1 에 띄우고, 바탕화면
바로가기(share_link.ps1)가 cloudflared 임시 터널을 열어 공개 주소를 받은 뒤
1회용 초대 링크를 만든다. 종인님(로컬 직접 접속)만 관리자다.

화면은 두 개다.
    /                 TOP100 (reports/top100_latest.json → tools/report_top100.build)
    /complex/<id>     단지 하나 평가 (검색 /search 에서 들어온다)

순위 재계산은 10분쯤 걸리므로 요청 안에서 돌리지 않는다. 관리자가 /api/recompute
를 누르면 배경 스레드가 tools/dump_top100.py → report_top100.py 를 돌리고,
화면은 /api/status 로 진행을 본다. (약국 앱에서 배운 것 — 수집·계산을 요청 안에서
돌리면 첫 로드가 90초를 넘긴다.)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, jsonify, make_response, redirect, render_template,
                   request)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import share  # noqa: E402  (web/share.py)
from report_top100 import build as build_report  # noqa: E402

from apt_engine.blind import cutoff as cutoff_mod  # noqa: E402
from apt_engine.db.connection import get_conn  # noqa: E402
from apt_engine.features import access, assemble  # noqa: E402
from apt_engine.scoring import consensus as cons_mod  # noqa: E402
from apt_engine.scoring import models as models_mod  # noqa: E402
from apt_engine.scoring import normalize  # noqa: E402
from apt_engine.scoring import weights as weights_mod  # noqa: E402

PORT = 5088
PY = ROOT / ".venv" / "Scripts" / "python.exe"
REPORT_JSON = ROOT / "reports" / "top100_latest.json"

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
app.config["JSON_AS_ASCII"] = False

_job = {"running": False, "started": None, "finished": None, "ok": None, "log": ""}
_lock = threading.Lock()


# ── 공유 접근 제어 (약국 앱과 동일) ──────────────────────────────────
@app.before_request
def _guard():
    if share.is_owner(request.remote_addr, request.headers):
        return None
    path = request.path
    if path.startswith("/join/") or path.startswith("/static/"):
        return None
    if path.startswith("/admin") or path.startswith("/api/admin/") or path == "/api/recompute":
        abort(403)
    con = share.connect()
    try:
        sess = share.verify(con, request.cookies.get(share.COOKIE),
                            request.headers.get("Cf-Connecting-Ip") or request.remote_addr)
    finally:
        con.close()
    if sess:
        return None
    if path.startswith("/api/"):
        return jsonify({"error": "접속 권한이 없습니다. 초대 링크를 다시 받으세요."}), 403
    return render_template("share_blocked.html", title="접속 권한이 없습니다",
                           message="초대 링크가 끊겼거나, 이 기기에서는 열린 적이 없는 링크입니다."), 403


@app.route("/join/<token>", methods=["GET", "POST"])
def join(token):
    """GET 은 '입장하기' 버튼만 보여주고 링크를 소모하지 않는다 — 카카오톡 미리보기
    봇이 GET 으로 먼저 열어 1회용 링크를 써 버린 사고(약국 앱, 2026-09-04) 재발 방지."""
    ip = request.headers.get("Cf-Connecting-Ip") or request.remote_addr
    ua = request.headers.get("User-Agent", "")
    con = share.connect()
    try:
        if share.verify(con, request.cookies.get(share.COOKIE), ip):
            return redirect("/")
        if request.method == "GET":
            row = share.peek(con, token)
            if not row:
                return render_template("share_blocked.html", title="이미 사용됐거나 끊긴 링크입니다",
                                       message="초대 링크는 처음 입장한 기기 한 곳에서만 열립니다. 새 링크를 요청하세요."), 403
            return render_template("share_join.html", label=row["label"])
        if share.is_bot(ua):
            abort(403)
        sid = share.redeem(con, token, ip, ua)
    finally:
        con.close()
    if not sid:
        return render_template("share_blocked.html", title="이미 사용됐거나 끊긴 링크입니다",
                               message="다른 사람이 쓴 링크나 끊긴 링크는 열리지 않습니다."), 403
    resp = make_response(redirect("/app"))
    secure = (request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
              or "Cf-Ray" in request.headers)
    resp.set_cookie(share.COOKIE, share.cookie_value(sid), max_age=share.COOKIE_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=secure)
    return resp


@app.route("/admin/share")
def share_admin_page():
    con = share.connect()
    try:
        return render_template("share_admin.html", base=share.base_url(con))
    finally:
        con.close()


@app.route("/api/admin/share/list")
def api_share_list():
    con = share.connect()
    try:
        return jsonify({"base": share.base_url(con), "sessions": share.list_sessions(con)})
    finally:
        con.close()


@app.route("/api/admin/share/create", methods=["POST"])
def api_share_create():
    body = request.get_json(silent=True) or {}
    con = share.connect()
    try:
        s = share.create(con, str(body.get("label") or request.args.get("label") or ""))
        base = share.base_url(con)
        return jsonify({**s, "base": base,
                        "link": f"{base or request.host_url.rstrip('/')}/join/{s['token']}"})
    finally:
        con.close()


@app.route("/api/admin/share/revoke", methods=["POST"])
def api_share_revoke():
    body = request.get_json(silent=True) or {}
    con = share.connect()
    try:
        n = share.revoke_all(con) if body.get("all") else int(share.revoke(con, str(body.get("id") or "")))
        return jsonify({"revoked": n})
    finally:
        con.close()


@app.route("/api/admin/share/base", methods=["POST"])
def api_share_base():
    body = request.get_json(silent=True) or {}
    con = share.connect()
    try:
        share.set_base_url(con, str(body.get("url") or request.args.get("url") or ""))
        return jsonify({"base": share.base_url(con)})
    finally:
        con.close()


# ── 화면 ────────────────────────────────────────────────────────────
def _nav(owner: bool) -> str:
    links = ['<a href="/app"><b>앱</b></a>', '<a href="/">TOP100</a>', '<a href="/search">단지 검색</a>']
    if owner:
        links += ['<a href="/admin/share">공유 링크</a>',
                  '<a href="#" onclick="recompute();return false;">다시 계산</a>']
    status = ('<span id="jobstatus"></span>'
              # 현금·기간·개수를 물어본다. 빈 값이면 예전과 같은 3억·5년·100개.
              '<script>async function recompute(){'
              'const cash=prompt("투자금 (억 단위, 예: 1 또는 3)","3");if(cash===null)return;'
              'const horizon=prompt("투자기간 (년)","5");if(horizon===null)return;'
              'const top=prompt("몇 개까지 볼까요? (카톡으로 보낼 땐 10)","100");if(top===null)return;'
              'if(!confirm(cash+"억 · "+horizon+"년 · TOP"+top+" 으로 다시 계산합니다 (10분쯤 걸림). 계속?"))return;'
              'const r=await fetch("/api/recompute",{method:"POST",headers:{"Content-Type":"application/json"},'
              'body:JSON.stringify({cash:cash,horizon:horizon,top:top})});'
              'const d=await r.json();if(d.error){alert(d.error);return;}poll();}'
              'async function poll(){const r=await fetch("/api/status");const d=await r.json();'
              'const el=document.getElementById("jobstatus");if(!el)return;'
              'if(d.running){el.textContent="계산 중… "+(d.elapsed||"");setTimeout(poll,5000);}'
              'else if(d.finished&&d.ok===false){el.textContent="계산 실패 — 로그 확인";}'
              'else if(d.finished&&d.just){el.textContent="완료 — 새로고침하세요";}}'
              'poll();</script>') if owner else ""
    return ('<nav style="display:flex;gap:14px;align-items:center;padding:10px 16px;'
            'font:14px/1.4 -apple-system,\'IBM Plex Sans KR\',\'Malgun Gothic\',sans-serif;'
            'border-bottom:1px solid #d6dee7;background:#fff">'
            '<b>아파트 투자 후보</b>' + "".join(links) + status + '</nav>')


@app.route("/app")
def app_page():
    """tools/build_app.py 가 만든 단일 파일 앱(reports/apt_app.html). 한글이 깨지지 않게 charset 을 명시."""
    p = REPORT_JSON.parent / "apt_app.html"
    if not p.exists():
        return _nav(share.is_owner(request.remote_addr, request.headers)) + \
            "<p style='padding:16px'>앱 파일이 없습니다. tools/build_app.py 를 먼저 실행하세요.</p>"
    resp = make_response(p.read_bytes())
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    owner = share.is_owner(request.remote_addr, request.headers)
    if not REPORT_JSON.exists():
        return _nav(owner) + "<p style='padding:16px'>아직 계산된 순위가 없습니다. 관리자가 '다시 계산'을 누르면 만들어집니다.</p>"
    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    return _nav(owner) + build_report(data)


@app.route("/search")
def search_page():
    owner = share.is_owner(request.remote_addr, request.headers)
    return _nav(owner) + render_template("search.html")


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.id, c.name, c.lawd_cd, c.approval_year, c.apt_households, r.name AS region "
            "  FROM complex c LEFT JOIN region r ON r.lawd_cd = c.lawd_cd "
            " WHERE c.name LIKE ? ORDER BY c.apt_households DESC LIMIT 30",
            (f"%{q}%",)).fetchall()
        return jsonify([dict(r) for r in rows])


def _evaluate(conn, cid: int, band: str, horizon: int) -> dict:
    """단지 하나를 같은 시도·같은 면적대 후보군 안에서 채점한다."""
    as_of = cutoff_mod.AsOf(datetime.now().date().isoformat())
    cx = conn.execute("SELECT c.*, r.name AS region FROM complex c LEFT JOIN region r "
                      " ON r.lawd_cd=c.lawd_cd WHERE c.id=?", (cid,)).fetchone()
    if not cx:
        abort(404)
    sido = cx["lawd_cd"][:2]
    pool = [r["complex_id"] for r in conn.execute(
        "SELECT DISTINCT ps.complex_id FROM price_snapshot ps JOIN complex c ON c.id=ps.complex_id "
        " WHERE c.lawd_cd LIKE ? AND ps.area_band=? AND ps.as_of_ym >= ?",
        (sido + "%", band, as_of.observable.ym[:4] + "01"))]
    if cid not in pool:
        pool.append(cid)
    feats = {}
    for pid in pool:
        try:
            feats[pid] = assemble.build(conn, pid, band, as_of=as_of, horizon_years=horizon)
        except Exception:
            pass
    if cid not in feats:
        abort(404)
    keys = {k for m in models_mod.SPEC for k, _ in models_mod.spec_for(m, horizon)}
    ranks = {k: normalize.percentile_rank({p: fs[k].value for p, fs in feats.items()
                                           if k in fs and fs[k].usable}) for k in keys}
    w = weights_mod.for_regime("침체")
    scored = []
    for pid, fs in feats.items():
        cc = cons_mod.combine(pid, models_mod.score_all(pid, fs, ranks, horizon_years=horizon), w)
        scored.append((cc.score, pid, cc))
    scored.sort(key=lambda x: -x[0])
    pos = next(i for i, (_, pid, _) in enumerate(scored, 1) if pid == cid)
    mine = next(cc for _, pid, cc in scored if pid == cid)
    price = conn.execute("SELECT representative_price p, as_of_ym, sample_n FROM price_snapshot "
                         " WHERE complex_id=? AND area_band=? AND as_of_ym<=? ORDER BY as_of_ym DESC LIMIT 1",
                         (cid, band, as_of.observable.ym)).fetchone()
    jeonse = conn.execute("SELECT representative_deposit d FROM jeonse_snapshot WHERE complex_id=? "
                          " AND area_band=? ORDER BY as_of_ym DESC LIMIT 1", (cid, band)).fetchone()
    acc = access.drift(conn, cid, horizon_years=horizon)
    hist = [dict(ym=r["as_of_ym"], p=r["representative_price"]) for r in conn.execute(
        "SELECT as_of_ym, representative_price FROM price_snapshot WHERE complex_id=? AND area_band=? "
        " AND substr(as_of_ym,5,2)='06' ORDER BY as_of_ym", (cid, band))]
    fs = feats[cid]
    return {
        "complex": dict(cx), "band": band, "horizon": horizon,
        "price": price["p"] if price else None, "price_ym": price["as_of_ym"] if price else None,
        "jeonse": jeonse["d"] if jeonse else None,
        "score": round(mine.score), "confidence": round(mine.confidence),
        "agreement": round(mine.agreement * 100),
        "rank": pos, "pool": len(scored), "sido": sido,
        "models": [{"model": m, "value": (round(s.value * 100) if s and s.known else None)}
                   for m in models_mod.SPEC for s in [mine.scores.get(m)]],
        "features": [{"key": k, "value": f.value, "confidence": round(f.confidence * 100),
                      "label": f.label} for k, f in sorted(fs.items.items()) if f.known],
        "access": acc.detail if acc.known else None,
        "history": hist,
    }


@app.route("/complex/<int:cid>")
def complex_page(cid):
    owner = share.is_owner(request.remote_addr, request.headers)
    band = request.args.get("band") or "84"
    horizon = int(request.args.get("h") or 5)
    with get_conn() as conn:
        bands = [r["area_band"] for r in conn.execute(
            "SELECT area_band, COUNT(*) n FROM price_snapshot WHERE complex_id=? GROUP BY area_band ORDER BY n DESC",
            (cid,))]
        if bands and band not in bands:
            band = bands[0]
        data = _evaluate(conn, cid, band, horizon)
    data["bands"] = bands
    return _nav(owner) + render_template("complex.html", d=data)


# ── 재계산 (관리자, 배경) ────────────────────────────────────────────
def _run_job(cash: str = "3", horizon: int = 5, top: int = 100):
    log = []
    ok = True
    for args in (["tools/dump_top100.py", "--cash", str(cash),
                  "--horizon", str(horizon), "--top", str(top)],
                 ["tools/report_top100.py"]):
        r = subprocess.run([str(PY), *args], cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        log.append(r.stdout[-2000:] + r.stderr[-2000:])
        if r.returncode != 0:
            ok = False
            break
    with _lock:
        _job.update(running=False, finished=datetime.now(), ok=ok, log="\n".join(log), just=True)


@app.route("/api/recompute", methods=["POST"])
def api_recompute():
    # 현금(억)·투자기간(년)·개수를 받는다. 안 주면 예전과 같은 3억·5년·100개.
    body = request.get_json(silent=True) or {}
    try:
        cash = float(body.get("cash", 3))
        horizon = int(body.get("horizon", 5))
        top = int(body.get("top", 100))
    except (TypeError, ValueError):
        return jsonify({"error": "cash·horizon·top 은 숫자여야 합니다"}), 400
    if not (0 < cash <= 100) or not (1 <= horizon <= 30) or not (1 <= top <= 100):
        return jsonify({"error": "범위 밖: cash 0~100억 · horizon 1~30년 · top 1~100"}), 400
    with _lock:
        if _job["running"]:
            return jsonify({"running": True})
        _job.update(running=True, started=datetime.now(), finished=None, ok=None, just=False)
    threading.Thread(target=_run_job, args=(cash, horizon, top), daemon=True).start()
    return jsonify({"running": True, "cash": cash, "horizon": horizon, "top": top})


@app.route("/api/status")
def api_status():
    with _lock:
        j = dict(_job)
    elapsed = ""
    if j["running"] and j["started"]:
        elapsed = f"{int((datetime.now() - j['started']).total_seconds() // 60)}분"
    just = j.pop("just", False)
    if just:
        with _lock:
            _job["just"] = False
    return jsonify({"running": j["running"], "finished": bool(j["finished"]), "ok": j["ok"],
                    "elapsed": elapsed, "just": just,
                    "report": REPORT_JSON.exists()})


if __name__ == "__main__":
    os.chdir(ROOT)
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
