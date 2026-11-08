"""Aplicacion financiera de excepciones puntuales previamente aprobadas."""

from __future__ import annotations

import pandas as pd

from .commission_rules import calculate_commission_values
from .normalization import normalize_number, normalize_upper
from .rule_parameters import RuleParameters


FLOOR_FORCE_COMPLY = "FLOOR_FORCE_COMPLY"
FLOOR_FORCE_INCLUDE = "FLOOR_FORCE_INCLUDE"
PAYMENT_FORCE_PAY = "PAYMENT_FORCE_PAY"
COMMISSION_VALUE_EXECUTIVE = "COMMISSION_VALUE_EXECUTIVE"
COMMISSION_VALUE_MANAGER = "COMMISSION_VALUE_MANAGER"
COMMISSION_VALUE_DIRECTOR = "COMMISSION_VALUE_DIRECTOR"

DOCUMENT_ACTIONS = {
    FLOOR_FORCE_INCLUDE,
    PAYMENT_FORCE_PAY,
    COMMISSION_VALUE_EXECUTIVE,
    COMMISSION_VALUE_MANAGER,
    COMMISSION_VALUE_DIRECTOR,
}
SUPPORTED_ACTIONS = DOCUMENT_ACTIONS | {FLOOR_FORCE_COMPLY}


def apply_floor_inclusion_exceptions(
    sales_floor: pd.DataFrame, exceptions: pd.DataFrame
) -> pd.DataFrame:
    result = sales_floor.copy()
    for exception in _actions(exceptions, FLOOR_FORCE_INCLUDE):
        mask = _document_mask(result, exception)
        _require_single_match(mask, exception)
        result.loc[mask, "estado_piso"] = "suma"
        result.loc[mask, "TotalParaPiso_final"] = result.loc[
            mask, "TotalParaPiso_base"
        ]
        result.loc[mask, "observacion_piso"] = _reason(exception)
    return result


def apply_floor_compliance_exceptions(
    compliance: pd.DataFrame, exceptions: pd.DataFrame
) -> pd.DataFrame:
    result = compliance.copy()
    periods = result["periodo_mes"].astype("period[M]").astype("string")
    executives = normalize_upper(result["entityid"])
    for exception in _actions(exceptions, FLOOR_FORCE_COMPLY):
        mask = executives.eq(_executive(exception)) & periods.eq(_period(exception))
        if not mask.any():
            raise ValueError("La excepción de piso no coincide con ningún resultado")
        result.loc[mask, "cumple"] = True
        result.loc[mask, "observacion_cumple"] = _reason(exception)
        result.loc[mask, "requiere_revision_cumplimiento"] = False
        result.loc[mask, "motivo_revision_cumplimiento"] = ""
    return result


def apply_payment_exceptions(
    commission_detail: pd.DataFrame,
    exceptions: pd.DataFrame,
    parameters: RuleParameters,
) -> pd.DataFrame:
    result = commission_detail.copy()
    payment_actions = list(_actions(exceptions, PAYMENT_FORCE_PAY))
    for exception in payment_actions:
        mask = _document_mask(result, exception)
        _require_single_match(mask, exception)
        result.loc[mask, "decision_pago"] = "PAGA 100%"
        result.loc[mask, "motivo_pago"] = _reason(exception)
        result.loc[mask, "nota_pago"] = _reason(exception)
        result.loc[mask, "requiere_revision_pago"] = False
    if payment_actions:
        result = calculate_commission_values(result, parameters)

    value_columns = {
        COMMISSION_VALUE_EXECUTIVE: "valor_comision_ejecutivo",
        COMMISSION_VALUE_MANAGER: "valor_comision_jefe",
        COMMISSION_VALUE_DIRECTOR: "valor_comision_gerente",
    }
    for action, column in value_columns.items():
        for exception in _actions(exceptions, action):
            mask = _document_mask(result, exception)
            _require_single_match(mask, exception)
            value = normalize_number(pd.Series([exception["exceptional_result"]])).iloc[0]
            if pd.isna(value) or value < 0:
                raise ValueError("El valor excepcional de comisión debe ser numérico y no negativo")
            result.loc[mask, column] = float(value)
            result.loc[mask, "motivo_pago"] = _reason(exception)
    return result


def _actions(exceptions: pd.DataFrame, action: str) -> list[dict[str, object]]:
    if exceptions.empty:
        return []
    required = {"rule_code", "executive_code", "period", "document", "reason"}
    missing = sorted(required.difference(exceptions.columns))
    if missing:
        raise ValueError("Excepciones sin columnas: " + ", ".join(missing))
    unknown = set(normalize_upper(exceptions["rule_code"]).dropna()) - SUPPORTED_ACTIONS
    if unknown:
        raise ValueError("Acciones de excepción no soportadas: " + ", ".join(sorted(unknown)))
    return exceptions.loc[
        normalize_upper(exceptions["rule_code"]).eq(action)
    ].to_dict(orient="records")


def _document_mask(data: pd.DataFrame, exception: dict[str, object]) -> pd.Series:
    periods = data["periodo_mes"].astype("period[M]").astype("string")
    return (
        normalize_upper(data["entityid"]).eq(_executive(exception))
        & periods.eq(_period(exception))
        & normalize_upper(data["tranId"]).eq(normalize_upper(pd.Series([exception["document"]])).iloc[0])
    )


def _require_single_match(mask: pd.Series, exception: dict[str, object]) -> None:
    if not mask.any():
        raise ValueError(f"La excepción no coincide con el documento {exception['document']}")


def _executive(exception: dict[str, object]) -> str:
    return str(exception["executive_code"]).strip().upper()


def _period(exception: dict[str, object]) -> str:
    return str(exception["period"]).strip()


def _reason(exception: dict[str, object]) -> str:
    return "Excepción aprobada: " + str(exception["reason"]).strip()
