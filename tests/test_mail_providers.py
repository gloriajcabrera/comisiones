import os
import unittest
from unittest.mock import patch

from src.mail_providers import (
    SmtpMailProvider,
    SmtpSettings,
    mail_provider_from_environment,
)


class FakeSmtp:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started_tls = False
        self.login_args = None
        self.message = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def starttls(self, context):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.message = message


class MailProvidersTest(unittest.TestCase):
    def setUp(self):
        FakeSmtp.instances.clear()

    def test_factory_is_disabled_without_host(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(mail_provider_from_environment())

    def test_external_configuration_requires_sender_and_consistent_security(self):
        with patch.dict(
            os.environ, {"COMMISSIONS_SMTP_HOST": "smtp.test"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "SMTP_FROM"):
                mail_provider_from_environment()

        with self.assertRaisesRegex(ValueError, "no pueden estar activos"):
            SmtpSettings(
                "smtp.test", 465, "pay@test", use_starttls=True, use_ssl=True
            ).validate()

    def test_smtp_settings_do_not_expose_password_in_representation(self):
        settings = SmtpSettings(
            host="smtp.test",
            port=587,
            sender="pay@test",
            username="service",
            password="smtp-secret-value",
        )

        self.assertNotIn("smtp-secret-value", repr(settings))

    @patch("src.mail_providers.smtplib.SMTP", FakeSmtp)
    def test_smtp_provider_uses_tls_authentication_and_memory_attachment(self):
        provider = SmtpMailProvider(SmtpSettings(
            host="smtp.test",
            port=587,
            sender="pay@test",
            username="service",
            password="secret",
        ))

        reference = provider.send(
            recipients=["payroll@test"],
            subject="Pago",
            body="Archivo aprobado",
            attachment_name="pago.xlsx",
            attachment_bytes=b"PK-memory-only",
        )

        client = FakeSmtp.instances[0]
        self.assertTrue(client.started_tls)
        self.assertEqual(("service", "secret"), client.login_args)
        self.assertEqual("payroll@test", client.message["To"])
        attachment = next(client.message.iter_attachments())
        self.assertEqual(b"PK-memory-only", attachment.get_payload(decode=True))
        self.assertEqual(reference, client.message["Message-ID"])


if __name__ == "__main__":
    unittest.main()
