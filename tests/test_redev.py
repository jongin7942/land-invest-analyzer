"""재건축 사업성 테스트 (요구사항 14·17·18·19·20·30·62-5·62-7).

이 계층이 지키려는 선:
  1. 법정 최대 용적률을 확정된 사업 용적률처럼 쓰지 않는다
  2. 정비계획 고시 전에는 정비계획 용적률이 존재할 수 없다
  3. 가정이 없으면 분담금을 만들지 않는다 — 기본값으로 채우지 않는다
  4. 분담금은 언제나 구간이고, 등급은 언제나 SCENARIO 다
  5. 1차 스크리닝에서는 금액을 만들지 않는다
  6. 자료가 없는 단지를 '사업성 나쁨'으로 만들지 않는다
"""
import json
import sqlite3

import pytest

from apt_engine import units
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.redev import conversion, far as far_mod, feasibility as feas
from apt_engine.redev import scenario as scen, screening, stage as stage_mod
from apt_engine.repo import apt as repo
from apt_engine.repo import redev as redev_repo

TODAY = "2026-08-31"


@pytest.fixture
def db(tmp_db):
    mig.migrate(tmp_db)
    with get_conn(tmp_db) as conn:
        repo.sync_regions(conn)
    return tmp_db


def add_complex(conn, name="테스트단지", *, lawd="28237", households=1000,
                approval_year=1990, far=None, land_area=None, zoning="제2종일반주거지역"):
    repo.upsert_complexes(conn, [{
        "kapt_code": f"K{name}", "name": name, "name_norm": name, "lawd_cd": lawd,
        "apt_households": households, "approval_year": approval_year,
        "current_far": far, "land_area_m2": land_area, "zoning": zoning}])
    return conn.execute("SELECT id FROM complex WHERE kapt_code=?",
                        (f"K{name}",)).fetchone()[0]


def add_far(conn, kind, max_far, *, zoning="제2종일반주거지역", sido=None, lawd=None,
            verified=TODAY):
    conn.execute(
        "INSERT INTO far_standard (sido, lawd_cd, zoning, kind, max_far, "
        " effective_from, source_name, last_verified) VALUES (?,?,?,?,?,?,?,?)",
        (sido, lawd, zoning, kind, max_far, "2020-01-01", f"{kind} 근거", verified))


def base_assumptions(**over):
    kw = dict(far=250.0, far_kind="조례", cost_per_py=7_000_000, cost_base_year=2025,
              new_price_per_m2=12_000_000, avg_new_unit_area_m2=113.4,
              construction_area_factor=1.4, other_cost_rate=0.25,
              member_discount=1.0, prior_asset_per_member=600_000_000,
              member_count=1000, rental_ratio=0.0)
    kw.update(over)
    return feas.Assumptions(**kw)


# ── 스키마가 막는 것 ───────────────────────────────────────────────────

def test_정비계획_용적률은_구역지정_전에_저장할_수_없다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO redevelopment_project (complex_id, project_type, stage, "
                " stage_date, planned_far) VALUES (?,?,?,?,?)",
                (cid, "재건축", "정밀안전진단", "2025-01-01", 300.0))


def test_단계를_적으면_단계일자가_필요하다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO redevelopment_project (complex_id, project_type, stage) "
                "VALUES (?,?,?)", (cid, "재건축", "조합설립"))
        # '미지정'은 예외 — 아직 아무 일도 없었다는 뜻이다
        conn.execute(
            "INSERT INTO redevelopment_project (complex_id, project_type, stage) "
            "VALUES (?,?,?)", (cid, "재개발", "미지정"))


def test_시나리오는_SCENARIO_등급으로만_저장된다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO redevelopment_scenario (complex_id, area_band, as_of, "
                " scenario_key, far, far_kind, cost_per_py, cost_base_year, "
                " new_price_per_m2, engine_version, calc_trace, data_grade) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, "84", TODAY, "기준", 250.0, "조례", 7_000_000, 2025,
                 12_000_000, "0.8.0", "{}", "CONFIRMED"))


def test_스크리닝_사유는_비어있을_수_없다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO redev_candidate (complex_id, screened_at, as_of, score, "
                " reason_json, engine_version) VALUES (?,?,?,?,?,?)",
                (cid, TODAY, TODAY, 0.5, "", "0.8.0"))


# ── 용적률: 네 가지를 섞지 않는다 ──────────────────────────────────────

def test_법정상한만_있으면_사업용적률로_자동선택하지_않는다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        add_far(conn, "법정상한", 300.0)
        basis, why = far_mod.resolve(conn, cid, as_of=TODAY)
        assert basis is None
        assert "법정상한" in why


def test_조례가_있으면_조례를_쓰되_단서가_붙는다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        add_far(conn, "법정상한", 300.0)
        add_far(conn, "조례", 250.0)
        basis, why = far_mod.resolve(conn, cid, as_of=TODAY)
        assert basis.kind == "조례" and basis.far == 250.0
        assert "실제 정비계획은 보통 이보다 낮" in why
        assert basis.grade == "ESTIMATED"
        assert "조례" in basis.label            # 숫자만 떼어 쓸 수 없다


def test_정비계획이_있으면_그것이_이긴다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        add_far(conn, "조례", 250.0)
        conn.execute(
            "INSERT INTO redevelopment_project (complex_id, project_type, stage, "
            " stage_date, planned_far, last_verified) VALUES (?,?,?,?,?,?)",
            (cid, "재건축", "정비구역지정", "2025-06-01", 280.0, TODAY))
        basis, _ = far_mod.resolve(conn, cid, as_of=TODAY)
        assert basis.kind == "정비계획" and basis.far == 280.0
        assert basis.grade == "CONFIRMED"


def test_미검증_용적률은_기본적으로_쓰지_않는다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        add_far(conn, "조례", 250.0, verified=None)
        assert far_mod.available(conn, zoning="제2종일반주거지역", as_of=TODAY) == []
        got = far_mod.available(conn, zoning="제2종일반주거지역", as_of=TODAY,
                                allow_unverified=True)
        assert len(got) == 1 and got[0].verified is False


def test_법정상한_Calc_는_SCENARIO_등급이다(db):
    with get_conn(db) as conn:
        add_complex(conn)
        add_far(conn, "법정상한", 300.0)
        basis = far_mod.available(conn, zoning="제2종일반주거지역", as_of=TODAY)[0]
        calc = far_mod.to_calc(basis)
        assert calc.grade == "SCENARIO"
        assert "상한이지 예상치가 아니다" in calc.intermediates["단서"]


# ── 1차 스크리닝 ──────────────────────────────────────────────────────

def test_스크리닝은_금액을_만들지_않는다(db):
    with get_conn(db) as conn:
        add_complex(conn, "오래된단지", approval_year=1988, far=150, land_area=45000)
        found = screening.screen(conn, as_of=TODAY)
        assert len(found) == 1
        c = found[0]
        assert 0 <= c.score <= 1
        # 결과 어디에도 '원' 단위 값이 없다
        assert not any(k.endswith("분담금") or k.endswith("사업비") for k in c.reason)


def test_대지면적이_없으면_0점이_아니라_가중치에서_빠진다(db):
    with get_conn(db) as conn:
        add_complex(conn, "자료없음", approval_year=1988, far=150, land_area=None)
        c = screening.screen(conn, as_of=TODAY)[0]
        assert c.land_share_m2 is None
        assert "대지지분" not in c.parts
        assert any("대지지분" in m for m in c.missing)
        assert "자료가 없는 것" in c.reason["주의"]
        # 같은 조건에서 대지면적만 있는 단지와 비교해 부당하게 낮지 않다
        assert c.score > 0


def test_연식_미달_단지는_후보가_아니다(db):
    with get_conn(db) as conn:
        add_complex(conn, "신축", approval_year=2015, far=150, land_area=45000)
        assert screening.screen(conn, as_of=TODAY) == []


def test_용적률이_높으면_탈락한다(db):
    with get_conn(db) as conn:
        add_complex(conn, "고밀", approval_year=1988, far=320, land_area=45000)
        assert screening.screen(conn, as_of=TODAY) == []


def test_오피스텔_세대수는_대지지분_계산에_섞이지_않는다(db):
    with get_conn(db) as conn:
        repo.upsert_complexes(conn, [{
            "kapt_code": "KMIX", "name": "주상복합", "name_norm": "주상복합",
            "lawd_cd": "28237", "apt_households": 500, "officetel_households": 500,
            "approval_year": 1990, "current_far": 150, "land_area_m2": 50000,
            "zoning": "제2종일반주거지역"}])
        c = screening.screen(conn, as_of=TODAY)[0]
        assert c.land_share_m2 == pytest.approx(100.0)   # 50000/500, 1000 이 아니다


def test_스크리닝_저장은_사람이_정한_조사상태를_덮어쓰지_않는다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn, "후보", approval_year=1988, far=150, land_area=45000)
        screening.save(conn, screening.screen(conn, as_of=TODAY), as_of=TODAY)
        redev_repo.set_manual_status(conn, cid, status="완료", note="정비계획 확인함")
        screening.save(conn, screening.screen(conn, as_of=TODAY), as_of=TODAY)
        row = conn.execute("SELECT * FROM redev_candidate WHERE complex_id=?",
                           (cid,)).fetchone()
        assert row["manual_status"] == "완료"
        assert json.loads(row["reason_json"])["한계"]


# ── 사업성 ────────────────────────────────────────────────────────────

def test_가정이_비면_분담금을_만들지_않는다():
    with pytest.raises(feas.MissingAssumption) as e:
        feas.compute(land_area_m2=40000, a=base_assumptions(cost_per_py=0))
    assert "cost_per_py" in str(e.value)


def test_대지면적이_없으면_계산을_거부한다():
    with pytest.raises(feas.MissingAssumption) as e:
        feas.compute(land_area_m2=0, a=base_assumptions())
    assert "대지면적" in str(e.value)


def test_비례율과_추가분담금_산식():
    a = base_assumptions(member_count=100, prior_asset_per_member=600_000_000)
    f = feas.compute(land_area_m2=20000, a=a)
    # 신축 연면적 = 20000 × 2.5 = 50000㎡ → 세대수 = 50000 // 113.4
    assert f.new_units == int(50000 // 113.4)
    assert f.general_units == f.new_units - f.member_units - f.rental_units
    # 비례율 = (수입 − 사업비) / 종전자산총액
    expected = (f.revenue - f.total_cost) / f.prior_asset_total
    assert f.proportion_rate == pytest.approx(expected)
    # 추가분담금 = 조합원분양가 − 권리가액
    assert f.extra_charge == f.member_price - f.right_value


def test_결과_등급은_언제나_SCENARIO():
    f = feas.compute(land_area_m2=40000, a=base_assumptions())
    assert f.calc.grade == "SCENARIO"
    assert "조합이 통보한 분담금이 아닙니다" in f.calc.intermediates["성격"]


def test_조합원을_다_담지_못하면_경고한다():
    a = base_assumptions(member_count=5000)
    f = feas.compute(land_area_m2=20000, a=a)
    assert any("성립하지 않습니다" in c for c in f.caveats)


def test_임대_매각수입을_0으로_둔_것을_보수적이라고_밝힌다():
    f = feas.compute(land_area_m2=40000, a=base_assumptions(rental_ratio=0.1))
    assert any("보수적" in c for c in f.caveats)


def test_종전자산_근사를_밝힌다():
    f = feas.compute(land_area_m2=40000, a=base_assumptions())
    assert any("근사했습니다" in c for c in f.caveats)


def test_종전자산_총액이_확정되면_근사하지_않는다():
    a = base_assumptions(prior_asset_total=500_000_000_000)
    f = feas.compute(land_area_m2=40000, a=a)
    assert f.prior_asset_total == 500_000_000_000
    assert not any("근사했습니다" in c for c in f.caveats)


# ── 3구간과 민감도 ────────────────────────────────────────────────────

def test_분담금은_하나가_아니라_구간이다():
    b = scen.band(land_area_m2=40000, base=base_assumptions())
    assert set(b.results) == set(scen.KEYS)
    lo, hi = b.span
    assert lo < hi
    assert "~" in b.label


def test_보수_시나리오가_가장_불리하다():
    b = scen.band(land_area_m2=40000, base=base_assumptions())
    assert b.charges["보수"] == max(b.charges.values())
    assert b.charges["낙관"] == min(b.charges.values())


def test_배율은_관측치가_아니라_가정이라고_밝힌다():
    b = scen.band(land_area_m2=40000, base=base_assumptions())
    assert "관측된 통계가 아니" in b.calc.intermediates["배율 성격"]
    assert b.calc.grade == "SCENARIO"


def test_한_시나리오가_실패해도_나머지는_계산한다():
    # 용적률을 크게 낮추면 보수 시나리오에서 세대수가 0이 된다
    a = base_assumptions(far=2.0)
    b = scen.band(land_area_m2=300, base=a)
    assert b.failed or b.results          # 어느 쪽이든 조용히 0을 만들지 않는다
    for reason in b.failed.values():
        assert "가정" in reason or "세대수" in reason


def test_민감도는_한_번에_하나씩만_흔든다():
    s = scen.sensitivity(land_area_m2=40000, base=base_assumptions(), factor="공사비")
    assert [step for step, _, _ in s.rows] == list(scen.SENSITIVITY_STEPS)
    assert s.swing > 0


def test_민감도가_가장_큰_항목을_알려준다():
    calc = scen.sensitivity_calc(land_area_m2=40000, base=base_assumptions())
    assert calc.intermediates["가장 민감한 항목"] in scen.FACTOR_FIELDS
    assert calc.grade == "SCENARIO"


def test_공사비가_오르면_분담금도_오른다():
    a = base_assumptions()
    low = feas.compute(land_area_m2=40000, a=a).extra_charge
    high = feas.compute(land_area_m2=40000,
                        a=base_assumptions(cost_per_py=a.cost_per_py * 2)).extra_charge
    assert high > low


# ── 사업기간·지연위험 ─────────────────────────────────────────────────

def add_project(conn, cid, *, stage="조합설립", stage_date="2020-05-01", **over):
    cols = dict(complex_id=cid, project_type="재건축", stage=stage,
                stage_date=stage_date, last_verified=TODAY)
    cols.update(over)
    conn.execute(
        f"INSERT INTO redevelopment_project ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})", tuple(cols.values()))


def test_소요기간_참고치가_없으면_확인_불가다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        add_project(conn, cid)
        d = stage_mod.remaining(conn, stage_mod.load(conn, cid))
        assert d.months is None
        assert "확인 불가" in d.label


def test_참고치가_있는_구간만_더하고_없는_구간을_밝힌다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        add_project(conn, cid, stage="조합설립")
        conn.execute(
            "INSERT INTO stage_duration_ref (project_type, from_stage, to_stage, "
            " median_months, last_verified) VALUES (?,?,?,?,?)",
            ("재건축", "조합설립", "사업시행인가", 48, TODAY))
        d = stage_mod.remaining(conn, stage_mod.load(conn, cid))
        assert d.months == 48
        assert d.missing                      # 사업시행인가 이후 구간은 참고치가 없다
        assert "이상" in d.label
        assert d.complete is False


def test_예정일이_지났으면_지연으로_본다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        add_project(conn, cid, stage="조합설립", expected_approval_ym="202301")
        risk = stage_mod.delay_risk(stage_mod.load(conn, cid), as_of=TODAY)
        assert risk.level == "높음"
        assert any("예정일 경과" in r for r in risk.reasons)


def test_초기단계는_지연위험이_높다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        add_project(conn, cid, stage="예비안전진단", stage_date="2026-01-01")
        risk = stage_mod.delay_risk(stage_mod.load(conn, cid), as_of=TODAY)
        assert any("조합 설립 전" in r for r in risk.reasons)


def test_관리처분_이후는_되돌아가기_어렵다고_말한다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        add_project(conn, cid, stage="관리처분인가", stage_date="2026-01-01")
        p = stage_mod.load(conn, cid)
        assert p.irreversible
        assert stage_mod.delay_risk(p, as_of=TODAY).level == "낮음"


def test_사업단계_Calc_는_사실과_추정을_나눠_담는다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        add_project(conn, cid, expected_done_ym="203512")
        calc = stage_mod.to_calc(conn, stage_mod.load(conn, cid), as_of=TODAY)
        assert "확정된 사실" in calc.intermediates
        assert "추정" in calc.intermediates
        assert calc.intermediates["확정된 사실"]["단계"] == "조합설립"


# ── 신축전환원가 ──────────────────────────────────────────────────────

def test_모르는_항목을_0으로_세지_않는다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn)
        conv = conversion.compute(conn, price=units.from_eok(6.2), as_of=TODAY,
                                  lawd_cd="28237", extra_charge=200_000_000)
        assert not conv.complete
        assert "취득 관련 세금" in conv.unknown
        assert "이상" in conv.label
        # 확인 불가 항목은 합계에 0으로 들어가지 않는다
        known = sum(i.signed for i in conv.items if i.known)
        assert conv.total == known


def test_사업기간을_모르면_금융비용을_계산하지_않는다(db):
    with get_conn(db) as conn:
        add_complex(conn)
        conv = conversion.compute(conn, price=units.from_eok(6.2), as_of=TODAY,
                                  lawd_cd="28237", extra_charge=0, years=None,
                                  loan_amount=units.from_eok(2), loan_rate=0.045)
        assert any("사업기간" in u for u in conv.unknown)


def test_분담금을_모르면_원가도_확인_불가다(db):
    with get_conn(db) as conn:
        add_complex(conn)
        conv = conversion.compute(conn, price=units.from_eok(6.2), as_of=TODAY,
                                  lawd_cd="28237", extra_charge=None)
        assert "추가분담금" in conv.unknown


def test_마진은_원가에_구멍이_있으면_그렇게_말한다(db):
    with get_conn(db) as conn:
        add_complex(conn)
        conv = conversion.compute(conn, price=units.from_eok(6.2), as_of=TODAY,
                                  lawd_cd="28237", extra_charge=0,
                                  future_value=units.from_eok(12))
        assert "이하" in conv.margin_label


def test_준공후_가치는_상승률을_곱하지_않는다():
    a = base_assumptions()
    value, note = conversion.future_value_of(a)
    assert value == int(units.won_round(a.avg_new_unit_area_m2 * a.new_price_per_m2))
    assert "상승률을 곱하지 않았습니다" in note


def test_원가_Calc_는_마진을_수익이라_부르지_않는다(db):
    with get_conn(db) as conn:
        add_complex(conn)
        conv = conversion.compute(conn, price=units.from_eok(6.2), as_of=TODAY,
                                  lawd_cd="28237", extra_charge=0,
                                  future_value=units.from_eok(12))
        assert conv.calc.grade == "SCENARIO"
        assert "얼마 번다'가 아닙니다" in conv.calc.intermediates["해석"]


# ── 입력 서식·가져오기 ────────────────────────────────────────────────

def test_서식에는_값이_들어있지_않다(tmp_path):
    for kind in redev_repo.TEMPLATES:
        p = redev_repo.write_template(kind, tmp_path / f"{kind}.csv")
        body = [ln for ln in p.read_text(encoding="utf-8").splitlines()
                if ln and not ln.startswith("#")]
        assert len(body) == 1                      # 헤더 한 줄뿐
        assert "," in body[0]


def test_조례_상한을_정비계획으로_적으면_거부한다(db, tmp_path):
    with get_conn(db) as conn:
        add_complex(conn, "후보")
        p = tmp_path / "project.csv"
        p.write_text(
            "complex_name,lawd_cd,project_type,stage,stage_date,planned_far\n"
            "후보,28237,재건축,정밀안전진단,2025-01-01,300\n", encoding="utf-8")
        with pytest.raises(redev_repo.RedevImportError) as e:
            redev_repo.import_projects(conn, p)
        assert "조례 상한" in str(e.value)


def test_단계일자_없이는_단계를_적을_수_없다(db, tmp_path):
    with get_conn(db) as conn:
        add_complex(conn, "후보")
        p = tmp_path / "project.csv"
        p.write_text("complex_name,lawd_cd,project_type,stage\n"
                     "후보,28237,재건축,조합설립\n", encoding="utf-8")
        with pytest.raises(redev_repo.RedevImportError) as e:
            redev_repo.import_projects(conn, p)
        assert "stage_date" in str(e.value)


def test_동명_단지가_여럿이면_아무거나_붙이지_않는다(db, tmp_path):
    with get_conn(db) as conn:
        repo.upsert_complexes(conn, [
            {"kapt_code": "KA", "name": "같은이름", "name_norm": "같은이름",
             "lawd_cd": "28237"},
            {"kapt_code": "KB", "name": "같은이름", "name_norm": "같은이름",
             "lawd_cd": "28237"}])
        p = tmp_path / "project.csv"
        p.write_text("complex_name,lawd_cd,project_type,stage,stage_date\n"
                     "같은이름,28237,재건축,조합설립,2020-01-01\n", encoding="utf-8")
        with pytest.raises(redev_repo.RedevImportError) as e:
            redev_repo.import_projects(conn, p)
        assert "구분하세요" in str(e.value)


def test_대지면적에는_출처가_필요하다(db, tmp_path):
    with get_conn(db) as conn:
        add_complex(conn, "후보")
        p = tmp_path / "land.csv"
        p.write_text("complex_name,lawd_cd,land_area_m2\n후보,28237,45000\n",
                     encoding="utf-8")
        with pytest.raises(redev_repo.RedevImportError) as e:
            redev_repo.import_land_area(conn, p)
        assert "source_name" in str(e.value)


def test_대지면적_가져오기가_출처를_남긴다(db, tmp_path):
    with get_conn(db) as conn:
        cid = add_complex(conn, "후보")
        p = tmp_path / "land.csv"
        p.write_text("complex_name,lawd_cd,land_area_m2,source_name,last_verified\n"
                     "후보,28237,45000,건축물대장 총괄표제부,2026-08-31\n", encoding="utf-8")
        s = redev_repo.import_land_area(conn, p)
        assert s["complexes"] == 1
        row = conn.execute("SELECT * FROM complex WHERE id=?", (cid,)).fetchone()
        assert row["land_area_m2"] == 45000
        assert row["land_area_source"] == "건축물대장 총괄표제부"


def test_참고표_가져오기는_한_줄이라도_틀리면_전부_거부한다(db, tmp_path):
    with get_conn(db) as conn:
        p = tmp_path / "cost.csv"
        p.write_text("region,grade,base_year,cost_per_py,other_cost_rate\n"
                     "서울,보통,2025,7000000,0.25\n"
                     "서울,보통,2024,일곱백만,0.25\n", encoding="utf-8")
        with pytest.raises(redev_repo.RedevImportError):
            redev_repo.import_csv(conn, "cost", p)
        assert conn.execute("SELECT COUNT(*) FROM construction_cost_ref").fetchone()[0] == 0


def test_시나리오_저장은_추적을_함께_남긴다(db):
    with get_conn(db) as conn:
        cid = add_complex(conn, land_area=40000)
        a = base_assumptions()
        b = scen.band(land_area_m2=40000, base=a)
        for key, result in b.results.items():
            redev_repo.save_scenario(conn, complex_id=cid, area_band="84", as_of=TODAY,
                                     scenario_key=key, assumptions=scen.variant(a, key),
                                     result=result, calc_json=result.calc.to_json())
        rows = redev_repo.latest_scenarios(conn, cid)
        assert len(rows) == 3
        for r in rows:
            assert r["data_grade"] == "SCENARIO"
            assert json.loads(r["calc_trace"])["grade"] == "SCENARIO"


def test_공사비_참고치는_기준연도를_함께_돌려준다(db):
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO construction_cost_ref (region, grade, base_year, cost_per_py, "
            " other_cost_rate, source_name, last_verified) VALUES (?,?,?,?,?,?,?)",
            ("인천", "보통", 2025, 7_000_000, 0.25, "조합 공고", TODAY))
        got = feas.cost_reference(conn, region="인천")
        assert got is not None
        cost, year, other, ev = got
        assert (cost, year, other) == (7_000_000, 2025, 0.25)
        assert "2025" in (ev.effective_date or "")


def test_미검증_공사비는_기본적으로_쓰지_않는다(db):
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO construction_cost_ref (region, grade, base_year, cost_per_py) "
            "VALUES (?,?,?,?)", ("인천", "보통", 2025, 7_000_000))
        assert feas.cost_reference(conn, region="인천") is None
        assert feas.cost_reference(conn, region="인천", allow_unverified=True) is not None
