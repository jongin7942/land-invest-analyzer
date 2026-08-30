"""용적률 기준 — 네 가지를 절대 섞지 않는다 (요구사항 14·62-7).

    법정상한     국토계획법 시행령의 용도지역별 최대치. 실제로는 거의 못 받는다
    조례         지자체 도시계획조례 상한. 실무 출발점
    정비계획     그 구역에 고시된 정비계획 용적률. 사실에 가장 가깝다
    역세권특례   특례로 상향 가능한 한도. **조건부**이고 공공기여가 따라온다

"제3종일반주거지역이니 300% 받는다"는 계산은 하지 않는다. 300% 는 법정상한이고,
조례는 그보다 낮으며, 실제 사업은 정비계획 용적률로 한다. 그래서 조회 결과에는
언제나 kind 가 붙어 다니고, 화면·리포트에서 kind 를 떼고 숫자만 쓰지 못하게
`FarBasis.label` 이 항상 종류를 함께 말한다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from apt_engine import rules
from apt_engine.trace import Calc, Evidence

# 사업에 실제로 적용될 확률이 높은 순. 정비계획이 있으면 그게 답이다.
KINDS = ("정비계획", "역세권특례", "조례", "법정상한")
KIND_ORDER = {k: i for i, k in enumerate(KINDS)}

# 종류별 데이터 등급. 법정상한·조례는 "받을 수 있다"가 아니라 "그 이상은 안 된다"이다.
KIND_GRADE = {
    "정비계획": "CONFIRMED",
    "역세권특례": "SCENARIO",
    "조례": "ESTIMATED",
    "법정상한": "SCENARIO",
}

KIND_CAVEAT = {
    "정비계획": "고시된 정비계획 용적률",
    "역세권특례": "특례 적용 시 한도 — 조건 충족과 공공기여가 전제다. 확정이 아니다",
    "조례": "조례 상한 — 실제 정비계획은 보통 이보다 낮다",
    "법정상한": "법정 최대치 — 이 값을 받는 사업은 드물다. 상한이지 예상치가 아니다",
}


@dataclass(frozen=True)
class FarBasis:
    """용적률 하나. 숫자와 종류가 절대 분리되지 않는다."""
    far: float
    kind: str
    zoning: str
    scope: str                       # '전국' / 시도 / 시군구
    public_contribution_rate: float | None
    verified: bool
    source_name: str | None
    source_url: str | None
    note: str | None = None

    @property
    def grade(self) -> str:
        return KIND_GRADE.get(self.kind, "SCENARIO")

    @property
    def label(self) -> str:
        return f"{self.far:g}% ({self.kind})"

    @property
    def caveat(self) -> str:
        return KIND_CAVEAT.get(self.kind, "")

    @property
    def evidence(self) -> Evidence:
        return Evidence(
            source=self.source_name or f"{self.kind} 용적률 (수기 입력)",
            url=self.source_url,
            note=self.caveat,
        )


def available(conn: sqlite3.Connection, *, zoning: str, as_of: str | date,
              lawd_cd: str | None = None, sido: str | None = None,
              allow_unverified: bool = False) -> list[FarBasis]:
    """그 용도지역에서 확인된 용적률 기준 전부. 적용 확률이 높은 순으로 돌려준다."""
    day = rules.as_ymd(as_of)
    rows = conn.execute(
        f"SELECT * FROM far_standard WHERE zoning = ? AND {rules.effective_clause()}",
        (zoning, day, day)).fetchall()

    out: list[FarBasis] = []
    for r in rows:
        # 지역 범위가 맞지 않는 행은 버린다. NULL 은 상위 범위(전국/시도 전체)다.
        if r["lawd_cd"] and r["lawd_cd"] != lawd_cd:
            continue
        if r["sido"] and sido and r["sido"] != sido:
            continue
        verified = bool(r["last_verified"])
        if not verified and not allow_unverified:
            continue
        scope = r["lawd_cd"] or r["sido"] or "전국"
        out.append(FarBasis(
            far=float(r["max_far"]), kind=r["kind"], zoning=zoning, scope=scope,
            public_contribution_rate=r["public_contribution_rate"],
            verified=verified, source_name=r["source_name"], source_url=r["source_url"],
            note=r["note"]))

    # 같은 종류가 여러 개면 더 좁은 범위(시군구 > 시도 > 전국)를 먼저.
    out.sort(key=lambda b: (KIND_ORDER.get(b.kind, 99), b.scope == "전국"))
    return out


def planned(conn: sqlite3.Connection, complex_id: int) -> FarBasis | None:
    """정비계획 고시로 확정된 용적률. 없으면 None — 조례로 대신하지 않는다."""
    row = conn.execute(
        "SELECT p.planned_far, p.source_name, p.source_url, p.last_verified, "
        "       p.stage, c.zoning "
        "  FROM redevelopment_project p JOIN complex c ON c.id = p.complex_id "
        " WHERE p.complex_id = ? AND p.planned_far IS NOT NULL",
        (complex_id,)).fetchone()
    if not row:
        return None
    return FarBasis(
        far=float(row["planned_far"]), kind="정비계획",
        zoning=row["zoning"] or "확인 불가", scope="해당 구역",
        public_contribution_rate=None, verified=bool(row["last_verified"]),
        source_name=row["source_name"], source_url=row["source_url"],
        note=f"{row['stage']} 단계 고시 기준")


def resolve(conn: sqlite3.Connection, complex_id: int, *, as_of: str | date,
            prefer: str | None = None,
            allow_unverified: bool = False) -> tuple[FarBasis | None, str]:
    """이 단지의 사업 용적률로 쓸 값 하나. (기준, 설명).

    정비계획이 있으면 그것을 쓴다. 없으면 조례를 쓰되, 결과에 '조례 상한이라
    실제는 더 낮을 수 있다'는 단서가 붙는다. **법정상한은 자동 선택하지 않는다.**
    """
    got = planned(conn, complex_id)
    if got is not None:
        return got, "고시된 정비계획 용적률"

    row = conn.execute(
        "SELECT c.zoning, c.lawd_cd FROM complex c WHERE c.id = ?",
        (complex_id,)).fetchone()
    if not row or not row["zoning"]:
        return None, "용도지역 미입력 — 용적률 기준을 정할 수 없습니다"

    from apt_engine import regions
    sido = regions.sido_of(row["lawd_cd"])
    options = available(conn, zoning=row["zoning"], as_of=as_of,
                        lawd_cd=row["lawd_cd"], sido=sido,
                        allow_unverified=allow_unverified)
    if not options:
        return None, (f"'{row['zoning']}' 용적률 기준 미입력 — 확인 불가. "
                      f"`redev template far` 로 양식을 받아 넣으세요")

    if prefer:
        for b in options:
            if b.kind == prefer:
                return b, f"요청한 기준({prefer})"

    for kind in ("정비계획", "조례"):
        for b in options:
            if b.kind == kind:
                return b, KIND_CAVEAT[kind]

    # 남은 건 법정상한/역세권특례뿐이다. 자동으로 고르지 않고 호출부에 알린다.
    kinds = ", ".join(sorted({b.kind for b in options}))
    return None, (f"조례·정비계획 용적률이 없습니다(있는 것: {kinds}). "
                  f"법정상한을 사업 용적률로 자동 사용하지 않습니다 — "
                  f"쓰려면 --far-kind 로 명시하세요")


def to_calc(basis: FarBasis) -> Calc:
    return Calc(
        value=basis.far, unit="%",
        formula=f"{basis.kind} 용적률",
        inputs={"용도지역": basis.zoning, "적용범위": basis.scope},
        intermediates={"종류": basis.kind, "단서": basis.caveat,
                       "공공기여율": (f"{basis.public_contribution_rate:.1%}"
                                 if basis.public_contribution_rate is not None
                                 else "확인 불가")},
        evidence=(basis.evidence,),
        grade=basis.grade,
    )
