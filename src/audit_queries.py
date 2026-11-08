"""Consulta central append-only sobre historiales existentes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from .access_control import (
    AccessDeniedError,
    AuthorizationService,
    CALCULATION_OPERATOR_ROLES,
    PERM_AUDIT_VIEW,
    PERM_CALCULATION_APPROVE,
    PERM_CALCULATION_EXECUTE,
    ROLE_ADMIN,
    ROLE_PARAMETERIZER,
)
from .database import Database


@dataclass(frozen=True)
class AuditEvent:
    occurred_at: str
    actor_user_id: str | None
    action: str
    entity_type: str
    entity_id: str | None
    previous_value: str | None
    new_value: str | None
    reason: str | None


@dataclass(frozen=True)
class AuditPage:
    items: tuple[AuditEvent, ...]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size


@dataclass(frozen=True)
class CalculationRunEvent:
    run_code: str
    period: str | None
    rules_version: str
    status: str
    workflow_status: str | None
    started_at: str
    finished_at: str | None
    executed_by: str
    commission_rows: int
    floor_rows: int
    payment_rows: int
    discount_rows: int
    warning_count: int
    error_count: int
    message: str | None


@dataclass(frozen=True)
class CalculationRunPage:
    items: tuple[CalculationRunEvent, ...]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size


_EVENTS_SQL = """
    SELECT occurred_at AS occurred_at,
           CAST(actor_user_id AS TEXT) AS actor_user_id,
           action AS action,
           entity_type AS entity_type,
           CAST(entity_id AS TEXT) AS entity_id,
           previous_json AS previous_value,
           new_json AS new_value,
           reason AS reason
    FROM audit_events
    UNION ALL
    SELECT changed_at, CAST(changed_by AS TEXT), 'PERIOD_STATUS_CHANGED',
           'BUSINESS_PERIOD', period, previous_status, new_status, reason
    FROM business_period_status_history
    UNION ALL
    SELECT changed_at, CAST(changed_by AS TEXT), 'REVIEW_STATUS_CHANGED',
           'REVIEW_REQUEST', CAST(request_id AS TEXT), previous_status,
           new_status, concept
    FROM review_request_status_history
    UNION ALL
    SELECT changed_at, CAST(changed_by AS TEXT), 'EXCEPTION_STATUS_CHANGED',
           'COMMISSION_EXCEPTION', CAST(exception_id AS TEXT), previous_status,
           new_status, concept
    FROM exception_status_history
    UNION ALL
    SELECT changed_at, CAST(changed_by AS TEXT), 'PARAMETER_STATUS_CHANGED',
           'RULE_PARAMETER', CAST(parameter_version_id AS TEXT), previous_status,
           new_status, concept
    FROM rule_parameter_status_history
    UNION ALL
    SELECT changed_at, CAST(changed_by AS TEXT), 'PARAMETER_ENABLED_CHANGED',
           'RULE_PARAMETER', parameter_code,
           CAST(previous_enabled AS TEXT), CAST(new_enabled AS TEXT), reason
    FROM rule_definition_enabled_history
    UNION ALL
    SELECT changed_at, CAST(changed_by AS TEXT), 'TARGET_STATUS_CHANGED',
           'TARGET_BATCH', CAST(batch_id AS TEXT), previous_status,
           new_status, observation
    FROM target_status_history
    UNION ALL
    SELECT changed_at, CAST(changed_by AS TEXT), 'CUT_STATUS_CHANGED',
           'CUT_PERIOD_BATCH', CAST(batch_id AS TEXT), previous_status,
           new_status, observation
    FROM cut_period_status_history
    UNION ALL
    SELECT COALESCE(finished_at, started_at), executed_by, 'NETSUITE_SYNC',
           'SYNC_RUN', run_code, NULL, status, message
    FROM netsuite_sync_runs
    UNION ALL
    SELECT COALESCE(finished_at, started_at), executed_by, 'CALCULATION_RUN',
           'CALCULATION', run_code, NULL, status, message
    FROM calculation_runs
    UNION ALL
    SELECT changed_at, CAST(changed_by AS TEXT), 'CALCULATION_STATUS_CHANGED',
           'CALCULATION', run_code, previous_status, new_status, reason
    FROM calculation_workflow_history
    UNION ALL
    SELECT finalized_at, CAST(finalized_by AS TEXT), 'PAYMENT_FILE_FINALIZED',
           'PAYMENT_BATCH', CAST(id AS TEXT), NULL, status, NULL
    FROM payment_batches
    UNION ALL
    SELECT downloaded_at, CAST(downloaded_by AS TEXT), 'PAYMENT_FILE_DOWNLOADED',
           'PAYMENT_BATCH', CAST(batch_id AS TEXT), NULL, 'DESCARGADO', NULL
    FROM payment_download_events
    UNION ALL
    SELECT sent_at, CAST(sender_user_id AS TEXT), 'PAYMENT_FILE_SENT',
           'PAYMENT_BATCH', CAST(batch_id AS TEXT), NULL, status, error_message
    FROM payment_file_send_events
    UNION ALL
    SELECT created_at, created_by, 'CANCELLATION_DISCOUNT_CREATED',
           'CANCELLATION_DISCOUNT', CAST(id AS TEXT), NULL, status,
           credit_notes
    FROM cancellation_discounts
    UNION ALL
    SELECT applied_at, applied_by, 'CANCELLATION_DISCOUNT_APPLIED',
           'CANCELLATION_DISCOUNT', CAST(discount_id AS TEXT), NULL,
           CAST(amount AS TEXT), observation
    FROM cancellation_discount_applications
"""

_EVENT_COLUMNS = (
    "occurred_at",
    "actor_user_id",
    "action",
    "entity_type",
    "entity_id",
    "previous_value",
    "new_value",
    "reason",
)


class AuditQueryService:
    _SORTS = {
        "occurred_at": "occurred_at",
        "action": "action",
        "entity_type": "entity_type",
        "actor_user_id": "actor_user_id",
    }

    def __init__(
        self, database: Database, authorization: AuthorizationService
    ) -> None:
        self.database = database
        self.authorization = authorization

    def query(
        self,
        actor_user_id: int,
        *,
        actions: Iterable[str] | None = None,
        entity_types: Iterable[str] | None = None,
        actors: Iterable[str | int] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        search: str | None = None,
        sort_by: str = "occurred_at",
        descending: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> AuditPage:
        self.authorization.require_permission(actor_user_id, PERM_AUDIT_VIEW)
        if page < 1:
            raise ValueError("page debe ser mayor o igual a 1")
        if not 1 <= page_size <= 500:
            raise ValueError("page_size debe estar entre 1 y 500")
        sort_column = self._SORTS.get(sort_by)
        if sort_column is None:
            raise ValueError(f"Ordenamiento no permitido: {sort_by}")

        conditions: list[str] = []
        parameters: list[object] = []
        self._add_in_filter(conditions, parameters, "action", actions)
        self._add_in_filter(conditions, parameters, "entity_type", entity_types)
        self._add_in_filter(conditions, parameters, "actor_user_id", actors)
        if date_from:
            conditions.append("occurred_at >= ?")
            parameters.append(str(date_from))
        if date_to:
            upper_bound = str(date_to).strip()
            if len(upper_bound) == 10:
                next_day = date.fromisoformat(upper_bound) + timedelta(days=1)
                conditions.append("occurred_at < ?")
                parameters.append(next_day.isoformat())
            else:
                conditions.append("occurred_at <= ?")
                parameters.append(upper_bound)
        if search and str(search).strip():
            conditions.append(
                "UPPER(COALESCE(entity_id, '') || ' ' || action || ' ' || "
                "entity_type || ' ' || COALESCE(reason, '')) LIKE ?"
            )
            parameters.append(f"%{str(search).strip().upper()}%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        projection = ", ".join(_EVENT_COLUMNS)
        base = f"SELECT {projection} FROM ({_EVENTS_SQL}) audit{where}"

        with self.database.session() as session:
            total = int(session.execute(
                f"SELECT COUNT(*) FROM ({base}) filtered", tuple(parameters)
            ).fetchone()[0])
            direction = "DESC" if descending else "ASC"
            offset = (page - 1) * page_size
            rows = session.execute(
                f"{base} ORDER BY {sort_column} {direction}, action, entity_id "
                "LIMIT ? OFFSET ?",
                (*parameters, page_size, offset),
            ).fetchall()
        return AuditPage(
            tuple(AuditEvent(*row) for row in rows), total, page, page_size
        )

    def facets(self, actor_user_id: int) -> dict[str, tuple[str, ...]]:
        """Return complete filter values without materializing the audit history."""
        self.authorization.require_permission(actor_user_id, PERM_AUDIT_VIEW)
        source = f"({_EVENTS_SQL}) audit"
        with self.database.session() as session:
            actions = session.execute(
                f"SELECT DISTINCT action FROM {source} "
                "WHERE action IS NOT NULL ORDER BY action"
            ).fetchall()
            entity_types = session.execute(
                f"SELECT DISTINCT entity_type FROM {source} "
                "WHERE entity_type IS NOT NULL ORDER BY entity_type"
            ).fetchall()
            actors = session.execute(
                f"SELECT DISTINCT actor_user_id FROM {source} "
                "WHERE actor_user_id IS NOT NULL ORDER BY actor_user_id"
            ).fetchall()
        return {
            "actions": tuple(str(row[0]) for row in actions),
            "entity_types": tuple(str(row[0]) for row in entity_types),
            "actors": tuple(str(row[0]) for row in actors),
        }

    def calculation_runs(
        self,
        actor_user_id: int,
        *,
        periods: Iterable[str] | None = None,
        statuses: Iterable[str] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> CalculationRunPage:
        try:
            self.authorization.require_permission(actor_user_id, PERM_AUDIT_VIEW)
        except AccessDeniedError:
            try:
                self.authorization.require_permission(
                    actor_user_id, PERM_CALCULATION_EXECUTE
                )
                self.authorization.require_any_role(
                    actor_user_id, CALCULATION_OPERATOR_ROLES
                )
            except AccessDeniedError:
                self.authorization.require_permission(
                    actor_user_id, PERM_CALCULATION_APPROVE
                )
                self.authorization.require_any_role(
                    actor_user_id, {ROLE_PARAMETERIZER, ROLE_ADMIN}
                )
        if page < 1:
            raise ValueError("page debe ser mayor o igual a 1")
        if not 1 <= page_size <= 500:
            raise ValueError("page_size debe estar entre 1 y 500")
        conditions: list[str] = []
        parameters: list[object] = []
        selected_periods = [
            str(value).strip() for value in (periods or ()) if str(value).strip()
        ]
        selected_statuses = [
            str(value).strip().upper()
            for value in (statuses or ()) if str(value).strip()
        ]
        for column, selected in (
            ("r.period", selected_periods), ("r.status", selected_statuses)
        ):
            if selected:
                placeholders = ",".join("?" for _ in selected)
                conditions.append(f"{column} IN ({placeholders})")
                parameters.extend(selected)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        source = (
            " FROM calculation_runs r LEFT JOIN calculation_run_metrics m "
            "ON m.run_code = r.run_code "
            "LEFT JOIN calculation_workflows w ON w.run_code = r.run_code" + where
        )
        with self.database.session() as session:
            total = int(session.execute(
                "SELECT COUNT(*)" + source, tuple(parameters)
            ).fetchone()[0])
            offset = (page - 1) * page_size
            rows = session.execute(
                "SELECT r.run_code, r.period, r.rules_version, r.status, w.status, "
                "r.started_at, r.finished_at, r.executed_by, "
                "COALESCE(m.commission_rows, 0), COALESCE(m.floor_rows, 0), "
                "COALESCE(m.payment_rows, 0), COALESCE(m.discount_rows, 0), "
                "COALESCE(m.warning_count, 0), COALESCE(m.error_count, 0), "
                "r.message" + source +
                " ORDER BY r.started_at DESC, r.run_code LIMIT ? OFFSET ?",
                (*parameters, page_size, offset),
            ).fetchall()
        return CalculationRunPage(
            tuple(CalculationRunEvent(*row) for row in rows),
            total,
            page,
            page_size,
        )

    @staticmethod
    def _add_in_filter(
        conditions: list[str],
        parameters: list[object],
        column: str,
        values: Iterable[object] | None,
    ) -> None:
        if values is None:
            return
        selected = [str(value).strip().upper() for value in values if str(value).strip()]
        if not selected:
            return
        placeholders = ",".join("?" for _ in selected)
        conditions.append(f"UPPER({column}) IN ({placeholders})")
        parameters.extend(selected)
