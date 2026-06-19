from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "app_data"
DB_PATH = DATA_DIR / "review_state.db"
MANIFEST_PATH = DATA_DIR / "manifest.json"

APPROVAL_OPTIONS = ["Pendiente", "Aprobado", "No aprobado"]
DATASET_SUFFIXES = [".parquet", ".csv", ".csv.gz", ".pkl", ".xlsx", ".xls"]
DATASET_ORDER = [
    "para_revision_reporte",
    "para_pagar_full_reporte",
    "para_pago_full",
    "cumpl_reporte",
    "cumplimiento_final",
    "detalle_reporte",
    "ventas_piso",
]
DATASET_LABELS = {
    "para_revision_reporte": "Para revisión",
    "para_pagar_full_reporte": "Para pagar",
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
    "Estado Piso": "Estado piso",
    "estado_piso": "Estado piso",
    "Cumple": "Cumple",
    "estado_revision": "Estado revision app",
}


def ensure_state_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                dataset_name TEXT NOT NULL,
                row_key TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (dataset_name, row_key)
            )
            """
        )
        conn.commit()


def load_approvals() -> pd.DataFrame:
    ensure_state_db()
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """
            SELECT dataset_name, row_key, decision, reason, updated_at
            FROM approvals
            """,
            conn,
        )
    if df.empty:
        return pd.DataFrame(
            columns=["dataset_name", "row_key", "decision", "reason", "updated_at"]
        )
    return df


def save_approval(dataset_name: str, row_key: str, decision: str, reason: str) -> None:
    ensure_state_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO approvals (dataset_name, row_key, decision, reason, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(dataset_name, row_key)
            DO UPDATE SET
                decision=excluded.decision,
                reason=excluded.reason,
                updated_at=excluded.updated_at
            """,
            (dataset_name, row_key, decision, reason, datetime.now().isoformat(timespec="seconds")),
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
        return out

    app_df = approvals[approvals["dataset_name"].eq(dataset_name)].copy()
    app_df = app_df.rename(
        columns={
            "row_key": "__row_key",
            "decision": "estado_revision",
            "reason": "motivo_revision",
            "updated_at": "fecha_revision",
        }
    )

    out = out.merge(
        app_df[["__row_key", "estado_revision", "motivo_revision", "fecha_revision"]],
        on="__row_key",
        how="left",
        validate="m:1",
    )

    out["estado_revision"] = out["estado_revision"].fillna("Pendiente")
    out["motivo_revision"] = out["motivo_revision"].fillna("")
    out["fecha_revision"] = out["fecha_revision"].fillna("")
    return out


def candidate_filter_columns(df: pd.DataFrame) -> list[str]:
    candidates = [
        "Ejecutivo",
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
    primary_cols = [col for col in ["Ejecutivo", "Periodo Corte", "Periodo Factura", "Periodo", "periodo_mes", "Decision Pago", "decision_pago"] if col in filter_cols]
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


def render_editor(dataset_name: str, filtered_df: pd.DataFrame) -> None:
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
            for k, v in row.drop(labels=["__row_key"]).to_dict().items()
        }
    )

    current_decision = row.get("estado_revision", "Pendiente")
    default_index = APPROVAL_OPTIONS.index(current_decision) if current_decision in APPROVAL_OPTIONS else 0

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
            save_approval(dataset_name, selected_key, decision, reason.strip())
            st.success("Revisión guardada.")
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="Revisión de comisiones", layout="wide")
    st.title("Revisión local de comisiones")
    st.caption("Visualiza dataframes, filtra y registra aprobaciones sin depender de Excel.")

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
            "    'para_revision_reporte': para_revision_reporte,\n"
            "}, out_dir='app_data')",
            language="python",
        )
        return

    approvals = load_approvals()

    dataset_names = ordered_dataset_names(datasets)
    default_dataset = "para_revision_reporte" if "para_revision_reporte" in dataset_names else dataset_names[0]
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
    st.dataframe(filtered_df, use_container_width=True, height=520)

    summary = (
        df["estado_revision"]
        .astype(str)
        .value_counts(dropna=False)
        .rename_axis("estado_revision")
        .reset_index(name="filas")
    )
    st.subheader("Resumen de revisión")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    render_editor(dataset_name, filtered_df)


if __name__ == "__main__":
    main()
