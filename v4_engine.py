import pandas as pd
from mootdx.quotes import Quotes
import time
import os
import numpy as np
import socket

# ==========================================
# 🎯 核心配置 (自动识别路径，修复系统权限报错)
# ==========================================
# 获取当前脚本所在的文件夹绝对路径 (如 /Users/ziranfeng/Desktop/CMLM V4.0/)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 映射表路径：确保该文件与脚本在同一目录下
MAP_FILE_PATH = os.path.join(BASE_DIR, 'stock_to_sector.csv')

# 数据保存路径
SAVE_PATH = os.path.join(BASE_DIR, 'v4_rrg_data.csv')
SURGE_SAVE_PATH = os.path.join(BASE_DIR, 'v4_volume_surge.csv')
PULLBACK_SAVE_PATH = os.path.join(BASE_DIR, 'v4_pullback_candidates.csv')

# 监控名单
DEBUG_STOCKS = ['603878', '601727', '002640', '603418', '002009', '002971', '600111']


def clean_stock_code(code_series):
    """提取纯数字代码并转为 6 位字符串"""
    return code_series.astype(str).str.extract(r'(\d{6})')[0]


def run_v4_engine():
    print("🚀 启动 CMLM V4.4 终极双模引擎 (本地/云端全适配版)...")

    # ------------------------------------------
    # [1/5] 加载本地映射表
    # ------------------------------------------
    print(f"⏳ [1/5] 正在加载映射表: {MAP_FILE_PATH}")
    if not os.path.exists(MAP_FILE_PATH):
        print(f"❌ 致命错误：在此路径下找不到 stock_to_sector.csv: {MAP_FILE_PATH}")
        return

    try:
        encodings = ['utf-16', 'gbk', 'utf-8-sig', 'utf-8']
        df_map = None
        for enc in encodings:
            try:
                df_map = pd.read_csv(MAP_FILE_PATH, encoding=enc, sep=None, engine='python')
                if len(df_map.columns) > 1: break
            except:
                pass

        code_col = [c for c in df_map.columns if '代码' in c][0]
        name_col = [c for c in df_map.columns if '名称' in c][0]
        industry_col = [c for c in df_map.columns if '行业' in c][0]

        df_map = df_map[[code_col, name_col, industry_col]].copy()
        df_map.columns = ['代码', '名称', '板块']
        df_map['代码'] = clean_stock_code(df_map['代码'])
        df_map = df_map[df_map['代码'].str.match(r'^(60|688|00|300)\d{4}$')]
        df_map.dropna(subset=['代码', '板块'], inplace=True)
    except Exception as e:
        print(f"❌ 解析映射表失败: {e}");
        return

    # ------------------------------------------
    # [2/5] 智能连接通达信 (带防卡死护盾)
    # ------------------------------------------
    print("\n⏳ [2/5] 正在连接行情节点 (5s 超时保护)...")
    socket.setdefaulttimeout(5.0)  # 设置全局网络超时

    client = None
    # 优先尝试自动测速，失败则轮询核心节点
    try:
        client = Quotes.factory(market='std')
    except:
        tdx_servers = [
            ('119.147.212.81', 7709), ('114.80.63.12', 7709),
            ('121.14.110.200', 7709), ('110.139.17.151', 7709)
        ]
        for server in tdx_servers:
            try:
                client = Quotes.factory(market='std', server=server)
                if client: break
            except:
                continue

    socket.setdefaulttimeout(None)  # 恢复默认超时，防止大数据拉取中断

    if not client:
        print("❌ 网络彻底阻断，请检查代理/VPN设置（建议关闭全局模式重试）。")
        return

    symbol_list = df_map['代码'].tolist()
    all_quotes = []
    print(f"   ✅ 已连接，正在抓取全市场 {len(symbol_list)} 只个股快照...")

    for i in range(0, len(symbol_list), 80):
        chunk = symbol_list[i:i + 80]
        try:
            res = client.quotes(symbol=chunk)
            if isinstance(res, pd.DataFrame): all_quotes.append(res)
            time.sleep(0.01)
        except:
            pass

    if not all_quotes:
        print("❌ 行情拉取失败。");
        client.client.close();
        return

    df_quotes = pd.concat(all_quotes, ignore_index=True)
    df_quotes['代码'] = clean_stock_code(df_quotes['code'])

    # ------------------------------------------
    # [3/5] 合成 RRG 四象限数据
    # ------------------------------------------
    print("\n⏳ [3/5] 正在合成板块动能数据...")
    df_merged = pd.merge(df_map, df_quotes[['代码', 'price', 'last_close', 'vol', 'amount']], on='代码')
    df_merged = df_merged[df_merged['last_close'] > 0].copy()
    df_merged['涨跌幅'] = (df_merged['price'] - df_merged['last_close']) / df_merged['last_close'] * 100

    # 板块聚合逻辑
    sector_df = df_merged.groupby('板块').agg({'涨跌幅': 'mean', 'amount': 'sum'}).reset_index()
    m_change, m_amount = sector_df['涨跌幅'].median(), sector_df['amount'].median()
    sector_df['相对强弱(X)'] = sector_df['涨跌幅'] - m_change
    sector_df['动量加速度(Y)'] = np.log1p(sector_df['amount']) / np.log1p(m_amount)

    sector_df.to_csv(SAVE_PATH, index=False)

    # ------------------------------------------
    # [4/5] 🚀 龙抬头：右侧放量突破
    # ------------------------------------------
    print("\n⏳ [4/5] 执行【龙抬头】主升扫描...")
    df_surge_seed = df_merged[df_merged['涨跌幅'] >= 2.5].copy()
    surge_results = []

    for _, row in df_surge_seed.iterrows():
        code, amt, change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        try:
            df_k = client.bars(symbol=code, frequency=9, offset=30)
            if df_k is not None and not df_k.empty:
                df_k = df_k.sort_index().reset_index()  # 统一处理索引
                if len(df_k) >= 20:
                    df_k['pct'] = df_k['close'].pct_change() * 100
                    base = df_k['amount'].iloc[-16:-1].drop(df_k['amount'].iloc[-16:-1].idxmax()).mean()
                    if base > 0:
                        ratio = amt / base
                        if (change >= 5.0 and ratio >= 1.35) or (change >= 3.0 and ratio >= 1.25):
                            surge_results.append({
                                '代码': code, '名称': name, '板块': row['板块'], '涨跌幅(%)': round(change, 2),
                                '增量倍数': round(ratio, 2), '今日成交额(亿)': round(amt / 1e8, 2), '入选逻辑': "强势突破"
                            })
        except:
            pass
    pd.DataFrame(surge_results).to_csv(SURGE_SAVE_PATH, index=False)

    # ------------------------------------------
    # [5/5] 🐉 龙回头：左侧缩量回踩
    # ------------------------------------------
    print("\n⏳ [5/5] 执行【龙回头】缩量洗盘扫描 (耗时约 1 分钟)...")
    df_pb_seed = df_merged[(df_merged['涨跌幅'] <= 1.5) & (df_merged['amount'] >= 30000000)].copy()
    pb_results = []

    for _, row in df_pb_seed.iterrows():
        code, amt, change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        try:
            df_k = client.bars(symbol=code, frequency=9, offset=30)
            if df_k is not None and not df_k.empty:
                df_k = df_k.sort_index().reset_index()
                if len(df_k) >= 20:
                    df_k['pct'] = df_k['close'].pct_change() * 100
                    # 寻找前 2-6 天的放量阳线
                    break_idx = None
                    for i in range(len(df_k) - 7, len(df_k) - 2):
                        if df_k['pct'].iloc[i] >= 5.0:
                            b_amt = df_k['amount'].iloc[i]
                            prev_base = df_k['amount'].iloc[i - 15:i].mean()
                            if b_amt / prev_base >= 1.5: break_idx = i; break

                    if break_idx:
                        # 缩量下跌且不破爆发开盘价
                        if amt < df_k['amount'].iloc[-2] and amt < df_k['amount'].iloc[break_idx] * 0.65:
                            if df_k['close'].iloc[-1] < df_k['close'].iloc[-2] and df_k['close'].iloc[-1] > \
                                    df_k['open'].iloc[break_idx]:
                                pb_results.append({
                                    '代码': code, '名称': name, '板块': row['板块'], '今日涨幅(%)': round(change, 2),
                                    '回踩天数': f"{len(df_k) - 1 - break_idx} 天",
                                    '今日量/爆发量': f"{round((amt / df_k['amount'].iloc[break_idx]) * 100, 1)}%",
                                    '今日成交额(亿)': round(amt / 1e8, 2)
                                })
        except:
            pass

    pd.DataFrame(pb_results).to_csv(PULLBACK_SAVE_PATH, index=False)
    client.client.close()
    print(f"\n🎉 V4.4 运行完毕！数据已存入: {BASE_DIR}")


if __name__ == "__main__":
    run_v4_engine()