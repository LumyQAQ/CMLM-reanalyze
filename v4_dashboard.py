from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "v4_rrg_data.csv"
SURGE_PATH = BASE_DIR / "v4_surge_trend.csv"

st.set_page_config(page_title="城门立木 V4", layout="wide", page_icon="🎯")


@st.cache_data(ttl=60)
def load_data(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype={"代码": str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    except Exception as exc:
        st.warning(f"数据解析异常：{path.name} - {exc}")
        return pd.DataFrame()

    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str).str.zfill(6)
    return df


def ensure_rrg_columns(df: pd.DataFrame) -> pd.DataFrame:
    text_defaults = {
        "1日先锋": "等待引擎更新...",
        "3日动能": "等待引擎更新...",
        "5日趋势": "等待引擎更新...",
        "核心中军": "等待引擎更新...",
    }
    numeric_defaults = {
        "amount": 0,
        "涨跌幅": 0,
        "相对强弱(X)": 0,
        "动量加速度(Y)": 1,
        "突破动能得分": 1,
        "突破标的": 0,
    }
    for column, value in text_defaults.items():
        if column not in df.columns:
            df[column] = value
    for column, value in numeric_defaults.items():
        if column not in df.columns:
            df[column] = value
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(value)
    return df


df = load_data(CSV_PATH)
df_surge = load_data(SURGE_PATH)

if df.empty:
    st.error(f"找不到雷达数据：{CSV_PATH.name}。请先运行 v4_engine.py 或同步最新数据。")
    st.stop()

df = ensure_rrg_columns(df)
df["总成交额(亿)"] = (df["amount"] / 100000000).round(2)
df["涨跌幅"] = df["涨跌幅"].round(2)
df["相对强弱(X)"] = df["相对强弱(X)"].round(2)
df["动量加速度(Y)"] = df["动量加速度(Y)"].round(3)
df["突破动能得分"] = df["突破动能得分"].clip(lower=1)

top_amount = df.nlargest(20, "amount")["板块"].tolist() if "板块" in df.columns else []
df["展示标签"] = df.apply(
    lambda row: row["板块"] if (row.get("板块") in top_amount or row["突破标的"] >= 2) else "",
    axis=1,
)


def get_quadrant_color(x: float, y: float) -> str:
    if x >= 0 and y >= 1.0:
        return "👑 强势领军 (红)"
    if x < 0 and y >= 1.0:
        return "🛡️ 资金潜伏 (蓝)"
    if x < 0 and y < 1.0:
        return "❄️ 弱势滞后 (绿)"
    return "⚠️ 动能衰退 (黄)"


df["阵营"] = df.apply(lambda r: get_quadrant_color(r["相对强弱(X)"], r["动量加速度(Y)"]), axis=1)

color_map = {
    "👑 强势领军 (红)": "#FF3B30",
    "🛡️ 资金潜伏 (蓝)": "#007AFF",
    "❄️ 弱势滞后 (绿)": "#34C759",
    "⚠️ 动能衰退 (黄)": "#FFCC00",
}

st.title("🎯 城门立木 · 游资 RRG 四象限雷达 (V4)")
st.markdown("宏观看板块强弱和资金动量，微观看下方量价异动池。")

fig = px.scatter(
    df,
    x="相对强弱(X)",
    y="动量加速度(Y)",
    size="突破动能得分",
    color="阵营",
    text="展示标签",
    color_discrete_map=color_map,
    size_max=60,
    height=750,
    custom_data=["板块", "涨跌幅", "总成交额(亿)", "突破标的", "1日先锋", "3日动能", "5日趋势", "核心中军"],
)

fig.update_traces(
    textposition="top center",
    hovertemplate=(
        "<b><span style='font-size:22px'>🎯 %{customdata[0]}</span></b><br><br>"
        "⚔️ <b>微观穿透面板:</b><br>"
        "🟢 <b>1日先锋:</b> <span style='color:#FF3B30'><b>%{customdata[4]}</b></span><br>"
        "🔥 <b>3日动能:</b> <span style='color:#FF9500'>%{customdata[5]}</span><br>"
        "📈 <b>5日趋势:</b> <span style='color:#007AFF'>%{customdata[6]}</span><br>"
        "🐘 <b>核心中军:</b> <span style='color:#AF52DE'><b>%{customdata[7]}</b></span><br><br>"
        "📊 板块等权涨幅: %{customdata[1]}%<br>"
        "💰 板块总成交额: %{customdata[2]} 亿<br>"
        "<extra></extra>"
    ),
    marker=dict(line=dict(width=1, color="rgba(0,0,0,0.4)")),
    textfont=dict(size=14, weight="bold", color="#222222"),
)

fig.add_vline(x=0, line_dash="solid", line_width=2, line_color="rgba(0,0,0,0.5)")
fig.add_hline(y=1.0, line_dash="solid", line_width=2, line_color="rgba(0,0,0,0.5)")

x_max, x_min = df["相对强弱(X)"].max(), df["相对强弱(X)"].min()
y_max, y_min = df["动量加速度(Y)"].max(), df["动量加速度(Y)"].min()
fig.add_annotation(x=x_max * 0.8, y=y_max * 0.9, text="👑 领军区", showarrow=False, font=dict(color="#FF3B30", size=24), opacity=0.15)
fig.add_annotation(x=x_min * 0.8, y=y_max * 0.9, text="🛡️ 修复区", showarrow=False, font=dict(color="#007AFF", size=24), opacity=0.15)
fig.add_annotation(x=x_min * 0.8, y=y_min * 0.8, text="❄️ 滞后区", showarrow=False, font=dict(color="#34C759", size=24), opacity=0.15)
fig.add_annotation(x=x_max * 0.8, y=y_min * 0.8, text="⚠️ 衰退区", showarrow=False, font=dict(color="#FFCC00", size=24), opacity=0.15)

fig.update_layout(
    xaxis_title="◀ 弱势跑输  ------  相对强弱 (超额收益/X)  ------  强势碾压 ▶",
    yaxis_title="动能衰退  ------  资金动量加速度 (Y)  ------  量能爆发",
    plot_bgcolor="#F4F6F9",
    xaxis=dict(zeroline=False, showgrid=True, gridcolor="white", gridwidth=1.5),
    yaxis=dict(zeroline=False, showgrid=True, gridcolor="white", gridwidth=1.5),
    hoverlabel=dict(bgcolor="white", font_size=15, font_family="Arial"),
)

st.plotly_chart(fig, use_container_width=True)

st.markdown("---")
st.markdown("### 🚀 今日【量价齐升】爆破标的")

if not df_surge.empty:
    st.dataframe(
        df_surge,
        use_container_width=True,
        hide_index=True,
        height=400,
        column_config={
            "代码": st.column_config.TextColumn("代码"),
            "名称": st.column_config.TextColumn("名称"),
            "逻辑标签": st.column_config.TextColumn("🔥 资金意图"),
            "涨跌幅(%)": st.column_config.NumberColumn("涨跌幅", format="%.2f %%"),
            "3日涨幅(%)": st.column_config.NumberColumn("3日涨幅", format="%.2f %%"),
            "5日涨幅(%)": st.column_config.NumberColumn("5日涨幅", format="%.2f %%"),
            "增量倍数": st.column_config.NumberColumn("📈 增量倍数", format="%.2f x"),
            "今日成交额(亿)": st.column_config.NumberColumn("今日成交额", format="%.2f"),
            "常态均额(亿)": st.column_config.NumberColumn("常态均额", format="%.2f"),
        },
    )
else:
    st.info("今日未读取到趋势放量池数据。")
