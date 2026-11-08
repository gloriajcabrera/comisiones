from __future__ import annotations

import unittest

import pandas as pd

from src.document_relationships import (
    apply_fallback_replacements,
    attach_credit_notes,
    resolve_explicit_replacements,
)
from src.normalization import build_sales_base, normalize_transaction_documents


def document(number: str, internal_id: int, *, source=None, replacement=None):
    return {
        "tranId": number,
        "tranDate": "2026-01-10",
        "dueDate": "2026-02-10",
        "id": internal_id,
        "createdfrom": source,
        "custbody_sm_reemplaza_a": None,
        "custbody_sm_factura_reemplazo": replacement,
        "custbody_ks_sm_proforma": False,
        "type": "CustInvc",
        "status": "B",
        "entityid": "EJECUTIVO",
        "Servicio": "SERVICIO A",
        "lote": "LOTE 1",
        "cc": "CC",
        "memo": "",
        "clase": "RETIE",
        "Valor": 1000,
        "Precio": 1000,
        "quantity": 1,
    }


def credit_note(number: str, source: int, *, replacement=None, value=-1000):
    row = document(number, 9000, source=source, replacement=replacement)
    row["type"] = "CustCred"
    row["Valor"] = value
    return row


class DocumentRelationshipTest(unittest.TestCase):
    def test_assigns_unique_credit_note_to_invoice_line(self) -> None:
        sales = build_sales_base(pd.DataFrame([document("SM1", 1)]), pd.DataFrame())
        notes = normalize_transaction_documents(pd.DataFrame([credit_note("NC1", 1)]))

        actual = attach_credit_notes(sales, notes)

        self.assertEqual("NC1", actual.loc[0, "tranId_nc"])
        self.assertEqual("servicio_unica", actual.loc[0, "tipo_match_nc"])
        self.assertFalse(bool(actual.loc[0, "alarma_nc_servicio"]))

    def test_matches_multiple_notes_by_value(self) -> None:
        invoice = document("SM1", 1)
        invoice["Valor"] = 600
        sales = build_sales_base(pd.DataFrame([invoice]), pd.DataFrame())
        notes = pd.DataFrame(
            [
                credit_note("NC1", 1, value=-400),
                credit_note("NC2", 1, value=-600),
            ]
        )

        actual = attach_credit_notes(sales, notes)

        self.assertEqual("NC2", actual.loc[0, "tranId_nc"])
        self.assertEqual("servicio_valor", actual.loc[0, "tipo_match_nc"])

    def test_nc_replacement_preserves_origin_and_credit_note(self) -> None:
        invoices = pd.DataFrame([document("SM1", 1), document("SM2", 2)])
        sales = build_sales_base(invoices, pd.DataFrame())
        notes = pd.DataFrame([credit_note("NC1", 1, replacement=2)])
        with_notes = attach_credit_notes(sales, notes)

        actual = resolve_explicit_replacements(with_notes, notes)
        replacement = actual.loc[actual["tranId"].eq("SM2")].iloc[0]

        self.assertTrue(bool(replacement["reemplazo"]))
        self.assertEqual("SM1", replacement["documento_origen"])
        self.assertEqual("NC1", replacement["tranId_nc"])
        self.assertEqual("reemplazo_factura_nc", replacement["tipo_reemplazo"])
        self.assertIn("mediante NC1", replacement["observacion_reemplazo"])

    def test_createdfrom_proforma_is_explicit_replacement(self) -> None:
        proforma = document("PR-1", 10)
        proforma["type"] = "SalesOrd"
        invoice = document("SM1", 20, source=10)
        sales = build_sales_base(pd.DataFrame([invoice]), pd.DataFrame([proforma]))
        with_notes = attach_credit_notes(sales, pd.DataFrame(columns=[
            "createdfrom", "Servicio", "custbody_sm_factura_reemplazo",
            "tranId", "tranDate", "Valor",
        ]))

        actual = resolve_explicit_replacements(with_notes, pd.DataFrame(columns=[
            "createdfrom", "Servicio", "custbody_sm_factura_reemplazo",
            "tranId", "tranDate", "Valor",
        ]))
        replacement = actual.loc[actual["tranId"].eq("SM1")].iloc[0]

        self.assertEqual("PR-1", replacement["documento_origen"])
        self.assertEqual("reemplazo_proforma_createdfrom", replacement["tipo_reemplazo"])

    def test_fallback_marks_only_later_distinct_document(self) -> None:
        first = document("SM1", 1)
        duplicate_line = document("SM1", 1)
        second = document("SM2", 2)
        second["tranDate"] = "2026-02-10"
        sales = build_sales_base(
            pd.DataFrame([first, duplicate_line, second]), pd.DataFrame()
        )
        sales["tranId_nc"] = pd.NA
        explicit = resolve_explicit_replacements(
            sales,
            pd.DataFrame(
                columns=[
                    "createdfrom",
                    "Servicio",
                    "custbody_sm_factura_reemplazo",
                    "tranId",
                    "tranDate",
                    "Valor",
                ]
            ),
        )
        payments = pd.DataFrame(columns=["factura", "tipo_pago", "valor_pago"])

        actual = apply_fallback_replacements(explicit, payments)

        first_rows = actual.loc[actual["tranId"].eq("SM1")]
        second_row = actual.loc[actual["tranId"].eq("SM2")].iloc[0]
        self.assertFalse(first_rows["reemplazo"].any())
        self.assertTrue(bool(second_row["reemplazo"]))
        self.assertEqual("SM1", second_row["documento_origen"])
        self.assertEqual("reemplazo_fallback", second_row["tipo_reemplazo"])

    def test_positive_linked_payment_disables_fallback_for_group(self) -> None:
        first = document("SM1", 1)
        second = document("SM2", 2)
        second["tranDate"] = "2026-02-10"
        sales = build_sales_base(pd.DataFrame([first, second]), pd.DataFrame())
        sales["tranId_nc"] = pd.NA
        explicit = resolve_explicit_replacements(
            sales,
            pd.DataFrame(
                columns=[
                    "createdfrom",
                    "Servicio",
                    "custbody_sm_factura_reemplazo",
                    "tranId",
                    "tranDate",
                    "Valor",
                ]
            ),
        )
        payments = pd.DataFrame(
            {"factura": ["SM2"], "tipo_pago": ["CustPymt"], "valor_pago": [100]}
        )

        actual = apply_fallback_replacements(explicit, payments)

        self.assertFalse(actual["reemplazo"].any())

    def test_sm71824_with_linked_payment_is_independent_from_sm60283(self) -> None:
        original = document("SM60283", 60283)
        candidate = document("SM71824", 71824)
        candidate["tranDate"] = "2026-02-10"
        sales = build_sales_base(
            pd.DataFrame([original, candidate]),
            pd.DataFrame(),
        )
        sales["tranId_nc"] = pd.NA
        explicit = resolve_explicit_replacements(
            sales,
            pd.DataFrame(
                columns=[
                    "createdfrom",
                    "Servicio",
                    "custbody_sm_factura_reemplazo",
                    "tranId",
                    "tranDate",
                    "Valor",
                ]
            ),
        )
        payments = pd.DataFrame(
            {
                "factura": ["SM71824"],
                "tipo_pago": ["CustPymt"],
                "valor_pago": [1],
            }
        )

        actual = apply_fallback_replacements(explicit, payments).set_index("tranId")

        self.assertFalse(bool(actual.loc["SM60283", "reemplazo"]))
        self.assertFalse(bool(actual.loc["SM71824", "reemplazo"]))
        self.assertTrue(pd.isna(actual.loc["SM71824", "documento_origen"]))
        self.assertEqual("ninguno", actual.loc["SM71824", "tipo_reemplazo"])

    def test_credit_note_alone_does_not_disable_fallback(self) -> None:
        first = document("SM1", 1)
        second = document("SM2", 2)
        second["tranDate"] = "2026-02-10"
        sales = build_sales_base(pd.DataFrame([first, second]), pd.DataFrame())
        sales["tranId_nc"] = pd.NA
        explicit = resolve_explicit_replacements(
            sales,
            pd.DataFrame(
                columns=[
                    "createdfrom",
                    "Servicio",
                    "custbody_sm_factura_reemplazo",
                    "tranId",
                    "tranDate",
                    "Valor",
                ]
            ),
        )
        payments = pd.DataFrame(
            {"factura": ["SM2"], "tipo_pago": ["CustCred"], "valor_pago": [100]}
        )

        actual = apply_fallback_replacements(explicit, payments)

        self.assertTrue(
            bool(actual.loc[actual["tranId"].eq("SM2"), "reemplazo"].iloc[0])
        )


if __name__ == "__main__":
    unittest.main()
