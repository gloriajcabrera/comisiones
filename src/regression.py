from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_DATASETS = (
    "detalle_reporte",
    "cumpl_reporte",
    "cumplimiento_final",
    "para_pagar_full_reporte",
    "para_pago_full",
    "para_revision_reporte",
    "resumen_pago",
)

CONTROL_COLUMNS = (
    "Valor Comision Ejecutivo",
    "Valor Comision Jefe",
    "Valor Comision Gerente",
    "COMISION",
    "DESCUENTOS",
    "VALOR A PAGAR",
    "Total Para Piso",
    "TotalParaPiso",
)

CASE_COLUMNS = (
    "Factura",
    "Documento Origen",
    "Documento Base Comision",
    "Documento Fuente Pago",
)

PAYMENT_DATE_DOCUMENTS = (
    "PR-9553",
    "SM72586",
    "PR-9551",
    "SM72791",
    "SM73590",
    "SM72209",
    "SM72210",
    "SM65917",
    "SM72582",
)


@dataclass(frozen=True)
class Difference:
    scope: str
    field: str
    expected: Any
    actual: Any


def check_bundle_against_snapshot(
    bundle_dir: Path,
    snapshot_path: Path,
) -> dict[str, Any]:
    """Compare a bundle with an approved snapshot without modifying either."""
    expected = load_snapshot(snapshot_path)
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_cut = expected.get("source_generated_at")
    actual_cut = manifest.get("generated_at")
    if expected_cut != actual_cut:
        return {
            "status": "CUT_MISMATCH",
            "expected_cut": expected_cut,
            "actual_cut": actual_cut,
            "differences": [],
        }

    actual = build_snapshot(bundle_dir, expected.get("documents_requested", []))
    differences = compare_snapshots(expected, actual)
    return {
        "status": "MATCH" if not differences else "DIFFERENCES",
        "expected_cut": expected_cut,
        "actual_cut": actual_cut,
        "differences": [
            {
                "scope": item.scope,
                "field": item.field,
                "expected": item.expected,
                "actual": item.actual,
            }
            for item in differences
        ],
    }


def load_dataset(bundle_dir: Path, name: str) -> pd.DataFrame:
    """Load a published dataset without depending on notebook globals."""
    parquet_path = bundle_dir / f"{name}.parquet"
    csv_path = bundle_dir / f"{name}.csv"
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
    if csv_path.exists():
        return pd.read_csv(csv_path, low_memory=False)
    raise FileNotFoundError(f"Dataset not found: {name}")


def dataset_controls(df: pd.DataFrame) -> dict[str, Any]:
    controls: dict[str, Any] = {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
    }
    for column in CONTROL_COLUMNS:
        if column in df.columns:
            values = pd.to_numeric(df[column], errors="coerce").fillna(0)
            controls[f"sum_{column}"] = round(float(values.sum()), 6)
    return controls


def document_cases(df: pd.DataFrame, documents: list[str]) -> dict[str, Any]:
    missing = [column for column in CASE_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Missing case columns: {missing}")

    normalized = df.loc[:, CASE_COLUMNS].astype("string")
    selected = df.loc[normalized.isin(documents).any(axis=1)].copy()
    if selected.empty:
        return {}

    grouped = selected.groupby("Factura", dropna=False)
    result: dict[str, Any] = {}
    for document, rows in grouped:
        result[str(document)] = {
            "rows": int(len(rows)),
            "origin": _first_text(rows, "Documento Origen"),
            "commission_base": _first_text(rows, "Documento Base Comision"),
            "payment_source": _first_text(rows, "Documento Fuente Pago"),
            "credit_notes": _first_text(rows, "Documentos Anulacion NC"),
            "invoice_value_sum": _numeric_sum(rows, "Valor Factura"),
            "paid_value_max": _numeric_max(rows, "Valor Pagado"),
            "balance_max": _numeric_max(rows, "Saldo Factura"),
            "commission_factor_max": _numeric_max(rows, "Factor Comision Final"),
            "payment_decision": _first_text(rows, "Decision Pago"),
        }
    return result


def build_snapshot(bundle_dir: Path, documents: list[str]) -> dict[str, Any]:
    manifest_path = bundle_dir / "manifest.json"
    generated_at = None
    if manifest_path.exists():
        generated_at = json.loads(manifest_path.read_text(encoding="utf-8")).get(
            "generated_at"
        )

    controls = {
        name: dataset_controls(load_dataset(bundle_dir, name))
        for name in DEFAULT_DATASETS
    }
    payment_report = load_dataset(bundle_dir, "para_pagar_full_reporte")
    return {
        "source_generated_at": generated_at,
        "documents_requested": documents,
        "dataset_controls": controls,
        "document_cases": document_cases(payment_report, documents),
        "payment_date_cases": payment_date_cases(payment_report),
    }


def compare_snapshots(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    absolute_tolerance: float = 0.01,
) -> list[Difference]:
    differences: list[Difference] = []
    _compare_mapping(
        "dataset_controls",
        expected.get("dataset_controls", {}),
        actual.get("dataset_controls", {}),
        differences,
        absolute_tolerance,
    )
    _compare_mapping(
        "document_cases",
        expected.get("document_cases", {}),
        actual.get("document_cases", {}),
        differences,
        absolute_tolerance,
    )
    _compare_mapping(
        "payment_date_cases",
        expected.get("payment_date_cases", {}),
        actual.get("payment_date_cases", {}),
        differences,
        absolute_tolerance,
    )
    return differences


def payment_date_cases(df: pd.DataFrame) -> dict[str, Any]:
    selected = df.loc[df["Factura"].astype("string").isin(PAYMENT_DATE_DOCUMENTS)]
    result: dict[str, Any] = {}
    for document, rows in selected.groupby("Factura", dropna=False):
        result[str(document)] = {
            "real_payment_date": _first_date(rows, "Fecha Real Pago"),
            "accounting_payment_date": _first_date(
                rows, "Fecha Contabilizacion Pago"
            ),
            "first_payment_date": _first_date(rows, "Fecha Primer Pago"),
            "full_payment_date": _first_date(rows, "Fecha Pago Total"),
            "payment_date_origin": _first_text(rows, "Origen Fecha Pago"),
        }
    return result


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _compare_mapping(
    scope: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    differences: list[Difference],
    tolerance: float,
) -> None:
    for key in sorted(set(expected) | set(actual)):
        child_scope = f"{scope}.{key}"
        if key not in expected:
            differences.append(Difference(child_scope, "presence", None, "present"))
            continue
        if key not in actual:
            differences.append(Difference(child_scope, "presence", "present", None))
            continue
        expected_value = expected[key]
        actual_value = actual[key]
        if isinstance(expected_value, dict) and isinstance(actual_value, dict):
            _compare_mapping(
                child_scope,
                expected_value,
                actual_value,
                differences,
                tolerance,
            )
        elif not _values_equal(expected_value, actual_value, tolerance):
            differences.append(
                Difference(child_scope, "value", expected_value, actual_value)
            )


def _values_equal(expected: Any, actual: Any, tolerance: float) -> bool:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if math.isnan(expected) and math.isnan(actual):
            return True
        return math.isclose(expected, actual, rel_tol=0, abs_tol=tolerance)
    return expected == actual


def _first_text(df: pd.DataFrame, column: str) -> str | None:
    if column not in df.columns:
        return None
    values = df[column].dropna().astype("string")
    return None if values.empty else str(values.iloc[0])


def _numeric_sum(df: pd.DataFrame, column: str) -> float | None:
    if column not in df.columns:
        return None
    values = pd.to_numeric(df[column], errors="coerce")
    return None if values.notna().sum() == 0 else round(float(values.sum()), 6)


def _numeric_max(df: pd.DataFrame, column: str) -> float | None:
    if column not in df.columns:
        return None
    values = pd.to_numeric(df[column], errors="coerce")
    return None if values.notna().sum() == 0 else round(float(values.max()), 6)


def _first_date(df: pd.DataFrame, column: str) -> str | None:
    if column not in df.columns:
        return None
    values = pd.to_datetime(df[column], errors="coerce").dropna()
    return None if values.empty else values.iloc[0].date().isoformat()
