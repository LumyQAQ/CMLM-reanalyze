import streamlit as st
import pandas as pd
import os
import numpy as np
from datetime import datetime
from mootdx.quotes import Quotes
import time

# ==========================================
# 🎯 智能路径配置 (适配云端与本地)
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAP_FILE = os.path.join(BASE_DIR, 'stock_to_sector.csv')
SURGE_FILE = os.path.join(BASE_DIR, 'v4_volume_surge.csv')
PULLBACK_FILE = os.path.join(BASE_DIR, 'v4_pullback_candidates.csv')

st.set_page_config(page_title="城门立木量价复盘", layout="wide", page_icon="⚔️")


# ==========================================
# ⚙️ 核心引擎逻辑 (整合进网页)
# ==========================================
def run_quant_engine():
    """在云端执行全市场扫描"""
    progress_bar = st.progress(0)
    status_text = st.empty()

    status_text.text("⏳ 正在加载映射表...")
    try:
        # 云端通常使用 utf-8 或 gbk
        df_map = pd.read_csv(MAP_FILE, encoding='utf-16', sep=None, engine='python')
        code_col = [c for c in df_map.columns if '代码' in c][0]
        name_col = [c for c in df_map.columns if '名称' in c][0]
        industry_col = [c for c in df_map.columns if '行业' in c][0]
        df_map = df_map[[code_col, name_col, industry_col]].copy()
        df_map.columns = ['代码', '名称', '板块']
        df_map['代码'] = df_map['代码'].astype(str).str.extract(r'(\d{6})')[0]
        df_map = df_map[df_map['代码'].str.match(r'^(60|688|00|300)\d{4}$')]
        df_map.dropna(inplace=True)
    except Exception as e:
        st.error(f"映射表加载失败: {e}")
        return

        # [2/4] 拉取全市场快照
        status_text.text("📡 [2/4] 正在穿透云端网络，硬连接通达信主节点...")

        # 🛡️ 终极防线：准备多个高可用国内节点，跳过云端无法执行的 ping 测速
        tdx_servers = [
            ('119.147.212.81', 7709),  # 深圳电信
            ('114.80.63.12', 7709),  # 上海电信
            ('121.14.110.200', 7709),  # 广东电信
            ('110.139.17.151', 7709)  # 湖北电信
        ]

        client = None
        for server in tdx_servers:
            try:
                # 强制传入 server 参数，让 mootdx 闭嘴，直接连！
                client = Quotes.factory(market='std', server=server)
                break  # 只要连上一个就跳出循环
            except:
                continue

        if client is None:
            st.error("❌ 云端网络被彻底阻断，无法直连任何国内通达信服务器。")
            progress_bar.empty()
            return

        symbol_list = df_map['代码'].tolist()
    all_quotes = []

    # 云端建议分块稍大以提高速度
    for i in range(0, len(symbol_list), 80):
        chunk = symbol_list[i:i + 80]
        res = client.quotes(symbol=chunk)
        if isinstance(res, pd.DataFrame): all_quotes.append(res)
        progress_bar.progress(min(0.5, (i / len(symbol_list))))

    df_quotes = pd.concat(all_quotes)
    df_quotes['代码'] = df_quotes['code'].astype(str).str.extract(r'(\d{6})')[0]
    df_merged = pd.merge(df_map, df_quotes, on='代码')
    df_merged['涨跌幅'] = (df_merged['price'] - df_merged['last_close']) / df_merged['last_close'] * 100

    status_text.text("🐉 正在执行量价漏斗穿透...")
    # 这里保留你最核心的 V4.4 逻辑 (简化版展示)
    surge_results = []
    pullback_results = []

    # 模拟穿透 (实际部署时这里放入你完整的 [4/5] 和 [5/5] 逻辑)
    # ... (为了篇幅，此处省略具体计算过程，结构同你本地脚本)

    # 保存结果
    pd.DataFrame(surge_results).to_csv(SURGE_FILE, index=False)
    pd.DataFrame(pullback_results).to_csv(PULLBACK_FILE, index=False)

    status_text.text("✅ 全市场扫描完成！")
    progress_bar.empty()
    st.rerun()


# ==========================================
# 🎨 网页 UI 渲染
# ==========================================
st.title("⚔️ 城门立木 · 量价关系双模复盘")

# 侧边栏点火按钮
with st.sidebar:
    st.header("控制台")
    if st.button("🚀 重新扫描全市场 (需1分钟)"):
        run_quant_engine()

    if os.path.exists(SURGE_FILE):
        mtime = os.path.getmtime(SURGE_FILE)
        st.write(f"📅 最后更新: {datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')}")


# 数据加载逻辑
def load_local_data(path):
    if not os.path.exists(path): return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={'代码': str})
    except:
        return pd.DataFrame()


df_surge = load_local_data(SURGE_FILE)
df_pullback = load_local_data(PULLBACK_FILE)

tab1, tab2 = st.tabs(["🚀 右侧主升 (龙抬头)", "🐉 左侧低吸 (龙回头)"])

with tab1:
    if not df_surge.empty:
        df_surge['代码'] = df_surge['代码'].str.zfill(6)
        st.dataframe(df_surge.sort_values('增量倍数', ascending=False), use_container_width=True, hide_index=True)
    else:
        st.info("点击左侧按钮开始扫描数据")

with tab2:
    if not df_pullback.empty:
        df_pullback['代码'] = df_pullback['代码'].str.zfill(6)
        st.dataframe(df_pullback, use_container_width=True, hide_index=True)
    else:
        st.info("暂无回踩标的")