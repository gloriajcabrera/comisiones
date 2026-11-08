"""Proveedores de autenticacion separados de autorizacion y perfiles internos."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import os
import secrets
import json
from datetime import date
from typing import Callable, Mapping, Protocol

from .access_control import (
    AccessDeniedError,
    AuthorizationService,
    PERM_USERS_MANAGE,
    UserIdentity,
    UserProfile,
)


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class ExternalIdentity:
    domain_username: str
    external_identifier: str | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)


class AuthenticationProvider(Protocol):
    def authenticate(self, username: str, password: str) -> ExternalIdentity: ...


class UserAccessRepository(Protocol):
    def get_identity(self, domain_username: str) -> UserIdentity | None: ...

    def effective_permissions(self, user_id: int) -> set[str]: ...

    def record_last_access(self, user_id: int) -> None: ...

    def list_user_profiles(self, effective_on: date | str) -> list[UserProfile]: ...


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)


class LocalTestAuthenticationProvider:
    """In-memory test login; it must remain disabled in production."""

    def __init__(
        self,
        accounts: Mapping[str, str],
        enabled: bool | None = None,
    ) -> None:
        if enabled is None:
            enabled = os.getenv("LOCAL_TEST_AUTH_ENABLED", "false").lower() == "true"
        environment = os.getenv("COMMISSIONS_ENV", "development").strip().lower()
        if enabled and environment == "production":
            raise RuntimeError(
                "La autenticacion local de pruebas no puede habilitarse en produccion"
            )
        self.enabled = enabled
        self._accounts: dict[str, tuple[bytes, bytes]] = {}
        for username, password in accounts.items():
            salt = secrets.token_bytes(16)
            self._accounts[username.strip().lower()] = (
                salt,
                _password_hash(password, salt),
            )

    def authenticate(self, username: str, password: str) -> ExternalIdentity:
        if not self.enabled:
            raise AuthenticationError("La autenticacion local de pruebas esta deshabilitada")
        normalized = username.strip().lower()
        stored = self._accounts.get(normalized)
        if stored is None:
            raise AuthenticationError("Credenciales invalidas")
        salt, expected = stored
        actual = _password_hash(password, salt)
        if not hmac.compare_digest(actual, expected):
            raise AuthenticationError("Credenciales invalidas")
        return ExternalIdentity(normalized, attributes={"provider": "local-test"})


@dataclass(frozen=True)
class LdapSettings:
    server: str
    port: int
    domain: str
    base_dn: str
    use_tls: bool = True
    timeout_seconds: int = 10
    search_user: str | None = field(default=None, repr=False)
    search_password: str | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls) -> "LdapSettings":
        values = {
            "server": os.getenv("LDAP_SERVER", "").strip(),
            "domain": os.getenv("LDAP_DOMAIN", "").strip(),
            "base_dn": os.getenv("LDAP_BASE_DN", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError(
                "Faltan variables LDAP: "
                + ", ".join(f"LDAP_{name.upper()}" for name in missing)
            )
        port = int(os.getenv("LDAP_PORT", "636"))
        timeout = int(os.getenv("LDAP_TIMEOUT_SECONDS", "10"))
        if port <= 0 or timeout <= 0:
            raise ValueError("Puerto y timeout LDAP deben ser mayores que cero")
        use_tls = os.getenv("LDAP_USE_TLS", "true").lower() == "true"
        return cls(
            port=port,
            use_tls=use_tls,
            timeout_seconds=timeout,
            search_user=os.getenv("LDAP_SEARCH_USER", "").strip() or None,
            search_password=os.getenv("LDAP_SEARCH_PASSWORD", "") or None,
            **values,
        )


LdapAuthenticator = Callable[[LdapSettings, str, str], ExternalIdentity]


class LdapAuthenticationProvider:
    """LDAP contract with an injected adapter to avoid coupling to a package."""

    def __init__(
        self,
        adapter: LdapAuthenticator,
        settings: LdapSettings | None = None,
    ) -> None:
        self.adapter = adapter
        self.settings = settings

    def authenticate(self, username: str, password: str) -> ExternalIdentity:
        if not username.strip() or not password:
            raise AuthenticationError("Credenciales invalidas")
        settings = self.settings or LdapSettings.from_environment()
        try:
            identity = self.adapter(settings, username, password)
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError("No fue posible validar la identidad corporativa") from exc
        return ExternalIdentity(
            identity.domain_username.strip().lower(),
            identity.external_identifier,
            identity.attributes,
        )


def ldap3_authenticate(
    settings: LdapSettings, username: str, password: str
) -> ExternalIdentity:
    """Valida por LDAP/LDAPS sin usar APIs exclusivas de Windows."""
    try:
        import ldap3
    except ImportError as exc:
        raise RuntimeError(
            "La autenticacion corporativa requiere instalar 'ldap3'"
        ) from exc
    account = f"{settings.domain}\\{username.strip()}"
    server = ldap3.Server(
        settings.server,
        port=settings.port,
        use_ssl=settings.use_tls,
        connect_timeout=settings.timeout_seconds,
    )
    connection = ldap3.Connection(
        server,
        user=account,
        password=password,
        receive_timeout=settings.timeout_seconds,
        auto_bind=True,
        raise_exceptions=True,
    )
    try:
        return ExternalIdentity(
            username.strip().lower(),
            attributes={"provider": "ldap"},
        )
    finally:
        connection.unbind()


@dataclass(frozen=True)
class DirectoryUser:
    domain_username: str
    first_names: str = ""
    last_names: str = ""
    email: str = ""
    identification: str = ""
    external_identifier: str = ""


class DirectorySearchProvider(Protocol):
    def search(self, query: str, limit: int = 20) -> list[DirectoryUser]: ...


LdapDirectorySearcher = Callable[
    [LdapSettings, str, int], list[DirectoryUser]
]


class LdapDirectorySearchProvider:
    def __init__(
        self,
        adapter: LdapDirectorySearcher,
        settings: LdapSettings | None = None,
    ) -> None:
        self.adapter = adapter
        self.settings = settings

    def search(self, query: str, limit: int = 20) -> list[DirectoryUser]:
        normalized = str(query).strip()
        if len(normalized) < 2:
            raise ValueError("La búsqueda debe contener al menos dos caracteres")
        if not 1 <= limit <= 100:
            raise ValueError("El límite de búsqueda debe estar entre 1 y 100")
        settings = self.settings or LdapSettings.from_environment()
        if not settings.search_user or not settings.search_password:
            raise RuntimeError("Faltan credenciales técnicas para búsqueda LDAP")
        return self.adapter(settings, normalized, limit)


def ldap3_search_directory(
    settings: LdapSettings, query: str, limit: int
) -> list[DirectoryUser]:
    try:
        import ldap3
        from ldap3.utils.conv import escape_filter_chars
    except ImportError as exc:
        raise RuntimeError("La búsqueda corporativa requiere instalar 'ldap3'") from exc
    server = ldap3.Server(
        settings.server,
        port=settings.port,
        use_ssl=settings.use_tls,
        connect_timeout=settings.timeout_seconds,
    )
    connection = ldap3.Connection(
        server,
        user=settings.search_user,
        password=settings.search_password,
        receive_timeout=settings.timeout_seconds,
        auto_bind=True,
        raise_exceptions=True,
    )
    escaped = escape_filter_chars(query)
    search_filter = (
        "(&(objectCategory=person)(objectClass=user)(|"
        f"(sAMAccountName=*{escaped}*)(displayName=*{escaped}*)"
        f"(mail=*{escaped}*)))"
    )
    attributes = [
        "sAMAccountName", "givenName", "sn", "mail", "employeeID", "objectGUID"
    ]
    try:
        connection.search(
            settings.base_dn,
            search_filter,
            attributes=attributes,
            size_limit=limit,
        )
        result: list[DirectoryUser] = []
        for entry in connection.entries:
            values = entry.entry_attributes_as_dict
            text = lambda name: str((values.get(name) or [""])[0] or "")
            result.append(DirectoryUser(
                domain_username=text("sAMAccountName").strip().lower(),
                first_names=text("givenName").strip(),
                last_names=text("sn").strip(),
                email=text("mail").strip(),
                identification=text("employeeID").strip(),
                external_identifier=text("objectGUID").strip(),
            ))
        return [item for item in result if item.domain_username]
    finally:
        connection.unbind()


class DirectoryLookupService:
    def __init__(
        self,
        provider: DirectorySearchProvider,
        authorization: AuthorizationService,
    ) -> None:
        self.provider = provider
        self.authorization = authorization

    def search(
        self, actor_user_id: int, query: str, limit: int = 20
    ) -> list[DirectoryUser]:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        return self.provider.search(query, limit)


def directory_search_provider_from_environment() -> DirectorySearchProvider | None:
    username = os.getenv("LDAP_SEARCH_USER", "").strip()
    password = os.getenv("LDAP_SEARCH_PASSWORD", "")
    if not username and not password:
        return None
    if not username or not password:
        raise RuntimeError(
            "LDAP_SEARCH_USER y LDAP_SEARCH_PASSWORD deben configurarse juntos"
        )
    return LdapDirectorySearchProvider(ldap3_search_directory)


def authentication_provider_from_environment() -> AuthenticationProvider:
    environment = os.getenv("COMMISSIONS_ENV", "development").strip().lower()
    default_mode = "ldap" if environment == "production" else "local"
    mode = os.getenv("COMMISSIONS_AUTH_MODE", default_mode).strip().lower()
    if environment == "production" and mode != "ldap":
        raise RuntimeError("Produccion requiere autenticacion LDAP/LDAPS")
    if mode == "ldap":
        return LdapAuthenticationProvider(ldap3_authenticate)
    if mode != "local":
        raise ValueError(f"Modo de autenticacion no soportado: {mode}")
    raw_accounts = os.getenv("LOCAL_TEST_ACCOUNTS_JSON", "{}")
    try:
        accounts = json.loads(raw_accounts)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LOCAL_TEST_ACCOUNTS_JSON no contiene JSON valido") from exc
    if not isinstance(accounts, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in accounts.items()
    ):
        raise RuntimeError("LOCAL_TEST_ACCOUNTS_JSON debe ser un objeto usuario/contrasena")
    return LocalTestAuthenticationProvider(accounts)


@dataclass(frozen=True)
class AuthenticatedSession:
    user: UserIdentity
    external_identity: ExternalIdentity


class AuthenticationService:
    def __init__(
        self,
        provider: AuthenticationProvider,
        users: UserAccessRepository,
    ) -> None:
        self.provider = provider
        self.users = users

    def login(self, username: str, password: str) -> AuthenticatedSession:
        external = self.provider.authenticate(username, password)
        internal = self.users.get_identity(external.domain_username)
        if internal is None or not internal.active:
            raise AccessDeniedError("El perfil interno no existe o esta inactivo")
        if not self.users.effective_permissions(internal.user_id):
            raise AccessDeniedError("El perfil interno no tiene permisos asignados")
        self.users.record_last_access(internal.user_id)
        return AuthenticatedSession(internal, external)


@dataclass(frozen=True)
class ApplicationSession:
    authenticated: AuthenticatedSession
    profile: UserProfile
    permissions: frozenset[str]


class ApplicationSessionService:
    def __init__(
        self,
        authentication: AuthenticationService,
        users: UserAccessRepository,
    ) -> None:
        self.authentication = authentication
        self.users = users

    def login(
        self,
        username: str,
        password: str,
        effective_on: date | str | None = None,
    ) -> ApplicationSession:
        authenticated = self.authentication.login(username, password)
        when = effective_on or date.today()
        profile = next(
            (
                item
                for item in self.users.list_user_profiles(when)
                if item.user_id == authenticated.user.user_id
            ),
            None,
        )
        if profile is None:
            raise AccessDeniedError("No fue posible cargar el perfil interno")
        permissions = frozenset(
            self.users.effective_permissions(authenticated.user.user_id)
        )
        return ApplicationSession(authenticated, profile, permissions)
