from __future__ import annotations

import unittest

import pandas as pd

from src.floor_compliance import build_floor_compliance


def sale(line_id, value, *, executive="EJ1", period="2026-01-15", lot="L1", state="suma"):
    return {
        "tranDate": period,
        "periodo_factura": pd.Period(period[:7], freq="M"),
        "entityid": executive,
        "custitem_sm_lineacomision": line_id,
        "TotalParaPiso_final": value,
        "lote": lot,
        "estado_piso": state,
    }


def target(line, meta=100, floor=80, *, executive="EJ1", period="2026-01-01"):
    return {
        "periodo": period,
        "ejecutivo": executive,
        "linea_comision": line,
        "meta": meta,
        "piso": floor,
    }


class FloorComplianceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.costs = pd.DataFrame({"lote": ["L1"], "Valor": [30]})

    def test_retie_enables_ritel_and_unconfigured_line(self) -> None:
        sales = pd.DataFrame([sale(10, 120), sale(15, 10), sale(19, 5)])
        targets = pd.DataFrame([target("RETIE/LAP"), target("RITEL")])
        actual = build_floor_compliance(sales, targets, self.costs).set_index(
            "linea_comision"
        )
        self.assertTrue(bool(actual.loc["RETIE/LAP", "cumple"]))
        self.assertTrue(bool(actual.loc["RITEL", "cumple"]))
        self.assertTrue(bool(actual.loc["NA", "cumple"]))
        self.assertTrue(bool(actual.loc["RITEL", "habilitada_por_retie"]))
        self.assertTrue(bool(actual.loc["NA", "habilitada_por_retie"]))

    def test_ritel_alone_does_not_enable_other_lines(self) -> None:
        sales = pd.DataFrame([sale(15, 120), sale(2, 10)])
        targets = pd.DataFrame([target("RITEL"), target("LMA")])
        actual = build_floor_compliance(sales, targets, self.costs).set_index(
            "linea_comision"
        )
        self.assertTrue(bool(actual.loc["RITEL", "cumple"]))
        self.assertFalse(bool(actual.loc["LMA", "cumple"]))
        self.assertFalse(bool(actual.loc["LMA", "habilitada_por_linea_principal"]))

    def test_any_non_ritel_configured_line_can_enable_others(self) -> None:
        sales = pd.DataFrame([sale(2, 100), sale(19, 1)])
        targets = pd.DataFrame([target("LMA")])
        actual = build_floor_compliance(sales, targets, self.costs).set_index(
            "linea_comision"
        )
        self.assertTrue(bool(actual.loc["NA", "cumple"]))
        self.assertTrue(bool(actual.loc["NA", "habilitada_por_linea_principal"]))
        self.assertFalse(bool(actual.loc["NA", "habilitada_por_retie"]))

    def test_unconfigured_period_requires_review(self) -> None:
        sales = pd.DataFrame([sale(10, 120, period="2025-12-15")])
        targets = pd.DataFrame([target("RETIE/LAP")])
        actual = build_floor_compliance(sales, targets, self.costs).iloc[0]
        self.assertTrue(bool(actual["requiere_revision_cumplimiento"]))
        self.assertIn("Sin configuración", actual["motivo_revision_cumplimiento"])

    def test_duplicate_target_is_rejected(self) -> None:
        sales = pd.DataFrame([sale(10, 120)])
        targets = pd.DataFrame([target("RETIE/LAP"), target("RETIE/LAP")])
        with self.assertRaisesRegex(ValueError, "duplicados"):
            build_floor_compliance(sales, targets, self.costs)

    def test_cost_is_counted_once_per_lot_and_does_not_change_compliance(self) -> None:
        sales = pd.DataFrame([sale(10, 90), sale(10, 5)])
        targets = pd.DataFrame([target("RETIE/LAP", meta=100, floor=90)])
        actual = build_floor_compliance(sales, targets, self.costs).iloc[0]
        self.assertEqual(30, actual["costo"])
        self.assertEqual(95, actual["TotalParaPiso"])
        self.assertTrue(bool(actual["cumple"]))
        self.assertEqual("Cumple piso", actual["observacion_cumple"])


if __name__ == "__main__":
    unittest.main()
