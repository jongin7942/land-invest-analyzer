"""Calc 추적 규약 테스트.

핵심은 등급 전파다 — "추정치를 확정값처럼 표시하지 말 것"(요구사항 26-12)을
사람의 주의력이 아니라 코드가 지키는지 확인한다.
"""
import json

import pytest

from apt_engine import ENGINE_VERSION
from apt_engine.trace import Calc, Evidence, weakest


def price(value=1_120_000_000, grade="CONFIRMED"):
    return Calc(value=value, unit="원", formula="정상거래 median", grade=grade)


class TestGradePropagation:
    def test_가장_약한_등급이_이긴다(self):
        assert weakest("CONFIRMED", "CONFIRMED") == "CONFIRMED"
        assert weakest("CONFIRMED", "ESTIMATED") == "ESTIMATED"
        assert weakest("CONFIRMED", "SCENARIO") == "SCENARIO"
        assert weakest("ESTIMATED", "SCENARIO") == "SCENARIO"

    def test_입력이_없으면_확정(self):
        assert weakest() == "CONFIRMED"

    def test_모르는_등급은_거부(self):
        with pytest.raises(ValueError, match="알 수 없는 등급"):
            weakest("확정")

    def test_derive_는_등급을_자동으로_낮춘다(self):
        # 확정된 매매가 + 시나리오로 가정한 분담금 → 결과는 시나리오다.
        result = Calc.derive(
            1_320_000_000,
            unit="원",
            formula="매매가 + 추가분담금",
            sources={
                "매매가": price(grade="CONFIRMED"),
                "추가분담금": Calc(value=200_000_000, unit="원",
                                   formula="용적률 300% · 공사비 평당 1,000만원 가정",
                                   grade="SCENARIO"),
            },
        )
        assert result.grade == "SCENARIO"

    def test_명시한_등급이_자동판정을_이긴다(self):
        result = Calc.derive(1, unit="점", formula="f",
                             sources={"a": price(grade="SCENARIO")},
                             grade="ESTIMATED")
        assert result.grade == "ESTIMATED"


class TestDerive:
    def test_소스의_값이_inputs_로_옮겨온다(self):
        result = Calc.derive(
            490_000_000,
            unit="원",
            formula="매매가 - 승계전세 + 취득비용",
            sources={"매매가": price(1_120_000_000), "전세": price(660_000_000)},
            inputs={"취득비용": 30_000_000},
        )
        assert result.inputs == {
            "매매가": 1_120_000_000, "전세": 660_000_000, "취득비용": 30_000_000,
        }

    def test_근거는_합쳐지고_중복은_제거된다(self):
        molit = Evidence(source="국토부 실거래가", url="https://data.go.kr")
        kapt = Evidence(source="K-apt 단지정보")
        a = Calc(value=1, unit="원", formula="a", evidence=(molit,))
        b = Calc(value=2, unit="원", formula="b", evidence=(molit, kapt))

        result = Calc.derive(3, unit="원", formula="a+b", sources={"a": a, "b": b})
        assert result.evidence == (molit, kapt)  # 순서 유지, 중복 1건 제거

    def test_소스가_없으면_확정_등급(self):
        assert Calc.derive(1, unit="원", formula="상수").grade == "CONFIRMED"


class TestImmutability:
    def test_필드를_바꿀_수_없다(self):
        c = price()
        with pytest.raises(Exception):
            c.value = 999

    def test_inputs_딕셔너리도_못_바꾼다(self):
        # 저장된 trace 와 값이 조용히 어긋나는 걸 막는다.
        c = Calc(value=1, unit="원", formula="f", inputs={"a": 1})
        with pytest.raises(TypeError):
            c.inputs["a"] = 2

    def test_생성_후_원본_딕셔너리를_바꿔도_영향_없다(self):
        src = {"a": 1}
        c = Calc(value=1, unit="원", formula="f", inputs=src)
        src["a"] = 999
        assert c.inputs["a"] == 1


class TestSerialization:
    def test_json_왕복(self):
        original = Calc.derive(
            490_000_000,
            unit="원",
            formula="매매가 - 승계전세 + 취득비용",
            sources={"매매가": price()},
            intermediates={"전세가율": 0.589},
            evidence=[Evidence(source="국토부 실거래가", url="https://data.go.kr",
                               effective_date="2026-08-01")],
            grade="ESTIMATED",
        )
        restored = Calc.from_json(original.to_json())

        assert restored.value == original.value
        assert restored.grade == "ESTIMATED"
        assert restored.formula == original.formula
        assert dict(restored.inputs) == dict(original.inputs)
        assert dict(restored.intermediates) == dict(original.intermediates)
        assert restored.evidence == original.evidence

    def test_한글이_이스케이프되지_않는다(self):
        # DB에 들어간 calc_trace 를 사람이 그대로 읽을 수 있어야 한다.
        text = price().to_json()
        assert "정상거래 median" in text

    def test_엔진버전이_함께_저장된다(self):
        # 산식이 바뀌었을 때 재계산 대상을 골라내는 근거.
        assert json.loads(price().to_json())["engine_version"] == ENGINE_VERSION

    def test_근거의_빈_필드는_직렬화에서_빠진다(self):
        d = Evidence(source="K-apt").to_dict()
        assert d == {"source": "K-apt"}


class TestValidation:
    def test_모르는_등급으로는_만들_수_없다(self):
        with pytest.raises(ValueError, match="알 수 없는 등급"):
            Calc(value=1, unit="원", formula="f", grade="확정")


class TestExplain:
    def test_사람이_읽는_설명에_값_식_입력_근거가_다_들어간다(self):
        c = Calc.derive(
            490_000_000,
            unit="원",
            formula="매매가 - 승계전세",
            sources={"매매가": price()},
            evidence=[Evidence(source="국토부 실거래가", url="https://data.go.kr")],
            grade="ESTIMATED",
        )
        text = c.explain()
        assert "490000000 원" in text
        assert "[ESTIMATED]" in text
        assert "매매가 - 승계전세" in text
        assert "국토부 실거래가" in text
