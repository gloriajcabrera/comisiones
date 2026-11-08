from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

if os.getenv("COMMISSIONS_ENV", "development").strip().lower() == "production":
    raise RuntimeError(
        "local_review_app.py es una herramienta heredada y no puede ejecutarse en produccion"
    )

from src.input_loader import active_input_summary, missing_required_inputs
from src.input_store import (
    INPUT_DEFINITIONS,
    MAX_VERSIONS_PER_INPUT,
    STATE_DB_PATH,
    ensure_input_store,
    load_dataframe_from_bytes,
    load_dataframe_from_path,
    save_uploaded_input,
)


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "app_data"
DB_PATH = STATE_DB_PATH
MANIFEST_PATH = DATA_DIR / "manifest.json"

APPROVAL_OPTIONS = ["Pendiente", "Aprobado", "No aprobado"]
DATASET_SUFFIXES = [".parquet", ".csv", ".csv.gz", ".pkl", ".xlsx", ".xls"]
DATASET_ORDER = [
    "para_revision_reporte",
    "para_pagar_full_reporte",
    "resumen_pago",
    "para_pago_full",
    "cumpl_reporte",
    "cumplimiento_final",
    "detalle_reporte",
    "ventas_piso",
]
DATASET_LABELS = {
    "para_revision_reporte": "Para revisión",
    "para_pagar_full_reporte": "Para pagar",
    "resumen_pago": "Resumen para pago",
    "para_pago_full": "Base completa de pago",
    "cumpl_reporte": "Resumen de cumplimiento",
    "cumplimiento_final": "Cumplimiento final",
    "detalle_reporte": "Detalle de facturas",
    "ventas_piso": "Base de pisos",
}
FILTER_LABELS = {
    "Ejecutivo": "Ejecutivo",
    "entityid": "Ejecutivo (ID original)",
    "Periodo": "Periodo",
    "periodo_mes": "Periodo",
    "Periodo Factura": "Periodo factura",
    "Periodo Corte": "Periodo corte",
    "Factura": "Factura",
    "tranId": "Factura / tranId",
    "Lote": "Lote",
    "lote": "Lote",
    "Linea Comision": "Linea comision",
    "linea_comision": "Linea comision",
    "Decision Pago": "Decision pago",
    "decision_pago": "Decision pago",
    "Representante de ventas": "Representante de ventas",
    "Estado Piso": "Estado piso",
    "estado_piso": "Estado piso",
    "Cumple": "Cumple",
    "estado_revision": "Estado revision app",
}


def ensure_state_db() -> None:
    ensure_input_store()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                dataset_name TEXT NOT NULL,
                row_key TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                updated_at TEXT NOT NULL,
                updated_by TEXT,
                PRIMARY KEY (dataset_name, row_key)
            )
            """
        )

        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(approvals)").fetchall()
        }
        if "updated_by" not in cols:
            conn.execute(
                "ALTER TABLE approvals ADD COLUMN updated_by TEXT"
            )

        conn.commit()


def load_approvals() -> pd.DataFrame:
    ensure_state_db()
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """
            SELECT dataset_name, row_key, decision, reason, updated_at, updated_by
            FROM approvals
            """,
            conn,
        )
    if df.empty:
        return pd.DataFrame(
            columns=[
                "dataset_name",
                "row_key",
                "decision",
                "reason",
                "updated_at",
                "updated_by",
            ]
        )
    return df


def save_approval(
    dataset_name: str,
    row_key: str,
    decision: str,
    reason: str,
    updated_by: str,
) -> None:
    ensure_state_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO approvals (
                dataset_name, row_key, decision, reason, updated_at, updated_by
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(dataset_name, row_key)
            DO UPDATE SET
                decision=excluded.decision,
                reason=excluded.reason,
                updated_at=excluded.updated_at,
                updated_by=excluded.updated_by
            """,
            (
                dataset_name,
                row_key,
                decision,
                reason,
                datetime.now().isoformat(timespec="seconds"),
                updated_by.strip() or None,
            ),
        )
        conn.commit()


def infer_dataset_kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".csv.gz"):
        return "csv"
    for suffix in DATASET_SUFFIXES:
        if name.endswith(suffix):
            return suffix.lstrip(".")
    raise ValueError(f"Formato no soportado: {path.name}")


def find_alternative_dataset(path: Path) -> Path | None:
    name = path.name.lower()
    for suffix in DATASET_SUFFIXES:
        if name.endswith(suffix):
            stem = path.name[: -len(suffix)]
            break
    else:
        stem = path.stem

    for suffix in DATASET_SUFFIXES:
        candidate = path.with_name(f"{stem}{suffix}")
        if candidate.exists():
            return candidate
    return None


def read_frame(path: Path) -> pd.DataFrame:
    kind = infer_dataset_kind(path)
    try:
        if kind == "pkl":
            return pd.read_pickle(path)
        if kind == "parquet":
            return pd.read_parquet(path)
        if kind == "csv":
            return pd.read_csv(path, low_memory=False)
        if kind in {"xlsx", "xls"}:
            return pd.read_excel(path)
    except NotImplementedError as exc:
        alt_path = find_alternative_dataset(path)
        extra_hint = ""
        if alt_path is not None and alt_path != path:
            extra_hint = f" Hay un archivo alterno disponible: `{alt_path.name}`."
        raise RuntimeError(
            "No pude leer este dataset porque fue guardado en un formato pickle "
            "incompatible con la version actual de pandas. "
            "Vuelve a generar el bundle con `save_dataframe_bundle(...)` "
            "para que quede en parquet o csv."
            f"{extra_hint}"
        ) from exc

    raise ValueError(f"Formato no soportado: {path.name}")


def available_datasets() -> dict[str, Path]:
    datasets: dict[str, Path] = {}

    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for item in manifest.get("datasets", []):
            rel_path = item.get("path")
            name = item.get("name")
            if rel_path and name:
                path = DATA_DIR / rel_path
                alt_path = find_alternative_dataset(path)
                if alt_path is not None:
                    datasets[name] = alt_path
                elif path.exists():
                    datasets[name] = path

    if datasets:
        return datasets

    for name in [
        "detalle_reporte",
        "cumpl_reporte",
        "para_pagar_full_reporte",
        "resumen_pago",
        "para_revision_reporte",
        "cumplimiento_final",
        "para_pago_full",
        "ventas_piso",
    ]:
        for suffix in DATASET_SUFFIXES:
            path = DATA_DIR / f"{name}{suffix}"
            if path.exists():
                datasets[name] = path
                break

    return datasets


def ordered_dataset_names(datasets: dict[str, Path]) -> list[str]:
    known = [name for name in DATASET_ORDER if name in datasets]
    extra = sorted([name for name in datasets if name not in DATASET_ORDER])
    return known + extra


def dataset_display_name(name: str) -> str:
    return DATASET_LABELS.get(name, name.replace("_", " ").strip().title())


def build_row_key(row: pd.Series, dataset_name: str) -> str:
    preferred_cols = [
        "Factura",
        "tranId",
        "Documento Base Comision",
        "Lote",
        "lote",
        "Servicio",
        "Periodo",
        "periodo_mes",
        "Periodo Factura",
        "Ejecutivo",
        "entityid",
        "Linea Comision",
        "linea_comision",
    ]
    payload = {"dataset": dataset_name}
    for col in preferred_cols:
        if col in row.index:
            payload[col] = None if pd.isna(row[col]) else str(row[col]).strip()

    if len(payload) == 1:
        payload["row"] = {
            str(k): (None if pd.isna(v) else str(v)) for k, v in row.to_dict().items()
        }

    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return digest


def build_row_label(row: pd.Series) -> str:
    parts: list[str] = []
    for col in [
        "Factura",
        "tranId",
        "Lote",
        "lote",
        "Servicio",
        "Ejecutivo",
        "entityid",
        "Linea Comision",
        "linea_comision",
        "Periodo",
        "periodo_mes",
    ]:
        if col in row.index and pd.notna(row[col]):
            value = str(row[col]).strip()
            if value:
                parts.append(f"{col}: {value}")
    return " | ".join(parts[:6]) if parts else "Fila sin identificadores"


def attach_reviews(df: pd.DataFrame, dataset_name: str, approvals: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["__row_key"] = out.apply(lambda row: build_row_key(row, dataset_name), axis=1)
    out["__row_label"] = out.apply(build_row_label, axis=1)

    if approvals.empty:
        out["estado_revision"] = "Pendiente"
        out["motivo_revision"] = ""
        out["fecha_revision"] = ""
        out["usuario_revision"] = ""
        return out

    app_df = approvals[approvals["dataset_name"].eq(dataset_name)].copy()
    app_df = app_df.rename(
        columns={
            "row_key": "__row_key",
            "decision": "estado_revision",
            "reason": "motivo_revision",
            "updated_at": "fecha_revision",
            "updated_by": "usuario_revision",
        }
    )

    out = out.merge(
        app_df[
            [
                "__row_key",
                "estado_revision",
                "motivo_revision",
                "fecha_revision",
                "usuario_revision",
            ]
        ],
        on="__row_key",
        how="left",
        validate="m:1",
    )

    out["estado_revision"] = out["estado_revision"].fillna("Pendiente")
    out["motivo_revision"] = out["motivo_revision"].fillna("")
    out["fecha_revision"] = out["fecha_revision"].fillna("")
    out["usuario_revision"] = out["usuario_revision"].fillna("")
    return out


def candidate_filter_columns(df: pd.DataFrame) -> list[str]:
    candidates = [
        "Ejecutivo",
        "Representante de ventas",
        "Periodo Corte",
        "Periodo Factura",
        "Periodo",
        "periodo_mes",
        "Decision Pago",
        "decision_pago",
        "entityid",
        "Factura",
        "tranId",
        "Lote",
        "lote",
        "Linea Comision",
        "linea_comision",
        "Estado Piso",
        "Cumple",
        "estado_piso",
        "estado_revision",
    ]
    return [col for col in candidates if col in df.columns]


def _render_multiselect_filter(df: pd.DataFrame, col: str) -> pd.DataFrame:
    values = (
        df[col]
        .dropna()
        .astype(str)
        .sort_values()
        .unique()
        .tolist()
    )
    if not values or len(values) > 200:
        return df

    label = FILTER_LABELS.get(col, col)
    selected = st.sidebar.multiselect(label, values, default=[], key=f"filter_{col}")
    if selected:
        return df[df[col].astype(str).isin(selected)].copy()
    return df


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    filtered = df.copy()
    filter_cols = candidate_filter_columns(df)
    primary_cols = [
        col
        for col in [
            "Ejecutivo",
            "Representante de ventas",
            "Periodo Corte",
            "Periodo Factura",
            "Periodo",
            "periodo_mes",
            "Decision Pago",
            "decision_pago",
        ]
        if col in filter_cols
    ]
    secondary_cols = [col for col in filter_cols if col not in primary_cols]

    st.sidebar.subheader("Filtros principales")
    for col in primary_cols:
        filtered = _render_multiselect_filter(filtered, col)

    if secondary_cols:
        with st.sidebar.expander("Mas filtros", expanded=False):
            for col in secondary_cols:
                values = (
                    filtered[col]
                    .dropna()
                    .astype(str)
                    .sort_values()
                    .unique()
                    .tolist()
                )
                if not values or len(values) > 200:
                    continue
                label = FILTER_LABELS.get(col, col)
                selected = st.multiselect(label, values, default=[], key=f"filter_{col}")
                if selected:
                    filtered = filtered[filtered[col].astype(str).isin(selected)].copy()

    text_filter = st.sidebar.text_input("Buscar texto libre")
    if text_filter:
        pattern = text_filter.strip().lower()
        mask = filtered.astype(str).apply(
            lambda s: s.str.lower().str.contains(pattern, na=False)
        ).any(axis=1)
        filtered = filtered[mask].copy()

    return filtered


def strip_technical_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop(columns=["__row_key", "__row_label"], errors="ignore")


def looks_like_currency_column(col_name: str) -> bool:
    col = str(col_name).strip().lower()
    positive_keywords = [
        "valor",
        "comision",
        "comisión",
        "descuento",
        "pagar",
        "meta",
        "piso",
        "saldo",
        "costo",
        "venta",
        "total",
    ]
    negative_keywords = [
        "porcentaje",
        "factor",
        "dia",
        "días",
        "dias",
        "periodo",
        "id",
        "lote",
    ]
    return any(token in col for token in positive_keywords) and not any(
        token in col for token in negative_keywords
    )


def format_cop(value: object) -> str:
    if pd.isna(value):
        return ""
    try:
        amount = round(float(value))
    except (TypeError, ValueError):
        return str(value)

    sign = "-" if amount < 0 else ""
    amount_abs = abs(int(amount))
    formatted = f"{amount_abs:,}".replace(",", ".")
    return f"{sign}$ {formatted}"


def format_zero_decimals(value: object) -> str:
    if pd.isna(value):
        return ""
    try:
        amount = round(float(value))
    except (TypeError, ValueError):
        return str(value)
    return f"{int(amount):,}".replace(",", ".")


def format_dataframe_for_display(
    df: pd.DataFrame,
    dataset_name: str | None = None,
) -> pd.io.formats.style.Styler:
    visible_df = strip_technical_columns(df).copy()
    formatters: dict[str, object] = {}

    for col in visible_df.columns:
        if not pd.api.types.is_numeric_dtype(visible_df[col]):
            continue
        if dataset_name == "resumen_pago" and col != "Representante de ventas":
            formatters[col] = format_cop
        elif looks_like_currency_column(col):
            formatters[col] = format_cop
        else:
            formatters[col] = format_zero_decimals

    return visible_df.style.format(formatters, na_rep="")


def format_file_size(size: int | float | None) -> str:
    if not size:
        return "0 B"
    num = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def render_input_section(definition_key: str, user_name: str) -> None:
    definition = INPUT_DEFINITIONS[definition_key]
    status = next(
        item for item in active_input_summary() if item["key"] == definition_key
    )
    active = status["active"]
    history = status["history"]

    st.subheader(definition.label)
    st.caption(definition.description)

    if active:
        cols = st.columns(4)
        cols[0].metric("Versión activa", active["original_name"])
        cols[1].metric("Cargado", str(active["uploaded_at"]))
        cols[2].metric("Usuario", str(active.get("uploaded_by") or "No informado"))
        cols[3].metric("Tamaño", format_file_size(active.get("file_size")))
    else:
        st.warning(
            "No hay una versión vigente todavía. Este archivo deberá cargarse antes "
            "de usar la ejecución automatizada."
        )

    uploader_label = f"Cargar o actualizar {definition.label.lower()}"
    uploaded_file = st.file_uploader(
        uploader_label,
        type=[ext.lstrip(".") for ext in definition.allowed_extensions],
        key=f"uploader_{definition.key}",
    )
    note = st.text_input(
        "Nota opcional de la carga",
        value="",
        key=f"note_{definition.key}",
        placeholder="Ejemplo: corte mayo 2026 o ajuste anual",
    )

    if uploaded_file is not None:
        payload = uploaded_file.getvalue()
        try:
            preview_df = load_dataframe_from_bytes(uploaded_file.name, payload)
        except Exception as exc:
            st.error(f"No pude leer el archivo cargado: {exc}")
        else:
            st.caption(
                f"Vista previa: {preview_df.shape[0]} filas x {preview_df.shape[1]} columnas"
            )
            st.dataframe(preview_df.head(10), use_container_width=True, height=220)
            st.caption("Columnas detectadas: " + ", ".join(map(str, preview_df.columns)))

            if st.button(
                f"Guardar nueva versión de {definition.label}",
                key=f"save_{definition.key}",
                type="primary",
            ):
                try:
                    result = save_uploaded_input(
                        definition.key,
                        uploaded_file.name,
                        payload,
                        uploaded_by=user_name,
                        note=note,
                    )
                except Exception as exc:
                    st.error(f"No pude guardar el archivo: {exc}")
                else:
                    st.success("Archivo guardado y marcado como versión vigente.")
                    st.caption(
                        "Se muestran hasta "
                        f"{MAX_VERSIONS_PER_INPUT} versiones recientes; la historia completa se conserva."
                    )
                    st.rerun()

    if active:
        with st.expander("Vista rápida del archivo vigente", expanded=False):
            try:
                active_df = load_dataframe_from_path(active["stored_path"])
            except Exception as exc:
                st.error(f"No pude leer la versión activa: {exc}")
            else:
                st.dataframe(active_df.head(10), use_container_width=True, height=220)
                st.caption(
                    f"Columnas: {', '.join(map(str, active_df.columns))}"
                )

    with st.expander("Histórico guardado", expanded=False):
        if not history:
            st.info("Todavía no hay versiones guardadas.")
        else:
            hist_df = pd.DataFrame(history)
            hist_df = hist_df.rename(
                columns={
                    "original_name": "Archivo original",
                    "uploaded_at": "Fecha carga",
                    "uploaded_by": "Usuario",
                    "file_size": "Tamaño",
                    "is_active": "Activa",
                    "note": "Nota",
                }
            )
            if "Tamaño" in hist_df.columns:
                hist_df["Tamaño"] = hist_df["Tamaño"].map(format_file_size)
            if "Activa" in hist_df.columns:
                hist_df["Activa"] = hist_df["Activa"].map(lambda x: "Sí" if int(x) == 1 else "No")
            keep_cols = [
                col
                for col in [
                    "Archivo original",
                    "Fecha carga",
                    "Usuario",
                    "Tamaño",
                    "Activa",
                    "Nota",
                ]
                if col in hist_df.columns
            ]
            st.dataframe(hist_df[keep_cols], use_container_width=True, hide_index=True)


def render_inputs_page(user_name: str) -> None:
    st.subheader("Insumos")
    st.caption(
        "Aquí administras las versiones vigentes de pisos y periodos de corte. "
        "La historia completa se conserva y la pantalla muestra las versiones "
        "más recientes."
    )

    missing = missing_required_inputs()
    if missing:
        st.warning(
            "Faltan insumos vigentes para dejar lista la futura ejecución automática: "
            + ", ".join(missing)
        )
    else:
        st.success("Ya existe una versión activa para todos los insumos configurados.")

    for key in INPUT_DEFINITIONS:
        render_input_section(key, user_name)
        st.divider()


def render_editor(dataset_name: str, filtered_df: pd.DataFrame, user_name: str) -> None:
    st.subheader("Revisión")

    if filtered_df.empty:
        st.info("No hay filas para revisar con los filtros actuales.")
        return

    options = filtered_df["__row_key"].tolist()
    labels = dict(zip(filtered_df["__row_key"], filtered_df["__row_label"]))
    selected_key = st.selectbox(
        "Fila a revisar",
        options,
        format_func=lambda key: labels.get(key, key),
    )

    row = filtered_df[filtered_df["__row_key"].eq(selected_key)].iloc[0]

    st.caption("Detalle de la fila seleccionada")
    st.json(
        {
            k: (None if pd.isna(v) else str(v))
            for k, v in row.drop(labels=["__row_key", "__row_label"], errors="ignore").to_dict().items()
        }
    )

    current_decision = row.get("estado_revision", "Pendiente")
    default_index = (
        APPROVAL_OPTIONS.index(current_decision)
        if current_decision in APPROVAL_OPTIONS
        else 0
    )

    decision = st.radio(
        "Decisión",
        APPROVAL_OPTIONS,
        index=default_index,
        horizontal=True,
    )

    reason = st.text_area(
        "Motivo / observación",
        value=str(row.get("motivo_revision", "") or ""),
        height=120,
        placeholder="Describe por qué no apruebas o qué hay que depurar después.",
    )

    if st.button("Guardar revisión", type="primary"):
        if decision == "No aprobado" and not reason.strip():
            st.error("Si la decisión es 'No aprobado', debes indicar el motivo.")
        else:
            save_approval(dataset_name, selected_key, decision, reason.strip(), user_name)
            st.success("Revisión guardada.")
            st.rerun()


def render_review_page(user_name: str) -> None:
    datasets = available_datasets()
    if not datasets:
        st.warning(
            "No encontré dataframes guardados en `app_data/`.\n\n"
            "Guarda primero un bundle desde el notebook y vuelve a abrir la app."
        )
        st.code(
            "from review_bundle import save_dataframe_bundle\n"
            "save_dataframe_bundle({\n"
            "    'detalle_reporte': detalle_reporte,\n"
            "    'cumpl_reporte': cumpl_reporte,\n"
            "    'para_pagar_full_reporte': para_pagar_full_reporte,\n"
            "    'resumen_pago': resumen_pago,\n"
            "    'para_revision_reporte': para_revision_reporte,\n"
            "}, out_dir='app_data')",
            language="python",
        )
        return

    approvals = load_approvals()

    dataset_names = ordered_dataset_names(datasets)
    if "resumen_pago" in dataset_names:
        default_dataset = "resumen_pago"
    elif "para_revision_reporte" in dataset_names:
        default_dataset = "para_revision_reporte"
    else:
        default_dataset = dataset_names[0]
    default_index = dataset_names.index(default_dataset)

    dataset_name = st.sidebar.selectbox(
        "Conjunto de datos",
        dataset_names,
        index=default_index,
        format_func=dataset_display_name,
    )
    source_path = datasets[dataset_name]
    st.sidebar.caption(f"Vista: {dataset_display_name(dataset_name)}")
    st.sidebar.caption(f"Fuente: {source_path.name}")

    try:
        df = read_frame(source_path)
    except Exception as exc:
        st.error(
            "No pude cargar este dataset. "
            "Lo mas probable es que el bundle actual haya quedado en un pickle incompatible."
        )
        st.code(
            "from review_bundle import save_dataframe_bundle\n"
            "save_dataframe_bundle({\n"
            "    'detalle_reporte': detalle_reporte,\n"
            "    'cumpl_reporte': cumpl_reporte,\n"
            "    'para_pagar_full_reporte': para_pagar_full_reporte,\n"
            "    'resumen_pago': resumen_pago,\n"
            "    'para_revision_reporte': para_revision_reporte,\n"
            "    'cumplimiento_final': cumplimiento_final,\n"
            "    'para_pago_full': para_pago_full,\n"
            "}, out_dir='app_data')",
            language="python",
        )
        st.exception(exc)
        st.stop()

    df = attach_reviews(df, dataset_name, approvals)
    filtered_df = apply_filters(df)

    metric_cols = st.columns(4)
    metric_cols[0].metric("Filas totales", len(df))
    metric_cols[1].metric("Filas filtradas", len(filtered_df))
    metric_cols[2].metric(
        "Aprobadas",
        int(filtered_df["estado_revision"].astype(str).eq("Aprobado").sum()),
    )
    metric_cols[3].metric(
        "No aprobadas",
        int(filtered_df["estado_revision"].astype(str).eq("No aprobado").sum()),
    )

    st.subheader("Datos")

    pagination_cols = st.columns([1, 1, 3])
    page_size = pagination_cols[0].selectbox(
        "Filas por página",
        options=[100, 250, 500],
        index=1,
        key=f"page_size_{dataset_name}",
    )
    total_pages = max(1, (len(filtered_df) + page_size - 1) // page_size)
    page_number = pagination_cols[1].number_input(
        "Página",
        min_value=1,
        max_value=total_pages,
        value=1,
        step=1,
        key=f"page_number_{dataset_name}",
    )

    page_start = (int(page_number) - 1) * page_size
    page_end = min(page_start + page_size, len(filtered_df))
    page_df = filtered_df.iloc[page_start:page_end].copy()

    pagination_cols[2].caption(
        f"Mostrando filas {page_start + 1:,} a {page_end:,} "
        f"de {len(filtered_df):,}. Página {int(page_number):,} de {total_pages:,}."
    )

    st.dataframe(
        format_dataframe_for_display(page_df, dataset_name=dataset_name),
        width="stretch",
        height=520,
    )

    summary = (
        df["estado_revision"]
        .astype(str)
        .value_counts(dropna=False)
        .rename_axis("estado_revision")
        .reset_index(name="filas")
    )
    st.subheader("Resumen de revisión")
    st.dataframe(
        summary.style.format({"filas": format_zero_decimals}, na_rep=""),
        width="stretch",
        hide_index=True,
    )

    render_editor(dataset_name, page_df, user_name)


def main() -> None:
    st.set_page_config(page_title="Revisión de comisiones", layout="wide")
    st.title("Revisión local de comisiones")
    st.caption(
        "Administra insumos, revisa dataframes y registra aprobaciones sin depender de Excel."
    )

    ensure_state_db()

    user_name = st.sidebar.text_input("Usuario", value="gloria")
    section = st.sidebar.radio("Sección", ["Insumos", "Revisión"], index=1)

    if section == "Insumos":
        render_inputs_page(user_name)
    else:
        render_review_page(user_name)


if __name__ == "__main__":
    main()
