"""Kill Score — 좋은 점수가 많아도 이건 제거한다 (지시서 §45).

> 높은 Kill Score 는 높은 Base Score 보다 중요할 수 있다.

점수를 더하는 방식으로는 이걸 표현할 수 없다. 아홉 모델이 다 좋다고 해도
공급 폭탄 하나면 그 투자는 실패한다. 그래서 Kill 은 **감점이 아니라 배제**다.

각 위험은 독립적으로 판정되고, 하나라도 임계를 넘으면 그 사유가 남는다.
지시서 §65 가 "탈락 이유를 보여줘라" 고 했으므로, 제거된 후보도 사라지지 않고
이유와 함께 목록에 남는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apt_engine.features.base import FeatureSet

# 이 값을 넘으면 TOP10 에서 뺀다. **판정 기준**이라 백테스트가 대체한다.
KILL_THRESHOLD = 0.60

# 위험 하나하나의 판정 기준. 전부 "이 정도면 위험하다" 는 가정이다.
RULES: dict[str, dict] = {
    "공급충격": {"feature": "supply_ratio_2y", "over": 0.08,
             "why": "향후 2년 입주물량이 기존 stock 대비 8%를 넘습니다"},
    "전세약화": {"feature": "downside_defense", "under": 0.15,
             "why": "전세가 매매를 받쳐 주지 못해 하방이 깊습니다"},
    "급등후매수": {"feature": "discovery_lag", "over": 0.70,
              "why": "이미 크게 오른 뒤입니다 — 늦게 발견한 Winner 입니다(§40)"},
    "호재선반영": {"feature": "catalyst_priced_in", "over": 0.85,
              "why": "호재가 대부분 가격에 반영됐습니다"},
    "상대고평가": {"feature": "entry_position", "over": 1.0,
              "why": "매수가 구간의 Wait 선을 넘었습니다"},
    "거래고점": {"feature": "flow_stage", "over": 4.5,
             "why": "과열/고점 회전 국면입니다"},
    "거래질악화": {"feature": "transaction_quality", "under": 0.25,
              "why": "저층·소수 거래뿐이라 가격 신호를 믿기 어렵습니다"},
}

THRESHOLD_NOTE = ("Kill 기준은 관측된 분포가 아니라 판정 기준입니다. "
                  "백테스트(§55)가 '이 조건이 실제로 손실과 연결됐는가' 로 대체합니다")


@dataclass(frozen=True)
class Killed:
    reason: str
    feature_key: str
    value: float
    why: str


@dataclass(frozen=True)
class KillScore:
    value: float                       # 0~1
    hits: list[Killed] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)

    @property
    def killed(self) -> bool:
        return self.value >= KILL_THRESHOLD

    @property
    def label(self) -> str:
        if not self.hits:
            base = "위험 없음"
        else:
            base = " · ".join(h.reason for h in self.hits)
        tail = "  → TOP10 제외" if self.killed else ""
        if self.unchecked:
            tail += f"  (미확인 {len(self.unchecked)}개)"
        return f"{self.value:.2f}  {base}{tail}"


def evaluate(features: FeatureSet) -> KillScore:
    """위험 하나하나를 독립적으로 본다. **확인 못 한 위험은 '없음' 이 아니다.**"""
    hits: list[Killed] = []
    unchecked: list[str] = []

    for reason, rule in RULES.items():
        f = features[rule["feature"]]
        if not f.usable:
            unchecked.append(reason)
            continue
        over, under = rule.get("over"), rule.get("under")
        if over is not None and f.value > over:
            hits.append(Killed(reason, rule["feature"], f.value, rule["why"]))
        elif under is not None and f.value < under:
            hits.append(Killed(reason, rule["feature"], f.value, rule["why"]))

    checked = len(RULES) - len(unchecked)
    if checked == 0:
        # 아무것도 확인하지 못했다면 "안전" 이 아니라 "모른다" 다.
        return KillScore(0.0, [], unchecked)

    value = len(hits) / checked
    return KillScore(value, hits, unchecked)
