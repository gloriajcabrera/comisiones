"""Versiones de pago persistidas y Excel generado solo bajo demanda."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import logging
from pathlib import Path
from typing import Protocol, Sequence

import pandas as pd
from openpyxl import Workbook

from .access_control import (
    AuthorizationService,
    PERM_PAYMENT_FILE_DOWNLOAD,
    PERM_PAYMENT_FILE_GENERATE,
    PERM_PAYMENT_FILE_SEND,
)
from .database import Database
from .observability import safe_error_message
from .business_periods import PERIOD_CLOSED, PERIOD_FINALIZED, normalize_period
from .calculation_service import CALC_SUCCESS_STATUSES


BATCH_FINALIZED = "FINALIZADO"
FILE_SENT = "ENVIADO"
FILE_SEND_ERROR = "ERROR_ENVIO"
REQUIRED_PAYMENT_COLUMNS = ("identificacion", "nombre", "valor_total")


logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MailProvider(Protocol):
    def send(
        self,
        *,
        recipients: Sequence[str],
        subject: str,
        body: str,
        attachment_name: str,
        attachment_bytes: bytes,
    ) -> str | None: ...


class PaymentSummaryProvider(Protocol):
    def load_payment_summary(
        self, calculation_run: str, period: str
    ) -> pd.DataFrame: ...


class ActiveCalculationPaymentSummaryProvider:
    """Build the payable people summary from the active canonical calculation."""

    def __init__(
        self,
        result_provider: object,
        access_repository: object,
        discount_repository: object | None = None,
    ) -> None:
        self.result_provider = result_provider
        self.access_repository = access_repository
        self.discount_repository = discount_repository

    def load_payment_summary(
        self, calculation_run: str, period: str
    ) -> pd.DataFrame:
        active_run = str(getattr(self.result_provider, "run_code", ""))
        if active_run != calculation_run:
            raise RuntimeError(
                "La versión seleccionada pertenece a otro cálculo. "
                "Cargue ese cálculo antes de generar el archivo."
            )
        payments = self.result_provider.commission_results(calculation_run).copy()
        payments = payments.loc[payments["period"].astype("string").eq(period)].copy()
        if payments.empty:
            return pd.DataFrame(columns=REQUIRED_PAYMENT_COLUMNS)

        hierarchy = pd.DataFrame(
            self.access_repository.executive_hierarchy_context(
                payments["executive"].dropna().astype(str).unique(),
                f"{period}-01",
            )
        )
        if hierarchy.empty:
            hierarchy = pd.DataFrame(
                columns=["executive", "manager", "director"]
            )
        payments = payments.merge(
            hierarchy[["executive", "manager", "director"]],
            on="executive",
            how="left",
            validate="m:1",
        )
        parts = []
        for beneficiary, value_column in (
            ("executive", "commission_executive"),
            ("manager", "commission_manager"),
            ("director", "commission_director"),
        ):
            part = payments[[beneficiary, value_column]].rename(
                columns={beneficiary: "entity", value_column: "valor_total"}
            )
            part["valor_total"] = pd.to_numeric(
                part["valor_total"], errors="coerce"
            ).fillna(0)
            parts.append(part.loc[part["entity"].notna()])
        totals = pd.concat(parts, ignore_index=True).groupby(
            "entity", as_index=False
        )["valor_total"].sum()

        if self.discount_repository is not None:
            discounts = self.discount_repository.list_applications(period)
            if not discounts.empty:
                applied = discounts.groupby("executive", as_index=False)["amount"].sum()
                applied = applied.rename(
                    columns={"executive": "entity", "amount": "discount"}
                )
                totals = totals.merge(applied, on="entity", how="left", validate="1:1")
                totals["valor_total"] -= pd.to_numeric(
                    totals.pop("discount"), errors="coerce"
                ).fillna(0)

        profiles = self.access_repository.list_user_profiles(f"{period}-01")
        identity_rows = [
            {
                "entity": entity,
                "identificacion": profile.identification,
                "nombre": f"{profile.first_names} {profile.last_names}".strip(),
            }
            for profile in profiles
            for entity in profile.commercial_entities
        ]
        identities = pd.DataFrame(
            identity_rows, columns=["entity", "identificacion", "nombre"]
        )
        result = totals.merge(identities, on="entity", how="left", validate="1:1")
        return _validate_summary(
            result[["identificacion", "nombre", "valor_total"]]
        )


@dataclass(frozen=True)
class PaymentBatch:
    batch_id: int
    period: str
    version: int
    calculation_run: str
    status: str

    @property
    def download_name(self) -> str:
        return f"pago_comisiones_{self.period}_v{self.version:03d}.xlsx"


@dataclass(frozen=True)
class PaymentBatchRecord:
    batch_id: int
    period: str
    version: int
    calculation_run: str
    status: str
    finalized_by: int
    finalized_at: str
    download_count: int
    send_count: int
    last_send_status: str | None
    finalization_reason: str | None


class PaymentFileRepository(Protocol):
    def create_batch(
        self, period: str, calculation_run: str, finalized_by: int,
        finalization_reason: str = "",
        payment_summary: pd.DataFrame | None = None,
    ) -> PaymentBatch: ...

    def batch(self, batch_id: int) -> PaymentBatch: ...

    def payment_summary(self, batch_id: int) -> pd.DataFrame: ...

    def record_download(self, batch_id: int, user_id: int) -> None: ...

    def record_send(
        self, batch_id: int, sender_user_id: int, recipients: Sequence[str],
        status: str, provider_reference: str | None = None,
        error_message: str | None = None,
    ) -> None: ...

    def list_batches(self, period: str | None = None) -> list[PaymentBatchRecord]: ...

    def list_events(self, batch_id: int) -> list[dict[str, object]]: ...


class PersistentPaymentFileRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        identity = self.database.identity_primary_key()
        identifier = self.database.identifier_type()
        with self.database.session() as session:
            session.execute_statements((
                f"""
                CREATE TABLE IF NOT EXISTS payment_batches (
                    id {identity},
                    period TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    calculation_run TEXT NOT NULL,
                    status TEXT NOT NULL,
                    finalized_by INTEGER NOT NULL,
                    finalized_at TEXT NOT NULL,
                    UNIQUE (period, version)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS payment_batch_finalization_events (
                    batch_id {identifier} PRIMARY KEY,
                    reason TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (batch_id) REFERENCES payment_batches(id)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS payment_batch_totals (
                    id {identity},
                    batch_id {identifier} NOT NULL,
                    identification TEXT NOT NULL,
                    beneficiary_name TEXT NOT NULL,
                    total_value {self.database.money_type()} NOT NULL,
                    FOREIGN KEY (batch_id) REFERENCES payment_batches(id),
                    UNIQUE (batch_id, identification)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS payment_download_events (
                    id {identity},
                    batch_id {identifier} NOT NULL,
                    downloaded_by {identifier} NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    FOREIGN KEY (batch_id) REFERENCES payment_batches(id)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS payment_file_send_events (
                    id {identity},
                    batch_id {identifier} NOT NULL,
                    sender_user_id {identifier} NOT NULL,
                    recipients TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_reference TEXT,
                    error_message TEXT,
                    FOREIGN KEY (batch_id) REFERENCES payment_batches(id)
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_payment_batch_period
                ON payment_batches(period, version, status)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_payment_download_batch
                ON payment_download_events(batch_id, downloaded_at DESC)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_payment_send_batch
                ON payment_file_send_events(batch_id, sent_at DESC)
                """,
            ))

    def create_batch(
        self,
        period: str,
        calculation_run: str,
        finalized_by: int,
        finalization_reason: str = "",
        payment_summary: pd.DataFrame | None = None,
    ) -> PaymentBatch:
        with self.database.session() as session:
            session.lock_table_for_write("payment_batches")
            version = int(session.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM payment_batches WHERE period = ?",
                (period,),
            ).fetchone()[0])
            batch_id = session.insert_returning_id(
                """
                INSERT INTO payment_batches
                (period, version, calculation_run, status, finalized_by, finalized_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (period, version, calculation_run, BATCH_FINALIZED,
                 finalized_by, _utc_now()),
            )
            session.execute(
                """
                INSERT INTO payment_batch_finalization_events
                (batch_id, reason, recorded_at) VALUES (?, ?, ?)
                """,
                (batch_id, str(finalization_reason).strip(), _utc_now()),
            )
            if payment_summary is not None and not payment_summary.empty:
                rows = [
                    (
                        batch_id,
                        str(row.identificacion),
                        str(row.nombre),
                        float(row.valor_total),
                    )
                    for row in payment_summary.itertuples(index=False)
                ]
                session.executemany(
                    """
                    INSERT INTO payment_batch_totals
                    (batch_id, identification, beneficiary_name, total_value)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )
        return PaymentBatch(
            batch_id, period, version, calculation_run, BATCH_FINALIZED
        )

    def batch(self, batch_id: int) -> PaymentBatch:
        with self.database.session() as session:
            row = session.execute(
                """
                SELECT id, period, version, calculation_run, status
                FROM payment_batches WHERE id = ?
                """,
                (batch_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Version de pago inexistente: {batch_id}")
        return PaymentBatch(
            int(row[0]), str(row[1]), int(row[2]), str(row[3]), str(row[4])
        )

    def payment_summary(self, batch_id: int) -> pd.DataFrame:
        self.batch(batch_id)
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT identification, beneficiary_name, total_value
                FROM payment_batch_totals
                WHERE batch_id = ? ORDER BY identification
                """,
                (batch_id,),
            ).fetchall()
        return pd.DataFrame(rows, columns=REQUIRED_PAYMENT_COLUMNS)

    def record_download(self, batch_id: int, user_id: int) -> None:
        with self.database.session() as session:
            session.execute(
                """
                INSERT INTO payment_download_events
                (batch_id, downloaded_by, downloaded_at) VALUES (?, ?, ?)
                """,
                (batch_id, user_id, _utc_now()),
            )

    def record_send(
        self, batch_id: int, sender_user_id: int, recipients: Sequence[str],
        status: str, provider_reference: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.database.session() as session:
            session.execute(
                """
                INSERT INTO payment_file_send_events
                (batch_id, sender_user_id, recipients, sent_at, status,
                 provider_reference, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (batch_id, sender_user_id, ";".join(recipients), _utc_now(),
                 status, provider_reference, error_message),
            )

    def list_batches(self, period: str | None = None) -> list[PaymentBatchRecord]:
        query = """
            SELECT b.id, b.period, b.version, b.calculation_run, b.status,
                   b.finalized_by, b.finalized_at,
                   (SELECT COUNT(*) FROM payment_download_events d
                    WHERE d.batch_id = b.id) AS download_count,
                   (SELECT COUNT(*) FROM payment_file_send_events s
                    WHERE s.batch_id = b.id) AS send_count,
                   (SELECT s.status FROM payment_file_send_events s
                    WHERE s.batch_id = b.id ORDER BY s.sent_at DESC, s.id DESC
                    LIMIT 1) AS last_send_status,
                   (SELECT f.reason FROM payment_batch_finalization_events f
                    WHERE f.batch_id = b.id) AS finalization_reason
            FROM payment_batches b
        """
        parameters: tuple[object, ...] = ()
        if period is not None:
            query += " WHERE b.period = ?"
            parameters = (normalize_period(period),)
        query += " ORDER BY b.period DESC, b.version DESC"
        with self.database.session() as session:
            rows = session.execute(query, parameters).fetchall()
        return [PaymentBatchRecord(*row) for row in rows]

    def list_events(self, batch_id: int) -> list[dict[str, object]]:
        self.batch(batch_id)
        with self.database.session() as session:
            downloads = session.execute(
                """
                SELECT 'DESCARGA', downloaded_by, downloaded_at, NULL, NULL, NULL
                FROM payment_download_events WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchall()
            sends = session.execute(
                """
                SELECT 'ENVIO', sender_user_id, sent_at, status,
                       recipients, error_message
                FROM payment_file_send_events WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchall()
        columns = ("event", "user_id", "occurred_at", "status", "recipients", "error")
        events = [dict(zip(columns, row)) for row in (*downloads, *sends)]
        return sorted(events, key=lambda item: str(item["occurred_at"]), reverse=True)


class SqlitePaymentFileRepository(PersistentPaymentFileRepository):
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        super().__init__(Database.sqlite(self.database_path))


class PostgresPaymentFileRepository(PersistentPaymentFileRepository):
    def __init__(self, dsn: str) -> None:
        super().__init__(Database.postgresql(dsn))


class PaymentFileService:
    def __init__(
        self, repository: PaymentFileRepository,
        authorization: AuthorizationService,
        summary_provider: PaymentSummaryProvider,
        mail_provider: MailProvider | None = None,
        calculation_repository: object | None = None,
        period_repository: object | None = None,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.summary_provider = summary_provider
        self.mail_provider = mail_provider
        self.calculation_repository = calculation_repository
        self.period_repository = period_repository

    def finalize(
        self, actor_user_id: int, period: str, calculation_run: str,
        finalization_reason: str = "",
    ) -> PaymentBatch:
        self.authorization.require_permission(actor_user_id, PERM_PAYMENT_FILE_GENERATE)
        if not str(finalization_reason).strip():
            raise ValueError("La observación de finalización es obligatoria")
        normalized_period = normalize_period(period)
        if self.period_repository is not None:
            period_status = self.period_repository.status(normalized_period)
            if period_status not in {PERIOD_FINALIZED, PERIOD_CLOSED}:
                if period_status is None:
                    raise KeyError(f"Periodo inexistente: {normalized_period}")
                raise RuntimeError(
                    f"El periodo {normalized_period} está {period_status}; "
                    "debe estar FINALIZADO o CERRADO para generar una versión de pago"
                )
        if self.calculation_repository is not None:
            calculation = self.calculation_repository.get_run(calculation_run)
            if calculation is None:
                raise KeyError(f"Cálculo inexistente: {calculation_run}")
            if calculation["status"] not in CALC_SUCCESS_STATUSES:
                raise RuntimeError(
                    f"El cálculo {calculation_run} no está completado"
                )
            workflow_status = self.calculation_repository.workflow_status(
                calculation_run
            )
            if workflow_status != "APROBADO":
                raise RuntimeError(
                    f"El cálculo {calculation_run} no está aprobado"
                )
            if str(calculation.get("period") or "") != normalized_period:
                raise ValueError(
                    f"El cálculo {calculation_run} no corresponde al periodo "
                    f"{normalized_period}"
                )
        summary = _validate_summary(
            self.summary_provider.load_payment_summary(
                calculation_run, normalized_period
            )
        )
        return self.repository.create_batch(
            normalized_period, calculation_run, actor_user_id,
            finalization_reason, summary,
        )

    def list_batches(
        self, actor_user_id: int, period: str | None = None
    ) -> list[PaymentBatchRecord]:
        self.authorization.require_permission(actor_user_id, PERM_PAYMENT_FILE_GENERATE)
        return self.repository.list_batches(period)

    def list_events(
        self, actor_user_id: int, batch_id: int
    ) -> list[dict[str, object]]:
        self.authorization.require_permission(actor_user_id, PERM_PAYMENT_FILE_GENERATE)
        return self.repository.list_events(batch_id)

    def download(self, actor_user_id: int, batch_id: int) -> tuple[str, bytes]:
        self.authorization.require_permission(actor_user_id, PERM_PAYMENT_FILE_DOWNLOAD)
        batch = self.repository.batch(batch_id)
        summary = _validate_summary(self.repository.payment_summary(batch_id))
        content = _workbook_bytes(
            summary, batch.period, batch.calculation_run
        )
        self.repository.record_download(batch_id, actor_user_id)
        return batch.download_name, content

    def send(
        self, actor_user_id: int, batch_id: int, recipients: Sequence[str],
        subject: str, body: str,
    ) -> None:
        self.authorization.require_permission(actor_user_id, PERM_PAYMENT_FILE_SEND)
        if self.mail_provider is None:
            raise RuntimeError("No hay proveedor de correo configurado")
        normalized = tuple(dict.fromkeys(
            address.strip().lower() for address in recipients if address.strip()
        ))
        if not normalized:
            raise ValueError("Debe indicar al menos un destinatario")
        batch = self.repository.batch(batch_id)
        summary = _validate_summary(self.repository.payment_summary(batch_id))
        content = _workbook_bytes(
            summary, batch.period, batch.calculation_run
        )
        try:
            reference = self.mail_provider.send(
                recipients=normalized, subject=subject, body=body,
                attachment_name=batch.download_name, attachment_bytes=content,
            )
        except Exception as exc:
            message = safe_error_message(exc)
            self.repository.record_send(
                batch_id, actor_user_id, normalized, FILE_SEND_ERROR,
                error_message=message,
            )
            logger.error(
                "Envio de archivo de pago fallido: %s",
                message,
                extra={
                    "event": "payment_file.send_failed",
                    "run_code": batch.calculation_run,
                    "status": FILE_SEND_ERROR,
                    "actor_user_id": actor_user_id,
                    "period": batch.period,
                },
            )
            raise
        self.repository.record_send(
            batch_id, actor_user_id, normalized, FILE_SENT,
            provider_reference=reference,
        )


def _validate_summary(data: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(REQUIRED_PAYMENT_COLUMNS).difference(data.columns))
    if missing:
        raise ValueError(f"Faltan columnas de pago: {', '.join(missing)}")
    work = data.loc[:, REQUIRED_PAYMENT_COLUMNS].copy()
    work["identificacion"] = work["identificacion"].astype("string").str.strip()
    work["nombre"] = work["nombre"].astype("string").str.strip()
    work["valor_total"] = pd.to_numeric(work["valor_total"], errors="coerce")
    if work[list(REQUIRED_PAYMENT_COLUMNS)].isna().any().any():
        raise ValueError("El resumen de pago contiene valores obligatorios invalidos")
    if work["identificacion"].eq("").any() or work["nombre"].eq("").any():
        raise ValueError("Identificacion y nombre no pueden estar vacios")
    names_per_id = work.groupby("identificacion")["nombre"].nunique()
    if names_per_id.gt(1).any():
        conflicts = ", ".join(names_per_id.loc[names_per_id.gt(1)].index.astype(str))
        raise ValueError(
            "Una identificación tiene nombres diferentes en el resumen: " + conflicts
        )
    work = work.groupby("identificacion", as_index=False).agg(
        nombre=("nombre", "first"), valor_total=("valor_total", "sum")
    )
    return work.sort_values(["identificacion", "nombre"], kind="stable")


def _workbook_bytes(data: pd.DataFrame, period: str, calculation_run: str) -> bytes:
    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = "Pago"
        sheet.append(["Identificacion", "Nombre", "Valor Total"])
        for row in data.itertuples(index=False, name=None):
            sheet.append(list(row))
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.column_dimensions["A"].width = 20
        sheet.column_dimensions["B"].width = 42
        sheet.column_dimensions["C"].width = 18
        for cell in sheet[1]:
            font = copy(cell.font)
            font.bold = True
            cell.font = font
        for cell in sheet["C"][1:]:
            cell.number_format = '#,##0.00'
        metadata = workbook.create_sheet("Trazabilidad")
        metadata.append(["Periodo", period])
        metadata.append(["Calculo", calculation_run])
        metadata.append(["Generado UTC", _utc_now()])
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
    finally:
        workbook.close()
