"""아파트 투자분석 엔진 로컬 웹 UI.

CLI 로만 되던 것(`price` · `cash` · `rule status` · `report`)을 브라우저에서 본다.

    python apt_app.py
    http://localhost:5001

토지 프로그램의 `app.py`(5000 포트)와 별개다. 같이 띄워도 안 부딪힌다.

── 설계에서 지킨 것 ────────────────────────────────────────────────────
1. **DB 를 읽기 전용으로 연다.** 수집 배치가 몇 시간씩 도는 동안에도 화면이
   막히지 않고, UI 가 실수로 데이터를 건드릴 일도 없다.
2. **'확인 불가'를 0 이나 빈칸으로 그리지 않는다.** 이 엔진의 존재 이유가
   "모르는 것을 모른다고 말하는 것"이라, 화면에서 그게 흐려지면 안 된다.
   확인 불가는 눈에 띄는 색으로 따로 표시하고, 합계에는 '이상'을 붙인다.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import date
from urllib.parse import urlencode

from flask import Flask, make_response, redirect, render_template, request

import config
from apt_engine import area as area_mod
from apt_engine import regions, units
from apt_engine.repo import apt as repo
from apt_engine.repo import rules as rule_repo

app = Flask(__name__, template_folder="templates", static_folder="static")
PORT = int(os.getenv("PORT") or 5001)

# ── 공개 모드 ─────────────────────────────────────────────────────────
#
# 링크를 밖으로 공유할 때 켠다(APT_PUBLIC=1). 켜면 두 가지가 달라진다.
#
#   ① 잠금 우회(`?unlock=1`)를 막는다.
#      우회 화면은 "가중치가 임시값이라 투자 판단 근거가 아닙니다" 라고
#      스스로 밝히는 화면이다. 링크를 받은 사람이 주소에 파라미터 하나를
#      붙여서 그 화면에 닿을 수 있으면, 우리가 안 된다고 적어 둔 것을
#      주소창으로 우회할 수 있게 두는 셈이다. 내 PC 에서 내가 보는 것과
#      남에게 보내는 것은 다르다.
#
#   ② 접속 코드를 요구할 수 있다(APT_ACCESS_CODE).
#      카톡 링크는 전달되고 캡처된다. 코드는 '보안' 이라기보다
#      **검색엔진과 무작위 접속을 걸러내는 문턱**이다.
PUBLIC = (os.getenv("APT_PUBLIC") or "").strip().lower() in ("1", "true", "yes")
ACCESS_CODE = (os.getenv("APT_ACCESS_CODE") or "").strip()
SITE_NAME = (os.getenv("APT_SITE_NAME") or "수도권 아파트 투자분석").strip()
SITE_URL = (os.getenv("APT_SITE_URL") or "").strip().rstrip("/")

# ── 보기 모드 (§3) ────────────────────────────────────────────────────
#
# 같은 데이터를 두 방식으로 보여준다. 라우트를 나누지 않는 이유:
# URL 을 나누면 링크를 공유했을 때 상대가 다른 모드로 보게 되고,
# 새로고침에서 모드가 풀린다.
MODE_EASY = "easy"
MODE_EXPERT = "expert"
MODES = (MODE_EASY, MODE_EXPERT)
DEFAULT_MODE = MODE_EASY


@app.context_processor
def _inject_mode():
    """모든 템플릿에서 `mode` 를 쓸 수 있게 한다.

    모르는 값이 오면 기본으로 되돌린다 — 쿼리스트링을 손으로 고쳐도
    화면이 깨지지 않아야 한다.
    """
    raw = (request.args.get("mode") or "").strip().lower()
    return {"mode": raw if raw in MODES else DEFAULT_MODE,
            "MODE_EASY": MODE_EASY, "MODE_EXPERT": MODE_EXPERT}


# ── 비교 후보 (§4 아파트 검색·후보 관리) ──────────────────────────────
#
# 최대 5개. 그 이상은 가로 비교표가 읽히지 않는다 — 화면에 다 안 들어가고,
# 사람이 한 번에 비교할 수 있는 항목 수도 그쯤에서 끝난다.
#
# 저장은 쿠키다. DB 커넥션이 읽기 전용(수집 배치와 나란히 읽어야 해서)이라
# 서버에 쓸 수 없고, 담아둔 후보는 개인 설정이라 서버에 둘 이유도 없다.
WATCH_MAX = 5
WATCH_COOKIE = "watch"


def _watch_ids() -> list[int]:
    """쿠키에서 후보 id 목록. 손으로 고쳐도 화면이 깨지지 않게 다 걸러낸다."""
    raw = request.cookies.get(WATCH_COOKIE) or ""
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        cid = int(part)
        if cid not in out:
            out.append(cid)
    return out[:WATCH_MAX]


@app.context_processor
def _inject_asof():
    """상단 바의 '데이터 기준' — 실거래 마지막 신고일.

    화면마다 다른 날짜를 쓰면 사용자가 어느 시점 이야기인지 잃어버린다.
    여기 한 곳에서만 만든다. 실거래가 없으면 아무것도 표시하지 않는다 —
    '오늘' 로 채우면 오늘 데이터인 것처럼 보인다.
    """
    try:
        with ro_conn() as conn:
            row = conn.execute("SELECT MAX(deal_ymd) FROM trade").fetchone()
    except sqlite3.Error:
        return {"data_asof": None}
    ymd = row[0] if row else None
    if not ymd or len(str(ymd)) < 8:
        return {"data_asof": None}
    ymd = str(ymd)
    return {"data_asof": f"{ymd[:4]}.{ymd[4:6]}.{ymd[6:8]}"}


@app.context_processor
def _inject_watch():
    ids = _watch_ids()
    return {"watch_ids": ids, "watch_max": WATCH_MAX}


def _watch_rows(conn, ids: list[int]):
    """담아둔 순서를 지켜서 단지 행을 돌려준다 (IN 절은 순서를 안 지킨다)."""
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT * FROM complex WHERE id IN ({marks})", ids).fetchall()}
    return [rows[i] for i in ids if i in rows]


def _safe_next(raw: str | None) -> str:
    """열린 리다이렉트를 막는다. 우리 사이트 안의 경로만 허용."""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/search"
    return raw


def _set_watch(ids: list[int], target: str):
    resp = make_response(redirect(target, code=303))
    resp.set_cookie(WATCH_COOKIE, ",".join(str(i) for i in ids[:WATCH_MAX]),
                    max_age=60 * 60 * 24 * 30, samesite="Lax", path="/")
    return resp


@app.route("/nav-toggle", methods=["POST"])
def nav_toggle():
    """왼쪽 메뉴 접기/펴기. 쿠키에만 남는 개인 설정이다."""
    mini = request.cookies.get("nav") != "mini"
    resp = make_response(redirect(_safe_next(request.form.get("next")), code=303))
    resp.set_cookie("nav", "mini" if mini else "wide",
                    max_age=60 * 60 * 24 * 180, samesite="Lax", path="/")
    return resp


@app.route("/watchlist/add", methods=["POST"])
def watchlist_add():
    ids = _watch_ids()
    raw = (request.form.get("id") or "").strip()
    if raw.isdigit() and int(raw) not in ids and len(ids) < WATCH_MAX:
        ids.append(int(raw))
    return _set_watch(ids, _safe_next(request.form.get("next")))


@app.route("/watchlist/remove", methods=["POST"])
def watchlist_remove():
    raw = (request.form.get("id") or "").strip()
    ids = [i for i in _watch_ids() if not (raw.isdigit() and i == int(raw))]
    return _set_watch(ids, _safe_next(request.form.get("next")))


@app.route("/watchlist/clear", methods=["POST"])
def watchlist_clear():
    return _set_watch([], _safe_next(request.form.get("next")))


@app.after_request
def _harden(resp):
    """공개했을 때 최소한으로 필요한 헤더.

    이 앱은 남의 글을 렌더하지 않으므로 XSS 표면이 좁지만, 링크가 밖으로
    나가면 프레임에 끼워 넣거나(clickjacking) 검색엔진에 색인되는 것은
    막아야 한다. 색인은 특히 — 이 화면의 숫자는 수집 진행도에 따라
    바뀌는데, 검색결과에 옛날 순위가 남으면 그게 사실처럼 읽힌다.
    """
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if PUBLIC:
        resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return resp


@app.before_request
def _gate():
    """접속 코드. 코드를 안 걸었으면 아무것도 하지 않는다."""
    if not ACCESS_CODE or request.path.startswith("/static/") \
            or request.path == "/healthz":
        return None
    given = request.args.get("code") or request.cookies.get("code") or ""
    # compare_digest 는 **ASCII 문자열만** 받는다. 한글 코드를 넣으면
    # 비교가 아니라 TypeError 로 앱이 죽는다. bytes 로 넘겨야 한다.
    if secrets.compare_digest(given.encode("utf-8"), ACCESS_CODE.encode("utf-8")):
        # 주소로 들어왔으면 쿠키에 옮긴다 — 링크에 코드가 계속 붙어 다니면
        # 그 주소가 그대로 다시 공유되고, 화면 캡처에도 남는다.
        if request.args.get("code") and not request.cookies.get("code"):
            keep = {k: v for k, v in request.args.items(multi=True) if k != "code"}
            target = request.path + (("?" + urlencode(keep, doseq=True)) if keep else "")
            resp = make_response(redirect(target, code=303))
            resp.set_cookie("code", ACCESS_CODE, max_age=60 * 60 * 24 * 30,
                            samesite="Lax", path="/", httponly=True)
            return resp
        return None
    return render_template("apt_gate.html", site_name=SITE_NAME), 401


@app.route("/healthz")
def healthz():
    """배포한 곳이 살아 있는지 확인하는 용도. DB 까지 실제로 열어 본다."""
    try:
        with ro_conn() as conn:
            conn.execute("SELECT 1").fetchone()
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}, 503
    return {"ok": True}


@app.context_processor
def _inject_site():
    return {"SITE_NAME": SITE_NAME, "SITE_URL": SITE_URL, "PUBLIC": PUBLIC}


# ── DB ────────────────────────────────────────────────────────────────

def ro_conn() -> sqlite3.Connection:
    """읽기 전용 커넥션.

    수집 배치는 쓰기 트랜잭션을 오래 잡는다. 일반 커넥션으로 열면 화면이
    'database is locked' 로 죽는다. 읽기 전용 + WAL 이면 배치와 나란히 읽힌다.
    """
    conn = sqlite3.connect(f"file:{config.APT_DB_PATH}?mode=ro", uri=True,
                           timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


SIDO_OF_PREFIX = {"11": "서울", "28": "인천", "41": "경기"}


# ── 공통 표시 헬퍼 ────────────────────────────────────────────────────

@app.template_filter("eok")
def _eok(v):
    return units.fmt_eok(v) if v is not None else None


@app.template_filter("won")
def _won(v):
    return units.fmt_won(v) if v is not None else None


@app.template_filter("pct")
def _pct(v):
    return units.fmt_pct(v) if v is not None else None


@app.template_filter("band")
def _band(v):
    return area_mod.label_of(v) if v else "-"


@app.template_filter("sgg")
def _sgg(code):
    return regions.name_of(code) if code else "-"


# ── 대시보드 ──────────────────────────────────────────────────────────

def _collection_stats(conn) -> dict:
    q = lambda s, *a: conn.execute(s, a).fetchone()
    by_sido = []
    for prefix, n in conn.execute(
            "SELECT substr(lawd_cd,1,2), COUNT(*) FROM complex GROUP BY 1 ORDER BY 1"):
        by_sido.append({"sido": SIDO_OF_PREFIX.get(prefix, prefix), "n": n})

    span = q("SELECT MIN(deal_ymd), MAX(deal_ymd) FROM trade") or (None, None)
    trade_n = q("SELECT COUNT(*) FROM trade")[0]
    jeonse_n = q("SELECT COUNT(*) FROM jeonse_contract")[0]
    unmatched = q("SELECT COUNT(*) FROM trade WHERE complex_id IS NULL")[0]

    return {
        "complexes": q("SELECT COUNT(*) FROM complex")[0],
        "with_basis": q("SELECT COUNT(*) FROM complex WHERE approval_year IS NOT NULL")[0],
        "by_sido": by_sido,
        "trades": trade_n,
        "jeonse": jeonse_n,
        "snapshots": q("SELECT COUNT(*) FROM price_snapshot")[0],
        "unmatched": unmatched,
        "unmatched_pct": (unmatched / trade_n * 100) if trade_n else None,
        "first_ymd": span[0],
        "last_ymd": span[1],
        "months": _month_span(span[0], span[1]),
    }


def _month_span(a: str | None, b: str | None) -> int | None:
    if not (a and b):
        return None
    return (int(b[:4]) - int(a[:4])) * 12 + (int(b[4:6]) - int(a[4:6])) + 1


RULE_LABELS = {"regulation": "규제지역", "permit": "토지거래허가구역",
               "tax": "세법", "loan": "대출규제", "cost": "취득 부대비용"}


@app.route("/status")
def status():
    with ro_conn() as conn:
        stats = _collection_stats(conn)
        cov = rule_repo.coverage(conn)
        rules_rows = [
            {"label": RULE_LABELS.get(k, k), **v} for k, v in cov.items()
        ]
        recent = conn.execute(
            "SELECT c.id, c.name, c.lawd_cd, c.emd_name, c.apt_households, "
            "       c.approval_year "
            "FROM complex c WHERE c.apt_households >= 1000 "
            "ORDER BY c.apt_households DESC LIMIT 12").fetchall()
    return render_template("apt_index.html", stats=stats, rules=rules_rows,
                           big=recent, port=PORT)


# ── 첫 화면: 현금을 넣으면 순위가 나온다 ──────────────────────────────

DEFAULT_HORIZON = 2


@app.route("/")
def home():
    cash_in = (request.args.get("cash") or "").strip()
    house_count = int(request.args.get("house_count") or 1)
    horizon = int(request.args.get("horizon") or DEFAULT_HORIZON)
    limit = int(request.args.get("limit") or 10)
    form = {"cash": cash_in, "house_count": house_count,
            "horizon": horizon, "limit": limit}

    # 공개 모드에서는 잠금 우회를 받지 않는다 — 위 PUBLIC 주석 참고.
    override = request.args.get("unlock") == "1" and not PUBLIC
    with ro_conn() as conn:
        lock = _lock_state(conn)
        if lock and not override:
            return render_template("apt_home.html", form=form, result=None,
                                   lock=lock)
        view = _rank_view(conn, cash_in, house_count, horizon, limit) \
            if cash_in else None
        rail = (_rail(conn, view["rows"][0])
                if view and view.get("rows") else None)
    return render_template("apt_home.html", form=form, result=view, rail=rail,
                           lock=None, unlocked=lock if override else None)


def _lock_state(conn) -> dict | None:
    """순위 화면 잠금.

    백테스트로 학습한 가중치가 없으면 순위는 **배관이 도는지 확인한 결과**일 뿐이다.
    임시(heuristic) 가중치로 만든 1위를 화면에 띄우면, 아무리 경고를 붙여도
    사람은 순위를 먼저 읽는다. 그래서 아예 잠근다 — 학습 가중치가 생기면 저절로 풀린다.
    """
    from apt_engine.backtest import usefulness as useful_mod

    try:
        learned = useful_mod.load_weights(conn, market_source="REAL")
    except Exception:                          # 백테스트 테이블이 아직 없을 수 있다
        learned = None
    if learned is not None:
        return None

    q = lambda s: conn.execute(s).fetchone()
    span = q("SELECT MIN(deal_ymd), MAX(deal_ymd) FROM trade") or (None, None)
    months = _month_span(span[0], span[1])
    sido = {r[0]: r[1] for r in conn.execute(
        "SELECT substr(lawd_cd,1,2), COUNT(*) FROM trade GROUP BY 1")}
    return {
        "months": months,
        "need_months": 240,
        "trades": q("SELECT COUNT(*) FROM trade")[0],
        "by_sido": [{"sido": SIDO_OF_PREFIX.get(k, k), "n": v}
                    for k, v in sorted(sido.items())],
        "missing_sido": [s for k, s in sorted(SIDO_OF_PREFIX.items())
                         if k not in sido],
    }


def _rank_view(conn, cash_in, house_count, horizon, limit):
    """랭킹 한 번. 후보가 0 이면 '왜 0 인지'까지 담아 돌려준다.

    빈 화면에 '후보 없음' 만 띄우면 사용자가 할 수 있는 게 없다. 이 엔진에서
    후보가 0 이 되는 이유는 거의 정해져 있어서(스냅샷 개월 부족 · 표본 부족 ·
    현금 부족 · 규칙 미입력) 그걸 그대로 보여준다.
    """
    from dataclasses import replace

    from apt_engine.blind import cutoff as cutoff_mod
    from apt_engine.invest.budget import Profile
    from apt_engine.listing.provider import parse_price
    from apt_engine.ranking import explain as explain_mod
    from apt_engine.ranking import pipeline as pipeline_mod

    try:
        cash = parse_price(cash_in)
    except Exception:
        return {"error": f"투자금을 읽지 못했습니다: {cash_in!r} (예: 3 또는 300000000)"}

    profile = replace(Profile(name="balanced"), available_cash=cash)
    as_of = cutoff_mod.AsOf(date.today().isoformat())

    try:
        result = pipeline_mod.run(conn, as_of=as_of, profile=profile,
                                  horizon_years=horizon, scan_limit=2000)
    except ValueError as e:
        return {"error": str(e)}

    rows = []
    if result.top10:
        ids = [c.complex_id for c in result.top10]
        meta = {r["id"]: r for r in conn.execute(
            f"SELECT id, name, lawd_cd, emd_name, apt_households, approval_year "
            f"FROM complex WHERE id IN ({','.join('?' * len(ids))})", ids)}
        for i, c in enumerate(result.top10[:limit], 1):
            rows.append(_card(c, i, meta.get(c.complex_id), house_count))

    return {
        "summary": result.summary,
        "regime": result.regime,
        "weights_label": result.weights.label,
        "weights_source": result.weights.source,
        "heuristic": result.weights.source == "HEURISTIC",
        "cash_recommended": result.cash_recommended,
        "cash_reason": result.cash_reason,
        "rows": rows,
        "total": len(result.top10),
        "diagnosis": None if result.top10 else _why_empty(conn, as_of, result),
        "explain": explain_mod,
    }


# ── 표에 들어가는 등급 ────────────────────────────────────────────────
#
# 새 점수를 만들지 않는다. 이미 계산된 **모델 점수를 말로 바꿔서 보여줄 뿐**이다.
# 여기서 따로 산식을 만들면 상세화면의 숫자와 표의 등급이 어긋나고,
# 사용자는 어느 쪽이 맞는지 알 방법이 없다.
#
# 모델 점수는 0.0~1.0 이다. 경계는 3등분보다 살짝 위로 잡았다 —
# 절반쯤 되는 값을 '높음' 이라고 부르면 등급이 아무 말도 안 하게 된다.
RATING_COLUMNS = [
    ("value",    "저평가",    ("저평가", "보통", "주의")),
    ("momentum", "상승 여력", ("높음", "보통", "낮음")),
    ("jeonse",   "하락 방어", ("높음", "보통", "낮음")),
]
RATING_HIGH = 0.66
RATING_MID = 0.40


def _ratings(c) -> list[dict]:
    """모델 점수 → 표에 넣을 등급. 못 구한 모델은 '확인 불가' 로 남는다."""
    out = []
    for model, label, (hi, mid, lo) in RATING_COLUMNS:
        score = c.consensus.scores.get(model)
        if score is None or not score.known or score.value is None:
            out.append({"key": model, "label": label, "level": None,
                        "text": None, "value": None})
            continue
        v = score.value
        level = "high" if v >= RATING_HIGH else ("mid" if v >= RATING_MID else "low")
        out.append({"key": model, "label": label, "level": level,
                    "text": {"high": hi, "mid": mid, "low": lo}[level],
                    "value": v})
    return out


def _card(c, rank, meta, house_count):
    """카드 하나에 들어갈 것만 추린다 — 이유 / 주의 / 매수가 구간 / 업사이드."""
    from apt_engine.ranking import explain as explain_mod

    entry = c.features["entry_position"]
    detail = entry.detail if entry.usable else {}
    bands = detail.get("구간") or {}
    market = explain_mod.what_market_prices(c.features)

    # 대출 한도 규칙이 다 채워지지 않으면 policy_max 가 과대평가된다
    # (규제지역 데이터가 없으면 LTV 규칙이 안 걸리고 '집값' 이라는 산술 상한만 남는다).
    # 그 상태의 실투자금 하나만 크게 띄우면 "400만원이면 산다"로 읽힌다.
    # 확실한 값(대출 없이)과 하한(대출 최대)을 같이 보여준다.
    cap = c.capital
    mortgage = getattr(cap, "mortgage", None) if cap else None

    return {
        "rank": rank,
        "id": c.complex_id,
        "name": meta["name"] if meta else f"#{c.complex_id}",
        "region": (f"{regions.name_of(meta['lawd_cd'])} {meta['emd_name'] or ''}"
                   if meta else ""),
        "households": meta["apt_households"] if meta else None,
        "year": meta["approval_year"] if meta else None,
        "band": c.area_band,
        "price": c.price,
        "equity": c.required_equity,
        "equity_no_loan": getattr(cap, "required_without_loan", None) if cap else None,
        "mortgage_label": mortgage.label if mortgage else None,
        "mortgage_unknown": list(mortgage.unknown) if mortgage else [],
        "capital_unknown": list(cap.unknown) if cap else [],
        "score": c.score,
        "confidence": c.confidence,
        "kill": c.kill.label,
        "why_buy": explain_mod.why_buy(c),
        "why_not": explain_mod.why_not(c),
        "verdict": detail.get("판정"),
        "strong_buy": bands.get("Strong Buy"),
        "fair_buy": bands.get("Fair Buy"),
        "wait": bands.get("Wait"),
        "peak": detail.get("과거 고점"),
        "upside": market["아직 반영 안 된 것"],
        "priced": market["시장이 반영한 것"],
        "coverage": c.features.coverage,
        "house_count": house_count,
        "ratings": _ratings(c),
        # 보유기간 뒤의 세후수익은 이 엔진이 아직 내지 않는다. 0 이나
        # 추정치를 넣지 않고 비워 둔다 — 표에서 '확인 불가' 로 나온다.
        "after_tax_return": None,
    }


# ── 오른쪽 패널: "왜 1위인가요?" ──────────────────────────────────────
#
# 1위 하나를 자세히 푼다. §6 이 요구한 것 — 종합점수만 크게 보여주지 않고
# 점수를 올린 요인·내린 요인·원본 데이터를 같이 둔다.
#
# 새로 계산하는 것은 **없다.** 이미 나온 카드 값과 스냅샷 시계열을
# 옮겨 담을 뿐이다.

def _price_series(conn, complex_id: int, band: str, months: int = 72) -> dict:
    """실거래가·전세가 추이. 차트는 SVG 로 그리므로 값만 넘긴다."""
    sale = conn.execute(
        "SELECT as_of_ym, representative_price FROM price_snapshot "
        "WHERE complex_id=? AND area_band=? ORDER BY as_of_ym DESC LIMIT ?",
        (complex_id, band, months)).fetchall()
    jeonse = conn.execute(
        "SELECT as_of_ym, representative_deposit FROM jeonse_snapshot "
        "WHERE complex_id=? AND area_band=? ORDER BY as_of_ym DESC LIMIT ?",
        (complex_id, band, months)).fetchall()
    return {
        "sale": [(r[0], r[1]) for r in reversed(sale)],
        "jeonse": [(r[0], r[1]) for r in reversed(jeonse)],
    }


def _rail(conn, row) -> dict:
    """1위 카드 하나를 오른쪽 패널용으로 푼다."""
    series = _price_series(conn, row["id"], row["band"])
    # 평당가 — 전용면적 기준이 아니라 공급면적 기준 관행값이라 우리는
    # 내지 않는다. 잘못 쓰면 다른 사이트 숫자와 안 맞는데 왜 다른지
    # 설명할 근거가 없다.
    return {
        "row": row,
        "series": series,
        "has_series": bool(series["sale"]),
    }


def _why_empty(conn, as_of, result) -> dict:
    """후보가 0 인 이유를 데이터로 설명한다."""
    from apt_engine import area as area_mod
    from apt_engine.blind import universe as universe_mod

    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT as_of_ym FROM price_snapshot ORDER BY 1")]
    uni = universe_mod.build(conn, as_of=as_of, area_band=area_mod.DEFAULT_BAND)

    reasons = []
    if not months:
        reasons.append("대표가격 스냅샷이 하나도 없습니다. `cli snapshot` 을 먼저 돌리세요.")
    elif not uni.rows:
        # 달 목록을 그대로 붙이면 120개가 한 문단으로 쏟아져 아무도 안 읽는다.
        # 범위와 개수만 문장에 넣고, 전체 목록은 화면이 접어서 보여준다.
        span = (f"{months[0]}~{months[-1]} {len(months)}개월"
                if len(months) > 1 else months[0])
        reasons.append(
            f"스냅샷이 {span} 뿐인데, 랭킹은 신고 지연을 감안해 "
            f"관측 가능 시점({as_of.observable.ym})의 <b>직전 달까지만</b> 봅니다. "
            f"그래서 가장 최근 달 스냅샷은 후보에 들어가지 않습니다 — "
            f"<code>cli snapshot --months 6</code> 처럼 과거 달도 만들어야 합니다.")
    if uni.excluded:
        reasons.append("표본 기준에서 빠진 단지: "
                       + " · ".join(f"{k} {v}개" for k, v in uni.excluded.items()))
    drops = {}
    for d in result.dropped:
        drops[d.stage] = drops.get(d.stage, 0) + 1
    return {"reasons": reasons, "universe": len(uni.rows),
            "snapshot_months": months, "observable": as_of.observable.ym,
            "dropped": drops,
            "dropped_examples": [(d.stage, d.reason) for d in result.dropped[:5]]}


# ── 검색 ──────────────────────────────────────────────────────────────

@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    rows = []
    with ro_conn() as conn:
        if q:
            rows = repo.find_complexes(conn, q)
        watchlist = _watch_rows(conn, _watch_ids())
    return render_template("apt_search.html", q=q, rows=rows,
                           watchlist=watchlist)


# ── 단지 상세 ─────────────────────────────────────────────────────────

@app.route("/complex/<int:cid>")
def complex_detail(cid: int):
    price_in = (request.args.get("price") or "").strip()
    house_count = int(request.args.get("house_count") or 1)
    assume_jeonse = request.args.get("jeonse") == "on"
    income_in = (request.args.get("income") or "").strip()
    # 폼을 처음 열 때(price 없음)는 주담대 사용을 기본값 켜짐으로 보여준다.
    use_loan = request.args.get("loan") == "on" if price_in else True

    with ro_conn() as conn:
        row = conn.execute("SELECT * FROM complex WHERE id=?", (cid,)).fetchone()
        if row is None:
            return render_template("apt_search.html", q="", rows=[],
                                   error=f"단지 #{cid} 를 찾을 수 없습니다."), 404

        bands = [r[0] for r in conn.execute(
            "SELECT DISTINCT area_band FROM trade WHERE complex_id=? ORDER BY 1",
            (cid,)).fetchall()]
        prices = []
        for b in bands:
            ps = repo.latest_price_snapshot(conn, cid, b)
            js = repo.latest_jeonse_snapshot(conn, cid, b)
            prices.append({"band": b, "price": ps, "jeonse": js})

        capital, cap_error = None, None
        if price_in:
            capital, cap_error = _compute_capital(
                conn, row, price_in, house_count, assume_jeonse, income_in,
                prices, use_loan)

    return render_template("apt_complex.html", c=row, prices=prices,
                           capital=capital, cap_error=cap_error,
                           form={"price": price_in, "house_count": house_count,
                                 "jeonse": assume_jeonse, "income": income_in,
                                 "loan": use_loan})


def _compute_capital(conn, row, price_in, house_count, assume_jeonse, income_in,
                     prices, use_loan=True):
    """실투자금. 규칙이 없어 못 세는 항목은 예외가 아니라 결과 안에 남는다."""
    from apt_engine.cash import self_capital as capital_mod
    from apt_engine.listing.provider import parse_price

    try:
        price = parse_price(price_in)
    except Exception:
        return None, f"매수가를 읽지 못했습니다: {price_in!r} (예: 6.2 또는 620000000)"

    deposit = None
    if assume_jeonse:
        # 국민평형(84) 스냅샷을 우선 쓰고, 없으면 가진 것 중 첫 번째.
        pick = next((p for p in prices if p["band"] == "84" and p["jeonse"]), None) \
            or next((p for p in prices if p["jeonse"]), None)
        if pick:
            deposit = pick["jeonse"]["representative_deposit"]

    try:
        cap = capital_mod.compute(
            conn, price=price, as_of=date.today().isoformat(),
            lawd_cd=row["lawd_cd"], emd_name=row["emd_name"],
            current_home_count=max(house_count - 1, 0),
            annual_income=parse_price(income_in) if income_in else None,
            jeonse_deposit=deposit, assume_jeonse=assume_jeonse,
            use_mortgage=use_loan)
    except Exception as e:                      # 규칙 미입력 등은 여기로 온다
        return None, f"{type(e).__name__}: {e}"
    return cap, None


# ── 종합 비교 (§4) ────────────────────────────────────────────────────
#
# 계산은 전부 기존 함수를 그대로 쓴다. 이 화면이 하는 일은 **줄 세우기**뿐이고,
# 새로운 산식을 만들지 않는다 — 상세화면과 비교화면의 숫자가 다르면
# 둘 중 하나는 틀린 것이고, 사용자는 어느 쪽이 틀렸는지 알 방법이 없다.

def _compare_band(prices, want: str | None = None):
    """비교에 쓸 면적대 하나를 고른다.

    같은 단지 안에서도 면적대가 다르면 다른 상품이다. 아무거나 고르면
    59㎡ 와 84㎡ 를 나란히 놓고 '싸다' 는 결론이 나온다. 고른 면적대는
    화면에 반드시 같이 표시한다.
    """
    if want:
        hit = next((p for p in prices if p["band"] == want), None)
        if hit:
            return hit
    hit = next((p for p in prices if p["band"] == "84" and p["price"]), None)
    if hit:
        return hit
    priced = [p for p in prices if p["price"]]
    if priced:
        return max(priced, key=lambda p: p["price"]["sample_n"])
    return prices[0] if prices else None


def _compare_row(conn, row, house_count, band_want):
    """단지 하나의 비교용 값. 못 구한 값은 0 이 아니라 None 으로 남긴다."""
    cid = row["id"]
    bands = [r[0] for r in conn.execute(
        "SELECT DISTINCT area_band FROM trade WHERE complex_id=? ORDER BY 1",
        (cid,)).fetchall()]
    prices = [{"band": b,
               "price": repo.latest_price_snapshot(conn, cid, b),
               "jeonse": repo.latest_jeonse_snapshot(conn, cid, b)}
              for b in bands]
    pick = _compare_band(prices, band_want)
    ps = pick["price"] if pick else None
    js = pick["jeonse"] if pick else None

    capital, cap_error = None, None
    if ps:
        capital, cap_error = _compute_capital(
            conn, row, str(ps["representative_price"]), house_count,
            False, "", prices, True)

    return {
        "id": cid,
        "name": row["name"],
        "region": f"{regions.name_of(row['lawd_cd'])} {row['emd_name'] or ''}".strip(),
        "households": row["apt_households"],
        "year": row["approval_year"],
        "bands": bands,
        "band": pick["band"] if pick else None,
        "price": ps["representative_price"] if ps else None,
        "price_as_of": ps["as_of_ym"] if ps else None,
        "price_confidence": ps["confidence"] if ps else None,
        "price_n": ps["sample_n"] if ps else None,
        "deposit": js["representative_deposit"] if js else None,
        "deposit_as_of": js["as_of_ym"] if js else None,
        "deposit_n": js["sample_n"] if js else None,
        "jeonse_ratio": js["jeonse_ratio"] if js else None,
        "equity": getattr(capital, "required", None),
        "equity_no_loan": getattr(capital, "required_without_loan", None),
        "unknown": list(capital.unknown) if capital else [],
        "confirmed": getattr(capital, "confirmed", None),
        "cap_error": cap_error,
    }


# 확인 불가 항목이 있으면 그 단지의 필요 현금은 **하한**이다. 하한끼리
# 비교해서 1등을 뽑으면, 못 구한 비용이 큰 단지가 가장 싸 보인다.
LOWER_BOUND_KEYS = {"equity", "equity_no_loan"}


def _best(rows, key, want_max: bool):
    """이 항목 하나만 놓고 봤을 때 가장 좋은 단지의 id.

    종합 판정이 아니다. 항목별 최고가 곧 최선이라고 읽히지 않도록
    화면에서 '이 항목만 놓고 볼 때' 라고 밝힌다.

    하한값(확인 불가 항목이 있는 필요 현금)은 아예 비교하지 않는다 —
    "이 정도 이상" 인 값끼리 대소를 따지면 결론이 뒤집힐 수 있다.
    """
    usable = rows
    if key in LOWER_BOUND_KEYS:
        usable = [r for r in rows if not r.get("unknown")]
    vals = [(r["id"], r[key]) for r in usable if r.get(key) is not None]
    if len(vals) < 2:
        return None      # 비교 대상이 하나뿐이면 1등이라는 말이 성립하지 않는다
    return (max if want_max else min)(vals, key=lambda kv: kv[1])[0]


@app.route("/compare")
def compare():
    house_count = int(request.args.get("house_count") or 1)
    band_want = (request.args.get("band") or "").strip() or None
    ids = _watch_ids()
    with ro_conn() as conn:
        complexes = _watch_rows(conn, ids)
        rows = [_compare_row(conn, r, house_count, band_want) for r in complexes]

    mixed_bands = len({r["band"] for r in rows if r["band"]}) > 1
    best = {
        "price": _best(rows, "price", want_max=False),
        "equity": _best(rows, "equity", want_max=False),
        "equity_no_loan": _best(rows, "equity_no_loan", want_max=False),
        "jeonse_ratio": _best(rows, "jeonse_ratio", want_max=True),
    }
    return render_template("apt_compare.html", rows=rows, best=best,
                           mixed_bands=mixed_bands,
                           form={"house_count": house_count, "band": band_want or ""})


# ── 최종 투자결론 (§4) ────────────────────────────────────────────────
#
# 이 화면은 **새 점수를 만들지 않는다.** 결론을 낼 근거가 없으면 결론을
# 내지 않고, 무엇이 없어서 못 내는지를 대신 보여준다(§12: 모든 후보가
# 나쁘더라도 억지로 추천하지 않는다).
#
# 지금 데이터로 정직하게 말할 수 있는 것은 두 가지뿐이다
#   ① 가진 현금으로 실제로 살 수 있는가        — 규칙만 있으면 사실 판정
#   ② 그중 어느 것이 더 좋은가                 — 가중치를 학습해야 답할 수 있다
# ②를 못 하는 동안 ①을 ②인 것처럼 보여주지 않는다.

@app.route("/conclusion")
def conclusion():
    cash_in = (request.args.get("cash") or "").strip()
    house_count = int(request.args.get("house_count") or 1)
    band_want = (request.args.get("band") or "").strip() or None

    cash = None
    cash_error = None
    if cash_in:
        from apt_engine.listing.provider import parse_price
        try:
            cash = parse_price(cash_in)
        except Exception:
            cash_error = f"투자금을 읽지 못했습니다: {cash_in!r} (예: 3 또는 300000000)"

    with ro_conn() as conn:
        lock = _lock_state(conn)
        complexes = _watch_rows(conn, _watch_ids())
        rows = [_compare_row(conn, r, house_count, band_want) for r in complexes]

    # ① 살 수 있는가 — 못 구한 항목이 있으면 '가능' 이라고 말하지 않는다.
    for r in rows:
        need = r["equity"]
        if cash is None or need is None:
            r["afford"] = None
            r["afford_reason"] = ("투자금을 넣어 주세요" if cash is None
                                  else (r["cap_error"] or "필요 현금을 구하지 못했습니다"))
        elif r["unknown"]:
            # 확인 불가 항목을 0원으로 세면 '살 수 있다' 가 거짓이 된다.
            r["afford"] = None
            r["afford_reason"] = (
                f"확인 불가 항목 {len(r['unknown'])}개가 있어 실제 필요 현금은 "
                f"이보다 큽니다 — 가능하다고 말할 수 없습니다")
        else:
            r["afford"] = need <= cash
            r["afford_reason"] = ("여유 " + _won(cash - need) if need <= cash
                                  else _won(need - cash) + " 부족")

    affordable = [r for r in rows if r["afford"] is True]
    return render_template("apt_conclusion.html", rows=rows, lock=lock,
                           cash=cash, cash_error=cash_error,
                           affordable=affordable,
                           form={"cash": cash_in, "house_count": house_count,
                                 "band": band_want or ""})


# ── 미매칭 리포트 ─────────────────────────────────────────────────────

@app.route("/unmatched")
def unmatched():
    limit = int(request.args.get("limit") or 50)
    with ro_conn() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt, lawd_cd, emd_name, build_year, apt_name "
            "FROM trade WHERE complex_id IS NULL "
            "GROUP BY lawd_cd, emd_name, build_year, apt_name "
            "ORDER BY cnt DESC LIMIT ?", (limit,)).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM trade WHERE complex_id IS NULL").fetchone()[0]
        alln = conn.execute("SELECT COUNT(*) FROM trade").fetchone()[0]
    return render_template("apt_unmatched.html", rows=rows, total=total,
                           alln=alln, limit=limit)


if __name__ == "__main__":
    print(f"아파트 투자분석 UI  →  http://localhost:{PORT}")
    print(f"DB (읽기 전용): {config.APT_DB_PATH}")
    app.run(host="127.0.0.1", port=PORT, debug=False)
