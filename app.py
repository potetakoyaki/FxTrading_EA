"""
🔮 未来予測チャート分析ツール — マルチタイムフレーム統合版
全足種（5m〜週足）をそれぞれ独立分析し、加重統合した総合予測を表示。
"""

import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

from indicators import add_sma, add_ema, add_bollinger_bands, add_rsi, add_macd, get_analysis_summary
from forecaster import composite_forecast, get_forecast_summary
from advanced_analyzer import run_full_analysis
from multi_tf_analyzer import fetch_all_timeframes, analyze_all_timeframes, generate_unified_rationale, TIMEFRAMES

# ──────────────────────────────────────────────
st.set_page_config(page_title="未来予測チャート", page_icon="🔮", layout="wide", initial_sidebar_state="expanded")

PRESETS = {
    "🇫🇽 FX": {
        "USD/JPY": "USDJPY=X", "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X",
        "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X", "AUD/USD": "AUDUSD=X",
        "AUD/JPY": "AUDJPY=X", "USD/CHF": "USDCHF=X",
    },
    "📈 指数": {
        "日経225": "^N225", "S&P 500": "^GSPC", "NASDAQ 100": "^NDX",
        "ダウ平均": "^DJI", "DAX": "^GDAXI", "FTSE 100": "^FTSE", "VIX": "^VIX",
    },
    "🥇 コモディティ": {
        "ゴールド": "GC=F", "シルバー": "SI=F", "WTI原油": "CL=F",
        "天然ガス": "NG=F", "プラチナ": "PL=F", "銅": "HG=F",
    },
}

# ──────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+JP:wght@400;500;700&display=swap');
.stApp{font-family:'Inter','Noto Sans JP',sans-serif}

.hdr{display:flex;align-items:center;gap:.8rem;padding:.5rem 1rem;background:linear-gradient(135deg,#080818,#160a30);border-radius:10px;margin-bottom:.5rem;border:1px solid rgba(138,43,226,.18)}
.hdr .t{color:#fff;font-size:1.15rem;font-weight:700}
.hdr .s{color:rgba(255,255,255,.35);font-size:.68rem;margin-left:auto}

.verdict{background:linear-gradient(135deg,#0d0d2b,#1a0a3e);border:1px solid rgba(138,43,226,.3);border-radius:14px;padding:1rem 1.2rem;text-align:center;margin-bottom:.5rem}
.verdict .dir{font-size:2rem;font-weight:800;letter-spacing:.03em}
.verdict .sub{color:rgba(200,160,255,.5);font-size:.72rem;margin-top:.25rem}
.positive{color:#00e676}.negative{color:#ff5252}.neutral{color:#ffd740}

.tf-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:.4rem;margin-bottom:.5rem}
.tf-card{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:9px;padding:.5rem .6rem;text-align:center}
.tf-card .tf-label{color:rgba(255,255,255,.35);font-size:.55rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
.tf-card .tf-score{font-size:1rem;font-weight:800;line-height:1.2}
.tf-card .tf-detail{color:rgba(255,255,255,.4);font-size:.52rem;margin-top:.15rem;line-height:1.3}

.chart-box{background:#08090e;border-radius:12px;padding:.2rem;box-shadow:0 6px 24px rgba(0,0,0,.3);border:1px solid rgba(138,43,226,.1);margin-bottom:.4rem}

.rationale-box{background:linear-gradient(135deg,#0a0a1a,#110a25);border:1px solid rgba(138,43,226,.15);border-radius:12px;padding:1rem 1.2rem;margin:.5rem 0;color:rgba(255,255,255,.85);font-size:.8rem;line-height:1.7}
.rationale-box strong{color:#e0b0ff}
.rationale-box h2{font-size:1rem;margin-bottom:.5rem}
.rationale-box h3{font-size:.85rem;margin-top:.8rem;margin-bottom:.25rem}
.rationale-box hr{border:none;border-top:1px solid rgba(138,43,226,.12);margin:.6rem 0}

[data-testid="stSidebar"]{background:linear-gradient(180deg,#08081a,#141428)}
[data-testid="stSidebar"] .stMarkdown h1,[data-testid="stSidebar"] .stMarkdown h2,[data-testid="stSidebar"] .stMarkdown h3{color:#fff;font-size:.82rem}
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────
# サイドバー
# ──────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔮 銘柄")
    category = st.selectbox("カテゴリ", list(PRESETS.keys()) + ["✏️ カスタム"])
    if category == "✏️ カスタム":
        ticker = st.text_input("ティッカー", "USDJPY=X").strip().upper()
    else:
        items = PRESETS[category]
        ticker = items[st.selectbox("銘柄", list(items.keys()))]

    st.markdown("---")
    st.markdown("### 📐 チャート表示")
    chart_tf_label = st.selectbox("表示する足種", ["5分足","15分足","30分足","1時間足","4時間足","日足","週足"], index=5)
    tf_map = {"5分足":"5m","15分足":"15m","30分足":"30m","1時間足":"1h","4時間足":"4h","日足":"1d","週足":"1wk"}
    chart_interval = tf_map[chart_tf_label]

    show_fibo = st.checkbox("フィボナッチ", value=True)
    show_channel = st.checkbox("チャネル", value=True)
    show_sr = st.checkbox("サポレジ", value=True)
    show_sma = st.checkbox("SMA", value=True)
    show_bb = st.checkbox("ボリンジャー", value=False)

# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────
st.markdown('<div class="hdr"><span style="font-size:1.3rem">🔮</span><span class="t">未来予測チャート</span><span class="s">マルチタイムフレーム総合分析</span></div>', unsafe_allow_html=True)

if not ticker:
    st.info("👈 銘柄を選択"); st.stop()

# ──────── 全足種データ取得 & 分析 ────────
with st.spinner(f"🧠 {ticker} を全7足種で分析中... (30〜60秒)"):
    all_dfs = fetch_all_timeframes(ticker)
    if not all_dfs:
        st.error("データ取得に失敗しました"); st.stop()

    mtf_result = analyze_all_timeframes(ticker, all_dfs)

unified = mtf_result["unified"]
tf_summary = mtf_result["tf_summary"]
individual = mtf_result["individual"]

# 表示用チャートのデータ
chart_df = all_dfs.get(chart_interval)
if chart_df is None:
    chart_df = list(all_dfs.values())[0]
    chart_interval = list(all_dfs.keys())[0]

current_price = float(chart_df["Close"].iloc[-1])
info_cache = {}
try:
    info_cache = yf.Ticker(ticker).info
except:
    pass
display_name = info_cache.get("longName") or info_cache.get("shortName") or ticker

# ──────── 総合判定カード ────────
u_cls = "positive" if unified["score"] > 0.1 else ("negative" if unified["score"] < -0.1 else "neutral")
st.markdown(f"""<div class="verdict">
<div style="color:rgba(200,160,255,.4);font-size:.6rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase">
{display_name} — マルチタイムフレーム総合判定</div>
<div class="dir {u_cls}">{unified['direction']}　{unified['score']:+.3f}</div>
<div class="sub">
全{unified['valid_count']}足種分析 ┃
上昇 <span class="positive">{unified['bullish_count']}</span> ┃
下降 <span class="negative">{unified['bearish_count']}</span> ┃
中立 <span class="neutral">{unified['neutral_count']}</span> ┃
方向一致度 <b>{unified['consensus']:.0%}</b>
</div>
</div>""", unsafe_allow_html=True)

# ──────── 全足種スコアカード ────────
cards_html = ""
for item in tf_summary:
    if item["direction"] == "エラー":
        continue
    s = item["score"]
    cls = "positive" if s > 0.1 else ("negative" if s < -0.1 else "neutral")
    icon = "🟢" if s > 0.1 else ("🔴" if s < -0.1 else "⚪")
    cards_html += f"""<div class="tf-card">
    <div class="tf-label">{item['label']}（{item['weight']:.0%}）</div>
    <div class="tf-score {cls}">{icon} {s:+.2f}</div>
    <div class="tf-detail">{item['elliott']}<br>ADX {item['adx']:.0f} {item['adx_strength']}</div>
    </div>"""

st.markdown(f'<div class="tf-grid">{cards_html}</div>', unsafe_allow_html=True)

# ──────── チャート（選択足種 + 予測ライン） ────────
# テクニカル指標
if show_sma: chart_df = add_sma(chart_df, [20, 50])
if show_bb: chart_df = add_bollinger_bands(chart_df)

# 選択足種の分析結果
chart_analysis = individual.get(chart_interval, {})
fib = chart_analysis.get("fibonacci", {})
ch = chart_analysis.get("channel", {})
sr = chart_analysis.get("support_resistance", {})

# 予測ライン
chart_cfg = TIMEFRAMES.get(chart_interval, {"forecast": 90})
try:
    fc = composite_forecast(chart_df, chart_analysis, periods=chart_cfg["forecast"], interval=chart_interval)
except:
    fc = None

fig = make_subplots(rows=1, cols=1)

ci = chart_df.index
if hasattr(ci, "tz") and ci.tz is not None:
    ci = ci.tz_localize(None)

fig.add_trace(go.Candlestick(x=ci, open=chart_df["Open"], high=chart_df["High"],
    low=chart_df["Low"], close=chart_df["Close"], name="OHLC",
    increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350"))

if show_sma:
    for i, p in enumerate([20, 50]):
        c = f"SMA_{p}"
        if c in chart_df.columns:
            fig.add_trace(go.Scatter(x=ci, y=chart_df[c], name=f"SMA{p}",
                line=dict(width=1, color=["#42a5f5","#ffa726"][i])))

if show_bb and "BB_Upper" in chart_df.columns:
    fig.add_trace(go.Scatter(x=ci, y=chart_df["BB_Upper"], line=dict(width=0.7, color="rgba(255,193,7,.3)"), showlegend=False))
    fig.add_trace(go.Scatter(x=ci, y=chart_df["BB_Lower"], line=dict(width=0.7, color="rgba(255,193,7,.3)"),
        fill="tonexty", fillcolor="rgba(255,193,7,.03)", showlegend=False))

if show_channel and ch.get("center_line"):
    lookback = len(ch["center_line"])
    ch_idx = ci[-lookback:]
    fig.add_trace(go.Scatter(x=ch_idx, y=ch["upper_channel"],
        line=dict(width=1, dash="dash", color="rgba(0,229,255,.3)"), showlegend=False))
    fig.add_trace(go.Scatter(x=ch_idx, y=ch["lower_channel"],
        line=dict(width=1, dash="dash", color="rgba(0,229,255,.3)"),
        fill="tonexty", fillcolor="rgba(0,229,255,.03)", showlegend=False))

if show_fibo and fib.get("levels"):
    fibo_colors = {"0.0%":"#ff5252","23.6%":"#ffa726","38.2%":"#ffd740",
                   "50.0%":"#fff176","61.8%":"#aed581","78.6%":"#4fc3f7","100.0%":"#42a5f5"}
    for label, price in fib["levels"].items():
        color = fibo_colors.get(label, "rgba(255,255,255,.2)")
        fig.add_hline(y=price, line_dash="dot", line_color=color, line_width=0.6,
                      annotation_text=f"Fib {label}", annotation_font_size=7,
                      annotation_font_color=color)

if show_sr:
    for s in sr.get("supports", [])[:3]:
        fig.add_hline(y=s["price"], line_dash="dash", line_color="rgba(0,230,118,.2)", line_width=0.6,
                      annotation_text=f"S {s['price']:.4f}", annotation_font_size=7,
                      annotation_font_color="rgba(0,230,118,.35)")
    for r in sr.get("resistances", [])[:3]:
        fig.add_hline(y=r["price"], line_dash="dash", line_color="rgba(255,82,82,.2)", line_width=0.6,
                      annotation_text=f"R {r['price']:.4f}", annotation_font_size=7,
                      annotation_font_color="rgba(255,82,82,.35)")

# 予測ライン
if fc:
    last_naive = ci[-1]
    last_close = float(chart_df["Close"].iloc[-1])
    dates = [last_naive] + list(fc["dates"])
    pred = [last_close] + list(fc["predicted"])
    uppers = [last_close] + list(fc["upper"])
    lowers = [last_close] + list(fc["lower"])

    fig.add_trace(go.Scatter(x=dates, y=pred, name=f"🔮 {fc['method']}",
        line=dict(width=3, color="#e040fb", dash="dot"), mode="lines"))
    fig.add_trace(go.Scatter(x=dates, y=uppers, line=dict(width=0), mode="lines", showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=dates, y=lowers, line=dict(width=0), mode="lines",
        fill="tonexty", fillcolor="rgba(224,64,251,.08)", showlegend=False, hoverinfo="skip"))

    fig.add_vline(x=last_naive, line_dash="dash", line_color="rgba(138,43,226,.35)", line_width=1.5)
    fig.add_annotation(x=last_naive, y=1.02, yref="paper", text="◀ 過去 ┃ 未来 ▶",
        showarrow=False, font=dict(size=9, color="rgba(200,160,255,.45)"), xanchor="center")

fig.update_layout(
    height=620, template="plotly_dark", paper_bgcolor="#08090e", plot_bgcolor="#08090e",
    font=dict(family="Inter,Noto Sans JP,sans-serif", color="#fff", size=11),
    legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1,
        bgcolor="rgba(0,0,0,0)", font=dict(size=9)),
    margin=dict(l=50, r=15, t=25, b=12),
    xaxis_rangeslider_visible=False, hovermode="x unified")
fig.update_yaxes(gridcolor="rgba(255,255,255,.03)")
fig.update_xaxes(gridcolor="rgba(255,255,255,.03)")

st.markdown(f'<div style="color:rgba(255,255,255,.3);font-size:.65rem;margin-bottom:.2rem">📈 {chart_tf_label} チャート（{display_name}）</div>', unsafe_allow_html=True)
st.markdown('<div class="chart-box">', unsafe_allow_html=True)
st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": True, "scrollZoom": True})
st.markdown("</div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────
# 📝 マルチTF統合 — 予測根拠
# ──────────────────────────────────────────────
rationale = generate_unified_rationale(mtf_result, ticker, current_price)
st.markdown("### 📝 全足種分析 — 予測の根拠")
st.markdown(f'<div class="rationale-box">\n\n{rationale}\n\n</div>', unsafe_allow_html=True)

# ──── 各足種テーブル ────
with st.expander("📊 全足種スコア一覧"):
    table_data = []
    for item in tf_summary:
        if item["direction"] == "エラー":
            continue
        icon = "🟢" if item["score"] > 0.1 else ("🔴" if item["score"] < -0.1 else "⚪")
        table_data.append({
            "足種": item["label"],
            "判定": f"{icon} {item['direction']}",
            "スコア": f"{item['score']:+.3f}",
            "重み": f"{item['weight']:.0%}",
            "ADX": f"{item['adx']:.0f}",
            "トレンド": item["adx_strength"],
            "エリオット": item["elliott"],
            "チャネル": item["channel"],
            "フィボ": item["fibo_trend"],
        })
    st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)

# 予測データ
if fc:
    with st.expander("📋 予測データテーブル"):
        st.dataframe(pd.DataFrame({
            "日時": fc["dates"],
            "予測": [round(v, 4) for v in fc["predicted"]],
            "上限": [round(v, 4) for v in fc["upper"]],
            "下限": [round(v, 4) for v in fc["lower"]],
        }), use_container_width=True, hide_index=True, height=200)
