import unittest

import pandas as pd

from src.access_control import AccessDeniedError, PERM_CALCULATION_EXECUTE
from src.engine import CommissionResult
from src.payment_base import OfficialPaymentBase
from src.reporting import (
    CalculationReviewDocumentValidator,
    CalculationViewService,
    InMemoryCalculationResultProvider,
)


def result_fixture():
    detail = pd.DataFrame(
        {
            "tranId": ["SM1", "SM2", "SM3"],
            "entityid": ["EJ1", "EJ1", "EJ2"],
            "periodo_mes": ["2026-01", "2026-01", "2026-02"],
            "linea_comision_key": ["RETIE/LAP", "RITEL", "PRODUCTO"],
            "decision_pago": ["PAGA 100%", "PARA REVISAR", "NO PAGA"],
            "valor_comision_ejecutivo": [40.0, 20.0, 0.0],
            "valor_comision_jefe": [10.0, 5.0, 0.0],
            "valor_comision_gerente": [20.0, 10.0, 0.0],
            "valor_total_documento": [1000.0, 500.0, 800.0],
            "valor_moneda_local": [4000.0, 2000.0, 3200.0],
            "Fecha Real Pago": ["2026-02-01", "2026-02-10", pd.NaT],
            "Fecha Contabilizado Pago": ["2026-02-02", "2026-02-11", pd.NaT],
            "motivo_pago": ["Pago oportuno", "Validar fecha", "Pendiente"],
            "tranDate": ["2026-01-05", "2026-01-06", "2026-02-01"],
            "documento_origen": ["SM0", "SM2", "SM3"],
            "fecha_origen": ["2025-12-20", "2026-01-06", "2026-02-01"],
            "tranId_nc": ["NC1", pd.NA, pd.NA],
            "tranDate_nc": ["2026-01-20", pd.NaT, pd.NaT],
            "factura_reemplazo_tranId_nc": ["SM1", pd.NA, pd.NA],
            "fecha_reemplazo_nc": ["2026-01-05", pd.NaT, pd.NaT],
            "lote": ["L1", "L2", "L3"],
            "Servicio": ["Ensayo", "Inspección", "Producto"],
            "Pagado Con:": ["RC-1", "RC-2", pd.NA],
            "Pagado Con (Tipo)": ["PAGO", "PAGO", pd.NA],
            "Pagado": [4000.0, 1900.0, 0.0],
            "dias_pago": [10, 35, 0],
            "factor": [1.0, 0.5, 0.0],
        }
    )
    floor = pd.DataFrame(
        {
            "entityid": ["EJ1", "EJ2"],
            "periodo_mes": ["2026-01", "2026-02"],
            "linea_comision": ["RETIE/LAP", "PRODUCTO"],
            "cumple": [True, False],
            "TotalParaPiso": [1500.0, 800.0],
            "meta": [1200.0, 1000.0],
            "piso": [900.0, 900.0],
            "observacion_cumple": ["Cumple", "No alcanza piso"],
        }
    )
    sales_floor = pd.DataFrame(
        {
            "tranId": ["SM1", "SM4"],
            "entityid": ["EJ1", "EJ1"],
            "periodo_mes": ["2026-01", "2026-01"],
            "linea_comision_key": ["RETIE/LAP", "RETIE/LAP"],
            "valor_total_documento": [1000.0, 700.0],
            "valor_moneda_local": [4000.0, 2800.0],
            "estado_piso": ["suma", "suma"],
            "observacion_piso": ["Suma por emisión", "Suma aunque no tiene pago"],
            "tranDate": ["2026-01-05", "2026-01-12"],
        }
    )
    empty = pd.DataFrame()
    payments = OfficialPaymentBase(empty, empty, empty)
    return CommissionResult(empty, empty, empty, sales_floor, floor, payments, detail)


class ReportingTest(unittest.TestCase):
    def setUp(self):
        self.provider = InMemoryCalculationResultProvider("calc-1", result_fixture())

    def test_reports_are_derived_without_files_or_duplicate_datasets(self):
        payments = self.provider.commission_results("calc-1")
        review = self.provider.review_results("calc-1")

        self.assertEqual(3, len(payments))
        self.assertEqual(["SM2"], review["document"].tolist())
        self.assertEqual("EJ1", payments.iloc[0]["executive"])

    def test_functional_views_expose_document_values_in_local_currency(self):
        payments = self.provider.commission_results("calc-1")
        floor_documents = self.provider.document_compliance_results("calc-1")

        self.assertEqual([4000.0, 2000.0, 3200.0], payments["document_value"].tolist())
        self.assertEqual([4000.0, 2800.0], floor_documents["document_value"].tolist())

    def test_payment_detail_exposes_traceability_and_keeps_unpaid_values_empty(self):
        payments = self.provider.commission_results("calc-1")
        paid = payments.loc[payments["document"].eq("SM1")].iloc[0]
        unpaid = payments.loc[payments["document"].eq("SM3")].iloc[0]

        self.assertEqual("SM0", paid["document_origin"])
        self.assertEqual("NC1", paid["credit_note"])
        self.assertEqual("RC-1", paid["payment_document"])
        self.assertEqual(4000.0, paid["paid_value"])
        self.assertTrue(pd.isna(unpaid["paid_value"]))
        self.assertTrue(pd.isna(unpaid["payment_factor"]))

    def test_review_context_is_attached_without_duplicating_documents(self):
        class Context:
            def document_review_context(self):
                return [{
                    "executive": "EJ1", "period": "2026-01", "document": "SM2",
                    "request_id": 7, "request_status": "RESUELTA",
                    "request_final_concept": "Pago confirmado",
                }]

        provider = InMemoryCalculationResultProvider("calc-1", result_fixture(), Context())
        payments = provider.commission_results("calc-1")
        item = payments.loc[payments["document"].eq("SM2")].iloc[0]

        self.assertEqual(3, len(payments))
        self.assertEqual(7, item["request_id"])
        self.assertEqual("Pago confirmado", item["request_final_concept"])

    def test_payment_summary_is_an_aggregation_with_discounts(self):
        discounts = pd.DataFrame(
            {"executive": ["EJ1"], "period": ["2026-01"], "amount": [15.0]}
        )
        summary = self.provider.payment_summary("calc-1", discounts)
        ej1 = summary.loc[summary["executive"].eq("EJ1")].iloc[0]

        self.assertEqual(60.0, ej1["commission"])
        self.assertEqual(15.0, ej1["discounts"])
        self.assertEqual(45.0, ej1["payable"])
        self.assertEqual(2, ej1["documents"])

    def test_floor_report_uses_canonical_names(self):
        floor = self.provider.floor_results("calc-1")
        self.assertEqual(
            ["executive", "period", "commission_line", "complies",
             "result_value", "target", "floor", "concept"],
            floor.columns.tolist(),
        )

    def test_document_compliance_includes_documents_without_payment(self):
        documents = self.provider.document_compliance_results("calc-1")

        self.assertEqual(["SM1", "SM4"], documents["document"].tolist())
        unpaid = documents.loc[documents["document"].eq("SM4")].iloc[0]
        self.assertTrue(unpaid["adds_to_floor"])
        self.assertEqual("Suma aunque no tiene pago", unpaid["floor_reason"])

    def test_document_compliance_uses_available_period_fallback(self):
        result = result_fixture()
        result.sales_floor["periodo_factura"] = result.sales_floor["periodo_mes"]
        result.sales_floor["periodo_mes"] = pd.NA
        provider = InMemoryCalculationResultProvider("calc-period", result)

        documents = provider.document_compliance_results("calc-period")

        self.assertEqual(["2026-01", "2026-01"], documents["period"].tolist())

    def test_unknown_run_is_rejected(self):
        with self.assertRaises(KeyError):
            self.provider.commission_results("missing")

    def test_view_service_materializes_only_after_explicit_refresh(self):
        class Execution:
            run_code = "calc-current"
            result = result_fixture()

        class Calculator:
            def __init__(self):
                self.calls = []

            def execute_approved_inputs(self, **kwargs):
                self.calls.append(kwargs)
                return Execution()

            def load_approved_result(self, period):
                self.calls.append({"approved_period": period})
                return Execution()

        calculator = Calculator()
        service = CalculationViewService(calculator)

        provider = service.refresh("reviewer-1", "2026-01")

        self.assertEqual(1, len(calculator.calls))
        self.assertTrue(calculator.calls[0]["materialize_reused_result"])
        self.assertEqual("2026-01", calculator.calls[0]["period"])
        self.assertEqual(3, len(provider.commission_results("calc-current")))

    def test_view_service_rejects_blank_period_before_calling_calculator(self):
        class Calculator:
            def __init__(self):
                self.calls = []

            def execute_approved_inputs(self, **kwargs):
                self.calls.append(kwargs)

        calculator = Calculator()
        service = CalculationViewService(calculator)

        with self.assertRaisesRegex(ValueError, "periodo es obligatorio"):
            service.load_existing("reviewer-1", " ")

        self.assertEqual([], calculator.calls)

    def test_view_service_rejects_invalid_period_before_calling_calculator(self):
        class Calculator:
            def __init__(self):
                self.calls = []

            def execute_approved_inputs(self, **kwargs):
                self.calls.append(kwargs)

        calculator = Calculator()
        service = CalculationViewService(calculator)

        with self.assertRaisesRegex(ValueError, "YYYY-MM"):
            service.refresh("reviewer-1", "09/2026")

        self.assertEqual([], calculator.calls)

    def test_refresh_requires_permission_but_existing_query_does_not_recalculate(self):
        class Execution:
            run_code = "calc-existing"
            result = result_fixture()

        class Calculator:
            def __init__(self):
                self.calls = []

            def execute_approved_inputs(self, **kwargs):
                self.calls.append(kwargs)
                return Execution()

            def load_approved_result(self, period):
                self.calls.append({"approved_period": period})
                return Execution()

        class Authorization:
            def __init__(self):
                self.calls = []

            def require_permission(self, user_id, permission):
                self.calls.append((user_id, permission))

            def require_any_role(self, user_id, roles):
                self.calls.append((user_id, frozenset(roles)))

        calculator = Calculator()
        authorization = Authorization()
        service = CalculationViewService(calculator, None, authorization)

        service.load_existing("12", "2026-01")
        self.assertEqual("2026-01", calculator.calls[0]["approved_period"])
        self.assertEqual([], authorization.calls)

        service.refresh("12", "2026-01")
        self.assertEqual(PERM_CALCULATION_EXECUTE, authorization.calls[0][1])
        self.assertEqual(12, authorization.calls[1][0])
        self.assertFalse(calculator.calls[1]["require_existing"])

    def test_refresh_rejects_role_outside_controlled_calculation_roles(self):
        class Calculator:
            def execute_approved_inputs(self, **kwargs):
                raise AssertionError("El calculo no debe ejecutarse")

        class Authorization:
            def require_permission(self, user_id, permission):
                return None

            def require_any_role(self, user_id, roles):
                raise AccessDeniedError("perfil no autorizado")

        service = CalculationViewService(Calculator(), None, Authorization())

        with self.assertRaisesRegex(AccessDeniedError, "perfil no autorizado"):
            service.refresh("12", "2026-01")

    def test_review_document_validator_uses_approved_calculation_ownership(self):
        class Execution:
            run_code = "calc-existing"
            result = result_fixture()

        class Calculator:
            def load_approved_result(self, period):
                return Execution()

        validator = CalculationReviewDocumentValidator(
            CalculationViewService(Calculator())
        )

        validator.validate_documents(12, "EJ1", "2026-01", ["SM1", "sm4"])
        with self.assertRaises(AccessDeniedError):
            validator.validate_documents(12, "EJ1", "2026-01", ["SM3"])


if __name__ == "__main__":
    unittest.main()
