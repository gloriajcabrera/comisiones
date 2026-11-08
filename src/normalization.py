from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


TRANSACTION_TEXT_COLUMNS = (
    "tranId",
    "type",
    "status",
    "entityid",
    "Servicio",
    "cc",
    "memo",
    "clase",
    "itemType",
    "custitem_sm_linea_serv",
)
TRANSACTION_DATE_COLUMNS = ("tranDate", "dueDate")
TRANSACTION_NUMBER_COLUMNS = (
    "Valor",
    "Precio",
    "quantity",
    "valor_total_documento",
    "valor_moneda_local",
    "valor_moneda_transaccion",
    "foreignamountunpaid",
)
TRANSACTION_ID_COLUMNS = (
    "id",
    "createdfrom",
    "custitem_sm_lineacomision",
    "custbody_sm_reemplaza_a",
    "custbody_sm_factura_reemplazo",
)

TRANSACTION_CANONICAL_COLUMNS = (
    *TRANSACTION_TEXT_COLUMNS,
    *TRANSACTION_DATE_COLUMNS,
    *TRANSACTION_NUMBER_COLUMNS,
    *TRANSACTION_ID_COLUMNS,
    "lote",
    "custbody_ks_sm_proforma",
)

TRANSACTION_OPTIONAL_COLUMNS = (
    "dueDate",
    "Servicio",
    "itemType",
    "custitem_sm_linea_serv",
    "createdfrom",
    "custitem_sm_lineacomision",
    "custbody_sm_reemplaza_a",
    "custbody_sm_factura_reemplazo",
    "custbody_ks_sm_proforma",
    "lote",
    "memo",
    "clase",
    "cc",
    "foreignamountunpaid",
)


def normalize_text(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def normalize_upper(series: pd.Series) -> pd.Series:
    return normalize_text(series).str.upper()


def normalize_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def normalize_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def normalize_id(series: pd.Series) -> pd.Series:
    normalized = normalize_text(series).str.replace(r"\.0$", "", regex=True)
    return normalized.fillna("0")


def normalize_boolean(series: pd.Series) -> pd.Series:
    # Convert through object so nullable/string extension dtypes accept False.
    normalized = series.astype("object").where(series.notna(), False)
    return normalized.astype(bool)


def normalize_transaction_documents(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize invoices, proformas or credit notes without mutating input."""
    source_columns = {str(column).lower() for column in data.columns}
    expanded_provider_schema = "line_id" in source_columns
    service_filter_available = data.attrs.get(
        "commission_service_filter_available",
        "custitem_sm_linea_serv" in source_columns or expanded_provider_schema,
    )
    item_type_filter_available = data.attrs.get(
        "commission_item_type_filter_available",
        "itemtype" in source_columns or expanded_provider_schema,
    )
    result = _canonicalize_columns(data, TRANSACTION_CANONICAL_COLUMNS)
    for column in TRANSACTION_OPTIONAL_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA
    _apply(result, TRANSACTION_TEXT_COLUMNS, normalize_text)
    _apply(result, TRANSACTION_DATE_COLUMNS, normalize_date)
    _apply(result, TRANSACTION_NUMBER_COLUMNS, normalize_number)
    _apply(result, TRANSACTION_ID_COLUMNS, normalize_id)

    if "lote" in result.columns:
        result["lote"] = normalize_text(result["lote"]).fillna("")
    if "custbody_ks_sm_proforma" in result.columns:
        result["custbody_ks_sm_proforma"] = normalize_boolean(
            result["custbody_ks_sm_proforma"]
        )
    result.attrs["commission_service_filter_available"] = bool(
        service_filter_available
    )
    result.attrs["commission_item_type_filter_available"] = bool(
        item_type_filter_available
    )
    return result


def _canonicalize_columns(
    data: pd.DataFrame, canonical_columns: Iterable[str]
) -> pd.DataFrame:
    """Restore stable engine names when a provider lowercases SQL aliases."""
    result = data.copy()
    existing = {str(column).lower(): str(column) for column in result.columns}
    rename = {
        existing[canonical.lower()]: canonical
        for canonical in canonical_columns
        if canonical not in result.columns and canonical.lower() in existing
    }
    return result.rename(columns=rename)


def normalize_proforma_cancellations(data: pd.DataFrame) -> pd.DataFrame:
    result = _canonicalize_columns(
        data, ("tranId", "tranDate", "Valor", "memo", "documentos_detectados")
    )
    _apply(result, ("tranId", "memo", "documentos_detectados"), normalize_text)
    _apply(result, ("tranDate",), normalize_date)
    return result


def normalize_targets(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    for column in result.columns:
        if result[column].dtype == "object":
            result[column] = normalize_text(result[column])
    _apply(
        result,
        ("meta", "piso", "comision_ejecutivo", "comision_jefe", "comision_gerente"),
        normalize_number,
    )
    return result


def normalize_invoice_payments(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    _apply(
        result,
        ("factura", "cliente", "documento_pago", "tipo_pago", "memo_pago"),
        normalize_text,
    )
    _apply(
        result,
        ("factura_id", "cliente_id", "pago_id"),
        normalize_id,
    )
    _apply(
        result,
        ("fecha_factura", "fecha_contabilizacion", "fecha_real", "fecha_pago"),
        normalize_date,
    )
    _apply(result, ("valor_pago",), normalize_number)
    return result


def normalize_proforma_payments(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    _apply(
        result,
        (
            "proforma",
            "cliente",
            "deposito",
            "documento_pago",
            "tipo_pago",
            "memo_pago",
            "tipo_vinculo",
        ),
        normalize_text,
    )
    _apply(
        result,
        ("proforma_id", "cliente_id", "deposito_id", "pago_id"),
        normalize_id,
    )
    _apply(
        result,
        (
            "fecha_proforma",
            "fecha_deposito",
            "fecha_transaccion",
            "fecha_real_transaccion",
            "fecha_pago",
        ),
        normalize_date,
    )
    _apply(result, ("valor_pagado_proforma",), normalize_number)
    return result


def build_sales_base(
    invoices: pd.DataFrame,
    proformas: pd.DataFrame,
) -> pd.DataFrame:
    """Build the shared sales base using source dataset as document truth."""
    invoice_base = annotate_commission_eligibility(invoices, "factura")
    proforma_base = annotate_commission_eligibility(proformas, "proforma")
    invoice_base["origen_documento"] = "factura"
    proforma_base["origen_documento"] = "proforma"

    result = pd.concat([invoice_base, proforma_base], ignore_index=True, sort=False)
    if "custbody_ks_sm_proforma" not in result.columns:
        result["custbody_ks_sm_proforma"] = False
    else:
        result["custbody_ks_sm_proforma"] = normalize_boolean(
            result["custbody_ks_sm_proforma"]
        )

    result["es_factura"] = result["origen_documento"].eq("factura")
    result["es_proforma"] = result["origen_documento"].eq("proforma")
    result["periodo_factura"] = result["tranDate"].dt.to_period("M")
    result["Servicio_key"] = normalize_upper(result["Servicio"])
    result["lote_key"] = normalize_upper(result["lote"])
    result["valor_abs"] = result["Valor"].abs().round(2)
    result["createdfrom_key"] = normalize_id(result["createdfrom"])
    result["id_key"] = normalize_id(result["id"])
    result["reemplaza_a_key"] = normalize_id(result["custbody_sm_reemplaza_a"])
    result["factura_reemplazo_key"] = normalize_id(
        result["custbody_sm_factura_reemplazo"]
    )
    return result


def annotate_commission_eligibility(
    data: pd.DataFrame,
    document_kind: str,
) -> pd.DataFrame:
    """Keep every source line while preserving the legacy inclusion rules."""
    kind = str(document_kind).strip().lower()
    if kind not in {"factura", "proforma"}:
        raise ValueError("document_kind debe ser factura o proforma")
    result = normalize_transaction_documents(data)
    has_service_line = bool(
        result.attrs.get("commission_service_filter_available", False)
    )
    has_item_type = bool(
        result.attrs.get("commission_item_type_filter_available", False)
    )
    line = (
        result["custitem_sm_linea_serv"].astype("string").str.strip()
        if has_service_line
        else pd.Series(pd.NA, index=result.index, dtype="string")
    )
    item_type = (
        result["itemType"].astype("string").str.strip().str.lower()
        if has_item_type
        else pd.Series(pd.NA, index=result.index, dtype="string")
    )
    accounting_value = (
        result["Valor"].notna()
        if "Valor" in result.columns
        else pd.Series(True, index=result.index)
    )
    eligible = accounting_value.copy()
    if has_service_line:
        eligible &= line.notna() & line.ne("") & line.ne("18")
    if kind == "proforma" and has_item_type:
        eligible &= item_type.eq("service")

    reason = pd.Series("Elegible para comisión", index=result.index, dtype="string")
    reason.loc[~accounting_value] = "No suma: línea sin valor contable"
    if has_service_line:
        reason.loc[line.isna() | line.eq("")] = (
            "No suma: artículo sin línea de servicio"
        )
        reason.loc[line.eq("18")] = "No suma: línea de servicio 18 excluida"
    if kind == "proforma" and has_item_type:
        reason.loc[item_type.ne("service") | item_type.isna()] = (
            "No suma: artículo de proforma no es tipo servicio"
        )
    reason.loc[eligible] = "Elegible para comisión"
    result["elegible_comision"] = eligible
    result["motivo_elegibilidad"] = reason
    return result


def _apply(
    data: pd.DataFrame,
    columns: Iterable[str],
    operation,
) -> None:
    for column in columns:
        if column in data.columns:
            data[column] = operation(data[column])
