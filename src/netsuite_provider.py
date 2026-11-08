"""Abstraccion de lectura de NetSuite independiente del motor financiero."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Callable, Mapping, Protocol, runtime_checkable
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import pandas as pd

from .netsuite_queries import (
    PROFORMA_PAYMENT_DOCUMENTS_SQL,
    PROFORMA_PAYMENT_LINKS_SQL,
    QUERY_CATALOG,
)


@dataclass(frozen=True)
class NetSuiteDataset:
    invoices: pd.DataFrame
    costs: pd.DataFrame
    proformas: pd.DataFrame
    credit_notes: pd.DataFrame
    proforma_cancellations: pd.DataFrame
    invoice_payments: pd.DataFrame
    proforma_payments: pd.DataFrame


@runtime_checkable
class NetSuiteDataProvider(Protocol):
    def load_dataset(self) -> NetSuiteDataset:
        """Carga una fotografia consistente de los datos requeridos."""


@runtime_checkable
class IncrementalNetSuiteDataProvider(Protocol):
    def load_dataset_modified_between(
        self, start: str, end: str
    ) -> NetSuiteDataset:
        """Carga registros modificados dentro de un intervalo semiabierto."""


@dataclass(frozen=True)
class OdbcSettings:
    dsn: str
    user: str
    password: str = field(repr=False)
    timeout_seconds: int = 20

    @classmethod
    def from_environment(cls) -> "OdbcSettings":
        values = {
            "dsn": os.getenv("NETSUITE_ODBC_DSN", "NetSuite2").strip(),
            "user": os.getenv("NETSUITE_ODBC_USER", "").strip(),
            "password": os.getenv("NETSUITE_ODBC_PASSWORD", ""),
        }
        missing = [key for key in ("user", "password") if not values[key]]
        if missing:
            names = ", ".join(f"NETSUITE_ODBC_{key.upper()}" for key in missing)
            raise RuntimeError(f"Faltan variables de entorno de NetSuite: {names}")

        timeout = int(os.getenv("NETSUITE_ODBC_TIMEOUT_SECONDS", "20"))
        if timeout <= 0:
            raise ValueError("NETSUITE_ODBC_TIMEOUT_SECONDS debe ser mayor que cero")
        return cls(timeout_seconds=timeout, **values)

    def connection_string(self) -> str:
        return f"DSN={self.dsn};UID={self.user};PWD={self.password}"


@dataclass(frozen=True)
class ApiSettings:
    account_id: str
    consumer_key: str = field(repr=False)
    consumer_secret: str = field(repr=False)
    token_id: str = field(repr=False)
    token_secret: str = field(repr=False)
    timeout_seconds: int = 30
    page_size: int = 1000
    max_results: int = 100000
    max_retries: int = 3

    @classmethod
    def from_environment(cls) -> "ApiSettings":
        names = (
            "NETSUITE_ACCOUNT_ID", "NETSUITE_CONSUMER_KEY",
            "NETSUITE_CONSUMER_SECRET", "NETSUITE_TOKEN_ID",
            "NETSUITE_TOKEN_SECRET",
        )
        values = {name: os.getenv(name, "").strip() for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError("Faltan variables de NetSuite API: " + ", ".join(missing))
        settings = cls(
            account_id=values["NETSUITE_ACCOUNT_ID"],
            consumer_key=values["NETSUITE_CONSUMER_KEY"],
            consumer_secret=values["NETSUITE_CONSUMER_SECRET"],
            token_id=values["NETSUITE_TOKEN_ID"],
            token_secret=values["NETSUITE_TOKEN_SECRET"],
            timeout_seconds=int(os.getenv("NETSUITE_API_TIMEOUT_SECONDS", "30")),
            page_size=int(os.getenv("NETSUITE_API_PAGE_SIZE", "1000")),
            max_results=int(os.getenv("NETSUITE_API_MAX_RESULTS", "100000")),
            max_retries=int(os.getenv("NETSUITE_API_MAX_RETRIES", "3")),
        )
        return settings.validate()

    def validate(self) -> "ApiSettings":
        if self.timeout_seconds <= 0:
            raise ValueError("NETSUITE_API_TIMEOUT_SECONDS debe ser mayor que cero")
        if not 1 <= self.page_size <= 1000:
            raise ValueError("NETSUITE_API_PAGE_SIZE debe estar entre 1 y 1000")
        if not 1 <= self.max_results <= 100000:
            raise ValueError("NETSUITE_API_MAX_RESULTS debe estar entre 1 y 100000")
        if self.max_retries < 0:
            raise ValueError("NETSUITE_API_MAX_RETRIES no puede ser negativo")
        return self

    @property
    def suiteql_url(self) -> str:
        account = self.account_id.lower().replace("_", "-")
        return f"https://{account}.suitetalk.api.netsuite.com/services/rest/query/v1/suiteql"


ApiTransport = Callable[[str, Mapping[str, str], bytes, int], Mapping[str, object]]


class NetSuiteApiHttpError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"NetSuite REST respondio HTTP {status_code}: {detail[:1000]}")
        self.status_code = status_code


class NetSuiteCatalogCheckError(RuntimeError):
    def __init__(self, dataset: str, cause: BaseException) -> None:
        super().__init__(f"Fallo la validacion del conjunto {dataset}")
        self.dataset = dataset
        self.cause = cause


def _oauth_quote(value: object) -> str:
    return quote(str(value), safe="~-._")


def _oauth_header(settings: ApiSettings, method: str, url: str) -> str:
    oauth = {
        "oauth_consumer_key": settings.consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": settings.token_id,
        "oauth_version": "1.0",
    }
    parsed = urlsplit(url)
    parameters = [*parse_qsl(parsed.query, keep_blank_values=True), *oauth.items()]
    normalized = "&".join(
        f"{_oauth_quote(key)}={_oauth_quote(value)}"
        for key, value in sorted(parameters, key=lambda item: (_oauth_quote(item[0]), _oauth_quote(item[1])))
    )
    base_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    signature_base = "&".join(
        (_oauth_quote(method.upper()), _oauth_quote(base_url), _oauth_quote(normalized))
    )
    signing_key = f"{_oauth_quote(settings.consumer_secret)}&{_oauth_quote(settings.token_secret)}"
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), signature_base.encode(), hashlib.sha256).digest()
    ).decode()
    values = {"realm": settings.account_id, **oauth, "oauth_signature": signature}
    return "OAuth " + ", ".join(
        f'{key}="{_oauth_quote(value)}"' for key, value in values.items()
    )


def _urllib_transport(
    url: str, headers: Mapping[str, str], body: bytes, timeout: int
) -> Mapping[str, object]:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise NetSuiteApiHttpError(exc.code, detail) from exc


def _clean_source_frames(frames: dict[str, pd.DataFrame]) -> NetSuiteDataset:
    cancellations = frames["proforma_cancellations"].copy()
    if "Valor" in cancellations.columns:
        cancellations["Valor"] = pd.to_numeric(cancellations["Valor"], errors="coerce")
        cancellations = cancellations.loc[cancellations["Valor"].le(0)]
    frames["proforma_cancellations"] = cancellations.reset_index(drop=True)
    costs = frames["costs"]
    if "lote" in costs.columns:
        costs = costs.dropna(subset=["lote"])
    frames["costs"] = costs.reset_index(drop=True)
    return NetSuiteDataset(**frames)


def _limit_query_for_check(query: str) -> str:
    """Limit SuiteQL inside NetSuite instead of only limiting the HTTP page."""
    normalized = query.strip()
    marker = "ORDER BY"
    position = normalized.upper().rfind(marker)
    if position < 0:
        return f"{normalized} AND ROWNUM <= 1"
    return (
        f"{normalized[:position].rstrip()} AND ROWNUM <= 1 "
        f"{normalized[position:]}"
    )


def _add_query_conditions(query: str, *conditions: str) -> str:
    normalized = query.strip()
    suffix = " AND " + " AND ".join(conditions)
    position = normalized.upper().rfind("ORDER BY")
    if position < 0:
        return normalized + suffix
    return f"{normalized[:position].rstrip()}{suffix} {normalized[position:]}"


def _modified_window(alias: str, start: str, end: str) -> tuple[str, str]:
    parsed_start = datetime.strptime(start, "%Y-%m-%d").date()
    parsed_end = datetime.strptime(end, "%Y-%m-%d").date()
    if parsed_end <= parsed_start:
        raise ValueError("La fecha final debe ser posterior a la fecha inicial")
    return (
        f"{alias}.lastmodifieddate >= TO_DATE('{parsed_start.isoformat()}', 'YYYY-MM-DD')",
        f"{alias}.lastmodifieddate < TO_DATE('{parsed_end.isoformat()}', 'YYYY-MM-DD')",
    )


class NetSuiteOdbcProvider:
    def __init__(
        self,
        settings: OdbcSettings | None = None,
        connection_factory: Callable[[], object] | None = None,
    ) -> None:
        self._settings = settings
        self._connection_factory = connection_factory

    def _connect(self) -> object:
        if self._connection_factory is not None:
            return self._connection_factory()

        settings = self._settings or OdbcSettings.from_environment()
        try:
            import pyodbc
        except ImportError as exc:
            raise RuntimeError(
                "pyodbc es necesario solamente para usar NetSuiteOdbcProvider"
            ) from exc
        return pyodbc.connect(
            settings.connection_string(),
            timeout=settings.timeout_seconds,
        )

    @staticmethod
    def _read(cursor: object, query: str) -> pd.DataFrame:
        cursor.execute(query)
        columns = [column[0] for column in cursor.description]
        return pd.DataFrame.from_records(cursor.fetchall(), columns=columns)

    def load_dataset(self) -> NetSuiteDataset:
        with closing(self._connect()) as connection:
            cursor = connection.cursor()
            frames = {
                name: self._read(cursor, query)
                for name, query in QUERY_CATALOG.items()
            }

        return _clean_source_frames(frames)


class NetSuiteApiProvider:
    def __init__(
        self,
        settings: ApiSettings | None = None,
        transport: ApiTransport | None = None,
    ) -> None:
        self.settings = (settings or ApiSettings.from_environment()).validate()
        self.transport = transport or _urllib_transport

    def _request(
        self, url: str, headers: Mapping[str, str], body: bytes
    ) -> Mapping[str, object]:
        for attempt in range(self.settings.max_retries + 1):
            try:
                return self.transport(
                    url, headers, body, self.settings.timeout_seconds
                )
            except NetSuiteApiHttpError as exc:
                retryable = exc.status_code == 429 or exc.status_code >= 500
                if not retryable or attempt >= self.settings.max_retries:
                    raise
                time.sleep(min(2 ** attempt, 10))
        raise RuntimeError("No fue posible completar la solicitud a NetSuite")

    def _query(self, query: str, *, result_limit: int | None = None) -> pd.DataFrame:
        if result_limit is not None and result_limit <= 0:
            raise ValueError("result_limit debe ser mayor que cero")
        rows: list[dict[str, object]] = []
        offset = 0
        while True:
            page_size = self.settings.page_size
            if result_limit is not None:
                page_size = min(page_size, result_limit - len(rows))
            url = f"{self.settings.suiteql_url}?{urlencode({'limit': page_size, 'offset': offset})}"
            body = json.dumps({"q": query}, separators=(",", ":")).encode("utf-8")
            headers = {
                "Authorization": _oauth_header(self.settings, "POST", url),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Prefer": "transient",
            }
            response = self._request(url, headers, body)
            items = response.get("items", [])
            if not isinstance(items, list):
                raise RuntimeError("Respuesta SuiteQL invalida: items no es una lista")
            rows.extend(
                {key: value for key, value in item.items() if key != "links"}
                for item in items if isinstance(item, dict)
            )
            total = int(response.get("totalResults", len(rows)))
            if result_limit is not None and len(rows) >= result_limit:
                break
            if result_limit is None and total > self.settings.max_results:
                raise RuntimeError(
                    f"SuiteQL reporta {total} filas; divida la consulta en cortes de hasta "
                    f"{self.settings.max_results}"
                )
            if not bool(response.get("hasMore", offset + len(items) < total)):
                break
            if not items:
                raise RuntimeError("SuiteQL indico mas paginas pero devolvio una pagina vacia")
            offset += len(items)
        return pd.DataFrame.from_records(rows)

    @staticmethod
    def _netsuite_ids(values: pd.Series) -> list[str]:
        ids = []
        for value in values.dropna().astype(str).unique():
            normalized = value.strip()
            if not normalized.isdigit():
                raise ValueError("NetSuite devolvio un deposito_id no numerico")
            ids.append(normalized)
        return ids

    def _load_proforma_payments(self) -> pd.DataFrame:
        documents = self._query(PROFORMA_PAYMENT_DOCUMENTS_SQL)
        if "deposito_id" not in documents.columns or documents.empty:
            return pd.DataFrame(columns=(
                "proforma_id", "proforma", "fecha_proforma", "cliente_id",
                "cliente", "deposito_id", "deposito", "fecha_deposito",
                "pago_id", "documento_pago", "tipo_pago", "fecha_transaccion",
                "fecha_real_transaccion", "fecha_pago", "memo_pago",
                "valor_pagado_proforma", "tipo_vinculo",
                "previous_line", "next_line",
            ))

        ids = self._netsuite_ids(documents["deposito_id"])
        payment_frames = []
        for start in range(0, len(ids), 500):
            batch = ", ".join(ids[start:start + 500])
            payment_frames.append(
                self._query(
                    f"{PROFORMA_PAYMENT_LINKS_SQL.strip()} "
                    f"AND l.previousDoc IN ({batch})"
                )
            )
        payments = pd.concat(payment_frames, ignore_index=True) if payment_frames else pd.DataFrame()
        if payments.empty:
            return documents.iloc[0:0].copy()
        documents = documents.copy()
        payments = payments.copy()
        documents["deposito_id"] = documents["deposito_id"].astype(str)
        payments["deposito_id"] = payments["deposito_id"].astype(str)
        return documents.merge(payments, on="deposito_id", how="inner", validate="m:m")

    def _load_proforma_payments_modified_between(
        self, start: str, end: str
    ) -> pd.DataFrame:
        payments = self._query(_add_query_conditions(
            PROFORMA_PAYMENT_LINKS_SQL,
            *_modified_window("pay", start, end),
        ))
        if "deposito_id" not in payments.columns or payments.empty:
            return self._load_proforma_payments_empty()
        ids = self._netsuite_ids(payments["deposito_id"])
        document_frames = []
        for offset in range(0, len(ids), 500):
            batch = ", ".join(ids[offset:offset + 500])
            document_frames.append(self._query(_add_query_conditions(
                PROFORMA_PAYMENT_DOCUMENTS_SQL,
                f"dep.id IN ({batch})",
            )))
        documents = pd.concat(document_frames, ignore_index=True)
        documents["deposito_id"] = documents["deposito_id"].astype(str)
        payments["deposito_id"] = payments["deposito_id"].astype(str)
        return documents.merge(payments, on="deposito_id", how="inner", validate="m:m")

    @staticmethod
    def _load_proforma_payments_empty() -> pd.DataFrame:
        return pd.DataFrame(columns=(
            "proforma_id", "proforma", "fecha_proforma", "cliente_id",
            "cliente", "deposito_id", "deposito", "fecha_deposito",
            "pago_id", "documento_pago", "tipo_pago", "fecha_transaccion",
            "fecha_real_transaccion", "fecha_pago", "memo_pago",
            "valor_pagado_proforma", "tipo_vinculo",
            "previous_line", "next_line",
        ))

    def _check_proforma_payments(self) -> pd.DataFrame:
        documents = self._query(
            f"{PROFORMA_PAYMENT_DOCUMENTS_SQL.strip()} AND ROWNUM <= 1",
            result_limit=1,
        )
        links = self._query(
            f"{PROFORMA_PAYMENT_LINKS_SQL.strip()} AND ROWNUM <= 1",
            result_limit=1,
        )
        return pd.DataFrame(columns=[*documents.columns, *(
            column for column in links.columns if column != "deposito_id"
        )])

    def check_access(self, *, validate_catalog: bool = False) -> dict[str, object]:
        """Comprueba acceso de solo lectura sin persistir ni sincronizar datos."""
        authentication = self._query(
            "SELECT id FROM transaction WHERE ROWNUM <= 1",
            result_limit=1,
        )
        catalog: list[dict[str, object]] = []
        if validate_catalog:
            for name, query in QUERY_CATALOG.items():
                try:
                    frame = (
                        self._check_proforma_payments()
                        if name == "proforma_payments"
                        else self._query(
                            _limit_query_for_check(query), result_limit=1
                        )
                    )
                except Exception as exc:
                    raise NetSuiteCatalogCheckError(name, exc) from exc
                catalog.append(
                    {
                        "dataset": name,
                        "status": "OK",
                        "sample_rows": len(frame),
                        "column_count": len(frame.columns),
                    }
                )
        return {
            "operation": "netsuite-check",
            "status": "READY",
            "mode": "catalog" if validate_catalog else "authentication",
            "authentication_rows": len(authentication),
            "catalog": catalog,
        }

    def load_dataset(self) -> NetSuiteDataset:
        frames = {
            name: (
                self._load_proforma_payments()
                if name == "proforma_payments"
                else self._query(query)
            )
            for name, query in QUERY_CATALOG.items()
        }
        return _clean_source_frames(frames)

    def load_dataset_modified_between(
        self, start: str, end: str
    ) -> NetSuiteDataset:
        aliases = {
            "invoices": "t",
            "costs": "t",
            "proformas": "t",
            "credit_notes": "t",
            "proforma_cancellations": "t",
            "invoice_payments": "pay",
        }
        frames = {
            name: self._query(_add_query_conditions(
                QUERY_CATALOG[name],
                *_modified_window(alias, start, end),
            ))
            for name, alias in aliases.items()
        }
        frames["proforma_payments"] = (
            self._load_proforma_payments_modified_between(start, end)
        )
        return _clean_source_frames(frames)


def netsuite_provider_from_environment() -> NetSuiteDataProvider:
    provider = os.getenv("NETSUITE_PROVIDER", "odbc").strip().lower()
    if provider == "api":
        return NetSuiteApiProvider()
    if provider == "odbc":
        return NetSuiteOdbcProvider()
    raise ValueError("NETSUITE_PROVIDER debe ser api u odbc")
