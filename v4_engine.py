import os
import socket
import time
import numpy as np
import pandas as pd
from mootdx.quotes import Quotes

# ==========================================
# 🎯 核心配置 (自动识别绝对路径)
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAP_FILE_PATH = os.path.join(BASE_DIR, 'stock_to_sector.csv')
SAVE_PATH = os.path.join(BASE_DIR, 'v4_rrg_data.csv')
SURGE_SAVE_PATH = os.path.join(BASE_DIR, 'v4_volume_surge.csv')
PULLBACK_SAVE_PATH = os.path.join(BASE_DIR, 'v4_pullback_candidates.csv')


def clean_stock_code(code_series):
    return code_series.astype(str).str.extract(r'(\d{6})')[0]


def run_v4_engine():
    print("🚀 启动 CMLM V4.4 终极双模引擎 (本地极速版)...")

    # ------------------------------------------
    # [1/5] 加载本地映射表
    # ------------------------------------------
    print(f"⏳ [1/5] 正在加载映射表: {MAP_FILE_PATH}")
    if not os.path.exists(MAP_FILE_PATH):
        print("❌ 致命错误：找不到 stock_to_sector.csv！")
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
    # [2/5] 极速连接通达信 (回归 mootdx 智能测速)
    # ------------------------------------------
    print("\n⏳ [2/5] 正在呼叫 mootdx 寻找全网最快节点...")

    # 给予合理的测速等待时间
    socket.setdefaulttimeout(10.0)

    try:
        # 本地网络下，直接使用原生 factory，它会自动 ping 几十个节点并选出最优
        client = Quotes.factory(market='std')
        print("   ✅ 最佳节点连接成功！")
    except Exception as e:
        print(f"❌ 网络连接或测速失败: {e}")
        print("💡 提示：如果开启了全局代理，请切换为【规则模式】。")
        return

    # 连接成功后，给后续拉取数据套上 15 秒超时护盾，防止偶发断流
    socket.setdefaulttimeout(15.0)

    symbol_list = df_map['代码'].tolist()
    all_quotes = []
    total_symbols = len(symbol_list)
    print(f"   📡 正在全速抓取全市场 {total_symbols} 只个股实时快照...")

    for i in range(0, total_symbols, 80):
        chunk = symbol_list[i:i + 80]
        try:
            res = client.quotes(symbol=chunk)
            # 空壳过滤器：确保拿回来的真有 code 列
            if isinstance(res, pd.DataFrame) and not res.empty and 'code' in res.columns:
                all_quotes.append(res)
            elif isinstance(res, list) and len(res) > 0:
                temp_df = pd.DataFrame(res)
                if 'code' in temp_df.columns:
                    all_quotes.append(temp_df)

            # 心跳播报
            if (i + 80) % 800 == 0 or (i + 80) >= total_symbols:
                print(f"   ...已成功抓取 {min(i + 80, total_symbols)} / {total_symbols} 只...")

            time.sleep(0.01)  # 本地网络强悍，睡眠时间可以缩短
        except Exception as e:
            print(f"   ⚠️ 第 {i} 批次网络颠簸: {e}")
            pass

    if not all_quotes:
        print("❌ 致命错误：全市场抓取结果为空。");
        client.client.close();
        return

    df_quotes = pd.concat(all_quotes, ignore_index=True)
    df_quotes['代码'] = clean_stock_code(df_quotes['code'])
    print(f"   ✅ 快照拉取完毕，有效数据 {len(df_quotes)} 条！")

    # ------------------------------------------
    # [3/5] 合成 RRG 四象限数据
    # ------------------------------------------
    print("\n⏳ [3/5] 正在合成板块动能数据...")
    df_merged = pd.merge(df_map, df_quotes[['代码', 'price', 'last_close', 'vol', 'amount']], on='代码', how='inner')
    df_merged = df_merged[df_merged['last_close'] > 0].copy()
    df_merged['涨跌幅'] = (df_merged['price'] - df_merged['last_close']) / df_merged['last_close'] * 100

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
            if df_k is None or df_k.empty: df_k = client.bars(
                symbol=('sh' + code if code.startswith(('6', '9')) else 'sz' + code), frequency=9, offset=30)
            if isinstance(df_k, pd.DataFrame) and not df_k.empty:
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
                            surge_results.append(
                                {'代码': code, '名称': name, '板块': row['板块'], '涨跌幅(%)': round(change, 2),
                                 '增量倍数': round(ratio, 2), '今日成交额(亿)': round(amt / 1e8, 2),
                                 '常态均额(亿)': round(base / 1e8, 2), '入选逻辑': reason})
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
    print("\n⏳ [5/5] 执行【龙回头】缩量洗盘扫描 (耗时约 1 分钟)...")
    df_pb_seed = df_merged[
        (df_merged['涨跌幅'] <= 1.5) & (df_merged['涨跌幅'] >= -7.0) & (df_merged['amount'] >= 30000000)].copy()
    pb_results = []

    for _, row in df_pb_seed.iterrows():
        code, amt, change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
        try:
            df_k = client.bars(symbol=code, frequency=9, offset=30)
            if df_k is None or df_k.empty: df_k = client.bars(
                symbol=('sh' + code if code.startswith(('6', '9')) else 'sz' + code), frequency=9, offset=30)
            if isinstance(df_k, pd.DataFrame) and not df_k.empty:
                df_k = df_k.sort_values('datetime').reset_index(
                    drop=True) if 'datetime' in df_k.columns else df_k.sort_index().reset_index(drop=True)
                if len(df_k) >= 20:
                    df_k['pct'] = df_k['close'].pct_change() * 100
                    recent_window = df_k.iloc[-7:-2]
                    breakout_idx, breakout_ratio = None, 0

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
                        if amt < df_k['amount'].iloc[-2] and amt < df_k['amount'].iloc[breakout_idx] * 0.65:
                            if df_k['close'].iloc[-1] < df_k['close'].iloc[-2] and df_k['close'].iloc[-1] > \
                                    df_k['open'].iloc[breakout_idx]:
                                pb_results.append(
                                    {'代码': code, '名称': name, '板块': row['板块'], '今日涨幅(%)': round(change, 2),
                                     '回踩天数': f"{len(df_k) - 1 - breakout_idx} 天",
                                     '今日量/爆发量': f"{round((amt / df_k['amount'].iloc[breakout_idx]) * 100, 1)}%",
                                     '爆发日强度': f"放量 {round(breakout_ratio, 1)}倍",
                                     '今日成交额(亿)': round(amt / 1e8, 2)})
        except:
            pass

    df_pullback_final = pd.DataFrame(pb_results)
    if not df_pullback_final.empty:
        df_pullback_final.sort_values('今日量/爆发量', ascending=True, inplace=True)
    df_pullback_final.to_csv(PULLBACK_SAVE_PATH, index=False)

    client.client.close()
    print(f"\n🎉 引擎扫描完毕！数据已更新至本地！")


if __name__ == "__main__":
    run_v4_engine()