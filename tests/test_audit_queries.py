from pathlib import Path
import tempfile
import unittest

from src.access_control import (
    AccessDeniedError,
    ROLE_ADMIN,
    ROLE_PARAMETERIZER,
    ROLE_REVIEWER,
)
from src.database import DatabaseSettings, SQLITE
from src.runtime import DEVELOPMENT, RuntimeSettings, build_runtime


class Provider:
    def load_dataset(self):
        raise AssertionError("La consulta de auditoria no debe consultar NetSuite")


class AuditQueriesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "audit.db"
        self.runtime = build_runtime(
            RuntimeSettings(
                DEVELOPMENT,
                DatabaseSettings(backend=SQLITE, sqlite_path=path),
            ),
            Provider(),
        )
        self.admin = self.runtime.access_repository.create_user(
            "admin.audit", "Admin", "Audit"
        )
        self.runtime.access_repository.assign_role(self.admin, ROLE_ADMIN)
        self.reviewer = self.runtime.access_repository.create_user(
            "reviewer.audit", "Reviewer", "Audit"
        )
        self.runtime.access_repository.assign_role(self.reviewer, ROLE_REVIEWER)
        self.parameterizer = self.runtime.access_repository.create_user(
            "parameterizer.audit", "Parameterizer", "Audit"
        )
        self.runtime.access_repository.assign_role(
            self.parameterizer, ROLE_PARAMETERIZER
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_admin_reads_unified_history_with_filters_before_pagination(self):
        self.runtime.business_periods.create("2026-08", self.admin)
        self.runtime.business_periods.transition(
            "2026-08", "ABIERTO", "EN_REVISION", self.admin, "Revision mensual"
        )

        first = self.runtime.audit_query_service.query(
            self.admin,
            entity_types=["BUSINESS_PERIOD"],
            page=1,
            page_size=1,
            sort_by="occurred_at",
            descending=False,
        )
        second = self.runtime.audit_query_service.query(
            self.admin,
            entity_types=["BUSINESS_PERIOD"],
            page=2,
            page_size=1,
            sort_by="occurred_at",
            descending=False,
        )

        self.assertEqual(2, first.total)
        self.assertEqual(2, first.pages)
        self.assertEqual("ABIERTO", first.items[0].new_value)
        self.assertEqual("EN_REVISION", second.items[0].new_value)
        self.assertEqual("Revision mensual", second.items[0].reason)

    def test_search_uses_full_event_set(self):
        self.runtime.business_periods.create("2026-09", self.admin)

        result = self.runtime.audit_query_service.query(
            self.admin, search="2026-09", page_size=10
        )

        self.assertTrue(result.items)
        self.assertTrue(all(item.entity_id == "2026-09" for item in result.items))

    def test_date_to_includes_events_from_the_selected_day(self):
        self.runtime.business_periods.create("2026-11", self.admin)
        event = self.runtime.audit_query_service.query(
            self.admin,
            entity_types=["BUSINESS_PERIOD"],
            search="2026-11",
            page_size=10,
        ).items[0]
        selected_day = event.occurred_at[:10]

        result = self.runtime.audit_query_service.query(
            self.admin,
            entity_types=["BUSINESS_PERIOD"],
            search="2026-11",
            date_to=selected_day,
            page_size=10,
        )

        self.assertEqual(1, result.total)
        self.assertEqual("2026-11", result.items[0].entity_id)

    def test_parameter_enabled_change_is_visible_in_central_audit(self):
        self.runtime.rule_parameter_workflows.set_enabled(
            self.admin,
            "payment_full_days",
            False,
            "Suspension temporal aprobada",
        )

        result = self.runtime.audit_query_service.query(
            self.admin,
            actions=["PARAMETER_ENABLED_CHANGED"],
            entity_types=["RULE_PARAMETER"],
            page_size=10,
        )

        self.assertEqual(1, result.total)
        event = result.items[0]
        self.assertEqual("payment_full_days", event.entity_id)
        self.assertEqual("1", event.previous_value)
        self.assertEqual("0", event.new_value)
        self.assertEqual("Suspension temporal aprobada", event.reason)

    def test_facets_are_complete_and_permission_protected(self):
        self.runtime.business_periods.create("2026-10", self.admin)

        facets = self.runtime.audit_query_service.facets(self.admin)

        self.assertIn("PERIOD_STATUS_CHANGED", facets["actions"])
        self.assertIn("BUSINESS_PERIOD", facets["entity_types"])
        self.assertIn(str(self.admin), facets["actors"])
        with self.assertRaises(AccessDeniedError):
            self.runtime.audit_query_service.facets(self.reviewer)

    def test_non_admin_cannot_query_audit_backend(self):
        with self.assertRaises(AccessDeniedError):
            self.runtime.audit_query_service.query(self.reviewer)

    def test_calculation_runs_include_metrics_and_filter_before_pagination(self):
        repository = self.runtime.calculations
        repository.start_run("calc-aug", "key-aug", "2026-08", "rules-1", "10")
        repository.finish_run(
            "calc-aug", "COMPLETADO", metrics={
                "commission_rows": 8, "floor_rows": 3, "payment_rows": 6,
                "discount_rows": 1, "warning_count": 0, "error_count": 0,
            }
        )
        repository.start_run("calc-sep", "key-sep", "2026-09", "rules-2", "11")
        repository.finish_run(
            "calc-sep", "ERROR", "Fallo controlado", {"error_count": 1}
        )

        result = self.runtime.audit_query_service.calculation_runs(
            self.admin, periods=["2026-09"], statuses=["ERROR"], page_size=1
        )

        self.assertEqual(1, result.total)
        self.assertEqual("calc-sep", result.items[0].run_code)
        self.assertEqual(1, result.items[0].error_count)
        self.assertEqual("Fallo controlado", result.items[0].message)
        parameterizer_result = self.runtime.audit_query_service.calculation_runs(
            self.parameterizer, page_size=10
        )
        self.assertEqual(2, parameterizer_result.total)
        with self.assertRaises(AccessDeniedError):
            self.runtime.audit_query_service.calculation_runs(self.reviewer)

    def test_unsafe_sort_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Ordenamiento no permitido"):
            self.runtime.audit_query_service.query(
                self.admin, sort_by="DROP TABLE audit_events"
            )


if __name__ == "__main__":
    unittest.main()
