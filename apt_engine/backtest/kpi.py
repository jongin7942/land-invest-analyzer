"""백테스트 KPI 14종 (지시서 §57).

성적표를 하나의 숫자로 만들지 않는다. "수익률이 좋았다" 는 것만으로는
**왜** 좋았는지, 다음에도 그럴지 알 수 없기 때문이다. 14개를 나눠 보면
서로 다른 실패 방식이 각각 드러난다.

| KPI | 무엇을 잡아내는가 | § |
|---|---|---|
| winner_recall_at_k | 진짜 좋았던 것을 몇 개나 찾았나 | §42 |
| precision_at_k | 우리가 고른 것 중 몇 개가 진짜였나 | §43 |
| false_positive_rate | 자신 있게 골랐는데 아니었던 비율 | §43 |
| false_follower_rate | "따라 오를 것" 이라고 봤는데 안 오른 비율 | §44 |
| regret | 그때 살 수 있었던 최선 대비 얼마나 손해였나 | §35 |
| opportunity_alpha | 그냥 아무거나 산 것보다 나았나 | §33 |
| ex_post_capital_rank | 사후에 보니 우리 1위가 실제 몇 위였나 | §34 |
| median_forward_return | 고른 것들의 실제 수익률 중앙값 | §55 |
| hit_rate | 시장 중앙값을 이긴 비율 | §55 |
| rank_ic | 점수 순서와 실제 성과 순서가 맞았나 | §74 |
| max_drawdown | 보유 중 최대 낙폭 | §36 |
| recovery_months | 저점에서 회복까지 | §36 |
| discovery_lag | 상승이 시작되고 몇 달 뒤에 찾았나 | §40 |
| coverage | 후보 중 정답을 계산할 수 있었던 비율 | §67 |

**표본이 없으면 값을 만들지 않는다.** 스키마의 CHECK 가 `value IS NULL OR
sample_n > 0` 을 강제한다 — 표본 0개로 낸 100% 정확도는 성적이 아니다.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass

from apt_engine.backtest import outcome as outcome_mod

# 표시 순서 = 위 표의 순서. 리포트가 이 순서를 따른다.
#
# DELTA UPGRADE §26 이 다섯을 더 요구했다.
#   missed_better_alternative_rate  같은 자기자본으로 더 좋은 걸 살 수 있었나 (§24)
#   cash_accuracy                   현금이 정답이었을 때 현금이라고 했나 (§3)
#   after_cost_return               취득비용까지 뺀 실현 수익
#   after_interest_return           대출이자까지 뺀
#   after_tax_return                양도세까지 뺀
#
# 마지막 셋은 **자기자본 기준**이라 매매가 기준 수익률과 다르다. 레버리지가
# 크면 같은 +10% 도 자기자본 기준으로는 +40% 가 되고, 이자를 빼면 +25% 가 된다.
# 이 구분이 없으면 §25 의 3단계 성공 정의를 만들 수 없다.
KPI_KEYS = (
    "winner_recall_at_k", "precision_at_k", "false_positive_rate",
    "false_follower_rate", "regret", "opportunity_alpha",
    "ex_post_capital_rank", "median_forward_return", "hit_rate", "rank_ic",
    "max_drawdown", "recovery_months", "discovery_lag", "coverage",
    "missed_better_alternative_rate", "cash_accuracy",
    "after_cost_return", "after_interest_return", "after_tax_return",
)

KPI_LABEL = {
    "winner_recall_at_k": "Winner Recall@K",
    "precision_at_k": "Precision@K",
    "false_positive_rate": "False Positive 비율",
    "false_follower_rate": "False Follower 비율",
    "regret": "Regret",
    "opportunity_alpha": "Opportunity Alpha",
    "ex_post_capital_rank": "사후 자본 순위",
    "median_forward_return": "실현 수익률 중앙값",
    "hit_rate": "시장 초과 비율",
    "rank_ic": "점수-성과 순위상관",
    "max_drawdown": "최대 낙폭",
    "recovery_months": "회복 개월",
    "discovery_lag": "발견 지연",
    "coverage": "정답 계산 가능 비율",
    "missed_better_alternative_rate": "더 좋은 대안을 놓친 비율",
    "cash_accuracy": "현금 판단 정확도",
    "after_cost_return": "비용 차감 후 수익률",
    "after_interest_return": "이자 차감 후 수익률",
    "after_tax_return": "세후 수익률",
}

KPI_UNIT = {
    "winner_recall_at_k": "비율", "precision_at_k": "비율",
    "false_positive_rate": "비율", "false_follower_rate": "비율",
    "regret": "수익률차", "opportunity_alpha": "수익률차",
    "ex_post_capital_rank": "등", "median_forward_return": "수익률",
    "hit_rate": "비율", "rank_ic": "상관", "max_drawdown": "수익률",
    "recovery_months": "개월", "discovery_lag": "개월", "coverage": "비율",
    "missed_better_alternative_rate": "비율", "cash_accuracy": "비율",
    "after_cost_return": "수익률", "after_interest_return": "수익률",
    "after_tax_return": "수익률",
}

# 값이 클수록 좋은가. 리포트에서 화살표 방향을 정할 때 쓴다.
HIGHER_IS_BETTER = {
    "winner_recall_at_k": True, "precision_at_k": True,
    "false_positive_rate": False, "false_follower_rate": False,
    "regret": False, "opportunity_alpha": True,
    "ex_post_capital_rank": False, "median_forward_return": True,
    "hit_rate": True, "rank_ic": True, "max_drawdown": True,
    "recovery_months": False, "discovery_lag": False, "coverage": True,
    "missed_better_alternative_rate": False, "cash_accuracy": True,
    "after_cost_return": True, "after_interest_return": True,
    "after_tax_return": True,
}

# ── §25 백테스트 성공 3단계 ──────────────────────────────────────────
#
# > 1. Absolute Success   그냥 돈을 벌었나
# > 2. Benchmark Success  시장(중앙값)보다 나았나
# > 3. Capital Opportunity Success
# >    같은 자기자본으로 살 수 있었던 것 중 상위권을 골랐나
# >
# > 최종 최적화 대상은 3번이다.
#
# 1번만 보면 상승장에서는 아무거나 사도 성공이 된다. 2번까지 봐도
# "같은 돈으로 더 좋은 걸 살 수 있었다" 는 실패를 못 잡는다(§24).
ABSOLUTE = "ABSOLUTE"
BENCHMARK = "BENCHMARK"
CAPITAL_OPPORTUNITY = "CAPITAL_OPPORTUNITY"
SUCCESS_LEVELS = (ABSOLUTE, BENCHMARK, CAPITAL_OPPORTUNITY)

SUCCESS_KPI = {
    ABSOLUTE: "median_forward_return",
    BENCHMARK: "opportunity_alpha",
    CAPITAL_OPPORTUNITY: "missed_better_alternative_rate",
}
OPTIMIZATION_TARGET = CAPITAL_OPPORTUNITY
SUCCESS_NOTE = ("최종 최적화 대상은 Capital Opportunity Success 입니다(§25). "
                "절대 수익만 보면 상승장에서 아무거나 사도 성공이 됩니다")


@dataclass(frozen=True)
class Kpi:
    key: str
    value: float | None
    sample_n: int
    note: str | None = None

    @property
    def label(self) -> str:
        name = KPI_LABEL.get(self.key, self.key)
        if self.value is None:
            return f"{name}: 확인 불가 — {self.note or '사유 미기록'}"
        unit = KPI_UNIT.get(self.key, "")
        if unit in ("비율", "수익률", "수익률차"):
            shown = f"{self.value:+.1%}" if unit != "비율" else f"{self.value:.1%}"
        elif unit == "상관":
            shown = f"{self.value:+.3f}"
        else:
            shown = f"{self.value:.1f}{unit}"
        return f"{name}: {shown}  (n={self.sample_n})"


def _missing(key: str, why: str) -> Kpi:
    return Kpi(key, None, 0, why)


def compute_window(outcomes: list[outcome_mod.Outcome], *,
                   picked_order: list[int],
                   scores: dict[int, float] | None = None,
                   costs: dict[int, "Costs"] | None = None,
                   cash_return: float | None = None) -> list[Kpi]:
    """창 하나의 KPI.

    `picked_order` 는 우리가 매긴 순위대로의 complex_id 목록(1위부터).
    `scores`       점수. rank_ic 계산에만 쓴다.
    `costs`        후보별 비용·이자·세금 비율. 없으면 세후 지표는 '확인 불가'.
    `cash_return`  같은 기간 현금 수익률. 없으면 cash_accuracy 를 내지 않는다.
    """
    known = [o for o in outcomes if o.known]
    out: list[Kpi] = []

    total = len(outcomes)
    out.append(Kpi("coverage", len(known) / total if total else None,
                   total, None if total else "후보가 없습니다"))

    if not known:
        why = "정답을 계산할 수 있는 후보가 없습니다"
        return out + [_missing(k, why) for k in KPI_KEYS if k != "coverage"]

    picked = [o for o in known if o.picked]
    winners = [o for o in known
               if o.winner_class in (outcome_mod.WINNER_FOUND,
                                     outcome_mod.MISSED_WINNER)]
    found = [o for o in known if o.winner_class == outcome_mod.WINNER_FOUND]
    false_pos = [o for o in known if o.winner_class == outcome_mod.FALSE_POSITIVE]

    # §42 — 진짜 좋았던 것 중 몇 개를 찾았나
    out.append(Kpi("winner_recall_at_k",
                   len(found) / len(winners) if winners else None,
                   len(winners),
                   None if winners else "Winner 로 분류된 후보가 없습니다"))

    # §43 — 고른 것 중 몇 개가 진짜였나 / 아니었나
    out.append(Kpi("precision_at_k",
                   len(found) / len(picked) if picked else None,
                   len(picked), None if picked else "고른 후보가 없습니다"))
    out.append(Kpi("false_positive_rate",
                   len(false_pos) / len(picked) if picked else None,
                   len(picked), None if picked else "고른 후보가 없습니다"))

    # §44 False Follower — 선도 단지를 따라 오를 것으로 본 후보가 실제로는
    # 시장 중앙값에도 못 미친 비율. "선도가 올랐으니 이것도 오른다" 가 틀리는 경우다.
    market = statistics.median(o.forward_return for o in known)
    followers = [o for o in picked if o.forward_return < market]
    out.append(Kpi("false_follower_rate",
                   len(followers) / len(picked) if picked else None,
                   len(picked), None if picked else "고른 후보가 없습니다"))

    # §35 Regret — 그때 살 수 있었던 최선 대비 얼마나 손해였나.
    # "전지전능했다면" 과 비교하는 것이라 항상 0 이상이다.
    best = max(o.forward_return for o in known)
    if picked:
        ours = max(o.forward_return for o in picked)
        out.append(Kpi("regret", best - ours, len(known)))
    else:
        out.append(_missing("regret", "고른 후보가 없습니다"))

    # §33 Opportunity Alpha — 아무거나 샀을 때(중앙값) 대비
    if picked:
        picked_med = statistics.median(o.forward_return for o in picked)
        out.append(Kpi("opportunity_alpha", picked_med - market, len(picked)))
    else:
        out.append(_missing("opportunity_alpha", "고른 후보가 없습니다"))

    # §34 Ex-post Capital Rank — 우리 1위가 사후에 실제 몇 등이었나
    top1 = next((cid for cid in picked_order), None)
    rank = next((o.ex_post_rank for o in known if o.complex_id == top1), None)
    out.append(Kpi("ex_post_capital_rank", float(rank) if rank else None,
                   len(known),
                   None if rank else "1위 후보의 정답을 계산하지 못했습니다"))

    out.append(Kpi("median_forward_return",
                   statistics.median(o.forward_return for o in picked)
                   if picked else None,
                   len(picked), None if picked else "고른 후보가 없습니다"))

    hits = [o for o in picked if o.forward_return > market]
    out.append(Kpi("hit_rate", len(hits) / len(picked) if picked else None,
                   len(picked), None if picked else "고른 후보가 없습니다"))

    # §74 — 점수 순서가 실제 성과 순서와 맞았나. 이게 0 근처면 그 점수는
    # 순위를 만들 자격이 없다.
    ic = rank_ic(scores or {}, {o.complex_id: o.forward_return for o in known})
    out.append(Kpi("rank_ic", ic, len(known) if ic is not None else 0,
                   None if ic is not None else
                   "점수가 있는 후보가 3개 미만이라 순위상관을 내지 않습니다"))

    mdds = [o.max_drawdown for o in picked if o.max_drawdown is not None]
    out.append(Kpi("max_drawdown", statistics.median(mdds) if mdds else None,
                   len(mdds), None if mdds else "낙폭을 계산한 후보가 없습니다"))

    recs = [o.recovery_months for o in picked if o.recovery_months is not None]
    unrecovered = sum(1 for o in picked if o.recovered is False)
    rec_note = (None if recs else
                (f"보유기간 안에 회복한 후보가 없습니다 (미회복 {unrecovered}개)"
                 if unrecovered else "회복 개월을 계산한 후보가 없습니다"))
    out.append(Kpi("recovery_months",
                   statistics.median(recs) if recs else None, len(recs), rec_note))

    lags = [o.months_late for o in picked if o.months_late is not None]
    out.append(Kpi("discovery_lag", statistics.median(lags) if lags else None,
                   len(lags),
                   None if lags else "상승 시작 시점을 찾은 후보가 없습니다"))

    out.extend(_delta_metrics(known, picked, costs=costs,
                              cash_return=cash_return))
    return _in_order(out)


@dataclass(frozen=True)
class Costs:
    """후보 하나의 비용 구조. 전부 **매수가 대비 비율**이다.

    모르는 항목은 0 이 아니라 None 이다 — 0 으로 두면 "비용이 없었다" 가 되어
    세후 수익률이 부풀려진다.
    """
    acquisition: float | None = None      # 취득비용(취득세·중개·법무)
    interest: float | None = None         # 보유기간 대출이자 총액
    tax: float | None = None              # 양도세·지방소득세
    leverage: float | None = None         # 매수가 ÷ 실투자금

    def net(self, gross: float, *, upto: str) -> float | None:
        """단계별 차감. 필요한 항목이 없으면 만들어내지 않는다."""
        value = gross
        if upto in ("cost", "interest", "tax"):
            if self.acquisition is None:
                return None
            value -= self.acquisition
        if upto in ("interest", "tax"):
            if self.interest is None:
                return None
            value -= self.interest
        if upto == "tax":
            if self.tax is None:
                return None
            value -= self.tax
        return value


def _delta_metrics(known, picked, *, costs, cash_return) -> list[Kpi]:
    """§26 이 추가로 요구한 다섯 가지."""
    out: list[Kpi] = []

    # §24 — 같은 자기자본으로 더 좋은 걸 살 수 있었나.
    # 우리가 고른 것보다 실제로 더 잘한 후보가 **같은 후보군 안에** 있었으면
    # 그건 절대적으로는 성공이어도 상대적으로는 실패다.
    if picked and len(known) > len(picked):
        best_picked = max(o.forward_return for o in picked)
        missed = [o for o in known
                  if not o.picked and o.forward_return > best_picked]
        out.append(Kpi("missed_better_alternative_rate",
                       len(missed) / len(known), len(known),
                       (f"고른 것 중 최고 {best_picked:+.1%} 보다 잘한 "
                        f"비선택 후보가 {len(missed)}개")))
    else:
        out.append(_missing("missed_better_alternative_rate",
                            "비교할 비선택 후보가 없습니다"))

    # §3 — 현금이 정답이었을 때 현금이라고 했나.
    if cash_return is None:
        out.append(_missing("cash_accuracy",
                            "같은 기간 현금 수익률을 몰라 판정하지 않습니다 — "
                            "0 으로 가정하면 현금이 항상 오답이 됩니다"))
    elif not picked:
        # 아무것도 안 골랐다 = 현금을 골랐다. 그게 맞았는가?
        beat_cash = [o for o in known if o.forward_return > cash_return]
        correct = 1.0 if not beat_cash else 0.0
        out.append(Kpi("cash_accuracy", correct, len(known),
                       ("현금을 골랐고 실제로 현금보다 나은 후보가 없었습니다"
                        if correct else
                        f"현금을 골랐지만 현금을 이긴 후보가 {len(beat_cash)}개 "
                        f"있었습니다")))
    else:
        beat = sum(1 for o in picked if o.forward_return > cash_return)
        out.append(Kpi("cash_accuracy", beat / len(picked), len(picked),
                       f"고른 {len(picked)}개 중 {beat}개가 현금을 이겼습니다"))

    # 비용·이자·세금 차감 후 (§26)
    for key, upto, label in (("after_cost_return", "cost", "취득비용"),
                             ("after_interest_return", "interest", "대출이자"),
                             ("after_tax_return", "tax", "양도세")):
        out.append(_net_return(picked, costs, key=key, upto=upto, label=label))
    return out


def _net_return(picked, costs, *, key, upto, label) -> Kpi:
    if not picked:
        return _missing(key, "고른 후보가 없습니다")
    if not costs:
        return _missing(key, (
            f"{label} 정보가 없어 계산하지 않습니다 — 0 으로 두면 "
            f"수익률이 부풀려집니다"))
    values = []
    unknown = 0
    for o in picked:
        c = costs.get(o.complex_id)
        net = c.net(o.forward_return, upto=upto) if c else None
        if net is None:
            unknown += 1
            continue
        values.append(net)
    if not values:
        return _missing(key, f"{label}을(를) 아는 후보가 없습니다 "
                             f"(확인 불가 {unknown}개)")
    note = (f"{unknown}개는 {label} 미확인이라 제외했습니다" if unknown else None)
    return Kpi(key, statistics.median(values), len(values), note)


def success_level(kpis: list[Kpi]) -> tuple[str | None, str]:
    """§25 — 어느 단계까지 성공했나.

    3번(Capital Opportunity)이 최종 목표다. 1·2번만 통과한 것을 성공으로
    보고하지 않는다.
    """
    by_key = {k.key: k for k in kpis}
    absolute = by_key.get("median_forward_return")
    benchmark = by_key.get("opportunity_alpha")
    capital = by_key.get("missed_better_alternative_rate")

    if absolute is None or absolute.value is None:
        return None, "실현 수익률을 몰라 성공 여부를 판정하지 않습니다"
    if absolute.value <= 0:
        return None, f"실현 수익률 {absolute.value:+.1%} — Absolute 부터 실패입니다"

    level = ABSOLUTE
    reasons = [f"Absolute 성공 (수익률 {absolute.value:+.1%})"]

    if benchmark is None or benchmark.value is None:
        reasons.append("시장 대비를 몰라 Benchmark 단계는 판정 못 했습니다")
        return level, " · ".join(reasons)
    if benchmark.value <= 0:
        reasons.append(f"시장 대비 {benchmark.value:+.1%} — Benchmark 실패")
        return level, " · ".join(reasons)
    level = BENCHMARK
    reasons.append(f"Benchmark 성공 (시장 대비 {benchmark.value:+.1%})")

    if capital is None or capital.value is None:
        reasons.append("놓친 대안을 몰라 Capital Opportunity 는 판정 못 했습니다 "
                       "— 여기가 최종 목표입니다(§25)")
        return level, " · ".join(reasons)
    if capital.value > 0:
        reasons.append(
            f"같은 자기자본으로 더 나은 대안이 {capital.value:.1%} 있었습니다 "
            f"— Capital Opportunity 실패. 절대수익은 났지만 상대적으로는 "
            f"실패입니다(§24)")
        return level, " · ".join(reasons)
    reasons.append("Capital Opportunity 성공 — 같은 돈으로 더 나은 대안이 없었습니다")
    return CAPITAL_OPPORTUNITY, " · ".join(reasons)


def rank_ic(scores: dict[int, float],
            returns: dict[int, float]) -> float | None:
    """점수 순위 vs 실제 수익 순위의 Spearman 상관.

    scipy 없이 계산한다. 동점은 평균 순위를 준다 — 동점을 순서대로 매기면
    임의의 순서가 상관에 섞인다.
    """
    common = sorted(set(scores) & set(returns))
    if len(common) < 3:
        return None
    a = _ranks([scores[c] for c in common])
    b = _ranks([returns[c] for c in common])
    n = len(common)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    if da == 0 or db == 0:
        return None                        # 전부 동점이면 상관을 정의할 수 없다
    return num / (da * db)


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def _in_order(kpis: list[Kpi]) -> list[Kpi]:
    by_key = {k.key: k for k in kpis}
    return [by_key[k] for k in KPI_KEYS if k in by_key]


def aggregate(per_window: list[list[Kpi]]) -> list[Kpi]:
    """여러 창의 KPI 를 하나로 (§55 walk-forward 전체 성적).

    창끼리 기간이 겹칠 수 있으므로 표본을 단순히 더하지 않고 **창 수**를
    sample_n 으로 쓴다. 겹친 표본을 곱해 세면 신뢰도가 부풀려진다.
    """
    out: list[Kpi] = []
    for key in KPI_KEYS:
        values = [k.value for row in per_window for k in row
                  if k.key == key and k.value is not None]
        if not values:
            out.append(_missing(key, f"{len(per_window)}개 창 모두 확인 불가"))
            continue
        out.append(Kpi(key, statistics.median(values), len(values),
                       f"{len(values)}/{len(per_window)}개 창에서 계산 (창 중앙값)"))
    return out


def save(conn: sqlite3.Connection, run_id: int, kpis: list[Kpi], *,
         window_id: int | None = None, split: str | None = None,
         cash_bucket: int | None = None, list_kind: str | None = None,
         horizon_years: int | None = None) -> int:
    for k in kpis:
        conn.execute(
            "INSERT INTO backtest_kpi (run_id, window_id, split, cash_bucket, "
            " list_kind, horizon_years, kpi_key, value, unit, sample_n, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, window_id, split, cash_bucket, list_kind, horizon_years,
             k.key, k.value, KPI_UNIT.get(k.key), k.sample_n,
             k.note or (None if k.value is not None else "사유 미기록")))
    return len(kpis)
