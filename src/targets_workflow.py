"""Versionamiento y aprobacion de pisos y metas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd

from .access_control import (
    AccessDeniedError,
    AuthorizationService,
    PERM_INPUT_REGISTER,
    PERM_TARGET_APPROVE,
    PERM_TARGET_REGISTER,
    PERM_TARGET_VERIFY,
)
from .database import Database


DRAFT = "BORRADOR"
PENDING_VERIFICATION = "PENDIENTE_VERIFICACION"
VERIFIED = "VERIFICADO"
PENDING_APPROVAL = "PENDIENTE_APROBACION"
APPROVED = "APROBADO"

TRANSITIONS = {
    DRAFT: PENDING_VERIFICATION,
    PENDING_VERIFICATION: VERIFIED,
    VERIFIED: PENDING_APPROVAL,
    PENDING_APPROVAL: APPROVED,
}

REQUIRED_COLUMNS = ("periodo", "ejecutivo", "linea_comision", "meta", "piso")
BUSINESS_KEY = ("periodo", "ejecutivo", "linea_comision")


@dataclass(frozen=True)
class TargetBatch:
    batch_id: int
    version: int
    status: str
    rows_count: int
    valid_count: int
    error_count: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_rows(data: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    missing = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError("Faltan columnas obligatorias: " + ", ".join(missing))

    rows = data.copy().reset_index(drop=True)
    rows["periodo"] = pd.to_datetime(
        rows["periodo"], errors="coerce", format="mixed"
    ).dt.to_period("M")
    rows["ejecutivo"] = rows["ejecutivo"].astype("string").str.strip().str.upper()
    rows["linea_comision"] = (
        rows["linea_comision"].astype("string").str.strip().str.upper()
    )
    rows["meta"] = pd.to_numeric(rows["meta"], errors="coerce")
    rows["piso"] = pd.to_numeric(rows["piso"], errors="coerce")

    duplicate_mask = rows.duplicated(list(BUSINESS_KEY), keep=False)
    issue_masks = {
        "Periodo invalido": rows["periodo"].isna(),
        "Ejecutivo vacio": rows["ejecutivo"].isna()
        | rows["ejecutivo"].eq("").fillna(True),
        "Linea vacia": rows["linea_comision"].isna()
        | rows["linea_comision"].eq("").fillna(True),
        "Meta no numerica": rows["meta"].isna(),
        "Piso no numerico": rows["piso"].isna(),
        "Clave periodo+ejecutivo+linea duplicada en la carga": duplicate_mask,
    }
    issue_columns = {
        message: pd.Series(pd.NA, index=rows.index, dtype="string").mask(mask, message)
        for message, mask in issue_masks.items()
    }
    stacked = pd.DataFrame(issue_columns).stack(future_stack=True).dropna()
    grouped_issues = stacked.groupby(level=0, sort=True).agg(list)
    errors = [
        {"row_number": int(index), "messages": list(messages)}
        for index, messages in grouped_issues.items()
    ]

    valid = rows.loc[~rows.index.isin(grouped_issues.index)].copy()
    valid["periodo"] = valid["periodo"].astype("string")
    return valid, errors


class PersistentTargetsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        identity = self.database.identity_primary_key()
        money = self.database.money_type()
        with self.database.session() as session:
            session.execute_statements((
                f"""
                CREATE TABLE IF NOT EXISTS target_batches (
                    id {identity},
                    version INTEGER NOT NULL UNIQUE,
                    source_name TEXT,
                    source_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rows_count INTEGER NOT NULL,
                    valid_count INTEGER NOT NULL,
                    error_count INTEGER NOT NULL,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_by INTEGER,
                    approved_at TEXT,
                    observation TEXT
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS target_batch_rows (
                    id {identity},
                    batch_id INTEGER NOT NULL,
                    row_number INTEGER NOT NULL,
                    period TEXT NOT NULL,
                    executive TEXT NOT NULL,
                    commission_line TEXT NOT NULL,
                    target_value {money} NOT NULL,
                    floor_value {money} NOT NULL,
                    manager TEXT,
                    executive_rate {money},
                    manager_rate {money},
                    director TEXT,
                    director_rate {money},
                    FOREIGN KEY (batch_id) REFERENCES target_batches(id),
                    UNIQUE (batch_id, period, executive, commission_line)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS target_batch_errors (
                    id {identity},
                    batch_id INTEGER NOT NULL,
                    row_number INTEGER NOT NULL,
                    messages_json TEXT NOT NULL,
                    FOREIGN KEY (batch_id) REFERENCES target_batches(id)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS target_status_history (
                    id {identity},
                    batch_id INTEGER NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    changed_by INTEGER NOT NULL,
                    changed_at TEXT NOT NULL,
                    observation TEXT,
                    FOREIGN KEY (batch_id) REFERENCES target_batches(id)
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_target_rows_key
                ON target_batch_rows(period, executive, commission_line, batch_id)
                """,
            ))

    def create_batch(
        self,
        data: pd.DataFrame,
        created_by: int,
        source_name: str | None = None,
        observation: str | None = None,
    ) -> TargetBatch:
        valid, errors = _normalize_rows(data)
        source_json = data.to_json(
            orient="records", date_format="iso", default_handler=str
        )
        source_hash = hashlib.sha256(source_json.encode("utf-8")).hexdigest()
        now = _utc_now()
        with self.database.session() as session:
            session.lock_table_for_write("target_batches")
            version = int(
                session.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM target_batches"
                ).fetchone()[0]
            )
            batch_id = session.insert_returning_id(
                """
                INSERT INTO target_batches (
                    version, source_name, source_hash, status, rows_count,
                    valid_count, error_count, created_by, created_at,
                    updated_at, observation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version, source_name, source_hash, DRAFT, len(data),
                    len(valid), len(errors), created_by, now, now, observation,
                ),
            )
            valid_records = []
            for row in valid.reset_index(names="row_number").to_dict(orient="records"):
                valid_records.append((
                    batch_id, int(row["row_number"]), str(row["periodo"]),
                    str(row["ejecutivo"]), str(row["linea_comision"]),
                    float(row["meta"]), float(row["piso"]),
                    _optional_text(row.get("jefe")),
                    _optional_number(row.get("comision_ejecutivo")),
                    _optional_number(row.get("comision_jefe")),
                    _optional_text(row.get("gerente")),
                    _optional_number(row.get("comision_gerente")),
                ))
            if valid_records:
                session.executemany(
                    """
                    INSERT INTO target_batch_rows (
                        batch_id, row_number, period, executive, commission_line,
                        target_value, floor_value, manager, executive_rate,
                        manager_rate, director, director_rate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    valid_records,
                )
            error_records = [
                (
                    batch_id, error["row_number"],
                    json.dumps(error["messages"], ensure_ascii=True),
                )
                for error in errors
            ]
            if error_records:
                session.executemany(
                    """
                    INSERT INTO target_batch_errors (
                        batch_id, row_number, messages_json
                    ) VALUES (?, ?, ?)
                    """,
                    error_records,
                )
            session.execute(
                """
                INSERT INTO target_status_history (
                    batch_id, previous_status, new_status, changed_by,
                    changed_at, observation
                ) VALUES (?, NULL, ?, ?, ?, ?)
                """,
                (batch_id, DRAFT, created_by, now, observation),
            )
        return TargetBatch(batch_id, version, DRAFT, len(data), len(valid), len(errors))

    def get_batch(self, batch_id: int) -> TargetBatch:
        with self.database.session() as session:
            row = session.execute(
                """
                SELECT id, version, status, rows_count, valid_count, error_count
                FROM target_batches WHERE id = ?
                """,
                (batch_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Carga inexistente: {batch_id}")
        return TargetBatch(*row)

    def list_batches(self) -> list[dict[str, object]]:
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT id, version, source_name, status, rows_count,
                       valid_count, error_count, created_by, created_at,
                       updated_at, approved_by, approved_at, observation
                FROM target_batches ORDER BY version DESC
                """
            ).fetchall()
        columns = (
            "batch_id", "version", "source_name", "status", "rows_count",
            "valid_count", "error_count", "created_by", "created_at",
            "updated_at", "approved_by", "approved_at", "observation",
        )
        return [dict(zip(columns, row)) for row in rows]

    def transition(
        self,
        batch_id: int,
        expected_status: str,
        new_status: str,
        changed_by: int,
        observation: str | None = None,
    ) -> TargetBatch:
        with self.database.session() as session:
            session.lock_table_for_write("target_batches")
            row = session.execute(
                "SELECT status, error_count FROM target_batches WHERE id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Carga inexistente: {batch_id}")
            if row[0] != expected_status:
                raise ValueError(
                    f"Transicion invalida: se esperaba {expected_status} y esta en {row[0]}"
                )
            if expected_status == DRAFT and row[1] > 0:
                raise ValueError("La carga tiene errores y no puede enviarse a verificacion")
            now = _utc_now()
            approved_values = (
                (changed_by, now) if new_status == APPROVED else (None, None)
            )
            session.execute(
                """
                UPDATE target_batches
                SET status = ?, updated_at = ?, approved_by = COALESCE(?, approved_by),
                    approved_at = COALESCE(?, approved_at)
                WHERE id = ?
                """,
                (new_status, now, *approved_values, batch_id),
            )
            session.execute(
                """
                INSERT INTO target_status_history (
                    batch_id, previous_status, new_status, changed_by,
                    changed_at, observation
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id, expected_status, new_status, changed_by,
                    now, observation,
                ),
            )
        return self.get_batch(batch_id)

    def load_approved_targets(self) -> pd.DataFrame:
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT r.period, r.executive, r.commission_line,
                       r.target_value, r.floor_value, r.manager,
                       r.executive_rate, r.manager_rate, r.director,
                       r.director_rate, b.version, b.approved_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.period, r.executive, r.commission_line
                           ORDER BY b.version DESC
                       ) AS row_priority
                FROM target_batch_rows r
                JOIN target_batches b ON b.id = r.batch_id
                WHERE b.status = ?
                """,
                (APPROVED,),
            ).fetchall()
        columns = [
            "periodo", "ejecutivo", "linea_comision", "meta", "piso",
            "jefe", "comision_ejecutivo", "comision_jefe", "gerente",
            "comision_gerente", "version_insumo", "fecha_aprobacion",
            "row_priority",
        ]
        output = pd.DataFrame.from_records(rows, columns=columns)
        if output.empty:
            return output.drop(columns="row_priority")
        return output.loc[output["row_priority"].eq(1)].drop(
            columns="row_priority"
        ).reset_index(drop=True)


class SqliteTargetsRepository(PersistentTargetsRepository):
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        super().__init__(Database.sqlite(self.database_path))


class PostgresTargetsRepository(PersistentTargetsRepository):
    def __init__(self, dsn: str) -> None:
        super().__init__(Database.postgresql(dsn))


def _optional_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _optional_number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


class TargetsWorkflowService:
    def __init__(
        self,
        repository: PersistentTargetsRepository,
        authorization: AuthorizationService,
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    def create_batch(
        self,
        data: pd.DataFrame,
        actor_user_id: int,
        source_name: str | None = None,
        observation: str | None = None,
        auto_approve: bool = False,
    ) -> TargetBatch:
        try:
            self.authorization.require_permission(actor_user_id, PERM_TARGET_REGISTER)
        except AccessDeniedError:
            self.authorization.require_permission(actor_user_id, PERM_INPUT_REGISTER)
        batch = self.repository.create_batch(
            data, actor_user_id, source_name, observation
        )
        if auto_approve:
            self.authorization.require_permission(actor_user_id, PERM_TARGET_APPROVE)
            if batch.error_count:
                raise ValueError("No se puede autoaprobar una carga con errores")
            for expected, new in TRANSITIONS.items():
                batch = self.repository.transition(
                    batch.batch_id, expected, new, actor_user_id, "Autoaprobacion"
                )
        return batch

    def submit(self, batch_id: int, actor_user_id: int) -> TargetBatch:
        self.authorization.require_permission(actor_user_id, PERM_TARGET_REGISTER)
        return self.repository.transition(
            batch_id, DRAFT, PENDING_VERIFICATION, actor_user_id
        )

    def verify(self, batch_id: int, actor_user_id: int) -> TargetBatch:
        self.authorization.require_permission(actor_user_id, PERM_TARGET_VERIFY)
        return self.repository.transition(
            batch_id, PENDING_VERIFICATION, VERIFIED, actor_user_id
        )

    def request_approval(self, batch_id: int, actor_user_id: int) -> TargetBatch:
        self.authorization.require_permission(actor_user_id, PERM_TARGET_VERIFY)
        return self.repository.transition(
            batch_id, VERIFIED, PENDING_APPROVAL, actor_user_id
        )

    def approve(self, batch_id: int, actor_user_id: int) -> TargetBatch:
        self.authorization.require_permission(actor_user_id, PERM_TARGET_APPROVE)
        return self.repository.transition(
            batch_id, PENDING_APPROVAL, APPROVED, actor_user_id
        )

    def list_batches(self) -> list[dict[str, object]]:
        return self.repository.list_batches()
