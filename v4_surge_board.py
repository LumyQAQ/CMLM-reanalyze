import streamlit as st
import pandas as pd
import os
from datetime import datetime

# ==========================================
# 🎯 智能路径配置
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SURGE_FILE = os.path.join(BASE_DIR, 'v4_volume_surge.csv')
PULLBACK_FILE = os.path.join(BASE_DIR, 'v4_pullback_candidates.csv')

st.set_page_config(page_title="量价关系双模复盘", layout="wide", page_icon="⚔️")


# ==========================================
# 🎨 页面排版与极速数据加载模块 (强固防空壳版)
# ==========================================
@st.cache_data(ttl=60)
def load_data(path):
    if not os.path.exists(path):
        return pd.DataFrame(), "等待数据同步..."
    try:
        mtime = os.path.getmtime(path)
        trade_date = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')

        # 🛡️ 防御机制：如果是 0 字节的空文件，直接返回空表，绝不报错
        if os.path.getsize(path) == 0:
            return pd.DataFrame(), trade_date

        df = pd.read_csv(path, dtype={'代码': str})
        if '代码' in df.columns:
            df['代码'] = df['代码'].astype(str).str.zfill(6)
        return df, trade_date
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), trade_date  # 捕获 pandas 空数据报错
    except Exception as e:
        return pd.DataFrame(), "解析异常"


# 加载静态数据
df_surge, surge_date = load_data(SURGE_FILE)
df_pullback, pb_date = load_data(PULLBACK_FILE)
trade_date = surge_date if surge_date not in ["等待数据同步...", "解析异常"] else pb_date

st.title("⚔️ 城门立木 · 量价关系双模复盘")
st.subheader(f"📅 最新数据更新时间：{trade_date}")
st.markdown("---")

with st.sidebar:
    st.header("🎛️ 监控控制台")
    st.success("✅ 云端状态：前后端解耦模式运行中")
    st.markdown("""
    **💡 系统架构说明：**
    - 本网页为纯前端展示端，加载速度已优化至极速。
    - 每日收盘数据由本地 Mac 高性能引擎 (`v4_engine.py`) 扫板后自动同步。
    """)
    if st.button("🔄 刷新网页数据"):
        st.cache_data.clear()
        st.rerun()

tab1, tab2 = st.tabs(["🚀 右侧主升：放量突破 (龙抬头)", "🐉 左侧低吸：缩量回踩 (龙回头)"])

with tab1:
    st.markdown("💡 **核心逻辑：** 锁定全市场今日【量价齐升】的异动标的。")
    if not df_surge.empty:
        if '增量倍数' in df_surge.columns:
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
        st.info("📉 暂无数据（今日无符合条件的标的，或等待同步）。")

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
        st.info("📉 暂无回踩标的（今日无符合条件的标的，或等待同步）。")