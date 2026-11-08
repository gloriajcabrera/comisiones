"""Optional mail adapters configured outside the application repository."""

from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid
import os
import smtplib
import ssl
from typing import Sequence


def _boolean(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError("Los valores booleanos SMTP deben ser true o false")
    return normalized == "true"


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    sender: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    use_starttls: bool = True
    use_ssl: bool = False
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> "SmtpSettings | None":
        host = os.getenv("COMMISSIONS_SMTP_HOST", "").strip()
        if not host:
            return None
        sender = os.getenv("COMMISSIONS_SMTP_FROM", "").strip()
        if not sender:
            raise ValueError("Falta COMMISSIONS_SMTP_FROM")
        settings = cls(
            host=host,
            port=int(os.getenv("COMMISSIONS_SMTP_PORT", "587")),
            sender=sender,
            username=os.getenv("COMMISSIONS_SMTP_USERNAME") or None,
            password=os.getenv("COMMISSIONS_SMTP_PASSWORD") or None,
            use_starttls=_boolean(
                os.getenv("COMMISSIONS_SMTP_STARTTLS"), default=True
            ),
            use_ssl=_boolean(os.getenv("COMMISSIONS_SMTP_SSL"), default=False),
            timeout_seconds=float(os.getenv("COMMISSIONS_SMTP_TIMEOUT", "30")),
        )
        return settings.validate()

    def validate(self) -> "SmtpSettings":
        if self.use_ssl and self.use_starttls:
            raise ValueError("SMTP_SSL y SMTP_STARTTLS no pueden estar activos a la vez")
        if not 1 <= self.port <= 65535:
            raise ValueError("Puerto SMTP inválido")
        if self.timeout_seconds <= 0:
            raise ValueError("El timeout SMTP debe ser mayor que cero")
        if bool(self.username) != bool(self.password):
            raise ValueError("Usuario y contraseña SMTP deben configurarse juntos")
        return self


class SmtpMailProvider:
    def __init__(self, settings: SmtpSettings) -> None:
        self.settings = settings.validate()

    def send(
        self,
        *,
        recipients: Sequence[str],
        subject: str,
        body: str,
        attachment_name: str,
        attachment_bytes: bytes,
    ) -> str:
        message = EmailMessage()
        message["From"] = self.settings.sender
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        reference = make_msgid(domain=self.settings.sender.partition("@")[2] or None)
        message["Message-ID"] = reference
        message.set_content(body)
        message.add_attachment(
            attachment_bytes,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=attachment_name,
        )

        client_class = smtplib.SMTP_SSL if self.settings.use_ssl else smtplib.SMTP
        context = ssl.create_default_context()
        kwargs = {
            "host": self.settings.host,
            "port": self.settings.port,
            "timeout": self.settings.timeout_seconds,
        }
        if self.settings.use_ssl:
            kwargs["context"] = context
        with client_class(**kwargs) as client:
            if self.settings.use_starttls:
                client.starttls(context=context)
            if self.settings.username:
                client.login(self.settings.username, self.settings.password or "")
            client.send_message(message)
        return reference


def mail_provider_from_environment() -> SmtpMailProvider | None:
    settings = SmtpSettings.from_environment()
    return None if settings is None else SmtpMailProvider(settings)
