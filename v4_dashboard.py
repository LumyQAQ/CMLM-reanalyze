import streamlit as st
import pandas as pd
import plotly.express as px
import os

# ==========================================
# 🎯 数据源配置
# ==========================================
CSV_PATH = '/CMLM V4.0/v4_rrg_data.csv'
SURGE_PATH = '/CMLM V4.0/v4_volume_surge.csv'

st.set_page_config(page_title="城门立木 V4", layout="wide", page_icon="🎯")


@st.cache_data(ttl=60)
def load_data(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        # 🛡️ 核心修复：明确指定 '代码' 列必须按字符串(str)读取，禁止自动转为数字！
        df = pd.read_csv(path, dtype={'代码': str})

        # 🛡️ 双重保险：强制转换成字符串，并在左侧用 '0' 补齐至 6 位数
        if '代码' in df.columns:
            df['代码'] = df['代码'].astype(str).str.zfill(6)

        return df
    except pd.errors.EmptyDataError:
        return pd.DataFrame()

df = load_data(CSV_PATH)
df_surge = load_data(SURGE_PATH)

if df.empty:
    st.error(f"🚨 找不到雷达数据：{CSV_PATH}\n请先运行 v4_engine.py！")
    st.stop()

# 容错与格式化
expected_columns = ['1日先锋', '3日动能', '5日趋势', '核心中军', '突破标的']
for col in expected_columns:
    if col not in df.columns:
        df[col] = "等待引擎更新..."

df['总成交额(亿)'] = (df['amount'] / 100000000).round(2)
df['涨跌幅'] = df['涨跌幅'].round(2)
df['相对强弱(X)'] = df['相对强弱(X)'].round(2)
df['动量加速度(Y)'] = df['动量加速度(Y)'].round(3)

top_amount = df.nlargest(20, 'amount')['板块'].tolist()
df['展示标签'] = df.apply(lambda row: row['板块'] if (row['板块'] in top_amount or row['突破标的'] >= 2) else "", axis=1)

def get_quadrant_color(x, y):
    if x >= 0 and y >= 1.0: return '👑 强势领军 (红)'
    elif x < 0 and y >= 1.0: return '🛡️ 资金潜伏 (蓝)'
    elif x < 0 and y < 1.0: return '❄️ 弱势滞后 (绿)'
    else: return '⚠️ 动能衰退 (黄)'

df['阵营'] = df.apply(lambda r: get_quadrant_color(r['相对强弱(X)'], r['动量加速度(Y)']), axis=1)

color_map = {
    '👑 强势领军 (红)': '#FF3B30',
    '🛡️ 资金潜伏 (蓝)': '#007AFF',
    '❄️ 弱势滞后 (绿)': '#34C759',
    '⚠️ 动能衰退 (黄)': '#FFCC00'
}

# ==========================================
# 🎨 页面排版
# ==========================================
st.title("🎯 城门立木 · 游资 RRG 四象限雷达 (V4)")
st.markdown("💡 **战法：** 宏观寻找右上角【领军区】的板块红气泡，悬停查看核心中军。微观在下方表格锁定放量爆破标的！")

# ----------------- 雷达图区域 -----------------
fig = px.scatter(
    df, x="相对强弱(X)", y="动量加速度(Y)", size="突破动能得分",
    color="阵营", text="展示标签", color_discrete_map=color_map,
    size_max=60, height=750,
    custom_data=['板块', '涨跌幅', '总成交额(亿)', '突破标的', '1日先锋', '3日动能', '5日趋势', '核心中军']
)

hover_template = (
    "<b><span style='font-size:22px'>🎯 %{customdata[0]}</span></b><br><br>"
    "⚔️ <b>微观穿透面板:</b><br>"
    "🟢 <b>1日先锋:</b> <span style='color:#FF3B30'><b>%{customdata[4]}</b></span><br>"
    "🔥 <b>3日动能:</b> <span style='color:#FF9500'>%{customdata[5]}</span><br>"
    "📈 <b>5日趋势:</b> <span style='color:#007AFF'>%{customdata[6]}</span><br>"
    "🐘 <b>核心中军:</b> <span style='color:#AF52DE'><b>%{customdata[7]}</b></span><br><br>"
    "📊 板块等权涨幅: %{customdata[1]}%<br>"
    "💰 板块总成交额: %{customdata[2]} 亿<br>"
    "<extra></extra>"
)

fig.update_traces(
    textposition='top center', hovertemplate=hover_template,
    marker=dict(line=dict(width=1, color='rgba(0,0,0,0.4)')),
    textfont=dict(size=14, weight='bold', color='#222222')
)

fig.add_vline(x=0, line_dash="solid", line_width=2, line_color="rgba(0,0,0,0.5)")
fig.add_hline(y=1.0, line_dash="solid", line_width=2, line_color="rgba(0,0,0,0.5)")

x_max, x_min = df['相对强弱(X)'].max(), df['相对强弱(X)'].min()
y_max, y_min = df['动量加速度(Y)'].max(), df['动量加速度(Y)'].min()

fig.add_annotation(x=x_max*0.8, y=y_max*0.9, text="👑 领军区", showarrow=False, font=dict(color="#FF3B30", size=24), opacity=0.15)
fig.add_annotation(x=x_min*0.8, y=y_max*0.9, text="🛡️ 修复区", showarrow=False, font=dict(color="#007AFF", size=24), opacity=0.15)
fig.add_annotation(x=x_min*0.8, y=y_min*0.8, text="❄️ 滞后区", showarrow=False, font=dict(color="#34C759", size=24), opacity=0.15)
fig.add_annotation(x=x_max*0.8, y=y_min*0.8, text="⚠️ 衰退区", showarrow=False, font=dict(color="#FFCC00", size=24), opacity=0.15)

fig.update_layout(
    xaxis_title="◀ 弱势跑输  ------  相对强弱 (超额收益/X)  ------  强势碾压 ▶",
    yaxis_title="动能衰退  ------  资金动量加速度 (Y)  ------  量能爆发",
    plot_bgcolor="#F4F6F9",
    xaxis=dict(zeroline=False, showgrid=True, gridcolor='white', gridwidth=1.5),
    yaxis=dict(zeroline=False, showgrid=True, gridcolor='white', gridwidth=1.5),
    hoverlabel=dict(bgcolor="white", font_size=15, font_family="Arial")
)

st.plotly_chart(fig, use_container_width=True)

# ----------------- 个股爆破表格区域 -----------------
st.markdown("---")
st.markdown("### 🚀 今日【量价齐升】爆破标的")
st.markdown("过滤条件：**当日涨幅 ≥ 5%** 且 **当日成交额 ≥ 15日均额的 1.5 倍**（按涨幅从高到低排列）")

if not df_surge.empty:
    st.dataframe(
        df_surge,
        use_container_width=True,
        hide_index=True,
        height=400,
        column_config={
            "代码": st.column_config.TextColumn("代码"),
            "名称": st.column_config.TextColumn("名称"),
            "入选逻辑": st.column_config.TextColumn(
                "🔥 资金意图",
                help="基于多维量价共振算法提取的资金进攻逻辑"
            ),
            "涨跌幅(%)": st.column_config.NumberColumn("涨跌幅(%)", format="%.2f %%"),
            "增量倍数": st.column_config.NumberColumn(
                "📈 增量倍数",
                help="对比剔除极值后的常态均量",
                format="%.2f x"
            ),
            "今日成交额(亿)": st.column_config.NumberColumn("今日成交额(亿)", format="%.2f"),
            "常态均额(亿)": st.column_config.NumberColumn("常态均额(亿)", format="%.2f"),
        }
    )
else:
    st.info("📉 今日市场情绪低迷，未扫描到符合多维共振标准的标的。")