import streamlit as st
import pandas as pd
import os
import numpy as np
from datetime import datetime
from mootdx.quotes import Quotes
import time

# ==========================================
# 🎯 智能路径配置 (自动识别云端与本地)
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

    # --- [1/4] 加载映射表 ---
    status_text.text("⏳ [1/4] 正在加载底层板块映射表...")
    if not os.path.exists(MAP_FILE):
        st.error(f"❌ 找不到底层映射表 {MAP_FILE}，请确保已将 stock_to_sector.csv 上传至 GitHub 根目录！")
        return
    try:
        # 云端环境通常对编码敏感，尝试多重解析
        encodings = ['utf-16', 'gbk', 'utf-8-sig', 'utf-8']
        df_map = None
        for enc in encodings:
            try:
                temp_df = pd.read_csv(MAP_FILE, encoding=enc, sep=None, engine='python')
                if len(temp_df.columns) > 1: df_map = temp_df; break
            except:
                pass

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

    # --- [2/4] 硬连接通达信节点 ---
    status_text.text("📡 [2/4] 正在穿透云端网络，硬连接国内主节点...")
    tdx_servers = [
        ('119.147.212.81', 7709), ('114.80.63.12', 7709),
        ('121.14.110.200', 7709), ('110.139.17.151', 7709)
    ]
    client = None
    for server in tdx_servers:
        try:
            client = Quotes.factory(market='std', server=server)
            break
        except:
            continue

    if client is None:
        st.error("❌ 云端网络被阻断，无法连接国内服务器。")
        progress_bar.empty();
        return

    symbol_list = df_map['代码'].tolist()
    all_quotes_dfs = []

    for i in range(0, len(symbol_list), 80):
        chunk = symbol_list[i:i + 80]
        try:
            res = client.quotes(symbol=chunk)
            if isinstance(res, pd.DataFrame) and not res.empty: all_quotes_dfs.append(res)
        except:
            pass
        progress_bar.progress(0.1 + 0.2 * (min(i + 80, len(symbol_list)) / len(symbol_list)))

    if not all_quotes_dfs:
        st.error("❌ 行情拉取失败。");
        client.client.close();
        return

    df_quotes = pd.concat(all_quotes_dfs, ignore_index=True)
    df_quotes['代码'] = clean_stock_code(df_quotes['code'])

    # --- [3/4] 数据预处理 ---
    status_text.text("🧠 [3/4] 正在合并数据，锁定异动种子池...")
    df_merged = pd.merge(df_map, df_quotes[['代码', 'price', 'last_close', 'vol', 'amount']], on='代码', how='inner')
    df_merged = df_merged[df_merged['last_close'] > 0].copy()
    df_merged['涨跌幅'] = (df_merged['price'] - df_merged['last_close']) / df_merged['last_close'] * 100

    df_surge_seed = df_merged[df_merged['涨跌幅'] >= 2.5].copy()
    df_pullback_seed = df_merged[
        (df_merged['涨跌幅'] <= 1.5) & (df_merged['涨跌幅'] >= -7.0) & (df_merged['amount'] >= 30000000)].copy()
    progress_bar.progress(0.4)

    # --- [4/4] 核心量价穿透 ---
    status_text.text("⚔️ [4/4] 启动智能漏斗：历史 K 线穿透中...")
    surge_results, pullback_results = [], []
    total_targets = len(df_surge_seed) + len(df_pullback_seed)
    processed = 0

    # 龙抬头扫描
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
                    past_15 = df_k['amount'].iloc[-16:-1]
                    true_base = past_15.drop(past_15.idxmax()).mean()
                    has_breakout = (df_k['pct_change'].iloc[-16:-1] >= 8.0).any()
                    if true_base > 0:
                        ratio = today_amt / true_base
                        cond_a, cond_b, cond_c = (today_change >= 5.0 and ratio >= 1.35), (
                                    3.0 <= today_change < 5.0 and ratio >= 1.25), (
                                    has_breakout and today_change >= 2.5 and ratio >= 1.1)
                        if cond_a or cond_b or cond_c:
                            surge_results.append(
                                {'代码': code, '名称': name, '板块': row['板块'], '涨跌幅(%)': round(today_change, 2),
                                 '增量倍数': round(ratio, 2), '今日成交额(亿)': round(today_amt / 1e8, 2),
                                 '常态均额(亿)': round(true_base / 1e8, 2),
                                 '入选逻辑': "回调二波起涨" if (cond_c and not cond_a) else (
                                     "趋势异动" if cond_b else "强势突破")})
        except:
            pass
        processed += 1
        if processed % 10 == 0: progress_bar.progress(0.4 + 0.6 * (processed / total_targets))

    # 龙回头扫描
    for idx, row in df_pullback_seed.iterrows():
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
                    recent_window = df_k.iloc[-7:-2]
                    breakout_idx = None
                    for i in range(len(recent_window)):
                        ri = recent_window.index[i]
                        if df_k['pct_change'].iloc[ri] >= 5.0:
                            past_15 = df_k['amount'].iloc[ri - 15:ri]
                            if len(past_15) == 15 and (
                                    df_k['amount'].iloc[ri] / past_15.drop(past_15.idxmax()).mean()) >= 1.5:
                                breakout_idx = ri;
                                break
                    if breakout_idx:
                        if today_amt < df_k['amount'].iloc[-2] and today_amt < df_k['amount'].iloc[
                            breakout_idx] * 0.65 and df_k['close'].iloc[-1] < df_k['close'].iloc[-2] and \
                                df_k['close'].iloc[-1] > df_k['open'].iloc[breakout_idx]:
                            pullback_results.append(
                                {'代码': code, '名称': name, '板块': row['板块'], '今日涨幅(%)': round(today_change, 2),
                                 '回踩天数': f"{len(df_k) - 1 - breakout_idx} 天",
                                 '今日量/爆发量': f"{round((today_amt / df_k['amount'].iloc[breakout_idx]) * 100, 1)}%",
                                 '爆发日强度': "放量大涨", '今日成交额(亿)': round(today_amt / 1e8, 2)})
        except:
            pass
        processed += 1
        if processed % 10 == 0: progress_bar.progress(min(1.0, 0.4 + 0.6 * (processed / total_targets)))

    client.client.close()
    pd.DataFrame(surge_results).to_csv(SURGE_FILE, index=False)
    pd.DataFrame(pullback_results).to_csv(PULLBACK_FILE, index=False)
    status_text.text("✅ 扫描完成！数据已同步至云端存储。")
    time.sleep(1);
    progress_bar.empty();
    status_text.empty();
    st.rerun()


# ==========================================
# 🎨 页面展示模块
# ==========================================
@st.cache_data(ttl=60)
def get_data(path):
    if not os.path.exists(path): return pd.DataFrame(), "未知日期"
    try:
        df = pd.read_csv(path, dtype={'代码': str})
        if '代码' in df.columns: df['代码'] = df['代码'].str.zfill(6)
        return df, datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')
    except:
        return pd.DataFrame(), "解析失败"


df_s, s_date = get_data(SURGE_FILE)
df_p, p_date = get_data(PULLBACK_FILE)
t_date = s_date if s_date != "未知日期" else p_date

st.title("⚔️ 城门立木 · 量价关系双模复盘")
st.subheader(f"📅 交易日期：{t_date}")

with st.sidebar:
    st.header("🎛️ 控制台")
    if st.button("🚀 重新扫描全市场 (1-2分钟)", type="primary"):
        run_quant_engine()
    st.markdown("---")
    st.caption(f"底层节点: 中国电信主干网\n云端状态: 运行中")

tab1, tab2 = st.tabs(["🚀 右侧主升 (龙抬头)", "🐉 左侧低吸 (龙回头)"])

with tab1:
    if not df_s.empty:
        st.dataframe(df_s.sort_values('增量倍数', ascending=False), use_container_width=True, hide_index=True,
                     height=600, column_config={"代码": "代码", "增量倍数": st.column_config.NumberColumn("📈 增量倍数",
                                                                                                          format="%.2f x"),
                                                "今日成交额(亿)": "今日额(亿)"})
    else:
        st.info("点击左侧按钮开始扫描数据。")

with tab2:
    if not df_p.empty:
        st.dataframe(df_p, use_container_width=True, hide_index=True, height=600,
                     column_config={"今日量/爆发量": "📉 缩量程度"})
    else:
        st.info("暂无回踩标的。")