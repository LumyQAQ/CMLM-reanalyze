import pandas as pd
from mootdx.quotes import Quotes
import time
import os
import numpy as np
from datetime import datetime

# ==========================================
# 🎯 核心配置 (已修复系统只读报错 & 匹配全市场)
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MAP_FILE_PATH = os.path.join(BASE_DIR, 'stock_to_sector.csv')
SAVE_PATH = os.path.join(BASE_DIR, 'v4_rrg_data.csv')
SURGE_SAVE_PATH = os.path.join(BASE_DIR, 'v4_volume_surge.csv')
PULLBACK_SAVE_PATH = os.path.join(BASE_DIR, 'v4_pullback_candidates.csv')

DEBUG_STOCKS = ['603878', '601727', '002640', '603418', '002009', '002971', '600111']


def clean_stock_code(code_series):
    return code_series.astype(str).str.extract(r'(\d{6})')[0]


def get_volume_factor():
    """🕒 核心算法：获取盘中量能动态外推系数"""
    now = datetime.now()
    if now.weekday() >= 5: return 1.0

    current_mins = now.hour * 60 + now.minute
    if current_mins < 570:
        return 1.0
    elif 570 <= current_mins <= 690:
        elapsed = current_mins - 570
        if elapsed == 0: elapsed = 1
        return 240.0 / elapsed
    elif 690 < current_mins < 780:
        return 2.0
    elif 780 <= current_mins <= 900:
        elapsed = 120 + (current_mins - 780)
        return 240.0 / elapsed
    else:
        return 1.0


def get_logic_weight(reason):
    """排序权重：区间破位(最高优先级) > 强势放量 > 二波起涨 > 趋势异动"""
    if "区间破位" in reason: return 1
    if reason == "强势放量突破": return 2
    if reason == "回调二波起涨": return 3
    return 4


def run_v4_engine():
    print("🚀 启动 CMLM V4.5 终极引擎 (新增【区间放量破位】形态识别)...")

    # ------------------------------------------
    # [0/5] 计算盘中外推系数
    # ------------------------------------------
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
    except:
        return

    # ------------------------------------------
    # [2/5] mootdx 闪电拉取快照
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
    # [3/5] 合成 RRG 四象限
    # ------------------------------------------
    print("\n⏳ [3/5] 正在合成板块微观数据...")
    df_merged = pd.merge(df_map, df_quotes[['代码', 'price', 'last_close', 'vol', 'amount']], on='代码', how='inner')
    df_merged = df_merged[df_merged['last_close'] > 0].copy()
    df_merged['涨跌幅'] = (df_merged['price'] - df_merged['last_close']) / df_merged['last_close'] * 100
    df_merged['突破标的'] = (df_merged['涨跌幅'] >= 8.0).astype(int)

    sector_df = df_merged.groupby('板块').agg({'涨跌幅': 'mean', 'amount': 'sum', '突破标的': 'sum'}).reset_index()
    m_change, m_amount = sector_df['涨跌幅'].median(), sector_df['amount'].median()
    sector_df['相对强弱(X)'] = sector_df['涨跌幅'] - m_change
    sector_df['动量加速度(Y)'] = np.log1p(sector_df['amount']) / np.log1p(m_amount)
    sector_df['突破动能得分'] = sector_df['突破标的'] * 10 + 5

    df_merged['名称_涨幅'] = df_merged['名称'] + " (+" + df_merged['涨跌幅'].round(1).astype(str) + "%)"
    pioneer = df_merged.sort_values('涨跌幅', ascending=False).groupby('板块').first()['名称_涨幅'].to_dict()
    df_merged['名称_成交额'] = df_merged['名称'] + " (" + (df_merged['amount'] / 1e8).round(1).astype(str) + "亿)"
    general = df_merged.sort_values('amount', ascending=False).groupby('板块').first()['名称_成交额'].to_dict()

    sector_df['1日先锋'], sector_df['核心中军'] = sector_df['板块'].map(pioneer), sector_df['板块'].map(general)
    os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
    sector_df.to_csv(SAVE_PATH, index=False)

    # ------------------------------------------
    # [4/5] 🚀 龙抬头：趋势放量 + 区间破位 漏斗
    # ------------------------------------------
    print("\n⏳ [4/5] 启动【龙抬头】综合主升漏斗 (含前高突破监测)...")
    df_surge = df_merged[df_merged['涨跌幅'] >= 2.5].copy()
    surge_results = []

    for idx, row in df_surge.iterrows():
        code, today_amt, today_change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        try:
            # 🔮 核心升级：拉取60天数据，以便观察长达1-2个月的震荡区间
            df_k = client.bars(symbol=code, frequency=9, offset=60)
            if df_k is None or df_k.empty: df_k = client.bars(
                symbol=('sh' + code if code.startswith(('6', '9')) else 'sz' + code), frequency=9, offset=60)

            if isinstance(df_k, pd.DataFrame) and not df_k.empty and len(df_k) >= 45:
                df_k = df_k.sort_values('datetime').reset_index(
                    drop=True) if 'datetime' in df_k.columns else df_k.sort_index().reset_index(drop=True)
                df_k['pct_change'] = df_k['close'].pct_change() * 100

                # 常态均量
                past_15_amts = df_k['amount'].iloc[-16:-1]
                true_base_amt = past_15_amts.drop(past_15_amts.idxmax()).mean()
                has_recent_breakout = (df_k['pct_change'].iloc[-16:-1] >= 8.0).any()

                if true_base_amt > 0:
                    est_today_amt = today_amt * vol_factor
                    ratio = est_today_amt / true_base_amt

                    # --- 形态识别引擎：寻找前高 ---
                    # 截取过去 40 天(排除今天)的收盘价，找到最高点
                    recent_40_closes = df_k['close'].iloc[-41:-1]
                    prev_high_close = recent_40_closes.max()
                    prev_high_idx = recent_40_closes.idxmax()
                    # 计算距离这个前高过去了几天
                    days_since_high = len(df_k) - 1 - prev_high_idx
                    today_close = df_k['close'].iloc[-1]

                    # --- 漏斗条件池 ---
                    # 条件A/B/C：原有的趋势内连升放量打法
                    cond_a = (today_change >= 5.0) and (ratio >= 1.35)
                    cond_b = (today_change >= 3.0) and (today_change < 5.0) and (ratio >= 1.25)
                    cond_c = has_recent_breakout and (today_change >= 2.5) and (ratio >= 1.1)

                    # 条件D：🔥 区间放量破位 (调整超过4天以上，且今日收盘突破40日最高收盘价，量能放大1.5倍)
                    cond_d = (today_change >= 4.0) and (days_since_high >= 4) and (today_close > prev_high_close) and (
                                ratio >= 1.5)

                    if cond_a or cond_b or cond_c or cond_d:
                        # 标签优先级：区间破位 > 强势放量 > 回调二波 > 趋势异动
                        if cond_d:
                            reason = f"🏆 区间破位 ({days_since_high}天前高)"
                        elif cond_a:
                            reason = "强势放量突破"
                        elif cond_c:
                            reason = "回调二波起涨"
                        else:
                            reason = "趋势放量异动"

                        surge_results.append({
                            '代码': code, '名称': name, '板块': row['板块'], '涨跌幅(%)': round(today_change, 2),
                            '增量倍数': round(ratio, 2), '今日成交额(亿)': round(today_amt / 1e8, 2),
                            '常态均额(亿)': round(true_base_amt / 1e8, 2), '入选逻辑': reason
                        })
        except:
            pass

    df_surge_final = pd.DataFrame(surge_results)
    if not df_surge_final.empty:
        df_surge_final['逻辑权重'] = df_surge_final['入选逻辑'].apply(get_logic_weight)
        df_surge_final.sort_values(['逻辑权重', '增量倍数'], ascending=[True, False], inplace=True)
        df_surge_final.drop(columns=['逻辑权重'], inplace=True)
    df_surge_final.to_csv(SURGE_SAVE_PATH, index=False)

    # ------------------------------------------
    # [5/5] 🐉 龙回头：缩量回踩洗盘漏斗
    # ------------------------------------------
    print("\n⏳ [5/5] 启动【龙回头】极致缩量回踩漏斗...")
    df_pullback = df_merged[
        (df_merged['涨跌幅'] <= 1.5) & (df_merged['涨跌幅'] >= -7.0) & (df_merged['amount'] >= 30000000)].copy()
    pullback_results = []

    for idx, row in df_pullback.iterrows():
        code, today_amt, today_change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        try:
            df_k = client.bars(symbol=code, frequency=9, offset=30)
            if df_k is None or df_k.empty: df_k = client.bars(
                symbol=('sh' + code if code.startswith(('6', '9')) else 'sz' + code), frequency=9, offset=30)
            if isinstance(df_k, pd.DataFrame) and not df_k.empty and len(df_k) >= 20:
                df_k = df_k.sort_values('datetime').reset_index(
                    drop=True) if 'datetime' in df_k.columns else df_k.sort_index().reset_index(drop=True)
                df_k['pct_change'] = df_k['close'].pct_change() * 100

                recent_window = df_k.iloc[-7:-2]
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
    df_pullback_final.to_csv(PULLBACK_SAVE_PATH, index=False)

    print(f"\n🎉 V4.5 引擎全套处理完毕！")


if __name__ == "__main__": run_v4_engine()