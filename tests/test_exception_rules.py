import unittest

import pandas as pd

from src.exception_rules import (
    COMMISSION_VALUE_EXECUTIVE,
    FLOOR_FORCE_COMPLY,
    FLOOR_FORCE_INCLUDE,
    PAYMENT_FORCE_PAY,
    apply_floor_compliance_exceptions,
    apply_floor_inclusion_exceptions,
    apply_payment_exceptions,
)
from src.rule_parameters import RuleParameters


def exception(action, *, document=None, result="APLICAR"):
    return {
        "rule_code": action,
        "executive_code": "EJ1",
        "period": "2026-01",
        "document": document,
        "exceptional_result": result,
        "reason": "Aprobación parametrizador",
    }


class ExceptionRulesTest(unittest.TestCase):
    def test_document_can_be_forced_to_sum_before_floor_calculation(self):
        sales = pd.DataFrame(
            {
                "tranId": ["SM1"], "entityid": ["EJ1"],
                "periodo_mes": [pd.Period("2026-01", freq="M")],
                "estado_piso": ["no suma"], "TotalParaPiso_base": [1000],
                "TotalParaPiso_final": [0], "observacion_piso": ["Anulada"],
            }
        )

        actual = apply_floor_inclusion_exceptions(
            sales, pd.DataFrame([exception(FLOOR_FORCE_INCLUDE, document="SM1")])
        ).iloc[0]

        self.assertEqual("suma", actual["estado_piso"])
        self.assertEqual(1000, actual["TotalParaPiso_final"])
        self.assertIn("Excepción aprobada", actual["observacion_piso"])

    def test_floor_exception_marks_all_period_lines_as_compliant(self):
        compliance = pd.DataFrame(
            {
                "entityid": ["EJ1", "EJ1"],
                "periodo_mes": [pd.Period("2026-01", freq="M")] * 2,
                "linea_comision": ["RETIE/LAP", "RITEL"],
                "cumple": [False, False],
                "observacion_cumple": ["No cumple", "No cumple"],
                "requiere_revision_cumplimiento": [False, True],
                "motivo_revision_cumplimiento": ["", "Sin piso"],
            }
        )

        actual = apply_floor_compliance_exceptions(
            compliance, pd.DataFrame([exception(FLOOR_FORCE_COMPLY)])
        )

        self.assertTrue(actual["cumple"].all())
        self.assertFalse(actual["requiere_revision_cumplimiento"].any())

    def test_payment_and_local_commission_value_are_explicit_overrides(self):
        detail = pd.DataFrame(
            {
                "tranId": ["SM1"], "entityid": ["EJ1"],
                "periodo_mes": [pd.Period("2026-01", freq="M")],
                "decision_pago": ["NO PAGA"], "motivo_pago": ["Fuera de plazo"],
                "nota_pago": ["Fuera de plazo"], "requiere_revision_pago": [False],
                "cc": ["NORMAL"], "Valor": [1000],
                "comision_ejecutivo": [0.04], "comision_jefe": [0.01],
                "comision_gerente": [0.02],
            }
        )
        exceptions = pd.DataFrame(
            [
                exception(PAYMENT_FORCE_PAY, document="SM1"),
                exception(COMMISSION_VALUE_EXECUTIVE, document="SM1", result="123.45"),
            ]
        )

        actual = apply_payment_exceptions(
            detail, exceptions, RuleParameters()
        ).iloc[0]

        self.assertEqual("PAGA 100%", actual["decision_pago"])
        self.assertEqual(123.45, actual["valor_comision_ejecutivo"])
        self.assertEqual(10, actual["valor_comision_jefe"])
        self.assertEqual(20, actual["valor_comision_gerente"])


if __name__ == "__main__":
    unittest.main()
