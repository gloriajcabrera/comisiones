from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

from src.access_control import (
    AccessDeniedError,
    AuthorizationService,
    ROLE_EXECUTIVE,
    ROLE_PARAMETERIZER,
    ROLE_REVIEWER,
    SqliteAccessRepository,
)
from src.cut_periods_workflow import (
    APPROVED,
    CutPeriodWorkflowService,
    SqliteCutPeriodRepository,
)


def cuts(january_end="2026-01-31", include_february=True):
    rows = [
        {
            "inicio_corte": "2026-01-01",
            "fin_corte": january_end,
            "periodo_corte": "2026-01",
        }
    ]
    if include_february:
        rows.append(
            {
                "inicio_corte": "2026-02-01",
                "fin_corte": "2026-02-28",
                "periodo_corte": "2026-02",
            }
        )
    return pd.DataFrame(rows)


class CutPeriodsWorkflowTest(unittest.TestCase):
    def test_no_approved_cuts_returns_empty_frame_with_schema(self):
        actual = self.repository.load_approved_cuts()

        self.assertTrue(actual.empty)
        self.assertEqual(
            ["inicio_corte", "fin_corte", "periodo_corte"],
            actual.columns.tolist(),
        )

    def test_batch_queue_exposes_version_status_and_origin(self):
        batch = self.service.create_batch(
            cuts(include_february=False), self.reviewer, "cortes.csv", "Carga mensual"
        )

        queue = self.service.list_batches()

        self.assertEqual(batch.batch_id, queue[0]["batch_id"])
        self.assertEqual("cortes.csv", queue[0]["source_name"])
        self.assertEqual("BORRADOR", queue[0]["status"])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "cuts.db"
        self.access = SqliteAccessRepository(self.db_path)
        self.authorization = AuthorizationService(self.access)
        self.repository = SqliteCutPeriodRepository(self.db_path)
        self.service = CutPeriodWorkflowService(
            self.repository, self.authorization
        )
        self.reviewer = self._user("reviewer", ROLE_REVIEWER)
        self.parameterizer = self._user("parameterizer", ROLE_PARAMETERIZER)
        self.executive = self._user("executive", ROLE_EXECUTIVE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _user(self, name, role):
        user_id = self.access.create_user(name, name, "Test")
        self.access.assign_role(user_id, role)
        return user_id

    def approve(self, data, source="cortes.csv"):
        batch = self.service.create_batch(
            data, self.reviewer, source, "Calendario oficial"
        )
        self.service.submit(batch.batch_id, self.reviewer)
        self.service.verify(batch.batch_id, self.reviewer)
        self.service.request_approval(batch.batch_id, self.reviewer)
        return self.service.approve(batch.batch_id, self.parameterizer)

    def test_approved_cuts_are_loaded_from_database_with_full_history(self):
        batch = self.approve(cuts())

        self.assertEqual(APPROVED, batch.status)
        actual = self.repository.load_approved_cuts()
        self.assertEqual(["2026-01", "2026-02"], actual["periodo_corte"].tolist())
        with closing(sqlite3.connect(self.db_path)) as connection:
            history = connection.execute(
                "SELECT COUNT(*) FROM cut_period_status_history WHERE batch_id = ?",
                (batch.batch_id,),
            ).fetchone()[0]
        self.assertEqual(5, history)

    def test_partial_update_keeps_other_approved_periods(self):
        self.approve(cuts())
        self.approve(cuts("2026-01-25", include_february=False), "manual")

        actual = self.repository.load_approved_cuts().set_index("periodo_corte")
        self.assertEqual(pd.Timestamp("2026-01-25"), actual.loc["2026-01", "fin_corte"])
        self.assertEqual(pd.Timestamp("2026-02-28"), actual.loc["2026-02", "fin_corte"])

    def test_approval_rejects_overlap_with_existing_approved_period(self):
        self.approve(cuts())
        batch = self.service.create_batch(
            cuts("2026-02-05", include_february=False), self.reviewer
        )
        self.service.submit(batch.batch_id, self.reviewer)
        self.service.verify(batch.batch_id, self.reviewer)
        self.service.request_approval(batch.batch_id, self.reviewer)

        with self.assertRaisesRegex(ValueError, "superpuestos"):
            self.service.approve(batch.batch_id, self.parameterizer)

    def test_executive_cannot_register_cut_periods(self):
        with self.assertRaises(AccessDeniedError):
            self.service.create_batch(cuts(), self.executive)


if __name__ == "__main__":
    unittest.main()
