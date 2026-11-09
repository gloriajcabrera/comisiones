"""Interfaz de consulta sobre servicios canónicos, sin archivos intermedios."""

from __future__ import annotations

import os
from datetime import date
from html import escape

import pandas as pd
import streamlit as st

from src.engine import CommissionResult
from src.business_periods import normalize_period
from src.observability import configure_logging
from src.payment_base import OfficialPaymentBase
from src.reporting import InMemoryCalculationResultProvider
from src.access_control import (
    PERM_CALCULATION_APPROVE,
    PERM_CALCULATION_EXECUTE,
    PERM_NETSUITE_SYNC,
    PERM_PAYMENT_FILE_SEND,
    REL_EXECUTIVE_MANAGER,
    REL_MANAGER_DIRECTOR,
    ROLE_ADMIN,
    ROLE_DIRECTOR,
    ROLE_EXECUTIVE,
    ROLE_MANAGER,
    ROLE_PARAMETERIZER,
    ROLE_REVIEWER,
)
from src.exception_rules import (
    COMMISSION_VALUE_EXECUTIVE,
    FLOOR_FORCE_COMPLY,
    FLOOR_FORCE_INCLUDE,
    PAYMENT_FORCE_PAY,
)
from src.review_workflows import (
    CLOSED,
    EXCEPTION_FLOOR,
    EXCEPTION_PAYMENT,
    IN_REVIEW,
    PENDING_APPROVAL,
    REQUEST_APPROVED,
    REQUEST_REJECTED,
    RESOLVED,
)
from src.input_store import load_dataframe_from_bytes


RUN_CODE = "demo-2026-09"

ROLE_PROFILES = {
    ROLE_EXECUTIVE: "Ejecutivo",
    ROLE_MANAGER: "Jefe de ventas",
    ROLE_DIRECTOR: "Gerente",
    ROLE_REVIEWER: "Revisor",
    ROLE_PARAMETERIZER: "Parametrizador",
    ROLE_ADMIN: "Administrador",
}

COMMISSION_LINES = [
    "RETIE/LAP", "RITEL", "ENERGIA", "PRODUCTO", "MACROMEDIDORES",
    "ASCENSORES Y ESCALERAS", "AGUA", "SISTEMAS DE GESTION", "NA",
]
PERIOD_HELP = "Use el formato AAAA-MM. Ejemplo: 2026-09 para septiembre de 2026."


def _valid_period(value: object) -> bool:
    try:
        normalize_period(str(value))
    except ValueError:
        return False
    return True

PROFILE_NAVIGATION = {
    "Ejecutivo": ["Inicio", "Cumplimiento de piso", "Pagos", "Descuentos", "Solicitudes"],
    "Jefe de ventas": [
        "Inicio", "Cumplimiento de piso", "Pagos", "Descuentos", "Ejecutivos a cargo", "Pisos y metas", "Solicitudes",
    ],
    "Gerente": ["Inicio", "Cumplimiento de piso", "Pagos", "Descuentos", "Solicitudes"],
    "Revisor": [
        "Inicio", "Cumplimiento", "Pagos", "Descuentos", "Revisiones", "Pisos y metas",
        "Insumos y cortes", "Excepciones", "Ejecuciones de cálculo", "Cierre y archivo",
    ],
    "Parametrizador": [
        "Panel de parametrización", "Parámetros", "Aprobaciones", "Pisos y metas",
        "Excepciones", "Ejecuciones de cálculo", "Cierre y archivo", "Cumplimiento", "Pagos", "Descuentos", "Revisiones",
    ],
    "Administrador": [
        "Inicio", "Cumplimiento", "Pagos", "Descuentos", "Revisiones", "Pisos y metas",
        "Excepciones", "Parámetros", "Aprobaciones", "Periodos", "Usuarios", "Auditoría",
    ],
}

MANAGER_MENUS = {
    "Consultas": ["Inicio", "Cumplimiento de piso", "Pagos", "Descuentos"],
    "Gestión": ["Ejecutivos a cargo", "Pisos y metas", "Solicitudes"],
}

PARAMETERIZER_MENUS = {
    "Parametrización": ["Panel de parametrización", "Parámetros", "Aprobaciones"],
    "Gestión": ["Insumos y cortes", "Pisos y metas", "Excepciones", "Ejecuciones de cálculo", "Cierre y archivo"],
    "Consultas": ["Inicio", "Cumplimiento de piso", "Pagos", "Descuentos", "Revisiones"],
}

ADMIN_MENUS = {
    "Administración": [
        "Panel de administración", "Usuarios", "Periodos",
        "Ejecuciones de cálculo", "Auditoría",
    ],
    "Configuración": ["Parámetros", "Aprobaciones", "Insumos y cortes", "Pisos y metas", "Excepciones"],
    "Gestión": ["Cierre y archivo"],
    "Consultas": ["Inicio", "Cumplimiento de piso", "Pagos", "Descuentos", "Revisiones"],
}

PROFILE_MENUS = {
    "Ejecutivo": {
        "Consultas": ["Inicio", "Cumplimiento de piso", "Pagos", "Descuentos"],
        "Gestión": ["Solicitudes"],
    },
    "Jefe de ventas": MANAGER_MENUS,
    "Gerente": {
        "Consultas": ["Inicio", "Cumplimiento de piso", "Pagos", "Descuentos"],
        "Gestión": ["Solicitudes"],
    },
    "Revisor": {
        "Consultas": ["Inicio", "Cumplimiento", "Pagos", "Descuentos"],
        "Gestión": ["Revisiones", "Insumos y cortes", "Excepciones", "Ejecuciones de cálculo", "Cierre y archivo"],
    },
    "Parametrizador": PARAMETERIZER_MENUS,
    "Administrador": ADMIN_MENUS,
}


def _menus_for_profile(
    profile: str, permissions: set[str] | None = None
) -> dict[str, list[str]]:
    menus = {
        category: list(pages)
        for category, pages in PROFILE_MENUS[profile].items()
    }
    if PERM_NETSUITE_SYNC in (permissions or set()):
        category = "Administración" if profile == "Administrador" else "Gestión"
        menus.setdefault(category, []).append("Sincronización NetSuite")
    return menus

PROFILE_MENU_KEYS = {
    "Ejecutivo": "executive_menu",
    "Jefe de ventas": "manager_menu",
    "Gerente": "director_menu",
    "Revisor": "reviewer_menu",
    "Parametrizador": "parameterizer_menu",
    "Administrador": "admin_menu",
}

PROFILE_SCOPE = {
    "Ejecutivo": {"EJ-01"},
    "Jefe de ventas": {"EJ-01", "EJ-02"},
    "Gerente": {"EJ-01", "EJ-02", "EJ-03"},
}

COLUMN_LABELS = {
    "issue_type": "Tipo de pendiente",
    "review_reason": "Qué debe revisarse",
    "affected_lines": "Líneas afectadas",
    "document": "Documento",
    "document_date": "Fecha documento",
    "executive": "Ejecutivo",
    "executive_name": "Nombre del ejecutivo",
    "period": "Periodo",
    "commission_line": "Línea",
    "decision": "Resultado",
    "commission_executive": "Comisión ejecutivo (moneda local)",
    "occurred_at": "Fecha y hora",
    "actor_user_id": "Usuario",
    "action": "Acción",
    "entity_type": "Tipo de entidad",
    "entity_id": "Registro",
    "previous_value": "Valor anterior",
    "new_value": "Valor nuevo",
    "reason": "Motivo o resultado",
    "commission_manager": "Comisión jefe (moneda local)",
    "commission_director": "Comisión gerente (moneda local)",
    "document_value": "Valor documento (moneda local)",
    "payment_date": "Fecha real de pago",
    "accounting_date": "Fecha contabilización",
    "payment_reason": "Motivo",
    "payment_document": "Documento de pago",
    "payment_type": "Tipo de pago",
    "paid_value": "Valor pagado (moneda local)",
    "complies": "Cumple",
    "result_value": "Valor obtenido (moneda local)",
    "goal": "Meta (moneda local)",
    "floor": "Piso (moneda local)",
    "concept": "Concepto",
    "commission": "Comisiones (moneda local)",
    "manager_commission": "Comisión jefe (moneda local)",
    "director_commission": "Comisión gerente (moneda local)",
    "discount": "Descuentos (moneda local)",
    "discounts": "Descuentos (moneda local)",
    "payable": "Valor a pagar (moneda local)",
    "documents": "Documentos",
    "document_origin": "Documento origen",
    "document_origin_date": "Fecha documento origen",
    "credit_note": "Nota crédito",
    "credit_note_date": "Fecha nota crédito",
    "replacement_document": "Documento reemplazo",
    "replacement_document_date": "Fecha documento reemplazo",
    "adds_to_floor": "Suma al piso",
    "floor_reason": "Motivo suma/no suma",
    "lot": "Lote",
    "service": "Servicio",
    "payment_days": "Días de pago",
    "payment_factor": "Factor",
    "commission_rate": "% comisión",
    "manager": "Jefe",
    "manager_name": "Nombre del jefe",
    "director": "Gerente",
    "director_name": "Nombre del gerente",
    "status": "Estado",
    "request_id": "Solicitud",
    "request_status": "Estado de solicitud",
    "request_final_concept": "Concepto final",
    "discount_id": "Descuento",
    "detection_period": "Periodo de detección",
    "cancelled_documents": "Facturas anuladas",
    "credit_notes": "Notas crédito",
    "commission_lines": "Líneas afectadas",
    "paid_documents": "Facturas pagadas afectadas",
    "total_debt": "Descuento total (moneda local)",
    "applied_amount": "Valor aplicado (moneda local)",
    "balance": "Saldo pendiente (moneda local)",
}


def _demo_provider() -> InMemoryCalculationResultProvider:
    rows = [
        ("SM72210", "EJ-01", "2026-08", "RETIE/LAP", "PAGA 100%", 370000, "Pago completo"),
        ("SM71824", "EJ-01", "2026-08", "RETIE/LAP", "PAGA 100%", 248000, "Pago de reemplazo"),
        ("PR-9553", "EJ-02", "2026-09", "RITEL", "PAGA 100%", 182000, "RC-019486"),
        ("PR-9551", "EJ-02", "2026-09", "RETIE/LAP", "PENDIENTE", pd.NA, "Pendiente recibir pago"),
        ("SM71487", "EJ-03", "2026-07", "ENERGIA", "PAGA 100%", 154000, "Tasa por defecto"),
        ("SM71632", "EJ-03", "2026-07", "NA", "NO PAGA", 0, "Línea NA al 0%"),
        ("SM65917", "EJ-04", "2026-08", "MACROMEDIDORES", "NO PAGA", pd.NA, "Anulada NC5247"),
        ("SM72582", "EJ-04", "2026-08", "MACROMEDIDORES", "PAGA 50%", 96000, "Reemplazo trazable"),
        ("SM73590", "EJ-02", "2026-09", "RETIE/LAP", "PENDIENTE", pd.NA, "Pendiente recibir pago"),
    ]
    detail = pd.DataFrame(
        rows,
        columns=[
            "tranId", "entityid", "periodo_mes", "linea_comision_key",
            "decision_pago", "valor_comision_ejecutivo", "motivo_pago",
        ],
    )
    detail["valor_comision_jefe"] = detail["valor_comision_ejecutivo"] * 0.25
    detail["valor_comision_gerente"] = detail["valor_comision_ejecutivo"] * 0.50
    detail["valor_total_documento"] = [9250000, 6200000, 4550000, 3200000, 3850000, 1800000, 3284400, 3284400, 3200000]
    detail["valor_moneda_local"] = detail["valor_total_documento"]
    detail["Fecha Real Pago"] = pd.to_datetime(
        ["2026-08-24", "2026-08-18", "2026-09-15", None, "2026-07-29", None, None, "2026-08-30", None]
    )
    detail["Fecha Contabilizado Pago"] = pd.to_datetime(
        ["2026-08-25", "2026-08-18", "2026-09-18", None, "2026-07-30", None, None, "2026-08-31", None]
    )
    floor = pd.DataFrame(
        {
            "entityid": ["EJ-01", "EJ-02", "EJ-02", "EJ-03", "EJ-04"],
            "periodo_mes": ["2026-08", "2026-09", "2026-09", "2026-07", "2026-08"],
            "linea_comision": ["RETIE/LAP", "RETIE/LAP", "RITEL", "ENERGIA", "MACROMEDIDORES"],
            "cumple": [True, True, False, True, True],
            "TotalParaPiso": [15450000, 7200000, 4550000, 5650000, 6568800],
            "meta": [14000000, 7000000, 5000000, 5000000, 6000000],
            "piso": [12000000, 6000000, 4500000, 4500000, 5500000],
            "observacion_cumple": ["Cumple", "Cumple", "No alcanza meta", "Cumple", "Cumple"],
        }
    )
    documents = pd.DataFrame(
        {
            "tranId": detail["tranId"],
            "documento_origen": ["SM72209", "SM60283", "PR-9553", "PR-9551", "SM71487", "SM71632", "SM65917", "SM65917", "PR-9551"],
            "tranId_nc": ["NC5226", pd.NA, pd.NA, "NC5317", pd.NA, pd.NA, "NC5247", "NC5247", "NC5317"],
            "factura_reemplazo_tranId_nc": ["SM72210", "SM71824", "SM72586", "SM73590", pd.NA, pd.NA, "SM72582", "SM72582", "SM73590"],
            "entityid": detail["entityid"],
            "periodo_mes": detail["periodo_mes"],
            "linea_comision_key": detail["linea_comision_key"],
            "lote": ["L-2209", "L-60283", "L-9553", "L-9551", "L-71487", "L-71632", "L-65917", "L-72582", "L-73590"],
            "Servicio": ["Inspección RETIE", "Inspección RETIE", "Inspección RITEL", "Inspección RETIE", "Consultoría energética", "Servicio no comisionable", "Calibración", "Calibración", "Inspección RETIE"],
            "valor_total_documento": detail["valor_total_documento"],
            "valor_moneda_local": detail["valor_moneda_local"],
            "estado_piso": ["suma", "suma", "suma", "suma", "suma", "no suma", "no suma", "suma", "no suma"],
            "observacion_piso": ["Suma por documento origen", "Suma por documento origen", "Suma como proforma origen", "Suma en el periodo original", "Suma por línea habilitada", "Línea NA: valor final cero", "Anulada con nota crédito", "Reemplazo que conserva trazabilidad", "No suma: reemplazo del documento original"],
        }
    )
    empty = pd.DataFrame()
    result = CommissionResult(
        empty, empty, empty, documents, floor,
        OfficialPaymentBase(empty, empty, empty), detail,
    )
    return InMemoryCalculationResultProvider(RUN_CODE, result)


@st.cache_resource
def _production_runtime():
    from src.runtime import build_runtime

    configure_logging()
    return build_runtime()


def _backend_scope(data: pd.DataFrame, runtime, user_id: int) -> pd.DataFrame:
    if "executive" not in data.columns:
        return data.copy()
    if "period" in data.columns:
        selected_indexes: list[object] = []
        for period, group in data.groupby("period", dropna=False):
            period_text = str(period)
            effective_on = (
                f"{period_text}-01" if len(period_text) == 7 else date.today()
            )
            scope = runtime.authorization.executive_scope(user_id, effective_on)
            if scope is None:
                selected_indexes.extend(group.index.tolist())
            else:
                normalized = group["executive"].astype("string").str.strip().str.upper()
                selected_indexes.extend(group.index[normalized.isin(scope)].tolist())
        return data.loc[selected_indexes].copy()
    scope = runtime.authorization.executive_scope(user_id, date.today())
    if scope is None:
        return data.copy()
    normalized = data["executive"].astype("string").str.strip().str.upper()
    return data.loc[normalized.isin(scope)].copy()


def _restrict_commission_visibility(data: pd.DataFrame, profile: str) -> pd.DataFrame:
    result = data.copy()
    if profile in {"Ejecutivo", "Jefe de ventas"}:
        result = result.drop(
            columns=[
                column for column in ("commission_director", "director_commission")
                if column in result.columns
            ]
        )
    if profile == "Ejecutivo":
        result = result.drop(
            columns=[
                column for column in ("commission_manager", "manager_commission")
                if column in result.columns
            ]
        )
    return result


def _attach_hierarchy_context(
    data: pd.DataFrame, runtime, user_id: int
) -> pd.DataFrame:
    if data.empty or not {"executive", "period"}.issubset(data.columns):
        return data.copy()
    contexts: list[pd.DataFrame] = []
    for period, group in data.groupby("period", dropna=True):
        text_period = str(period)
        if len(text_period) != 7:
            continue
        rows = runtime.authorization.hierarchy_context(
            user_id, group["executive"].dropna().astype(str).unique(),
            f"{text_period}-01",
        )
        if rows:
            frame = pd.DataFrame(rows)
            frame["period"] = text_period
            contexts.append(frame)
    if not contexts:
        return data.copy()
    context = pd.concat(contexts, ignore_index=True).drop_duplicates(
        ["executive", "period"]
    )
    overlapping = [
        column for column in (
            "executive_name", "manager", "manager_name", "director", "director_name"
        ) if column in data.columns
    ]
    base = data.drop(columns=overlapping)
    return base.merge(context, on=["executive", "period"], how="left", validate="m:1")


def _money(value: float) -> str:
    return "$ " + f"{float(value):,.0f}".replace(",", ".")


def _payment_summary(
    payments: pd.DataFrame, discount_applications: pd.DataFrame | None = None
) -> pd.DataFrame:
    data = payments.copy()
    numeric = [
        column for column in (
            "commission_executive", "commission_manager", "commission_director"
        ) if column in data.columns
    ]
    for column in numeric:
        data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0)
    hierarchy = [
        column for column in (
            "executive_name", "manager", "manager_name", "director", "director_name"
        ) if column in data.columns
    ]
    dimensions = ["executive", *hierarchy, "period", "commission_line"]
    aggregations = {
        "commission": ("commission_executive", "sum"),
        "documents": ("document", "nunique"),
    }
    if "commission_manager" in data.columns:
        aggregations["manager_commission"] = ("commission_manager", "sum")
    if "commission_director" in data.columns:
        aggregations["director_commission"] = ("commission_director", "sum")
    summary = data.groupby(
        dimensions, as_index=False, dropna=False
    ).agg(**aggregations)
    summary["discounts"] = 0.0
    if discount_applications is not None and not discount_applications.empty:
        grouped = (
            discount_applications.groupby(["executive", "period"], as_index=False)["amount"]
            .sum()
            .rename(columns={"amount": "discounts"})
        )
        metadata = data[["executive", "period", *hierarchy]].drop_duplicates(
            ["executive", "period"]
        )
        grouped = grouped.merge(
            metadata, on=["executive", "period"], how="left", validate="m:1"
        )
        grouped["commission_line"] = "DESCUENTOS DEL PERIODO"
        grouped["commission"] = 0.0
        if "manager_commission" in summary.columns:
            grouped["manager_commission"] = 0.0
        if "director_commission" in summary.columns:
            grouped["director_commission"] = 0.0
        grouped["documents"] = 0
        summary = pd.concat(
            [summary, grouped[summary.columns]], ignore_index=True
        )
    summary["payable"] = summary["commission"] - summary["discounts"]
    return summary


def _enrich_demo_payments(payments: pd.DataFrame) -> pd.DataFrame:
    data = payments.copy()
    data["document_origin"] = ["SM72209", "SM60283", "PR-9553", "PR-9551", "SM71487", "SM71632", "SM65917", "SM65917", "PR-9551"]
    data["credit_note"] = ["NC5226", pd.NA, pd.NA, "NC5317", pd.NA, pd.NA, "NC5247", "NC5247", "NC5317"]
    data["replacement_document"] = ["SM72210", "SM71824", "SM72586", "SM73590", pd.NA, pd.NA, "SM72582", "SM72582", "SM73590"]
    data["adds_to_floor"] = [True, True, True, True, True, False, False, True, False]
    data["floor_reason"] = [
        "Suma por documento origen", "Suma por documento origen", "Suma como proforma origen",
        "Suma en el periodo original", "Suma por línea habilitada", "Línea NA: valor final cero",
        "Anulada con nota crédito", "Reemplazo que conserva trazabilidad", "No suma: reemplazo del documento original",
    ]
    data["lot"] = ["L-2209", "L-60283", "L-9553", "L-9551", "L-71487", "L-71632", "L-65917", "L-72582", "L-73590"]
    data["service"] = [
        "Inspección RETIE", "Inspección RETIE", "Inspección RITEL", "Inspección RETIE",
        "Consultoría energética", "Servicio no comisionable", "Calibración", "Calibración", "Inspección RETIE",
    ]
    data["payment_document"] = ["RC-019297", "RC-019100", "RC-019486", pd.NA, "RC-018950", pd.NA, pd.NA, "RC-019320", pd.NA]
    data["payment_type"] = ["Recibo de caja", "Recibo de caja", "Recibo de caja", pd.NA, "Recibo de caja", pd.NA, pd.NA, "Recibo de caja", pd.NA]
    data["paid_value"] = [9250000, 6200000, 4550000, pd.NA, 3850000, pd.NA, pd.NA, 1642200, pd.NA]
    data["payment_days"] = [24, 38, 55, pd.NA, 29, pd.NA, pd.NA, 20, pd.NA]
    data["payment_factor"] = [1.0, 1.0, 1.0, pd.NA, 1.0, 0.0, pd.NA, 0.5, pd.NA]
    data["commission_rate"] = [4.0, 4.0, 4.0, pd.NA, 4.0, 0.0, pd.NA, 4.0, pd.NA]
    data["manager"] = ["J-01", "J-01", "J-01", "J-01", "J-02", "J-02", "J-02", "J-02", "J-01"]
    return data


def _attach_demo_hierarchy(data: pd.DataFrame) -> pd.DataFrame:
    if "executive" not in data.columns:
        return data.copy()
    result = data.copy()
    executive_names = {
        "EJ-01": "Ana Torres", "EJ-02": "Carlos Ruiz",
        "EJ-03": "Diana López", "EJ-04": "Esteban Mora",
    }
    managers = {
        "EJ-01": ("J-01", "Laura Méndez"),
        "EJ-02": ("J-01", "Laura Méndez"),
        "EJ-03": ("J-02", "Miguel Díaz"),
        "EJ-04": ("J-02", "Miguel Díaz"),
    }
    result["executive_name"] = result["executive"].map(executive_names)
    result["manager"] = result["executive"].map(
        {code: values[0] for code, values in managers.items()}
    )
    result["manager_name"] = result["executive"].map(
        {code: values[1] for code, values in managers.items()}
    )
    result["director"] = "G-01"
    result["director_name"] = "Gerencia Comercial"
    return result


def _line_payment_summary(payments: pd.DataFrame) -> pd.DataFrame:
    return _payment_summary(payments)


def _demo_discounts() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "discount_id": [1, 2],
            "executive": ["EJ-01", "EJ-03"],
            "period": ["2026-08", "2026-07"],
            "detection_period": ["2026-09", "2026-09"],
            "cancelled_documents": ["SM70001", "SM70002"],
            "credit_notes": ["NC5300", "NC5301"],
            "commission_lines": ["RETIE/LAP", "ENERGIA"],
            "paid_documents": ["SM70001,SM70005", "SM70002"],
            "total_debt": [120000.0, 80000.0],
            "applied_amount": [50000.0, 0.0],
            "balance": [70000.0, 80000.0],
            "status": ["APLICADO_PARCIAL", "PENDIENTE"],
        }
    )


def _demo_discount_applications() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "executive": ["EJ-01"],
            "period": ["2026-08"],
            "amount": [50000.0],
        }
    )


def _open_discounts(profile: str) -> None:
    menu_key = PROFILE_MENU_KEYS[profile]
    st.session_state[menu_key] = "Consultas"
    st.session_state[f"active_{menu_key}"] = "Consultas"
    st.session_state["navigation"] = "Descuentos"


def _period_workspace(runtime=None, actor_user_id: int | None = None) -> None:
    if runtime is not None and actor_user_id is not None:
        service = runtime.business_period_workflows
        records = service.list_periods(actor_user_id)
        periods = pd.DataFrame([
            {
                "period": item.period,
                "status": item.status,
                "created_by": item.created_by,
                "created_at": item.created_at,
                "modified_by": item.modified_by,
                "modified_at": item.modified_at,
            }
            for item in records
        ])
        view = st.radio(
            "Vista de trabajo", ["Consultar", "Crear periodo", "Gestionar periodo"],
            horizontal=True, key="productive-period-view",
        )
        if view == "Consultar":
            if periods.empty:
                st.info("No hay periodos registrados.")
                return
            statuses = st.multiselect(
                "Estado del periodo",
                ["ABIERTO", "EN_REVISION", "FINALIZADO", "CERRADO"],
                placeholder="Todos",
            )
            data = periods if not statuses else periods.loc[periods["status"].isin(statuses)]
            _table(data, "productive-periods-table")
            selected = st.selectbox(
                "Periodo para consultar historial", data["period"].tolist()
            ) if not data.empty else None
            if selected:
                history = pd.DataFrame([
                    vars(item) for item in service.history(actor_user_id, selected)
                ])
                st.markdown("### Historial del periodo")
                _table(history, "productive-period-history")
            return
        if view == "Crear periodo":
            with st.form("create-business-period"):
                period = st.text_input(
                    "Periodo", placeholder="AAAA-MM", help=PERIOD_HELP
                )
                reason = st.text_area(
                    "Observación", value="Creación del periodo operativo",
                    disabled=True,
                )
                create = st.form_submit_button("Crear periodo", type="primary")
            if create:
                try:
                    service.create(actor_user_id, period)
                    st.success(f"Periodo {period} creado en estado ABIERTO.")
                except Exception as exc:
                    st.error(str(exc))
            return
        if periods.empty:
            st.info("Primero debe crear un periodo.")
            return
        st.caption("Los cambios requieren una acción explícita y quedan en el historial.")
        with st.form("productive-period-management"):
            columns = st.columns(2)
            selected = columns[0].selectbox("Periodo", periods["period"].tolist())
            current = str(periods.set_index("period").loc[selected, "status"])
            available = ["Avanzar al siguiente estado"]
            if current == "CERRADO":
                available = ["Reabrir"]
            action = columns[1].selectbox("Acción", available)
            st.text_input("Estado actual", value=current, disabled=True)
            reason = st.text_area(
                "Motivo obligatorio",
                placeholder="Explique el cambio de estado o la reapertura.",
            )
            save = st.form_submit_button("Guardar cambio", type="primary")
        if save:
            if not reason.strip():
                st.warning("El motivo del cambio de estado es obligatorio.")
                return
            try:
                if action == "Reabrir":
                    service.reopen(actor_user_id, selected, reason.strip())
                    target = "ABIERTO"
                else:
                    target = service.advance(actor_user_id, selected, reason.strip())
                st.success(f"Periodo {selected} actualizado a {target}.")
            except Exception as exc:
                st.error(str(exc))
        return
    periods = pd.DataFrame(
        {
            "period": ["2026-09", "2026-08", "2026-07"],
            "status": ["ABIERTO", "CERRADO", "FINALIZADO"],
            "modified_at": ["2026-10-01", "2026-09-05", "2026-08-04"],
            "modified_by": ["Administrador", "Administrador", "Revisor"],
        }
    )
    view = st.radio(
        "Vista de trabajo", ["Consultar", "Gestionar periodo"],
        horizontal=True, key="period-view",
    )
    if view == "Consultar":
        statuses = st.multiselect(
            "Estado del periodo",
            ["ABIERTO", "EN_REVISION", "FINALIZADO", "CERRADO"],
            placeholder="Todos",
        )
        data = periods if not statuses else periods.loc[periods["status"].isin(statuses)]
        _table(data, "periods-table")
        return

    st.caption("Los cambios requieren una acción explícita y quedan en el historial.")
    with st.form("period-management"):
        columns = st.columns(2)
        period = columns[0].selectbox("Periodo", periods["period"].tolist())
        action = columns[1].selectbox(
            "Acción",
            ["Iniciar revisión", "Finalizar", "Cerrar", "Reabrir"],
        )
        reason = st.text_area(
            "Motivo obligatorio",
            placeholder="Explique el cierre, finalización o reapertura.",
        )
        save = st.form_submit_button("Guardar cambio", type="primary")
    if save:
        if not reason.strip():
            st.warning("El motivo del cambio de estado es obligatorio.")
        else:
            st.success(
                f"Modo demo: acción '{action}' preparada para {period}. "
                "En producción se validará el estado actual y el permiso del usuario."
            )


def _payment_file_workspace(
    profile: str,
    runtime: object | None = None,
    actor_user_id: int | None = None,
    result_provider: object | None = None,
) -> None:
    demo = runtime is None or actor_user_id is None
    demo_batches = pd.DataFrame(
        {
            "period": ["2026-09", "2026-08"],
            "version": [2, 1],
            "calculation_run": ["calc-2026-09-v2", "calc-2026-08-v1"],
            "status": ["FINALIZADO", "FINALIZADO"],
            "finalized_at": ["2026-10-02", "2026-09-03"],
            "download_count": [1, 2],
            "send_count": [0, 1],
            "last_send_status": [pd.NA, "ENVIADO"],
        }
    )
    if demo:
        batches = demo_batches
        service = None
    else:
        from src.payment_files import ActiveCalculationPaymentSummaryProvider

        summary_provider = ActiveCalculationPaymentSummaryProvider(
            result_provider, runtime.access_repository, runtime.discounts
        )
        try:
            service = runtime.payment_file_service(summary_provider)
            batches = pd.DataFrame(
                [vars(item) for item in service.list_batches(actor_user_id)]
            )
        except Exception as exc:
            st.error(str(exc))
            return

    view = st.radio(
        "Vista de trabajo",
        ["Consultar versiones", "Finalizar periodo", "Descargar", "Enviar"],
        horizontal=True,
        key="payment-file-view",
    )
    if view == "Consultar versiones":
        periods = [] if batches.empty else batches["period"].drop_duplicates().tolist()
        period = st.selectbox(
            "Periodo", ["Todos", *periods],
            key="batch-period-filter",
        )
        data = batches if period == "Todos" else batches.loc[batches["period"].eq(period)]
        _table(data, "payment-batches")
        if not demo and not data.empty:
            labels = [
                f"{row.period} - v{row.version:03d}" for row in data.itertuples()
            ]
            selected = st.selectbox(
                "Historial de la versión", labels, key="payment-event-version"
            )
            index = labels.index(selected)
            try:
                events = pd.DataFrame(
                    service.list_events(
                        actor_user_id, int(data.iloc[index]["batch_id"])
                    )
                )
                if events.empty:
                    st.caption("Esta versión todavía no tiene descargas ni envíos.")
                else:
                    _table(events, "payment-version-events")
            except Exception as exc:
                st.error(str(exc))
        return

    if view == "Finalizar periodo":
        with st.form("finalize-payment-period"):
            columns = st.columns(2)
            period = columns[0].text_input("Periodo", placeholder="AAAA-MM", help=PERIOD_HELP)
            active_calculation = str(getattr(result_provider, "run_code", ""))
            calculation = columns[1].text_input(
                "Cálculo aprobado",
                value=active_calculation,
                disabled=bool(active_calculation) and not demo,
            )
            observation = st.text_area("Observación obligatoria")
            save = st.form_submit_button("Finalizar y crear versión", type="primary")
        if save:
            if not period.strip() or not calculation.strip() or not observation.strip():
                st.warning("Periodo, cálculo aprobado y observación son obligatorios.")
            else:
                if demo:
                    st.success("Modo demo: versión preparada sin generar todavía el Excel.")
                else:
                    try:
                        batch = service.finalize(
                            actor_user_id, period, calculation, observation
                        )
                        st.success(
                            f"Versión v{batch.version:03d} finalizada. "
                            "El Excel todavía no se ha generado."
                        )
                    except Exception as exc:
                        st.error(str(exc))
        return

    if batches.empty:
        st.info("No existen versiones finalizadas para esta acción.")
        return
    selected = st.selectbox(
        "Versión del archivo",
        [f"{row.period} - v{row.version:03d}" for row in batches.itertuples()],
        key="payment-batch-selection",
    )
    selected_index = [
        f"{row.period} - v{row.version:03d}" for row in batches.itertuples()
    ].index(selected)
    batch_id = int(batches.iloc[selected_index]["batch_id"]) if "batch_id" in batches else 0
    st.caption(f"Versión seleccionada: {selected}")
    if view == "Descargar":
        st.info("El Excel se genera en memoria únicamente al solicitar la descarga.")
        if st.button("Generar y descargar Excel", type="primary"):
            if demo:
                st.info("Modo demo: la descarga real está disponible con sesión productiva.")
            else:
                try:
                    name, content = service.download(actor_user_id, batch_id)
                    st.session_state["payment_download"] = (name, content)
                except Exception as exc:
                    st.error(str(exc))
        prepared = st.session_state.get("payment_download")
        if prepared is not None:
            st.download_button(
                "Descargar archivo preparado",
                data=prepared[1], file_name=prepared[0],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        return

    recipients = st.text_input("Destinatarios", placeholder="correo1@empresa.com; correo2@empresa.com")
    observation = st.text_area("Mensaje y observación obligatoria")
    if st.button("Enviar archivo", type="primary"):
        if not recipients.strip() or not observation.strip():
            st.warning("Destinatarios y observación son obligatorios.")
        else:
            if demo:
                st.info("Modo demo: no se realiza ningún envío.")
            else:
                try:
                    addresses = [item.strip() for item in recipients.split(";")]
                    service.send(
                        actor_user_id, batch_id, addresses,
                        f"Pago de comisiones {selected}", observation,
                    )
                    st.success("Archivo enviado y evento auditado.")
                except Exception as exc:
                    st.error(str(exc))


def _audit_workspace(runtime=None, actor_user_id: int | None = None) -> None:
    if runtime is None or actor_user_id is None:
        demo = pd.DataFrame([
            {
                "occurred_at": "2026-10-16T14:20:00+00:00",
                "actor_user_id": "1",
                "action": "PERIOD_STATUS_CHANGED",
                "entity_type": "BUSINESS_PERIOD",
                "entity_id": "2026-09",
                "previous_value": "EN_REVISION",
                "new_value": "FINALIZADO",
                "reason": "Cierre mensual revisado",
            },
            {
                "occurred_at": "2026-10-16T14:30:00+00:00",
                "actor_user_id": "1",
                "action": "PAYMENT_FILE_FINALIZED",
                "entity_type": "PAYMENT_BATCH",
                "entity_id": "1",
                "previous_value": None,
                "new_value": "FINALIZADO",
                "reason": "Archivo de pago aprobado",
            },
        ])
        st.caption("Vista demostrativa del historial inmutable.")
        _table(demo, "audit-demo-table")
        return

    service = runtime.audit_query_service
    try:
        facets = service.facets(actor_user_id)
    except Exception as exc:
        st.error(str(exc))
        return

    st.markdown(
        '<div class="filter-heading"><b>Filtros de auditoría</b>'
        '<span>Se aplican al historial completo antes de paginar.</span></div>',
        unsafe_allow_html=True,
    )
    filters = st.columns([1.2, 1.2, .8, .9, .9, 1.3])
    actions = filters[0].multiselect("Acción", facets["actions"])
    entities = filters[1].multiselect("Entidad", facets["entity_types"])
    actors = filters[2].multiselect("Usuario", facets["actors"])
    date_from = filters[3].text_input("Desde", placeholder="AAAA-MM-DD")
    date_to = filters[4].text_input("Hasta", placeholder="AAAA-MM-DD")
    search = filters[5].text_input("Buscar", placeholder="Registro, acción o motivo")
    controls = st.columns([1.2, 1, .7, .7, 3])
    sort_label = controls[0].selectbox(
        "Ordenar por", ["Fecha", "Acción", "Entidad", "Usuario"]
    )
    descending = controls[1].selectbox(
        "Dirección", ["Más reciente", "Más antiguo"]
    ) == "Más reciente"
    page_size = int(controls[2].selectbox("Filas", [25, 50, 100, 250]))
    page = int(controls[3].number_input("Página", min_value=1, value=1, step=1))
    sort_columns = {
        "Fecha": "occurred_at", "Acción": "action",
        "Entidad": "entity_type", "Usuario": "actor_user_id",
    }
    try:
        result = service.query(
            actor_user_id,
            actions=actions or None,
            entity_types=entities or None,
            actors=actors or None,
            date_from=date_from.strip() or None,
            date_to=(date_to.strip() + "T23:59:59") if date_to.strip() else None,
            search=search.strip() or None,
            sort_by=sort_columns[sort_label],
            descending=descending,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        st.error(str(exc))
        return
    st.markdown(
        f'<p class="table-count"><b>{result.total}</b> eventos encontrados · '
        f'Página {result.page} de {max(result.pages, 1)}</p>',
        unsafe_allow_html=True,
    )
    _table(pd.DataFrame([vars(item) for item in result.items]), "audit-table")


def _netsuite_sync_workspace(runtime, actor_user_id: int) -> None:
    service = runtime.sync_service
    st.markdown("### Historial de sincronizaciones")
    st.caption(
        "NetSuite es la fuente empresarial; esta tabla muestra las actualizaciones "
        "persistidas para uso operativo de la aplicación."
    )
    try:
        runs = service.list_runs(actor_user_id, limit=100)
    except Exception as exc:
        st.error(str(exc))
        return

    history = pd.DataFrame([
        {
            "Ejecución": item.run_code,
            "Tipo": item.sync_type,
            "Estado": item.status,
            "Inicio": item.started_at,
            "Fin": item.finished_at,
            "Ejecutado por": item.executed_by,
            "Creados": item.created_count,
            "Actualizados": item.updated_count,
            "Sin cambios": item.unchanged_count,
            "Desactivados": item.deactivated_count,
            "Detalle": item.message,
        }
        for item in runs
    ])
    if history.empty:
        st.info("Todavía no hay sincronizaciones registradas.")
    else:
        _table(history, "netsuite-sync-history")

    st.markdown("### Actualizar datos desde NetSuite")
    st.warning(
        "La actualización consulta el origen configurado, valida los datos y "
        "actualiza la fuente operativa. No recalcula comisiones automáticamente."
    )
    with st.form("netsuite-sync-form"):
        st.selectbox(
            "Tipo de actualización",
            ["Sincronización completa"],
            help=(
                "La sincronización incremental se habilitará cuando exista el "
                "contrato real de la API."
            ),
        )
        confirmed = st.checkbox(
            "Confirmo que deseo actualizar ahora los datos operativos desde NetSuite."
        )
        execute = st.form_submit_button(
            "Actualizar datos desde NetSuite", type="primary"
        )
    if not execute:
        return
    if not confirmed:
        st.error("Confirme la actualización antes de ejecutarla.")
        return
    try:
        with st.spinner("Sincronizando y validando datos de NetSuite..."):
            result = service.synchronize(actor_user_id, sync_type="FULL")
        st.success(
            f"Sincronización {result.run_code} finalizada con estado {result.status}."
        )
        st.rerun()
    except Exception as exc:
        st.error(f"La sincronización no pudo completarse: {exc}")


def _calculation_review_issues(
    result: CommissionResult,
    provider: InMemoryCalculationResultProvider,
    run_code: str,
) -> tuple[pd.DataFrame, int, int]:
    def column(frame: pd.DataFrame, name: str) -> pd.Series:
        if name in frame.columns:
            return frame[name]
        return pd.Series(pd.NA, index=frame.index, dtype="object")

    payments = provider.review_results(run_code).copy()
    payment_issues = pd.DataFrame({
        "issue_type": pd.Series("Pago", index=payments.index, dtype="string"),
        "review_reason": column(payments, "payment_reason"),
        "executive": column(payments, "executive"),
        "period": column(payments, "period"),
        "commission_line": column(payments, "commission_line"),
        "document": column(payments, "document"),
        "decision": column(payments, "decision"),
        "document_value": column(payments, "document_value"),
        "document_date": column(payments, "document_date"),
        "credit_note": column(payments, "credit_note"),
        "credit_note_date": column(payments, "credit_note_date"),
        "replacement_document": column(payments, "replacement_document"),
        "replacement_document_date": column(
            payments, "replacement_document_date"
        ),
        "lot": column(payments, "lot"),
        "service": column(payments, "service"),
        "payment_date": column(payments, "payment_date"),
        "accounting_date": column(payments, "accounting_date"),
        "payment_document": column(payments, "payment_document"),
        "paid_value": column(payments, "paid_value"),
        "request_id": column(payments, "request_id"),
        "request_status": column(payments, "request_status"),
    })
    payment_issues["affected_lines"] = 1

    if not payment_issues.empty:
        group_columns = [
            "issue_type", "review_reason", "executive", "period",
            "commission_line", "document", "decision",
        ]

        def joined_unique(values: pd.Series) -> object:
            clean = values.dropna().astype("string").str.strip()
            clean = clean.loc[clean.ne("") & clean.ne("None")]
            unique = list(dict.fromkeys(clean.tolist()))
            return " | ".join(unique) if unique else pd.NA

        payment_issues = (
            payment_issues.groupby(group_columns, dropna=False, as_index=False)
            .agg({
                "affected_lines": "sum",
                "document_value": "max",
                "document_date": joined_unique,
                "credit_note": joined_unique,
                "credit_note_date": joined_unique,
                "replacement_document": joined_unique,
                "replacement_document_date": joined_unique,
                "lot": joined_unique,
                "service": joined_unique,
                "payment_date": joined_unique,
                "accounting_date": joined_unique,
                "payment_document": joined_unique,
                "paid_value": "max",
                "request_id": joined_unique,
                "request_status": joined_unique,
            })
        )

    floor = result.floor_compliance.copy()
    review_flag = floor.get(
        "requiere_revision_cumplimiento",
        pd.Series(False, index=floor.index),
    ).astype("boolean").fillna(False)
    floor = floor.loc[review_flag].copy()
    floor_period = floor.get("periodo_mes", pd.Series(pd.NA, index=floor.index))
    floor_issues = pd.DataFrame({
        "issue_type": pd.Series(
            "Cumplimiento de piso", index=floor.index, dtype="string"
        ),
        "review_reason": column(floor, "motivo_revision_cumplimiento").combine_first(
            column(floor, "observacion_cumple")
        ),
        "executive": column(floor, "entityid"),
        "period": floor_period.astype("string"),
        "commission_line": column(floor, "linea_comision"),
        "document": pd.Series(pd.NA, index=floor.index, dtype="string"),
        "decision": pd.Series(
            "REVISAR CUMPLIMIENTO", index=floor.index, dtype="string"
        ),
        "document_value": pd.to_numeric(
            column(floor, "TotalParaPiso"), errors="coerce"
        ),
        "affected_lines": pd.Series(pd.NA, index=floor.index, dtype="Int64"),
        "document_date": pd.Series(pd.NA, index=floor.index, dtype="string"),
        "credit_note": pd.Series(pd.NA, index=floor.index, dtype="string"),
        "credit_note_date": pd.Series(pd.NA, index=floor.index, dtype="string"),
        "replacement_document": pd.Series(
            pd.NA, index=floor.index, dtype="string"
        ),
        "replacement_document_date": pd.Series(
            pd.NA, index=floor.index, dtype="string"
        ),
        "lot": pd.Series(pd.NA, index=floor.index, dtype="string"),
        "service": pd.Series(pd.NA, index=floor.index, dtype="string"),
        "payment_date": pd.Series(pd.NA, index=floor.index, dtype="string"),
        "accounting_date": pd.Series(pd.NA, index=floor.index, dtype="string"),
        "payment_document": pd.Series(pd.NA, index=floor.index, dtype="string"),
        "paid_value": pd.Series(pd.NA, index=floor.index, dtype="Float64"),
        "request_id": pd.Series(pd.NA, index=floor.index, dtype="string"),
        "request_status": pd.Series(pd.NA, index=floor.index, dtype="string"),
    })
    issues = pd.concat([floor_issues, payment_issues], ignore_index=True)
    return issues, len(payment_issues), len(floor_issues)


def _calculation_runs_workspace(runtime=None, actor_user_id: int | None = None) -> None:
    if runtime is None or actor_user_id is None:
        demo = pd.DataFrame([{
            "Ejecución": "calc-demo-2026-09",
            "Periodo": "2026-09",
            "Versión de reglas": "1",
            "Estado": "COMPLETADO",
            "Inicio": "2026-10-01T08:00:00+00:00",
            "Fin": "2026-10-01T08:02:00+00:00",
            "Ejecutado por": "admin.demo",
            "Comisiones": 3,
            "Cumplimientos": 3,
            "Pagos": 6,
            "Descuentos": 1,
            "Advertencias": 0,
            "Errores": 0,
            "Detalle": None,
        }])
        st.caption("Vista demostrativa del historial técnico de cálculos.")
        _table(demo, "calculation-runs-demo-table")
        return
    service = runtime.audit_query_service
    st.markdown(
        '<div class="filter-heading"><b>Filtros de ejecuciones</b>'
        '<span>Se aplican antes de paginar.</span></div>',
        unsafe_allow_html=True,
    )
    filters = st.columns([1.4, 1.4, .7, .7, 2])
    periods = filters[0].multiselect(
        "Periodo", [], accept_new_options=True,
        placeholder="Todos o escriba AAAA-MM",
    )
    statuses = filters[1].multiselect(
        "Estado",
        ["PENDIENTE", "EN_PROCESO", "COMPLETADO", "COMPLETADO_CON_ADVERTENCIAS", "ERROR"],
        placeholder="Todos",
    )
    page_size = int(filters[2].selectbox("Filas", [25, 50, 100, 250]))
    page = int(filters[3].number_input("Página", min_value=1, value=1, step=1))
    try:
        result = service.calculation_runs(
            actor_user_id,
            periods=periods or None,
            statuses=statuses or None,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        st.error(str(exc))
        return
    st.markdown(
        f'<p class="table-count"><b>{result.total}</b> ejecuciones encontradas · '
        f'Página {result.page} de {max(result.pages, 1)}</p>',
        unsafe_allow_html=True,
    )
    data = pd.DataFrame([{
        "Ejecución": item.run_code,
        "Periodo": item.period,
        "Versión de reglas": item.rules_version,
        "Estado": item.status,
        "Estado de aprobación": item.workflow_status or "SIN FLUJO",
        "Inicio": item.started_at,
        "Fin": item.finished_at,
        "Ejecutado por": item.executed_by,
        "Comisiones": item.commission_rows,
        "Cumplimientos": item.floor_rows,
        "Pagos": item.payment_rows,
        "Descuentos": item.discount_rows,
        "Advertencias": item.warning_count,
        "Errores": item.error_count,
        "Detalle": item.message,
    } for item in result.items])
    if data.empty:
        st.info("No hay ejecuciones que coincidan con los filtros.")
    else:
        _table(data, "calculation-runs-table")
        st.subheader("Decisión del cálculo")
        selected_run = st.selectbox(
            "Ejecución",
            data["Ejecución"].astype(str).tolist(),
            key="calculation_workflow_run",
        )
        selected_row = data.loc[data["Ejecución"].eq(selected_run)].iloc[0]
        workflow_status = str(selected_row["Estado de aprobación"])
        st.caption(f"Estado actual: {workflow_status}")
        review_result = runtime.calculations.load_result(selected_run)
        if review_result is None:
            st.warning(
                "Esta ejecución histórica no tiene un borrador persistido para revisar."
            )
        else:
            draft_provider = InMemoryCalculationResultProvider(
                selected_run, review_result, runtime.reviews
            )
            st.markdown("### Contenido del borrador")
            review_view = st.radio(
                "Vista de revisión",
                [
                    "Pendientes de revisión",
                    "Resumen de pagos",
                    "Cumplimiento de pisos",
                    "Facturas evaluadas",
                    "Detalle de pagos",
                ],
                horizontal=True,
                key="calculation_review_view",
            )
            if review_view == "Pendientes de revisión":
                review_data, payment_count, floor_count = _calculation_review_issues(
                    review_result, draft_provider, selected_run
                )
                _metric_cards(
                    [
                        ("Total pendientes", str(len(review_data))),
                        ("Pagos", str(payment_count)),
                        ("Cumplimientos", str(floor_count)),
                    ],
                    "Revisión del cálculo",
                )
                if review_data.empty:
                    st.success("El cálculo no tiene advertencias pendientes de revisión.")
                else:
                    st.warning(
                        "Revise y resuelva estos registros antes de enviar el cálculo "
                        "a aprobación. El motivo aparece en la segunda columna."
                    )
                    review_data = _filter(
                        review_data,
                        "calculation-draft-issues",
                        ("issue_type", "period", "executive", "commission_line"),
                    )
            elif review_view == "Resumen de pagos":
                review_data = draft_provider.payment_summary(selected_run)
                review_data = _filter(
                    review_data,
                    "calculation-draft-summary",
                    ("period", "executive"),
                )
            elif review_view == "Cumplimiento de pisos":
                review_data = _filter(
                    draft_provider.floor_results(selected_run),
                    "calculation-draft-floor",
                )
            elif review_view == "Facturas evaluadas":
                review_data = _filter(
                    draft_provider.document_compliance_results(selected_run),
                    "calculation-draft-documents",
                )
            else:
                review_data = _filter(
                    draft_provider.commission_results(selected_run),
                    "calculation-draft-payments",
                    ("period", "executive", "commission_line", "decision"),
                )
            _table(review_data, "calculation-draft-review")
        reason = st.text_area(
            "Observación obligatoria",
            placeholder="Explique la revisión, aprobación o rechazo.",
            key="calculation_workflow_reason",
        )
        actions: list[str] = []
        if workflow_status == "PENDIENTE_REVISION":
            actions.append("Enviar a aprobación")
        effective_permissions = runtime.access_repository.effective_permissions(
            actor_user_id
        )
        if (
            workflow_status == "PENDIENTE_APROBACION"
            and PERM_CALCULATION_APPROVE in effective_permissions
        ):
            actions.extend(["Aprobar", "Rechazar"])
        if not actions:
            st.info("Esta ejecución no tiene una acción pendiente.")
        else:
            action = st.selectbox(
                "Acción", actions, key="calculation_workflow_action"
            )
            if st.button("Guardar decisión", type="primary"):
                try:
                    if action == "Enviar a aprobación":
                        runtime.calculation_service.submit_for_approval(
                            actor_user_id, selected_run, reason
                        )
                    elif action == "Aprobar":
                        runtime.calculation_service.approve(
                            actor_user_id, selected_run, reason
                        )
                    else:
                        runtime.calculation_service.reject(
                            actor_user_id, selected_run, reason
                        )
                    st.success("Decisión guardada con trazabilidad.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def _team_data() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ("EJ-01", "Ana Torres", "J-01", "ACTIVO", "2026-01-01", ""),
            ("EJ-02", "Carlos Ruiz", "J-01", "ACTIVO", "2026-01-01", ""),
        ],
        columns=[
            "Ejecutivo", "Nombre", "Jefe", "Estado",
            "Fecha inicio vigencia", "Fecha de inactivación",
        ],
    )


def _metric_cards(values: list[tuple[str, str]], profile: str) -> None:
    cards = "".join(
        f'<article class="metric-card"><span>{label}</span><b>{value}</b></article>'
        for label, value in values
    )
    st.markdown(
        f'<section class="metric-grid" data-profile="{profile}">{cards}</section>',
        unsafe_allow_html=True,
    )


def _parameter_data() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ("payment_full_days", "Días para pago completo", "Entero", "30", "días", True, "ESTÁNDAR"),
            ("payment_half_days", "Días para pago medio", "Entero", "60", "días", True, "ESTÁNDAR"),
            ("payment_factor_full", "Factor pago completo", "Decimal", "1.00", "factor", True, "ESTÁNDAR"),
            ("payment_factor_half", "Factor pago medio", "Decimal", "0.50", "factor", True, "ESTÁNDAR"),
            ("payment_factor_late", "Factor pago tardío", "Decimal", "0.00", "factor", True, "ESTÁNDAR"),
            ("product_first_payment_threshold", "Umbral primer pago PRODUCTO", "Decimal", "0.50", "proporción", True, "ESTÁNDAR"),
            ("product_first_payment_days", "Días primer pago PRODUCTO", "Entero", "60", "días", True, "ESTÁNDAR"),
            ("default_commission_executive", "Comisión ejecutivo por defecto", "Decimal", "0.04", "proporción", True, "ESTÁNDAR"),
            ("default_commission_manager", "Comisión jefe por defecto", "Decimal", "0.01", "proporción", True, "ESTÁNDAR"),
            ("default_commission_director", "Comisión gerente por defecto", "Decimal", "0.02", "proporción", True, "ESTÁNDAR"),
            ("subcontract_factor", "Factor subcontratado", "Decimal", "0.50", "factor", True, "ESTÁNDAR"),
            ("balance_tolerance_rate", "Tolerancia relativa de saldo", "Decimal", "0.000001", "proporción", True, "ESTÁNDAR"),
            ("balance_tolerance_minimum", "Tolerancia mínima de saldo", "Decimal", "1.00", "moneda local", True, "ESTÁNDAR"),
            ("include_proformas", "Incluir proformas", "Booleano", "Sí", "bandera", True, "ESTÁNDAR"),
        ],
        columns=[
            "Código", "Parámetro", "Tipo", "Valor", "Unidad", "Habilitado", "Estado"
        ],
    )


def _navigate(menu_key: str, menu: str, page: str) -> None:
    """Change sidebar widgets before Streamlit instantiates them on the next run."""
    st.session_state[menu_key] = menu
    st.session_state[f"active_{menu_key}"] = menu
    st.session_state["navigation"] = page


def _track_navigation(profile: str) -> None:
    current = st.session_state.get("navigation")
    key = f"last_navigation_{profile}"
    previous = st.session_state.get(key)
    if previous and current and previous != current:
        history = st.session_state.setdefault(f"navigation_history_{profile}", [])
        history.append(previous)
    st.session_state[key] = current


def _navigate_back(profile: str) -> None:
    history = st.session_state.get(f"navigation_history_{profile}", [])
    if not history:
        return
    target = history.pop()
    st.session_state["navigation"] = target
    st.session_state[f"last_navigation_{profile}"] = target


def _clear_session_state(state) -> None:
    """Remove identity, results, filters and drafts when a user logs out."""
    for key in list(state.keys()):
        del state[key]


def _parameterizer_dashboard() -> None:
    st.markdown("### Trabajo pendiente")
    pending = _parameter_data().loc[_parameter_data()["Estado"].eq("PENDIENTE")]
    _table(pending, "parameter-pending")
    st.markdown("### Acciones rápidas")
    columns = st.columns(3)
    columns[0].button(
        "Gestionar parámetros", width="stretch", on_click=_navigate,
        args=("parameterizer_menu", "Parametrización", "Parámetros"),
    )
    columns[1].button(
        "Revisar aprobaciones", width="stretch", on_click=_navigate,
        args=("parameterizer_menu", "Parametrización", "Aprobaciones"),
    )
    columns[2].button(
        "Revisar excepciones", width="stretch", on_click=_navigate,
        args=("parameterizer_menu", "Gestión", "Excepciones"),
    )


def _administrator_dashboard() -> None:
    st.markdown("### Estado operativo")
    status = pd.DataFrame(
        [
            ("Usuarios", "6 activos", "Sin bloqueos"),
            ("Periodos", "1 abierto", "Septiembre 2026"),
            ("Aprobaciones", "1 pendiente", "Requiere atención"),
            ("Auditoría", "2 eventos", "Disponibles para consulta"),
        ],
        columns=["Módulo", "Estado", "Detalle"],
    )
    _table(status, "admin-status")
    st.markdown("### Acciones administrativas")
    st.caption("Accesos a configuración y control. Todas las acciones sensibles conservan auditoría.")
    first = st.columns(3)
    first[0].button(
        "Administrar usuarios", width="stretch", on_click=_navigate,
        args=("admin_menu", "Administración", "Usuarios"),
    )
    first[1].button(
        "Gestionar periodos", width="stretch", on_click=_navigate,
        args=("admin_menu", "Administración", "Periodos"),
    )
    first[2].button(
        "Consultar auditoría", width="stretch", on_click=_navigate,
        args=("admin_menu", "Administración", "Auditoría"),
    )
    second = st.columns(3)
    second[0].button(
        "Gestionar parámetros", width="stretch", on_click=_navigate,
        args=("admin_menu", "Configuración", "Parámetros"),
    )
    second[1].button(
        "Revisar aprobaciones", width="stretch", on_click=_navigate,
        args=("admin_menu", "Configuración", "Aprobaciones"),
    )
    second[2].button(
        "Cerrar periodo o generar archivo", width="stretch", on_click=_navigate,
        args=("admin_menu", "Gestión", "Cierre y archivo"),
    )


def _parameter_editor(runtime=None, actor_user_id: int | None = None) -> None:
    st.markdown("### Parámetros y reglas vigentes")
    st.caption("Seleccione un parámetro y use Editar. Los valores de la tabla no se modifican directamente.")
    if runtime is not None and actor_user_id is not None:
        workflow = runtime.rule_parameter_workflows
        definitions = pd.DataFrame(workflow.list_definitions(actor_user_id))
        versions = pd.DataFrame(workflow.list_versions(actor_user_id))
        effective = runtime.rule_parameters.load_parameters(date.today())
        current_values = vars(effective)
        definitions["current_value"] = definitions["code"].map(current_values)
        display = definitions.rename(columns={
            "code": "Código", "name": "Parámetro", "description": "Descripción",
            "data_type": "Tipo", "unit": "Unidad", "enabled": "Habilitado",
            "current_value": "Valor vigente",
        })
        _table(display, "productive-parameters")
        selected_code = st.selectbox(
            "Parámetro que desea modificar", definitions["code"].tolist(),
            format_func=lambda code: definitions.set_index("code").loc[code, "name"],
            key="productive-parameter-code",
        )
        selected = definitions.loc[definitions["code"].eq(selected_code)].iloc[0]
        if st.button("Editar parámetro", key="edit-productive-parameter"):
            st.session_state["editing_parameter"] = selected_code
        editing = st.session_state.get("editing_parameter")
        if editing:
            selected = definitions.loc[definitions["code"].eq(editing)].iloc[0]
            with st.form("productive-parameter-form"):
                columns = st.columns(3)
                columns[0].text_input("Código", value=str(editing), disabled=True)
                columns[1].text_input(
                    "Valor actual", value=str(current_values[editing]), disabled=True
                )
                data_type = str(selected["data_type"])
                if data_type == "boolean":
                    new_value = columns[2].checkbox(
                        "Nuevo valor", value=bool(current_values[editing])
                    )
                elif data_type == "integer":
                    new_value = columns[2].number_input(
                        "Nuevo valor", value=int(current_values[editing]), step=1
                    )
                else:
                    new_value = columns[2].number_input(
                        "Nuevo valor", value=float(current_values[editing]), format="%.6f"
                    )
                enabled = st.checkbox(
                    "Regla habilitada",
                    value=bool(selected["enabled"]),
                    help=(
                        "Al deshabilitarla se usa el valor estándar. "
                        "El cambio queda registrado en auditoría."
                    ),
                )
                dates = st.columns(2)
                valid_from = dates[0].date_input("Vigente desde", value=date.today())
                open_ended = st.checkbox("Vigencia sin fecha final", value=True)
                valid_to = dates[1].date_input(
                    "Vigente hasta", value=date.today(), disabled=open_ended
                )
                observation = st.text_area(
                    "Justificación del cambio",
                    placeholder="Explique la razón y el efecto esperado de la modificación.",
                )
                save = st.form_submit_button("Guardar cambios", type="primary")
            if save:
                value_changed = new_value != current_values[editing]
                enabled_changed = enabled != bool(selected["enabled"])
                if not observation.strip():
                    st.warning("La justificación del cambio es obligatoria.")
                elif not value_changed and not enabled_changed:
                    st.warning("Modifique el valor o el estado habilitado antes de guardar.")
                elif value_changed and enabled_changed:
                    st.warning(
                        "Guarde por separado el cambio de valor y el cambio de estado. "
                        "El valor requiere aprobación; la habilitación se aplica de inmediato."
                    )
                else:
                    try:
                        version_id = None
                        if value_changed:
                            version_id = workflow.propose(
                                actor_user_id, editing, new_value, valid_from,
                                None if open_ended else valid_to, observation.strip(),
                            )
                            workflow.submit(actor_user_id, version_id)
                        if enabled_changed:
                            workflow.set_enabled(
                                actor_user_id, editing, enabled, observation.strip()
                            )
                        st.session_state.pop("editing_parameter", None)
                        messages = []
                        if version_id is not None:
                            messages.append(
                                f"versión {version_id} enviada a aprobación"
                            )
                        if enabled_changed:
                            messages.append("estado habilitado actualizado con auditoría")
                        st.success("; ".join(messages).capitalize() + ".")
                    except Exception as exc:
                        st.error(str(exc))
        st.markdown("### Historial de versiones")
        if versions.empty:
            st.info("Todavía no existen versiones registradas.")
        else:
            _table(versions, "productive-parameter-versions")
        enabled_history = pd.DataFrame(
            workflow.list_enabled_history(actor_user_id, selected_code)
        )
        st.markdown("### Historial de habilitación")
        if enabled_history.empty:
            st.info("Este parámetro no tiene cambios de habilitación registrados.")
        else:
            _table(enabled_history, "productive-parameter-enabled-history")
        return
    data = _parameter_data()
    status = st.multiselect("Estado del parámetro", sorted(data["Estado"].unique()), placeholder="Todos")
    if status:
        data = data.loc[data["Estado"].isin(status)]
    _table(data, "parameters")
    selected_code = st.selectbox("Parámetro que desea modificar", data["Código"].tolist())
    if st.button("Editar parámetro", key="edit-parameter"):
        st.session_state["editing_parameter"] = selected_code

    editing = st.session_state.get("editing_parameter")
    if not editing:
        return
    current = _parameter_data().loc[_parameter_data()["Código"].eq(editing)].iloc[0]
    st.markdown(f"### Editar: {current['Parámetro']}")
    with st.form("explicit-parameter-form"):
        columns = st.columns(3)
        columns[0].text_input("Código", value=str(current["Código"]), disabled=True)
        columns[1].text_input("Valor actual", value=str(current["Valor"]), disabled=True)
        new_value = columns[2].text_input("Nuevo valor", value=str(current["Valor"]))
        enabled = st.checkbox(
            "Regla habilitada", value=bool(current["Habilitado"])
        )
        valid_from = st.date_input("Vigente desde")
        observation = st.text_area(
            "Justificación del cambio",
            placeholder="Explique la razón y el efecto esperado de la modificación.",
        )
        save = st.form_submit_button("Guardar cambios", type="primary")
    if save:
        value_changed = new_value != str(current["Valor"])
        enabled_changed = enabled != bool(current["Habilitado"])
        if not observation.strip():
            st.warning("La justificación del cambio es obligatoria.")
        elif not value_changed and not enabled_changed:
            st.warning("Modifique el valor o el estado habilitado antes de guardar.")
        elif value_changed and enabled_changed:
            st.warning(
                "Guarde por separado el cambio de valor y el cambio de estado."
            )
        else:
            st.success(
                f"Modo demo: cambio de {editing} preparado desde {valid_from}. "
                "En producción quedará registrado con su flujo y auditoría."
            )


def _parameter_approval_panel(runtime, actor_user_id: int) -> None:
    pending = pd.DataFrame(
        runtime.rule_parameter_workflows.list_versions(
            actor_user_id, "PENDIENTE_APROBACION"
        )
    )
    st.markdown("### Parámetros pendientes")
    if pending.empty:
        st.info("No hay cambios de parámetros pendientes de aprobación.")
        return
    _table(pending, "pending-parameter-versions")
    selected_id = st.selectbox(
        "Versión de parámetro", pending["version_id"].astype(int).tolist(),
        key="pending-parameter-version",
    )
    concept = st.text_area(
        "Concepto de la decisión", key="parameter-approval-concept",
        placeholder="Explique el motivo de la aprobación o el rechazo.",
    )
    columns = st.columns(2)
    approve = columns[0].button("Aprobar parámetro", type="primary")
    reject = columns[1].button("Rechazar parámetro")
    if approve or reject:
        if not concept.strip():
            st.warning("El concepto de la decisión es obligatorio.")
            return
        try:
            runtime.rule_parameter_workflows.decide(
                actor_user_id, int(selected_id), approve, concept.strip()
            )
            st.success("La decisión fue registrada con trazabilidad.")
        except Exception as exc:
            st.error(str(exc))


def _team_approval_panel(runtime, actor_user_id: int) -> None:
    requests = pd.DataFrame(
        runtime.team_change_requests.list_for_actor(actor_user_id)
    )
    st.markdown("### Incorporaciones de ejecutivos pendientes")
    if requests.empty or "status" not in requests.columns:
        st.info("No hay incorporaciones pendientes de aprobación.")
        return
    pending = requests.loc[
        requests["status"].astype("string").eq("PENDIENTE_APROBACION")
    ]
    if pending.empty:
        st.info("No hay incorporaciones pendientes de aprobación.")
        return
    _table(pending, "pending-team-requests")
    request_id = st.selectbox(
        "Solicitud de incorporación", pending["request_id"].astype(int).tolist(),
        key="pending-team-request",
    )
    concept = st.text_area(
        "Concepto sobre la incorporación", key="team-approval-concept",
        placeholder="Explique el motivo de la aprobación o el rechazo.",
    )
    columns = st.columns(2)
    approve = columns[0].button("Aprobar incorporación", type="primary")
    reject = columns[1].button("Rechazar incorporación")
    if approve or reject:
        if not concept.strip():
            st.warning("El concepto de la decisión es obligatorio.")
            return
        try:
            runtime.team_change_requests.decide(
                actor_user_id, int(request_id), approve, concept.strip()
            )
            st.success(
                "La decisión fue registrada."
                + (" El ejecutivo quedó creado y asignado al jefe." if approve else "")
            )
        except Exception as exc:
            st.error(str(exc))


def _manual_input_form(
    profile: str, key: str, runtime=None, actor_user_id: int | None = None
) -> None:
    input_options = (
        ["Pisos y metas"]
        if profile == "Jefe de ventas"
        else ["Pisos y metas", "Fechas de corte"]
    )
    input_type = st.selectbox("Tipo de insumo", input_options, key=f"input-type-{key}")
    if input_type == "Pisos y metas":
        columns = st.columns(5)
        executive = columns[0].text_input("Ejecutivo", key=f"input-executive-{key}")
        manager = columns[1].text_input("Jefe asociado", key=f"input-manager-{key}")
        line = columns[2].selectbox("Línea", COMMISSION_LINES, key=f"input-line-{key}")
        period = columns[3].text_input(
            "Periodo", placeholder="AAAA-MM", help=PERIOD_HELP, key=f"input-period-{key}"
        )
        floor = columns[4].number_input("Piso", min_value=0.0, key=f"input-floor-{key}")
        goal = st.number_input("Meta", min_value=0.0, key=f"input-goal-{key}")
        complete = floor > 0 or goal > 0
    else:
        columns = st.columns(3)
        cut_period = columns[0].text_input(
            "Periodo", placeholder="AAAA-MM", help=PERIOD_HELP, key=f"cut-period-{key}"
        )
        start_date = columns[1].date_input("Inicio del corte", key=f"cut-start-{key}")
        end_date = columns[2].date_input("Fin del corte", key=f"cut-end-{key}")
        complete = bool(cut_period.strip()) and start_date <= end_date
    observation = st.text_area(
        "Observación y motivo",
        placeholder="Explique el origen y la razón del registro o actualización.",
        key=f"input-observation-{key}",
    )
    if st.button("Guardar y enviar a aprobación", type="primary", key=f"input-save-{key}"):
        if not complete or not observation.strip():
            st.warning("Complete los valores requeridos y registre la observación.")
        elif runtime is not None and actor_user_id is not None:
            try:
                if input_type == "Pisos y metas":
                    frame = pd.DataFrame([{
                        "periodo": period,
                        "ejecutivo": executive,
                        "linea_comision": line,
                        "meta": goal,
                        "piso": floor,
                        "jefe": manager,
                    }])
                    batch = runtime.target_workflows.create_batch(
                        frame, actor_user_id, "registro-manual", observation
                    )
                    batch = runtime.target_workflows.submit(batch.batch_id, actor_user_id)
                else:
                    frame = pd.DataFrame([{
                        "inicio_corte": start_date,
                        "fin_corte": end_date,
                        "periodo_corte": cut_period,
                    }])
                    batch = runtime.cut_period_workflows.create_batch(
                        frame, actor_user_id, "registro-manual", observation
                    )
                    batch = runtime.cut_period_workflows.submit(batch.batch_id, actor_user_id)
                st.success(f"Lote {batch.batch_id} guardado en estado {batch.status}.")
            except Exception as exc:
                st.error(str(exc))
        else:
            destination = "aprobación del Parametrizador" if profile != "Parametrizador" else "aprobación"
            st.success(f"Modo demo: {input_type} guardado y enviado a {destination}.")


def _batch_transition_panel(data: pd.DataFrame, workflow, actor_user_id: int, key: str) -> None:
    if data.empty:
        st.info("No hay versiones registradas.")
        return
    _table(data, f"{key}-versions")
    selected = st.selectbox(
        "Lote", data["batch_id"].astype(int).tolist(), key=f"{key}-batch"
    )
    action = st.selectbox(
        "Acción", ["Enviar a verificación", "Verificar", "Enviar a aprobación", "Aprobar"],
        key=f"{key}-transition",
    )
    if st.button("Guardar cambio de estado", type="primary", key=f"{key}-apply"):
        methods = {
            "Enviar a verificación": workflow.submit,
            "Verificar": workflow.verify,
            "Enviar a aprobación": workflow.request_approval,
            "Aprobar": workflow.approve,
        }
        try:
            batch = methods[action](int(selected), actor_user_id)
            st.success(f"Lote {batch.batch_id} actualizado a {batch.status}.")
        except Exception as exc:
            st.error(str(exc))


def _inputs_workspace(profile: str, runtime=None, actor_user_id: int | None = None) -> None:
    view = st.radio(
        "Vista de trabajo", ["Consultar versiones", "Registro manual", "Cargar archivo"],
        horizontal=True, key="inputs-view",
    )
    if view == "Consultar versiones":
        if runtime is None:
            versions = pd.DataFrame(
                [("Pisos y metas", 3, "APROBADO"), ("Fechas de corte", 2, "PENDIENTE_APROBACION")],
                columns=["Tipo", "Versión", "Estado"],
            )
            _table(versions, "input-versions")
        else:
            target_batches = pd.DataFrame(runtime.target_workflows.list_batches())
            cut_batches = pd.DataFrame(runtime.cut_period_workflows.list_batches())
            input_kind = st.radio(
                "Tipo de versión", ["Pisos y metas", "Fechas de corte"], horizontal=True
            )
            selected_data, workflow, key = (
                (target_batches, runtime.target_workflows, "targets")
                if input_kind == "Pisos y metas"
                else (cut_batches, runtime.cut_period_workflows, "cuts")
            )
            _batch_transition_panel(selected_data, workflow, actor_user_id, key)
    elif view == "Registro manual":
        st.markdown("### Registrar o actualizar un insumo")
        _manual_input_form(profile, "manual", runtime, actor_user_id)
    else:
        st.markdown("### Cargar archivo de pisos, metas o fechas de corte")
        st.caption("La carga crea una versión; no elimina silenciosamente información anterior.")
        columns = st.columns(2)
        input_type = columns[0].selectbox("Contenido del archivo", ["Pisos y metas", "Fechas de corte"])
        load_mode = columns[1].selectbox("Modalidad", ["Carga parcial", "Nueva versión completa"])
        uploaded = st.file_uploader("Archivo CSV o Excel", type=["csv", "xlsx"])
        if input_type == "Pisos y metas":
            st.caption("Columnas requeridas: Ejecutivo, Jefe, Línea, Periodo, Piso y Meta.")
        observation = st.text_area(
            "Observación y motivo de la carga",
            placeholder="Indique origen, periodo y razón de la actualización.",
            key="upload-observation",
        )
        if st.button("Validar y cargar", type="primary", key="upload-input"):
            if uploaded is None or not observation.strip():
                st.warning("Seleccione un archivo y registre la observación.")
            elif runtime is not None and actor_user_id is not None:
                try:
                    frame = load_dataframe_from_bytes(
                        uploaded.name, uploaded.getvalue()
                    )
                    if input_type == "Pisos y metas":
                        batch = runtime.target_workflows.create_batch(
                            frame, actor_user_id, uploaded.name, observation
                        )
                        batch = runtime.target_workflows.submit(
                            batch.batch_id, actor_user_id
                        )
                    else:
                        batch = runtime.cut_period_workflows.create_batch(
                            frame, actor_user_id, uploaded.name, observation
                        )
                        batch = runtime.cut_period_workflows.submit(
                            batch.batch_id, actor_user_id
                        )
                    st.success(
                        f"Archivo validado como lote {batch.batch_id} en estado {batch.status}."
                    )
                except Exception as exc:
                    st.error(str(exc))
            else:
                st.success(
                    f"Modo demo: {input_type} recibido como {load_mode.lower()}. "
                    "La validación mostrará registros válidos y errores antes de aprobar."
                )


def _commercial_identity_workspace(runtime, actor_user_id: int) -> None:
    profiles = runtime.user_administration.list_profiles(actor_user_id, date.today())
    entities = pd.DataFrame(runtime.hierarchy.list_entities(actor_user_id))
    st.markdown("### Identidades comerciales")
    st.caption(
        "El usuario de dominio y el código comercial son datos distintos y se vinculan con vigencia."
    )
    if entities.empty:
        st.info("No hay entidades comerciales registradas.")
    else:
        _table(entities, "commercial-entities")
    if not profiles:
        st.warning("Primero debe crear el usuario interno.")
        return
    with st.form("commercial-identity-form"):
        user_id = st.selectbox(
            "Usuario interno", [profile.user_id for profile in profiles],
            format_func=lambda selected: next(
                f"{profile.domain_username} - {profile.first_names} {profile.last_names}"
                for profile in profiles if profile.user_id == selected
            ),
        )
        columns = st.columns(2)
        code = columns[0].text_input("Código comercial")
        entity_type = columns[1].selectbox(
            "Tipo comercial", [ROLE_EXECUTIVE, ROLE_MANAGER, ROLE_DIRECTOR]
        )
        display_name = st.text_input("Nombre para mostrar")
        dates = st.columns(2)
        valid_from = dates[0].date_input("Vigente desde", value=date.today())
        open_ended = st.checkbox("Vigencia sin fecha final", value=True)
        valid_to = dates[1].date_input(
            "Vigente hasta", value=date.today(), disabled=open_ended
        )
        save = st.form_submit_button("Guardar identidad comercial", type="primary")
    if save:
        if not code.strip() or not display_name.strip():
            st.warning("Código comercial y nombre son obligatorios.")
            return
        try:
            runtime.user_administration.create_commercial_identity(
                actor_user_id, int(user_id), code, entity_type, display_name,
                valid_from, None if open_ended else valid_to,
            )
            st.success("Identidad comercial creada y vinculada con vigencia.")
        except Exception as exc:
            st.error(str(exc))


def _hierarchy_workspace(runtime, actor_user_id: int) -> None:
    assignments = pd.DataFrame(runtime.hierarchy.list_assignments(actor_user_id))
    st.markdown("### Jerarquía comercial con vigencias")
    st.caption(
        "Las reasignaciones cierran la vigencia anterior; ninguna relación histórica se elimina."
    )
    if assignments.empty:
        st.info("No hay relaciones jerárquicas registradas.")
    else:
        _table(assignments, "hierarchy-history")
    with st.form("hierarchy-explicit-form"):
        relation_label = st.selectbox(
            "Tipo de relación", ["Ejecutivo → Jefe", "Jefe → Gerente"]
        )
        relationship = (
            REL_EXECUTIVE_MANAGER
            if relation_label == "Ejecutivo → Jefe"
            else REL_MANAGER_DIRECTOR
        )
        source_type, target_type = (
            (ROLE_EXECUTIVE, ROLE_MANAGER)
            if relationship == REL_EXECUTIVE_MANAGER
            else (ROLE_MANAGER, ROLE_DIRECTOR)
        )
        sources = runtime.hierarchy.list_entities(actor_user_id, source_type)
        targets = runtime.hierarchy.list_entities(actor_user_id, target_type)
        if not sources or not targets:
            st.warning(
                "Debe crear y vincular entidades comerciales de ambos niveles antes de asignar."
            )
            st.form_submit_button("Guardar relación", disabled=True)
            return
        columns = st.columns(2)
        source_id = columns[0].selectbox(
            "Entidad origen", [int(item["entity_id"]) for item in sources],
            format_func=lambda entity_id: next(
                f"{item['code']} - {item['name']}" for item in sources
                if int(item["entity_id"]) == entity_id
            ),
        )
        target_id = columns[1].selectbox(
            "Entidad destino", [int(item["entity_id"]) for item in targets],
            format_func=lambda entity_id: next(
                f"{item['code']} - {item['name']}" for item in targets
                if int(item["entity_id"]) == entity_id
            ),
        )
        action = st.radio(
            "Acción", ["Asignación inicial", "Reasignar"], horizontal=True
        )
        effective = st.date_input("Vigente desde", value=date.today())
        open_ended = st.checkbox("Vigencia sin fecha final", value=True)
        valid_to = st.date_input(
            "Vigente hasta", value=date.today(), disabled=open_ended
        )
        reason = st.text_area(
            "Observación obligatoria",
            placeholder="Explique la asignación o el motivo de la reasignación.",
        )
        save = st.form_submit_button("Guardar relación", type="primary")
    if save:
        if not reason.strip():
            st.warning("La observación es obligatoria.")
            return
        try:
            if action == "Asignación inicial":
                runtime.hierarchy.assign(
                    actor_user_id, int(source_id), int(target_id), relationship,
                    effective, None if open_ended else valid_to,
                )
            else:
                runtime.hierarchy.reassign(
                    actor_user_id, int(source_id), int(target_id), relationship,
                    effective, reason.strip(),
                )
            st.success("Relación jerárquica guardada con vigencia e historial.")
        except Exception as exc:
            st.error(str(exc))


def _use_directory_user(record: dict[str, str]) -> None:
    st.session_state["directory_user_prefill"] = record
    st.session_state["editing_user_id"] = "NEW"
    st.session_state["admin-users-view"] = "Usuarios"


def _directory_search_workspace(runtime, actor_user_id: int) -> None:
    if runtime.directory_lookup is None:
        st.info(
            "La búsqueda corporativa no está configurada. Puede continuar creando "
            "usuarios manualmente en la vista Usuarios. Para habilitarla configure "
            "LDAP_SEARCH_USER y LDAP_SEARCH_PASSWORD en el servidor."
        )
        return
    st.markdown("### Buscar usuario corporativo")
    st.caption(
        "La búsqueda consulta Active Directory y solo precarga atributos; no crea "
        "usuarios ni almacena contraseñas."
    )
    columns = st.columns([3, 1])
    query = columns[0].text_input(
        "Nombre, correo o usuario de dominio", key="directory-search-query"
    )
    search = columns[1].button(
        "Buscar en Active Directory", type="primary", use_container_width=True
    )
    if search:
        try:
            records = runtime.directory_lookup.search(actor_user_id, query)
            st.session_state["directory_search_results"] = [
                vars(item) for item in records
            ]
        except Exception as exc:
            st.error(str(exc))
    records = st.session_state.get("directory_search_results", [])
    if not records:
        if search:
            st.warning("No se encontraron usuarios con ese criterio.")
        return
    data = pd.DataFrame(records).rename(columns={
        "domain_username": "Usuario de dominio",
        "first_names": "Nombres",
        "last_names": "Apellidos",
        "email": "Correo",
        "identification": "Identificación",
        "external_identifier": "Identificador AD",
    })
    _table(data, "directory-search-results")
    selected = st.selectbox(
        "Usuario encontrado",
        list(range(len(records))),
        format_func=lambda index: (
            f"{records[index]['domain_username']} - "
            f"{records[index]['first_names']} {records[index]['last_names']}"
        ),
    )
    st.button(
        "Usar datos y crear perfil interno",
        on_click=_use_directory_user,
        args=(records[int(selected)],),
        type="primary",
    )


def _admin_users_workspace(runtime=None, actor_user_id: int | None = None) -> None:
    if runtime is not None and actor_user_id is not None:
        view = st.radio(
            "Vista de gestión",
            [
                "Usuarios", "Buscar en Active Directory",
                "Identidades comerciales", "Jerarquía comercial",
            ],
            horizontal=True, key="admin-users-view",
        )
        if view == "Buscar en Active Directory":
            _directory_search_workspace(runtime, actor_user_id)
            return
        if view == "Identidades comerciales":
            _commercial_identity_workspace(runtime, actor_user_id)
            return
        if view == "Jerarquía comercial":
            _hierarchy_workspace(runtime, actor_user_id)
            return
        profiles = runtime.user_administration.list_profiles(
            actor_user_id, date.today()
        )
        users = pd.DataFrame([
            {
                "ID": profile.user_id,
                "Usuario de dominio": profile.domain_username,
                "Identificación": profile.identification,
                "Nombres": profile.first_names,
                "Apellidos": profile.last_names,
                "Correo": profile.email,
                "Acceso activo": profile.active,
                "Estado laboral": profile.employment_status,
                "Roles": ", ".join(profile.roles),
                "Entidades comerciales": ", ".join(profile.commercial_entities),
                "Último acceso": profile.last_access_at,
            }
            for profile in profiles
        ])
        st.markdown("### Usuarios, roles y acceso")
        if users.empty:
            st.info("No hay usuarios internos registrados.")
        else:
            _table(users, "productive-admin-users")
        selected_id = None
        if not users.empty:
            selected_id = st.selectbox(
                "Usuario", users["ID"].astype(int).tolist(),
                format_func=lambda user_id: (
                    f"{user_id} - "
                    f"{users.set_index('ID').loc[user_id, 'Usuario de dominio']}"
                ),
                key="productive-admin-user-selected",
            )
        actions = st.columns(3)
        if actions[0].button(
            "Editar usuario", disabled=selected_id is None,
            key="edit-productive-user",
        ):
            st.session_state["editing_user_id"] = int(selected_id)
        if actions[1].button("Crear usuario", key="create-productive-user"):
            st.session_state["editing_user_id"] = "NEW"
        if actions[2].button(
            "Gestionar permisos", disabled=selected_id is None,
            key="permissions-productive-user",
        ):
            st.session_state["editing_user_permissions_id"] = int(selected_id)
        permission_user_id = st.session_state.get("editing_user_permissions_id")
        if permission_user_id is not None:
            selected_profile = next(
                item for item in profiles if item.user_id == int(permission_user_id)
            )
            effective_permissions = runtime.access_repository.effective_permissions(
                int(permission_user_id)
            )
            st.markdown("### Permisos independientes")
            st.caption(
                f"Usuario: {selected_profile.domain_username}. Estos permisos no "
                "se conceden automáticamente por ningún rol."
            )
            with st.form("productive-independent-permissions-form"):
                can_sync = st.checkbox(
                    "Actualizar datos desde NetSuite",
                    value=PERM_NETSUITE_SYNC in effective_permissions,
                )
                can_calculate = st.checkbox(
                    "Recalcular resultados financieros",
                    value=PERM_CALCULATION_EXECUTE in effective_permissions,
                )
                can_send = st.checkbox(
                    "Enviar archivo final de pago",
                    value=PERM_PAYMENT_FILE_SEND in effective_permissions,
                )
                permission_reason = st.text_area(
                    "Observación obligatoria",
                    placeholder="Explique por qué concede o retira estos permisos.",
                )
                save_permissions = st.form_submit_button(
                    "Guardar permisos", type="primary"
                )
            if save_permissions:
                try:
                    runtime.user_administration.set_independent_permissions(
                        actor_user_id,
                        int(permission_user_id),
                        {
                            PERM_NETSUITE_SYNC: can_sync,
                            PERM_CALCULATION_EXECUTE: can_calculate,
                            PERM_PAYMENT_FILE_SEND: can_send,
                        },
                        permission_reason,
                    )
                    st.session_state.pop("editing_user_permissions_id", None)
                    st.success("Permisos guardados con observación y auditoría.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        editing = st.session_state.get("editing_user_id")
        if editing is None:
            return
        profile = (
            next(item for item in profiles if item.user_id == int(editing))
            if editing != "NEW" else None
        )
        prefill = (
            st.session_state.get("directory_user_prefill", {})
            if profile is None else {}
        )
        role_options = [
            ROLE_EXECUTIVE, ROLE_MANAGER, ROLE_DIRECTOR, ROLE_REVIEWER,
            ROLE_PARAMETERIZER, ROLE_ADMIN,
        ]
        with st.form("productive-admin-user-form"):
            domain_username = st.text_input(
                "Usuario de dominio",
                value=(
                    str(prefill.get("domain_username", ""))
                    if profile is None else profile.domain_username
                ),
                disabled=profile is not None,
            )
            identity = st.text_input(
                "Número de identificación",
                value=(
                    str(prefill.get("identification", ""))
                    if profile is None else (profile.identification or "")
                ),
            )
            name_columns = st.columns(2)
            first_names = name_columns[0].text_input(
                "Nombres", value=(
                    str(prefill.get("first_names", ""))
                    if profile is None else profile.first_names
                )
            )
            last_names = name_columns[1].text_input(
                "Apellidos", value=(
                    str(prefill.get("last_names", ""))
                    if profile is None else profile.last_names
                )
            )
            email = st.text_input(
                "Correo electrónico", value=(
                    str(prefill.get("email", ""))
                    if profile is None else (profile.email or "")
                )
            )
            ad_identifier = st.text_input(
                "Identificador de Active Directory",
                value=(
                    str(prefill.get("external_identifier", ""))
                    if profile is None else (profile.ad_identifier or "")
                ),
            )
            roles = st.multiselect(
                "Roles asignados", role_options,
                default=[] if profile is None else list(profile.roles),
            )
            states = st.columns(2)
            access_active = states[0].checkbox(
                "Acceso activo", value=True if profile is None else profile.active
            )
            employment_status = states[1].selectbox(
                "Estado laboral", ["ACTIVO", "RETIRADO"],
                index=0 if profile is None or profile.employment_status == "ACTIVO" else 1,
            )
            effective = st.date_input(
                "Fecha efectiva del estado laboral", value=date.today()
            )
            reason = st.text_area(
                "Observación obligatoria",
                placeholder="Explique la creación, modificación o deshabilitación.",
            )
            save = st.form_submit_button("Guardar usuario", type="primary")
        if save:
            if not domain_username.strip() or not first_names.strip() or not last_names.strip():
                st.warning("Usuario de dominio, nombres y apellidos son obligatorios.")
            elif not roles:
                st.warning("Seleccione al menos un rol.")
            elif not reason.strip():
                st.warning("La observación es obligatoria.")
            else:
                try:
                    if profile is None:
                        user_id = runtime.user_administration.create(
                            actor_user_id, domain_username, first_names, last_names,
                            roles, identity or None, email or None,
                            ad_identifier or None, reason.strip(),
                        )
                        if not access_active:
                            runtime.user_administration.set_active(
                                actor_user_id, user_id, False, reason.strip()
                            )
                        if employment_status == "RETIRADO":
                            runtime.user_administration.change_employment_status(
                                actor_user_id, user_id, employment_status,
                                effective, reason.strip(),
                            )
                    else:
                        runtime.user_administration.update(
                            actor_user_id, profile.user_id, identity or None,
                            first_names, last_names, email or None,
                            ad_identifier or None, roles, reason.strip(),
                        )
                        if access_active != profile.active:
                            runtime.user_administration.set_active(
                                actor_user_id, profile.user_id, access_active,
                                reason.strip(),
                            )
                        if employment_status != profile.employment_status:
                            runtime.user_administration.change_employment_status(
                                actor_user_id, profile.user_id, employment_status,
                                effective, reason.strip(),
                            )
                    st.session_state.pop("editing_user_id", None)
                    st.session_state.pop("directory_user_prefill", None)
                    st.success("Usuario guardado con historial y auditoría.")
                except Exception as exc:
                    st.error(str(exc))
        return
    users = pd.DataFrame(
        [
            ("J-01", "Laura Méndez", "JEFE", "ACTIVO", "2026-01-01", "", ""),
            ("J-02", "Miguel Díaz", "JEFE", "ACTIVO", "2026-01-01", "", ""),
            ("EJ-01", "Ana Torres", "EJECUTIVO", "ACTIVO", "2026-01-01", "", "J-01"),
            ("EJ-02", "Carlos Ruiz", "EJECUTIVO", "ACTIVO", "2026-01-01", "", "J-01"),
        ],
        columns=[
            "Código", "Nombre", "Rol", "Estado", "Fecha inicio vigencia",
            "Fecha de inactivación", "Jefe",
        ],
    )
    st.markdown("### Usuarios y estructura comercial")
    _table(users, "admin-users")
    selected = st.selectbox("Usuario", users["Código"].tolist(), key="admin-user-selected")
    columns = st.columns(2)
    if columns[0].button("Editar usuario", key="edit-user"):
        st.session_state["editing_user"] = selected
    if columns[1].button("Crear usuario", key="create-user"):
        st.session_state["editing_user"] = "NEW"
    editing = st.session_state.get("editing_user")
    if not editing:
        return
    existing = users.loc[users["Código"].eq(editing)]
    row = existing.iloc[0] if not existing.empty else None
    with st.form("admin-user-form"):
        code = st.text_input("Código", value="" if row is None else str(row["Código"]), disabled=row is not None)
        name = st.text_input("Nombre", value="" if row is None else str(row["Nombre"]))
        role = st.selectbox(
            "Rol", ["EJECUTIVO", "JEFE", "GERENTE", "REVISOR", "PARAMETRIZADOR", "ADMINISTRADOR"],
            index=0 if row is None else ["EJECUTIVO", "JEFE", "GERENTE", "REVISOR", "PARAMETRIZADOR", "ADMINISTRADOR"].index(str(row["Rol"])),
        )
        status = st.selectbox("Estado", ["ACTIVO", "INACTIVO"], index=0 if row is None or row["Estado"] == "ACTIVO" else 1)
        effective_label = (
            "Fecha efectiva de inactivación" if status == "INACTIVO"
            else "Fecha de inicio de vigencia"
        )
        effective = st.date_input(effective_label)
        reason = st.text_area(
            "Observación obligatoria",
            placeholder="Explique la creación, modificación o deshabilitación.",
        )
        save = st.form_submit_button("Guardar usuario", type="primary")
    if save:
        if not code.strip() or not name.strip() or not reason.strip():
            st.warning("Código, nombre y observación son obligatorios.")
        else:
            st.success(
                f"Modo demo: {code} guardado como {role} en estado {status} desde {effective}. "
                "La relación histórica anterior se conserva."
            )


def _manager_team_workspace(
    floor: pd.DataFrame, section: str, runtime=None, actor_user_id: int | None = None
) -> None:
    if section == "Ejecutivos a cargo":
        if runtime is not None and actor_user_id is not None:
            team = pd.DataFrame(
                runtime.team_change_requests.list_team(actor_user_id, date.today())
            )
            requests = pd.DataFrame(
                runtime.team_change_requests.list_for_actor(actor_user_id)
            )
            st.markdown("### Ejecutivos asociados al Jefe autenticado")
            if team.empty:
                st.info("No hay ejecutivos vigentes asociados a este jefe.")
            else:
                _table(team, "productive-manager-team")
            st.markdown("### Solicitudes de incorporación")
            if requests.empty:
                st.info("No hay solicitudes registradas.")
            else:
                _table(requests, "productive-manager-team-requests")
            if st.button("Registrar nuevo ejecutivo", key="propose-team-member"):
                st.session_state["proposing_team_member"] = True
            if not st.session_state.get("proposing_team_member"):
                return
            with st.form("productive-team-request-form"):
                columns = st.columns(2)
                domain_username = columns[0].text_input("Usuario de dominio")
                commercial_code = columns[1].text_input("Código del ejecutivo")
                names = st.columns(2)
                first_names = names[0].text_input("Nombres")
                last_names = names[1].text_input("Apellidos")
                details = st.columns(2)
                identification = details[0].text_input("Número de identificación")
                email = details[1].text_input("Correo electrónico")
                valid_from = st.date_input("Vigente desde", value=date.today())
                reason = st.text_area(
                    "Observación obligatoria",
                    placeholder="Explique la incorporación solicitada.",
                )
                save = st.form_submit_button(
                    "Guardar y solicitar aprobación", type="primary"
                )
            if save:
                try:
                    request_id = runtime.team_change_requests.propose_new_executive(
                        actor_user_id, domain_username, commercial_code,
                        first_names, last_names, identification or None,
                        email or None, valid_from, reason,
                    )
                    st.session_state.pop("proposing_team_member", None)
                    st.success(
                        f"Solicitud {request_id} enviada al parametrizador. "
                        "El ejecutivo aún no está activo."
                    )
                except Exception as exc:
                    st.error(str(exc))
            return
        st.markdown("### Ejecutivos asociados al Jefe autenticado")
        st.caption("El Jefe asignado es automático. Registrar o editar genera aprobación del Parametrizador.")
        team = _team_data()
        _table(team, "manager-team")
        selected = st.selectbox("Ejecutivo", team["Ejecutivo"].tolist(), key="manager-executive")
        action_columns = st.columns(2)
        edit = action_columns[0].button("Editar asociación", key="edit-team-member")
        add = action_columns[1].button("Registrar nuevo ejecutivo", key="add-team-member")
        if edit:
            st.session_state["team_edit_mode"] = "edit"
            st.session_state["team_edit_executive"] = selected
        if add:
            st.session_state["team_edit_mode"] = "add"
        mode = st.session_state.get("team_edit_mode")
        if mode:
            with st.form("team-explicit-form"):
                current = st.session_state.get("team_edit_executive", "") if mode == "edit" else ""
                executive = st.text_input("Código del ejecutivo", value=current, disabled=mode == "edit")
                name = st.text_input("Nombre del ejecutivo")
                identification = st.text_input("Número de identificación")
                email = st.text_input("Correo electrónico")
                st.text_input("Jefe asignado", value="J-01", disabled=True)
                status = st.selectbox("Estado", ["ACTIVO", "INACTIVO"])
                date_label = (
                    "Fecha efectiva de inactivación" if status == "INACTIVO"
                    else "Fecha de inicio de vigencia"
                )
                effective_date = st.date_input(date_label)
                reason = st.text_area(
                    "Observación obligatoria",
                    placeholder="Explique la asociación, reasignación o deshabilitación.",
                )
                save = st.form_submit_button("Guardar asociación", type="primary")
            if save:
                if not executive.strip() or not reason.strip():
                    st.warning("El ejecutivo y la observación son obligatorios.")
                else:
                    st.success(
                        f"Modo demo: {executive} quedó en estado {status} con fecha efectiva {effective_date} "
                        "y asignación automática a J-01. "
                        "Se creó la solicitud de aprobación para el Parametrizador."
                    )
    else:
        st.markdown("### Pisos y metas de mis ejecutivos")
        st.caption("Puede registrar un valor nuevo o editar uno existente. Ambos requieren aprobación.")
        data = _filter(floor, "manager-targets")
        _table(data, "manager-targets")
        if data.empty:
            st.info("No hay registros disponibles para editar con los filtros seleccionados.")
            return
        options = (
            data["executive"].astype(str) + " | " + data["period"].astype(str)
            + " | " + data["commission_line"].astype(str)
        ).tolist()
        selected = st.selectbox("Registro", options, key="manager-target-record")
        action_columns = st.columns(2)
        if action_columns[0].button("Editar piso y meta", key="edit-manager-target"):
            st.session_state["manager_target_edit"] = selected
            st.session_state["manager_target_mode"] = "edit"
        if action_columns[1].button("Registrar piso y meta", key="create-manager-target"):
            st.session_state["manager_target_mode"] = "add"
        mode = st.session_state.get("manager_target_mode")
        if mode:
            row = data.iloc[options.index(st.session_state["manager_target_edit"])] if mode == "edit" else None
            with st.form("manager-target-form"):
                executive_options = sorted(floor["executive"].astype(str).unique())
                executive = st.selectbox(
                    "Ejecutivo a cargo", executive_options,
                    index=executive_options.index(str(row["executive"])) if row is not None else 0,
                    disabled=row is not None,
                )
                st.text_input("Jefe asociado", value="J-01", disabled=True)
                columns = st.columns(3)
                columns[0].text_input(
                    "Periodo", value="" if row is None else str(row["period"]),
                    disabled=row is not None, placeholder="AAAA-MM", help=PERIOD_HELP,
                )
                line_value = "RETIE/LAP" if row is None else str(row["commission_line"])
                line_options = list(dict.fromkeys([line_value, *COMMISSION_LINES]))
                columns[1].selectbox(
                    "Línea", line_options, index=0, disabled=row is not None,
                )
                new_floor = columns[2].number_input("Piso", min_value=0.0, value=0.0 if row is None else float(row["floor"]))
                new_target = st.number_input("Meta", min_value=0.0, value=0.0 if row is None else float(row["target"]))
                reason = st.text_area("Observación obligatoria")
                save = st.form_submit_button("Guardar piso y meta", type="primary")
            if save:
                if not reason.strip():
                    st.warning("La observación es obligatoria.")
                else:
                    st.success(
                        f"Modo demo: piso {new_floor:,.0f} y meta {new_target:,.0f} guardados para {executive}. "
                        "Se creó la solicitud de aprobación para el Parametrizador."
                    )


def _filter(
    data: pd.DataFrame,
    key: str,
    filter_columns: tuple[str, ...] = ("period", "executive", "commission_line"),
) -> pd.DataFrame:
    filtered = data.copy()
    st.markdown(
        '<div class="filter-heading"><b>Filtros</b><span>Combine criterios si lo necesita</span></div>',
        unsafe_allow_html=True,
    )
    columns = st.columns(len(filter_columns))
    labels = {
        "issue_type": "Tipo de pendiente",
        "period": "Periodo",
        "executive": "Ejecutivo",
        "commission_line": "Línea",
        "decision": "Decisión de pago",
    }
    for index, column in enumerate(filter_columns):
        if column not in filtered.columns:
            continue
        options = sorted(filtered[column].dropna().astype(str).unique())
        selected = columns[index].multiselect(
            labels.get(column, column),
            options,
            placeholder="Todos",
            key=f"{key}-{column}",
        )
        if selected:
            filtered = filtered.loc[filtered[column].astype(str).isin(selected)]
    return filtered


def _apply_demo_scope(data: pd.DataFrame, profile: str) -> pd.DataFrame:
    """Represent the backend scope in demo mode; production uses ResultQueryService."""
    scope = PROFILE_SCOPE.get(profile)
    if scope is None or "executive" not in data.columns:
        return data.copy()
    return data.loc[data["executive"].astype(str).isin(scope)].copy()


def _table(data: pd.DataFrame, key: str) -> None:
    controls = st.columns([3, 1, 1])
    controls[0].markdown(
        f'<div class="table-count"><b>{len(data):,}</b> registros encontrados</div>',
        unsafe_allow_html=True,
    )
    size = controls[1].selectbox("Filas", [10, 25, 50, 100], index=1, key=f"size-{key}")
    pages = max(1, (len(data) + size - 1) // size)
    page = controls[2].number_input("Página", 1, pages, 1, key=f"page-{key}")
    start = (page - 1) * size
    visible = data.iloc[start:start + size].rename(columns=COLUMN_LABELS)
    visible = visible.astype("object").where(pd.notna(visible), "")
    st.dataframe(visible, width="stretch", hide_index=True)
    st.caption(f"Página {page} de {pages}")


def _selection_table(data: pd.DataFrame, key: str) -> pd.DataFrame:
    selection = data.reset_index(drop=True).rename(columns=COLUMN_LABELS).copy()
    selection.insert(0, "Seleccionar", False)
    edited = st.data_editor(
        selection,
        width="stretch",
        hide_index=True,
        disabled=[column for column in selection.columns if column != "Seleccionar"],
        column_config={
            "Seleccionar": st.column_config.CheckboxColumn("Seleccionar", default=False),
        },
        key=f"selection-{key}",
    )
    return edited.loc[edited["Seleccionar"]].drop(columns="Seleccionar")


def _review_actions(
    data: pd.DataFrame,
    profile: str,
    key: str,
    actions: list[str] | None = None,
) -> None:
    st.markdown("### Selección de registros")
    st.caption("Marque uno o varios registros. La acción se aplicará únicamente a la selección.")
    selected = _selection_table(data, key)
    st.markdown(f'<div class="selection-summary"><b>{len(selected)}</b> registros seleccionados</div>', unsafe_allow_html=True)

    if actions is None:
        if profile in {"Parametrizador", "Administrador"}:
            actions = ["Aprobar", "Rechazar", "Devolver para ajuste"]
        else:
            actions = ["Iniciar revisión", "Solicitar información", "Enviar a aprobación", "Cerrar revisión"]
    action_columns = st.columns([1, 2])
    action = action_columns[0].selectbox("Acción", actions, key=f"action-{key}")
    observation = action_columns[1].text_area(
        "Observación o concepto",
        placeholder="Explique la decisión para conservar la trazabilidad.",
        key=f"observation-{key}",
    )
    if st.button("Aplicar a seleccionados", type="primary", key=f"apply-{key}"):
        if selected.empty:
            st.warning("Seleccione al menos un registro.")
        elif not observation.strip():
            st.warning("La observación es obligatoria para aplicar la acción.")
        else:
            st.success(
                f"Modo demo: {action} preparado para {len(selected)} registro(s). "
                "En producción se guardará con usuario, fecha e historial."
            )


def _request_queue_actions(data: pd.DataFrame, workflow, actor_user_id: int) -> None:
    selected = _selection_table(data, "request-queue")
    transitions = {
        "Iniciar revisión": IN_REVIEW,
        "Enviar a aprobación": PENDING_APPROVAL,
        "Aprobar": REQUEST_APPROVED,
        "Rechazar": REQUEST_REJECTED,
        "Marcar resuelta": RESOLVED,
        "Cerrar": CLOSED,
    }
    columns = st.columns([1, 2])
    action = columns[0].selectbox("Acción", list(transitions), key="request-queue-action")
    concept = columns[1].text_area(
        "Observación o concepto",
        placeholder="Explique la decisión para conservar la trazabilidad.",
        key="request-queue-concept",
    )
    if st.button("Aplicar a solicitudes", type="primary"):
        if selected.empty:
            st.warning("Seleccione al menos una solicitud.")
            return
        if not concept.strip():
            st.warning("La observación es obligatoria.")
            return
        id_column = COLUMN_LABELS.get("request_id", "Solicitud")
        completed = 0
        errors = []
        for value in selected[id_column].dropna().unique():
            try:
                workflow.transition_request(
                    actor_user_id, int(value), transitions[action], concept
                )
                completed += 1
            except Exception as exc:
                errors.append(f"Solicitud {value}: {exc}")
        if completed:
            st.success(f"{completed} solicitud(es) actualizada(s).")
        for message in errors:
            st.error(message)


def _exception_queue_actions(data: pd.DataFrame, workflow, actor_user_id: int) -> None:
    selected = _selection_table(data, "exception-queue")
    columns = st.columns([1, 2])
    action = columns[0].selectbox(
        "Decisión", ["Aprobar", "Rechazar"], key="exception-queue-action"
    )
    concept = columns[1].text_area(
        "Concepto obligatorio",
        placeholder="Justifique la aprobación o el rechazo.",
        key="exception-queue-concept",
    )
    if st.button("Aplicar a excepciones", type="primary"):
        if selected.empty:
            st.warning("Seleccione al menos una excepción.")
            return
        if not concept.strip():
            st.warning("El concepto es obligatorio.")
            return
        completed = 0
        errors = []
        for value in selected["exception_id"].dropna().unique():
            try:
                workflow.decide_exception(
                    actor_user_id, int(value), action == "Aprobar", concept
                )
                completed += 1
            except Exception as exc:
                errors.append(f"Excepción {value}: {exc}")
        if completed:
            st.success(f"{completed} excepción(es) decidida(s).")
        for message in errors:
            st.error(message)


def _exception_form(
    data: pd.DataFrame,
    key: str,
    workflow=None,
    actor_user_id: int | None = None,
) -> None:
    st.markdown("### Nueva excepción")
    st.caption("La excepción es puntual, no modifica la regla general y requiere aprobación del Parametrizador.")
    documents = sorted(data.get("document", pd.Series(dtype="string")).dropna().astype(str).unique())
    exception_type = st.selectbox(
        "Tipo de excepción",
        [
            "Dar piso por cumplido",
            "Pagar comisión de una factura",
            "Modificar valor de comisión de una factura",
            "Hacer que una factura sume al cumplimiento",
        ],
        key=f"type-{key}",
    )
    document = None
    executive = ""
    period = ""
    original_result = None
    exceptional_result = "true"
    rule_code = FLOOR_FORCE_COMPLY
    exception_code = EXCEPTION_FLOOR
    if exception_type == "Dar piso por cumplido":
        columns = st.columns(2)
        executive = columns[0].text_input("Ejecutivo", key=f"exception-executive-{key}")
        period = columns[1].text_input(
            "Periodo", placeholder="AAAA-MM", help=PERIOD_HELP,
            key=f"exception-period-{key}",
        )
    else:
        columns = st.columns(2)
        document = columns[0].selectbox(
            "Factura",
            documents or ["Sin documentos disponibles"],
            key=f"document-{key}",
        )
        matching = data.loc[
            data.get(
                "document", pd.Series(pd.NA, index=data.index, dtype="string")
            ).astype("string").eq(document)
        ]
        if not matching.empty:
            source = matching.iloc[0]
            executive = str(source.get("executive", ""))
            period = str(source.get("period", ""))
            original_result = source.get("decision")
        if exception_type == "Modificar valor de comisión de una factura":
            exceptional_result = str(columns[1].number_input(
                "Valor de comisión propuesto", min_value=0.0, key=f"commission-value-{key}"
            ))
            rule_code = COMMISSION_VALUE_EXECUTIVE
            exception_code = EXCEPTION_PAYMENT
        elif exception_type == "Pagar comisión de una factura":
            columns[1].text_input("Resultado propuesto", value="Pagar comisión", disabled=True)
            rule_code = PAYMENT_FORCE_PAY
            exception_code = EXCEPTION_PAYMENT
        else:
            columns[1].text_input(
                "Resultado propuesto", value="Sumar al cumplimiento del periodo", disabled=True
            )
            rule_code = FLOOR_FORCE_INCLUDE
            exception_code = EXCEPTION_FLOOR
    reason = st.text_area(
        "Observación obligatoria y motivo del cambio",
        placeholder="Indique por qué este caso debe apartarse de la regla general.",
        key=f"reason-{key}",
    )
    if st.button("Guardar y enviar al Parametrizador", type="primary", key=f"propose-{key}"):
        if not executive.strip() or not period.strip():
            st.warning("La excepción requiere ejecutivo y periodo.")
        elif not _valid_period(period):
            st.warning("El periodo debe tener formato AAAA-MM.")
        elif not reason.strip():
            st.warning("El motivo de la excepción es obligatorio.")
        elif workflow is not None and actor_user_id is not None:
            try:
                proposed = workflow.propose_exception(
                    actor_user_id,
                    exception_code,
                    executive,
                    period,
                    date.today(),
                    rule_code,
                    None if original_result is None else str(original_result),
                    exceptional_result,
                    reason,
                    document,
                )
                pending = workflow.submit_exception(
                    actor_user_id, proposed.exception_id
                )
                st.success(
                    f"Excepción {pending.exception_id} enviada al Parametrizador."
                )
            except Exception as exc:
                st.error(str(exc))
        else:
            target = document or "el piso seleccionado"
            st.success(
                f"Modo demo: propuesta '{exception_type}' preparada para {target}. "
                "Queda pendiente de aprobación del Parametrizador."
            )


def _review_request_form(
    data: pd.DataFrame,
    key: str,
    workflow=None,
    actor_user_id: int | None = None,
) -> None:
    st.markdown("### Facturas incluidas en la solicitud")
    st.caption("Seleccione una o varias facturas y, si lo necesita, agregue una observación particular por fila.")
    selection = data.reset_index(drop=True).rename(columns=COLUMN_LABELS).copy()
    selection.insert(0, "Seleccionar", False)
    selection["Observación particular"] = ""
    editable = {"Seleccionar", "Observación particular"}
    edited = st.data_editor(
        selection,
        width="stretch",
        hide_index=True,
        disabled=[column for column in selection.columns if column not in editable],
        column_config={
            "Seleccionar": st.column_config.CheckboxColumn("Seleccionar", default=False),
            "Observación particular": st.column_config.TextColumn(
                "Observación particular",
                help="Detalle opcional específico para esta factura.",
            ),
        },
        key=f"request-selection-{key}",
    )
    selected = edited.loc[edited["Seleccionar"]]
    reason = st.text_area(
        "Motivo general de la solicitud",
        placeholder="Describa el motivo común de la revisión solicitada.",
        key=f"request-reason-{key}",
    )
    if st.button("Crear solicitud de revisión", type="primary", key=f"request-submit-{key}"):
        if selected.empty:
            st.warning("Seleccione al menos una factura.")
        elif not reason.strip():
            st.warning("El motivo general es obligatorio.")
        elif workflow is not None and actor_user_id is not None:
            executive_column = COLUMN_LABELS["executive"]
            period_column = COLUMN_LABELS["period"]
            document_column = COLUMN_LABELS["document"]
            decision_column = COLUMN_LABELS["decision"]
            combinations = selected[[executive_column, period_column]].drop_duplicates()
            if len(combinations) != 1:
                st.warning(
                    "Las facturas de una solicitud deben pertenecer al mismo ejecutivo y periodo."
                )
            else:
                executive, period = combinations.iloc[0].tolist()
                items = [
                    {
                        "document": row[document_column],
                        "observation": row["Observación particular"],
                        "previous_result": row.get(decision_column),
                    }
                    for _, row in selected.iterrows()
                ]
                try:
                    request = workflow.create_request(
                        actor_user_id,
                        str(executive),
                        str(period),
                        date.today(),
                        reason,
                        items,
                    )
                    st.success(
                        f"Solicitud {request.request_id} creada con {request.item_count} factura(s)."
                    )
                except Exception as exc:
                    st.error(str(exc))
        else:
            st.success(
                f"Modo demo: solicitud preparada con {len(selected)} factura(s). "
                "En producción quedará pendiente de revisión y con historial."
            )


def _style() -> None:
    st.markdown(
        """
        <style>
        :root { --ink:#102d4a; --muted:#5c7187; --line:#d8e5ec; --soft:#f1f6f8;
                --blue:#1683b9; --navy:#123451; --pale:#f3f7f9; --green:#309a67; --amber:#d59417; }
        html, body, [class*="css"], [data-testid="stAppViewContainer"] {
            font-family:"Segoe UI",Arial,sans-serif; color:var(--ink);
            color-scheme:light !important;
        }
        .stApp { background:var(--pale); }
        [data-testid="stHeader"] { background:rgba(255,255,255,.96); border-bottom:1px solid var(--line); }
        [data-testid="stMainBlockContainer"] { max-width:1400px; padding:1.8rem 2rem 4rem; }
        h1,h2,h3 { color:var(--ink) !important; letter-spacing:-.02em; }
        h1 { font-size:1.72rem !important; margin:.15rem 0 .2rem !important; }
        h2 { font-size:1.2rem !important; }
        [data-testid="stRadio"] label,
        [data-testid="stRadio"] label p,
        [data-testid="stRadio"] [data-testid="stMarkdownContainer"] p {
            color:var(--ink) !important;
        }
        [data-testid="stRadio"] label {
            padding:.42rem .65rem; border-radius:7px;
        }
        [data-testid="stRadio"] label:hover,
        [data-testid="stRadio"] label:has(input:checked) {
            background:#e3f4fa;
        }
        [data-testid="stSidebar"] { background:var(--navy); border-right:0; }
        [data-testid="stSidebar"] > div { padding-top:1.1rem; }
        [data-testid="stSidebar"] *,
        [data-testid="stSidebar"] [data-testid="stRadio"] label p,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
            color:#dbe8f6 !important;
        }
        [data-testid="stSidebar"] [role="radiogroup"] { gap:.28rem; }
        [data-testid="stSidebar"] [data-testid="stRadio"] label {
            padding:.72rem .8rem; border-radius:8px; transition:background .15s ease;
        }
        [data-testid="stSidebar"] [data-testid="stRadio"] label:hover { background:#1f5f82; }
        [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) { background:#1f5f82; }
        .brand { display:flex; align-items:center; gap:.7rem; margin:.15rem .15rem 1.45rem; color:#fff; }
        .brand-mark { display:grid; place-items:center; width:38px; height:38px; border-radius:9px;
                      background:#1f5f82; color:#dff3fb; font-weight:800; }
        .brand strong { display:block; font-size:1.05rem; color:#fff; }
        .brand small { display:block; color:#c4d6e5; font-size:.74rem; }
        .sidebar-footer { margin-top:2rem; padding:1rem .25rem 0; border-top:1px solid #315575;
                          color:#b8ccdc; font-size:.74rem; line-height:1.55; }
        .status-pill { display:inline-block; padding:4px 9px; border-radius:16px; background:#dceff7;
                       color:#12678e; font-weight:700; font-size:.72rem; }
        .app-topbar { background:#fff; border:1px solid var(--line); border-radius:10px; padding:14px 18px;
                      display:flex; justify-content:space-between; align-items:center; margin-bottom:1.5rem; }
        .app-topbar strong { display:block; color:var(--ink); font-size:.98rem; }
        .app-topbar small { color:var(--muted); }
        .user-block { display:flex; align-items:center; gap:.65rem; text-align:right; }
        .avatar { display:grid; place-items:center; width:34px; height:34px; border-radius:50%;
                  background:#e3f4fa; color:var(--blue); font-weight:800; }
        .page-heading { margin:0 0 1.35rem; }
        .eyebrow { margin:0; color:var(--blue); font-size:.76rem; font-weight:800;
                   letter-spacing:.09em; text-transform:uppercase; }
        .page-heading p:last-child { margin:.35rem 0 0; color:var(--muted); }
        [data-testid="stMetric"] { background:#fff; border:1px solid var(--line); border-radius:10px;
                                    padding:16px 18px; box-shadow:none; min-height:112px; }
        [data-testid="stMetric"]:has([data-testid="stMetricValue"]) { border-left:5px solid var(--blue); }
        [data-testid="stMetricLabel"] { color:#536b77; font-weight:700; }
        [data-testid="stMetricValue"] { color:var(--ink); font-size:1.8rem; font-weight:750; }
        .metric-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:.5rem 0 1.7rem; }
        .metric-card { min-height:112px; padding:18px; display:grid; align-content:center; gap:.5rem;
                       background:#fff; border:1px solid var(--line); border-left:5px solid var(--blue);
                       border-radius:10px; }
        .metric-card span { color:#536b77; font-size:.83rem; font-weight:700; }
        .metric-card b { color:var(--ink); font-size:1.8rem; line-height:1.1; }
        [data-testid="stDataFrame"] { background:#fff; border:1px solid var(--line); border-radius:10px;
                                       padding:.35rem; overflow:hidden; }
        [data-testid="stVerticalBlockBorderWrapper"] { background:#fff; border-color:var(--line); border-radius:10px; }
        .table-count { padding:1.95rem 0 .25rem; color:var(--muted); font-size:.84rem; }
        .table-count b { color:var(--ink); }
        .filter-heading { display:flex; justify-content:space-between; align-items:center; margin:.45rem 0 .15rem;
                          padding:.25rem 0; border-bottom:1px solid var(--line); }
        .filter-heading b { color:var(--ink); font-size:.88rem; }
        .filter-heading span { color:var(--muted); font-size:.72rem; }
        div[data-testid="stMultiSelect"] { margin-bottom:-.4rem; }
        div[data-testid="stMultiSelect"] [data-baseweb="select"] > div { min-height:36px; }
        div[data-testid="stMultiSelect"] label p { font-size:.78rem; margin-bottom:0; }
        .selection-summary { margin:.65rem 0 1rem; padding:.7rem .85rem; border-radius:7px;
                             background:#edf8fc; border:1px solid #b9dced; color:var(--ink); }
        div[data-baseweb="select"] > div, [data-testid="stNumberInput"] input {
            border-color:#bdd5e2; border-radius:7px; background:#fff;
        }
        input, textarea, [data-baseweb="input"], [data-baseweb="textarea"] { background:#fff !important; color:var(--ink) !important; }
        [data-testid="stDataFrame"], [data-testid="stDataEditor"] {
            color-scheme:light !important; background-color:#fff !important;
        }
        button, input, textarea, select { color-scheme:light !important; }
        .stMultiSelect [data-baseweb="tag"] { background:#dceff7; color:#12678e; }
        [data-testid="stAlert"] { border-radius:8px; border:1px solid #b9dced; background:#edf8fc; }
        hr { border-color:var(--line); }
        @media(max-width:700px) {
            [data-testid="stMainBlockContainer"] { padding:1rem .85rem 3rem; }
            .app-topbar { align-items:flex-start; }
            .user-block small { display:none; }
            .metric-grid { grid-template-columns:1fr 1fr; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Comisiones Servimeters", page_icon="C", layout="wide")
    _style()
    demo = os.getenv("COMMISSION_APP_DEMO", "false").lower() == "true"
    st.sidebar.markdown(
        '<div class="brand"><span class="brand-mark">C</span>'
        '<span><strong>Comisiones</strong><small>Gestión comercial</small></span></div>',
        unsafe_allow_html=True,
    )
    runtime = None
    application_session = None
    if demo:
        profile = st.sidebar.selectbox(
            "Perfil de prueba", list(PROFILE_NAVIGATION), index=3, key="profile"
        )
    else:
        try:
            runtime = _production_runtime()
        except Exception as exc:
            st.error(f"No fue posible iniciar el runtime: {exc}")
            st.stop()
        application_session = st.session_state.get("application_session")
        if application_session is None:
            st.title("Iniciar sesión")
            st.caption("Use sus credenciales corporativas. El acceso depende además de su perfil interno.")
            with st.form("login-form"):
                username = st.text_input("Usuario de dominio")
                password = st.text_input("Contraseña", type="password")
                submitted = st.form_submit_button("Ingresar", type="primary")
            if submitted:
                try:
                    st.session_state["application_session"] = runtime.sessions.login(
                        username, password
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
            st.stop()
        assigned_profiles = [
            ROLE_PROFILES[role]
            for role in application_session.profile.roles
            if role in ROLE_PROFILES
        ]
        if not assigned_profiles:
            st.error("El usuario no tiene un rol funcional activo.")
            st.stop()
        profile = st.sidebar.selectbox("Vista activa", assigned_profiles, key="profile")
        if st.sidebar.button("Cerrar sesión", use_container_width=True):
            _clear_session_state(st.session_state)
            st.rerun()
    permissions = set()
    if not demo:
        permissions = runtime.access_repository.effective_permissions(
            application_session.profile.user_id
        )
    profile_menus = _menus_for_profile(profile, permissions)
    if st.session_state.get("active_profile") != profile:
        st.session_state["active_profile"] = profile
        menu_key = PROFILE_MENU_KEYS[profile]
        first_menu = next(iter(profile_menus))
        st.session_state[menu_key] = first_menu
        st.session_state[f"active_{menu_key}"] = first_menu
        st.session_state["navigation"] = profile_menus[first_menu][0]
    menu_key = PROFILE_MENU_KEYS[profile]
    menu_label = {
        "Parametrizador": "Menú del parametrizador",
        "Administrador": "Menú del administrador",
        "Jefe de ventas": "Menú del jefe",
    }.get(profile, "Tipo de menú")
    if st.session_state.get(menu_key) not in profile_menus:
        first_menu = next(iter(profile_menus))
        st.session_state[menu_key] = first_menu
        st.session_state[f"active_{menu_key}"] = first_menu
        st.session_state["navigation"] = profile_menus[first_menu][0]
    history = st.session_state.get(f"navigation_history_{profile}", [])
    st.button(
        "← Atrás", disabled=not history, on_click=_navigate_back, args=(profile,),
        key=f"back-{profile}",
    )
    menu = st.radio(
        menu_label,
        list(profile_menus),
        key=menu_key,
        horizontal=True,
    )
    if st.session_state.get(f"active_{menu_key}") != menu:
        st.session_state[f"active_{menu_key}"] = menu
        st.session_state["navigation"] = profile_menus[menu][0]
    navigation_options = profile_menus[menu]
    if st.session_state.get("navigation") not in navigation_options:
        st.session_state["navigation"] = navigation_options[0]
    page = st.radio(
        "Navegación", navigation_options, key="navigation", horizontal=True,
        on_change=_track_navigation, args=(profile,),
    )
    st.session_state.setdefault(f"last_navigation_{profile}", page)
    if demo:
        st.sidebar.markdown(
            '<div class="sidebar-footer"><span class="status-pill">MODO DEMO</span><br><br>'
            'Datos ficticios para validar la interfaz.<br>Sin archivos intermedios.</div>',
            unsafe_allow_html=True,
        )
        provider = _demo_provider()
        run_code = RUN_CODE
        payments = _enrich_demo_payments(provider.commission_results(run_code))
        documents = provider.document_compliance_results(run_code)
        floor = provider.floor_results(run_code)
        reviews = provider.review_results(run_code)
        payments = _attach_demo_hierarchy(payments)
        documents = _attach_demo_hierarchy(documents)
        floor = _attach_demo_hierarchy(floor)
        reviews = _attach_demo_hierarchy(reviews)
        request_records = reviews.copy()
        exception_records = reviews.copy()
        discounts = _demo_discounts()
        discount_applications = _demo_discount_applications()
        user_name = "Usuario de prueba"
    else:
        st.sidebar.markdown("### Resultados")
        calculation_period = st.sidebar.text_input(
            "Periodo de resultados", placeholder="AAAA-MM", help=PERIOD_HELP
        )
        if st.sidebar.button(
            "Consultar resultados existentes", type="primary",
            use_container_width=True,
        ):
            if not calculation_period.strip():
                st.sidebar.error("Indique el periodo en formato AAAA-MM.")
            else:
                try:
                    with st.spinner("Cargando resultados aprobados..."):
                        provider = runtime.calculation_views.load_existing(
                            str(application_session.profile.user_id),
                            calculation_period.strip(),
                        )
                    st.session_state["calculation_provider"] = provider
                    st.session_state["calculation_run_code"] = provider.run_code
                    st.session_state["calculation_provider_is_draft"] = False
                    st.rerun()
                except Exception as exc:
                    st.sidebar.error(str(exc))
        calculation_profiles = {
            ROLE_PROFILES[ROLE_REVIEWER],
            ROLE_PROFILES[ROLE_PARAMETERIZER],
            ROLE_PROFILES[ROLE_ADMIN],
        }
        if (
            profile in calculation_profiles
            and PERM_CALCULATION_EXECUTE in permissions
            and st.sidebar.button("Calcular borrador", use_container_width=True)
        ):
            if not calculation_period.strip():
                st.sidebar.error("Indique el periodo en formato AAAA-MM.")
            else:
                try:
                    with st.spinner("Calculando resultados para revisión..."):
                        provider = runtime.calculation_views.refresh(
                            str(application_session.profile.user_id),
                            calculation_period.strip(),
                        )
                    st.session_state["calculation_provider"] = provider
                    st.session_state["calculation_run_code"] = provider.run_code
                    st.session_state["calculation_provider_is_draft"] = True
                    st.rerun()
                except Exception as exc:
                    st.sidebar.error(str(exc))
        provider = st.session_state.get("calculation_provider")
        run_code = st.session_state.get("calculation_run_code")
        if (
            st.session_state.get("calculation_provider_is_draft", False)
            and profile not in calculation_profiles
        ):
            provider = None
            run_code = None
        if provider is None or not run_code:
            operational_pages = {
                "Panel de parametrización", "Panel de administración", "Parámetros",
                "Aprobaciones", "Insumos y cortes", "Pisos y metas", "Excepciones",
                "Usuarios", "Periodos", "Cierre y archivo", "Ejecutivos a cargo",
                "Sincronización NetSuite",
                "Ejecuciones de cálculo",
            }
            if page not in operational_pages:
                st.info(
                    "Seleccione un periodo y consulte los resultados existentes. "
                    "Si los insumos cambiaron, solicite un recálculo autorizado."
                )
                st.stop()
            payments = pd.DataFrame()
            documents = pd.DataFrame()
            floor = pd.DataFrame()
            reviews = pd.DataFrame()
        else:
            payments = provider.commission_results(run_code)
            documents = provider.document_compliance_results(run_code)
            floor = provider.floor_results(run_code)
            reviews = provider.review_results(run_code)
        user_id = application_session.profile.user_id
        request_records = pd.DataFrame(
            runtime.review_workflows.list_requests(user_id, date.today())
        )
        exception_records = pd.DataFrame(
            runtime.review_workflows.list_exceptions(user_id, date.today()),
            columns=[
                "exception_id", "exception_type", "executive", "period",
                "document", "rule_code", "original_result",
                "exceptional_result", "reason", "status", "proposed_by",
                "approved_by", "created_at", "updated_at", "approved_at",
            ],
        )
        discounts = runtime.discounts.list_discounts()
        discount_applications = runtime.discounts.list_applications()
        payments = _backend_scope(payments, runtime, user_id)
        documents = _backend_scope(documents, runtime, user_id)
        floor = _backend_scope(floor, runtime, user_id)
        reviews = _backend_scope(reviews, runtime, user_id)
        discounts = _backend_scope(discounts, runtime, user_id)
        discount_applications = _backend_scope(discount_applications, runtime, user_id)
        payments = _attach_hierarchy_context(payments, runtime, user_id)
        documents = _attach_hierarchy_context(documents, runtime, user_id)
        floor = _attach_hierarchy_context(floor, runtime, user_id)
        reviews = _attach_hierarchy_context(reviews, runtime, user_id)
        discounts = _attach_hierarchy_context(discounts, runtime, user_id)
        discount_applications = _attach_hierarchy_context(
            discount_applications, runtime, user_id
        )
        user_name = (
            f"{application_session.profile.first_names} "
            f"{application_session.profile.last_names}"
        ).strip()

    st.markdown(
        '<div class="app-topbar"><div><strong>Portal de comisiones</strong>'
        '<small>Resultados y trazabilidad comercial</small></div>'
        f'<div class="user-block"><div><strong>{escape(user_name)}</strong>'
        f'<small>{escape(profile)}</small></div><span class="avatar">U</span></div></div>',
        unsafe_allow_html=True,
    )
    if demo:
        payments = _apply_demo_scope(payments, profile)
        documents = _apply_demo_scope(documents, profile)
        floor = _apply_demo_scope(floor, profile)
        reviews = _apply_demo_scope(reviews, profile)
        discounts = _apply_demo_scope(discounts, profile)
        discount_applications = _apply_demo_scope(discount_applications, profile)
    payments = _restrict_commission_visibility(payments, profile)
    reviews = _restrict_commission_visibility(reviews, profile)
    discounts = _restrict_commission_visibility(discounts, profile)
    discount_applications = _restrict_commission_visibility(
        discount_applications, profile
    )
    page_context = {
        "Panel de parametrización": (
            "Centro de control",
            "Pendientes, reglas vigentes y accesos directos para administrar el cálculo.",
        ),
        "Panel de administración": (
            "Centro de administración",
            "Usuarios, periodos, auditoría y accesos a la configuración del sistema.",
        ),
        "Inicio": ("Vista general", "Resumen del periodo y valores consolidados por ejecutivo."),
        "Cumplimiento": ("Resultados comerciales", "Cumplimiento de pisos y metas por periodo y línea."),
        "Cumplimiento de piso": (
            "Detalle de cumplimiento",
            "Facturas del alcance autorizado, con indicación de si suman y el motivo.",
        ),
        "Pagos": ("Detalle financiero", "Documentos, decisiones y valores calculados por el motor."),
        "Descuentos": (
            "Detalle de descuentos",
            "Anulaciones sin reemplazo, valores aplicados y saldo pendiente.",
        ),
        "Solicitudes": ("Solicitudes de revisión", "Casos propios o del equipo autorizados por la jerarquía."),
        "Revisiones": ("Control operativo", "Casos que requieren validación antes del cierre."),
        "Pisos y metas": ("Insumos comerciales", "Registro, verificación y aprobación según el perfil."),
        "Ejecutivos a cargo": (
            "Gestión del equipo",
            "Registro y vigencia de los ejecutivos asociados al Jefe autenticado.",
        ),
        "Insumos y cortes": (
            "Carga y actualización",
            "Pisos, metas y fechas de corte mediante registro manual o archivo controlado.",
        ),
        "Excepciones": ("Excepciones", "Propuestas y aprobaciones puntuales con trazabilidad."),
        "Parámetros": ("Configuración", "Reglas versionadas y valores vigentes del motor."),
        "Aprobaciones": ("Bandeja de aprobación", "Elementos pendientes de decisión del parametrizador."),
        "Cierre y archivo": ("Cierre del periodo", "Finalización y generación voluntaria del archivo de pago."),
        "Periodos": ("Administración", "Estados, cierres y reaperturas auditadas."),
        "Usuarios": ("Administración", "Usuarios, roles, permisos y estructura comercial."),
        "Auditoría": ("Trazabilidad", "Registro inmutable de acciones sensibles del sistema."),
        "Sincronización NetSuite": (
            "Integración de datos",
            "Actualización manual, controlada y auditable de la fuente operativa.",
        ),
        "Ejecuciones de cálculo": (
            "Procesos financieros",
            "Historial técnico de cálculos, estados y métricas sin duplicar resultados.",
        ),
    }
    eyebrow, description = page_context[page]
    st.markdown(f'<div class="page-heading"><p class="eyebrow">{eyebrow}</p>', unsafe_allow_html=True)
    st.title(page)
    st.markdown(f"<p>{description}</p></div>", unsafe_allow_html=True)

    if page == "Panel de parametrización":
        _parameterizer_dashboard()
    elif page == "Panel de administración":
        _administrator_dashboard()
    elif page == "Parámetros":
        _parameter_editor(
            None if demo else runtime,
            None if demo else application_session.profile.user_id,
        )
    elif page == "Inicio":
        summary = _payment_summary(payments, discount_applications)
        line_summary = _line_payment_summary(payments)
        reviews = payments.loc[
            payments["decision"].astype("string").str.upper().eq("PARA REVISAR")
        ]
        _metric_cards(
            [
                ("Comisiones", _money(summary["commission"].sum())),
                ("Por pagar", _money(summary["payable"].sum())),
                ("Documentos", f'{int(summary["documents"].sum()):,}'.replace(",", ".")),
                ("Para revisión", f"{len(reviews):,}".replace(",", ".")),
            ],
            profile,
        )
        st.subheader("Resumen por ejecutivo, periodo y línea")
        _table(summary, "summary")
        st.button(
            "Ver detalle de descuentos",
            on_click=_open_discounts,
            args=(profile,),
        )
        st.subheader("Cumplimiento de piso por periodo y línea")
        _table(floor, "home-floor-summary")
    elif page in {"Cumplimiento de piso", "Cumplimiento"}:
        view = st.radio(
            "Vista", ["Resumen", "Detalle de facturas", "Solicitar revisión"],
            horizontal=True, key="floor-work-view",
        )
        if view == "Resumen":
            summary_data = _filter(floor, "floor-summary")
            _table(summary_data, "floor-summary-table")
            return
        data = _filter(documents, "floor-detail")
        detail_columns = [
            "document", "document_date", "document_origin", "document_origin_date",
            "credit_note", "credit_note_date", "replacement_document",
            "replacement_document_date",
            "executive", "period", "commission_line", "lot", "service",
            "document_value", "adds_to_floor", "floor_reason", "request_id",
            "request_status", "request_final_concept",
        ]
        hierarchy_columns = [
            column for column in (
                "executive_name", "manager", "manager_name", "director", "director_name"
            ) if column in data.columns
        ]
        visible_columns = [
            column for column in [
                *detail_columns[:8], "executive", *hierarchy_columns,
                *detail_columns[9:],
            ] if column in data.columns
        ]
        if view == "Detalle de facturas":
            _table(data[visible_columns], "floor-detail-table")
        else:
            _review_request_form(
                data[visible_columns], "floor-direct-review",
                None if demo else runtime.review_workflows,
                None if demo else application_session.profile.user_id,
            )
    elif page == "Pagos":
        view = st.radio(
            "Vista", ["Resumen", "Detalle de facturas", "Solicitar revisión"],
            horizontal=True, key="payment-work-view",
        )
        data = _filter(
            payments,
            "payments",
            ("period", "executive", "commission_line", "decision"),
        )
        if view == "Resumen":
            applications = discount_applications
            if not applications.empty and {"executive", "period"}.issubset(data.columns):
                pairs = data[["executive", "period"]].drop_duplicates()
                applications = applications.merge(
                    pairs, on=["executive", "period"], how="inner", validate="m:1"
                )
            _table(_payment_summary(data, applications), "payment-summary-table")
            return
        payment_columns = [
            "document", "document_date", "document_origin", "document_origin_date",
            "credit_note", "credit_note_date", "replacement_document",
            "replacement_document_date",
            "executive", "period", "commission_line", "lot", "service", "document_value",
            "decision", "payment_reason", "payment_document", "payment_type", "paid_value",
            "payment_date", "accounting_date", "payment_days",
            "payment_factor", "commission_rate", "commission_executive", "request_id",
            "request_status", "request_final_concept",
        ]
        hierarchy_columns = [
            column for column in (
                "executive_name", "manager", "manager_name", "director", "director_name"
            ) if column in data.columns
        ]
        visible_columns = [
            column for column in [
                *payment_columns[:8], "executive", *hierarchy_columns,
                *payment_columns[9:],
            ] if column in data.columns
        ]
        if view == "Detalle de facturas":
            _table(data[visible_columns], "payments-table")
        else:
            _review_request_form(
                data[visible_columns], "payment-direct-review",
                None if demo else runtime.review_workflows,
                None if demo else application_session.profile.user_id,
            )
    elif page == "Descuentos":
        data = _filter(
            discounts,
            "discounts",
            ("period", "executive", "detection_period", "status"),
        )
        _table(data, "discounts-table")
    elif page in {"Revisiones", "Solicitudes"}:
        candidates = payments.copy() if page == "Solicitudes" else payments.loc[
            payments["decision"].astype("string").str.upper().eq("PARA REVISAR")
        ].copy()
        candidates = _filter(candidates, "review")
        if page == "Solicitudes":
            views = ["Consultar", "Crear solicitud"]
        else:
            views = ["Consultar", "Revisar", "Crear excepción"]
        view = st.radio("Vista de trabajo", views, horizontal=True, key="review-view")
        if view == "Consultar":
            st.caption("Solicitudes registradas y su estado actual.")
            _table(request_records, "request-table")
        elif view == "Crear solicitud":
            _review_request_form(
                candidates,
                "review",
                None if demo else runtime.review_workflows,
                None if demo else application_session.profile.user_id,
            )
        elif view == "Revisar":
            if demo:
                _review_actions(candidates, profile, "review")
            else:
                _request_queue_actions(
                    request_records,
                    runtime.review_workflows,
                    application_session.profile.user_id,
                )
        else:
            _exception_form(
                candidates,
                "review",
                None if demo else runtime.review_workflows,
                None if demo else application_session.profile.user_id,
            )
    elif page == "Insumos y cortes":
        _inputs_workspace(
            profile,
            None if demo else runtime,
            None if demo else application_session.profile.user_id,
        )
    elif page == "Ejecutivos a cargo":
        _manager_team_workspace(
            floor, page,
            None if demo else runtime,
            None if demo else application_session.profile.user_id,
        )
    elif page == "Usuarios":
        _admin_users_workspace(
            None if demo else runtime,
            None if demo else application_session.profile.user_id,
        )
    elif page == "Periodos":
        _period_workspace(
            None if demo else runtime,
            None if demo else application_session.profile.user_id,
        )
    elif page == "Cierre y archivo":
        _payment_file_workspace(
            profile,
            None if demo else runtime,
            None if demo else application_session.profile.user_id,
            None if demo else provider,
        )
    elif page == "Auditoría":
        _audit_workspace(
            None if demo else runtime,
            None if demo else application_session.profile.user_id,
        )
    elif page == "Sincronización NetSuite":
        _netsuite_sync_workspace(runtime, application_session.profile.user_id)
    elif page == "Ejecuciones de cálculo":
        _calculation_runs_workspace(
            None if demo else runtime,
            None if demo else application_session.profile.user_id,
        )
    elif page == "Pisos y metas":
        if not demo:
            view = st.radio(
                "Vista de trabajo",
                ["Consultar resultados", "Registrar piso y meta", "Versiones y aprobación"],
                horizontal=True,
                key="productive-target-view",
            )
            if view == "Consultar resultados":
                _table(_filter(floor, "targets"), "targets-table")
            elif view == "Registrar piso y meta":
                _manual_input_form(
                    profile,
                    "targets-page",
                    runtime,
                    application_session.profile.user_id,
                )
            else:
                batches = pd.DataFrame(runtime.target_workflows.list_batches())
                _batch_transition_panel(
                    batches,
                    runtime.target_workflows,
                    application_session.profile.user_id,
                    "targets-page",
                )
            return
        if profile == "Jefe de ventas":
            _manager_team_workspace(floor, page)
            return
        allowed_action = {
            "Jefe de ventas": "Registrar o modificar",
            "Revisor": "Verificar",
            "Parametrizador": "Registrar, verificar o aprobar",
            "Administrador": "Administrar",
        }.get(profile, "Consultar")
        st.info(f"Acción habilitada para este perfil: {allowed_action}.")
        data = _filter(floor, "targets")
        views = ["Consultar"]
        if profile in {"Jefe de ventas", "Revisor", "Parametrizador", "Administrador"}:
            views.append("Revisar registros")
        view = st.radio("Vista de trabajo", views, horizontal=True, key="target-view")
        if view == "Revisar registros":
            target_actions = {
                "Jefe de ventas": ["Guardar borrador", "Enviar a verificación"],
                "Revisor": ["Verificar", "Devolver para ajuste", "Enviar a aprobación"],
                "Parametrizador": ["Aprobar", "Rechazar", "Devolver para ajuste"],
                "Administrador": ["Aprobar", "Rechazar", "Devolver para ajuste"],
            }[profile]
            _review_actions(data, profile, "targets", target_actions)
        else:
            _table(data, "targets-table")
    elif page == "Excepciones":
        action = "Aprobar o rechazar" if profile in {"Parametrizador", "Administrador"} else "Proponer"
        st.info(f"Acción habilitada para este perfil: {action}. Solo las excepciones aprobadas afectan el cálculo.")
        views = ["Consultar", "Proponer excepción"]
        if profile in {"Parametrizador", "Administrador"}:
            views.append("Decidir pendientes")
        view = st.radio("Vista de trabajo", views, horizontal=True, key="exception-view")
        if view == "Consultar":
            _table(exception_records, "exceptions-table")
        elif view == "Proponer excepción":
            _exception_form(
                reviews,
                "exceptions",
                None if demo else runtime.review_workflows,
                None if demo else application_session.profile.user_id,
            )
        else:
            if demo:
                _review_actions(reviews, profile, "exceptions")
            else:
                pending = exception_records.loc[
                    exception_records["status"].astype("string").eq(
                        "PENDIENTE_APROBACION"
                    )
                ]
                _exception_queue_actions(
                    pending,
                    runtime.review_workflows,
                    application_session.profile.user_id,
                )
    elif page == "Aprobaciones":
        st.info("Puede decidir cada registro por separado o seleccionar varios para aplicar la misma decisión.")
        if demo:
            _review_actions(reviews, profile, "approvals")
        else:
            _parameter_approval_panel(
                runtime, application_session.profile.user_id
            )
            _team_approval_panel(
                runtime, application_session.profile.user_id
            )
            pending = exception_records.loc[
                exception_records["status"].astype("string").eq(
                    "PENDIENTE_APROBACION"
                )
            ]
            _exception_queue_actions(
                pending,
                runtime.review_workflows,
                application_session.profile.user_id,
            )
    else:
        st.info(
            "Este módulo respeta el permiso del perfil seleccionado. Su operación se conectará "
            "al servicio backend correspondiente, con auditoría y sin usar archivos intermedios."
        )


if __name__ == "__main__":
    main()
