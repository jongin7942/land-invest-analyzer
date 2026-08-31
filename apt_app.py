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

import sqlite3
from datetime import date

from flask import Flask, render_template, request

import config
from apt_engine import area as area_mod
from apt_engine import regions, units
from apt_engine.repo import apt as repo
from apt_engine.repo import rules as rule_repo

app = Flask(__name__, template_folder="templates")
PORT = 5001


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

    override = request.args.get("unlock") == "1"
    with ro_conn() as conn:
        lock = _lock_state(conn)
        if lock and not override:
            return render_template("apt_home.html", form=form, result=None,
                                   lock=lock)
        view = _rank_view(conn, cash_in, house_count, horizon, limit) \
            if cash_in else None
    return render_template("apt_home.html", form=form, result=view,
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
        reasons.append(
            f"스냅샷이 {', '.join(months)} 뿐인데, 랭킹은 신고 지연을 감안해 "
            f"관측 가능 시점({as_of.observable.ym})의 **직전 달까지만** 봅니다. "
            f"그래서 가장 최근 달 스냅샷은 후보에 들어가지 않습니다 — "
            f"`cli snapshot --months 6` 처럼 과거 달도 만들어야 합니다.")
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
    if q:
        with ro_conn() as conn:
            rows = repo.find_complexes(conn, q)
    return render_template("apt_search.html", q=q, rows=rows)


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
