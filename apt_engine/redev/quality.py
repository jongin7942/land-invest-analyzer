"""완공 후 품질 반영률 — Quality Capture Ratio (지시서 §27).

**무엇을 계산하나**

리모델링·재건축이 끝나면 주변 신축 가격의 **몇 %까지** 받을 수 있는가.

    완공 후 예상가치 = 주변 대표 신축의 미래가격 × 품질 반영률

**왜 90% 를 고정으로 쓰면 안 되는가**

리모델링은 새 아파트가 아니다. 기존 구조체를 남기기 때문에 지하주차장
연결, 동간거리, 평면 효율, 단지 배치에서 신축을 따라가지 못하는 부분이
남는다. 그런데 그 격차는 단지마다 다르다 — 대지가 넉넉하고 세대수가
많은 단지는 신축에 가깝고, 좁은 대지에 동을 그대로 두는 단지는 멀다.

90% 를 고정값으로 박으면 그 차이가 통째로 사라지고, **결과적으로
사업성이 나쁜 단지의 기대가치를 부풀린다.** 그래서 요소별로 감점해서
범위를 만든다.

**범위로 내는 이유 (§49-16)**

품질 반영률은 관측이 아니라 가정이다. 하나의 숫자로 확정해 저장하면
그 다음부터 아무도 그게 가정이었다는 것을 기억하지 못한다. 그래서
낙관/기본/보수 세 개를 같이 들고 다닌다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 사업 방식별 출발점. 재건축은 새로 짓는 것이라 신축과 같은 선에서 출발하고,
# 리모델링은 구조체를 남기므로 시작부터 깎고 들어간다.
BASE_RATIO = {
    "재건축": 1.00,
    "리모델링": 0.88,
}

# 요소별 감점 폭. 각 항목은 (감점, 왜) 다.
#
# 값의 근거는 관측이 아니라 **가정**이다. 백테스트로 바꿔야 할 값이고,
# 지금은 "이 요소가 신축 대비 얼마나 깎이는가" 에 대한 상식적 초기값이다.
PENALTY = {
    "지하주차_부족":   (0.05, "지하주차장을 새로 못 파거나 세대당 대수가 모자람"),
    "동간거리_협소":   (0.04, "기존 동 배치를 그대로 써서 동간거리가 좁음"),
    "평면_비효율":     (0.04, "구조체 제약으로 평면이 신축만 못함"),
    "단지규모_작음":   (0.03, "세대수가 적어 커뮤니티·관리비 규모의 경제가 안 됨"),
    "브랜드_약함":     (0.02, "시공 브랜드가 주변 신축보다 약함"),
    "커뮤니티_부족":   (0.02, "커뮤니티 시설을 넣을 공간이 부족"),
    "필로티_불가":     (0.02, "1층 필로티·조경 개선이 어려움"),
}

# 범위 폭. 이 값 자체가 "우리가 얼마나 모르는가" 다.
SPREAD = 0.06


@dataclass(frozen=True)
class CaptureRatio:
    base: float | None
    ratio: float | None
    optimistic: float | None
    pessimistic: float | None
    method: str
    applied: dict[str, str] = field(default_factory=dict)
    reason: str | None = None

    @property
    def known(self) -> bool:
        return self.ratio is not None

    @property
    def label(self) -> str:
        if not self.known:
            return f"확인 불가 — {self.reason}"
        return (f"{self.ratio:.0%} "
                f"(보수 {self.pessimistic:.0%} ~ 낙관 {self.optimistic:.0%})")


def capture_ratio(*, method: str,
                  factors: dict[str, bool] | None = None) -> CaptureRatio:
    """품질 반영률.

    `factors` 는 감점 요소별 True/False. **모르면 넣지 않는다** — False 로
    넣으면 "그 문제가 없다" 는 관측이 되는데, 실제로는 안 본 것이다.
    """
    base = BASE_RATIO.get(method)
    if base is None:
        return CaptureRatio(None, None, None, None, method,
                            reason=f"사업 방식 '{method}' 의 기준값이 없습니다 "
                                   f"({' · '.join(BASE_RATIO)} 중 하나여야 합니다)")

    applied: dict[str, str] = {}
    total = 0.0
    for key, on in (factors or {}).items():
        if not on:
            continue
        hit = PENALTY.get(key)
        if hit is None:
            continue
        total += hit[0]
        applied[key] = hit[1]

    ratio = max(0.50, base - total)     # 아무리 나빠도 신축의 절반은 받는다
    return CaptureRatio(
        base=base, ratio=ratio,
        optimistic=min(1.0, ratio + SPREAD),
        pessimistic=max(0.40, ratio - SPREAD),
        method=method, applied=applied)


def expected_value(*, peer_new_future_price: int | None,
                   ratio: CaptureRatio) -> dict:
    """완공 후 예상가치 = 주변 신축 미래가격 × 품질 반영률.

    주변 신축 가격을 모르면 **추정하지 않는다.** 여기서 임의로 채우면
    정비사업 기대수익 전체가 지어낸 숫자 위에 서게 된다(§49-16).
    """
    if peer_new_future_price is None:
        return {"값": None,
                "사유": "주변 대표 신축의 미래 예상가격이 없습니다"}
    if not ratio.known:
        return {"값": None, "사유": ratio.reason}
    return {
        "값": int(peer_new_future_price * ratio.ratio),
        "낙관": int(peer_new_future_price * ratio.optimistic),
        "보수": int(peer_new_future_price * ratio.pessimistic),
        "기준 신축가격": peer_new_future_price,
        "품질 반영률": ratio.label,
        "깎은 요소": ratio.applied,
        "주의": "품질 반영률은 관측이 아니라 가정입니다. 범위로 보세요.",
    }


__all__ = ["CaptureRatio", "capture_ratio", "expected_value",
           "BASE_RATIO", "PENALTY"]
