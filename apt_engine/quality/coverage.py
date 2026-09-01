"""수집 완성도 검사 — 지역 × 월 × 거래유형 Coverage.

**이 모듈이 존재하는 이유 하나**

    "수집 프로세스가 종료됐다" 와 "240개월 데이터가 완전하게 확보됐다" 는
    같은 말이 아니다.

수집은 일일 한도 소진·API 오류·중단으로 언제든 도중에 끝난다. 그런데
끝난 뒤 화면에는 "수집 완료" 라고 뜨고, 기존 `report gaps` 는 **DB 에
들어온 달들** 만 보고 공백을 찾는다. 120개월만 받고 멈추면 그 120개월
안에는 공백이 없으므로 "공백 없음" 이라고 답한다. 없는 8년을 없다고
말하지 못하는 것이다.

그래서 여기서는 **받아야 할 격자를 먼저 만들고** 그것과 대조한다.

    필요 격자 = { (거래유형, 시군구코드, 거래월)
                  | 거래월 ∈ 최근 240개월,
                    시군구코드 ∈ 그 달에 유효했던 코드,
                    거래유형 ∈ {매매, 전월세} }

격자의 한 칸이 채워졌다는 증거는 `collection_log` 의 OK/EMPTY 다.
**EMPTY 는 채워진 칸이다** — "받아봤더니 거래가 없었다" 는 사실이지
공백이 아니다. 반대로 로그가 아예 없는 칸(NEVER)은 "거래가 없었다" 가
아니라 **"물어보지 않았다"** 이고, 그 둘을 섞으면 안 받은 달이 조용히
'거래 없는 달' 로 둔갑한다.

── 완료 기준 ─────────────────────────────────────────────────────────

`audit(...).passed` 가 True 인 상태가 완료다. 수집 명령이 정상 종료한
것은 완료가 아니다.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date

from apt_engine import regions
from apt_engine.ingest import recent_yms

REQUIRED_MONTHS = 240

# 거래유형 — collection_log.source_key 와 짝이 맞아야 한다.
KINDS: dict[str, tuple[str, str, str]] = {
    # 이름: (source_key, 테이블, 날짜컬럼)
    "매매": ("molit_apt_trade", "trade", "deal_ymd"),
    "전월세": ("molit_apt_rent", "jeonse_contract", "contract_ymd"),
}

# 칸의 상태
FILLED = "OK"        # 받았고 거래가 있었다
EMPTY = "EMPTY"      # 받았고 거래가 없었다 — 이것도 채워진 칸이다
FAILED = "FAILED"    # 받으려다 실패했다
NEVER = "NEVER"      # **물어보지 않았다.** 거래가 없는 것과 다르다

DONE_STATUSES = (FILLED, EMPTY)

# 0건인 달을 의심할 기준.
#
# 옹진군은 한 달에 아파트 거래가 0건일 수 있다. 강남구는 그럴 수 없다.
# 그래서 "그 지역이 평소 몇 건이나 나오는가" 를 먼저 보고, 평소에 꾸준히
# 나오던 지역이 갑자기 0건이면 의심한다. 절대 건수로 자르면 시골 군을
# 전부 오탐으로 만들고, 아예 안 보면 수집 실패를 놓친다.
BUSY_MEDIAN = 5      # 월 중앙값이 이 이상이면 '거래가 꾸준한 지역'
SUSPICIOUS_RATIO = 0.0   # 그런 지역에서 0건이면 의심


@dataclass(frozen=True)
class Hole:
    """받지 못한 칸 하나."""
    kind: str
    lawd_cd: str
    ym: str
    status: str
    error: str | None = None

    @property
    def region(self) -> str:
        return regions.name_of(self.lawd_cd)


@dataclass(frozen=True)
class SuspiciousZero:
    """받긴 했는데 0건인 달 — 그 지역치고 이상한 경우만."""
    kind: str
    lawd_cd: str
    ym: str
    region_median: float

    @property
    def region(self) -> str:
        return regions.name_of(self.lawd_cd)


@dataclass
class KindCoverage:
    """거래유형 하나의 격자 채움 상태."""
    kind: str
    required: int = 0
    filled: int = 0
    empty: int = 0
    failed: list[Hole] = field(default_factory=list)
    never: list[Hole] = field(default_factory=list)
    months_required: tuple[str, ...] = ()
    months_missing: tuple[str, ...] = ()
    by_region_missing: dict[str, int] = field(default_factory=dict)

    @property
    def done(self) -> int:
        return self.filled + self.empty

    @property
    def rate(self) -> float | None:
        """채움률. 필요 격자가 0이면 비율이 없다 — 100% 가 아니다."""
        if not self.required:
            return None
        return self.done / self.required

    @property
    def missing(self) -> int:
        return self.required - self.done

    @property
    def passed(self) -> bool:
        return self.required > 0 and self.missing == 0


@dataclass
class CoverageReport:
    months: int
    sido: str | None
    kinds: dict[str, KindCoverage]
    suspicious_zeros: list[SuspiciousZero]
    lineage_issues: list[str]
    orphan_codes: dict[str, int]
    complexes_without_trade: int
    complexes_total: int

    @property
    def passed(self) -> bool:
        """**완료 기준.** 수집 명령의 정상 종료가 아니라 이것이다."""
        return (bool(self.kinds)
                and all(k.passed for k in self.kinds.values())
                and not self.lineage_issues
                and not self.orphan_codes)


# ── 격자 ──────────────────────────────────────────────────────────────

def required_grid(months: int = REQUIRED_MONTHS, sido: str | None = None,
                  *, end: date | None = None) -> dict[str, set[tuple[str, str]]]:
    """받아야 할 (시군구, 거래월) 목록 — 거래유형별로.

    그 달에 **유효했던** 코드를 쓴다(`regions.codes_for_ym`). 수집도 같은
    함수를 쓰므로 격자와 수집이 같은 기준으로 움직인다. 여기서 기준이
    갈라지면 영원히 안 채워지는 칸이 생긴다.
    """
    yms = recent_yms(months, end=end)
    cells = {(code, ym) for ym in yms for code in regions.codes_for_ym(ym, sido)}
    return {kind: set(cells) for kind in KINDS}


def _log_status(conn: sqlite3.Connection, source_key: str
                ) -> dict[tuple[str, str], tuple[str, str | None]]:
    """(시군구, 거래월) → (마지막 상태, 오류).

    같은 칸을 여러 번 받았으면 **마지막 시도**가 아니라 '한 번이라도
    끝났는가' 로 본다. 어제 OK 로 받은 달을 오늘 재시도하다 실패했다고
    데이터가 사라지지는 않기 때문이다.
    """
    out: dict[tuple[str, str], tuple[str, str | None]] = {}
    for r in conn.execute(
            "SELECT target, period, status, error FROM collection_log "
            "WHERE source_key = ? AND target IS NOT NULL AND period IS NOT NULL "
            "ORDER BY ran_at", (source_key,)):
        key = (r["target"], r["period"])
        prev = out.get(key)
        if prev and prev[0] in DONE_STATUSES:
            continue          # 이미 끝난 칸은 나중 실패로 되돌리지 않는다
        out[key] = (r["status"], r["error"])
    return out


# ── 1·2. 거래유형별 240개월 완전성 ────────────────────────────────────

def audit_kind(conn: sqlite3.Connection, kind: str, *,
               months: int = REQUIRED_MONTHS, sido: str | None = None,
               end: date | None = None) -> KindCoverage:
    source_key = KINDS[kind][0]
    need = required_grid(months, sido, end=end)[kind]
    seen = _log_status(conn, source_key)

    cov = KindCoverage(kind=kind, required=len(need))
    months_with_hole: set[str] = set()
    for code, ym in sorted(need):
        status, error = seen.get((code, ym), (NEVER, None))
        if status == FILLED:
            cov.filled += 1
        elif status == EMPTY:
            cov.empty += 1
        else:
            hole = Hole(kind=kind, lawd_cd=code, ym=ym, status=status, error=error)
            (cov.failed if status == FAILED else cov.never).append(hole)
            months_with_hole.add(ym)
            cov.by_region_missing[code] = cov.by_region_missing.get(code, 0) + 1

    cov.months_required = tuple(recent_yms(months, end=end))
    cov.months_missing = tuple(sorted(months_with_hole))
    return cov


# ── 4. 비정상 0건 구간 ────────────────────────────────────────────────

def suspicious_zeros(conn: sqlite3.Connection, *, sido: str | None = None
                     ) -> list[SuspiciousZero]:
    """받긴 했는데 0건인 달 중 **그 지역치고 이상한** 것.

    EMPTY 자체는 정상이다. 옹진군에 한 달 아파트 거래가 없는 것은 흔하다.
    이상한 것은 평소 꾸준히 거래가 나오던 지역이 갑자기 0건인 경우다 —
    그건 대개 수집이 아니라 그 달 응답이 비어서 생긴다.
    """
    out: list[SuspiciousZero] = []
    for kind, (source_key, table, ymd_col) in KINDS.items():
        counts: dict[str, list[int]] = {}
        for r in conn.execute(
                f"SELECT lawd_cd, substr({ymd_col},1,6) AS ym, COUNT(*) AS cnt "
                f"FROM {table} GROUP BY lawd_cd, ym"):
            counts.setdefault(r["lawd_cd"], []).append(r["cnt"])

        empties = conn.execute(
            "SELECT target, period FROM collection_log "
            "WHERE source_key = ? AND status = 'EMPTY' "
            "AND target IS NOT NULL AND period IS NOT NULL",
            (source_key,)).fetchall()
        for r in empties:
            code = r["target"]
            if sido and regions.sido_of(code) != sido:
                continue
            seen = counts.get(code)
            if not seen:
                continue    # 그 지역 거래가 아예 없다 — 0건 판단의 근거가 없다
            med = statistics.median(seen)
            if med >= BUSY_MEDIAN:
                out.append(SuspiciousZero(kind=kind, lawd_cd=code,
                                          ym=r["period"], region_median=med))
    return sorted(out, key=lambda s: (s.kind, s.lawd_cd, s.ym))


# ── 6. 행정구역 개편 전후 코드 연결 ───────────────────────────────────

def lineage_check(conn: sqlite3.Connection) -> tuple[list[str], dict[str, int]]:
    """개편 전후 코드가 끊기지 않았는지.

    두 가지를 본다.
      · 승계표가 비어 있지 않은가 — 비면 폐지 코드 데이터가 고아가 된다
      · 거래·단지가 region 에 없는 코드로 들어와 있지 않은가
    """
    issues: list[str] = []

    try:
        have = {(r["predecessor_lawd_cd"], r["successor_lawd_cd"]) for r in conn.execute(
            "SELECT predecessor_lawd_cd, successor_lawd_cd FROM region_lineage")}
    except sqlite3.OperationalError:
        # 020 이전 스키마. 없다고 조용히 통과시키면 폐지 코드 데이터가
        # 고아인 채로 "정상" 이라고 나온다.
        return (["region_lineage 표가 없습니다 — "
                 "`python -m apt_engine.cli init` 으로 마이그레이션을 먼저 돌리세요"],
                {})
    want = {(a, b) for a, b, _, _ in regions.LINEAGE}
    if missing := want - have:
        issues.append(f"승계 관계 누락: {sorted(missing)} — sync_regions 를 돌리세요")

    for code in regions.RETIRED:
        row = conn.execute("SELECT is_active FROM region WHERE lawd_cd = ?",
                           (code,)).fetchone()
        if row is None:
            issues.append(f"폐지 코드 {code}({regions.name_of(code)}) 가 region 에 없다 "
                          f"— 그 코드로 저장된 과거 데이터가 고아가 된다")
        elif row["is_active"]:
            issues.append(f"폐지 코드 {code} 가 아직 is_active=1 이다")

    # region 에 없는 코드로 들어온 데이터. 이건 조용히 계산에서 빠진다.
    orphans: dict[str, int] = {}
    for table in ("trade", "jeonse_contract", "complex"):
        for r in conn.execute(
                f"SELECT t.lawd_cd AS code, COUNT(*) AS n FROM {table} t "
                f"LEFT JOIN region g ON g.lawd_cd = t.lawd_cd "
                f"WHERE g.lawd_cd IS NULL GROUP BY t.lawd_cd"):
            orphans[r["code"]] = orphans.get(r["code"], 0) + r["n"]
    return issues, orphans


# ── 전체 ──────────────────────────────────────────────────────────────

def audit(conn: sqlite3.Connection, *, months: int = REQUIRED_MONTHS,
          sido: str | None = None, end: date | None = None) -> CoverageReport:
    kinds = {k: audit_kind(conn, k, months=months, sido=sido, end=end)
             for k in KINDS}
    issues, orphans = lineage_check(conn)

    total = conn.execute("SELECT COUNT(*) FROM complex").fetchone()[0]
    no_trade = conn.execute(
        "SELECT COUNT(*) FROM complex c WHERE NOT EXISTS "
        "(SELECT 1 FROM trade t WHERE t.complex_id = c.id)").fetchone()[0]

    return CoverageReport(
        months=months, sido=sido, kinds=kinds,
        suspicious_zeros=suspicious_zeros(conn, sido=sido),
        lineage_issues=issues, orphan_codes=orphans,
        complexes_without_trade=no_trade, complexes_total=total)


# ── 7. 리포트 ─────────────────────────────────────────────────────────

def _pct(x: float | None) -> str:
    return "확인 불가" if x is None else f"{x * 100:.2f}%"


def report(rep: CoverageReport, *, limit: int = 12) -> str:
    L: list[str] = []
    scope = rep.sido or "수도권 전체"
    L.append(f"■ 수집 완성도 — {scope} · 최근 {rep.months}개월")
    L.append("")
    L.append("  이 검사가 통과해야 '수집 완료' 입니다.")
    L.append("  수집 명령이 정상 종료한 것은 완료가 아닙니다.")
    L.append("")

    L.append(f"  {'거래유형':8s} {'필요':>8s} {'받음':>8s} {'거래없음':>8s} "
             f"{'미수집':>8s} {'실패':>6s} {'채움률':>9s}")
    for k, c in rep.kinds.items():
        L.append(f"  {k:8s} {c.required:>8,d} {c.filled:>8,d} {c.empty:>8,d} "
                 f"{len(c.never):>8,d} {len(c.failed):>6,d} {_pct(c.rate):>9s}")
    L.append("")
    L.append("  '거래없음(EMPTY)' 은 채워진 칸입니다 — 받아봤더니 없었다는 사실입니다.")
    L.append("  '미수집(NEVER)' 은 물어보지 않은 칸입니다. 둘을 섞으면 안 받은 달이")
    L.append("  조용히 '거래 없는 달' 로 둔갑합니다.")

    for k, c in rep.kinds.items():
        if c.passed:
            continue
        L.append("")
        L.append(f"── {k} 미수집 {c.missing:,}칸 ──")
        if c.months_missing:
            head = ", ".join(c.months_missing[:limit])
            more = f" … 총 {len(c.months_missing)}개월" if len(c.months_missing) > limit else ""
            L.append(f"  공백 있는 달: {head}{more}")
        worst = sorted(c.by_region_missing.items(), key=lambda kv: -kv[1])[:limit]
        for code, n in worst:
            L.append(f"    {regions.name_of(code):16s} {n:>4,d}개월")
        if c.failed:
            L.append(f"  수집 실패 {len(c.failed)}칸 (재시도 대상):")
            for h in c.failed[:5]:
                L.append(f"    {h.region:16s} {h.ym}  {(h.error or '')[:60]}")

    if rep.suspicious_zeros:
        L.append("")
        L.append(f"── 의심스러운 0건 구간 {len(rep.suspicious_zeros)}건 ──")
        L.append("  평소 거래가 꾸준한 지역인데 그 달만 0건입니다. 수집 문제일 수 있습니다.")
        for s in rep.suspicious_zeros[:limit]:
            L.append(f"    {s.kind:6s} {s.region:16s} {s.ym}  "
                     f"(평소 월 중앙값 {s.region_median:.0f}건)")
        if len(rep.suspicious_zeros) > limit:
            L.append(f"    … 외 {len(rep.suspicious_zeros) - limit}건")

    if rep.lineage_issues or rep.orphan_codes:
        L.append("")
        L.append("── 행정구역 개편 연결 ──")
        for m in rep.lineage_issues:
            L.append(f"    ✗ {m}")
        for code, n in sorted(rep.orphan_codes.items()):
            L.append(f"    ✗ region 에 없는 코드 {code} 로 들어온 데이터 {n:,}건")
    else:
        L.append("")
        L.append("── 행정구역 개편 연결: 정상 ──")

    if rep.complexes_total:
        share = rep.complexes_without_trade / rep.complexes_total
        L.append("")
        L.append(f"── 단지 결측 ── 거래가 한 건도 없는 단지 "
                 f"{rep.complexes_without_trade:,}/{rep.complexes_total:,} ({share*100:.1f}%)")
        L.append("  전부 문제는 아닙니다 — 거래가 드문 단지가 실제로 있습니다.")
        L.append("  다만 이 비율이 크면 매칭(match) 을 먼저 의심하세요.")

    L.append("")
    L.append("  판정: " + ("통과 — 투자점수 산출로 넘어가도 됩니다"
                          if rep.passed else
                          "미통과 — 아직 수집이 끝나지 않았습니다"))
    if not rep.passed:
        L.append("")
        L.append("  다음 명령:")
        L.append("    python -m apt_engine.cli collect trades --months 240")
        L.append("    python -m apt_engine.cli collect rents  --months 240")
        L.append("  (일일 한도로 끊기면 다시 돌리면 됩니다. 받은 달은 건너뜁니다)")
    return "\n".join(L)


__all__ = ["REQUIRED_MONTHS", "KINDS", "FILLED", "EMPTY", "FAILED", "NEVER",
           "Hole", "SuspiciousZero", "KindCoverage", "CoverageReport",
           "required_grid", "audit", "audit_kind", "suspicious_zeros",
           "lineage_check", "report"]
