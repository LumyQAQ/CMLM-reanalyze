import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def load_auto_job():
    spec = importlib.util.spec_from_file_location("cmlm_auto_job", ROOT / "scripts" / "cmlm_auto_job.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sample_frames():
    trend = pd.DataFrame(
        [
            {
                "板块": "半导体",
                "代码": "688256",
                "名称": "寒武纪",
                "今日成交额(亿)": 146.09,
                "增量倍数": 1.71,
                "涨跌幅(%)": 7.42,
                "3日涨幅(%)": 11.23,
                "5日涨幅(%)": 18.45,
                "逻辑标签": "强势放量突破",
            },
            {
                "板块": "元件",
                "代码": "002384",
                "名称": "东山精密",
                "今日成交额(亿)": 156.19,
                "增量倍数": 1.74,
                "涨跌幅(%)": 9.84,
                "3日涨幅(%)": 15.2,
                "5日涨幅(%)": 21.8,
                "逻辑标签": "强势放量突破",
            },
        ]
    )
    range_df = pd.DataFrame(
        [
            {
                "板块": "半导体",
                "代码": "688001",
                "名称": "华兴源创",
                "今日成交额(亿)": 18.0,
                "增量倍数": 2.3,
                "涨跌幅(%)": 6.5,
                "3日涨幅(%)": 9.0,
                "5日涨幅(%)": 12.0,
                "突破类型": "突破 12 天前高",
            }
        ]
    )
    pullback = pd.DataFrame(
        [
            {
                "板块": "半导体",
                "代码": "688002",
                "名称": "睿创微纳",
                "今日成交额(亿)": 5.1,
                "今日涨幅(%)": -1.1,
                "3日涨幅(%)": -2.0,
                "5日涨幅(%)": 3.0,
                "回踩天数": "3 天",
                "今日量/爆发量": "42.0%",
                "爆发日强度": "放量 2.0倍",
            }
        ]
    )
    return trend, range_df, pullback


class CmlmAutoJobTest(unittest.TestCase):
    def test_leader_analysis_uses_cmlm_review_frame(self):
        module = load_auto_job()
        trend, range_df, pullback = sample_frames()

        text, payload = module.make_leader_analysis("1505", "2026-06-15 15:05:00", trend, range_df, pullback)

        self.assertEqual(payload["type"], "leader")
        for section in ["市场真相", "极端程度", "资金性质", "预期差", "散户最容易犯的错", "观察条件"]:
            self.assertIn(section, text)
        self.assertIn("龙头板块 + 龙头股", text)
        self.assertIn("3日+11.23%", text)
        self.assertIn("5日+18.45%", text)

    def test_watchlist_uses_tail_session_frame(self):
        module = load_auto_job()
        trend, range_df, pullback = sample_frames()

        text, payload = module.make_watchlist("1430", "2026-06-15 14:30:00", trend, range_df, pullback)

        self.assertEqual(payload["type"], "watchlist")
        for section in ["市场真相", "右侧主升接力", "区间突破观察", "龙回头低吸观察", "观察条件"]:
            self.assertIn(section, text)
        self.assertIn("尾盘不是追涨的借口", text)

    def test_publish_snapshot_detects_copy_mismatch(self):
        module = load_auto_job()

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source.csv"
            publish_dir = root / "publish"
            publish_dir.mkdir()
            source.write_text("a,b\n1,2\n", encoding="utf-8")
            (publish_dir / "source.csv").write_text("a,b\n1,2\n", encoding="utf-8")

            module.verify_publish_snapshot(publish_dir, [source])

            (publish_dir / "source.csv").write_text("a,b\n1,3\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "artifact mismatch"):
                module.verify_publish_snapshot(publish_dir, [source])


if __name__ == "__main__":
    unittest.main()
