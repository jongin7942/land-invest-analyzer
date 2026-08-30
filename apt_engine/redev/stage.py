"""정비사업 단계 · 남은 사업기간 · 지연위험 (요구사항 17·19·20).

단계는 **사실**이고 기간은 **추정**이다. 둘을 한 문장에 섞지 않는다.

남은 기간은 `stage_duration_ref`(단계별 소요기간 통계, 수기 입력)에서만 온다.
그 표가 비어 있으면 "평균 10년" 같은 그럴듯한 숫자를 만들지 않고 `확인 불가` 를
돌려준다. 재건축에서 기간을 틀리면 그 위의 IRR·기회비용이 전부 틀리기 때문에,
근거 없는 기간 추정이 근거 없는 가격 추정보다 오히려 더 위험하다.

지연위험은 점수를 지어내지 않고, **관측 가능한 사실 세 가지**로만 판정한다.
    1. 지금 단계가 어디인가 (착공 전은 무산될 수 있다)
    2. 스스로 적어둔 예정일이 이미 지났는가 (지났으면 이미 지연 중이다)
    3. 마지막 단계 변경이 얼마나 오래 전인가 (오래 멈춰 있으면 정체다)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from apt_engine import rules
from apt_engine.trace import Calc, Evidence

# 뒤로 갈수록 확실하다. 인덱스가 곧 진행도다.
STAGES = ("미지정", "예비안전진단", "정밀안전진단", "정비구역지정", "추진위원회",
          "조합설립", "사업시행인가", "관리처분인가", "이주철거", "착공", "준공")
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}

# 관리처분인가부터는 사업이 사실상 되돌아가지 않는다. 그 전은 무산 사례가 흔하다.
IRREVERSIBLE_FROM = STAGE_ORDER["관리처분인가"]

# 이 단계 이후로 오래 멈춰 있으면 정체로 본다(개월).
STALL_MONTHS = 36


@dataclass(frozen=True)
class Project:
    complex_id: int
    project_type: str
    name: str | None
    stage: str
    stage_date: str | None
    safety_grade: str | None
    expected_approval_ym: str | None
    expected_move_ym: str | None
    expected_done_ym: str | None
    planned_far: float | None
    planned_units: int | None
    rental_ratio: float | None
    public_contribution_rate: float | None
    member_count: int | None
    prior_asset_total: int | None
    verified: bool
    source_name: str | None
    source_url: str | None
    data_grade: str

    @property
    def order(self) -> int:
        return STAGE_ORDER.get(self.stage, 0)

    @property
    def started(self) -> bool:
        """조합설립 이후 — 사업이 실체를 가진 상태."""
        return self.order >= STAGE_ORDER["조합설립"]

    @property
    def irreversible(self) -> bool:
        return self.order >= IRREVERSIBLE_FROM

    @property
    def evidence(self) -> Evidence:
        return Evidence(
            source=self.source_name or "정비사업 단계 (수기 입력)",
            url=self.source_url,
            effective_date=self.stage_date,
            note=f"{self.project_type} · {self.stage}"
                 + ("" if self.verified else " · 미검증"),
        )


def load(conn: sqlite3.Connection, complex_id: int,
         project_type: str | None = None) -> Project | None:
    sql = "SELECT * FROM redevelopment_project WHERE complex_id = ?"
    params: list = [complex_id]
    if project_type:
        sql += " AND project_type = ?"
        params.append(project_type)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return None
    # 여러 개면 가장 진행된 것.
    row = max(rows, key=lambda r: STAGE_ORDER.get(r["stage"], 0))
    return Project(
        complex_id=complex_id, project_type=row["project_type"], name=row["name"],
        stage=row["stage"], stage_date=row["stage_date"], safety_grade=row["safety_grade"],
        expected_approval_ym=row["expected_approval_ym"],
        expected_move_ym=row["expected_move_ym"], expected_done_ym=row["expected_done_ym"],
        planned_far=row["planned_far"], planned_units=row["planned_units"],
        rental_ratio=row["rental_ratio"],
        public_contribution_rate=row["public_contribution_rate"],
        member_count=row["member_count"], prior_asset_total=row["prior_asset_total"],
        verified=bool(row["last_verified"]), source_name=row["source_name"],
        source_url=row["source_url"], data_grade=row["data_grade"])


def _months_between(from_ymd: str, to_ymd: str) -> int:
    fy, fm = int(from_ymd[:4]), int(from_ymd[5:7])
    ty, tm = int(to_ymd[:4]), int(to_ymd[5:7])
    return (ty - fy) * 12 + (tm - fm)


@dataclass(frozen=True)
class Duration:
    months: int | None                # None = 확인 불가
    low_months: int | None
    high_months: int | None
    covered: list[str]                # 기간을 찾은 구간
    missing: list[str]                # 참고치가 없어 못 더한 구간

    @property
    def complete(self) -> bool:
        return self.months is not None and not self.missing

    @property
    def label(self) -> str:
        if self.months is None:
            return "확인 불가 — 단계별 소요기간 참고치 미입력"
        head = f"약 {self.months / 12:.1f}년"
        if self.low_months is not None and self.high_months is not None:
            head += f" ({self.low_months / 12:.1f}~{self.high_months / 12:.1f}년)"
        if self.missing:
            head += f" 이상 — {len(self.missing)}개 구간 참고치 없음"
        return head


def remaining(conn: sqlite3.Connection, project: Project, *,
              target: str = "준공", region: str | None = None,
              allow_unverified: bool = False) -> Duration:
    """현재 단계에서 target 단계까지 남은 기간. 참고치가 없는 구간은 더하지 않는다."""
    start, end = project.order, STAGE_ORDER.get(target, STAGE_ORDER["준공"])
    if start >= end:
        return Duration(0, 0, 0, [], [])

    total = low = high = 0
    covered: list[str] = []
    missing: list[str] = []
    have_band = True

    for i in range(start, end):
        a, b = STAGES[i], STAGES[i + 1]
        rows = conn.execute(
            "SELECT * FROM stage_duration_ref "
            " WHERE project_type = ? AND from_stage = ? AND to_stage = ?",
            (project.project_type, a, b)).fetchall()
        rows = [r for r in rows if r["region"] is None or r["region"] == region]
        rows = [r for r in rows if r["last_verified"] or allow_unverified]
        if not rows:
            missing.append(f"{a}→{b}")
            continue
        # 지역이 적힌 참고치를 먼저.
        row = sorted(rows, key=lambda r: r["region"] is None)[0]
        total += int(row["median_months"])
        if row["p25_months"] is None or row["p75_months"] is None:
            have_band = False
        else:
            low += int(row["p25_months"])
            high += int(row["p75_months"])
        covered.append(f"{a}→{b} {row['median_months']}개월")

    if not covered:
        return Duration(None, None, None, [], missing)
    return Duration(total, low if have_band else None, high if have_band else None,
                    covered, missing)


@dataclass(frozen=True)
class DelayRisk:
    level: str                       # 높음 / 보통 / 낮음 / 확인 불가
    reasons: list[str]

    @property
    def label(self) -> str:
        return self.level + (f" — {'; '.join(self.reasons)}" if self.reasons else "")


def delay_risk(project: Project, *, as_of: str | date) -> DelayRisk:
    """지연위험. 점수를 지어내지 않고 관측된 사실만 나열한다."""
    day = rules.as_ymd(as_of)
    reasons: list[str] = []
    score = 0

    if project.order <= STAGE_ORDER["정비구역지정"]:
        score += 2
        reasons.append(f"'{project.stage}' 단계 — 조합 설립 전이라 무산 사례가 흔하다")
    elif not project.irreversible:
        score += 1
        reasons.append(f"'{project.stage}' 단계 — 관리처분 전이라 분담금 확정 전이다")
    else:
        reasons.append(f"'{project.stage}' 단계 — 사업이 되돌아가기 어려운 구간이다")

    # 스스로 적어둔 예정일이 이미 지났는가. 이건 추정이 아니라 관측이다.
    now_ym = day[:4] + day[5:7]
    slipped = [(label, ym) for label, ym in
               (("사업시행인가", project.expected_approval_ym),
                ("이주", project.expected_move_ym),
                ("준공", project.expected_done_ym))
               if ym and ym < now_ym]
    if slipped:
        score += 2
        reasons.append("예정일 경과: " +
                       ", ".join(f"{l} {ym} 예정이었으나 아직 {project.stage}"
                                 for l, ym in slipped))

    if project.stage_date:
        try:
            stalled = _months_between(project.stage_date, day)
        except (ValueError, IndexError):
            stalled = None
        if stalled is not None and stalled >= STALL_MONTHS and not project.irreversible:
            score += 1
            reasons.append(f"{project.stage_date} 이후 {stalled // 12}년째 단계 변동 없음")
    else:
        reasons.append("단계 변경일 미입력 — 정체 여부 확인 불가")

    if not project.verified:
        reasons.append("단계 정보 미검증 — 최신 상태가 아닐 수 있다")

    level = "높음" if score >= 3 else "보통" if score >= 1 else "낮음"
    return DelayRisk(level, reasons)


def to_calc(conn: sqlite3.Connection, project: Project, *, as_of: str | date,
            region: str | None = None, allow_unverified: bool = False) -> Calc:
    dur = remaining(conn, project, region=region, allow_unverified=allow_unverified)
    risk = delay_risk(project, as_of=as_of)
    return Calc(
        value=dur.months, unit="개월",
        formula="현재 단계 → 준공까지 단계별 소요기간 중앙값 합",
        inputs={"사업유형": project.project_type, "현재 단계": project.stage,
                "단계 변경일": project.stage_date or "확인 불가", "기준일": rules.as_ymd(as_of)},
        intermediates={
            "확정된 사실": {
                "단계": project.stage, "단계 변경일": project.stage_date or "확인 불가",
                "안전진단 등급": project.safety_grade or "확인 불가",
                "정비계획 용적률": (f"{project.planned_far:g}%" if project.planned_far
                              else "미고시"),
            },
            "추정": {
                "남은 기간": dur.label,
                "구간별": dur.covered or "참고치 없음",
                "참고치 없는 구간": dur.missing or "없음",
            },
            "지연위험": risk.label,
        },
        evidence=(project.evidence,),
        # 값 자체가 추정이다. 못 구했으면 값이 None 일 뿐 등급은 그대로다.
        grade="ESTIMATED",
    )
