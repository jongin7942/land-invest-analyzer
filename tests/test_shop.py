"""상가/개원 입지 모듈 오프라인 테스트 (네트워크 불필요).
실행: python -m unittest tests.test_shop -v
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "naver_shop_mobile_sample.json"


class ParserTests(unittest.TestCase):
    def test_money(self):
        from collectors.naver_shop import parse_korean_money as m
        self.assertEqual(m("1억 5,000"), 15000)
        self.assertEqual(m("5,000"), 5000)
        self.assertEqual(m(300), 300)
        self.assertEqual(m("3억"), 30000)
        self.assertIsNone(m(""))
        self.assertIsNone(m(None))

    def test_floor(self):
        from collectors.naver_shop import parse_floor, floor_band
        self.assertEqual(parse_floor("2/5"), (2, 5))
        self.assertEqual(parse_floor("B1/5"), (-1, 5))
        self.assertEqual(parse_floor("고/15"), (None, 15))
        self.assertEqual(floor_band(-1), "지하")
        self.assertEqual(floor_band(1), "1층")
        self.assertEqual(floor_band(2), "2층")
        self.assertEqual(floor_band(7), "3층이상")
        self.assertEqual(floor_band(None, "고/15"), "3층이상")

    def test_normalize_mobile_and_new_shapes(self):
        from collectors.naver_shop import normalize_article
        reg = {"cortar_no": "1168010100", "sido": "서울특별시", "sgg": "강남구", "umd": "역삼동"}
        mobile = {"atclNo": "1", "tradTpNm": "월세", "rletTpNm": "상가", "prc": 5000, "rentPrc": 300,
                  "flrInfo": "2/5", "spc2": 99.2, "atclCfmYmd": "25.09.20.", "tagList": ["엘리베이터"]}
        n = normalize_article(mobile, reg)
        self.assertEqual((n["deposit"], n["rent"], n["sale_price"]), (5000, 300, None))
        self.assertEqual(n["floor_band"], "2층")
        self.assertEqual(n["confirm_ymd"], "20250920")
        self.assertEqual(n["umd"], "역삼동")
        new = {"articleNo": "2", "tradeTypeName": "매매", "realEstateTypeName": "상가",
               "dealOrWarrantPrc": "25억", "floorInfo": "1/6", "area2": "66.1", "articleConfirmYmd": "20250920"}
        n = normalize_article(new, reg)
        self.assertEqual((n["deposit"], n["sale_price"]), (None, 250000))
        self.assertEqual(n["area_exclusive"], 66.1)
        self.assertIsNone(normalize_article({"foo": 1}, reg))


class AnalysisTests(unittest.TestCase):
    def test_premium(self):
        from analysis.premium import parse_premium, vacancy_hint
        self.assertEqual(parse_premium({"feature_desc": "무권리 즉시입주"}), ("없음", 0))
        self.assertEqual(parse_premium({"feature_desc": "권리금 3,000만원 협의"}), ("있음", 3000))
        self.assertEqual(parse_premium({"feature_desc": "권리금 1억 5000 시설권리"}), ("있음", 15000))
        self.assertEqual(parse_premium({"feature_desc": "권리금 협의"}), ("있음", None))
        self.assertEqual(parse_premium({"feature_desc": "역세권 코너"}), ("미상", None))
        self.assertEqual(vacancy_hint({"feature_desc": "무권리 즉시입주"}), 1)
        self.assertEqual(vacancy_hint({"feature_desc": "현 카페 운영중"}), 0)

    def test_medical_flags(self):
        from analysis.medical_fit import medical_flags as f
        self.assertIn("병원양도", f({"feature_desc": "현 피부과 운영중 양도 권리금 협의"}))
        self.assertIn("병원양도", f({"feature_desc": "현 내과 통째 매매 장비 포함"}))
        self.assertEqual(f({"feature_desc": "전 치과 자리 인테리어 승계 가능"}), ["전병원자리"])
        self.assertEqual(f({"feature_desc": "기존 소아과 폐업 후 공실"}), ["전병원자리"])
        # 건물명이 메디컬타워인 편의점은 양도 매물이 아니다
        self.assertEqual(f({"feature_desc": "현 편의점 영업중", "building_name": "메디컬타워"}), ["메디컬빌딩"])
        self.assertEqual(f({"feature_desc": "역세권 코너 학원 운영중"}), [])
        self.assertIn("의료가능", f({"feature_desc": "병원 가능 정화조 전기 증설 완료"}))

    def test_converted_rent_and_baseline(self):
        from analysis import rent_baseline as rb
        l = {"trade_type": "월세", "deposit": 5000, "rent": 300, "area_exclusive": 132.2,
             "sido": "S", "sgg": "G", "umd": "U", "floor_band": "2층", "status": "active"}
        rb.enrich(l)
        self.assertAlmostEqual(l["conv_rent"], 325.0)
        self.assertAlmostEqual(l["rent_per_py"], 8.13, places=2)
        rows = []
        for i in range(6):
            r = dict(l); r["rent"] = 300 + i * 10; rb.enrich(r); rows.append(r)
        bl = rb.build_baselines(rows, min_samples=4)
        self.assertIn(("U", "2층"), bl["L1"])
        a = rb.assess(rows[0], bl)
        self.assertEqual(a["label"], "적정")
        est = rb.estimate_monthly_cost(bl, "S", "G", "U", "2층", 50)
        self.assertIsNotNone(est)
        # 월세 + 보증금×전환율/12 == 환산월세 총액
        self.assertAlmostEqual(est["rent_est"] + est["deposit_est"] * rb.config.RENT_CONVERSION_RATE / 12,
                               est["conv_rent"], places=6)

    def test_activity_profile(self):
        from analysis.premium import activity_profile
        ls = [{"trade_type": "월세", "premium": "있음", "vacancy_hint": 0, "first_seen": "2026-01-01 00:00:00"}] * 8 \
           + [{"trade_type": "월세", "premium": "없음", "vacancy_hint": 1, "first_seen": "2026-01-01 00:00:00"}] * 2
        p = activity_profile(ls, now_iso="2026-01-10T00:00:00")
        self.assertEqual(p["n"], 10)
        self.assertAlmostEqual(p["premium_ratio"], 0.8)
        self.assertEqual(p["label"], "활성")
        self.assertEqual(activity_profile([])["label"], "판단보류")


class PipelineTests(unittest.TestCase):
    """임시 SQLite 로 fixture → 적재 → 기준선 → 개원노트 전 과정."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DB_PATH"] = str(Path(self.tmp.name) / "t.db")
        import importlib, config
        importlib.reload(config)
        from db import schema
        schema.init_db()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DB_PATH", None)

    def test_end_to_end(self):
        from collectors import naver_shop
        from db import shop as shopdb
        from analysis import clinic_note
        import shop_pipeline as sp
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        regs = {"1168010100": {"cortar_no": "1168010100", "sido": "서울특별시", "sgg": "강남구", "umd": "역삼동"},
                "1168010800": {"cortar_no": "1168010800", "sido": "서울특별시", "sgg": "강남구", "umd": "논현동"}}
        rows = [naver_shop.normalize_article(it, regs[it["cortarNo"]], "fixture") for it in data["body"]]
        sp._analyze_rows(rows)
        st = shopdb.upsert_listings(rows)
        self.assertEqual(st["new"], len(rows))
        # 재수집: 가격 변동 1건, 소멸 3건
        rows[0]["rent"] -= 20
        st2 = shopdb.upsert_listings(rows[:-3])
        self.assertEqual(st2["price_changed"], 1)
        self.assertEqual(len(shopdb.price_history(rows[0]["article_no"])), 2)
        self.assertEqual(shopdb.mark_gone("1168010800", {r["article_no"] for r in rows[:-3]}), 3)
        self.assertEqual(shopdb.listing_stats()["active"], len(rows) - 3)

        shopdb.upsert_clinics([{"ykiho": "Y1", "name": "서울피부과의원", "cl_cd": "31", "cl_name": "의원",
                                "sggu_name": "강남구", "emdong_name": "역삼동", "lat": 37.5006, "lon": 127.0366}])
        md = clinic_note.build_note("강남구", "역삼동", dept="피부과", pyeong=50, top_n=3)
        self.assertIn("# 개원노트 · 강남구 역삼동", md)
        self.assertIn("## 2. 임대료 기준선", md)
        self.assertIn("병원양도", md)
        self.assertIn("동일과(피부과)", md)
        self.assertIn("## 6. 개원 후보 매물", md)


if __name__ == "__main__":
    unittest.main()
