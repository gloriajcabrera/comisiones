from __future__ import annotations

import unittest

import pandas as pd

from src.commission_rules import calculate_commission_values, calculate_payment_decision


def payment_row(**overrides):
    row = {
        "estado_piso": "suma",
        "cumple": True,
        "saldo_cero_pago": True,
        "Saldo": 0,
        "reemplazo": False,
        "requiere_revision_cumplimiento": False,
        "ciclo_reemplazo": False,
        "linea_comision_key": "RETIE/LAP",
        "sin_resultado_cumplimiento": False,
        "dueDate_base_pago": pd.Timestamp("2026-01-01"),
        "Fecha Real Pago": pd.Timestamp("2026-01-20"),
        "Fecha Contabilizado Pago": pd.Timestamp("2026-01-21"),
        "Periodo Corte": "2026-01",
        "es_documento_comision": True,
        "reemplazo_trazable_pago": False,
        "factor": 1.0,
        "cadena_rota_mismo_dia": False,
        "es_producto_mixto": False,
        "tiene_nota_credito_pago": False,
        "saldo_superior_pago": False,
        "revision_pago_fuente": False,
        "documentos_ciclo_reemplazo": pd.NA,
        "motivo_revision_cumplimiento": "",
        "observacion_cumple": "Cumple meta",
        "observacion_piso": "Suma para piso",
        "nota_pago": "Pago <= 30 días desde dueDate",
        "cc": "NORMAL",
        "Valor": 1000,
        "comision_ejecutivo": None,
        "comision_jefe": None,
        "comision_gerente": None,
    }
    row.update(overrides)
    return row


class CommissionRulesTest(unittest.TestCase):
    def test_standard_payment_is_full(self) -> None:
        actual = calculate_payment_decision(pd.DataFrame([payment_row()])).iloc[0]
        self.assertEqual("PAGA 100%", actual["decision_pago"])

    def test_review_has_priority_over_valid_payment_factor(self) -> None:
        actual = calculate_payment_decision(
            pd.DataFrame([payment_row(requiere_revision_cumplimiento=True)])
        ).iloc[0]
        self.assertEqual("PARA REVISAR", actual["decision_pago"])

    def test_same_day_cancelled_origin_never_pays(self) -> None:
        actual = calculate_payment_decision(
            pd.DataFrame(
                [payment_row(cadena_rota_mismo_dia=True, requiere_revision_cumplimiento=True)]
            )
        ).iloc[0]
        self.assertEqual("NO PAGA", actual["decision_pago"])

    def test_traceable_replacement_can_pay_even_when_it_does_not_sum(self) -> None:
        actual = calculate_payment_decision(
            pd.DataFrame(
                [
                    payment_row(
                        estado_piso="no suma",
                        reemplazo=True,
                        reemplazo_trazable_pago=True,
                    )
                ]
            )
        ).iloc[0]
        self.assertEqual("PAGA 100%", actual["decision_pago"])

    def test_pending_payment_and_zero_payment_have_distinct_concepts(self) -> None:
        pending = payment_row(
            saldo_cero_pago=False,
            Saldo=100,
            **{"Fecha Real Pago": pd.NaT, "Fecha Contabilizado Pago": pd.NaT},
        )
        zero = payment_row(factor=0.0)
        actual = calculate_payment_decision(pd.DataFrame([pending, zero]))
        self.assertEqual("NO PAGA", actual.iloc[0]["decision_pago"])
        self.assertEqual("Pendiente recibir pago de la factura", actual.iloc[0]["nota_pago"])
        self.assertEqual("PAGO EN CERO", actual.iloc[1]["decision_pago"])

    def test_partial_payment_does_not_generate_commission_before_full_payment(self) -> None:
        partial = payment_row(
            saldo_cero_pago=False,
            Saldo=60,
            **{
                "Fecha Real Pago": pd.Timestamp("2026-01-18"),
                "Fecha Contabilizado Pago": pd.Timestamp("2026-01-20"),
            },
        )

        decision = calculate_payment_decision(pd.DataFrame([partial]))
        actual = calculate_commission_values(decision).iloc[0]

        self.assertEqual("NO PAGA", actual["decision_pago"])
        self.assertEqual(
            "Pendiente recibir pago total de la factura",
            actual["nota_pago"],
        )
        self.assertEqual(0, actual["factor_pago_comision"])
        self.assertEqual(0, actual["valor_comision_ejecutivo"])
        self.assertEqual(0, actual["valor_comision_jefe"])
        self.assertEqual(0, actual["valor_comision_gerente"])

    def test_na_always_has_zero_commission_decision(self) -> None:
        actual = calculate_payment_decision(
            pd.DataFrame(
                [payment_row(linea_comision_key="NA", tiene_nota_credito_pago=True)]
            )
        ).iloc[0]
        self.assertEqual("PAGO EN CERO", actual["decision_pago"])
        self.assertFalse(bool(actual["requiere_revision_pago"]))

    def test_ineligible_line_remains_visible_without_entering_review_queue(self) -> None:
        decision = calculate_payment_decision(pd.DataFrame([
            payment_row(
                elegible_comision=False,
                motivo_elegibilidad="No suma: línea de servicio 18 excluida",
                requiere_revision_cumplimiento=True,
            )
        ]))
        actual = calculate_commission_values(decision).iloc[0]

        self.assertEqual("NO APLICA", actual["decision_pago"])
        self.assertEqual(
            "No suma: línea de servicio 18 excluida", actual["motivo_pago"]
        )
        self.assertFalse(bool(actual["requiere_revision_pago"]))
        self.assertEqual(0, actual["factor_pago_comision"])
        self.assertEqual(0, actual["valor_comision_ejecutivo"])

    def test_default_rates_and_subcontract_factor_apply_to_all_roles(self) -> None:
        decisions = calculate_payment_decision(
            pd.DataFrame([payment_row(cc="SERVICIO SUBCONTRATADO")])
        )
        actual = calculate_commission_values(decisions).iloc[0]
        self.assertEqual(0.5, actual["factor_comision_final"])
        self.assertEqual(20, actual["valor_comision_ejecutivo"])
        self.assertEqual(5, actual["valor_comision_jefe"])
        self.assertEqual(10, actual["valor_comision_gerente"])
        self.assertEqual(2, actual["porcentaje_comision_ejecutivo"])


if __name__ == "__main__":
    unittest.main()
