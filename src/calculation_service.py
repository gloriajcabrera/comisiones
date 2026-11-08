"""Ejecucion y persistencia auditable del motor de comisiones."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import pandas as pd

from .access_control import (
    AccessDeniedError,
    AuthorizationService,
    CALCULATION_OPERATOR_ROLES,
    PERM_CALCULATION_APPROVE,
    PERM_CALCULATION_EXECUTE,
    ROLE_ADMIN,
    ROLE_PARAMETERIZER,
)
from .engine import CommissionInputs, CommissionResult, calculate_commissions
from .engine import recalculate_cancelled_without_replacement
from .business_periods import BusinessPeriodRepository, normalize_period
from .cancellation_discounts import (
    CancellationDiscountRepository,
    build_cancellation_discounts,
)
from .database import Database
from .netsuite_provider import NetSuiteDataProvider, NetSuiteDataset
from .data_quality import filter_netsuite_history
from .rule_parameters import RuleParameters
from .review_workflows import ReviewRepository
from .observability import safe_error_message
from .payment_base import OfficialPaymentBase


CALC_RUNNING = "EN_PROCESO"
CALC_COMPLETED = "COMPLETADO"
CALC_COMPLETED_WARNINGS = "COMPLETADO_CON_ADVERTENCIAS"
CALC_ERROR = "ERROR"
CALC_SUCCESS_STATUSES = {CALC_COMPLETED, CALC_COMPLETED_WARNINGS}

WORKFLOW_PENDING_REVIEW = "PENDIENTE_REVISION"
WORKFLOW_PENDING_APPROVAL = "PENDIENTE_APROBACION"
WORKFLOW_APPROVED = "APROBADO"
WORKFLOW_REJECTED = "RECHAZADO"
WORKFLOW_SUPERSEDED = "SUSTITUIDO"


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalculationExecution:
    run_code: str
    status: str
    idempotency_key: str
    reused: bool
    commission_rows: int
    floor_rows: int
    payment_rows: int
    warning_count: int | None = None
    warning_message: str | None = None
    discount_rows: int = 0
    discount_ids: tuple[int, ...] = ()
    discounts: pd.DataFrame | None = None
    result: CommissionResult | None = None


@dataclass(frozen=True)
class CancellationRecalculationContext:
    previous_compliance: pd.DataFrame
    paid_commissions: pd.DataFrame
    detection_period: str
    source_calculation: str


class CalculationRepository(Protocol):
    def find_run(self, idempotency_key: str) -> dict[str, object] | None: ...

    def get_run(self, run_code: str) -> dict[str, object] | None: ...

    def archive_failed_run(self, idempotency_key: str) -> None: ...

    def start_run(
        self,
        run_code: str,
        idempotency_key: str,
        period: str | None,
        rules_version: str,
        executed_by: str,
    ) -> None: ...

    def finish_run(
        self,
        run_code: str,
        status: str,
        message: str | None = None,
        metrics: dict[str, int] | None = None,
    ) -> None: ...

    def save_result(self, run_code: str, result: CommissionResult) -> None: ...

    def load_result(self, run_code: str) -> CommissionResult | None: ...

    def initialize_workflow(
        self, run_code: str, actor_user_id: int | str
    ) -> None: ...

    def workflow_status(self, run_code: str) -> str | None: ...

    def transition_workflow(
        self, run_code: str, expected_status: str, new_status: str,
        actor_user_id: int, reason: str,
    ) -> None: ...

    def approve_workflow(
        self, run_code: str, actor_user_id: int, reason: str,
    ) -> None: ...

    def latest_approved_run(self, period: str) -> dict[str, object] | None: ...


class ApprovedTargetsProvider(Protocol):
    def load_approved_targets(self) -> pd.DataFrame: ...


class ApprovedCutsProvider(Protocol):
    def load_approved_cuts(self) -> pd.DataFrame: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return json.loads(
        frame.to_json(
            orient="records",
            date_format="iso",
            date_unit="ms",
            default_handler=str,
        )
    )


def _serialize_frame(frame: pd.DataFrame) -> str:
    return json.dumps({
        "columns": [str(column) for column in frame.columns],
        "records": _records(frame),
    }, separators=(",", ":"), ensure_ascii=True)


def _deserialize_frame(payload: str | None) -> pd.DataFrame:
    if not payload:
        return pd.DataFrame()
    parsed = json.loads(payload)
    return pd.DataFrame(parsed.get("records", []), columns=parsed.get("columns", []))


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    rows = [
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        for row in _records(frame)
    ]
    descriptor = {
        "columns": [str(column) for column in frame.columns],
        "dtypes": [str(dtype) for dtype in frame.dtypes],
        "rows": sorted(rows),
    }
    payload = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _calculation_warnings(result: CommissionResult) -> tuple[int, str | None]:
    counts: list[tuple[str, int]] = []
    detail = result.commission_detail
    if "decision_pago" in detail.columns:
        reviews = int(
            detail["decision_pago"].astype("string").str.strip().str.upper()
            .eq("PARA REVISAR").sum()
        )
        if reviews:
            counts.append(("documentos para revisar", reviews))
    floor = result.floor_compliance
    if "requiere_revision_cumplimiento" in floor.columns:
        floor_reviews = int(
            floor["requiere_revision_cumplimiento"]
            .astype("boolean").fillna(False).sum()
        )
        if floor_reviews:
            counts.append(("cumplimientos para revisar", floor_reviews))
    unmatched = len(result.payment_base.unmatched_movements)
    if unmatched:
        counts.append(("movimientos sin documento base", unmatched))
    total = sum(count for _, count in counts)
    message = "; ".join(f"{label}: {count}" for label, count in counts) or None
    return total, message


def _scope_frame_to_period(frame: pd.DataFrame, period: str | None) -> pd.DataFrame:
    if period is None or frame.empty:
        return frame.copy()
    period_columns = (
        "periodo_mes", "periodo_factura", "periodo_piso", "period",
        "Periodo Corte", "periodo_corte_pago", "periodo_corte",
    )
    for column in period_columns:
        if column in frame.columns:
            values = frame[column].astype("string").str.strip().str[:7]
            return frame.loc[values.eq(period)].copy()
    date_columns = (
        "tranDate", "fecha_factura", "fecha_proforma",
        "fecha_contabilizacion", "fecha_pago",
    )
    for column in date_columns:
        if column in frame.columns:
            values = pd.to_datetime(frame[column], errors="coerce").dt.strftime("%Y-%m")
            return frame.loc[values.eq(period)].copy()
    return frame.iloc[0:0].copy()


def _scope_result_to_period(
    result: CommissionResult, period: str | None
) -> CommissionResult:
    if period is None:
        return result
    payments = result.payment_base
    payment_documents = payments.documents
    if "fecha_documento" in payment_documents.columns:
        document_period = pd.to_datetime(
            payment_documents["fecha_documento"], errors="coerce"
        ).dt.strftime("%Y-%m")
        payment_documents = payment_documents.loc[
            document_period.eq(period)
        ].copy()
    else:
        payment_documents = _scope_frame_to_period(payment_documents, period)
    return CommissionResult(
        sales_base=_scope_frame_to_period(result.sales_base, period),
        sales_with_credit_notes=_scope_frame_to_period(
            result.sales_with_credit_notes, period
        ),
        sales_with_origins=_scope_frame_to_period(result.sales_with_origins, period),
        sales_floor=_scope_frame_to_period(result.sales_floor, period),
        floor_compliance=_scope_frame_to_period(result.floor_compliance, period),
        payment_base=OfficialPaymentBase(
            documents=payment_documents,
            history=_scope_frame_to_period(payments.history, period),
            unmatched_movements=_scope_frame_to_period(
                payments.unmatched_movements, period
            ),
        ),
        commission_detail=_scope_frame_to_period(result.commission_detail, period),
    )


def calculation_idempotency_key(
    dataset: NetSuiteDataset,
    targets: pd.DataFrame,
    cuts: pd.DataFrame,
    period: str | None,
    rules_version: str,
    parameters: RuleParameters | None = None,
    cancellation_context: CancellationRecalculationContext | None = None,
    approved_exceptions: pd.DataFrame | None = None,
) -> str:
    frames = {
        name: getattr(dataset, name)
        for name in NetSuiteDataset.__dataclass_fields__
    }
    frames["targets"] = targets
    frames["cuts"] = cuts
    parts = {
        name: _frame_fingerprint(frame)
        for name, frame in sorted(frames.items())
    }
    parts["period"] = period or ""
    parts["rules_version"] = rules_version
    parts["parameters"] = asdict((parameters or RuleParameters()).validate())
    parts["approved_exceptions"] = _frame_fingerprint(
        approved_exceptions if approved_exceptions is not None else pd.DataFrame()
    )
    if cancellation_context is not None:
        parts["previous_compliance"] = _frame_fingerprint(
            cancellation_context.previous_compliance
        )
        parts["paid_commissions"] = _frame_fingerprint(
            cancellation_context.paid_commissions
        )
        parts["detection_period"] = str(cancellation_context.detection_period)
        parts["source_calculation"] = str(cancellation_context.source_calculation)
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PersistentCalculationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.database.session() as session:
            session.execute_statements((
                """
                CREATE TABLE IF NOT EXISTS calculation_runs (
                    run_code TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    period TEXT,
                    rules_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    executed_by TEXT NOT NULL,
                    message TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS calculation_run_metrics (
                    run_code TEXT PRIMARY KEY,
                    commission_rows INTEGER NOT NULL DEFAULT 0,
                    floor_rows INTEGER NOT NULL DEFAULT 0,
                    payment_rows INTEGER NOT NULL DEFAULT 0,
                    discount_rows INTEGER NOT NULL DEFAULT 0,
                    warning_count INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (run_code) REFERENCES calculation_runs(run_code)
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_calculation_runs_period_status_started
                ON calculation_runs(period, status, started_at DESC)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_calculation_runs_status_started
                ON calculation_runs(status, started_at DESC)
                """,
                """
                CREATE TABLE IF NOT EXISTS calculation_result_snapshots (
                    run_code TEXT NOT NULL,
                    result_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_code, result_name),
                    FOREIGN KEY (run_code) REFERENCES calculation_runs(run_code)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS calculation_workflows (
                    run_code TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    modified_by TEXT NOT NULL,
                    modified_at TEXT NOT NULL,
                    reason TEXT,
                    FOREIGN KEY (run_code) REFERENCES calculation_runs(run_code)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS calculation_workflow_history (
                    id {identity},
                    run_code TEXT NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    changed_by TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    FOREIGN KEY (run_code) REFERENCES calculation_runs(run_code)
                )
                """.format(identity=self.database.identity_primary_key()),
                """
                CREATE INDEX IF NOT EXISTS idx_calculation_workflow_status
                ON calculation_workflows(status, modified_at DESC)
                """,
            ))

    def find_run(self, idempotency_key: str) -> dict[str, object] | None:
        with self.database.session() as session:
            row = session.execute(
                """
                SELECT r.run_code, r.idempotency_key, r.status, r.message,
                       COALESCE(m.commission_rows, 0),
                       COALESCE(m.floor_rows, 0),
                       COALESCE(m.payment_rows, 0),
                       COALESCE(m.discount_rows, 0),
                       COALESCE(m.warning_count, 0),
                       COALESCE(m.error_count, 0)
                FROM calculation_runs r
                LEFT JOIN calculation_run_metrics m ON m.run_code = r.run_code
                WHERE r.idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_code": row[0],
            "idempotency_key": row[1],
            "status": row[2],
            "message": row[3],
            "commission_rows": int(row[4]),
            "floor_rows": int(row[5]),
            "payment_rows": int(row[6]),
            "discount_rows": int(row[7]),
            "warning_count": int(row[8]),
            "error_count": int(row[9]),
        }

    def get_run(self, run_code: str) -> dict[str, object] | None:
        with self.database.session() as session:
            row = session.execute(
                """
                SELECT run_code, period, rules_version, status, started_at,
                       finished_at, executed_by, message
                FROM calculation_runs WHERE run_code = ?
                """,
                (str(run_code).strip(),),
            ).fetchone()
        if row is None:
            return None
        columns = (
            "run_code", "period", "rules_version", "status", "started_at",
            "finished_at", "executed_by", "message",
        )
        return dict(zip(columns, row, strict=True))

    def archive_failed_run(self, idempotency_key: str) -> None:
        with self.database.session() as session:
            session.lock_table_for_write("calculation_runs")
            row = session.execute(
                "SELECT run_code, status FROM calculation_runs "
                "WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                return
            if row[1] != CALC_ERROR:
                raise RuntimeError(
                    f"La ejecucion {row[0]} no esta en estado ERROR"
                )
            archived_key = f"{idempotency_key}:error:{row[0]}"
            session.execute(
                "UPDATE calculation_runs SET idempotency_key = ? "
                "WHERE run_code = ?",
                (archived_key, row[0]),
            )

    def start_run(
        self,
        run_code: str,
        idempotency_key: str,
        period: str | None,
        rules_version: str,
        executed_by: str,
    ) -> None:
        with self.database.session() as session:
            session.lock_table_for_write("calculation_runs")
            active = session.execute(
                "SELECT run_code FROM calculation_runs WHERE status = ? LIMIT 1",
                (CALC_RUNNING,),
            ).fetchone()
            if active is not None:
                raise RuntimeError(f"Ya existe un calculo en proceso: {active[0]}")
            session.execute(
                """
                INSERT INTO calculation_runs (
                    run_code, idempotency_key, period, rules_version,
                    status, started_at, executed_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_code, idempotency_key, period, rules_version,
                    CALC_RUNNING, _utc_now(), executed_by,
                ),
            )

    def finish_run(
        self,
        run_code: str,
        status: str,
        message: str | None = None,
        metrics: dict[str, int] | None = None,
    ) -> None:
        values = metrics or {}
        with self.database.session() as session:
            session.execute(
                """
                UPDATE calculation_runs
                SET status = ?, finished_at = ?, message = ?
                WHERE run_code = ?
                """,
                (status, _utc_now(), message, run_code),
            )
            session.execute(
                """
                INSERT INTO calculation_run_metrics (
                    run_code, commission_rows, floor_rows, payment_rows,
                    discount_rows, warning_count, error_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_code) DO UPDATE SET
                    commission_rows = excluded.commission_rows,
                    floor_rows = excluded.floor_rows,
                    payment_rows = excluded.payment_rows,
                    discount_rows = excluded.discount_rows,
                    warning_count = excluded.warning_count,
                    error_count = excluded.error_count
                """,
                (
                    run_code,
                    int(values.get("commission_rows", 0)),
                    int(values.get("floor_rows", 0)),
                    int(values.get("payment_rows", 0)),
                    int(values.get("discount_rows", 0)),
                    int(values.get("warning_count", 0)),
                    int(values.get("error_count", 0)),
                ),
            )

    def save_result(self, run_code: str, result: CommissionResult) -> None:
        frames = {
            "commission_detail": result.commission_detail,
            "floor_compliance": result.floor_compliance,
            "sales_floor": result.sales_floor,
            "payment_documents": result.payment_base.documents,
        }
        now = _utc_now()
        with self.database.session() as session:
            for name, frame in frames.items():
                session.execute(
                    """
                    INSERT INTO calculation_result_snapshots (
                        run_code, result_name, payload_json, row_count, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(run_code, result_name) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        row_count = excluded.row_count,
                        created_at = excluded.created_at
                    """,
                    (run_code, name, _serialize_frame(frame), len(frame), now),
                )

    def load_result(self, run_code: str) -> CommissionResult | None:
        with self.database.session() as session:
            rows = session.execute(
                "SELECT result_name, payload_json FROM calculation_result_snapshots "
                "WHERE run_code = ?", (run_code,),
            ).fetchall()
        values = {name: _deserialize_frame(payload) for name, payload in rows}
        required = {"commission_detail", "floor_compliance", "sales_floor"}
        if not required.issubset(values):
            return None
        empty = pd.DataFrame()
        return CommissionResult(
            sales_base=empty.copy(),
            sales_with_credit_notes=empty.copy(),
            sales_with_origins=empty.copy(),
            sales_floor=values["sales_floor"],
            floor_compliance=values["floor_compliance"],
            payment_base=OfficialPaymentBase(
                documents=values.get("payment_documents", empty.copy()),
                history=empty.copy(),
                unmatched_movements=empty.copy(),
            ),
            commission_detail=values["commission_detail"],
        )

    def initialize_workflow(self, run_code: str, actor_user_id: int | str) -> None:
        now = _utc_now()
        with self.database.session() as session:
            session.execute(
                """
                INSERT INTO calculation_workflows (
                    run_code, status, created_by, created_at,
                    modified_by, modified_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_code) DO NOTHING
                """,
                (run_code, WORKFLOW_PENDING_REVIEW, actor_user_id, now,
                 actor_user_id, now, "Resultado calculado; pendiente de revisión"),
            )
            session.execute(
                """
                INSERT INTO calculation_workflow_history (
                    run_code, previous_status, new_status, changed_by,
                    changed_at, reason
                ) SELECT ?, NULL, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM calculation_workflow_history WHERE run_code = ?
                )
                """,
                (run_code, WORKFLOW_PENDING_REVIEW, actor_user_id, now,
                 "Resultado calculado; pendiente de revisión", run_code),
            )

    def workflow_status(self, run_code: str) -> str | None:
        with self.database.session() as session:
            row = session.execute(
                "SELECT status FROM calculation_workflows WHERE run_code = ?",
                (run_code,),
            ).fetchone()
        return None if row is None else str(row[0])

    def transition_workflow(
        self, run_code: str, expected_status: str, new_status: str,
        actor_user_id: int, reason: str,
    ) -> None:
        now = _utc_now()
        with self.database.session() as session:
            session.lock_table_for_write("calculation_workflows")
            current = session.execute(
                "SELECT status FROM calculation_workflows WHERE run_code = ?",
                (run_code,),
            ).fetchone()
            if current is None:
                raise KeyError(f"Flujo de cálculo inexistente: {run_code}")
            if current[0] != expected_status:
                raise RuntimeError(
                    f"Transición inválida: se esperaba {expected_status} y está en {current[0]}"
                )
            session.execute(
                "UPDATE calculation_workflows SET status=?, modified_by=?, "
                "modified_at=?, reason=? WHERE run_code=?",
                (new_status, actor_user_id, now, reason, run_code),
            )
            session.execute(
                "INSERT INTO calculation_workflow_history (run_code, previous_status, "
                "new_status, changed_by, changed_at, reason) VALUES (?, ?, ?, ?, ?, ?)",
                (run_code, expected_status, new_status, actor_user_id, now, reason),
            )

    def approve_workflow(
        self, run_code: str, actor_user_id: int, reason: str,
    ) -> None:
        run = self.get_run(run_code)
        if run is None:
            raise KeyError(f"Cálculo inexistente: {run_code}")
        period = str(run.get("period") or "")
        now = _utc_now()
        with self.database.session() as session:
            session.lock_table_for_write("calculation_workflows")
            current = session.execute(
                "SELECT status FROM calculation_workflows WHERE run_code = ?",
                (run_code,),
            ).fetchone()
            if current is None or current[0] != WORKFLOW_PENDING_APPROVAL:
                actual = None if current is None else current[0]
                raise RuntimeError(
                    f"El cálculo debe estar en {WORKFLOW_PENDING_APPROVAL}; estado actual: {actual}"
                )
            previous = session.execute(
                "SELECT w.run_code FROM calculation_workflows w "
                "JOIN calculation_runs r ON r.run_code=w.run_code "
                "WHERE r.period=? AND w.status=? AND w.run_code<>?",
                (period, WORKFLOW_APPROVED, run_code),
            ).fetchall()
            for (previous_code,) in previous:
                session.execute(
                    "UPDATE calculation_workflows SET status=?, modified_by=?, "
                    "modified_at=?, reason=? WHERE run_code=?",
                    (WORKFLOW_SUPERSEDED, actor_user_id, now,
                     f"Sustituido por {run_code}", previous_code),
                )
                session.execute(
                    "INSERT INTO calculation_workflow_history (run_code, previous_status, "
                    "new_status, changed_by, changed_at, reason) VALUES (?, ?, ?, ?, ?, ?)",
                    (previous_code, WORKFLOW_APPROVED, WORKFLOW_SUPERSEDED,
                     actor_user_id, now, f"Sustituido por {run_code}"),
                )
            session.execute(
                "UPDATE calculation_workflows SET status=?, modified_by=?, "
                "modified_at=?, reason=? WHERE run_code=?",
                (WORKFLOW_APPROVED, actor_user_id, now, reason, run_code),
            )
            session.execute(
                "INSERT INTO calculation_workflow_history (run_code, previous_status, "
                "new_status, changed_by, changed_at, reason) VALUES (?, ?, ?, ?, ?, ?)",
                (run_code, WORKFLOW_PENDING_APPROVAL, WORKFLOW_APPROVED,
                 actor_user_id, now, reason),
            )

    def latest_approved_run(self, period: str) -> dict[str, object] | None:
        with self.database.session() as session:
            row = session.execute(
                "SELECT r.run_code FROM calculation_runs r "
                "JOIN calculation_workflows w ON w.run_code=r.run_code "
                "WHERE r.period=? AND w.status=? "
                "ORDER BY w.modified_at DESC, r.started_at DESC LIMIT 1",
                (period, WORKFLOW_APPROVED),
            ).fetchone()
        return None if row is None else self.get_run(str(row[0]))


class SqliteCalculationRepository(PersistentCalculationRepository):
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        super().__init__(Database.sqlite(self.database_path))


class PostgresCalculationRepository(PersistentCalculationRepository):
    def __init__(self, dsn: str) -> None:
        super().__init__(Database.postgresql(dsn))


class CommissionCalculationService:
    def __init__(
        self,
        provider: NetSuiteDataProvider,
        repository: CalculationRepository,
        rules_version: str = "1",
        parameters: RuleParameters | None = None,
        period_repository: BusinessPeriodRepository | None = None,
        discount_repository: CancellationDiscountRepository | None = None,
        exception_repository: ReviewRepository | None = None,
        targets_provider: ApprovedTargetsProvider | None = None,
        cuts_provider: ApprovedCutsProvider | None = None,
        parameter_provider: object | None = None,
        authorization: AuthorizationService | None = None,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.rules_version = rules_version
        self.parameters = (parameters or RuleParameters()).validate()
        self.period_repository = period_repository
        self.discount_repository = discount_repository
        self.exception_repository = exception_repository
        self.targets_provider = targets_provider
        self.cuts_provider = cuts_provider
        self.parameter_provider = parameter_provider
        self.authorization = authorization

    def execute_approved_inputs(
        self,
        executed_by: str,
        period: str | None = None,
        run_code: str | None = None,
        cancellation_context: CancellationRecalculationContext | None = None,
        materialize_reused_result: bool = False,
        require_existing: bool = False,
    ) -> CalculationExecution:
        """Production entry point using only approved database inputs."""
        if self.authorization is not None:
            actor_user_id = int(executed_by)
            self.authorization.require_permission(
                actor_user_id, PERM_CALCULATION_EXECUTE
            )
            self.authorization.require_any_role(
                actor_user_id, CALCULATION_OPERATOR_ROLES
            )
        if period is None or not str(period).strip():
            raise ValueError("El periodo es obligatorio para el cálculo productivo")
        period = normalize_period(period)
        if self.targets_provider is None or self.cuts_provider is None:
            raise RuntimeError(
                "El cálculo productivo requiere repositorios de pisos y cortes aprobados"
            )
        return self.execute(
            self.targets_provider.load_approved_targets(),
            self.cuts_provider.load_approved_cuts(),
            executed_by,
            period,
            run_code,
            cancellation_context,
            materialize_reused_result,
            require_existing,
            True,
        )

    def load_approved_result(self, period: str) -> CalculationExecution:
        normalized_period = normalize_period(period)
        run = self.repository.latest_approved_run(normalized_period)
        if run is None:
            raise LookupError(
                f"No existe un cálculo aprobado para el periodo {normalized_period}"
            )
        run_code = str(run["run_code"])
        result = self.repository.load_result(run_code)
        if result is None:
            raise RuntimeError(
                f"El cálculo aprobado {run_code} no tiene resultado persistido"
            )
        return CalculationExecution(
            run_code=run_code,
            status=WORKFLOW_APPROVED,
            idempotency_key="",
            reused=True,
            commission_rows=len(result.commission_detail),
            floor_rows=len(result.floor_compliance),
            payment_rows=len(result.payment_base.documents),
            result=result,
        )

    def submit_for_approval(
        self, actor_user_id: int, run_code: str, reason: str,
    ) -> None:
        self._require_operator(actor_user_id)
        observation = self._require_reason(reason)
        self.repository.transition_workflow(
            run_code, WORKFLOW_PENDING_REVIEW, WORKFLOW_PENDING_APPROVAL,
            actor_user_id, observation,
        )

    def approve(
        self, actor_user_id: int, run_code: str, reason: str,
    ) -> None:
        self._require_approver(actor_user_id)
        self.repository.approve_workflow(
            run_code, actor_user_id, self._require_reason(reason)
        )

    def reject(
        self, actor_user_id: int, run_code: str, reason: str,
    ) -> None:
        self._require_approver(actor_user_id)
        self.repository.transition_workflow(
            run_code, WORKFLOW_PENDING_APPROVAL, WORKFLOW_REJECTED,
            actor_user_id, self._require_reason(reason),
        )

    def _require_operator(self, actor_user_id: int) -> None:
        if self.authorization is None:
            return
        self.authorization.require_permission(
            actor_user_id, PERM_CALCULATION_EXECUTE
        )
        self.authorization.require_any_role(
            actor_user_id, CALCULATION_OPERATOR_ROLES
        )

    def _require_approver(self, actor_user_id: int) -> None:
        if self.authorization is None:
            return
        self.authorization.require_permission(
            actor_user_id, PERM_CALCULATION_APPROVE
        )
        roles = self.authorization.effective_roles(actor_user_id)
        if not roles.intersection({ROLE_PARAMETERIZER, ROLE_ADMIN}):
            raise AccessDeniedError(
                "Solo Parametrizador o Administrador puede aprobar cálculos"
            )

    @staticmethod
    def _require_reason(reason: str) -> str:
        observation = str(reason).strip()
        if not observation:
            raise ValueError("La observación es obligatoria")
        return observation

    def execute(
        self,
        targets: pd.DataFrame,
        cuts: pd.DataFrame,
        executed_by: str,
        period: str | None = None,
        run_code: str | None = None,
        cancellation_context: CancellationRecalculationContext | None = None,
        materialize_reused_result: bool = False,
        require_existing: bool = False,
        scope_to_period: bool = False,
    ) -> CalculationExecution:
        if cancellation_context is not None and self.discount_repository is None:
            raise ValueError(
                "El recálculo por anulaciones requiere un repositorio de descuentos"
            )
        parameters = self.parameters
        if self.parameter_provider is not None:
            effective_on = f"{period}-01" if period is not None else datetime.now().date()
            parameters = self.parameter_provider.load_parameters(effective_on)
        dataset = filter_netsuite_history(self.provider.load_dataset())
        approved_exceptions = pd.DataFrame(
            self.exception_repository.active_approved_exceptions(period)
            if self.exception_repository is not None
            else []
        )
        key = calculation_idempotency_key(
            dataset, targets, cuts, period, self.rules_version, parameters,
            cancellation_context,
            approved_exceptions,
        )
        existing = self.repository.find_run(key)
        if existing is not None:
            if existing["status"] in CALC_SUCCESS_STATUSES:
                reused_result = None
                if materialize_reused_result:
                    reused_result = calculate_commissions(
                        CommissionInputs(
                            invoices=dataset.invoices,
                            proformas=dataset.proformas,
                            credit_notes=dataset.credit_notes,
                            proforma_cancellations=dataset.proforma_cancellations,
                            invoice_payments=dataset.invoice_payments,
                            proforma_payments=dataset.proforma_payments,
                            targets=targets,
                            costs=dataset.costs,
                            cuts=cuts,
                            parameters=parameters,
                            approved_exceptions=approved_exceptions,
                        )
                    )
                    if cancellation_context is not None:
                        reused_result = recalculate_cancelled_without_replacement(
                            reused_result,
                            targets,
                            dataset.costs,
                            cuts,
                            parameters,
                            approved_exceptions,
                        )
                    if scope_to_period:
                        reused_result = _scope_result_to_period(reused_result, period)
                return CalculationExecution(
                    run_code=str(existing["run_code"]),
                    status=str(existing["status"]),
                    idempotency_key=key,
                    reused=True,
                    commission_rows=int(existing.get("commission_rows", 0)),
                    floor_rows=int(existing.get("floor_rows", 0)),
                    payment_rows=int(existing.get("payment_rows", 0)),
                    discount_rows=int(existing.get("discount_rows", 0)),
                    warning_count=int(existing.get("warning_count", 0)),
                    warning_message=(
                        str(existing["message"])
                        if existing.get("message") is not None else None
                    ),
                    result=reused_result,
                )
            if existing["status"] == CALC_ERROR and not require_existing:
                self.repository.archive_failed_run(key)
                existing = None
            else:
                raise RuntimeError(
                    "Ya existe una ejecucion no completada para estos insumos: "
                    f"{existing['run_code']}"
                )

        if require_existing:
            raise LookupError(
                "No existe un cálculo completado para el periodo y los insumos "
                "vigentes. Solicite un recálculo a un usuario autorizado."
            )

        if period is not None and self.period_repository is not None:
            self.period_repository.require_recalculation_allowed(period)

        code = run_code or f"calculation-{uuid4()}"
        self.repository.start_run(
            code, key, period, self.rules_version, executed_by
        )
        logger.info(
            "Ejecucion de calculo iniciada",
            extra={
                "event": "calculation.started",
                "run_code": code,
                "status": CALC_RUNNING,
                "actor_user_id": executed_by,
                "period": period,
            },
        )
        try:
            result = calculate_commissions(
                CommissionInputs(
                    invoices=dataset.invoices,
                    proformas=dataset.proformas,
                    credit_notes=dataset.credit_notes,
                    proforma_cancellations=dataset.proforma_cancellations,
                    invoice_payments=dataset.invoice_payments,
                    proforma_payments=dataset.proforma_payments,
                    targets=targets,
                    costs=dataset.costs,
                    cuts=cuts,
                    parameters=parameters,
                    approved_exceptions=approved_exceptions,
                )
            )
            discounts = None
            discount_ids: tuple[int, ...] = ()
            if cancellation_context is not None:
                original_result = result
                result = recalculate_cancelled_without_replacement(
                    original_result,
                    targets,
                    dataset.costs,
                    cuts,
                    parameters,
                    approved_exceptions,
                )
                discounts = build_cancellation_discounts(
                    original_result.sales_floor,
                    cancellation_context.previous_compliance,
                    result.floor_compliance,
                    cancellation_context.paid_commissions,
                    cancellation_context.detection_period,
                    cancellation_context.source_calculation,
                    code,
                )
                discount_ids = tuple(
                    self.discount_repository.save(discounts, executed_by)
                )
            if scope_to_period:
                result = _scope_result_to_period(result, period)
            warning_count, warning_message = _calculation_warnings(result)
            status = (
                CALC_COMPLETED_WARNINGS if warning_count else CALC_COMPLETED
            )
            metrics = {
                "commission_rows": len(result.commission_detail),
                "floor_rows": len(result.floor_compliance),
                "payment_rows": len(result.payment_base.documents),
                "discount_rows": 0 if discounts is None else len(discounts),
                "warning_count": warning_count,
                "error_count": 0,
            }
            self.repository.save_result(code, result)
            self.repository.initialize_workflow(code, executed_by)
            self.repository.finish_run(code, status, warning_message, metrics)
            logger.info(
                "Ejecucion de calculo completada",
                extra={
                    "event": "calculation.completed",
                    "run_code": code,
                    "status": status,
                    "actor_user_id": executed_by,
                    "period": period,
                },
            )
            return CalculationExecution(
                run_code=code,
                status=status,
                idempotency_key=key,
                reused=False,
                commission_rows=len(result.commission_detail),
                floor_rows=len(result.floor_compliance),
                payment_rows=len(result.payment_base.documents),
                warning_count=warning_count,
                warning_message=warning_message,
                discount_rows=0 if discounts is None else len(discounts),
                discount_ids=discount_ids,
                discounts=discounts,
                result=result,
            )
        except Exception as exc:
            message = safe_error_message(exc)
            self.repository.finish_run(
                code, CALC_ERROR, message, {"error_count": 1}
            )
            logger.error(
                "Ejecucion de calculo fallida: %s",
                message,
                extra={
                    "event": "calculation.failed",
                    "run_code": code,
                    "status": CALC_ERROR,
                    "actor_user_id": executed_by,
                    "period": period,
                },
            )
            raise
