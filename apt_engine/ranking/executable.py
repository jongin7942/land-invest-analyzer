"""실행 랭킹 · Pre-Breakout Watch · 시장온도 · Coverage
   (신규 지시서 §37·§39·§40·§43·§46).

§37 이 요구하는 두 화면:

    TODAY / EXECUTABLE TOP   지금 이 가격에 실제로 사기 좋은 후보
    PRE-BREAKOUT WATCH       아직 확신은 낮지만 남은 알파가 크고 초기 조건이 형성된 후보

둘을 한 리스트로 섞으면 안 되는 이유는 **판단 기준이 다르기 때문**이다.
Executable 은 "지금 사도 되는가", Watch 는 "지켜볼 만한가" 다. Watch 후보가
Visible Movement 조건을 채우면 자동으로 Executable 로 올라온다.

§46 최종 질문이 이 모듈의 마지막 관문이다.

> "지금 이 가격에 이 Apartment × Area × Type 을 사는 것이, 현재 자기자본으로
>  실제 매수 가능한 모든 대안 및 CASH 보다 좋은가?"

YES 가 아니면 TOP 에 넣지 않는다. **모르면 YES 가 아니다.**
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from apt_engine import units
from apt_engine.features import stage as stage_mod
from apt_engine.invest import cash_candidate as cash_mod

# §40 시장 투자온도
OPPORTUNITY_RICH = "OPPORTUNITY_RICH"
SELECTIVE_BUY = "SELECTIVE_BUY"
PRICE_CAUTION = "PRICE_CAUTION"
CASH_DOMINANT = "CASH_DOMINANT"

TEMPERATURE_LABEL = {
    OPPORTUNITY_RICH: "기회가 많습니다 — 고를 수 있는 구간입니다",
    SELECTIVE_BUY: "일부만 살 만합니다 — 선별이 필요합니다",
    PRICE_CAUTION: "대부분 비쌉니다 — 서두를 이유가 없습니다",
    CASH_DOMINANT: "살 만한 것이 거의 없습니다 — 현금이 우위입니다",
}

# 온도 경계. **판정 기준이지 관측이 아니다.**
TEMPERATURE_EDGES = ((0.20, OPPORTUNITY_RICH), (0.08, SELECTIVE_BUY),
                     (0.02, PRICE_CAUTION))
TEMPERATURE_NOTE = ("시장온도는 개별 후보 점수를 강제로 바꾸지 않습니다(§40). "
                    "지금 시장이 어떤 상태인지 보여주는 배경입니다")

# §43 Coverage 판정 — 이 아래면 '수도권 전체' 라고 쓰지 않는다
FULL_COVERAGE_MIN = 0.80


@dataclass(frozen=True)
class PriceBands:
    """§39 — 하나의 점수가 아니라 가격대별 투자매력."""
    strong_buy: int | None
    fair: int | None
    do_not_buy: int | None
    current: int
    competitive_note: str = ""

    def verdict(self, price: int | None = None) -> str:
        p = price if price is not None else self.current
        if self.do_not_buy is not None and p >= self.do_not_buy:
            return "DO NOT BUY"
        if self.strong_buy is not None and p <= self.strong_buy:
            return "STRONG BUY"
        if self.fair is not None and p <= self.fair:
            return "BUY"
        if self.do_not_buy is not None:
            return "WAIT"
        return "확인 불가"

    @property
    def label(self) -> str:
        def f(v):
            return units.fmt_eok(v) if v is not None else "확인 불가"
        return (f"≤{f(self.strong_buy)} STRONG BUY · "
                f"≤{f(self.fair)} BUY · "
                f"≥{f(self.do_not_buy)} DO NOT BUY  "
                f"(현재 {units.fmt_eok(self.current)} → {self.verdict()})")


def price_bands(current_price: int, *, entry_position: float | None,
                alternatives_quality: float | None = None) -> PriceBands:
    """가격대별 판정선 (§39).

    `entry_position` 이 0.15 면 "지금 가격이 매수구간 하위 15%" 라는 뜻이다.
    거기서 STRONG/FAIR/WAIT 선의 **금액**을 역산한다.

    `alternatives_quality` 같은 자기자본으로 살 수 있는 다른 후보들의 질(0~1).
    좋은 대안이 많으면 이 후보의 최대 매수가격을 **낮춘다** — §39 가 요구한
    Competitive Buy Price 다. 굳이 이걸 살 이유가 줄기 때문이다.
    """
    if entry_position is None or entry_position <= 0:
        return PriceBands(None, None, None, current_price,
                          "매수구간 위치를 몰라 가격대를 만들지 않았습니다")

    # 현재가와 위치로 구간 전체의 스케일을 되돌린다.
    unit = current_price / entry_position
    strong = int(unit * 0.15)
    fair = int(unit * 0.50)
    do_not = int(unit * 1.00)

    note = ""
    if alternatives_quality is not None:
        # 대안이 좋을수록 (0→1) 최대 5%까지 매수가를 낮춘다.
        shrink = 1.0 - 0.05 * max(0.0, min(1.0, alternatives_quality))
        strong, fair, do_not = int(strong * shrink), int(fair * shrink), int(do_not * shrink)
        note = (f"같은 자기자본 대안의 질이 {alternatives_quality:.2f} 라 "
                f"매수가를 {(1 - shrink):.1%} 낮췄습니다(§39 Competitive Buy Price)")

    return PriceBands(strong, fair, do_not, current_price, note)


@dataclass(frozen=True)
class Split:
    executable: list = field(default_factory=list)
    watch: list = field(default_factory=list)
    excluded: list[tuple[int, str]] = field(default_factory=list)
    cash_rank: int | None = None
    temperature: str | None = None
    temperature_reason: str = ""

    @property
    def summary(self) -> str:
        head = (f"EXECUTABLE {len(self.executable)} · "
                f"PRE-BREAKOUT WATCH {len(self.watch)}")
        if self.temperature:
            head += f"\n  시장온도 {self.temperature} — {TEMPERATURE_LABEL[self.temperature]}"
        if self.cash_rank is not None:
            head += f"\n  CASH 순위 {self.cash_rank}위"
        return head


def split(candidates, stages: dict[int, stage_mod.Verdict], *,
          cash: cash_mod.CashOption,
          expected_returns: dict[int, float] | None = None,
          risk_penalties: dict[int, float] | None = None,
          limit: int = 10) -> Split:
    """후보를 두 화면으로 나누고 CASH 를 그 사이에 끼워 넣는다 (§3·§37·§46)."""
    returns = expected_returns or {}
    penalties = risk_penalties or {}

    executable, watch = [], []
    excluded: list[tuple[int, str]] = []

    for c in candidates:
        v = stages.get(c.complex_id)
        if v is None:
            excluded.append((c.complex_id, "Stage 를 판정하지 못했습니다"))
            continue
        if v.stage in stage_mod.EXCLUDED_FROM_EXECUTABLE:
            if stage_mod.watchable(v):
                watch.append(c)
            else:
                excluded.append((c.complex_id,
                                 f"{v.stage} — {stage_mod.STAGE_LABEL[v.stage]}"))
            continue

        # §46 최종 질문 — CASH 보다 나은가. 모르면 넣지 않는다.
        better, why = cash_mod.beats(
            cash, candidate_return=returns.get(c.complex_id),
            risk_penalty=penalties.get(c.complex_id, 0.0))
        if better is False:
            excluded.append((c.complex_id, why))
            if stage_mod.watchable(v):
                watch.append(c)
            continue
        if better is None and returns:
            # 다른 후보는 계산됐는데 이것만 못 냈다면 근거가 없는 것이다.
            excluded.append((c.complex_id, why))
            continue

        if stage_mod.executable(v):
            executable.append(c)
        if stage_mod.watchable(v):
            watch.append(c)

    cash_rank = _cash_rank(executable, cash, returns, penalties)
    temperature, why = _temperature(candidates, executable)

    return Split(executable[:limit], watch[:limit], excluded, cash_rank,
                 temperature, why)


def _cash_rank(executable, cash: cash_mod.CashOption,
               returns: dict[int, float],
               penalties: dict[int, float]) -> int | None:
    """CASH 가 몇 위인가 (§3). 기준선을 모르면 순위를 만들지 않는다."""
    if not cash.known:
        return None
    hurdle = cash.expected_return
    better_than_cash = 0
    for c in executable:
        r = returns.get(c.complex_id)
        if r is None:
            continue
        if r * (1 - penalties.get(c.complex_id, 0.0)) > hurdle:
            better_than_cash += 1
    return better_than_cash + 1


def _temperature(all_candidates, executable) -> tuple[str | None, str]:
    """§40 — 지금 시장에 살 만한 것이 얼마나 있나."""
    total = len(all_candidates)
    if not total:
        return None, "후보가 없어 시장온도를 내지 않습니다"
    share = len(executable) / total
    temp = CASH_DOMINANT
    for edge, name in TEMPERATURE_EDGES:
        if share >= edge:
            temp = name
            break
    return temp, (f"매수 가능 후보 {len(executable)}/{total} = {share:.1%}. "
                  f"{TEMPERATURE_NOTE}")


# ── §43 Universe Coverage ────────────────────────────────────────────

@dataclass(frozen=True)
class Coverage:
    scanned: int
    known: int
    parts: dict[str, float | None] = field(default_factory=dict)

    @property
    def ratio(self) -> float | None:
        return self.scanned / self.known if self.known else None

    @property
    def verdict(self) -> str:
        r = self.ratio
        if r is not None and r >= FULL_COVERAGE_MIN:
            return "FULL_UNIVERSE"
        return "PARTIAL_VERIFIED_UNIVERSE"

    @property
    def title(self) -> str:
        """화면 제목. 다 못 봤으면 '수도권 전체' 라고 쓰지 않는다(§43)."""
        if self.verdict == "FULL_UNIVERSE":
            return "FULL UNIVERSE TOP10"
        r = self.ratio
        share = f"{r:.0%}" if r is not None else "확인 불가"
        return f"PARTIAL VERIFIED UNIVERSE TOP10 (커버리지 {share})"

    @property
    def label(self) -> str:
        lines = [self.title, f"  스캔 {self.scanned:,} / 모수 {self.known:,}"]
        for key, value in self.parts.items():
            lines.append(f"  {key}: " +
                         (f"{value:.1%}" if value is not None else "확인 불가"))
        return "\n".join(lines)


def measure(conn: sqlite3.Connection, *, as_of: str, area_band: str,
            scanned_ids: set[int]) -> Coverage:
    """§43 — 무엇을 얼마나 봤는가.

    모수를 못 구하면 비율을 **1.0 으로 가정하지 않는다.** 모르면 None 이고,
    그러면 판정은 PARTIAL 로 떨어진다 — 모르는 것을 '전체' 라고 부르지 않는다.
    """
    row = conn.execute("SELECT COUNT(*) FROM complex WHERE canonical_id IS NULL"
                       ).fetchone()
    known = int(row[0]) if row else 0
    scanned = len(scanned_ids)

    parts: dict[str, float | None] = {}
    parts["단지수 커버리지"] = (scanned / known) if known else None

    if scanned_ids:
        marks = ",".join("?" * len(scanned_ids))
        ids = tuple(scanned_ids)
        hh = conn.execute(
            f"SELECT SUM(apt_households) FROM complex WHERE id IN ({marks})",
            ids).fetchone()[0]
        total_hh = conn.execute(
            "SELECT SUM(apt_households) FROM complex "
            " WHERE canonical_id IS NULL").fetchone()[0]
        parts["세대수 커버리지"] = (hh / total_hh) if (hh and total_hh) else None

        regions = conn.execute(
            f"SELECT COUNT(DISTINCT lawd_cd) FROM complex WHERE id IN ({marks})",
            ids).fetchone()[0]
        total_regions = conn.execute(
            "SELECT COUNT(DISTINCT lawd_cd) FROM complex").fetchone()[0]
        parts["시군구 커버리지"] = ((regions / total_regions)
                             if (regions and total_regions) else None)
    else:
        parts["세대수 커버리지"] = None
        parts["시군구 커버리지"] = None

    return Coverage(scanned, known, parts)


def save_coverage(conn: sqlite3.Connection, coverage: Coverage, *, as_of: str,
                  area_band: str) -> None:
    conn.execute(
        "INSERT INTO universe_coverage (as_of, area_band, scanned_n, known_n, "
        " household_coverage, region_coverage, verdict, note) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(as_of, area_band) DO UPDATE SET "
        " scanned_n=excluded.scanned_n, known_n=excluded.known_n, "
        " household_coverage=excluded.household_coverage, "
        " region_coverage=excluded.region_coverage, verdict=excluded.verdict",
        (as_of, area_band, coverage.scanned, coverage.known,
         coverage.parts.get("세대수 커버리지"),
         coverage.parts.get("시군구 커버리지"), coverage.verdict,
         coverage.title))
