"""호가 계층 테스트 (요구사항 3·4·5·8·9·10·46).

호가는 실거래가 아니다. 이 테스트들이 지키려는 선은 하나다 —
**최저호가 하나를 현재 시장가격이라고 단정하지 않는다.**
"""
import json

import pytest

from apt_engine import units
from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn
from apt_engine.listing import change, dedupe, distribution, gap, pressure, special
from apt_engine.listing.provider import (ListingError, ManualListingProvider,
                                          normalize_row, parse_price, write_template)
from apt_engine.price import snapshot as snap_mod
from apt_engine.repo import apt as repo
from apt_engine.repo import listing as listing_repo


def raw(name="동아1단지", ttype="매매", price="6.2", m2="84.96", **kw):
    return {"apt_name": name, "trade_type": ttype, "price": price,
            "exclusive_area_m2": m2, **kw}


def listing(price_eok, *, key=None, special_flags=None, floor=7, top=15, dong=None,
            ttype="매매", direction=None):
    flags = special_flags or []
    return {
        "listing_key": key or f"L{price_eok}-{floor}-{dong}",
        "provider": "manual", "apt_name": "동아1단지", "trade_type": ttype,
        "price": int(units.from_eok(price_eok)), "monthly_rent": 0,
        "exclusive_area_m2": 84.96, "area_band": "84",
        "dong": dong, "floor": floor, "top_floor": top,
        "floor_group": special.floor_group(floor, top),
        "direction": direction,
        "special_flags": flags, "is_special": 1 if special.is_special(flags) else 0,
        "first_seen_at": "2026-08-01", "last_seen_at": "2026-08-01",
    }


# ── 입력 ──────────────────────────────────────────────────────────────

class TestPriceParsing:
    @pytest.mark.parametrize("text,expected", [
        ("6.2", 620_000_000), ("6.2억", 620_000_000),
        ("620000000", 620_000_000), ("620,000,000", 620_000_000),
        ("62000만", 620_000_000), ("62,000만원", 620_000_000),
    ])
    def test_억과_원을_모두_받는다(self, text, expected):
        assert parse_price(text) == expected

    @pytest.mark.parametrize("bad", ["", None, "비싸요", "0", "-3"])
    def test_잘못된_가격은_거부(self, bad):
        with pytest.raises(ListingError):
            parse_price(bad)


class TestManualProvider:
    def test_필수항목이_없으면_몇_행인지_알려준다(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("apt_name,trade_type,price,exclusive_area_m2\n"
                     "동아1단지,매매,,84.96\n", encoding="utf-8")
        with pytest.raises(ListingError, match="2행"):
            ManualListingProvider.from_csv(p)

    def test_거래유형이_이상하면_거부(self):
        with pytest.raises(ListingError, match="거래유형"):
            normalize_row(raw(ttype="반전세"), provider="manual", seen_on="2026-08-01")

    def test_월세인데_월세액이_없으면_거부(self):
        with pytest.raises(ListingError, match="monthly_rent"):
            normalize_row(raw(ttype="월세"), provider="manual", seen_on="2026-08-01")

    def test_서식_파일을_읽을_수_있다(self, tmp_path):
        path = write_template(tmp_path / "t.csv")
        prov = ManualListingProvider.from_csv(path)
        assert len(prov.get_sale_listings()) == 2
        assert len(prov.get_jeonse_listings()) == 1

    def test_지문은_가격이_바뀌어도_같다(self):
        """같은 매물의 가격 인하를 새 매물로 세면 안 된다."""
        a = normalize_row(raw(price="6.2"), provider="manual", seen_on="2026-08-01")
        b = normalize_row(raw(price="6.05"), provider="manual", seen_on="2026-08-15")
        assert a["listing_key"] == b["listing_key"]

    def test_동이나_층이_다르면_다른_매물이다(self):
        a = normalize_row(raw(floor="7"), provider="manual", seen_on="2026-08-01")
        b = normalize_row(raw(floor="9"), provider="manual", seen_on="2026-08-01")
        assert a["listing_key"] != b["listing_key"]

    def test_특수조건이_자동_감지된다(self):
        row = normalize_row(raw(features="급매 올수리필요", floor="2"),
                             provider="manual", seen_on="2026-08-01")
        assert set(row["special_flags"]) >= {"급매", "수리필요", "저층"}
        assert row["is_special"] == 1

    def test_json_입력(self, tmp_path):
        p = tmp_path / "l.json"
        p.write_text(json.dumps([raw()], ensure_ascii=False), encoding="utf-8")
        assert len(ManualListingProvider.from_json(p).get_all()) == 1


# ── 특수조건 ──────────────────────────────────────────────────────────

class TestSpecial:
    def test_층_구분은_전체_층수를_알아야_한다(self):
        # 15층 건물의 12층과 30층 건물의 12층은 다르다.
        assert special.floor_group(12, 15) == "고층"
        assert special.floor_group(12, 30) == "중층"
        assert special.floor_group(12, None) is None
        assert special.floor_group(10, 15) == "중층"   # 정확히 2/3 지점은 중층

    def test_최상층과_확장안됨은_특수매물이_아니다(self):
        # 선호도 차이일 뿐 시세 자체를 왜곡하지는 않는다.
        assert special.is_special(["최상층", "확장안됨"]) is False
        assert special.is_special(["급매"]) is True


# ── 중복 추정 ─────────────────────────────────────────────────────────

class TestDedupe:
    def test_동_층_면적이_같으면_확실한_중복(self):
        rows = [listing(6.2, key="a", dong="101", floor=7),
                listing(6.25, key="b", dong="101", floor=7)]
        r = dedupe.estimate(rows)
        assert r.certain_duplicates == 1
        assert r.unique_max == 1

    def test_동을_모르면_확정하지_않고_범위로_낸다(self):
        rows = [listing(6.2, key="a", floor=7), listing(6.2, key="b", floor=7)]
        r = dedupe.estimate(rows)
        assert r.has_uncertainty or r.unique_min <= r.unique_max
        assert r.raw_count == 2

    def test_범위_표기(self):
        r = dedupe.estimate([listing(6.2, key=f"k{i}", floor=i) for i in range(3, 8)])
        assert "건" in r.range_label

    def test_빈_목록(self):
        r = dedupe.estimate([])
        assert (r.raw_count, r.unique_min, r.unique_max) == (0, 0, 0)


# ── 호가 분포 ─────────────────────────────────────────────────────────

class TestDistribution:
    ROWS = [
        listing(6.05, key="a", special_flags=["급매", "수리필요"], floor=2, dong="103"),
        listing(6.20, key="b", floor=7, dong="101"),
        listing(6.25, key="c", floor=9, dong="102"),
        listing(6.30, key="d", floor=12, dong="104"),
        listing(6.50, key="e", floor=14, dong="105"),
    ]

    def test_최저호가와_정상매물_최저호가를_따로_낸다(self):
        d = distribution.analyze(self.ROWS)
        assert d.low == units.from_eok(6.05)
        assert d.low_normal == units.from_eok(6.20)
        assert d.low_is_special is True

    def test_특수매물이_없으면_두_값이_같다(self):
        d = distribution.analyze(self.ROWS[1:])
        assert d.low == d.low_normal
        assert d.low_is_special is False

    def test_계산근거에_주의_문구가_들어간다(self):
        d = distribution.analyze(self.ROWS)
        assert "주의" in d.calc.intermediates
        assert "특수매물" in d.calc.intermediates["주의"]

    def test_분포를_전부_낸다(self):
        d = distribution.analyze(self.ROWS)
        assert d.count == 5 and d.normal_count == 4 and d.special_count == 1
        assert d.low < d.p25 <= d.median <= d.p75 < d.high

    def test_층별로_나눠_본다(self):
        d = distribution.analyze(self.ROWS)
        assert set(d.by_floor_group) <= {"저층", "중층", "고층"}
        assert d.by_floor_group["저층"]["n"] == 1

    def test_특수조건_집계(self):
        d = distribution.analyze(self.ROWS)
        assert d.special_flags["급매"] == 1
        assert d.special_flags["수리필요"] == 1

    def test_거래유형이_섞이지_않는다(self):
        rows = self.ROWS + [listing(3.7, key="j", ttype="전세")]
        assert distribution.analyze(rows, trade_type="매매").count == 5
        assert distribution.analyze(rows, trade_type="전세").count == 1


# ── 호가 vs 실거래 괴리 ───────────────────────────────────────────────

class TestGap:
    def _snapshot(self, eok):
        rows = [{"deal_amount": int(units.from_eok(eok)), "deal_ymd": "20260601",
                 "floor": 7, "deal_type": "중개거래", "cancel_yn": 0} for _ in range(3)]
        return snap_mod.build_price(rows, as_of_ym="202606")

    def test_괴리율_계산(self):
        d = distribution.analyze(TestDistribution.ROWS)
        calc = gap.analyze(d, self._snapshot(5.90), recent_trade_price=int(units.from_eok(5.93)))
        # 정상매물 최저호가 6.20 vs 대표실거래 5.90 → +5.1%
        assert calc.value == pytest.approx((6.20 - 5.90) / 5.90, abs=0.005)

    def test_특수매물_주의가_괴리_근거에도_남는다(self):
        d = distribution.analyze(TestDistribution.ROWS)
        calc = gap.analyze(d, self._snapshot(5.90))
        assert "주의" in calc.intermediates

    def test_실거래가_없으면_확인_불가로_낸다(self):
        # 0% 라고 하지 않는다. 모르는 것과 차이가 없는 것은 다르다.
        d = distribution.analyze(TestDistribution.ROWS)
        calc = gap.analyze(d, None)
        assert calc.value is None
        assert "확인 불가" in str(calc.inputs.values())


# ── 변화 감지 ─────────────────────────────────────────────────────────

class TestChange:
    BEFORE = [{"listing_key": "a", "price": units.from_eok(6.2)},
              {"listing_key": "b", "price": units.from_eok(6.3)},
              {"listing_key": "c", "price": units.from_eok(6.5)}]
    AFTER = [{"listing_key": "a", "price": units.from_eok(6.1)},   # 인하
             {"listing_key": "b", "price": units.from_eok(6.4)},   # 인상
             {"listing_key": "d", "price": units.from_eok(6.6)}]   # 신규 ('c' 이탈)

    def test_신규와_이탈과_가격변동을_구분한다(self):
        c = change.compare(self.BEFORE, self.AFTER,
                           from_date="2026-07-01", to_date="2026-08-01")
        assert c.new_keys == ["d"]
        assert c.gone_keys == ["c"]
        assert [m.listing_key for m in c.cuts] == ["a"]
        assert [m.listing_key for m in c.raises] == ["b"]

    def test_사라진_매물을_거래완료라고_부르지_않는다(self):
        c = change.compare(self.BEFORE, self.AFTER,
                           from_date="2026-07-01", to_date="2026-08-01")
        text = " ".join(c.summary_lines())
        assert "거래완료" not in text
        assert "시장 이탈" in text and "확정 불가" in text

    def test_최저호가_변화율(self):
        c = change.compare(self.BEFORE, self.AFTER,
                           from_date="2026-07-01", to_date="2026-08-01")
        assert c.low_before == units.from_eok(6.2)
        assert c.low_after == units.from_eok(6.1)
        assert c.low_delta_ratio < 0

    def test_빈_스냅샷도_에러가_아니다(self):
        c = change.compare([], [], from_date="a", to_date="b")
        assert c.count_before == 0 and c.low_delta_ratio is None


# ── 시장압력 ──────────────────────────────────────────────────────────

class TestPressure:
    def _change(self, before_n, after_n, low_before, low_after, cuts=0, raises=0):
        before = [{"listing_key": f"b{i}", "price": units.from_eok(low_before + i * 0.1)}
                  for i in range(before_n)]
        after = [{"listing_key": f"b{i}", "price": units.from_eok(low_after + i * 0.1)}
                 for i in range(after_n)]
        c = change.compare(before, after, from_date="2026-07-01", to_date="2026-08-01")
        return c

    def test_매물감소_호가상승이면_매도자우위(self):
        c = self._change(32, 21, 5.9, 6.2)
        p = pressure.build(change=c, trade_trend=0.03, jeonse_trend=0.02)
        assert p.score > 60
        assert p.direction == "매도자우위"

    def test_매물증가_호가하락이면_매수자우위(self):
        c = self._change(21, 32, 6.2, 5.9)
        p = pressure.build(change=c, trade_trend=-0.03, jeonse_trend=-0.02)
        assert p.score < 40
        assert p.direction == "매수자우위"

    def test_근거가_없으면_점수를_믿지_말라고_말한다(self):
        p = pressure.build()
        assert p.coverage == 0.0
        assert "주의" in p.calc.intermediates
        assert p.calc.grade == "ESTIMATED"

    def test_없는_구성요소는_가중치에서_빠진다(self):
        # 없는 데이터를 '중립'으로 세면 점수가 50 쪽으로 끌려간다.
        c = self._change(32, 16, 6.0, 6.0)
        only_change = pressure.build(change=c)
        with_all = pressure.build(change=c, trade_trend=0.0, jeonse_trend=0.0)
        assert only_change.coverage < with_all.coverage
        assert only_change.score > with_all.score   # 희석되지 않았다

    def test_가중치를_바꿀_수_있다(self):
        c = self._change(32, 21, 5.9, 6.2)
        base = pressure.build(change=c)
        tweaked = pressure.build(change=c, weights={"매물증감": 1.0, "최저호가": 0.0,
                                                     "중위호가": 0.0, "가격인하": 0.0})
        assert base.score != tweaked.score

    def test_구성요소마다_원값과_설명이_남는다(self):
        c = self._change(32, 21, 5.9, 6.2)
        p = pressure.build(change=c)
        for comp in p.components:
            assert comp.note
        assert p.calc.intermediates["가중치"]


# ── 저장 ──────────────────────────────────────────────────────────────

class TestPersistence:
    @pytest.fixture
    def db(self, tmp_db):
        mig.migrate(tmp_db)
        with get_conn(tmp_db) as conn:
            repo.sync_regions(conn)
            repo.upsert_complexes(conn, [{
                "kapt_code": "A1", "name": "동아1단지", "name_norm": "동아1단지",
                "lawd_cd": "28237", "emd_name": "산곡동",
                "apt_households": 1200, "approval_year": 1990,
            }])
        return tmp_db

    def test_같은_매물을_다시_넣으면_갱신된다(self, db):
        rows = [listing(6.2, key="a")]
        with get_conn(db) as conn:
            first = listing_repo.upsert_listings(conn, rows)
            rows[0]["price"] = int(units.from_eok(6.05))
            rows[0]["last_seen_at"] = "2026-08-15"
            second = listing_repo.upsert_listings(conn, rows)
            row = conn.execute("SELECT * FROM listing").fetchone()
        assert (first["new"], second["new"]) == (1, 0)
        assert row["price"] == units.from_eok(6.05)
        assert row["first_seen_at"] == "2026-08-01"   # 최초 발견일은 유지
        assert row["last_seen_at"] == "2026-08-15"

    def test_안_보이는_매물은_비활성이_되지만_지워지지_않는다(self, db):
        with get_conn(db) as conn:
            listing_repo.upsert_listings(conn, [listing(6.2, key="a")])
            n = listing_repo.deactivate_missing(conn, "manual", "2026-08-15")
            row = conn.execute("SELECT is_active FROM listing").fetchone()
        assert n == 1 and row["is_active"] == 0

    def test_일별_스냅샷은_같은_날_두_번_넣어도_하나(self, db):
        with get_conn(db) as conn:
            listing_repo.save_daily_snapshot(conn, [listing(6.2, key="a")], "2026-08-01")
            listing_repo.save_daily_snapshot(conn, [listing(6.2, key="a")], "2026-08-01")
            assert conn.execute("SELECT COUNT(*) FROM listing_snapshot").fetchone()[0] == 1

    def test_시장압력이_구성요소와_함께_저장된다(self, db):
        c = change.compare(
            [{"listing_key": "a", "price": units.from_eok(6.2)}],
            [{"listing_key": "a", "price": units.from_eok(6.3)}],
            from_date="2026-07-01", to_date="2026-08-01")
        p = pressure.build(change=c)
        with get_conn(db) as conn:
            cid = conn.execute("SELECT id FROM complex").fetchone()[0]
            listing_repo.save_pressure(conn, complex_id=cid, area_band="84",
                                       as_of_date="2026-08-01", window_days=30, pressure=p)
            row = conn.execute("SELECT * FROM market_pressure").fetchone()
        assert row["score"] == p.score
        assert json.loads(row["components_json"])[0]["key"] == "매물증감"


# ── 현장 확인값 (요구사항 46) ────────────────────────────────────────

class TestFieldNote:
    @pytest.fixture
    def db(self, tmp_db):
        mig.migrate(tmp_db)
        with get_conn(tmp_db) as conn:
            repo.sync_regions(conn)
            repo.upsert_complexes(conn, [{
                "kapt_code": "A1", "name": "동아1단지", "name_norm": "동아1단지",
                "lawd_cd": "28237", "apt_households": 1200}])
        return tmp_db

    def test_협상가는_호가와_다른_테이블에_들어간다(self, db):
        with get_conn(db) as conn:
            cid = conn.execute("SELECT id FROM complex").fetchone()[0]
            listing_repo.upsert_listings(conn, [listing(6.2, key="a")])
            listing_repo.add_field_note(
                conn, complex_id=cid, area_band="84", noted_on="2026-08-30",
                kind="중개사확인", price=int(units.from_eok(6.05)),
                note="6.05억이면 맞출 수 있다고 함", source="○○공인 김실장")

            listings = conn.execute("SELECT price FROM listing").fetchall()
            notes = listing_repo.field_notes(conn, cid)

        assert [r[0] for r in listings] == [units.from_eok(6.2)]   # 호가는 그대로 6.2억
        assert notes[0]["price"] == units.from_eok(6.05)           # 협상가는 따로
        assert notes[0]["kind"] == "중개사확인"

    def test_출처가_없으면_거부된다(self, db):
        import sqlite3
        with get_conn(db) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                listing_repo.add_field_note(
                    conn, complex_id=1, area_band="84", noted_on="2026-08-30",
                    kind="협상가", note="싸게 된다더라", source="  ")
