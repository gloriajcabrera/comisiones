"""Servicio idempotente de sincronizacion entre NetSuite y persistencia local."""

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

from .netsuite_provider import (
    IncrementalNetSuiteDataProvider,
    NetSuiteDataProvider,
    NetSuiteDataset,
)
from .database import Database
from .access_control import AuthorizationService, PERM_NETSUITE_SYNC
from .data_quality import STABLE_KEY_FIELDS, validate_netsuite_dataset
from .observability import safe_error_message


SYNC_PENDING = "PENDIENTE"
SYNC_RUNNING = "EN_PROCESO"
SYNC_COMPLETED = "COMPLETADO"
SYNC_COMPLETED_WARNINGS = "COMPLETADO_CON_ADVERTENCIAS"
SYNC_ERROR = "ERROR"


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncMetrics:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0


@dataclass(frozen=True)
class SyncResult:
    run_code: str
    status: str
    rows_by_dataset: dict[str, int]
    metrics: SyncMetrics


@dataclass(frozen=True)
class SyncRun:
    run_code: str
    sync_type: str
    status: str
    started_at: str
    finished_at: str | None
    executed_by: str
    created_count: int
    updated_count: int
    unchanged_count: int
    deactivated_count: int
    message: str | None


class NetSuiteSyncRepository(Protocol):
    def start_run(
        self,
        run_code: str,
        executed_by: str,
        sync_type: str,
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> None: ...

    def save_snapshot(
        self,
        run_code: str,
        dataset: NetSuiteDataset,
        *,
        deactivate_missing: bool = True,
    ) -> SyncMetrics: ...

    def finish_run(
        self,
        run_code: str,
        status: str,
        metrics: SyncMetrics,
        message: str | None = None,
    ) -> None: ...

    def list_runs(self, limit: int = 50) -> list[SyncRun]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dataset_frames(dataset: NetSuiteDataset) -> dict[str, pd.DataFrame]:
    return {
        name: frame
        for name, frame in asdict(dataset).items()
        if isinstance(frame, pd.DataFrame)
    }


def _canonical_records(
    dataset_name: str, frame: pd.DataFrame
) -> list[tuple[str, str, str]]:
    """Return stable business keys, content hashes and JSON payloads."""
    if frame.empty:
        return []
    key_fields = STABLE_KEY_FIELDS[dataset_name]
    missing = sorted(set(key_fields).difference(frame.columns))
    if missing:
        raise ValueError(
            f"{dataset_name}: faltan campos de identidad estable: {', '.join(missing)}"
        )
    ordering_fields = [
        field for field in (*key_fields, "previous_line", "next_line")
        if field in frame.columns
    ]
    ordered = frame.sort_values(
        ordering_fields, kind="stable", na_position="last"
    ) if ordering_fields else frame
    payloads = json.loads(ordered.to_json(orient="records", date_format="iso"))
    occurrences: dict[str, int] = {}
    records: list[tuple[str, str, str]] = []
    for row in payloads:
        payload = json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        source_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        identity = [row.get(field) for field in key_fields]
        if any(value is None or str(value).strip() == "" for value in identity):
            raise ValueError(
                f"{dataset_name}: identidad estable nula en {', '.join(key_fields)}"
            )
        identity_json = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
        base_key = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
        occurrence = occurrences.get(base_key, 0)
        occurrences[base_key] = occurrence + 1
        record_key = f"{base_key}:{occurrence}"
        records.append((record_key, source_hash, payload))
    return records


class PersistentNetSuiteSyncRepository:
    """Transactional staging repository shared by SQLite and PostgreSQL."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.database.session() as session:
            session.execute_statements((
                """
                CREATE TABLE IF NOT EXISTS netsuite_sync_runs (
                    run_code TEXT PRIMARY KEY,
                    sync_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    executed_by TEXT NOT NULL,
                    created_count INTEGER NOT NULL DEFAULT 0,
                    updated_count INTEGER NOT NULL DEFAULT 0,
                    unchanged_count INTEGER NOT NULL DEFAULT 0,
                    deactivated_count INTEGER NOT NULL DEFAULT 0,
                    message TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS netsuite_sync_records (
                    dataset_name TEXT NOT NULL,
                    record_key TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    first_seen_run TEXT NOT NULL,
                    last_seen_run TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (dataset_name, record_key),
                    FOREIGN KEY (first_seen_run) REFERENCES netsuite_sync_runs(run_code),
                    FOREIGN KEY (last_seen_run) REFERENCES netsuite_sync_runs(run_code)
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_netsuite_sync_runs_status_started
                ON netsuite_sync_runs(status, started_at DESC)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_netsuite_records_active
                ON netsuite_sync_records(dataset_name, is_active)
                """,
                """
                CREATE TABLE IF NOT EXISTS netsuite_sync_schemas (
                    dataset_name TEXT PRIMARY KEY,
                    columns_json TEXT NOT NULL,
                    dtypes_json TEXT NOT NULL,
                    last_seen_run TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (last_seen_run) REFERENCES netsuite_sync_runs(run_code)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS netsuite_sync_windows (
                    run_code TEXT PRIMARY KEY,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    FOREIGN KEY (run_code) REFERENCES netsuite_sync_runs(run_code)
                )
                """,
            ))

    def start_run(
        self,
        run_code: str,
        executed_by: str,
        sync_type: str,
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> None:
        with self.database.session() as session:
            session.lock_table_for_write("netsuite_sync_runs")
            active = session.execute(
                "SELECT run_code FROM netsuite_sync_runs WHERE status = ? LIMIT 1",
                (SYNC_RUNNING,),
            ).fetchone()
            if active is not None:
                raise RuntimeError(
                    f"Ya existe una sincronizacion en proceso: {active[0]}"
                )
            session.execute(
                """
                INSERT INTO netsuite_sync_runs (
                    run_code, sync_type, status, started_at, executed_by
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_code, sync_type, SYNC_RUNNING, _utc_now(), executed_by),
            )
            if window_start is not None and window_end is not None:
                session.execute(
                    """
                    INSERT INTO netsuite_sync_windows (
                        run_code, window_start, window_end
                    ) VALUES (?, ?, ?)
                    """,
                    (run_code, window_start, window_end),
                )

    def save_snapshot(
        self,
        run_code: str,
        dataset: NetSuiteDataset,
        *,
        deactivate_missing: bool = True,
    ) -> SyncMetrics:
        totals = SyncMetrics()
        now = _utc_now()
        with self.database.session() as session:
            session.lock_table_for_write("netsuite_sync_records")
            for dataset_name, frame in _dataset_frames(dataset).items():
                session.execute(
                    """
                    INSERT INTO netsuite_sync_schemas (
                        dataset_name, columns_json, dtypes_json,
                        last_seen_run, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(dataset_name) DO UPDATE SET
                        columns_json = excluded.columns_json,
                        dtypes_json = excluded.dtypes_json,
                        last_seen_run = excluded.last_seen_run,
                        updated_at = excluded.updated_at
                    """,
                    (
                        dataset_name,
                        json.dumps([str(column) for column in frame.columns]),
                        json.dumps(
                            {
                                str(column): str(dtype)
                                for column, dtype in frame.dtypes.items()
                            }
                        ),
                        run_code,
                        now,
                    ),
                )
                records = _canonical_records(dataset_name, frame)
                incoming_keys = {record[0] for record in records}
                existing_rows = session.execute(
                    """
                    SELECT record_key, source_hash, is_active
                    FROM netsuite_sync_records
                    WHERE dataset_name = ?
                    """,
                    (dataset_name,),
                ).fetchall()
                existing = {row[0]: (row[1], bool(row[2])) for row in existing_rows}

                created = updated = unchanged = 0
                for record_key, source_hash, payload in records:
                    previous = existing.get(record_key)
                    if previous is None:
                        session.execute(
                            """
                            INSERT INTO netsuite_sync_records (
                                dataset_name, record_key, source_hash, payload_json,
                                first_seen_run, last_seen_run, is_active,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                            """,
                            (
                                dataset_name, record_key, source_hash, payload,
                                run_code, run_code, now, now,
                            ),
                        )
                        created += 1
                    else:
                        was_active = previous[1]
                        session.execute(
                            """
                            UPDATE netsuite_sync_records
                            SET payload_json = ?, source_hash = ?, last_seen_run = ?,
                                is_active = 1, updated_at = ?
                            WHERE dataset_name = ? AND record_key = ?
                            """,
                            (payload, source_hash, run_code, now, dataset_name, record_key),
                        )
                        if was_active and previous[0] == source_hash:
                            unchanged += 1
                        else:
                            updated += 1

                active_keys = {key for key, (_, active) in existing.items() if active}
                missing_keys = (
                    active_keys - incoming_keys if deactivate_missing else set()
                )
                for record_key in missing_keys:
                    session.execute(
                        """
                        UPDATE netsuite_sync_records
                        SET is_active = 0, updated_at = ?
                        WHERE dataset_name = ? AND record_key = ?
                        """,
                        (now, dataset_name, record_key),
                    )

                totals = SyncMetrics(
                    created=totals.created + created,
                    updated=totals.updated + updated,
                    unchanged=totals.unchanged + unchanged,
                    deactivated=totals.deactivated + len(missing_keys),
                )
        return totals

    def finish_run(
        self,
        run_code: str,
        status: str,
        metrics: SyncMetrics,
        message: str | None = None,
    ) -> None:
        with self.database.session() as session:
            session.execute(
                """
                UPDATE netsuite_sync_runs
                SET status = ?, finished_at = ?, created_count = ?,
                    updated_count = ?, unchanged_count = ?,
                    deactivated_count = ?, message = ?
                WHERE run_code = ?
                """,
                (
                    status, _utc_now(), metrics.created, metrics.updated,
                    metrics.unchanged, metrics.deactivated, message, run_code,
                ),
            )

    def load_active_frame(self, dataset_name: str) -> pd.DataFrame:
        with self.database.session() as session:
            schema = session.execute(
                """
                SELECT columns_json
                FROM netsuite_sync_schemas
                WHERE dataset_name = ?
                """,
                (dataset_name,),
            ).fetchone()
            if schema is None:
                raise KeyError(f"No hay datos sincronizados para: {dataset_name}")
            rows = session.execute(
                """
                SELECT payload_json
                FROM netsuite_sync_records
                WHERE dataset_name = ? AND is_active = 1
                ORDER BY record_key
                """,
                (dataset_name,),
            ).fetchall()
        columns = json.loads(schema[0])
        return pd.DataFrame([json.loads(row[0]) for row in rows], columns=columns)

    def list_runs(self, limit: int = 50) -> list[SyncRun]:
        if not 1 <= int(limit) <= 500:
            raise ValueError("limit debe estar entre 1 y 500")
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT run_code, sync_type, status, started_at, finished_at,
                       executed_by, created_count, updated_count,
                       unchanged_count, deactivated_count, message
                FROM netsuite_sync_runs
                ORDER BY started_at DESC, run_code DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [SyncRun(*row) for row in rows]

    def load_dataset(self) -> NetSuiteDataset:
        """Expose the active synchronized snapshot through the provider contract."""
        return NetSuiteDataset(
            **{
                dataset_name: self.load_active_frame(dataset_name)
                for dataset_name in NetSuiteDataset.__dataclass_fields__
            }
        )


class SqliteNetSuiteSyncRepository(PersistentNetSuiteSyncRepository):
    """SQLite staging repository used for development and automated tests."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        super().__init__(Database.sqlite(self.database_path))


class PostgresNetSuiteSyncRepository(PersistentNetSuiteSyncRepository):
    """PostgreSQL operational repository used in production."""

    def __init__(self, dsn: str) -> None:
        super().__init__(Database.postgresql(dsn))


class NetSuiteSyncService:
    def __init__(
        self,
        provider: NetSuiteDataProvider,
        repository: NetSuiteSyncRepository,
        authorization: AuthorizationService,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.authorization = authorization

    def synchronize(
        self,
        actor_user_id: int,
        sync_type: str = "FULL",
        run_code: str | None = None,
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> SyncResult:
        self.authorization.require_permission(actor_user_id, PERM_NETSUITE_SYNC)
        normalized_type = sync_type.strip().upper()
        if normalized_type not in {"FULL", "INCREMENTAL"}:
            raise ValueError("sync_type debe ser FULL o INCREMENTAL")
        if normalized_type == "INCREMENTAL":
            if not window_start or not window_end:
                raise ValueError(
                    "INCREMENTAL requiere window_start y window_end"
                )
            if not isinstance(self.provider, IncrementalNetSuiteDataProvider):
                raise RuntimeError(
                    "El proveedor configurado no soporta sincronizacion incremental"
                )
        elif window_start or window_end:
            raise ValueError("FULL no acepta una ventana incremental")
        code = run_code or f"netsuite-{uuid4()}"
        self.repository.start_run(
            code,
            executed_by=str(actor_user_id),
            sync_type=normalized_type,
            window_start=window_start,
            window_end=window_end,
        )
        logger.info(
            "Sincronizacion de NetSuite iniciada",
            extra={
                "event": "netsuite_sync.started",
                "run_code": code,
                "status": SYNC_RUNNING,
                "actor_user_id": actor_user_id,
            },
        )
        metrics = SyncMetrics()
        try:
            dataset = (
                self.provider.load_dataset_modified_between(
                    window_start, window_end
                )
                if normalized_type == "INCREMENTAL"
                else self.provider.load_dataset()
            )
            quality = validate_netsuite_dataset(dataset)
            quality.require_valid()
            metrics = self.repository.save_snapshot(
                code,
                dataset,
                deactivate_missing=normalized_type == "FULL",
            )
            rows = {
                name: int(len(frame))
                for name, frame in _dataset_frames(dataset).items()
            }
            status = (
                SYNC_COMPLETED_WARNINGS if quality.warnings else SYNC_COMPLETED
            )
            message = quality.summary(quality.warnings) or None
            self.repository.finish_run(code, status, metrics, message)
            logger.info(
                "Sincronizacion de NetSuite completada",
                extra={
                    "event": "netsuite_sync.completed",
                    "run_code": code,
                    "status": status,
                    "actor_user_id": actor_user_id,
                },
            )
            return SyncResult(code, status, rows, metrics)
        except Exception as exc:
            message = safe_error_message(exc)
            self.repository.finish_run(code, SYNC_ERROR, metrics, message)
            logger.error(
                "Sincronizacion de NetSuite fallida: %s",
                message,
                extra={
                    "event": "netsuite_sync.failed",
                    "run_code": code,
                    "status": SYNC_ERROR,
                    "actor_user_id": actor_user_id,
                },
            )
            raise

    def list_runs(self, actor_user_id: int, limit: int = 50) -> list[SyncRun]:
        self.authorization.require_permission(actor_user_id, PERM_NETSUITE_SYNC)
        return self.repository.list_runs(limit)
