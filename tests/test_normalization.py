from __future__ import annotations

import unittest

import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal

from src.normalization import (
    annotate_commission_eligibility,
    build_sales_base,
    normalize_boolean,
    normalize_id,
    normalize_invoice_payments,
    normalize_proforma_payments,
    normalize_transaction_documents,
)


class NormalizationTest(unittest.TestCase):
    def test_suiteql_lowercase_aliases_are_restored_before_calculation(self):
        invoice = pd.DataFrame({
            "trandate": ["2026-09-01"],
            "duedate": ["2026-09-30"],
            "tranid": ["SM-API-1"],
            "servicio": ["Servicio API"],
            "valor": [125000],
            "precio": [125000],
            "itemtype": ["Service"],
            "lote": ["L-1"],
            "id": [10],
        })

        actual = build_sales_base(invoice, pd.DataFrame())

        self.assertEqual("SM-API-1", actual.loc[0, "tranId"])
        self.assertEqual("2026-09", str(actual.loc[0, "periodo_factura"]))
        self.assertEqual(125000, actual.loc[0, "Valor"])
        self.assertEqual("0", actual.loc[0, "createdfrom_key"])
        self.assertEqual("0", actual.loc[0, "reemplaza_a_key"])
        self.assertNotIn("trandate", actual.columns)

    def test_eligibility_keeps_excluded_lines_visible_with_reason(self) -> None:
        source = pd.DataFrame({
            "Valor": [100, 200, pd.NA],
            "custitem_sm_linea_serv": ["10", "18", "10"],
            "itemType": ["Service", "Service", "Service"],
        })

        actual = annotate_commission_eligibility(source, "factura")

        self.assertEqual([True, False, False], actual["elegible_comision"].tolist())
        self.assertEqual(
            "No suma: línea de servicio 18 excluida",
            actual.loc[1, "motivo_elegibilidad"],
        )
        self.assertEqual(
            "No suma: línea sin valor contable",
            actual.loc[2, "motivo_elegibilidad"],
        )

    def test_legacy_prefiltered_frame_remains_eligible(self) -> None:
        actual = annotate_commission_eligibility(
            pd.DataFrame({"Valor": [100]}), "factura"
        )

        self.assertTrue(bool(actual.loc[0, "elegible_comision"]))

    def test_empty_string_boolean_series_normalizes_to_bool_dtype(self) -> None:
        source = pd.Series([], dtype="string")
        actual = normalize_boolean(source)

        self.assertEqual(str(actual.dtype), "bool")
        self.assertTrue(actual.empty)

    def test_normalize_id_matches_notebook_behavior(self) -> None:
        source = pd.Series([123.0, " 456.0 ", None, "ABC"], dtype="object")
        actual = normalize_id(source)
        expected = pd.Series(["123", "456", "0", "ABC"], dtype="string")
        assert_series_equal(expected, actual)

    def test_transaction_normalization_does_not_mutate_source(self) -> None:
        source = pd.DataFrame(
            {
                "tranId": [" SM1 "],
                "tranDate": ["2026-01-02"],
                "id": ["10.0"],
                "Valor": ["1250.50"],
                "lote": [None],
                "custbody_ks_sm_proforma": [None],
            }
        )
        original = source.copy(deep=True)
        actual = normalize_transaction_documents(source)

        assert_frame_equal(source, original)
        self.assertEqual("SM1", actual.loc[0, "tranId"])
        self.assertEqual("10", actual.loc[0, "id"])
        self.assertEqual(1250.5, actual.loc[0, "Valor"])
        self.assertEqual("", actual.loc[0, "lote"])
        self.assertFalse(bool(actual.loc[0, "custbody_ks_sm_proforma"]))

    def test_sales_base_uses_source_as_document_type_truth(self) -> None:
        columns = {
            "tranDate": ["2026-01-15"],
            "tranId": [" DOC-1 "],
            "id": [1.0],
            "createdfrom": [None],
            "custbody_sm_reemplaza_a": [None],
            "custbody_sm_factura_reemplazo": [None],
            "custbody_ks_sm_proforma": [True],
            "Servicio": [" servicio x "],
            "lote": [" lote a "],
            "Valor": [-100.129],
        }
        invoice = pd.DataFrame(columns)
        proforma = pd.DataFrame(columns).assign(tranId=" PR-1 ")
        actual = build_sales_base(invoice, proforma)

        self.assertEqual([True, False], actual["es_factura"].tolist())
        self.assertEqual([False, True], actual["es_proforma"].tolist())
        self.assertEqual(["SERVICIO X", "SERVICIO X"], actual["Servicio_key"].tolist())
        self.assertEqual(["LOTE A", "LOTE A"], actual["lote_key"].tolist())
        self.assertEqual([100.13, 100.13], actual["valor_abs"].tolist())
        self.assertEqual(["0", "0"], actual["createdfrom_key"].tolist())

    def test_payment_normalizers_preserve_multiple_applications(self) -> None:
        invoices = pd.DataFrame(
            {
                "factura": [" SM1 ", " SM1 "],
                "pago_id": ["8.0", "8.0"],
                "fecha_pago": ["2026-01-10", "2026-01-10"],
                "valor_pago": ["100", "50"],
            }
        )
        proformas = pd.DataFrame(
            {
                "proforma": [" PR-1 ", " PR-1 "],
                "pago_id": ["9.0", "9.0"],
                "fecha_pago": ["2026-01-11", "2026-01-11"],
                "valor_pagado_proforma": ["75", "25"],
            }
        )

        normalized_invoices = normalize_invoice_payments(invoices)
        normalized_proformas = normalize_proforma_payments(proformas)

        self.assertEqual(2, len(normalized_invoices))
        self.assertEqual(2, len(normalized_proformas))
        self.assertEqual(150, normalized_invoices["valor_pago"].sum())
        self.assertEqual(100, normalized_proformas["valor_pagado_proforma"].sum())


if __name__ == "__main__":
    unittest.main()
