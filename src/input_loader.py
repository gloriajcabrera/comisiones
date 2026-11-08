from __future__ import annotations

from pathlib import Path

import pandas as pd

from .input_store import (
    INPUT_DEFINITIONS,
    get_active_input,
    list_input_statuses,
    load_dataframe_from_path,
)


def load_active_input_frame(input_type: str) -> pd.DataFrame:
    active = get_active_input(input_type)
    if active is None:
        raise FileNotFoundError(f"No hay una versión activa para: {input_type}")
    return load_dataframe_from_path(active["stored_path"])


def preview_active_input_frame(input_type: str, rows: int = 10) -> pd.DataFrame:
    return load_active_input_frame(input_type).head(rows)


def active_input_summary() -> list[dict[str, object]]:
    return list_input_statuses()


def missing_required_inputs() -> list[str]:
    missing: list[str] = []
    for key, definition in INPUT_DEFINITIONS.items():
        active = get_active_input(key)
        if active is None:
            missing.append(definition.label)
    return missing


def active_input_path(input_type: str) -> Path | None:
    active = get_active_input(input_type)
    if active is None:
        return None
    return Path(str(active["stored_path"]))
