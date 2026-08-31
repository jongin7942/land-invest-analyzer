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
KPI_KEYS = (
    "winner_recall_at_k", "precision_at_k", "false_positive_rate",
    "false_follower_rate", "regret", "opportunity_alpha",
    "ex_post_capital_rank", "median_forward_return", "hit_rate", "rank_ic",
    "max_drawdown", "recovery_months", "discovery_lag", "coverage",
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
}

KPI_UNIT = {
    "winner_recall_at_k": "비율", "precision_at_k": "비율",
    "false_positive_rate": "비율", "false_follower_rate": "비율",
    "regret": "수익률차", "opportunity_alpha": "수익률차",
    "ex_post_capital_rank": "등", "median_forward_return": "수익률",
    "hit_rate": "비율", "rank_ic": "상관", "max_drawdown": "수익률",
    "recovery_months": "개월", "discovery_lag": "개월", "coverage": "비율",
}

# 값이 클수록 좋은가. 리포트에서 화살표 방향을 정할 때 쓴다.
HIGHER_IS_BETTER = {
    "winner_recall_at_k": True, "precision_at_k": True,
    "false_positive_rate": False, "false_follower_rate": False,
    "regret": False, "opportunity_alpha": True,
    "ex_post_capital_rank": False, "median_forward_return": True,
    "hit_rate": True, "rank_ic": True, "max_drawdown": True,
    "recovery_months": False, "discovery_lag": False, "coverage": True,
}


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
                   scores: dict[int, float] | None = None) -> list[Kpi]:
    """창 하나의 KPI 14종.

    `picked_order` 는 우리가 매긴 순위대로의 complex_id 목록(1위부터).
    `scores`       점수. rank_ic 계산에만 쓴다.
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

    return _in_order(out)


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
