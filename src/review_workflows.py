"""Solicitudes de revision y excepciones con historial completo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Mapping, Protocol

from .access_control import (
    AccessDeniedError,
    AuthorizationService,
    PERM_EXCEPTION_APPROVE,
    PERM_EXCEPTION_PROPOSE,
    PERM_REVIEW_CREATE,
    PERM_REVIEW_MANAGE,
)
from .database import Database
from .business_periods import normalize_period
from .exception_rules import (
    COMMISSION_VALUE_DIRECTOR,
    COMMISSION_VALUE_EXECUTIVE,
    COMMISSION_VALUE_MANAGER,
    FLOOR_FORCE_COMPLY,
    FLOOR_FORCE_INCLUDE,
    PAYMENT_FORCE_PAY,
)


REQUESTED = "SOLICITADA"
IN_REVIEW = "EN_REVISION"
PENDING_APPROVAL = "PENDIENTE_APROBACION"
REQUEST_APPROVED = "APROBADA"
REQUEST_REJECTED = "RECHAZADA"
RESOLVED = "RESUELTA"
CLOSED = "CERRADA"

REQUEST_TRANSITIONS = {
    REQUESTED: {IN_REVIEW},
    IN_REVIEW: {PENDING_APPROVAL, RESOLVED},
    PENDING_APPROVAL: {REQUEST_APPROVED, REQUEST_REJECTED},
    REQUEST_APPROVED: {RESOLVED},
    REQUEST_REJECTED: {RESOLVED},
    RESOLVED: {CLOSED},
}

EXCEPTION_PROPOSED = "PROPUESTA"
EXCEPTION_PENDING = "PENDIENTE_APROBACION"
EXCEPTION_APPROVED = "APROBADA"
EXCEPTION_REJECTED = "RECHAZADA"
EXCEPTION_INACTIVE = "INACTIVA"

EXCEPTION_TRANSITIONS = {
    EXCEPTION_PROPOSED: {EXCEPTION_PENDING},
    EXCEPTION_PENDING: {EXCEPTION_APPROVED, EXCEPTION_REJECTED},
    EXCEPTION_APPROVED: {EXCEPTION_INACTIVE},
}

EXCEPTION_FLOOR = "PISO"
EXCEPTION_PAYMENT = "PAGO"

EXCEPTION_ACTIONS = {
    FLOOR_FORCE_COMPLY: (EXCEPTION_FLOOR, False),
    FLOOR_FORCE_INCLUDE: (EXCEPTION_FLOOR, True),
    PAYMENT_FORCE_PAY: (EXCEPTION_PAYMENT, True),
    COMMISSION_VALUE_EXECUTIVE: (EXCEPTION_PAYMENT, True),
    COMMISSION_VALUE_MANAGER: (EXCEPTION_PAYMENT, True),
    COMMISSION_VALUE_DIRECTOR: (EXCEPTION_PAYMENT, True),
}


@dataclass(frozen=True)
class ReviewRequest:
    request_id: int
    executive_code: str
    period: str
    status: str
    item_count: int


@dataclass(frozen=True)
class ExceptionRecord:
    exception_id: int
    exception_type: str
    executive_code: str
    period: str
    document: str | None
    status: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_executive(value: object) -> str:
    executive = str(value).strip().upper()
    if not executive:
        raise ValueError("El ejecutivo es obligatorio")
    return executive


class ReviewRepository(Protocol):
    def create_request(self, *args, **kwargs) -> ReviewRequest: ...
    def get_request(self, request_id: int) -> ReviewRequest: ...
    def transition_request(self, *args, **kwargs) -> ReviewRequest: ...
    def resolve_item(self, *args, **kwargs) -> None: ...
    def create_exception(self, *args, **kwargs) -> ExceptionRecord: ...
    def transition_exception(self, *args, **kwargs) -> ExceptionRecord: ...
    def active_approved_exceptions(
        self, period: str | None = None
    ) -> list[dict[str, object]]: ...
    def document_review_context(self) -> list[dict[str, object]]: ...
    def list_requests(self) -> list[dict[str, object]]: ...
    def list_exceptions(self) -> list[dict[str, object]]: ...
    def exception_proposer(self, exception_id: int) -> int: ...


class ReviewDocumentValidator(Protocol):
    def validate_documents(
        self,
        actor_user_id: int,
        executive_code: str,
        period: str,
        documents: Iterable[str],
    ) -> None: ...


class PersistentReviewRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        identity = self.database.identity_primary_key()
        identifier = self.database.identifier_type()
        document_context_view = self.database.create_view_statement(
            "vw_review_document_context",
            """
            SELECT executive, period, document, request_id,
                   request_status, request_final_concept
            FROM (
                SELECT r.executive_code AS executive,
                       r.period AS period,
                       i.document AS document,
                       r.id AS request_id,
                       r.status AS request_status,
                       i.concept AS request_final_concept,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.executive_code, r.period, i.document
                           ORDER BY r.updated_at DESC, r.id DESC
                       ) AS position
                FROM review_requests r
                JOIN review_request_items i ON i.request_id = r.id
            ) ranked
            WHERE position = 1
            """,
        )
        with self.database.session() as session:
            session.execute_statements((
                f"""
                CREATE TABLE IF NOT EXISTS review_requests (
                    id {identity},
                    requester_user_id {identifier} NOT NULL,
                    executive_code TEXT NOT NULL,
                    period TEXT NOT NULL,
                    general_reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS review_request_items (
                    id {identity},
                    request_id {identifier} NOT NULL,
                    document TEXT NOT NULL,
                    observation TEXT,
                    previous_result TEXT,
                    final_result TEXT,
                    concept TEXT,
                    FOREIGN KEY (request_id) REFERENCES review_requests(id)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS review_request_status_history (
                    id {identity},
                    request_id {identifier} NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    changed_by {identifier} NOT NULL,
                    changed_at TEXT NOT NULL,
                    concept TEXT,
                    FOREIGN KEY (request_id) REFERENCES review_requests(id)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS commission_exceptions (
                    id {identity},
                    exception_type TEXT NOT NULL,
                    executive_code TEXT NOT NULL,
                    period TEXT NOT NULL,
                    document TEXT,
                    rule_code TEXT NOT NULL,
                    original_result TEXT,
                    exceptional_result TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    proposed_by {identifier} NOT NULL,
                    approved_by {identifier},
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    CHECK (exception_type IN ('PISO', 'PAGO')),
                    CHECK (exception_type <> 'PAGO' OR document IS NOT NULL)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS exception_status_history (
                    id {identity},
                    exception_id {identifier} NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    changed_by {identifier} NOT NULL,
                    changed_at TEXT NOT NULL,
                    concept TEXT,
                    FOREIGN KEY (exception_id) REFERENCES commission_exceptions(id)
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_review_request_scope
                ON review_requests(executive_code, period, status)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_active_exception_scope
                ON commission_exceptions(executive_code, period, document, status)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_review_item_document
                ON review_request_items(document, request_id)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_review_status_history
                ON review_request_status_history(request_id, changed_at, id)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_exception_status_history
                ON exception_status_history(exception_id, changed_at, id)
                """,
                document_context_view,
            ))

    def create_request(
        self,
        requester_user_id: int,
        executive_code: str,
        period: str,
        general_reason: str,
        items: Iterable[Mapping[str, object]],
    ) -> ReviewRequest:
        normalized_executive = _normalize_executive(executive_code)
        normalized_period = normalize_period(period)
        normalized_reason = str(general_reason).strip()
        if not normalized_reason:
            raise ValueError("El motivo general de la solicitud es obligatorio")
        item_list = list(items)
        if not item_list:
            raise ValueError("La solicitud debe incluir al menos una factura")
        documents = [str(item.get("document", "")).strip().upper() for item in item_list]
        if any(not document for document in documents):
            raise ValueError("Todos los detalles deben tener documento")
        if len(documents) != len(set(documents)):
            raise ValueError("Una factura no puede repetirse dentro de la solicitud")
        now = _utc_now()
        with self.database.session() as session:
            request_id = session.insert_returning_id(
                """
                INSERT INTO review_requests (
                    requester_user_id, executive_code, period, general_reason,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    requester_user_id, normalized_executive, normalized_period,
                    normalized_reason, REQUESTED, now, now,
                ),
            )
            for document, item in zip(documents, item_list):
                session.execute(
                    """
                    INSERT INTO review_request_items (
                        request_id, document, observation, previous_result,
                        final_result, concept
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id, document, item.get("observation"),
                        item.get("previous_result"), item.get("final_result"),
                        item.get("concept"),
                    ),
                )
            session.execute(
                """
                INSERT INTO review_request_status_history (
                    request_id, previous_status, new_status, changed_by, changed_at
                ) VALUES (?, NULL, ?, ?, ?)
                """,
                (request_id, REQUESTED, requester_user_id, now),
            )
        return ReviewRequest(
            request_id, normalized_executive, normalized_period, REQUESTED, len(item_list)
        )

    def get_request(self, request_id: int) -> ReviewRequest:
        with self.database.session() as session:
            row = session.execute(
                """
                SELECT r.id, r.executive_code, r.period, r.status, COUNT(i.id)
                FROM review_requests r
                LEFT JOIN review_request_items i ON i.request_id = r.id
                WHERE r.id = ? GROUP BY r.id
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Solicitud inexistente: {request_id}")
        return ReviewRequest(*row)

    def transition_request(
        self,
        request_id: int,
        new_status: str,
        changed_by: int,
        concept: str | None = None,
    ) -> ReviewRequest:
        normalized_concept = str(concept or "").strip()
        if not normalized_concept:
            raise ValueError("El concepto de la transición es obligatorio")
        with self.database.session() as session:
            session.lock_table_for_write("review_requests")
            row = session.execute(
                "SELECT status FROM review_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Solicitud inexistente: {request_id}")
            previous = row[0]
            if new_status not in REQUEST_TRANSITIONS.get(previous, set()):
                raise ValueError(f"Transicion de solicitud invalida: {previous} -> {new_status}")
            now = _utc_now()
            session.execute(
                "UPDATE review_requests SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, request_id),
            )
            session.execute(
                """
                INSERT INTO review_request_status_history (
                    request_id, previous_status, new_status, changed_by,
                    changed_at, concept
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id, previous, new_status, changed_by, now,
                    normalized_concept,
                ),
            )
        return self.get_request(request_id)

    def resolve_item(
        self,
        request_id: int,
        document: str,
        final_result: str,
        concept: str,
    ) -> None:
        normalized_result = str(final_result).strip()
        normalized_concept = str(concept).strip()
        if not normalized_result:
            raise ValueError("El resultado final es obligatorio")
        if not normalized_concept:
            raise ValueError("El concepto final es obligatorio")
        with self.database.session() as session:
            updated = session.execute(
                """
                UPDATE review_request_items SET final_result = ?, concept = ?
                WHERE request_id = ? AND document = ?
                """,
                (
                    normalized_result, normalized_concept, request_id,
                    document.strip().upper(),
                ),
            ).rowcount
            if updated != 1:
                raise KeyError("Detalle de solicitud inexistente")

    def create_exception(
        self,
        exception_type: str,
        executive_code: str,
        period: str,
        rule_code: str,
        original_result: str | None,
        exceptional_result: str,
        reason: str,
        proposed_by: int,
        document: str | None = None,
    ) -> ExceptionRecord:
        normalized_executive = _normalize_executive(executive_code)
        normalized_period = normalize_period(period)
        if exception_type not in {EXCEPTION_FLOOR, EXCEPTION_PAYMENT}:
            raise ValueError("Tipo de excepcion invalido")
        action = str(rule_code).strip().upper()
        expected = EXCEPTION_ACTIONS.get(action)
        if expected is None:
            raise ValueError(f"Acción de excepción no soportada: {action}")
        if expected[0] != exception_type:
            raise ValueError("La acción no corresponde al tipo de excepción")
        requires_document = expected[1]
        if requires_document and not document:
            raise ValueError("La excepción requiere una factura")
        if action in {
            COMMISSION_VALUE_EXECUTIVE,
            COMMISSION_VALUE_MANAGER,
            COMMISSION_VALUE_DIRECTOR,
        }:
            try:
                commission_value = Decimal(str(exceptional_result).strip())
            except InvalidOperation as exc:
                raise ValueError(
                    "El valor excepcional de comisión debe ser numérico"
                ) from exc
            if not commission_value.is_finite() or commission_value < 0:
                raise ValueError(
                    "El valor excepcional de comisión debe ser numérico y no negativo"
                )
        if not str(reason).strip():
            raise ValueError("El motivo de la excepción es obligatorio")
        now = _utc_now()
        normalized_document = document.strip().upper() if document else None
        with self.database.session() as session:
            exception_id = session.insert_returning_id(
                """
                INSERT INTO commission_exceptions (
                    exception_type, executive_code, period, document, rule_code,
                    original_result, exceptional_result, reason, status,
                    proposed_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exception_type, normalized_executive, normalized_period,
                    normalized_document, action, original_result,
                    exceptional_result, reason.strip(), EXCEPTION_PROPOSED,
                    proposed_by, now, now,
                ),
            )
            session.execute(
                """
                INSERT INTO exception_status_history (
                    exception_id, previous_status, new_status, changed_by, changed_at
                ) VALUES (?, NULL, ?, ?, ?)
                """,
                (exception_id, EXCEPTION_PROPOSED, proposed_by, now),
            )
        return ExceptionRecord(
            exception_id, exception_type, normalized_executive,
            normalized_period, normalized_document, EXCEPTION_PROPOSED,
        )

    def transition_exception(
        self,
        exception_id: int,
        new_status: str,
        changed_by: int,
        concept: str | None = None,
    ) -> ExceptionRecord:
        normalized_concept = str(concept or "").strip()
        if (
            new_status in {
                EXCEPTION_APPROVED, EXCEPTION_REJECTED, EXCEPTION_INACTIVE
            }
            and not normalized_concept
        ):
            raise ValueError("El concepto de la decisión es obligatorio")
        with self.database.session() as session:
            session.lock_table_for_write("commission_exceptions")
            row = session.execute(
                """
                SELECT exception_type, executive_code, period, document,
                       status, rule_code
                FROM commission_exceptions WHERE id = ?
                """,
                (exception_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Excepcion inexistente: {exception_id}")
            previous = row[4]
            if new_status not in EXCEPTION_TRANSITIONS.get(previous, set()):
                raise ValueError(f"Transicion de excepcion invalida: {previous} -> {new_status}")
            if new_status == EXCEPTION_APPROVED:
                overlap = session.execute(
                    """
                    SELECT id FROM commission_exceptions
                    WHERE id <> ? AND status = ? AND executive_code = ?
                      AND period = ? AND rule_code = ?
                      AND COALESCE(document, '') = COALESCE(?, '')
                    LIMIT 1
                    """,
                    (
                        exception_id, EXCEPTION_APPROVED, row[1], row[2],
                        row[5], row[3],
                    ),
                ).fetchone()
                if overlap is not None:
                    raise ValueError(
                        "Ya existe una excepción aprobada para la misma regla, "
                        "ejecutivo, periodo y factura"
                    )
            now = _utc_now()
            approver = changed_by if new_status in {EXCEPTION_APPROVED, EXCEPTION_REJECTED} else None
            approved_at = now if new_status == EXCEPTION_APPROVED else None
            session.execute(
                """
                UPDATE commission_exceptions
                SET status = ?, updated_at = ?,
                    approved_by = COALESCE(?, approved_by),
                    approved_at = COALESCE(?, approved_at)
                WHERE id = ?
                """,
                (new_status, now, approver, approved_at, exception_id),
            )
            session.execute(
                """
                INSERT INTO exception_status_history (
                    exception_id, previous_status, new_status, changed_by,
                    changed_at, concept
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    exception_id, previous, new_status, changed_by, now,
                    normalized_concept or None,
                ),
            )
        return ExceptionRecord(exception_id, row[0], row[1], row[2], row[3], new_status)

    def exception_proposer(self, exception_id: int) -> int:
        with self.database.session() as session:
            row = session.execute(
                "SELECT proposed_by FROM commission_exceptions WHERE id = ?",
                (exception_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Excepcion inexistente: {exception_id}")
        return int(row[0])

    def active_approved_exceptions(self, period: str | None = None) -> list[dict[str, object]]:
        query = """
            SELECT id, exception_type, executive_code, period, document,
                   rule_code, original_result, exceptional_result, reason
            FROM commission_exceptions WHERE status = ?
        """
        params: list[object] = [EXCEPTION_APPROVED]
        if period is not None:
            query += " AND period = ?"
            params.append(period)
        with self.database.session() as session:
            rows = session.execute(query, tuple(params)).fetchall()
        columns = (
            "id", "exception_type", "executive_code", "period", "document",
            "rule_code", "original_result", "exceptional_result", "reason",
        )
        return [dict(zip(columns, row)) for row in rows]

    def document_review_context(self) -> list[dict[str, object]]:
        """Return the latest request associated with each scoped document."""
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT executive, period, document, request_id,
                       request_status, request_final_concept
                FROM vw_review_document_context
                """
            ).fetchall()
        columns = (
            "executive", "period", "document", "request_id",
            "request_status", "request_final_concept",
        )
        return [dict(zip(columns, row)) for row in rows]

    def list_requests(self) -> list[dict[str, object]]:
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT r.id, r.requester_user_id, r.executive_code, r.period,
                       r.general_reason, r.status, r.created_at, r.updated_at,
                       COUNT(i.id)
                FROM review_requests r
                LEFT JOIN review_request_items i ON i.request_id = r.id
                GROUP BY r.id, r.requester_user_id, r.executive_code, r.period,
                         r.general_reason, r.status, r.created_at, r.updated_at
                ORDER BY r.updated_at DESC, r.id DESC
                """
            ).fetchall()
        columns = (
            "request_id", "requester_user_id", "executive", "period",
            "general_reason", "status", "created_at", "updated_at", "item_count",
        )
        return [dict(zip(columns, row)) for row in rows]

    def list_exceptions(self) -> list[dict[str, object]]:
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT id, exception_type, executive_code, period, document,
                       rule_code, original_result, exceptional_result, reason,
                       status, proposed_by, approved_by, created_at, updated_at,
                       approved_at
                FROM commission_exceptions
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        columns = (
            "exception_id", "exception_type", "executive", "period", "document",
            "rule_code", "original_result", "exceptional_result", "reason",
            "status", "proposed_by", "approved_by", "created_at", "updated_at",
            "approved_at",
        )
        return [dict(zip(columns, row)) for row in rows]


class SqliteReviewRepository(PersistentReviewRepository):
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        super().__init__(Database.sqlite(self.database_path))


class PostgresReviewRepository(PersistentReviewRepository):
    def __init__(self, dsn: str) -> None:
        super().__init__(Database.postgresql(dsn))


class ReviewWorkflowService:
    def __init__(
        self,
        repository: ReviewRepository,
        authorization: AuthorizationService,
        document_validator: ReviewDocumentValidator,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.document_validator = document_validator

    def create_request(
        self,
        actor_user_id: int,
        executive_code: str,
        period: str,
        effective_on: date | str,
        general_reason: str,
        items: Iterable[Mapping[str, object]],
    ) -> ReviewRequest:
        self.authorization.require_permission(actor_user_id, PERM_REVIEW_CREATE)
        self.authorization.require_executive_access(
            actor_user_id, executive_code, effective_on
        )
        item_list = list(items)
        self.document_validator.validate_documents(
            actor_user_id,
            executive_code,
            normalize_period(period),
            [str(item.get("document", "")) for item in item_list],
        )
        return self.repository.create_request(
            actor_user_id, executive_code, period, general_reason, item_list
        )

    def transition_request(
        self,
        actor_user_id: int,
        request_id: int,
        new_status: str,
        concept: str | None = None,
    ) -> ReviewRequest:
        self.authorization.require_permission(actor_user_id, PERM_REVIEW_MANAGE)
        return self.repository.transition_request(
            request_id, new_status, actor_user_id, concept
        )

    def resolve_request_item(
        self,
        actor_user_id: int,
        request_id: int,
        document: str,
        final_result: str,
        concept: str,
    ) -> None:
        self.authorization.require_permission(actor_user_id, PERM_REVIEW_MANAGE)
        self.repository.resolve_item(
            request_id, document, final_result, concept
        )

    def propose_exception(
        self,
        actor_user_id: int,
        exception_type: str,
        executive_code: str,
        period: str,
        effective_on: date | str,
        rule_code: str,
        original_result: str | None,
        exceptional_result: str,
        reason: str,
        document: str | None = None,
    ) -> ExceptionRecord:
        self.authorization.require_permission(actor_user_id, PERM_EXCEPTION_PROPOSE)
        self.authorization.require_executive_access(
            actor_user_id, executive_code, effective_on
        )
        if document is not None:
            self.document_validator.validate_documents(
                actor_user_id,
                executive_code,
                normalize_period(period),
                [document],
            )
        return self.repository.create_exception(
            exception_type, executive_code, period, rule_code, original_result,
            exceptional_result, reason, actor_user_id, document,
        )

    def submit_exception(self, actor_user_id: int, exception_id: int) -> ExceptionRecord:
        self.authorization.require_permission(actor_user_id, PERM_EXCEPTION_PROPOSE)
        if self.repository.exception_proposer(exception_id) != actor_user_id:
            raise AccessDeniedError(
                "Solo el autor puede enviar la excepcion para aprobacion"
            )
        return self.repository.transition_exception(
            exception_id, EXCEPTION_PENDING, actor_user_id
        )

    def decide_exception(
        self,
        actor_user_id: int,
        exception_id: int,
        approve: bool,
        concept: str,
    ) -> ExceptionRecord:
        self.authorization.require_permission(actor_user_id, PERM_EXCEPTION_APPROVE)
        return self.repository.transition_exception(
            exception_id,
            EXCEPTION_APPROVED if approve else EXCEPTION_REJECTED,
            actor_user_id,
            concept,
        )

    def deactivate_exception(
        self,
        actor_user_id: int,
        exception_id: int,
        concept: str,
    ) -> ExceptionRecord:
        self.authorization.require_permission(actor_user_id, PERM_EXCEPTION_APPROVE)
        return self.repository.transition_exception(
            exception_id, EXCEPTION_INACTIVE, actor_user_id, concept
        )

    def list_requests(
        self, actor_user_id: int, effective_on: date | str
    ) -> list[dict[str, object]]:
        return self._within_scope(
            actor_user_id, effective_on, self.repository.list_requests()
        )

    def list_exceptions(
        self, actor_user_id: int, effective_on: date | str
    ) -> list[dict[str, object]]:
        return self._within_scope(
            actor_user_id, effective_on, self.repository.list_exceptions()
        )

    def _within_scope(
        self,
        actor_user_id: int,
        effective_on: date | str,
        rows: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        scope = self.authorization.executive_scope(actor_user_id, effective_on)
        if scope is None:
            return rows
        return [
            row for row in rows
            if str(row.get("executive", "")).strip().upper() in scope
        ]
