from __future__ import annotations

import json
import os
from tempfile import TemporaryDirectory
import unittest
from pathlib import Path

import pandas as pd

from src.engine import CommissionInputs, calculate_commissions
from src.regression import (
    DEFAULT_DATASETS,
    build_snapshot,
    check_bundle_against_snapshot,
    compare_snapshots,
    load_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "regression_baseline_2026-10-08.json"
FINANCIAL_REFERENCE = ROOT / "tests" / "fixtures" / "financial_reference_v1.json"
BUNDLE = ROOT / "app_data"


class RegressionBaselineTest(unittest.TestCase):
    def test_bundle_check_rejects_a_different_cut_before_comparing_values(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            bundle.mkdir()
            baseline = root / "baseline.json"
            baseline.write_text(
                json.dumps(
                    {
                        "source_generated_at": "2026-10-08T09:11:37",
                        "documents_requested": [],
                    }
                ),
                encoding="utf-8",
            )
            (bundle / "manifest.json").write_text(
                json.dumps({"generated_at": "2026-10-09T10:39:05"}),
                encoding="utf-8",
            )

            result = check_bundle_against_snapshot(bundle, baseline)

        self.assertEqual("CUT_MISMATCH", result["status"])
        self.assertEqual("2026-10-08T09:11:37", result["expected_cut"])
        self.assertEqual("2026-10-09T10:39:05", result["actual_cut"])
        self.assertEqual([], result["differences"])

    def test_modular_engine_matches_mandatory_financial_reference(self) -> None:
        reference = load_snapshot(FINANCIAL_REFERENCE)
        inputs = reference["inputs"]
        invoices = pd.DataFrame(inputs["invoices"])
        result = calculate_commissions(
            CommissionInputs(
                invoices=invoices,
                proformas=pd.DataFrame(inputs["proformas"]),
                credit_notes=invoices.iloc[0:0].copy(),
                proforma_cancellations=pd.DataFrame(),
                invoice_payments=pd.DataFrame(inputs["invoice_payments"]),
                proforma_payments=pd.DataFrame(inputs["proforma_payments"]),
                targets=pd.DataFrame(inputs["targets"]),
                costs=pd.DataFrame(inputs["costs"]),
                cuts=pd.DataFrame(inputs["cuts"]),
            )
        )

        expected = reference["expected"]
        actual_stage_rows = {
            "sales_base": len(result.sales_base),
            "sales_with_credit_notes": len(result.sales_with_credit_notes),
            "sales_with_origins": len(result.sales_with_origins),
            "sales_floor": len(result.sales_floor),
            "floor_compliance": len(result.floor_compliance),
            "payment_history": len(result.payment_base.history),
            "payment_documents": len(result.payment_base.documents),
            "commission_detail": len(result.commission_detail),
        }
        self.assertEqual(expected["stage_rows"], actual_stage_rows)

        floor = result.floor_compliance.iloc[0]
        expected_floor = expected["floor"]
        self.assertEqual(expected_floor["period"], str(floor["periodo_mes"]))
        self.assertEqual(expected_floor["executive"], floor["entityid"])
        self.assertEqual(expected_floor["line"], floor["linea_comision"])
        self.assertAlmostEqual(expected_floor["total_for_floor"], floor["TotalParaPiso"])
        self.assertAlmostEqual(expected_floor["target"], floor["meta"])
        self.assertAlmostEqual(expected_floor["floor"], floor["piso"])
        self.assertEqual(expected_floor["complies"], bool(floor["cumple"]))

        detail = result.commission_detail.set_index("tranId")
        for document, expected_document in expected["documents"].items():
            actual = detail.loc[document]
            self.assertEqual(expected_document["decision"], actual["decision_pago"])
            self.assertAlmostEqual(expected_document["factor"], actual["factor_comision_final"])
            self.assertAlmostEqual(
                expected_document["executive_commission"],
                actual["valor_comision_ejecutivo"],
            )
            self.assertAlmostEqual(
                expected_document["manager_commission"], actual["valor_comision_jefe"]
            )
            self.assertAlmostEqual(
                expected_document["director_commission"], actual["valor_comision_gerente"]
            )
            self.assertEqual(
                expected_document["real_payment_date"],
                actual["Fecha Real Pago"].date().isoformat(),
            )
            self.assertEqual(
                expected_document["accounting_payment_date"],
                actual["Fecha Contabilizado Pago"].date().isoformat(),
            )
            self.assertEqual(expected_document["cut_period"], actual["Periodo Corte"])

        totals = expected["totals"]
        self.assertAlmostEqual(
            totals["executive_commission"],
            result.commission_detail["valor_comision_ejecutivo"].sum(),
        )
        self.assertAlmostEqual(
            totals["manager_commission"],
            result.commission_detail["valor_comision_jefe"].sum(),
        )
        self.assertAlmostEqual(
            totals["director_commission"],
            result.commission_detail["valor_comision_gerente"].sum(),
        )

    def test_historical_snapshot_contract_is_versioned_and_complete(self) -> None:
        expected = load_snapshot(BASELINE)

        self.assertTrue(expected.get("source_generated_at"))
        self.assertEqual(
            set(DEFAULT_DATASETS), set(expected.get("dataset_controls", {}))
        )
        self.assertTrue(expected.get("documents_requested"))
        self.assertEqual(
            len(expected["documents_requested"]),
            len(set(expected["documents_requested"])),
        )
        for dataset_name, controls in expected["dataset_controls"].items():
            self.assertIn("rows", controls, dataset_name)
            self.assertIn("columns", controls, dataset_name)

    def test_local_bundle_matches_historical_snapshot(self) -> None:
        if os.getenv("COMMISSION_COMPARE_LOCAL_BUNDLE") != "1":
            return
        self.assertTrue((BUNDLE / "manifest.json").exists(), "Local bundle is missing")
        expected = load_snapshot(BASELINE)
        manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
        expected_cut = expected.get("source_generated_at")
        actual_cut = manifest.get("generated_at")
        self.assertEqual(
            expected_cut,
            actual_cut,
            "The local bundle is not the frozen historical cut. Regenerate the "
            "matching cut or create a separately approved baseline.",
        )
        actual = build_snapshot(BUNDLE, expected["documents_requested"])
        differences = compare_snapshots(expected, actual)
        formatted = "\n".join(
            f"{item.scope}: expected={item.expected!r}, actual={item.actual!r}"
            for item in differences
        )
        self.assertEqual([], differences, formatted)


if __name__ == "__main__":
    unittest.main()
