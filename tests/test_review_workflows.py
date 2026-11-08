from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.access_control import (
    AccessDeniedError,
    AuthorizationService,
    ROLE_EXECUTIVE,
    ROLE_PARAMETERIZER,
    ROLE_REVIEWER,
    SqliteAccessRepository,
)
from src.review_workflows import (
    CLOSED,
    EXCEPTION_APPROVED,
    EXCEPTION_FLOOR,
    EXCEPTION_PAYMENT,
    EXCEPTION_INACTIVE,
    IN_REVIEW,
    PENDING_APPROVAL,
    REQUEST_APPROVED,
    REQUESTED,
    RESOLVED,
    ReviewWorkflowService,
    SqliteReviewRepository,
)
from src.exception_rules import (
    COMMISSION_VALUE_EXECUTIVE,
    FLOOR_FORCE_COMPLY,
    PAYMENT_FORCE_PAY,
)


class ReviewWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "review.db"
        self.access = SqliteAccessRepository(self.db_path)
        self.authorization = AuthorizationService(self.access)
        self.repository = SqliteReviewRepository(self.db_path)
        self.documents = {
            ("EJ1", "2026-06"): {"SM1", "SM2"},
            ("EJ2", "2026-06"): {"SM2"},
        }
        self.service = ReviewWorkflowService(
            self.repository,
            self.authorization,
            self,
        )
        self.executive = self._user("executive", ROLE_EXECUTIVE)
        self.other = self._user("other", ROLE_EXECUTIVE)
        self.reviewer = self._user("reviewer", ROLE_REVIEWER)
        self.parameterizer = self._user("parameterizer", ROLE_PARAMETERIZER)
        self._link(self.executive, "EJ1")
        self._link(self.other, "EJ2")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _user(self, name, role):
        user_id = self.access.create_user(name, name, "Test")
        self.access.assign_role(user_id, role)
        return user_id

    def _link(self, user_id, code):
        entity = self.access.create_commercial_entity(code, ROLE_EXECUTIVE, code)
        self.access.link_user_entity(user_id, entity, "2026-01-01")

    def validate_documents(
        self, actor_user_id, executive_code, period, documents
    ):
        requested = {str(document).strip().upper() for document in documents}
        allowed = self.documents.get((executive_code.strip().upper(), period), set())
        if not requested.issubset(allowed):
            raise AccessDeniedError(
                "Uno o mas documentos no pertenecen al ejecutivo y periodo autorizados"
            )

    def test_multiple_document_request_keeps_header_detail_and_history(self):
        request = self.service.create_request(
            self.executive, "EJ1", "2026-06", "2026-06-30", "Revisar pagos",
            [
                {"document": "SM1", "observation": "Pago no visible"},
                {"document": "SM2", "observation": "Validar fecha"},
            ],
        )
        self.assertEqual(request.status, REQUESTED)
        self.assertEqual(request.item_count, 2)
        for status in [IN_REVIEW, PENDING_APPROVAL, REQUEST_APPROVED, RESOLVED, CLOSED]:
            if status == RESOLVED:
                self.service.resolve_request_item(
                    self.reviewer, request.request_id, "SM1", "PAGA", "Pago validado"
                )
            request = self.service.transition_request(
                self.reviewer, request.request_id, status, "Concepto"
            )
        self.assertEqual(request.status, CLOSED)
        with closing(sqlite3.connect(self.db_path)) as connection:
            history = connection.execute(
                "SELECT COUNT(*) FROM review_request_status_history WHERE request_id = ?",
                (request.request_id,),
            ).fetchone()[0]
        self.assertEqual(history, 6)

    def test_document_context_returns_latest_request_and_final_concept(self):
        first = self.service.create_request(
            self.executive, "EJ1", "2026-06", "2026-06-30", "Primera revisión",
            [{"document": "SM1", "observation": "Primera"}],
        )
        self.service.resolve_request_item(
            self.reviewer, first.request_id, "SM1", "NO PAGA", "Concepto anterior"
        )
        second = self.service.create_request(
            self.executive, "EJ1", "2026-06", "2026-06-30", "Nueva revisión",
            [{"document": "SM1", "observation": "Segunda"}],
        )
        self.service.resolve_request_item(
            self.reviewer, second.request_id, "SM1", "PAGA", "Pago confirmado"
        )

        context = self.repository.document_review_context()

        self.assertEqual(1, len(context))
        self.assertEqual(second.request_id, context[0]["request_id"])
        self.assertEqual("Pago confirmado", context[0]["request_final_concept"])
        with closing(sqlite3.connect(self.db_path)) as connection:
            view_type = connection.execute(
                "SELECT type FROM sqlite_master WHERE name = 'vw_review_document_context'"
            ).fetchone()[0]
        self.assertEqual("view", view_type)

    def test_executive_cannot_request_review_for_another_executive(self):
        with self.assertRaises(AccessDeniedError):
            self.service.create_request(
                self.executive, "EJ2", "2026-06", "2026-06-30", "No autorizado",
                [{"document": "SM2"}],
            )

    def test_executive_cannot_attach_another_executives_document(self):
        with self.assertRaises(AccessDeniedError):
            self.service.create_request(
                self.executive,
                "EJ1",
                "2026-06",
                "2026-06-30",
                "Intento por identificador",
                [{"document": "SM3"}],
            )

    def test_requests_and_exceptions_reject_invalid_periods(self):
        with self.assertRaisesRegex(ValueError, "formato AAAA-MM"):
            self.service.create_request(
                self.executive, "EJ1", "2026-13", "2026-06-30", "Periodo malo",
                [{"document": "SM1"}],
            )
        with self.assertRaisesRegex(ValueError, "formato AAAA-MM"):
            self.service.propose_exception(
                self.reviewer, EXCEPTION_FLOOR, "EJ1", "2026-6", "2026-06-30",
                "FLOOR_FORCE_COMPLY", "false", "true", "Periodo malo",
            )

    def test_review_traceability_fields_are_required_in_backend(self):
        with self.assertRaisesRegex(ValueError, "motivo general"):
            self.service.create_request(
                self.executive, "EJ1", "2026-06", "2026-06-30", " ",
                [{"document": "SM1"}],
            )
        request = self.service.create_request(
            self.executive, "EJ1", "2026-06", "2026-06-30", "Revisar",
            [{"document": "SM1"}],
        )
        with self.assertRaisesRegex(ValueError, "concepto de la transición"):
            self.service.transition_request(
                self.reviewer, request.request_id, IN_REVIEW, ""
            )
        with self.assertRaisesRegex(ValueError, "resultado final"):
            self.service.resolve_request_item(
                self.reviewer, request.request_id, "SM1", "", "Concepto"
            )
        with self.assertRaisesRegex(ValueError, "concepto final"):
            self.service.resolve_request_item(
                self.reviewer, request.request_id, "SM1", "PAGA", ""
            )

        exception = self.service.propose_exception(
            self.reviewer, EXCEPTION_FLOOR, "EJ1", "2026-06", "2026-06-30",
            "FLOOR_FORCE_COMPLY", "false", "true", "Motivo",
        )
        self.service.submit_exception(self.reviewer, exception.exception_id)
        with self.assertRaisesRegex(ValueError, "concepto de la decisión"):
            self.service.decide_exception(
                self.parameterizer, exception.exception_id, True, ""
            )

    def test_payment_exception_requires_document_and_approval(self):
        with self.assertRaisesRegex(ValueError, "requiere una factura"):
            self.service.propose_exception(
                self.reviewer, EXCEPTION_PAYMENT, "EJ1", "2026-06", "2026-06-30",
                PAYMENT_FORCE_PAY, "NO PAGA", "PAGA", "Pago confirmado",
            )
        exception = self.service.propose_exception(
            self.reviewer, EXCEPTION_PAYMENT, "EJ1", "2026-06", "2026-06-30",
            PAYMENT_FORCE_PAY, "NO PAGA", "PAGA", "Pago confirmado", "SM1",
        )
        self.service.submit_exception(self.reviewer, exception.exception_id)
        exception = self.service.decide_exception(
            self.parameterizer, exception.exception_id, True, "Aprobada"
        )
        self.assertEqual(exception.status, EXCEPTION_APPROVED)
        active = self.repository.active_approved_exceptions("2026-06")
        self.assertEqual(active[0]["document"], "SM1")
        exception = self.service.deactivate_exception(
            self.parameterizer, exception.exception_id, "Fin de vigencia"
        )
        self.assertEqual(exception.status, EXCEPTION_INACTIVE)
        self.assertEqual(self.repository.active_approved_exceptions("2026-06"), [])

    def test_exception_id_cannot_be_submitted_by_another_proposer(self):
        exception = self.service.propose_exception(
            self.reviewer,
            EXCEPTION_PAYMENT,
            "EJ1",
            "2026-06",
            "2026-06-30",
            PAYMENT_FORCE_PAY,
            "NO PAGA",
            "PAGA",
            "Correccion documentada",
            "SM1",
        )

        with self.assertRaises(AccessDeniedError):
            self.service.submit_exception(
                self.parameterizer,
                exception.exception_id,
            )
        pending = self.service.submit_exception(
            self.reviewer,
            exception.exception_id,
        )
        self.assertEqual("PENDIENTE_APROBACION", pending.status)

    def test_exception_rule_and_commission_value_are_validated_on_proposal(self):
        with self.assertRaisesRegex(ValueError, "no soportada"):
            self.service.propose_exception(
                self.reviewer, EXCEPTION_PAYMENT, "EJ1", "2026-06", "2026-06-30",
                "REGLA_INVENTADA", "NO PAGA", "PAGA", "No válida", "SM1",
            )
        for invalid in ("texto", "-1", "NaN", "Infinity"):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, "valor excepcional"):
                    self.service.propose_exception(
                        self.reviewer, EXCEPTION_PAYMENT, "EJ1", "2026-06",
                        "2026-06-30", COMMISSION_VALUE_EXECUTIVE,
                        "0", invalid, "No válida", "SM1",
                    )

    def test_floor_exception_is_period_specific(self):
        exception = self.service.propose_exception(
            self.reviewer, EXCEPTION_FLOOR, "EJ1", "2026-06", "2026-06-30",
            FLOOR_FORCE_COMPLY, "NO CUMPLE", "CUMPLE", "Excepcion comercial",
        )
        self.service.submit_exception(self.reviewer, exception.exception_id)
        self.service.decide_exception(
            self.parameterizer, exception.exception_id, True, "Aprobada"
        )
        self.assertEqual(len(self.repository.active_approved_exceptions("2026-06")), 1)
        self.assertEqual(len(self.repository.active_approved_exceptions("2026-07")), 0)

    def test_overlapping_approved_exception_is_rejected(self):
        first = self.service.propose_exception(
            self.reviewer, EXCEPTION_FLOOR, "EJ1", "2026-06", "2026-06-30",
            FLOOR_FORCE_COMPLY, "false", "true", "Primera",
        )
        self.service.submit_exception(self.reviewer, first.exception_id)
        self.service.decide_exception(
            self.parameterizer, first.exception_id, True, "Aprobada"
        )
        second = self.service.propose_exception(
            self.reviewer, EXCEPTION_FLOOR, "EJ1", "2026-06", "2026-06-30",
            FLOOR_FORCE_COMPLY, "false", "true", "Segunda",
        )
        self.service.submit_exception(self.reviewer, second.exception_id)

        with self.assertRaisesRegex(ValueError, "misma regla"):
            self.service.decide_exception(
                self.parameterizer, second.exception_id, True, "Duplicada"
            )

    def test_request_and_exception_queues_respect_backend_scope(self):
        own_request = self.service.create_request(
            self.executive, "EJ1", "2026-06", "2026-06-30", "Propia",
            [{"document": "SM1"}],
        )
        self.service.create_request(
            self.other, "EJ2", "2026-06", "2026-06-30", "Ajena",
            [{"document": "SM2"}],
        )
        own_exception = self.service.propose_exception(
            self.reviewer, EXCEPTION_FLOOR, "EJ1", "2026-06", "2026-06-30",
            "FLOOR_FORCE_COMPLY", "false", "true", "Autorizada",
        )
        self.service.propose_exception(
            self.reviewer, EXCEPTION_FLOOR, "EJ2", "2026-06", "2026-06-30",
            "FLOOR_FORCE_COMPLY", "false", "true", "Fuera de alcance",
        )

        executive_requests = self.service.list_requests(
            self.executive, "2026-06-30"
        )
        executive_exceptions = self.service.list_exceptions(
            self.executive, "2026-06-30"
        )
        reviewer_requests = self.service.list_requests(
            self.reviewer, "2026-06-30"
        )

        self.assertEqual([own_request.request_id], [row["request_id"] for row in executive_requests])
        self.assertEqual([own_exception.exception_id], [row["exception_id"] for row in executive_exceptions])
        self.assertEqual(2, len(reviewer_requests))


if __name__ == "__main__":
    unittest.main()
