"""Periodos de negocio, cierres y reaperturas con historia auditable."""

from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from .access_control import (
    AuthorizationService,
    PERM_PERIOD_CLOSE,
    PERM_PERIOD_REOPEN,
)
from .database import Database, POSTGRESQL


PERIOD_OPEN = "ABIERTO"
PERIOD_IN_REVIEW = "EN_REVISION"
PERIOD_FINALIZED = "FINALIZADO"
PERIOD_CLOSED = "CERRADO"

_FORWARD_TRANSITIONS = {
    PERIOD_OPEN: PERIOD_IN_REVIEW,
    PERIOD_IN_REVIEW: PERIOD_FINALIZED,
    PERIOD_FINALIZED: PERIOD_CLOSED,
}
_ALLOWED_TRANSITIONS = set(_FORWARD_TRANSITIONS.items()) | {
    (PERIOD_CLOSED, PERIOD_OPEN)
}


@dataclass(frozen=True)
class BusinessPeriod:
    period: str
    status: str
    created_by: int
    created_at: str
    modified_by: int
    modified_at: str


@dataclass(frozen=True)
class BusinessPeriodHistory:
    history_id: int
    period: str
    previous_status: str | None
    new_status: str
    changed_by: int
    changed_at: str
    reason: str | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_period(period: str) -> str:
    value = str(period).strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if match is None:
        raise ValueError("El periodo debe tener formato AAAA-MM (YYYY-MM)")
    try:
        year, month = (int(part) for part in match.groups())
        date(year, month, 1)
    except ValueError as exc:
        raise ValueError("El periodo debe tener formato AAAA-MM (YYYY-MM)") from exc
    return value


class BusinessPeriodRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        history_id = self.database.identity_primary_key()
        with self.database.session() as session:
            session.execute_statements((
                """
                CREATE TABLE IF NOT EXISTS business_periods (
                    period TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    modified_by INTEGER NOT NULL,
                    modified_at TEXT NOT NULL
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS business_period_status_history (
                    id {history_id},
                    period TEXT NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    changed_by INTEGER NOT NULL,
                    changed_at TEXT NOT NULL,
                    reason TEXT,
                    FOREIGN KEY (period) REFERENCES business_periods(period)
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_business_period_history
                ON business_period_status_history(period, changed_at, id)
                """,
            ))

    def create(self, period: str, actor_user_id: int) -> None:
        normalized = normalize_period(period)
        now = _utc_now()
        with self.database.session() as session:
            session.execute(
                """
                INSERT INTO business_periods
                (period, status, created_by, created_at, modified_by, modified_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized, PERIOD_OPEN, actor_user_id, now, actor_user_id, now),
            )
            session.execute(
                """
                INSERT INTO business_period_status_history
                (period, previous_status, new_status, changed_by, changed_at, reason)
                VALUES (?, NULL, ?, ?, ?, ?)
                """,
                (normalized, PERIOD_OPEN, actor_user_id, now, "Creacion del periodo"),
            )

    def status(self, period: str) -> str | None:
        normalized = normalize_period(period)
        with self.database.session() as session:
            row = session.execute(
                "SELECT status FROM business_periods WHERE period = ?", (normalized,)
            ).fetchone()
        return str(row[0]) if row is not None else None

    def list_periods(
        self, statuses: Iterable[str] | None = None
    ) -> list[BusinessPeriod]:
        query = """
            SELECT period, status, created_by, created_at, modified_by, modified_at
            FROM business_periods
        """
        parameters: tuple[object, ...] = ()
        if statuses is not None:
            selected = sorted({str(value).strip().upper() for value in statuses if str(value).strip()})
            if not selected:
                return []
            placeholders = ",".join("?" for _ in selected)
            query += f" WHERE UPPER(status) IN ({placeholders})"
            parameters = tuple(selected)
        query += " ORDER BY period DESC"
        with self.database.session() as session:
            rows = session.execute(query, parameters).fetchall()
        return [BusinessPeriod(*row) for row in rows]

    def history(self, period: str) -> list[BusinessPeriodHistory]:
        normalized = normalize_period(period)
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT id, period, previous_status, new_status, changed_by,
                       changed_at, reason
                FROM business_period_status_history
                WHERE period = ? ORDER BY changed_at, id
                """,
                (normalized,),
            ).fetchall()
        return [BusinessPeriodHistory(*row) for row in rows]

    def transition(
        self,
        period: str,
        expected: str,
        new_status: str,
        actor_user_id: int,
        reason: str,
    ) -> None:
        normalized = normalize_period(period)
        if not str(reason).strip():
            raise ValueError("El motivo del cambio de estado es obligatorio")
        if (expected, new_status) not in _ALLOWED_TRANSITIONS:
            raise ValueError(
                f"Transición de periodo inválida: {expected} -> {new_status}"
            )
        now = _utc_now()
        with self.database.session() as session:
            session.begin_write()
            lock = " FOR UPDATE" if self.database.backend == POSTGRESQL else ""
            row = session.execute(
                "SELECT status FROM business_periods WHERE period = ?" + lock,
                (normalized,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Periodo inexistente: {normalized}")
            if row[0] != expected:
                raise ValueError(f"Estado esperado {expected}; estado actual {row[0]}")
            session.execute(
                """
                UPDATE business_periods
                SET status = ?, modified_by = ?, modified_at = ?
                WHERE period = ?
                """,
                (new_status, actor_user_id, now, normalized),
            )
            session.execute(
                """
                INSERT INTO business_period_status_history
                (period, previous_status, new_status, changed_by, changed_at, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized, expected, new_status, actor_user_id, now, reason.strip()),
            )

    def require_recalculation_allowed(self, period: str) -> None:
        status = self.status(period)
        if status is None:
            raise KeyError(f"Periodo inexistente: {normalize_period(period)}")
        if status not in {PERIOD_OPEN, PERIOD_IN_REVIEW}:
            raise RuntimeError(
                f"El periodo {normalize_period(period)} esta {status}; debe reabrirse para recalcular"
            )


class SqliteBusinessPeriodRepository(BusinessPeriodRepository):
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        super().__init__(Database.sqlite(self.database_path))


class PostgresBusinessPeriodRepository(BusinessPeriodRepository):
    def __init__(self, dsn: str) -> None:
        super().__init__(Database.postgresql(dsn))


class BusinessPeriodService:
    def __init__(
        self,
        repository: BusinessPeriodRepository,
        authorization: AuthorizationService,
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    def create(self, actor_user_id: int, period: str) -> None:
        self.authorization.require_permission(actor_user_id, PERM_PERIOD_CLOSE)
        self.repository.create(period, actor_user_id)

    def list_periods(
        self, actor_user_id: int, statuses: Iterable[str] | None = None
    ) -> list[BusinessPeriod]:
        self.authorization.require_permission(actor_user_id, PERM_PERIOD_CLOSE)
        return self.repository.list_periods(statuses)

    def history(
        self, actor_user_id: int, period: str
    ) -> list[BusinessPeriodHistory]:
        self.authorization.require_permission(actor_user_id, PERM_PERIOD_CLOSE)
        return self.repository.history(period)

    def advance(self, actor_user_id: int, period: str, reason: str) -> str:
        self.authorization.require_permission(actor_user_id, PERM_PERIOD_CLOSE)
        current = self.repository.status(period)
        if current not in _FORWARD_TRANSITIONS:
            if current is None:
                raise KeyError(f"Periodo inexistente: {normalize_period(period)}")
            raise ValueError(f"El periodo {period} no puede avanzar desde {current}")
        target = _FORWARD_TRANSITIONS[current]
        self.repository.transition(period, current, target, actor_user_id, reason)
        return target

    def reopen(self, actor_user_id: int, period: str, reason: str) -> None:
        self.authorization.require_permission(actor_user_id, PERM_PERIOD_REOPEN)
        self.repository.transition(
            period, PERIOD_CLOSED, PERIOD_OPEN, actor_user_id, reason
        )
