import pandas as pd
from mootdx.quotes import Quotes
import time
import os
import json
import numpy as np
from datetime import datetime

from kpl_concept_matcher import attach_best_concepts, load_stock_concept_map

# ==========================================
# 🎯 核心配置 (V4.6 满血生产版 - 修复 datetime 歧义)
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MAP_FILE_PATH = os.path.join(BASE_DIR, 'stock_to_sector.csv')
SURGE_TREND_PATH = os.path.join(BASE_DIR, 'v4_surge_trend.csv')
SURGE_RANGE_PATH = os.path.join(BASE_DIR, 'v4_surge_range.csv')
PULLBACK_PATH = os.path.join(BASE_DIR, 'v4_pullback_candidates.csv')
VALIDATION_REPORT_PATH = os.path.join(BASE_DIR, 'v4_data_validation_latest.json')

MIN_TREND_AMOUNT = 1.5e8
MIN_RANGE_AMOUNT = 1.5e8
MIN_PULLBACK_AMOUNT = 5e7


def clean_stock_code(code_series):
    return code_series.astype(str).str.extract(r'(\d{6})')[0]


def get_volume_factor():
    """🕒 获取盘中量能动态外推系数"""
    now = datetime.now()
    if now.weekday() >= 5: return 1.0

    current_mins = now.hour * 60 + now.minute
    if current_mins < 570:
        return 1.0
    elif 570 <= current_mins <= 690:
        elapsed = current_mins - 570
        return 240.0 / elapsed if elapsed > 0 else 1.0
    elif 690 < current_mins < 780:
        return 2.0
    elif 780 <= current_mins <= 900:
        elapsed = 120 + (current_mins - 780)
        return 240.0 / elapsed
    else:
        return 1.0


def calc_period_change(df_k, days):
    if len(df_k) <= days:
        return np.nan
    base = df_k['close'].iloc[-days - 1]
    current = df_k['close'].iloc[-1]
    if base <= 0:
        return np.nan
    return round((current - base) / base * 100, 2)


def is_excluded_name(name):
    text = str(name).upper()
    return ("ST" in text) or ("退" in str(name))


def classify_trend_signal(today_change, ratio, has_recent_breakout, est_today_amt, change_3d):
    if est_today_amt < MIN_TREND_AMOUNT:
        return None
    if (today_change >= 5.0) and (ratio >= 1.35):
        return "强势放量突破"
    if (today_change >= 3.5) and (ratio >= 1.45):
        return "趋势放量异动"
    if has_recent_breakout and (today_change >= 4.0) and (ratio >= 1.2) and (change_3d >= 5.0):
        return "回调二波起涨"
    return None


def is_live_trading_window(now=None):
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    current_mins = now.hour * 60 + now.minute
    return (570 <= current_mins <= 690) or (780 <= current_mins <= 900)


def validate_market_snapshot(df_map, df_merged, now=None):
    now = now or datetime.now()

    total_universe = int(len(df_map))
    merged_count = int(len(df_merged))
    coverage_ratio = round(merged_count / total_universe, 4) if total_universe else 0.0

    hard_failures = []
    warnings = []

    zero_amount_ratio = 1.0
    flat_ratio = 1.0
    median_abs_change = 0.0
    median_change = 0.0
    mean_change = 0.0
    up_ratio = 0.0
    down_ratio = 0.0
    limit_up_count = 0
    limit_down_count = 0
    excluded_count = 0

    if merged_count > 0:
        zero_amount_ratio = round(float((df_merged['amount'] <= 0).mean()), 4)
        flat_ratio = round(float((df_merged['涨跌幅'].abs() < 0.01).mean()), 4)
        median_abs_change = round(float(df_merged['涨跌幅'].abs().median()), 4)
        median_change = round(float(df_merged['涨跌幅'].median()), 4)
        mean_change = round(float(df_merged['涨跌幅'].mean()), 4)
        up_ratio = round(float((df_merged['涨跌幅'] > 0).mean()), 4)
        down_ratio = round(float((df_merged['涨跌幅'] < 0).mean()), 4)
        limit_up_count = int((df_merged['涨跌幅'] >= 9.8).sum())
        limit_down_count = int((df_merged['涨跌幅'] <= -9.8).sum())
        excluded_count = int(df_merged['名称'].astype(str).apply(is_excluded_name).sum()) if '名称' in df_merged.columns else 0

    if total_universe == 0:
        hard_failures.append("映射表为空，无法验证当天行情。")
    if merged_count == 0:
        hard_failures.append("行情快照为空，疑似 mootdx 未返回有效数据。")
    if coverage_ratio < 0.90:
        hard_failures.append(f"快照覆盖率仅 {coverage_ratio:.1%}，低于 90%，当天数据不可靠。")
    if zero_amount_ratio > 0.12:
        hard_failures.append(f"零成交额占比 {zero_amount_ratio:.1%}，说明大量样本未正确更新。")
    if excluded_count > 0:
        hard_failures.append(f"快照中仍混入 {excluded_count} 只 ST/退市样本，映射或过滤存在异常。")

    if is_live_trading_window(now):
        if flat_ratio > 0.75 and median_abs_change < 0.08:
            hard_failures.append(
                f"盘中快照疑似静止：近乎零涨跌样本占比 {flat_ratio:.1%}，涨跌中位绝对值仅 {median_abs_change:.2f}% 。"
            )
        elif flat_ratio > 0.60 and median_abs_change < 0.12:
            warnings.append(
                f"盘中静态样本偏多：近乎零涨跌样本占比 {flat_ratio:.1%}，建议复核行情源。"
            )

    report = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "passed": len(hard_failures) == 0,
        "hard_failures": hard_failures,
        "warnings": warnings,
        "metrics": {
            "total_universe": total_universe,
            "merged_count": merged_count,
            "coverage_ratio": coverage_ratio,
            "zero_amount_ratio": zero_amount_ratio,
            "flat_ratio": flat_ratio,
            "median_abs_change": median_abs_change,
            "median_change": median_change,
            "mean_change": mean_change,
            "up_ratio": up_ratio,
            "down_ratio": down_ratio,
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "excluded_count": excluded_count,
        },
    }
    return report


def write_validation_report(report):
    with open(VALIDATION_REPORT_PATH, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def print_validation_report(report):
    metrics = report['metrics']
    print(
        "📋 [数据校验] "
        f"覆盖率 {metrics['coverage_ratio']:.1%} | "
        f"零成交额 {metrics['zero_amount_ratio']:.1%} | "
        f"静止样本 {metrics['flat_ratio']:.1%} | "
        f"涨跌中位绝对值 {metrics['median_abs_change']:.2f}%"
    )
    for msg in report['warnings']:
        print(f"⚠️ [数据校验] {msg}")
    for msg in report['hard_failures']:
        print(f"❌ [数据校验] {msg}")


def run_v4_engine():
    print("🚀 启动 CMLM V4.6 终极引擎 (趋势/区间/回踩 三频段雷达版)...")

    vol_factor = get_volume_factor()
    if vol_factor > 1.0:
        print(f"🕒 [系统状态] 盘中量能外推已开启！(放大系数: {vol_factor:.2f}x)")
    else:
        print("🕒 [系统状态] 盘后静态复盘模式 (系数 1.0x)")

    # ------------------------------------------
    # [1/5] 加载映射表
    # ------------------------------------------
    print(f"⏳ [1/5] 正在加载本地映射表...")
    if not os.path.exists(MAP_FILE_PATH): return
    try:
        encodings = ['utf-16', 'gbk', 'utf-8-sig', 'utf-8']
        df_map = None
        for enc in encodings:
            try:
                temp_df = pd.read_csv(MAP_FILE_PATH, encoding=enc, sep=None, engine='python')
                if len(temp_df.columns) > 1: df_map = temp_df; break
            except:
                pass

        code_col = [c for c in df_map.columns if '代码' in c][0]
        name_col = [c for c in df_map.columns if '名称' in c][0]
        industry_col = [c for c in df_map.columns if '行业' in c][0]

        df_map = df_map[[code_col, name_col, industry_col]].copy()
        df_map.columns = ['代码', '名称', '板块']
        df_map['代码'] = clean_stock_code(df_map['代码'])
        df_map = df_map[df_map['代码'].str.match(r'^(60|00|30|68)\d{4}$')]
        df_map.dropna(subset=['代码', '板块'], inplace=True)
        df_map = df_map[~df_map['名称'].apply(is_excluded_name)].copy()
    except:
        return

    concept_map = load_stock_concept_map()
    if concept_map:
        print(f"🧭 [概念库] 已加载开盘啦概念映射：{len(concept_map)} 只个股")
    else:
        print("⚠️ [概念库] 未找到可用概念库，将回退使用行业板块。")

    # ------------------------------------------
    # [2/5] mootdx 拉取快照
    # ------------------------------------------
    print("\n⏳ [2/5] 呼叫 mootdx 瞬间拉取全市场切片数据...")
    client = Quotes.factory(market='std')
    symbol_list = df_map['代码'].tolist()
    all_quotes = []

    for i in range(0, len(symbol_list), 80):
        chunk = symbol_list[i:i + 80]
        try:
            res = client.quotes(symbol=chunk)
            if isinstance(res, pd.DataFrame) and not res.empty:
                all_quotes.append(res)
            elif isinstance(res, list) and len(res) > 0:
                all_quotes.append(pd.DataFrame(res))
            time.sleep(0.01)
        except:
            pass

    if not all_quotes: return
    df_quotes = pd.concat(all_quotes, ignore_index=True)
    df_quotes['代码'] = clean_stock_code(df_quotes['code'])

    # ------------------------------------------
    # [3/5] 合成基本数据
    # ------------------------------------------
    print("\n⏳ [3/5] 正在合成板块微观数据...")
    df_merged = pd.merge(df_map, df_quotes[['代码', 'price', 'last_close', 'vol', 'amount']], on='代码', how='inner')
    df_merged = df_merged[df_merged['last_close'] > 0].copy()
    df_merged['涨跌幅'] = (df_merged['price'] - df_merged['last_close']) / df_merged['last_close'] * 100

    validation_report = validate_market_snapshot(df_map, df_merged)
    write_validation_report(validation_report)
    print_validation_report(validation_report)
    if not validation_report['passed']:
        try:
            client.client.close()
        except:
            pass
        raise RuntimeError("当天行情快照校验失败，已停止生成输出文件。")

    # ------------------------------------------
    # [4/5] 🚀 趋势与区间双轨扫描
    # ------------------------------------------
    print("\n⏳ [4/5] 启动【放量主升】双轨漏斗 (趋势放量 & 区间破位)...")
    df_surge = df_merged[
        (df_merged['涨跌幅'] >= 2.5)
        & (df_merged['amount'] >= MIN_TREND_AMOUNT)
    ].copy()
    trend_results = []
    range_results = []

    for idx, row in df_surge.iterrows():
        code, today_amt, today_change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        try:
            df_k = client.bars(symbol=code, frequency=9, offset=60)
            if df_k is None or df_k.empty:
                df_k = client.bars(symbol=('sh' + code if code.startswith(('6', '9')) else 'sz' + code), frequency=9,
                                   offset=60)

            if isinstance(df_k, pd.DataFrame) and not df_k.empty and len(df_k) >= 45:
                # 🛠️ 核心修复区：消除 datetime 歧义 BUG
                if df_k.index.name == 'datetime': df_k.index.name = None
                df_k = df_k.reset_index(drop=True)
                if 'datetime' in df_k.columns: df_k = df_k.sort_values('datetime').reset_index(drop=True)

                df_k['pct_change'] = df_k['close'].pct_change() * 100
                change_3d = calc_period_change(df_k, 3)
                change_5d = calc_period_change(df_k, 5)

                past_15_amts = df_k['amount'].iloc[-16:-1]
                true_base_amt = past_15_amts.drop(past_15_amts.idxmax()).mean()
                has_recent_breakout = (df_k['pct_change'].iloc[-16:-1] >= 8.0).any()

                if true_base_amt > 0:
                    est_today_amt = today_amt * vol_factor
                    ratio = est_today_amt / true_base_amt

                    # --- 前高数据提取 ---
                    recent_40_closes = df_k['close'].iloc[-41:-1]
                    prev_high_close = recent_40_closes.max()
                    prev_high_idx = recent_40_closes.idxmax()
                    days_since_high = len(df_k) - 1 - prev_high_idx
                    today_close = df_k['close'].iloc[-1]

                    # 轨道1：常规趋势放量打法
                    reason = classify_trend_signal(
                        today_change=today_change,
                        ratio=ratio,
                        has_recent_breakout=has_recent_breakout,
                        est_today_amt=est_today_amt,
                        change_3d=change_3d,
                    )
                    if reason:
                        trend_results.append({
                            '代码': code, '名称': name, '板块': row['板块'], '涨跌幅(%)': round(today_change, 2),
                            '3日涨幅(%)': change_3d, '5日涨幅(%)': change_5d,
                            '增量倍数': round(ratio, 2), '今日成交额(亿)': round(today_amt / 1e8, 2),
                            '常态均额(亿)': round(true_base_amt / 1e8, 2), '逻辑标签': reason
                        })

                    # 轨道2：独立区间破位打法 (调整>4天，且今日收盘突破40日最高，量能放大>1.5倍)
                    if (est_today_amt >= MIN_RANGE_AMOUNT) and (today_change >= 4.0) and (days_since_high >= 4) and (today_close > prev_high_close) and (
                            ratio >= 1.5):
                        range_results.append({
                            '代码': code, '名称': name, '板块': row['板块'], '涨跌幅(%)': round(today_change, 2),
                            '3日涨幅(%)': change_3d, '5日涨幅(%)': change_5d,
                            '突破类型': f"突破 {days_since_high} 天前高", '增量倍数': round(ratio, 2),
                            '今日成交额(亿)': round(today_amt / 1e8, 2)
                        })
        except:
            pass

    # 保存轨道1数据
    df_trend_final = pd.DataFrame(trend_results)
    if not df_trend_final.empty: df_trend_final.sort_values('增量倍数', ascending=False, inplace=True)
    df_trend_final = attach_best_concepts(df_trend_final, concept_map)
    df_trend_final.to_csv(SURGE_TREND_PATH, index=False)

    # 保存轨道2数据
    df_range_final = pd.DataFrame(range_results)
    if not df_range_final.empty: df_range_final.sort_values('增量倍数', ascending=False, inplace=True)
    df_range_final = attach_best_concepts(df_range_final, concept_map)
    df_range_final.to_csv(SURGE_RANGE_PATH, index=False)

    # ------------------------------------------
    # [5/5] 🐉 龙回头：缩量回踩洗盘漏斗
    # ------------------------------------------
    print("\n⏳ [5/5] 启动【龙回头】极致缩量回踩漏斗...")
    df_pullback = df_merged[
        (df_merged['涨跌幅'] <= 1.5) & (df_merged['涨跌幅'] >= -7.0) & (df_merged['amount'] >= MIN_PULLBACK_AMOUNT)].copy()
    pullback_results = []

    for idx, row in df_pullback.iterrows():
        code, today_amt, today_change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        try:
            df_k = client.bars(symbol=code, frequency=9, offset=30)
            if df_k is None or df_k.empty:
                df_k = client.bars(symbol=('sh' + code if code.startswith(('6', '9')) else 'sz' + code), frequency=9,
                                   offset=30)

            if isinstance(df_k, pd.DataFrame) and not df_k.empty and len(df_k) >= 20:
                # 🛠️ 核心修复区：消除 datetime 歧义 BUG
                if df_k.index.name == 'datetime': df_k.index.name = None
                df_k = df_k.reset_index(drop=True)
                if 'datetime' in df_k.columns: df_k = df_k.sort_values('datetime').reset_index(drop=True)

                df_k['pct_change'] = df_k['close'].pct_change() * 100
                change_3d = calc_period_change(df_k, 3)
                change_5d = calc_period_change(df_k, 5)

                recent_window = df_k.iloc[-7:-1]
                breakout_idx = None
                breakout_ratio = 0
                for i in range(len(recent_window)):
                    real_idx = recent_window.index[i]
                    if df_k['pct_change'].iloc[real_idx] >= 5.0:
                        past_15 = df_k['amount'].iloc[real_idx - 15: real_idx]
                        if len(past_15) == 15:
                            base_amt = past_15.drop(past_15.idxmax()).mean()
                            if base_amt > 0 and (df_k['amount'].iloc[real_idx] / base_amt) >= 1.5:
                                breakout_idx = real_idx
                                breakout_ratio = df_k['amount'].iloc[real_idx] / base_amt

                if breakout_idx is not None:
                    today_close, yest_close = df_k['close'].iloc[-1], df_k['close'].iloc[-2]
                    breakout_amt, breakout_open = df_k['amount'].iloc[breakout_idx], df_k['open'].iloc[breakout_idx]
                    yest_amt = df_k['amount'].iloc[-2]

                    est_today_amt = today_amt * vol_factor
                    vol_shrink = (est_today_amt < yest_amt) and (est_today_amt < breakout_amt * 0.65)
                    price_drop = (today_close < yest_close)
                    holding_support = (today_close > breakout_open)

                    if vol_shrink and price_drop and holding_support:
                        days_since = len(df_k) - 1 - breakout_idx
                        pullback_results.append({
                            '代码': code, '名称': name, '板块': row['板块'], '今日涨幅(%)': round(today_change, 2),
                            '3日涨幅(%)': change_3d, '5日涨幅(%)': change_5d,
                            '回踩天数': f"{days_since} 天",
                            '今日量/爆发量': f"{round((today_amt / breakout_amt) * 100, 1)}% (预估 {round((est_today_amt / breakout_amt) * 100, 1)}%)" if vol_factor > 1.0 else f"{round((today_amt / breakout_amt) * 100, 1)}%",
                            '爆发日强度': f"放量 {round(breakout_ratio, 1)}倍",
                            '今日成交额(亿)': round(today_amt / 1e8, 2)
                        })
        except:
            pass

    client.client.close()
    df_pullback_final = pd.DataFrame(pullback_results)
    if not df_pullback_final.empty: df_pullback_final.sort_values('今日量/爆发量', ascending=True, inplace=True)
    df_pullback_final = attach_best_concepts(df_pullback_final, concept_map)
    df_pullback_final.to_csv(PULLBACK_PATH, index=False)

    print(f"\n🎉 V4.6 引擎处理完毕！三大独立池文件已成功生成！")


if __name__ == "__main__": run_v4_engine()
