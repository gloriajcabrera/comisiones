from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.normalization import normalize_boolean
from src.rule_parameters import RuleParameters


MOVEMENT_KEYS = (
    "tipo_documento",
    "id_documento",
    "documento",
    "fecha_documento",
    "deposito_id",
    "deposito",
    "pago_id",
    "documento_pago",
    "tipo_pago",
    "fecha_contabilizacion",
    "fecha_real",
    "fecha_pago",
    "tipo_vinculo",
)


@dataclass(frozen=True)
class OfficialPaymentBase:
    documents: pd.DataFrame
    history: pd.DataFrame
    unmatched_movements: pd.DataFrame


def payment_text(series: pd.Series, *, upper: bool = False) -> pd.Series:
    result = series.astype("string").str.strip()
    if upper:
        result = result.str.upper()
    return result.replace({"": pd.NA, "NAN": pd.NA, "NONE": pd.NA, "<NA>": pd.NA})


def payment_id(series: pd.Series) -> pd.Series:
    return payment_text(series, upper=True).str.replace(r"\.0+$", "", regex=True)


def join_payment_texts(series: pd.Series) -> str:
    values = series.dropna().astype("string").str.strip()
    values = values.loc[
        ~values.str.upper().isin(["", "NAN", "NONE", "<NA>"])
    ].drop_duplicates()
    return " | ".join(values.tolist())


def normalize_cut_periods(cuts: pd.DataFrame) -> pd.DataFrame:
    _require_columns(cuts, ("inicio_corte", "fin_corte", "periodo_corte"), "cuts")
    result = cuts[["inicio_corte", "fin_corte", "periodo_corte"]].copy()
    result["inicio_corte"] = pd.to_datetime(
        result["inicio_corte"], errors="coerce"
    ).dt.normalize()
    result["fin_corte"] = pd.to_datetime(
        result["fin_corte"], errors="coerce"
    ).dt.normalize()
    result["periodo_corte"] = payment_text(result["periodo_corte"])
    result = (
        result.dropna(subset=["inicio_corte", "fin_corte", "periodo_corte"])
        .sort_values("inicio_corte")
        .drop_duplicates("periodo_corte", keep="last")
        .reset_index(drop=True)
    )
    if result.empty:
        raise ValueError("La tabla de periodos de corte está vacía.")
    if result["inicio_corte"].gt(result["fin_corte"]).any():
        raise ValueError("Existen periodos con inicio_corte posterior a fin_corte.")
    previous_end = result["fin_corte"].shift()
    if result["inicio_corte"].le(previous_end).fillna(False).any():
        raise ValueError("Existen periodos de corte superpuestos.")
    return result


def assign_cut_period(
    data: pd.DataFrame,
    date_column: str,
    cuts: pd.DataFrame,
) -> pd.DataFrame:
    normalized_cuts = normalize_cut_periods(cuts)
    if date_column not in data.columns:
        raise ValueError(f"Missing date column: {date_column}")
    result = data.copy()
    result["periodo_corte"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["inicio_corte"] = pd.NaT
    result["fin_corte"] = pd.NaT
    dates = pd.to_datetime(result[date_column], errors="coerce").dt.normalize()

    for cut in normalized_cuts.itertuples(index=False):
        mask = (
            dates.ge(cut.inicio_corte)
            & dates.le(cut.fin_corte)
            & result["periodo_corte"].isna()
        )
        result.loc[mask, "periodo_corte"] = cut.periodo_corte
        result.loc[mask, "inicio_corte"] = cut.inicio_corte
        result.loc[mask, "fin_corte"] = cut.fin_corte

    first_start = normalized_cuts["inicio_corte"].min()
    historical = dates.notna() & dates.lt(first_start) & result["periodo_corte"].isna()
    historical_period = dates.loc[historical].dt.to_period("M")
    result.loc[historical, "periodo_corte"] = historical_period.astype("string")
    result.loc[historical, "inicio_corte"] = historical_period.dt.start_time
    result.loc[historical, "fin_corte"] = historical_period.dt.end_time.dt.normalize()
    return result


def prepare_payment_documents(data: pd.DataFrame, document_type: str) -> pd.DataFrame:
    required = (
        "tranId",
        "tranDate",
        "dueDate",
        "id",
        "clase",
        "valor_total_documento",
        "valor_moneda_transaccion",
        "valor_moneda_local",
        "moneda_id",
    )
    _require_columns(data, required, document_type)
    work = data.copy()
    if "foreignamountunpaid" not in work.columns:
        work["foreignamountunpaid"] = np.nan
    work["tipo_documento"] = document_type
    work["documento"] = payment_text(work["tranId"], upper=True)
    work["id_documento"] = payment_id(work["id"])
    work["fecha_documento"] = pd.to_datetime(work["tranDate"], errors="coerce")
    work["fecha_vencimiento"] = pd.to_datetime(
        work["dueDate"], errors="coerce"
    ).combine_first(work["fecha_documento"])
    work["clase_normalizada"] = payment_text(work["clase"], upper=True)
    for column in (
        "valor_total_documento",
        "valor_moneda_transaccion",
        "valor_moneda_local",
        "foreignamountunpaid",
    ):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work["saldo_netsuite_transaccion"] = work["foreignamountunpaid"]
    work["valor_total_transaccion"] = work["valor_moneda_transaccion"].fillna(
        work["valor_total_documento"]
    )
    work = work.loc[work["documento"].notna()].copy()
    documents = (
        work.groupby(["tipo_documento", "documento"], as_index=False, dropna=False)
        .agg(
            id_documento=("id_documento", "first"),
            fecha_documento=("fecha_documento", "first"),
            fecha_vencimiento=("fecha_vencimiento", "first"),
            moneda_id=("moneda_id", "first"),
            valor_total_transaccion=("valor_total_transaccion", "first"),
            valor_total_local=("valor_moneda_local", "first"),
            saldo_netsuite_transaccion=("saldo_netsuite_transaccion", "first"),
            clases_documento=("clase_normalizada", join_payment_texts),
            cantidad_clases=("clase_normalizada", lambda values: values.dropna().nunique()),
            es_producto=("clase_normalizada", lambda values: values.eq("PRODUCTO").any()),
        )
    )
    documents["es_producto"] = normalize_boolean(documents["es_producto"])
    documents["es_producto_mixto"] = documents["es_producto"] & documents[
        "cantidad_clases"
    ].gt(1)
    documents["es_producto_puro"] = documents["es_producto"] & ~documents[
        "es_producto_mixto"
    ]
    documents["valor_total_transaccion"] = pd.to_numeric(
        documents["valor_total_transaccion"], errors="coerce"
    )
    documents["valor_total_local"] = pd.to_numeric(
        documents["valor_total_local"], errors="coerce"
    )
    rate = (
        documents["valor_total_local"] / documents["valor_total_transaccion"]
    ).replace([np.inf, -np.inf], np.nan)
    documents["tasa_documento"] = rate.fillna(1.0)
    documents["valor_total_local"] = documents["valor_total_local"].fillna(
        documents["valor_total_transaccion"] * documents["tasa_documento"]
    )
    return documents


def prepare_invoice_payment_history(data: pd.DataFrame) -> pd.DataFrame:
    required = (
        "factura_id",
        "factura",
        "fecha_factura",
        "pago_id",
        "documento_pago",
        "tipo_pago",
        "fecha_contabilizacion",
        "fecha_real",
        "fecha_pago",
        "valor_pago",
        "memo_pago",
    )
    _require_columns(data, required, "PFF")
    result = pd.DataFrame(index=data.index)
    result["tipo_documento"] = "FACTURA"
    result["id_documento"] = payment_id(data["factura_id"])
    result["documento"] = payment_text(data["factura"], upper=True)
    result["fecha_documento"] = pd.to_datetime(data["fecha_factura"], errors="coerce")
    result["deposito_id"] = pd.Series(pd.NA, index=data.index, dtype="string")
    result["deposito"] = pd.Series(pd.NA, index=data.index, dtype="string")
    result["pago_id"] = payment_id(data["pago_id"])
    result["documento_pago"] = payment_text(data["documento_pago"], upper=True)
    result["tipo_pago"] = payment_text(data["tipo_pago"], upper=True)
    result["fecha_contabilizacion"] = pd.to_datetime(
        data["fecha_contabilizacion"], errors="coerce"
    )
    result["fecha_real"] = pd.to_datetime(data["fecha_real"], errors="coerce")
    result["fecha_pago"] = pd.to_datetime(data["fecha_pago"], errors="coerce")
    result["valor_aplicado_transaccion"] = pd.to_numeric(
        data["valor_pago"], errors="coerce"
    )
    result["memo_pago"] = payment_text(data["memo_pago"])
    result["tipo_vinculo"] = "PAYMENT"
    return result


def prepare_proforma_payment_history(data: pd.DataFrame) -> pd.DataFrame:
    required = (
        "proforma_id",
        "proforma",
        "fecha_proforma",
        "deposito_id",
        "deposito",
        "pago_id",
        "documento_pago",
        "tipo_pago",
        "fecha_transaccion",
        "fecha_real_transaccion",
        "fecha_pago",
        "valor_pagado_proforma",
        "memo_pago",
        "tipo_vinculo",
    )
    _require_columns(data, required, "PPF")
    result = pd.DataFrame(index=data.index)
    result["tipo_documento"] = "PROFORMA"
    result["id_documento"] = payment_id(data["proforma_id"])
    result["documento"] = payment_text(data["proforma"], upper=True)
    result["fecha_documento"] = pd.to_datetime(data["fecha_proforma"], errors="coerce")
    result["deposito_id"] = payment_id(data["deposito_id"])
    result["deposito"] = payment_text(data["deposito"], upper=True)
    result["pago_id"] = payment_id(data["pago_id"])
    result["documento_pago"] = payment_text(data["documento_pago"], upper=True)
    result["tipo_pago"] = payment_text(data["tipo_pago"], upper=True)
    result["fecha_contabilizacion"] = pd.to_datetime(
        data["fecha_transaccion"], errors="coerce"
    )
    result["fecha_real"] = pd.to_datetime(
        data["fecha_real_transaccion"], errors="coerce"
    )
    result["fecha_pago"] = pd.to_datetime(data["fecha_pago"], errors="coerce")
    result["valor_aplicado_transaccion"] = pd.to_numeric(
        data["valor_pagado_proforma"], errors="coerce"
    )
    result["memo_pago"] = payment_text(data["memo_pago"])
    result["tipo_vinculo"] = payment_text(data["tipo_vinculo"], upper=True)
    return result


def build_official_payment_history(
    invoice_payments: pd.DataFrame,
    proforma_payments: pd.DataFrame,
    cuts: pd.DataFrame,
) -> pd.DataFrame:
    invoice_history = prepare_invoice_payment_history(invoice_payments)
    proforma_history = prepare_proforma_payment_history(proforma_payments)
    result = pd.concat([invoice_history, proforma_history], ignore_index=True)
    result["fecha_pago"] = result["fecha_pago"].fillna(result["fecha_real"]).fillna(
        result["fecha_contabilizacion"]
    )
    result = result.loc[result["documento"].notna() & result["pago_id"].notna()].copy()
    result = (
        result.groupby(list(MOVEMENT_KEYS), as_index=False, dropna=False)
        .agg(
            valor_aplicado_transaccion=("valor_aplicado_transaccion", "sum"),
            memo_pago=("memo_pago", join_payment_texts),
        )
    )
    result["es_nota_credito"] = result["tipo_pago"].eq("CUSTCRED")
    result["es_pago"] = ~result["es_nota_credito"]
    result["tipo_movimiento"] = np.where(
        result["es_nota_credito"], "NOTA CREDITO", "PAGO"
    )
    result["valor_pago_transaccion"] = result["valor_aplicado_transaccion"].where(
        result["es_pago"], 0
    )
    result["valor_nota_credito_transaccion"] = result[
        "valor_aplicado_transaccion"
    ].where(result["es_nota_credito"], 0)
    return assign_cut_period(result, "fecha_contabilizacion", cuts)


def build_payment_document_base(
    invoices: pd.DataFrame,
    proformas: pd.DataFrame,
) -> pd.DataFrame:
    result = pd.concat(
        [
            prepare_payment_documents(invoices, "FACTURA"),
            prepare_payment_documents(proformas, "PROFORMA"),
        ],
        ignore_index=True,
    )
    if result.duplicated(["tipo_documento", "documento"]).any():
        raise ValueError("documentos_pago_base contiene documentos duplicados.")
    return result


def build_payment_status(
    documents: pd.DataFrame,
    history: pd.DataFrame,
    parameters: RuleParameters | None = None,
) -> pd.DataFrame:
    """Consolidate balances, payment dates, product rule and review status."""
    _require_columns(
        documents,
        (
            "tipo_documento",
            "documento",
            "valor_total_transaccion",
            "saldo_netsuite_transaccion",
            "tasa_documento",
            "fecha_vencimiento",
            "es_producto_puro",
            "es_producto_mixto",
        ),
        "documents",
    )
    _require_columns(
        history,
        (
            "tipo_documento",
            "documento",
            "pago_id",
            "documento_pago",
            "fecha_pago",
            "fecha_contabilizacion",
            "periodo_corte",
            "inicio_corte",
            "fin_corte",
            "es_pago",
            "es_nota_credito",
            "valor_pago_transaccion",
            "valor_nota_credito_transaccion",
        ),
        "history",
    )
    params = (parameters or RuleParameters()).validate()
    history = history.copy()
    for column in (
        "valor_pago_transaccion",
        "valor_nota_credito_transaccion",
    ):
        history[column] = pd.to_numeric(history[column], errors="coerce")
    for column in (
        "fecha_pago",
        "fecha_contabilizacion",
        "inicio_corte",
        "fin_corte",
    ):
        history[column] = pd.to_datetime(history[column], errors="coerce")
    keys = ["tipo_documento", "documento"]
    amounts = (
        history.groupby(keys, as_index=False)
        .agg(
            total_pagado_transaccion=("valor_pago_transaccion", "sum"),
            total_notas_credito_transaccion=(
                "valor_nota_credito_transaccion",
                "sum",
            ),
            cantidad_movimientos=("pago_id", "nunique"),
            cantidad_notas_credito=("es_nota_credito", "sum"),
        )
    )
    positive = history.loc[history["es_pago"] & history["valor_pago_transaccion"].gt(0)].copy()
    by_real_date = positive.sort_values(
        keys + ["fecha_pago", "fecha_contabilizacion", "pago_id"],
        na_position="last",
    )
    first_payment = _first_or_last_payment(by_real_date, "first")
    last_payment = _first_or_last_payment(by_real_date, "last")

    accumulated = positive.merge(
        documents[keys + ["valor_total_transaccion"]],
        on=keys,
        how="inner",
        validate="m:1",
    ).sort_values(keys + ["fecha_pago", "fecha_contabilizacion", "pago_id"])
    accumulated["pago_acumulado_transaccion"] = accumulated.groupby(keys)[
        "valor_pago_transaccion"
    ].cumsum()
    accumulated["tolerancia_total"] = np.maximum(
        1, accumulated["valor_total_transaccion"].abs() * 0.000001
    )
    accumulated["completa_pago"] = accumulated["pago_acumulado_transaccion"].ge(
        accumulated["valor_total_transaccion"] - accumulated["tolerancia_total"]
    )
    full_payment = (
        accumulated.loc[accumulated["completa_pago"]]
        .drop_duplicates(keys, keep="first")
        [keys + ["pago_id", "documento_pago", "fecha_pago"]]
        .rename(
            columns={
                "pago_id": "pago_total_id",
                "documento_pago": "documento_pago_total",
                "fecha_pago": "fecha_pago_total",
            }
        )
    )

    by_accounting = accumulated.sort_values(
        keys + ["fecha_contabilizacion", "fecha_pago", "pago_id"]
    ).copy()
    by_accounting["pago_acumulado_contabilizado"] = by_accounting.groupby(keys)[
        "valor_pago_transaccion"
    ].cumsum()
    by_accounting["completa_pago_contabilizado"] = by_accounting[
        "pago_acumulado_contabilizado"
    ].ge(by_accounting["valor_total_transaccion"] - by_accounting["tolerancia_total"])
    accounting_completion = (
        by_accounting.loc[by_accounting["completa_pago_contabilizado"]]
        .drop_duplicates(keys, keep="first")
        [
            keys
            + ["fecha_contabilizacion", "periodo_corte", "inicio_corte", "fin_corte"]
        ]
        .rename(
            columns={
                "fecha_contabilizacion": "fecha_contabilizacion_saldo_cero",
                "periodo_corte": "periodo_corte_saldo_cero",
                "inicio_corte": "inicio_corte_saldo_cero",
                "fin_corte": "fin_corte_saldo_cero",
            }
        )
    )

    result = (
        documents.merge(amounts, on=keys, how="left", validate="1:1")
        .merge(first_payment, on=keys, how="left", validate="1:1")
        .merge(last_payment, on=keys, how="left", validate="1:1")
        .merge(full_payment, on=keys, how="left", validate="1:1")
        .merge(accounting_completion, on=keys, how="left", validate="1:1")
    )
    for column in (
        "total_pagado_transaccion",
        "total_notas_credito_transaccion",
        "cantidad_movimientos",
        "cantidad_notas_credito",
    ):
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0)
    result["saldo_calculado_transaccion"] = (
        result["valor_total_transaccion"]
        - result["total_pagado_transaccion"]
        - result["total_notas_credito_transaccion"]
    )
    official_invoice_balance = result["tipo_documento"].eq("FACTURA") & result[
        "saldo_netsuite_transaccion"
    ].notna()
    result["saldo_actual_transaccion"] = result["saldo_calculado_transaccion"]
    result.loc[official_invoice_balance, "saldo_actual_transaccion"] = result.loc[
        official_invoice_balance, "saldo_netsuite_transaccion"
    ]
    result["saldo_actual_local"] = (
        result["saldo_actual_transaccion"] * result["tasa_documento"]
    )
    result["tolerancia_saldo"] = np.maximum(
        params.balance_tolerance_minimum,
        result["valor_total_transaccion"].abs() * params.balance_tolerance_rate,
    )
    result["saldo_cero_actual"] = result["saldo_actual_transaccion"].abs().le(
        result["tolerancia_saldo"]
    )
    result["saldo_superior_al_total"] = result["saldo_actual_transaccion"].lt(
        -result["tolerancia_saldo"]
    )
    result["tiene_nota_credito"] = result["total_notas_credito_transaccion"].gt(0)

    result["porcentaje_primer_pago"] = (
        result["valor_primer_pago_transaccion"] / result["valor_total_transaccion"]
    ).replace([np.inf, -np.inf], np.nan)
    result["dias_primer_pago"] = (
        result["fecha_primer_pago"] - result["fecha_vencimiento"]
    ).dt.days
    result["primer_pago_50_o_mas"] = result["porcentaje_primer_pago"].ge(
        params.product_first_payment_threshold
    )
    result["primer_pago_50_en_plazo"] = (
        result["primer_pago_50_o_mas"]
        & result["dias_primer_pago"].notna()
        & result["dias_primer_pago"].le(params.product_first_payment_days)
    )
    result["aplica_regla_producto_50"] = (
        result["es_producto_puro"]
        & result["saldo_cero_actual"]
        & ~result["tiene_nota_credito"]
        & result["primer_pago_50_en_plazo"]
    )

    result["fecha_pago_referencia"] = pd.NaT
    normal_payment = result["saldo_cero_actual"] & result["fecha_pago_total"].notna()
    result.loc[normal_payment, "fecha_pago_referencia"] = result.loc[
        normal_payment, "fecha_pago_total"
    ]
    product_rule = result["aplica_regla_producto_50"]
    result.loc[product_rule, "fecha_pago_referencia"] = result.loc[
        product_rule, "fecha_primer_pago"
    ]
    fallback_date = (
        result["saldo_cero_actual"]
        & result["fecha_pago_referencia"].isna()
        & result["fecha_ultimo_pago"].notna()
        & ~result["tiene_nota_credito"]
    )
    result.loc[fallback_date, "fecha_pago_referencia"] = result.loc[
        fallback_date, "fecha_ultimo_pago"
    ]
    result["origen_fecha_pago"] = pd.Series(
        "NO APLICA - SALDO PENDIENTE", index=result.index, dtype="string"
    )
    result.loc[normal_payment, "origen_fecha_pago"] = "FECHA EN QUE SE COMPLETO EL PAGO"
    result.loc[product_rule, "origen_fecha_pago"] = (
        (
            "PRIMER PAGO DE PRODUCTO IGUAL O SUPERIOR AL "
            f"{params.product_first_payment_threshold:.0%}"
        )
    )
    result.loc[fallback_date, "origen_fecha_pago"] = (
        "ULTIMO PAGO DISPONIBLE - REVISAR HISTORIAL"
    )
    result.loc[result["tiene_nota_credito"], "origen_fecha_pago"] = (
        "DOCUMENTO CON NOTA CREDITO"
    )

    result["fecha_contabilizacion_corte"] = result[
        "fecha_contabilizacion_saldo_cero"
    ]
    result["periodo_corte_pago"] = result["periodo_corte_saldo_cero"]
    fallback_accounting = (
        result["saldo_cero_actual"]
        & result["fecha_contabilizacion_corte"].isna()
        & result["fecha_contabilizacion_ultimo_pago"].notna()
        & ~result["tiene_nota_credito"]
    )
    result.loc[fallback_accounting, "fecha_contabilizacion_corte"] = result.loc[
        fallback_accounting, "fecha_contabilizacion_ultimo_pago"
    ]
    result.loc[fallback_accounting, "periodo_corte_pago"] = result.loc[
        fallback_accounting, "periodo_corte_ultimo_pago"
    ]

    result["dias_pago_referencia"] = (
        result["fecha_pago_referencia"] - result["fecha_vencimiento"]
    ).dt.days
    result["factor_pago_referencia"] = params.payment_factor_late
    result.loc[
        result["dias_pago_referencia"].notna()
        & result["dias_pago_referencia"].le(params.payment_full_days),
        "factor_pago_referencia",
    ] = params.payment_factor_full
    result.loc[
        result["dias_pago_referencia"].gt(params.payment_full_days)
        & result["dias_pago_referencia"].le(params.payment_half_days),
        "factor_pago_referencia",
    ] = params.payment_factor_half

    result["estado_pago_documento"] = "SALDO PENDIENTE"
    result.loc[result["saldo_cero_actual"], "estado_pago_documento"] = "SALDO CERO"
    result.loc[result["saldo_superior_al_total"], "estado_pago_documento"] = (
        "PAGO O APLICACION SUPERIOR AL TOTAL"
    )
    result.loc[result["tiene_nota_credito"], "estado_pago_documento"] = (
        "DOCUMENTO CON NOTA CREDITO"
    )
    result["requiere_revision_pago"] = (
        result["es_producto_mixto"]
        | result["saldo_superior_al_total"]
        | (
            result["saldo_cero_actual"]
            & result["fecha_pago_referencia"].isna()
            & ~result["tiene_nota_credito"]
        )
        | (
            result["saldo_cero_actual"]
            & result["fecha_contabilizacion_corte"].isna()
            & ~result["tiene_nota_credito"]
        )
    )
    return result.sort_values(keys).reset_index(drop=True)


def build_official_payment_base(
    invoices: pd.DataFrame,
    proformas: pd.DataFrame,
    invoice_payments: pd.DataFrame,
    proforma_payments: pd.DataFrame,
    cuts: pd.DataFrame,
    parameters: RuleParameters | None = None,
) -> OfficialPaymentBase:
    documents = build_payment_document_base(invoices, proformas)
    history = build_official_payment_history(invoice_payments, proforma_payments, cuts)
    status = build_payment_status(documents, history, parameters)
    unmatched = (
        history.merge(
            documents[["tipo_documento", "documento"]],
            on=["tipo_documento", "documento"],
            how="left",
            indicator=True,
        )
        .loc[lambda data: data["_merge"].eq("left_only")]
        .drop(columns="_merge")
        .copy()
    )
    history = history.sort_values(
        [
            "tipo_documento",
            "documento",
            "fecha_contabilizacion",
            "fecha_pago",
            "pago_id",
        ]
    ).reset_index(drop=True)
    return OfficialPaymentBase(status, history, unmatched)


def _first_or_last_payment(data: pd.DataFrame, position: str) -> pd.DataFrame:
    keys = ["tipo_documento", "documento"]
    selected = data.drop_duplicates(keys, keep=position)[
        keys
        + [
            "pago_id",
            "documento_pago",
            "fecha_pago",
            "fecha_contabilizacion",
            "valor_pago_transaccion",
            "periodo_corte",
        ]
    ]
    if position == "first":
        return selected.rename(
            columns={
                "pago_id": "primer_pago_id",
                "documento_pago": "primer_documento_pago",
                "fecha_pago": "fecha_primer_pago",
                "fecha_contabilizacion": "fecha_contabilizacion_primer_pago",
                "valor_pago_transaccion": "valor_primer_pago_transaccion",
                "periodo_corte": "periodo_corte_primer_pago",
            }
        )
    return selected.drop(columns="valor_pago_transaccion").rename(
        columns={
            "pago_id": "ultimo_pago_id",
            "documento_pago": "ultimo_documento_pago",
            "fecha_pago": "fecha_ultimo_pago",
            "fecha_contabilizacion": "fecha_contabilizacion_ultimo_pago",
            "periodo_corte": "periodo_corte_ultimo_pago",
        }
    )


def _require_columns(data: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = sorted(set(columns).difference(data.columns))
    if missing:
        raise ValueError(f"{name}: missing required columns: {', '.join(missing)}")
