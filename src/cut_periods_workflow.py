"""Fechas de corte versionadas, aprobadas y consumibles desde base de datos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pandas as pd

from .access_control import (
    AuthorizationService,
    PERM_INPUT_REGISTER,
    PERM_TARGET_APPROVE,
    PERM_TARGET_VERIFY,
)
from .database import Database
from .payment_base import normalize_cut_periods
from .targets_workflow import (
    APPROVED,
    DRAFT,
    PENDING_APPROVAL,
    PENDING_VERIFICATION,
    VERIFIED,
)


@dataclass(frozen=True)
class CutBatch:
    batch_id: int
    version: int
    status: str
    rows_count: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_cut_input(data: pd.DataFrame) -> pd.DataFrame:
    cuts = normalize_cut_periods(data)
    cuts["inicio_corte"] = cuts["inicio_corte"].dt.strftime("%Y-%m-%d")
    cuts["fin_corte"] = cuts["fin_corte"].dt.strftime("%Y-%m-%d")
    return cuts


class PersistentCutPeriodRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        identity = self.database.identity_primary_key()
        with self.database.session() as session:
            session.execute_statements((
                f"""
                CREATE TABLE IF NOT EXISTS cut_period_batches (
                    id {identity}, version INTEGER NOT NULL UNIQUE,
                    source_name TEXT, source_hash TEXT NOT NULL,
                    status TEXT NOT NULL, rows_count INTEGER NOT NULL,
                    created_by INTEGER NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, approved_by INTEGER,
                    approved_at TEXT, observation TEXT
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS cut_period_rows (
                    id {identity}, batch_id INTEGER NOT NULL,
                    start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    cut_period TEXT NOT NULL,
                    UNIQUE(batch_id, cut_period),
                    FOREIGN KEY(batch_id) REFERENCES cut_period_batches(id)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS cut_period_status_history (
                    id {identity}, batch_id INTEGER NOT NULL,
                    previous_status TEXT, new_status TEXT NOT NULL,
                    changed_by INTEGER NOT NULL, changed_at TEXT NOT NULL,
                    observation TEXT,
                    FOREIGN KEY(batch_id) REFERENCES cut_period_batches(id)
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_cut_period_rows_key
                ON cut_period_rows(cut_period, batch_id)
                """,
            ))

    def create_batch(
        self, data: pd.DataFrame, created_by: int,
        source_name: str | None = None, observation: str | None = None,
    ) -> CutBatch:
        cuts = normalize_cut_input(data)
        payload = cuts.to_json(orient="records", date_format="iso")
        source_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        now = _utc_now()
        with self.database.session() as session:
            session.lock_table_for_write("cut_period_batches")
            version = int(session.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM cut_period_batches"
            ).fetchone()[0])
            batch_id = session.insert_returning_id(
                """
                INSERT INTO cut_period_batches (
                    version, source_name, source_hash, status, rows_count,
                    created_by, created_at, updated_at, observation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (version, source_name, source_hash, DRAFT, len(cuts), created_by,
                 now, now, observation),
            )
            session.executemany(
                """
                INSERT INTO cut_period_rows
                (batch_id, start_date, end_date, cut_period) VALUES (?, ?, ?, ?)
                """,
                [
                    (batch_id, row.inicio_corte, row.fin_corte, row.periodo_corte)
                    for row in cuts.itertuples(index=False)
                ],
            )
            session.execute(
                """
                INSERT INTO cut_period_status_history
                (batch_id, previous_status, new_status, changed_by, changed_at, observation)
                VALUES (?, NULL, ?, ?, ?, ?)
                """,
                (batch_id, DRAFT, created_by, now, observation),
            )
        return CutBatch(batch_id, version, DRAFT, len(cuts))

    def transition(
        self, batch_id: int, expected: str, new: str,
        changed_by: int, observation: str | None = None,
    ) -> CutBatch:
        with self.database.session() as session:
            session.lock_table_for_write("cut_period_batches")
            row = session.execute(
                "SELECT version, status, rows_count FROM cut_period_batches WHERE id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Carga de cortes inexistente: {batch_id}")
            if row[1] != expected:
                raise ValueError(f"Transición inválida: se esperaba {expected} y está en {row[1]}")
            if new == APPROVED:
                normalize_cut_periods(self._effective_rows(session, batch_id))
            now = _utc_now()
            approved_by, approved_at = (
                (changed_by, now) if new == APPROVED else (None, None)
            )
            session.execute(
                """
                UPDATE cut_period_batches SET status = ?, updated_at = ?,
                    approved_by = COALESCE(?, approved_by),
                    approved_at = COALESCE(?, approved_at) WHERE id = ?
                """,
                (new, now, approved_by, approved_at, batch_id),
            )
            session.execute(
                """
                INSERT INTO cut_period_status_history
                (batch_id, previous_status, new_status, changed_by, changed_at, observation)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (batch_id, expected, new, changed_by, now, observation),
            )
        return CutBatch(batch_id, int(row[0]), new, int(row[2]))

    def list_batches(self) -> list[dict[str, object]]:
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT id, version, source_name, status, rows_count,
                       created_by, created_at, updated_at, approved_by,
                       approved_at, observation
                FROM cut_period_batches ORDER BY version DESC
                """
            ).fetchall()
        columns = (
            "batch_id", "version", "source_name", "status", "rows_count",
            "created_by", "created_at", "updated_at", "approved_by",
            "approved_at", "observation",
        )
        return [dict(zip(columns, row)) for row in rows]

    def _effective_rows(self, session, pending_batch_id: int | None = None) -> pd.DataFrame:
        rows = session.execute(
            """
            SELECT r.start_date, r.end_date, r.cut_period, b.version, b.id,
                   CASE WHEN b.id = ? THEN 1 ELSE 0 END AS pending_priority
            FROM cut_period_rows r JOIN cut_period_batches b ON b.id = r.batch_id
            WHERE b.status = ? OR b.id = ?
            """,
            (pending_batch_id, APPROVED, pending_batch_id),
        ).fetchall()
        latest: dict[str, tuple[object, ...]] = {}
        for row in rows:
            current = latest.get(str(row[2]))
            rank = (int(row[5]), int(row[3]))
            if current is None or rank > (int(current[5]), int(current[3])):
                latest[str(row[2])] = row
        return pd.DataFrame(
            [
                {"inicio_corte": row[0], "fin_corte": row[1], "periodo_corte": row[2]}
                for row in latest.values()
            ],
            columns=("inicio_corte", "fin_corte", "periodo_corte"),
        )

    def load_approved_cuts(self) -> pd.DataFrame:
        with self.database.session() as session:
            result = self._effective_rows(session)
        if result.empty:
            return result
        return normalize_cut_periods(result)


class SqliteCutPeriodRepository(PersistentCutPeriodRepository):
    def __init__(self, path: str | Path) -> None:
        self.database_path = Path(path)
        super().__init__(Database.sqlite(path))


class PostgresCutPeriodRepository(PersistentCutPeriodRepository):
    def __init__(self, dsn: str) -> None:
        super().__init__(Database.postgresql(dsn))


class CutPeriodWorkflowService:
    def __init__(self, repository: PersistentCutPeriodRepository,
                 authorization: AuthorizationService) -> None:
        self.repository = repository
        self.authorization = authorization

    def create_batch(self, data: pd.DataFrame, actor_user_id: int,
                     source_name: str | None = None,
                     observation: str | None = None) -> CutBatch:
        self.authorization.require_permission(actor_user_id, PERM_INPUT_REGISTER)
        return self.repository.create_batch(
            data, actor_user_id, source_name, observation
        )

    def submit(self, batch_id: int, actor_user_id: int) -> CutBatch:
        self.authorization.require_permission(actor_user_id, PERM_INPUT_REGISTER)
        return self.repository.transition(
            batch_id, DRAFT, PENDING_VERIFICATION, actor_user_id
        )

    def verify(self, batch_id: int, actor_user_id: int) -> CutBatch:
        self.authorization.require_permission(actor_user_id, PERM_TARGET_VERIFY)
        return self.repository.transition(
            batch_id, PENDING_VERIFICATION, VERIFIED, actor_user_id
        )

    def request_approval(self, batch_id: int, actor_user_id: int) -> CutBatch:
        self.authorization.require_permission(actor_user_id, PERM_TARGET_VERIFY)
        return self.repository.transition(
            batch_id, VERIFIED, PENDING_APPROVAL, actor_user_id
        )

    def approve(self, batch_id: int, actor_user_id: int) -> CutBatch:
        self.authorization.require_permission(actor_user_id, PERM_TARGET_APPROVE)
        return self.repository.transition(
            batch_id, PENDING_APPROVAL, APPROVED, actor_user_id
        )

    def list_batches(self) -> list[dict[str, object]]:
        return self.repository.list_batches()
