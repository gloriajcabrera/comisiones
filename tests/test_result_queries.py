from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.access_control import (
    AccessDeniedError,
    AuthorizationService,
    REL_EXECUTIVE_MANAGER,
    ROLE_EXECUTIVE,
    ROLE_MANAGER,
    ROLE_REVIEWER,
    SqliteAccessRepository,
)
from src.result_queries import ResultQueryService


class FrameResultProvider:
    def __init__(self):
        self.commissions = pd.DataFrame(
            {
                "document": ["SM3", "SM1", "SM2"],
                "executive": ["EJ1", "EJ1", "EJ2"],
                "period": ["2026-01"] * 3,
                "commission_line": ["RETIE/LAP", "ENERGIA", "RETIE/LAP"],
                "decision": ["PAGA 100%"] * 3,
                "commission_executive": [30, 10, 20],
                "commission_manager": [6, 2, 4],
                "commission_director": [3, 1, 2],
            }
        )
        self.floor = pd.DataFrame(
            {
                "executive": ["EJ1", "EJ1", "EJ2"],
                "period": ["2026-01"] * 3,
                "commission_line": ["RETIE/LAP", "ENERGIA", "RETIE/LAP"],
                "complies": [True, True, True],
                "result_value": [100, 100, 100],
            }
        )
        self.documents = pd.DataFrame(
            {
                "document": ["SM3", "SM1", "SM2"],
                "document_date": ["2026-01-03", "2026-01-01", "2026-01-02"],
                "document_origin": [pd.NA, "PR-1", pd.NA],
                "credit_note": [pd.NA, "NC1", pd.NA],
                "replacement_document": [pd.NA, "SM1", pd.NA],
                "executive": ["EJ1", "EJ1", "EJ2"],
                "period": ["2026-01"] * 3,
                "commission_line": ["RETIE/LAP", "ENERGIA", "RETIE/LAP"],
                "document_value": [300, 100, 200],
                "adds_to_floor": [True, False, True],
            }
        )

    def commission_results(self, run_code):
        return self.commissions.copy()

    def floor_results(self, run_code):
        return self.floor.copy()

    def document_compliance_results(self, run_code):
        return self.documents.copy()

    def payment_summary(self, run_code, discounts=None):
        result = pd.DataFrame(
            {
                "executive": ["EJ1", "EJ2"],
                "period": ["2026-03", "2026-03"],
                "commission": [100.0, 200.0],
                "discounts": [0.0, 0.0],
                "payable": [100.0, 200.0],
                "documents": [2, 1],
            }
        )
        if discounts is not None and not discounts.empty:
            grouped = discounts.groupby(["executive", "period"], as_index=False)["amount"].sum()
            result = result.drop(columns=["discounts", "payable"]).merge(
                grouped.rename(columns={"amount": "discounts"}),
                on=["executive", "period"], how="left", validate="1:1",
            )
            result["discounts"] = result["discounts"].fillna(0)
            result["payable"] = result["commission"] - result["discounts"]
        return result


class FrameDiscountProvider:
    def list_discounts(self):
        return pd.DataFrame(
            {
                "discount_id": [1, 2],
                "executive": ["EJ1", "EJ2"],
                "period": ["2026-01", "2026-01"],
                "detection_period": ["2026-03", "2026-03"],
                "total_debt": [100.0, 200.0],
                "applied_amount": [25.0, 0.0],
                "balance": [75.0, 200.0],
                "status": ["APLICADO_PARCIAL", "PENDIENTE"],
                "commission_executive": [60.0, 120.0],
                "commission_manager": [25.0, 50.0],
                "commission_director": [15.0, 30.0],
            }
        )

    def list_applications(self, payment_period=None):
        data = pd.DataFrame(
            {"executive": ["EJ1"], "period": ["2026-03"], "amount": [25.0]}
        )
        if payment_period is not None:
            data = data.loc[data["period"].eq(payment_period)]
        return data


class ResultQueriesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.access = SqliteAccessRepository(root / "access.db")
        self.authorization = AuthorizationService(self.access)
        self.provider = FrameResultProvider()
        self.service = ResultQueryService(
            self.provider, self.authorization, FrameDiscountProvider()
        )
        self.executive = self._user("executive", ROLE_EXECUTIVE)
        entity = self.access.create_commercial_entity("EJ1", ROLE_EXECUTIVE, "EJ1")
        self.access.link_user_entity(self.executive, entity, "2026-01-01")
        self.reviewer = self._user("reviewer", ROLE_REVIEWER)
        self.manager = self._user("manager", ROLE_MANAGER)
        manager_entity = self.access.create_commercial_entity(
            "J1", ROLE_MANAGER, "J1"
        )
        self.access.link_user_entity(self.manager, manager_entity, "2026-01-01")
        self.access.assign_hierarchy(
            entity,
            manager_entity,
            REL_EXECUTIVE_MANAGER,
            "2026-01-01",
            changed_by=self.manager,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _user(self, username, role):
        user_id = self.access.create_user(username, username, "Test")
        self.access.assign_role(user_id, role)
        return user_id

    def test_executive_scope_is_enforced_without_frontend_filter(self):
        result = self.service.commissions(
            self.executive, "run-1", "2026-01-15", page_size=10
        )
        self.assertEqual(2, result.total)
        self.assertEqual({"EJ1"}, {item["executive"] for item in result.items})
        self.assertNotIn("commission_manager", result.items[0])
        self.assertNotIn("commission_director", result.items[0])

    def test_manager_cannot_read_director_commission(self):
        result = self.service.commissions(
            self.manager, "run-1", "2026-01-15", page_size=10
        )

        self.assertIn("commission_manager", result.items[0])
        self.assertNotIn("commission_director", result.items[0])

    def test_reviewer_keeps_all_commission_columns(self):
        result = self.service.commissions(
            self.reviewer, "run-1", "2026-01-15", page_size=10
        )

        self.assertIn("commission_manager", result.items[0])
        self.assertIn("commission_director", result.items[0])

    def test_explicit_out_of_scope_filter_is_rejected(self):
        with self.assertRaises(AccessDeniedError):
            self.service.commissions(
                self.executive, "run-1", "2026-01-15", executives=["EJ2"]
            )

    def test_global_sort_happens_before_pagination(self):
        first = self.service.commissions(
            self.reviewer, "run-1", "2026-01-15",
            sort_by="document", page=1, page_size=2,
        )
        second = self.service.commissions(
            self.reviewer, "run-1", "2026-01-15",
            sort_by="document", page=2, page_size=2,
        )
        self.assertEqual(["SM1", "SM2"], [item["document"] for item in first.items])
        self.assertEqual(["SM3"], [item["document"] for item in second.items])
        self.assertEqual(3, first.total)
        self.assertEqual(2, first.pages)

    def test_filters_are_combined_before_counting_pages(self):
        result = self.service.commissions(
            self.reviewer, "run-1", "2026-01-15",
            executives=["EJ1"], commission_lines=["RETIE/LAP"],
            decisions=["PAGA 100%"], page_size=1,
        )
        self.assertEqual(1, result.total)
        self.assertEqual("SM3", result.items[0]["document"])

    def test_floor_query_uses_same_scope_and_validates_sort(self):
        result = self.service.floor_results(
            self.executive, "run-1", "2026-01-15", complies=True
        )
        self.assertEqual(2, result.total)
        with self.assertRaisesRegex(ValueError, "Ordenamiento no permitido"):
            self.service.floor_results(
                self.reviewer, "run-1", "2026-01-15", sort_by="unsafe"
            )

    def test_document_compliance_filters_before_pagination_and_enforces_scope(self):
        result = self.service.document_compliance(
            self.executive,
            "run-1",
            "2026-01-15",
            adds_to_floor=True,
            sort_by="document_date",
            page_size=1,
        )

        self.assertEqual(1, result.total)
        self.assertEqual("SM3", result.items[0]["document"])
        with self.assertRaises(AccessDeniedError):
            self.service.document_compliance(
                self.executive, "run-1", "2026-01-15", executives=["EJ2"]
            )

    def test_discount_detail_uses_backend_scope_and_preserves_balance(self):
        result = self.service.discounts(
            self.executive,
            "2026-03-31",
            periods=["2026-01"],
            statuses=["APLICADO_PARCIAL"],
        )

        self.assertEqual(1, result.total)
        self.assertEqual(75.0, result.items[0]["balance"])
        self.assertNotIn("commission_manager", result.items[0])
        self.assertNotIn("commission_director", result.items[0])
        with self.assertRaises(AccessDeniedError):
            self.service.discounts(
                self.executive, "2026-03-31", executives=["EJ2"]
            )

    def test_payment_summary_subtracts_only_applied_discounts(self):
        result = self.service.payment_summary(
            self.executive, "run-1", "2026-03-31", periods=["2026-03"]
        )

        self.assertEqual(1, result.total)
        self.assertEqual(25.0, result.items[0]["discounts"])
        self.assertEqual(75.0, result.items[0]["payable"])


if __name__ == "__main__":
    unittest.main()
