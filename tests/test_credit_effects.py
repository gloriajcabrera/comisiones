from __future__ import annotations

import unittest

import pandas as pd

from src.credit_effects import apply_credit_effects, calculate_floor_line_status


def sale(
    number: str,
    date: str,
    *,
    invoice=True,
    note=None,
    note_date=None,
    replacement=None,
    value=1000,
):
    return {
        "tranId": number,
        "tranDate": date,
        "es_factura": invoice,
        "es_proforma": not invoice,
        "reemplazo": bool(replacement),
        "tipo_reemplazo": "reemplazo_factura_nc" if replacement else "ninguno",
        "documento_origen": replacement,
        "fecha_origen": pd.NaT,
        "documento_reemplazado": replacement,
        "observacion_reemplazo": "",
        "tranId_nc": note,
        "tranDate_nc": note_date,
        "Valor_nc": -abs(value) if note else None,
        "Valor": value,
        "factura_reemplazo_tranId_nc": None,
        "alarma_nc_servicio": False,
        "motivo_alarma_nc": "",
    }


class CreditEffectsTest(unittest.TestCase):
    def test_ineligible_line_remains_visible_and_does_not_sum(self) -> None:
        row = sale("SM-EXCLUDED", "2026-01-10")
        row["elegible_comision"] = False
        row["motivo_elegibilidad"] = "No suma: línea de servicio 18 excluida"
        affected = apply_credit_effects(pd.DataFrame([row]), pd.DataFrame())

        actual = calculate_floor_line_status(affected).iloc[0]

        self.assertEqual("no suma", actual["estado_piso"])
        self.assertEqual(0, actual["TotalParaPiso_final"])
        self.assertEqual(
            "No suma: línea de servicio 18 excluida",
            actual["observacion_piso"],
        )

    def test_other_month_credit_without_replacement_requires_recalculation(self) -> None:
        data = pd.DataFrame(
            [sale("SM1", "2026-01-10", note="NC1", note_date="2026-03-01")]
        )

        actual = apply_credit_effects(data, pd.DataFrame())
        row = actual.iloc[0]

        self.assertEqual("factura_nc_otro_mes_sin_reemplazo", row["tipo_afectacion_nc"])
        self.assertTrue(bool(row["requiere_recalculo_piso"]))
        self.assertEqual("2026-01", row["periodo_recalculo"])

    def test_same_day_chain_is_cut_and_replacement_becomes_origin(self) -> None:
        original = sale("SM1", "2026-01-10", note="NC1", note_date="2026-01-10")
        original["factura_reemplazo_tranId_nc"] = "SM2"
        # Reproduce la asociacion propagada que aparecia en el corte real.
        replacement = sale(
            "SM2",
            "2026-01-10",
            replacement="SM1",
            note="NC1",
            note_date="2026-01-10",
        )
        replacement["factura_reemplazo_tranId_nc"] = "SM2"
        data = pd.DataFrame([original, replacement])

        actual = apply_credit_effects(data, pd.DataFrame())
        original_result = actual.loc[actual["tranId"].eq("SM1")].iloc[0]
        replacement_result = actual.loc[actual["tranId"].eq("SM2")].iloc[0]

        self.assertTrue(bool(original_result["cadena_rota_mismo_dia"]))
        self.assertEqual("factura_nc_mismo_mes", original_result["tipo_afectacion_nc"])
        self.assertFalse(bool(replacement_result["reemplazo"]))
        self.assertTrue(pd.isna(replacement_result["documento_origen"]))
        self.assertTrue(pd.isna(replacement_result["tranId_nc"]))
        self.assertEqual("ninguna", replacement_result["tipo_afectacion_nc"])
        self.assertFalse(bool(replacement_result["cadena_rota_mismo_dia"]))

        floor = calculate_floor_line_status(actual)
        original_floor = floor.loc[floor["tranId"].eq("SM1")].iloc[0]
        replacement_floor = floor.loc[floor["tranId"].eq("SM2")].iloc[0]
        self.assertEqual("no suma", original_floor["estado_piso"])
        self.assertEqual("suma", replacement_floor["estado_piso"])
        self.assertEqual(1000, replacement_floor["TotalParaPiso_final"])

    def test_sm72210_does_not_inherit_nc5226_from_sm72209(self) -> None:
        original = sale(
            "SM72209",
            "2026-07-24",
            note="NC5226",
            note_date="2026-07-24",
            value=12_950_000,
        )
        original["factura_reemplazo_tranId_nc"] = "SM72210"
        replacement = sale(
            "SM72210",
            "2026-07-24",
            replacement="SM72209",
            note="NC5226",
            note_date="2026-07-24",
            value=9_250_000,
        )
        replacement["factura_reemplazo_tranId_nc"] = "SM72210"

        actual = apply_credit_effects(
            pd.DataFrame([original, replacement]), pd.DataFrame()
        )
        replacement_result = actual.loc[actual["tranId"].eq("SM72210")].iloc[0]

        self.assertFalse(bool(replacement_result["reemplazo"]))
        self.assertTrue(pd.isna(replacement_result["documento_origen"]))
        self.assertTrue(pd.isna(replacement_result["tranId_nc"]))
        self.assertEqual("ninguna", replacement_result["tipo_afectacion_nc"])

        floor = calculate_floor_line_status(actual)
        replacement_floor = floor.loc[floor["tranId"].eq("SM72210")].iloc[0]
        self.assertEqual("suma", replacement_floor["estado_piso"])
        self.assertEqual(9_250_000, replacement_floor["TotalParaPiso_final"])

    def test_proforma_cancellation_detected_from_memo(self) -> None:
        data = pd.DataFrame([sale("PR-1", "2026-01-10", invoice=False)])
        cancellation = pd.DataFrame(
            {"tranId": ["NC-P1"], "tranDate": ["2026-02-03"], "memo": ["ANULA PR-1"]}
        )

        actual = apply_credit_effects(data, cancellation)
        row = actual.iloc[0]

        self.assertTrue(bool(row["documento_anulado"]))
        self.assertEqual("proforma_anulada_otro_mes", row["tipo_afectacion_nc"])
        self.assertTrue(bool(row["requiere_recalculo_piso"]))
        self.assertEqual(-1000, row["Valor_nc"])

    def test_floor_priority_replacement_before_review(self) -> None:
        data = pd.DataFrame([sale("SM2", "2026-01-10", replacement="SM1")])
        data["tipo_afectacion_nc"] = "ninguna"
        data["observacion_nc"] = ""
        data["alarma_nc_servicio"] = True
        data["motivo_alarma_nc"] = "alarma"
        data["observacion_reemplazo"] = "No suma por ser reemplazo de SM1"

        actual = calculate_floor_line_status(data)
        row = actual.iloc[0]

        self.assertEqual("no suma", row["estado_piso"])
        self.assertEqual(0, row["TotalParaPiso_final"])
        self.assertEqual("No suma por ser reemplazo de SM1", row["observacion_piso"])

    def test_unresolved_credit_match_is_for_review(self) -> None:
        data = pd.DataFrame([sale("SM1", "2026-01-10")])
        data["tipo_afectacion_nc"] = "ninguna"
        data["observacion_nc"] = ""
        data["alarma_nc_servicio"] = True
        data["motivo_alarma_nc"] = "multiples_nc_sin_match_por_valor_o_lote"

        actual = calculate_floor_line_status(data)
        row = actual.iloc[0]

        self.assertEqual("para revision", row["estado_piso"])
        self.assertTrue(pd.isna(row["TotalParaPiso_final"]))


if __name__ == "__main__":
    unittest.main()
