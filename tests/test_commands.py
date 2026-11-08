from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from src.calculation_service import CalculationExecution
from src.commands import _month_windows, _prepare_legacy_targets, main
from src.netsuite_provider import NetSuiteApiHttpError, NetSuiteCatalogCheckError
from src.netsuite_sync import SyncMetrics, SyncResult


class SyncService:
    def __init__(self):
        self.calls = []

    def synchronize(
        self, actor_user_id, sync_type, run_code, window_start, window_end
    ):
        self.calls.append(
            (actor_user_id, sync_type, run_code, window_start, window_end)
        )
        return SyncResult(
            "sync-1", "COMPLETADO", {"invoices": 2},
            SyncMetrics(created=2),
        )


class CalculationService:
    def __init__(self):
        self.calls = []

    def execute_approved_inputs(self, executed_by, period, run_code):
        self.calls.append((executed_by, period, run_code))
        return CalculationExecution(
            run_code="calc-1",
            status="COMPLETADO",
            idempotency_key="key",
            reused=False,
            commission_rows=5,
            floor_rows=2,
            payment_rows=4,
            warning_count=2,
            warning_message="documentos para revisar: 2",
        )


class Runtime:
    def __init__(self):
        self.sync_service = SyncService()
        self.calculation_service = CalculationService()
        self.access_repository = BootstrapRepository()


class Workflow:
    def __init__(self, *, target=False, existing=None):
        self.target = target
        self.existing = existing or []
        self.created = []
        self.submitted = []

    def list_batches(self):
        return self.existing

    def create_batch(self, data, actor_user_id, source_name, observation):
        self.created.append((data.copy(), actor_user_id, source_name, observation))
        if self.target:
            errors = 43 if source_name.endswith("2025") else 0
            return SimpleNamespace(
                batch_id=len(self.created), version=len(self.created),
                status="BORRADOR", rows_count=len(data),
                valid_count=len(data) - errors, error_count=errors,
            )
        return SimpleNamespace(
            batch_id=30, version=1, status="BORRADOR", rows_count=len(data)
        )

    def submit(self, batch_id, actor_user_id):
        self.submitted.append((batch_id, actor_user_id))
        created = self.created[batch_id - 1] if self.target else self.created[0]
        if self.target:
            return SimpleNamespace(
                batch_id=batch_id, version=batch_id,
                status="PENDIENTE_VERIFICACION", rows_count=len(created[0]),
                valid_count=len(created[0]), error_count=0,
            )
        return SimpleNamespace(
            batch_id=batch_id, version=1,
            status="PENDIENTE_VERIFICACION", rows_count=len(created[0]),
        )


class MigrationRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.target_workflows = Workflow(target=True)
        self.cut_period_workflows = Workflow()


class BootstrapRepository:
    def __init__(self):
        self.calls = []

    def bootstrap_initial_administrator(
        self, username, first_names, last_names, reason
    ):
        self.calls.append((username, first_names, last_names, reason))
        return 42


class CommandsTest(unittest.TestCase):
    def test_prepare_legacy_targets_corrects_historical_header_and_filters_year(self):
        data = pd.DataFrame({
            "perido": ["2025-12", "2026-01", "invalido"],
            "ejecutivo": ["E1", "E1", "E2"],
        })

        result = _prepare_legacy_targets(data, 2025)

        self.assertEqual(["2025-12"], result["periodo"].tolist())
        self.assertNotIn("perido", result.columns)

    def test_legacy_migration_preserves_errors_and_submits_only_valid_inputs(self):
        runtime = MigrationRuntime()
        frames = {
            "pisos_1.xlsx": pd.DataFrame({"perido": ["2025-01", "2026-01"]}),
            "pisos.xlsx": pd.DataFrame({"periodo": ["2025-01", "2026-01"]}),
            "cortes.xlsx": pd.DataFrame({"periodo_corte": ["2026-01"]}),
        }
        output = StringIO()

        with patch(
            "src.commands.load_dataframe_from_path",
            side_effect=lambda path: frames[Path(path).name],
        ):
            with redirect_stdout(output):
                code = main([
                    "migrate-legacy-inputs",
                    "--actor-id", "1",
                    "--targets-2025", "pisos_1.xlsx",
                    "--targets-2026", "pisos.xlsx",
                    "--cuts", "cortes.xlsx",
                    "--reason", "Migracion inicial controlada",
                ], runtime)

        payload = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("COMPLETADO_CON_ADVERTENCIAS", payload["status"])
        self.assertEqual("BORRADOR", payload["targets"][0]["status"])
        self.assertEqual(43, payload["targets"][0]["error_count"])
        self.assertEqual("PENDIENTE_VERIFICACION", payload["targets"][1]["status"])
        self.assertEqual([(2, 1)], runtime.target_workflows.submitted)
        self.assertEqual([(30, 1)], runtime.cut_period_workflows.submitted)

    def test_backfill_splits_interval_into_bounded_quarters(self):
        self.assertEqual(
            [
                ("2026-01-01", "2026-04-01"),
                ("2026-04-01", "2026-07-01"),
                ("2026-07-01", "2026-08-01"),
            ],
            _month_windows("2026-01-01", "2026-08-01", 3),
        )

    def test_backfill_requires_month_boundaries_and_bounded_chunk(self):
        with self.assertRaisesRegex(ValueError, "primer dia"):
            _month_windows("2026-01-02", "2026-02-01", 1)
        with self.assertRaisesRegex(ValueError, "entre 1 y 12"):
            _month_windows("2026-01-01", "2026-02-01", 13)

    def test_backfill_executes_incremental_windows_without_calculation(self):
        runtime = Runtime()
        output = StringIO()

        with redirect_stdout(output):
            code = main([
                "sync-backfill",
                "--actor-id", "7",
                "--since", "2026-01-01",
                "--until", "2026-05-01",
                "--chunk-months", "3",
                "--run-prefix", "history",
            ], runtime)

        payload = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("COMPLETADO", payload["status"])
        self.assertEqual([
            (7, "INCREMENTAL", "history-20260101-20260401", "2026-01-01", "2026-04-01"),
            (7, "INCREMENTAL", "history-20260401-20260501", "2026-04-01", "2026-05-01"),
        ], runtime.sync_service.calls)
        self.assertEqual([], runtime.calculation_service.calls)

    def test_bootstrap_admin_uses_one_time_repository_operation(self):
        runtime = Runtime()
        output = StringIO()

        with redirect_stdout(output):
            code = main([
                "bootstrap-admin",
                "--username", "Sistemas",
                "--first-names", "Administrador",
                "--last-names", "Sistemas",
                "--reason", "Alta inicial controlada",
            ], runtime)

        payload = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual(42, payload["user_id"])
        self.assertEqual("sistemas", payload["domain_username"])
        self.assertEqual([
            ("Sistemas", "Administrador", "Sistemas", "Alta inicial controlada")
        ], runtime.access_repository.calls)

    def test_sync_command_uses_authorized_service_and_prints_metadata(self):
        runtime = Runtime()
        output = StringIO()
        with redirect_stdout(output):
            code = main(
                ["sync", "--actor-id", "7", "--type", "FULL",
                 "--run-code", "sync-requested"],
                runtime,
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual(
            [(7, "FULL", "sync-requested", None, None)],
            runtime.sync_service.calls,
        )
        self.assertEqual("sync-1", payload["run_code"])
        self.assertEqual(2, payload["metrics"]["created"])

    def test_calculate_command_uses_only_approved_inputs(self):
        runtime = Runtime()
        output = StringIO()
        with redirect_stdout(output):
            code = main(
                ["calculate", "--actor-id", "9", "--period", "2026-09",
                 "--run-code", "calc-requested"],
                runtime,
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual(
            [("9", "2026-09", "calc-requested")],
            runtime.calculation_service.calls,
        )
        self.assertEqual("calc-1", payload["run_code"])
        self.assertEqual(5, payload["commission_rows"])
        self.assertEqual(2, payload["warning_count"])

    def test_actor_and_period_are_required(self):
        with self.assertRaises(SystemExit):
            main(["calculate", "--period", "2026-09"], Runtime())

    def test_regression_check_does_not_build_application_runtime(self):
        result = {
            "status": "MATCH",
            "expected_cut": "2026-10-08T09:11:37",
            "actual_cut": "2026-10-08T09:11:37",
            "differences": [],
        }
        output = StringIO()
        with patch("src.commands.check_bundle_against_snapshot", return_value=result):
            with patch("src.commands.build_runtime") as build_runtime:
                with redirect_stdout(output):
                    code = main(
                        [
                            "regression-check",
                            "--bundle",
                            "app_data",
                            "--baseline",
                            "baseline.json",
                        ]
                    )

        self.assertEqual(0, code)
        build_runtime.assert_not_called()
        self.assertEqual("MATCH", json.loads(output.getvalue())["status"])

    def test_regression_check_returns_nonzero_for_different_cut(self):
        result = {
            "status": "CUT_MISMATCH",
            "expected_cut": "2026-10-08T09:11:37",
            "actual_cut": "2026-10-09T10:39:05",
            "differences": [],
        }
        output = StringIO()
        with patch("src.commands.check_bundle_against_snapshot", return_value=result):
            with redirect_stdout(output):
                code = main(
                    [
                        "regression-check",
                        "--bundle",
                        "app_data",
                        "--baseline",
                        "baseline.json",
                    ]
                )

        self.assertEqual(2, code)
        self.assertEqual("CUT_MISMATCH", json.loads(output.getvalue())["status"])

    def test_production_check_does_not_build_runtime_or_expose_secrets(self):
        environment = {
            "COMMISSIONS_ENV": "production",
            "COMMISSIONS_DB_BACKEND": "postgresql",
            "COMMISSIONS_POSTGRES_DSN": "postgresql://secret-user:secret-pass@db/app",
            "COMMISSIONS_AUTH_MODE": "ldap",
            "LOCAL_TEST_AUTH_ENABLED": "false",
            "COMMISSION_APP_DEMO": "false",
            "STREAMLIT_SERVER_ADDRESS": "0.0.0.0",
            "STREAMLIT_SERVER_PORT": "8501",
            "LDAP_SERVER": "ldap.internal",
            "LDAP_DOMAIN": "SERVIMETERS",
            "LDAP_BASE_DN": "DC=internal,DC=test",
            "NETSUITE_PROVIDER": "api",
            "NETSUITE_ACCOUNT_ID": "5845631",
            "NETSUITE_CONSUMER_KEY": "secret-consumer-key",
            "NETSUITE_CONSUMER_SECRET": "secret-consumer-value",
            "NETSUITE_TOKEN_ID": "secret-token-id",
            "NETSUITE_TOKEN_SECRET": "secret-token-value",
        }
        output = StringIO()
        with patch.dict("os.environ", environment, clear=True):
            with patch("src.commands.build_runtime") as build_runtime:
                with redirect_stdout(output):
                    code = main(["production-check"])

        payload = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("READY", payload["status"])
        self.assertEqual("api", payload["netsuite_provider"])
        self.assertEqual("disabled", payload["smtp"])
        self.assertEqual("0.0.0.0:8501", payload["web_binding"])
        self.assertNotIn("secret", output.getvalue())
        build_runtime.assert_not_called()

    def test_production_check_rejects_demo_mode(self):
        environment = {
            "COMMISSIONS_ENV": "production",
            "COMMISSIONS_DB_BACKEND": "postgresql",
            "COMMISSIONS_POSTGRES_DSN": "configured",
            "COMMISSIONS_AUTH_MODE": "ldap",
            "LOCAL_TEST_AUTH_ENABLED": "false",
            "COMMISSION_APP_DEMO": "true",
            "LDAP_SERVER": "ldap.internal",
            "LDAP_DOMAIN": "SERVIMETERS",
            "LDAP_BASE_DN": "DC=internal,DC=test",
            "NETSUITE_ODBC_USER": "user",
            "NETSUITE_ODBC_PASSWORD": "password",
        }
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEMO"):
                main(["production-check"])

    def test_production_check_rejects_invalid_web_port(self):
        environment = {
            "COMMISSIONS_ENV": "production",
            "COMMISSIONS_DB_BACKEND": "postgresql",
            "COMMISSIONS_POSTGRES_DSN": "configured",
            "COMMISSIONS_AUTH_MODE": "ldap",
            "LOCAL_TEST_AUTH_ENABLED": "false",
            "COMMISSION_APP_DEMO": "false",
            "STREAMLIT_SERVER_PORT": "70000",
        }
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(ValueError, "entre 1 y 65535"):
                main(["production-check"])

    def test_netsuite_check_does_not_build_runtime(self):
        result = {
            "operation": "netsuite-check",
            "status": "READY",
            "mode": "catalog",
            "authentication_rows": 1,
            "catalog": [],
        }
        output = StringIO()
        with patch("src.commands.NetSuiteApiProvider") as provider_class:
            provider_class.return_value.check_access.return_value = result
            with patch("src.commands.build_runtime") as build_runtime:
                with redirect_stdout(output):
                    code = main(["netsuite-check", "--catalog"])

        self.assertEqual(0, code)
        build_runtime.assert_not_called()
        provider_class.return_value.check_access.assert_called_once_with(
            validate_catalog=True
        )
        self.assertEqual("READY", json.loads(output.getvalue())["status"])

    def test_netsuite_check_classifies_permission_error_without_traceback(self):
        output = StringIO()
        error = NetSuiteCatalogCheckError(
            "invoice_payments",
            NetSuiteApiHttpError(403, "token=private denied"),
        )
        with patch("src.commands.NetSuiteApiProvider") as provider_class:
            provider_class.return_value.check_access.side_effect = error
            with redirect_stdout(output):
                code = main(["netsuite-check", "--catalog"])

        payload = json.loads(output.getvalue())
        self.assertEqual(2, code)
        self.assertEqual("ERROR", payload["status"])
        self.assertEqual("PERMISSION_DENIED", payload["error_code"])
        self.assertEqual("invoice_payments", payload["dataset"])
        self.assertEqual(403, payload["http_status"])
        self.assertNotIn("private", output.getvalue())

    def test_netsuite_check_classifies_authentication_and_configuration_errors(self):
        cases = (
            (NetSuiteApiHttpError(401, "invalid login"), "AUTHENTICATION_FAILED"),
            (RuntimeError("Faltan variables de NetSuite API"), "CONFIGURATION_OR_CONNECTION_ERROR"),
        )
        for error, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                output = StringIO()
                with patch("src.commands.NetSuiteApiProvider", side_effect=error):
                    with redirect_stdout(output):
                        code = main(["netsuite-check"])
                payload = json.loads(output.getvalue())
                self.assertEqual(2, code)
                self.assertEqual(expected_code, payload["error_code"])


if __name__ == "__main__":
    unittest.main()
