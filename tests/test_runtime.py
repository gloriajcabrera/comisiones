from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.database import DatabaseSettings, POSTGRESQL, SQLITE
from src.authentication import LocalTestAuthenticationProvider
from src.netsuite_provider import NetSuiteDataProvider
from src.runtime import (
    DEVELOPMENT,
    PRODUCTION,
    RuntimeSettings,
    build_runtime,
)


class EmptyProvider:
    def load_dataset(self):
        raise AssertionError("Construir el runtime no debe consultar NetSuite")


class RuntimeTest(unittest.TestCase):
    def test_development_runtime_composes_services_on_one_sqlite_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commissions.db"
            runtime = build_runtime(
                RuntimeSettings(
                    DEVELOPMENT,
                    DatabaseSettings(backend=SQLITE, sqlite_path=path),
                ),
                EmptyProvider(),
                LocalTestAuthenticationProvider({}, enabled=False),
            )

            self.assertTrue(path.exists())
            self.assertIs(runtime.sync_service.provider.__class__, EmptyProvider)
            self.assertIs(runtime.sessions.users, runtime.access_repository)
            self.assertIs(
                runtime.calculation_service.provider,
                runtime.netsuite_sync_repository,
            )
            self.assertIs(
                runtime.calculation_service.targets_provider, runtime.targets
            )
            self.assertIs(
                runtime.calculation_service.cuts_provider, runtime.cut_periods
            )
            self.assertIs(
                runtime.calculation_views.calculations,
                runtime.calculation_service,
            )
            self.assertIs(
                runtime.review_workflows.repository,
                runtime.reviews,
            )
            self.assertIs(runtime.target_workflows.repository, runtime.targets)
            self.assertIs(
                runtime.cut_period_workflows.repository,
                runtime.cut_periods,
            )
            self.assertIs(
                runtime.business_period_workflows.repository,
                runtime.business_periods,
            )
            self.assertIs(
                runtime.team_change_requests.repository,
                runtime.access_repository,
            )
            self.assertIsInstance(runtime.sync_service.provider, NetSuiteDataProvider)

    def test_production_rejects_sqlite(self):
        settings = RuntimeSettings(
            PRODUCTION,
            DatabaseSettings(backend=SQLITE, sqlite_path=Path("local.db")),
        )
        with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
            build_runtime(settings, EmptyProvider())

    def test_production_rejects_local_test_authentication(self):
        settings = RuntimeSettings(
            PRODUCTION,
            DatabaseSettings(backend=POSTGRESQL, postgres_dsn="postgresql://db"),
        )
        with patch.dict("os.environ", {"LOCAL_TEST_AUTH_ENABLED": "true"}):
            with self.assertRaisesRegex(RuntimeError, "autenticacion local"):
                settings.validate()

    def test_environment_settings_do_not_require_secrets_for_sqlite_development(self):
        environment = {
            "COMMISSIONS_ENV": DEVELOPMENT,
            "COMMISSIONS_DB_BACKEND": SQLITE,
            "COMMISSIONS_SQLITE_PATH": "app_data/test-runtime.db",
            "LOCAL_TEST_AUTH_ENABLED": "false",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = RuntimeSettings.from_environment()

        self.assertEqual(DEVELOPMENT, settings.environment)
        self.assertEqual(SQLITE, settings.database.backend)


if __name__ == "__main__":
    unittest.main()
