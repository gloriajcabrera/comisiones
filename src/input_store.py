from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Iterable

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parent.parent
APP_DATA_DIR = PROJECT_DIR / "app_data"
INPUT_DATA_DIR = PROJECT_DIR / "input_data"
STATE_DB_PATH = APP_DATA_DIR / "review_state.db"

# Limita solo la cantidad mostrada en la interfaz. La historia no se elimina.
MAX_VERSIONS_PER_INPUT = 20


@dataclass(frozen=True)
class InputDefinition:
    key: str
    label: str
    description: str
    allowed_extensions: tuple[str, ...]
    required_columns: tuple[str, ...] = ()


INPUT_DEFINITIONS: dict[str, InputDefinition] = {
    "pisos": InputDefinition(
        key="pisos",
        label="Pisos y metas",
        description=(
            "No hace falta recargarlo en cada corrida. Solo actualízalo cuando cambien "
            "las metas, pisos o responsables."
        ),
        allowed_extensions=(".xlsx", ".xls", ".csv"),
    ),
    "periodos_corte": InputDefinition(
        key="periodos_corte",
        label="Periodos de corte",
        description=(
            "Tabla de periodos de corte. Normalmente se actualiza cuando cambia el año "
            "o se redefine la tabla oficial."
        ),
        allowed_extensions=(".xlsx", ".xls", ".csv"),
        required_columns=("inicio_corte", "fin_corte", "periodo_corte"),
    ),
}


def _sanitize_filename(name: str) -> str:
    cleaned = Path(name).name.strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or "archivo"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _file_hash(payload: bytes) -> str:
    return hashlib.sha1(payload).hexdigest()


def ensure_input_store() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_DATA_DIR.mkdir(parents=True, exist_ok=True)

    for definition in INPUT_DEFINITIONS.values():
        (INPUT_DATA_DIR / definition.key).mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(STATE_DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS input_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                input_type TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                stored_path TEXT NOT NULL UNIQUE,
                file_size INTEGER NOT NULL,
                file_hash TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                uploaded_by TEXT,
                note TEXT,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS process_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_code TEXT NOT NULL UNIQUE,
                periodo_corte TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                executed_by TEXT,
                status TEXT NOT NULL,
                message TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_inputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                input_version_id INTEGER NOT NULL,
                input_type TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES process_runs(id),
                FOREIGN KEY(input_version_id) REFERENCES input_versions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                output_name TEXT NOT NULL,
                stored_path TEXT,
                rows_count INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES process_runs(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL UNIQUE,
                published_at TEXT NOT NULL,
                published_by TEXT,
                is_current INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(run_id) REFERENCES process_runs(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_input_versions_type_uploaded_at "
            "ON input_versions(input_type, uploaded_at DESC, id DESC)"
        )
        conn.commit()


def _fetch_rows(query: str, params: Iterable[object] = ()) -> list[dict[str, object]]:
    ensure_input_store()
    with closing(sqlite3.connect(STATE_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def list_input_versions(input_type: str, limit: int = MAX_VERSIONS_PER_INPUT) -> list[dict[str, object]]:
    return _fetch_rows(
        """
        SELECT id, input_type, original_name, stored_name, stored_path, file_size,
               file_hash, uploaded_at, uploaded_by, note, is_active
        FROM input_versions
        WHERE input_type = ?
        ORDER BY uploaded_at DESC, id DESC
        LIMIT ?
        """,
        (input_type, limit),
    )


def get_active_input(input_type: str) -> dict[str, object] | None:
    rows = _fetch_rows(
        """
        SELECT id, input_type, original_name, stored_name, stored_path, file_size,
               file_hash, uploaded_at, uploaded_by, note, is_active
        FROM input_versions
        WHERE input_type = ? AND is_active = 1
        ORDER BY uploaded_at DESC, id DESC
        LIMIT 1
        """,
        (input_type,),
    )
    return rows[0] if rows else None


def list_input_statuses() -> list[dict[str, object]]:
    statuses: list[dict[str, object]] = []
    for definition in INPUT_DEFINITIONS.values():
        active = get_active_input(definition.key)
        statuses.append(
            {
                "key": definition.key,
                "label": definition.label,
                "description": definition.description,
                "required_columns": definition.required_columns,
                "active": active,
                "history": list_input_versions(definition.key),
            }
        )
    return statuses


def load_dataframe_from_bytes(file_name: str, payload: bytes) -> pd.DataFrame:
    suffix = Path(file_name).suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(pd.io.common.BytesIO(payload), low_memory=False)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(pd.io.common.BytesIO(payload))
    raise ValueError(f"Formato no soportado: {suffix or 'sin extensión'}")


def load_dataframe_from_path(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(file_path, low_memory=False)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(file_path)
    raise ValueError(f"Formato no soportado: {suffix or 'sin extensión'}")


def validate_input_dataframe(input_type: str, df: pd.DataFrame) -> tuple[bool, list[str]]:
    definition = INPUT_DEFINITIONS[input_type]
    issues: list[str] = []

    if df.empty:
        issues.append("El archivo no tiene filas.")

    if definition.required_columns:
        normalized = {str(col).strip().lower(): str(col) for col in df.columns}
        missing = [col for col in definition.required_columns if col.lower() not in normalized]
        if missing:
            issues.append(
                "Faltan columnas obligatorias: " + ", ".join(missing)
            )

    return (len(issues) == 0, issues)


def save_uploaded_input(
    input_type: str,
    file_name: str,
    payload: bytes,
    uploaded_by: str = "",
    note: str = "",
) -> dict[str, object]:
    if input_type not in INPUT_DEFINITIONS:
        raise KeyError(f"Tipo de insumo no soportado: {input_type}")

    ensure_input_store()

    definition = INPUT_DEFINITIONS[input_type]
    suffix = Path(file_name).suffix.lower()
    if suffix not in definition.allowed_extensions:
        allowed = ", ".join(definition.allowed_extensions)
        raise ValueError(
            f"Formato no permitido para {definition.label}. Usa uno de: {allowed}"
        )

    df = load_dataframe_from_bytes(file_name, payload)
    is_valid, issues = validate_input_dataframe(input_type, df)
    if not is_valid:
        raise ValueError(" | ".join(issues))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    sanitized_name = _sanitize_filename(file_name)
    stored_name = f"{timestamp}_{sanitized_name}"
    folder = INPUT_DATA_DIR / input_type
    stored_path = folder / stored_name
    stored_path.write_bytes(payload)

    hash_value = _file_hash(payload)
    uploaded_at = _now_iso()
    with closing(sqlite3.connect(STATE_DB_PATH)) as conn:
        conn.execute(
            "UPDATE input_versions SET is_active = 0 WHERE input_type = ?",
            (input_type,),
        )
        cursor = conn.execute(
            """
            INSERT INTO input_versions (
                input_type, original_name, stored_name, stored_path, file_size,
                file_hash, uploaded_at, uploaded_by, note, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                input_type,
                file_name,
                stored_name,
                str(stored_path),
                len(payload),
                hash_value,
                uploaded_at,
                uploaded_by.strip() or None,
                note.strip() or None,
            ),
        )
        new_id = int(cursor.lastrowid)

        conn.commit()

    saved = get_active_input(input_type)
    return {
        "id": new_id,
        "saved": saved,
        "issues": issues,
        "pruned": [],
    }
