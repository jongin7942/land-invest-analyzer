"""마이그레이션 러너 테스트.

가장 중요한 건 원자성이다 — 중간에 실패한 마이그레이션이 스키마를 반쯤 바꿔놓고
버전만 올려버리면, 그 DB는 어떤 코드로도 다시 정상화할 수 없다.
"""
import sqlite3

import pytest

from apt_engine.db import migrate as mig
from apt_engine.db.connection import get_conn, table_names


def write(directory, name, sql):
    (directory / name).write_text(sql, encoding="utf-8")


@pytest.fixture
def migrations(tmp_path):
    d = tmp_path / "migrations"
    d.mkdir()
    return d


class TestDiscover:
    def test_번호순으로_찾는다(self, migrations):
        write(migrations, "002_second.sql", "CREATE TABLE b (id INTEGER);")
        write(migrations, "001_first.sql", "CREATE TABLE a (id INTEGER);")
        assert [(v, n) for v, n, _ in mig.discover(migrations)] == [(1, "first"), (2, "second")]

    def test_파일명_규칙_위반은_거부(self, migrations):
        write(migrations, "1_first.sql", "SELECT 1;")
        with pytest.raises(mig.MigrationError, match="파일명 규칙"):
            mig.discover(migrations)

    def test_번호가_빠지면_거부(self, migrations):
        # 003 이 빠진 채 004 를 적용하면 나중에 003 이 추가됐을 때 순서가 꼬인다.
        write(migrations, "001_a.sql", "SELECT 1;")
        write(migrations, "003_c.sql", "SELECT 1;")
        with pytest.raises(mig.MigrationError, match="빠진 번호"):
            mig.discover(migrations)

    def test_빈_폴더는_빈_목록(self, migrations):
        assert mig.discover(migrations) == []


class TestMigrate:
    def test_적용하면_버전이_올라간다(self, tmp_db, migrations):
        write(migrations, "001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
        write(migrations, "002_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")

        assert mig.migrate(tmp_db, directory=migrations) == [1, 2]
        with get_conn(tmp_db) as conn:
            assert mig.current_version(conn) == 2
            assert {"a", "b"} <= set(table_names(conn))

    def test_두_번_돌려도_안전하다(self, tmp_db, migrations):
        write(migrations, "001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
        mig.migrate(tmp_db, directory=migrations)
        assert mig.migrate(tmp_db, directory=migrations) == []

    def test_새_마이그레이션만_추가로_적용된다(self, tmp_db, migrations):
        write(migrations, "001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
        mig.migrate(tmp_db, directory=migrations)

        write(migrations, "002_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
        assert mig.migrate(tmp_db, directory=migrations) == [2]

    def test_target_까지만_적용(self, tmp_db, migrations):
        write(migrations, "001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
        write(migrations, "002_b.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")

        assert mig.migrate(tmp_db, directory=migrations, target=1) == [1]
        with get_conn(tmp_db) as conn:
            assert "b" not in table_names(conn)

    def test_적용_이력이_남는다(self, tmp_db, migrations):
        write(migrations, "001_meta.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
        mig.migrate(tmp_db, directory=migrations)
        with get_conn(tmp_db) as conn:
            row = conn.execute("SELECT version, name, applied_at FROM _migration").fetchone()
        assert row["version"] == 1 and row["name"] == "meta" and row["applied_at"]


class TestAtomicity:
    def test_실패하면_전부_롤백된다(self, tmp_db, migrations):
        write(migrations, "001_ok.sql", "CREATE TABLE ok (id INTEGER PRIMARY KEY);")
        # 두 번째 문장에서 깨지는 마이그레이션: 앞의 CREATE 도 남으면 안 된다.
        write(migrations, "002_broken.sql",
              "CREATE TABLE half (id INTEGER PRIMARY KEY);\n"
              "CREATE TABLE half (id INTEGER PRIMARY KEY);\n")

        with pytest.raises(sqlite3.Error):
            mig.migrate(tmp_db, directory=migrations)

        with get_conn(tmp_db) as conn:
            assert mig.current_version(conn) == 1     # 001 까지만
            assert "ok" in table_names(conn)
            assert "half" not in table_names(conn)    # 반쯤 적용된 흔적 없음

    def test_마이그레이션_안의_트랜잭션_제어문은_거부(self, tmp_db, migrations):
        # 러너가 감싸는데 안에서 COMMIT 하면 원자성이 깨진다.
        write(migrations, "001_bad.sql", "BEGIN;\nCREATE TABLE a (id INTEGER);\nCOMMIT;")
        with pytest.raises(mig.MigrationError, match="트랜잭션 제어문"):
            mig.migrate(tmp_db, directory=migrations)


class TestRealMigrations:
    """실제 apt_engine/db/migrations/ 내용."""

    def test_실제_마이그레이션이_적용된다(self, tmp_db):
        mig.migrate(tmp_db)
        with get_conn(tmp_db) as conn:
            tables = set(table_names(conn))
        assert {"data_source", "collection_log", "engine_version"} <= tables

    def test_출처_종류는_정해진_값만_허용(self, tmp_db):
        mig.migrate(tmp_db)
        with get_conn(tmp_db) as conn:
            conn.execute("INSERT INTO data_source (key, name, kind) VALUES (?,?,?)",
                         ("test_source", "테스트 출처", "API"))
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO data_source (key, name, kind) VALUES (?,?,?)",
                             ("bad", "정체불명", "그냥어디선가"))

    def test_수집이력은_없음과_실패를_구분한다(self, tmp_db):
        # E-10: 예외를 삼키면 "데이터가 원래 없음"과 "수집 실패"가 뭉개진다.
        mig.migrate(tmp_db)
        with get_conn(tmp_db) as conn:
            conn.executemany(
                "INSERT INTO collection_log (source_key, target, period, status, row_count, error) "
                "VALUES (?,?,?,?,?,?)",
                [("molit_apt_trade", "41135", "202608", "OK", 214, None),
                 ("molit_apt_trade", "41290", "202608", "EMPTY", 0, None),
                 ("molit_apt_trade", "11680", "202608", "FAILED", None, "HTTP 500")],
            )
            failed = conn.execute(
                "SELECT target, error FROM collection_log WHERE status='FAILED'").fetchall()
        assert len(failed) == 1 and failed[0]["error"] == "HTTP 500"

    def test_엔진_버전이_기록돼_있다(self, tmp_db):
        from apt_engine import ENGINE_VERSION
        mig.migrate(tmp_db)
        with get_conn(tmp_db) as conn:
            versions = [r[0] for r in conn.execute("SELECT version FROM engine_version")]
        assert ENGINE_VERSION in versions

    def test_status_요약(self, tmp_db):
        mig.migrate(tmp_db)
        s = mig.status(tmp_db)
        assert s["pending"] == []
        assert s["version"] == s["latest"] >= 1
        assert "_migration" not in s["tables"]  # 내부 테이블은 숨긴다


class TestConnection:
    def test_예외가_나면_롤백된다(self, tmp_db, migrations):
        write(migrations, "001_a.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY, v TEXT);")
        mig.migrate(tmp_db, directory=migrations)

        with pytest.raises(RuntimeError):
            with get_conn(tmp_db) as conn:
                conn.execute("INSERT INTO a (v) VALUES ('x')")
                raise RuntimeError("중간에 터짐")

        with get_conn(tmp_db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM a").fetchone()[0] == 0

    def test_외래키가_켜져_있다(self, tmp_db):
        mig.migrate(tmp_db)
        with get_conn(tmp_db) as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
