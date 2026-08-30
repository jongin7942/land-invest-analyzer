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
    region: str | None = None

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
            region=row["region"])

    def save(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO user_profile (name, available_cash, annual_income, "
            " existing_annual_payment, current_home_count, first_home_buyer, "
            " buyer_type, mortgage_term_years, interest_rate, repayment_type, region) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
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
            " updated_at=datetime('now','localtime')",
            (self.name, self.available_cash, self.annual_income,
             self.existing_annual_payment, self.current_home_count,
             int(self.first_home_buyer), self.buyer_type, self.mortgage_term_years,
             self.interest_rate, self.repayment_type, self.region))


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
        repayment_type=profile.repayment_type, use_mortgage=use_mortgage,
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
