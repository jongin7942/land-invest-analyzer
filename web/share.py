"""공유 링크 접근 제어 — 약국 앱(눈뜬개국)의 share.py 를 그대로 옮겨 왔다.

규칙(종인님 2026-09-04, 약국 앱과 동일):
  - 초대 링크는 한 사람이 한 번만 쓸 수 있다(처음 연 브라우저에 묶임).
    링크를 다른 사람에게 다시 보내도 열리지 않는다.
  - 종인님은 언제든 특정 사람의 접속을 끊을 수 있다(/admin/share → 끊기).
  - 터널(cloudflared)을 닫으면 전원 즉시 끊긴다.

종인님 판별 = 127.0.0.1 에서 직접 온 요청이면서 cloudflared 헤더(Cf-*)가 없는 것.

엔진 DB(apt_engine)는 건드리지 않는다. 공유 세션은 web/share.db 에 따로 둔다 —
공유 기능이 엔진 스키마·마이그레이션과 얽히면 안 된다.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "share.db")
_SECRET_PATH = os.path.join(HERE, "share_secret.txt")

COOKIE = "apt_session"
COOKIE_DAYS = 30
_secret: Optional[bytes] = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS share_sessions(
        id TEXT PRIMARY KEY, label TEXT, token TEXT UNIQUE, created_at TEXT,
        redeemed_at TEXT, revoked_at TEXT, last_seen TEXT, last_ip TEXT,
        hits INTEGER DEFAULT 0, user_agent TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    return con


def secret() -> bytes:
    global _secret
    if _secret is None:
        if os.path.exists(_SECRET_PATH):
            _secret = open(_SECRET_PATH, "rb").read().strip()
        if not _secret:
            _secret = secrets.token_hex(32).encode()
            open(_SECRET_PATH, "wb").write(_secret)
    return _secret


def _sign(sid: str) -> str:
    return hmac.new(secret(), sid.encode(), hashlib.sha256).hexdigest()[:32]


def cookie_value(sid: str) -> str:
    return f"{sid}.{_sign(sid)}"


def parse_cookie(value: Optional[str]) -> Optional[str]:
    if not value or "." not in value:
        return None
    sid, sig = value.rsplit(".", 1)
    return sid if hmac.compare_digest(sig, _sign(sid)) else None


def create(con: sqlite3.Connection, label: str) -> dict:
    sid, token = secrets.token_urlsafe(9), secrets.token_urlsafe(24)
    with con:
        con.execute("INSERT INTO share_sessions(id,label,token,created_at) VALUES(?,?,?,?)",
                    (sid, label.strip() or "이름없음", token, _now()))
    return {"id": sid, "label": label, "token": token}


def redeem(con: sqlite3.Connection, token: str, ip: str, ua: str) -> Optional[str]:
    row = con.execute("SELECT id, redeemed_at, revoked_at FROM share_sessions WHERE token=?",
                      (token,)).fetchone()
    if not row or row["revoked_at"] or row["redeemed_at"]:
        return None
    with con:
        con.execute("UPDATE share_sessions SET redeemed_at=?, last_seen=?, last_ip=?, "
                    "user_agent=?, hits=1 WHERE id=?",
                    (_now(), _now(), ip, (ua or "")[:200], row["id"]))
    return row["id"]


BOT_MARKERS = ("scrap", "bot", "facebookexternalhit", "crawler", "spider", "preview",
               "slurp", "whatsapp", "telegram")


def is_bot(ua: str) -> bool:
    """카카오톡 링크 미리보기 봇(kakaotalk-scrap) 등에는 세션을 절대 주지 않는다."""
    u = (ua or "").lower()
    return not u or any(m in u for m in BOT_MARKERS)


def peek(con: sqlite3.Connection, token: str) -> Optional[dict]:
    row = con.execute("SELECT id, label FROM share_sessions WHERE token=? AND redeemed_at IS NULL "
                      "AND revoked_at IS NULL", (token,)).fetchone()
    return dict(row) if row else None


def verify(con: sqlite3.Connection, cookie: Optional[str], ip: str) -> Optional[dict]:
    sid = parse_cookie(cookie)
    if not sid:
        return None
    row = con.execute("SELECT * FROM share_sessions WHERE id=? AND redeemed_at IS NOT NULL "
                      "AND revoked_at IS NULL", (sid,)).fetchone()
    if not row:
        return None
    with con:
        con.execute("UPDATE share_sessions SET last_seen=?, last_ip=?, hits=hits+1 WHERE id=?",
                    (_now(), ip, sid))
    return dict(row)


def revoke(con: sqlite3.Connection, sid: str) -> bool:
    with con:
        return con.execute("UPDATE share_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                           (_now(), sid)).rowcount > 0


def revoke_all(con: sqlite3.Connection) -> int:
    with con:
        return con.execute("UPDATE share_sessions SET revoked_at=? WHERE revoked_at IS NULL",
                           (_now(),)).rowcount


def list_sessions(con: sqlite3.Connection) -> list:
    rows = [dict(r) for r in con.execute("SELECT * FROM share_sessions ORDER BY created_at DESC")]
    for r in rows:
        r["state"] = "끊김" if r["revoked_at"] else ("사용중" if r["redeemed_at"] else "미사용(링크 대기)")
    return rows


def is_owner(remote_addr: Optional[str], headers) -> bool:
    if remote_addr not in ("127.0.0.1", "::1"):
        return False
    return not any(h in headers for h in ("Cf-Connecting-Ip", "Cf-Ray", "X-Forwarded-For"))


def base_url(con: sqlite3.Connection) -> str:
    row = con.execute("SELECT value FROM meta WHERE key='share_base_url'").fetchone()
    return (row["value"] if row else "") or ""


def set_base_url(con: sqlite3.Connection, url: str) -> None:
    with con:
        con.execute("INSERT INTO meta(key,value) VALUES('share_base_url',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (url.strip().rstrip("/"),))
