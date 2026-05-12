import streamlit as st
import pandas as pd
import os
from datetime import datetime

# ==========================================
# 🎯 数据源配置
# ==========================================
SURGE_PATH = '/CMLM V4.0/v4_volume_surge.csv'
PULLBACK_PATH = '/CMLM V4.0/v4_pullback_candidates.csv'

st.set_page_config(page_title="量价关系双模复盘", layout="wide", page_icon="⚔️")

@st.cache_data(ttl=60)
def load_data(path):
    if not os.path.exists(path):
        return pd.DataFrame(), "未知日期"
    mtime = os.path.getmtime(path)
    trade_date = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
    try:
        df = pd.read_csv(path, dtype={'代码': str})
        if '代码' in df.columns:
            df['代码'] = df['代码'].astype(str).str.zfill(6)
        return df, trade_date
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), trade_date

df_surge, surge_date = load_data(SURGE_PATH)
df_pullback, pb_date = load_data(PULLBACK_PATH)
trade_date = surge_date if surge_date != "未知日期" else pb_date

# ==========================================
# 🎨 页面排版
# ==========================================
st.title("⚔️ 城门立木 · 量价关系双模复盘")
st.subheader(f"📅 交易日期：{trade_date}")
st.markdown("---")

# 🌟 建立双模标签页
tab1, tab2 = st.tabs(["🚀 右侧主升：放量突破 (龙抬头)", "🐉 左侧低吸：缩量回踩 (龙回头)"])

# ------------------------------------------
# Tab 1: 龙抬头 (爆破)
# ------------------------------------------
with tab1:
    st.markdown("💡 **核心逻辑：** 实时锁定全市场今日【量价齐升】的异动标的。")
    if not df_surge.empty:
        df_surge.sort_values(by='增量倍数', ascending=False, inplace=True)
        st.dataframe(
            df_surge, use_container_width=True, hide_index=True, height=600,
            column_config={
                "代码": st.column_config.TextColumn("代码"),
                "名称": st.column_config.TextColumn("名称"),
                "入选逻辑": st.column_config.TextColumn("🔥 资金意图"),
                "涨跌幅(%)": st.column_config.NumberColumn("涨跌幅(%)", format="%.2f %%"),
                "增量倍数": st.column_config.NumberColumn("📈 增量倍数", format="%.2f x"),
                "今日成交额(亿)": st.column_config.NumberColumn("今日成交额(亿)", format="%.2f"),
                "常态均额(亿)": st.column_config.NumberColumn("常态均额(亿)", format="%.2f"),
            }
        )
    else:
        st.info("📉 今日未扫描到符合放量突破的标的。")

# ------------------------------------------
# Tab 2: 龙回头 (缩量洗盘)
# ------------------------------------------
with tab2:
    st.markdown("💡 **核心逻辑：** 寻找近 2-6 天内放量大涨，且最近 2-3 天呈现 **持续缩量下跌（洗盘）**，抛压濒临枯竭的潜伏标的。")
    if not df_pullback.empty:
        # 强制按今日量/爆发量的缩减程度排序（缩得越极致越好）
        st.dataframe(
            df_pullback, use_container_width=True, hide_index=True, height=600,
            column_config={
                "代码": st.column_config.TextColumn("代码"),
                "名称": st.column_config.TextColumn("名称"),
                "板块": st.column_config.TextColumn("板块"),
                "今日涨幅(%)": st.column_config.NumberColumn("今日回踩跌幅", format="%.2f %%"),
                "回踩天数": st.column_config.TextColumn("⏳ 回踩天数"),
                "今日量/爆发量": st.column_config.TextColumn("📉 缩量程度 (今日/爆发日)"),
                "爆发日强度": st.column_config.TextColumn("💥 前期爆发特征"),
                "今日成交额(亿)": st.column_config.NumberColumn("今日地量(亿)", format="%.2f"),
            }
        )
    else:
        st.info("📉 今日未扫描到完美的缩量回踩标的。")

st.caption(f"数据更新时间：{datetime.fromtimestamp(os.path.getmtime(SURGE_PATH)).strftime('%Y-%m-%d %H:%M:%S') if os.path.exists(SURGE_PATH) else '无'}")