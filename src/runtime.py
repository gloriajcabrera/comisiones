"""Punto de composicion del runtime sin dependencias de Jupyter ni Excel."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .access_control import (
    AuthorizationService,
    HierarchyService,
    PostgresAccessRepository,
    SqliteAccessRepository,
    TeamChangeRequestService,
    UserAdministrationService,
)
from .business_periods import (
    BusinessPeriodService,
    PostgresBusinessPeriodRepository,
    SqliteBusinessPeriodRepository,
)
from .authentication import (
    ApplicationSessionService,
    AuthenticationProvider,
    AuthenticationService,
    DirectoryLookupService,
    directory_search_provider_from_environment,
    authentication_provider_from_environment,
)
from .audit_queries import AuditQueryService
from .calculation_service import (
    CommissionCalculationService,
    PostgresCalculationRepository,
    SqliteCalculationRepository,
)
from .cancellation_discounts import (
    PostgresCancellationDiscountRepository,
    SqliteCancellationDiscountRepository,
)
from .cut_periods_workflow import (
    CutPeriodWorkflowService,
    PostgresCutPeriodRepository,
    SqliteCutPeriodRepository,
)
from .database import Database, DatabaseSettings, POSTGRESQL, SQLITE
from .netsuite_provider import (
    NetSuiteDataProvider,
    netsuite_provider_from_environment,
)
from .netsuite_sync import (
    NetSuiteSyncService,
    PostgresNetSuiteSyncRepository,
    SqliteNetSuiteSyncRepository,
)
from .mail_providers import mail_provider_from_environment
from .payment_files import (
    PaymentFileService,
    PostgresPaymentFileRepository,
    SqlitePaymentFileRepository,
)
from .review_workflows import (
    PostgresReviewRepository,
    ReviewWorkflowService,
    SqliteReviewRepository,
)
from .reporting import CalculationViewService
from .rule_parameters import (
    PostgresRuleParameterRepository,
    RuleParameterService,
    SqliteRuleParameterRepository,
)
from .targets_workflow import PostgresTargetsRepository, SqliteTargetsRepository
from .targets_workflow import TargetsWorkflowService


DEVELOPMENT = "development"
TEST = "test"
PRODUCTION = "production"


@dataclass(frozen=True)
class RuntimeSettings:
    environment: str
    database: DatabaseSettings

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        environment = os.getenv("COMMISSIONS_ENV", DEVELOPMENT).strip().lower()
        if environment not in {DEVELOPMENT, TEST, PRODUCTION}:
            raise ValueError(f"Ambiente no soportado: {environment}")
        settings = cls(environment, DatabaseSettings.from_environment())
        settings.validate()
        return settings

    def validate(self) -> "RuntimeSettings":
        if self.environment == PRODUCTION:
            if self.database.backend != POSTGRESQL:
                raise RuntimeError("Produccion requiere PostgreSQL")
            if os.getenv("LOCAL_TEST_AUTH_ENABLED", "false").strip().lower() == "true":
                raise RuntimeError(
                    "La autenticacion local de pruebas debe estar deshabilitada "
                    "en produccion"
                )
        return self


@dataclass(frozen=True)
class ApplicationRuntime:
    settings: RuntimeSettings
    access_repository: object
    authorization: AuthorizationService
    user_administration: UserAdministrationService
    hierarchy: HierarchyService
    team_change_requests: TeamChangeRequestService
    sessions: ApplicationSessionService
    business_periods: object
    business_period_workflows: BusinessPeriodService
    calculations: object
    discounts: object
    cut_periods: object
    cut_period_workflows: CutPeriodWorkflowService
    netsuite_sync_repository: object
    payment_files: object
    reviews: object
    review_workflows: ReviewWorkflowService
    rule_parameters: object
    rule_parameter_workflows: RuleParameterService
    targets: object
    target_workflows: TargetsWorkflowService
    sync_service: NetSuiteSyncService
    calculation_service: CommissionCalculationService
    calculation_views: CalculationViewService
    audit_query_service: AuditQueryService
    directory_lookup: DirectoryLookupService | None

    def payment_file_service(self, summary_provider: object) -> PaymentFileService:
        return PaymentFileService(
            self.payment_files,
            self.authorization,
            summary_provider,
            mail_provider=mail_provider_from_environment(),
            calculation_repository=self.calculations,
            period_repository=self.business_periods,
        )


def build_runtime(
    settings: RuntimeSettings | None = None,
    netsuite_provider: NetSuiteDataProvider | None = None,
    authentication_provider: AuthenticationProvider | None = None,
) -> ApplicationRuntime:
    configured = (settings or RuntimeSettings.from_environment()).validate()
    source = netsuite_provider or netsuite_provider_from_environment()

    if configured.database.backend == SQLITE:
        path = configured.database.sqlite_path
        if path is None:
            raise ValueError("SQLite requiere una ruta configurada")
        path = Path(path)
        access = SqliteAccessRepository(path)
        periods = SqliteBusinessPeriodRepository(path)
        calculations = SqliteCalculationRepository(path)
        discounts = SqliteCancellationDiscountRepository(path)
        cuts = SqliteCutPeriodRepository(path)
        sync_repository = SqliteNetSuiteSyncRepository(path)
        payment_files = SqlitePaymentFileRepository(path)
        reviews = SqliteReviewRepository(path)
        parameters = SqliteRuleParameterRepository(path)
        targets = SqliteTargetsRepository(path)
    else:
        dsn = configured.database.postgres_dsn
        if not dsn:
            raise ValueError("PostgreSQL requiere DSN configurado")
        access = PostgresAccessRepository(dsn)
        periods = PostgresBusinessPeriodRepository(dsn)
        calculations = PostgresCalculationRepository(dsn)
        discounts = PostgresCancellationDiscountRepository(dsn)
        cuts = PostgresCutPeriodRepository(dsn)
        sync_repository = PostgresNetSuiteSyncRepository(dsn)
        payment_files = PostgresPaymentFileRepository(dsn)
        reviews = PostgresReviewRepository(dsn)
        parameters = PostgresRuleParameterRepository(dsn)
        targets = PostgresTargetsRepository(dsn)

    authorization = AuthorizationService(access)
    user_administration = UserAdministrationService(access, authorization)
    hierarchy = HierarchyService(access, authorization)
    team_change_requests = TeamChangeRequestService(access, authorization)
    business_period_workflows = BusinessPeriodService(periods, authorization)
    authentication = AuthenticationService(
        authentication_provider or authentication_provider_from_environment(),
        access,
    )
    sessions = ApplicationSessionService(authentication, access)
    operational_database = (
        Database.sqlite(configured.database.sqlite_path)
        if configured.database.backend == SQLITE
        else Database.postgresql(configured.database.postgres_dsn or "")
    )
    audit_query_service = AuditQueryService(
        operational_database, authorization
    )
    directory_provider = directory_search_provider_from_environment()
    directory_lookup = (
        DirectoryLookupService(directory_provider, authorization)
        if directory_provider is not None else None
    )
    sync_service = NetSuiteSyncService(source, sync_repository, authorization)
    calculation_service = CommissionCalculationService(
        sync_repository,
        calculations,
        rules_version=os.getenv(
            "COMMISSIONS_RULES_VERSION", "engine-2026-11-controlled-workflow-v1"
        ).strip(),
        period_repository=periods,
        discount_repository=discounts,
        exception_repository=reviews,
        targets_provider=targets,
        cuts_provider=cuts,
        parameter_provider=parameters,
        authorization=authorization,
    )
    calculation_views = CalculationViewService(
        calculation_service, reviews, authorization
    )
    from .reporting import CalculationReviewDocumentValidator

    review_workflows = ReviewWorkflowService(
        reviews,
        authorization,
        CalculationReviewDocumentValidator(calculation_views),
    )
    rule_parameter_workflows = RuleParameterService(parameters, authorization)
    target_workflows = TargetsWorkflowService(targets, authorization)
    cut_period_workflows = CutPeriodWorkflowService(cuts, authorization)
    return ApplicationRuntime(
        configured,
        access,
        authorization,
        user_administration,
        hierarchy,
        team_change_requests,
        sessions,
        periods,
        business_period_workflows,
        calculations,
        discounts,
        cuts,
        cut_period_workflows,
        sync_repository,
        payment_files,
        reviews,
        review_workflows,
        parameters,
        rule_parameter_workflows,
        targets,
        target_workflows,
        sync_service,
        calculation_service,
        calculation_views,
        audit_query_service,
        directory_lookup,
    )
