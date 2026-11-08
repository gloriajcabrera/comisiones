from __future__ import annotations

import unittest

import pandas as pd

from src.payment_base import (
    assign_cut_period,
    build_official_payment_history,
    build_payment_status,
    prepare_payment_documents,
)


def cuts():
    return pd.DataFrame(
        {
            "inicio_corte": ["2026-01-05", "2026-02-05"],
            "fin_corte": ["2026-02-04", "2026-03-04"],
            "periodo_corte": ["2026-01", "2026-02"],
        }
    )


def invoice_payment(**overrides):
    row = {
        "factura_id": 1,
        "factura": "SM1",
        "fecha_factura": "2026-01-01",
        "pago_id": 10,
        "documento_pago": "RC-1",
        "tipo_pago": "CustPymt",
        "fecha_contabilizacion": "2026-01-20",
        "fecha_real": "2026-01-18",
        "fecha_pago": None,
        "valor_pago": 100,
        "memo_pago": "pago",
    }
    row.update(overrides)
    return row


def proforma_payment(**overrides):
    row = {
        "proforma_id": 2,
        "proforma": "PR-1",
        "fecha_proforma": "2026-01-02",
        "deposito_id": 3,
        "deposito": "DEP-1",
        "pago_id": 11,
        "documento_pago": "DEP-1",
        "tipo_pago": "DepAppl",
        "fecha_transaccion": "2026-01-21",
        "fecha_real_transaccion": "2026-01-19",
        "fecha_pago": None,
        "valor_pagado_proforma": 200,
        "memo_pago": "deposito",
        "tipo_vinculo": "Payment",
    }
    row.update(overrides)
    return row


def payment_document(*, product=False, mixed=False, official_balance=None):
    return pd.DataFrame(
        {
            "tipo_documento": ["FACTURA"],
            "documento": ["SM1"],
            "valor_total_transaccion": [100.0],
            "saldo_netsuite_transaccion": [official_balance],
            "tasa_documento": [4000.0],
            "fecha_vencimiento": [pd.Timestamp("2026-01-01")],
            "es_producto_puro": [product],
            "es_producto_mixto": [mixed],
        }
    )


def empty_payment_history():
    return pd.DataFrame(
        columns=[
            "tipo_documento",
            "documento",
            "pago_id",
            "documento_pago",
            "fecha_pago",
            "fecha_contabilizacion",
            "periodo_corte",
            "inicio_corte",
            "fin_corte",
            "es_pago",
            "es_nota_credito",
            "valor_pago_transaccion",
            "valor_nota_credito_transaccion",
        ]
    )


class PaymentBaseTest(unittest.TestCase):
    def test_document_without_payment_keeps_payment_fields_empty(self) -> None:
        actual = build_payment_status(
            payment_document(official_balance=100),
            empty_payment_history(),
        ).iloc[0]

        self.assertEqual("SALDO PENDIENTE", actual["estado_pago_documento"])
        self.assertEqual(0, actual["total_pagado_transaccion"])
        self.assertTrue(pd.isna(actual["fecha_primer_pago"]))
        self.assertTrue(pd.isna(actual["fecha_ultimo_pago"]))
        self.assertTrue(pd.isna(actual["fecha_pago_referencia"]))
        self.assertTrue(pd.isna(actual["fecha_contabilizacion_corte"]))
        self.assertTrue(pd.isna(actual["periodo_corte_pago"]))

    def test_partial_payment_keeps_movement_but_not_completion_date(self) -> None:
        history = build_official_payment_history(
            pd.DataFrame([invoice_payment(valor_pago=40)]),
            pd.DataFrame([proforma_payment()]),
            cuts(),
        )
        actual = build_payment_status(
            payment_document(official_balance=60),
            history.loc[history["documento"].eq("SM1")],
        ).iloc[0]

        self.assertEqual("SALDO PENDIENTE", actual["estado_pago_documento"])
        self.assertEqual(40, actual["total_pagado_transaccion"])
        self.assertEqual(pd.Timestamp("2026-01-18"), actual["fecha_ultimo_pago"])
        self.assertTrue(pd.isna(actual["fecha_pago_total"]))
        self.assertTrue(pd.isna(actual["fecha_pago_referencia"]))
        self.assertTrue(pd.isna(actual["fecha_contabilizacion_corte"]))

    def test_cut_period_rejects_overlap(self) -> None:
        overlapping = pd.DataFrame(
            {
                "inicio_corte": ["2026-01-01", "2026-01-15"],
                "fin_corte": ["2026-01-31", "2026-02-15"],
                "periodo_corte": ["A", "B"],
            }
        )
        with self.assertRaisesRegex(ValueError, "superpuestos"):
            assign_cut_period(pd.DataFrame({"fecha": ["2026-01-20"]}), "fecha", overlapping)

    def test_cut_assignment_uses_calendar_month_before_first_configured_cut(self) -> None:
        data = pd.DataFrame({"fecha": ["2025-12-20", "2026-01-20"]})
        actual = assign_cut_period(data, "fecha", cuts())
        self.assertEqual(["2025-12", "2026-01"], actual["periodo_corte"].tolist())

    def test_document_base_keeps_foreign_transaction_value_and_product_mix(self) -> None:
        data = pd.DataFrame(
            {
                "tranId": ["SM1", "SM1"],
                "tranDate": ["2026-01-01", "2026-01-01"],
                "dueDate": ["2026-02-01", "2026-02-01"],
                "id": [1, 1],
                "clase": ["PRODUCTO", "RETIE"],
                "valor_total_documento": [400000, 400000],
                "valor_moneda_transaccion": [100, 100],
                "valor_moneda_local": [400000, 400000],
                "moneda_id": [2, 2],
                "foreignamountunpaid": [25, 25],
            }
        )
        actual = prepare_payment_documents(data, "FACTURA").iloc[0]
        self.assertEqual(100, actual["valor_total_transaccion"])
        self.assertEqual(4000, actual["tasa_documento"])
        self.assertTrue(bool(actual["es_producto_mixto"]))
        self.assertFalse(bool(actual["es_producto_puro"]))

    def test_history_uses_real_date_and_sums_same_payment_lines(self) -> None:
        invoices = pd.DataFrame(
            [invoice_payment(valor_pago=100), invoice_payment(valor_pago=50, memo_pago="segunda")]
        )
        proformas = pd.DataFrame([proforma_payment()])
        actual = build_official_payment_history(invoices, proformas, cuts())
        invoice = actual.loc[actual["documento"].eq("SM1")].iloc[0]
        proforma = actual.loc[actual["documento"].eq("PR-1")].iloc[0]
        self.assertEqual(150, invoice["valor_pago_transaccion"])
        self.assertEqual(pd.Timestamp("2026-01-18"), invoice["fecha_pago"])
        self.assertEqual("2026-01", invoice["periodo_corte"])
        self.assertEqual("PROFORMA", proforma["tipo_documento"])

    def test_credit_application_is_not_counted_as_cash_payment(self) -> None:
        invoices = pd.DataFrame(
            [invoice_payment(tipo_pago="CustCred", valor_pago=80, documento_pago="NC1")]
        )
        actual = build_official_payment_history(
            invoices, pd.DataFrame([proforma_payment()]), cuts()
        )
        credit = actual.loc[actual["documento"].eq("SM1")].iloc[0]
        self.assertTrue(bool(credit["es_nota_credito"]))
        self.assertEqual(0, credit["valor_pago_transaccion"])
        self.assertEqual(80, credit["valor_nota_credito_transaccion"])

    def test_full_payment_uses_completion_date_and_accounting_cut(self) -> None:
        invoices = pd.DataFrame(
            [
                invoice_payment(
                    pago_id=10,
                    valor_pago=60,
                    fecha_real="2026-01-10",
                    fecha_contabilizacion="2026-01-12",
                ),
                invoice_payment(
                    pago_id=11,
                    documento_pago="RC-2",
                    valor_pago=40,
                    fecha_real="2026-02-10",
                    fecha_contabilizacion="2026-02-12",
                ),
            ]
        )
        history = build_official_payment_history(
            invoices, pd.DataFrame([proforma_payment()]), cuts()
        )
        actual = build_payment_status(
            payment_document(official_balance=0), history.loc[history["documento"].eq("SM1")]
        ).iloc[0]
        self.assertEqual(pd.Timestamp("2026-02-10"), actual["fecha_pago_total"])
        self.assertEqual(pd.Timestamp("2026-02-12"), actual["fecha_contabilizacion_corte"])
        self.assertEqual("2026-02", actual["periodo_corte_pago"])
        self.assertEqual(0.5, actual["factor_pago_referencia"])

    def test_product_uses_first_payment_date_but_completion_accounting_cut(self) -> None:
        invoices = pd.DataFrame(
            [
                invoice_payment(
                    pago_id=10,
                    valor_pago=60,
                    fecha_real="2026-01-20",
                    fecha_contabilizacion="2026-01-21",
                ),
                invoice_payment(
                    pago_id=11,
                    documento_pago="RC-2",
                    valor_pago=40,
                    fecha_real="2026-03-01",
                    fecha_contabilizacion="2026-03-02",
                ),
            ]
        )
        history = build_official_payment_history(
            invoices, pd.DataFrame([proforma_payment()]), cuts()
        )
        actual = build_payment_status(
            payment_document(product=True, official_balance=0),
            history.loc[history["documento"].eq("SM1")],
        ).iloc[0]
        self.assertTrue(bool(actual["aplica_regla_producto_50"]))
        self.assertEqual(pd.Timestamp("2026-01-20"), actual["fecha_pago_referencia"])
        self.assertEqual(pd.Timestamp("2026-03-02"), actual["fecha_contabilizacion_corte"])
        self.assertEqual(1.0, actual["factor_pago_referencia"])

    def test_invoice_official_balance_overrides_calculated_balance(self) -> None:
        history = build_official_payment_history(
            pd.DataFrame([invoice_payment(valor_pago=100)]),
            pd.DataFrame([proforma_payment()]),
            cuts(),
        )
        actual = build_payment_status(
            payment_document(official_balance=25),
            history.loc[history["documento"].eq("SM1")],
        ).iloc[0]
        self.assertEqual(0, actual["saldo_calculado_transaccion"])
        self.assertEqual(25, actual["saldo_actual_transaccion"])
        self.assertFalse(bool(actual["saldo_cero_actual"]))
        self.assertEqual("SALDO PENDIENTE", actual["estado_pago_documento"])

    def test_credit_note_controls_state_and_payment_origin(self) -> None:
        history = build_official_payment_history(
            pd.DataFrame(
                [invoice_payment(tipo_pago="CustCred", valor_pago=100, documento_pago="NC1")]
            ),
            pd.DataFrame([proforma_payment()]),
            cuts(),
        )
        actual = build_payment_status(
            payment_document(official_balance=0),
            history.loc[history["documento"].eq("SM1")],
        ).iloc[0]
        self.assertTrue(bool(actual["tiene_nota_credito"]))
        self.assertEqual("DOCUMENTO CON NOTA CREDITO", actual["estado_pago_documento"])
        self.assertEqual("DOCUMENTO CON NOTA CREDITO", actual["origen_fecha_pago"])


if __name__ == "__main__":
    unittest.main()
