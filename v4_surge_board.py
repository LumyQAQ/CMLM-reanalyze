import streamlit as st
import pandas as pd
import os
import numpy as np
from datetime import datetime
from mootdx.quotes import Quotes
import time
import socket

# ==========================================
# 🎯 智能路径配置 (自动识别云端与本地环境)
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAP_FILE = os.path.join(BASE_DIR, 'stock_to_sector.csv')
SURGE_FILE = os.path.join(BASE_DIR, 'v4_volume_surge.csv')
PULLBACK_FILE = os.path.join(BASE_DIR, 'v4_pullback_candidates.csv')

st.set_page_config(page_title="量价关系双模复盘", layout="wide", page_icon="⚔️")


def clean_stock_code(code_series):
    return code_series.astype(str).str.extract(r'(\d{6})')[0]


# ==========================================
# ⚙️ 核心引擎逻辑 (云端实时计算模块)
# ==========================================
def run_quant_engine():
    progress_bar = st.progress(0.0)
    status_text = st.empty()

    # --- [1/4] 加载底层映射表 ---
    status_text.text("⏳ [1/4] 正在加载底层板块映射表...")
    if not os.path.exists(MAP_FILE):
        st.error(f"❌ 致命错误：找不到映射表 {MAP_FILE}，请确保 stock_to_sector.csv 已上传！")
        return
    try:
        encodings = ['utf-16', 'gbk', 'utf-8-sig', 'utf-8']
        df_map = None
        for enc in encodings:
            try:
                temp_df = pd.read_csv(MAP_FILE, encoding=enc, sep=None, engine='python')
                if len(temp_df.columns) > 1:
                    df_map = temp_df
                    break
            except:
                pass

        if df_map is None: raise ValueError("无法解析映射表编码")

        code_col = [c for c in df_map.columns if '代码' in c][0]
        name_col = [c for c in df_map.columns if '名称' in c][0]
        industry_col = [c for c in df_map.columns if '行业' in c][0]

        df_map = df_map[[code_col, name_col, industry_col]].copy()
        df_map.columns = ['代码', '名称', '板块']
        df_map['代码'] = clean_stock_code(df_map['代码'])
        df_map = df_map[df_map['代码'].str.match(r'^(60|688|00|300)\d{4}$')]
        df_map.dropna(inplace=True)
    except Exception as e:
        st.error(f"❌ 读取映射表失败: {e}")
        return
    progress_bar.progress(0.1)

    # --- [2/4] 带护盾的节点连接与快照拉取 ---
    status_text.text("📡 [2/4] 正在穿透云端网络，硬连接国内主节点...")

    # 开启防卡死护盾，最多等待5秒
    socket.setdefaulttimeout(5.0)
    client = None

    # 优先尝试自动测速，失败则启用备用静态IP池
    try:
        client = Quotes.factory(market='std')
    except:
        tdx_servers = [
            ('119.147.212.81', 7709), ('114.80.63.12', 7709),
            ('121.14.110.200', 7709), ('110.139.17.151', 7709),
            ('101.226.9.17', 7709), ('120.25.132.147', 7709)
        ]
        for server in tdx_servers:
            try:
                client = Quotes.factory(market='std', server=server)
                if client: break
            except:
                continue

    # 连接成功后恢复默认超时，防止拉取数据时中断
    socket.setdefaulttimeout(None)

    if client is None:
        st.error("❌ 云端网络被彻底阻断，无法直连任何国内通达信服务器。")
        progress_bar.empty()
        return

    # 关键变量定义，绝不漏掉
    symbol_list = df_map['代码'].tolist()
    all_quotes_dfs = []

    for i in range(0, len(symbol_list), 80):
        chunk = symbol_list[i:i + 80]
        try:
            res = client.quotes(symbol=chunk)
            if isinstance(res, pd.DataFrame) and not res.empty:
                all_quotes_dfs.append(res)
        except:
            pass
        progress_bar.progress(0.1 + 0.2 * (min(i + 80, len(symbol_list)) / len(symbol_list)))

    if not all_quotes_dfs:
        st.error("❌ 行情拉取彻底失败。")
        client.client.close()
        return

    df_quotes = pd.concat(all_quotes_dfs, ignore_index=True)
    df_quotes['代码'] = clean_stock_code(df_quotes['code'])

    # --- [3/4] 数据合并与初筛 ---
    status_text.text("🧠 [3/4] 正在合并数据，锁定异动种子池...")
    df_merged = pd.merge(df_map, df_quotes[['代码', 'price', 'last_close', 'vol', 'amount']], on='代码', how='inner')
    df_merged = df_merged[df_merged['last_close'] > 0].copy()
    df_merged['涨跌幅'] = (df_merged['price'] - df_merged['last_close']) / df_merged['last_close'] * 100

    df_surge_seed = df_merged[df_merged['涨跌幅'] >= 2.5].copy()
    df_pullback_seed = df_merged[
        (df_merged['涨跌幅'] <= 1.5) & (df_merged['涨跌幅'] >= -7.0) & (df_merged['amount'] >= 30000000)].copy()
    progress_bar.progress(0.4)

    # --- [4/4] 核心量价穿透 ---
    status_text.text("⚔️ [4/4] 启动游资智能漏斗：历史 K 线穿透中 (耗时约 1-2 分钟)...")
    surge_results = []
    pullback_results = []

    total_targets = len(df_surge_seed) + len(df_pullback_seed)
    processed_count = 0

    # 1. 穿透 龙抬头 (右侧主升)
    for idx, row in df_surge_seed.iterrows():
        code, today_amt, today_change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
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
        processed_count += 1
        if processed_count % 20 == 0: progress_bar.progress(0.4 + 0.6 * (processed_count / total_targets))

    # 2. 穿透 龙回头 (左侧洗盘)
    for idx, row in df_pullback_seed.iterrows():
        code, today_amt, today_change, name = row['代码'], row['amount'], row['涨跌幅'], row['名称']
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
                    df_k['pct_change'] = df_k['close'].pct_change() * 100
                    recent_window = df_k.iloc[-7:-2]
                    breakout_idx, breakout_ratio = None, 0
                    for i in range(len(recent_window)):
                        real_idx = recent_window.index[i]
                        if df_k['pct_change'].iloc[real_idx] >= 5.0:
                            past_15 = df_k['amount'].iloc[real_idx - 15: real_idx]
                            if len(past_15) == 15:
                                base_amt = past_15.drop(past_15.idxmax()).mean()
                                if base_amt > 0 and (df_k['amount'].iloc[real_idx] / base_amt) >= 1.5:
                                    breakout_idx = real_idx;
                                    breakout_ratio = df_k['amount'].iloc[real_idx] / base_amt
                    if breakout_idx is not None:
                        today_close, yest_close = df_k['close'].iloc[-1], df_k['close'].iloc[-2]
                        breakout_amt, yest_amt = df_k['amount'].iloc[breakout_idx], df_k['amount'].iloc[-2]
                        breakout_open = df_k['open'].iloc[breakout_idx]

                        vol_shrink = (today_amt < yest_amt) and (today_amt < breakout_amt * 0.65)
                        price_drop = (today_close < yest_close)
                        holding_support = (today_close > breakout_open)

                        if vol_shrink and price_drop and holding_support:
                            pullback_results.append({
                                '代码': code, '名称': name, '板块': row['板块'], '今日涨幅(%)': round(today_change, 2),
                                '回踩天数': f"{len(df_k) - 1 - breakout_idx} 天",
                                '今日量/爆发量': f"{round((today_amt / breakout_amt) * 100, 1)}%",
                                '爆发日强度': f"放量 {round(breakout_ratio, 1)}倍",
                                '今日成交额(亿)': round(today_amt / 1e8, 2)
                            })
        except:
            pass
        processed_count += 1
        if processed_count % 20 == 0: progress_bar.progress(min(1.0, 0.4 + 0.6 * (processed_count / total_targets)))

    client.client.close()

    # --- 结果保存与刷新 ---
    df_surge_final = pd.DataFrame(surge_results)
    if not df_surge_final.empty:
        df_surge_final['逻辑权重'] = df_surge_final['入选逻辑'].map(
            {'回调二波起涨': 1, '强势放量突破': 2, '趋势放量异动': 3})
        df_surge_final.sort_values(['逻辑权重', '涨跌幅(%)'], ascending=[True, False], inplace=True)
        df_surge_final.drop(columns=['逻辑权重'], inplace=True)
    df_surge_final.to_csv(SURGE_FILE, index=False)

    df_pullback_final = pd.DataFrame(pullback_results)
    if not df_pullback_final.empty:
        df_pullback_final.sort_values('今日量/爆发量', ascending=True, inplace=True)
    df_pullback_final.to_csv(PULLBACK_FILE, index=False)

    status_text.text("✅ 全市场扫描完成！数据已更新。")
    time.sleep(1)
    progress_bar.empty()
    status_text.empty()
    st.rerun()


# ==========================================
# 🎨 页面排版与数据加载模块
# ==========================================
@st.cache_data(ttl=60)
def load_data(path):
    if not os.path.exists(path): return pd.DataFrame(), "未知日期"
    try:
        mtime = os.path.getmtime(path)
        trade_date = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
        df = pd.read_csv(path, dtype={'代码': str})
        if '代码' in df.columns:
            df['代码'] = df['代码'].astype(str).str.zfill(6)
        return df, trade_date
    except:
        return pd.DataFrame(), "未知日期"


df_surge, surge_date = load_data(SURGE_FILE)
df_pullback, pb_date = load_data(PULLBACK_FILE)
trade_date = surge_date if surge_date != "未知日期" else pb_date

st.title("⚔️ 城门立木 · 量价关系双模复盘")
st.subheader(f"📅 交易日期：{trade_date}")
st.markdown("---")

# 侧边栏点火控制台
with st.sidebar:
    st.header("🎛️ 监控控制台")
    st.markdown("点击下方按钮，服务器将实时直连底层券商节点，进行全市场 5000 只标的的量价漏斗筛查。")
    if st.button("🚀 重新扫描全市场 (需 1-2 分钟)", type="primary"):
        run_quant_engine()

    st.markdown("---")
    st.caption(
        f"数据最后更新: {datetime.fromtimestamp(os.path.getmtime(SURGE_FILE)).strftime('%H:%M:%S') if os.path.exists(SURGE_FILE) else '无'}")

tab1, tab2 = st.tabs(["🚀 右侧主升：放量突破 (龙抬头)", "🐉 左侧低吸：缩量回踩 (龙回头)"])

with tab1:
    st.markdown("💡 **核心逻辑：** 实时锁定全市场今日【量价齐升】的异动标的。")
    if not df_surge.empty:
        df_surge.sort_values(by='增量倍数', ascending=False, inplace=True)
        st.dataframe(df_surge, use_container_width=True, hide_index=True, height=600,
                     column_config={
                         "代码": st.column_config.TextColumn("代码"),
                         "名称": st.column_config.TextColumn("名称"),
                         "入选逻辑": st.column_config.TextColumn("🔥 资金意图"),
                         "涨跌幅(%)": st.column_config.NumberColumn("涨跌幅(%)", format="%.2f %%"),
                         "增量倍数": st.column_config.NumberColumn("📈 增量倍数", format="%.2f x"),
                         "今日成交额(亿)": st.column_config.NumberColumn("今日成交额(亿)", format="%.2f"),
                         "常态均额(亿)": st.column_config.NumberColumn("常态均额(亿)", format="%.2f"),
                     })
    else:
        st.info("📉 暂无数据或未扫描到标的。请点击左侧【🚀 重新扫描全市场】按钮！")

with tab2:
    st.markdown("💡 **核心逻辑：** 寻找近 2-6 天内放量大涨，且最近 2-3 天呈现 **持续缩量下跌（洗盘）** 的潜伏标的。")
    if not df_pullback.empty:
        st.dataframe(df_pullback, use_container_width=True, hide_index=True, height=600,
                     column_config={
                         "代码": st.column_config.TextColumn("代码"),
                         "名称": st.column_config.TextColumn("名称"),
                         "板块": st.column_config.TextColumn("板块"),
                         "今日涨幅(%)": st.column_config.NumberColumn("今日回踩跌幅", format="%.2f %%"),
                         "回踩天数": st.column_config.TextColumn("⏳ 回踩天数"),
                         "今日量/爆发量": st.column_config.TextColumn("📉 缩量程度 (今日/爆发日)"),
                         "爆发日强度": st.column_config.TextColumn("💥 前期特征"),
                         "今日成交额(亿)": st.column_config.NumberColumn("今日地量(亿)", format="%.2f"),
                     })
    else:
        st.info("📉 暂无回踩标的。请点击左侧【🚀 重新扫描全市场】按钮！")