from __future__ import annotations

import numpy as np
import pandas as pd

from src.normalization import normalize_boolean, normalize_date, normalize_number, normalize_upper
from src.rule_parameters import RuleParameters


def apply_default_commission_rates(
    data: pd.DataFrame, parameters: RuleParameters | None = None
) -> pd.DataFrame:
    result = data.copy()
    params = (parameters or RuleParameters()).validate()
    rates = {
        "comision_ejecutivo": params.default_commission_executive,
        "comision_jefe": params.default_commission_manager,
        "comision_gerente": params.default_commission_director,
    }
    for column, default in rates.items():
        if column not in result.columns:
            result[column] = default
        else:
            result[column] = normalize_number(result[column]).fillna(default)
    return result


def calculate_payment_decision(
    data: pd.DataFrame, parameters: RuleParameters | None = None
) -> pd.DataFrame:
    """Apply current review/no-pay/pay priority to assembled commission rows."""
    required = (
        "estado_piso",
        "cumple",
        "saldo_cero_pago",
        "Saldo",
        "reemplazo",
        "requiere_revision_cumplimiento",
        "ciclo_reemplazo",
        "linea_comision_key",
        "sin_resultado_cumplimiento",
        "dueDate_base_pago",
        "Fecha Real Pago",
        "Fecha Contabilizado Pago",
        "Periodo Corte",
        "es_documento_comision",
        "reemplazo_trazable_pago",
        "factor",
        "cadena_rota_mismo_dia",
        "es_producto_mixto",
        "tiene_nota_credito_pago",
        "saldo_superior_pago",
        "revision_pago_fuente",
    )
    _require_columns(data, required, "payment_decision")
    params = (parameters or RuleParameters()).validate()
    result = data.copy()
    result["Fecha Real Pago"] = normalize_date(result["Fecha Real Pago"])
    result["Fecha Contabilizado Pago"] = normalize_date(
        result["Fecha Contabilizado Pago"]
    )
    result["Saldo"] = normalize_number(result["Saldo"])
    result["factor"] = normalize_number(result["factor"])
    state = result["estado_piso"].astype("string").str.strip().str.lower()
    complies = normalize_boolean(result["cumple"])
    zero_balance = normalize_boolean(result["saldo_cero_pago"])
    replacement_traceable = normalize_boolean(result["reemplazo_trazable_pago"])

    no_payment = (
        result["Fecha Real Pago"].isna()
        & result["Fecha Contabilizado Pago"].isna()
        & ~zero_balance
    )
    open_balance = result["Saldo"].notna() & result["Saldo"].ne(0)
    factor_100 = result["factor"].eq(params.payment_factor_full)
    factor_50 = result["factor"].eq(params.payment_factor_half)
    factor_zero = result["factor"].eq(params.payment_factor_late)
    if "nota_pago" not in result.columns:
        result["nota_pago"] = "Sin cálculo de días de pago"
    result.loc[no_payment, "nota_pago"] = "Pendiente recibir pago de la factura"
    result.loc[open_balance & ~no_payment, "nota_pago"] = (
        "Pendiente recibir pago total de la factura"
    )
    result.loc[factor_zero, "nota_pago"] = (
        "Pago recibido fuera de plazo: comisión en cero"
    )

    masks = [
        normalize_boolean(result["cadena_rota_mismo_dia"]),
        normalize_boolean(result["requiere_revision_cumplimiento"]),
        normalize_boolean(result["ciclo_reemplazo"]),
        normalize_boolean(result["tiene_nota_credito_pago"]),
        normalize_boolean(result["es_producto_mixto"]),
        normalize_boolean(result["saldo_superior_pago"]),
        normalize_boolean(result["revision_pago_fuente"]),
        result["linea_comision_key"].isna(),
        normalize_boolean(result["sin_resultado_cumplimiento"]),
        state.eq("para revision"),
        result["dueDate_base_pago"].isna()
        & (result["Fecha Real Pago"].notna() | result["Fecha Contabilizado Pago"].notna()),
        result["Fecha Real Pago"].isna() & result["Fecha Contabilizado Pago"].notna(),
        result["Fecha Contabilizado Pago"].isna() & result["Fecha Real Pago"].notna(),
        result["Fecha Contabilizado Pago"].notna() & result["Periodo Corte"].isna(),
        zero_balance
        & result["Fecha Real Pago"].isna()
        & result["Fecha Contabilizado Pago"].isna(),
        ~normalize_boolean(result["es_documento_comision"]),
        state.eq("no suma") & ~replacement_traceable,
        ~complies,
        no_payment,
        open_balance,
        factor_100,
        factor_50,
        factor_zero,
    ]
    decisions = [
        "NO PAGA",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "PARA REVISAR",
        "NO PAGA",
        "NO PAGA",
        "NO PAGA",
        "NO PAGA",
        "NO PAGA",
        "PAGA 100%",
        "PAGA 50%",
        "PAGO EN CERO",
    ]
    reasons = [
        "Documento original anulado con nota crédito el mismo día; la cadena inicia en el reemplazo",
        _text_fallback(
            result,
            "motivo_revision_cumplimiento",
            "observacion_cumple",
            "Revisión de cumplimiento",
        ),
        "Ciclo en cadena de reemplazos: "
        + _text_column(result, "documentos_ciclo_reemplazo", "sin detalle"),
        "Documento con nota crédito; no se paga automáticamente y debe validarse su efecto",
        "Documento mixto con PRODUCTO; requiere validación manual de la regla de pago",
        "Pago o aplicación superior al total del documento",
        "La base oficial de pagos marcó el documento para revisión",
        "Sin mapeo de línea de comisión",
        "Sin resultado de cumplimiento para periodo + ejecutivo + línea",
        _text_column(result, "observacion_piso", "Estado piso en revisión"),
        "Sin dueDate base para medir días de pago",
        "Hay Fecha Contabilizado Pago pero falta Fecha Real Pago",
        "Hay Fecha Real Pago pero falta Fecha Contabilizado Pago",
        "La Fecha Contabilizado Pago quedó fuera de la tabla de cortes",
        "Saldo 0 pero sin fechas de pago; validar soporte",
        "Documento no final de la cadena; la comisión se calcula sobre el último reemplazo pagado",
        _text_column(result, "observacion_piso", "No suma para piso"),
        _text_column(result, "observacion_cumple", "No cumplió piso del mes"),
        "Pendiente recibir pago de la factura",
        "Pendiente recibir pago total de la factura",
        f"Cumple pago <= {params.payment_full_days} días desde dueDate",
        (
            f"Cumple pago entre {params.payment_full_days + 1} y "
            f"{params.payment_half_days} días desde dueDate"
        ),
        (
            f"Pago recibido después de {params.payment_half_days} días "
            "desde dueDate: comisión en cero"
        ),
    ]
    result["decision_pago"] = "PARA REVISAR"
    result["motivo_pago"] = "Validar caso manualmente"
    # Reverse assignment preserves np.select's first-match priority.
    for mask, decision, reason in reversed(list(zip(masks, decisions, reasons))):
        result.loc[mask, "decision_pago"] = decision
        if isinstance(reason, pd.Series):
            result.loc[mask, "motivo_pago"] = reason.loc[mask]
        else:
            result.loc[mask, "motivo_pago"] = reason

    line_na = result["linea_comision_key"].eq("NA")
    result.loc[line_na, "decision_pago"] = "PAGO EN CERO"
    result.loc[line_na, "motivo_pago"] = (
        "Línea NA no comisionable: valor de comisión en cero"
    )
    result.loc[line_na, "nota_pago"] = (
        "Línea NA: suma para piso, pero no genera comisión"
    )
    if "elegible_comision" in result.columns:
        ineligible = ~normalize_boolean(result["elegible_comision"])
        eligibility_reason = _text_column(
            result,
            "motivo_elegibilidad",
            "Documento fuera del universo comisionable",
        )
        result.loc[ineligible, "decision_pago"] = "NO APLICA"
        result.loc[ineligible, "motivo_pago"] = eligibility_reason.loc[ineligible]
        result.loc[ineligible, "nota_pago"] = (
            "La línea se conserva para trazabilidad y no genera comisión"
        )
    result["requiere_revision_pago"] = result["decision_pago"].eq("PARA REVISAR")
    return result


def calculate_commission_values(
    data: pd.DataFrame, parameters: RuleParameters | None = None
) -> pd.DataFrame:
    """Apply payment timing and subcontracting factors to all three roles."""
    _require_columns(data, ("decision_pago", "cc", "Valor"), "commission_values")
    params = (parameters or RuleParameters()).validate()
    result = apply_default_commission_rates(data, params)
    result["factor_pago_comision"] = np.select(
        [
            result["decision_pago"].eq("PAGA 100%"),
            result["decision_pago"].eq("PAGA 50%"),
            result["decision_pago"].isin(
                ["NO PAGA", "PAGO EN CERO", "NO APLICA"]
            ),
        ],
        [
            params.payment_factor_full,
            params.payment_factor_half,
            params.payment_factor_late,
        ],
        default=np.nan,
    )
    cost_center = normalize_upper(result["cc"]).fillna("")
    result["es_subcontratado"] = cost_center.str.contains(
        "SUBCONTRAT", regex=False, na=False
    )
    result["factor_subcontratado"] = np.where(
        result["es_subcontratado"], params.subcontract_factor, 1.0
    )
    result["observacion_subcontratado"] = np.where(
        result["es_subcontratado"],
        "Centro de costo subcontratado: comisión reducida a la mitad",
        "Centro de costo normal: comisión completa",
    )
    result["factor_comision_final"] = (
        result["factor_subcontratado"] * result["factor_pago_comision"]
    )
    result["Valor"] = normalize_number(result["Valor"])
    for role in ("ejecutivo", "jefe", "gerente"):
        rate = f"comision_{role}"
        result[rate] = normalize_number(result[rate])
        result[f"porcentaje_base_{role}"] = result[rate] * 100
        result[f"porcentaje_comision_{role}"] = (
            result[rate] * result["factor_comision_final"] * 100
        )
        result[f"valor_comision_{role}"] = (
            result["Valor"] * result[rate] * result["factor_comision_final"]
        )
    return result


def _text_column(data: pd.DataFrame, column: str, fallback: str) -> pd.Series:
    if column not in data.columns:
        return pd.Series(fallback, index=data.index, dtype="string")
    return data[column].astype("string").fillna(fallback)


def _text_fallback(
    data: pd.DataFrame,
    primary: str,
    secondary: str,
    fallback: str,
) -> pd.Series:
    if primary in data.columns:
        primary_values = data[primary].astype("string")
    else:
        primary_values = pd.Series(pd.NA, index=data.index, dtype="string")
    if secondary in data.columns:
        secondary_values = data[secondary].astype("string")
    else:
        secondary_values = pd.Series(pd.NA, index=data.index, dtype="string")
    return primary_values.fillna(secondary_values).fillna(fallback)


def _require_columns(data: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = sorted(set(columns).difference(data.columns))
    if missing:
        raise ValueError(f"{name}: missing required columns: {', '.join(missing)}")
