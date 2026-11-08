"""Comandos operativos para sincronizar y calcular sin Jupyter."""

from __future__ import annotations

import argparse
from datetime import date
import json
import sys
from pathlib import Path
from typing import Sequence

import pandas as pd

from .input_store import load_dataframe_from_path
from .observability import configure_logging, safe_error_message
from .netsuite_provider import (
    NetSuiteApiHttpError,
    NetSuiteApiProvider,
    NetSuiteCatalogCheckError,
)
from .production_check import validate_production_environment
from .regression import check_bundle_against_snapshot
from .runtime import ApplicationRuntime, build_runtime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comisiones")
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", help="Actualizar datos desde NetSuite")
    sync.add_argument("--actor-id", type=int, required=True)
    sync.add_argument("--type", choices=("FULL", "INCREMENTAL"), default="FULL")
    sync.add_argument("--run-code")
    sync.add_argument("--since", help="Inicio incluido YYYY-MM-DD")
    sync.add_argument("--until", help="Fin excluido YYYY-MM-DD")

    backfill = commands.add_parser(
        "sync-backfill",
        help="Cargar historia de NetSuite en ventanas mensuales controladas",
    )
    backfill.add_argument("--actor-id", type=int, required=True)
    backfill.add_argument("--since", required=True, help="Inicio YYYY-MM-01")
    backfill.add_argument("--until", required=True, help="Fin excluido YYYY-MM-01")
    backfill.add_argument("--chunk-months", type=int, default=3)
    backfill.add_argument("--run-prefix", required=True)

    calculate = commands.add_parser(
        "calculate", help="Calcular usando snapshot e insumos aprobados"
    )
    calculate.add_argument("--actor-id", type=int, required=True)
    calculate.add_argument("--period", required=True)
    calculate.add_argument("--run-code")

    regression = commands.add_parser(
        "regression-check",
        help="Comparar un bundle con una referencia aprobada sin modificarla",
    )
    regression.add_argument("--bundle", type=Path, required=True)
    regression.add_argument("--baseline", type=Path, required=True)
    commands.add_parser(
        "production-check",
        help="Validar configuracion productiva sin abrir conexiones",
    )
    netsuite_check = commands.add_parser(
        "netsuite-check",
        help="Probar acceso REST SuiteQL de solo lectura sin sincronizar",
    )
    netsuite_check.add_argument(
        "--catalog",
        action="store_true",
        help="Validar una fila de cada consulta del catalogo",
    )
    bootstrap = commands.add_parser(
        "bootstrap-admin",
        help="Crear el primer administrador solo cuando la base no tiene usuarios",
    )
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--first-names", required=True)
    bootstrap.add_argument("--last-names", required=True)
    bootstrap.add_argument("--reason", required=True)
    migration = commands.add_parser(
        "migrate-legacy-inputs",
        help="Registrar pisos, metas y cortes historicos en los flujos auditables",
    )
    migration.add_argument("--actor-id", type=int, required=True)
    migration.add_argument("--targets-2025", type=Path, required=True)
    migration.add_argument("--targets-2026", type=Path, required=True)
    migration.add_argument("--cuts", type=Path, required=True)
    migration.add_argument("--reason", required=True)
    return parser


def execute_command(
    arguments: argparse.Namespace,
    runtime: ApplicationRuntime | None,
) -> dict[str, object]:
    if arguments.command == "regression-check":
        result = check_bundle_against_snapshot(arguments.bundle, arguments.baseline)
        return {"operation": "regression-check", **result}
    if arguments.command == "production-check":
        return validate_production_environment()
    if arguments.command == "netsuite-check":
        try:
            return NetSuiteApiProvider().check_access(
                validate_catalog=arguments.catalog,
            )
        except NetSuiteCatalogCheckError as exc:
            return _netsuite_error_payload(exc.cause, dataset=exc.dataset)
        except Exception as exc:
            return _netsuite_error_payload(exc)

    if runtime is None:
        raise RuntimeError("Application runtime is required for this command")

    if arguments.command == "bootstrap-admin":
        user_id = runtime.access_repository.bootstrap_initial_administrator(
            arguments.username,
            arguments.first_names,
            arguments.last_names,
            arguments.reason,
        )
        return {
            "operation": "bootstrap-admin",
            "status": "COMPLETADO",
            "user_id": user_id,
            "domain_username": arguments.username.strip().lower(),
        }

    if arguments.command == "migrate-legacy-inputs":
        return _migrate_legacy_inputs(arguments, runtime)

    if arguments.command == "sync-backfill":
        executions = []
        for window_start, window_end in _month_windows(
            arguments.since, arguments.until, arguments.chunk_months
        ):
            result = runtime.sync_service.synchronize(
                arguments.actor_id,
                sync_type="INCREMENTAL",
                run_code=(
                    f"{arguments.run_prefix}-{window_start.replace('-', '')}"
                    f"-{window_end.replace('-', '')}"
                ),
                window_start=window_start,
                window_end=window_end,
            )
            executions.append({
                "run_code": result.run_code,
                "status": result.status,
                "window_start": window_start,
                "window_end": window_end,
                "rows": sum(result.rows_by_dataset.values()),
                "created": result.metrics.created,
                "updated": result.metrics.updated,
                "unchanged": result.metrics.unchanged,
                "deactivated": result.metrics.deactivated,
            })
        return {
            "operation": "sync-backfill",
            "status": "COMPLETADO",
            "executions": executions,
        }

    if arguments.command == "sync":
        result = runtime.sync_service.synchronize(
            arguments.actor_id,
            sync_type=arguments.type,
            run_code=arguments.run_code,
            window_start=arguments.since,
            window_end=arguments.until,
        )
        return {
            "operation": "sync",
            "run_code": result.run_code,
            "status": result.status,
            "rows_by_dataset": result.rows_by_dataset,
            "metrics": {
                "created": result.metrics.created,
                "updated": result.metrics.updated,
                "unchanged": result.metrics.unchanged,
                "deactivated": result.metrics.deactivated,
            },
        }

    result = runtime.calculation_service.execute_approved_inputs(
        str(arguments.actor_id),
        period=arguments.period,
        run_code=arguments.run_code,
    )
    return {
        "operation": "calculate",
        "run_code": result.run_code,
        "status": result.status,
        "reused": result.reused,
        "commission_rows": result.commission_rows,
        "floor_rows": result.floor_rows,
        "payment_rows": result.payment_rows,
        "warning_count": result.warning_count,
        "warning_message": result.warning_message,
        "discount_rows": result.discount_rows,
    }


def main(
    argv: Sequence[str] | None = None,
    runtime: ApplicationRuntime | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    standalone = {"regression-check", "production-check", "netsuite-check"}
    if runtime is None and arguments.command not in standalone:
        configure_logging(stream=sys.stderr)
        active_runtime = build_runtime()
    else:
        active_runtime = runtime
    payload = execute_command(arguments, active_runtime)
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    if arguments.command == "regression-check" and payload["status"] != "MATCH":
        return 2
    if arguments.command == "netsuite-check" and payload["status"] != "READY":
        return 2
    return 0


def _netsuite_error_payload(
    error: BaseException,
    *,
    dataset: str | None = None,
) -> dict[str, object]:
    error_code = "CONFIGURATION_OR_CONNECTION_ERROR"
    http_status: int | None = None
    if isinstance(error, NetSuiteApiHttpError):
        http_status = error.status_code
        error_code = {
            400: "QUERY_REJECTED",
            401: "AUTHENTICATION_FAILED",
            403: "PERMISSION_DENIED",
            429: "RATE_LIMITED",
        }.get(http_status, "SERVICE_UNAVAILABLE" if http_status >= 500 else "HTTP_ERROR")
    payload: dict[str, object] = {
        "operation": "netsuite-check",
        "status": "ERROR",
        "error_code": error_code,
        "message": safe_error_message(error),
    }
    if dataset is not None:
        payload["dataset"] = dataset
    if http_status is not None:
        payload["http_status"] = http_status
    return payload


def _month_windows(
    start: str, end: str, chunk_months: int
) -> list[tuple[str, str]]:
    if not 1 <= chunk_months <= 12:
        raise ValueError("chunk_months debe estar entre 1 y 12")
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if start_date.day != 1 or end_date.day != 1:
        raise ValueError("Las fechas del backfill deben ser el primer dia del mes")
    if end_date <= start_date:
        raise ValueError("La fecha final debe ser posterior a la inicial")
    windows = []
    current = start_date
    while current < end_date:
        month_index = current.year * 12 + current.month - 1 + chunk_months
        candidate = date(month_index // 12, month_index % 12 + 1, 1)
        following = min(candidate, end_date)
        windows.append((current.isoformat(), following.isoformat()))
        current = following
    return windows


def _prepare_legacy_targets(data: pd.DataFrame, year: int) -> pd.DataFrame:
    result = data.copy()
    if "periodo" not in result.columns and "perido" in result.columns:
        result = result.rename(columns={"perido": "periodo"})
    if "periodo" not in result.columns:
        raise ValueError("El archivo de pisos y metas no contiene la columna periodo")
    periods = pd.to_datetime(result["periodo"], errors="coerce")
    return result.loc[periods.dt.year.eq(year)].reset_index(drop=True)


def _migrate_legacy_inputs(
    arguments: argparse.Namespace,
    runtime: ApplicationRuntime,
) -> dict[str, object]:
    reason = arguments.reason.strip()
    if not reason:
        raise ValueError("El motivo de la migracion es obligatorio")

    target_sources = {
        2025: (arguments.targets_2025, "MIGRACION_INICIAL_2025"),
        2026: (arguments.targets_2026, "MIGRACION_INICIAL_2026"),
    }
    existing_targets = {
        str(batch.get("source_name")): batch
        for batch in runtime.target_workflows.list_batches()
    }
    target_results = []
    for year, (path, source_name) in target_sources.items():
        existing = existing_targets.get(source_name)
        if existing is not None:
            target_results.append({"year": year, "reused": True, **existing})
            continue
        data = _prepare_legacy_targets(load_dataframe_from_path(path), year)
        batch = runtime.target_workflows.create_batch(
            data,
            arguments.actor_id,
            source_name=source_name,
            observation=f"{reason}. Origen: {path.name}",
        )
        if batch.error_count == 0:
            batch = runtime.target_workflows.submit(
                batch.batch_id, arguments.actor_id
            )
        target_results.append({
            "year": year,
            "reused": False,
            "batch_id": batch.batch_id,
            "version": batch.version,
            "status": batch.status,
            "rows_count": batch.rows_count,
            "valid_count": batch.valid_count,
            "error_count": batch.error_count,
        })

    cut_source = "MIGRACION_INICIAL_CORTES_2025_2026"
    existing_cuts = {
        str(batch.get("source_name")): batch
        for batch in runtime.cut_period_workflows.list_batches()
    }
    existing_cut = existing_cuts.get(cut_source)
    if existing_cut is not None:
        cut_result = {"reused": True, **existing_cut}
    else:
        cut_batch = runtime.cut_period_workflows.create_batch(
            load_dataframe_from_path(arguments.cuts),
            arguments.actor_id,
            source_name=cut_source,
            observation=f"{reason}. Origen: {arguments.cuts.name}",
        )
        cut_batch = runtime.cut_period_workflows.submit(
            cut_batch.batch_id, arguments.actor_id
        )
        cut_result = {
            "reused": False,
            "batch_id": cut_batch.batch_id,
            "version": cut_batch.version,
            "status": cut_batch.status,
            "rows_count": cut_batch.rows_count,
        }

    return {
        "operation": "migrate-legacy-inputs",
        "status": "COMPLETADO_CON_ADVERTENCIAS"
        if any(item.get("error_count", 0) for item in target_results)
        else "COMPLETADO",
        "targets": target_results,
        "cuts": cut_result,
    }


if __name__ == "__main__":
    raise SystemExit(main())
