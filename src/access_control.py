"""Usuarios, RBAC y alcance jerarquico con vigencias."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .database import Database


ROLE_EXECUTIVE = "EJECUTIVO"
ROLE_MANAGER = "JEFE"
ROLE_DIRECTOR = "GERENTE"
ROLE_REVIEWER = "REVISOR"
ROLE_PARAMETERIZER = "PARAMETRIZADOR"
ROLE_ADMIN = "ADMINISTRADOR"

CALCULATION_OPERATOR_ROLES = frozenset({
    ROLE_REVIEWER,
    ROLE_PARAMETERIZER,
    ROLE_ADMIN,
})

PERM_VIEW_OWN = "RESULTS_VIEW_OWN"
PERM_VIEW_TEAM = "RESULTS_VIEW_TEAM"
PERM_VIEW_ANY = "RESULTS_VIEW_ANY"
PERM_REVIEW_CREATE = "REVIEW_REQUEST_CREATE"
PERM_REVIEW_MANAGE = "REVIEW_REQUEST_MANAGE"
PERM_TARGET_REGISTER = "TARGET_REGISTER"
PERM_TARGET_VERIFY = "TARGET_VERIFY"
PERM_TARGET_APPROVE = "TARGET_APPROVE"
PERM_INPUT_REGISTER = "INPUT_REGISTER"
PERM_PARAMETERS_CHANGE = "PARAMETERS_CHANGE"
PERM_EXCEPTION_PROPOSE = "EXCEPTION_PROPOSE"
PERM_EXCEPTION_APPROVE = "EXCEPTION_APPROVE"
PERM_PAYMENT_FILE_GENERATE = "PAYMENT_FILE_GENERATE"
PERM_PAYMENT_FILE_DOWNLOAD = "PAYMENT_FILE_DOWNLOAD"
PERM_PERIOD_CLOSE = "PERIOD_CLOSE"
PERM_PERIOD_REOPEN = "PERIOD_REOPEN"
PERM_USERS_MANAGE = "USERS_MANAGE"
PERM_NETSUITE_SYNC = "NETSUITE_SYNC_EXECUTE"
PERM_CALCULATION_EXECUTE = "CALCULATION_EXECUTE"
PERM_CALCULATION_APPROVE = "CALCULATION_APPROVE"
PERM_PAYMENT_FILE_SEND = "PAYMENT_FILE_SEND"
PERM_AUDIT_VIEW = "AUDIT_VIEW"
PERM_TEAM_PROPOSE = "TEAM_PROPOSE"
PERM_TEAM_APPROVE = "TEAM_APPROVE"

REL_EXECUTIVE_MANAGER = "EJECUTIVO_JEFE"
REL_MANAGER_DIRECTOR = "JEFE_GERENTE"

EMPLOYMENT_ACTIVE = "ACTIVO"
EMPLOYMENT_RETIRED = "RETIRADO"


ROLE_PERMISSIONS: dict[str, set[str]] = {
    ROLE_EXECUTIVE: {PERM_VIEW_OWN, PERM_REVIEW_CREATE},
    ROLE_MANAGER: {
        PERM_VIEW_OWN,
        PERM_VIEW_TEAM,
        PERM_REVIEW_CREATE,
        PERM_TARGET_REGISTER,
        PERM_TEAM_PROPOSE,
    },
    ROLE_DIRECTOR: {PERM_VIEW_OWN, PERM_VIEW_TEAM, PERM_REVIEW_CREATE},
    ROLE_REVIEWER: {
        PERM_VIEW_OWN,
        PERM_VIEW_TEAM,
        PERM_VIEW_ANY,
        PERM_REVIEW_CREATE,
        PERM_REVIEW_MANAGE,
        PERM_TARGET_VERIFY,
        PERM_INPUT_REGISTER,
        PERM_EXCEPTION_PROPOSE,
        PERM_PAYMENT_FILE_GENERATE,
        PERM_PAYMENT_FILE_DOWNLOAD,
        PERM_PERIOD_CLOSE,
    },
    ROLE_PARAMETERIZER: {
        PERM_VIEW_OWN,
        PERM_VIEW_TEAM,
        PERM_VIEW_ANY,
        PERM_REVIEW_CREATE,
        PERM_REVIEW_MANAGE,
        PERM_TARGET_REGISTER,
        PERM_TARGET_VERIFY,
        PERM_TARGET_APPROVE,
        PERM_INPUT_REGISTER,
        PERM_PARAMETERS_CHANGE,
        PERM_EXCEPTION_PROPOSE,
        PERM_EXCEPTION_APPROVE,
        PERM_PAYMENT_FILE_GENERATE,
        PERM_PAYMENT_FILE_DOWNLOAD,
        PERM_PERIOD_CLOSE,
        PERM_PERIOD_REOPEN,
        PERM_TEAM_APPROVE,
        PERM_CALCULATION_APPROVE,
    },
    ROLE_ADMIN: {
        PERM_VIEW_OWN,
        PERM_VIEW_TEAM,
        PERM_VIEW_ANY,
        PERM_REVIEW_CREATE,
        PERM_REVIEW_MANAGE,
        PERM_TARGET_REGISTER,
        PERM_TARGET_VERIFY,
        PERM_TARGET_APPROVE,
        PERM_INPUT_REGISTER,
        PERM_PARAMETERS_CHANGE,
        PERM_EXCEPTION_PROPOSE,
        PERM_EXCEPTION_APPROVE,
        PERM_PAYMENT_FILE_GENERATE,
        PERM_PAYMENT_FILE_DOWNLOAD,
        PERM_PERIOD_CLOSE,
        PERM_PERIOD_REOPEN,
        PERM_USERS_MANAGE,
        PERM_AUDIT_VIEW,
        PERM_TEAM_APPROVE,
        PERM_CALCULATION_APPROVE,
    },
}

ALL_PERMISSIONS = set().union(*ROLE_PERMISSIONS.values()) | {
    PERM_NETSUITE_SYNC,
    PERM_CALCULATION_EXECUTE,
    PERM_PAYMENT_FILE_SEND,
}


class AccessDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class UserIdentity:
    user_id: int
    domain_username: str
    active: bool


@dataclass(frozen=True)
class UserProfile:
    user_id: int
    domain_username: str
    identification: str | None
    first_names: str
    last_names: str
    email: str | None
    ad_identifier: str | None
    active: bool
    employment_status: str
    roles: tuple[str, ...]
    commercial_entities: tuple[str, ...]
    created_at: str
    updated_at: str
    last_access_at: str | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date_value(value: date | str) -> str:
    return date.fromisoformat(str(value)).isoformat()


class SqliteAccessRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        self._seed_permissions()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_schema(self) -> None:
        identity = self._identity_primary_key()
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS users (
                    id {identity},
                    domain_username TEXT NOT NULL UNIQUE,
                    identification TEXT,
                    first_names TEXT NOT NULL,
                    last_names TEXT NOT NULL,
                    email TEXT,
                    ad_identifier TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_access_at TEXT
                );
                CREATE TABLE IF NOT EXISTS roles (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS permissions (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_roles (
                    user_id INTEGER NOT NULL,
                    role_code TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    assigned_by INTEGER,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (user_id, role_code),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (role_code) REFERENCES roles(code)
                );
                CREATE TABLE IF NOT EXISTS employment_status_history (
                    id {identity},
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    effective_from TEXT NOT NULL,
                    changed_by INTEGER NOT NULL,
                    changed_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    UNIQUE (user_id, effective_from),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (changed_by) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS role_permissions (
                    role_code TEXT NOT NULL,
                    permission_code TEXT NOT NULL,
                    PRIMARY KEY (role_code, permission_code),
                    FOREIGN KEY (role_code) REFERENCES roles(code),
                    FOREIGN KEY (permission_code) REFERENCES permissions(code)
                );
                CREATE INDEX IF NOT EXISTS idx_employment_status_effective
                ON employment_status_history(user_id, effective_from DESC);
                CREATE TABLE IF NOT EXISTS user_permissions (
                    user_id INTEGER NOT NULL,
                    permission_code TEXT NOT NULL,
                    granted INTEGER NOT NULL,
                    assigned_at TEXT NOT NULL,
                    assigned_by INTEGER,
                    PRIMARY KEY (user_id, permission_code),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (permission_code) REFERENCES permissions(code)
                );
                CREATE TABLE IF NOT EXISTS commercial_entities (
                    id {identity},
                    external_code TEXT NOT NULL UNIQUE,
                    entity_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS user_commercial_entities (
                    user_id INTEGER NOT NULL,
                    commercial_entity_id INTEGER NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    PRIMARY KEY (user_id, commercial_entity_id, valid_from),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (commercial_entity_id) REFERENCES commercial_entities(id)
                );
                CREATE TABLE IF NOT EXISTS hierarchy_assignments (
                    id {identity},
                    source_entity_id INTEGER NOT NULL,
                    target_entity_id INTEGER NOT NULL,
                    relationship_type TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    changed_by INTEGER,
                    changed_at TEXT NOT NULL,
                    FOREIGN KEY (source_entity_id) REFERENCES commercial_entities(id),
                    FOREIGN KEY (target_entity_id) REFERENCES commercial_entities(id),
                    CHECK (source_entity_id <> target_entity_id),
                    CHECK (valid_to IS NULL OR valid_to >= valid_from)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id {identity},
                    actor_user_id INTEGER,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    occurred_at TEXT NOT NULL,
                    previous_json TEXT,
                    new_json TEXT,
                    reason TEXT,
                    context_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_hierarchy_effective
                ON hierarchy_assignments(
                    relationship_type, target_entity_id, valid_from, valid_to, is_active
                );
                CREATE INDEX IF NOT EXISTS idx_hierarchy_source_effective
                ON hierarchy_assignments(
                    source_entity_id, relationship_type, is_active, valid_from, valid_to
                );
                CREATE INDEX IF NOT EXISTS idx_user_entity_effective
                ON user_commercial_entities(
                    user_id, valid_from, valid_to, commercial_entity_id
                );
                CREATE INDEX IF NOT EXISTS idx_audit_occurred
                ON audit_events(occurred_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_entity
                ON audit_events(entity_type, entity_id, occurred_at DESC);
                CREATE TABLE IF NOT EXISTS team_change_requests (
                    id {identity},
                    request_type TEXT NOT NULL,
                    requested_by INTEGER NOT NULL,
                    manager_entity_id INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    decided_by INTEGER,
                    decided_at TEXT,
                    decision_concept TEXT,
                    created_user_id INTEGER,
                    FOREIGN KEY (requested_by) REFERENCES users(id),
                    FOREIGN KEY (manager_entity_id) REFERENCES commercial_entities(id),
                    FOREIGN KEY (decided_by) REFERENCES users(id),
                    FOREIGN KEY (created_user_id) REFERENCES users(id)
                );
                CREATE INDEX IF NOT EXISTS idx_team_change_request_status
                ON team_change_requests(status, requested_at);
                """
            )

    def _identity_primary_key(self) -> str:
        return "INTEGER PRIMARY KEY AUTOINCREMENT"

    def _seed_permissions(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                "INSERT INTO permissions(code, name) VALUES (?, ?) "
                "ON CONFLICT(code) DO NOTHING",
                [(code, code.replace("_", " ").title()) for code in ALL_PERMISSIONS],
            )
            connection.executemany(
                "INSERT INTO roles(code, name) VALUES (?, ?) "
                "ON CONFLICT(code) DO NOTHING",
                [(code, code.title()) for code in ROLE_PERMISSIONS],
            )
            for role, permissions in ROLE_PERMISSIONS.items():
                connection.executemany(
                    """
                    INSERT INTO role_permissions(role_code, permission_code)
                    VALUES (?, ?) ON CONFLICT(role_code, permission_code) DO NOTHING
                    """,
                    [(role, permission) for permission in permissions],
                )

    def _audit(
        self,
        connection: sqlite3.Connection,
        actor_user_id: int | None,
        action: str,
        entity_type: str,
        entity_id: object,
        new_value: object,
        previous_value: object = None,
        reason: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events (
                actor_user_id, action, entity_type, entity_id, occurred_at,
                previous_json, new_json, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_user_id,
                action,
                entity_type,
                str(entity_id),
                _utc_now(),
                json.dumps(previous_value, ensure_ascii=True),
                json.dumps(new_value, ensure_ascii=True),
                reason,
            ),
        )

    def create_user(
        self,
        domain_username: str,
        first_names: str,
        last_names: str,
        identification: str | None = None,
        email: str | None = None,
        ad_identifier: str | None = None,
        actor_user_id: int | None = None,
    ) -> int:
        username = domain_username.strip().lower()
        if not username:
            raise ValueError("El usuario de dominio es obligatorio")
        now = _utc_now()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    domain_username, identification, first_names, last_names,
                    email, ad_identifier, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username, identification, first_names.strip(), last_names.strip(),
                    email, ad_identifier, now, now,
                ),
            )
            user_id = int(cursor.lastrowid)
            self._audit(
                connection, actor_user_id, "USER_CREATED", "USER", user_id,
                {"domain_username": username, "active": True},
            )
        return user_id

    def bootstrap_initial_administrator(
        self,
        domain_username: str,
        first_names: str,
        last_names: str,
        reason: str,
    ) -> int:
        username = domain_username.strip().lower()
        if not username or not first_names.strip() or not last_names.strip():
            raise ValueError("Usuario, nombres y apellidos son obligatorios")
        if not reason.strip():
            raise ValueError("El motivo del alta inicial es obligatorio")
        now = _utc_now()
        with closing(self._connect()) as connection, connection:
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
                raise RuntimeError(
                    "El alta inicial solo se permite cuando no existen usuarios"
                )
            cursor = connection.execute(
                """
                INSERT INTO users (
                    domain_username, first_names, last_names,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (username, first_names.strip(), last_names.strip(), now, now),
            )
            user_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO user_roles (
                    user_id, role_code, assigned_at, assigned_by, is_active
                ) VALUES (?, ?, ?, ?, 1)
                """,
                (user_id, ROLE_ADMIN, now, user_id),
            )
            connection.execute(
                """
                INSERT INTO user_permissions (
                    user_id, permission_code, granted, assigned_at, assigned_by
                ) VALUES (?, ?, 1, ?, ?)
                """,
                (user_id, PERM_NETSUITE_SYNC, now, user_id),
            )
            self._audit(
                connection,
                user_id,
                "INITIAL_ADMIN_BOOTSTRAPPED",
                "USER",
                user_id,
                {
                    "domain_username": username,
                    "roles": [ROLE_ADMIN],
                    "permissions": [PERM_NETSUITE_SYNC],
                },
                reason=reason.strip(),
            )
        return user_id

    def set_user_active(
        self,
        user_id: int,
        active: bool,
        actor_user_id: int,
        reason: str,
    ) -> None:
        if not str(reason).strip():
            raise ValueError("El motivo del cambio de acceso es obligatorio")
        with closing(self._connect()) as connection, connection:
            previous = connection.execute(
                "SELECT is_active FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if previous is None:
                raise KeyError(f"Usuario inexistente: {user_id}")
            connection.execute(
                "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(active), _utc_now(), user_id),
            )
            self._audit(
                connection, actor_user_id, "USER_STATUS_CHANGED", "USER", user_id,
                {"active": active}, {"active": bool(previous[0])}, reason.strip(),
            )

    def update_user_profile(
        self,
        user_id: int,
        identification: str | None,
        first_names: str,
        last_names: str,
        email: str | None,
        ad_identifier: str | None,
        actor_user_id: int,
        reason: str,
    ) -> None:
        if not first_names.strip() or not last_names.strip():
            raise ValueError("Nombres y apellidos son obligatorios")
        if not str(reason).strip():
            raise ValueError("El motivo de la modificación es obligatorio")
        with closing(self._connect()) as connection, connection:
            previous = connection.execute(
                """
                SELECT identification, first_names, last_names, email, ad_identifier
                FROM users WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            if previous is None:
                raise KeyError(f"Usuario inexistente: {user_id}")
            new_value = {
                "identification": identification,
                "first_names": first_names.strip(),
                "last_names": last_names.strip(),
                "email": email,
                "ad_identifier": ad_identifier,
            }
            connection.execute(
                """
                UPDATE users SET identification = ?, first_names = ?, last_names = ?,
                    email = ?, ad_identifier = ?, updated_at = ? WHERE id = ?
                """,
                (*new_value.values(), _utc_now(), user_id),
            )
            self._audit(
                connection, actor_user_id, "USER_PROFILE_UPDATED", "USER", user_id,
                new_value,
                dict(zip(
                    ("identification", "first_names", "last_names", "email", "ad_identifier"),
                    previous,
                    strict=True,
                )),
                reason.strip(),
            )

    def list_user_profiles(self, effective_on: date | str) -> list[UserProfile]:
        effective = _date_value(effective_on)
        with closing(self._connect()) as connection:
            users = connection.execute(
                """
                SELECT id, domain_username, identification, first_names,
                       last_names, email, ad_identifier, is_active, created_at, updated_at,
                       last_access_at
                FROM users ORDER BY last_names, first_names, domain_username
                """
            ).fetchall()
            profiles: list[UserProfile] = []
            for row in users:
                roles = connection.execute(
                    """
                    SELECT role_code FROM user_roles
                    WHERE user_id = ? AND is_active = 1 ORDER BY role_code
                    """,
                    (row[0],),
                ).fetchall()
                entities = connection.execute(
                    """
                    SELECT ce.external_code
                    FROM user_commercial_entities uce
                    JOIN commercial_entities ce ON ce.id = uce.commercial_entity_id
                    WHERE uce.user_id = ? AND ce.is_active = 1
                      AND uce.valid_from <= ?
                      AND COALESCE(uce.valid_to, '9999-12-31') >= ?
                    ORDER BY ce.external_code
                    """,
                    (row[0], effective, effective),
                ).fetchall()
                profiles.append(
                    UserProfile(
                        user_id=int(row[0]),
                        domain_username=str(row[1]),
                        identification=row[2],
                        first_names=str(row[3]),
                        last_names=str(row[4]),
                        email=row[5],
                        ad_identifier=row[6],
                        active=bool(row[7]),
                        employment_status=self._employment_status_on(
                            connection, int(row[0]), effective
                        ),
                        roles=tuple(str(item[0]) for item in roles),
                        commercial_entities=tuple(str(item[0]) for item in entities),
                        created_at=str(row[8]),
                        updated_at=str(row[9]),
                        last_access_at=row[10],
                    )
                )
        return profiles

    def set_employment_status(
        self,
        user_id: int,
        status: str,
        effective_from: date | str,
        actor_user_id: int,
        reason: str,
    ) -> None:
        normalized_status = str(status).strip().upper()
        if normalized_status not in {EMPLOYMENT_ACTIVE, EMPLOYMENT_RETIRED}:
            raise ValueError(f"Estado laboral invalido: {status}")
        if not str(reason).strip():
            raise ValueError("El motivo del cambio laboral es obligatorio")
        effective = _date_value(effective_from)
        now = _utc_now()
        with closing(self._connect()) as connection, connection:
            if connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (user_id,)
            ).fetchone() is None:
                raise KeyError(f"Usuario inexistente: {user_id}")
            previous = self._employment_status_on(connection, user_id, effective)
            connection.execute(
                """
                INSERT INTO employment_status_history
                (user_id, status, effective_from, changed_by, changed_at, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, normalized_status, effective, actor_user_id,
                    now, reason.strip(),
                ),
            )
            self._audit(
                connection,
                actor_user_id,
                "EMPLOYMENT_STATUS_CHANGED",
                "USER",
                user_id,
                {"status": normalized_status, "effective_from": effective},
                {"status": previous},
                reason.strip(),
            )

    def employment_status_on(
        self, user_id: int, effective_on: date | str
    ) -> str:
        with closing(self._connect()) as connection:
            if connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (user_id,)
            ).fetchone() is None:
                raise KeyError(f"Usuario inexistente: {user_id}")
            return self._employment_status_on(
                connection, user_id, _date_value(effective_on)
            )

    @staticmethod
    def _employment_status_on(
        connection: sqlite3.Connection, user_id: int, effective_on: str
    ) -> str:
        row = connection.execute(
            """
            SELECT status FROM employment_status_history
            WHERE user_id = ? AND effective_from <= ?
            ORDER BY effective_from DESC, id DESC LIMIT 1
            """,
            (user_id, effective_on),
        ).fetchone()
        return str(row[0]) if row is not None else EMPLOYMENT_ACTIVE

    def assign_role(
        self, user_id: int, role_code: str, actor_user_id: int | None = None
    ) -> None:
        if role_code not in ROLE_PERMISSIONS:
            raise KeyError(f"Rol inexistente: {role_code}")
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO user_roles (
                    user_id, role_code, assigned_at, assigned_by, is_active
                ) VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(user_id, role_code) DO UPDATE SET
                    assigned_at = excluded.assigned_at,
                    assigned_by = excluded.assigned_by,
                    is_active = 1
                """,
                (user_id, role_code, _utc_now(), actor_user_id),
            )
            self._audit(
                connection, actor_user_id, "ROLE_ASSIGNED", "USER", user_id,
                {"role": role_code},
            )

    def replace_roles(
        self, user_id: int, role_codes: Iterable[str], actor_user_id: int, reason: str
    ) -> None:
        selected = {str(role).strip().upper() for role in role_codes}
        invalid = selected - set(ROLE_PERMISSIONS)
        if invalid:
            raise KeyError(f"Roles inexistentes: {', '.join(sorted(invalid))}")
        if not selected:
            raise ValueError("El usuario debe conservar al menos un rol")
        if not str(reason).strip():
            raise ValueError("El motivo del cambio de roles es obligatorio")
        with closing(self._connect()) as connection, connection:
            if connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (user_id,)
            ).fetchone() is None:
                raise KeyError(f"Usuario inexistente: {user_id}")
            previous = {
                str(row[0]) for row in connection.execute(
                    "SELECT role_code FROM user_roles WHERE user_id = ? AND is_active = 1",
                    (user_id,),
                ).fetchall()
            }
            connection.execute(
                "UPDATE user_roles SET is_active = 0 WHERE user_id = ?", (user_id,)
            )
            now = _utc_now()
            for role_code in selected:
                connection.execute(
                    """
                    INSERT INTO user_roles
                    (user_id, role_code, assigned_at, assigned_by, is_active)
                    VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(user_id, role_code) DO UPDATE SET
                        assigned_at = excluded.assigned_at,
                        assigned_by = excluded.assigned_by,
                        is_active = 1
                    """,
                    (user_id, role_code, now, actor_user_id),
                )
            self._audit(
                connection, actor_user_id, "USER_ROLES_REPLACED", "USER", user_id,
                {"roles": sorted(selected)}, {"roles": sorted(previous)}, reason.strip(),
            )

    def grant_user_permission(
        self,
        user_id: int,
        permission_code: str,
        granted: bool,
        actor_user_id: int | None = None,
        reason: str | None = None,
    ) -> None:
        self.set_user_permissions(
            user_id,
            {permission_code: granted},
            actor_user_id,
            reason,
        )

    def set_user_permissions(
        self,
        user_id: int,
        permissions: dict[str, bool],
        actor_user_id: int | None = None,
        reason: str | None = None,
    ) -> None:
        invalid = set(permissions) - ALL_PERMISSIONS
        if invalid:
            raise KeyError(f"Permisos inexistentes: {sorted(invalid)}")
        with closing(self._connect()) as connection, connection:
            for permission_code, granted in permissions.items():
                previous_row = connection.execute(
                    "SELECT granted FROM user_permissions "
                    "WHERE user_id = ? AND permission_code = ?",
                    (user_id, permission_code),
                ).fetchone()
                previous = None if previous_row is None else bool(previous_row[0])
                granted = bool(granted)
                if previous is not None and previous == granted:
                    continue
                connection.execute(
                    """
                    INSERT INTO user_permissions (
                        user_id, permission_code, granted, assigned_at, assigned_by
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, permission_code) DO UPDATE SET
                        granted = excluded.granted,
                        assigned_at = excluded.assigned_at,
                        assigned_by = excluded.assigned_by
                    """,
                    (
                        user_id, permission_code, int(granted), _utc_now(),
                        actor_user_id,
                    ),
                )
                self._audit(
                    connection, actor_user_id, "USER_PERMISSION_CHANGED", "USER",
                    user_id,
                    {"permission": permission_code, "granted": granted},
                    None if previous is None else {
                        "permission": permission_code, "granted": previous
                    },
                    reason,
                )

    def create_commercial_entity(
        self, external_code: str, entity_type: str, display_name: str,
        actor_user_id: int | None = None,
    ) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO commercial_entities(external_code, entity_type, display_name)
                VALUES (?, ?, ?)
                """,
                (external_code.strip().upper(), entity_type, display_name.strip()),
            )
            entity_id = int(cursor.lastrowid)
            self._audit(
                connection, actor_user_id, "COMMERCIAL_ENTITY_CREATED",
                "COMMERCIAL_ENTITY", entity_id,
                {"code": external_code.strip().upper(), "type": entity_type},
            )
        return entity_id

    def list_commercial_entities(
        self, entity_type: str | None = None
    ) -> list[dict[str, object]]:
        query = """
            SELECT id, external_code, entity_type, display_name, is_active
            FROM commercial_entities
        """
        parameters: tuple[object, ...] = ()
        if entity_type is not None:
            query += " WHERE entity_type = ?"
            parameters = (entity_type,)
        query += " ORDER BY entity_type, display_name, external_code"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        columns = ("entity_id", "code", "entity_type", "name", "active")
        result = [dict(zip(columns, row, strict=True)) for row in rows]
        for item in result:
            item["active"] = bool(item["active"])
        return result

    def list_hierarchy_assignments(self) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT ha.id, source.id, source.external_code, source.display_name,
                       target.id, target.external_code, target.display_name,
                       ha.relationship_type, ha.valid_from, ha.valid_to,
                       ha.is_active, ha.changed_by, ha.changed_at
                FROM hierarchy_assignments ha
                JOIN commercial_entities source ON source.id = ha.source_entity_id
                JOIN commercial_entities target ON target.id = ha.target_entity_id
                ORDER BY ha.relationship_type, source.external_code, ha.valid_from DESC
                """
            ).fetchall()
        columns = (
            "assignment_id", "source_entity_id", "source_code", "source_name",
            "target_entity_id", "target_code", "target_name", "relationship_type",
            "valid_from", "valid_to", "active", "changed_by", "changed_at",
        )
        result = [dict(zip(columns, row, strict=True)) for row in rows]
        for item in result:
            item["active"] = bool(item["active"])
        return result

    def create_team_request(
        self,
        requested_by: int,
        payload: dict[str, object],
        valid_from: date | str,
    ) -> int:
        effective = _date_value(valid_from)
        required = {
            "domain_username", "commercial_code", "first_names", "last_names",
            "identification", "email", "reason",
        }
        if required - set(payload):
            raise ValueError("La solicitud de ejecutivo está incompleta")
        if not str(payload["reason"]).strip():
            raise ValueError("La observación de la solicitud es obligatoria")
        with closing(self._connect()) as connection, connection:
            managers = connection.execute(
                """
                SELECT ce.id
                FROM user_commercial_entities uce
                JOIN commercial_entities ce ON ce.id = uce.commercial_entity_id
                WHERE uce.user_id = ? AND ce.entity_type = ? AND ce.is_active = 1
                  AND uce.valid_from <= ?
                  AND COALESCE(uce.valid_to, '9999-12-31') >= ?
                """,
                (requested_by, ROLE_MANAGER, effective, effective),
            ).fetchall()
            if len(managers) != 1:
                raise ValueError(
                    "El jefe debe tener exactamente una identidad comercial vigente"
                )
            now = _utc_now()
            cursor = connection.execute(
                """
                INSERT INTO team_change_requests (
                    request_type, requested_by, manager_entity_id, payload_json,
                    valid_from, status, requested_at
                ) VALUES (?, ?, ?, ?, ?, 'PENDIENTE_APROBACION', ?)
                """,
                (
                    "NUEVO_EJECUTIVO", requested_by, int(managers[0][0]),
                    json.dumps(payload, ensure_ascii=True), effective, now,
                ),
            )
            request_id = int(cursor.lastrowid)
            self._audit(
                connection, requested_by, "TEAM_REQUEST_CREATED", "TEAM_REQUEST",
                request_id,
                {"status": "PENDIENTE_APROBACION", "valid_from": effective},
                reason=str(payload["reason"]).strip(),
            )
        return request_id

    def list_team_requests(
        self, requested_by: int | None = None, status: str | None = None
    ) -> list[dict[str, object]]:
        conditions: list[str] = []
        parameters: list[object] = []
        if requested_by is not None:
            conditions.append("r.requested_by = ?")
            parameters.append(requested_by)
        if status is not None:
            conditions.append("r.status = ?")
            parameters.append(status)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT r.id, r.request_type, r.requested_by,
                       manager.external_code, r.payload_json, r.valid_from,
                       r.status, r.requested_at, r.decided_by, r.decided_at,
                       r.decision_concept, r.created_user_id
                FROM team_change_requests r
                JOIN commercial_entities manager ON manager.id = r.manager_entity_id
                """ + where + " ORDER BY r.requested_at DESC, r.id DESC",
                tuple(parameters),
            ).fetchall()
        columns = (
            "request_id", "request_type", "requested_by", "manager_code", "payload",
            "valid_from", "status", "requested_at", "decided_by", "decided_at",
            "decision_concept", "created_user_id",
        )
        result = [dict(zip(columns, row, strict=True)) for row in rows]
        for item in result:
            payload = json.loads(str(item.pop("payload")))
            item.update(payload)
        return result

    def list_team_members(
        self, manager_user_id: int, effective_on: date | str
    ) -> list[dict[str, object]]:
        effective = _date_value(effective_on)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT executive.external_code, executive.display_name,
                       manager.external_code, ha.valid_from, ha.valid_to,
                       u.id, u.domain_username, u.identification, u.email,
                       u.is_active
                FROM user_commercial_entities manager_link
                JOIN commercial_entities manager
                  ON manager.id = manager_link.commercial_entity_id
                JOIN hierarchy_assignments ha ON ha.target_entity_id = manager.id
                JOIN commercial_entities executive ON executive.id = ha.source_entity_id
                LEFT JOIN user_commercial_entities executive_link
                  ON executive_link.commercial_entity_id = executive.id
                 AND executive_link.valid_from <= ?
                 AND COALESCE(executive_link.valid_to, '9999-12-31') >= ?
                LEFT JOIN users u ON u.id = executive_link.user_id
                WHERE manager_link.user_id = ? AND manager.entity_type = ?
                  AND manager_link.valid_from <= ?
                  AND COALESCE(manager_link.valid_to, '9999-12-31') >= ?
                  AND ha.relationship_type = ? AND ha.is_active = 1
                  AND ha.valid_from <= ?
                  AND COALESCE(ha.valid_to, '9999-12-31') >= ?
                ORDER BY executive.display_name, executive.external_code
                """,
                (
                    effective, effective, manager_user_id, ROLE_MANAGER,
                    effective, effective, REL_EXECUTIVE_MANAGER,
                    effective, effective,
                ),
            ).fetchall()
        columns = (
            "executive_code", "executive_name", "manager_code", "valid_from",
            "valid_to", "user_id", "domain_username", "identification", "email",
            "access_active",
        )
        result = [dict(zip(columns, row, strict=True)) for row in rows]
        for item in result:
            item["access_active"] = bool(item["access_active"])
        return result

    def executive_hierarchy_context(
        self, executive_codes: Iterable[str], effective_on: date | str
    ) -> list[dict[str, object]]:
        codes = sorted({str(code).strip().upper() for code in executive_codes})
        if not codes:
            return []
        effective = _date_value(effective_on)
        placeholders = ",".join("?" for _ in codes)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT executive.external_code, executive.display_name,
                       manager.external_code, manager.display_name,
                       director.external_code, director.display_name
                FROM commercial_entities executive
                LEFT JOIN hierarchy_assignments em
                  ON em.source_entity_id = executive.id
                 AND em.relationship_type = ? AND em.is_active = 1
                 AND em.valid_from <= ?
                 AND COALESCE(em.valid_to, '9999-12-31') >= ?
                LEFT JOIN commercial_entities manager ON manager.id = em.target_entity_id
                LEFT JOIN hierarchy_assignments md
                  ON md.source_entity_id = manager.id
                 AND md.relationship_type = ? AND md.is_active = 1
                 AND md.valid_from <= ?
                 AND COALESCE(md.valid_to, '9999-12-31') >= ?
                LEFT JOIN commercial_entities director ON director.id = md.target_entity_id
                WHERE executive.entity_type = ?
                  AND executive.external_code IN ({placeholders})
                ORDER BY executive.external_code
                """,
                (
                    REL_EXECUTIVE_MANAGER, effective, effective,
                    REL_MANAGER_DIRECTOR, effective, effective,
                    ROLE_EXECUTIVE, *codes,
                ),
            ).fetchall()
        columns = (
            "executive", "executive_name", "manager", "manager_name",
            "director", "director_name",
        )
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def decide_team_request(
        self, request_id: int, decided_by: int, approve: bool, concept: str
    ) -> None:
        if not str(concept).strip():
            raise ValueError("El concepto de la decisión es obligatorio")
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT requested_by, manager_entity_id, payload_json, valid_from, status
                FROM team_change_requests WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Solicitud de equipo inexistente: {request_id}")
            if str(row[4]) != "PENDIENTE_APROBACION":
                raise ValueError("La solicitud ya fue decidida")
            now = _utc_now()
            new_status = "APROBADO" if approve else "RECHAZADO"
            created_user_id = None
            if approve:
                payload = json.loads(str(row[2]))
                username = str(payload["domain_username"]).strip().lower()
                user_cursor = connection.execute(
                    """
                    INSERT INTO users (
                        domain_username, identification, first_names, last_names,
                        email, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        username, payload.get("identification"),
                        str(payload["first_names"]).strip(),
                        str(payload["last_names"]).strip(), payload.get("email"),
                        now, now,
                    ),
                )
                created_user_id = int(user_cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO user_roles
                    (user_id, role_code, assigned_at, assigned_by, is_active)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (created_user_id, ROLE_EXECUTIVE, now, decided_by),
                )
                entity_cursor = connection.execute(
                    """
                    INSERT INTO commercial_entities
                    (external_code, entity_type, display_name)
                    VALUES (?, ?, ?)
                    """,
                    (
                        str(payload["commercial_code"]).strip().upper(),
                        ROLE_EXECUTIVE,
                        f"{str(payload['first_names']).strip()} "
                        f"{str(payload['last_names']).strip()}",
                    ),
                )
                entity_id = int(entity_cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO user_commercial_entities
                    (user_id, commercial_entity_id, valid_from, valid_to)
                    VALUES (?, ?, ?, NULL)
                    """,
                    (created_user_id, entity_id, str(row[3])),
                )
                hierarchy_cursor = connection.execute(
                    """
                    INSERT INTO hierarchy_assignments (
                        source_entity_id, target_entity_id, relationship_type,
                        valid_from, valid_to, changed_by, changed_at
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        entity_id, int(row[1]), REL_EXECUTIVE_MANAGER,
                        str(row[3]), decided_by, now,
                    ),
                )
                self._audit(
                    connection, decided_by, "TEAM_REQUEST_MATERIALIZED", "USER",
                    created_user_id,
                    {
                        "commercial_entity_id": entity_id,
                        "hierarchy_assignment_id": int(hierarchy_cursor.lastrowid),
                        "manager_entity_id": int(row[1]),
                    },
                    reason=str(concept).strip(),
                )
            connection.execute(
                """
                UPDATE team_change_requests
                SET status = ?, decided_by = ?, decided_at = ?,
                    decision_concept = ?, created_user_id = ?
                WHERE id = ? AND status = 'PENDIENTE_APROBACION'
                """,
                (
                    new_status, decided_by, now, str(concept).strip(),
                    created_user_id, request_id,
                ),
            )
            self._audit(
                connection, decided_by, "TEAM_REQUEST_DECIDED", "TEAM_REQUEST",
                request_id, {"status": new_status, "created_user_id": created_user_id},
                {"status": "PENDIENTE_APROBACION"}, str(concept).strip(),
            )

    def link_user_entity(
        self,
        user_id: int,
        entity_id: int,
        valid_from: date | str,
        valid_to: date | str | None = None,
        actor_user_id: int | None = None,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO user_commercial_entities (
                    user_id, commercial_entity_id, valid_from, valid_to
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    user_id, entity_id, _date_value(valid_from),
                    _date_value(valid_to) if valid_to else None,
                ),
            )
            self._audit(
                connection, actor_user_id, "USER_COMMERCIAL_ENTITY_LINKED",
                "USER", user_id,
                {
                    "commercial_entity_id": entity_id,
                    "valid_from": _date_value(valid_from),
                    "valid_to": _date_value(valid_to) if valid_to else None,
                },
            )

    def assign_hierarchy(
        self,
        source_entity_id: int,
        target_entity_id: int,
        relationship_type: str,
        valid_from: date | str,
        valid_to: date | str | None = None,
        changed_by: int | None = None,
    ) -> int:
        if relationship_type not in {REL_EXECUTIVE_MANAGER, REL_MANAGER_DIRECTOR}:
            raise ValueError(f"Tipo de relacion no soportado: {relationship_type}")
        start = _date_value(valid_from)
        end = _date_value(valid_to) if valid_to else None
        with closing(self._connect()) as connection, connection:
            expected_types = {
                REL_EXECUTIVE_MANAGER: (ROLE_EXECUTIVE, ROLE_MANAGER),
                REL_MANAGER_DIRECTOR: (ROLE_MANAGER, ROLE_DIRECTOR),
            }
            entities = connection.execute(
                "SELECT id, entity_type FROM commercial_entities WHERE id IN (?, ?)",
                (source_entity_id, target_entity_id),
            ).fetchall()
            entity_types = {int(row[0]): str(row[1]) for row in entities}
            if len(entity_types) != 2:
                raise KeyError("Entidad comercial de origen o destino inexistente")
            source_type, target_type = expected_types[relationship_type]
            if (
                entity_types[source_entity_id] != source_type
                or entity_types[target_entity_id] != target_type
            ):
                raise ValueError("Los tipos de entidad no corresponden a la relacion")
            overlap = connection.execute(
                """
                SELECT id FROM hierarchy_assignments
                WHERE source_entity_id = ? AND relationship_type = ? AND is_active = 1
                  AND COALESCE(valid_to, '9999-12-31') >= ?
                  AND COALESCE(?, '9999-12-31') >= valid_from
                LIMIT 1
                """,
                (source_entity_id, relationship_type, start, end),
            ).fetchone()
            if overlap is not None:
                raise ValueError(
                    "La entidad ya tiene una relacion jerarquica vigente que se superpone"
                )
            cursor = connection.execute(
                """
                INSERT INTO hierarchy_assignments (
                    source_entity_id, target_entity_id, relationship_type,
                    valid_from, valid_to, changed_by, changed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_entity_id, target_entity_id, relationship_type,
                    start, end, changed_by, _utc_now(),
                ),
            )
            assignment_id = int(cursor.lastrowid)
            self._audit(
                connection, changed_by, "HIERARCHY_ASSIGNED", "HIERARCHY",
                assignment_id,
                {
                    "source": source_entity_id,
                    "target": target_entity_id,
                    "relationship": relationship_type,
                    "valid_from": start,
                    "valid_to": end,
                },
            )
        return assignment_id

    def reassign_hierarchy(
        self,
        source_entity_id: int,
        new_target_entity_id: int,
        relationship_type: str,
        effective_from: date | str,
        changed_by: int,
        reason: str,
    ) -> int:
        if relationship_type not in {REL_EXECUTIVE_MANAGER, REL_MANAGER_DIRECTOR}:
            raise ValueError(f"Tipo de relacion no soportado: {relationship_type}")
        if not str(reason).strip():
            raise ValueError("El motivo de la reasignacion es obligatorio")
        start = _date_value(effective_from)
        expected_types = {
            REL_EXECUTIVE_MANAGER: (ROLE_EXECUTIVE, ROLE_MANAGER),
            REL_MANAGER_DIRECTOR: (ROLE_MANAGER, ROLE_DIRECTOR),
        }
        with closing(self._connect()) as connection, connection:
            entities = connection.execute(
                "SELECT id, entity_type FROM commercial_entities WHERE id IN (?, ?)",
                (source_entity_id, new_target_entity_id),
            ).fetchall()
            entity_types = {int(row[0]): str(row[1]) for row in entities}
            if len(entity_types) != 2:
                raise KeyError("Entidad comercial de origen o destino inexistente")
            source_type, target_type = expected_types[relationship_type]
            if (
                entity_types[source_entity_id] != source_type
                or entity_types[new_target_entity_id] != target_type
            ):
                raise ValueError("Los tipos de entidad no corresponden a la relacion")

            current = connection.execute(
                """
                SELECT id, target_entity_id, valid_from, valid_to
                FROM hierarchy_assignments
                WHERE source_entity_id = ? AND relationship_type = ?
                  AND is_active = 1 AND valid_from <= ?
                  AND COALESCE(valid_to, '9999-12-31') >= ?
                """,
                (source_entity_id, relationship_type, start, start),
            ).fetchone()
            if current is None:
                raise ValueError("No existe una relacion jerarquica vigente para reasignar")
            if int(current[1]) == new_target_entity_id:
                raise ValueError("La entidad ya esta asignada al destino indicado")
            if start <= str(current[2]):
                raise ValueError(
                    "La nueva vigencia debe ser posterior al inicio de la relacion actual"
                )

            future = connection.execute(
                """
                SELECT id FROM hierarchy_assignments
                WHERE source_entity_id = ? AND relationship_type = ?
                  AND is_active = 1 AND valid_from > ?
                LIMIT 1
                """,
                (source_entity_id, relationship_type, start),
            ).fetchone()
            if future is not None:
                raise ValueError(
                    "Existe una relacion jerarquica futura; debe resolverse antes de reasignar"
                )

            previous_end = (
                date.fromisoformat(start) - timedelta(days=1)
            ).isoformat()
            connection.execute(
                "UPDATE hierarchy_assignments SET valid_to = ? WHERE id = ?",
                (previous_end, current[0]),
            )
            cursor = connection.execute(
                """
                INSERT INTO hierarchy_assignments (
                    source_entity_id, target_entity_id, relationship_type,
                    valid_from, valid_to, changed_by, changed_at
                ) VALUES (?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    source_entity_id, new_target_entity_id, relationship_type,
                    start, changed_by, _utc_now(),
                ),
            )
            assignment_id = int(cursor.lastrowid)
            self._audit(
                connection,
                changed_by,
                "HIERARCHY_REASSIGNED",
                "HIERARCHY",
                assignment_id,
                {
                    "source": source_entity_id,
                    "target": new_target_entity_id,
                    "relationship": relationship_type,
                    "valid_from": start,
                },
                previous_value={
                    "assignment_id": int(current[0]),
                    "target": int(current[1]),
                    "valid_from": str(current[2]),
                    "valid_to": previous_end,
                },
                reason=reason.strip(),
            )
        return assignment_id

    def get_identity(self, domain_username: str) -> UserIdentity | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT id, domain_username, is_active FROM users WHERE domain_username = ?",
                (domain_username.strip().lower(),),
            ).fetchone()
        if row is None:
            return None
        return UserIdentity(int(row[0]), str(row[1]), bool(row[2]))

    def record_last_access(self, user_id: int) -> None:
        now = _utc_now()
        with closing(self._connect()) as connection, connection:
            updated = connection.execute(
                "UPDATE users SET last_access_at = ?, updated_at = ? WHERE id = ?",
                (now, now, user_id),
            ).rowcount
            if updated != 1:
                raise KeyError(f"Usuario inexistente: {user_id}")

    def effective_permissions(self, user_id: int) -> set[str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT rp.permission_code
                FROM user_roles ur
                JOIN role_permissions rp ON rp.role_code = ur.role_code
                WHERE ur.user_id = ? AND ur.is_active = 1
                UNION
                SELECT permission_code
                FROM user_permissions
                WHERE user_id = ? AND granted = 1
                """,
                (user_id, user_id),
            ).fetchall()
            denied = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT permission_code FROM user_permissions
                    WHERE user_id = ? AND granted = 0
                    """,
                    (user_id,),
                ).fetchall()
            }
        return {row[0] for row in rows} - denied

    def effective_roles(self, user_id: int) -> set[str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT role_code FROM user_roles
                WHERE user_id = ? AND is_active = 1
                """,
                (user_id,),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def allowed_executive_codes(
        self, user_id: int, effective_on: date | str
    ) -> set[str] | None:
        permissions = self.effective_permissions(user_id)
        if PERM_VIEW_ANY in permissions:
            return None
        when = _date_value(effective_on)
        with closing(self._connect()) as connection:
            own_rows = connection.execute(
                """
                SELECT ce.id, ce.external_code, ce.entity_type
                FROM user_commercial_entities uce
                JOIN commercial_entities ce ON ce.id = uce.commercial_entity_id
                WHERE uce.user_id = ? AND ce.is_active = 1
                  AND uce.valid_from <= ?
                  AND COALESCE(uce.valid_to, '9999-12-31') >= ?
                """,
                (user_id, when, when),
            ).fetchall()
            own_ids = {row[0] for row in own_rows}
            allowed = {
                row[1] for row in own_rows if row[2] == ROLE_EXECUTIVE
            }
            if PERM_VIEW_TEAM not in permissions or not own_ids:
                return allowed

            placeholders = ",".join("?" for _ in own_ids)
            direct = connection.execute(
                f"""
                SELECT DISTINCT ce.id, ce.external_code
                FROM hierarchy_assignments ha
                JOIN commercial_entities ce ON ce.id = ha.source_entity_id
                WHERE ha.target_entity_id IN ({placeholders})
                  AND ha.relationship_type = ? AND ha.is_active = 1
                  AND ha.valid_from <= ?
                  AND COALESCE(ha.valid_to, '9999-12-31') >= ?
                """,
                (*own_ids, REL_EXECUTIVE_MANAGER, when, when),
            ).fetchall()
            allowed.update(row[1] for row in direct)

            managers = connection.execute(
                f"""
                SELECT DISTINCT ha.source_entity_id
                FROM hierarchy_assignments ha
                WHERE ha.target_entity_id IN ({placeholders})
                  AND ha.relationship_type = ? AND ha.is_active = 1
                  AND ha.valid_from <= ?
                  AND COALESCE(ha.valid_to, '9999-12-31') >= ?
                """,
                (*own_ids, REL_MANAGER_DIRECTOR, when, when),
            ).fetchall()
            manager_ids = {row[0] for row in managers}
            if manager_ids:
                manager_placeholders = ",".join("?" for _ in manager_ids)
                indirect = connection.execute(
                    f"""
                    SELECT DISTINCT ce.external_code
                    FROM hierarchy_assignments ha
                    JOIN commercial_entities ce ON ce.id = ha.source_entity_id
                    WHERE ha.target_entity_id IN ({manager_placeholders})
                      AND ha.relationship_type = ? AND ha.is_active = 1
                      AND ha.valid_from <= ?
                      AND COALESCE(ha.valid_to, '9999-12-31') >= ?
                    """,
                    (*manager_ids, REL_EXECUTIVE_MANAGER, when, when),
                ).fetchall()
                allowed.update(row[0] for row in indirect)
        return allowed


class _PostgresInsertCursor:
    def __init__(self, generated_id: int) -> None:
        self.lastrowid = generated_id


class _PostgresAccessConnection:
    """Compatibility layer for the repository's existing DB-API operations."""

    _IDENTITY_TABLES = {
        "users",
        "employment_status_history",
        "commercial_entities",
        "hierarchy_assignments",
        "audit_events",
    }

    def __init__(self, database: Database) -> None:
        self._context = database.session()
        self._session = self._context.__enter__()
        self._finished = False

    def __enter__(self) -> "_PostgresAccessConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._finished:
            self._context.__exit__(exc_type, exc, traceback)
            self._finished = True

    def close(self) -> None:
        if not self._finished:
            self._context.__exit__(None, None, None)
            self._finished = True

    def execute(self, sql: str, parameters: tuple[object, ...] = ()):
        normalized = " ".join(sql.strip().split()).lower()
        for table in self._IDENTITY_TABLES:
            if normalized.startswith(f"insert into {table} "):
                generated_id = self._session.insert_returning_id(sql, parameters)
                return _PostgresInsertCursor(generated_id)
        return self._session.execute(sql, parameters)

    def executemany(self, sql: str, rows: Iterable[tuple[object, ...]]) -> None:
        for parameters in rows:
            self.execute(sql, parameters)

    def executescript(self, script: str) -> None:
        statements = tuple(
            statement.strip() for statement in script.split(";") if statement.strip()
        )
        self._session.execute_statements(statements)


class PostgresAccessRepository(SqliteAccessRepository):
    """Production access repository with the same RBAC and hierarchy behavior."""

    def __init__(self, dsn: str) -> None:
        self.database = Database.postgresql(dsn)
        self._ensure_schema()
        self._seed_permissions()

    def _connect(self) -> _PostgresAccessConnection:
        return _PostgresAccessConnection(self.database)

    def _identity_primary_key(self) -> str:
        return self.database.identity_primary_key()


class AuthorizationService:
    def __init__(self, repository: SqliteAccessRepository) -> None:
        self.repository = repository

    def require_active_identity(self, domain_username: str) -> UserIdentity:
        identity = self.repository.get_identity(domain_username)
        if identity is None or not identity.active:
            raise AccessDeniedError("El usuario no existe o esta inactivo")
        return identity

    def require_permission(self, user_id: int, permission: str) -> None:
        if permission not in self.repository.effective_permissions(user_id):
            raise AccessDeniedError(f"Permiso requerido: {permission}")

    def effective_roles(self, user_id: int) -> set[str]:
        return self.repository.effective_roles(user_id)

    def require_any_role(self, user_id: int, allowed_roles: Iterable[str]) -> None:
        allowed = set(allowed_roles)
        if not self.effective_roles(user_id).intersection(allowed):
            raise AccessDeniedError(
                "El perfil no esta autorizado para ejecutar esta accion"
            )

    def require_executive_access(
        self, user_id: int, executive_code: str, effective_on: date | str
    ) -> None:
        scope = self.repository.allowed_executive_codes(user_id, effective_on)
        if scope is not None and executive_code.strip().upper() not in scope:
            raise AccessDeniedError("El ejecutivo solicitado esta fuera del alcance")

    def filter_executives(
        self,
        user_id: int,
        requested_codes: Iterable[str],
        effective_on: date | str,
    ) -> set[str]:
        requested = {code.strip().upper() for code in requested_codes}
        scope = self.repository.allowed_executive_codes(user_id, effective_on)
        if scope is None:
            return requested
        return requested & scope

    def executive_scope(
        self, user_id: int, effective_on: date | str
    ) -> set[str] | None:
        """Return effective backend scope; None represents authorized global access."""
        return self.repository.allowed_executive_codes(user_id, effective_on)

    def hierarchy_context(
        self,
        user_id: int,
        executive_codes: Iterable[str],
        effective_on: date | str,
    ) -> list[dict[str, object]]:
        allowed = self.filter_executives(user_id, executive_codes, effective_on)
        return self.repository.executive_hierarchy_context(allowed, effective_on)


class EmploymentStatusService:
    def __init__(
        self,
        repository: SqliteAccessRepository,
        authorization: AuthorizationService,
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    def change(
        self,
        actor_user_id: int,
        user_id: int,
        status: str,
        effective_from: date | str,
        reason: str,
    ) -> None:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        self.repository.set_employment_status(
            user_id, status, effective_from, actor_user_id, reason
        )


class UserAdministrationService:
    def __init__(
        self,
        repository: SqliteAccessRepository,
        authorization: AuthorizationService,
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    def list_profiles(
        self, actor_user_id: int, effective_on: date | str
    ) -> list[UserProfile]:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        return self.repository.list_user_profiles(effective_on)

    def create(
        self,
        actor_user_id: int,
        domain_username: str,
        first_names: str,
        last_names: str,
        roles: Iterable[str],
        identification: str | None = None,
        email: str | None = None,
        ad_identifier: str | None = None,
        reason: str = "",
    ) -> int:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        if not str(reason).strip():
            raise ValueError("El motivo de creación es obligatorio")
        selected_roles = tuple({str(role).strip().upper() for role in roles})
        if not selected_roles or set(selected_roles) - set(ROLE_PERMISSIONS):
            raise ValueError("Debe seleccionar únicamente roles válidos")
        user_id = self.repository.create_user(
            domain_username, first_names, last_names, identification, email,
            ad_identifier, actor_user_id,
        )
        self.repository.replace_roles(user_id, selected_roles, actor_user_id, reason)
        return user_id

    def update(
        self,
        actor_user_id: int,
        user_id: int,
        identification: str | None,
        first_names: str,
        last_names: str,
        email: str | None,
        ad_identifier: str | None,
        roles: Iterable[str],
        reason: str,
    ) -> None:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        self.repository.update_user_profile(
            user_id, identification, first_names, last_names, email,
            ad_identifier, actor_user_id, reason,
        )
        self.repository.replace_roles(user_id, roles, actor_user_id, reason)

    def change_employment_status(
        self,
        actor_user_id: int,
        user_id: int,
        status: str,
        effective_from: date | str,
        reason: str,
    ) -> None:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        self.repository.set_employment_status(
            user_id, status, effective_from, actor_user_id, reason
        )

    def create_commercial_identity(
        self,
        actor_user_id: int,
        user_id: int,
        external_code: str,
        entity_type: str,
        display_name: str,
        valid_from: date | str,
        valid_to: date | str | None = None,
    ) -> int:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        if entity_type not in {ROLE_EXECUTIVE, ROLE_MANAGER, ROLE_DIRECTOR}:
            raise ValueError("Tipo de entidad comercial inválido")
        entity_id = self.repository.create_commercial_entity(
            external_code, entity_type, display_name, actor_user_id
        )
        self.repository.link_user_entity(
            user_id, entity_id, valid_from, valid_to, actor_user_id
        )
        return entity_id

    def set_active(
        self,
        actor_user_id: int,
        user_id: int,
        active: bool,
        reason: str,
    ) -> None:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        self.repository.set_user_active(user_id, active, actor_user_id, reason)

    def set_independent_permissions(
        self,
        actor_user_id: int,
        user_id: int,
        permissions: dict[str, bool],
        reason: str,
    ) -> None:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        if not str(reason).strip():
            raise ValueError("La observación del cambio de permisos es obligatoria")
        allowed = {
            PERM_NETSUITE_SYNC,
            PERM_CALCULATION_EXECUTE,
            PERM_PAYMENT_FILE_SEND,
        }
        invalid = set(permissions) - allowed
        if invalid:
            raise ValueError(
                f"Permisos independientes no administrables: {sorted(invalid)}"
            )
        self.repository.set_user_permissions(
            user_id,
            permissions,
            actor_user_id,
            reason.strip(),
        )


class HierarchyService:
    def __init__(
        self,
        repository: SqliteAccessRepository,
        authorization: AuthorizationService,
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    def list_entities(
        self, actor_user_id: int, entity_type: str | None = None
    ) -> list[dict[str, object]]:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        return self.repository.list_commercial_entities(entity_type)

    def list_assignments(self, actor_user_id: int) -> list[dict[str, object]]:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        return self.repository.list_hierarchy_assignments()

    def assign(
        self,
        actor_user_id: int,
        source_entity_id: int,
        target_entity_id: int,
        relationship_type: str,
        valid_from: date | str,
        valid_to: date | str | None = None,
    ) -> int:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        return self.repository.assign_hierarchy(
            source_entity_id, target_entity_id, relationship_type,
            valid_from, valid_to, actor_user_id,
        )

    def reassign(
        self,
        actor_user_id: int,
        source_entity_id: int,
        new_target_entity_id: int,
        relationship_type: str,
        effective_from: date | str,
        reason: str,
    ) -> int:
        self.authorization.require_permission(actor_user_id, PERM_USERS_MANAGE)
        return self.repository.reassign_hierarchy(
            source_entity_id,
            new_target_entity_id,
            relationship_type,
            effective_from,
            actor_user_id,
            reason,
        )


class TeamChangeRequestService:
    def __init__(
        self,
        repository: SqliteAccessRepository,
        authorization: AuthorizationService,
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    def propose_new_executive(
        self,
        actor_user_id: int,
        domain_username: str,
        commercial_code: str,
        first_names: str,
        last_names: str,
        identification: str | None,
        email: str | None,
        valid_from: date | str,
        reason: str,
    ) -> int:
        self.authorization.require_permission(actor_user_id, PERM_TEAM_PROPOSE)
        required = {
            "Usuario de dominio": domain_username,
            "Código comercial": commercial_code,
            "Nombres": first_names,
            "Apellidos": last_names,
            "Motivo": reason,
        }
        missing = [label for label, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"Campos obligatorios: {', '.join(missing)}")
        return self.repository.create_team_request(
            actor_user_id,
            {
                "domain_username": domain_username.strip().lower(),
                "commercial_code": commercial_code.strip().upper(),
                "first_names": first_names.strip(),
                "last_names": last_names.strip(),
                "identification": identification or None,
                "email": email or None,
                "reason": reason.strip(),
            },
            valid_from,
        )

    def list_for_actor(self, actor_user_id: int) -> list[dict[str, object]]:
        permissions = self.repository.effective_permissions(actor_user_id)
        if PERM_TEAM_APPROVE in permissions:
            return self.repository.list_team_requests()
        self.authorization.require_permission(actor_user_id, PERM_TEAM_PROPOSE)
        return self.repository.list_team_requests(requested_by=actor_user_id)

    def list_team(
        self, actor_user_id: int, effective_on: date | str
    ) -> list[dict[str, object]]:
        self.authorization.require_permission(actor_user_id, PERM_TEAM_PROPOSE)
        return self.repository.list_team_members(actor_user_id, effective_on)

    def decide(
        self, actor_user_id: int, request_id: int, approve: bool, concept: str
    ) -> None:
        self.authorization.require_permission(actor_user_id, PERM_TEAM_APPROVE)
        self.repository.decide_team_request(
            request_id, actor_user_id, approve, concept
        )
