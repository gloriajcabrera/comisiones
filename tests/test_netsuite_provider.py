import os
import json
import unittest
from unittest.mock import patch

from src.netsuite_provider import (
    ApiSettings,
    NetSuiteApiHttpError,
    NetSuiteApiProvider,
    NetSuiteDataProvider,
    NetSuiteOdbcProvider,
    OdbcSettings,
    _limit_query_for_check,
    netsuite_provider_from_environment,
)
from src.netsuite_queries import (
    COSTS_SQL,
    CREDIT_NOTES_SQL,
    INVOICES_SQL,
    INVOICE_PAYMENTS_SQL,
    PROFORMA_PAYMENT_LINKS_SQL,
    PROFORMA_PAYMENTS_SQL,
    PROFORMAS_SQL,
    QUERY_CATALOG,
)


class FakeCursor:
    def __init__(self):
        self.description = []
        self.queries = []
        self._rows = []

    def execute(self, query):
        self.queries.append(query)
        if "custbody_ks_sm_tipoje = 6" in query:
            self.description = [("Valor",)]
            self._rows = [(-10,), (5,)]
        elif "t.type = 'PurchOrd'" in query:
            self.description = [("lote",), ("Valor",)]
            self._rows = [(None, 20), ("L1", 30)]
        else:
            self.description = [("documento",)]
            self._rows = [("DOC-1",)]
        return self

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


class NetSuiteProviderTest(unittest.TestCase):
    def test_payment_queries_include_stable_link_line_identifiers(self):
        for query in (
            INVOICE_PAYMENTS_SQL,
            PROFORMA_PAYMENTS_SQL,
            PROFORMA_PAYMENT_LINKS_SQL,
        ):
            self.assertIn("previousLine AS previous_line", query)
            self.assertIn("nextLine AS next_line", query)

    def test_catalog_check_limits_suiteql_before_optional_ordering(self):
        unordered = "SELECT id FROM transaction WHERE type = 'CustInvc'"
        ordered = unordered + " ORDER BY id DESC"

        self.assertEqual(
            unordered + " AND ROWNUM <= 1",
            _limit_query_for_check(unordered),
        )
        self.assertEqual(
            unordered + " AND ROWNUM <= 1 ORDER BY id DESC",
            _limit_query_for_check(ordered),
        )

    def test_transaction_queries_use_rest_compatible_createdfrom(self):
        for query in (INVOICES_SQL, COSTS_SQL, PROFORMAS_SQL, CREDIT_NOTES_SQL):
            self.assertIn("l.createdfrom AS createdfrom", query)
            self.assertNotIn("t.createdfrom", query)

    def test_transaction_queries_do_not_join_unused_accounting_alias(self):
        for query in (INVOICES_SQL, COSTS_SQL, PROFORMAS_SQL, CREDIT_NOTES_SQL):
            self.assertNotIn("TransactionAccountingLine u", query)
            self.assertNotIn("INNER JOIN account a", query)

    def test_environment_settings_never_require_credentials_in_code(self):
        env = {
            "NETSUITE_ODBC_DSN": "TestDsn",
            "NETSUITE_ODBC_USER": "user@example.com",
            "NETSUITE_ODBC_PASSWORD": "secret",
            "NETSUITE_ODBC_TIMEOUT_SECONDS": "15",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = OdbcSettings.from_environment()

        self.assertEqual(settings.dsn, "TestDsn")
        self.assertEqual(settings.timeout_seconds, 15)
        self.assertIn("UID=user@example.com", settings.connection_string())
        self.assertNotIn("secret", repr(settings))

    def test_missing_credentials_fail_before_connecting(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "NETSUITE_ODBC_USER"):
                OdbcSettings.from_environment()

    def test_provider_loads_every_catalog_query_and_applies_source_cleanup(self):
        connection = FakeConnection()
        provider = NetSuiteOdbcProvider(connection_factory=lambda: connection)

        dataset = provider.load_dataset()

        self.assertIsInstance(provider, NetSuiteDataProvider)
        self.assertEqual(len(connection.cursor_instance.queries), len(QUERY_CATALOG))
        self.assertTrue(connection.closed)
        self.assertEqual(dataset.costs["lote"].tolist(), ["L1"])
        self.assertEqual(dataset.proforma_cancellations["Valor"].tolist(), [-10])
        self.assertEqual(dataset.invoices["documento"].tolist(), ["DOC-1"])

    def test_timeout_must_be_positive(self):
        env = {
            "NETSUITE_ODBC_USER": "user",
            "NETSUITE_ODBC_PASSWORD": "secret",
            "NETSUITE_ODBC_TIMEOUT_SECONDS": "0",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "mayor que cero"):
                OdbcSettings.from_environment()

    def test_api_settings_hide_all_credentials(self):
        settings = ApiSettings(
            "5845631", "consumer-key", "consumer-secret",
            "token-id", "token-secret",
        )

        representation = repr(settings)

        self.assertIn("5845631", representation)
        for secret in ("consumer-key", "consumer-secret", "token-id", "token-secret"):
            self.assertNotIn(secret, representation)
        self.assertEqual(
            "https://5845631.suitetalk.api.netsuite.com/services/rest/query/v1/suiteql",
            settings.suiteql_url,
        )

    def test_api_query_signs_and_paginates_without_exposing_secrets(self):
        calls = []

        def transport(url, headers, body, timeout):
            calls.append((url, headers, json.loads(body), timeout))
            offset = 0 if "offset=0" in url else 2
            if offset == 0:
                return {
                    "items": [
                        {"links": [], "id": "1"},
                        {"links": [], "id": "2"},
                    ],
                    "totalResults": 3,
                    "hasMore": True,
                }
            return {
                "items": [{"links": [], "id": "3"}],
                "totalResults": 3,
                "hasMore": False,
            }

        settings = ApiSettings(
            "5845631", "consumer-key", "consumer-secret",
            "token-id", "token-secret", page_size=2,
        )
        frame = NetSuiteApiProvider(settings, transport)._query("SELECT id FROM transaction")

        self.assertEqual(["1", "2", "3"], frame["id"].tolist())
        self.assertNotIn("links", frame.columns)
        self.assertEqual(2, len(calls))
        self.assertEqual({"q": "SELECT id FROM transaction"}, calls[0][2])
        self.assertEqual("transient", calls[0][1]["Prefer"])
        authorization = calls[0][1]["Authorization"]
        self.assertIn('oauth_signature_method="HMAC-SHA256"', authorization)
        self.assertNotIn("consumer-secret", authorization)
        self.assertNotIn("token-secret", authorization)

    def test_api_query_rejects_result_above_configured_limit(self):
        settings = ApiSettings(
            "5845631", "key", "secret", "token", "token-secret",
            max_results=10,
        )
        provider = NetSuiteApiProvider(
            settings,
            lambda *_: {"items": [], "totalResults": 11, "hasMore": True},
        )

        with self.assertRaisesRegex(RuntimeError, "divida la consulta"):
            provider._query("SELECT id FROM transaction")

    def test_api_access_check_reads_only_one_row_without_persisting(self):
        calls = []

        def transport(url, headers, body, timeout):
            calls.append((url, json.loads(body)["q"]))
            return {
                "items": [{"id": "1"}],
                "totalResults": 500000,
                "hasMore": True,
            }

        provider = NetSuiteApiProvider(
            ApiSettings("5845631", "key", "secret", "token", "token-secret"),
            transport,
        )

        result = provider.check_access()

        self.assertEqual("READY", result["status"])
        self.assertEqual("authentication", result["mode"])
        self.assertEqual(1, result["authentication_rows"])
        self.assertEqual([], result["catalog"])
        self.assertEqual(1, len(calls))
        self.assertIn("limit=1", calls[0][0])
        self.assertIn("ROWNUM <= 1", calls[0][1])

    def test_api_catalog_check_reports_metadata_not_business_values(self):
        queries = []

        def transport(url, headers, body, timeout):
            queries.append(json.loads(body)["q"])
            return {
                "items": [{"secret_business_value": "not-returned"}],
                "totalResults": 1,
                "hasMore": False,
            }

        provider = NetSuiteApiProvider(
            ApiSettings("5845631", "key", "secret", "token", "token-secret"),
            transport,
        )

        result = provider.check_access(validate_catalog=True)

        self.assertEqual("catalog", result["mode"])
        self.assertEqual(len(QUERY_CATALOG), len(result["catalog"]))
        self.assertEqual(len(QUERY_CATALOG) + 2, len(queries))
        self.assertNotIn("not-returned", json.dumps(result))
        self.assertTrue(all(item["status"] == "OK" for item in result["catalog"]))

    def test_api_loads_proforma_payments_in_two_stages(self):
        queries = []

        def transport(url, headers, body, timeout):
            query = json.loads(body)["q"]
            queries.append(query)
            if "custbody_sm_docuemtnos_relacionados" in query:
                items = [{
                    "proforma_id": "10",
                    "proforma": "PR-10",
                    "deposito_id": "20",
                    "deposito": "DEP-20",
                }]
            elif "l.previousDoc IN (20)" in query:
                items = [{
                    "deposito_id": "20",
                    "pago_id": "30",
                    "documento_pago": "PAY-30",
                    "valor_pagado_proforma": "100",
                }]
            elif "custbody_ks_sm_tipoje = 6" in query:
                items = [{"Valor": "-10"}]
            elif "t.type = 'PurchOrd'" in query:
                items = [{"lote": "L1", "Valor": "20"}]
            else:
                items = [{"documento": "DOC-1"}]
            return {"items": items, "totalResults": len(items), "hasMore": False}

        provider = NetSuiteApiProvider(
            ApiSettings("5845631", "key", "secret", "token", "token-secret"),
            transport,
        )

        dataset = provider.load_dataset()

        self.assertEqual(["PR-10"], dataset.proforma_payments["proforma"].tolist())
        self.assertEqual(["PAY-30"], dataset.proforma_payments["documento_pago"].tolist())
        self.assertTrue(any("l.previousDoc IN (20)" in query for query in queries))
        self.assertFalse(any(
            "INNER JOIN previoustransactionlinelink l" in query
            and "custbody_sm_docuemtnos_relacionados" in query
            for query in queries
        ))

    def test_api_incremental_filters_documents_and_payments_by_modified_window(self):
        queries = []

        def transport(url, headers, body, timeout):
            query = json.loads(body)["q"]
            queries.append(query)
            return {"items": [], "totalResults": 0, "hasMore": False}

        provider = NetSuiteApiProvider(
            ApiSettings("5845631", "key", "secret", "token", "token-secret"),
            transport,
        )

        provider.load_dataset_modified_between("2026-10-01", "2026-11-01")

        self.assertTrue(any(
            "t.lastmodifieddate >= TO_DATE('2026-10-01'" in query
            and "t.lastmodifieddate < TO_DATE('2026-11-01'" in query
            for query in queries
        ))
        self.assertTrue(any(
            "pay.lastmodifieddate >= TO_DATE('2026-10-01'" in query
            and "pay.lastmodifieddate < TO_DATE('2026-11-01'" in query
            for query in queries
        ))

    def test_api_incremental_rejects_invalid_or_reversed_dates(self):
        provider = NetSuiteApiProvider(
            ApiSettings("5845631", "key", "secret", "token", "token-secret"),
            lambda *_: {"items": [], "totalResults": 0, "hasMore": False},
        )
        with self.assertRaises(ValueError):
            provider.load_dataset_modified_between("2026/10/01", "2026-11-01")
        with self.assertRaisesRegex(ValueError, "posterior"):
            provider.load_dataset_modified_between("2026-11-01", "2026-10-01")

    def test_api_retries_rate_limit_but_not_authentication_errors(self):
        attempts = []

        def rate_limited(*_):
            attempts.append("call")
            if len(attempts) < 3:
                raise NetSuiteApiHttpError(429, "rate limit")
            return {"items": [], "totalResults": 0, "hasMore": False}

        settings = ApiSettings(
            "5845631", "key", "secret", "token", "token-secret",
            max_retries=2,
        )
        with patch("src.netsuite_provider.time.sleep") as sleep:
            NetSuiteApiProvider(settings, rate_limited)._query("SELECT id FROM transaction")

        self.assertEqual(3, len(attempts))
        self.assertEqual(2, sleep.call_count)

        denied = NetSuiteApiProvider(
            settings,
            lambda *_: (_ for _ in ()).throw(NetSuiteApiHttpError(401, "denied")),
        )
        with patch("src.netsuite_provider.time.sleep") as sleep:
            with self.assertRaises(NetSuiteApiHttpError):
                denied._query("SELECT id FROM transaction")
        sleep.assert_not_called()

    def test_api_provider_uses_same_catalog_and_cleanup_as_odbc(self):
        calls = []

        def transport(url, headers, body, timeout):
            query = json.loads(body)["q"]
            calls.append(query)
            if "custbody_ks_sm_tipoje = 6" in query:
                items = [{"Valor": "-10"}, {"Valor": "5"}]
            elif "t.type = 'PurchOrd'" in query:
                items = [{"lote": None, "Valor": "20"}, {"lote": "L1", "Valor": "30"}]
            else:
                items = [{"documento": "DOC-1"}]
            return {"items": items, "totalResults": len(items), "hasMore": False}

        provider = NetSuiteApiProvider(
            ApiSettings("5845631", "key", "secret", "token", "token-secret"),
            transport,
        )
        dataset = provider.load_dataset()

        self.assertIsInstance(provider, NetSuiteDataProvider)
        self.assertEqual(len(QUERY_CATALOG), len(calls))
        self.assertEqual(["L1"], dataset.costs["lote"].tolist())
        self.assertEqual([-10], dataset.proforma_cancellations["Valor"].tolist())

    def test_environment_selects_api_provider(self):
        environment = {
            "NETSUITE_PROVIDER": "api",
            "NETSUITE_ACCOUNT_ID": "5845631",
            "NETSUITE_CONSUMER_KEY": "key",
            "NETSUITE_CONSUMER_SECRET": "secret",
            "NETSUITE_TOKEN_ID": "token",
            "NETSUITE_TOKEN_SECRET": "token-secret",
        }
        with patch.dict(os.environ, environment, clear=True):
            provider = netsuite_provider_from_environment()

        self.assertIsInstance(provider, NetSuiteApiProvider)


if __name__ == "__main__":
    unittest.main()
