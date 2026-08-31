"""Blind Candidate Generation — 수도권 전체에서 동일 규칙으로 후보를 만든다 (§1·§2).

파이프라인 순서가 지시서에 고정돼 있다.

    수도권 전체 → Blind Candidate Generation → TOP100 → Deep Dive TOP30
    → Final TOP10 → **그 다음에야** 사용자 관심단지 표시

이 모듈은 첫 칸만 담당한다. 그리고 **관심단지 테이블을 아예 import 하지 않는다** —
읽을 방법이 없으면 우대할 방법도 없다. tests/test_blind.py 가 AST 로 확인한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from apt_engine import area as area_mod
from apt_engine.blind import cutoff as cutoff_mod


@dataclass(frozen=True)
class UniverseRow:
    complex_id: int
    lawd_cd: str
    area_band: str
    as_of_ym: str
    representative_price: int
    sample_n: int
    confidence: str
    apt_households: int | None
    approval_year: int | None

    @property
    def price_per_m2(self) -> float | None:
        try:
            return self.representative_price / float(self.area_band)
        except (TypeError, ValueError, ZeroDivisionError):
            return None


@dataclass(frozen=True)
class Universe:
    as_of: cutoff_mod.AsOf
    area_band: str
    rows: list[UniverseRow] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def summary(self) -> str:
        dropped = " · ".join(f"{k} {v}" for k, v in self.excluded.items())
        return (f"{self.as_of.day} 기준 후보 {len(self.rows)}개"
                + (f"  (제외: {dropped})" if dropped else ""))


# 후보가 되려면 최소한 이건 있어야 한다. 없는 걸 추정해서 채우지 않는다.
MIN_SAMPLE_N = 3


def build(conn: sqlite3.Connection, *, as_of: cutoff_mod.AsOf,
          area_band: str | None = None, lawd_cd: str | None = None,
          min_sample_n: int = MIN_SAMPLE_N,
          window_months: int = 12) -> Universe:
    """컷오프 시점의 후보 전체.

    이름·지역명으로 거르지 않는다. 거르는 기준은 **그 시점에 가격을 알 수 있었는가**
    하나뿐이다.
    """
    band = area_band or area_mod.DEFAULT_BAND
    observable = as_of.observable          # 신고 지연을 반영한 실제 관측 가능 시점
    # 스냅샷은 월 단위라 '진행 중인 달' 은 아직 완성되지 않았다.
    # 관측 가능 시점이 속한 달까지 포함하면, 그 달 말에 신고될 거래를 미리 쓰게 된다.
    # 그래서 **마지막으로 완료된 달**까지만 본다.
    end_ym = _shift_ym(observable.ym, -1)
    start_ym = _shift_ym(end_ym, -window_months + 1)

    rows: list[UniverseRow] = []
    excluded = {"표본부족": 0, "가격없음": 0}

    with cutoff_mod.guard(conn, observable) as guarded:
        # price_snapshot 은 as_of_ym 으로 컷오프된다. guard 가 그걸 확인한다.
        found = guarded.execute(
            "SELECT s.complex_id, s.area_band, s.as_of_ym, s.representative_price, "
            "       s.sample_n, s.confidence, c.lawd_cd, c.apt_households, "
            "       c.approval_year "
            "  FROM price_snapshot s JOIN complex c ON c.id = s.complex_id "
            " WHERE s.area_band = ? AND s.as_of_ym >= ? AND s.as_of_ym <= ? "
            + (" AND c.lawd_cd = ?" if lawd_cd else "") +
            " ORDER BY s.complex_id, s.as_of_ym DESC",
            (band, start_ym, end_ym) + ((lawd_cd,) if lawd_cd else ())
        ).fetchall()

    seen: set[int] = set()
    for r in found:
        cid = int(r["complex_id"])
        if cid in seen:                    # 단지당 가장 최근 스냅샷 하나
            continue
        seen.add(cid)
        if not r["representative_price"]:
            excluded["가격없음"] += 1
            continue
        if (r["sample_n"] or 0) < min_sample_n:
            excluded["표본부족"] += 1
            continue
        rows.append(UniverseRow(
            complex_id=cid, lawd_cd=r["lawd_cd"], area_band=r["area_band"],
            as_of_ym=r["as_of_ym"],
            representative_price=int(r["representative_price"]),
            sample_n=int(r["sample_n"] or 0), confidence=r["confidence"],
            apt_households=r["apt_households"], approval_year=r["approval_year"]))

    rows.sort(key=lambda u: u.complex_id)   # 이름이 아니라 id 순 — 재현성
    return Universe(as_of, band, rows, {k: v for k, v in excluded.items() if v})


def _shift_ym(ym: str, months: int) -> str:
    year, month = int(ym[:4]), int(ym[4:6])
    total = year * 12 + (month - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def ranking_fingerprint_input(universe: "Universe") -> list[tuple]:
    """Placebo Test 용 지문 — 이름이 아니라 순서·가격만 본다.

    익명화 전후로 이 값이 같으면, 단지명이 후보 생성에 영향을 주지 않은 것이다.
    """
    return [(i, r.representative_price, r.sample_n, r.area_band)
            for i, r in enumerate(universe.rows, start=1)]
