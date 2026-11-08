from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

from src.access_control import AccessDeniedError, PERM_NETSUITE_SYNC
from src.netsuite_provider import NetSuiteDataProvider, NetSuiteDataset
from src.netsuite_queries import QUERY_CATALOG
from src.netsuite_sync import (
    NetSuiteSyncService,
    SqliteNetSuiteSyncRepository,
    SYNC_COMPLETED,
    SYNC_COMPLETED_WARNINGS,
    SYNC_ERROR,
)


def make_dataset(invoice_values=(100, 100)):
    empty = pd.DataFrame()
    return NetSuiteDataset(
        invoices=pd.DataFrame(
            {
                "id": [1] * len(invoice_values),
                "line_id": list(range(10, 10 + len(invoice_values))),
                "Valor": list(invoice_values),
            }
        ),
        costs=empty.copy(),
        proformas=empty.copy(),
        credit_notes=empty.copy(),
        proforma_cancellations=empty.copy(),
        invoice_payments=empty.copy(),
        proforma_payments=empty.copy(),
    )


class StaticProvider:
    def __init__(self, dataset):
        self.dataset = dataset

    def load_dataset(self):
        return self.dataset

    def load_dataset_modified_between(self, start, end):
        self.window = (start, end)
        return self.dataset


class FailingProvider:
    def load_dataset(self):
        raise RuntimeError("fallo controlado")


class SecretFailingProvider:
    def load_dataset(self):
        raise RuntimeError("password=privada\ntoken=secreto")


class Authorization:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    def require_permission(self, user_id, permission):
        self.calls.append((user_id, permission))
        if not self.allowed:
            raise AccessDeniedError("Permiso denegado")


class NetSuiteSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "sync.db"
        self.repository = SqliteNetSuiteSyncRepository(self.db_path)
        self.authorization = Authorization()

    def service(self, provider, authorization=None):
        return NetSuiteSyncService(
            provider, self.repository, authorization or self.authorization
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_schema_indexes_sync_status_and_history_order(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            indexes = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='netsuite_sync_runs'"
                )
            }

        self.assertIn("idx_netsuite_sync_runs_status_started", indexes)

    def test_repeated_snapshot_is_idempotent_and_preserves_duplicates(self):
        service = self.service(StaticProvider(make_dataset()))

        first = service.synchronize(10, run_code="run-1")
        second = service.synchronize(10, run_code="run-2")

        self.assertEqual(first.status, SYNC_COMPLETED)
        self.assertEqual(first.metrics.created, 2)
        self.assertEqual(second.metrics.created, 0)
        self.assertEqual(second.metrics.unchanged, 2)
        self.assertEqual(
            [(10, PERM_NETSUITE_SYNC), (10, PERM_NETSUITE_SYNC)],
            self.authorization.calls,
        )
        active = self.repository.load_active_frame("invoices")
        self.assertEqual(len(active), 2)
        with closing(sqlite3.connect(self.db_path)) as connection:
            actors = connection.execute(
                "SELECT DISTINCT executed_by FROM netsuite_sync_runs"
            ).fetchall()
        self.assertEqual([("10",)], actors)

    def test_repository_is_provider_and_preserves_empty_frame_schema(self):
        original = make_dataset((100,))
        dataset = NetSuiteDataset(
            **{
                **original.__dict__,
                "credit_notes": pd.DataFrame(columns=["id", "tranId", "Valor"]),
            }
        )
        self.service(StaticProvider(dataset)).synchronize(10, run_code="run-schema")

        self.assertIsInstance(self.repository, NetSuiteDataProvider)
        restored = self.repository.load_dataset()
        self.assertEqual(
            restored.credit_notes.columns.tolist(), ["id", "tranId", "Valor"]
        )
        self.assertTrue(restored.credit_notes.empty)
        self.assertEqual(len(restored.invoices), 1)

    def test_changed_snapshot_deactivates_missing_records_without_deleting_history(self):
        self.service(StaticProvider(make_dataset())).synchronize(10, run_code="run-1")
        result = self.service(StaticProvider(make_dataset((100,)))).synchronize(
            10, run_code="run-2"
        )

        self.assertEqual(result.metrics.deactivated, 1)
        self.assertEqual(len(self.repository.load_active_frame("invoices")), 1)
        with closing(sqlite3.connect(self.db_path)) as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM netsuite_sync_records WHERE dataset_name='invoices'"
            ).fetchone()[0]
        self.assertEqual(total, 2)

    def test_incremental_snapshot_never_deactivates_records_outside_its_cut(self):
        self.service(StaticProvider(make_dataset())).synchronize(
            10, sync_type="FULL", run_code="run-full"
        )

        result = self.service(StaticProvider(make_dataset((125,)))).synchronize(
            10,
            sync_type="INCREMENTAL",
            run_code="run-incremental",
            window_start="2026-10-01",
            window_end="2026-11-01",
        )

        self.assertEqual(0, result.metrics.deactivated)
        active = self.repository.load_active_frame("invoices")
        self.assertEqual(2, len(active))
        self.assertEqual([125, 100], active.sort_values("line_id")["Valor"].tolist())
        with closing(sqlite3.connect(self.db_path)) as connection:
            sync_type = connection.execute(
                "SELECT sync_type FROM netsuite_sync_runs "
                "WHERE run_code='run-incremental'"
            ).fetchone()[0]
            window = connection.execute(
                "SELECT window_start, window_end FROM netsuite_sync_windows "
                "WHERE run_code='run-incremental'"
            ).fetchone()
        self.assertEqual("INCREMENTAL", sync_type)
        self.assertEqual(("2026-10-01", "2026-11-01"), window)

    def test_invalid_sync_type_is_rejected_before_creating_run(self):
        with self.assertRaisesRegex(ValueError, "FULL o INCREMENTAL"):
            self.service(StaticProvider(make_dataset())).synchronize(
                10, sync_type="PARCIAL", run_code="run-invalid-type"
            )

        with closing(sqlite3.connect(self.db_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM netsuite_sync_runs"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_incremental_requires_complete_window(self):
        with self.assertRaisesRegex(ValueError, "window_start y window_end"):
            self.service(StaticProvider(make_dataset())).synchronize(
                10, sync_type="INCREMENTAL", run_code="run-no-window"
            )

    def test_content_change_updates_same_netsuite_identity(self):
        self.service(StaticProvider(make_dataset((100,)))).synchronize(
            10, run_code="run-1"
        )
        result = self.service(StaticProvider(make_dataset((125,)))).synchronize(
            10, run_code="run-2"
        )

        self.assertEqual(0, result.metrics.created)
        self.assertEqual(1, result.metrics.updated)
        self.assertEqual(0, result.metrics.deactivated)
        active = self.repository.load_active_frame("invoices")
        self.assertEqual([125], active["Valor"].tolist())
        with closing(sqlite3.connect(self.db_path)) as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM netsuite_sync_records "
                "WHERE dataset_name='invoices'"
            ).fetchone()[0]
        self.assertEqual(1, total)

    def test_quality_warnings_are_audited_without_blocking_snapshot(self):
        dataset = make_dataset((100, 125))
        dataset.invoices.loc[1, "line_id"] = 10
        dataset.invoices["tranDate"] = ["2026-01-01", "fecha invalida"]

        result = self.service(StaticProvider(dataset)).synchronize(
            10, run_code="run-warning"
        )

        self.assertEqual(SYNC_COMPLETED_WARNINGS, result.status)
        self.assertEqual(2, len(self.repository.load_active_frame("invoices")))
        with closing(sqlite3.connect(self.db_path)) as connection:
            status, message = connection.execute(
                "SELECT status, message FROM netsuite_sync_runs "
                "WHERE run_code='run-warning'"
            ).fetchone()
        self.assertEqual(SYNC_COMPLETED_WARNINGS, status)
        self.assertIn("DUPLICATE_IDENTITY", message)
        self.assertIn("INVALID_DATE", message)

    def test_nonempty_dataset_without_stable_identity_is_rejected(self):
        dataset = make_dataset((100,))
        dataset = NetSuiteDataset(
            **{**dataset.__dict__, "invoices": dataset.invoices.drop(columns="line_id")}
        )

        with self.assertRaisesRegex(ValueError, "identidad estable"):
            self.service(StaticProvider(dataset)).synchronize(
                10, run_code="run-invalid"
            )

    def test_document_queries_expose_stable_transaction_line_identity(self):
        for dataset_name in (
            "invoices", "costs", "proformas", "credit_notes",
            "proforma_cancellations",
        ):
            self.assertIn("line_id", QUERY_CATALOG[dataset_name], dataset_name)

    def test_concurrent_running_sync_is_rejected(self):
        self.repository.start_run("run-active", "tester", "FULL")
        with self.assertRaisesRegex(RuntimeError, "sincronizacion en proceso"):
            self.repository.start_run("run-other", "tester", "FULL")

    def test_provider_failure_is_audited(self):
        service = self.service(FailingProvider())
        with self.assertRaisesRegex(RuntimeError, "fallo controlado"):
            service.synchronize(10, run_code="run-error")

        with closing(sqlite3.connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT status, message FROM netsuite_sync_runs WHERE run_code='run-error'"
            ).fetchone()
        self.assertEqual(row[0], SYNC_ERROR)
        self.assertEqual(row[1], "fallo controlado")

    def test_provider_failure_redacts_secrets_before_persisting(self):
        service = self.service(SecretFailingProvider())
        with self.assertRaises(RuntimeError):
            service.synchronize(10, run_code="run-secret-error")

        with closing(sqlite3.connect(self.db_path)) as connection:
            message = connection.execute(
                "SELECT message FROM netsuite_sync_runs "
                "WHERE run_code='run-secret-error'"
            ).fetchone()[0]
        self.assertNotIn("privada", message)
        self.assertNotIn("secreto", message)
        self.assertNotIn("\n", message)
        self.assertEqual(
            "password=[REDACTED] token=[REDACTED]", message
        )

    def test_synchronization_requires_independent_backend_permission(self):
        denied = Authorization(allowed=False)

        with self.assertRaises(AccessDeniedError):
            self.service(StaticProvider(make_dataset()), denied).synchronize(
                77, run_code="run-denied"
            )

        self.assertEqual([(77, PERM_NETSUITE_SYNC)], denied.calls)
        with closing(sqlite3.connect(self.db_path)) as connection:
            runs = connection.execute(
                "SELECT COUNT(*) FROM netsuite_sync_runs"
            ).fetchone()[0]
        self.assertEqual(0, runs)

    def test_run_history_is_queryable_only_with_independent_permission(self):
        service = self.service(StaticProvider(make_dataset((100,))))
        service.synchronize(10, run_code="run-history")

        history = service.list_runs(10)

        self.assertEqual("run-history", history[0].run_code)
        self.assertEqual(SYNC_COMPLETED, history[0].status)
        self.assertEqual(1, history[0].created_count)
        denied = self.service(StaticProvider(make_dataset()), Authorization(False))
        with self.assertRaises(AccessDeniedError):
            denied.list_runs(77)

    def test_run_history_limit_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "limit"):
            self.repository.list_runs(0)
        with self.assertRaisesRegex(ValueError, "limit"):
            self.repository.list_runs(501)


if __name__ == "__main__":
    unittest.main()
