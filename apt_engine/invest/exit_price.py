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
class TransitInput:
    """§30.3 교통 선점(착공된 1급 노선 역 1km & 미개통). 사건연구 실측: 착공~개통 상대수익 +5.1%p(급행 +8.6%p), 개통 뒤 되돌림.
    표본 14·6건(PROXY) — 종인님 지시(2026-09-06)로 표본 30건 미만이어도 Bull 에만 반영한다."""
    line: str
    station: str
    km: float
    express: bool
    expected_open_ym: str | None


TRANSIT_PREOPEN_UPLIFT = {"local": 0.051, "express": 0.086}     # PROXY, 연구로그 §30.3


def load_transit_preopen() -> dict[int, TransitInput]:
    """DB transit_station(status='착공', 1급 노선) 1km 안 단지 → TransitInput. 개통 뒤에는 자동 소멸(status 가 개통으로 바뀌면 빠짐)."""
    from apt_engine.db.connection import get_conn
    from apt_engine.exitprice import panel as _pm
    from apt_engine.relative import store as _st
    out: dict[int, TransitInput] = {}
    with get_conn() as conn:
        st = [(float(r["lat"]), float(r["lon"]), r["name"], r["line_id"], r["expected_open_ym"]) for r in conn.execute(
            "SELECT s.lat, s.lon, s.name, p.line_id, s.expected_open_ym FROM transit_station s JOIN transit_project p ON p.id=s.project_id "
            "WHERE s.lat IS NOT NULL AND s.status='착공' AND p.destination_tier=1")]
        cx = _st.load_complexes(conn)
    for c in cx.values():
        best = None
        for la, lo, nm, line, exp in st:
            d = _st.haversine_m(c.lat, c.lon, la, lo) / 1000.0
            if d <= 1.0 and (best is None or d < best.km):
                best = TransitInput(line, nm, round(d, 2), _pm.is_express(line, nm), exp)
        if best:
            out[c.id] = best
    return out


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


@dataclass
class Prediction:
    bear: float
    base: float
    bull: float
    model: str
    status: str


def load_predictions(path=None) -> dict[tuple[int, str], Prediction]:
    """Exit Price Engine(§12, tools/run_exit_price.py) 의 5년 배율. 없으면 빈 dict → 무성장 Base. path 로 모델 변형(안정형·공격형) 파일 지정 가능."""
    out: dict[tuple[int, str], Prediction] = {}
    p = Path(path) if path else RULES / "exit_price_2026.csv"
    if not p.exists():
        return out
    with p.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[(int(r["complex_id"]), r["band"])] = Prediction(
                float(r["bear_factor"]), float(r["base_factor"]), float(r["bull_factor"]), r["model"], r["status"])
    return out


def build(base_price: int, *, relative: RelativeInput | None, option: OptionInput | None,
          adjust: dict[str, float] | None = None, prediction: Prediction | None = None, transit: TransitInput | None = None) -> ExitSet:
    if prediction is not None:
        factors = {"Bear": prediction.bear, "Base": prediction.base, "Bull": prediction.bull}
        notes: list[str] = [f"Base/Bear/Bull = Exit Price Engine 예측(잔차 P50/P20/P80) · {prediction.model} · {prediction.status}"]
    else:
        factors = adjust or PRICE_ADJUST
        notes = ["Base = 현재 대표가격(명목 무성장). Exit Price Engine 예측 없음 — 성장률을 지어내지 않음"]
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

    t_up = 0.0
    if transit is not None:
        t_up = TRANSIT_PREOPEN_UPLIFT["express" if transit.express else "local"]
        notes.append(f"교통 선점(PROXY, §30.3): 착공된 1급 {transit.line} {transit.station} {transit.km}km{' 급행' if transit.express else ''}"
                     f"{' · 개통예정 ' + str(transit.expected_open_ym) if transit.expected_open_ym else ''} → Bull 에만 {t_up:+.3f} (착공~개통 실측, 개통 뒤 되돌림이라 Base 미반영)")
    prices = {}
    for k in ("Bear", "Base", "Bull"):
        f = factors.get(k, 1.0)
        if k != "Bear":
            f *= (1.0 + uplift)
        if applied and k != "Bear":
            f *= (1.0 + float(option.option_value))
        if k == "Bull":
            f *= (1.0 + t_up)
        prices[k] = int(round(base_price * f / 1_000_000) * 1_000_000)
    return ExitSet(base_price, prices, uplift, rstatus, applied, onote, notes)


def expected_tw(net_profits: dict[str, int | None]) -> tuple[int | None, int | None]:
    """확률가중 기대 순이익(EXPECTED_TW)과 Wealth Floor(Bear). 하나라도 없으면 None."""
    if any(net_profits.get(k) is None for k in SCENARIO_PROB):
        return None, net_profits.get("Bear")
    e = sum(SCENARIO_PROB[k] * net_profits[k] for k in SCENARIO_PROB)
    return int(e), net_profits["Bear"]
