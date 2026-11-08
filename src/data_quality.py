"""Validaciones comunes de calidad para datos sincronizados de NetSuite."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .netsuite_provider import NetSuiteDataset
from .normalization import annotate_commission_eligibility


SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "ADVERTENCIA"

STABLE_KEY_FIELDS = {
    "invoices": ("id", "line_id"),
    "costs": ("id", "line_id"),
    "proformas": ("id", "line_id"),
    "credit_notes": ("id", "line_id"),
    "proforma_cancellations": ("id", "line_id"),
    "invoice_payments": ("factura_id", "pago_id", "tipo_pago"),
    "proforma_payments": (
        "proforma_id", "deposito_id", "pago_id", "tipo_vinculo"
    ),
}

PAYMENT_LINE_IDENTITY_FIELDS = {
    "invoice_payments": ("previous_line", "next_line"),
    "proforma_payments": ("previous_line", "next_line"),
}

DOCUMENT_DATASETS = {
    "invoices", "proformas", "credit_notes", "proforma_cancellations"
}
COMMERCIAL_DOCUMENT_DATASETS = {"invoices", "proformas"}
NUMERIC_FIELDS = {
    "Precio", "quantity", "Valor", "valor_total_documento",
    "valor_moneda_local", "valor_moneda_transaccion", "foreignamountunpaid",
    "valor_pago", "valor_pagado_proforma",
}
HISTORY_START_DATE = pd.Timestamp("2023-01-01")


def filter_netsuite_history(dataset: NetSuiteDataset) -> NetSuiteDataset:
    """Keep the approved operational universe without deleting persisted history."""
    date_columns = {
        "invoices": "tranDate",
        "costs": "tranDate",
        "proformas": "tranDate",
        "credit_notes": "tranDate",
        "proforma_cancellations": "tranDate",
        "invoice_payments": "fecha_factura",
        "proforma_payments": "fecha_proforma",
    }
    frames = {}
    for name, date_column in date_columns.items():
        frame = getattr(dataset, name)
        if frame.empty or date_column not in frame.columns:
            frames[name] = frame.copy()
            continue
        dates = pd.to_datetime(frame[date_column], errors="coerce")
        frames[name] = frame.loc[dates.ge(HISTORY_START_DATE)].reset_index(drop=True)
    return NetSuiteDataset(**frames)


@dataclass(frozen=True)
class DataQualityIssue:
    dataset: str
    code: str
    severity: str
    message: str
    affected_rows: int


@dataclass(frozen=True)
class DataQualityReport:
    issues: tuple[DataQualityIssue, ...]

    @property
    def errors(self) -> tuple[DataQualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == SEVERITY_ERROR)

    @property
    def warnings(self) -> tuple[DataQualityIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity == SEVERITY_WARNING
        )

    def require_valid(self) -> None:
        if self.errors:
            raise ValueError(self.summary(self.errors))

    def summary(self, issues: tuple[DataQualityIssue, ...] | None = None) -> str:
        selected = self.issues if issues is None else issues
        return "; ".join(
            f"{issue.dataset}/{issue.code}: {issue.message} "
            f"({issue.affected_rows} filas)"
            for issue in selected
        )


def validate_netsuite_dataset(dataset: NetSuiteDataset) -> DataQualityReport:
    issues: list[DataQualityIssue] = []
    for dataset_name in NetSuiteDataset.__dataclass_fields__:
        frame = getattr(dataset, dataset_name)
        if frame.empty:
            continue
        key_fields = STABLE_KEY_FIELDS[dataset_name]
        missing = [field for field in key_fields if field not in frame.columns]
        if missing:
            issues.append(DataQualityIssue(
                dataset_name,
                "MISSING_IDENTITY_COLUMNS",
                SEVERITY_ERROR,
                "Faltan columnas de identidad estable: " + ", ".join(missing),
                len(frame),
            ))
            continue

        null_identity = pd.Series(False, index=frame.index)
        for field in key_fields:
            values = frame[field]
            null_identity |= values.isna() | values.astype("string").str.strip().eq("")
        if null_identity.any():
            issues.append(DataQualityIssue(
                dataset_name,
                "NULL_IDENTITY",
                SEVERITY_ERROR,
                "La identidad estable contiene valores nulos o vacios",
                int(null_identity.sum()),
            ))

        line_fields = PAYMENT_LINE_IDENTITY_FIELDS.get(dataset_name, ())
        duplicate_fields = (
            (*key_fields, *line_fields)
            if set(line_fields).issubset(frame.columns)
            else key_fields
        )
        duplicated = frame.duplicated(list(duplicate_fields), keep=False)
        if duplicated.any():
            issues.append(DataQualityIssue(
                dataset_name,
                "DUPLICATE_IDENTITY",
                SEVERITY_WARNING,
                "La identidad estable esta repetida",
                int(duplicated.sum()),
            ))

        if dataset_name in DOCUMENT_DATASETS and "tranId" in frame.columns:
            document_number = frame["tranId"]
            missing_number = (
                document_number.isna()
                | document_number.astype("string").str.strip().eq("")
            )
            if missing_number.any():
                issues.append(DataQualityIssue(
                    dataset_name,
                    "NULL_DOCUMENT_NUMBER",
                    SEVERITY_WARNING,
                    "El numero visible del documento es nulo o vacio",
                    int(missing_number.sum()),
                ))

        if dataset_name in COMMERCIAL_DOCUMENT_DATASETS:
            business_frame = annotate_commission_eligibility(
                frame,
                "factura" if dataset_name == "invoices" else "proforma",
            )
            business_frame = business_frame.loc[
                business_frame["elegible_comision"].fillna(False)
            ]
            _append_blank_business_field_issue(
                issues,
                dataset_name,
                business_frame,
                ("entityid", "ejecutivo"),
                "MISSING_EXECUTIVE",
                "El documento comercial no tiene ejecutivo asociado",
            )
            _append_blank_business_field_issue(
                issues,
                dataset_name,
                business_frame,
                ("custitem_sm_lineacomision", "linea_comision"),
                "MISSING_COMMISSION_LINE",
                "El documento comercial no tiene linea de comision",
            )

        for column in NUMERIC_FIELDS.intersection(frame.columns):
            source = frame[column]
            present = source.notna() & source.astype("string").str.strip().ne("")
            invalid = present & pd.to_numeric(source, errors="coerce").isna()
            if invalid.any():
                issues.append(DataQualityIssue(
                    dataset_name,
                    "INVALID_NUMERIC_VALUE",
                    SEVERITY_WARNING,
                    f"La columna {column} contiene valores no numericos",
                    int(invalid.sum()),
                ))

        if dataset_name in DOCUMENT_DATASETS and "id" in frame.columns:
            for column in ("tranId", "valor_total_documento", "dueDate"):
                if column not in frame.columns:
                    continue
                inconsistent = frame.groupby("id", dropna=False)[column].transform(
                    lambda values: values.dropna().astype("string").nunique() > 1
                )
                if inconsistent.any():
                    issues.append(DataQualityIssue(
                        dataset_name,
                        "INCONSISTENT_DOCUMENT_VALUE",
                        SEVERITY_WARNING,
                        f"La columna {column} cambia entre lineas del documento",
                        int(inconsistent.sum()),
                    ))

        for column in frame.columns:
            lowered = str(column).lower()
            if not (
                "fecha" in lowered
                or lowered.endswith("date")
                or lowered in {"trandate", "duedate"}
            ):
                continue
            source = frame[column]
            present = source.notna() & source.astype("string").str.strip().ne("")
            invalid = present & pd.to_datetime(source, errors="coerce").isna()
            if invalid.any():
                issues.append(DataQualityIssue(
                    dataset_name,
                    "INVALID_DATE",
                    SEVERITY_WARNING,
                    f"La columna {column} contiene fechas invalidas",
                    int(invalid.sum()),
                ))
    parent_rules = (
        ("invoice_payments", "factura_id", dataset.invoices, "id"),
        ("proforma_payments", "proforma_id", dataset.proformas, "id"),
    )
    for dataset_name, foreign_key, parents, parent_key in parent_rules:
        payments = getattr(dataset, dataset_name)
        if (
            payments.empty or parents.empty
            or foreign_key not in payments.columns or parent_key not in parents.columns
        ):
            continue
        known = set(parents[parent_key].dropna().astype("string").str.strip())
        references = payments[foreign_key].astype("string").str.strip()
        orphan = payments[foreign_key].notna() & ~references.isin(known)
        if orphan.any():
            issues.append(DataQualityIssue(
                dataset_name,
                "ORPHAN_DOCUMENT_REFERENCE",
                SEVERITY_WARNING,
                "El pago referencia un documento ausente del conjunto sincronizado",
                int(orphan.sum()),
            ))
    return DataQualityReport(tuple(issues))


def _append_blank_business_field_issue(
    issues: list[DataQualityIssue],
    dataset_name: str,
    frame: pd.DataFrame,
    aliases: tuple[str, ...],
    code: str,
    message: str,
) -> None:
    column = next((name for name in aliases if name in frame.columns), None)
    if column is None:
        return
    values = frame[column]
    blank = values.isna() | values.astype("string").str.strip().eq("")
    if blank.any():
        issues.append(DataQualityIssue(
            dataset_name,
            code,
            SEVERITY_WARNING,
            message,
            int(blank.sum()),
        ))
