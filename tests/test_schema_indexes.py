from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from src.access_control import SqliteAccessRepository
from src.cancellation_discounts import SqliteCancellationDiscountRepository
from src.payment_files import SqlitePaymentFileRepository
from src.review_workflows import SqliteReviewRepository


class SchemaIndexesTest(unittest.TestCase):
    def test_operational_query_indexes_are_installed(self) -> None:
        expected = {
            "idx_hierarchy_source_effective",
            "idx_user_entity_effective",
            "idx_audit_occurred",
            "idx_audit_entity",
            "idx_review_item_document",
            "idx_review_status_history",
            "idx_exception_status_history",
            "idx_cancellation_discount_scope",
            "idx_discount_application_period",
            "idx_payment_download_batch",
            "idx_payment_send_batch",
        }

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "commissions.db"
            SqliteAccessRepository(database_path)
            SqliteReviewRepository(database_path)
            SqliteCancellationDiscountRepository(database_path)
            SqlitePaymentFileRepository(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                installed = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    )
                }

        self.assertTrue(expected.issubset(installed), expected - installed)


if __name__ == "__main__":
    unittest.main()
