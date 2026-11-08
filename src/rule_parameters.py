"""Parametros tipados y versionados de reglas financieras existentes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Protocol

from .access_control import AuthorizationService, PERM_PARAMETERS_CHANGE
from .database import Database


PARAM_DRAFT = "BORRADOR"
PARAM_PENDING = "PENDIENTE_APROBACION"
PARAM_APPROVED = "APROBADO"
PARAM_REJECTED = "RECHAZADO"


@dataclass(frozen=True)
class RuleParameters:
    payment_full_days: int = 30
    payment_half_days: int = 60
    payment_factor_full: float = 1.0
    payment_factor_half: float = 0.5
    payment_factor_late: float = 0.0
    product_first_payment_threshold: float = 0.50
    product_first_payment_days: int = 60
    default_commission_executive: float = 0.04
    default_commission_manager: float = 0.01
    default_commission_director: float = 0.02
    subcontract_factor: float = 0.50
    balance_tolerance_rate: float = 0.000001
    balance_tolerance_minimum: float = 1.0
    include_proformas: bool = True

    def validate(self) -> "RuleParameters":
        if self.payment_full_days < 0:
            raise ValueError("payment_full_days no puede ser negativo")
        if self.payment_half_days < self.payment_full_days:
            raise ValueError("payment_half_days debe ser mayor o igual a payment_full_days")
        if not 0 <= self.product_first_payment_threshold <= 1:
            raise ValueError("product_first_payment_threshold debe estar entre 0 y 1")
        if self.product_first_payment_days < 0:
            raise ValueError("product_first_payment_days no puede ser negativo")
        factors = (
            self.payment_factor_full, self.payment_factor_half,
            self.payment_factor_late, self.default_commission_executive,
            self.default_commission_manager, self.default_commission_director,
            self.subcontract_factor, self.balance_tolerance_rate,
            self.balance_tolerance_minimum,
        )
        if any(value < 0 for value in factors):
            raise ValueError("Factores, tasas y tolerancias no pueden ser negativos")
        return self


PARAMETER_METADATA = {
    "payment_full_days": ("Dias pago completo", "integer", "dias"),
    "payment_half_days": ("Dias pago medio", "integer", "dias"),
    "payment_factor_full": ("Factor pago completo", "decimal", "factor"),
    "payment_factor_half": ("Factor pago medio", "decimal", "factor"),
    "payment_factor_late": ("Factor pago tardio", "decimal", "factor"),
    "product_first_payment_threshold": ("Umbral primer pago PRODUCTO", "decimal", "proporcion"),
    "product_first_payment_days": ("Dias primer pago PRODUCTO", "integer", "dias"),
    "default_commission_executive": ("Comision ejecutivo por defecto", "decimal", "proporcion"),
    "default_commission_manager": ("Comision jefe por defecto", "decimal", "proporcion"),
    "default_commission_director": ("Comision gerente por defecto", "decimal", "proporcion"),
    "subcontract_factor": ("Factor subcontratado", "decimal", "factor"),
    "balance_tolerance_rate": ("Tolerancia relativa de saldo", "decimal", "proporcion"),
    "balance_tolerance_minimum": ("Tolerancia minima de saldo", "decimal", "moneda"),
    "include_proformas": ("Incluir proformas", "boolean", "bandera"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date_value(value: date | str | None) -> str | None:
    return date.fromisoformat(str(value)).isoformat() if value is not None else None


def _coerce_value(code: str, value: object) -> object:
    data_type = PARAMETER_METADATA[code][1]
    if data_type == "integer":
        return int(value)
    if data_type == "decimal":
        return float(value)
    if data_type == "boolean":
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "si", "sí"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
            raise ValueError(f"Valor booleano invalido para {code}: {value}")
        return bool(value)
    return value


class RuleParameterRepository(Protocol):
    def create_version(
        self, code: str, value: object, valid_from: date | str,
        created_by: int, valid_to: date | str | None = None,
        observation: str | None = None,
    ) -> int: ...

    def transition(
        self, version_id: int, expected: str, new_status: str,
        changed_by: int, concept: str | None = None,
    ) -> None: ...

    def load_parameters(self, effective_on: date | str) -> RuleParameters: ...

    def set_enabled(
        self, code: str, enabled: bool, changed_by: int, reason: str
    ) -> None: ...

    def list_definitions(self) -> list[dict[str, object]]: ...

    def list_versions(self, status: str | None = None) -> list[dict[str, object]]: ...

    def list_enabled_history(
        self, code: str | None = None
    ) -> list[dict[str, object]]: ...


class PersistentRuleParameterRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        identity = self.database.identity_primary_key()
        identifier = self.database.identifier_type()
        with self.database.session() as session:
            session.execute_statements((
                """
                CREATE TABLE IF NOT EXISTS rule_definitions (
                    code TEXT PRIMARY KEY, name TEXT NOT NULL,
                    description TEXT, data_type TEXT NOT NULL, unit TEXT,
                    is_enabled INTEGER NOT NULL DEFAULT 1
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS rule_parameter_versions (
                    id {identity},
                    parameter_code TEXT NOT NULL, version INTEGER NOT NULL,
                    value_json TEXT NOT NULL, valid_from TEXT NOT NULL,
                    valid_to TEXT, status TEXT NOT NULL,
                    created_by INTEGER NOT NULL, created_at TEXT NOT NULL,
                    modified_by INTEGER NOT NULL, modified_at TEXT NOT NULL,
                    approved_by INTEGER, approved_at TEXT, observation TEXT,
                    FOREIGN KEY (parameter_code) REFERENCES rule_definitions(code),
                    UNIQUE (parameter_code, version),
                    CHECK (valid_to IS NULL OR valid_to >= valid_from)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS rule_parameter_status_history (
                    id {identity},
                    parameter_version_id {identifier} NOT NULL,
                    previous_status TEXT, new_status TEXT NOT NULL,
                    changed_by INTEGER NOT NULL, changed_at TEXT NOT NULL,
                    concept TEXT,
                    FOREIGN KEY (parameter_version_id) REFERENCES rule_parameter_versions(id)
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS rule_definition_enabled_history (
                    id {identity},
                    parameter_code TEXT NOT NULL,
                    previous_enabled INTEGER NOT NULL,
                    new_enabled INTEGER NOT NULL,
                    changed_by INTEGER NOT NULL,
                    changed_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    FOREIGN KEY (parameter_code) REFERENCES rule_definitions(code)
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_rule_parameter_effective
                ON rule_parameter_versions(parameter_code, status, valid_from, valid_to, version)
                """,
            ))
            for code, (name, data_type, unit) in PARAMETER_METADATA.items():
                session.execute(
                    """
                    INSERT INTO rule_definitions
                    (code, name, description, data_type, unit)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(code) DO NOTHING
                    """,
                    (code, name, name, data_type, unit),
                )

    def create_version(
        self, code: str, value: object, valid_from: date | str,
        created_by: int, valid_to: date | str | None = None,
        observation: str | None = None,
    ) -> int:
        if code not in PARAMETER_METADATA:
            raise KeyError(f"Parametro inexistente: {code}")
        coerced = _coerce_value(code, value)
        normalized = getattr(replace(RuleParameters(), **{code: coerced}).validate(), code)
        start, end, now = _date_value(valid_from), _date_value(valid_to), _utc_now()
        with self.database.session() as session:
            session.lock_table_for_write("rule_parameter_versions")
            version = int(session.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM rule_parameter_versions WHERE parameter_code = ?",
                (code,),
            ).fetchone()[0])
            version_id = session.insert_returning_id(
                """
                INSERT INTO rule_parameter_versions (
                    parameter_code, version, value_json, valid_from, valid_to,
                    status, created_by, created_at, modified_by, modified_at,
                    observation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (code, version, json.dumps(normalized), start, end, PARAM_DRAFT,
                 created_by, now, created_by, now, observation),
            )
            session.execute(
                """
                INSERT INTO rule_parameter_status_history
                (parameter_version_id, previous_status, new_status, changed_by, changed_at, concept)
                VALUES (?, NULL, ?, ?, ?, ?)
                """,
                (version_id, PARAM_DRAFT, created_by, now, observation),
            )
        return version_id

    def transition(
        self, version_id: int, expected: str, new_status: str,
        changed_by: int, concept: str | None = None,
    ) -> None:
        with self.database.session() as session:
            session.lock_table_for_write("rule_parameter_versions")
            row = session.execute(
                "SELECT parameter_code, status, valid_from, valid_to FROM rule_parameter_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Version inexistente: {version_id}")
            if row[1] != expected:
                raise ValueError(f"Estado esperado {expected}; estado actual {row[1]}")
            if new_status == PARAM_APPROVED:
                overlap = session.execute(
                    """
                    SELECT id FROM rule_parameter_versions
                    WHERE parameter_code = ? AND status = ? AND id <> ?
                      AND COALESCE(valid_to, '9999-12-31') >= ?
                      AND COALESCE(?, '9999-12-31') >= valid_from LIMIT 1
                    """,
                    (row[0], PARAM_APPROVED, version_id, row[2], row[3]),
                ).fetchone()
                if overlap is not None:
                    raise ValueError("Existe una version aprobada con vigencia superpuesta")
            now = _utc_now()
            session.execute(
                """
                UPDATE rule_parameter_versions
                SET status = ?, modified_by = ?, modified_at = ?,
                    approved_by = CASE WHEN ? = ? THEN ? ELSE approved_by END,
                    approved_at = CASE WHEN ? = ? THEN ? ELSE approved_at END
                WHERE id = ?
                """,
                (new_status, changed_by, now, new_status, PARAM_APPROVED,
                 changed_by, new_status, PARAM_APPROVED, now, version_id),
            )
            session.execute(
                """
                INSERT INTO rule_parameter_status_history
                (parameter_version_id, previous_status, new_status, changed_by, changed_at, concept)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (version_id, expected, new_status, changed_by, now, concept),
            )

    def load_parameters(self, effective_on: date | str) -> RuleParameters:
        when = _date_value(effective_on)
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT v.parameter_code, v.value_json
                FROM rule_parameter_versions v
                JOIN rule_definitions d ON d.code = v.parameter_code
                WHERE d.is_enabled = 1 AND v.status = ? AND v.valid_from <= ?
                  AND COALESCE(v.valid_to, '9999-12-31') >= ?
                """,
                (PARAM_APPROVED, when, when),
            ).fetchall()
        values = asdict(RuleParameters())
        for code, value_json in rows:
            values[code] = json.loads(value_json)
        return RuleParameters(**values).validate()

    def set_enabled(
        self, code: str, enabled: bool, changed_by: int, reason: str
    ) -> None:
        if code not in PARAMETER_METADATA:
            raise KeyError(f"Parametro inexistente: {code}")
        normalized_reason = str(reason).strip()
        if not normalized_reason:
            raise ValueError("El motivo es obligatorio para habilitar o deshabilitar")
        with self.database.session() as session:
            session.lock_table_for_write("rule_definitions")
            row = session.execute(
                "SELECT is_enabled FROM rule_definitions WHERE code = ?", (code,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Parametro inexistente: {code}")
            previous = int(row[0])
            new_value = int(enabled)
            if previous == new_value:
                return
            session.execute(
                "UPDATE rule_definitions SET is_enabled = ? WHERE code = ?",
                (new_value, code),
            )
            session.execute(
                """
                INSERT INTO rule_definition_enabled_history (
                    parameter_code, previous_enabled, new_enabled,
                    changed_by, changed_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (code, previous, new_value, changed_by, _utc_now(), normalized_reason),
            )

    def list_definitions(self) -> list[dict[str, object]]:
        with self.database.session() as session:
            rows = session.execute(
                """
                SELECT code, name, description, data_type, unit, is_enabled
                FROM rule_definitions
                ORDER BY name, code
                """
            ).fetchall()
        columns = ("code", "name", "description", "data_type", "unit", "enabled")
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def list_versions(self, status: str | None = None) -> list[dict[str, object]]:
        query = """
            SELECT v.id, v.parameter_code, d.name, d.data_type, d.unit,
                   v.version, v.value_json, v.valid_from, v.valid_to, v.status,
                   d.is_enabled, v.created_by, v.created_at, v.modified_by,
                   v.modified_at, v.approved_by, v.approved_at, v.observation
            FROM rule_parameter_versions v
            JOIN rule_definitions d ON d.code = v.parameter_code
        """
        parameters: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE v.status = ?"
            parameters = (status,)
        query += " ORDER BY v.parameter_code, v.version DESC"
        with self.database.session() as session:
            rows = session.execute(query, parameters).fetchall()
        columns = (
            "version_id", "code", "name", "data_type", "unit", "version",
            "value", "valid_from", "valid_to", "status", "enabled",
            "created_by", "created_at", "modified_by", "modified_at",
            "approved_by", "approved_at", "observation",
        )
        result = [dict(zip(columns, row, strict=True)) for row in rows]
        for item in result:
            item["value"] = json.loads(str(item["value"]))
            item["enabled"] = bool(item["enabled"])
        return result

    def list_enabled_history(
        self, code: str | None = None
    ) -> list[dict[str, object]]:
        query = """
            SELECT id, parameter_code, previous_enabled, new_enabled,
                   changed_by, changed_at, reason
            FROM rule_definition_enabled_history
        """
        parameters: tuple[object, ...] = ()
        if code is not None:
            if code not in PARAMETER_METADATA:
                raise KeyError(f"Parametro inexistente: {code}")
            query += " WHERE parameter_code = ?"
            parameters = (code,)
        query += " ORDER BY changed_at DESC, id DESC"
        with self.database.session() as session:
            rows = session.execute(query, parameters).fetchall()
        columns = (
            "event_id", "code", "previous_enabled", "new_enabled",
            "changed_by", "changed_at", "reason",
        )
        result = [dict(zip(columns, row, strict=True)) for row in rows]
        for item in result:
            item["previous_enabled"] = bool(item["previous_enabled"])
            item["new_enabled"] = bool(item["new_enabled"])
        return result


class SqliteRuleParameterRepository(PersistentRuleParameterRepository):
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        super().__init__(Database.sqlite(self.database_path))


class PostgresRuleParameterRepository(PersistentRuleParameterRepository):
    def __init__(self, dsn: str) -> None:
        super().__init__(Database.postgresql(dsn))


class RuleParameterService:
    def __init__(self, repository: RuleParameterRepository,
                 authorization: AuthorizationService) -> None:
        self.repository = repository
        self.authorization = authorization

    def propose(self, actor_user_id: int, code: str, value: object,
                valid_from: date | str, valid_to: date | str | None = None,
                observation: str | None = None) -> int:
        self.authorization.require_permission(actor_user_id, PERM_PARAMETERS_CHANGE)
        return self.repository.create_version(
            code, value, valid_from, actor_user_id, valid_to, observation
        )

    def submit(self, actor_user_id: int, version_id: int) -> None:
        self.authorization.require_permission(actor_user_id, PERM_PARAMETERS_CHANGE)
        self.repository.transition(version_id, PARAM_DRAFT, PARAM_PENDING, actor_user_id)

    def decide(self, actor_user_id: int, version_id: int,
               approve: bool, concept: str) -> None:
        self.authorization.require_permission(actor_user_id, PERM_PARAMETERS_CHANGE)
        self.repository.transition(
            version_id, PARAM_PENDING,
            PARAM_APPROVED if approve else PARAM_REJECTED,
            actor_user_id, concept,
        )

    def set_enabled(
        self, actor_user_id: int, code: str, enabled: bool, reason: str
    ) -> None:
        self.authorization.require_permission(actor_user_id, PERM_PARAMETERS_CHANGE)
        self.repository.set_enabled(code, enabled, actor_user_id, reason)

    def list_definitions(self, actor_user_id: int) -> list[dict[str, object]]:
        self.authorization.require_permission(actor_user_id, PERM_PARAMETERS_CHANGE)
        return self.repository.list_definitions()

    def list_versions(
        self, actor_user_id: int, status: str | None = None
    ) -> list[dict[str, object]]:
        self.authorization.require_permission(actor_user_id, PERM_PARAMETERS_CHANGE)
        return self.repository.list_versions(status)

    def list_enabled_history(
        self, actor_user_id: int, code: str | None = None
    ) -> list[dict[str, object]]:
        self.authorization.require_permission(actor_user_id, PERM_PARAMETERS_CHANGE)
        return self.repository.list_enabled_history(code)
