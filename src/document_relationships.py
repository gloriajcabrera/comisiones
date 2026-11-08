from __future__ import annotations

import numpy as np
import pandas as pd

from src.normalization import (
    normalize_boolean,
    normalize_id,
    normalize_text,
    normalize_upper,
)


def join_unique(series: pd.Series):
    values = []
    for value in series.dropna().astype(str):
        value = value.strip()
        if value and value not in {"0", "nan", "<NA>"}:
            values.append(value)
    unique = sorted(set(values))
    return " + ".join(unique) if unique else pd.NA


def prepare_credit_notes(
    sales_base: pd.DataFrame,
    credit_notes: pd.DataFrame,
) -> pd.DataFrame:
    """Normalize NC relationship keys and resolve replacement invoice number."""
    _require_columns(
        sales_base,
        ("id_key", "tranId", "es_factura"),
        "sales_base",
    )
    _require_columns(
        credit_notes,
        (
            "createdfrom",
            "Servicio",
            "custbody_sm_factura_reemplazo",
            "tranId",
            "tranDate",
            "Valor",
        ),
        "credit_notes",
    )

    result = credit_notes.copy()
    result["createdfrom_key"] = normalize_id(result["createdfrom"])
    result["Servicio_key"] = normalize_upper(result["Servicio"])
    if "lote" in result.columns:
        result["lote_key"] = normalize_upper(result["lote"]).fillna("")
    else:
        result["lote_key"] = ""

    result["factura_reemplazo_key_nc"] = normalize_id(
        result["custbody_sm_factura_reemplazo"]
    )
    result.loc[
        result["factura_reemplazo_key_nc"].eq("0"),
        "factura_reemplazo_key_nc",
    ] = pd.NA

    invoice_id_to_number = (
        sales_base.loc[sales_base["es_factura"], ["id_key", "tranId"]]
        .dropna(subset=["id_key", "tranId"])
        .drop_duplicates(subset=["id_key"], keep="first")
        .set_index("id_key")["tranId"]
        .to_dict()
    )
    result["factura_reemplazo_tranId_nc"] = result[
        "factura_reemplazo_key_nc"
    ].map(invoice_id_to_number)
    return result


def attach_credit_notes(
    sales_base: pd.DataFrame,
    credit_notes: pd.DataFrame,
) -> pd.DataFrame:
    """Assign NC lines by invoice/service, then by value or aggregated lot."""
    _require_columns(
        sales_base,
        (
            "id_key",
            "tranId",
            "es_factura",
            "Servicio_key",
            "lote_key",
            "Valor",
            "valor_abs",
        ),
        "sales_base",
    )
    sales = sales_base.copy().reset_index(drop=True)
    sales["__rowid"] = np.arange(len(sales))
    notes = prepare_credit_notes(sales, credit_notes)

    note_counts = (
        notes.groupby(["createdfrom_key", "Servicio_key"], as_index=False)
        .agg(nc_count=("tranId", "nunique"))
    )
    note_join = notes[
        [
            "createdfrom_key",
            "Servicio_key",
            "lote_key",
            "tranId",
            "tranDate",
            "Valor",
            "factura_reemplazo_key_nc",
            "factura_reemplazo_tranId_nc",
        ]
    ].rename(
        columns={
            "tranId": "tranId_nc",
            "tranDate": "tranDate_nc",
            "Valor": "Valor_nc",
        }
    )
    invoice_lines = sales.loc[sales["es_factura"]].copy()
    candidates = (
        invoice_lines[
            [
                "__rowid",
                "id_key",
                "Servicio_key",
                "lote_key",
                "Valor",
                "valor_abs",
                "tranId",
            ]
        ]
        .rename(columns={"lote_key": "lote_venta_key"})
        .merge(
            note_join,
            left_on=["id_key", "Servicio_key"],
            right_on=["createdfrom_key", "Servicio_key"],
            how="left",
        )
        .merge(
            note_counts,
            left_on=["id_key", "Servicio_key"],
            right_on=["createdfrom_key", "Servicio_key"],
            how="left",
        )
    )
    candidates["Valor_nc"] = pd.to_numeric(candidates["Valor_nc"], errors="coerce")

    unique_service = candidates["nc_count"].eq(1) & candidates["Valor_nc"].notna()
    multiple_by_value = (
        candidates["nc_count"].gt(1)
        & candidates["Valor_nc"].notna()
        & candidates["valor_abs"].round(2).eq(candidates["Valor_nc"].abs().round(2))
    )
    candidates["match_valido"] = unique_service | multiple_by_value
    candidates["tipo_match_nc"] = pd.Series(pd.NA, index=candidates.index, dtype="string")
    candidates.loc[unique_service, "tipo_match_nc"] = "servicio_unica"
    candidates.loc[multiple_by_value, "tipo_match_nc"] = "servicio_valor"

    valid = (
        candidates.loc[candidates["match_valido"]]
        .sort_values(["__rowid", "tranDate_nc"])
        .drop_duplicates("__rowid", keep="last")
    )
    assigned = set(valid["__rowid"])
    pending = invoice_lines.loc[~invoice_lines["__rowid"].isin(assigned)].copy()
    pending = pending.rename(columns={"lote_key": "lote_venta_key"})

    note_by_lot = (
        notes.loc[notes["lote_key"].fillna("").ne("")]
        .groupby(["createdfrom_key", "Servicio_key", "lote_key"], as_index=False)
        .agg(
            Valor_nc=("Valor", "sum"),
            tranId_nc=("tranId", join_unique),
            tranDate_nc=("tranDate", "max"),
            factura_reemplazo_key_nc=("factura_reemplazo_key_nc", join_unique),
            factura_reemplazo_tranId_nc=(
                "factura_reemplazo_tranId_nc",
                join_unique,
            ),
        )
        .rename(columns={"lote_key": "lote_nc_key"})
    )
    lot_candidates = pending[
        ["__rowid", "id_key", "Servicio_key", "lote_venta_key", "valor_abs"]
    ].merge(
        note_by_lot,
        left_on=["id_key", "Servicio_key", "lote_venta_key"],
        right_on=["createdfrom_key", "Servicio_key", "lote_nc_key"],
        how="inner",
    )
    lot_candidates["Valor_nc"] = pd.to_numeric(
        lot_candidates["Valor_nc"], errors="coerce"
    )
    lot_candidates = lot_candidates.loc[
        lot_candidates["valor_abs"].round(2).ge(
            lot_candidates["Valor_nc"].abs().round(2)
        )
    ].copy()
    lot_candidates["abs_val_venta"] = lot_candidates["valor_abs"]
    lot_matches = (
        lot_candidates.sort_values(
            [
                "id_key",
                "Servicio_key",
                "lote_venta_key",
                "abs_val_venta",
                "tranDate_nc",
            ],
            ascending=[True, True, True, False, True],
        )
        .drop_duplicates(["id_key", "Servicio_key", "lote_venta_key"], keep="first")
    )
    lot_assignments = lot_matches[
        [
            "__rowid",
            "Valor_nc",
            "tranId_nc",
            "tranDate_nc",
            "factura_reemplazo_key_nc",
            "factura_reemplazo_tranId_nc",
        ]
    ].copy()
    lot_assignments["tipo_match_nc"] = "servicio_lote_agregado"

    assignment_columns = [
        "__rowid",
        "Valor_nc",
        "tranId_nc",
        "tranDate_nc",
        "factura_reemplazo_key_nc",
        "factura_reemplazo_tranId_nc",
        "tipo_match_nc",
    ]
    assignment_frames = [
        frame[assignment_columns]
        for frame in (valid, lot_assignments)
        if not frame.empty
    ]
    if assignment_frames:
        assignments = pd.concat(assignment_frames, ignore_index=True).drop_duplicates(
            "__rowid", keep="first"
        )
    else:
        assignments = pd.DataFrame(columns=assignment_columns)
    result = sales.merge(assignments, on="__rowid", how="left", validate="1:1")

    has_note = (
        candidates.groupby("__rowid")["tranId_nc"]
        .apply(lambda values: values.notna().any())
        .rename("hay_nc_del_serv")
        .reset_index()
    )
    counts_by_line = candidates[["__rowid", "nc_count"]].drop_duplicates("__rowid")
    result = result.merge(has_note, on="__rowid", how="left", validate="1:1")
    result = result.merge(counts_by_line, on="__rowid", how="left", validate="1:1")
    result["hay_nc_del_serv"] = normalize_boolean(result["hay_nc_del_serv"])
    result["nc_count"] = result["nc_count"].fillna(0).astype("int64")
    result["alarma_nc_servicio"] = (
        result["es_factura"]
        & result["hay_nc_del_serv"]
        & result["Valor_nc"].isna()
    )
    result["motivo_alarma_nc"] = ""
    result.loc[
        result["alarma_nc_servicio"] & result["nc_count"].gt(1),
        "motivo_alarma_nc",
    ] = "multiples_nc_sin_match_por_valor_o_lote"
    result.loc[
        result["alarma_nc_servicio"] & result["nc_count"].eq(1),
        "motivo_alarma_nc",
    ] = "unica_nc_sin_asignar"
    return result


def resolve_explicit_replacements(
    sales_with_notes: pd.DataFrame,
    credit_notes: pd.DataFrame,
) -> pd.DataFrame:
    """Resolve explicit replacement fields, NC links and created-from links.

    The service/lot/value fallback is intentionally excluded from this function.
    """
    _require_columns(
        sales_with_notes,
        (
            "id_key",
            "tranId",
            "tranDate",
            "es_factura",
            "es_proforma",
            "createdfrom_key",
            "reemplaza_a_key",
            "tranId_nc",
        ),
        "sales_with_notes",
    )
    result = sales_with_notes.copy()
    notes = prepare_credit_notes(result, credit_notes)
    documents = (
        result[["id_key", "tranId", "tranDate", "es_factura", "es_proforma"]]
        .dropna(subset=["id_key", "tranId"])
        .sort_values(["id_key", "tranDate", "tranId"])
        .drop_duplicates("id_key", keep="first")
    )
    id_to_number = documents.set_index("id_key")["tranId"].to_dict()
    id_to_date = documents.set_index("id_key")["tranDate"].to_dict()
    invoice_ids = set(documents.loc[documents["es_factura"], "id_key"])
    proforma_ids = set(documents.loc[documents["es_proforma"], "id_key"])

    result["documento_origen"] = pd.NA
    result["fecha_origen"] = pd.NaT
    result["documento_reemplazado"] = pd.NA
    result["tipo_reemplazo"] = "ninguno"
    result["reemplazo"] = False
    result["observacion_reemplazo"] = ""

    by_field = (
        result["es_factura"]
        & result["reemplaza_a_key"].notna()
        & result["reemplaza_a_key"].ne("0")
        & result["reemplaza_a_key"].isin(invoice_ids)
    )
    _assign_origin(result, by_field, result["reemplaza_a_key"], id_to_number, id_to_date)
    result.loc[by_field, "tipo_reemplazo"] = "reemplazo_factura_campo"
    result.loc[by_field, "reemplazo"] = True

    note_pairs = (
        notes.loc[
            notes["factura_reemplazo_key_nc"].notna(),
            [
                "factura_reemplazo_key_nc",
                "createdfrom_key",
                "factura_reemplazo_tranId_nc",
                "tranId",
                "tranDate",
            ],
        ]
        .drop_duplicates()
        .rename(columns={"tranId": "nota_credito_tranId", "tranDate": "nota_credito_fecha"})
    )
    by_id = _map_first(note_pairs, "factura_reemplazo_key_nc", "createdfrom_key")
    nc_by_id = _map_first(note_pairs, "factura_reemplazo_key_nc", "nota_credito_tranId")
    nc_date_by_id = _map_first(note_pairs, "factura_reemplazo_key_nc", "nota_credito_fecha")
    pairs_with_number = note_pairs.loc[note_pairs["factura_reemplazo_tranId_nc"].notna()]
    by_number = _map_first(pairs_with_number, "factura_reemplazo_tranId_nc", "createdfrom_key")
    nc_by_number = _map_first(pairs_with_number, "factura_reemplazo_tranId_nc", "nota_credito_tranId")
    nc_date_by_number = _map_first(pairs_with_number, "factura_reemplazo_tranId_nc", "nota_credito_fecha")

    replacement_by_nc_id = result["es_factura"] & result["id_key"].isin(by_id) & ~result["reemplazo"]
    origin_ids = result.loc[replacement_by_nc_id, "id_key"].map(by_id)
    _assign_origin(result, replacement_by_nc_id, origin_ids, id_to_number, id_to_date)
    result.loc[replacement_by_nc_id, "tipo_reemplazo"] = "reemplazo_factura_nc"
    result.loc[replacement_by_nc_id, "reemplazo"] = True
    result.loc[replacement_by_nc_id, "tranId_nc"] = result.loc[replacement_by_nc_id, "id_key"].map(nc_by_id)
    result.loc[replacement_by_nc_id, "tranDate_nc"] = pd.to_datetime(
        result.loc[replacement_by_nc_id, "id_key"].map(nc_date_by_id), errors="coerce"
    )

    replacement_by_nc_number = (
        result["es_factura"]
        & result["tranId"].isin(by_number)
        & ~result["reemplazo"]
    )
    origin_ids = result.loc[replacement_by_nc_number, "tranId"].map(by_number)
    _assign_origin(result, replacement_by_nc_number, origin_ids, id_to_number, id_to_date)
    result.loc[replacement_by_nc_number, "tipo_reemplazo"] = "reemplazo_factura_nc"
    result.loc[replacement_by_nc_number, "reemplazo"] = True
    result.loc[replacement_by_nc_number, "tranId_nc"] = result.loc[replacement_by_nc_number, "tranId"].map(nc_by_number)
    result.loc[replacement_by_nc_number, "tranDate_nc"] = pd.to_datetime(
        result.loc[replacement_by_nc_number, "tranId"].map(nc_date_by_number), errors="coerce"
    )

    from_invoice = (
        result["es_factura"]
        & result["createdfrom_key"].notna()
        & result["createdfrom_key"].ne("0")
        & result["createdfrom_key"].isin(invoice_ids)
        & ~result["reemplazo"]
    )
    _assign_origin(result, from_invoice, result["createdfrom_key"], id_to_number, id_to_date)
    result.loc[from_invoice, "tipo_reemplazo"] = "reemplazo_factura_createdfrom"
    result.loc[from_invoice, "reemplazo"] = True

    from_proforma = (
        result["es_factura"]
        & result["createdfrom_key"].notna()
        & result["createdfrom_key"].ne("0")
        & result["createdfrom_key"].isin(proforma_ids)
        & ~result["reemplazo"]
    )
    _assign_origin(result, from_proforma, result["createdfrom_key"], id_to_number, id_to_date)
    result.loc[from_proforma, "tipo_reemplazo"] = "reemplazo_proforma_createdfrom"
    result.loc[from_proforma, "reemplazo"] = True

    self_reference = (
        result["reemplazo"]
        & result["documento_origen"].notna()
        & normalize_text(result["tranId"]).eq(normalize_text(result["documento_origen"]))
    )
    result.loc[self_reference, "documento_origen"] = pd.NA
    result.loc[self_reference, "fecha_origen"] = pd.NaT
    result.loc[self_reference, "documento_reemplazado"] = pd.NA
    result.loc[self_reference, "tipo_reemplazo"] = "ninguno"
    result.loc[self_reference, "reemplazo"] = False

    note_number = normalize_text(result["tranId_nc"])
    note_detail = np.where(note_number.notna(), " mediante " + note_number.fillna(""), "")
    result["observacion_reemplazo"] = np.where(
        result["reemplazo"],
        "No suma por ser reemplazo de "
        + result["documento_origen"].astype("string")
        + note_detail,
        "",
    )
    return result


def apply_fallback_replacements(
    sales_with_explicit_relationships: pd.DataFrame,
    invoice_payments: pd.DataFrame,
) -> pd.DataFrame:
    """Infer remaining replacements by service, lot and value.

    This fallback is deliberately isolated because it is weaker than explicit
    NetSuite relationships. A positive linked payment on any document in the
    candidate group disables inference for the complete group.
    """
    _require_columns(
        sales_with_explicit_relationships,
        (
            "id_key",
            "tranId",
            "tranDate",
            "es_factura",
            "Servicio_key",
            "lote_key",
            "valor_abs",
            "reemplazo",
        ),
        "sales_with_explicit_relationships",
    )
    _require_columns(
        invoice_payments,
        ("factura", "tipo_pago", "valor_pago"),
        "invoice_payments",
    )
    result = sales_with_explicit_relationships.copy()
    fallback = result.loc[
        ~result["reemplazo"].fillna(False) & result["es_factura"],
        [
            "id_key",
            "tranId",
            "tranDate",
            "Servicio_key",
            "lote_key",
            "valor_abs",
        ],
    ].copy()
    fallback = fallback.loc[
        fallback["Servicio_key"].notna()
        & fallback["lote_key"].notna()
        & fallback["valor_abs"].notna()
    ]
    fallback = (
        fallback.drop_duplicates(
            ["id_key", "tranId", "Servicio_key", "lote_key", "valor_abs"],
            keep="first",
        )
        .sort_values(["Servicio_key", "lote_key", "valor_abs", "tranDate", "tranId"])
        .copy()
    )

    payment_types = normalize_upper(invoice_payments["tipo_pago"])
    payment_values = pd.to_numeric(
        invoice_payments["valor_pago"], errors="coerce"
    ).abs().fillna(0)
    documents_with_payment = set(
        normalize_upper(
            invoice_payments.loc[
                payment_values.gt(0) & payment_types.ne("CUSTCRED"),
                "factura",
            ]
        ).dropna()
    )
    fallback["tiene_pago_link"] = normalize_upper(fallback["tranId"]).isin(
        documents_with_payment
    )
    keys = ["Servicio_key", "lote_key", "valor_abs"]
    group_has_payment = fallback.groupby(keys, dropna=False)[
        "tiene_pago_link"
    ].transform("any")
    fallback = fallback.loc[~group_has_payment].copy()
    fallback["rank_fallback"] = fallback.groupby(keys).cumcount() + 1

    originals = fallback.loc[
        fallback["rank_fallback"].eq(1),
        keys + ["id_key", "tranId", "tranDate"],
    ].rename(
        columns={
            "id_key": "id_key_original",
            "tranId": "tranId_original",
            "tranDate": "fecha_original",
        }
    )
    replacements = fallback.loc[fallback["rank_fallback"].gt(1)].merge(
        originals,
        on=keys,
        how="left",
        validate="m:1",
    )
    replacements = replacements.loc[
        replacements["id_key"].ne(replacements["id_key_original"])
    ]
    replacement_ids = set(replacements["id_key"])
    mask = result["id_key"].isin(replacement_ids) & ~result["reemplazo"]
    origin_number = _map_first(replacements, "id_key", "tranId_original")
    origin_date = _map_first(replacements, "id_key", "fecha_original")
    result.loc[mask, "documento_origen"] = result.loc[mask, "id_key"].map(
        origin_number
    )
    result.loc[mask, "fecha_origen"] = pd.to_datetime(
        result.loc[mask, "id_key"].map(origin_date), errors="coerce"
    )
    result.loc[mask, "documento_reemplazado"] = result.loc[mask, "id_key"].map(
        origin_number
    )
    result.loc[mask, "tipo_reemplazo"] = "reemplazo_fallback"
    result.loc[mask, "reemplazo"] = True

    self_reference = (
        result["reemplazo"].fillna(False)
        & result["documento_origen"].notna()
        & normalize_text(result["tranId"]).eq(normalize_text(result["documento_origen"]))
    )
    result.loc[self_reference, "documento_origen"] = pd.NA
    result.loc[self_reference, "fecha_origen"] = pd.NaT
    result.loc[self_reference, "documento_reemplazado"] = pd.NA
    result.loc[self_reference, "tipo_reemplazo"] = "ninguno"
    result.loc[self_reference, "reemplazo"] = False

    note_number = normalize_text(result["tranId_nc"])
    note_detail = np.where(note_number.notna(), " mediante " + note_number.fillna(""), "")
    result["observacion_reemplazo"] = np.where(
        result["reemplazo"],
        "No suma por ser reemplazo de "
        + result["documento_origen"].astype("string")
        + note_detail,
        "",
    )
    return result


def _assign_origin(
    data: pd.DataFrame,
    mask: pd.Series,
    origin_ids: pd.Series,
    id_to_number: dict,
    id_to_date: dict,
) -> None:
    origin_numbers = origin_ids.map(id_to_number)
    data.loc[mask, "documento_origen"] = origin_numbers
    data.loc[mask, "fecha_origen"] = pd.to_datetime(origin_ids.map(id_to_date), errors="coerce")
    data.loc[mask, "documento_reemplazado"] = origin_numbers


def _map_first(data: pd.DataFrame, key: str, value: str) -> dict:
    return data.drop_duplicates(key, keep="first").set_index(key)[value].to_dict()


def _require_columns(data: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = sorted(set(columns).difference(data.columns))
    if missing:
        raise ValueError(f"{name}: missing required columns: {', '.join(missing)}")
