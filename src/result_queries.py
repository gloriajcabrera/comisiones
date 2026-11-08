"""Consultas autorizadas sobre resultados suministrados sin duplicarlos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Protocol

import pandas as pd

from .access_control import (
    AccessDeniedError,
    AuthorizationService,
    ROLE_ADMIN,
    ROLE_DIRECTOR,
    ROLE_PARAMETERIZER,
    ROLE_REVIEWER,
)


class CalculationResultProvider(Protocol):
    def commission_results(self, run_code: str) -> pd.DataFrame: ...

    def floor_results(self, run_code: str) -> pd.DataFrame: ...

    def document_compliance_results(self, run_code: str) -> pd.DataFrame: ...

    def payment_summary(
        self, run_code: str, discounts: pd.DataFrame | None = None
    ) -> pd.DataFrame: ...


class DiscountResultProvider(Protocol):
    def list_discounts(self) -> pd.DataFrame: ...

    def list_applications(self, payment_period: str | None = None) -> pd.DataFrame: ...


@dataclass(frozen=True)
class PagedResult:
    items: list[dict[str, object]]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size


class ResultQueryService:
    _SORT_COLUMNS = {
        "commissions": {
            "document": "document",
            "executive": "executive",
            "period": "period",
            "commission_line": "commission_line",
            "decision": "decision",
            "commission_executive": "commission_executive",
        },
        "floor": {
            "executive": "executive",
            "period": "period",
            "commission_line": "commission_line",
            "complies": "complies",
            "result_value": "result_value",
        },
        "floor_documents": {
            "document": "document",
            "document_date": "document_date",
            "document_origin": "document_origin",
            "credit_note": "credit_note",
            "replacement_document": "replacement_document",
            "executive": "executive",
            "period": "period",
            "commission_line": "commission_line",
            "document_value": "document_value",
            "adds_to_floor": "adds_to_floor",
        },
        "discounts": {
            "discount_id": "discount_id",
            "executive": "executive",
            "period": "period",
            "detection_period": "detection_period",
            "total_debt": "total_debt",
            "applied_amount": "applied_amount",
            "balance": "balance",
            "status": "status",
        },
        "payment_summary": {
            "executive": "executive",
            "period": "period",
            "commission": "commission",
            "discounts": "discounts",
            "payable": "payable",
            "documents": "documents",
        },
    }

    def __init__(
        self,
        provider: CalculationResultProvider,
        authorization: AuthorizationService,
        discount_provider: DiscountResultProvider | None = None,
    ) -> None:
        self.provider = provider
        self.authorization = authorization
        self.discount_provider = discount_provider

    def commissions(
        self,
        actor_user_id: int,
        run_code: str,
        effective_on: date | str,
        *,
        executives: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        documents: Iterable[str] | None = None,
        commission_lines: Iterable[str] | None = None,
        decisions: Iterable[str] | None = None,
        sort_by: str = "document",
        descending: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> PagedResult:
        filters = {
            "executive": self._authorized_executives(
                actor_user_id, effective_on, executives
            ),
            "period": _clean(periods),
            "document": _clean(documents, upper=True),
            "commission_line": _clean(commission_lines, upper=True),
            "decision": _clean(decisions, upper=True),
        }
        return self._query(
            self._restrict_commission_visibility(
                actor_user_id, self.provider.commission_results(run_code)
            ),
            "commissions",
            filters,
            sort_by,
            descending,
            page,
            page_size,
        )

    def floor_results(
        self,
        actor_user_id: int,
        run_code: str,
        effective_on: date | str,
        *,
        executives: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        commission_lines: Iterable[str] | None = None,
        complies: bool | None = None,
        sort_by: str = "executive",
        descending: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> PagedResult:
        filters: dict[str, set[str] | bool | None] = {
            "executive": self._authorized_executives(
                actor_user_id, effective_on, executives
            ),
            "period": _clean(periods),
            "commission_line": _clean(commission_lines, upper=True),
            "complies": complies,
        }
        return self._query(
            self.provider.floor_results(run_code),
            "floor",
            filters,
            sort_by,
            descending,
            page,
            page_size,
        )

    def document_compliance(
        self,
        actor_user_id: int,
        run_code: str,
        effective_on: date | str,
        *,
        executives: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        documents: Iterable[str] | None = None,
        document_origins: Iterable[str] | None = None,
        credit_notes: Iterable[str] | None = None,
        replacement_documents: Iterable[str] | None = None,
        commission_lines: Iterable[str] | None = None,
        adds_to_floor: bool | None = None,
        sort_by: str = "document",
        descending: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> PagedResult:
        filters: dict[str, set[str] | bool | None] = {
            "executive": self._authorized_executives(
                actor_user_id, effective_on, executives
            ),
            "period": _clean(periods),
            "document": _clean(documents, upper=True),
            "document_origin": _clean(document_origins, upper=True),
            "credit_note": _clean(credit_notes, upper=True),
            "replacement_document": _clean(replacement_documents, upper=True),
            "commission_line": _clean(commission_lines, upper=True),
            "adds_to_floor": adds_to_floor,
        }
        return self._query(
            self.provider.document_compliance_results(run_code),
            "floor_documents",
            filters,
            sort_by,
            descending,
            page,
            page_size,
        )

    def discounts(
        self,
        actor_user_id: int,
        effective_on: date | str,
        *,
        executives: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        detection_periods: Iterable[str] | None = None,
        statuses: Iterable[str] | None = None,
        sort_by: str = "period",
        descending: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> PagedResult:
        if self.discount_provider is None:
            raise RuntimeError("No hay repositorio de descuentos configurado")
        filters = {
            "executive": self._authorized_executives(
                actor_user_id, effective_on, executives
            ),
            "period": _clean(periods),
            "detection_period": _clean(detection_periods),
            "status": _clean(statuses, upper=True),
        }
        return self._query(
            self._restrict_commission_visibility(
                actor_user_id, self.discount_provider.list_discounts()
            ),
            "discounts",
            filters,
            sort_by,
            descending,
            page,
            page_size,
        )

    def payment_summary(
        self,
        actor_user_id: int,
        run_code: str,
        effective_on: date | str,
        *,
        executives: Iterable[str] | None = None,
        periods: Iterable[str] | None = None,
        sort_by: str = "executive",
        descending: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> PagedResult:
        applications = None
        if self.discount_provider is not None:
            applications = self.discount_provider.list_applications()
        filters = {
            "executive": self._authorized_executives(
                actor_user_id, effective_on, executives
            ),
            "period": _clean(periods),
        }
        return self._query(
            self._restrict_commission_visibility(
                actor_user_id,
                self.provider.payment_summary(run_code, applications),
            ),
            "payment_summary",
            filters,
            sort_by,
            descending,
            page,
            page_size,
        )

    def _restrict_commission_visibility(
        self, actor_user_id: int, data: pd.DataFrame
    ) -> pd.DataFrame:
        roles = self.authorization.effective_roles(actor_user_id)
        unrestricted = {
            ROLE_REVIEWER, ROLE_PARAMETERIZER, ROLE_ADMIN, ROLE_DIRECTOR
        }
        if roles & unrestricted:
            return data.copy()
        result = data.copy()
        director_columns = {
            "commission_director", "director_commission"
        }
        result = result.drop(
            columns=[column for column in director_columns if column in result.columns]
        )
        if "JEFE" not in roles:
            manager_columns = {
                "commission_manager", "manager_commission"
            }
            result = result.drop(
                columns=[column for column in manager_columns if column in result.columns]
            )
        return result

    def _authorized_executives(
        self,
        actor_user_id: int,
        effective_on: date | str,
        requested: Iterable[str] | None,
    ) -> set[str] | None:
        scope = self.authorization.executive_scope(actor_user_id, effective_on)
        requested_set = _clean(requested, upper=True)
        if requested_set is None:
            return scope
        if scope is not None and not requested_set.issubset(scope):
            raise AccessDeniedError("Uno o mas ejecutivos solicitados estan fuera del alcance")
        return requested_set

    def _query(
        self,
        data: pd.DataFrame,
        view: str,
        filters: dict[str, set[str] | bool | None],
        sort_by: str,
        descending: bool,
        page: int,
        page_size: int,
    ) -> PagedResult:
        if page < 1:
            raise ValueError("page debe ser mayor o igual a 1")
        if not 1 <= page_size <= 500:
            raise ValueError("page_size debe estar entre 1 y 500")
        sort_column = self._SORT_COLUMNS[view].get(sort_by)
        if sort_column is None:
            raise ValueError(f"Ordenamiento no permitido: {sort_by}")
        required = set(self._SORT_COLUMNS[view].values()) | set(filters)
        missing = sorted(required.difference(data.columns))
        if missing:
            raise ValueError(f"Resultado {view} sin columnas: {', '.join(missing)}")

        result = data.copy()
        for column, selected in filters.items():
            if selected is None:
                continue
            if isinstance(selected, set):
                normalized = result[column].astype("string").str.strip().str.upper()
                result = result.loc[normalized.isin(selected)]
            else:
                result = result.loc[result[column].astype("boolean").eq(selected)]
        result = result.sort_values(
            sort_column, ascending=not descending, kind="stable", na_position="last"
        )
        total = len(result)
        start = (page - 1) * page_size
        items = result.iloc[start:start + page_size].to_dict(orient="records")
        return PagedResult(items=items, total=total, page=page, page_size=page_size)


def _clean(values: Iterable[str] | None, upper: bool = False) -> set[str] | None:
    if values is None:
        return None
    cleaned = {str(value).strip() for value in values if str(value).strip()}
    return {value.upper() for value in cleaned} if upper else cleaned
