import unittest

from src.netsuite_queries import (
    COSTS_SQL,
    CREDIT_NOTES_SQL,
    INVOICES_SQL,
    PROFORMAS_SQL,
)


class NetSuiteQueriesTest(unittest.TestCase):
    def test_commercial_documents_exclude_service_line_18_at_source(self):
        expected = "i.custitem_sm_linea_serv NOT IN (18)"

        for query in (INVOICES_SQL, PROFORMAS_SQL, CREDIT_NOTES_SQL):
            with self.subTest(query=query):
                self.assertIn(expected, query)

        self.assertNotIn(expected, COSTS_SQL)

    def test_document_types_keep_the_line_filter(self):
        expected = "i.custitem_sm_linea_serv NOT IN (18)"
        queries_by_type = {
            "CustInvc": INVOICES_SQL,
            "SalesOrd": PROFORMAS_SQL,
            "CustCred": CREDIT_NOTES_SQL,
        }

        for document_type, query in queries_by_type.items():
            with self.subTest(document_type=document_type):
                self.assertIn(f"t.type = '{document_type}'", query)
                self.assertIn(expected, query)


if __name__ == "__main__":
    unittest.main()
