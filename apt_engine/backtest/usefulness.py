"""Feature usefulness → 가중치 (지시서 §71·§74).

§74 가 정한 순서의 네 번째와 다섯 번째 칸이다.

    데이터 → Feature → Backtest → **Feature usefulness → Weight** → Ranking

핵심 규칙 셋:

1. **TRAIN 에서 찾고 VALIDATION 에서 확인한다.**
   TRAIN 에서만 좋았던 feature 는 가중치를 받지 못한다. 한 구간에서만 통하는
   규칙은 그 구간의 우연일 가능성이 높고, 그걸 가중치로 굳히면 다음 시장에서
   틀린다.

2. **부호가 뒤집히면 0 이다.**
   TRAIN 에서 +0.3, VALIDATION 에서 -0.2 인 feature 는 "약하게 유용" 이 아니라
   "무엇을 재는지 모르는 값" 이다. 평균 내서 +0.05 를 주면 안 된다.

3. **백테스트 결과가 기존 생각과 다르면 기존 생각을 버린다.**
   heuristic 가중치가 크게 준 모델이 IC 0 이면, 그 사실을 그대로 기록하고
   가중치를 0 으로 만든다. 지시서가 명시적으로 요구한 태도다.

Ablation(§71)은 별도 축이다. IC 는 "이 feature 가 혼자서 순위를 맞히는가" 를 보고,
Ablation 은 "빼면 전체 성적이 나빠지는가" 를 본다. 둘이 어긋나는 경우가 정보다 —
IC 는 낮은데 빼면 나빠지는 feature 는 다른 feature 의 오류를 상쇄하고 있다.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field

from apt_engine.backtest import kpi as kpi_mod
from apt_engine.backtest import windows as windows_mod
from apt_engine.scoring import models as models_mod
from apt_engine.scoring import weights as weights_mod

USEFUL, NEUTRAL, HARMFUL, INSUFFICIENT = (
    "USEFUL", "NEUTRAL", "HARMFUL", "INSUFFICIENT")

# 판정 기준. **관측이 아니라 실험 설계 값이다** — 바꾸면 판정이 바뀐다.
MIN_WINDOWS = 3            # 이보다 적은 창으로는 판정하지 않는다
USEFUL_IC = 0.05           # 이 이상이면 유용
HARMFUL_IC = -0.05         # 이 이하면 해롭다
# 중앙값만 보면 창 몇 개가 크게 맞은 것으로 통과할 수 있다. 방향이 **꾸준한지**
# 도 본다 — 창의 이 비율 이상에서 같은 방향이어야 한다.
MIN_CONSISTENCY = 0.60
# 창이 겹치면 표본이 늘어난 것처럼 보이지만 실제로 늘어난 게 아니다.
# 3개월 간격 × 2년 보유면 한 창이 뒤 8개 창과 같은 기간을 채점한다.
# 36개 창의 IC 를 모아 놓고 "표본 36" 이라고 쓰면 신뢰도를 8배 부풀린다.
# 그래서 **서로 겹치지 않는 창의 개수**를 유효표본으로 쓴다.
#
# 그 위에 유의성 검정을 둔다. 임계값 하나(IC ≥ 0.05)로는 부족하다 —
# 유효표본이 4개면 IC 의 표준오차가 0.09 쯤이라, +0.07 같은 값은 순수한 잡음이다.
# 신호를 0 으로 넣은 합성 시장에서 value 모델이 IC +0.07~+0.13 으로 통과하는 것을
# 실제로 봤고, 그걸 막는 것이 이 기준이다.
MIN_T = 2.0
CRITERIA_NOTE = (f"겹치지 않는 창 {MIN_WINDOWS}개 이상 · IC ≥ {USEFUL_IC} · "
                 f"t ≥ {MIN_T} · 방향 일관성 ≥ {MIN_CONSISTENCY:.0%} · "
                 f"VALIDATION 에서도 같은 기준을 통과할 때만 USEFUL 입니다")


@dataclass(frozen=True)
class Usefulness:
    key: str
    split: str
    rank_ic: float | None
    hit_rate: float | None
    sample_n: int                       # 창 수
    verdict: str
    ablation_delta: float | None = None
    note: str = ""
    consistency: float | None = None    # 같은 방향이 나온 창의 비율
    effective_n: int | None = None      # 서로 겹치지 않는 창의 수
    t_stat: float | None = None         # |IC| / 표준오차

    @property
    def label(self) -> str:
        if self.rank_ic is None:
            return f"{self.key}: 확인 불가 — {self.note}"
        tail = "" if self.consistency is None else f" · 일관 {self.consistency:.0%}"
        if self.t_stat is not None:
            tail += f" · t={self.t_stat:.1f}"
        return (f"{self.key}: IC {self.rank_ic:+.3f} · "
                f"적중 {self.hit_rate:.0%}{tail} · {self.verdict} "
                f"(창 {self.sample_n}, 유효 {self.effective_n})")


def measure(window_results, *, level: str = "model",
            split: str | None = None) -> list[Usefulness]:
    """창들을 모아 feature(또는 model)별 순위상관을 낸다.

    창마다 IC 를 따로 내고 그 **중앙값**을 쓴다. 창을 합쳐서 한 번에 계산하면
    시점마다 다른 가격 수준이 섞여서, 시장 전체가 오른 시기의 후보가 전부
    "좋은 후보" 로 보인다.
    """
    picked = [w for w in window_results
              if w.scored and (split is None or w.window.split == split)]
    if not picked:
        return []

    source = "model_values" if level == "model" else "feature_values"
    keys: set[str] = set()
    for w in picked:
        keys |= set(getattr(w, source))

    out: list[Usefulness] = []
    for key in sorted(keys):
        ics: list[float] = []
        hits: list[float] = []
        for w in picked:
            values = getattr(w, source).get(key, {})
            returns = {o.complex_id: o.forward_return
                       for o in w.outcomes if o.known}
            ic = kpi_mod.rank_ic(values, returns)
            if ic is not None:
                ics.append(ic)
            hit = _hit_rate(values, returns)
            if hit is not None:
                hits.append(hit)

        n_eff = effective_n([w.window for w in picked], counted=len(ics))
        if n_eff < MIN_WINDOWS:
            out.append(Usefulness(
                key, split or "ALL", None, None, len(ics), INSUFFICIENT,
                note=(f"창 {len(ics)}개를 봤지만 서로 겹치지 않는 것은 "
                      f"{n_eff}개뿐입니다(최소 {MIN_WINDOWS}개). "
                      f"겹친 창을 따로 세면 표본을 부풀리게 됩니다"),
                effective_n=n_eff))
            continue

        ic = statistics.median(ics)
        hit = statistics.median(hits) if hits else None
        same_way = sum(1 for x in ics if (x > 0) == (ic > 0)) / len(ics)
        spread = statistics.stdev(ics) if len(ics) > 1 else 0.0
        se = spread / (n_eff ** 0.5) if n_eff else None
        t = (abs(ic) / se) if se else None

        if ic >= USEFUL_IC:
            if t is not None and t < MIN_T:
                verdict = NEUTRAL
                note = (f"IC 중앙값 {ic:+.3f} 이지만 유효표본 {n_eff}개 기준 "
                        f"표준오차가 {se:.3f} 라 t={t:.1f} 입니다"
                        f"(기준 {MIN_T}). 잡음과 구분되지 않습니다")
            elif same_way < MIN_CONSISTENCY:
                verdict = NEUTRAL
                note = (f"IC 중앙값은 {ic:+.3f} 이지만 방향이 같은 창이 "
                        f"{same_way:.0%} 뿐입니다(기준 {MIN_CONSISTENCY:.0%}). "
                        f"몇 개 창이 크게 맞은 것을 꾸준함으로 보지 않습니다")
            else:
                verdict, note = USEFUL, ""
        elif ic <= HARMFUL_IC:
            verdict, note = HARMFUL, ""
        else:
            verdict, note = NEUTRAL, ""
        out.append(Usefulness(key, split or "ALL", ic, hit, len(ics), verdict,
                              note=note, consistency=same_way,
                              effective_n=n_eff, t_stat=t))
    return out


def effective_n(windows, *, counted: int | None = None) -> int:
    """서로 겹치지 않는 창의 최대 개수.

    [as_of, eval_day] 구간이 겹치는 창들은 같은 기간을 두 번 채점한 것이라
    독립적인 관측이 아니다. 앞에서부터 욕심껏 고르면 최대 개수가 나온다.
    """
    spans = sorted({(w.as_of, w.eval_day) for w in windows})
    picked_end = ""
    n = 0
    for start, end in spans:
        if start >= picked_end:
            n += 1
            picked_end = end
    if counted is not None:
        n = min(n, counted)
    return n


def _hit_rate(values: dict[int, float],
              returns: dict[int, float]) -> float | None:
    """상위 절반이 실제로 중앙값을 넘긴 비율."""
    common = sorted(set(values) & set(returns))
    if len(common) < 4:
        return None
    ordered = sorted(common, key=lambda c: -values[c])
    top = ordered[:max(1, len(ordered) // 2)]
    market = statistics.median(returns[c] for c in common)
    return sum(1 for c in top if returns[c] > market) / len(top)


def confirm(train: list[Usefulness],
            validation: list[Usefulness]) -> dict[str, Usefulness]:
    """TRAIN 판정을 VALIDATION 으로 확인한다.

    확인의 기준은 **부호가 같다** 가 아니라 **VALIDATION 에서도 기준을 넘는다** 다.

    부호만 보면 아무 신호가 없는 시장에서도 절반은 통과한다. 창이 몇 개 안 되는
    구간에서 부호가 우연히 맞는 일은 흔하고, 그걸 "확인됨" 으로 읽으면 잡음에
    가중치를 주게 된다. 실제로 신호를 0 으로 넣은 합성 시장에서 이 문제를 봤다.

    평균은 내지 않는다 — TRAIN +0.3 과 VALIDATION -0.2 의 평균 +0.05 는
    "약하게 유용" 이 아니라 "모른다" 다.
    """
    v = {u.key: u for u in validation}
    out: dict[str, Usefulness] = {}
    for u in train:
        other = v.get(u.key)
        if u.rank_ic is None:
            out[u.key] = u
            continue
        if other is None or other.rank_ic is None:
            out[u.key] = _replace(u, verdict=INSUFFICIENT,
                                  note="VALIDATION 구간에서 확인하지 못했습니다")
            continue

        if (u.rank_ic > 0) != (other.rank_ic > 0):
            out[u.key] = _replace(
                u, verdict=NEUTRAL,
                note=(f"TRAIN {u.rank_ic:+.3f} 인데 VALIDATION {other.rank_ic:+.3f} "
                      f"— 부호가 뒤집혔습니다. 평균 내지 않고 0 으로 둡니다"))
            continue
        if u.verdict == USEFUL and other.rank_ic < USEFUL_IC:
            out[u.key] = _replace(
                u, verdict=NEUTRAL,
                note=(f"TRAIN 은 {u.rank_ic:+.3f} 로 기준을 넘었지만 VALIDATION 은 "
                      f"{other.rank_ic:+.3f} 로 기준({USEFUL_IC})에 못 미칩니다. "
                      f"부호만 같은 것을 확인으로 보지 않습니다"))
            continue
        out[u.key] = _replace(
            u, note=f"VALIDATION 확인됨 (IC {other.rank_ic:+.3f})")
    return out


def fit_weights(confirmed: dict[str, Usefulness], *,
                regime: str | None = None) -> tuple[weights_mod.Weights, list[str]]:
    """확인된 유용성으로 모델 가중치를 만든다 (§74).

    IC 가 양수인 모델에만 IC 에 비례한 가중치를 준다. 음수·0·미확인은 0 이다.
    쓸 수 있는 모델이 하나도 없으면 **가중치를 만들지 않고** HEURISTIC 을
    그대로 돌려준다 — 근거 없는 학습 가중치가 '학습됨' 라벨을 다는 게 더 나쁘다.
    """
    notes: list[str] = []
    positive = {k: u.rank_ic for k, u in confirmed.items()
                if u.verdict == USEFUL and u.rank_ic and u.rank_ic > 0
                and k in weights_mod.MODELS}
    if not positive:
        notes.append("VALIDATION 까지 통과한 모델이 없습니다 — "
                     "가중치를 학습하지 않고 heuristic 을 유지합니다")
        return weights_mod.for_regime(regime, source=weights_mod.HEURISTIC), notes

    total = sum(positive.values())
    values = {m: 0.0 for m in weights_mod.MODELS}
    for model, ic in positive.items():
        values[model] = ic / total

    for model in weights_mod.MODELS:
        base = weights_mod.BASE.get(model, 0.0)
        fitted = values[model]
        if base >= 0.10 and fitted == 0.0:
            u = confirmed.get(model)
            why = u.note if u and u.note else "IC 가 확인되지 않았습니다"
            notes.append(
                f"heuristic 이 {base:.0%} 를 줬던 '{model}' 의 가중치를 0 으로 "
                f"내렸습니다 — {why}. (§74: 백테스트가 기존 생각과 다르면 "
                f"기존 생각을 버린다)")

    return weights_mod.Weights(values, weights_mod.BACKTESTED, regime), notes


def ablation(run_fn, *, groups: tuple[str, ...] | None = None,
             kpi_key: str = "rank_ic") -> list[tuple[str, float | None, str]]:
    """§71 Ablation — 그룹을 하나씩 빼고 성적이 얼마나 나빠지는지 본다.

    `run_fn(groups) -> list[Kpi]` 를 호출한다. 호출부가 파이프라인을 다시 돌리는
    책임을 가지므로 이 함수는 비교만 한다.

    delta > 0 이면 "빼면 나빠진다 = 기여하고 있다" 이고,
    delta < 0 이면 "빼면 좋아진다 = 방해하고 있다" 이다. 후자를 발견하면
    그 그룹은 가중치를 받을 게 아니라 **왜 방해가 되는지** 를 봐야 한다.
    """
    all_groups = groups or tuple(models_mod.SPEC)
    base = _kpi_value(run_fn(None), kpi_key)
    out: list[tuple[str, float | None, str]] = []
    for group in all_groups:
        rest = tuple(g for g in all_groups if g != group)
        value = _kpi_value(run_fn(rest), kpi_key)
        if base is None or value is None:
            out.append((group, None,
                        f"{kpi_key} 를 계산하지 못해 비교할 수 없습니다"))
            continue
        delta = base - value
        verdict = ("빼면 나빠짐(기여)" if delta > 0 else
                   "빼면 좋아짐(방해)" if delta < 0 else "차이 없음")
        out.append((group, delta, verdict))
    return out


def _kpi_value(kpis, key: str) -> float | None:
    for k in kpis or ():
        if k.key == key:
            return k.value
    return None


def save(conn: sqlite3.Connection, run_id: int, results: list[Usefulness], *,
         regime: str | None = None) -> int:
    for u in results:
        conn.execute(
            "INSERT INTO feature_usefulness (run_id, split, regime, feature_key, "
            " rank_ic, hit_rate, ablation_delta, sample_n, verdict, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, split, regime, feature_key) DO UPDATE SET "
            " rank_ic=excluded.rank_ic, hit_rate=excluded.hit_rate, "
            " ablation_delta=excluded.ablation_delta, "
            " sample_n=excluded.sample_n, verdict=excluded.verdict, "
            " note=excluded.note",
            (run_id, u.split if u.split != "ALL" else windows_mod.TRAIN, regime,
             u.key, u.rank_ic, u.hit_rate, u.ablation_delta, u.sample_n,
             u.verdict, u.note or None))
    return len(results)


def save_weights(conn: sqlite3.Connection, run_id: int,
                 weights: weights_mod.Weights, *, market_source: str,
                 sample_n: int, train: dict[str, Usefulness] | None = None,
                 validation: dict[str, Usefulness] | None = None) -> int:
    """학습된 가중치를 저장한다. HEURISTIC 은 저장하지 않는다 —
    weight_fit 테이블은 '학습된 것' 만 담는 자리다."""
    if weights.source != weights_mod.BACKTESTED:
        return 0
    written = 0
    for model, value in weights.values.items():
        t = (train or {}).get(model)
        v = (validation or {}).get(model)
        conn.execute(
            "INSERT INTO weight_fit (run_id, regime, model_key, weight, "
            " train_ic, validation_ic, sample_n, market_source, note) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, regime, model_key) DO UPDATE SET "
            " weight=excluded.weight, train_ic=excluded.train_ic, "
            " validation_ic=excluded.validation_ic, note=excluded.note",
            (run_id, weights.regime, model, value,
             t.rank_ic if t else None, v.rank_ic if v else None,
             sample_n, market_source, CRITERIA_NOTE))
        written += 1
    return written


def load_weights(conn: sqlite3.Connection, *, regime: str | None = None,
                 market_source: str = "REAL") -> weights_mod.Weights | None:
    """저장된 학습 가중치. 없으면 None — 호출부가 heuristic 으로 돌아간다.

    합성 시장으로 학습한 가중치는 기본적으로 읽지 않는다. 그건 하네스 검증용이지
    시장에 대한 지식이 아니다.
    """
    rows = conn.execute(
        "SELECT model_key, weight FROM weight_fit "
        " WHERE market_source=? AND (regime IS ? OR regime = ?) "
        " ORDER BY fitted_at DESC",
        (market_source, regime, regime)).fetchall()
    if not rows:
        return None
    seen: dict[str, float] = {}
    for r in rows:
        seen.setdefault(r["model_key"], float(r["weight"]))
    return weights_mod.Weights(seen, weights_mod.BACKTESTED, regime)


def _replace(u: Usefulness, **kw) -> Usefulness:
    from dataclasses import replace
    return replace(u, **kw)
