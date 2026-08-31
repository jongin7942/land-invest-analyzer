"""PropertyResolver — "이 이름이 가리키는 단지가 무엇인가" (지시서 §2).

같은 단지가 여러 이름으로 불리고, 다른 단지가 같은 이름을 쓴다.

    이름 변경   분양명 → 준공 후 단지명 → 브랜드 리뉴얼명
    동명 중복   '주공1단지' 는 전국에 수십 개, 한 시군구에 여럿일 수도 있다
    표기 흔들림 '래미안 강남포레스트' / '래미안강남포레스트' / 'RAEMIAN…'

이 모듈이 지키는 두 가지:

1. **애매하면 붙이지 않는다.** 후보가 둘 이상이면 `AMBIGUOUS` 를 돌려주고,
   호출부가 사람에게 묻거나 그 건을 버린다. 아무거나 골라 붙이면 그 뒤의 모든
   가격·수익률이 다른 단지 것이 된다.

2. **시점을 본다.** 2019년 실거래의 '△△△' 가 지금의 '○○○' 이라면, 그 별칭이
   **언제부터 유효했는지**(`valid_from`)를 보고 판단한다. 지금 이름으로 과거를
   조회하면 백테스트가 조용히 틀린다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import Enum

from apt_engine.collectors.matcher import normalize


class Resolution(str, Enum):
    EXACT = "EXACT"            # 단지 하나로 확정
    ALIAS = "ALIAS"            # 별칭 표에서 확정
    AMBIGUOUS = "AMBIGUOUS"    # 후보가 여럿 — 붙이지 않는다
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class Candidate:
    complex_id: int
    name: str
    lawd_cd: str
    emd_name: str | None
    approval_year: int | None
    apt_households: int | None
    via: str                   # 'name' / 'alias'


@dataclass(frozen=True)
class Resolved:
    resolution: Resolution
    complex_id: int | None
    candidates: list[Candidate] = field(default_factory=list)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.resolution in (Resolution.EXACT, Resolution.ALIAS)

    @property
    def label(self) -> str:
        if self.ok:
            return f"{self.resolution.value} → #{self.complex_id}  ({self.reason})"
        if self.resolution is Resolution.AMBIGUOUS:
            return (f"AMBIGUOUS — 후보 {len(self.candidates)}개. {self.reason}")
        return f"NOT_FOUND — {self.reason}"


def resolve(conn: sqlite3.Connection, name: str, *, lawd_cd: str | None = None,
            emd_name: str | None = None, approval_year: int | None = None,
            as_of: str | None = None, year_tolerance: int = 2) -> Resolved:
    """이름 → 단지. 확정하지 못하면 확정하지 않는다.

    as_of 를 주면 그 시점에 유효했던 별칭만 본다. 백테스트에서 필수다.
    """
    norm = normalize(name)
    if not norm:
        return Resolved(Resolution.NOT_FOUND, None, [], "이름이 비어 있습니다")

    found: dict[int, Candidate] = {}

    # ① 현재 단지명
    sql = ("SELECT id, name, lawd_cd, emd_name, approval_year, apt_households "
           "  FROM complex WHERE name_norm = ?")
    params: list = [norm]
    if lawd_cd:
        sql += " AND lawd_cd = ?"
        params.append(lawd_cd)
    for r in conn.execute(sql, params):
        found[int(r["id"])] = Candidate(int(r["id"]), r["name"], r["lawd_cd"],
                                        r["emd_name"], r["approval_year"],
                                        r["apt_households"], "name")

    # ② 별칭 — 그 시점에 유효했던 것만
    alias_sql = (
        "SELECT c.id, c.name, c.lawd_cd, c.emd_name, c.approval_year, "
        "       c.apt_households, a.kind "
        "  FROM complex_alias a JOIN complex c ON c.id = a.complex_id "
        " WHERE a.alias_norm = ?")
    alias_params: list = [norm]
    if lawd_cd:
        alias_sql += " AND c.lawd_cd = ?"
        alias_params.append(lawd_cd)
    if as_of:
        alias_sql += (" AND (a.valid_from IS NULL OR a.valid_from <= ?)"
                      " AND (a.valid_to IS NULL OR a.valid_to >= ?)")
        alias_params += [as_of, as_of]
    for r in conn.execute(alias_sql, alias_params):
        cid = int(r["id"])
        if cid not in found:
            found[cid] = Candidate(cid, r["name"], r["lawd_cd"], r["emd_name"],
                                   r["approval_year"], r["apt_households"],
                                   f"alias/{r['kind']}")

    candidates = list(found.values())
    if not candidates:
        return Resolved(Resolution.NOT_FOUND, None, [],
                        f"'{name}' (정규화 '{norm}') 로 찾은 단지가 없습니다")

    # ③ 보조키로 좁힌다. 좁히는 것만 하고, 없는 정보로 우열을 매기지 않는다.
    narrowed = candidates
    if emd_name:
        by_emd = [c for c in narrowed if c.emd_name == emd_name]
        if by_emd:
            narrowed = by_emd
    if approval_year and len(narrowed) > 1:
        by_year = [c for c in narrowed
                   if c.approval_year is not None
                   and abs(c.approval_year - approval_year) <= year_tolerance]
        if by_year:
            narrowed = by_year

    # ④ 대표 행으로 접기 — 중복 등록된 같은 실체는 하나로 본다
    canonical = _fold_canonical(conn, narrowed)

    if len(canonical) == 1:
        one = canonical[0]
        kind = Resolution.ALIAS if one.via.startswith("alias") else Resolution.EXACT
        bits = [f"경로 {one.via}"]
        if emd_name:
            bits.append(f"법정동 {emd_name}")
        if approval_year:
            bits.append(f"준공 {approval_year}±{year_tolerance}")
        return Resolved(kind, one.complex_id, canonical, " · ".join(bits))

    hint = "lawd_cd·법정동·준공연도를 함께 주면 좁혀집니다"
    return Resolved(Resolution.AMBIGUOUS, None, canonical,
                    f"'{name}' 후보가 {len(canonical)}개입니다. {hint}")


def _fold_canonical(conn: sqlite3.Connection,
                    candidates: list[Candidate]) -> list[Candidate]:
    """중복 등록 행을 대표 행으로 접는다.

    후보가 하나여도 접는다 — 병합된 행 하나만 걸리는 경우가 흔하고, 그때 접지
    않으면 대표가 아닌 id 를 돌려주게 된다. 그 id 로 조회한 가격·거래는
    반쪽짜리다.
    """
    out: dict[int, Candidate] = {}
    for c in candidates:
        row = conn.execute("SELECT canonical_id FROM complex WHERE id = ?",
                           (c.complex_id,)).fetchone()
        target = int(row["canonical_id"]) if row and row["canonical_id"] else c.complex_id
        if target != c.complex_id:
            # 대표 행의 정보로 갈아끼운다. 이름·법정동은 대표 행 것이 맞다.
            head = conn.execute(
                "SELECT id, name, lawd_cd, emd_name, approval_year, apt_households "
                "  FROM complex WHERE id = ?", (target,)).fetchone()
            if head is not None:
                c = Candidate(target, head["name"], head["lawd_cd"], head["emd_name"],
                              head["approval_year"], head["apt_households"], c.via)
        out.setdefault(target, c)
    return list(out.values())


# ── 별칭 등록 ─────────────────────────────────────────────────────────

class AliasError(ValueError):
    pass


def add_alias(conn: sqlite3.Connection, complex_id: int, alias: str, *, kind: str,
              reason: str, created_by: str, valid_from: str | None = None,
              valid_to: str | None = None) -> int:
    """별칭 등록. 근거와 등록자가 없으면 저장되지 않는다(스키마가 막는다).

    이미 다른 단지가 그 이름을 쓰고 있으면 경고 대신 **거부**한다 —
    같은 이름이 두 단지를 가리키면 resolve 가 영원히 AMBIGUOUS 가 되고,
    그건 등록 시점에 사람이 판단해야 할 문제다.
    """
    norm = normalize(alias)
    if not norm:
        raise AliasError("별칭이 비어 있습니다")

    row = conn.execute("SELECT lawd_cd FROM complex WHERE id = ?",
                       (complex_id,)).fetchone()
    if row is None:
        raise AliasError(f"단지 #{complex_id} 가 없습니다")

    # 이미 이 단지로 병합된 행은 충돌이 아니다 — 같은 실체이므로 이름이 같은 게 정상이다.
    clash = conn.execute(
        "SELECT id, name FROM complex "
        " WHERE name_norm = ? AND lawd_cd = ? AND id != ? "
        "   AND (canonical_id IS NULL OR canonical_id != ?)",
        (norm, row["lawd_cd"], complex_id, complex_id)).fetchone()
    if clash:
        raise AliasError(
            f"같은 시군구에 '{clash['name']}'(#{clash['id']}) 가 이미 그 이름을 씁니다. "
            f"별칭으로 등록하면 두 단지가 같은 이름이 돼 매칭이 영구히 애매해집니다")

    conn.execute(
        "INSERT INTO complex_alias (complex_id, alias, alias_norm, kind, valid_from, "
        " valid_to, reason, created_by) VALUES (?,?,?,?,?,?,?,?)",
        (complex_id, alias, norm, kind, valid_from, valid_to, reason, created_by))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def merge(conn: sqlite3.Connection, *, keep: int, drop: int, reason: str,
          created_by: str) -> None:
    """중복 등록된 두 행을 하나로 본다. **행을 지우지 않는다** — 이력이 남아야 한다."""
    if keep == drop:
        raise AliasError("같은 단지를 병합할 수 없습니다")
    for cid in (keep, drop):
        if conn.execute("SELECT 1 FROM complex WHERE id = ?", (cid,)).fetchone() is None:
            raise AliasError(f"단지 #{cid} 가 없습니다")
    conn.execute("UPDATE complex SET canonical_id = ? WHERE id = ?", (keep, drop))
    row = conn.execute("SELECT name FROM complex WHERE id = ?", (drop,)).fetchone()
    add_alias(conn, keep, row["name"], kind="별칭", reason=reason,
              created_by=created_by)


def canonical_id(conn: sqlite3.Connection, complex_id: int) -> int:
    """대표 행 id. 병합되지 않았으면 자기 자신."""
    row = conn.execute("SELECT canonical_id FROM complex WHERE id = ?",
                       (complex_id,)).fetchone()
    if row is None:
        raise AliasError(f"단지 #{complex_id} 가 없습니다")
    return int(row["canonical_id"]) if row["canonical_id"] else complex_id
