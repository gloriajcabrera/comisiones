import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from commission_app import (
    PROFILE_MENUS,
    _attach_demo_hierarchy,
    _apply_demo_scope,
    _demo_provider,
    _enrich_demo_payments,
    _payment_summary,
    _parameter_data,
    _production_runtime,
    _valid_period,
    _menus_for_profile,
    _clear_session_state,
)
from src.access_control import PERM_NETSUITE_SYNC


class CommissionAppTest(unittest.TestCase):
    def test_productive_runtime_configures_logging_once_through_cache(self):
        runtime = object()
        _production_runtime.clear()
        try:
            with patch("commission_app.configure_logging") as configure:
                with patch("src.runtime.build_runtime", return_value=runtime) as build:
                    self.assertIs(runtime, _production_runtime())
                    self.assertIs(runtime, _production_runtime())

            configure.assert_called_once_with()
            build.assert_called_once_with()
        finally:
            _production_runtime.clear()

    def test_period_validation_is_strict(self):
        self.assertTrue(_valid_period("2026-09"))
        self.assertFalse(_valid_period("2026-9"))
        self.assertFalse(_valid_period("2026-13"))
        self.assertFalse(_valid_period("texto"))

    def test_demo_parameters_match_effective_rule_catalog(self):
        parameters = _parameter_data().set_index("Código")

        self.assertNotIn("UMBRAL_PAGO", parameters.index)
        self.assertEqual("30", parameters.loc["payment_full_days", "Valor"])
        self.assertEqual("60", parameters.loc["payment_half_days", "Valor"])
        self.assertEqual(
            "0.50",
            parameters.loc["product_first_payment_threshold", "Valor"],
        )
        self.assertTrue(parameters["Habilitado"].all())

    def test_logout_clears_identity_results_filters_and_drafts(self):
        state = {
            "application_session": object(),
            "calculation_provider": object(),
            "navigation": "Usuarios",
            "directory_search_results": [{"domain_username": "user"}],
            "directory_user_prefill": {"email": "user@test"},
            "editing_user_id": "NEW",
        }

        _clear_session_state(state)

        self.assertEqual({}, state)

    def test_netsuite_sync_navigation_depends_on_independent_permission(self):
        without_permission = _menus_for_profile("Revisor", set())
        with_permission = _menus_for_profile("Revisor", {PERM_NETSUITE_SYNC})
        self.assertNotIn("Sincronización NetSuite", without_permission["Gestión"])
        self.assertIn("Sincronización NetSuite", with_permission["Gestión"])

        admin = _menus_for_profile("Administrador", {PERM_NETSUITE_SYNC})
        self.assertIn("Sincronización NetSuite", admin["Administración"])

    def setUp(self):
        self.environment = patch.dict(
            "os.environ", {"COMMISSION_APP_DEMO": "true"}
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def _app(self):
        return AppTest.from_file("commission_app.py").run(timeout=10)

    @staticmethod
    def _widget(elements, label):
        return next(element for element in elements if element.label == label)

    def _go_to(self, app, page):
        navigation = self._widget(app.radio, "Navegación")
        if page not in navigation.options:
            profile = self._widget(app.selectbox, "Perfil de prueba").value
            target_menu = next(
                menu for menu, pages in PROFILE_MENUS[profile].items() if page in pages
            )
            menu_widget = next(
                widget for widget in app.radio
                if widget.label in {"Tipo de menú", "Menú del jefe", "Menú del parametrizador", "Menú del administrador"}
            )
            app = menu_widget.set_value(target_menu).run(timeout=10)
            navigation = self._widget(app.radio, "Navegación")
        return navigation.set_value(page).run(timeout=10)

    def test_demo_home_renders_without_file_bundle(self):
        app = self._app()
        self.assertEqual([], list(app.exception))
        self.assertIn("Inicio", [title.value for title in app.title])
        self.assertTrue(any("Comisiones" in item.value for item in app.markdown))
        self.assertGreaterEqual(len(app.dataframe), 1)

    def test_home_totals_change_with_profile_scope(self):
        payments = _demo_provider().commission_results("demo-2026-09")
        expected = {
            "Ejecutivo": (618000, 2),
            "Jefe de ventas": (800000, 5),
            "Gerente": (954000, 7),
            "Revisor": (1050000, 9),
            "Parametrizador": (1050000, 9),
            "Administrador": (1050000, 9),
        }
        for profile, (commission, documents) in expected.items():
            summary = _payment_summary(_apply_demo_scope(payments, profile))
            self.assertEqual(commission, summary["commission"].sum(), profile)
            self.assertEqual(documents, summary["documents"].sum(), profile)

    def test_payment_summary_keeps_line_detail_and_separates_period_discounts(self):
        payments = _attach_demo_hierarchy(
            _enrich_demo_payments(_demo_provider().commission_results("demo-2026-09"))
        )
        discounts = pd.DataFrame({
            "executive": ["EJ-01"], "period": ["2026-08"], "amount": [50000.0]
        })

        summary = _payment_summary(payments, discounts)

        self.assertIn("commission_line", summary.columns)
        discount_row = summary.loc[
            summary["commission_line"].eq("DESCUENTOS DEL PERIODO")
        ].iloc[0]
        self.assertEqual(50000.0, discount_row["discounts"])
        self.assertEqual(-50000.0, discount_row["payable"])
        self.assertEqual(1050000.0, summary["commission"].sum())

    def test_navigation_categories_are_horizontal_and_back_is_available(self):
        app = self._app()
        menu = self._widget(app.radio, "Tipo de menú")
        navigation = self._widget(app.radio, "Navegación")

        self.assertTrue(menu.horizontal)
        self.assertTrue(navigation.horizontal)
        self.assertIn("← Atrás", [button.label for button in app.button])

    def test_floor_and_payment_pages_offer_summary_detail_and_direct_review(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Ejecutivo").run(timeout=10)
        app = self._go_to(app, "Cumplimiento de piso")
        view = self._widget(app.radio, "Vista")
        self.assertEqual(
            {"Resumen", "Detalle de facturas", "Solicitar revisión"},
            set(view.options),
        )
        app = view.set_value("Solicitar revisión").run(timeout=10)
        self.assertIn("Crear solicitud de revisión", [button.label for button in app.button])

        app = self._go_to(app, "Pagos")
        app = self._widget(app.radio, "Vista").set_value("Detalle de facturas").run(timeout=10)
        columns = set(app.dataframe[0].value.columns)
        self.assertTrue(
            {"Nombre del ejecutivo", "Jefe", "Nombre del jefe"}.issubset(columns)
        )

    def test_navigation_renders_floor_payments_and_review(self):
        app = self._app()
        for page in ("Cumplimiento", "Pagos", "Revisiones"):
            app = self._go_to(app, page)
            self.assertEqual([], list(app.exception), page)
            self.assertIn(page, [title.value for title in app.title])
            self.assertGreaterEqual(len(app.dataframe), 1)

    def test_profile_navigation_matches_permissions(self):
        expected = {
            "Ejecutivo": {"Inicio", "Cumplimiento de piso", "Pagos", "Descuentos"},
            "Jefe de ventas": {
                "Inicio", "Cumplimiento de piso", "Pagos", "Descuentos",
            },
            "Gerente": {"Inicio", "Cumplimiento de piso", "Pagos", "Descuentos"},
            "Revisor": {
                "Inicio", "Cumplimiento", "Pagos", "Descuentos",
            },
            "Parametrizador": {
                "Panel de parametrización", "Parámetros", "Aprobaciones",
            },
            "Administrador": {
                "Panel de administración", "Usuarios", "Periodos",
                "Ejecuciones de cálculo", "Auditoría",
            },
        }
        app = self._app()
        for profile, pages in expected.items():
            profile_selector = self._widget(app.selectbox, "Perfil de prueba")
            app = profile_selector.set_value(profile).run(timeout=10)
            self.assertEqual([], list(app.exception), profile)
            navigation = self._widget(app.radio, "Navegación")
            self.assertEqual(pages, set(navigation.options), profile)

    def test_administrator_can_consult_calculation_runs(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value(
            "Administrador"
        ).run(timeout=10)
        app = self._go_to(app, "Ejecuciones de cálculo")

        self.assertEqual([], list(app.exception))
        self.assertIn("Ejecuciones de cálculo", [title.value for title in app.title])
        self.assertIn("Estado", app.dataframe[0].value.columns)
        self.assertIn("Errores", app.dataframe[0].value.columns)

    def test_parameterizer_starts_in_specific_control_panel(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Parametrizador").run(timeout=10)

        self.assertEqual([], list(app.exception))
        self.assertIn("Panel de parametrización", [title.value for title in app.title])
        self.assertIn("Menú del parametrizador", [widget.label for widget in app.radio])
        self.assertEqual(
            {"Panel de parametrización", "Parámetros", "Aprobaciones"},
            set(self._widget(app.radio, "Navegación").options),
        )
        self.assertIn("Gestionar parámetros", [button.label for button in app.button])

    def test_parameterizer_has_separate_consultation_menu(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Parametrizador").run(timeout=10)
        menu = self._widget(app.radio, "Menú del parametrizador")
        app = menu.set_value("Consultas").run(timeout=10)

        self.assertEqual([], list(app.exception))
        self.assertEqual(
            {"Inicio", "Cumplimiento de piso", "Pagos", "Descuentos", "Revisiones"},
            set(self._widget(app.radio, "Navegación").options),
        )

    def test_home_discount_action_navigates_without_state_error(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Ejecutivo").run(timeout=10)
        button = next(item for item in app.button if item.label == "Ver detalle de descuentos")

        app = button.click().run(timeout=10)

        self.assertEqual([], list(app.exception))
        self.assertIn("Descuentos", [title.value for title in app.title])

    def test_executive_never_receives_manager_or_director_commission_columns(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value(
            "Ejecutivo"
        ).run(timeout=10)

        columns = {
            str(column)
            for table in app.dataframe
            for column in table.value.columns
        }
        self.assertNotIn("Comisión jefe (moneda local)", columns)
        self.assertNotIn("Comisión gerente (moneda local)", columns)

    def test_manager_receives_manager_but_not_director_commission(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value(
            "Jefe de ventas"
        ).run(timeout=10)

        columns = {
            str(column)
            for table in app.dataframe
            for column in table.value.columns
        }
        self.assertIn("Comisión jefe (moneda local)", columns)
        self.assertNotIn("Comisión gerente (moneda local)", columns)

    def test_administrator_period_management_requires_explicit_save_and_reason(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Administrador").run(timeout=10)
        app = self._go_to(app, "Periodos")
        view = self._widget(app.radio, "Vista de trabajo")
        app = view.set_value("Gestionar periodo").run(timeout=10)

        self.assertEqual([], list(app.exception))
        self.assertIn("Motivo obligatorio", [item.label for item in app.text_area])
        self.assertIn("Guardar cambio", [item.label for item in app.button])
        save = next(item for item in app.button if item.label == "Guardar cambio")
        app = save.click().run(timeout=10)
        self.assertTrue(any("motivo" in item.value.lower() for item in app.warning))

    def test_reviewer_payment_file_actions_are_separate_and_explicit(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Revisor").run(timeout=10)
        menu = self._widget(app.radio, "Tipo de menú")
        app = menu.set_value("Gestión").run(timeout=10)
        app = self._go_to(app, "Cierre y archivo")
        view = self._widget(app.radio, "Vista de trabajo")

        self.assertEqual(
            {"Consultar versiones", "Finalizar periodo", "Descargar", "Enviar"},
            set(view.options),
        )
        app = view.set_value("Finalizar periodo").run(timeout=10)
        self.assertIn("Finalizar y crear versión", [item.label for item in app.button])
        app = self._widget(app.radio, "Vista de trabajo").set_value("Descargar").run(timeout=10)
        self.assertIn("Generar y descargar Excel", [item.label for item in app.button])

    def test_parameterizer_quick_actions_navigate_without_state_error(self):
        destinations = {
            "Gestionar parámetros": "Parámetros",
            "Revisar aprobaciones": "Aprobaciones",
            "Revisar excepciones": "Excepciones",
        }
        for button, destination in destinations.items():
            app = self._app()
            app = self._widget(app.selectbox, "Perfil de prueba").set_value("Parametrizador").run(timeout=10)
            app = self._widget(app.button, button).click().run(timeout=10)
            self.assertEqual([], list(app.exception), button)
            self.assertIn(destination, [title.value for title in app.title], button)

    def test_parameter_edit_is_explicit(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Parametrizador").run(timeout=10)
        app = self._widget(app.button, "Gestionar parámetros").click().run(timeout=10)
        self.assertIn("Editar parámetro", [button.label for button in app.button])
        self.assertNotIn("Nuevo valor", [widget.label for widget in app.text_input])

        app = self._widget(app.button, "Editar parámetro").click().run(timeout=10)
        self.assertEqual([], list(app.exception))
        self.assertIn("Nuevo valor", [widget.label for widget in app.text_input])
        self.assertIn("Justificación del cambio", [widget.label for widget in app.text_area])
        self.assertIn("Guardar cambios", [button.label for button in app.button])

    def test_reviewer_can_register_or_upload_inputs(self):
        app = self._app()
        app = self._go_to(app, "Insumos y cortes")
        work_view = self._widget(app.radio, "Vista de trabajo")
        self.assertEqual(
            {"Consultar versiones", "Registro manual", "Cargar archivo"},
            set(work_view.options),
        )

        app = work_view.set_value("Registro manual").run(timeout=10)
        self.assertEqual([], list(app.exception))
        self.assertIn("Observación y motivo", [widget.label for widget in app.text_area])
        self.assertIn("Guardar y enviar a aprobación", [button.label for button in app.button])

        work_view = self._widget(app.radio, "Vista de trabajo")
        app = work_view.set_value("Cargar archivo").run(timeout=10)
        self.assertEqual([], list(app.exception))
        self.assertIn("Observación y motivo de la carga", [widget.label for widget in app.text_area])
        self.assertIn("Validar y cargar", [button.label for button in app.button])

    def test_executive_has_complete_floor_and_payment_tables(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Ejecutivo").run(timeout=10)
        app = self._go_to(app, "Cumplimiento de piso")
        app = self._widget(app.radio, "Vista").set_value("Detalle de facturas").run(timeout=10)
        floor_columns = set(app.dataframe[0].value.columns)
        self.assertTrue(
            {"Documento", "Fecha documento", "Documento origen", "Fecha documento origen",
             "Nota crédito", "Fecha nota crédito", "Documento reemplazo",
             "Fecha documento reemplazo", "Suma al piso", "Motivo suma/no suma",
             "Solicitud", "Estado de solicitud", "Concepto final"}.issubset(floor_columns)
        )

        app = self._go_to(app, "Descuentos")
        discount_columns = set(app.dataframe[0].value.columns)
        self.assertTrue(
            {"Descuento total (moneda local)", "Valor aplicado (moneda local)",
             "Saldo pendiente (moneda local)", "Facturas anuladas",
             "Notas crédito", "Estado"}.issubset(discount_columns)
        )

        app = self._go_to(app, "Pagos")
        app = self._widget(app.radio, "Vista").set_value("Detalle de facturas").run(timeout=10)
        payment_columns = set(app.dataframe[0].value.columns)
        self.assertTrue(
            {"Fecha documento", "Fecha documento origen", "Fecha nota crédito",
             "Fecha documento reemplazo", "Resultado", "Motivo", "Documento de pago",
             "Tipo de pago", "Valor pagado (moneda local)", "Fecha real de pago",
             "Fecha contabilización", "Días de pago", "Factor", "% comisión",
             "Solicitud", "Estado de solicitud", "Concepto final"}.issubset(payment_columns)
        )

    def test_floor_documents_do_not_require_a_payment(self):
        documents = _demo_provider().document_compliance_results("demo-2026-09")
        unpaid = documents.loc[documents["document"].eq("SM73590")].iloc[0]

        self.assertEqual("No suma: reemplazo del documento original", unpaid["floor_reason"])
        self.assertFalse(unpaid["adds_to_floor"])

    def test_unpaid_payment_fields_are_blank_and_explicit_zero_is_preserved(self):
        payments = _enrich_demo_payments(
            _demo_provider().commission_results("demo-2026-09")
        )
        unpaid = payments.loc[payments["document"].eq("SM73590")].iloc[0]
        explicit_zero = payments.loc[payments["document"].eq("SM71632")].iloc[0]

        for column in (
            "payment_document", "payment_type", "paid_value", "payment_date",
            "accounting_date", "payment_days", "payment_factor",
            "commission_rate", "commission_executive",
        ):
            self.assertTrue(pd.isna(unpaid[column]), column)
        self.assertEqual(0.0, explicit_zero["payment_factor"])
        self.assertEqual(0.0, explicit_zero["commission_rate"])
        self.assertEqual(0.0, explicit_zero["commission_executive"])

    def test_manager_uses_executive_filter_and_explicit_team_edit(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Jefe de ventas").run(timeout=10)
        self.assertNotIn("Mis resultados", self._widget(app.radio, "Navegación").options)
        self.assertNotIn("Mi equipo", self._widget(app.radio, "Navegación").options)

        app = self._go_to(app, "Cumplimiento de piso")
        self.assertIn("Ejecutivo", [widget.label for widget in app.multiselect])
        app = self._go_to(app, "Ejecutivos a cargo")
        self.assertIn("Editar asociación", [button.label for button in app.button])
        self.assertIn("Registrar nuevo ejecutivo", [button.label for button in app.button])
        self.assertNotIn("Guardar asociación", [button.label for button in app.button])

        app = self._widget(app.button, "Editar asociación").click().run(timeout=10)
        self.assertEqual([], list(app.exception))
        self.assertIn("Guardar asociación", [button.label for button in app.button])
        app = self._widget(app.selectbox, "Estado").set_value("INACTIVO").run(timeout=10)
        self.assertIn(
            "Fecha efectiva de inactivación",
            [widget.label for widget in app.date_input],
        )

    def test_manager_can_manually_register_executive_and_floor(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Jefe de ventas").run(timeout=10)
        app = self._go_to(app, "Ejecutivos a cargo")
        app = self._widget(app.button, "Registrar nuevo ejecutivo").click().run(timeout=10)
        self.assertEqual([], list(app.exception))
        self.assertIn("Código del ejecutivo", [widget.label for widget in app.text_input])
        self.assertIn("Jefe asignado", [widget.label for widget in app.text_input])
        self.assertIn("Guardar asociación", [button.label for button in app.button])

        app = self._go_to(app, "Pisos y metas")
        self.assertIn("Registrar piso y meta", [button.label for button in app.button])
        app = self._widget(app.button, "Registrar piso y meta").click().run(timeout=10)
        self.assertEqual([], list(app.exception))
        self.assertIn("Jefe asociado", [widget.label for widget in app.text_input])
        self.assertIn("Periodo", [widget.label for widget in app.text_input])
        self.assertIn("Línea", [widget.label for widget in app.selectbox])
        self.assertIn("Guardar piso y meta", [button.label for button in app.button])

    def test_every_profile_has_separate_menu_categories(self):
        app = self._app()
        for profile, menus in PROFILE_MENUS.items():
            app = self._widget(app.selectbox, "Perfil de prueba").set_value(profile).run(timeout=10)
            menu_widget = next(
                widget for widget in app.radio
                if widget.label in {"Tipo de menú", "Menú del jefe", "Menú del parametrizador", "Menú del administrador"}
            )
            self.assertEqual(set(menus), set(menu_widget.options), profile)

    def test_summary_cards_only_render_on_home(self):
        app = self._app()
        self.assertTrue(any('<section class="metric-grid"' in item.value for item in app.markdown))
        app = self._go_to(app, "Pagos")
        self.assertFalse(any('<section class="metric-grid"' in item.value for item in app.markdown))

    def test_administrator_can_explicitly_edit_and_disable_manager(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Administrador").run(timeout=10)
        app = self._widget(app.button, "Administrar usuarios").click().run(timeout=10)
        self.assertIn("Editar usuario", [button.label for button in app.button])
        self.assertNotIn("Guardar usuario", [button.label for button in app.button])

        app = self._widget(app.button, "Editar usuario").click().run(timeout=10)
        self.assertEqual([], list(app.exception))
        self.assertIn("Estado", [widget.label for widget in app.selectbox])
        self.assertIn("Observación obligatoria", [widget.label for widget in app.text_area])
        self.assertIn("Guardar usuario", [button.label for button in app.button])
        app = self._widget(app.selectbox, "Estado").set_value("INACTIVO").run(timeout=10)
        self.assertIn(
            "Fecha efectiva de inactivación",
            [widget.label for widget in app.date_input],
        )

    def test_no_ambiguous_manage_view_remains(self):
        app = self._app()
        for radio in app.radio:
            self.assertNotIn("Gestionar", radio.options)

    def test_administrator_starts_in_own_panel_and_quick_actions_work(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Administrador").run(timeout=10)
        self.assertEqual([], list(app.exception))
        self.assertIn("Panel de administración", [title.value for title in app.title])
        self.assertIn("Menú del administrador", [widget.label for widget in app.radio])

        destinations = {
            "Administrar usuarios": "Usuarios",
            "Gestionar periodos": "Periodos",
            "Consultar auditoría": "Auditoría",
            "Gestionar parámetros": "Parámetros",
            "Revisar aprobaciones": "Aprobaciones",
            "Cerrar periodo o generar archivo": "Cierre y archivo",
        }
        for button, destination in destinations.items():
            app = self._app()
            app = self._widget(app.selectbox, "Perfil de prueba").set_value("Administrador").run(timeout=10)
            app = self._widget(app.button, button).click().run(timeout=10)
            self.assertEqual([], list(app.exception), button)
            self.assertIn(destination, [title.value for title in app.title], button)

    def test_administrator_audit_page_is_a_real_table_view(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value(
            "Administrador"
        ).run(timeout=10)
        app = self._go_to(app, "Auditoría")

        self.assertEqual([], list(app.exception))
        self.assertTrue(any("historial inmutable" in item.value for item in app.caption))
        self.assertTrue(app.dataframe)

    def test_review_view_supports_selection_action_and_observation(self):
        app = self._app()
        app = self._go_to(app, "Revisiones")
        work_view = self._widget(app.radio, "Vista de trabajo")
        app = work_view.set_value("Revisar").run(timeout=10)

        self.assertEqual([], list(app.exception))
        self.assertIn("Acción", [widget.label for widget in app.selectbox])
        self.assertIn("Observación o concepto", [widget.label for widget in app.text_area])
        self.assertIn("Aplicar a seleccionados", [button.label for button in app.button])

    def test_exception_view_has_auditable_reason(self):
        app = self._app()
        app = self._go_to(app, "Excepciones")
        work_view = self._widget(app.radio, "Vista de trabajo")
        app = work_view.set_value("Proponer excepción").run(timeout=10)

        self.assertEqual([], list(app.exception))
        self.assertIn("Observación obligatoria y motivo del cambio", [widget.label for widget in app.text_area])
        self.assertIn("Guardar y enviar al Parametrizador", [button.label for button in app.button])

    def test_executive_creates_requests_but_cannot_review_them(self):
        app = self._app()
        app = self._widget(app.selectbox, "Perfil de prueba").set_value("Ejecutivo").run(timeout=10)
        app = self._go_to(app, "Solicitudes")
        work_view = self._widget(app.radio, "Vista de trabajo")
        self.assertEqual({"Consultar", "Crear solicitud"}, set(work_view.options))

        app = work_view.set_value("Crear solicitud").run(timeout=10)
        self.assertEqual([], list(app.exception))
        self.assertIn("Motivo general de la solicitud", [widget.label for widget in app.text_area])
        self.assertIn("Crear solicitud de revisión", [button.label for button in app.button])
        self.assertNotIn("Acción", [widget.label for widget in app.selectbox])

    def test_streamlit_theme_is_forced_to_light(self):
        config = Path(".streamlit/config.toml").read_text(encoding="utf-8")
        source = Path("commission_app.py").read_text(encoding="utf-8")
        self.assertIn('base = "light"', config)
        self.assertIn('secondaryBackgroundColor = "#FFFFFF"', config)
        self.assertIn("color-scheme:light !important", source)
        self.assertIn('[data-testid="stRadio"] label p', source)
        self.assertNotIn('[data-testid="stDataFrame"] *', source)
        self.assertNotIn('[data-testid="stDataEditor"] *', source)


if __name__ == "__main__":
    unittest.main()
