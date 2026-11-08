import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PRODUCTION_ENTRYPOINTS = (
    ROOT / "commission_app.py",
    SRC / "runtime.py",
    SRC / "commands.py",
    SRC / "calculation_service.py",
)
PRODUCTION_CODE = (*tuple(SRC.glob("*.py")), ROOT / "commission_app.py")


class ArchitectureBoundariesTest(unittest.TestCase):
    def test_production_dependencies_exclude_notebook_and_parquet_runtimes(self):
        dependencies = self._requirements("requirements.txt")
        forbidden = {"jupyter", "notebook", "pyarrow", "fastparquet", "xlsxwriter"}
        self.assertTrue(forbidden.isdisjoint(dependencies))
        self.assertIn("openpyxl", dependencies)
        self.assertIn("psycopg", dependencies)

    def test_odbc_and_legacy_dependencies_are_isolated(self):
        production = self._requirements("requirements.txt")
        odbc = self._requirements("requirements-odbc.txt")
        legacy = self._requirements("requirements-review-app.txt")
        self.assertNotIn("pyodbc", production)
        self.assertIn("pyodbc", odbc)
        self.assertNotIn("pyarrow", production)
        self.assertIn("pyarrow", legacy)

    def test_excel_io_is_limited_to_functional_input_and_final_output(self):
        violations = []
        for path in SRC.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = self._call_name(node.func)
                if name.endswith("read_excel") and path.name != "input_store.py":
                    violations.append(f"{path.name}:{node.lineno}:{name}")
                if name.endswith("to_excel") or name.endswith("ExcelWriter"):
                    violations.append(f"{path.name}:{node.lineno}:{name}")
        self.assertEqual([], violations)

    def test_excel_library_is_only_used_by_approved_boundary_modules(self):
        violations = []
        approved = {"input_store.py", "payment_files.py"}
        for path in SRC.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                if any(module.split(".")[0] in {"openpyxl", "xlsxwriter"} for module in modules):
                    if path.name not in approved:
                        violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual([], violations)

    def test_productive_entrypoints_do_not_import_legacy_bridges(self):
        forbidden = {"src.notebook_bridge", "local_review_app", "review_bundle"}
        violations = []
        for path in PRODUCTION_ENTRYPOINTS:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    if any(
                        module == name or module.startswith(f"{name}.")
                        for name in forbidden
                    ):
                        violations.append(f"{path.name}:{node.lineno}:{module}")
        self.assertEqual([], violations)

    def test_productive_entrypoints_do_not_read_intermediate_files(self):
        forbidden_calls = {
            "read_excel", "read_csv", "read_parquet", "read_feather",
            "read_pickle",
        }
        violations = []
        for path in PRODUCTION_ENTRYPOINTS:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = self._call_name(node.func).rsplit(".", 1)[-1]
                if name in forbidden_calls:
                    violations.append(f"{path.name}:{node.lineno}:{name}")
        self.assertEqual([], violations)

    def test_production_queries_do_not_use_select_all(self):
        violations = []
        for path in SRC.glob("*.py"):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "SELECT *" in line.upper():
                    violations.append(f"{path.name}:{line_number}")
        self.assertEqual([], violations)

    def test_production_runtime_has_no_destructive_financial_sql(self):
        forbidden = ("DELETE FROM", "DROP TABLE", "TRUNCATE TABLE")
        violations = []
        for path in SRC.glob("*.py"):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                normalized = " ".join(line.upper().split())
                for statement in forbidden:
                    if statement in normalized:
                        violations.append(
                            f"{path.name}:{line_number}:{statement}"
                        )
        self.assertEqual([], violations)

    def test_production_code_has_no_hardcoded_windows_paths(self):
        violations = []
        windows_path = re.compile(r"^[A-Za-z]:\\")
        for path in PRODUCTION_CODE:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and windows_path.match(node.value)
                ):
                    violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual([], violations)

    def test_production_code_has_no_literal_secrets(self):
        sensitive = ("password", "passwd", "token", "api_key", "secret")
        violations = []
        for path in PRODUCTION_CODE:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    value = node.value
                    for target in targets:
                        name = self._assignment_name(target).lower()
                        if (
                            any(part in name for part in sensitive)
                            and isinstance(value, ast.Constant)
                            and isinstance(value.value, str)
                            and value.value.strip()
                        ):
                            violations.append(f"{path.name}:{node.lineno}:{name}")
        self.assertEqual([], violations)

    def test_production_code_has_no_windows_only_imports(self):
        forbidden = {"win32api", "win32com", "win32security", "pythoncom", "pywintypes"}
        violations = []
        for path in PRODUCTION_CODE:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    if module.split(".", 1)[0] in forbidden:
                        violations.append(f"{path.name}:{node.lineno}:{module}")
        self.assertEqual([], violations)

    def test_linux_service_template_enforces_productive_runtime_boundaries(self):
        service = (ROOT / "deploy/systemd/comisiones.service.example").read_text(
            encoding="utf-8"
        )
        environment = (ROOT / "deploy/comisiones.env.example").read_text(
            encoding="utf-8"
        )

        self.assertIn("ExecStart=", service)
        self.assertIn("ExecStartPre=", service)
        self.assertIn("python -m src.commands production-check", service)
        self.assertIn("streamlit run commission_app.py", service)
        self.assertIn("--server.address=${STREAMLIT_SERVER_ADDRESS}", service)
        self.assertIn("--server.port=${STREAMLIT_SERVER_PORT}", service)
        self.assertIn("--theme.base=light", service)
        self.assertIn("--browser.gatherUsageStats=false", service)
        self.assertIn("EnvironmentFile=/etc/servimeters/comisiones.env", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("UMask=0027", service)
        self.assertIn("StateDirectory=servimeters-comisiones", service)
        self.assertIn("COMMISSIONS_ENV=production", environment)
        self.assertIn("COMMISSIONS_DB_BACKEND=postgresql", environment)
        self.assertIn("COMMISSIONS_AUTH_MODE=ldap", environment)
        self.assertIn("LOCAL_TEST_AUTH_ENABLED=false", environment)
        self.assertIn("COMMISSION_APP_DEMO=false", environment)
        self.assertIn("STREAMLIT_SERVER_ADDRESS=0.0.0.0", environment)
        self.assertIn("STREAMLIT_SERVER_PORT=8501", environment)

    def test_productive_entrypoint_is_not_ignored_by_git(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("!commission_app.py", ignore.splitlines())

    def test_postgresql_backup_is_private_validated_and_non_destructive(self):
        backup = (ROOT / "deploy/postgresql/backup.sh").read_text(encoding="utf-8")
        verify = (ROOT / "deploy/postgresql/verify-backup.sh").read_text(
            encoding="utf-8"
        )
        environment = (ROOT / "deploy/comisiones.env.example").read_text(
            encoding="utf-8"
        )

        self.assertIn("umask 077", backup)
        self.assertIn("pg_dump", backup)
        self.assertIn("--format=custom", backup)
        self.assertIn("pg_restore --list", backup)
        self.assertIn("PGPASSFILE", backup)
        self.assertIn("pg_restore --list", verify)
        self.assertIn("COMMISSIONS_POSTGRES_BACKUP_DSN=postgresql://", environment)
        self.assertIn("PGPASSFILE=/etc/servimeters/comisiones.pgpass", environment)

        combined = f"{backup}\n{verify}".upper()
        for forbidden in ("DROP DATABASE", "DROPDB", "PG_RESTORE --CLEAN"):
            self.assertNotIn(forbidden, combined)

    def test_postgresql_views_use_supported_idempotent_syntax(self):
        violations = []
        for path in PRODUCTION_CODE:
            if path.name == "database.py":
                continue
            source = path.read_text(encoding="utf-8").upper()
            if "CREATE VIEW IF NOT EXISTS" in source:
                violations.append(path.name)
        self.assertEqual([], violations)

    @staticmethod
    def _call_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = ArchitectureBoundariesTest._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    @staticmethod
    def _assignment_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    @staticmethod
    def _requirements(filename: str) -> set[str]:
        dependencies = set()
        for raw_line in (ROOT / filename).read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("#", "-r ")):
                continue
            name = line.split(";", 1)[0]
            for separator in ("[", "<", ">", "=", "!", "~"):
                name = name.split(separator, 1)[0]
            dependencies.add(name.strip().lower())
        return dependencies
