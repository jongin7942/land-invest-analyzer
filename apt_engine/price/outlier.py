"""정상거래 필터 — 무엇을 시세로 볼 것인가.

요구사항 2가 제외하라고 지목한 것들:
  직거래 / 가족간 거래 의심 / 지나치게 낮은 가격 / 지나치게 높은 단일 신고가 /
  특수층·특수조건 / 취소거래

여기서 조심할 게 하나 있다. **규칙이 과하면 표본이 사라진다.** 거래가 드문 단지에서
필터를 다 걸면 남는 게 0건이 되고, 그러면 "가격 없음"이 되어 분석 대상에서 조용히
빠진다. 그건 틀린 가격만큼이나 나쁘다.

그래서 제외를 두 종류로 나눈다:

  **hard** — 어떤 경우에도 시세가 아니다. 취소된 거래, 직거래, 월세 낀 계약.
            표본이 0이 되더라도 절대 되살리지 않는다.
  **soft** — 보통은 빼는 게 맞지만, 그것 때문에 표본이 최소치 아래로 떨어지면
            되살리고 **되살렸다는 사실을 계산근거에 남긴다**. 1층 거래, 통계적 이상치,
            갱신요구권 갱신계약이 여기 속한다.

되살렸다는 기록이 남으니 화면에서 "이 가격은 1층 거래까지 포함한 값"이라고 말할 수 있다.
조용히 포함하는 것과 다르다.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Reason:
    code: str
    label: str
    hard: bool


# ── hard: 어떤 경우에도 시세가 아니다 ─────────────────────────────────
CANCELLED = Reason("CANCELLED", "취소(해제)된 거래", True)
DIRECT_DEAL = Reason("DIRECT_DEAL", "직거래 — 특수관계인 거래 가능성", True)
NOT_JEONSE = Reason("NOT_JEONSE", "월세가 있는 계약(순수 전세 아님)", True)

# ── soft: 표본이 모자라면 되살린다 ────────────────────────────────────
SPECIAL_FLOOR = Reason("SPECIAL_FLOOR", "1층 이하 특수층", False)
RENEWAL_RIGHT = Reason("RENEWAL_RIGHT", "갱신요구권 사용 갱신계약(기존 계약 연장)", False)
OUTLIER_HIGH = Reason("OUTLIER_HIGH", "단일 신고가 — 통계적 이상치(고가)", False)
OUTLIER_LOW = Reason("OUTLIER_LOW", "지나치게 낮은 가격 — 통계적 이상치(저가)", False)

ALL_REASONS = (CANCELLED, DIRECT_DEAL, NOT_JEONSE,
               SPECIAL_FLOOR, RENEWAL_RIGHT, OUTLIER_HIGH, OUTLIER_LOW)
BY_CODE = {r.code: r for r in ALL_REASONS}

# 이 개수 아래로 떨어지면 soft 제외를 되살린다.
MIN_SAMPLES = 3

# 수정 z-score 임계값. 3.5 는 이상치 탐지의 관용적 기준이다.
Z_THRESHOLD = 3.5

# 표본이 이보다 적으면 통계적 이상치 판정 자체를 하지 않는다 —
# 3~4건에서 중앙값 기준 이상치를 논하는 건 근거가 없다.
MIN_FOR_OUTLIER = 6


def modified_zscores(values: list[float]) -> list[float]:
    """MAD 기반 수정 z-score.

    평균·표준편차 대신 중앙값·MAD 를 쓴다. 표본이 작을 때 이상치 하나가 평균과
    표준편차를 동시에 끌고 가서 정작 그 이상치를 못 잡는 문제를 피한다.
    """
    if len(values) < 2:
        return [0.0] * len(values)
    med = statistics.median(values)
    deviations = [abs(v - med) for v in values]
    mad = statistics.median(deviations)
    if mad > 0:
        return [0.6745 * (v - med) / mad for v in values]
    # 절반 이상이 같은 값이면 MAD 가 0이 된다. 평균절대편차로 대체.
    mean_ad = sum(deviations) / len(deviations)
    if mean_ad == 0:
        return [0.0] * len(values)
    return [(v - med) / (1.253314 * mean_ad) for v in values]


@dataclass
class FilterResult:
    kept: list[dict] = field(default_factory=list)
    excluded: list[tuple[dict, Reason]] = field(default_factory=list)
    relaxed: list[str] = field(default_factory=list)   # 되살린 soft 사유 코드

    @property
    def exclusion_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, reason in self.excluded:
            out[reason.code] = out.get(reason.code, 0) + 1
        return out

    @property
    def relaxed_labels(self) -> list[str]:
        return [BY_CODE[c].label for c in self.relaxed if c in BY_CODE]


def _hard_reason(row: dict, *, jeonse: bool) -> Reason | None:
    if row.get("cancel_yn"):
        return CANCELLED
    if jeonse:
        if (row.get("monthly_rent") or 0) > 0:
            return NOT_JEONSE
    else:
        if (row.get("deal_type") or "").strip() == "직거래":
            return DIRECT_DEAL
    return None


def _soft_reason(row: dict, *, jeonse: bool) -> Reason | None:
    floor = row.get("floor")
    if floor is not None and floor <= 1:
        return SPECIAL_FLOOR
    if jeonse and row.get("use_renewal_right") == 1:
        return RENEWAL_RIGHT
    return None


def filter_normal(rows: list[dict], *, jeonse: bool = False,
                  price_key: str = "deal_amount",
                  min_samples: int = MIN_SAMPLES,
                  z_threshold: float = Z_THRESHOLD) -> FilterResult:
    """정상거래만 남긴다.

    1) hard 제외 (되돌리지 않음)
    2) soft 제외 — 속성 기반(1층, 갱신계약)
    3) soft 제외 — 통계적 이상치 (표본 6건 이상일 때만)
    4) 남은 표본이 min_samples 미만이면 3) → 2) 순서로 되살린다
       (통계적 이상치를 먼저 되살린다 — 1층 거래보다는 이상치 판정이 덜 확실하다)
    """
    result = FilterResult()

    survivors = []
    for row in rows:
        reason = _hard_reason(row, jeonse=jeonse)
        if reason:
            result.excluded.append((row, reason))
        else:
            survivors.append(row)

    attr_excluded: list[tuple[dict, Reason]] = []
    after_attr = []
    for row in survivors:
        reason = _soft_reason(row, jeonse=jeonse)
        if reason:
            attr_excluded.append((row, reason))
        else:
            after_attr.append(row)

    stat_excluded = _statistical_outliers(after_attr, price_key, z_threshold)
    stat_codes = {id(r) for r, _ in stat_excluded}
    after_stat = [r for r in after_attr if id(r) not in stat_codes]

    # 되살리기 — 통계적 이상치부터.
    kept = after_stat
    if len(kept) < min_samples and stat_excluded:
        kept = after_attr
        result.relaxed.extend(sorted({reason.code for _, reason in stat_excluded}))
        stat_excluded = []
    if len(kept) < min_samples and attr_excluded:
        kept = kept + [r for r, _ in attr_excluded]
        result.relaxed.extend(sorted({reason.code for _, reason in attr_excluded}))
        attr_excluded = []

    result.kept = kept
    result.excluded.extend(attr_excluded)
    result.excluded.extend(stat_excluded)
    return result


def _statistical_outliers(rows: list[dict], price_key: str,
                          z_threshold: float) -> list[tuple[dict, Reason]]:
    """지나치게 높거나 낮은 가격. 표본이 적으면 판정하지 않는다."""
    if len(rows) < MIN_FOR_OUTLIER:
        return []
    values = [float(r[price_key]) for r in rows]
    zs = modified_zscores(values)
    out = []
    for row, z in zip(rows, zs):
        if z > z_threshold:
            out.append((row, OUTLIER_HIGH))
        elif z < -z_threshold:
            out.append((row, OUTLIER_LOW))
    return out
