from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

import pandas as pd

from src.access_control import (
    AccessDeniedError,
    AuthorizationService,
    ROLE_EXECUTIVE,
    ROLE_PARAMETERIZER,
    SqliteAccessRepository,
)
from src.commission_rules import calculate_commission_values, calculate_payment_decision
from src.rule_parameters import (
    RuleParameterService,
    RuleParameters,
    SqliteRuleParameterRepository,
)
from tests.test_commission_rules import payment_row


class RuleParametersTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.access_repository = SqliteAccessRepository(root / "access.db")
        self.authorization = AuthorizationService(self.access_repository)
        self.repository = SqliteRuleParameterRepository(root / "parameters.db")
        self.service = RuleParameterService(self.repository, self.authorization)
        self.parameterizer = self._user("parameterizer", ROLE_PARAMETERIZER)
        self.executive = self._user("executive", ROLE_EXECUTIVE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _user(self, username, role):
        user_id = self.access_repository.create_user(username, username, "Test")
        self.access_repository.assign_role(user_id, role)
        return user_id

    def _approve(self, code, value, valid_from, valid_to=None):
        version_id = self.service.propose(
            self.parameterizer, code, value, valid_from, valid_to
        )
        self.service.submit(self.parameterizer, version_id)
        self.service.decide(self.parameterizer, version_id, True, "Aprobado en prueba")
        return version_id

    def test_approved_version_only_applies_inside_its_validity(self):
        self._approve("payment_full_days", "45", "2026-07-01", "2026-12-31")

        self.assertEqual(30, self.repository.load_parameters("2026-06-30").payment_full_days)
        self.assertEqual(45, self.repository.load_parameters("2026-07-01").payment_full_days)
        self.assertEqual(30, self.repository.load_parameters("2027-01-01").payment_full_days)

    def test_overlapping_approved_versions_are_rejected(self):
        self._approve("payment_half_days", 65, "2026-01-01", "2026-12-31")
        second = self.service.propose(
            self.parameterizer, "payment_half_days", 70, "2026-06-01"
        )
        self.service.submit(self.parameterizer, second)

        with self.assertRaisesRegex(ValueError, "superpuesta"):
            self.service.decide(self.parameterizer, second, True, "No debe aprobar")

    def test_user_without_permission_cannot_propose_or_toggle(self):
        with self.assertRaises(AccessDeniedError):
            self.service.propose(
                self.executive, "payment_full_days", 45, "2026-01-01"
            )
        with self.assertRaises(AccessDeniedError):
            self.service.set_enabled(
                self.executive, "payment_full_days", False, "Sin permiso"
            )

    def test_disabled_override_falls_back_to_standard_value(self):
        self._approve("payment_full_days", 45, "2026-01-01")
        self.service.set_enabled(
            self.parameterizer, "payment_full_days", False,
            "Deshabilitar temporalmente",
        )

        self.assertEqual(30, self.repository.load_parameters("2026-06-01").payment_full_days)

    def test_enabled_change_requires_reason_and_preserves_history(self):
        with self.assertRaisesRegex(ValueError, "motivo es obligatorio"):
            self.service.set_enabled(
                self.parameterizer, "payment_full_days", False, ""
            )

        self.service.set_enabled(
            self.parameterizer, "payment_full_days", False,
            "Suspension aprobada",
        )
        self.service.set_enabled(
            self.parameterizer, "payment_full_days", False,
            "No debe duplicar historia",
        )

        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            rows = connection.execute(
                "SELECT parameter_code, previous_enabled, new_enabled, "
                "changed_by, reason FROM rule_definition_enabled_history"
            ).fetchall()
        self.assertEqual(
            [("payment_full_days", 1, 0, self.parameterizer, "Suspension aprobada")],
            rows,
        )
        history = self.service.list_enabled_history(
            self.parameterizer, "payment_full_days"
        )
        self.assertEqual(1, len(history))
        self.assertTrue(history[0]["previous_enabled"])
        self.assertFalse(history[0]["new_enabled"])
        self.assertEqual("Suspension aprobada", history[0]["reason"])

    def test_custom_factors_and_rates_affect_the_financial_result(self):
        parameters = RuleParameters(
            payment_factor_full=0.8,
            default_commission_executive=0.05,
        )
        row = payment_row(factor=0.8)
        decisions = calculate_payment_decision(pd.DataFrame([row]), parameters)
        actual = calculate_commission_values(decisions, parameters).iloc[0]

        self.assertEqual("PAGA 100%", actual["decision_pago"])
        self.assertEqual(0.8, actual["factor_pago_comision"])
        self.assertEqual(40, actual["valor_comision_ejecutivo"])

    def test_lists_definitions_and_version_history(self):
        version_id = self.service.propose(
            self.parameterizer, "payment_full_days", 45, "2026-07-01",
            observation="Cambio controlado",
        )
        self.service.submit(self.parameterizer, version_id)

        definitions = self.service.list_definitions(self.parameterizer)
        versions = self.service.list_versions(self.parameterizer, "PENDIENTE_APROBACION")

        self.assertIn("payment_full_days", {item["code"] for item in definitions})
        self.assertEqual(1, len(versions))
        self.assertEqual(version_id, versions[0]["version_id"])
        self.assertEqual(45, versions[0]["value"])
        self.assertEqual("Cambio controlado", versions[0]["observation"])

    def test_user_without_permission_cannot_list_parameter_configuration(self):
        with self.assertRaises(AccessDeniedError):
            self.service.list_definitions(self.executive)
        with self.assertRaises(AccessDeniedError):
            self.service.list_versions(self.executive)
        with self.assertRaises(AccessDeniedError):
            self.service.list_enabled_history(self.executive)


if __name__ == "__main__":
    unittest.main()
