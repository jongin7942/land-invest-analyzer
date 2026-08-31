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


@app.route("/")
def index():
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
