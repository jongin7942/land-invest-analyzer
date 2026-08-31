"""Feature 계약 — 값과 신뢰도를 절대 합치지 않는다 (지시서 §50·§67·§71).

지시서 §50:

> Score 와 Confidence 를 절대로 합치지 않는다.
> Score 92 / Confidence 41 이면 "좋아 보이지만 데이터가 약한 후보" 다.

그래서 Feature 는 값 하나가 아니라 **네 가지**를 들고 다닌다.

    value        숫자 (없으면 None — 0 이 아니다)
    confidence   0~1. 표본수·최신성·출처품질에서 나온다
    status       OK / DATA_MISSING / LOW_CONFIDENCE / NEEDS_VERIFICATION
    calc         어떻게 나온 값인지 (Calc 추적)

그리고 §71 Ablation Test 를 위해 **이름으로 끌 수 있어야** 한다.
FeatureSet.without("catalyst") 로 그 feature 를 뺀 사본이 나온다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable

from apt_engine.trace import Calc


class Status(str, Enum):
    OK = "OK"
    DATA_MISSING = "DATA_MISSING"              # 계산할 데이터가 없다
    LOW_CONFIDENCE = "LOW_CONFIDENCE"          # 계산은 됐지만 표본이 약하다
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"  # 사람이 확인해야 하는 입력에 의존


# 이 아래면 값이 있어도 신뢰하지 않는다. 백테스트로 바꿀 수 있게 상수로 둔다.
LOW_CONFIDENCE_BELOW = 0.35


@dataclass(frozen=True)
class Feature:
    key: str
    value: float | None
    unit: str = ""
    confidence: float = 0.0
    status: Status = Status.DATA_MISSING
    detail: dict = field(default_factory=dict)
    calc: Calc | None = None

    @property
    def known(self) -> bool:
        return self.value is not None and self.status is not Status.DATA_MISSING

    @property
    def usable(self) -> bool:
        """랭킹에 쓸 수 있는가. 값이 있어도 신뢰도가 바닥이면 쓰지 않는다."""
        return self.known and self.confidence >= LOW_CONFIDENCE_BELOW

    @property
    def label(self) -> str:
        if not self.known:
            return f"{self.status.value}"
        unit = self.unit or ""
        shown = f"{self.value:,.4g}{unit}"
        return f"{shown}  (신뢰도 {self.confidence:.0%}{'' if self.usable else ' · 낮음'})"

    @classmethod
    def missing(cls, key: str, reason: str, *, unit: str = "") -> "Feature":
        """계산하지 못했다. **0 으로 만들지 않는다.**"""
        return cls(key=key, value=None, unit=unit, confidence=0.0,
                   status=Status.DATA_MISSING, detail={"사유": reason})

    def with_confidence(self, confidence: float) -> "Feature":
        status = self.status
        if self.known:
            status = (Status.OK if confidence >= LOW_CONFIDENCE_BELOW
                      else Status.LOW_CONFIDENCE)
        return replace(self, confidence=max(0.0, min(1.0, confidence)), status=status)


@dataclass(frozen=True)
class FeatureSet:
    """한 후보의 Feature 묶음."""
    complex_id: int
    area_band: str
    as_of: str
    items: dict[str, Feature] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Feature:
        return self.items.get(key, Feature.missing(key, "계산되지 않음"))

    def __contains__(self, key: str) -> bool:
        return key in self.items

    def add(self, feature: Feature) -> "FeatureSet":
        return replace(self, items={**self.items, feature.key: feature})

    def without(self, *keys: str) -> "FeatureSet":
        """§71 Ablation — 이 feature 들을 뺀 사본. 원본은 그대로 둔다."""
        dropped = set(keys)
        return replace(self, items={k: v for k, v in self.items.items()
                                    if k not in dropped})

    @property
    def usable_keys(self) -> list[str]:
        return sorted(k for k, f in self.items.items() if f.usable)

    @property
    def missing_keys(self) -> list[str]:
        return sorted(k for k, f in self.items.items() if not f.known)

    @property
    def coverage(self) -> float:
        """쓸 수 있는 feature 비율. §50 Confidence 의 재료 중 하나."""
        return len(self.usable_keys) / len(self.items) if self.items else 0.0

    @property
    def summary(self) -> dict:
        return {k: f.label for k, f in sorted(self.items.items())}


# ── 신뢰도 계산 ────────────────────────────────────────────────────────

def sample_confidence(n: int, *, full_at: int = 10) -> float:
    """표본수 → 신뢰도. 1건이면 낮고, full_at 건이면 1.0.

    거래 1건으로 계산한 값을 10건으로 계산한 값과 같은 무게로 두면,
    표본이 적은 단지가 극단값 때문에 상위에 올라온다.
    """
    if n <= 0:
        return 0.0
    return min(1.0, n / float(full_at))


def freshness_confidence(months_old: float, *, half_life: float = 6.0) -> float:
    """최신성 → 신뢰도. 오래된 값일수록 낮다.

    half_life 개월마다 절반이 된다. 6개월 전 대표가격은 지금 시세가 아니다.
    """
    if months_old < 0:
        return 1.0
    return 0.5 ** (months_old / half_life)


def combine(*confidences: float, weights: Iterable[float] | None = None) -> float:
    """여러 신뢰도를 합친다. **가장 약한 것에 끌려간다.**

    산술평균을 쓰면 '표본 1건 + 최신 데이터' 가 '표본 10건 + 6개월 전' 과 같아진다.
    실제로는 전자가 훨씬 위험하므로 기하평균을 쓴다.
    """
    values = [max(0.0, min(1.0, c)) for c in confidences if c is not None]
    if not values:
        return 0.0
    if any(v == 0 for v in values):
        return 0.0
    ws = list(weights) if weights is not None else [1.0] * len(values)
    total = sum(ws)
    product = 1.0
    for v, w in zip(values, ws):
        product *= v ** (w / total)
    return product
