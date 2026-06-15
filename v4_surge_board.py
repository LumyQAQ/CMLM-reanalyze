import streamlit as st
import pandas as pd
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# ==========================================
# 🎯 智能路径配置
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SURGE_TREND_FILE = os.path.join(BASE_DIR, 'v4_surge_trend.csv')
SURGE_RANGE_FILE = os.path.join(BASE_DIR, 'v4_surge_range.csv')
PULLBACK_FILE = os.path.join(BASE_DIR, 'v4_pullback_candidates.csv')

st.set_page_config(page_title="量价关系三模复盘", layout="wide", page_icon="⚔️")


@st.cache_data(ttl=60)
def load_data(path):
    if not os.path.exists(path): return pd.DataFrame(), "等待数据同步..."
    try:
        mtime = os.path.getmtime(path)
        local_time = datetime.fromtimestamp(mtime, tz=ZoneInfo("Asia/Shanghai"))
        trade_date = local_time.strftime('%Y-%m-%d %H:%M')

        if os.path.getsize(path) == 0: return pd.DataFrame(), trade_date

        df = pd.read_csv(path, dtype={'代码': str})
        if '代码' in df.columns: df['代码'] = df['代码'].astype(str).str.zfill(6)
        return df, trade_date
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), trade_date
    except Exception as exc:
        return pd.DataFrame(), f"解析异常：{exc}"


def momentum_columns():
    return {
        "3日涨幅(%)": st.column_config.NumberColumn("3日涨幅", format="%.2f %%"),
        "5日涨幅(%)": st.column_config.NumberColumn("5日涨幅", format="%.2f %%"),
    }


df_trend, trend_date = load_data(SURGE_TREND_FILE)
df_range, range_date = load_data(SURGE_RANGE_FILE)
df_pullback, pb_date = load_data(PULLBACK_FILE)

# 获取有效日期
valid_dates = [d for d in [trend_date, range_date, pb_date] if not d.startswith(("等待数据同步", "解析异常"))]
trade_date = max(valid_dates) if valid_dates else "等待数据同步..."

st.title("⚔️ 城门立木 · 量价关系全景复盘")
st.subheader(f"📅 最新数据更新时间：{trade_date}")
st.markdown("---")

with st.sidebar:
    st.header("🎛️ 监控控制台")
    st.success("✅ V4.6 三频段独立引擎运行中")
    st.markdown("""
    **💡 系统模块说明：**
    1. **趋势放量：** 连升接力、强势趋势股。
    2. **区间破位：** 横盘洗盘后，放量突破前高（高胜率波段）。
    3. **缩量回踩：** 爆发后的极致缩量洗盘低吸点。
    """)
    if st.button("🔄 刷新网页数据"):
        st.cache_data.clear()
        st.rerun()

tab1, tab2, tab3 = st.tabs(["🚀 右侧趋势：成交额放量", "🏆 右侧结构：区间放量破位", "🐉 左侧低吸：突破后缩量回踩"])

with tab1:
    st.markdown("💡 **核心逻辑：** 寻找趋势内连升、接力、二波起涨的放量突破标的。")
    if not df_trend.empty:
        st.dataframe(df_trend, use_container_width=True, hide_index=True, height=600,
                     column_config={
                         "代码": st.column_config.TextColumn("代码"), "名称": st.column_config.TextColumn("名称"),
                         "逻辑标签": st.column_config.TextColumn("🔥 形态意图"),
                         "涨跌幅(%)": st.column_config.NumberColumn("涨跌幅", format="%.2f %%"),
                         **momentum_columns(),
                         "增量倍数": st.column_config.NumberColumn("📈 增量倍数", format="%.2f x"),
                         "今日成交额(亿)": st.column_config.NumberColumn("今日(含预估)成交额", format="%.2f"),
                         "常态均额(亿)": st.column_config.NumberColumn("常态均额", format="%.2f"),
                     })
    else:
        st.info("📉 今日无符合趋势放量条件的标的。")

with tab2:
    st.markdown(
        "💡 **核心逻辑：** 寻找在一段时间内形成震荡区间后，今日以放量大阳线 **强力突破前期历史高点** 的箱体破位标的。")
    if not df_range.empty:
        st.dataframe(df_range, use_container_width=True, hide_index=True, height=600,
                     column_config={
                         "代码": st.column_config.TextColumn("代码"), "名称": st.column_config.TextColumn("名称"),
                         "突破类型": st.column_config.TextColumn("⚔️ 破位结构"),
                         "涨跌幅(%)": st.column_config.NumberColumn("涨跌幅", format="%.2f %%"),
                         **momentum_columns(),
                         "增量倍数": st.column_config.NumberColumn("📈 增量倍数", format="%.2f x"),
                         "今日成交额(亿)": st.column_config.NumberColumn("今日(含预估)成交额", format="%.2f"),
                     })
    else:
        st.info("📉 今日无符合区间破位条件的标的。")

with tab3:
    st.markdown(
        "💡 **核心逻辑：** 寻找近几天内曾放量大涨，且最近 2-3 天呈现 **持续极致缩量下跌（洗盘且未破防线）** 的潜伏标的。")
    if not df_pullback.empty:
        st.dataframe(df_pullback, use_container_width=True, hide_index=True, height=600,
                     column_config={
                         "代码": st.column_config.TextColumn("代码"), "名称": st.column_config.TextColumn("名称"),
                         "今日涨幅(%)": st.column_config.NumberColumn("今日涨跌幅", format="%.2f %%"),
                         **momentum_columns(),
                         "今日量/爆发量": st.column_config.TextColumn("📉 缩量程度 (含预估)"),
                         "爆发日强度": st.column_config.TextColumn("💥 前期特征"),
                     })
    else:
        st.info("📉 今日无符合缩量回踩条件的标的。")
