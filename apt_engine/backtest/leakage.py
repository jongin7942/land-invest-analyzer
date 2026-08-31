"""누출 감사 (§18·§55·§69).

> LOOK-AHEAD LEAKAGE 가 발견되면 해당 backtest 는 무효 처리한다. (§55)

무효 처리를 하려면 먼저 **발견** 해야 한다. 여기에 세 겹을 둔다.

    1) 구조   컷오프 guard 가 조회 시점에 거부한다 (blind/cutoff.py)
    2) 정적   Feature·스코어링 코드가 정답지를 언급하는지 소스에서 검사
    3) 경험적 미래를 실제로 지운 DB 로 같은 결정을 다시 내려 결과를 비교

셋 중 3)이 가장 강하다. 1)은 SQL 문자열을 보는 것이라 파이썬 쪽에서 날짜를
비교하는 우회를 못 잡고, 2)는 이름을 바꾸면 피해간다. 3)은 **결과가 달라지면
어딘가에서 미래를 봤다** 는 사실 그 자체라서 우회할 방법이 없다.

    원본 DB      로 2020-01-01 결정 → 지문 A
    미래 삭제 DB 로 2020-01-01 결정 → 지문 B
    A ≠ B  →  누출

이 검사는 실제로 잡는지 확인해야 의미가 있으므로, 테스트에서 **일부러 누출을
심고** 이 함수가 잡아내는지 본다(tests/test_backtest.py).
"""
from __future__ import annotations

import ast
import pathlib
import sqlite3
from dataclasses import dataclass, field

from apt_engine.blind import cutoff as cutoff_mod

# 정적 검사 대상 — "그 시점에 서 있어야 하는" 코드
DECISION_PACKAGES = ("features", "scoring", "ranking", "blind")


@dataclass(frozen=True)
class Finding:
    kind: str                  # STATIC / EMPIRICAL / GUARD
    where: str
    detail: str

    @property
    def label(self) -> str:
        return f"[{self.kind}] {self.where} — {self.detail}"


@dataclass
class Audit:
    findings: list[Finding] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def summary(self) -> str:
        if not self.checked:
            return "누출 검사를 하지 않았습니다 — 이 백테스트는 유효하다고 말할 수 없습니다"
        if self.clean:
            return f"누출 없음 ({' · '.join(self.checked)})"
        return (f"누출 {len(self.findings)}건 — 이 백테스트는 무효입니다\n  "
                + "\n  ".join(f.label for f in self.findings))


# ── 2) 정적 검사 ─────────────────────────────────────────────────────

def scan_sources(root: pathlib.Path | None = None) -> list[Finding]:
    """결정 경로 코드가 정답지를 건드리는지 소스에서 본다.

    잡는 것: 문자열 안의 정답지 테이블 이름, `backtest.outcome` import.
    못 잡는 것: 테이블 이름을 조립해서 만드는 경우. 그래서 이게 전부가 아니다.
    """
    base = root or pathlib.Path(__file__).resolve().parent.parent
    out: list[Finding] = []
    for package in DECISION_PACKAGES:
        folder = base / package
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            rel = f"{package}/{path.name}"
            if path.name == "cutoff.py":
                continue               # 차단 목록을 정의한 파일이라 이름이 나온다
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for table in cutoff_mod.ANSWER_KEY_TABLES:
                        if table in node.value:
                            out.append(Finding(
                                "STATIC", f"{rel}:{node.lineno}",
                                f"정답지 '{table}' 을(를) 문자열로 참조합니다"))
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = ([a.name for a in node.names]
                             + ([node.module] if isinstance(node, ast.ImportFrom)
                                and node.module else []))
                    if any(n and "backtest" in n for n in names):
                        out.append(Finding(
                            "STATIC", f"{rel}:{node.lineno}",
                            "결정 경로가 backtest 패키지를 import 합니다 — "
                            "채점 코드는 결정 코드에서 보이면 안 됩니다"))
    return out


# ── 3) 경험적 검사 ───────────────────────────────────────────────────

def truncate_future(src: sqlite3.Connection, as_of: cutoff_mod.AsOf
                    ) -> sqlite3.Connection:
    """미래를 실제로 지운 사본을 메모리에 만든다.

    시점 컬럼이 있는 모든 테이블에서 컷오프 이후 행을 **삭제** 한다.
    NULL 인 행도 지운다 — "언제 알았는지 모르는 값" 은 과거 모델이 알 수 있었다고
    말할 근거가 없다.

    정답지 테이블도 통째로 비운다. 결정 코드가 그걸 읽고 있었다면 여기서
    결과가 달라져 잡힌다.
    """
    dst = sqlite3.connect(":memory:")
    dst.row_factory = sqlite3.Row
    src.backup(dst)
    dst.execute("PRAGMA foreign_keys=OFF")

    existing = {r[0] for r in dst.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    for table in cutoff_mod.ANSWER_KEY_TABLES & existing:
        dst.execute(f"DELETE FROM {table}")

    for table, columns in cutoff_mod.DATED_TABLES.items():
        if table not in existing:
            continue
        for column in columns:
            cutoff_value = _cutoff_value(column, as_of)
            dst.execute(
                f"DELETE FROM {table} "
                f" WHERE {column} IS NULL OR {column} > ?", (cutoff_value,))
    dst.commit()
    return dst


def _cutoff_value(column: str, as_of: cutoff_mod.AsOf) -> str:
    if column.endswith("_ymd"):
        return as_of.ymd
    if column.endswith("_ym"):
        return as_of.ym
    return as_of.day


def compare_decisions(full: sqlite3.Connection, as_of: cutoff_mod.AsOf,
                      decide) -> list[Finding]:
    """같은 결정을 원본과 '미래 삭제본' 에서 각각 내려 비교한다.

    `decide(conn) -> 비교 가능한 값` — 보통 (순위, complex_id, 점수) 목록.
    점수는 부동소수라 미세한 차이가 날 수 있어 소수점 6자리에서 자른다.
    """
    truncated = truncate_future(full, as_of)
    try:
        a = _fingerprint(decide(full))
        b = _fingerprint(decide(truncated))
    finally:
        truncated.close()

    if a == b:
        return []

    only_a = [x for x in a if x not in set(b)]
    only_b = [x for x in b if x not in set(a)]
    detail = (f"원본과 미래삭제본의 결정이 다릅니다 "
              f"(원본에만 {len(only_a)}개, 삭제본에만 {len(only_b)}개). "
              f"어딘가에서 {as_of.day} 이후 데이터를 읽고 있습니다")
    if only_a[:1]:
        detail += f" 예: {only_a[0]}"
    return [Finding("EMPIRICAL", f"as_of={as_of.day}", detail)]


def _fingerprint(rows) -> list[tuple]:
    out = []
    for row in rows:
        out.append(tuple(round(v, 6) if isinstance(v, float) else v
                         for v in row))
    return out


def audit(conn: sqlite3.Connection, *, as_of: cutoff_mod.AsOf | None = None,
          decide=None, root: pathlib.Path | None = None) -> Audit:
    """세 겹 전부. `decide` 를 주지 않으면 경험적 검사는 건너뛰고 그 사실을 남긴다."""
    result = Audit()

    result.findings.extend(scan_sources(root))
    result.checked.append("정적 검사")

    if decide is not None and as_of is not None:
        result.findings.extend(compare_decisions(conn, as_of, decide))
        result.checked.append("미래삭제 비교")

    return result
