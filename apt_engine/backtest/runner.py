"""백테스트 구동 (§55·§56·§57·§72).

한 번의 실행이 하는 일:

    ① 창 생성            windows.generate  — 시간 분할까지 (§72)
    ② 창마다 결정        ranking.pipeline  — 그 시점 데이터만 보고
    ③ 창마다 채점        outcome.compute   — 후보 **전체** 에 대해
    ④ KPI                kpi.compute_window → aggregate (§57)
    ⑤ 누출 감사          leakage.audit
    ⑥ 유효/무효 확정     누출이 있으면 status='INVALID' (§55)

⑥이 마지막인 것이 중요하다. 성적을 먼저 보고 나서 누출을 찾으면, 사람은 성적이
좋았던 실행을 살리고 싶어진다. 그래서 **누출 검사를 통과하지 않으면 COMPLETE 가
될 수 없게** 스키마 CHECK 로 막아 뒀다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from apt_engine import ENGINE_VERSION, units
from apt_engine.backtest import kpi as kpi_mod
from apt_engine.backtest import leakage as leakage_mod
from apt_engine.backtest import outcome as outcome_mod
from apt_engine.backtest import usefulness as useful_mod
from apt_engine.backtest import windows as windows_mod
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.invest import buckets as buckets_mod
from apt_engine.invest.budget import Profile
from apt_engine.ranking import lists as lists_mod
from apt_engine.ranking import pipeline as pipeline_mod
from apt_engine.scoring import weights as weights_mod

REAL, SYNTHETIC = "REAL", "SYNTHETIC"
DEFAULT_TOP_K = 10


@dataclass
class WindowResult:
    window: windows_mod.Window
    window_id: int
    picks: dict[tuple[int, str], list[int]] = field(default_factory=dict)
    outcomes: list[outcome_mod.Outcome] = field(default_factory=list)
    kpis: list[kpi_mod.Kpi] = field(default_factory=list)
    scored: bool = False
    skip_reason: str | None = None
    # §74 Feature usefulness 를 계산하려면 "그때 각 후보의 feature 가 얼마였나" 가
    # 필요하다. DB 에 남기면 그 표가 곧 다음 백테스트의 누출 경로가 되므로
    # 실행 중 메모리에만 들고 있는다.
    feature_values: dict[str, dict[int, float]] = field(default_factory=dict)
    model_values: dict[str, dict[int, float]] = field(default_factory=dict)
    regime: str | None = None


@dataclass
class RunResult:
    run_id: int
    run_key: str
    market_source: str
    windows: list[WindowResult] = field(default_factory=list)
    aggregate: list[kpi_mod.Kpi] = field(default_factory=list)
    audit: leakage_mod.Audit | None = None
    status: str = "RUNNING"
    invalid_reason: str | None = None
    embargo: list[str] = field(default_factory=list)
    # §74 — 백테스트가 끝나야 나오는 것들
    usefulness: dict[str, useful_mod.Usefulness] = field(default_factory=dict)
    fitted: weights_mod.Weights | None = None
    fit_notes: list[str] = field(default_factory=list)

    @property
    def scored_windows(self) -> list[WindowResult]:
        return [w for w in self.windows if w.scored]

    @property
    def summary(self) -> str:
        head = [f"백테스트 {self.run_key} · {self.market_source} · {self.status}"]
        if self.market_source == SYNTHETIC:
            head.append("  ⚠ 합성 시장입니다. 이 성적을 실제 성과로 읽지 마세요")
        head.append(f"  창 {len(self.windows)}개 중 채점 {len(self.scored_windows)}개")
        for note in self.embargo:
            head.append(f"  ⚠ {note}")
        if self.invalid_reason:
            head.append(f"  무효: {self.invalid_reason}")
        if self.audit:
            head.append(f"  {self.audit.summary}")
        if self.aggregate:
            head.append("")
            head.append("  ── KPI (창 중앙값) ──")
            for k in self.aggregate:
                head.append(f"    {k.label}")
        if self.fitted is not None:
            head.append("")
            source = ("학습됨(BACKTESTED)" if self.fitted.source == "BACKTESTED"
                      else "학습 실패 — heuristic 유지")
            head.append(f"  ── 가중치: {source} ──")
            for model, value in sorted(self.fitted.values.items(),
                                       key=lambda kv: -kv[1]):
                if value > 0:
                    head.append(f"    {model:<20} {value:.3f}")
            for note in self.fit_notes:
                head.append(f"    · {note}")
        return "\n".join(head)


def run(conn: sqlite3.Connection, *, run_key: str, data_start: str,
        data_end: str, profile: Profile, area_band: str = "84",
        horizons: tuple[int, ...] = (2, 5),
        step_months: int = windows_mod.DEFAULT_STEP_MONTHS,
        cash_buckets: tuple[int, ...] | None = None,
        list_kinds: tuple[str, ...] = ("absolute",),
        top_k: int = DEFAULT_TOP_K,
        market_source: str = REAL,
        gate: str = pipeline_mod.GATE_STRICT,
        purge_embargo: bool = False,
        max_windows: int | None = None,
        weights_source: str = weights_mod.HEURISTIC,
        cash_hurdle_rate: float | None = None,
        train_fraction: float = windows_mod.TRAIN_FRACTION,
        validation_fraction: float = windows_mod.VALIDATION_FRACTION,
        run_leakage_audit: bool = True) -> RunResult:
    """walk-forward 한 회차."""
    buckets = cash_buckets or buckets_mod.BUCKETS
    all_windows = windows_mod.generate(
        data_start, data_end, horizons=horizons, step_months=step_months,
        train_fraction=train_fraction, validation_fraction=validation_fraction)
    embargo = windows_mod.embargo_conflicts(all_windows)
    if purge_embargo:
        all_windows = windows_mod.purge(all_windows)
    if max_windows is not None:
        all_windows = all_windows[:max_windows]

    run_id = _create_run(conn, run_key=run_key, data_start=data_start,
                         data_end=data_end, step_months=step_months,
                         horizons=horizons, buckets=buckets, top_k=top_k,
                         market_source=market_source, gate=gate)
    result = RunResult(run_id, run_key, market_source, embargo=embargo)

    for window in all_windows:
        wr = _run_window(conn, run_id, window, profile=profile,
                         area_band=area_band, buckets=buckets,
                         list_kinds=list_kinds, top_k=top_k, gate=gate,
                         weights_source=weights_source,
                         cash_hurdle_rate=cash_hurdle_rate)
        result.windows.append(wr)

    result.aggregate = kpi_mod.aggregate([w.kpis for w in result.scored_windows]) \
        if result.scored_windows else [
            kpi_mod.Kpi(k, None, 0, "채점된 창이 없습니다") for k in kpi_mod.KPI_KEYS]
    kpi_mod.save(conn, run_id, result.aggregate)

    # ④-2 Feature usefulness → Weight (§74). 순서가 여기인 것이 중요하다 —
    # KPI 를 내기 **전에** 가중치를 만들면 그건 학습이 아니라 끼워맞추기다.
    _fit(conn, run_id, result, market_source=market_source)

    # ⑤·⑥ 누출 감사 → 유효/무효 확정
    if run_leakage_audit:
        result.audit = leakage_mod.audit(conn)
        _finalize(conn, result)
    else:
        _mark_invalid(conn, result,
                      "누출 검사를 하지 않았습니다 — 검사하지 않은 백테스트는 "
                      "유효하다고 말할 수 없습니다(§55)")
    conn.commit()
    return result


def _fit(conn, run_id: int, result: RunResult, *, market_source: str) -> None:
    """TRAIN 에서 찾고 VALIDATION 에서 확인해 가중치를 만든다 (§74).

    확인되지 않으면 **가중치를 만들지 않는다.** heuristic 을 그대로 두고 그 이유를
    남긴다 — 근거 없는 숫자가 '학습됨' 라벨을 다는 게 더 위험하다.
    """
    train = useful_mod.measure(result.windows, level="model",
                               split=windows_mod.TRAIN)
    validation = useful_mod.measure(result.windows, level="model",
                                    split=windows_mod.VALIDATION)
    if train:
        useful_mod.save(conn, run_id, train)
    if validation:
        useful_mod.save(conn, run_id, validation)

    result.usefulness = useful_mod.confirm(train, validation)
    fitted, notes = useful_mod.fit_weights(result.usefulness)
    result.fitted, result.fit_notes = fitted, notes
    useful_mod.save_weights(
        conn, run_id, fitted, market_source=market_source,
        sample_n=max(1, len(result.scored_windows)),
        train={u.key: u for u in train},
        validation={u.key: u for u in validation})


def _run_window(conn, run_id: int, window: windows_mod.Window, *,
                profile: Profile, area_band: str, buckets: tuple[int, ...],
                list_kinds: tuple[str, ...], top_k: int, gate: str,
                weights_source: str,
                cash_hurdle_rate: float | None = None) -> WindowResult:
    window_id = _create_window(conn, run_id, window)
    wr = WindowResult(window, window_id)

    if not window.scorable:
        wr.skip_reason = window.skip_reason
        _skip_window(conn, window_id, window.skip_reason or "사유 미기록")
        return wr

    as_of = cutoff_mod.AsOf(window.as_of)
    universe_ids: set[int] = set()
    picked_ids: set[int] = set()
    picked_order: list[int] = []
    scores: dict[int, float] = {}

    for bucket in buckets:
        bucket_profile = _with_cash(profile, bucket)
        try:
            ranked = pipeline_mod.run(
                conn, as_of=as_of, profile=bucket_profile,
                horizon_years=window.horizon_years, area_band=area_band,
                weights_source=weights_source, gate=gate)
        except Exception as exc:                       # noqa: BLE001
            _skip_window(conn, window_id,
                         f"랭킹이 실패했습니다: {type(exc).__name__}: {exc}")
            wr.skip_reason = str(exc)
            return wr

        universe_ids |= {c.complex_id for c in ranked.feasible}
        wr.regime = wr.regime or ranked.regime
        for c in ranked.feasible:
            scores.setdefault(c.complex_id, c.score)
            for key, feature in c.features.items.items():
                if feature.usable and feature.value is not None:
                    wr.feature_values.setdefault(key, {}).setdefault(
                        c.complex_id, feature.value)
            for model, ms in c.consensus.scores.items():
                if ms.known:
                    wr.model_values.setdefault(model, {}).setdefault(
                        c.complex_id, ms.value)

        for kind in list_kinds:
            entries = lists_mod.build(ranked.top10, kind, limit=top_k)
            ids = [e.candidate.complex_id for e in entries]
            wr.picks[(bucket, kind)] = ids
            picked_ids |= set(ids)
            if not picked_order and ids:
                picked_order = ids
            _save_picks(conn, window_id, bucket, kind, entries, weights_source)

    if not universe_ids:
        reason = ("이 시점에 자본 게이트를 통과한 후보가 없습니다 "
                  f"(가장 큰 버킷 {units.fmt_eok(max(buckets))} 기준)")
        _skip_window(conn, window_id, reason)
        wr.skip_reason = reason
        return wr

    # ③ 채점 — 우리가 고른 것만이 아니라 **후보 전체**
    raw = [outcome_mod.compute(conn, cid, area_band, window=window)
           for cid in sorted(universe_ids)]
    wr.outcomes = outcome_mod.classify(raw, picked_ids)
    outcome_mod.save(conn, window_id, wr.outcomes)

    # §26 cash_accuracy — 같은 기간 현금 수익률. **없으면 지어내지 않는다.**
    cash_return = (None if cash_hurdle_rate is None else
                   (1 + cash_hurdle_rate) ** window.horizon_years - 1)
    wr.kpis = kpi_mod.compute_window(wr.outcomes, picked_order=picked_order,
                                     scores=scores, cash_return=cash_return)
    kpi_mod.save(conn, _run_id_of(conn, window_id), wr.kpis,
                 window_id=window_id, split=window.split,
                 horizon_years=window.horizon_years)

    conn.execute(
        "UPDATE backtest_window SET status='SCORED', universe_size=?, scored_n=? "
        " WHERE id=?",
        (len(universe_ids), sum(1 for o in wr.outcomes if o.known), window_id))
    wr.scored = True
    return wr


# ── 저장 ─────────────────────────────────────────────────────────────

def _create_run(conn, *, run_key: str, data_start: str, data_end: str,
                step_months: int, horizons, buckets, top_k: int,
                market_source: str, gate: str) -> int:
    conn.execute("DELETE FROM backtest_run WHERE run_key=?", (run_key,))
    cur = conn.execute(
        "INSERT INTO backtest_run (run_key, engine_version, data_start, data_end, "
        " step_months, horizons_json, buckets_json, top_k, market_source, note) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_key, ENGINE_VERSION, data_start, data_end, step_months,
         json.dumps(list(horizons)), json.dumps(list(buckets)), top_k,
         market_source,
         f"자본 게이트: {pipeline_mod.GATE_NOTE[gate]} · "
         f"{windows_mod.SPLIT_NOTE}"))
    return int(cur.lastrowid)


def _create_window(conn, run_id: int, w: windows_mod.Window) -> int:
    cur = conn.execute(
        "INSERT INTO backtest_window (run_id, as_of, horizon_years, eval_day, "
        " split) VALUES (?,?,?,?,?)",
        (run_id, w.as_of, w.horizon_years, w.eval_day, w.split))
    return int(cur.lastrowid)


def _skip_window(conn, window_id: int, reason: str) -> None:
    conn.execute(
        "UPDATE backtest_window SET status='SKIPPED', skip_reason=? WHERE id=?",
        (reason, window_id))


def _run_id_of(conn, window_id: int) -> int:
    return int(conn.execute("SELECT run_id FROM backtest_window WHERE id=?",
                            (window_id,)).fetchone()[0])


def _save_picks(conn, window_id: int, bucket: int, kind: str, entries,
                weights_source: str) -> None:
    conn.execute(
        "DELETE FROM backtest_pick WHERE window_id=? AND cash_bucket=? "
        " AND list_kind=?", (window_id, bucket, kind))
    for e in entries:
        c = e.candidate
        conn.execute(
            "INSERT INTO backtest_pick (window_id, cash_bucket, list_kind, rank, "
            " complex_id, area_band, score, confidence, entry_price, "
            " required_equity, weights_source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (window_id, bucket, kind, e.rank, c.complex_id, c.area_band,
             c.score, c.confidence, c.price, c.required_equity, weights_source))


def _finalize(conn, result: RunResult) -> None:
    audit = result.audit
    if audit is None or not audit.checked:
        _mark_invalid(conn, result, "누출 검사 기록이 없습니다")
        return
    if not audit.clean:
        _mark_invalid(conn, result,
                      f"누출 {len(audit.findings)}건: "
                      + " / ".join(f.label for f in audit.findings[:3]))
        return
    conn.execute(
        "UPDATE backtest_run SET leakage_checked=1, leakage_found=0, "
        " status='COMPLETE' WHERE id=?", (result.run_id,))
    result.status = "COMPLETE"


def _mark_invalid(conn, result: RunResult, reason: str) -> None:
    conn.execute(
        "UPDATE backtest_run SET leakage_checked=?, leakage_found=?, "
        " status='INVALID', invalid_reason=? WHERE id=?",
        (1 if (result.audit and result.audit.checked) else 0,
         1 if (result.audit and not result.audit.clean) else 0,
         reason, result.run_id))
    result.status = "INVALID"
    result.invalid_reason = reason


def _with_cash(profile: Profile, cash: int) -> Profile:
    from dataclasses import replace
    return replace(profile, available_cash=cash)
