import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import input_store


class InputStoreTest(unittest.TestCase):
    def test_upload_history_is_preserved_beyond_display_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_data = root / "app_data"
            input_data = root / "input_data"
            state_db = app_data / "review_state.db"

            with (
                patch.object(input_store, "APP_DATA_DIR", app_data),
                patch.object(input_store, "INPUT_DATA_DIR", input_data),
                patch.object(input_store, "STATE_DB_PATH", state_db),
            ):
                for version in range(input_store.MAX_VERSIONS_PER_INPUT + 2):
                    result = input_store.save_uploaded_input(
                        "periodos_corte",
                        "cortes.csv",
                        (
                            "inicio_corte,fin_corte,periodo_corte\n"
                            f"2026-01-01,2026-01-31,2026-{version + 1:02d}\n"
                        ).encode("utf-8"),
                        uploaded_by="tester",
                    )
                    self.assertEqual(result["pruned"], [])

                visible = input_store.list_input_versions("periodos_corte")
                complete = input_store.list_input_versions("periodos_corte", limit=100)

                self.assertEqual(len(visible), input_store.MAX_VERSIONS_PER_INPUT)
                self.assertEqual(len(complete), input_store.MAX_VERSIONS_PER_INPUT + 2)
                self.assertEqual(sum(int(row["is_active"]) for row in complete), 1)
                self.assertEqual(
                    len(list((input_data / "periodos_corte").glob("*.csv"))),
                    input_store.MAX_VERSIONS_PER_INPUT + 2,
                )


if __name__ == "__main__":
    unittest.main()
