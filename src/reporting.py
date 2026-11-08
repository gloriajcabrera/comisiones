"""Vistas funcionales derivadas de un resultado canónico, sin archivos puente."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

import pandas as pd

from .access_control import (
    AccessDeniedError,
    AuthorizationService,
    CALCULATION_OPERATOR_ROLES,
    PERM_CALCULATION_EXECUTE,
)
from .business_periods import normalize_period
from .engine import CommissionResult


class ReviewContextProvider(Protocol):
    def document_review_context(self) -> list[dict[str, object]]: ...


class ApprovedCalculationExecutor(Protocol):
    def execute_approved_inputs(
        self,
        executed_by: str,
        period: str,
        run_code: str | None = None,
        cancellation_context: object | None = None,
        materialize_reused_result: bool = False,
        require_existing: bool = False,
    ) -> object: ...

    def load_approved_result(self, period: str) -> object: ...


@dataclass(frozen=True)
class CalculationViewService:
    """Materializa vistas bajo demanda sin persistir copias del detalle."""

    calculations: ApprovedCalculationExecutor
    review_context_provider: ReviewContextProvider | None = None
    authorization: AuthorizationService | None = None

    def refresh(
        self,
        executed_by: str,
        period: str,
        run_code: str | None = None,
    ) -> "InMemoryCalculationResultProvider":
        period = self._require_period(period)
        if self.authorization is not None:
            self.authorization.require_permission(
                int(executed_by), PERM_CALCULATION_EXECUTE
            )
            self.authorization.require_any_role(
                int(executed_by), CALCULATION_OPERATOR_ROLES
            )
        return self._materialize(executed_by, period, run_code, False)

    def load_existing(
        self,
        requested_by: str,
        period: str,
    ) -> "InMemoryCalculationResultProvider":
        period = self._require_period(period)
        execution = self.calculations.load_approved_result(period)
        result = getattr(execution, "result", None)
        if result is None:
            raise RuntimeError("El cálculo aprobado no tiene resultado consultable")
        return InMemoryCalculationResultProvider(
            run_code=str(getattr(execution, "run_code")),
            result=result,
            review_context_provider=self.review_context_provider,
        )

    @staticmethod
    def _require_period(period: str) -> str:
        if not str(period).strip():
            raise ValueError("El periodo es obligatorio para consultar o recalcular")
        return normalize_period(period)

    def _materialize(
        self,
        actor: str,
        period: str | None,
        run_code: str | None,
        require_existing: bool,
    ) -> "InMemoryCalculationResultProvider":
        execution = self.calculations.execute_approved_inputs(
            executed_by=actor,
            period=period,
            run_code=run_code,
            materialize_reused_result=True,
            require_existing=require_existing,
        )
        result = getattr(execution, "result", None)
        if result is None:
            raise RuntimeError("El cálculo no produjo un resultado consultable")
        return InMemoryCalculationResultProvider(
            run_code=str(getattr(execution, "run_code")),
            result=result,
            review_context_provider=self.review_context_provider,
        )


@dataclass(frozen=True)
class CalculationReviewDocumentValidator:
    """Valida documentos contra el cálculo aprobado, no contra el frontend."""

    calculation_views: CalculationViewService

    def validate_documents(
        self,
        actor_user_id: int,
        executive_code: str,
        period: str,
        documents: Iterable[str],
    ) -> None:
        normalized_period = normalize_period(period)
        requested = {
            str(document).strip().upper()
            for document in documents
            if str(document).strip()
        }
        if not requested:
            raise ValueError("Debe seleccionar al menos un documento")
        provider = self.calculation_views.load_existing(
            str(actor_user_id), normalized_period
        )
        available = provider.document_compliance_results(provider.run_code)
        executive = str(executive_code).strip().upper()
        allowed = available.loc[
            available["executive"].astype("string").str.strip().str.upper().eq(executive)
            & available["period"].astype("string").str.strip().eq(normalized_period),
            "document",
        ].astype("string").str.strip().str.upper()
        if not requested.issubset(set(allowed.dropna())):
            raise AccessDeniedError(
                "Uno o mas documentos no pertenecen al ejecutivo y periodo autorizados"
            )


def _period_text(values: pd.Series) -> pd.Series:
    return values.astype("string").str.strip()


def _date_text(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    formatted = parsed.dt.strftime("%Y-%m-%d").astype("string")
    return formatted.where(parsed.notna(), pd.NA)


def _project(
    data: pd.DataFrame,
    mapping: dict[str, str],
    defaults: dict[str, object] | None = None,
) -> pd.DataFrame:
    missing = [source for source in mapping if source not in data.columns]
    if missing:
        raise ValueError("Resultado sin columnas requeridas: " + ", ".join(missing))
    output = data[list(mapping)].rename(columns=mapping).copy()
    for column, value in (defaults or {}).items():
        if column not in output.columns:
            output[column] = value
    return output


def _first_available(
    data: pd.DataFrame, candidates: tuple[str, ...], default: object = pd.NA
) -> pd.Series:
    for column in candidates:
        if column in data.columns:
            return data[column]
    return pd.Series(default, index=data.index, dtype="object")


def _coalesce_available(
    data: pd.DataFrame, candidates: tuple[str, ...], default: object = pd.NA
) -> pd.Series:
    result = pd.Series(default, index=data.index, dtype="object")
    for column in candidates:
        if column not in data.columns:
            continue
        values = data[column].replace(r"^\s*$", pd.NA, regex=True)
        result = result.combine_first(values)
    return result


@dataclass
class InMemoryCalculationResultProvider:
    """Expone funciones de reporte sobre un único resultado calculado."""

    run_code: str
    result: CommissionResult
    review_context_provider: ReviewContextProvider | None = None

    def _attach_review_context(self, data: pd.DataFrame) -> pd.DataFrame:
        columns = ["request_id", "request_status", "request_final_concept"]
        if self.review_context_provider is None:
            for column in columns:
                data[column] = pd.NA
            return data
        context = pd.DataFrame(self.review_context_provider.document_review_context())
        if context.empty:
            for column in columns:
                data[column] = pd.NA
            return data
        keys = ["executive", "period", "document"]
        return data.merge(context[keys + columns], on=keys, how="left", validate="m:1")

    def _require_run(self, run_code: str) -> None:
        if run_code != self.run_code:
            raise KeyError(f"Cálculo inexistente o no activo: {run_code}")

    def commission_results(self, run_code: str) -> pd.DataFrame:
        self._require_run(run_code)
        data = self.result.commission_detail
        output = _project(
            data,
            {
                "tranId": "document",
                "entityid": "executive",
                "periodo_mes": "period",
                "linea_comision_key": "commission_line",
                "decision_pago": "decision",
                "valor_comision_ejecutivo": "commission_executive",
                "valor_comision_jefe": "commission_manager",
                "valor_comision_gerente": "commission_director",
                "valor_moneda_local": "document_value",
                "Fecha Real Pago": "payment_date",
                "Fecha Contabilizado Pago": "accounting_date",
                "motivo_pago": "payment_reason",
            },
        )
        output["document_date"] = _first_available(data, ("tranDate",))
        output["document_origin"] = _first_available(
            data, ("documento_origen", "tranId_origen")
        )
        output["document_origin_date"] = _first_available(
            data, ("fecha_origen", "tranDate_origen")
        )
        output["credit_note"] = _first_available(data, ("tranId_nc",))
        output["credit_note_date"] = _first_available(data, ("tranDate_nc",))
        output["replacement_document"] = _first_available(
            data, ("factura_reemplazo_tranId_nc", "factura_reemplazo_tranId")
        )
        output["replacement_document_date"] = _first_available(
            data, ("fecha_reemplazo_nc",)
        )
        for column in (
            "document_date", "document_origin_date", "credit_note_date",
            "replacement_document_date", "payment_date", "accounting_date",
        ):
            output[column] = _date_text(output[column])
        output["lot"] = _first_available(data, ("lote", "lote_key"))
        output["service"] = _first_available(
            data, ("Servicio", "Servicio_key", "servicio")
        )
        output["payment_document"] = _first_available(
            data, ("Pagado Con:", "ultimo_documento_pago")
        )
        output["payment_type"] = _first_available(
            data, ("Pagado Con (Tipo)", "tipo_pago")
        )
        output["paid_value"] = pd.to_numeric(
            _first_available(data, ("Pagado", "total_pagado_local")),
            errors="coerce",
        )
        has_payment_evidence = (
            output["payment_document"].notna()
            | output["payment_date"].notna()
            | output["accounting_date"].notna()
        )
        output.loc[~has_payment_evidence, "paid_value"] = pd.NA
        output["payment_days"] = pd.to_numeric(
            _first_available(data, ("dias_pago",)), errors="coerce"
        )
        output["payment_factor"] = pd.to_numeric(
            _first_available(data, ("factor",)), errors="coerce"
        )
        output["commission_rate"] = pd.to_numeric(
            _first_available(
                data,
                ("porcentaje_comision_ejecutivo", "tasa_comision_ejecutivo"),
            ),
            errors="coerce",
        )
        output.loc[~has_payment_evidence, ["payment_days", "payment_factor"]] = pd.NA
        output["period"] = _period_text(output["period"])
        return self._attach_review_context(output)

    def floor_results(self, run_code: str) -> pd.DataFrame:
        self._require_run(run_code)
        output = _project(
            self.result.floor_compliance,
            {
                "entityid": "executive",
                "periodo_mes": "period",
                "linea_comision": "commission_line",
                "cumple": "complies",
                "TotalParaPiso": "result_value",
                "meta": "target",
                "piso": "floor",
                "observacion_cumple": "concept",
            },
        )
        output["period"] = _period_text(output["period"])
        return output

    def document_compliance_results(self, run_code: str) -> pd.DataFrame:
        """Expose every issued document used by the floor calculation."""
        self._require_run(run_code)
        data = self.result.sales_floor.copy()
        if data.empty:
            return pd.DataFrame(
                columns=[
                    "document", "document_date", "document_origin",
                    "document_origin_date", "credit_note", "credit_note_date",
                    "replacement_document", "replacement_document_date",
                    "executive", "period",
                    "commission_line", "lot", "service", "document_value",
                    "adds_to_floor", "floor_reason", "request_id",
                    "request_status", "request_final_concept",
                ]
            )

        output = pd.DataFrame(index=data.index)
        output["document"] = _first_available(data, ("tranId",))
        output["document_date"] = _first_available(data, ("tranDate",))
        output["document_origin"] = _first_available(
            data, ("documento_origen", "tranId_origen")
        )
        output["document_origin_date"] = _first_available(
            data, ("fecha_origen", "tranDate_origen")
        )
        output["credit_note"] = _first_available(data, ("tranId_nc",))
        output["credit_note_date"] = _first_available(data, ("tranDate_nc",))
        output["replacement_document"] = _first_available(
            data,
            ("factura_reemplazo_tranId_nc", "factura_reemplazo_tranId"),
        )
        output["replacement_document_date"] = _first_available(
            data, ("fecha_reemplazo_nc",)
        )
        for column in (
            "document_date", "document_origin_date", "credit_note_date",
            "replacement_document_date",
        ):
            output[column] = _date_text(output[column])
        output["executive"] = _first_available(data, ("entityid",))
        output["period"] = _period_text(_coalesce_available(
            data,
            (
                "periodo_mes", "periodo", "periodo_factura",
                "periodo_piso", "period",
            ),
        ))
        output["commission_line"] = _first_available(
            data, ("linea_comision_key", "linea_comision", "clase")
        )
        output["lot"] = _first_available(data, ("lote", "lote_key"))
        output["service"] = _first_available(
            data, ("Servicio", "Servicio_key", "servicio")
        )
        output["document_value"] = pd.to_numeric(
            _first_available(
                data,
                ("valor_moneda_local", "TotalParaPiso_final", "TotalParaPiso"),
            ),
            errors="coerce",
        )
        floor_status = _first_available(data, ("estado_piso",))
        output["adds_to_floor"] = floor_status.astype("string").str.lower().eq("suma")
        output["floor_reason"] = _first_available(
            data, ("observacion_piso", "motivo_piso")
        )
        return self._attach_review_context(output.reset_index(drop=True))

    def review_results(self, run_code: str) -> pd.DataFrame:
        payments = self.commission_results(run_code)
        return payments.loc[
            payments["decision"].astype("string").str.upper().eq("PARA REVISAR")
        ].copy()

    def payment_summary(
        self, run_code: str, discounts: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        payments = self.commission_results(run_code)
        numeric = [
            "commission_executive", "commission_manager", "commission_director"
        ]
        for column in numeric:
            payments[column] = pd.to_numeric(payments[column], errors="coerce").fillna(0)
        summary = (
            payments.groupby(["executive", "period"], as_index=False)
            .agg(
                commission=("commission_executive", "sum"),
                manager_commission=("commission_manager", "sum"),
                director_commission=("commission_director", "sum"),
                documents=("document", "nunique"),
            )
        )
        summary["discounts"] = 0.0
        if discounts is not None and not discounts.empty:
            required = {"executive", "period", "amount"}
            missing = sorted(required.difference(discounts.columns))
            if missing:
                raise ValueError("Descuentos sin columnas: " + ", ".join(missing))
            grouped = (
                discounts.groupby(["executive", "period"], as_index=False)["amount"]
                .sum()
                .rename(columns={"amount": "discounts"})
            )
            summary = summary.drop(columns="discounts").merge(
                grouped, on=["executive", "period"], how="left", validate="1:1"
            )
            summary["discounts"] = summary["discounts"].fillna(0)
        summary["payable"] = summary["commission"] - summary["discounts"]
        return summary
