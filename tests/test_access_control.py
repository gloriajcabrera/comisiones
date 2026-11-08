from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src import access_control as access
from src.access_control import (
    AccessDeniedError,
    AuthorizationService,
    EMPLOYMENT_ACTIVE,
    EMPLOYMENT_RETIRED,
    EmploymentStatusService,
    HierarchyService,
    PERM_NETSUITE_SYNC,
    PERM_CALCULATION_EXECUTE,
    PERM_PAYMENT_FILE_SEND,
    PERM_TARGET_VERIFY,
    REL_EXECUTIVE_MANAGER,
    REL_MANAGER_DIRECTOR,
    ROLE_DIRECTOR,
    ROLE_ADMIN,
    ROLE_EXECUTIVE,
    ROLE_MANAGER,
    ROLE_PARAMETERIZER,
    ROLE_REVIEWER,
    PostgresAccessRepository,
    SqliteAccessRepository,
    UserAdministrationService,
    TeamChangeRequestService,
    _PostgresAccessConnection,
)


class _Session:
    def __init__(self):
        self.calls = []

    def execute(self, sql, parameters=()):
        self.calls.append(("execute", sql, parameters))
        return "cursor"

    def insert_returning_id(self, sql, parameters=()):
        self.calls.append(("insert", sql, parameters))
        return 42

    def execute_statements(self, statements):
        self.calls.append(("statements", statements))


class _SessionContext:
    def __init__(self, session):
        self.session = session
        self.exit_args = None

    def __enter__(self):
        return self.session

    def __exit__(self, *args):
        self.exit_args = args


class _Database:
    def __init__(self):
        self.session_value = _Session()
        self.context = _SessionContext(self.session_value)

    def session(self):
        return self.context


class AccessControlTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "access.db"
        self.repository = SqliteAccessRepository(self.db_path)
        self.authorization = AuthorizationService(self.repository)

    def tearDown(self):
        self.temp_dir.cleanup()

    def user(self, username, role):
        user_id = self.repository.create_user(username, username, "Test")
        self.repository.assign_role(user_id, role)
        return user_id

    def entity_for(self, user_id, code, entity_type):
        entity_id = self.repository.create_commercial_entity(code, entity_type, code)
        self.repository.link_user_entity(user_id, entity_id, "2026-01-01")
        return entity_id

    def test_initial_admin_bootstrap_is_one_time_and_audited(self):
        user_id = self.repository.bootstrap_initial_administrator(
            "Sistemas", "Administrador", "Sistemas", "Alta inicial controlada"
        )

        self.assertEqual({ROLE_ADMIN}, self.authorization.effective_roles(user_id))
        self.authorization.require_permission(user_id, PERM_NETSUITE_SYNC)
        with self.assertRaisesRegex(RuntimeError, "no existen usuarios"):
            self.repository.bootstrap_initial_administrator(
                "otro", "Otro", "Administrador", "Segundo intento"
            )
        with closing(sqlite3.connect(self.db_path)) as connection:
            event = connection.execute(
                "SELECT actor_user_id, action, reason FROM audit_events "
                "WHERE entity_id=?",
                (str(user_id),),
            ).fetchone()
        self.assertEqual(
            (user_id, "INITIAL_ADMIN_BOOTSTRAPPED", "Alta inicial controlada"),
            event,
        )

    def test_role_permission_matrix_matches_approved_contract(self):
        expected = {
            access.ROLE_EXECUTIVE: {
                access.PERM_VIEW_OWN,
                access.PERM_REVIEW_CREATE,
            },
            access.ROLE_MANAGER: {
                access.PERM_VIEW_OWN,
                access.PERM_VIEW_TEAM,
                access.PERM_REVIEW_CREATE,
                access.PERM_TARGET_REGISTER,
                access.PERM_TEAM_PROPOSE,
            },
            access.ROLE_DIRECTOR: {
                access.PERM_VIEW_OWN,
                access.PERM_VIEW_TEAM,
                access.PERM_REVIEW_CREATE,
            },
            access.ROLE_REVIEWER: {
                access.PERM_VIEW_OWN,
                access.PERM_VIEW_TEAM,
                access.PERM_VIEW_ANY,
                access.PERM_REVIEW_CREATE,
                access.PERM_REVIEW_MANAGE,
                access.PERM_TARGET_VERIFY,
                access.PERM_INPUT_REGISTER,
                access.PERM_EXCEPTION_PROPOSE,
                access.PERM_PAYMENT_FILE_GENERATE,
                access.PERM_PAYMENT_FILE_DOWNLOAD,
                access.PERM_PERIOD_CLOSE,
            },
            access.ROLE_PARAMETERIZER: {
                access.PERM_VIEW_OWN,
                access.PERM_VIEW_TEAM,
                access.PERM_VIEW_ANY,
                access.PERM_REVIEW_CREATE,
                access.PERM_REVIEW_MANAGE,
                access.PERM_TARGET_REGISTER,
                access.PERM_TARGET_VERIFY,
                access.PERM_TARGET_APPROVE,
                access.PERM_INPUT_REGISTER,
                access.PERM_PARAMETERS_CHANGE,
                access.PERM_EXCEPTION_PROPOSE,
                access.PERM_EXCEPTION_APPROVE,
                access.PERM_PAYMENT_FILE_GENERATE,
                access.PERM_PAYMENT_FILE_DOWNLOAD,
                access.PERM_PERIOD_CLOSE,
                access.PERM_PERIOD_REOPEN,
                access.PERM_TEAM_APPROVE,
                access.PERM_CALCULATION_APPROVE,
            },
            access.ROLE_ADMIN: {
                access.PERM_VIEW_OWN,
                access.PERM_VIEW_TEAM,
                access.PERM_VIEW_ANY,
                access.PERM_REVIEW_CREATE,
                access.PERM_REVIEW_MANAGE,
                access.PERM_TARGET_REGISTER,
                access.PERM_TARGET_VERIFY,
                access.PERM_TARGET_APPROVE,
                access.PERM_INPUT_REGISTER,
                access.PERM_PARAMETERS_CHANGE,
                access.PERM_EXCEPTION_PROPOSE,
                access.PERM_EXCEPTION_APPROVE,
                access.PERM_PAYMENT_FILE_GENERATE,
                access.PERM_PAYMENT_FILE_DOWNLOAD,
                access.PERM_PERIOD_CLOSE,
                access.PERM_PERIOD_REOPEN,
                access.PERM_USERS_MANAGE,
                access.PERM_AUDIT_VIEW,
                access.PERM_TEAM_APPROVE,
                access.PERM_CALCULATION_APPROVE,
            },
        }

        self.assertEqual(expected, access.ROLE_PERMISSIONS)
        inherited = set().union(*access.ROLE_PERMISSIONS.values())
        self.assertTrue(
            {
                access.PERM_NETSUITE_SYNC,
                access.PERM_CALCULATION_EXECUTE,
                access.PERM_PAYMENT_FILE_SEND,
            }.isdisjoint(inherited)
        )

    def test_executive_cannot_access_another_executive(self):
        first = self.user("first", ROLE_EXECUTIVE)
        second = self.user("second", ROLE_EXECUTIVE)
        self.entity_for(first, "EJ1", ROLE_EXECUTIVE)
        self.entity_for(second, "EJ2", ROLE_EXECUTIVE)

        self.authorization.require_executive_access(first, "EJ1", "2026-06-01")
        with self.assertRaises(AccessDeniedError):
            self.authorization.require_executive_access(first, "EJ2", "2026-06-01")

    def test_postgres_access_adapter_preserves_generated_ids_and_transactions(self):
        database = _Database()
        with _PostgresAccessConnection(database) as connection:
            cursor = connection.execute(
                "INSERT INTO users (domain_username) VALUES (?)", ("user",)
            )
            selected = connection.execute("SELECT ?", (7,))
            connection.executescript(
                "CREATE TABLE a(id BIGINT); CREATE TABLE b(id BIGINT);"
            )

        self.assertEqual(42, cursor.lastrowid)
        self.assertEqual("cursor", selected)
        self.assertEqual("insert", database.session_value.calls[0][0])
        self.assertEqual("execute", database.session_value.calls[1][0])
        self.assertEqual("statements", database.session_value.calls[2][0])
        self.assertIsNone(database.context.exit_args[0])
        self.assertTrue(issubclass(PostgresAccessRepository, SqliteAccessRepository))

    def test_manager_scope_respects_hierarchy_validity(self):
        manager = self.user("manager", ROLE_MANAGER)
        executive = self.user("executive", ROLE_EXECUTIVE)
        manager_entity = self.entity_for(manager, "J1", ROLE_MANAGER)
        executive_entity = self.entity_for(executive, "EJ1", ROLE_EXECUTIVE)
        self.repository.assign_hierarchy(
            executive_entity, manager_entity, REL_EXECUTIVE_MANAGER,
            "2026-01-01", "2026-06-30",
        )

        self.authorization.require_executive_access(manager, "EJ1", "2026-06-01")
        with self.assertRaises(AccessDeniedError):
            self.authorization.require_executive_access(manager, "EJ1", "2026-07-01")

    def test_hierarchy_context_uses_relation_effective_in_queried_period(self):
        admin = self.user("context-admin", ROLE_ADMIN)
        executive = self.user("context-executive", ROLE_EXECUTIVE)
        manager_one = self.user("context-manager-one", ROLE_MANAGER)
        manager_two = self.user("context-manager-two", ROLE_MANAGER)
        executive_entity = self.entity_for(executive, "EJ-CONTEXT", ROLE_EXECUTIVE)
        first_entity = self.entity_for(manager_one, "J-OLD", ROLE_MANAGER)
        second_entity = self.entity_for(manager_two, "J-NEW", ROLE_MANAGER)
        self.repository.assign_hierarchy(
            executive_entity, first_entity, REL_EXECUTIVE_MANAGER,
            "2026-01-01", "2026-06-30", admin,
        )
        self.repository.assign_hierarchy(
            executive_entity, second_entity, REL_EXECUTIVE_MANAGER,
            "2026-07-01", None, admin,
        )

        old = self.authorization.hierarchy_context(
            admin, ["EJ-CONTEXT"], "2026-06-01"
        )[0]
        new = self.authorization.hierarchy_context(
            admin, ["EJ-CONTEXT"], "2026-07-01"
        )[0]

        self.assertEqual("J-OLD", old["manager"])
        self.assertEqual("J-NEW", new["manager"])

    def test_director_reaches_executives_through_manager(self):
        director = self.user("director", ROLE_DIRECTOR)
        manager = self.user("manager", ROLE_MANAGER)
        executive = self.user("executive", ROLE_EXECUTIVE)
        director_entity = self.entity_for(director, "G1", ROLE_DIRECTOR)
        manager_entity = self.entity_for(manager, "J1", ROLE_MANAGER)
        executive_entity = self.entity_for(executive, "EJ1", ROLE_EXECUTIVE)
        self.repository.assign_hierarchy(
            manager_entity, director_entity, REL_MANAGER_DIRECTOR, "2026-01-01"
        )
        self.repository.assign_hierarchy(
            executive_entity, manager_entity, REL_EXECUTIVE_MANAGER, "2026-01-01"
        )

        self.authorization.require_executive_access(director, "EJ1", "2026-06-01")

    def test_combined_reviewer_role_grants_global_scope(self):
        director = self.user("director", ROLE_DIRECTOR)
        self.repository.assign_role(director, ROLE_REVIEWER)

        self.authorization.require_executive_access(
            director, "ANY-EXECUTIVE", "2026-06-01"
        )

    def test_director_needs_parameterizer_role_for_global_scope(self):
        director = self.user("director-parameterizer", ROLE_DIRECTOR)

        with self.assertRaises(AccessDeniedError):
            self.authorization.require_executive_access(
                director, "UNRELATED-EXECUTIVE", "2026-06-01"
            )

        self.repository.assign_role(director, ROLE_PARAMETERIZER)

        self.authorization.require_executive_access(
            director, "UNRELATED-EXECUTIVE", "2026-06-01"
        )
        self.authorization.require_permission(
            director, access.PERM_PARAMETERS_CHANGE
        )

    def test_independent_sync_permission_requires_explicit_grant(self):
        reviewer = self.user("reviewer", ROLE_REVIEWER)
        with self.assertRaises(AccessDeniedError):
            self.authorization.require_permission(reviewer, PERM_NETSUITE_SYNC)

        self.repository.grant_user_permission(reviewer, PERM_NETSUITE_SYNC, True)
        self.authorization.require_permission(reviewer, PERM_NETSUITE_SYNC)

    def test_admin_manages_independent_permissions_with_reason_and_audit(self):
        admin = self.user("permissions-admin", ROLE_ADMIN)
        reviewer = self.user("permissions-reviewer", ROLE_REVIEWER)
        service = UserAdministrationService(self.repository, self.authorization)

        with self.assertRaisesRegex(ValueError, "observación"):
            service.set_independent_permissions(
                admin, reviewer, {PERM_NETSUITE_SYNC: True}, ""
            )

        service.set_independent_permissions(
            admin,
            reviewer,
            {
                PERM_NETSUITE_SYNC: True,
                PERM_CALCULATION_EXECUTE: True,
                PERM_PAYMENT_FILE_SEND: False,
            },
            "Responsable temporal de integración",
        )
        self.authorization.require_permission(reviewer, PERM_NETSUITE_SYNC)
        self.authorization.require_permission(reviewer, PERM_CALCULATION_EXECUTE)
        with self.assertRaises(AccessDeniedError):
            self.authorization.require_permission(reviewer, PERM_PAYMENT_FILE_SEND)

        with closing(sqlite3.connect(self.db_path)) as connection:
            audit = connection.execute(
                "SELECT reason FROM audit_events "
                "WHERE action='USER_PERMISSION_CHANGED' AND entity_id=? "
                "ORDER BY id DESC LIMIT 1",
                (str(reviewer),),
            ).fetchone()
        self.assertEqual(("Responsable temporal de integración",), audit)

    def test_direct_denial_overrides_role_permission(self):
        reviewer = self.user("reviewer", ROLE_REVIEWER)
        self.repository.grant_user_permission(
            reviewer, PERM_TARGET_VERIFY, False
        )
        with self.assertRaises(AccessDeniedError):
            self.authorization.require_permission(reviewer, PERM_TARGET_VERIFY)

    def test_employment_status_preserves_effective_history(self):
        admin = self.user("admin", ROLE_ADMIN)
        executive = self.user("retired", ROLE_EXECUTIVE)
        service = EmploymentStatusService(self.repository, self.authorization)

        service.change(
            admin, executive, EMPLOYMENT_RETIRED, "2026-07-15", "Retiro"
        )
        service.change(
            admin, executive, EMPLOYMENT_ACTIVE, "2026-09-01", "Reingreso"
        )

        self.assertEqual(
            EMPLOYMENT_ACTIVE,
            self.repository.employment_status_on(executive, "2026-07-14"),
        )
        self.assertEqual(
            EMPLOYMENT_RETIRED,
            self.repository.employment_status_on(executive, "2026-08-01"),
        )
        self.assertEqual(
            EMPLOYMENT_ACTIVE,
            self.repository.employment_status_on(executive, "2026-09-01"),
        )
        identity = self.repository.get_identity("retired")
        self.assertTrue(identity.active)

    def test_only_user_administrator_can_change_employment_status(self):
        reviewer = self.user("reviewer-employment", ROLE_REVIEWER)
        executive = self.user("employee", ROLE_EXECUTIVE)
        service = EmploymentStatusService(self.repository, self.authorization)

        with self.assertRaises(AccessDeniedError):
            service.change(
                reviewer, executive, EMPLOYMENT_RETIRED,
                "2026-07-15", "Sin permiso",
            )

    def test_user_administration_lists_multiple_roles_and_effective_structure(self):
        admin = self.user("admin-users", ROLE_ADMIN)
        executive = self.user("multi-role", ROLE_EXECUTIVE)
        self.repository.assign_role(executive, ROLE_REVIEWER, admin)
        self.entity_for(executive, "EJ-MULTI", ROLE_EXECUTIVE)
        service = UserAdministrationService(self.repository, self.authorization)

        profiles = service.list_profiles(admin, "2026-06-01")
        profile = next(item for item in profiles if item.user_id == executive)

        self.assertEqual((ROLE_EXECUTIVE, ROLE_REVIEWER), profile.roles)
        self.assertEqual(("EJ-MULTI",), profile.commercial_entities)
        self.assertEqual(EMPLOYMENT_ACTIVE, profile.employment_status)

    def test_user_access_change_requires_admin_and_reason(self):
        admin = self.user("admin-access", ROLE_ADMIN)
        executive = self.user("access-user", ROLE_EXECUTIVE)
        service = UserAdministrationService(self.repository, self.authorization)

        with self.assertRaisesRegex(ValueError, "motivo"):
            service.set_active(admin, executive, False, "")
        with self.assertRaises(AccessDeniedError):
            service.set_active(executive, admin, False, "Intento")
        service.set_active(admin, executive, False, "Retiro de acceso")
        self.assertFalse(self.repository.get_identity("access-user").active)

    def test_administrator_creates_and_updates_multi_role_user_with_history(self):
        admin = self.user("admin-profile", ROLE_ADMIN)
        service = UserAdministrationService(self.repository, self.authorization)

        user_id = service.create(
            admin, "commercial.user", "Nombre", "Inicial",
            [ROLE_EXECUTIVE, ROLE_REVIEWER], "123", "first@example.com",
            reason="Alta inicial",
        )
        service.update(
            admin, user_id, "123", "Nombre", "Actualizado",
            "updated@example.com", None, [ROLE_EXECUTIVE, ROLE_MANAGER],
            "Cambio de responsabilidad",
        )
        service.change_employment_status(
            admin, user_id, EMPLOYMENT_RETIRED, "2026-10-01", "Retiro"
        )

        profile = next(
            item for item in service.list_profiles(admin, "2026-10-15")
            if item.user_id == user_id
        )
        self.assertEqual("Actualizado", profile.last_names)
        self.assertEqual((ROLE_EXECUTIVE, ROLE_MANAGER), profile.roles)
        self.assertEqual(EMPLOYMENT_RETIRED, profile.employment_status)
        self.assertTrue(self.repository.get_identity("commercial.user").active)

        with closing(sqlite3.connect(self.db_path)) as connection:
            actions = {
                row[0] for row in connection.execute(
                    "SELECT action FROM audit_events WHERE entity_id = ?",
                    (str(user_id),),
                ).fetchall()
            }
        self.assertIn("USER_PROFILE_UPDATED", actions)
        self.assertIn("USER_ROLES_REPLACED", actions)

    def test_non_administrator_cannot_create_internal_user(self):
        executive = self.user("no-user-admin", ROLE_EXECUTIVE)
        service = UserAdministrationService(self.repository, self.authorization)

        with self.assertRaises(AccessDeniedError):
            service.create(
                executive, "forbidden", "No", "Access", [ROLE_EXECUTIVE],
                reason="Intento sin permiso",
            )

    def test_administrator_creates_commercial_identity_with_audit(self):
        admin = self.user("admin-entity", ROLE_ADMIN)
        executive = self.user("entity-user", ROLE_EXECUTIVE)
        service = UserAdministrationService(self.repository, self.authorization)

        entity_id = service.create_commercial_identity(
            admin, executive, "EJ-900", ROLE_EXECUTIVE,
            "Ejecutivo Novecientos", "2026-01-01",
        )
        profile = next(
            item for item in service.list_profiles(admin, "2026-06-01")
            if item.user_id == executive
        )

        self.assertEqual(("EJ-900",), profile.commercial_entities)
        with closing(sqlite3.connect(self.db_path)) as connection:
            actions = {
                row[0] for row in connection.execute(
                    "SELECT action FROM audit_events WHERE entity_id IN (?, ?)",
                    (str(entity_id), str(executive)),
                ).fetchall()
            }
        self.assertIn("COMMERCIAL_ENTITY_CREATED", actions)
        self.assertIn("USER_COMMERCIAL_ENTITY_LINKED", actions)

    def test_hierarchy_service_lists_history_and_rejects_wrong_entity_types(self):
        admin = self.user("admin-hierarchy-list", ROLE_ADMIN)
        executive = self.user("hierarchy-exec", ROLE_EXECUTIVE)
        manager = self.user("hierarchy-manager", ROLE_MANAGER)
        executive_entity = self.entity_for(executive, "EJ-H", ROLE_EXECUTIVE)
        manager_entity = self.entity_for(manager, "J-H", ROLE_MANAGER)
        service = HierarchyService(self.repository, self.authorization)

        assignment_id = service.assign(
            admin, executive_entity, manager_entity,
            REL_EXECUTIVE_MANAGER, "2026-01-01",
        )

        self.assertEqual("EJ-H", service.list_assignments(admin)[0]["source_code"])
        self.assertEqual(
            {"EJ-H"}, {item["code"] for item in service.list_entities(admin, ROLE_EXECUTIVE)}
        )
        self.assertEqual(assignment_id, service.list_assignments(admin)[0]["assignment_id"])
        with self.assertRaisesRegex(ValueError, "tipos"):
            service.assign(
                admin, manager_entity, executive_entity,
                REL_EXECUTIVE_MANAGER, "2027-01-01",
            )

        with self.assertRaises(AccessDeniedError):
            service.list_assignments(executive)

    def test_manager_proposal_creates_executive_only_after_parameterizer_approval(self):
        manager = self.user("proposal-manager", ROLE_MANAGER)
        parameterizer = self.user("proposal-parameterizer", ROLE_PARAMETERIZER)
        self.entity_for(manager, "J-PROP", ROLE_MANAGER)
        service = TeamChangeRequestService(self.repository, self.authorization)

        request_id = service.propose_new_executive(
            manager, "new.executive", "EJ-PROP", "Nueva", "Ejecutiva",
            "900", "new@example.com", "2026-11-01", "Ingreso al equipo",
        )

        self.assertIsNone(self.repository.get_identity("new.executive"))
        self.assertEqual(
            "PENDIENTE_APROBACION",
            service.list_for_actor(manager)[0]["status"],
        )

        service.decide(parameterizer, request_id, True, "Perfil validado")

        identity = self.repository.get_identity("new.executive")
        self.assertIsNotNone(identity)
        team = service.list_team(manager, "2026-11-01")
        self.assertEqual("EJ-PROP", team[0]["executive_code"])
        self.assertEqual("J-PROP", team[0]["manager_code"])
        decided = service.list_for_actor(parameterizer)[0]
        self.assertEqual("APROBADO", decided["status"])
        self.assertEqual(identity.user_id, decided["created_user_id"])

    def test_rejected_team_proposal_does_not_create_user(self):
        manager = self.user("reject-manager", ROLE_MANAGER)
        parameterizer = self.user("reject-parameterizer", ROLE_PARAMETERIZER)
        self.entity_for(manager, "J-REJECT", ROLE_MANAGER)
        service = TeamChangeRequestService(self.repository, self.authorization)
        request_id = service.propose_new_executive(
            manager, "rejected.user", "EJ-REJECT", "Rejected", "User",
            None, None, "2026-11-01", "Solicitud de prueba",
        )

        service.decide(parameterizer, request_id, False, "No aprobado")

        self.assertIsNone(self.repository.get_identity("rejected.user"))
        self.assertEqual("RECHAZADO", service.list_for_actor(manager)[0]["status"])

    def test_overlapping_hierarchy_is_rejected_and_actions_are_audited(self):
        manager = self.user("manager", ROLE_MANAGER)
        executive = self.user("executive", ROLE_EXECUTIVE)
        manager_entity = self.entity_for(manager, "J1", ROLE_MANAGER)
        executive_entity = self.entity_for(executive, "EJ1", ROLE_EXECUTIVE)
        self.repository.assign_hierarchy(
            executive_entity, manager_entity, REL_EXECUTIVE_MANAGER, "2026-01-01"
        )
        with self.assertRaisesRegex(ValueError, "superpone"):
            self.repository.assign_hierarchy(
                executive_entity, manager_entity, REL_EXECUTIVE_MANAGER, "2026-02-01"
            )

        with closing(sqlite3.connect(self.db_path)) as connection:
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM audit_events"
            ).fetchone()[0]
        self.assertGreaterEqual(audit_count, 5)

    def test_hierarchy_reassignment_closes_previous_vigency_and_is_audited(self):
        admin = self.user("admin-reassign", ROLE_ADMIN)
        executive = self.user("executive-reassign", ROLE_EXECUTIVE)
        manager_one = self.user("manager-one", ROLE_MANAGER)
        manager_two = self.user("manager-two", ROLE_MANAGER)
        executive_entity = self.entity_for(
            executive, "EJ-REASSIGN", ROLE_EXECUTIVE
        )
        manager_one_entity = self.entity_for(manager_one, "J-01", ROLE_MANAGER)
        manager_two_entity = self.entity_for(manager_two, "J-02", ROLE_MANAGER)
        self.repository.assign_hierarchy(
            executive_entity,
            manager_one_entity,
            REL_EXECUTIVE_MANAGER,
            "2026-01-01",
            changed_by=admin,
        )

        assignment_id = HierarchyService(
            self.repository, self.authorization
        ).reassign(
            admin,
            executive_entity,
            manager_two_entity,
            REL_EXECUTIVE_MANAGER,
            "2026-07-01",
            "Cambio de zona comercial",
        )

        with closing(sqlite3.connect(self.db_path)) as connection:
            assignments = connection.execute(
                "SELECT target_entity_id, valid_from, valid_to "
                "FROM hierarchy_assignments WHERE source_entity_id = ? "
                "ORDER BY valid_from",
                (executive_entity,),
            ).fetchall()
            audit = connection.execute(
                "SELECT action, reason FROM audit_events "
                "WHERE entity_type='HIERARCHY' AND entity_id=?",
                (str(assignment_id),),
            ).fetchone()
        self.assertEqual(
            [
                (manager_one_entity, "2026-01-01", "2026-06-30"),
                (manager_two_entity, "2026-07-01", None),
            ],
            assignments,
        )
        self.assertEqual(
            ("HIERARCHY_REASSIGNED", "Cambio de zona comercial"), audit
        )
        self.assertEqual(
            {"EJ-REASSIGN"},
            self.repository.allowed_executive_codes(manager_one, "2026-06-30"),
        )
        self.assertEqual(
            set(),
            self.repository.allowed_executive_codes(manager_one, "2026-07-01"),
        )
        self.assertEqual(
            {"EJ-REASSIGN"},
            self.repository.allowed_executive_codes(manager_two, "2026-07-01"),
        )

    def test_hierarchy_reassignment_requires_admin_permission_and_reason(self):
        manager = self.user("manager-no-admin", ROLE_MANAGER)
        executive = self.user("executive-no-admin", ROLE_EXECUTIVE)
        other_manager = self.user("manager-target", ROLE_MANAGER)
        executive_entity = self.entity_for(executive, "EJ-NO-ADMIN", ROLE_EXECUTIVE)
        manager_entity = self.entity_for(manager, "J-CURRENT", ROLE_MANAGER)
        other_manager_entity = self.entity_for(
            other_manager, "J-TARGET", ROLE_MANAGER
        )
        self.repository.assign_hierarchy(
            executive_entity,
            manager_entity,
            REL_EXECUTIVE_MANAGER,
            "2026-01-01",
        )
        service = HierarchyService(self.repository, self.authorization)

        with self.assertRaises(AccessDeniedError):
            service.reassign(
                manager,
                executive_entity,
                other_manager_entity,
                REL_EXECUTIVE_MANAGER,
                "2026-07-01",
                "Intento sin permiso",
            )

        admin = self.user("admin-empty-reason", ROLE_ADMIN)
        with self.assertRaisesRegex(ValueError, "motivo"):
            service.reassign(
                admin,
                executive_entity,
                other_manager_entity,
                REL_EXECUTIVE_MANAGER,
                "2026-07-01",
                "",
            )


if __name__ == "__main__":
    unittest.main()
