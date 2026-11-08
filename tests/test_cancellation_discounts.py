from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

from src.cancellation_discounts import (
    DISCOUNT_APPLIED,
    DISCOUNT_PARTIAL,
    SqliteCancellationDiscountRepository,
    build_cancellation_discounts,
)


def cancellations():
    return pd.DataFrame(
        {
            "tranId": ["SM1"],
            "tranId_nc": ["NC1"],
            "entityid": ["EJ1"],
            "periodo_recalculo": ["2026-01"],
            "requiere_recalculo_piso": [True],
            "factura_reemplazo_tranId_nc": [pd.NA],
        }
    )


def compliance(complies):
    return pd.DataFrame(
        {
            "entityid": ["EJ1"],
            "periodo_mes": ["2026-01"],
            "linea_comision": ["RETIE/LAP"],
            "cumple": [complies],
        }
    )


def paid_commissions():
    return pd.DataFrame(
        {
            "executive": ["EJ1", "EJ1"],
            "period": ["2026-01", "2026-01"],
            "commission_line": ["RETIE/LAP", "RETIE/LAP"],
            "document": ["SM1", "SM2"],
            "commission_executive": [40, 20],
            "commission_manager": [10, 5],
            "commission_director": [20, 10],
        }
    )


class CancellationDiscountsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = SqliteCancellationDiscountRepository(
            Path(self.temp_dir.name) / "discounts.db"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _build(self, after=False):
        return build_cancellation_discounts(
            cancellations(),
            compliance(True),
            compliance(after),
            paid_commissions(),
            detection_period="2026-08",
            source_calculation="calc-original",
            recalculation="calc-recalculated",
        )

    def test_floor_loss_generates_full_historical_debt(self):
        actual = self._build().iloc[0]

        self.assertEqual("SM1", actual["cancelled_documents"])
        self.assertEqual("NC1", actual["credit_notes"])
        self.assertEqual("SM1,SM2", actual["paid_documents"])
        self.assertEqual(60, actual["commission_executive"])
        self.assertEqual(15, actual["commission_manager"])
        self.assertEqual(30, actual["commission_director"])
        self.assertEqual(105, actual["total_debt"])
        self.assertEqual(105, actual["balance"])

    def test_no_debt_when_executive_still_complies(self):
        self.assertTrue(self._build(after=True).empty)

    def test_replacement_prevents_discount_candidate(self):
        data = cancellations()
        data.loc[0, "factura_reemplazo_tranId_nc"] = "SM3"
        actual = build_cancellation_discounts(
            data, compliance(True), compliance(False), paid_commissions(),
            "2026-08", "calc-original", "calc-recalculated",
        )
        self.assertTrue(actual.empty)

    def test_save_is_idempotent_and_partial_applications_keep_balance(self):
        discounts = self._build()
        first = self.repository.save(discounts, "system")
        second = self.repository.save(discounts, "system")
        self.assertEqual(first, second)

        discount_id = first[0]
        self.repository.apply(discount_id, 40, "2026-08", "reviewer", "Nomina agosto")
        debts = self.repository.list_discounts()
        applications = self.repository.list_applications("2026-08")
        self.assertEqual(105, debts.iloc[0]["total_debt"])
        self.assertEqual(40, debts.iloc[0]["applied_amount"])
        self.assertEqual(65, debts.iloc[0]["balance"])
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            view_type = connection.execute(
                "SELECT type FROM sqlite_master WHERE name = 'vw_discount_applications'"
            ).fetchone()[0]
        self.assertEqual("view", view_type)
        self.assertEqual(1, len(applications))
        self.assertEqual("EJ1", applications.iloc[0]["executive"])
        self.assertEqual("2026-08", applications.iloc[0]["period"])
        self.assertEqual(40, applications.iloc[0]["amount"])
        self.assertEqual("2026-01", applications.iloc[0]["original_period"])
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            partial = connection.execute(
                "SELECT total_debt, applied_amount, balance, status "
                "FROM cancellation_discounts WHERE id = ?",
                (discount_id,),
            ).fetchone()
        self.assertEqual((105, 40, 65, DISCOUNT_PARTIAL), partial)

        self.repository.apply(discount_id, 65, "2026-09", "reviewer", "Saldo final")
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            final = connection.execute(
                "SELECT applied_amount, balance, status FROM cancellation_discounts WHERE id = ?",
                (discount_id,),
            ).fetchone()
            applications = connection.execute(
                "SELECT COUNT(*) FROM cancellation_discount_applications"
            ).fetchone()[0]
        self.assertEqual((105, 0, DISCOUNT_APPLIED), final)
        self.assertEqual(2, applications)

    def test_list_applications_does_not_expose_pending_debt_as_discount(self):
        self.repository.save(self._build(), "system")

        self.assertTrue(self.repository.list_applications().empty)

    def test_application_cannot_exceed_debt(self):
        discount_id = self.repository.save(self._build(), "system")[0]
        with self.assertRaisesRegex(ValueError, "supera"):
            self.repository.apply(
                discount_id, 106, "2026-08", "reviewer", "Valor incorrecto"
            )

    def test_discount_storage_uses_explicit_columns_without_json_copy(self):
        discount_id = self.repository.save(self._build(), "system")[0]
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(cancellation_discounts)"
                ).fetchall()
            }
            stored = connection.execute(
                """
                SELECT cancelled_documents, credit_notes, commission_executive,
                       commission_manager, commission_director, total_debt
                FROM cancellation_discounts WHERE id = ?
                """,
                (discount_id,),
            ).fetchone()
        self.assertNotIn("detail_json", columns)
        self.assertEqual(("SM1", "NC1", 60, 15, 30, 105), stored)


if __name__ == "__main__":
    unittest.main()
