from __future__ import annotations

import unittest

import pandas as pd

from src.engine import CommissionInputs, calculate_commissions, calculate_from_provider
from src.netsuite_provider import NetSuiteDataset
from src.reporting import InMemoryCalculationResultProvider


def transaction(number, internal_id, document_type, value):
    return {
        "tranId": number,
        "tranDate": "2026-01-01",
        "dueDate": "2026-01-31",
        "id": internal_id,
        "type": document_type,
        "status": "B",
        "entity": 1,
        "entityid": "EJ1",
        "Servicio": "SERVICIO",
        "itemType": "Service",
        "custitem_sm_linea_serv": 1,
        "Precio": value,
        "quantity": 1,
        "Valor": value,
        "valor_total_documento": value,
        "valor_moneda_transaccion": value,
        "valor_moneda_local": value,
        "foreignamountunpaid": 0,
        "moneda_id": 1,
        "createdfrom": None,
        "custitem_sm_lineacomision": 10,
        "custbody_sm_reemplaza_a": None,
        "custbody_sm_factura_reemplazo": None,
        "custbody_ks_sm_proforma": document_type == "SalesOrd",
        "clase": "RETIE",
        "cc": "NORMAL",
        "lote": "L1",
        "memo": "",
    }


class EngineTest(unittest.TestCase):
    def test_auxiliary_line_preserves_replacement_chain_without_entering_results(self) -> None:
        origin = transaction("SM-ORIGIN", 901, "CustInvc", 1000)
        replacement = transaction("SM-REPLACEMENT", 902, "CustInvc", 1000)
        replacement["tranDate"] = "2026-02-01"
        auxiliary = replacement.copy()
        auxiliary["line_id"] = 0
        auxiliary["createdfrom"] = 901
        auxiliary["Servicio"] = None
        auxiliary["custitem_sm_linea_serv"] = None
        auxiliary["custitem_sm_lineacomision"] = 0
        replacement["line_id"] = 1
        origin["line_id"] = 1

        invoices = pd.DataFrame([origin, auxiliary, replacement])
        result = calculate_commissions(
            CommissionInputs(
                invoices=invoices,
                proformas=invoices.iloc[0:0].copy(),
                credit_notes=invoices.iloc[0:0].copy(),
                proforma_cancellations=pd.DataFrame(),
                invoice_payments=pd.DataFrame(
                    {
                        "factura_id": [902],
                        "factura": ["SM-REPLACEMENT"],
                        "fecha_factura": ["2026-02-01"],
                        "pago_id": [903],
                        "documento_pago": ["RC-903"],
                        "tipo_pago": ["CustPymt"],
                        "fecha_contabilizacion": ["2026-02-12"],
                        "fecha_real": ["2026-02-10"],
                        "fecha_pago": ["2026-02-10"],
                        "valor_pago": [1000],
                        "memo_pago": ["PAGO TOTAL"],
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
                targets=pd.DataFrame(
                    {
                        "periodo": ["2026-01-01"],
                        "ejecutivo": ["EJ1"],
                        "linea_comision": ["RETIE/LAP"],
                        "meta": [1000],
                        "piso": [800],
                    }
                ),
                costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
                cuts=pd.DataFrame(
                    {
                        "inicio_corte": ["2026-02-01"],
                        "fin_corte": ["2026-02-28"],
                        "periodo_corte": ["2026-02"],
                    }
                ),
            )
        )

        self.assertEqual(2, len(result.commission_detail))
        detail = result.commission_detail.set_index("tranId")
        self.assertEqual("SM-ORIGIN", detail.loc["SM-REPLACEMENT", "doc_base_comision"])
        self.assertEqual("SM-REPLACEMENT", detail.loc["SM-REPLACEMENT", "doc_fuente_pago_key"])
        self.assertEqual(pd.Timestamp("2026-02-10"), detail.loc["SM-REPLACEMENT", "Fecha Real Pago"])

    def test_complete_invoice_produces_full_default_commission(self) -> None:
        invoices = pd.DataFrame([transaction("SM1", 1, "CustInvc", 1000)])
        proformas = pd.DataFrame([transaction("PR-1", 2, "SalesOrd", 500)])
        credit_columns = list(invoices.columns)
        credit_notes = pd.DataFrame(columns=credit_columns)
        invoice_payments = pd.DataFrame(
            {
                "factura_id": [1],
                "factura": ["SM1"],
                "fecha_factura": ["2026-01-01"],
                "pago_id": [10],
                "documento_pago": ["RC-1"],
                "tipo_pago": ["CustPymt"],
                "fecha_contabilizacion": ["2026-02-02"],
                "fecha_real": ["2026-02-01"],
                "fecha_pago": ["2026-02-01"],
                "valor_pago": [1000],
                "memo_pago": ["PAGO"],
            }
        )
        proforma_payments = pd.DataFrame(
            {
                "proforma_id": [2],
                "proforma": ["PR-1"],
                "fecha_proforma": ["2026-01-01"],
                "deposito_id": [20],
                "deposito": ["DEP-1"],
                "pago_id": [21],
                "documento_pago": ["DEP-1"],
                "tipo_pago": ["DepAppl"],
                "fecha_transaccion": ["2026-02-02"],
                "fecha_real_transaccion": ["2026-02-01"],
                "fecha_pago": ["2026-02-01"],
                "valor_pagado_proforma": [500],
                "memo_pago": ["PAGO"],
                "tipo_vinculo": ["Payment"],
            }
        )
        targets = pd.DataFrame(
            {
                "periodo": ["2026-01-01"],
                "ejecutivo": ["EJ1"],
                "linea_comision": ["RETIE/LAP"],
                "meta": [1000],
                "piso": [800],
                "jefe": ["JEFE"],
                "comision_ejecutivo": [None],
                "comision_jefe": [None],
                "gerente": ["GERENTE"],
                "comision_gerente": [None],
            }
        )
        cuts = pd.DataFrame(
            {
                "inicio_corte": ["2026-01-01"],
                "fin_corte": ["2026-02-28"],
                "periodo_corte": ["2026-02"],
            }
        )
        inputs = CommissionInputs(
            invoices=invoices,
            proformas=proformas,
            credit_notes=credit_notes,
            proforma_cancellations=pd.DataFrame(),
            invoice_payments=invoice_payments,
            proforma_payments=proforma_payments,
            targets=targets,
            costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
            cuts=cuts,
        )

        result = calculate_commissions(inputs)
        invoice = result.commission_detail.loc[
            result.commission_detail["tranId"].eq("SM1")
        ].iloc[0]
        self.assertEqual("PAGA 100%", invoice["decision_pago"])
        self.assertEqual(40, invoice["valor_comision_ejecutivo"])
        self.assertEqual(10, invoice["valor_comision_jefe"])
        self.assertEqual(20, invoice["valor_comision_gerente"])
        self.assertEqual(1, len(result.payment_base.history.loc[
            result.payment_base.history["documento"].eq("SM1")
        ]))

    def test_calculation_can_run_from_provider_contract(self) -> None:
        invoices = pd.DataFrame([transaction("SM1", 1, "CustInvc", 1000)])
        dataset = NetSuiteDataset(
            invoices=invoices,
            costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
            proformas=pd.DataFrame(
                columns=invoices.columns
            ),
            credit_notes=pd.DataFrame(columns=invoices.columns),
            proforma_cancellations=pd.DataFrame(),
            invoice_payments=pd.DataFrame(
                {
                    "factura_id": [1],
                    "factura": ["SM1"],
                    "fecha_factura": ["2026-01-01"],
                    "pago_id": [10],
                    "documento_pago": ["RC-1"],
                    "tipo_pago": ["CustPymt"],
                    "fecha_contabilizacion": ["2026-02-02"],
                    "fecha_real": ["2026-02-01"],
                    "fecha_pago": ["2026-02-01"],
                    "valor_pago": [1000],
                    "memo_pago": ["PAGO"],
                }
            ),
            proforma_payments=pd.DataFrame(
                columns=[
                    "proforma_id",
                    "proforma",
                    "fecha_proforma",
                    "deposito_id",
                    "deposito",
                    "pago_id",
                    "documento_pago",
                    "tipo_pago",
                    "fecha_transaccion",
                    "fecha_real_transaccion",
                    "fecha_pago",
                    "valor_pagado_proforma",
                    "memo_pago",
                    "tipo_vinculo",
                ]
            ),
        )

        class Provider:
            def load_dataset(self):
                return dataset

        targets = pd.DataFrame(
            {
                "periodo": ["2026-01-01"],
                "ejecutivo": ["EJ1"],
                "linea_comision": ["RETIE/LAP"],
                "meta": [1000],
                "piso": [800],
            }
        )
        cuts = pd.DataFrame(
            {
                "inicio_corte": ["2026-01-01"],
                "fin_corte": ["2026-02-28"],
                "periodo_corte": ["2026-02"],
            }
        )

        result = calculate_from_provider(Provider(), targets, cuts)
        invoice = result.commission_detail.iloc[0]
        self.assertEqual(invoice["tranId"], "SM1")
        self.assertEqual(invoice["decision_pago"], "PAGA 100%")

    def test_proforma_replacement_uses_receipt_dates_and_original_commission_base(self) -> None:
        proforma = transaction("PR-REF-9553", 101, "SalesOrd", 1750000)
        proforma["tranDate"] = "2026-07-22"
        proforma["dueDate"] = "2026-07-22"
        replacement = transaction("SM-REF-72586", 102, "CustInvc", 1750000)
        replacement["tranDate"] = "2026-08-15"
        replacement["dueDate"] = "2026-08-30"
        replacement["createdfrom"] = 101

        invoices = pd.DataFrame([replacement])
        proformas = pd.DataFrame([proforma])
        payments = pd.DataFrame(
            {
                "factura_id": [102],
                "factura": ["SM-REF-72586"],
                "fecha_factura": ["2026-08-15"],
                "pago_id": [19486],
                "documento_pago": ["RC-019486"],
                "tipo_pago": ["CustPymt"],
                "fecha_contabilizacion": ["2026-09-18"],
                "fecha_real": ["2026-09-15"],
                "fecha_pago": ["2026-09-15"],
                "valor_pago": [1750000],
                "memo_pago": ["PAGO TOTAL"],
            }
        )
        targets = pd.DataFrame(
            {
                "periodo": ["2026-07-01"],
                "ejecutivo": ["EJ1"],
                "linea_comision": ["RETIE/LAP"],
                "meta": [1000000],
                "piso": [800000],
            }
        )
        cuts = pd.DataFrame(
            {
                "inicio_corte": ["2026-09-01"],
                "fin_corte": ["2026-09-30"],
                "periodo_corte": ["2026-09"],
            }
        )
        result = calculate_commissions(
            CommissionInputs(
                invoices=invoices,
                proformas=proformas,
                credit_notes=invoices.iloc[0:0].copy(),
                proforma_cancellations=pd.DataFrame(),
                invoice_payments=payments,
                proforma_payments=pd.DataFrame(
                    columns=[
                        "proforma_id", "proforma", "fecha_proforma", "deposito_id",
                        "deposito", "pago_id", "documento_pago", "tipo_pago",
                        "fecha_transaccion", "fecha_real_transaccion", "fecha_pago",
                        "valor_pagado_proforma", "memo_pago", "tipo_vinculo",
                    ]
                ),
                targets=targets,
                costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
                cuts=cuts,
            )
        )

        detail = result.commission_detail.set_index("tranId")
        replacement_result = detail.loc["SM-REF-72586"]
        self.assertEqual("PR-REF-9553", replacement_result["doc_base_comision"])
        self.assertEqual("SM-REF-72586", replacement_result["doc_fuente_pago_key"])
        self.assertEqual(pd.Period("2026-07", freq="M"), replacement_result["periodo_mes"])
        self.assertEqual(pd.Timestamp("2026-07-22"), replacement_result["dueDate_base_pago"])
        self.assertEqual(pd.Timestamp("2026-09-15"), replacement_result["Fecha Real Pago"])
        self.assertEqual(
            pd.Timestamp("2026-09-18"),
            replacement_result["Fecha Contabilizado Pago"],
        )
        self.assertEqual("2026-09", replacement_result["Periodo Corte"])
        self.assertNotEqual(
            replacement_result["Fecha Real Pago"], replacement_result["tranDate"]
        )

    def test_replacement_chain_with_credit_and_unpaid_latest_document_stays_pending(self) -> None:
        proforma = transaction("PR-REF-9551", 201, "SalesOrd", 16560000)
        proforma["tranDate"] = "2026-07-20"
        proforma["dueDate"] = "2026-07-20"
        first_invoice = transaction("SM-REF-72791", 202, "CustInvc", 16560000)
        first_invoice["tranDate"] = "2026-08-01"
        first_invoice["createdfrom"] = 201
        latest_invoice = transaction("SM-REF-73590", 203, "CustInvc", 16560000)
        latest_invoice["tranDate"] = "2026-09-01"
        latest_invoice["foreignamountunpaid"] = 16560000

        credit_note = transaction("NC-REF-5317", 301, "CustCred", -16560000)
        credit_note["tranDate"] = "2026-08-20"
        credit_note["createdfrom"] = 202
        credit_note["custbody_sm_factura_reemplazo"] = 203
        credit_note["Valor"] = -16560000
        credit_note["valor_total_documento"] = -16560000
        credit_note["valor_moneda_transaccion"] = -16560000
        credit_note["valor_moneda_local"] = -16560000

        invoices = pd.DataFrame([first_invoice, latest_invoice])
        empty_invoice_payments = pd.DataFrame(
            columns=[
                "factura_id", "factura", "fecha_factura", "pago_id",
                "documento_pago", "tipo_pago", "fecha_contabilizacion",
                "fecha_real", "fecha_pago", "valor_pago", "memo_pago",
            ]
        )
        empty_proforma_payments = pd.DataFrame(
            columns=[
                "proforma_id", "proforma", "fecha_proforma", "deposito_id",
                "deposito", "pago_id", "documento_pago", "tipo_pago",
                "fecha_transaccion", "fecha_real_transaccion", "fecha_pago",
                "valor_pagado_proforma", "memo_pago", "tipo_vinculo",
            ]
        )
        result = calculate_commissions(
            CommissionInputs(
                invoices=invoices,
                proformas=pd.DataFrame([proforma]),
                credit_notes=pd.DataFrame([credit_note]),
                proforma_cancellations=pd.DataFrame(),
                invoice_payments=empty_invoice_payments,
                proforma_payments=empty_proforma_payments,
                targets=pd.DataFrame(
                    {
                        "periodo": ["2026-07-01"],
                        "ejecutivo": ["EJ1"],
                        "linea_comision": ["RETIE/LAP"],
                        "meta": [1000000],
                        "piso": [800000],
                    }
                ),
                costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
                cuts=pd.DataFrame(
                    {
                        "inicio_corte": ["2026-09-01"],
                        "fin_corte": ["2026-09-30"],
                        "periodo_corte": ["2026-09"],
                    }
                ),
            )
        )

        detail = result.commission_detail.set_index("tranId")
        latest = detail.loc["SM-REF-73590"]
        self.assertEqual("PR-REF-9551", latest["doc_base_comision"])
        self.assertEqual("SM-REF-73590", latest["doc_fuente_pago_key"])
        self.assertEqual("NC-REF-5317", latest["documentos_anulacion_nc"])
        self.assertEqual(pd.Period("2026-07", freq="M"), latest["periodo_mes"])
        self.assertTrue(pd.isna(latest["Fecha Real Pago"]))
        self.assertTrue(pd.isna(latest["Fecha Contabilizado Pago"]))
        self.assertGreater(latest["Saldo"], 0)
        self.assertEqual("NO PAGA", latest["decision_pago"])
        self.assertEqual("Pendiente recibir pago de la factura", latest["nota_pago"])
        self.assertFalse(bool(latest["requiere_revision_pago"]))

    def test_similar_invoices_with_linked_payments_remain_independent(self) -> None:
        first = transaction("SM-REF-70201", 401, "CustInvc", 657100)
        second = transaction("SM-REF-72083", 402, "CustInvc", 657100)
        second["tranDate"] = "2026-01-02"
        second["dueDate"] = "2026-01-31"
        invoices = pd.DataFrame([first, second])
        payments = pd.DataFrame(
            [
                {
                    "factura_id": 401, "factura": "SM-REF-70201",
                    "fecha_factura": "2026-01-01", "pago_id": 501,
                    "documento_pago": "LINK-REF-1", "tipo_pago": "CustPymt",
                    "fecha_contabilizacion": "2026-02-02", "fecha_real": "2026-02-01",
                    "fecha_pago": "2026-02-01", "valor_pago": 657100,
                    "memo_pago": "LINK DE PAGO 1",
                },
                {
                    "factura_id": 402, "factura": "SM-REF-72083",
                    "fecha_factura": "2026-01-02", "pago_id": 502,
                    "documento_pago": "LINK-REF-2", "tipo_pago": "CustPymt",
                    "fecha_contabilizacion": "2026-02-03", "fecha_real": "2026-02-02",
                    "fecha_pago": "2026-02-02", "valor_pago": 657100,
                    "memo_pago": "LINK DE PAGO 2",
                },
            ]
        )
        result = calculate_commissions(
            CommissionInputs(
                invoices=invoices,
                proformas=invoices.iloc[0:0].copy(),
                credit_notes=invoices.iloc[0:0].copy(),
                proforma_cancellations=pd.DataFrame(),
                invoice_payments=payments,
                proforma_payments=pd.DataFrame(
                    columns=[
                        "proforma_id", "proforma", "fecha_proforma", "deposito_id",
                        "deposito", "pago_id", "documento_pago", "tipo_pago",
                        "fecha_transaccion", "fecha_real_transaccion", "fecha_pago",
                        "valor_pagado_proforma", "memo_pago", "tipo_vinculo",
                    ]
                ),
                targets=pd.DataFrame(
                    {
                        "periodo": ["2026-01-01"], "ejecutivo": ["EJ1"],
                        "linea_comision": ["RETIE/LAP"], "meta": [1000000],
                        "piso": [800000],
                    }
                ),
                costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
                cuts=pd.DataFrame(
                    {
                        "inicio_corte": ["2026-02-01"], "fin_corte": ["2026-02-28"],
                        "periodo_corte": ["2026-02"],
                    }
                ),
            )
        )

        detail = result.commission_detail.set_index("tranId")
        for document in ("SM-REF-70201", "SM-REF-72083"):
            row = detail.loc[document]
            self.assertFalse(bool(row["reemplazo"]))
            self.assertTrue(pd.isna(row["documento_origen"]))
            self.assertEqual(document, row["doc_base_comision"])
            self.assertEqual(document, row["doc_fuente_pago_key"])
            self.assertEqual("suma", row["estado_piso"])
            self.assertEqual("PAGA 100%", row["decision_pago"])

    def test_credit_note_remains_visible_through_paid_replacement_chain(self) -> None:
        original = transaction("SM-REF-65917", 601, "CustInvc", 2760000)
        original["tranDate"] = "2026-01-15"
        original["dueDate"] = "2026-02-15"
        replacement = transaction("SM-REF-72582", 602, "CustInvc", 2750000)
        replacement["tranDate"] = "2026-08-10"
        replacement["dueDate"] = "2026-09-10"
        credit_note = transaction("NC-REF-5247", 603, "CustCred", -2760000)
        credit_note["tranDate"] = "2026-08-10"
        credit_note["createdfrom"] = 601
        credit_note["custbody_sm_factura_reemplazo"] = 602
        credit_note["Valor"] = -2760000
        credit_note["valor_total_documento"] = -2760000
        credit_note["valor_moneda_transaccion"] = -2760000
        credit_note["valor_moneda_local"] = -2760000

        invoices = pd.DataFrame([original, replacement])
        result = calculate_commissions(
            CommissionInputs(
                invoices=invoices,
                proformas=invoices.iloc[0:0].copy(),
                credit_notes=pd.DataFrame([credit_note]),
                proforma_cancellations=pd.DataFrame(),
                invoice_payments=pd.DataFrame(
                    {
                        "factura_id": [602], "factura": ["SM-REF-72582"],
                        "fecha_factura": ["2026-08-10"], "pago_id": [604],
                        "documento_pago": ["RC-REF-5247"], "tipo_pago": ["CustPymt"],
                        "fecha_contabilizacion": ["2026-09-16"],
                        "fecha_real": ["2026-09-15"], "fecha_pago": ["2026-09-15"],
                        "valor_pago": [2750000], "memo_pago": ["PAGO TOTAL"],
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
                targets=pd.DataFrame(
                    {
                        "periodo": ["2026-01-01"], "ejecutivo": ["EJ1"],
                        "linea_comision": ["RETIE/LAP"], "meta": [2000000],
                        "piso": [1500000],
                    }
                ),
                costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
                cuts=pd.DataFrame(
                    {
                        "inicio_corte": ["2026-09-01"], "fin_corte": ["2026-09-30"],
                        "periodo_corte": ["2026-09"],
                    }
                ),
            )
        )

        detail = result.commission_detail.set_index("tranId")
        original_result = detail.loc["SM-REF-65917"]
        replacement_result = detail.loc["SM-REF-72582"]
        self.assertEqual("NC-REF-5247", original_result["tranId_nc"])
        self.assertEqual("SM-REF-72582", original_result["factura_reemplazo_tranId_nc"])
        self.assertEqual("SM-REF-65917", replacement_result["documento_origen"])
        self.assertEqual("NC-REF-5247", replacement_result["tranId_nc"])
        self.assertEqual("SM-REF-65917", replacement_result["doc_base_comision"])
        self.assertEqual("SM-REF-72582", replacement_result["doc_fuente_pago_key"])
        self.assertEqual("NC-REF-5247", replacement_result["documentos_anulacion_nc"])
        self.assertIn("NC-REF-5247", replacement_result["observacion_reemplazo"])
        self.assertEqual("no suma", replacement_result["estado_piso"])
        self.assertIn("NC-REF-5247", replacement_result["observacion_piso"])
        self.assertEqual(pd.Timestamp("2026-09-15"), replacement_result["Fecha Real Pago"])
        self.assertEqual(0, replacement_result["factor_comision_final"])
        self.assertEqual("PAGO EN CERO", replacement_result["decision_pago"])

    def test_proforma_origins_never_become_self_replacements(self) -> None:
        first_proforma = transaction("PR-REF-8420", 701, "SalesOrd", 22000000)
        second_proforma = transaction("PR-REF-9599", 702, "SalesOrd", 5800000)
        second_proforma["tranDate"] = "2026-01-02"
        first_invoice = transaction("SM-REF-66954", 703, "CustInvc", 22000000)
        first_invoice["createdfrom"] = 701
        first_invoice["tranDate"] = "2026-02-01"
        second_invoice = transaction("SM-REF-73852", 704, "CustInvc", 5800000)
        second_invoice["createdfrom"] = 702
        second_invoice["tranDate"] = "2026-02-02"

        invoices = pd.DataFrame([first_invoice, second_invoice])
        result = calculate_commissions(
            CommissionInputs(
                invoices=invoices,
                proformas=pd.DataFrame([first_proforma, second_proforma]),
                credit_notes=invoices.iloc[0:0].copy(),
                proforma_cancellations=pd.DataFrame(),
                invoice_payments=pd.DataFrame(
                    [
                        {
                            "factura_id": 703, "factura": "SM-REF-66954",
                            "fecha_factura": "2026-02-01", "pago_id": 705,
                            "documento_pago": "RC-REF-8420", "tipo_pago": "CustPymt",
                            "fecha_contabilizacion": "2026-02-10", "fecha_real": "2026-02-09",
                            "fecha_pago": "2026-02-09", "valor_pago": 22000000,
                            "memo_pago": "PAGO TOTAL",
                        },
                        {
                            "factura_id": 704, "factura": "SM-REF-73852",
                            "fecha_factura": "2026-02-02", "pago_id": 706,
                            "documento_pago": "RC-REF-9599", "tipo_pago": "CustPymt",
                            "fecha_contabilizacion": "2026-02-11", "fecha_real": "2026-02-10",
                            "fecha_pago": "2026-02-10", "valor_pago": 5800000,
                            "memo_pago": "PAGO TOTAL",
                        },
                    ]
                ),
                proforma_payments=pd.DataFrame(
                    columns=[
                        "proforma_id", "proforma", "fecha_proforma", "deposito_id",
                        "deposito", "pago_id", "documento_pago", "tipo_pago",
                        "fecha_transaccion", "fecha_real_transaccion", "fecha_pago",
                        "valor_pagado_proforma", "memo_pago", "tipo_vinculo",
                    ]
                ),
                targets=pd.DataFrame(
                    {
                        "periodo": ["2026-01-01"], "ejecutivo": ["EJ1"],
                        "linea_comision": ["RETIE/LAP"], "meta": [20000000],
                        "piso": [15000000],
                    }
                ),
                costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
                cuts=pd.DataFrame(
                    {
                        "inicio_corte": ["2026-02-01"], "fin_corte": ["2026-02-28"],
                        "periodo_corte": ["2026-02"],
                    }
                ),
            )
        )

        detail = result.commission_detail.set_index("tranId")
        pairs = {
            "PR-REF-8420": "SM-REF-66954",
            "PR-REF-9599": "SM-REF-73852",
        }
        for origin, replacement in pairs.items():
            origin_row = detail.loc[origin]
            replacement_row = detail.loc[replacement]
            self.assertFalse(bool(origin_row["reemplazo"]))
            self.assertTrue(pd.isna(origin_row["documento_origen"]))
            self.assertEqual(origin, origin_row["doc_base_comision"])
            self.assertEqual("suma", origin_row["estado_piso"])
            self.assertNotIn(origin, str(origin_row["observacion_reemplazo"]))
            self.assertTrue(bool(replacement_row["reemplazo"]))
            self.assertEqual(origin, replacement_row["documento_origen"])
            self.assertEqual(origin, replacement_row["doc_base_comision"])
            self.assertEqual(replacement, replacement_row["doc_fuente_pago_key"])
            self.assertEqual("no suma", replacement_row["estado_piso"])

    def test_retie_enables_other_lines_with_defaults_while_na_pays_zero(self) -> None:
        specifications = [
            ("SM-REF-RETIE", 801, 10, 1000),
            ("SM-REF-RITEL", 802, 15, 100),
            ("SM-REF-LMA", 803, 2, 100),
            ("SM-REF-NA", 804, 19, 100),
        ]
        invoice_rows = []
        payment_rows = []
        for document, internal_id, line_id, value in specifications:
            row = transaction(document, internal_id, "CustInvc", value)
            row["custitem_sm_lineacomision"] = line_id
            invoice_rows.append(row)
            payment_rows.append(
                {
                    "factura_id": internal_id, "factura": document,
                    "fecha_factura": "2026-01-01", "pago_id": internal_id + 100,
                    "documento_pago": f"RC-{internal_id}", "tipo_pago": "CustPymt",
                    "fecha_contabilizacion": "2026-02-02", "fecha_real": "2026-02-01",
                    "fecha_pago": "2026-02-01", "valor_pago": value,
                    "memo_pago": "PAGO TOTAL",
                }
            )
        invoices = pd.DataFrame(invoice_rows)
        result = calculate_commissions(
            CommissionInputs(
                invoices=invoices,
                proformas=invoices.iloc[0:0].copy(),
                credit_notes=invoices.iloc[0:0].copy(),
                proforma_cancellations=pd.DataFrame(),
                invoice_payments=pd.DataFrame(payment_rows),
                proforma_payments=pd.DataFrame(
                    columns=[
                        "proforma_id", "proforma", "fecha_proforma", "deposito_id",
                        "deposito", "pago_id", "documento_pago", "tipo_pago",
                        "fecha_transaccion", "fecha_real_transaccion", "fecha_pago",
                        "valor_pagado_proforma", "memo_pago", "tipo_vinculo",
                    ]
                ),
                targets=pd.DataFrame(
                    [
                        {
                            "periodo": "2026-01-01", "ejecutivo": "EJ1",
                            "linea_comision": "RETIE/LAP", "meta": 800, "piso": 600,
                        },
                        {
                            "periodo": "2026-01-01", "ejecutivo": "EJ1",
                            "linea_comision": "RITEL", "meta": 1000, "piso": 900,
                        },
                    ]
                ),
                costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
                cuts=pd.DataFrame(
                    {
                        "inicio_corte": ["2026-02-01"], "fin_corte": ["2026-02-28"],
                        "periodo_corte": ["2026-02"],
                    }
                ),
            )
        )

        compliance = result.floor_compliance.set_index("linea_comision")
        self.assertTrue(bool(compliance.loc["RETIE/LAP", "cumple"]))
        for line in ("RITEL", "LMA", "NA"):
            self.assertTrue(bool(compliance.loc[line, "cumple"]))
            self.assertTrue(
                bool(compliance.loc[line, "habilitada_por_linea_principal"])
            )
            self.assertTrue(bool(compliance.loc[line, "habilitada_por_retie"]))

        detail = result.commission_detail.set_index("tranId")
        for document in ("SM-REF-RETIE", "SM-REF-RITEL", "SM-REF-LMA"):
            row = detail.loc[document]
            self.assertEqual("PAGA 100%", row["decision_pago"])
            self.assertEqual(4, row["porcentaje_comision_ejecutivo"])
            self.assertEqual(1, row["porcentaje_comision_jefe"])
            self.assertEqual(2, row["porcentaje_comision_gerente"])

        na = detail.loc["SM-REF-NA"]
        self.assertEqual("suma", na["estado_piso"])
        self.assertEqual("PAGO EN CERO", na["decision_pago"])
        self.assertEqual(0, na["factor_comision_final"])
        self.assertEqual(0, na["valor_comision_ejecutivo"])
        self.assertEqual(0, na["valor_comision_jefe"])
        self.assertEqual(0, na["valor_comision_gerente"])

    def test_foreign_currency_keeps_transaction_balance_and_local_commission_consistent(self) -> None:
        invoice = transaction("SM-REF-FX", 901, "CustInvc", 400000)
        invoice["valor_total_documento"] = 100
        invoice["valor_moneda_transaccion"] = 100
        invoice["valor_moneda_local"] = 400000
        invoice["foreignamountunpaid"] = 0
        invoice["moneda_id"] = 2
        invoice["Valor"] = 400000
        invoices = pd.DataFrame([invoice])
        result = calculate_commissions(
            CommissionInputs(
                invoices=invoices,
                proformas=invoices.iloc[0:0].copy(),
                credit_notes=invoices.iloc[0:0].copy(),
                proforma_cancellations=pd.DataFrame(),
                invoice_payments=pd.DataFrame(
                    {
                        "factura_id": [901], "factura": ["SM-REF-FX"],
                        "fecha_factura": ["2026-01-01"], "pago_id": [902],
                        "documento_pago": ["RC-REF-FX"], "tipo_pago": ["CustPymt"],
                        "fecha_contabilizacion": ["2026-02-02"],
                        "fecha_real": ["2026-02-01"], "fecha_pago": ["2026-02-01"],
                        "valor_pago": [100], "memo_pago": ["PAGO USD"],
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
                targets=pd.DataFrame(
                    {
                        "periodo": ["2026-01-01"], "ejecutivo": ["EJ1"],
                        "linea_comision": ["RETIE/LAP"], "meta": [300000],
                        "piso": [250000],
                    }
                ),
                costs=pd.DataFrame({"lote": ["L1"], "Valor": [0]}),
                cuts=pd.DataFrame(
                    {
                        "inicio_corte": ["2026-02-01"], "fin_corte": ["2026-02-28"],
                        "periodo_corte": ["2026-02"],
                    }
                ),
            )
        )

        payment = result.payment_base.documents.set_index("documento").loc["SM-REF-FX"]
        self.assertEqual(100, payment["valor_total_transaccion"])
        self.assertEqual(400000, payment["valor_total_local"])
        self.assertEqual(4000, payment["tasa_documento"])
        self.assertEqual(100, payment["total_pagado_transaccion"])
        self.assertEqual(0, payment["saldo_actual_transaccion"])
        self.assertEqual(0, payment["saldo_actual_local"])

        detail = result.commission_detail.set_index("tranId").loc["SM-REF-FX"]
        self.assertEqual(400000, detail["Pagado"])
        self.assertEqual(0, detail["Saldo"])
        self.assertEqual("PAGA 100%", detail["decision_pago"])
        self.assertEqual(16000, detail["valor_comision_ejecutivo"])
        self.assertEqual(4000, detail["valor_comision_jefe"])
        self.assertEqual(8000, detail["valor_comision_gerente"])
        functional = InMemoryCalculationResultProvider(
            "fx-reference", result
        ).commission_results("fx-reference").iloc[0]
        self.assertEqual(400000, functional["document_value"])


if __name__ == "__main__":
    unittest.main()
