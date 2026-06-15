import importlib.util
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def load_engine():
    mootdx = types.ModuleType("mootdx")
    quotes = types.ModuleType("mootdx.quotes")

    class Quotes:
        @staticmethod
        def factory(market="std"):
            raise AssertionError("tests should not open a live quote client")

    quotes.Quotes = Quotes
    sys.modules["mootdx"] = mootdx
    sys.modules["mootdx.quotes"] = quotes

    spec = importlib.util.spec_from_file_location("v4_engine", ROOT / "v4_engine.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class EngineCoreTest(unittest.TestCase):
    def test_volume_factor_accepts_injected_datetime(self):
        engine = load_engine()

        self.assertEqual(engine.get_volume_factor(datetime(2026, 6, 13, 10, 0)), 1.0)
        self.assertEqual(engine.get_volume_factor(datetime(2026, 6, 15, 8, 59)), 1.0)
        self.assertEqual(engine.get_volume_factor(datetime(2026, 6, 15, 11, 45)), 2.0)
        self.assertAlmostEqual(engine.get_volume_factor(datetime(2026, 6, 15, 14, 30)), 240 / 210)

    def test_normalize_kline_frame_sorts_datetime_and_resets_index(self):
        engine = load_engine()
        raw = pd.DataFrame(
            {
                "datetime": ["2026-06-03", "2026-06-01", "2026-06-02"],
                "close": [12.0, 10.0, 11.0],
                "open": [11.0, 9.5, 10.5],
                "amount": [120, 100, 110],
            }
        ).set_index("datetime")
        raw.index.name = "datetime"

        normalized = engine.normalize_kline_frame(raw)

        self.assertEqual(list(normalized["close"]), [10.0, 11.0, 12.0])
        self.assertIsNone(normalized.index.name)
        self.assertEqual(list(normalized.index), [0, 1, 2])
        self.assertIn("pct_change", normalized.columns)

    def test_momentum_fields_use_latest_close(self):
        engine = load_engine()
        df_k = pd.DataFrame(
            {
                "close": [10.0, 11.0, 12.0, 13.0, 15.0, 20.0],
                "open": [9.5, 10.5, 11.5, 12.5, 14.5, 19.5],
                "amount": [100, 110, 120, 130, 140, 150],
            }
        )

        self.assertEqual(engine.calculate_momentum_fields(df_k), {"3日涨幅(%)": 66.67, "5日涨幅(%)": 100.0})

    def test_write_result_csv_keeps_schema_for_empty_rows(self):
        engine = load_engine()

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trend.csv"
            engine.write_result_csv(path, [], engine.TREND_COLUMNS, sort_by="增量倍数")

            df = pd.read_csv(path)

        self.assertEqual(list(df.columns), engine.TREND_COLUMNS)
        self.assertTrue(df.empty)


if __name__ == "__main__":
    unittest.main()
