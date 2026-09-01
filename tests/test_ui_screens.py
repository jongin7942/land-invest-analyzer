"""화면 테스트 — Carbon 전환 뒤에도 화면이 거짓말을 하지 않는가.

이 파일이 지키는 것은 디자인이 아니라 **표시 규칙**이다.

  §5  못 구한 값은 0 이 아니라 '확인 불가' 로 나온다
  §5  하한값을 확정값처럼 보여주지 않는다 ('… 이상')
  §5  실거래와 호가를 한 값으로 섞지 않는다
  §6  종합점수만 크게 보여주지 않는다
  §7  빈 화면에 '결과 없음' 만 띄우지 않는다
  §12 살 수 있는 후보가 없으면 억지로 순위를 만들지 않는다

색이나 여백이 바뀌는 것은 상관없다. 위 규칙이 깨지면 테스트가 깨진다.
"""
import sqlite3

import pytest

from apt_engine.collectors import matcher
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.repo import apt as repo


@pytest.fixture
def ui(tmp_db, monkeypatch):
    """화면용 앱 + 합성 데이터.

    진짜 apt_invest.db 는 절대 열지 않는다 — 수집 배치가 도는 중이면
    테스트가 그것을 죽인다.
    """
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
        _seed(conn)

    import config
    import apt_app
    monkeypatch.setattr(config, "APT_DB_PATH", tmp_db)
    monkeypatch.setattr(apt_app.config, "APT_DB_PATH", tmp_db)
    apt_app.app.testing = True
    return apt_app.app.test_client()


def _seed(conn):
    """단지 3개 + 실거래 + 대표가격 스냅샷. 규칙은 일부러 비워 둔다."""
    for i, (name, price) in enumerate(
            [("화면가1", 900_000_000), ("화면가2", 700_000_000),
             ("화면가3", 500_000_000)], start=1):
        conn.execute(
            "INSERT INTO complex (kapt_code, name, name_norm, lawd_cd, emd_name,"
            " apt_households, approval_year) VALUES (?,?,?,?,?,?,?)",
            (f"UI{i:05d}", name, matcher.normalize(name), "11110", "청운동",
             500 + i, 2000 + i))
        for m in range(10):
            conn.execute(
                "INSERT INTO trade (complex_id, lawd_cd, emd_name, apt_name,"
                " exclusive_area_m2, area_band, deal_amount, deal_ymd, floor,"
                " build_year) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (i, "11110", "청운동", name, 84.0, "84",
                 price + m * 1_000_000, "20241005", 5 + m, 2000 + i))
        conn.execute(
            "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym,"
            " window_months, representative_price, method, sample_n, excluded_n,"
            " confidence, engine_version, data_grade, calc_trace)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, "84", "202410", 3, price, "median", 10, 0, "HIGH", "0.15.0",
             "CONFIRMED", '{"합성": true, "주의": "실제 거래가 아닙니다"}'))
        conn.execute(
            "INSERT INTO jeonse_snapshot (complex_id, area_band, as_of_ym,"
            " window_months, representative_deposit, method, sample_n,"
            " excluded_n, confidence, jeonse_ratio, engine_version, data_grade,"
            " calc_trace) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (i, "84", "202410", 3, int(price * 0.6), "median", 8, 0, "MEDIUM",
             0.6, "0.15.0", "CONFIRMED", '{"합성": true}'))
    conn.commit()


def body(resp):
    assert resp.status_code == 200, resp.status_code
    return resp.data.decode()


def values(html):
    """화면에 **값으로** 찍힌 것만 뽑는다.

    설명 문구에도 '0원' 같은 말이 나온다("0원이 아니라 확인 불가로 남습니다").
    그건 규칙을 설명하는 문장이지 값이 아니다. 값만 보고 판정해야
    문구를 고칠 때마다 테스트가 깨지지 않는다.
    """
    import re
    out = []
    for cls in ("metric__value", "num"):
        out += re.findall(rf'class="[^"]*\b{cls}\b[^"]*"[^>]*>(.*?)<',
                          html, re.S)
    return [v.strip() for v in out]


# ── §7 화면이 열리기는 하는가 ─────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "/", "/?cash=3", "/?mode=expert", "/status", "/search", "/search?q=화면가",
    "/complex/1", "/complex/1?price=9", "/complex/1?price=9&mode=expert",
    "/compare", "/conclusion", "/conclusion?cash=3", "/unmatched",
])
def test_모든_화면이_열린다(ui, url):
    assert ui.get(url).status_code == 200


def test_없는_단지는_404_지만_화면은_깨지지_않는다(ui):
    r = ui.get("/complex/99999")
    assert r.status_code == 404
    assert "찾을 수 없습니다" in r.data.decode()


# ── §5 못 구한 값 ─────────────────────────────────────────────────────

def test_실거래가_없는_단지는_0원이_아니라_확인_불가(ui, tmp_db):
    with get_conn(tmp_db) as conn:
        conn.execute(
            "INSERT INTO complex (kapt_code, name, name_norm, lawd_cd, emd_name)"
            " VALUES ('UIZZZ','빈단지','빈단지','11110','청운동')")
        conn.commit()
        cid = conn.execute(
            "SELECT id FROM complex WHERE name='빈단지'").fetchone()[0]
    html = body(ui.get(f"/complex/{cid}"))
    assert "매칭된 실거래가 없습니다" in html
    # 값 자리에 0 이 찍히면 안 된다. 모르는 값은 비워 두거나 '확인 불가' 다.
    assert not [v for v in values(html) if v.startswith("0")]


def test_규칙이_없으면_실투자금이_하한이라고_밝힌다(ui):
    """규칙 미입력 상태에서 계산하면 '확인 불가 항목' 이 남는다.

    그때 금액을 확정값처럼 보여주면 실제로 못 사는 집이 살 수 있어 보인다.
    """
    html = body(ui.get("/complex/1?price=9"))
    assert "확인 불가" in html
    if "확인 불가 항목" in html:
        assert "0원으로 세지 않았습니다" in html


def test_비교표는_확인_불가_항목이_있으면_이상_을_붙인다(ui):
    ui.set_cookie("watch", "1,2,3")
    html = body(ui.get("/compare"))
    if "확인 불가 항목" in html and "개</span>" in html:
        assert "이상" in html


def test_비교표는_실거래_등급을_붙여_호가와_섞지_않는다(ui):
    ui.set_cookie("watch", "1,2")
    html = body(ui.get("/compare"))
    assert "실거래" in html
    # 호가를 대표가격 자리에 넣지 않는다
    assert "호가" not in html.split("매매 대표가격")[1].split("전세 대표가격")[0]


# ── §7 빈 화면 ────────────────────────────────────────────────────────

def test_검색_결과가_없으면_이유와_다음_행동을_준다(ui):
    html = body(ui.get("/search?q=없는이름zzz"))
    assert "찾은 단지가 없습니다" in html
    assert "미매칭" in html          # 다음에 볼 곳
    assert "데이터 현황" in html      # 왜 없는지 확인할 곳


def test_비교_후보가_없으면_담는_법을_알려준다(ui):
    html = body(ui.get("/compare"))
    assert "비교할 단지가 없습니다" in html
    assert "/search" in html


# ── §4 후보 관리 ──────────────────────────────────────────────────────

def test_후보는_최대_5개까지만_담긴다(ui, tmp_db):
    with get_conn(tmp_db) as conn:
        for i in range(4, 10):
            conn.execute(
                "INSERT INTO complex (kapt_code, name, name_norm, lawd_cd)"
                " VALUES (?,?,?,'11110')", (f"UIX{i}", f"추가{i}", f"추가{i}"))
        conn.commit()
    for cid in range(1, 10):
        ui.post("/watchlist/add", data={"id": str(cid), "next": "/compare"})
    html = body(ui.get("/compare"))
    assert html.count('name="id"') <= 5


def test_후보_목록이_깨져도_화면이_죽지_않는다(ui):
    ui.set_cookie("watch", "1,abc,,-3,99999,2")
    assert ui.get("/compare").status_code == 200
    assert ui.get("/conclusion?cash=3").status_code == 200


def test_다른_사이트로_돌려보내지_않는다(ui):
    r = ui.post("/watchlist/add",
                data={"id": "1", "next": "//evil.example/x"})
    assert r.headers["Location"].startswith("/")
    assert not r.headers["Location"].startswith("//")
    r = ui.post("/watchlist/add",
                data={"id": "1", "next": "https://evil.example/x"})
    assert r.headers["Location"] == "/search"


# ── §12 억지로 결론을 만들지 않는다 ───────────────────────────────────

def test_결론화면은_자기_점수를_만들지_않는다(ui):
    """순위는 순위 화면 한 곳에서만 나온다.

    결론 화면이 따로 점수를 매기면 두 화면의 순서가 어긋나고,
    사용자는 어느 쪽이 맞는지 알 방법이 없다.
    """
    ui.set_cookie("watch", "1,2,3")
    html = body(ui.get("/conclusion?cash=3"))
    assert "아직 답할 수 없습니다" in html or "살 수 있는 후보가 없습니다" in html
    assert "종합점수" not in html


def test_확인_불가_항목이_있으면_살_수_있음이라고_말하지_않는다(ui):
    ui.set_cookie("watch", "3")
    html = body(ui.get("/conclusion?cash=99"))
    # 99억이면 5억짜리를 살 돈은 충분하다. 그런데 규칙이 없어 비용을 모른다.
    assert "판단 불가" in html
    # 배지로 '살 수 있음' 이 붙으면 안 된다 (아래 설명 문구에는 나온다)
    assert 'chip--ok">살 수 있음' not in html


# ── §3 두 가지 보기 모드 ──────────────────────────────────────────────

def test_전문가_모드는_산식과_출처를_더_보여준다(ui):
    easy = body(ui.get("/complex/1?price=9"))
    expert = body(ui.get("/complex/1?price=9&mode=expert"))
    assert len(expert) > len(easy)
    assert "실투자금 =" in expert


def test_모르는_모드는_기본값으로_되돌린다(ui):
    assert ui.get("/?mode=zzz").status_code == 200
    assert ui.get("/?mode=").status_code == 200
