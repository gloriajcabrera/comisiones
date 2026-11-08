from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from io import BytesIO
from types import SimpleNamespace

import pandas as pd
from openpyxl import load_workbook

from src.access_control import (
    AccessDeniedError,
    AuthorizationService,
    PERM_PAYMENT_FILE_SEND,
    ROLE_EXECUTIVE,
    ROLE_REVIEWER,
    SqliteAccessRepository,
)
from src.payment_files import (
    ActiveCalculationPaymentSummaryProvider,
    FILE_SENT,
    PaymentFileService,
    SqlitePaymentFileRepository,
)


class FakeResultProvider:
    run_code = "calc-1"

    def commission_results(self, run_code):
        if run_code != self.run_code:
            raise KeyError(run_code)
        return pd.DataFrame({
            "executive": ["EJ-01", "EJ-01"],
            "period": ["2026-08", "2026-08"],
            "commission_executive": [100, 50],
            "commission_manager": [20, 10],
            "commission_director": [5, 5],
        })


class FakeAccessProfiles:
    def executive_hierarchy_context(self, executive_codes, effective_on):
        return [{
            "executive": "EJ-01", "manager": "J-01", "director": "G-01",
        }]

    def list_user_profiles(self, effective_on):
        return [
            SimpleNamespace(
                identification="10", first_names="Eva", last_names="Ejecutiva",
                commercial_entities=("EJ-01",),
            ),
            SimpleNamespace(
                identification="20", first_names="Juan", last_names="Jefe",
                commercial_entities=("J-01",),
            ),
            SimpleNamespace(
                identification="30", first_names="Gina", last_names="Gerente",
                commercial_entities=("G-01",),
            ),
        ]


class FakeDiscounts:
    def list_applications(self, period):
        return pd.DataFrame({
            "executive": ["EJ-01"], "amount": [25], "period": [period],
        })


class FakeCalculationRepository:
    def __init__(
        self, status="COMPLETADO", period="2026-08", workflow_status="APROBADO"
    ):
        self.status = status
        self.period = period
        self.current_workflow_status = workflow_status

    def get_run(self, run_code):
        if run_code == "missing":
            return None
        return {"run_code": run_code, "status": self.status, "period": self.period}

    def workflow_status(self, run_code):
        return self.current_workflow_status


class FakePeriodRepository:
    def __init__(self, status="FINALIZADO"):
        self.current_status = status

    def status(self, period):
        return self.current_status


class FakeMailProvider:
    def __init__(self):
        self.messages = []

    def send(self, **message):
        self.messages.append(message)
        return "mail-123"


class FailingMailProvider:
    def send(self, **message):
        raise RuntimeError("api_key=privada\nfallo proveedor")


class FakeSummaryProvider:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def load_payment_summary(self, calculation_run, period):
        self.calls.append((calculation_run, period))
        return self.data.copy()


class PaymentFilesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.access = SqliteAccessRepository(root / "access.db")
        self.authorization = AuthorizationService(self.access)
        self.repository = SqlitePaymentFileRepository(root / "payments.db")
        self.mail = FakeMailProvider()
        self.summary_provider = FakeSummaryProvider(self.summary())
        self.service = PaymentFileService(
            self.repository, self.authorization, self.summary_provider, self.mail
        )
        self.reviewer = self._user("reviewer", ROLE_REVIEWER)
        self.executive = self._user("executive", ROLE_EXECUTIVE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _user(self, username, role):
        user_id = self.access.create_user(username, username, "Test")
        self.access.assign_role(user_id, role)
        return user_id

    @staticmethod
    def summary():
        return pd.DataFrame(
            {
                "identificacion": ["20", "10"],
                "nombre": ["Segundo", "Primero"],
                "valor_total": [200.5, 100],
                "columna_no_exportada": ["x", "y"],
            }
        )

    def test_finalization_persists_minimum_totals_without_creating_excel(self):
        first = self.service.finalize(
            self.reviewer, "2026-08", "calc-1", "Cierre revisado"
        )
        second = self.service.finalize(
            self.reviewer, "2026-08", "calc-1", "Segunda versión"
        )

        self.assertEqual(1, first.version)
        self.assertEqual(2, second.version)
        self.assertEqual([], list(Path(self.temp_dir.name).rglob("*.xlsx")))
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertNotIn("payment_batch_rows", tables)
        self.assertIn("payment_batch_totals", tables)
        self.assertEqual(
            [("calc-1", "2026-08"), ("calc-1", "2026-08")],
            self.summary_provider.calls,
        )

    def test_operational_summary_distributes_hierarchy_and_discount(self):
        provider = ActiveCalculationPaymentSummaryProvider(
            FakeResultProvider(), FakeAccessProfiles(), FakeDiscounts()
        )

        result = provider.load_payment_summary("calc-1", "2026-08")

        values = result.set_index("identificacion")["valor_total"].to_dict()
        self.assertEqual({"10": 125, "20": 30, "30": 10}, values)
        names = result.set_index("identificacion")["nombre"].to_dict()
        self.assertEqual("Juan Jefe", names["20"])

    def test_operational_summary_rejects_a_different_active_run(self):
        provider = ActiveCalculationPaymentSummaryProvider(
            FakeResultProvider(), FakeAccessProfiles()
        )
        with self.assertRaisesRegex(RuntimeError, "otro cálculo"):
            provider.load_payment_summary("calc-old", "2026-08")

    def test_summary_combines_multiple_commercial_roles_for_one_person(self):
        access = FakeAccessProfiles()
        profiles = access.list_user_profiles("2026-08-01")
        profiles[0].commercial_entities = ("EJ-01", "J-01")
        access.list_user_profiles = lambda effective_on: [profiles[0], profiles[2]]
        provider = ActiveCalculationPaymentSummaryProvider(
            FakeResultProvider(), access, FakeDiscounts()
        )

        result = provider.load_payment_summary("calc-1", "2026-08")

        values = result.set_index("identificacion")["valor_total"].to_dict()
        self.assertEqual({"10": 155, "30": 10}, values)

    def test_finalization_validates_period_and_calculation_in_backend(self):
        service = PaymentFileService(
            self.repository,
            self.authorization,
            self.summary_provider,
            calculation_repository=FakeCalculationRepository(),
            period_repository=FakePeriodRepository(),
        )

        batch = service.finalize(
            self.reviewer, "2026-08", "calc-1", "Periodo revisado"
        )

        self.assertEqual("2026-08", batch.period)
        record = service.list_batches(self.reviewer)[0]
        self.assertEqual("Periodo revisado", record.finalization_reason)

    def test_finalization_rejects_open_period_failed_or_mismatched_calculation(self):
        open_period = PaymentFileService(
            self.repository,
            self.authorization,
            self.summary_provider,
            calculation_repository=FakeCalculationRepository(),
            period_repository=FakePeriodRepository("ABIERTO"),
        )
        with self.assertRaisesRegex(RuntimeError, "FINALIZADO o CERRADO"):
            open_period.finalize(
                self.reviewer, "2026-08", "calc-1", "Intento prematuro"
            )

        failed = PaymentFileService(
            self.repository,
            self.authorization,
            self.summary_provider,
            calculation_repository=FakeCalculationRepository("ERROR"),
            period_repository=FakePeriodRepository(),
        )
        with self.assertRaisesRegex(RuntimeError, "no está completado"):
            failed.finalize(
                self.reviewer, "2026-08", "calc-1", "Cálculo fallido"
            )

        mismatched = PaymentFileService(
            self.repository,
            self.authorization,
            self.summary_provider,
            calculation_repository=FakeCalculationRepository(period="2026-07"),
            period_repository=FakePeriodRepository(),
        )
        with self.assertRaisesRegex(ValueError, "no corresponde"):
            mismatched.finalize(
                self.reviewer, "2026-08", "calc-1", "Periodo incorrecto"
            )

    def test_download_generates_excel_in_memory_and_is_audited(self):
        batch = self.service.finalize(
            self.reviewer, "2026-08", "calc-1", "Cierre revisado"
        )
        name, content = self.service.download(self.reviewer, batch.batch_id)

        self.assertEqual("pago_comisiones_2026-08_v001.xlsx", name)
        self.assertTrue(content.startswith(b"PK"))
        self.assertEqual([], list(Path(self.temp_dir.name).rglob("*.xlsx")))
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        try:
            rows = list(workbook["Pago"].iter_rows(values_only=True))
        finally:
            workbook.close()
        self.assertEqual(("Identificacion", "Nombre", "Valor Total"), rows[0])
        self.assertEqual(("10", "Primero", 100), rows[1])
        self.assertEqual([("calc-1", "2026-08")], self.summary_provider.calls)
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            downloads = connection.execute(
                "SELECT COUNT(*) FROM payment_download_events"
            ).fetchone()[0]
        self.assertEqual(1, downloads)

    def test_generation_requires_backend_permission(self):
        with self.assertRaises(AccessDeniedError):
            self.service.finalize(
                self.executive, "2026-08", "calc-1", "Sin permiso"
            )

    def test_send_requires_independent_permission_and_is_audited(self):
        artifact = self.service.finalize(
            self.reviewer, "2026-08", "calc-1", "Cierre revisado"
        )
        with self.assertRaises(AccessDeniedError):
            self.service.send(
                self.reviewer, artifact.batch_id, ["payroll@test"], "Pago", "Adjunto"
            )

        self.access.grant_user_permission(
            self.reviewer, PERM_PAYMENT_FILE_SEND, True
        )
        self.service.send(
            self.reviewer,
            artifact.batch_id,
            ["PAYROLL@test", "payroll@test"],
            "Pago",
            "Adjunto",
        )

        self.assertEqual(("payroll@test",), self.mail.messages[0]["recipients"])
        self.assertTrue(self.mail.messages[0]["attachment_bytes"].startswith(b"PK"))
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            event = connection.execute(
                "SELECT status, provider_reference FROM payment_file_send_events"
            ).fetchone()
        self.assertEqual((FILE_SENT, "mail-123"), event)

        batches = self.service.list_batches(self.reviewer, "2026-08")
        events = self.service.list_events(self.reviewer, artifact.batch_id)
        self.assertEqual(1, batches[0].send_count)
        self.assertEqual(FILE_SENT, batches[0].last_send_status)
        self.assertEqual("ENVIO", events[0]["event"])

    def test_batch_queries_require_permission_and_include_download_count(self):
        batch = self.service.finalize(
            self.reviewer, "2026-08", "calc-1", "Cierre revisado"
        )
        self.service.download(self.reviewer, batch.batch_id)

        records = self.service.list_batches(self.reviewer)

        self.assertEqual(1, records[0].download_count)
        self.assertEqual("calc-1", records[0].calculation_run)
        with self.assertRaises(AccessDeniedError):
            self.service.list_batches(self.executive)

    def test_invalid_summary_prevents_finalization(self):
        self.summary_provider.data = self.summary().drop(columns="identificacion")
        with self.assertRaisesRegex(ValueError, "Faltan columnas"):
            self.service.finalize(
                self.reviewer, "2026-08", "calc-1", "Cierre revisado"
            )
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM payment_batches"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_invalid_period_prevents_finalization(self):
        with self.assertRaisesRegex(ValueError, "AAAA-MM"):
            self.service.finalize(
                self.reviewer, "2026-13", "calc-1", "Cierre revisado"
            )

        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM payment_batches"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_download_uses_immutable_finalized_totals(self):
        batch = self.service.finalize(
            self.reviewer, "2026-08", "calc-1", "Cierre revisado"
        )
        self.summary_provider.data.loc[:, "valor_total"] = [9999, 9999]

        _, content = self.service.download(self.reviewer, batch.batch_id)

        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        try:
            rows = list(workbook["Pago"].iter_rows(values_only=True))
        finally:
            workbook.close()
        self.assertEqual(("10", "Primero", 100), rows[1])
        self.assertEqual([("calc-1", "2026-08")], self.summary_provider.calls)

    def test_send_failure_is_audited_without_persisting_secrets(self):
        self.access.grant_user_permission(
            self.reviewer, PERM_PAYMENT_FILE_SEND, True
        )
        service = PaymentFileService(
            self.repository,
            self.authorization,
            self.summary_provider,
            FailingMailProvider(),
        )
        batch = service.finalize(
            self.reviewer, "2026-08", "calc-1", "Cierre revisado"
        )

        with self.assertRaises(RuntimeError):
            service.send(
                self.reviewer, batch.batch_id, ["payroll@test"], "Pago", "Adjunto"
            )

        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            event = connection.execute(
                "SELECT status, error_message FROM payment_file_send_events"
            ).fetchone()
        self.assertEqual("ERROR_ENVIO", event[0])
        self.assertEqual(
            "api_key=[REDACTED] fallo proveedor", event[1]
        )


if __name__ == "__main__":
    unittest.main()
