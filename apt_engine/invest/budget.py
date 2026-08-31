"""내 현금으로 살 수 있는 아파트 (지시 §13·§14).

사용자가 "현금 3억" 이라고 하면 매매가 3억 이하를 찾는 게 아니라,

    SELF_CAPITAL_REQUIRED ≤ 300,000,000

인 아파트를 찾는다. 대출이 나오고 전세를 승계하면 6억짜리도 실투자금은 2억일 수
있고, 대출이 막히면 4억짜리도 실투자금은 4.3억이다. 매매가로 거르면 둘 다 틀린다.

실투자금을 **확정하지 못한 단지는 '가능' 목록에 넣지 않는다.** 모르는 비용을 0으로
세면 살 수 없는 집이 살 수 있는 집으로 올라온다. 대신 '확인 불가' 목록에 따로 담아
무엇이 없어서 판단하지 못했는지 알린다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import area as area_mod, regions, rules, units
from apt_engine.cash import self_capital as capital_mod
from apt_engine.repo import apt as repo


@dataclass(frozen=True)
class Profile:
    """사용자 프로필 (요구사항 24) — 코드에 하드코딩하지 않는다."""
    name: str = "기본"
    available_cash: int | None = None
    annual_income: int | None = None
    existing_annual_payment: int = 0
    current_home_count: int = 0
    first_home_buyer: bool = False
    buyer_type: str = "개인"
    mortgage_term_years: int = 30
    interest_rate: float | None = None
    repayment_type: str = "원리금균등"
    lender_type: str = "은행"          # 은행권 DSR 40% / 비은행권 50%
    region: str | None = None
    # §3 CASH 후보의 기준선. 추정하지 않는다 — 없으면 CASH 순위를 못 만든다.
    cash_hurdle_rate: float | None = None
    # §2 RequiredCash 구성요소. 없으면 0 이 아니라 '확인 불가' 로 다룬다.
    initial_repair_cost: int | None = None

    @classmethod
    def load(cls, conn: sqlite3.Connection, name: str) -> "Profile | None":
        row = conn.execute("SELECT * FROM user_profile WHERE name = ?",
                           (name,)).fetchone()
        if row is None:
            return None
        return cls(
            name=row["name"], available_cash=row["available_cash"],
            annual_income=row["annual_income"],
            existing_annual_payment=row["existing_annual_payment"] or 0,
            current_home_count=row["current_home_count"] or 0,
            first_home_buyer=bool(row["first_home_buyer"]),
            buyer_type=row["buyer_type"] or "개인",
            mortgage_term_years=row["mortgage_term_years"] or 30,
            interest_rate=row["interest_rate"],
            repayment_type=row["repayment_type"] or "원리금균등",
            lender_type=row["lender_type"] or "은행",
            region=row["region"],
            cash_hurdle_rate=row["cash_hurdle_rate"],
            initial_repair_cost=row["initial_repair_cost"])

    def save(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO user_profile (name, available_cash, annual_income, "
            " existing_annual_payment, current_home_count, first_home_buyer, "
            " buyer_type, mortgage_term_years, interest_rate, repayment_type, "
            " lender_type, region, cash_hurdle_rate, initial_repair_cost) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET "
            " available_cash=excluded.available_cash, "
            " annual_income=excluded.annual_income, "
            " existing_annual_payment=excluded.existing_annual_payment, "
            " current_home_count=excluded.current_home_count, "
            " first_home_buyer=excluded.first_home_buyer, "
            " buyer_type=excluded.buyer_type, "
            " mortgage_term_years=excluded.mortgage_term_years, "
            " interest_rate=excluded.interest_rate, "
            " repayment_type=excluded.repayment_type, region=excluded.region, "
            " cash_hurdle_rate=excluded.cash_hurdle_rate, "
            " initial_repair_cost=excluded.initial_repair_cost, "
            " updated_at=datetime('now','localtime')",
            (self.name, self.available_cash, self.annual_income,
             self.existing_annual_payment, self.current_home_count,
             int(self.first_home_buyer), self.buyer_type, self.mortgage_term_years,
             self.interest_rate, self.repayment_type, self.lender_type,
             self.region, self.cash_hurdle_rate, self.initial_repair_cost))


@dataclass(frozen=True)
class Candidate:
    complex_id: int
    name: str
    lawd_cd: str
    area_band: str
    price: int
    price_as_of: str
    jeonse: int | None
    capital: capital_mod.SelfCapital

    @property
    def required(self) -> int | None:
        return self.capital.required

    @property
    def region_name(self) -> str:
        return regions.name_of(self.lawd_cd)

    def utilization(self, cash: int) -> float | None:
        return self.capital.cash_utilization(cash)


@dataclass(frozen=True)
class Screen:
    cash: int
    affordable: list[Candidate] = field(default_factory=list)
    too_expensive: list[Candidate] = field(default_factory=list)
    undecidable: list[Candidate] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (f"현금 {units.fmt_eok(self.cash)} 기준 — "
                f"매수 가능 {len(self.affordable)}개 · "
                f"초과 {len(self.too_expensive)}개 · "
                f"확인 불가 {len(self.undecidable)}개")


def evaluate(conn: sqlite3.Connection, complex_id: int, *, profile: Profile,
             as_of: str | date, area_band: str | None = None,
             assume_jeonse: bool = False, use_mortgage: bool = True,
             price_override: int | None = None,
             allow_unverified: bool = False) -> Candidate | None:
    """단지 하나의 실투자금. 대표가격이 없으면 None — 가격을 지어내지 않는다."""
    band = area_band or area_mod.DEFAULT_BAND
    row = conn.execute("SELECT * FROM complex WHERE id = ?", (complex_id,)).fetchone()
    if row is None:
        return None

    price, price_as_of = price_override, "사용자 입력"
    if price is None:
        snap = repo.latest_price_snapshot(conn, complex_id, band)
        if snap is None or not snap["representative_price"]:
            return None
        price, price_as_of = int(snap["representative_price"]), snap["as_of_ym"]

    jeonse = None
    if assume_jeonse:
        js = repo.latest_jeonse_snapshot(conn, complex_id, band)
        if js is not None and js["representative_price"]:
            jeonse = int(js["representative_price"])

    exclusive = None
    try:
        exclusive = float(band)
    except ValueError:
        exclusive = None

    capital = capital_mod.compute(
        conn, price=price, as_of=as_of, lawd_cd=row["lawd_cd"],
        emd_name=row["emd_name"], current_home_count=profile.current_home_count,
        exclusive_area_m2=exclusive, first_home_buyer=profile.first_home_buyer,
        buyer_type=profile.buyer_type, annual_income=profile.annual_income,
        existing_annual_payment=profile.existing_annual_payment,
        interest_rate=profile.interest_rate,
        mortgage_term_years=profile.mortgage_term_years,
        repayment_type=profile.repayment_type, lender_type=profile.lender_type,
        use_mortgage=use_mortgage,
        jeonse_deposit=jeonse, assume_jeonse=assume_jeonse,
        region=profile.region or regions.sido_of(row["lawd_cd"]),
        allow_unverified=allow_unverified)

    return Candidate(complex_id, row["name"], row["lawd_cd"], band, price,
                     str(price_as_of), jeonse, capital)


def screen(conn: sqlite3.Connection, *, profile: Profile, as_of: str | date,
           area_band: str | None = None, lawd_cd: str | None = None,
           assume_jeonse: bool = False, use_mortgage: bool = True,
           limit: int = 200, allow_unverified: bool = False) -> Screen:
    """실투자금 기준으로 매수 가능한 단지를 고른다."""
    if not profile.available_cash:
        raise ValueError("가용 현금(available_cash)이 없으면 매수 가능 판정을 할 수 없습니다")
    band = area_band or area_mod.DEFAULT_BAND

    sql = ("SELECT DISTINCT s.complex_id FROM price_snapshot s "
           " JOIN complex c ON c.id = s.complex_id "
           " WHERE s.area_band = ? AND s.representative_price IS NOT NULL")
    params: list = [band]
    if lawd_cd:
        sql += " AND c.lawd_cd = ?"
        params.append(lawd_cd)
    sql += " LIMIT ?"
    params.append(limit)

    affordable: list[Candidate] = []
    too_expensive: list[Candidate] = []
    undecidable: list[Candidate] = []
    for (cid,) in conn.execute(sql, params):
        got = evaluate(conn, cid, profile=profile, as_of=as_of, area_band=band,
                       assume_jeonse=assume_jeonse, use_mortgage=use_mortgage,
                       allow_unverified=allow_unverified)
        if got is None:
            continue
        verdict = got.capital.affordable(profile.available_cash)
        if verdict is None:
            undecidable.append(got)
        elif verdict:
            affordable.append(got)
        else:
            too_expensive.append(got)

    affordable.sort(key=lambda c: -(c.required or 0))
    too_expensive.sort(key=lambda c: (c.required or 0))
    return Screen(profile.available_cash, affordable, too_expensive, undecidable)


# ── 랭킹 (§12·§16) ────────────────────────────────────────────────────
# "3억짜리 아파트 찾기" 가 아니라 "현금 3억으로 살 수 있는 것 중 가장 좋은 것 찾기".
# 그래서 순서가 고정돼 있다 — **먼저 거르고, 그다음 줄 세운다.**

@dataclass(frozen=True)
class Ranked:
    candidate: Candidate
    metrics: object                    # invest.ranking.Metrics
    sort_key: float | None

    @property
    def comparable(self) -> bool:
        return self.metrics.comparable


def rank(conn: sqlite3.Connection, *, profile: Profile, as_of: str | date,
         future_prices: dict[int, int] | None = None,
         downside_prices: dict[int, int] | None = None,
         holding_years: int = 5, area_band: str | None = None,
         lawd_cd: str | None = None, assume_jeonse: bool = False,
         use_mortgage: bool = True, scan: int = 200,
         allow_unverified: bool = False) -> tuple[list[Ranked], Screen]:
    """매수 가능한 단지만 골라 EXPECTED_ROE 순으로 줄 세운다.

    `future_prices` 는 {complex_id: 예상 매도가}. 주지 않은 단지는 수익률을
    계산하지 않는다 — 상승률을 지어내서 순위를 만들지 않는다. 그런 단지는
    `comparable=False` 로 남아 비교 대상에서 빠진다.
    """
    from apt_engine.invest import ranking as ranking_mod
    from apt_engine.invest import roe as roe_mod

    screened = screen(conn, profile=profile, as_of=as_of, area_band=area_band,
                      lawd_cd=lawd_cd, assume_jeonse=assume_jeonse,
                      use_mortgage=use_mortgage, limit=scan,
                      allow_unverified=allow_unverified)

    out: list[Ranked] = []
    for candidate in screened.affordable:      # ← 여기서 이미 실투자금 필터를 통과했다
        future = (future_prices or {}).get(candidate.complex_id)
        expected = roe_mod.expected_return(
            conn, capital=candidate.capital, future_sale_price=future, as_of=as_of,
            holding_years=holding_years, annual_rate=profile.interest_rate,
            mortgage_term_years=profile.mortgage_term_years,
            repayment_type=profile.repayment_type,
            region=profile.region or regions.sido_of(candidate.lawd_cd),
            house_count=profile.current_home_count + 1,
            allow_unverified=allow_unverified)
        metrics = ranking_mod.build(
            conn, capital=candidate.capital, expected=expected,
            available_cash=profile.available_cash,
            downside_sale_price=(downside_prices or {}).get(candidate.complex_id),
            as_of=as_of, holding_years=holding_years,
            annual_rate=profile.interest_rate,
            region=profile.region or regions.sido_of(candidate.lawd_cd),
            house_count=profile.current_home_count + 1,
            allow_unverified=allow_unverified)
        out.append(Ranked(candidate, metrics, metrics.expected_roe))

    # ROE 를 구한 단지가 먼저, 그중 높은 순. 못 구한 단지는 뒤로 밀되 버리지 않는다.
    out.sort(key=lambda r: (r.sort_key is None, -(r.sort_key or 0)))
    return out, screened
