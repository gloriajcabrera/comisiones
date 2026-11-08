from __future__ import annotations

import numpy as np
import pandas as pd

from src.normalization import normalize_date, normalize_number, normalize_text, normalize_upper


def apply_credit_effects(
    sales: pd.DataFrame,
    proforma_cancellations: pd.DataFrame,
) -> pd.DataFrame:
    """Classify invoice credit notes and proforma cancellations by period."""
    _require_columns(
        sales,
        (
            "tranId",
            "tranDate",
            "es_factura",
            "es_proforma",
            "reemplazo",
            "tranId_nc",
            "tranDate_nc",
            "Valor_nc",
            "Valor",
            "factura_reemplazo_tranId_nc",
        ),
        "sales",
    )
    result = sales.copy()
    result["tranDate"] = normalize_date(result["tranDate"])
    result["tranDate_nc"] = normalize_date(result["tranDate_nc"])
    result["Valor_nc"] = normalize_number(result["Valor_nc"])
    result["periodo_factura"] = result["tranDate"].dt.to_period("M")
    result["periodo_nc"] = result["tranDate_nc"].dt.to_period("M")
    result["documento_anulado"] = False
    result["tipo_afectacion_nc"] = "ninguna"
    result["requiere_recalculo_piso"] = False
    result["periodo_recalculo"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["observacion_nc"] = ""
    result["cadena_rota_mismo_dia"] = False

    invoice_with_note = result["es_factura"] & result["tranId_nc"].notna()
    same_period = invoice_with_note & result["periodo_factura"].eq(result["periodo_nc"])
    document_dates = (
        result[["tranId", "tranDate"]]
        .dropna(subset=["tranId"])
        .drop_duplicates("tranId", keep="first")
        .set_index("tranId")["tranDate"]
    )
    result["fecha_reemplazo_nc"] = result["factura_reemplazo_tranId_nc"].map(
        document_dates
    )
    same_day_replacement = (
        same_period
        & result["factura_reemplazo_tranId_nc"].notna()
        & result["tranDate"].dt.normalize().eq(result["tranDate_nc"].dt.normalize())
        & result["tranDate_nc"].dt.normalize().eq(
            result["fecha_reemplazo_nc"].dt.normalize()
        )
    )
    same_period_with_replacement = (
        same_period
        & result["factura_reemplazo_tranId_nc"].notna()
        & ~same_day_replacement
    )
    same_period_without_replacement = same_period & (
        result["factura_reemplazo_tranId_nc"].isna() | same_day_replacement
    )
    other_period = invoice_with_note & ~result["periodo_factura"].eq(result["periodo_nc"])
    other_period_with_replacement = other_period & result[
        "factura_reemplazo_tranId_nc"
    ].notna()
    other_period_without_replacement = other_period & result[
        "factura_reemplazo_tranId_nc"
    ].isna()

    result.loc[same_period_without_replacement, "tipo_afectacion_nc"] = (
        "factura_nc_mismo_mes"
    )
    result.loc[same_period_with_replacement, "tipo_afectacion_nc"] = (
        "factura_nc_mismo_mes_con_reemplazo"
    )
    result.loc[same_period_with_replacement, "observacion_nc"] = (
        "Suma en su mes original; NC "
        + result.loc[same_period_with_replacement, "tranId_nc"].astype("string")
        + " reemplazada por "
        + result.loc[
            same_period_with_replacement, "factura_reemplazo_tranId_nc"
        ].astype("string")
    )

    replacement_documents = set(
        result.loc[same_day_replacement, "factura_reemplazo_tranId_nc"]
        .dropna()
        .astype(str)
    )
    result.loc[same_day_replacement, "cadena_rota_mismo_dia"] = True
    new_origin = result["tranId"].isin(replacement_documents)
    result.loc[new_origin, "reemplazo"] = False
    result.loc[new_origin, "tipo_reemplazo"] = "ninguno"
    result.loc[new_origin, "documento_origen"] = pd.NA
    result.loc[new_origin, "fecha_origen"] = pd.NaT
    result.loc[new_origin, "documento_reemplazado"] = pd.NA
    result.loc[new_origin, "observacion_reemplazo"] = (
        "Nuevo documento original: cadena anterior anulada el mismo día"
    )
    # Al cortar la cadena, la NC pertenece al documento anulado. El reemplazo
    # inicia una cadena nueva y no puede heredar esa afectacion financiera.
    result.loc[new_origin, "tranId_nc"] = pd.NA
    result.loc[new_origin, "tranDate_nc"] = pd.NaT
    result.loc[new_origin, "Valor_nc"] = np.nan
    result.loc[new_origin, "periodo_nc"] = pd.NaT
    result.loc[new_origin, "factura_reemplazo_tranId_nc"] = pd.NA
    result.loc[new_origin, "fecha_reemplazo_nc"] = pd.NaT
    result.loc[new_origin, "documento_anulado"] = False
    result.loc[new_origin, "tipo_afectacion_nc"] = "ninguna"
    result.loc[new_origin, "requiere_recalculo_piso"] = False
    result.loc[new_origin, "periodo_recalculo"] = pd.NA
    result.loc[new_origin, "observacion_nc"] = ""
    result.loc[new_origin, "cadena_rota_mismo_dia"] = False

    result.loc[other_period_with_replacement, "tipo_afectacion_nc"] = (
        "factura_nc_otro_mes_con_reemplazo"
    )
    result.loc[other_period_with_replacement, "observacion_nc"] = (
        "Suma en su mes original; NC "
        + result.loc[other_period_with_replacement, "tranId_nc"].astype("string")
        + " reemplazada por "
        + result.loc[
            other_period_with_replacement, "factura_reemplazo_tranId_nc"
        ].astype("string")
    )
    result.loc[other_period_without_replacement, "tipo_afectacion_nc"] = (
        "factura_nc_otro_mes_sin_reemplazo"
    )
    result.loc[other_period_without_replacement, "requiere_recalculo_piso"] = True
    result.loc[other_period_without_replacement, "periodo_recalculo"] = result.loc[
        other_period_without_replacement, "periodo_factura"
    ].astype("string")
    result.loc[other_period_without_replacement, "observacion_nc"] = (
        "Suma en su mes original; recalcular piso de "
        + result.loc[other_period_without_replacement, "periodo_factura"].astype("string")
        + " por "
        + result.loc[other_period_without_replacement, "tranId_nc"].astype("string")
        + " sin reemplazo"
    )

    cancellation_documents = prepare_proforma_cancellations(proforma_cancellations)
    result["tranId_key"] = normalize_upper(result["tranId"])
    result = result.merge(cancellation_documents, on="tranId_key", how="left", validate="m:1")
    cancelled_proforma = result["es_proforma"] & result["tranId_nc_ncp"].notna()
    result.loc[cancelled_proforma, "tranId_nc"] = result.loc[
        cancelled_proforma, "tranId_nc_ncp"
    ]
    result.loc[cancelled_proforma, "tranDate_nc"] = result.loc[
        cancelled_proforma, "tranDate_nc_ncp"
    ]
    result.loc[cancelled_proforma, "documento_anulado"] = True
    result.loc[cancelled_proforma, "Valor_nc"] = -result.loc[
        cancelled_proforma, "Valor"
    ].abs()
    result.loc[cancelled_proforma, "periodo_nc"] = result.loc[
        cancelled_proforma, "tranDate_nc"
    ].dt.to_period("M")

    proforma_same_period = cancelled_proforma & result["periodo_factura"].eq(
        result["periodo_nc"]
    )
    proforma_other_period = cancelled_proforma & ~result["periodo_factura"].eq(
        result["periodo_nc"]
    )
    result.loc[proforma_same_period, "tipo_afectacion_nc"] = (
        "proforma_anulada_mismo_mes"
    )
    result.loc[proforma_other_period, "tipo_afectacion_nc"] = (
        "proforma_anulada_otro_mes"
    )
    result.loc[proforma_other_period, "requiere_recalculo_piso"] = True
    result.loc[proforma_other_period, "periodo_recalculo"] = result.loc[
        proforma_other_period, "periodo_factura"
    ].astype("string")
    result.loc[proforma_other_period, "observacion_nc"] = (
        "Suma en su mes original; recalcular piso de "
        + result.loc[proforma_other_period, "periodo_factura"].astype("string")
        + " por anulación "
        + result.loc[proforma_other_period, "tranId_nc"].astype("string")
    )
    return result


def prepare_proforma_cancellations(data: pd.DataFrame) -> pd.DataFrame:
    work = data.copy()
    for column in ("tranId", "memo"):
        if column in work.columns:
            work[column] = normalize_text(work[column])
    if "tranDate" in work.columns:
        work["tranDate"] = normalize_date(work["tranDate"])
    if "documentos_detectados" in work.columns:
        work["doc_afectado"] = normalize_upper(work["documentos_detectados"])
    else:
        memo = work.get(
            "memo", pd.Series("", index=work.index, dtype="string")
        )
        work["doc_afectado"] = normalize_upper(
            memo.astype("string").str.extract(r"(PR-\d+|SM\d+)", expand=False)
        )
    if "tranId" not in work.columns:
        work["tranId"] = pd.Series(pd.NA, index=work.index, dtype="string")
    if "tranDate" not in work.columns:
        work["tranDate"] = pd.NaT
    return (
        work.loc[work["doc_afectado"].notna(), ["doc_afectado", "tranId", "tranDate"]]
        .sort_values(["doc_afectado", "tranDate", "tranId"])
        .drop_duplicates("doc_afectado", keep="first")
        .rename(
            columns={
                "doc_afectado": "tranId_key",
                "tranId": "tranId_nc_ncp",
                "tranDate": "tranDate_nc_ncp",
            }
        )
    )


def calculate_floor_line_status(sales: pd.DataFrame) -> pd.DataFrame:
    """Apply the notebook priority rules for whether a line sums to floor."""
    _require_columns(
        sales,
        (
            "Valor",
            "reemplazo",
            "tipo_afectacion_nc",
            "alarma_nc_servicio",
            "motivo_alarma_nc",
            "observacion_reemplazo",
            "observacion_nc",
            "tranId_nc",
        ),
        "sales",
    )
    result = sales.copy()
    result["Valor"] = normalize_number(result["Valor"])
    result["TotalParaPiso_base"] = result["Valor"]
    result["estado_piso"] = "suma"
    result["TotalParaPiso_final"] = result["TotalParaPiso_base"]
    result["observacion_piso"] = ""

    eligible = (
        result["elegible_comision"].fillna(False).astype(bool)
        if "elegible_comision" in result.columns
        else pd.Series(True, index=result.index)
    )

    replacement = result["reemplazo"].fillna(False)
    same_period_credit = result["tipo_afectacion_nc"].isin(
        ["factura_nc_mismo_mes", "proforma_anulada_mismo_mes"]
    )
    review = result["alarma_nc_servicio"].fillna(False)
    other_period = result["tipo_afectacion_nc"].isin(
        [
            "factura_nc_otro_mes_con_reemplazo",
            "factura_nc_otro_mes_sin_reemplazo",
            "proforma_anulada_otro_mes",
        ]
    )
    result.loc[replacement, "estado_piso"] = "no suma"
    result.loc[~replacement & same_period_credit, "estado_piso"] = "no suma"
    result.loc[~replacement & ~same_period_credit & review, "estado_piso"] = (
        "para revision"
    )
    result.loc[result["estado_piso"].eq("no suma"), "TotalParaPiso_final"] = 0
    result.loc[result["estado_piso"].eq("para revision"), "TotalParaPiso_final"] = np.nan

    result.loc[replacement, "observacion_piso"] = result.loc[
        replacement, "observacion_reemplazo"
    ]
    invoice_same = ~replacement & result["tipo_afectacion_nc"].eq(
        "factura_nc_mismo_mes"
    )
    result.loc[invoice_same, "observacion_piso"] = (
        "No suma por nota crédito "
        + result.loc[invoice_same, "tranId_nc"].astype("string")
        + " del mismo periodo"
    )
    invoice_same_with_replacement = ~replacement & result["tipo_afectacion_nc"].eq(
        "factura_nc_mismo_mes_con_reemplazo"
    )
    result.loc[invoice_same_with_replacement, "observacion_piso"] = result.loc[
        invoice_same_with_replacement, "observacion_nc"
    ]
    proforma_same = ~replacement & result["tipo_afectacion_nc"].eq(
        "proforma_anulada_mismo_mes"
    )
    result.loc[proforma_same, "observacion_piso"] = (
        "No suma por anulación "
        + result.loc[proforma_same, "tranId_nc"].astype("string")
        + " del mismo periodo"
    )
    needs_review = ~replacement & ~same_period_credit & review
    result.loc[needs_review, "observacion_piso"] = (
        "Para revisión: "
        + result.loc[needs_review, "motivo_alarma_nc"].astype("string")
    )
    affected_other_period = result["estado_piso"].eq("suma") & other_period
    result.loc[affected_other_period, "observacion_piso"] = result.loc[
        affected_other_period, "observacion_nc"
    ]
    ineligible = ~eligible
    result.loc[ineligible, "estado_piso"] = "no suma"
    result.loc[ineligible, "TotalParaPiso_final"] = 0
    if "motivo_elegibilidad" in result.columns:
        result.loc[ineligible, "observacion_piso"] = result.loc[
            ineligible, "motivo_elegibilidad"
        ]
    empty_observation = result["observacion_piso"].astype("string").fillna("").eq("")
    result.loc[empty_observation, "observacion_piso"] = "Suma para piso"
    return result


def _require_columns(data: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = sorted(set(columns).difference(data.columns))
    if missing:
        raise ValueError(f"{name}: missing required columns: {', '.join(missing)}")
