from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.calculation_service import (
    CALC_COMPLETED,
    CALC_COMPLETED_WARNINGS,
    CALC_ERROR,
    CancellationRecalculationContext,
    CommissionCalculationService,
    SqliteCalculationRepository,
    WORKFLOW_APPROVED,
    WORKFLOW_PENDING_APPROVAL,
    WORKFLOW_PENDING_REVIEW,
)
from src.business_periods import SqliteBusinessPeriodRepository
from src.cancellation_discounts import SqliteCancellationDiscountRepository
from src.netsuite_provider import NetSuiteDataset
from src.rule_parameters import RuleParameters
from src.review_workflows import (
    EXCEPTION_APPROVED,
    EXCEPTION_PENDING,
    EXCEPTION_PAYMENT,
    SqliteReviewRepository,
)
from src.exception_rules import COMMISSION_VALUE_EXECUTIVE
from tests.test_engine import transaction


class Provider:
    def __init__(self, dataset):
        self.dataset = dataset

    def load_dataset(self):
        return self.dataset


class FrameProvider:
    def __init__(self, method_name, frame):
        setattr(self, method_name, lambda: frame)


def inputs():
    invoices = pd.DataFrame([transaction("SM1", 1, "CustInvc", 1000)])
    dataset = NetSuiteDataset(
        invoices=invoices,
        costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
        proformas=pd.DataFrame(columns=invoices.columns),
        credit_notes=pd.DataFrame(columns=invoices.columns),
        proforma_cancellations=pd.DataFrame(),
        invoice_payments=pd.DataFrame(
            {
                "factura_id": [1], "factura": ["SM1"],
                "fecha_factura": ["2026-01-01"], "pago_id": [10],
                "documento_pago": ["RC-1"], "tipo_pago": ["CustPymt"],
                "fecha_contabilizacion": ["2026-02-02"],
                "fecha_real": ["2026-02-01"], "fecha_pago": ["2026-02-01"],
                "valor_pago": [1000], "memo_pago": ["PAGO"],
            }
        ),
        proforma_payments=pd.DataFrame(
            columns=[
                "proforma_id", "proforma", "fecha_proforma", "deposito_id",
                "deposito", "pago_id", "documento_pago", "tipo_pago",
                "fecha_transaccion", "fecha_real_transaccion", "fecha_pago",
                "valor_pagado_proforma", "memo_pago", "tipo_vinculo",
            ]
        ),
    )
    targets = pd.DataFrame(
        {
            "periodo": ["2026-01-01"], "ejecutivo": ["EJ1"],
            "linea_comision": ["RETIE/LAP"], "meta": [1000], "piso": [800],
        }
    )
    cuts = pd.DataFrame(
        {
            "inicio_corte": ["2026-01-01"], "fin_corte": ["2026-02-28"],
            "periodo_corte": ["2026-02"],
        }
    )
    return dataset, targets, cuts


class CalculationServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "calculation.db"
        self.repository = SqliteCalculationRepository(self.db_path)

    def test_result_is_persisted_and_requires_controlled_approval(self):
        dataset, targets, cuts = inputs()
        repository = SqliteCalculationRepository(self.db_path)
        service = CommissionCalculationService(Provider(dataset), repository)

        execution = service.execute(
            targets, cuts, "1", period="2026-01", run_code="controlled-1"
        )

        self.assertEqual(
            WORKFLOW_PENDING_REVIEW, repository.workflow_status(execution.run_code)
        )
        persisted = repository.load_result(execution.run_code)
        self.assertIsNotNone(persisted)
        self.assertEqual(
            len(execution.result.commission_detail),
            len(persisted.commission_detail),
        )
        with self.assertRaises(LookupError):
            service.load_approved_result("2026-01")

        service.submit_for_approval(1, execution.run_code, "Revisión completada")
        self.assertEqual(
            WORKFLOW_PENDING_APPROVAL,
            repository.workflow_status(execution.run_code),
        )
        service.approve(1, execution.run_code, "Valores confirmados")
        self.assertEqual(
            WORKFLOW_APPROVED, repository.workflow_status(execution.run_code)
        )
        approved = service.load_approved_result("2026-01")
        self.assertEqual(execution.run_code, approved.run_code)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_schema_indexes_calculation_history_filters(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            indexes = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='calculation_runs'"
                )
            }

        self.assertIn("idx_calculation_runs_period_status_started", indexes)
        self.assertIn("idx_calculation_runs_status_started", indexes)

    def test_productive_entry_scopes_materialized_result_to_requested_period(self):
        dataset, targets, cuts = inputs()
        service = CommissionCalculationService(
            Provider(dataset),
            self.repository,
            targets_provider=FrameProvider("load_approved_targets", targets),
            cuts_provider=FrameProvider("load_approved_cuts", cuts),
        )

        result = service.execute_approved_inputs(
            "1", period="2026-02", run_code="calc-scoped-period"
        )

        self.assertEqual(0, result.commission_rows)
        self.assertEqual(0, result.floor_rows)
        self.assertEqual(0, result.payment_rows)
        self.assertTrue(result.result.commission_detail.empty)
        self.assertTrue(result.result.floor_compliance.empty)

    def test_require_existing_never_creates_a_new_calculation(self):
        dataset, targets, cuts = inputs()
        service = CommissionCalculationService(
            Provider(dataset), self.repository, rules_version="rules-1"
        )

        with self.assertRaisesRegex(LookupError, "Solicite un recálculo"):
            service.execute(
                targets, cuts, "viewer", period="2026-01",
                materialize_reused_result=True, require_existing=True,
            )
        with closing(sqlite3.connect(self.db_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM calculation_runs"
            ).fetchone()[0]
        self.assertEqual(0, count)

        service.execute(
            targets, cuts, "authorized", period="2026-01", run_code="calc-existing"
        )
        reused = service.execute(
            targets, cuts, "viewer", period="2026-01",
            materialize_reused_result=True, require_existing=True,
        )
        self.assertTrue(reused.reused)
        self.assertIsNotNone(reused.result)

    def test_execution_returns_results_and_persists_only_run_metadata(self):
        dataset, targets, cuts = inputs()
        result = CommissionCalculationService(
            Provider(dataset), self.repository, rules_version="rules-1"
        ).execute(targets, cuts, "tester", period="2026-01", run_code="calc-1")

        self.assertEqual(result.status, CALC_COMPLETED)
        self.assertFalse(result.reused)
        self.assertEqual(result.commission_rows, 1)
        self.assertEqual(
            40.0, result.result.commission_detail.iloc[0]["valor_comision_ejecutivo"]
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            run = connection.execute(
                "SELECT period, rules_version, status, started_at, finished_at "
                "FROM calculation_runs WHERE run_code = 'calc-1'"
            ).fetchone()
            metrics = connection.execute(
                """
                SELECT commission_rows, floor_rows, payment_rows,
                       discount_rows, warning_count, error_count
                FROM calculation_run_metrics WHERE run_code = 'calc-1'
                """
            ).fetchone()
        self.assertNotIn("calculation_lines", tables)
        self.assertNotIn("calculation_floor_results", tables)
        self.assertNotIn("calculation_payment_results", tables)
        self.assertEqual(("2026-01", "rules-1", CALC_COMPLETED), run[:3])
        self.assertTrue(run[3])
        self.assertTrue(run[4])
        self.assertEqual((1, 1, 1, 0, 0, 0), metrics)

    def test_productive_entry_point_loads_only_approved_database_inputs(self):
        dataset, targets, cuts = inputs()
        service = CommissionCalculationService(
            Provider(dataset),
            self.repository,
            targets_provider=FrameProvider("load_approved_targets", targets),
            cuts_provider=FrameProvider("load_approved_cuts", cuts),
        )

        execution = service.execute_approved_inputs(
            "tester", period="2026-01", run_code="calc-approved-inputs"
        )

        self.assertEqual(CALC_COMPLETED, execution.status)
        self.assertEqual(1, execution.commission_rows)

    def test_productive_entry_point_requires_an_explicit_period(self):
        dataset, targets, cuts = inputs()
        service = CommissionCalculationService(
            Provider(dataset),
            self.repository,
            targets_provider=FrameProvider("load_approved_targets", targets),
            cuts_provider=FrameProvider("load_approved_cuts", cuts),
        )

        with self.assertRaisesRegex(ValueError, "periodo es obligatorio"):
            service.execute_approved_inputs("tester")

        with closing(sqlite3.connect(self.db_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM calculation_runs"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_productive_entry_point_rejects_invalid_period_format(self):
        dataset, targets, cuts = inputs()
        service = CommissionCalculationService(
            Provider(dataset),
            self.repository,
            targets_provider=FrameProvider("load_approved_targets", targets),
            cuts_provider=FrameProvider("load_approved_cuts", cuts),
        )

        with self.assertRaisesRegex(ValueError, "YYYY-MM"):
            service.execute_approved_inputs("tester", period="2026-13")

        with closing(sqlite3.connect(self.db_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM calculation_runs"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_same_inputs_and_rules_reuse_completed_run(self):
        dataset, targets, cuts = inputs()
        service = CommissionCalculationService(
            Provider(dataset), self.repository, rules_version="rules-1"
        )
        first = service.execute(targets, cuts, "tester", run_code="calc-1")
        second = service.execute(targets, cuts, "tester", run_code="calc-2")

        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(second.run_code, "calc-1")
        self.assertEqual(first.commission_rows, second.commission_rows)
        self.assertEqual(first.floor_rows, second.floor_rows)
        self.assertEqual(first.payment_rows, second.payment_rows)
        with closing(sqlite3.connect(self.db_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM calculation_runs").fetchone()[0]
        self.assertEqual(count, 1)

    def test_reused_run_can_materialize_views_without_persisting_detail(self):
        dataset, targets, cuts = inputs()
        service = CommissionCalculationService(
            Provider(dataset), self.repository, rules_version="rules-1"
        )
        first = service.execute(targets, cuts, "tester", run_code="calc-1")

        second = service.execute(
            targets,
            cuts,
            "tester",
            run_code="ignored",
            materialize_reused_result=True,
        )

        self.assertTrue(second.reused)
        self.assertEqual(first.run_code, second.run_code)
        self.assertIsNotNone(second.result)
        self.assertEqual(first.commission_rows, len(second.result.commission_detail))
        with closing(sqlite3.connect(self.db_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM calculation_runs").fetchone()[0]
        self.assertEqual(1, count)

    def test_changed_target_creates_new_version(self):
        dataset, targets, cuts = inputs()
        service = CommissionCalculationService(Provider(dataset), self.repository)
        service.execute(targets, cuts, "tester", run_code="calc-1")
        changed = targets.copy()
        changed.loc[0, "piso"] = 1200
        second = service.execute(changed, cuts, "tester", run_code="calc-2")

        self.assertFalse(second.reused)
        self.assertEqual(second.run_code, "calc-2")

    def test_unmatched_payment_completes_with_audited_warning_and_is_reusable(self):
        dataset, targets, cuts = inputs()
        unmatched = dataset.invoice_payments.iloc[[0]].copy()
        unmatched["factura_id"] = 999
        unmatched["factura"] = "SM404"
        unmatched["pago_id"] = 11
        dataset = NetSuiteDataset(
            **{
                **dataset.__dict__,
                "invoice_payments": pd.concat(
                    [dataset.invoice_payments, unmatched], ignore_index=True
                ),
            }
        )
        service = CommissionCalculationService(Provider(dataset), self.repository)

        first = service.execute(
            targets, cuts, "tester", period="2026-01", run_code="calc-warning"
        )
        second = service.execute(
            targets, cuts, "tester", period="2026-01", run_code="ignored"
        )

        self.assertEqual(CALC_COMPLETED_WARNINGS, first.status)
        self.assertEqual(1, first.warning_count)
        self.assertIn("movimientos sin documento base: 1", first.warning_message)
        self.assertTrue(second.reused)
        self.assertEqual(CALC_COMPLETED_WARNINGS, second.status)
        self.assertEqual(1, second.warning_count)
        self.assertEqual(
            "movimientos sin documento base: 1", second.warning_message
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            status, message = connection.execute(
                "SELECT status, message FROM calculation_runs "
                "WHERE run_code='calc-warning'"
            ).fetchone()
        self.assertEqual(CALC_COMPLETED_WARNINGS, status)
        self.assertEqual("movimientos sin documento base: 1", message)

    def test_changed_parameters_create_new_calculation(self):
        dataset, targets, cuts = inputs()
        CommissionCalculationService(
            Provider(dataset), self.repository
        ).execute(targets, cuts, "tester", run_code="calc-1")
        changed = CommissionCalculationService(
            Provider(dataset),
            self.repository,
            parameters=RuleParameters(default_commission_executive=0.05),
        ).execute(targets, cuts, "tester", run_code="calc-2")

        self.assertFalse(changed.reused)
        self.assertEqual(50, changed.result.commission_detail.iloc[0]["valor_comision_ejecutivo"])

    def test_loads_approved_parameters_for_calculated_period(self):
        dataset, targets, cuts = inputs()

        class ParametersByDate:
            effective_on = None

            def load_parameters(self, effective_on):
                self.effective_on = str(effective_on)
                return RuleParameters(default_commission_executive=0.05)

        parameter_provider = ParametersByDate()
        result = CommissionCalculationService(
            Provider(dataset), self.repository,
            parameter_provider=parameter_provider,
        ).execute(
            targets, cuts, "tester", period="2026-07", run_code="calc-parameters"
        )

        self.assertEqual("2026-07-01", parameter_provider.effective_on)
        self.assertEqual(
            50, result.result.commission_detail.iloc[0]["valor_comision_ejecutivo"]
        )

    def test_invalid_inputs_are_audited_without_partial_results(self):
        dataset, targets, cuts = inputs()
        invalid = pd.concat([targets, targets], ignore_index=True)
        service = CommissionCalculationService(Provider(dataset), self.repository)

        with self.assertRaisesRegex(ValueError, "duplicados"):
            service.execute(invalid, cuts, "tester", run_code="calc-error")

        with closing(sqlite3.connect(self.db_path)) as connection:
            run = connection.execute(
                "SELECT status, message FROM calculation_runs WHERE run_code='calc-error'"
            ).fetchone()
            metrics = connection.execute(
                "SELECT commission_rows, warning_count, error_count "
                "FROM calculation_run_metrics WHERE run_code='calc-error'"
            ).fetchone()
        self.assertEqual(run[0], CALC_ERROR)
        self.assertIn("duplicados", run[1])
        self.assertEqual((0, 0, 1), metrics)

    def test_failed_run_is_preserved_and_same_inputs_can_be_retried(self):
        dataset, targets, cuts = inputs()
        service = CommissionCalculationService(Provider(dataset), self.repository)

        with patch(
            "src.calculation_service.calculate_commissions",
            side_effect=RuntimeError("fallo transitorio"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fallo transitorio"):
                service.execute(
                    targets, cuts, "tester", run_code="calc-first-error"
                )

        retried = service.execute(
            targets, cuts, "tester", run_code="calc-retry-success"
        )

        self.assertEqual(CALC_COMPLETED, retried.status)
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                "SELECT run_code, status, idempotency_key FROM calculation_runs "
                "ORDER BY started_at, run_code"
            ).fetchall()
        self.assertEqual(2, len(rows))
        statuses = {row[0]: row[1] for row in rows}
        self.assertEqual(CALC_ERROR, statuses["calc-first-error"])
        self.assertEqual(CALC_COMPLETED, statuses["calc-retry-success"])
        keys = {row[0]: row[2] for row in rows}
        self.assertIn(":error:calc-first-error", keys["calc-first-error"])
        self.assertNotEqual(keys["calc-first-error"], keys["calc-retry-success"])

    def test_closed_period_blocks_a_new_calculation(self):
        dataset, targets, cuts = inputs()
        periods = SqliteBusinessPeriodRepository(
            Path(self.temp_dir.name) / "periods.db"
        )
        periods.create("2026-01", 1)
        periods.transition("2026-01", "ABIERTO", "EN_REVISION", 1, "Revision")
        periods.transition("2026-01", "EN_REVISION", "FINALIZADO", 1, "Final")
        periods.transition("2026-01", "FINALIZADO", "CERRADO", 1, "Cierre")
        service = CommissionCalculationService(
            Provider(dataset), self.repository, period_repository=periods
        )

        with self.assertRaisesRegex(RuntimeError, "debe reabrirse"):
            service.execute(targets, cuts, "tester", period="2026-01")

        with closing(sqlite3.connect(self.db_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM calculation_runs"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_closed_period_can_reuse_an_identical_completed_calculation(self):
        dataset, targets, cuts = inputs()
        periods = SqliteBusinessPeriodRepository(
            Path(self.temp_dir.name) / "periods.db"
        )
        periods.create("2026-01", 1)
        service = CommissionCalculationService(
            Provider(dataset), self.repository, period_repository=periods
        )
        first = service.execute(
            targets, cuts, "tester", period="2026-01", run_code="calc-before-close"
        )
        periods.transition("2026-01", "ABIERTO", "EN_REVISION", 1, "Revision")
        periods.transition("2026-01", "EN_REVISION", "FINALIZADO", 1, "Final")
        periods.transition("2026-01", "FINALIZADO", "CERRADO", 1, "Cierre")

        reused = service.execute(targets, cuts, "viewer", period="2026-01")

        self.assertTrue(reused.reused)
        self.assertEqual(first.run_code, reused.run_code)

    def test_reopened_period_allows_a_new_calculation(self):
        dataset, targets, cuts = inputs()
        periods = SqliteBusinessPeriodRepository(
            Path(self.temp_dir.name) / "periods.db"
        )
        periods.create("2026-01", 1)
        periods.transition("2026-01", "ABIERTO", "EN_REVISION", 1, "Revision")
        periods.transition("2026-01", "EN_REVISION", "FINALIZADO", 1, "Final")
        periods.transition("2026-01", "FINALIZADO", "CERRADO", 1, "Cierre")
        periods.transition("2026-01", "CERRADO", "ABIERTO", 1, "Correccion")
        service = CommissionCalculationService(
            Provider(dataset), self.repository, period_repository=periods
        )

        execution = service.execute(
            targets, cuts, "tester", period="2026-01", run_code="calc-reopened"
        )

        self.assertFalse(execution.reused)
        self.assertEqual(CALC_COMPLETED, execution.status)

    def test_cancellation_recalculation_persists_debt_and_returns_recalculated_result(self):
        dataset, targets, cuts = inputs()
        credit_note = transaction("NC1", 20, "CustCred", -1000)
        credit_note["tranDate"] = "2026-08-01"
        credit_note["createdfrom"] = 1
        dataset = NetSuiteDataset(
            invoices=dataset.invoices,
            costs=dataset.costs,
            proformas=dataset.proformas,
            credit_notes=pd.DataFrame([credit_note]),
            proforma_cancellations=dataset.proforma_cancellations,
            invoice_payments=dataset.invoice_payments,
            proforma_payments=dataset.proforma_payments,
        )
        previous_compliance = pd.DataFrame(
            {
                "entityid": ["EJ1"], "periodo_mes": ["2026-01"],
                "linea_comision": ["RETIE/LAP"], "cumple": [True],
            }
        )
        paid_commissions = pd.DataFrame(
            {
                "executive": ["EJ1"], "period": ["2026-01"],
                "commission_line": ["RETIE/LAP"], "document": ["SM1"],
                "commission_executive": [40], "commission_manager": [10],
                "commission_director": [20],
            }
        )
        discount_repository = SqliteCancellationDiscountRepository(self.db_path)
        service = CommissionCalculationService(
            Provider(dataset),
            self.repository,
            discount_repository=discount_repository,
        )

        execution = service.execute(
            targets,
            cuts,
            "tester",
            period="2026-01",
            run_code="calc-recalculated",
            cancellation_context=CancellationRecalculationContext(
                previous_compliance=previous_compliance,
                paid_commissions=paid_commissions,
                detection_period="2026-08",
                source_calculation="calc-original",
            ),
        )

        self.assertEqual(1, execution.discount_rows)
        self.assertEqual(70, execution.discounts.iloc[0]["total_debt"])
        self.assertFalse(bool(execution.result.floor_compliance.iloc[0]["cumple"]))
        recalculated_document = execution.result.sales_floor.iloc[0]
        self.assertEqual("no suma", recalculated_document["estado_piso"])
        self.assertEqual(0, recalculated_document["TotalParaPiso_final"])
        with closing(sqlite3.connect(self.db_path)) as connection:
            stored = connection.execute(
                "SELECT total_debt, balance, source_calculation, recalculation "
                "FROM cancellation_discounts"
            ).fetchone()
        self.assertEqual(
            (70, 70, "calc-original", "calc-recalculated"), stored
        )

    def test_cancellation_history_changes_idempotency_key(self):
        dataset, targets, cuts = inputs()
        discount_repository = SqliteCancellationDiscountRepository(self.db_path)
        previous = pd.DataFrame(
            {
                "entityid": ["EJ1"], "periodo_mes": ["2026-01"],
                "linea_comision": ["RETIE/LAP"], "cumple": [True],
            }
        )
        paid = pd.DataFrame(
            {
                "executive": ["EJ1"], "period": ["2026-01"],
                "commission_line": ["RETIE/LAP"], "document": ["SM1"],
                "commission_executive": [40], "commission_manager": [10],
                "commission_director": [20],
            }
        )
        service = CommissionCalculationService(
            Provider(dataset), self.repository,
            discount_repository=discount_repository,
        )
        first = service.execute(
            targets, cuts, "tester", run_code="calc-a",
            cancellation_context=CancellationRecalculationContext(
                previous, paid, "2026-08", "calc-original-a"
            ),
        )
        second = service.execute(
            targets, cuts, "tester", run_code="calc-b",
            cancellation_context=CancellationRecalculationContext(
                previous, paid, "2026-08", "calc-original-b"
            ),
        )

        self.assertNotEqual(first.idempotency_key, second.idempotency_key)
        self.assertFalse(second.reused)

    def test_only_approved_exception_changes_financial_result(self):
        dataset, targets, cuts = inputs()
        reviews = SqliteReviewRepository(self.db_path)
        exception = reviews.create_exception(
            EXCEPTION_PAYMENT,
            "EJ1",
            "2026-01",
            COMMISSION_VALUE_EXECUTIVE,
            "40",
            "125",
            "Ajuste excepcional aprobado",
            1,
            "SM1",
        )
        proposed = CommissionCalculationService(
            Provider(dataset), self.repository, exception_repository=reviews
        ).execute(
            targets, cuts, "tester", period="2026-01", run_code="calc-proposed"
        )
        self.assertEqual(
            40, proposed.result.commission_detail.iloc[0]["valor_comision_ejecutivo"]
        )

        reviews.transition_exception(exception.exception_id, EXCEPTION_PENDING, 1)
        reviews.transition_exception(
            exception.exception_id, EXCEPTION_APPROVED, 2,
            "Ajuste excepcional aprobado",
        )
        approved = CommissionCalculationService(
            Provider(dataset), self.repository, exception_repository=reviews
        ).execute(
            targets, cuts, "tester", period="2026-01", run_code="calc-approved"
        )

        self.assertEqual(
            125, approved.result.commission_detail.iloc[0]["valor_comision_ejecutivo"]
        )
        self.assertNotEqual(proposed.idempotency_key, approved.idempotency_key)


if __name__ == "__main__":
    unittest.main()
