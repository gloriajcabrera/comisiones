from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

from src.access_control import (
    AccessDeniedError,
    AuthorizationService,
    ROLE_MANAGER,
    ROLE_PARAMETERIZER,
    ROLE_REVIEWER,
    SqliteAccessRepository,
)
from src.targets_workflow import (
    APPROVED,
    DRAFT,
    SqliteTargetsRepository,
    TargetsWorkflowService,
)


def target_rows(executive="EJ1", floor=800):
    return pd.DataFrame(
        {
            "periodo": ["2026-01-01"],
            "ejecutivo": [executive],
            "linea_comision": ["RETIE/LAP"],
            "meta": [1000],
            "piso": [floor],
            "comision_ejecutivo": [0.04],
        }
    )


class TargetsWorkflowTest(unittest.TestCase):
    def test_batch_queue_exposes_version_status_counts_and_origin(self):
        batch = self.service.create_batch(
            target_rows(), self.manager, "pisos-octubre.xlsx", "Carga mensual"
        )

        queue = self.service.list_batches()

        self.assertEqual(batch.batch_id, queue[0]["batch_id"])
        self.assertEqual("pisos-octubre.xlsx", queue[0]["source_name"])
        self.assertEqual("BORRADOR", queue[0]["status"])
        self.assertEqual(1, queue[0]["valid_count"])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "workflow.db"
        self.access = SqliteAccessRepository(self.db_path)
        self.authorization = AuthorizationService(self.access)
        self.repository = SqliteTargetsRepository(self.db_path)
        self.service = TargetsWorkflowService(self.repository, self.authorization)
        self.manager = self._user("manager", ROLE_MANAGER)
        self.reviewer = self._user("reviewer", ROLE_REVIEWER)
        self.parameterizer = self._user("parameterizer", ROLE_PARAMETERIZER)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _user(self, name, role):
        user_id = self.access.create_user(name, name, "Test")
        self.access.assign_role(user_id, role)
        return user_id

    def approve(self, data, source="targets.xlsx"):
        batch = self.service.create_batch(data, self.manager, source)
        self.service.submit(batch.batch_id, self.manager)
        self.service.verify(batch.batch_id, self.reviewer)
        self.service.request_approval(batch.batch_id, self.reviewer)
        return self.service.approve(batch.batch_id, self.parameterizer)

    def test_full_workflow_preserves_status_history(self):
        batch = self.approve(target_rows())
        self.assertEqual(batch.status, APPROVED)
        approved = self.repository.load_approved_targets()
        self.assertEqual(approved.loc[0, "ejecutivo"], "EJ1")
        self.assertEqual(approved.loc[0, "piso"], 800)
        with closing(sqlite3.connect(self.db_path)) as connection:
            history = connection.execute(
                "SELECT COUNT(*) FROM target_status_history WHERE batch_id = ?",
                (batch.batch_id,),
            ).fetchone()[0]
        self.assertEqual(history, 5)

    def test_partial_approved_update_keeps_other_business_keys(self):
        self.approve(pd.concat([target_rows("EJ1"), target_rows("EJ2")]))
        self.approve(target_rows("EJ1", floor=900), source="partial.xlsx")

        approved = self.repository.load_approved_targets().sort_values("ejecutivo")
        self.assertEqual(approved["ejecutivo"].tolist(), ["EJ1", "EJ2"])
        self.assertEqual(approved["piso"].tolist(), [900, 800])
        self.assertEqual(approved["version_insumo"].tolist(), [2, 1])

    def test_invalid_rows_are_recorded_and_cannot_be_submitted(self):
        invalid = target_rows()
        invalid["meta"] = invalid["meta"].astype("object")
        invalid.loc[0, "meta"] = "not-a-number"
        batch = self.service.create_batch(invalid, self.manager, "invalid.xlsx")

        self.assertEqual(batch.status, DRAFT)
        self.assertEqual(batch.valid_count, 0)
        self.assertEqual(batch.error_count, 1)
        with self.assertRaisesRegex(ValueError, "tiene errores"):
            self.service.submit(batch.batch_id, self.manager)

    def test_vectorized_validation_keeps_all_messages_per_invalid_row(self):
        invalid = pd.concat(
            [target_rows(""), target_rows("EJ2"), target_rows("EJ2")],
            ignore_index=True,
        )
        invalid.loc[0, "periodo"] = "periodo-invalido"
        invalid["meta"] = invalid["meta"].astype("object")
        invalid.loc[0, "meta"] = "no-numero"

        batch = self.service.create_batch(invalid, self.manager, "invalid-many.csv")

        self.assertEqual(0, batch.valid_count)
        self.assertEqual(3, batch.error_count)
        with closing(sqlite3.connect(self.db_path)) as connection:
            messages = [
                row[0] for row in connection.execute(
                    "SELECT messages_json FROM target_batch_errors "
                    "WHERE batch_id = ? ORDER BY row_number",
                    (batch.batch_id,),
                ).fetchall()
            ]
        self.assertIn("Periodo invalido", messages[0])
        self.assertIn("Meta no numerica", messages[0])
        self.assertIn("Ejecutivo vacio", messages[0])
        self.assertNotIn("duplicada", messages[0])
        self.assertTrue(all("duplicada" in value for value in messages[1:]))

    def test_actor_without_register_permission_cannot_create_batch(self):
        executive = self.access.create_user("executive", "Executive", "Test")
        with self.assertRaises(AccessDeniedError):
            self.service.create_batch(target_rows(), executive)

    def test_reviewer_can_load_target_input_but_cannot_approve_it(self):
        batch = self.service.create_batch(
            target_rows(), self.reviewer, "pisos.xlsx", "Carga para verificar"
        )
        self.assertEqual(DRAFT, batch.status)
        with self.assertRaises(AccessDeniedError):
            self.service.approve(batch.batch_id, self.reviewer)

    def test_parameterizer_can_autoapprove_when_configured(self):
        batch = self.service.create_batch(
            target_rows(), self.parameterizer, auto_approve=True
        )
        self.assertEqual(batch.status, APPROVED)

    def test_storage_uses_explicit_columns_without_duplicate_json(self):
        batch = self.approve(target_rows())
        approved = self.repository.load_approved_targets()

        with closing(sqlite3.connect(self.db_path)) as connection:
            row_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(target_batch_rows)"
                ).fetchall()
            }
            error_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(target_batch_errors)"
                ).fetchall()
            }
            stored_rate = connection.execute(
                "SELECT executive_rate FROM target_batch_rows WHERE batch_id = ?",
                (batch.batch_id,),
            ).fetchone()[0]
        self.assertNotIn("payload_json", row_columns)
        self.assertNotIn("payload_json", error_columns)
        self.assertEqual(0.04, stored_rate)
        self.assertEqual(0.04, approved.loc[0, "comision_ejecutivo"])


if __name__ == "__main__":
    unittest.main()
