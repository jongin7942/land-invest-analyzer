"""초대 — 허락한 사람만 화면을 볼 수 있게 한다.

**왜 공용 비밀번호 하나가 아닌가**

비번 하나를 단톡방에 뿌리면 세 가지를 못 한다.

  ① 한 명만 끊기      — 새어 나가면 전부 바꾸고 전부에게 다시 보내야 한다
  ② 누가 들어왔는지   — 아무도 안 본 링크와 다섯 명이 본 링크가 구분이 안 된다
  ③ 나중에 다시 보내기 — 누구에게 뭘 보냈는지 기억에 의존하게 된다

그래서 사람마다 코드를 따로 준다. 하나가 새면 그 하나만 끊는다.

**왜 별도 파일인가**

`apt_invest.db` 는 수집이 계속 쓰고 있고, 화면은 그 DB 를 **읽기 전용**으로
연다(`ro_conn`). 방문 기록을 남기려면 써야 하는데, 같은 파일에 쓰면
수집을 잠근다. 초대 정보는 작고 성격도 달라서 파일을 나누는 편이 맞다.

**코드를 평문으로 저장하는 이유**

비밀번호가 아니라 **초대장**이다. 소유자가 "철수한테 보낸 링크 뭐였지"
하고 다시 꺼내 보낼 수 있어야 한다. 해시로 저장하면 재발송이 불가능해서,
새 코드를 만들고 다시 보내는 수밖에 없다. 다른 서비스와 공유되는
비밀번호가 아니므로 유출 시 피해가 이 화면 하나에 갇힌다.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# 코드 길이. 12자 URL-safe = 약 72비트. 링크로 전달되므로 사람이 외울
# 필요는 없고, 무작위로 맞히는 것이 불가능하기만 하면 된다.
CODE_BYTES = 9

SCHEMA = """
CREATE TABLE IF NOT EXISTS invite (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,       -- 누구에게 준 것인지 (내가 알아보려고)
  code       TEXT NOT NULL UNIQUE,
  note       TEXT,
  created_at TEXT NOT NULL,
  revoked_at TEXT,                       -- 채워지면 그 즉시 안 열린다
  last_seen  TEXT,
  visits     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_invite_code ON invite(code);
"""


def db_path() -> str:
    raw = (os.getenv("APT_ACCESS_DB") or "").strip()
    if raw:
        return raw
    return str(Path(__file__).resolve().parent.parent / "access.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path(), timeout=5.0)
    conn.row_factory = sqlite3.Row
    # WAL 이면 방문 기록을 쓰는 동안에도 다른 요청이 읽을 수 있다.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


@dataclass(frozen=True)
class Invite:
    name: str
    code: str
    note: str | None
    created_at: str
    revoked_at: str | None
    last_seen: str | None
    visits: int

    @property
    def active(self) -> bool:
        return self.revoked_at is None


def _row(r: sqlite3.Row) -> Invite:
    return Invite(name=r["name"], code=r["code"], note=r["note"],
                  created_at=r["created_at"], revoked_at=r["revoked_at"],
                  last_seen=r["last_seen"], visits=r["visits"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add(conn: sqlite3.Connection, name: str, note: str | None = None) -> Invite:
    """한 사람에게 줄 코드를 만든다. 같은 이름이 이미 있으면 거절한다 —
    덮어쓰면 먼저 보낸 링크가 조용히 죽어서, 상대는 왜 안 되는지 모른다."""
    name = name.strip()
    if not name:
        raise ValueError("이름이 필요합니다 — 누구에게 준 코드인지 알아야 끊을 수 있습니다")
    if conn.execute("SELECT 1 FROM invite WHERE name=?", (name,)).fetchone():
        raise ValueError(
            f"'{name}' 은 이미 있습니다. 코드를 새로 주려면 먼저 revoke 하세요 "
            f"(먼저 보낸 링크가 조용히 죽지 않도록 덮어쓰지 않습니다)")
    code = secrets.token_urlsafe(CODE_BYTES)
    conn.execute(
        "INSERT INTO invite (name, code, note, created_at) VALUES (?,?,?,?)",
        (name, code, note, _now()))
    conn.commit()
    return _row(conn.execute("SELECT * FROM invite WHERE code=?", (code,)).fetchone())


def revoke(conn: sqlite3.Connection, name: str) -> bool:
    """그 사람만 끊는다. 나머지 링크는 그대로 산다."""
    cur = conn.execute(
        "UPDATE invite SET revoked_at=? WHERE name=? AND revoked_at IS NULL",
        (_now(), name.strip()))
    conn.commit()
    return cur.rowcount > 0


def restore(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "UPDATE invite SET revoked_at=NULL WHERE name=?", (name.strip(),))
    conn.commit()
    return cur.rowcount > 0


def list_all(conn: sqlite3.Connection) -> list[Invite]:
    return [_row(r) for r in conn.execute(
        "SELECT * FROM invite ORDER BY revoked_at IS NOT NULL, created_at")]


def any_active(conn: sqlite3.Connection) -> bool:
    """열려 있는 초대가 하나라도 있나."""
    return conn.execute(
        "SELECT 1 FROM invite WHERE revoked_at IS NULL LIMIT 1").fetchone() is not None


def any_invites(conn: sqlite3.Connection) -> bool:
    """초대를 **한 번이라도 만든 적이 있나** (끊은 것 포함).

    잠금 여부는 이것으로 판단한다. `any_active` 로 판단하면 마지막
    한 명을 끊는 순간 잠금이 통째로 사라져서, 모두를 내보내려던 행동이
    **모두를 들여보내는** 결과가 된다. 끊는 것과 잠금을 푸는 것은 다르다.
    """
    return conn.execute("SELECT 1 FROM invite LIMIT 1").fetchone() is not None


def check(conn: sqlite3.Connection, code: str) -> Invite | None:
    """코드가 살아 있으면 방문을 기록하고 돌려준다.

    비교는 상수 시간으로 한다. 코드가 12자라 현실적인 위협은 아니지만,
    맞는 글자 수에 따라 응답이 빨라지는 것을 굳이 남길 이유가 없다.
    """
    code = (code or "").strip()
    if not code:
        return None
    given = code.encode("utf-8")
    for r in conn.execute("SELECT * FROM invite WHERE revoked_at IS NULL"):
        if secrets.compare_digest(given, r["code"].encode("utf-8")):
            conn.execute(
                "UPDATE invite SET last_seen=?, visits=visits+1 WHERE id=?",
                (_now(), r["id"]))
            conn.commit()
            return _row(r)
    return None


def link(base_url: str, code: str) -> str:
    return f"{base_url.rstrip('/')}/?code={code}"


__all__ = ["Invite", "connect", "db_path", "add", "revoke", "restore",
           "list_all", "any_active", "any_invites", "check", "link"]
