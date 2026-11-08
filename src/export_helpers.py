from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.api.types import (
    is_categorical_dtype,
    is_datetime64_any_dtype,
    is_interval_dtype,
    is_object_dtype,
    is_period_dtype,
)


def stringify_period_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    for col in out.columns:
        series = out[col]
        dtype = series.dtype
        col_name = str(col).strip().lower()

        if is_period_dtype(dtype) or is_interval_dtype(dtype):
            out[col] = series.astype("string")
            continue

        if "periodo" in col_name:
            sample = series.dropna().head(100)
            if is_datetime64_any_dtype(dtype):
                out[col] = series.dt.strftime("%Y-%m")
            elif sample.empty:
                out[col] = series.astype("string")
            else:
                out[col] = series.astype("string")
            continue

        if is_object_dtype(dtype):
            sample = series.dropna().head(100)
            if not sample.empty and sample.map(lambda x: isinstance(x, (pd.Period, pd.Interval))).any():
                out[col] = series.astype("string")

    return out


def sort_cols(df: pd.DataFrame, cols_first: list[str]) -> pd.DataFrame:
    out = df.copy()
    out = out.loc[:, ~out.columns.duplicated()].copy()
    cols_existing = [c for c in cols_first if c in out.columns]
    cols_rest = [c for c in out.columns if c not in cols_existing]
    return out[cols_existing + cols_rest].copy()


def limpiar_para_excel(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.replace([np.inf, -np.inf], pd.NA)

    for col in out.columns:
        series = out[col]
        dtype = series.dtype

        if is_period_dtype(dtype) or is_interval_dtype(dtype) or is_categorical_dtype(dtype):
            out[col] = series.astype("string")
            continue

        if is_datetime64_any_dtype(dtype):
            continue

        if is_object_dtype(dtype):
            sample = series.dropna().head(100)
            if sample.empty:
                continue

            needs_string = sample.map(
                lambda value: isinstance(
                    value,
                    (pd.Period, pd.Interval, list, tuple, set, dict, bytes, Path),
                )
            ).any()

            if needs_string:
                out[col] = series.astype("string")

    return out


def safe_name(value: object, default: str = "sin_nombre") -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return default

    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(". ")
    return text or default
