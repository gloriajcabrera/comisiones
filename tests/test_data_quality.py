import unittest

import pandas as pd

from src.data_quality import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    filter_netsuite_history,
    validate_netsuite_dataset,
)
from src.netsuite_provider import NetSuiteDataset


def dataset_with(invoices):
    empty = pd.DataFrame()
    return NetSuiteDataset(
        invoices=invoices,
        costs=empty.copy(),
        proformas=empty.copy(),
        credit_notes=empty.copy(),
        proforma_cancellations=empty.copy(),
        invoice_payments=empty.copy(),
        proforma_payments=empty.copy(),
    )


class DataQualityTest(unittest.TestCase):
    def test_history_filter_preserves_only_documents_from_2023(self):
        empty = pd.DataFrame()
        dataset = NetSuiteDataset(
            invoices=pd.DataFrame({
                "id": [1, 2], "tranDate": ["2022-12-31", "2023-01-01"]
            }),
            costs=empty.copy(),
            proformas=empty.copy(),
            credit_notes=empty.copy(),
            proforma_cancellations=empty.copy(),
            invoice_payments=pd.DataFrame({
                "factura_id": [1, 2],
                "fecha_factura": ["2022-12-31", "2023-01-01"],
            }),
            proforma_payments=empty.copy(),
        )

        filtered = filter_netsuite_history(dataset)

        self.assertEqual([2], filtered.invoices["id"].tolist())
        self.assertEqual([2], filtered.invoice_payments["factura_id"].tolist())

    def test_missing_or_null_identity_is_blocking(self):
        report = validate_netsuite_dataset(dataset_with(pd.DataFrame({
            "id": [1, None], "line_id": [None, 2]
        })))

        self.assertEqual(1, len(report.errors))
        self.assertEqual(SEVERITY_ERROR, report.errors[0].severity)
        self.assertEqual(2, report.errors[0].affected_rows)
        with self.assertRaisesRegex(ValueError, "NULL_IDENTITY"):
            report.require_valid()

    def test_duplicate_identity_and_invalid_date_are_warnings(self):
        report = validate_netsuite_dataset(dataset_with(pd.DataFrame({
            "id": [1, 1],
            "line_id": [10, 10],
            "tranDate": ["2026-01-01", "fecha mala"],
        })))

        self.assertFalse(report.errors)
        self.assertEqual(2, len(report.warnings))
        self.assertTrue(all(
            issue.severity == SEVERITY_WARNING for issue in report.warnings
        ))
        report.require_valid()

    def test_valid_dataset_has_no_issues(self):
        report = validate_netsuite_dataset(dataset_with(pd.DataFrame({
            "id": [1], "line_id": [10], "tranDate": ["2026-01-01"],
            "entityid": ["EJ1"], "custitem_sm_lineacomision": ["RETIE/LAP"],
        })))

        self.assertFalse(report.issues)

    def test_document_business_warnings_are_classified_without_blocking(self):
        report = validate_netsuite_dataset(dataset_with(pd.DataFrame({
            "id": [1, 1],
            "line_id": [10, 11],
            "tranId": ["", ""],
            "valor_total_documento": ["1000", "valor malo"],
            "dueDate": ["2026-02-01", "2026-02-02"],
        })))

        codes = {issue.code for issue in report.warnings}
        self.assertEqual(
            {
                "NULL_DOCUMENT_NUMBER",
                "INVALID_NUMERIC_VALUE",
                "INCONSISTENT_DOCUMENT_VALUE",
            },
            codes,
        )
        report.require_valid()

    def test_payment_without_parent_document_is_warning(self):
        invoices = pd.DataFrame({"id": [1], "line_id": [10]})
        empty = pd.DataFrame()
        dataset = NetSuiteDataset(
            invoices=invoices,
            costs=empty.copy(),
            proformas=empty.copy(),
            credit_notes=empty.copy(),
            proforma_cancellations=empty.copy(),
            invoice_payments=pd.DataFrame({
                "factura_id": [2], "pago_id": [20], "tipo_pago": ["CustPymt"]
            }),
            proforma_payments=empty.copy(),
        )

        report = validate_netsuite_dataset(dataset)

        self.assertIn(
            "ORPHAN_DOCUMENT_REFERENCE",
            {issue.code for issue in report.warnings},
        )

    def test_payment_link_lines_distinguish_multiple_applications(self):
        empty = pd.DataFrame()
        dataset = NetSuiteDataset(
            invoices=pd.DataFrame({"id": [1], "line_id": [10]}),
            costs=empty.copy(),
            proformas=empty.copy(),
            credit_notes=empty.copy(),
            proforma_cancellations=empty.copy(),
            invoice_payments=pd.DataFrame({
                "factura_id": [1, 1],
                "pago_id": [20, 20],
                "tipo_pago": ["CustPymt", "CustPymt"],
                "previous_line": [0, 1],
                "next_line": [0, 1],
            }),
            proforma_payments=empty.copy(),
        )

        report = validate_netsuite_dataset(dataset)

        self.assertNotIn(
            "DUPLICATE_IDENTITY",
            {issue.code for issue in report.warnings},
        )

    def test_missing_executive_and_commission_line_are_review_warnings(self):
        report = validate_netsuite_dataset(dataset_with(pd.DataFrame({
            "id": [1, 1],
            "line_id": [10, 11],
            "tranId": ["SM1", "SM1"],
            "entityid": [None, "EJ1"],
            "custitem_sm_lineacomision": ["", "RETIE/LAP"],
            "custitem_sm_linea_serv": ["2", "2"],
            "itemType": ["Service", "Service"],
        })))

        issues = {issue.code: issue for issue in report.warnings}
        self.assertEqual(1, issues["MISSING_EXECUTIVE"].affected_rows)
        self.assertEqual(1, issues["MISSING_COMMISSION_LINE"].affected_rows)
        report.require_valid()

    def test_ineligible_line_does_not_raise_business_field_warnings(self):
        report = validate_netsuite_dataset(dataset_with(pd.DataFrame({
            "id": [1],
            "line_id": [10],
            "tranId": ["SM-EXCLUDED"],
            "Valor": [100],
            "entityid": [None],
            "custitem_sm_lineacomision": [None],
            "custitem_sm_linea_serv": ["18"],
            "itemType": ["Service"],
        })))

        codes = {issue.code for issue in report.warnings}
        self.assertNotIn("MISSING_EXECUTIVE", codes)
        self.assertNotIn("MISSING_COMMISSION_LINE", codes)

    def test_document_number_must_be_consistent_between_lines(self):
        report = validate_netsuite_dataset(dataset_with(pd.DataFrame({
            "id": [1, 1],
            "line_id": [10, 11],
            "tranId": ["SM1", "SM2"],
            "entityid": ["EJ1", "EJ1"],
            "custitem_sm_lineacomision": ["RETIE/LAP", "RETIE/LAP"],
        })))

        inconsistent = [
            issue for issue in report.warnings
            if issue.code == "INCONSISTENT_DOCUMENT_VALUE"
        ]
        self.assertEqual(1, len(inconsistent))
        self.assertIn("tranId", inconsistent[0].message)
        self.assertEqual(2, inconsistent[0].affected_rows)


if __name__ == "__main__":
    unittest.main()
