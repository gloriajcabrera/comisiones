"""Preflight seguro de configuracion para el despliegue productivo."""

from __future__ import annotations

import os

from .authentication import LdapSettings
from .mail_providers import SmtpSettings
from .netsuite_provider import ApiSettings, OdbcSettings
from .runtime import PRODUCTION, RuntimeSettings


def _require_false(name: str) -> None:
    value = os.getenv(name, "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} debe ser true o false")
    if value == "true":
        raise RuntimeError(f"{name} debe estar deshabilitado en produccion")


def _validate_web_binding() -> tuple[str, int]:
    address = os.getenv("STREAMLIT_SERVER_ADDRESS", "127.0.0.1").strip()
    if not address:
        raise ValueError("STREAMLIT_SERVER_ADDRESS es obligatorio")
    port = int(os.getenv("STREAMLIT_SERVER_PORT", "8501"))
    if not 1 <= port <= 65535:
        raise ValueError("STREAMLIT_SERVER_PORT debe estar entre 1 y 65535")
    return address, port


def validate_production_environment() -> dict[str, object]:
    """Valida configuracion sin abrir conexiones ni revelar valores sensibles."""
    runtime = RuntimeSettings.from_environment()
    if runtime.environment != PRODUCTION:
        raise RuntimeError("production-check requiere COMMISSIONS_ENV=production")
    if os.getenv("COMMISSIONS_AUTH_MODE", "ldap").strip().lower() != "ldap":
        raise RuntimeError("Produccion requiere autenticacion LDAP/LDAPS")
    _require_false("LOCAL_TEST_AUTH_ENABLED")
    _require_false("COMMISSION_APP_DEMO")
    address, port = _validate_web_binding()

    LdapSettings.from_environment()
    provider = os.getenv("NETSUITE_PROVIDER", "odbc").strip().lower()
    if provider == "api":
        ApiSettings.from_environment()
    elif provider == "odbc":
        OdbcSettings.from_environment()
    else:
        raise ValueError("NETSUITE_PROVIDER debe ser api u odbc")
    smtp = SmtpSettings.from_environment()
    return {
        "operation": "production-check",
        "status": "READY",
        "database": "postgresql",
        "authentication": "ldap",
        "netsuite_provider": provider,
        "smtp": "configured" if smtp is not None else "disabled",
        "web_binding": f"{address}:{port}",
    }
