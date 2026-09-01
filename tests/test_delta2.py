"""2차 DELTA 검증 — 돈의 흐름 분리 · 토허 Gate · 가격함수 · 편향 차단.

이 파일이 지키는 것은 지시서가 **"절대 하지 마라"** 라고 적은 것들이다.
문서에 적는 것과 코드가 거부하는 것은 다르다.
"""
import sqlite3

import pytest

from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.features import money
from apt_engine.features.base import Feature, FeatureSet, Status
from apt_engine.ranking import executable as ex
from apt_engine.ranking import narrate
from apt_engine.redev import quality
from apt_engine.regulation import gate, zone


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    return tmp_db


def F(key, value, conf=0.8):
    return Feature(key=key, value=value, confidence=conf, status=Status.OK)


# ── §8 두 종류의 돈을 합치지 않는다 ───────────────────────────────────

def test_소득_유입과_사다리_이동은_별개_feature다(db):
    """합쳐서 '자금 유입 70점' 으로 만들면 왜 오를 것인지 설명할 수 없다."""
    with get_conn(db) as conn:
        feats = money.all_features(conn, lawd_cd="11110", as_of_ym="202601")
    keys = {f.key for f in feats}
    assert "income_flow" in keys and "migration_flow" in keys
    assert "money_flow" not in keys      # 하나로 뭉친 것이 있으면 안 된다


def test_소득_자료가_없으면_0이_아니라_확인_불가다(db):
    """0 으로 두면 '소득이 안 늘었다' 는 관측이 된다. 실제로는 안 본 것이다."""
    with get_conn(db) as conn:
        inc = money.income_flow(conn, lawd_cd="11110", as_of_ym="202601")
    assert inc.value is None
    assert money.income_feature(inc).status is Status.DATA_MISSING


def test_확인되지_않은_소득_입력은_계산에서_뺀다(db):
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO region_income (lawd_cd, as_of_ym, metric, value, unit,"
            " yoy_change_pct, source_name, observed_on)"
            " VALUES ('11110','202512','고용자수',100,'명',0.08,'통계청','2025-12-01')")
        conn.commit()
        inc = money.income_flow(conn, lawd_cd="11110", as_of_ym="202601")
        assert inc.value is None and "고용자수" in inc.skipped

        conn.execute("UPDATE region_income SET last_verified='2026-01-05'")
        conn.commit()
        inc = money.income_flow(conn, lawd_cd="11110", as_of_ym="202601")
    assert inc.value == pytest.approx(0.08)


def test_오래된_소득_자료는_쓰지_않는다(db):
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO region_income (lawd_cd, as_of_ym, metric, value, unit,"
            " yoy_change_pct, source_name, observed_on, last_verified)"
            " VALUES ('11110','202001','고용자수',100,'명',0.30,'통계청',"
            "'2020-01-01','2020-02-01')")
        conn.commit()
        inc = money.income_flow(conn, lawd_cd="11110", as_of_ym="202601")
    assert inc.value is None
    assert "지난 자료" in inc.skipped["고용자수"]


def test_값도_이유도_없는_소득행은_DB가_거부한다(db):
    """§49-16 데이터가 없는데 값을 추정해 확정값처럼 저장 금지."""
    with get_conn(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO region_income (lawd_cd, as_of_ym, metric, unit,"
                " source_name, observed_on)"
                " VALUES ('11110','202601','고용자수','명','통계청','2026-01-01')")


# ── §8-B 사다리는 이웃한다는 사실만으로 점수를 주지 않는다 ────────────

@pytest.mark.parametrize("rise,gap,overlap,인정", [
    (0.20, 0.30, 0.50, True),
    (0.01, 0.30, 0.50, False),   # 위 칸이 안 올랐다
    (0.20, 0.02, 0.50, False),   # 가격차가 이미 없다
    (0.20, 0.30, 0.05, False),   # 구매자가 안 겹친다 — 남의 동네 이야기
])
def test_사다리는_세_조건이_동시에_성립해야_흐름이다(db, rise, gap, overlap, 인정):
    with get_conn(db) as conn:
        conn.execute("INSERT INTO ladder_axis (name, rationale, curated_by)"
                     " VALUES ('서남부','테스트','tester')")
        conn.execute(
            "INSERT INTO migration_flow (axis_id, from_lawd_cd, to_lawd_cd,"
            " as_of_ym, upper_rise_pct, gap_pct, buyer_overlap, evidence_json,"
            " engine_version) VALUES (1,'11110','41190','202512',?,?,?,'{\"t\":1}','x')",
            (rise, gap, overlap))
        conn.commit()
        m = money.migration_flow(conn, lawd_cd="41190", as_of_ym="202601")
    assert (m.value is not None) is 인정


# ── §9 이름이 아니라 데이터가 움직여야 Dual Flow 다 ───────────────────

def test_두_흐름이_다_있어도_데이터가_안_움직이면_Dual이_아니다():
    inc = money.Income(0.05, {"고용자수": 0.05}, {})
    mig = money.Migration(0.03, "11110", 0.2, 0.3, 0.5)
    d = money.dual_flow(inc, mig, volume_up=False, p25_up=False,
                        median_up=False, jeonse_held=False)
    assert d.exposed is False
    assert "안 움직" in d.reason


def test_데이터가_움직이면_Dual로_인정한다():
    inc = money.Income(0.05, {"고용자수": 0.05}, {})
    mig = money.Migration(0.03, "11110", 0.2, 0.3, 0.5)
    d = money.dual_flow(inc, mig, volume_up=True, p25_up=True,
                        median_up=None, jeonse_held=None)
    assert d.exposed is True and len(d.evidence) == 2


# ── §10 돈이 지역에 와도 이 집까지 닿아야 한다 ────────────────────────

def test_대장이_올라도_구매자가_안_겹치면_전파가_0이다():
    f = money.accessible_money_flow(
        income=Feature.missing("income_flow", "없음"),
        migration=Feature.missing("migration_flow", "없음"),
        leader_pressure=0.5, buyer_overlap=0.0, transmission_prob=0.9)
    assert f.value == 0.0        # 곱이라 하나가 0 이면 0


def test_아무것도_못_보면_0이_아니라_확인_불가다():
    f = money.accessible_money_flow(
        income=Feature.missing("income_flow", "없음"),
        migration=Feature.missing("migration_flow", "없음"),
        leader_pressure=None, buyer_overlap=None, transmission_prob=None)
    assert f.status is Status.DATA_MISSING


# ── §5 토허는 감점이 아니라 Gate 다 ───────────────────────────────────

def _permit(**kw):
    base = dict(lawd_cd="11110", as_of="2026-01-01", scope="내국인",
                designated=True)
    base.update(kw)
    return zone.PermitStatus(**base)


def test_실거주의무_구역은_비거주_투자로_매수_불가다():
    d = gate.decide(_permit(residence_duty_months=24), purpose=gate.INVEST)
    assert d.verdict == gate.BLOCKED and d.executable is False


def test_같은_구역도_실거주_목적이면_통과한다():
    d = gate.decide(_permit(residence_duty_months=24), purpose=gate.LIVE_IN)
    assert d.executable is True


def test_확인_못_한_토허는_통과시키지_않는다():
    """'아마 될 것' 으로 두면 Gate 가 아니다."""
    assert gate.decide(_permit(checked=False), purpose=gate.INVEST).executable is False
    assert gate.decide(_permit(), purpose=gate.INVEST).verdict == gate.NEEDS_CHECK


def test_유예가_확인된_경우만_되돌린다():
    p = _permit(residence_duty_months=24)
    assert gate.decide(p, purpose=gate.INVEST, grace_allowed=None).executable is False
    assert gate.decide(p, purpose=gate.INVEST, grace_allowed=False).executable is False
    assert gate.decide(p, purpose=gate.INVEST, grace_allowed=True).executable is True


def test_기대수익이_1위여도_Gate에_막히면_실행목록에서_빠진다():
    """감점이었다면 점수로 이겨서 1위로 올라온다. 그래서 Gate 여야 한다."""
    from apt_engine.features import stage as stage_mod
    from apt_engine.invest import cash_candidate as cash_mod

    class C:
        def __init__(self, cid): self.complex_id = cid

    cands = [C(1), C(2)]
    v = stage_mod.Verdict(stage=stage_mod.EXECUTABLE_STAGES[0], quadrant=None,
                          quiet_compounder=False, reasons=[])
    stages = {1: v, 2: v}
    cash = cash_mod.CashOption(capital=500_000_000, hurdle_rate=0.01,
                               horizon_years=2)
    blocked = gate.decide(_permit(residence_duty_months=24), purpose=gate.INVEST)

    s = ex.split(cands, stages, cash=cash,
                 expected_returns={1: 0.99, 2: 0.10},   # 1번이 압도적 1위
                 gates={1: blocked})
    ids = [c.complex_id for c in s.executable]
    assert 1 not in ids, "Gate 에 막힌 후보가 실행목록에 있으면 안 된다"
    assert 2 in ids
    # 연구 데이터에서는 지우지 않는다 (§5)
    assert 1 in [c.complex_id for c in s.pure]
    assert any(cid == 1 for cid, _ in s.gate_blocked)


# ── §2 순위는 아파트가 아니라 아파트 × 가격에 붙는다 ──────────────────

def test_같은_단지도_가격이_오르면_매력이_떨어진다():
    b = ex.price_bands(390_000_000, entry_position=0.60)
    vals = [b.attractiveness(p) for p in
            (360_000_000, 375_000_000, 390_000_000, 410_000_000)]
    assert vals == sorted(vals, reverse=True), "가격이 올랐는데 매력이 안 떨어졌다"
    assert vals[0] > vals[-1]


def test_가격_5구간이_모두_나온다():
    b = ex.price_bands(390_000_000, entry_position=0.60)
    got = {b.verdict(p) for p in (b.strong_buy, b.fair, b.compare,
                                  b.chase, b.do_not_buy)}
    assert ex.STRONG_BUY in got and ex.DO_NOT_BUY in got
    assert ex.COMPARE in got or ex.CHASE_RISK in got


# ── §28·§29 사용자 관심 편향 ──────────────────────────────────────────

INTEREST_WORDS = ("question_count", "interest_count", "search_count",
                  "favorite", "bookmark", "관심횟수", "질문횟수", "조회수")


def test_점수_계산_코드에_관심_변수가_아예_없다():
    """§28 — 변수가 존재하면 언젠가 누가 쓴다. 처음부터 없어야 한다."""
    import pathlib
    hits = []
    for p in pathlib.Path("apt_engine").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for w in INTEREST_WORDS:
            if w in text:
                hits.append(f"{p}: {w}")
    assert not hits, f"투자점수 코드에 관심 편향 변수가 있다: {hits}"


def test_같은_데이터면_몇_번_물어봐도_결과가_같다():
    """§29 User Interest Invariance Test.

    같은 Snapshot 을 0번·10번·100번 조회해도 점수·순위가 바뀌면 안 된다.
    """
    b = ex.price_bands(390_000_000, entry_position=0.60)
    first = (b.verdict(), b.attractiveness(), b.strong_buy, b.do_not_buy)
    for _ in range(100):
        again = ex.price_bands(390_000_000, entry_position=0.60)
        assert (again.verdict(), again.attractiveness(),
                again.strong_buy, again.do_not_buy) == first


def test_조회를_반복해도_돈의_흐름_값이_변하지_않는다(db):
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO region_income (lawd_cd, as_of_ym, metric, value, unit,"
            " yoy_change_pct, source_name, observed_on, last_verified)"
            " VALUES ('11110','202512','고용자수',100,'명',0.08,'통계청',"
            "'2025-12-01','2026-01-05')")
        conn.commit()
        vals = {money.income_flow(conn, lawd_cd="11110", as_of_ym="202601").value
                for _ in range(50)}
    assert len(vals) == 1


# ── §27 품질 반영률은 고정값이 아니다 ─────────────────────────────────

def test_리모델링은_신축과_같은_값을_받지_않는다():
    r = quality.capture_ratio(method="리모델링")
    assert r.ratio < quality.capture_ratio(method="재건축").ratio


def test_감점_요소가_많을수록_반영률이_낮아진다():
    plain = quality.capture_ratio(method="리모델링")
    poor = quality.capture_ratio(method="리모델링", factors={
        "지하주차_부족": True, "동간거리_협소": True, "평면_비효율": True})
    assert poor.ratio < plain.ratio
    assert poor.applied      # 왜 깎였는지 남는다


def test_주변_신축가를_모르면_완공가치를_지어내지_않는다():
    r = quality.capture_ratio(method="재건축")
    assert quality.expected_value(peer_new_future_price=None, ratio=r)["값"] is None


# ── §38 차량시간은 점수에 못 들어간다 ─────────────────────────────────

def test_차량시간을_점수에_쓰겠다는_행은_DB가_거부한다(db):
    from apt_engine.repo import apt as repo
    with get_conn(db) as conn:
        repo.sync_regions(conn)
        conn.execute("INSERT INTO complex (kapt_code, name, name_norm, lawd_cd)"
                     " VALUES ('T1','테스트','테스트','11110')")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO drive_time (origin_label, complex_id, minutes,"
                " source_name, measured_on, excluded_from_score)"
                " VALUES ('평택',1,30,'카카오','2026-01-01',0)")


# ── §36 설명은 변수명 나열이 아니다 ───────────────────────────────────

def test_설명에_내부_변수명이_새어나가지_않는다():
    fs = FeatureSet(complex_id=1, area_band="59", as_of="2026-01-01", items={
        f.key: f for f in [F("entry_position", 0.3), F("visible_movement", 1.0),
                           F("downside_defense", 0.7)]})
    text = narrate.paragraph(fs, bands=ex.price_bands(390_000_000,
                                                      entry_position=0.6))
    for bad in ("Remaining Alpha", "entry_position", "Stretch", "Alpha",
                "visible_movement", "Emerging"):
        assert bad not in text, f"설명에 전문용어 '{bad}' 가 남아 있다"
    assert len(text) > 40


def test_못_사는_물건은_그_말이_먼저_나온다():
    fs = FeatureSet(complex_id=1, area_band="59", as_of="2026-01-01",
                    items={f.key: f for f in [F("entry_position", 0.3)]})
    d = gate.decide(_permit(residence_duty_months=24), purpose=gate.INVEST)
    first = narrate.sentences(fs, gate=d)[0]
    assert "매수할 수 없" in first


def test_확인_안_된_지표는_설명에_안_쓴다():
    """없는 것을 '없다' 로 바꿔 말하면 안 된다."""
    fs = FeatureSet(complex_id=1, area_band="59", as_of="2026-01-01", items={
        "income_flow": Feature.missing("income_flow", "자료 없음")})
    text = narrate.paragraph(fs)
    assert "일자리" not in text and "소득이 늘" not in text


# ── §40 대출 가능 여부 ────────────────────────────────────────────────

def test_소득을_모르면_대출한도를_지어내지_않는다():
    label, _ = narrate.loan_feasibility(
        cash=200_000_000, required_no_loan=400_000_000,
        required_with_loan=None, has_income_data=False)
    assert label == narrate.NEEDS_FINANCE_DATA


def test_확인_불가_비용이_있으면_가능하다고_말하지_않는다():
    label, why = narrate.loan_feasibility(
        cash=900_000_000, required_no_loan=400_000_000,
        required_with_loan=300_000_000, has_income_data=True, unknown_costs=3)
    assert label == narrate.NEEDS_CHECK and "더 큽니다" in why


# ── §49-3 호재 자체에는 점수를 주지 않는다 ────────────────────────────

def test_정비사업은_존재가_아니라_가격괴리로_점수를_낸다():
    """"재건축 추진 중" 이라는 **사실**에 알파를 주면 안 된다(§49-3).

    점수는 `redev_mispricing` — 계산한 가치와 지금 시장가의 차이 — 에서
    나와야 한다. 사업이 있다는 것만으로는 이미 가격에 반영됐을 수 있다.
    """
    from apt_engine.scoring import models
    feats = [f for f, _ in models.SPEC["redevelopment"]]
    assert feats == ["redev_mispricing"], (
        f"정비사업 점수가 가격괴리 외의 것에서 나온다: {feats}")


def test_공법별_품질상한은_점수가_아니라_상한이다():
    """`BASE_RATIO['재건축'] = 1.00` 은 호재 가점이 아니다.

    리모델링은 기존 구조체를 남기므로 완공 후에도 신축 가격을 다 받지
    못한다 — 그건 물리적 제약이지 점수가 아니다(§27). 구분이 무너지지
    않도록, 이 값이 **1.0 을 넘지 못한다**는 것으로 못박는다.
    점수라면 좋은 사업일수록 커져야 하지만, 상한은 1.0 을 못 넘는다.
    """
    from apt_engine.redev import quality
    assert all(0 < v <= 1.0 for v in quality.BASE_RATIO.values())
    # 그리고 이 값은 기대수익에 더해지는 것이 아니라 **곱해지는** 상한이다.
    r = quality.capture_ratio(method="재건축")
    ev = quality.expected_value(peer_new_future_price=1_000_000_000, ratio=r)
    assert ev["값"] <= 1_000_000_000, "품질 상한이 신축가를 넘겨선 안 된다"


def test_지역_이름에_직접_점수를_주는_곳이_없다():
    """§49-2 — 특정 지역 이름 자체에 상승점수 부여 금지."""
    import pathlib
    import re
    REGIONS = ("평택", "동탄", "판교", "광명", "부천", "김포", "수원", "용인")
    hits = []
    for p in pathlib.Path("apt_engine").rglob("*.py"):
        for m in re.finditer(
                r'["\'](' + "|".join(REGIONS) + r')["\']\s*:\s*[0-9.]+',
                p.read_text(encoding="utf-8")):
            hits.append(f"{p.name}: {m.group(0)}")
    assert not hits, f"지역 이름에 직접 점수를 주는 곳이 있다: {hits}"


# ── §24·§27 주변 대표 신축은 새 API 없이 기존 데이터로 찾는다 ─────────

def _mk(conn, name, *, year, lat, lon, band="84", price=None, n=10, ym="202512"):
    conn.execute(
        "INSERT INTO complex (kapt_code, name, name_norm, lawd_cd, emd_name,"
        " lat, lon, approval_year, apt_households)"
        " VALUES (?,?,?,'11110','청운동',?,?,?,500)",
        (f"K{name}", name, name, lat, lon, year))
    cid = conn.execute("SELECT id FROM complex WHERE name=?", (name,)).fetchone()[0]
    if price is not None:
        conn.execute(
            "INSERT INTO price_snapshot (complex_id, area_band, as_of_ym,"
            " window_months, representative_price, method, sample_n, excluded_n,"
            " confidence, engine_version, data_grade, calc_trace)"
            " VALUES (?,?,?,3,?,'median',?,0,'HIGH','x','CONFIRMED','{\"t\":1}')",
            (cid, band, ym, price, n))
    return cid


@pytest.fixture
def peers(db):
    from apt_engine.repo import apt as repo
    with get_conn(db) as conn:
        repo.sync_regions(conn)
        me = _mk(conn, "재건축단지", year=1985, lat=37.5850, lon=126.9700)
        _mk(conn, "신축A", year=2023, lat=37.5855, lon=126.9705, price=1_500_000_000)
        _mk(conn, "신축B", year=2022, lat=37.5860, lon=126.9710, price=1_200_000_000)
        _mk(conn, "먼신축", year=2023, lat=37.7000, lon=127.1000, price=2_000_000_000)
        _mk(conn, "표본부족", year=2024, lat=37.5852, lon=126.9702,
            price=3_000_000_000, n=2)
        conn.commit()
    return db, me


def test_주변_대표_신축을_기존_데이터로_찾는다(peers):
    from apt_engine.redev import peer_new
    db, me = peers
    with get_conn(db) as conn:
        p = peer_new.find(conn, complex_id=me, area_band="84", as_of_ym="202601")
    assert p.known
    assert p.name == "신축A", "가장 비싼 주변 신축이 대표여야 한다"
    assert p.price == 1_500_000_000


def test_멀리_있는_신축은_기준이_되지_않는다(peers):
    """생활권이 갈리면 그 동네 기준이 아니다."""
    from apt_engine.redev import peer_new
    db, me = peers
    with get_conn(db) as conn:
        p = peer_new.find(conn, complex_id=me, area_band="84", as_of_ym="202601")
    assert p.name != "먼신축"


def test_표본이_적은_스냅샷은_대표로_쓰지_않는다(peers):
    """가장 비싸지만 표본 2건인 단지를 대표로 쓰면 §49-4 위반이다."""
    from apt_engine.redev import peer_new
    db, me = peers
    with get_conn(db) as conn:
        p = peer_new.find(conn, complex_id=me, area_band="84", as_of_ym="202601")
    assert p.name != "표본부족"


def test_신축이_하나뿐이면_대표를_정하지_않는다(db):
    from apt_engine.redev import peer_new
    from apt_engine.repo import apt as repo
    with get_conn(db) as conn:
        repo.sync_regions(conn)
        me = _mk(conn, "구축", year=1990, lat=37.585, lon=126.970)
        _mk(conn, "유일신축", year=2023, lat=37.586, lon=126.971, price=1_000_000_000)
        conn.commit()
        p = peer_new.find(conn, complex_id=me, area_band="84", as_of_ym="202601")
    assert not p.known and "1개뿐" in p.reason


def test_상승률을_모르면_미래가격을_지어내지_않는다(peers):
    from apt_engine.redev import peer_new
    db, me = peers
    with get_conn(db) as conn:
        p = peer_new.find(conn, complex_id=me, area_band="84", as_of_ym="202601")
    out = peer_new.future_price(p, annual_growth=None, years=5)
    assert out["값"] is None and "가정" in out["사유"]
    ok = peer_new.future_price(p, annual_growth=0.03, years=5)
    assert ok["값"] > p.price and "가정" in ok
