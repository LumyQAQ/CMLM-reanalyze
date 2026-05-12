import pandas as pd
from mootdx.quotes import Quotes
import time
import os
import numpy as np

# ==========================================
# 🎯 核心配置
# ==========================================
MAP_FILE_PATH = '/CMLM/CMLM V1.1/stock_to_sector.csv'
SAVE_PATH = '/CMLM V4.0/v4_rrg_data.csv'
SURGE_SAVE_PATH = '/CMLM V4.0/v4_volume_surge.csv'
# 🌟 新增：龙回头（缩量回踩）数据保存路径
PULLBACK_SAVE_PATH = '/CMLM V4.0/v4_pullback_candidates.csv'

DEBUG_STOCKS = ['603878', '601727', '002640', '603418', '002009', '002971', '600111']


def clean_stock_code(code_series):
    return code_series.astype(str).str.extract(r'(\d{6})')[0]


def run_v4_engine():
    print("🚀 启动 CMLM V4.4 终极引擎 (新增【龙回头】缩量回踩算法)...")

    # ------------------------------------------
    # [1/5] 加载映射表
    # ------------------------------------------
    print(f"⏳ [1/5] 正在加载本地映射表...")
    if not os.path.exists(MAP_FILE_PATH):
        print("❌ 致命错误：找不到映射表文件！")
        return
    try:
        encodings_to_try = ['utf-16', 'gbk', 'utf-8-sig', 'utf-8', 'gb18030']
        df_map = None
        for enc in encodings_to_try:
            try:
                temp_df = pd.read_csv(MAP_FILE_PATH, encoding=enc, sep=None, engine='python')
                if len(temp_df.columns) > 1: df_map = temp_df; break
            except:
                pass
        if df_map is None: raise ValueError("无法解析文件。")

        code_col = [c for c in df_map.columns if '代码' in c][0]
        name_col = [c for c in df_map.columns if '名称' in c][0]
        industry_col = [c for c in df_map.columns if '行业' in c][0]

        df_map = df_map[[code_col, name_col, industry_col]].copy()
        df_map.columns = ['代码', '名称', '板块']
        df_map['代码'] = clean_stock_code(df_map['代码'])
        df_map = df_map[df_map['代码'].str.match(r'^(60|688|00|300)\d{4}$')]
        df_map.dropna(subset=['代码', '板块'], inplace=True)
    except Exception as e:
        print(f"❌ 读取 CSV 失败: {e}");
        return

    # ------------------------------------------
    # [2/5] mootdx 闪电拉取快照
    # ------------------------------------------
    print("\n⏳ [2/5] 呼叫 mootdx 瞬间拉取全市场切片数据...")
    client = Quotes.factory(market='std')
    symbol_list = df_map['代码'].tolist()
    all_quotes_dfs = []

    for i in range(0, len(symbol_list), 80):
        chunk = symbol_list[i:i + 80]
        try:
            res = client.quotes(symbol=chunk)
            if isinstance(res, pd.DataFrame) and not res.empty:
                all_quotes_dfs.append(res)
            elif isinstance(res, list) and len(res) > 0:
                all_quotes_dfs.append(pd.DataFrame(res))
            time.sleep(0.01)
        except:
            pass

    if not all_quotes_dfs:
        print("❌ mootdx 拉取彻底失败。");
        client.client.close();
        return

    df_quotes = pd.concat(all_quotes_dfs, ignore_index=True)
    df_quotes['代码'] = clean_stock_code(df_quotes['code'])

    # ------------------------------------------
    # [3/5] 合成 RRG 四象限
    # ------------------------------------------
    print("\n⏳ [3/5] 正在合成 RRG 四象限与板块微观数据...")
    df_merged = pd.merge(df_map, df_quotes[['代码', 'price', 'last_close', 'vol', 'amount']], on='代码', how='inner')
    df_merged = df_merged[df_merged['last_close'] > 0].copy()
    df_merged['涨跌幅'] = (df_merged['price'] - df_merged['last_close']) / df_merged['last_close'] * 100
    df_merged['突破标的'] = (df_merged['涨跌幅'] >= 8.0).astype(int)

    sector_df = df_merged.groupby('板块').agg({'涨跌幅': 'mean', 'amount': 'sum', '突破标的': 'sum'}).reset_index()
    market_median_change = sector_df['涨跌幅'].median()
    market_median_amount = sector_df['amount'].median()

    sector_df['相对强弱(X)'] = sector_df['涨跌幅'] - market_median_change
    sector_df['动量加速度(Y)'] = np.log1p(sector_df['amount']) / np.log1p(market_median_amount)
    sector_df['突破动能得分'] = sector_df['突破标的'] * 10 + 5

    df_merged['名称_涨幅'] = df_merged['名称'] + " (+" + df_merged['涨跌幅'].round(1).astype(str) + "%)"
    pioneer_dict = df_merged.sort_values('涨跌幅', ascending=False).groupby('板块').first()['名称_涨幅'].to_dict()
    df_merged['名称_成交额'] = df_merged['名称'] + " (" + (df_merged['amount'] / 100000000).round(1).astype(str) + "亿)"
    general_dict = df_merged.sort_values('amount', ascending=False).groupby('板块').first()['名称_成交额'].to_dict()

    sector_df['1日先锋'] = sector_df['板块'].map(pioneer_dict)
    sector_df['核心中军'] = sector_df['板块'].map(general_dict)

    os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
    sector_df.to_csv(SAVE_PATH, index=False)

    # ------------------------------------------
    # [4/5] 🚀 龙抬头：强势放量突破漏斗
    # ------------------------------------------
    print("\n⏳ [4/5] 启动【龙抬头】游资突破漏斗...")
    df_surge = df_merged[df_merged['涨跌幅'] >= 2.5].copy()
    surge_results = []

    for idx, row in df_surge.iterrows():
        code, today_amt, today_change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        try:
            df_k = client.bars(symbol=code, frequency=9, offset=30)
            if df_k is None or df_k.empty: df_k = client.bars(
                symbol=('sh' + code if code.startswith(('6', '9')) else 'sz' + code), frequency=9, offset=30)
            if isinstance(df_k, pd.DataFrame) and not df_k.empty:
                if df_k.index.name == 'datetime': df_k.index.name = None
                df_k = df_k.sort_values('datetime').reset_index(
                    drop=True) if 'datetime' in df_k.columns else df_k.sort_index().reset_index(drop=True)

                if len(df_k) >= 20:
                    df_k['pct_change'] = df_k['close'].pct_change() * 100
                    past_15_amts = df_k['amount'].iloc[-16:-1]
                    true_base_amt = past_15_amts.drop(past_15_amts.idxmax()).mean()
                    has_recent_breakout = (df_k['pct_change'].iloc[-16:-1] >= 8.0).any()

                    if true_base_amt > 0:
                        ratio = today_amt / true_base_amt
                        cond_a = (today_change >= 5.0) and (ratio >= 1.35)
                        cond_b = (today_change >= 3.0) and (today_change < 5.0) and (ratio >= 1.25)
                        cond_c = has_recent_breakout and (today_change >= 2.5) and (ratio >= 1.1)

                        if cond_a or cond_b or cond_c:
                            reason = "回调二波起涨" if (cond_c and not cond_a) else (
                                "趋势放量异动" if cond_b else "强势放量突破")
                            surge_results.append({
                                '代码': code, '名称': name, '板块': row['板块'], '涨跌幅(%)': round(today_change, 2),
                                '增量倍数': round(ratio, 2), '今日成交额(亿)': round(today_amt / 1e8, 2),
                                '常态均额(亿)': round(true_base_amt / 1e8, 2), '入选逻辑': reason
                            })
        except:
            pass

    df_surge_final = pd.DataFrame(surge_results)
    if not df_surge_final.empty:
        df_surge_final['逻辑权重'] = df_surge_final['入选逻辑'].map(
            {'回调二波起涨': 1, '强势放量突破': 2, '趋势放量异动': 3})
        df_surge_final.sort_values(['逻辑权重', '涨跌幅(%)'], ascending=[True, False], inplace=True)
        df_surge_final.drop(columns=['逻辑权重'], inplace=True)
    df_surge_final.to_csv(SURGE_SAVE_PATH, index=False)

    # ------------------------------------------
    # [5/5] 🐉 龙回头：缩量回踩洗盘漏斗 (新增)
    # ------------------------------------------
    print("\n⏳ [5/5] 启动【龙回头】极致缩量回踩漏斗 (耗时约1分钟)...")

    # 过滤：今天处于回踩状态（跌幅在 -7% 到 1.5% 之间），且成交额大于3000万，剔除僵尸股
    df_pullback = df_merged[
        (df_merged['涨跌幅'] <= 1.5) & (df_merged['涨跌幅'] >= -7.0) & (df_merged['amount'] >= 30000000)].copy()
    pullback_results = []
    count = 0

    for idx, row in df_pullback.iterrows():
        code, today_amt, today_change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        is_debug = code in DEBUG_STOCKS

        try:
            df_k = client.bars(symbol=code, frequency=9, offset=30)
            if df_k is None or df_k.empty: df_k = client.bars(
                symbol=('sh' + code if code.startswith(('6', '9')) else 'sz' + code), frequency=9, offset=30)

            if isinstance(df_k, pd.DataFrame) and not df_k.empty:
                if df_k.index.name == 'datetime': df_k.index.name = None
                df_k = df_k.sort_values('datetime').reset_index(
                    drop=True) if 'datetime' in df_k.columns else df_k.sort_index().reset_index(drop=True)

                if len(df_k) >= 20:
                    df_k['pct_change'] = df_k['close'].pct_change() * 100

                    # 1. 在倒数第7天到倒数第2天（即前2-6天）寻找爆发阳线
                    recent_window = df_k.iloc[-7:-2]
                    breakout_idx = None
                    breakout_ratio = 0

                    for i in range(len(recent_window)):
                        real_idx = recent_window.index[i]
                        # 检查爆发当天的涨幅和量能
                        if df_k['pct_change'].iloc[real_idx] >= 5.0:
                            past_15 = df_k['amount'].iloc[real_idx - 15: real_idx]
                            if len(past_15) == 15:
                                base_amt = past_15.drop(past_15.idxmax()).mean()
                                if base_amt > 0:
                                    ratio = df_k['amount'].iloc[real_idx] / base_amt
                                    if ratio >= 1.5:  # 找到放量爆发点
                                        breakout_idx = real_idx
                                        breakout_ratio = ratio

                    # 2. 如果找到爆发点，检查近2-3天的回踩特征
                    if breakout_idx is not None:
                        today_close = df_k['close'].iloc[-1]
                        yest_close = df_k['close'].iloc[-2]
                        breakout_amt = df_k['amount'].iloc[breakout_idx]
                        yest_amt = df_k['amount'].iloc[-2]
                        breakout_open = df_k['open'].iloc[breakout_idx]

                        # 核心洗盘逻辑：
                        # a) 缩量：今天量 < 昨天量，且今天量小于爆发日的 65% (极致缩量)
                        # b) 回踩：今天收盘 < 昨天收盘
                        # c) 防御：今天收盘 > 爆发日开盘价 (没有彻底破位)
                        vol_shrink = (today_amt < yest_amt) and (today_amt < breakout_amt * 0.65)
                        price_drop = (today_close < yest_close)
                        holding_support = (today_close > breakout_open)

                        if is_debug:
                            print(
                                f"🎯 [回踩诊断] {name}({code}): 距爆发 {len(df_k) - 1 - breakout_idx}天. 缩量:{vol_shrink}(比值{today_amt / breakout_amt:.2f}), 下跌:{price_drop}, 支撑:{holding_support}")

                        if vol_shrink and price_drop and holding_support:
                            days_since = len(df_k) - 1 - breakout_idx
                            pullback_results.append({
                                '代码': code, '名称': name, '板块': row['板块'],
                                '今日涨幅(%)': round(today_change, 2),
                                '回踩天数': f"{days_since} 天",
                                '今日量/爆发量': f"{round((today_amt / breakout_amt) * 100, 1)}%",
                                '爆发日强度': f"放量 {round(breakout_ratio, 1)}倍",
                                '今日成交额(亿)': round(today_amt / 1e8, 2)
                            })
        except:
            pass

        count += 1
        if count % 200 == 0: print(f"   已扫描 {count}/{len(df_pullback)} 只回踩标的...")

    client.client.close()

    df_pullback_final = pd.DataFrame(pullback_results)
    if not df_pullback_final.empty:
        # 按缩量程度排序（缩得越狠排越前面）
        df_pullback_final.sort_values('今日量/爆发量', ascending=True, inplace=True)
    df_pullback_final.to_csv(PULLBACK_SAVE_PATH, index=False)

    print(f"\n🎉 V4.4 引擎全套处理完毕！")
    print(f"💾 【主升爆破】已保存至: {SURGE_SAVE_PATH}")
    print(f"💾 【缩量回踩】已保存至: {PULLBACK_SAVE_PATH}")


if __name__ == "__main__":
    run_v4_engine()