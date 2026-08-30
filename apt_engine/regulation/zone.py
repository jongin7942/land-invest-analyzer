"""규제지역·토지거래허가구역 판정 (요구사항 22).

두 함수 모두 `as_of` 와 `scope` 를 **키워드 필수 인자**로 받는다. 기본값이 없어서
호출부가 매번 "언제 기준인지"를 적어야 하고, 그 덕에 지정기간이 끝난 토허를 현재로
표시하거나 외국인 대상 토허를 내국인에게 적용하는 사고가 호출 시점에 막힌다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from apt_engine import rules
from apt_engine.trace import Calc, Evidence

# 요구사항 22: 내국인/외국인 토허를 절대 섞지 않는다.
DOMESTIC, FOREIGN, ALL = "내국인", "외국인", "전체"


@dataclass(frozen=True)
class ZoneStatus:
    """규제지역 판정 결과."""
    lawd_cd: str
    as_of: str
    types: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    checked: bool = True          # False = 데이터 자체가 없어 판정 못 함

    @property
    def regulated(self) -> bool | None:
        """규제지역인가. 데이터가 없으면 False 가 아니라 None(확인 불가)이다."""
        if not self.checked:
            return None
        return bool(self.types)

    @property
    def label(self) -> str:
        if not self.checked:
            return "확인 불가 (규제지역 데이터 미입력)"
        if not self.types:
            return "비규제"
        return " · ".join(self.types) + ("  ⚠ 미검증" if self.unverified else "")


@dataclass(frozen=True)
class PermitStatus:
    """토지거래허가구역 판정 결과."""
    lawd_cd: str
    as_of: str
    scope: str
    designated: bool
    checked: bool = True
    residence_duty_months: int | None = None
    jeonse_succession_allowed: bool | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    resale_restriction: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    unverified: bool = False

    @property
    def can_use_jeonse(self) -> bool | None:
        """전세를 끼고 살 수 있는가 — Initial Equity 에서 보증금을 뺄지 결정한다.

        판정 데이터가 없으면 True 가 아니라 **None(확인 불가)** 이다.
        요구사항 62-11: 토허 여부를 확인하지 않고 갭투자 가능하다고 판단하지 않는다.
        """
        if not self.checked:
            return None
        if not self.designated:
            return True
        return bool(self.jeonse_succession_allowed)

    @property
    def label(self) -> str:
        if not self.checked:
            return "확인 불가 (토허 데이터 미입력)"
        if not self.designated:
            return f"토허 아님 ({self.scope} 기준)"
        parts = [f"토지거래허가구역 ({self.scope})"]
        if self.effective_to:
            parts.append(f"{self.effective_from}~{self.effective_to}")
        if self.residence_duty_months:
            parts.append(f"실거주 의무 {self.residence_duty_months}개월")
        parts.append("전세승계 " + ("가능" if self.jeonse_succession_allowed else "불가"))
        if self.unverified:
            parts.append("⚠ 미검증")
        return " · ".join(parts)


def zone_at(conn: sqlite3.Connection, lawd_cd: str, *, as_of: str | date,
            emd_name: str | None = None) -> ZoneStatus:
    """그 시점의 규제지역 지정 상태."""
    day = rules.as_ymd(as_of)
    rows = conn.execute(
        f"SELECT * FROM regulation_zone WHERE lawd_cd = ? AND {rules.effective_clause()}",
        (lawd_cd, day, day)).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM regulation_zone").fetchone()[0]
    if total == 0:
        return ZoneStatus(lawd_cd, day, checked=False)

    hits = [r for r in rows if r["emd_name"] is None or r["emd_name"] == emd_name]
    types = sorted({r["zone_type"] for r in hits})
    unverified = sorted({r["zone_type"] for r in hits if not r["last_verified"]})
    ev = [rules.Rule(r, 0).evidence for r in hits]
    return ZoneStatus(lawd_cd, day, types, ev, unverified)


def permit_zone_at(conn: sqlite3.Connection, lawd_cd: str, *, as_of: str | date,
                   scope: str, emd_name: str | None = None) -> PermitStatus:
    """그 시점의 토지거래허가구역 지정 상태.

    scope 는 필수다. '내국인'과 '외국인' 지정은 별개이고 섞으면 안 된다.
    """
    if scope not in (DOMESTIC, FOREIGN, ALL):
        raise ValueError(f"scope 는 {DOMESTIC}/{FOREIGN}/{ALL} 중 하나여야 합니다: {scope!r}")
    day = rules.as_ymd(as_of)

    total = conn.execute("SELECT COUNT(*) FROM land_permit_zone").fetchone()[0]
    if total == 0:
        return PermitStatus(lawd_cd, day, scope, designated=False, checked=False)

    rows = conn.execute(
        "SELECT * FROM land_permit_zone WHERE lawd_cd = ? "
        "AND effective_from <= ? AND effective_to >= ? "
        "AND target_scope IN (?, ?)",
        (lawd_cd, day, day, scope, ALL)).fetchall()
    hits = [r for r in rows if r["emd_name"] is None or r["emd_name"] == emd_name]

    if not hits:
        return PermitStatus(lawd_cd, day, scope, designated=False)

    # 여러 지정이 겹치면 가장 엄격한 것(전세승계 불가)을 따른다.
    hit = min(hits, key=lambda r: (r["jeonse_succession_allowed"],
                                   -(r["residence_duty_months"] or 0)))
    return PermitStatus(
        lawd_cd, day, scope, designated=True,
        residence_duty_months=hit["residence_duty_months"],
        jeonse_succession_allowed=bool(hit["jeonse_succession_allowed"]),
        effective_from=hit["effective_from"], effective_to=hit["effective_to"],
        resale_restriction=hit["resale_restriction"],
        evidence=[rules.Rule(h, 0).evidence for h in hits],
        unverified=not hit["last_verified"],
    )


def summarize(zone: ZoneStatus, permit: PermitStatus) -> Calc:
    """규제 판정을 하나의 Calc 로. 매수 판단 전에 가장 먼저 봐야 하는 값이다."""
    unknown = (zone.regulated is None) or (permit.can_use_jeonse is None)
    return Calc(
        value=None if unknown else (bool(zone.types) or permit.designated),
        unit="bool",
        formula="규제지역 지정 OR 토지거래허가구역 지정",
        inputs={"규제지역": zone.label, "토지거래허가구역": permit.label,
                "기준일": zone.as_of, "대상": permit.scope},
        intermediates={
            "전세 활용 가능": ("확인 불가" if permit.can_use_jeonse is None
                            else "가능" if permit.can_use_jeonse else "불가 — 실투자금에서 "
                                                                    "전세보증금을 빼면 안 됨"),
            **({"주의": "규제·토허 데이터가 입력되지 않아 판정할 수 없습니다. "
                        "`cli rule template` 로 서식을 받아 채워 넣으세요."} if unknown else {}),
        },
        evidence=tuple(zone.evidence + permit.evidence),
        grade="CONFIRMED" if not unknown else "ESTIMATED",
    )
