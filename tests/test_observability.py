import json
import logging
import unittest

from src.observability import JsonLogFormatter, safe_error_message


class ObservabilityTest(unittest.TestCase):
    def test_error_message_redacts_secrets_and_removes_line_breaks(self):
        message = safe_error_message(
            "password=abc123\ntoken:xyz https://user:pass@example.com/path"
        )

        self.assertNotIn("abc123", message)
        self.assertNotIn("xyz", message)
        self.assertNotIn("user:pass", message)
        self.assertNotIn("\n", message)
        self.assertIn("password=[REDACTED]", message)

    def test_error_message_is_bounded(self):
        self.assertEqual(2000, len(safe_error_message("x" * 2500)))

    def test_json_formatter_includes_operational_context(self):
        record = logging.LogRecord(
            "src.test", logging.INFO, __file__, 1, "Finalizado", (), None
        )
        record.event = "calculation.completed"
        record.run_code = "calc-1"
        record.status = "COMPLETADO"
        record.actor_user_id = 7
        record.period = "2026-08"

        payload = json.loads(JsonLogFormatter().format(record))

        self.assertEqual("Finalizado", payload["message"])
        self.assertEqual("calculation.completed", payload["event"])
        self.assertEqual("calc-1", payload["run_code"])
        self.assertEqual(7, payload["actor_user_id"])


if __name__ == "__main__":
    unittest.main()
