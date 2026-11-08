from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.access_control import (
    AccessDeniedError,
    AuthorizationService,
    ROLE_ADMIN,
    ROLE_REVIEWER,
    ROLE_EXECUTIVE,
    SqliteAccessRepository,
)
from src.authentication import (
    ApplicationSessionService,
    AuthenticationError,
    AuthenticationService,
    ExternalIdentity,
    DirectoryLookupService,
    DirectoryUser,
    LdapDirectorySearchProvider,
    LdapAuthenticationProvider,
    LdapSettings,
    LocalTestAuthenticationProvider,
    authentication_provider_from_environment,
    directory_search_provider_from_environment,
)


class AuthenticationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "auth.db"
        self.users = SqliteAccessRepository(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_local_authentication_can_be_disabled(self):
        provider = LocalTestAuthenticationProvider(
            {"user": "password"}, enabled=False
        )
        with self.assertRaisesRegex(AuthenticationError, "deshabilitada"):
            provider.authenticate("user", "password")

    def test_valid_external_login_requires_active_internal_profile(self):
        provider = LocalTestAuthenticationProvider(
            {"user": "password"}, enabled=True
        )
        service = AuthenticationService(provider, self.users)
        with self.assertRaises(AccessDeniedError):
            service.login("user", "password")

        user_id = self.users.create_user("user", "Test", "User")
        self.users.assign_role(user_id, ROLE_EXECUTIVE)
        session = service.login("USER", "password")
        self.assertEqual(session.user.user_id, user_id)
        with closing(sqlite3.connect(self.db_path)) as connection:
            last_access = connection.execute(
                "SELECT last_access_at FROM users WHERE id = ?", (user_id,)
            ).fetchone()[0]
        self.assertIsNotNone(last_access)

    def test_inactive_internal_profile_is_rejected(self):
        user_id = self.users.create_user("user", "Test", "User")
        self.users.assign_role(user_id, ROLE_EXECUTIVE)
        self.users.set_user_active(user_id, False, user_id, "Retiro")
        provider = LocalTestAuthenticationProvider(
            {"user": "password"}, enabled=True
        )
        with self.assertRaises(AccessDeniedError):
            AuthenticationService(provider, self.users).login("user", "password")

    def test_local_provider_rejects_wrong_password_without_plaintext_storage(self):
        provider = LocalTestAuthenticationProvider(
            {"user": "plain-secret"}, enabled=True
        )
        self.assertNotIn("plain-secret", repr(provider._accounts))
        with self.assertRaisesRegex(AuthenticationError, "Credenciales invalidas"):
            provider.authenticate("user", "wrong")

    def test_ldap_provider_uses_injected_adapter_and_normalizes_username(self):
        calls = []
        settings = LdapSettings("ldap.internal", 636, "DOMAIN", "dc=test")

        def adapter(actual_settings, username, password):
            calls.append((actual_settings, username, password))
            return ExternalIdentity(" Corporate.User ", "ad-1", {"mail": "u@test"})

        provider = LdapAuthenticationProvider(adapter, settings)
        identity = provider.authenticate("input", "secret")
        self.assertEqual(identity.domain_username, "corporate.user")
        self.assertEqual(calls[0][0], settings)

    def test_active_profile_without_permissions_cannot_login(self):
        self.users.create_user("user", "Test", "User")
        provider = LocalTestAuthenticationProvider(
            {"user": "password"}, enabled=True
        )

        with self.assertRaisesRegex(AccessDeniedError, "no tiene permisos"):
            AuthenticationService(provider, self.users).login("user", "password")

    def test_local_authentication_cannot_be_forced_on_in_production(self):
        with patch.dict("os.environ", {"COMMISSIONS_ENV": "production"}):
            with self.assertRaisesRegex(RuntimeError, "produccion"):
                LocalTestAuthenticationProvider(
                    {"user": "password"}, enabled=True
                )

    def test_ldap_rejects_empty_username_before_calling_adapter(self):
        provider = LdapAuthenticationProvider(
            lambda *_: ExternalIdentity("unexpected"),
            LdapSettings("ldap.internal", 636, "DOMAIN", "dc=test"),
        )
        with self.assertRaises(AuthenticationError):
            provider.authenticate(" ", "secret")

    def test_application_session_uses_authenticated_profile_and_effective_roles(self):
        user_id = self.users.create_user("user", "Test", "User")
        self.users.assign_role(user_id, ROLE_EXECUTIVE)
        provider = LocalTestAuthenticationProvider(
            {"user": "password"}, enabled=True
        )
        service = ApplicationSessionService(
            AuthenticationService(provider, self.users), self.users
        )

        session = service.login("user", "password", "2026-10-15")

        self.assertEqual(user_id, session.profile.user_id)
        self.assertEqual((ROLE_EXECUTIVE,), session.profile.roles)
        self.assertTrue(session.permissions)

    def test_internal_profile_preserves_active_directory_identifier(self):
        user_id = self.users.create_user(
            "directory.user", "Directory", "User",
            ad_identifier="ad-object-123",
        )
        self.users.assign_role(user_id, ROLE_EXECUTIVE)

        profile = next(
            item for item in self.users.list_user_profiles("2026-10-17")
            if item.user_id == user_id
        )

        self.assertEqual("ad-object-123", profile.ad_identifier)

    def test_environment_factory_forces_ldap_in_production(self):
        with patch.dict(
            "os.environ",
            {
                "COMMISSIONS_ENV": "production",
                "COMMISSIONS_AUTH_MODE": "local",
                "LOCAL_TEST_AUTH_ENABLED": "false",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "LDAP"):
                authentication_provider_from_environment()

    def test_environment_factory_validates_local_account_json(self):
        with patch.dict(
            "os.environ",
            {
                "COMMISSIONS_ENV": "development",
                "COMMISSIONS_AUTH_MODE": "local",
                "LOCAL_TEST_ACCOUNTS_JSON": "[]",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "usuario/contrasena"):
                authentication_provider_from_environment()

    def test_directory_lookup_requires_admin_permission(self):
        admin = self.users.create_user("admin.directory", "Admin", "Directory")
        self.users.assign_role(admin, ROLE_ADMIN)
        reviewer = self.users.create_user(
            "reviewer.directory", "Reviewer", "Directory"
        )
        self.users.assign_role(reviewer, ROLE_REVIEWER)

        class Provider:
            def search(self, query, limit=20):
                return [DirectoryUser("user.one", "User", "One")]

        service = DirectoryLookupService(
            Provider(), AuthorizationService(self.users)
        )
        self.assertEqual("user.one", service.search(admin, "user")[0].domain_username)
        with self.assertRaises(AccessDeniedError):
            service.search(reviewer, "user")

    def test_ldap_directory_provider_validates_query_and_hides_credentials(self):
        calls = []
        settings = LdapSettings(
            "ldap.internal", 636, "DOMAIN", "dc=test",
            search_user="technical", search_password="secret-value",
        )
        provider = LdapDirectorySearchProvider(
            lambda actual, query, limit: calls.append((actual, query, limit)) or [
                DirectoryUser("corporate.user", "Corporate", "User", "u@test")
            ],
            settings,
        )

        with self.assertRaisesRegex(ValueError, "dos caracteres"):
            provider.search("x")
        result = provider.search(" corporate ", 5)

        self.assertEqual("corporate.user", result[0].domain_username)
        self.assertEqual("corporate", calls[0][1])
        self.assertNotIn("secret-value", repr(settings))

    def test_directory_factory_requires_both_technical_credentials(self):
        with patch.dict(
            "os.environ",
            {"LDAP_SEARCH_USER": "technical", "LDAP_SEARCH_PASSWORD": ""},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "deben configurarse juntos"):
                directory_search_provider_from_environment()

        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(directory_search_provider_from_environment())


if __name__ == "__main__":
    unittest.main()
