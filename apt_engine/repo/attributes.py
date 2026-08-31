"""단지 속성 — 값마다 출처가 붙는다 (지시서 §2·§3).

학군·생활권·업무지 접근성처럼 **공식 API 가 없어 사람이 넣는 값**들을 담는다.
컬럼이 아니라 행으로 두는 이유는, 값 하나하나에 출처·시점·신뢰도가 따라붙어야
하고 항목이 계속 늘기 때문이다.

같은 key 에 출처가 다른 값이 여러 개 있는 건 정상이다. 그중 무엇을 쓸지는
`best()` 가 **출처 등급(공식이 우선) → 최신 → 신뢰도** 순으로 정한다.
값이 서로 다르면 조용히 이기지 않고 `source_conflict` 에 기록한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from apt_engine import rules

# 자주 쓰는 키. 자유롭게 늘릴 수 있지만, 오타로 새 키가 생기는 걸 막으려고 모아 둔다.
SCHOOL_ZONE = "school_zone"
LIFE_ZONE = "life_zone"
PARKING_RATIO = "parking_ratio"
LAND_SHARE_M2 = "land_share_m2"
STATION_WALK_MIN = "station_walk_min"
KNOWN_KEYS = (SCHOOL_ZONE, LIFE_ZONE, PARKING_RATIO, LAND_SHARE_M2, STATION_WALK_MIN)


@dataclass(frozen=True)
class Attribute:
    complex_id: int
    key: str
    text: str | None
    num: float | None
    unit: str | None
    as_of: str | None
    source_name: str
    source_tier: int
    confidence: str
    verification: str

    @property
    def value(self):
        return self.num if self.num is not None else self.text

    @property
    def label(self) -> str:
        v = f"{self.num:g}{self.unit or ''}" if self.num is not None else (self.text or "")
        return f"{v}  [{self.source_name} · tier{self.source_tier} · {self.verification}]"


def put(conn: sqlite3.Connection, complex_id: int, key: str, *,
        text: str | None = None, num: float | None = None, unit: str | None = None,
        as_of: str | None = None, source_name: str, source_tier: int,
        source_url: str | None = None, confidence: str = "MEDIUM",
        verification: str = "NEEDS_VERIFICATION", note: str | None = None) -> None:
    """값 하나. 같은 (단지·키·출처·시점)이면 덮어쓴다 — 다른 출처는 따로 남는다."""
    if text is None and num is None:
        raise ValueError(f"'{key}' 값이 비었습니다. 모르는 값은 행을 만들지 않습니다")
    conn.execute(
        "INSERT INTO complex_attribute (complex_id, attr_key, value_text, value_num, "
        " unit, as_of, source_name, source_url, source_tier, confidence, "
        " verification, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(complex_id, attr_key, source_name, as_of) DO UPDATE SET "
        " value_text=excluded.value_text, value_num=excluded.value_num, "
        " unit=excluded.unit, source_url=excluded.source_url, "
        " source_tier=excluded.source_tier, confidence=excluded.confidence, "
        " verification=excluded.verification, note=excluded.note",
        (complex_id, key, text, num, unit, as_of, source_name, source_url,
         source_tier, confidence, verification, note))


def _rows(conn: sqlite3.Connection, complex_id: int, key: str,
          as_of: str | None) -> list[Attribute]:
    sql = ("SELECT * FROM complex_attribute WHERE complex_id = ? AND attr_key = ?")
    params: list = [complex_id, key]
    if as_of:
        # 그 시점에 알 수 있었던 값만. as_of 가 비어 있는 행은 시점 불명이라 제외한다 —
        # 백테스트에서 시점 불명 값을 쓰면 언제 알았는지 모르는 정보를 쓰는 것이다.
        sql += " AND as_of IS NOT NULL AND as_of <= ?"
        params.append(rules.as_ymd(as_of))
    rows = conn.execute(sql, params).fetchall()
    return [Attribute(complex_id, key, r["value_text"], r["value_num"], r["unit"],
                      r["as_of"], r["source_name"], int(r["source_tier"]),
                      r["confidence"], r["verification"]) for r in rows]


_CONFIDENCE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def best(conn: sqlite3.Connection, complex_id: int, key: str, *,
         as_of: str | None = None) -> Attribute | None:
    """이 값으로 쓸 것 하나. 없으면 None — 추정하지 않는다.

    우선순위: 출처 등급(공식) → 최신 → 신뢰도. 등급이 같은데 값이 다르면
    `conflicts()` 가 그걸 잡아낸다.
    """
    rows = _rows(conn, complex_id, key, as_of)
    if not rows:
        return None
    rows.sort(key=lambda a: (a.source_tier, _desc(a.as_of or ""),
                             _CONFIDENCE_ORDER.get(a.confidence, 9)))
    return rows[0]


def conflicts(conn: sqlite3.Connection, complex_id: int, key: str, *,
              as_of: str | None = None) -> list[tuple[Attribute, Attribute]]:
    """서로 다른 값을 말하는 출처 쌍. 덮어쓰지 않고 그대로 돌려준다(§3)."""
    rows = _rows(conn, complex_id, key, as_of)
    out = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a.value != b.value:
                out.append((a, b))
    return out


def record_conflicts(conn: sqlite3.Connection, complex_id: int, key: str, *,
                     as_of: str | None = None, field_label: str | None = None) -> int:
    """충돌을 source_conflict 에 남긴다. 공식 출처가 이기되, 진 값도 보존한다."""
    saved = 0
    for a, b in conflicts(conn, complex_id, key, as_of=as_of):
        winner = a if a.source_tier <= b.source_tier else b
        conn.execute(
            "INSERT INTO source_conflict (entity_type, entity_id, field_name, "
            " value_a, source_a, source_a_tier, value_b, source_b, source_b_tier, "
            " resolved_to, resolved_by, note) VALUES ('complex',?,?,?,?,?,?,?,?,?,?,?)",
            (complex_id, field_label or key, str(a.value), a.source_name,
             a.source_tier, str(b.value), b.source_name, b.source_tier,
             str(winner.value) if a.source_tier != b.source_tier else None,
             "tier" if a.source_tier != b.source_tier else None,
             None if a.source_tier != b.source_tier
             else "출처 등급이 같아 자동으로 정하지 않았습니다 — 사람이 확인하세요"))
        saved += 1
    return saved


def _desc(text: str):
    return tuple(-ord(c) for c in text)
