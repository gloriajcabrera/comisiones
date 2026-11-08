from __future__ import annotations

import pandas as pd

from src.normalization import (
    normalize_boolean,
    normalize_date,
    normalize_id,
    normalize_number,
    normalize_text,
    normalize_upper,
)


COMMISSION_LINE_MAP = {
    "14": "ADMON",
    "13": "CALIBRACION",
    "7": "CAPACITACION",
    "8": "CERTIFICACION",
    "18": "CSG",
    "9": "ESCAS",
    "17": "FRONTERAS",
    "16": "INTER",
    "2": "LMA",
    "1": "LMAM",
    "3": "LME",
    "6": "LVM",
    "5": "LVV",
    "19": "NA",
    "12": "PRUEBAS",
    "10": "RETIE/LAP",
    "11": "RIESGO",
    "15": "RITEL",
    "4": "VARIABLES ELECTRICAS",
}

GROUP_KEYS = [
    "periodo_piso",
    "entityid",
    "ejecutivo_key",
    "linea_comision_key",
    "periodo_tiene_config",
    "linea_tiene_mapeo",
]


def to_month_period(series: pd.Series) -> pd.Series:
    if str(series.dtype).startswith("period"):
        return series.astype("period[M]")
    return normalize_date(series).dt.to_period("M")


def build_floor_compliance(
    sales_floor: pd.DataFrame,
    targets: pd.DataFrame,
    costs: pd.DataFrame,
) -> pd.DataFrame:
    """Calculate floor compliance while keeping costs informational only."""
    _require_columns(
        sales_floor,
        (
            "tranDate",
            "entityid",
            "custitem_sm_lineacomision",
            "TotalParaPiso_final",
            "lote",
            "estado_piso",
        ),
        "sales_floor",
    )
    _require_columns(
        targets,
        ("periodo", "ejecutivo", "linea_comision", "meta", "piso"),
        "targets",
    )
    _require_columns(costs, ("lote", "Valor"), "costs")

    base = sales_floor.copy()
    if "periodo_factura" not in base.columns:
        base["periodo_factura"] = normalize_date(base["tranDate"]).dt.to_period("M")
    base["periodo_piso"] = to_month_period(base["periodo_factura"])
    base["ejecutivo_key"] = normalize_upper(base["entityid"])
    base["custitem_sm_lineacomision_key"] = normalize_id(
        base["custitem_sm_lineacomision"]
    )
    base["TotalParaPiso_final"] = normalize_number(base["TotalParaPiso_final"])
    base["lote_key"] = normalize_text(base["lote"]).fillna("")
    line_map = pd.DataFrame(
        {
            "custitem_sm_lineacomision_key": list(COMMISSION_LINE_MAP),
            "linea_comision_key": list(COMMISSION_LINE_MAP.values()),
        }
    )
    base = base.merge(
        line_map,
        on="custitem_sm_lineacomision_key",
        how="left",
        validate="m:1",
    )
    base["tiene_revision_origen"] = (
        base["estado_piso"].astype("string").str.strip().str.lower().eq("para revision")
    )

    cost_work = costs.copy()
    cost_work.columns = [str(column).strip() for column in cost_work.columns]
    cost_work["lote_key"] = normalize_text(cost_work["lote"]).fillna("")
    cost_work["costo"] = normalize_number(cost_work["Valor"]).abs().fillna(0)
    cost_work = cost_work.groupby("lote_key", as_index=False).agg(costo=("costo", "sum"))

    target_work = targets.copy()
    target_work["periodo_piso"] = normalize_date(target_work["periodo"]).dt.to_period("M")
    target_work["ejecutivo_key"] = normalize_upper(target_work["ejecutivo"])
    target_work["linea_comision_key"] = normalize_upper(target_work["linea_comision"])
    target_work["meta"] = normalize_number(target_work["meta"])
    target_work["piso"] = normalize_number(target_work["piso"])
    duplicates = (
        target_work[["periodo_piso", "ejecutivo_key", "linea_comision_key"]]
        .dropna()
        .groupby(["periodo_piso", "ejecutivo_key", "linea_comision_key"], as_index=False)
        .size()
    )
    duplicates = duplicates.loc[duplicates["size"].gt(1)]
    if not duplicates.empty:
        raise ValueError(
            "Hay duplicados en pisos por periodo+ejecutivo+linea.\n"
            + duplicates.head(20).to_string(index=False)
        )

    configured_periods = set(target_work["periodo_piso"].dropna())
    base["periodo_tiene_config"] = base["periodo_piso"].isin(configured_periods)
    base["linea_tiene_mapeo"] = base["linea_comision_key"].notna()
    sales_sum = (
        base.groupby(GROUP_KEYS, dropna=False, as_index=False)
        .agg(
            TotalParaPiso=("TotalParaPiso_final", "sum"),
            tiene_revision_origen=("tiene_revision_origen", "max"),
        )
    )
    cost_sum = (
        base[GROUP_KEYS + ["lote_key"]]
        .drop_duplicates(GROUP_KEYS + ["lote_key"], keep="first")
        .merge(cost_work, on="lote_key", how="left", validate="m:1")
        .groupby(GROUP_KEYS, dropna=False, as_index=False)
        .agg(costo=("costo", "sum"))
    )
    merged = sales_sum.merge(cost_sum, on=GROUP_KEYS, how="left", validate="1:1")
    merged["costo"] = normalize_number(merged["costo"]).fillna(0)
    merged = merged.merge(
        target_work[
            ["periodo_piso", "ejecutivo_key", "linea_comision_key", "meta", "piso"]
        ],
        on=["periodo_piso", "ejecutivo_key", "linea_comision_key"],
        how="left",
        validate="m:1",
    )
    for column in ("TotalParaPiso", "meta", "piso", "costo"):
        merged[column] = normalize_number(merged[column])
    merged["costo"] = merged["costo"].fillna(0)
    merged["cumple"] = False
    merged["observacion_cumple"] = "No cumple piso ni meta"
    merged["requiere_revision_cumplimiento"] = False
    merged["motivo_revision_cumplimiento"] = ""

    no_period = ~merged["periodo_tiene_config"].fillna(False)
    no_line_mapping = ~merged["linea_tiene_mapeo"].fillna(False)
    source_review = merged["tiene_revision_origen"].fillna(False)
    merged.loc[no_period, "requiere_revision_cumplimiento"] = True
    merged.loc[no_period, "motivo_revision_cumplimiento"] = (
        "Sin configuración de piso para el período"
    )
    merged.loc[no_period, "observacion_cumple"] = (
        "Para revisión: sin configuración de piso para el período"
    )
    unmapped = ~no_period & no_line_mapping
    merged.loc[unmapped, "requiere_revision_cumplimiento"] = True
    merged.loc[unmapped, "motivo_revision_cumplimiento"] = (
        "Sin mapeo de línea de comisión"
    )
    merged.loc[unmapped, "observacion_cumple"] = (
        "Para revisión: sin mapeo de línea de comisión"
    )
    review_from_source = ~no_period & ~no_line_mapping & source_review
    merged.loc[review_from_source, "requiere_revision_cumplimiento"] = True
    merged.loc[review_from_source, "motivo_revision_cumplimiento"] = (
        "Hay documentos con estado_piso='para revision' dentro de la línea/periodo"
    )
    merged.loc[review_from_source, "observacion_cumple"] = (
        "Para revisión: hay documentos en revisión dentro de la línea/periodo"
    )

    evaluable = ~merged["requiere_revision_cumplimiento"].fillna(False)
    merged["meta_configurada"] = merged["meta"].notna()
    merged["meta"] = normalize_number(merged["meta"]).fillna(0)
    merged["piso"] = normalize_number(merged["piso"]).fillna(0)
    merged["TotalParaPiso"] = normalize_number(merged["TotalParaPiso"]).fillna(0)
    meets_target = (
        evaluable
        & merged["meta_configurada"]
        & merged["TotalParaPiso"].ge(merged["meta"])
    )
    meets_floor = (
        evaluable
        & merged["meta_configurada"]
        & merged["TotalParaPiso"].lt(merged["meta"])
        & merged["TotalParaPiso"].ge(merged["piso"])
    )
    merged.loc[meets_target | meets_floor, "cumple"] = True
    merged.loc[meets_target, "observacion_cumple"] = "Cumple meta"
    merged.loc[meets_floor, "observacion_cumple"] = "Cumple piso"
    no_individual_compliance = evaluable & ~merged["cumple"].fillna(False)
    merged.loc[no_individual_compliance, "observacion_cumple"] = (
        "No cumple piso ni meta de la línea"
    )

    previously_compliant = merged["cumple"].fillna(False).copy()
    principal = (
        merged.loc[
            evaluable
            & merged["meta_configurada"]
            & merged["linea_comision_key"].ne("RITEL")
            & previously_compliant,
            ["periodo_piso", "ejecutivo_key", "linea_comision_key"],
        ]
        .dropna(subset=["linea_comision_key"])
        .groupby(["periodo_piso", "ejecutivo_key"], as_index=False)
        .agg(
            cumple_linea_principal=("linea_comision_key", "size"),
            lineas_principales_cumplidas=(
                "linea_comision_key",
                lambda values: " | ".join(dict.fromkeys(values.astype(str))),
            ),
        )
    )
    principal["cumple_linea_principal"] = principal["cumple_linea_principal"].gt(0)
    merged = merged.merge(
        principal,
        on=["periodo_piso", "ejecutivo_key"],
        how="left",
        validate="m:1",
    )
    evaluable = ~merged["requiere_revision_cumplimiento"].fillna(False)
    enabled_by_principal = evaluable & normalize_boolean(
        merged["cumple_linea_principal"]
    )
    newly_enabled = enabled_by_principal & ~merged["cumple"].fillna(False)
    merged.loc[enabled_by_principal, "cumple"] = True
    merged["habilitada_por_linea_principal"] = newly_enabled
    retie_is_principal = merged["lineas_principales_cumplidas"].fillna("").str.contains(
        r"(?:^| \| )RETIE/LAP(?:$| \| )", regex=True
    )
    merged["habilitada_por_retie"] = newly_enabled & retie_is_principal
    merged.loc[newly_enabled, "observacion_cumple"] = (
        "Cumple por habilitación de línea principal: "
        + merged.loc[newly_enabled, "lineas_principales_cumplidas"].fillna(
            "línea principal"
        )
    )

    merged["periodo_mes"] = merged["periodo_piso"].astype("period[M]")
    merged["linea_comision"] = merged["linea_comision_key"]
    columns = [
        "periodo_mes",
        "entityid",
        "linea_comision",
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
    result = merged[columns].sort_values(
        ["periodo_mes", "entityid", "linea_comision"], na_position="last"
    ).reset_index(drop=True)
    if result.duplicated(["periodo_mes", "entityid", "linea_comision"]).any():
        raise ValueError(
            "El resultado de cumplimiento contiene duplicados por periodo + ejecutivo + línea."
        )
    return result


def _require_columns(data: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = sorted(set(columns).difference(data.columns))
    if missing:
        raise ValueError(f"{name}: missing required columns: {', '.join(missing)}")
