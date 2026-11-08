import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.database import (
    Database,
    DatabaseSession,
    DatabaseSettings,
    POSTGRESQL,
    SQLITE,
)


class _FakeConnection:
    def __init__(self):
        self.calls = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, sql, parameters=()):
        self.calls.append((sql, parameters))
        return self

    def cursor(self):
        return _FakeBatchCursor(self.calls)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _FakeCursor:
    def __init__(self, returned_id):
        self.returned_id = returned_id

    def fetchone(self):
        return (self.returned_id,)


class _FakeBatchCursor:
    def __init__(self, calls):
        self.calls = calls
        self.closed = False

    def executemany(self, sql, rows):
        self.calls.append((sql, rows))

    def close(self):
        self.closed = True


class DatabaseTest(unittest.TestCase):
    def test_environment_requires_postgres_dsn(self):
        with patch.dict(
            os.environ,
            {"COMMISSIONS_DB_BACKEND": POSTGRESQL},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "COMMISSIONS_POSTGRES_DSN"):
                DatabaseSettings.from_environment()

    def test_sqlite_settings_use_external_path(self):
        with patch.dict(
            os.environ,
            {
                "COMMISSIONS_DB_BACKEND": SQLITE,
                "COMMISSIONS_SQLITE_PATH": "state/test.db",
            },
            clear=True,
        ):
            settings = DatabaseSettings.from_environment()
        self.assertEqual(SQLITE, settings.backend)
        self.assertEqual(Path("state/test.db"), settings.sqlite_path)

    def test_postgres_session_adapts_parameters_and_closes(self):
        connection = _FakeConnection()
        with DatabaseSession(connection, POSTGRESQL) as session:
            session.execute("SELECT ? AS value", (7,))
        self.assertEqual([("SELECT %s AS value", (7,))], connection.calls)
        self.assertTrue(connection.committed)
        self.assertTrue(connection.closed)

    def test_postgres_executemany_adapts_parameters(self):
        connection = _FakeConnection()
        rows = [(1, "A"), (2, "B")]
        with DatabaseSession(connection, POSTGRESQL) as session:
            session.executemany(
                "INSERT INTO sample(id, name) VALUES (?, ?)", rows
            )
        self.assertEqual(
            [("INSERT INTO sample(id, name) VALUES (%s, %s)", rows)],
            connection.calls,
        )

    def test_postgres_write_lock_uses_validated_table_name(self):
        connection = _FakeConnection()
        with DatabaseSession(connection, POSTGRESQL) as session:
            session.lock_table_for_write("calculation_runs")
            with self.assertRaisesRegex(ValueError, "tabla inválido"):
                session.lock_table_for_write("calculation_runs; DROP TABLE users")
        self.assertEqual(
            ("LOCK TABLE calculation_runs IN EXCLUSIVE MODE", ()),
            connection.calls[0],
        )

    def test_postgres_insert_returns_generated_id(self):
        connection = _FakeConnection()
        connection.execute = lambda sql, parameters=(): _FakeCursor(42)
        with DatabaseSession(connection, POSTGRESQL) as session:
            generated = session.insert_returning_id(
                "INSERT INTO sample(name) VALUES (?)", ("x",)
            )
        self.assertEqual(42, generated)

    def test_sqlite_session_rolls_back_on_error(self):
        connection = _FakeConnection()
        with self.assertRaisesRegex(RuntimeError, "fallo"):
            with DatabaseSession(connection, SQLITE):
                raise RuntimeError("fallo")
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.closed)

    def test_sqlite_database_creates_parent_and_executes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "state.db"
            database = Database.sqlite(path)
            with database.session() as session:
                session.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
            self.assertTrue(path.exists())

    def test_financial_type_preserves_postgres_precision(self):
        self.assertEqual("REAL", Database.sqlite(Path("local.db")).money_type())
        self.assertEqual(
            "NUMERIC(20, 6)",
            Database.postgresql("postgresql://configured-externally").money_type(),
        )

    def test_view_creation_is_idempotent_for_each_database(self):
        query = "SELECT 1 AS value"
        sqlite_statement = Database.sqlite(Path("local.db")).create_view_statement(
            "vw_sample", query
        )
        postgres_statement = Database.postgresql(
            "postgresql://configured-externally"
        ).create_view_statement("vw_sample", query)

        self.assertEqual(
            "CREATE VIEW IF NOT EXISTS vw_sample AS SELECT 1 AS value",
            sqlite_statement,
        )
        self.assertEqual(
            "CREATE OR REPLACE VIEW vw_sample AS SELECT 1 AS value",
            postgres_statement,
        )


if __name__ == "__main__":
    unittest.main()
