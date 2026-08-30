"""아파트 DB 커넥션.

기존 `db/schema.get_conn()` 과 사용법은 같지만 세 가지가 다르다:
  * 여는 파일이 `config.APT_DB_PATH` (토지 DB가 아니다)
  * 예외 시 명시적으로 롤백한다
  * WAL 저널 + busy_timeout 을 켠다 — 수집 배치가 도는 중에도 웹에서 읽을 수 있게.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import config


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # WAL: 쓰기 중에도 읽기가 막히지 않는다(수집 배치 + 웹 동시 사용).
    # :memory: DB 에서는 무시되지만 에러는 아니다.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")


@contextmanager
def get_conn(db_path: str | None = None):
    """트랜잭션 컨텍스트. 정상 종료 시 커밋, 예외 시 롤백."""
    conn = sqlite3.connect(db_path or config.APT_DB_PATH)
    try:
        _configure(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def table_names(conn: sqlite3.Connection) -> list[str]:
    """사용자 테이블 목록(sqlite 내부 테이블 제외)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]
