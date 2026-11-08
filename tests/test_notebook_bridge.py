import unittest

import pandas as pd

from src.notebook_bridge import load_periodos_corte_table


class NotebookBridgeTest(unittest.TestCase):
    def test_cut_period_diagnostic_uses_logging_instead_of_print(self):
        scope = {
            "cortes": pd.DataFrame([
                {
                    "inicio_corte": "2026-01-01",
                    "fin_corte": "2026-01-31",
                    "periodo_corte": "2026-01",
                }
            ])
        }

        with self.assertLogs("src.notebook_bridge", level="INFO") as captured:
            result = load_periodos_corte_table(
                scope, prefer_active=False, write_to_scope=False, verbose=True
            )

        self.assertEqual(1, len(result))
        self.assertIn("Periodos de corte resueltos", captured.output[0])


if __name__ == "__main__":
    unittest.main()
