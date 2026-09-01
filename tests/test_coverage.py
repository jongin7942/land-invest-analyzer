"""수집 완성도 검사 — '수집이 끝났다' 와 '데이터가 다 있다' 를 구분한다.

이 파일이 지키는 명제 하나:

    수집 프로세스가 정상 종료해도, 240개월이 확보되지 않았으면 완료가
    아니다.

기존 `report gaps` 는 DB 에 들어온 달들만 보기 때문에 120개월만 받고
멈춰도 "공백 없음" 이라고 답한다. 그 착각을 여기서 못박는다.
"""
import datetime

import pytest

from apt_engine import ingest, regions
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.quality import coverage as cov
from apt_engine.repo import apt as repo

END = datetime.date(2026, 9, 1)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "apt.db")
    mig.migrate(path)
    with get_conn(path) as conn:
        repo.sync_regions(conn)
        conn.commit()
    return path


def fill(conn, kind, yms, *, codes=None, status="OK", rows=10):
    key = cov.KINDS[kind][0]
    for ym in yms:
        for code in (codes or regions.codes_for_ym(ym, None)):
            repo.log_collection(conn, key, target=code, period=ym,
                                status=status, row_count=rows)


def audit(path, **kw):
    with get_conn(path) as conn:
        return cov.audit(conn, months=kw.pop("months", 240), end=END, **kw)


# ── 격자 ──────────────────────────────────────────────────────────────

def test_필요_격자는_그_달에_유효했던_코드로_만들어진다():
    """수집도 codes_for_ym 을 쓴다. 여기서 기준이 갈리면 영영 안 채워지는
    칸이 생긴다."""
    grid = cov.required_grid(240, end=END)
    assert set(grid) == {"매매", "전월세"}
    codes = {c for c, _ in grid["매매"]}
    # 폐지 코드는 격자에 없다 — 실거래가 API 가 과거도 현재 코드로 준다
    assert not (codes & set(regions.RETIRED))
    assert {"28125", "28155", "28275", "28290"} <= codes
    yms = {y for _, y in grid["매매"]}
    assert len(yms) == 240


def test_격자는_거래유형마다_따로_센다():
    grid = cov.required_grid(12, end=END)
    assert len(grid["매매"]) == len(grid["전월세"]) > 0


# ── 1·2. 240개월 완전성 ───────────────────────────────────────────────

def test_빈_DB는_통과하지_않는다(db):
    rep = audit(db)
    assert not rep.passed
    assert rep.kinds["매매"].done == 0
    assert rep.kinds["매매"].rate == 0.0


def test_절반만_받고_멈추면_미통과다(db):
    """가장 중요한 회귀 테스트.

    수집 명령은 정상 종료했고, 받은 120개월 안에는 공백이 하나도 없다.
    그래도 완료가 아니다.
    """
    yms = ingest.recent_yms(240, end=END)
    with get_conn(db) as conn:
        fill(conn, "매매", yms[-120:])
        fill(conn, "전월세", yms[-120:])
        conn.commit()
    rep = audit(db)
    assert not rep.passed
    assert rep.kinds["매매"].rate == pytest.approx(0.5)
    assert len(rep.kinds["매매"].months_missing) == 120


def test_전부_받으면_통과한다(db):
    yms = ingest.recent_yms(240, end=END)
    with get_conn(db) as conn:
        fill(conn, "매매", yms)
        fill(conn, "전월세", yms)
        conn.commit()
    rep = audit(db)
    assert rep.passed, cov.report(rep)
    assert rep.kinds["매매"].rate == 1.0


def test_매매만_받으면_통과하지_않는다(db):
    """거래유형 축을 빼먹으면 전세가율·실투자금이 통째로 빈다."""
    yms = ingest.recent_yms(240, end=END)
    with get_conn(db) as conn:
        fill(conn, "매매", yms)
        conn.commit()
    rep = audit(db)
    assert not rep.passed
    assert rep.kinds["매매"].passed
    assert not rep.kinds["전월세"].passed


def test_거래없음은_채워진_칸이다(db):
    """EMPTY 는 '받아봤더니 없었다' 는 사실이다. 공백이 아니다."""
    yms = ingest.recent_yms(240, end=END)
    with get_conn(db) as conn:
        fill(conn, "매매", yms, status="EMPTY", rows=0)
        fill(conn, "전월세", yms, status="EMPTY", rows=0)
        conn.commit()
    rep = audit(db)
    assert rep.passed
    assert rep.kinds["매매"].empty == rep.kinds["매매"].required
    assert rep.kinds["매매"].filled == 0


def test_미수집과_거래없음을_섞지_않는다(db):
    """섞으면 안 받은 달이 조용히 '거래 없는 달' 로 둔갑한다."""
    yms = ingest.recent_yms(240, end=END)
    with get_conn(db) as conn:
        fill(conn, "매매", yms[:-1], status="EMPTY", rows=0)
        conn.commit()
    c = audit(db).kinds["매매"]
    assert c.empty > 0
    assert len(c.never) > 0
    assert all(h.status == cov.NEVER for h in c.never)


def test_실패는_미수집과_따로_센다(db):
    """재시도하면 되는 칸과 아예 안 물어본 칸은 대응이 다르다."""
    yms = ingest.recent_yms(240, end=END)
    with get_conn(db) as conn:
        fill(conn, "매매", yms, status="FAILED")
        conn.commit()
    c = audit(db).kinds["매매"]
    assert len(c.failed) == c.required
    assert not c.never
    assert not c.passed


def test_한번_받은_칸은_나중_실패로_되돌아가지_않는다(db):
    """어제 OK 로 받은 달을 오늘 재시도하다 실패해도 데이터는 남아 있다."""
    yms = ingest.recent_yms(240, end=END)
    with get_conn(db) as conn:
        fill(conn, "매매", yms)
        fill(conn, "전월세", yms)
        fill(conn, "매매", yms[:3], status="FAILED")   # 나중 재시도 실패
        conn.commit()
    rep = audit(db)
    assert rep.passed


def test_채움률은_필요격자가_0이면_비율이_없다(db):
    """0/0 을 100% 라고 말하면 아무것도 안 받고 통과한다."""
    c = cov.KindCoverage(kind="매매")
    assert c.rate is None
    assert not c.passed


# ── 4. 비정상 0건 구간 ────────────────────────────────────────────────

def _seed_trades(conn, code, ym, n):
    for i in range(n):
        conn.execute(
            "INSERT INTO trade (lawd_cd, emd_name, jibun, apt_name,"
            " exclusive_area_m2, area_band, floor, build_year, deal_ymd,"
            " deal_amount, source_id) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
            (code, "역삼동", f"{i}", "가나아파트", 84.0, "84", 5, 2000,
             f"{ym}15", 100_000_000 + i))


def test_거래가_꾸준한_지역의_0건은_의심한다(db):
    with get_conn(db) as conn:
        for ym in ("202601", "202602", "202604"):
            _seed_trades(conn, "11680", ym, 30)
        fill(conn, "매매", ["202603"], codes=["11680"], status="EMPTY", rows=0)
        conn.commit()
        found = cov.suspicious_zeros(conn)
    assert [(s.lawd_cd, s.ym) for s in found] == [("11680", "202603")]


def test_거래가_드문_지역의_0건은_의심하지_않는다(db):
    """옹진군에 한 달 아파트 거래가 없는 것은 흔하다. 여기를 오탐으로
    채우면 진짜 수집 실패가 묻힌다."""
    with get_conn(db) as conn:
        for ym in ("202601", "202602", "202604"):
            _seed_trades(conn, "28720", ym, 1)
        fill(conn, "매매", ["202603"], codes=["28720"], status="EMPTY", rows=0)
        conn.commit()
        assert cov.suspicious_zeros(conn) == []


def test_거래가_전혀_없는_지역은_0건_판단의_근거가_없다(db):
    with get_conn(db) as conn:
        fill(conn, "매매", ["202603"], codes=["11680"], status="EMPTY", rows=0)
        conn.commit()
        assert cov.suspicious_zeros(conn) == []


# ── 6. 행정구역 개편 전후 연결 ────────────────────────────────────────

def test_승계표가_정상이면_지적이_없다(db):
    with get_conn(db) as conn:
        issues, orphans = cov.lineage_check(conn)
    assert issues == []
    assert orphans == {}


def test_승계표가_비면_잡아낸다(db):
    with get_conn(db) as conn:
        conn.execute("DELETE FROM region_lineage")
        conn.commit()
        issues, _ = cov.lineage_check(conn)
    assert any("승계 관계 누락" in m for m in issues)


def test_폐지코드가_사라지면_잡아낸다(db):
    """폐지 코드를 지우면 그 코드로 저장된 과거 데이터가 고아가 된다."""
    with get_conn(db) as conn:
        conn.execute("DELETE FROM region WHERE lawd_cd='28110'")
        conn.commit()
        issues, _ = cov.lineage_check(conn)
    assert any("28110" in m for m in issues)


def test_region에_없는_코드로_들어온_데이터를_잡아낸다(db):
    """이런 행은 조용히 계산에서 빠진다 — 빠졌다는 것조차 안 보인다."""
    with get_conn(db) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        _seed_trades(conn, "99999", "202601", 3)
        conn.commit()
        _, orphans = cov.lineage_check(conn)
    assert orphans == {"99999": 3}


def test_고아_코드가_있으면_통과하지_않는다(db):
    yms = ingest.recent_yms(240, end=END)
    with get_conn(db) as conn:
        fill(conn, "매매", yms)
        fill(conn, "전월세", yms)
        conn.execute("PRAGMA foreign_keys=OFF")
        _seed_trades(conn, "99999", "202601", 1)
        conn.commit()
    assert not audit(db).passed


# ── 7. 리포트 ─────────────────────────────────────────────────────────

def test_리포트는_완료_기준을_먼저_말한다(db):
    text = cov.report(audit(db))
    assert "수집 명령이 정상 종료한 것은 완료가 아닙니다" in text
    assert "미통과" in text


def test_통과하면_다음_단계를_안내한다(db):
    yms = ingest.recent_yms(240, end=END)
    with get_conn(db) as conn:
        fill(conn, "매매", yms)
        fill(conn, "전월세", yms)
        conn.commit()
    text = cov.report(audit(db))
    assert "통과" in text and "미통과" not in text
    assert "투자점수" in text


def test_시도별로_좁혀_볼_수_있다(db):
    yms = ingest.recent_yms(12, end=END)
    with get_conn(db) as conn:
        for ym in yms:
            fill(conn, "매매", [ym], codes=regions.codes_for_ym(ym, "서울"))
            fill(conn, "전월세", [ym], codes=regions.codes_for_ym(ym, "서울"))
        conn.commit()
        seoul = cov.audit(conn, months=12, sido="서울", end=END)
        allsi = cov.audit(conn, months=12, end=END)
    assert seoul.passed
    assert not allsi.passed
