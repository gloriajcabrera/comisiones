from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.access_control import (
    AccessDeniedError,
    AuthorizationService,
    ROLE_ADMIN,
    ROLE_EXECUTIVE,
    ROLE_REVIEWER,
    SqliteAccessRepository,
)
from src.business_periods import (
    BusinessPeriodService,
    PERIOD_CLOSED,
    PERIOD_FINALIZED,
    PERIOD_IN_REVIEW,
    PERIOD_OPEN,
    SqliteBusinessPeriodRepository,
)


class BusinessPeriodsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.access = SqliteAccessRepository(root / "access.db")
        self.authorization = AuthorizationService(self.access)
        self.repository = SqliteBusinessPeriodRepository(root / "periods.db")
        self.service = BusinessPeriodService(self.repository, self.authorization)
        self.reviewer = self._user("reviewer", ROLE_REVIEWER)
        self.admin = self._user("admin", ROLE_ADMIN)
        self.executive = self._user("executive", ROLE_EXECUTIVE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _user(self, username, role):
        user_id = self.access.create_user(username, username, "Test")
        self.access.assign_role(user_id, role)
        return user_id

    def test_full_close_and_reopen_preserve_history(self):
        self.service.create(self.reviewer, "2026-07")
        self.assertEqual(PERIOD_OPEN, self.repository.status("2026-07"))
        self.assertEqual(
            PERIOD_IN_REVIEW,
            self.service.advance(self.reviewer, "2026-07", "Inicio revision"),
        )
        self.assertEqual(
            PERIOD_FINALIZED,
            self.service.advance(self.reviewer, "2026-07", "Revision completa"),
        )
        self.assertEqual(
            PERIOD_CLOSED,
            self.service.advance(self.reviewer, "2026-07", "Pago final generado"),
        )
        with self.assertRaisesRegex(RuntimeError, "debe reabrirse"):
            self.repository.require_recalculation_allowed("2026-07")

        self.service.reopen(self.admin, "2026-07", "Ajuste posterior documentado")
        self.repository.require_recalculation_allowed("2026-07")
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            history = connection.execute(
                "SELECT previous_status, new_status, reason "
                "FROM business_period_status_history WHERE period='2026-07' ORDER BY id"
            ).fetchall()
        self.assertEqual(5, len(history))
        self.assertEqual((PERIOD_CLOSED, PERIOD_OPEN, "Ajuste posterior documentado"), history[-1])

    def test_reviewer_cannot_reopen_and_reason_is_required(self):
        self.service.create(self.reviewer, "2026-08")
        for reason in ("Revision", "Finalizacion", "Cierre"):
            self.service.advance(self.reviewer, "2026-08", reason)
        with self.assertRaises(AccessDeniedError):
            self.service.reopen(self.reviewer, "2026-08", "Intento")
        with self.assertRaisesRegex(ValueError, "motivo"):
            self.service.reopen(self.admin, "2026-08", "")

    def test_executive_cannot_create_period(self):
        with self.assertRaises(AccessDeniedError):
            self.service.create(self.executive, "2026-09")

    def test_invalid_or_missing_period_is_rejected(self):
        for invalid in ("09/2026", "2026-9", "2026-13", "texto"):
            with self.subTest(period=invalid):
                with self.assertRaisesRegex(ValueError, "YYYY-MM"):
                    self.repository.status(invalid)
        with self.assertRaises(KeyError):
            self.repository.require_recalculation_allowed("2026-09")

    def test_repository_rejects_arbitrary_status_transition(self):
        self.service.create(self.reviewer, "2026-10")

        with self.assertRaisesRegex(ValueError, "Transición de periodo inválida"):
            self.repository.transition(
                "2026-10", PERIOD_OPEN, PERIOD_CLOSED,
                self.reviewer, "No puede saltar estados",
            )
        self.assertEqual(PERIOD_OPEN, self.repository.status("2026-10"))

    def test_authorized_period_queries_return_current_state_and_history(self):
        self.service.create(self.reviewer, "2026-07")
        self.service.create(self.reviewer, "2026-08")
        self.service.advance(self.reviewer, "2026-07", "Inicio revisión")

        periods = self.service.list_periods(
            self.reviewer, statuses=[PERIOD_IN_REVIEW]
        )
        history = self.service.history(self.reviewer, "2026-07")

        self.assertEqual(["2026-07"], [item.period for item in periods])
        self.assertEqual(PERIOD_IN_REVIEW, periods[0].status)
        self.assertEqual(2, len(history))
        self.assertEqual("Inicio revisión", history[-1].reason)
        with self.assertRaises(AccessDeniedError):
            self.service.list_periods(self.executive)


if __name__ == "__main__":
    unittest.main()
