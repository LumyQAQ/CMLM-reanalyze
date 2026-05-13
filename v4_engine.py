import pandas as pd
from mootdx.quotes import Quotes
import time
import os
import numpy as np
import socket

# ==========================================
# 🎯 核心配置 (自动识别路径，修复系统只读权限报错)
# ==========================================
# 获取当前脚本所在的文件夹绝对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 映射表路径：确保该文件与脚本在同一目录下
MAP_FILE_PATH = os.path.join(BASE_DIR, 'stock_to_sector.csv')

# 数据保存路径：固定在当前文件夹内
SAVE_PATH = os.path.join(BASE_DIR, 'v4_rrg_data.csv')
SURGE_SAVE_PATH = os.path.join(BASE_DIR, 'v4_volume_surge.csv')
PULLBACK_SAVE_PATH = os.path.join(BASE_DIR, 'v4_pullback_candidates.csv')

# 调试名单
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
                temp_df = pd.read_csv(MAP_FILE_PATH, encoding=enc, sep=None, engine='python')
                if len(temp_df.columns) > 1:
                    df_map = temp_df
                    break
            except:
                pass

        if df_map is None: raise ValueError("无法解析映射表文件。")

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
    # [2/5] 智能连接通达信 (强制轮询静态IP，彻底禁用测速黑洞)
    # ------------------------------------------
    print("\n⏳ [2/5] 正在连接行情节点 (强制静态路由)...")
    socket.setdefaulttimeout(5.0)  # 设置全局网络超时为 5 秒

    client = None
    tdx_servers = [
        ('119.147.212.81', 7709), ('114.80.63.12', 7709),
        ('121.14.110.200', 7709), ('110.139.17.151', 7709),
        ('101.226.9.17', 7709), ('120.25.132.147', 7709)
    ]

    for server in tdx_servers:
        try:
            print(f"   --> 尝试连接节点: {server[0]}")
            client = Quotes.factory(market='std', server=server)
            if client:
                print(f"   ✅ 成功硬连接节点: {server[0]}")
                break
        except:
            print(f"   ❌ 节点 {server[0]} 拒绝连接，切换下一个...")
            continue

    socket.setdefaulttimeout(None)  # 恢复默认超时，防止后续拉取大数据中断

    if not client:
        print("❌ 致命错误：网络彻底阻断，所有备用节点均失效。")
        return

    symbol_list = df_map['代码'].tolist()
    all_quotes = []
    print(f"   📡 正在抓取全市场 {len(symbol_list)} 只个股实时快照...")

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
    df_merged = pd.merge(df_map, df_quotes[['代码', 'price', 'last_close', 'vol', 'amount']], on='代码', how='inner')
    df_merged = df_merged[df_merged['last_close'] > 0].copy()
    df_merged['涨跌幅'] = (df_merged['price'] - df_merged['last_close']) / df_merged['last_close'] * 100

    # 基础板块运算
    sector_df = df_merged.groupby('板块').agg({'涨跌幅': 'mean', 'amount': 'sum'}).reset_index()
    m_change, m_amount = sector_df['涨跌幅'].median(), sector_df['amount'].median()
    sector_df['相对强弱(X)'] = sector_df['涨跌幅'] - m_change
    sector_df['动量加速度(Y)'] = np.log1p(sector_df['amount']) / np.log1p(m_amount)

    sector_df.to_csv(SAVE_PATH, index=False)

    # ------------------------------------------
    # [4/5] 🚀 龙抬头：右侧放量突破漏斗
    # ------------------------------------------
    print("\n⏳ [4/5] 执行【龙抬头】主升扫描...")
    df_surge_seed = df_merged[df_merged['涨跌幅'] >= 2.5].copy()
    surge_results = []

    for _, row in df_surge_seed.iterrows():
        code, amt, change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        try:
            df_k = client.bars(symbol=code, frequency=9, offset=30)
            if df_k is None or df_k.empty:
                df_k = client.bars(symbol=('sh' + code if code.startswith(('6', '9')) else 'sz' + code), frequency=9,
                                   offset=30)
            if isinstance(df_k, pd.DataFrame) and not df_k.empty:
                if df_k.index.name == 'datetime': df_k.index.name = None
                df_k = df_k.sort_values('datetime').reset_index(
                    drop=True) if 'datetime' in df_k.columns else df_k.sort_index().reset_index(drop=True)
                if len(df_k) >= 20:
                    df_k['pct'] = df_k['close'].pct_change() * 100
                    base = df_k['amount'].iloc[-16:-1].drop(df_k['amount'].iloc[-16:-1].idxmax()).mean()
                    has_recent_breakout = (df_k['pct'].iloc[-16:-1] >= 8.0).any()
                    if base > 0:
                        ratio = amt / base
                        cond_a = (change >= 5.0) and (ratio >= 1.35)
                        cond_b = (change >= 3.0) and (change < 5.0) and (ratio >= 1.25)
                        cond_c = has_recent_breakout and (change >= 2.5) and (ratio >= 1.1)

                        if cond_a or cond_b or cond_c:
                            reason = "回调二波起涨" if (cond_c and not cond_a) else (
                                "趋势放量异动" if cond_b else "强势放量突破")
                            surge_results.append({
                                '代码': code, '名称': name, '板块': row['板块'], '涨跌幅(%)': round(change, 2),
                                '增量倍数': round(ratio, 2), '今日成交额(亿)': round(amt / 1e8, 2),
                                '常态均额(亿)': round(base / 1e8, 2), '入选逻辑': reason
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
    # [5/5] 🐉 龙回头：左侧缩量洗盘漏斗
    # ------------------------------------------
    print("\n⏳ [5/5] 执行【龙回头】缩量洗盘扫描 (耗时约 1-2 分钟)...")
    df_pb_seed = df_merged[
        (df_merged['涨跌幅'] <= 1.5) & (df_merged['涨跌幅'] >= -7.0) & (df_merged['amount'] >= 30000000)].copy()
    pb_results = []

    for _, row in df_pb_seed.iterrows():
        code, amt, change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        try:
            df_k = client.bars(symbol=code, frequency=9, offset=30)
            if df_k is None or df_k.empty:
                df_k = client.bars(symbol=('sh' + code if code.startswith(('6', '9')) else 'sz' + code), frequency=9,
                                   offset=30)
            if isinstance(df_k, pd.DataFrame) and not df_k.empty:
                if df_k.index.name == 'datetime': df_k.index.name = None
                df_k = df_k.sort_values('datetime').reset_index(
                    drop=True) if 'datetime' in df_k.columns else df_k.sort_index().reset_index(drop=True)
                if len(df_k) >= 20:
                    df_k['pct'] = df_k['close'].pct_change() * 100
                    recent_window = df_k.iloc[-7:-2]
                    breakout_idx, breakout_ratio = None, 0

                    # 寻找前 2-6 天的放量大阳线
                    for i in range(len(recent_window)):
                        ri = recent_window.index[i]
                        if df_k['pct'].iloc[ri] >= 5.0:
                            past_15 = df_k['amount'].iloc[ri - 15:ri]
                            if len(past_15) == 15:
                                base_amt = past_15.drop(past_15.idxmax()).mean()
                                if base_amt > 0 and (df_k['amount'].iloc[ri] / base_amt) >= 1.5:
                                    breakout_idx = ri;
                                    breakout_ratio = df_k['amount'].iloc[ri] / base_amt;
                                    break

                    if breakout_idx is not None:
                        # 洗盘三要素：缩量极致、价格回踩、不破底线
                        if amt < df_k['amount'].iloc[-2] and amt < df_k['amount'].iloc[breakout_idx] * 0.65:
                            if df_k['close'].iloc[-1] < df_k['close'].iloc[-2] and df_k['close'].iloc[-1] > \
                                    df_k['open'].iloc[breakout_idx]:
                                pb_results.append({
                                    '代码': code, '名称': name, '板块': row['板块'], '今日涨幅(%)': round(change, 2),
                                    '回踩天数': f"{len(df_k) - 1 - breakout_idx} 天",
                                    '今日量/爆发量': f"{round((amt / df_k['amount'].iloc[breakout_idx]) * 100, 1)}%",
                                    '爆发日强度': f"放量 {round(breakout_ratio, 1)}倍",
                                    '今日成交额(亿)': round(amt / 1e8, 2)
                                })
        except:
            pass

    df_pullback_final = pd.DataFrame(pb_results)
    if not df_pullback_final.empty:
        df_pullback_final.sort_values('今日量/爆发量', ascending=True, inplace=True)
    df_pullback_final.to_csv(PULLBACK_SAVE_PATH, index=False)

    client.client.close()
    print(f"\n🎉 V4.4 全套扫描完毕！数据已存入: {BASE_DIR}")


if __name__ == "__main__":
    run_v4_engine()