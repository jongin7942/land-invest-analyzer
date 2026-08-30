"""계산 추적(Calc) 규약 — 이 엔진의 뼈대.

요구사항: 모든 계산은 "입력값 → 계산식 → 결과값 → 근거"가 추적 가능해야 한다.

기존 `pipeline.py` 의 `score` 는 최종 숫자만 남아 어느 항목이 몇 점 기여했는지
재현할 수 없다. `analysis/narrative.py` 가 나중에 DB 필드로 문장을 다시 만들지만
그건 재현이 아니라 재서술이라, 산식이 바뀌면 저장된 점수와 설명이 조용히 어긋난다.

그래서 **모든 엔진 함수는 값이 아니라 Calc 를 반환한다.** Calc 는 그대로 JSON 으로
직렬화돼 파생 테이블의 `calc_trace` 컬럼에 저장되고, 화면의 "왜 이 숫자인가"와
리포트 문장이 전부 여기서 나온다.

등급(grade)은 세 가지다 — 화면의 ● 확정 / ◐ 추정 / △ 시나리오 에 그대로 대응한다.

    CONFIRMED  실거래·공식자료에서 직접 온 값
    ESTIMATED  우리가 추정한 값 (배율·근사·보간)
    SCENARIO   가정에 기반한 값 (Bear/Base/Bull, 공사비 시나리오 등)

여러 값을 합성하면 **가장 약한 등급이 결과의 등급**이 된다. 확정값 하나와
시나리오값 하나를 더한 결과는 시나리오다. `Calc.derive()` 가 이걸 자동으로 한다 —
"추정치를 확정값처럼 표시하지 말 것"을 사람의 주의력이 아니라 코드가 지킨다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping

from apt_engine import ENGINE_VERSION

Grade = Literal["CONFIRMED", "ESTIMATED", "SCENARIO"]

# 강한 순서. 인덱스가 클수록 약하다.
GRADES: tuple[Grade, ...] = ("CONFIRMED", "ESTIMATED", "SCENARIO")


def weakest(*grades: Grade) -> Grade:
    """합성 결과의 등급. 하나라도 SCENARIO 면 결과는 SCENARIO."""
    if not grades:
        return "CONFIRMED"
    for g in grades:
        if g not in GRADES:
            raise ValueError(f"알 수 없는 등급: {g!r} (가능한 값: {', '.join(GRADES)})")
    return max(grades, key=GRADES.index)


@dataclass(frozen=True)
class Evidence:
    """숫자 하나를 뒷받침하는 출처.

    요구사항 5: "AI가 이유 없이 숫자를 만들어내면 안 된다." 촉매·미래비율처럼
    근거가 필수인 값은 evidence 가 비면 저장 자체를 거부한다(PHASE 5).
    """
    source: str                      # 예: "국토부 아파트 매매 실거래가"
    url: str | None = None
    quote: str | None = None         # 원문 인용(법령 조문, 고시 문구 등)
    effective_date: str | None = None  # 그 근거가 유효한 기준일 YYYY-MM-DD
    retrieved_at: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Evidence":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass(frozen=True)
class Calc:
    """계산 결과 + 그 결과가 나온 경위 전부.

    frozen 이라 만들어진 뒤엔 못 바꾼다 — 저장된 trace 와 값이 어긋나는 걸 막는다.
    inputs/intermediates 는 읽기 전용 매핑으로 감싸 dict 를 나중에 건드리는 것도 막는다.
    """
    value: Any
    unit: str                        # "원", "㎡", "ratio", "%p", "년", "점" 등
    formula: str                     # 사람이 읽는 계산식
    inputs: Mapping[str, Any] = field(default_factory=dict)
    intermediates: Mapping[str, Any] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    grade: Grade = "CONFIRMED"
    engine_version: str = ENGINE_VERSION

    def __post_init__(self) -> None:
        if self.grade not in GRADES:
            raise ValueError(f"알 수 없는 등급: {self.grade!r} (가능한 값: {', '.join(GRADES)})")
        # frozen 이라 object.__setattr__ 로 정규화한다.
        object.__setattr__(self, "inputs", MappingProxyType(dict(self.inputs)))
        object.__setattr__(self, "intermediates", MappingProxyType(dict(self.intermediates)))
        object.__setattr__(self, "evidence", tuple(self.evidence))

    # ── 합성 ──────────────────────────────────────────────────────────

    @classmethod
    def derive(
        cls,
        value: Any,
        *,
        unit: str,
        formula: str,
        sources: Mapping[str, "Calc"] | None = None,
        inputs: Mapping[str, Any] | None = None,
        intermediates: Mapping[str, Any] | None = None,
        evidence: Iterable[Evidence] = (),
        grade: Grade | None = None,
        engine_version: str | None = None,
    ) -> "Calc":
        """다른 Calc 들로부터 새 Calc 를 만든다.

        sources 로 넘긴 Calc 들에서 자동으로:
          * 값을 inputs 에 이름째로 옮기고,
          * evidence 를 순서 유지·중복 제거해 합치고,
          * **등급을 가장 약한 것으로 낮춘다**(grade 를 명시하면 그걸 쓴다).
        """
        srcs = dict(sources or {})
        merged_inputs: dict[str, Any] = {name: c.value for name, c in srcs.items()}
        merged_inputs.update(dict(inputs or {}))

        merged_ev: list[Evidence] = []
        for c in srcs.values():
            merged_ev.extend(c.evidence)
        merged_ev.extend(evidence)
        deduped = list(dict.fromkeys(merged_ev))  # 순서 유지 중복 제거

        resolved = grade if grade is not None else weakest(*(c.grade for c in srcs.values()))
        return cls(
            value=value,
            unit=unit,
            formula=formula,
            inputs=merged_inputs,
            intermediates=dict(intermediates or {}),
            evidence=tuple(deduped),
            grade=resolved,
            engine_version=engine_version or ENGINE_VERSION,
        )

    # ── 직렬화 ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "formula": self.formula,
            "inputs": dict(self.inputs),
            "intermediates": dict(self.intermediates),
            "evidence": [e.to_dict() for e in self.evidence],
            "grade": self.grade,
            "engine_version": self.engine_version,
        }

    def to_json(self) -> str:
        """`calc_trace` 컬럼에 그대로 넣는 형태."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Calc":
        return cls(
            value=d["value"],
            unit=d["unit"],
            formula=d["formula"],
            inputs=d.get("inputs") or {},
            intermediates=d.get("intermediates") or {},
            evidence=tuple(Evidence.from_dict(e) for e in d.get("evidence") or ()),
            grade=d.get("grade", "CONFIRMED"),
            engine_version=d.get("engine_version", ENGINE_VERSION),
        )

    @classmethod
    def from_json(cls, text: str) -> "Calc":
        return cls.from_dict(json.loads(text))

    # ── 사람이 읽는 형태 ───────────────────────────────────────────────

    def explain(self) -> str:
        """디버깅·리포트용 다중행 설명. 값 포맷은 표시 계층(units)이 담당하므로
        여기서는 원값을 그대로 보여준다."""
        lines = [f"값: {self.value} {self.unit}  [{self.grade}]", f"식: {self.formula}"]
        if self.inputs:
            lines.append("입력:")
            lines += [f"  {k} = {v}" for k, v in self.inputs.items()]
        if self.intermediates:
            lines.append("중간값:")
            lines += [f"  {k} = {v}" for k, v in self.intermediates.items()]
        if self.evidence:
            lines.append("근거:")
            for e in self.evidence:
                tail = f" ({e.url})" if e.url else ""
                lines.append(f"  - {e.source}{tail}")
        return "\n".join(lines)
