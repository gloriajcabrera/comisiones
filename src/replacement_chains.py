from __future__ import annotations

import pandas as pd

from src.normalization import normalize_boolean, normalize_date, normalize_text, normalize_upper


def resolve_replacement_chains(sales: pd.DataFrame) -> pd.DataFrame:
    """Resolve commission root and payment source for every replacement chain."""
    required = (
        "tranId",
        "tranDate",
        "dueDate",
        "periodo_factura",
        "documento_origen",
        "reemplazo",
        "tranId_nc",
        "factura_reemplazo_tranId_nc",
    )
    _require_columns(sales, required, "sales")
    result = sales.copy()
    result["tranId"] = normalize_text(result["tranId"])
    result["tranId_key"] = normalize_upper(result["tranId"])
    result["tranDate"] = normalize_date(result["tranDate"])
    result["dueDate"] = normalize_date(result["dueDate"]).combine_first(
        result["tranDate"]
    )
    result["documento_origen"] = normalize_upper(result["documento_origen"])
    result["periodo_mes_documento"] = _to_month_period(result["periodo_factura"])

    relationships = (
        result.loc[
            normalize_boolean(result["reemplazo"])
            & result["documento_origen"].notna(),
            ["tranId_key", "documento_origen"],
        ]
        .dropna()
        .drop_duplicates()
    )
    conflicting = relationships.groupby("tranId_key")["documento_origen"].nunique()
    conflicting = conflicting.loc[conflicting.gt(1)]
    if not conflicting.empty:
        raise ValueError(
            "Hay documentos de reemplazo asociados a más de un origen: "
            + ", ".join(conflicting.index.astype(str).tolist()[:20])
        )
    parent_map = (
        relationships.drop_duplicates("tranId_key")
        .set_index("tranId_key")["documento_origen"]
        .to_dict()
    )
    document_dates = (
        result[["tranId_key", "tranDate"]]
        .dropna(subset=["tranId_key"])
        .sort_values("tranDate", na_position="last")
        .drop_duplicates("tranId_key", keep="first")
        .set_index("tranId_key")["tranDate"]
        .to_dict()
    )

    resolved = pd.DataFrame(
        result["tranId_key"]
        .map(lambda document: _resolve_root(document, parent_map, document_dates))
        .tolist(),
        index=result.index,
        columns=[
            "doc_base_comision_key",
            "ciclo_reemplazo",
            "documentos_ciclo_reemplazo",
        ],
    )
    result["doc_base_comision_key"] = resolved["doc_base_comision_key"]
    result["ciclo_reemplazo"] = resolved["ciclo_reemplazo"].astype(bool)
    result["documentos_ciclo_reemplazo"] = resolved[
        "documentos_ciclo_reemplazo"
    ]
    result["doc_base_comision"] = result["doc_base_comision_key"]

    chain_documents = (
        result[["doc_base_comision_key", "tranId_key", "tranDate"]]
        .dropna(subset=["doc_base_comision_key", "tranId_key"])
        .drop_duplicates()
        .sort_values(
            ["doc_base_comision_key", "tranDate", "tranId_key"],
            na_position="first",
        )
    )
    payment_source = (
        chain_documents.drop_duplicates("doc_base_comision_key", keep="last")
        .set_index("doc_base_comision_key")["tranId_key"]
    )
    result["doc_fuente_pago_preferido_key"] = result[
        "doc_base_comision_key"
    ].map(payment_source).fillna(result["doc_base_comision_key"])

    cancellation = result["tranId_nc"].notna() & result[
        "factura_reemplazo_tranId_nc"
    ].notna()
    cancellation_events = (
        result.loc[cancellation, ["doc_base_comision_key", "tranId_nc"]]
        .assign(
            tranId_nc=lambda data: data["tranId_nc"]
            .astype("string")
            .str.split(" | ", regex=False)
        )
        .explode("tranId_nc")
        .dropna()
        .drop_duplicates()
    )
    cancellation_summary = cancellation_events.groupby("doc_base_comision_key")[
        "tranId_nc"
    ].agg(
        cantidad_anulaciones_nc="nunique",
        documentos_anulacion_nc=lambda values: " | ".join(
            sorted(values.astype(str).unique())
        ),
    )
    result["cantidad_anulaciones_nc"] = (
        result["doc_base_comision_key"]
        .map(cancellation_summary.get("cantidad_anulaciones_nc", pd.Series(dtype=float)))
        .fillna(0)
        .astype("Int64")
    )
    result["documentos_anulacion_nc"] = result["doc_base_comision_key"].map(
        cancellation_summary.get("documentos_anulacion_nc", pd.Series(dtype="string"))
    )

    due_lookup = (
        result[["tranId_key", "dueDate"]]
        .dropna(subset=["tranId_key"])
        .sort_values(["tranId_key", "dueDate"], na_position="last")
        .drop_duplicates("tranId_key", keep="first")
        .set_index("tranId_key")["dueDate"]
    )
    result["dueDate_base_pago"] = result["doc_base_comision_key"].map(due_lookup)
    result["dueDate_base_pago"] = result["dueDate_base_pago"].combine_first(
        result["dueDate"]
    )
    period_lookup = (
        result[["tranId_key", "periodo_mes_documento"]]
        .dropna(subset=["tranId_key"])
        .drop_duplicates("tranId_key", keep="first")
        .set_index("tranId_key")["periodo_mes_documento"]
    )
    result["fecha_origen"] = normalize_date(
        result.get("fecha_origen", pd.Series(pd.NaT, index=result.index))
    )
    root_period = result["doc_base_comision_key"].map(period_lookup)
    origin_date_period = result["fecha_origen"].dt.to_period("M")
    is_replacement = normalize_boolean(result["reemplazo"])
    result["periodo_mes"] = result["periodo_mes_documento"]
    result.loc[is_replacement, "periodo_mes"] = root_period.loc[
        is_replacement
    ].combine_first(origin_date_period.loc[is_replacement])
    result["reemplazo_trazable_pago"] = (
        is_replacement
        & result["documento_origen"].notna()
        & result["periodo_mes"].notna()
        & result["dueDate_base_pago"].notna()
    )
    result["es_documento_comision"] = result["tranId_key"].eq(
        result["doc_fuente_pago_preferido_key"]
    )
    return result


def select_available_payment_source(
    resolved_sales: pd.DataFrame,
    available_documents: pd.Series,
) -> pd.DataFrame:
    """Use the latest chain document when available, otherwise use the root."""
    result = resolved_sales.copy()
    available = set(normalize_upper(available_documents).dropna())
    result["doc_fuente_pago_key"] = result[
        "doc_fuente_pago_preferido_key"
    ].where(
        result["doc_fuente_pago_preferido_key"].isin(available),
        result["doc_base_comision_key"],
    )
    return result


def _resolve_root(
    document: str,
    parent_map: dict[str, str],
    document_dates: dict[str, pd.Timestamp],
) -> tuple[str, bool, object]:
    current = document
    route: list[str] = []
    while current in parent_map:
        if current in route:
            cycle = route[route.index(current) :]
            technical_root = min(
                cycle,
                key=lambda item: (document_dates.get(item, pd.Timestamp.max), item),
            )
            return technical_root, True, " | ".join(sorted(cycle))
        route.append(current)
        current = parent_map[current]
    return current, False, pd.NA


def _to_month_period(series: pd.Series) -> pd.Series:
    if str(series.dtype).startswith("period"):
        return series.astype("period[M]")
    return pd.to_datetime(series, errors="coerce").dt.to_period("M")


def _require_columns(data: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = sorted(set(columns).difference(data.columns))
    if missing:
        raise ValueError(f"{name}: missing required columns: {', '.join(missing)}")
