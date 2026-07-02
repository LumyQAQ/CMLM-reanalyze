import pandas as pd

from kpl_concept_matcher import attach_best_concepts


def test_attach_best_concepts_prefers_single_dominant_hot_theme():
    signals = pd.DataFrame(
        [
            {"名称": "华工科技", "板块": "通信设备", "涨跌幅(%)": 6.0, "增量倍数": 2.0, "今日成交额(亿)": 30.0},
            {"名称": "中际旭创", "板块": "通信设备", "涨跌幅(%)": 8.0, "增量倍数": 2.5, "今日成交额(亿)": 80.0},
            {"名称": "光迅科技", "板块": "通信设备", "涨跌幅(%)": 5.0, "增量倍数": 1.8, "今日成交额(亿)": 20.0},
            {"名称": "沃格光电", "板块": "光学光电子", "涨跌幅(%)": 3.0, "增量倍数": 1.2, "今日成交额(亿)": 10.0},
        ]
    )
    concept_map = {
        "华工科技": ["AI硬件", "玻璃基板", "光通信设备"],
        "中际旭创": ["AI硬件", "光通信设备"],
        "光迅科技": ["AI硬件", "光通信设备"],
        "沃格光电": ["玻璃基板"],
    }

    enriched = attach_best_concepts(signals, concept_map)
    row = enriched.loc[enriched["名称"] == "华工科技"].iloc[0]

    assert row["相关概念/板块"] == "AI硬件"
    assert "同池" in row["概念匹配说明"]


def test_attach_best_concepts_keeps_two_or_three_close_themes():
    signals = pd.DataFrame(
        [
            {"名称": "兴发集团", "板块": "化学制品", "今日涨幅(%)": 4.0, "今日成交额(亿)": 25.0},
            {"名称": "云天化", "板块": "化肥行业", "今日涨幅(%)": 3.8, "今日成交额(亿)": 20.0},
            {"名称": "湖北宜化", "板块": "化肥行业", "今日涨幅(%)": 3.0, "今日成交额(亿)": 12.0},
            {"名称": "兆易创新", "板块": "半导体", "今日涨幅(%)": 4.2, "今日成交额(亿)": 40.0},
            {"名称": "中电港", "板块": "半导体", "今日涨幅(%)": 3.5, "今日成交额(亿)": 14.0},
        ]
    )
    concept_map = {
        "兴发集团": ["磷化工", "涨价概念", "半导体材料"],
        "云天化": ["磷化工", "涨价概念"],
        "湖北宜化": ["磷化工"],
        "兆易创新": ["半导体材料", "涨价概念"],
        "中电港": ["半导体材料"],
    }

    enriched = attach_best_concepts(signals, concept_map)
    themes = enriched.loc[enriched["名称"] == "兴发集团", "相关概念/板块"].iloc[0].split("、")

    assert 2 <= len(themes) <= 3
    assert themes[0] == "磷化工"
    assert set(themes).issubset({"磷化工", "涨价概念", "半导体材料"})


def test_attach_best_concepts_falls_back_to_industry_board():
    signals = pd.DataFrame([{"名称": "冷门股份", "板块": "专用设备", "涨跌幅(%)": 5.0}])

    enriched = attach_best_concepts(signals, {})

    assert enriched.loc[0, "相关概念/板块"] == "专用设备"
    assert enriched.loc[0, "概念匹配说明"] == "概念库未命中，回退到行业板块。"


def test_attach_best_concepts_can_prefer_hot_board_over_weak_identity_tag():
    signals = pd.DataFrame(
        [
            {"名称": "宣泰医药", "板块": "化学制药", "涨跌幅(%)": 5.5, "今日成交额(亿)": 2.0},
            {"名称": "海欣股份", "板块": "化学制药", "涨跌幅(%)": 6.2, "今日成交额(亿)": 8.0},
            {"名称": "双成药业", "板块": "化学制药", "涨跌幅(%)": 4.8, "今日成交额(亿)": 5.0},
            {"名称": "张江高科", "板块": "房地产开发", "涨跌幅(%)": 1.2, "今日成交额(亿)": 4.0},
        ]
    )
    concept_map = {
        "宣泰医药": ["地方国资"],
        "海欣股份": ["创新药进度整理"],
        "双成药业": ["创新药进度整理"],
        "张江高科": ["地方国资"],
    }

    enriched = attach_best_concepts(signals, concept_map)
    theme = enriched.loc[enriched["名称"] == "宣泰医药", "相关概念/板块"].iloc[0]

    assert theme == "化学制药"
