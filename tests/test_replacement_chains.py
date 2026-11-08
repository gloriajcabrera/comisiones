from __future__ import annotations

import unittest

import pandas as pd

from src.replacement_chains import (
    resolve_replacement_chains,
    select_available_payment_source,
)


def chain_row(number, date, *, parent=None, note=None, note_replacement=None):
    return {
        "tranId": number,
        "tranDate": date,
        "dueDate": pd.Timestamp(date) + pd.Timedelta(days=30),
        "periodo_factura": pd.Period(date[:7], freq="M"),
        "documento_origen": parent,
        "fecha_origen": pd.Timestamp("2026-01-01") if parent else pd.NaT,
        "reemplazo": parent is not None,
        "tranId_nc": note,
        "factura_reemplazo_tranId_nc": note_replacement,
    }


class ReplacementChainTest(unittest.TestCase):
    def test_resolves_root_latest_payment_source_and_original_period(self) -> None:
        data = pd.DataFrame(
            [
                chain_row("SM1", "2026-01-01", note="NC1", note_replacement="SM2"),
                chain_row(
                    "SM2",
                    "2026-02-01",
                    parent="SM1",
                    note="NC2",
                    note_replacement="SM3",
                ),
                chain_row("SM3", "2026-03-01", parent="SM2"),
            ]
        )
        actual = resolve_replacement_chains(data).set_index("tranId")
        self.assertEqual("SM1", actual.loc["SM3", "doc_base_comision_key"])
        self.assertEqual("SM3", actual.loc["SM1", "doc_fuente_pago_preferido_key"])
        self.assertEqual(pd.Period("2026-01", freq="M"), actual.loc["SM3", "periodo_mes"])
        self.assertEqual(2, actual.loc["SM3", "cantidad_anulaciones_nc"])
        self.assertEqual("NC1 | NC2", actual.loc["SM3", "documentos_anulacion_nc"])
        self.assertTrue(bool(actual.loc["SM3", "es_documento_comision"]))
        self.assertFalse(bool(actual.loc["SM1", "es_documento_comision"]))

    def test_sm59616_cycle_is_flagged_without_interrupting_calculation(self) -> None:
        data = pd.DataFrame(
            [
                chain_row("SM59616", "2026-01-01", parent="SM59617"),
                chain_row("SM59617", "2026-02-01", parent="SM59616"),
            ]
        )
        actual = resolve_replacement_chains(data)
        self.assertTrue(actual["ciclo_reemplazo"].all())
        self.assertEqual({"SM59616"}, set(actual["doc_base_comision_key"]))
        self.assertEqual(
            {"SM59616 | SM59617"},
            set(actual["documentos_ciclo_reemplazo"]),
        )

    def test_conflicting_parents_are_rejected(self) -> None:
        rows = [
            chain_row("SM3", "2026-03-01", parent="SM1"),
            chain_row("SM3", "2026-03-01", parent="SM2"),
        ]
        with self.assertRaisesRegex(ValueError, "más de un origen"):
            resolve_replacement_chains(pd.DataFrame(rows))

    def test_payment_source_falls_back_to_root_when_latest_is_unavailable(self) -> None:
        data = pd.DataFrame(
            [
                chain_row("SM1", "2026-01-01"),
                chain_row("SM2", "2026-02-01", parent="SM1"),
            ]
        )
        resolved = resolve_replacement_chains(data)
        actual = select_available_payment_source(
            resolved, pd.Series(["SM1"], dtype="string")
        )
        self.assertEqual({"SM1"}, set(actual["doc_fuente_pago_key"]))


if __name__ == "__main__":
    unittest.main()
