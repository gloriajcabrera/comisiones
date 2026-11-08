"""Descuentos trazables por anulaciones posteriores sin reemplazo."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import pandas as pd

from .normalization import normalize_boolean, normalize_number, normalize_upper
from .database import Database


DISCOUNT_PENDING = "PENDIENTE"
DISCOUNT_PARTIAL = "APLICADO_PARCIAL"
DISCOUNT_APPLIED = "APLICADO"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_cancellation_discounts(
    cancellations: pd.DataFrame,
    previous_compliance: pd.DataFrame,
    recalculated_compliance: pd.DataFrame,
    paid_commissions: pd.DataFrame,
    detection_period: str,
    source_calculation: str,
    recalculation: str,
) -> pd.DataFrame:
    """Return debts only where an eligible cancellation causes floor loss."""
    cancellation_columns = (
        "tranId",
        "tranId_nc",
        "entityid",
        "periodo_recalculo",
        "requiere_recalculo_piso",
        "factura_reemplazo_tranId_nc",
    )
    compliance_columns = ("entityid", "periodo_mes", "linea_comision", "cumple")
    commission_columns = (
        "executive",
        "period",
        "commission_line",
        "document",
        "commission_executive",
        "commission_manager",
        "commission_director",
    )
    _require_columns(cancellations, cancellation_columns, "cancellations")
    _require_columns(previous_compliance, compliance_columns, "previous_compliance")
    _require_columns(recalculated_compliance, compliance_columns, "recalculated_compliance")
    _require_columns(paid_commissions, commission_columns, "paid_commissions")

    affected = cancellations.copy()
    affected = affected.loc[
        normalize_boolean(affected["requiere_recalculo_piso"])
        & affected["factura_reemplazo_tranId_nc"].isna()
    ].copy()
    if affected.empty:
        return _empty_result()
    affected["executive"] = normalize_upper(affected["entityid"])
    affected["period"] = affected["periodo_recalculo"].astype("string")
    affected["document"] = normalize_upper(affected["tranId"])
    affected["credit_note"] = normalize_upper(affected["tranId_nc"])

    before = _prepare_compliance(previous_compliance, "complied_before")
    after = _prepare_compliance(recalculated_compliance, "complies_after")
    comparison = before.merge(
        after,
        on=["executive", "period", "commission_line"],
        how="outer",
        validate="1:1",
    )
    comparison[["complied_before", "complies_after"]] = comparison[
        ["complied_before", "complies_after"]
    ].fillna(False)
    lost = comparison.loc[
        comparison["complied_before"] & ~comparison["complies_after"]
    ].copy()
    if lost.empty:
        return _empty_result()

    paid = paid_commissions.copy()
    paid["executive"] = normalize_upper(paid["executive"])
    paid["period"] = paid["period"].astype("string")
    paid["commission_line"] = normalize_upper(paid["commission_line"])
    for column in (
        "commission_executive",
        "commission_manager",
        "commission_director",
    ):
        paid[column] = normalize_number(paid[column]).fillna(0)

    scope = affected[["executive", "period"]].drop_duplicates().merge(
        lost,
        on=["executive", "period"],
        how="inner",
        validate="1:m",
    )
    debt_lines = scope.merge(
        paid,
        on=["executive", "period", "commission_line"],
        how="inner",
        validate="1:m",
    )
    if debt_lines.empty:
        return _empty_result()

    debts = (
        debt_lines.groupby(["executive", "period"], as_index=False)
        .agg(
            commission_executive=("commission_executive", "sum"),
            commission_manager=("commission_manager", "sum"),
            commission_director=("commission_director", "sum"),
            affected_commission_lines=(
                "commission_line",
                lambda values: ",".join(sorted(set(values.dropna().astype(str)))),
            ),
            paid_documents=(
                "document",
                lambda values: ",".join(sorted(set(values.dropna().astype(str)))),
            ),
        )
    )
    triggers = (
        affected.groupby(["executive", "period"], as_index=False)
        .agg(
            cancelled_documents=(
                "document",
                lambda values: ",".join(sorted(set(values.dropna().astype(str)))),
            ),
            credit_notes=(
                "credit_note",
                lambda values: ",".join(sorted(set(values.dropna().astype(str)))),
            ),
        )
    )
    result = debts.merge(
        triggers, on=["executive", "period"], how="left", validate="1:1"
    )
    result["total_debt"] = result[
        ["commission_executive", "commission_manager", "commission_director"]
    ].sum(axis=1)
    result["detection_period"] = str(detection_period)
    result["source_calculation"] = str(source_calculation)
    result["recalculation"] = str(recalculation)
    result["applied_amount"] = 0.0
    result["balance"] = result["total_debt"]
    result["status"] = DISCOUNT_PENDING
    return result


def _prepare_compliance(data: pd.DataFrame, result_column: str) -> pd.DataFrame:
    work = data.copy()
    work["executive"] = normalize_upper(work["entityid"])
    work["period"] = work["periodo_mes"].astype("string")
    work["commission_line"] = normalize_upper(work["linea_comision"])
    work[result_column] = normalize_boolean(work["cumple"])
    keys = ["executive", "period", "commission_line"]
    if work.duplicated(keys).any():
        raise ValueError("Cumplimiento duplicado por periodo + ejecutivo + linea")
    return work[keys + [result_column]]


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "executive", "period", "commission_executive", "commission_manager",
            "commission_director", "affected_commission_lines", "paid_documents",
            "cancelled_documents", "credit_notes", "total_debt", "detection_period",
            "source_calculation", "recalculation", "applied_amount", "balance", "status",
        ]
    )


def _require_columns(data: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = sorted(set(columns).difference(data.columns))
    if missing:
        raise ValueError(f"{name}: missing required columns: {', '.join(missing)}")


class CancellationDiscountRepository(Protocol):
    def save(self, discounts: pd.DataFrame, created_by: str) -> list[int]: ...

    def apply(
        self, discount_id: int, amount: float, payment_period: str,
        applied_by: str, observation: str,
    ) -> None: ...

    def list_applications(self, payment_period: str | None = None) -> pd.DataFrame: ...

    def list_discounts(self) -> pd.DataFrame: ...


class PersistentCancellationDiscountRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        identity = self.database.identity_primary_key()
        identifier = self.database.identifier_type()
        money = self.database.money_type()
        applications_view = self.database.create_view_statement(
            "vw_discount_applications",
            """
            SELECT a.id AS application_id, a.discount_id,
                   d.executive, a.payment_period AS period, a.amount,
                   a.applied_by, a.applied_at, a.observation,
                   d.original_period, d.cancelled_documents, d.credit_notes
            FROM cancellation_discount_applications a
            JOIN cancellation_discounts d ON d.id = a.discount_id
            """,
        )
        with self.database.session() as session:
            session.execute_statements((
                f"""
                CREATE TABLE IF NOT EXISTS cancellation_discounts (
                    id {identity},
                    executive TEXT NOT NULL,
                    original_period TEXT NOT NULL,
                    detection_period TEXT NOT NULL,
                    source_calculation TEXT NOT NULL,
                    recalculation TEXT NOT NULL,
                    cancelled_documents TEXT NOT NULL,
                    credit_notes TEXT NOT NULL,
                    affected_commission_lines TEXT NOT NULL,
                    paid_documents TEXT NOT NULL,
                    commission_executive {money} NOT NULL,
                    commission_manager {money} NOT NULL,
                    commission_director {money} NOT NULL,
                    total_debt {money} NOT NULL,
                    applied_amount {money} NOT NULL DEFAULT 0,
                    balance {money} NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (executive, original_period, source_calculation, recalculation)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS cancellation_discount_applications (
                    id {identity},
                    discount_id {identifier} NOT NULL,
                    payment_period TEXT NOT NULL,
                    amount {money} NOT NULL,
                    applied_by TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    observation TEXT NOT NULL,
                    FOREIGN KEY (discount_id) REFERENCES cancellation_discounts(id)
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_cancellation_discount_scope
                ON cancellation_discounts(executive, original_period, status)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_discount_application_period
                ON cancellation_discount_applications(payment_period, discount_id)
                """,
                applications_view,
            ))

    def save(self, discounts: pd.DataFrame, created_by: str) -> list[int]:
        ids: list[int] = []
        with self.database.session() as session:
            session.lock_table_for_write("cancellation_discounts")
            for row in discounts.to_dict(orient="records"):
                existing = session.execute(
                    """
                    SELECT id FROM cancellation_discounts
                    WHERE executive = ? AND original_period = ?
                      AND source_calculation = ? AND recalculation = ?
                    """,
                    (
                        row["executive"], row["period"],
                        row["source_calculation"], row["recalculation"],
                    ),
                ).fetchone()
                if existing is not None:
                    ids.append(int(existing[0]))
                    continue
                discount_id = session.insert_returning_id(
                    """
                    INSERT INTO cancellation_discounts (
                        executive, original_period, detection_period,
                        source_calculation, recalculation, cancelled_documents,
                        credit_notes, affected_commission_lines, paid_documents,
                        commission_executive, commission_manager,
                        commission_director, total_debt, applied_amount,
                        balance, status, created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["executive"], row["period"], row["detection_period"],
                        row["source_calculation"], row["recalculation"],
                        row["cancelled_documents"], row["credit_notes"],
                        row["affected_commission_lines"], row["paid_documents"],
                        float(row["commission_executive"]),
                        float(row["commission_manager"]),
                        float(row["commission_director"]),
                        float(row["total_debt"]), float(row["applied_amount"]),
                        float(row["balance"]), row["status"],
                        created_by, _utc_now(),
                    ),
                )
                ids.append(discount_id)
        return ids

    def apply(
        self,
        discount_id: int,
        amount: float,
        payment_period: str,
        applied_by: str,
        observation: str,
    ) -> None:
        value = float(amount)
        if value <= 0:
            raise ValueError("El valor aplicado debe ser mayor que cero")
        if not str(observation).strip():
            raise ValueError("La observacion de aplicacion es obligatoria")
        with self.database.session() as session:
            session.lock_table_for_write("cancellation_discounts")
            row = session.execute(
                "SELECT applied_amount, balance FROM cancellation_discounts WHERE id = ?",
                (discount_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Descuento inexistente: {discount_id}")
            if value > float(row[1]):
                raise ValueError("El valor aplicado supera el saldo del descuento")
            applied = float(row[0]) + value
            balance = float(row[1]) - value
            status = DISCOUNT_APPLIED if balance == 0 else DISCOUNT_PARTIAL
            session.execute(
                """
                UPDATE cancellation_discounts
                SET applied_amount = ?, balance = ?, status = ? WHERE id = ?
                """,
                (applied, balance, status, discount_id),
            )
            session.execute(
                """
                INSERT INTO cancellation_discount_applications
                (discount_id, payment_period, amount, applied_by, applied_at, observation)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    discount_id, payment_period, value, applied_by,
                    _utc_now(), observation.strip(),
                ),
            )

    def list_applications(
        self, payment_period: str | None = None
    ) -> pd.DataFrame:
        """Return only applied amounts, shaped for payment-summary consumption."""
        query = """
            SELECT application_id, discount_id, executive, period, amount,
                   applied_by, applied_at, observation, original_period,
                   cancelled_documents, credit_notes
            FROM vw_discount_applications
        """
        parameters: tuple[object, ...] = ()
        if payment_period is not None:
            query += " WHERE period = ?"
            parameters = (str(payment_period),)
        query += " ORDER BY period, executive, application_id"
        with self.database.session() as session:
            rows = session.execute(query, parameters).fetchall()
        columns = [
            "application_id", "discount_id", "executive", "period", "amount",
            "applied_by", "applied_at", "observation", "original_period",
            "cancelled_documents", "credit_notes",
        ]
        return pd.DataFrame.from_records(rows, columns=columns)

    def list_discounts(self) -> pd.DataFrame:
        """Return traceable debts and their current balances in local currency."""
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT id AS discount_id, executive, original_period AS period,
                       detection_period, cancelled_documents, credit_notes,
                       affected_commission_lines, paid_documents,
                       commission_executive, commission_manager,
                       commission_director, total_debt, applied_amount,
                       balance, status, source_calculation, recalculation,
                       created_by, created_at
                FROM cancellation_discounts
                ORDER BY original_period, executive, id
                """
            ).fetchall()
        columns = [
            "discount_id", "executive", "period", "detection_period",
            "cancelled_documents", "credit_notes", "commission_lines",
            "paid_documents", "commission_executive", "commission_manager",
            "commission_director", "total_debt", "applied_amount", "balance",
            "status", "source_calculation", "recalculation", "created_by",
            "created_at",
        ]
        return pd.DataFrame.from_records(rows, columns=columns)


class SqliteCancellationDiscountRepository(
    PersistentCancellationDiscountRepository
):
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        super().__init__(Database.sqlite(self.database_path))


class PostgresCancellationDiscountRepository(
    PersistentCancellationDiscountRepository
):
    def __init__(self, dsn: str) -> None:
        super().__init__(Database.postgresql(dsn))
