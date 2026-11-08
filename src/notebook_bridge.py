from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from .input_store import get_active_input, load_dataframe_from_path


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotebookInputSpec:
    scope_name: str
    label: str
    input_type: str | None = None
    required: bool = True


BASE_INPUT_SPECS: tuple[NotebookInputSpec, ...] = (
    NotebookInputSpec("f", "Facturas", required=True),
    NotebookInputSpec("p", "Proformas", required=True),
    NotebookInputSpec("nc", "Notas crédito", required=True),
    NotebookInputSpec("ncp", "NC / diarios de anulación de proformas", required=True),
    NotebookInputSpec("pisos", "Pisos y metas", input_type="pisos", required=True),
)


def _copy_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.copy()


def _describe_source(source: dict[str, Any]) -> str:
    if source["kind"] == "active_input":
        return f"input activo ({source['input_type']}: {source['file_name']})"
    if source["kind"] == "memory":
        return f"memoria ({source['scope_name']})"
    return source["kind"]


def _resolve_scope_dataframe(
    scope: dict[str, Any],
    spec: NotebookInputSpec,
    prefer_active: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    manual_df = scope.get(spec.scope_name)
    active_meta = get_active_input(spec.input_type) if (prefer_active and spec.input_type) else None

    if active_meta is not None:
        df = load_dataframe_from_path(active_meta["stored_path"])
        return df, {
            "kind": "active_input",
            "input_type": spec.input_type,
            "file_name": active_meta["original_name"],
            "stored_path": active_meta["stored_path"],
            "scope_name": spec.scope_name,
        }

    if isinstance(manual_df, pd.DataFrame):
        return manual_df, {
            "kind": "memory",
            "scope_name": spec.scope_name,
        }

    if spec.required:
        if spec.input_type:
            raise NameError(
                f"No encontré '{spec.scope_name}' en memoria ni una versión activa para "
                f"'{spec.input_type}'. Carga el archivo desde la app o crea el dataframe manual."
            )
        raise NameError(
            f"Falta el dataframe '{spec.scope_name}' en memoria para '{spec.label}'."
        )

    return pd.DataFrame(), {"kind": "missing", "scope_name": spec.scope_name}


def _normalize_periodos_corte(df: pd.DataFrame) -> pd.DataFrame:
    rename_map: dict[str, str] = {}
    normalized_cols = {str(col).strip().lower(): str(col) for col in df.columns}
    for required_col in ["inicio_corte", "fin_corte", "periodo_corte"]:
        source_col = normalized_cols.get(required_col)
        if source_col is None:
            raise KeyError(
                "La tabla de periodos de corte debe incluir las columnas "
                "'inicio_corte', 'fin_corte' y 'periodo_corte'."
            )
        rename_map[source_col] = required_col

    out = df.rename(columns=rename_map).copy()
    out = out[["inicio_corte", "fin_corte", "periodo_corte"]]
    out["inicio_corte"] = pd.to_datetime(out["inicio_corte"], errors="coerce")
    out["fin_corte"] = pd.to_datetime(out["fin_corte"], errors="coerce")
    out["periodo_corte"] = out["periodo_corte"].astype("string").str.strip()

    if out["inicio_corte"].isna().any() or out["fin_corte"].isna().any():
        raise ValueError("La tabla de periodos de corte tiene fechas inválidas.")

    invalid_rows = out["inicio_corte"] > out["fin_corte"]
    if invalid_rows.any():
        raise ValueError(
            "La tabla de periodos de corte tiene filas donde inicio_corte es mayor que fin_corte."
        )

    return out.sort_values(["inicio_corte", "fin_corte"]).reset_index(drop=True)


def load_periodos_corte_table(
    scope: dict[str, Any],
    prefer_active: bool = True,
    write_to_scope: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    manual_candidates = [
        scope.get("periodos_corte"),
        scope.get("periodos_corte_df"),
        scope.get("cortes"),
    ]
    manual_df = next((item for item in manual_candidates if isinstance(item, pd.DataFrame)), None)

    active_meta = get_active_input("periodos_corte") if prefer_active else None
    if active_meta is not None:
        cortes = _normalize_periodos_corte(
            load_dataframe_from_path(active_meta["stored_path"])
        )
        source = f"input activo (periodos_corte: {active_meta['original_name']})"
    elif manual_df is not None:
        cortes = _normalize_periodos_corte(manual_df)
        source = "memoria (cortes)"
    else:
        raise NameError(
            "No encontré periodos de corte en memoria ni una versión activa cargada "
            "desde la app local."
        )

    if write_to_scope:
        scope["cortes"] = cortes.copy()
        scope["periodos_corte_df"] = cortes.copy()

    if verbose:
        logger.info(
            "Periodos de corte resueltos desde %s: %s filas x %s columnas",
            source,
            cortes.shape[0],
            cortes.shape[1],
            extra={
                "event": "notebook_bridge.cut_periods_resolved",
                "rows": int(cortes.shape[0]),
                "columns": int(cortes.shape[1]),
            },
        )

    return cortes.copy()


def hydrate_notebook_inputs(
    scope: dict[str, Any],
    prefer_active: bool = True,
    write_to_scope: bool = True,
    verbose: bool = True,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    resolved: dict[str, pd.DataFrame] = {}
    summary_rows: list[dict[str, Any]] = []

    for spec in BASE_INPUT_SPECS:
        df, source_meta = _resolve_scope_dataframe(scope, spec, prefer_active=prefer_active)
        resolved[spec.scope_name] = _copy_df(df)
        summary_rows.append(
            {
                "scope_name": spec.scope_name,
                "label": spec.label,
                "source": _describe_source(source_meta),
                "rows": int(df.shape[0]),
                "cols": int(df.shape[1]),
            }
        )

    cortes_df = load_periodos_corte_table(
        scope,
        prefer_active=prefer_active,
        write_to_scope=write_to_scope,
        verbose=False,
    )
    resolved["cortes"] = cortes_df.copy()
    summary_rows.append(
        {
            "scope_name": "cortes",
            "label": "Periodos de corte",
            "source": (
                "input activo (periodos_corte)"
                if get_active_input("periodos_corte") is not None and prefer_active
                else "memoria (cortes)"
            ),
            "rows": int(cortes_df.shape[0]),
            "cols": int(cortes_df.shape[1]),
        }
    )

    if write_to_scope:
        for key, df in resolved.items():
            scope[key] = _copy_df(df)

    summary_df = pd.DataFrame(summary_rows)

    if verbose:
        for row in summary_rows:
            logger.info(
                "Fuente del notebook resuelta: %s, %s, %s filas x %s columnas",
                row["label"],
                row["source"],
                row["rows"],
                row["cols"],
                extra={
                    "event": "notebook_bridge.source_resolved",
                    "scope_name": row["scope_name"],
                    "rows": row["rows"],
                    "columns": row["cols"],
                },
            )

    return resolved, summary_df
