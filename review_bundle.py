from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from pandas.api.types import (
    is_categorical_dtype,
    is_interval_dtype,
    is_object_dtype,
    is_period_dtype,
)

def _prepare_frame_for_storage(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(col) for col in out.columns]

    for col in out.columns:
        series = out[col]
        dtype = series.dtype

        # Normaliza dtypes que suelen romper compatibilidad entre entornos.
        if is_period_dtype(dtype) or is_interval_dtype(dtype) or is_categorical_dtype(dtype):
            out[col] = series.astype("string")
            continue

        if str(dtype).startswith("string"):
            out[col] = series.astype("string")
            continue

        if is_object_dtype(dtype):
            sample = series.dropna().head(200)
            if sample.empty:
                continue

            unsupported = sample.map(
                lambda value: isinstance(
                    value,
                    (pd.Period, pd.Interval, list, tuple, set, dict, bytes),
                )
            ).any()
            if unsupported:
                out[col] = series.astype("string")

    return out


def _write_dataset(df: pd.DataFrame, dataset_base: Path) -> tuple[Path, str]:
    prepared = _prepare_frame_for_storage(df)
    errors: list[str] = []

    parquet_path = dataset_base.with_suffix(".parquet")
    try:
        prepared.to_parquet(parquet_path, index=False)
        return parquet_path, "parquet"
    except Exception as exc:
        errors.append(f"parquet: {type(exc).__name__}: {exc}")
        if parquet_path.exists():
            parquet_path.unlink()

    csv_path = dataset_base.with_suffix(".csv")
    try:
        prepared.to_csv(csv_path, index=False, encoding="utf-8-sig")
        return csv_path, "csv"
    except Exception as exc:
        errors.append(f"csv: {type(exc).__name__}: {exc}")
        if csv_path.exists():
            csv_path.unlink()

    raise RuntimeError(
        f"No fue posible exportar `{dataset_base.name}`. Errores: {' | '.join(errors)}"
    )


def save_dataframe_bundle(dataframes: dict[str, pd.DataFrame], out_dir: str | Path = "app_data") -> Path:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "datasets": [],
    }

    for name, df in dataframes.items():
        if not isinstance(df, pd.DataFrame):
            raise TypeError(f"{name} no es un DataFrame de pandas.")

        dataset_path, storage_format = _write_dataset(df, out_path / name)

        manifest["datasets"].append(
            {
                "name": name,
                "path": dataset_path.name,
                "format": storage_format,
                "rows": int(df.shape[0]),
                "cols": int(df.shape[1]),
                "columns": [str(c) for c in df.columns],
            }
        )

    manifest_path = out_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return manifest_path
