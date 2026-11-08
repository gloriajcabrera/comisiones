from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from src.commission_rules import (
    apply_default_commission_rates,
    calculate_commission_values,
    calculate_payment_decision,
)
from src.credit_effects import apply_credit_effects, calculate_floor_line_status
from src.document_relationships import (
    apply_fallback_replacements,
    attach_credit_notes,
    resolve_explicit_replacements,
)
from src.floor_compliance import COMMISSION_LINE_MAP, build_floor_compliance
from src.exception_rules import (
    apply_floor_compliance_exceptions,
    apply_floor_inclusion_exceptions,
    apply_payment_exceptions,
)
from src.normalization import (
    build_sales_base,
    normalize_date,
    normalize_boolean,
    normalize_id,
    normalize_number,
    normalize_proforma_cancellations,
    normalize_targets,
    normalize_transaction_documents,
    normalize_upper,
)
from src.netsuite_provider import NetSuiteDataProvider
from src.payment_base import OfficialPaymentBase, assign_cut_period, build_official_payment_base
from src.replacement_chains import resolve_replacement_chains, select_available_payment_source
from src.rule_parameters import RuleParameters


@dataclass(frozen=True)
class CommissionInputs:
    invoices: pd.DataFrame
    proformas: pd.DataFrame
    credit_notes: pd.DataFrame
    proforma_cancellations: pd.DataFrame
    invoice_payments: pd.DataFrame
    proforma_payments: pd.DataFrame
    targets: pd.DataFrame
    costs: pd.DataFrame
    cuts: pd.DataFrame
    parameters: RuleParameters = field(default_factory=RuleParameters)
    approved_exceptions: pd.DataFrame | None = None


@dataclass(frozen=True)
class CommissionResult:
    sales_base: pd.DataFrame
    sales_with_credit_notes: pd.DataFrame
    sales_with_origins: pd.DataFrame
    sales_floor: pd.DataFrame
    floor_compliance: pd.DataFrame
    payment_base: OfficialPaymentBase
    commission_detail: pd.DataFrame


def recalculate_cancelled_without_replacement(
    result: CommissionResult,
    targets: pd.DataFrame,
    costs: pd.DataFrame,
    cuts: pd.DataFrame,
    parameters: RuleParameters | None = None,
    approved_exceptions: pd.DataFrame | None = None,
) -> CommissionResult:
    """Recalculate a historical result after later cancellations without replacement."""
    sales_floor = result.sales_floor.copy()
    required = ("requiere_recalculo_piso", "factura_reemplazo_tranId_nc")
    missing = sorted(set(required).difference(sales_floor.columns))
    if missing:
        raise ValueError(
            "sales_floor: missing required columns: " + ", ".join(missing)
        )
    cancelled = (
        normalize_boolean(sales_floor["requiere_recalculo_piso"])
        & sales_floor["factura_reemplazo_tranId_nc"].isna()
    )
    sales_floor.loc[cancelled, "estado_piso"] = "no suma"
    sales_floor.loc[cancelled, "TotalParaPiso_final"] = 0.0
    sales_floor.loc[cancelled, "observacion_piso"] = sales_floor.loc[
        cancelled, "observacion_nc"
    ]

    normalized_targets = normalize_targets(targets)
    compliance = build_floor_compliance(sales_floor, normalized_targets, costs)
    exception_data = (
        approved_exceptions if approved_exceptions is not None else pd.DataFrame()
    )
    compliance = apply_floor_compliance_exceptions(compliance, exception_data)
    commission_detail = _assemble_commission_detail(
        sales_floor,
        compliance,
        result.payment_base.documents,
        normalized_targets,
        cuts,
        parameters,
    )
    commission_detail = apply_payment_exceptions(
        commission_detail,
        exception_data,
        (parameters or RuleParameters()).validate(),
    )
    return replace(
        result,
        sales_floor=sales_floor,
        floor_compliance=compliance,
        commission_detail=commission_detail,
    )


def calculate_from_provider(
    provider: NetSuiteDataProvider,
    targets: pd.DataFrame,
    cuts: pd.DataFrame,
    parameters: RuleParameters | None = None,
) -> CommissionResult:
    """Calculate from a synchronized provider without querying NetSuite directly."""
    dataset = provider.load_dataset()
    return calculate_commissions(
        CommissionInputs(
            invoices=dataset.invoices,
            proformas=dataset.proformas,
            credit_notes=dataset.credit_notes,
            proforma_cancellations=dataset.proforma_cancellations,
            invoice_payments=dataset.invoice_payments,
            proforma_payments=dataset.proforma_payments,
            targets=targets,
            costs=dataset.costs,
            cuts=cuts,
            parameters=parameters or RuleParameters(),
        )
    )


def calculate_commissions(inputs: CommissionInputs) -> CommissionResult:
    """Run the extracted calculation stages without notebook global state."""
    invoices = normalize_transaction_documents(inputs.invoices)
    params = inputs.parameters.validate()
    proformas = normalize_transaction_documents(inputs.proformas)
    if not params.include_proformas:
        proformas = proformas.iloc[0:0].copy()
    credit_notes = normalize_transaction_documents(inputs.credit_notes)
    targets = normalize_targets(inputs.targets)
    costs = normalize_transaction_documents(inputs.costs)
    proforma_cancellations = normalize_proforma_cancellations(
        inputs.proforma_cancellations
    )

    sales_base = build_sales_base(invoices, proformas)
    sales_with_notes = attach_credit_notes(sales_base, credit_notes)
    explicit = resolve_explicit_replacements(sales_with_notes, credit_notes)
    sales_with_origins = apply_fallback_replacements(explicit, inputs.invoice_payments)
    affected = apply_credit_effects(sales_with_origins, proforma_cancellations)
    relationship_floor = calculate_floor_line_status(affected)
    approved_exceptions = (
        inputs.approved_exceptions
        if inputs.approved_exceptions is not None
        else pd.DataFrame()
    )
    relationship_floor = apply_floor_inclusion_exceptions(
        relationship_floor, approved_exceptions
    )
    sales_floor = _eligible_commission_rows(relationship_floor)
    compliance = build_floor_compliance(sales_floor, targets, costs)
    compliance = apply_floor_compliance_exceptions(compliance, approved_exceptions)
    payments = build_official_payment_base(
        invoices,
        proformas,
        inputs.invoice_payments,
        inputs.proforma_payments,
        inputs.cuts,
        params,
    )
    commission_detail = _assemble_commission_detail(
        relationship_floor,
        compliance,
        payments.documents,
        targets,
        inputs.cuts,
        params,
    )
    commission_detail = apply_payment_exceptions(
        commission_detail, approved_exceptions, params
    )
    return CommissionResult(
        sales_base=sales_base,
        sales_with_credit_notes=sales_with_notes,
        sales_with_origins=sales_with_origins,
        sales_floor=sales_floor,
        floor_compliance=compliance,
        payment_base=payments,
        commission_detail=commission_detail,
    )


def _assemble_commission_detail(
    sales_floor: pd.DataFrame,
    compliance: pd.DataFrame,
    payment_documents: pd.DataFrame,
    targets: pd.DataFrame,
    cuts: pd.DataFrame,
    parameters: RuleParameters | None = None,
) -> pd.DataFrame:
    params = (parameters or RuleParameters()).validate()
    # Header and auxiliary lines can carry replacement metadata even though
    # they must not appear in financial results.
    result = _eligible_commission_rows(resolve_replacement_chains(sales_floor))
    line_map = pd.DataFrame(
        {
            "custitem_sm_lineacomision_key": list(COMMISSION_LINE_MAP),
            "linea_comision_key": list(COMMISSION_LINE_MAP.values()),
        }
    )
    result["ejecutivo_key"] = normalize_upper(result["entityid"])
    result["custitem_sm_lineacomision_key"] = normalize_id(
        result["custitem_sm_lineacomision"]
    )
    result = result.merge(
        line_map,
        on="custitem_sm_lineacomision_key",
        how="left",
        validate="m:1",
    )

    payment_projection = payment_documents.copy()
    payment_projection["Documento_pago_key"] = normalize_upper(
        payment_projection["documento"]
    )
    payment_projection["Fecha Real Pago"] = normalize_date(
        payment_projection["fecha_pago_referencia"]
    )
    payment_projection["Fecha Contabilizado Pago"] = normalize_date(
        payment_projection["fecha_contabilizacion_corte"]
    )
    payment_projection["Fecha de pago"] = payment_projection[
        "Fecha Real Pago"
    ].combine_first(payment_projection["Fecha Contabilizado Pago"])
    payment_projection["Saldo"] = normalize_number(
        payment_projection["saldo_actual_local"]
    )
    payment_projection["Pagado"] = (
        normalize_number(payment_projection["total_pagado_transaccion"]).fillna(0)
        + normalize_number(
            payment_projection["total_notas_credito_transaccion"]
        ).fillna(0)
    ) * normalize_number(payment_projection["tasa_documento"]).fillna(1)
    payment_projection["Pagado Con (Tipo)"] = payment_projection[
        "estado_pago_documento"
    ]
    payment_projection["Pagado Con:"] = payment_projection["ultimo_documento_pago"]
    payment_projection["tiene_nota_credito_pago"] = payment_projection[
        "tiene_nota_credito"
    ]
    payment_projection["saldo_cero_pago"] = payment_projection["saldo_cero_actual"]
    payment_projection["saldo_superior_pago"] = payment_projection[
        "saldo_superior_al_total"
    ]
    payment_projection["revision_pago_fuente"] = payment_projection[
        "requiere_revision_pago"
    ]
    payment_columns = [
        "Documento_pago_key",
        "Fecha Real Pago",
        "Fecha Contabilizado Pago",
        "Fecha de pago",
        "Pagado",
        "Saldo",
        "Pagado Con (Tipo)",
        "Pagado Con:",
        "es_producto_puro",
        "es_producto_mixto",
        "porcentaje_primer_pago",
        "fecha_primer_pago",
        "fecha_pago_total",
        "dias_primer_pago",
        "primer_pago_50_o_mas",
        "primer_pago_50_en_plazo",
        "aplica_regla_producto_50",
        "origen_fecha_pago",
        "tiene_nota_credito_pago",
        "saldo_cero_pago",
        "saldo_superior_pago",
        "revision_pago_fuente",
    ]
    payment_projection = payment_projection[payment_columns].drop_duplicates(
        "Documento_pago_key", keep="last"
    )
    result = select_available_payment_source(
        result, payment_projection["Documento_pago_key"]
    )
    result = result.merge(
        payment_projection,
        left_on="doc_fuente_pago_key",
        right_on="Documento_pago_key",
        how="left",
        validate="m:1",
    )

    compliance_key = compliance.copy()
    compliance_key["periodo_mes"] = compliance_key["periodo_mes"].astype("period[M]")
    compliance_key["ejecutivo_key"] = normalize_upper(compliance_key["entityid"])
    compliance_key["linea_comision_key"] = normalize_upper(
        compliance_key["linea_comision"]
    )
    compliance_columns = [
        "periodo_mes",
        "ejecutivo_key",
        "linea_comision_key",
        "TotalParaPiso",
        "costo",
        "meta",
        "piso",
        "cumple",
        "observacion_cumple",
        "habilitada_por_retie",
        "habilitada_por_linea_principal",
        "lineas_principales_cumplidas",
        "requiere_revision_cumplimiento",
        "motivo_revision_cumplimiento",
    ]
    result = result.merge(
        compliance_key[compliance_columns].drop_duplicates(
            ["periodo_mes", "ejecutivo_key", "linea_comision_key"], keep="last"
        ),
        on=["periodo_mes", "ejecutivo_key", "linea_comision_key"],
        how="left",
        validate="m:1",
    )
    result["sin_resultado_cumplimiento"] = (
        result["cumple"].isna()
        & result["observacion_cumple"].isna()
        & result["requiere_revision_cumplimiento"].isna()
    )

    config = targets.copy()
    config["periodo_mes"] = normalize_date(config["periodo"]).dt.to_period("M")
    config["ejecutivo_key"] = normalize_upper(config["ejecutivo"])
    config["linea_comision_key"] = normalize_upper(config["linea_comision"])
    config["configuracion_comision_encontrada"] = True
    config_keys = ["periodo_mes", "ejecutivo_key", "linea_comision_key"]
    if config.duplicated(config_keys).any():
        raise ValueError(
            "Hay duplicados en configuración de comisiones por periodo+ejecutivo+linea."
        )
    config_columns = config_keys + [
        column
        for column in (
            "jefe",
            "comision_ejecutivo",
            "comision_jefe",
            "gerente",
            "comision_gerente",
            "configuracion_comision_encontrada",
        )
        if column in config.columns
    ]
    result = result.merge(
        config[config_columns], on=config_keys, how="left", validate="m:1"
    )
    result = apply_default_commission_rates(result, params)

    result["Fecha Real Pago"] = normalize_date(result["Fecha Real Pago"])
    result["Fecha Contabilizado Pago"] = normalize_date(
        result["Fecha Contabilizado Pago"]
    )
    cut_assignment = assign_cut_period(
        result[["Fecha Contabilizado Pago"]], "Fecha Contabilizado Pago", cuts
    )
    result["Inicio Corte Contable"] = cut_assignment["inicio_corte"]
    result["Fin Corte Contable"] = cut_assignment["fin_corte"]
    result["Periodo Corte"] = cut_assignment["periodo_corte"]
    result["Cumple Corte Contable"] = result["Periodo Corte"].notna()
    result["dias_pago"] = (
        result["Fecha Real Pago"] - result["dueDate_base_pago"]
    ).dt.days
    result["factor"] = np.select(
        [
            result["dias_pago"].le(params.payment_full_days),
            result["dias_pago"].gt(params.payment_full_days)
            & result["dias_pago"].le(params.payment_half_days),
            result["dias_pago"].gt(params.payment_half_days),
        ],
        [
            params.payment_factor_full,
            params.payment_factor_half,
            params.payment_factor_late,
        ],
        default=np.nan,
    )
    result["nota_pago"] = np.select(
        [
            result["dias_pago"].le(params.payment_full_days),
            result["dias_pago"].gt(params.payment_full_days)
            & result["dias_pago"].le(params.payment_half_days),
            result["dias_pago"].gt(params.payment_half_days),
        ],
        [
            f"Pago <= {params.payment_full_days} días desde dueDate",
            (
                f"Pago entre {params.payment_full_days + 1} y "
                f"{params.payment_half_days} días desde dueDate"
            ),
            f"Pago > {params.payment_half_days} días desde dueDate",
        ],
        default="Sin cálculo de días de pago",
    )
    result = calculate_payment_decision(result, params)
    return calculate_commission_values(result, params)


def _eligible_commission_rows(data: pd.DataFrame) -> pd.DataFrame:
    if "elegible_comision" not in data.columns:
        return data.copy()
    return data.loc[normalize_boolean(data["elegible_comision"])].copy()
