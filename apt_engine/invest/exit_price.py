"""Liquid Exit Price 조립 — 상대가격 Mispricing(§35)과 정비사업 Option Value(§14)를
Terminal Wealth 입력으로 결합한다 (MASTER_SPEC §12·§13).

원칙
  * 점수에 더하지 않는다. 매도가 시나리오(Bear/Base/Bull)에만 들어간다.
  * 기준(Base) 매도가는 **현재 대표가격 그대로(명목 무성장)** 다. 미래 구매력·전세·상품성으로
    설명되는 Fundamental Exit Price(§12)는 아직 없으므로 성장률을 지어내지 않는다.
    Bear/Bull 배율은 cashflow.scenario.PRICE_ADJUST(감도용 가정)를 그대로 쓴다.
  * Mispricing 은 신뢰도에 따라 깎아서 Base·Bull 에만 얹는다(VERIFIED 1.0 · PROXY 0.5).
    Bear 에는 얹지 않는다 — 전달이 실패한 세계가 Bear 다.
  * Option Value 가 NOT_CALCULATED 이면 0 이 아니라 N/A 로 두고 매도가에 반영하지 않으며,
    그 사실을 결과에 남긴다.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from apt_engine.cashflow.scenario import PRICE_ADJUST

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "rules"

CONFIDENCE_FACTOR = {"VERIFIED": 1.0, "PROXY": 0.5}
SCENARIO_PROB = {"Bear": 0.25, "Base": 0.50, "Bull": 0.25}   # HEURISTIC — §12 확률은 백테스트 전
PROB_NOTE = "Bear/Base/Bull 확률 0.25/0.50/0.25 는 관측치가 아니라 가정이다(§12 시나리오 확률 미학습)"


@dataclass
class RelativeInput:
    mispricing: float | None
    status: str
    label: str
    consensus: str
    zone: str | None
    tier: int | None


@dataclass
class OptionInput:
    option_stage: int | None
    option_value: str | float
    status: str


@dataclass
class ExitSet:
    base_price: int
    prices: dict                      # {"Bear","Base","Bull"} → 매도가
    relative_uplift: float            # Base 에 얹은 비율(신뢰도 반영 후)
    relative_status: str
    option_applied: bool
    option_note: str
    notes: list = field(default_factory=list)


def load_relative() -> dict[tuple[int, str], RelativeInput]:
    out: dict[tuple[int, str], RelativeInput] = {}
    p = RULES / "relative_followers.csv"
    if not p.exists():
        return out
    with p.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            m = r.get("mispricing")
            out[(int(r["complex_id"]), r["band"])] = RelativeInput(
                float(m) if m not in (None, "", "None") else None,
                r.get("mispricing_status") or "NOT_CALCULATED", r.get("label") or "",
                r.get("consensus") or "", r.get("zone") or None,
                int(r["tier"]) if r.get("tier") not in (None, "", "None") else None)
    return out


def load_options() -> dict[int, OptionInput]:
    out: dict[int, OptionInput] = {}
    p = RULES / "option_stage_registry.csv"
    if not p.exists():
        return out
    with p.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            ov = r.get("option_value") or "NOT_CALCULATED"
            try:
                ovv: str | float = float(ov)
            except ValueError:
                ovv = ov
            out[int(r["complex_id"])] = OptionInput(
                int(r["option_stage"]) if r.get("option_stage") else None, ovv,
                r.get("stage_verification") or "")
    return out


def build(base_price: int, *, relative: RelativeInput | None, option: OptionInput | None,
          adjust: dict[str, float] | None = None) -> ExitSet:
    factors = adjust or PRICE_ADJUST
    notes: list[str] = ["Base = 현재 대표가격(명목 무성장). Fundamental Exit Price 미구현 — 성장률을 지어내지 않음"]
    uplift, rstatus = 0.0, "N/A"
    if relative is not None and relative.mispricing is not None:
        conf = CONFIDENCE_FACTOR.get(relative.status.split("(")[0], 0.0)
        uplift = max(0.0, relative.mispricing) * conf
        rstatus = relative.status
        notes.append(f"상대가격 Mispricing {relative.mispricing:+.3f} × 신뢰도 {conf:.1f} → Base/Bull 에 {uplift:+.3f}")
        if relative.label == "FALSE_CHEAP":
            uplift = 0.0
            notes.append("FALSE_CHEAP(구조적 가격차) → 상대가격 상승분 미반영")
    else:
        notes.append("상대가격 Mispricing 없음(N/A) → 미반영")

    applied = False
    if option is None:
        onote = "정비사업 옵션: 등재 없음(N/A)"
    elif isinstance(option.option_value, float):
        applied = True
        onote = f"정비사업 옵션가치 {option.option_value:+.3f} 반영(Stage {option.option_stage})"
    else:
        onote = f"정비사업 옵션: Stage {option.option_stage} · {option.option_value} → N/A, 매도가에 미반영(0 확정 아님)"
    notes.append(onote)

    prices = {}
    for k in ("Bear", "Base", "Bull"):
        f = factors.get(k, 1.0)
        if k != "Bear":
            f *= (1.0 + uplift)
        if applied and k != "Bear":
            f *= (1.0 + float(option.option_value))
        prices[k] = int(round(base_price * f / 1_000_000) * 1_000_000)
    return ExitSet(base_price, prices, uplift, rstatus, applied, onote, notes)


def expected_tw(net_profits: dict[str, int | None]) -> tuple[int | None, int | None]:
    """확률가중 기대 순이익(EXPECTED_TW)과 Wealth Floor(Bear). 하나라도 없으면 None."""
    if any(net_profits.get(k) is None for k in SCENARIO_PROB):
        return None, net_profits.get("Bear")
    e = sum(SCENARIO_PROB[k] * net_profits[k] for k in SCENARIO_PROB)
    return int(e), net_profits["Bear"]
