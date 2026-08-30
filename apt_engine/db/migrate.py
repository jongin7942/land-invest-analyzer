"""버전 기반 마이그레이션 러너.

기존 `db/schema._migrate()` 는 `new_cols` 튜플에 컬럼을 손으로 추가하는 방식이라
테이블이 20개가 되면 관리가 안 되고, 컬럼 삭제·타입변경·인덱스 변경을 아예 못 한다.

여기서는 `migrations/NNN_이름.sql` 파일을 순서대로 적용하고 `PRAGMA user_version`
으로 어디까지 적용됐는지 기록한다.

규칙:
  * 파일명은 `001_meta.sql` 처럼 3자리 번호 + 소문자/숫자/밑줄.
  * 번호는 1부터 빠짐없이 이어져야 한다(빠지면 discover 가 거부).
  * **이미 적용된 마이그레이션 파일은 절대 수정하지 않는다.** 고칠 게 있으면 새 번호로.
  * .sql 안에 BEGIN/COMMIT 을 쓰지 않는다 — 러너가 감싼다.

각 마이그레이션은 하나의 트랜잭션 안에서 [스키마 변경 + _migration 로그 + user_version]
이 함께 적용된다. 중간에 실패하면 전부 롤백돼 버전이 올라가지 않는다.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import config
from apt_engine.db.connection import get_conn

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_NAME_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")

_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS _migration (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)
"""


class MigrationError(RuntimeError):
    pass


def discover(directory: Path | str | None = None) -> list[tuple[int, str, Path]]:
    """`(버전, 이름, 경로)` 목록을 버전 오름차순으로. 번호가 중복/누락이면 에러."""
    d = Path(directory or MIGRATIONS_DIR)
    if not d.is_dir():
        raise MigrationError(f"마이그레이션 폴더가 없습니다: {d}")

    found: dict[int, tuple[str, Path]] = {}
    for path in sorted(d.glob("*.sql")):
        m = _NAME_RE.match(path.name)
        if not m:
            raise MigrationError(
                f"마이그레이션 파일명 규칙 위반: {path.name} "
                f"(예: 001_meta.sql — 3자리 번호 + 소문자/숫자/밑줄)"
            )
        version, name = int(m.group(1)), m.group(2)
        if version in found:
            raise MigrationError(f"버전 {version:03d} 중복: {found[version][1].name}, {path.name}")
        found[version] = (name, path)

    expected = list(range(1, len(found) + 1))
    if sorted(found) != expected:
        missing = sorted(set(expected) - set(found))
        raise MigrationError(f"마이그레이션 번호가 이어지지 않습니다. 빠진 번호: {missing}")

    return [(v, found[v][0], found[v][1]) for v in sorted(found)]


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def pending(conn: sqlite3.Connection, directory: Path | str | None = None):
    """아직 적용되지 않은 마이그레이션 목록."""
    at = current_version(conn)
    return [m for m in discover(directory) if m[0] > at]


def _apply_one(conn: sqlite3.Connection, version: int, name: str, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    if re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\b", sql, re.IGNORECASE | re.MULTILINE):
        raise MigrationError(
            f"{path.name} 안에 트랜잭션 제어문(BEGIN/COMMIT/ROLLBACK)이 있습니다. "
            f"러너가 감싸므로 빼주세요."
        )
    # executescript 는 실행 전에 열린 트랜잭션을 커밋해버리므로, 트랜잭션 제어를
    # 스크립트 문자열 안에 넣어야 실제로 원자적으로 적용된다.
    script = (
        "BEGIN;\n"
        f"{sql}\n"
        f"INSERT INTO _migration (version, name) VALUES ({version}, '{name}');\n"
        f"PRAGMA user_version = {version};\n"
        "COMMIT;"
    )
    try:
        conn.executescript(script)
    except sqlite3.Error:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass  # 이미 롤백된 상태면 무시
        raise


def migrate(db_path: str | None = None, *, directory: Path | str | None = None,
            target: int | None = None) -> list[int]:
    """미적용 마이그레이션을 순서대로 적용하고, 적용한 버전 목록을 반환한다.

    이미 최신이면 빈 리스트. `target` 을 주면 그 버전까지만 적용한다.
    """
    applied: list[int] = []
    with get_conn(db_path) as conn:
        conn.execute(_LOG_TABLE)
        conn.commit()
        for version, name, path in pending(conn, directory):
            if target is not None and version > target:
                break
            _apply_one(conn, version, name, path)
            applied.append(version)
    return applied


def status(db_path: str | None = None, *, directory: Path | str | None = None) -> dict:
    """CLI 용 현황 요약."""
    path = db_path or config.APT_DB_PATH
    with get_conn(path) as conn:
        conn.execute(_LOG_TABLE)
        at = current_version(conn)
        latest = discover(directory)
        waiting = [v for v, _, _ in latest if v > at]
        from apt_engine.db.connection import table_names
        tables = [t for t in table_names(conn) if not t.startswith("_")]
    return {
        "db_path": path,
        "version": at,
        "latest": latest[-1][0] if latest else 0,
        "pending": waiting,
        "tables": tables,
    }
