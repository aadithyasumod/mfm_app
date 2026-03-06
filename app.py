"""
app.py  —  Multi-Factor Momentum Model  (v4)
Run with:  streamlit run app.py

v4 changes:
  • Full NSE + BSE equity universe (~5 000+ stocks)
  • Autocomplete shows exchange suffix:  "Reliance Industries → RELIANCE (NSE)"
  • RS cache built in background (app stays usable during build)
  • Batched yfinance downloads (~10× faster cache build)
  • BSE tickers (.BO) fully supported throughout
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import os, io, time

from nifty500_tickers import (
    resolve_ticker, ALL_TICKERS, NAME_TO_TICKER,
    DISPLAY_ENTRIES, DISPLAY_TO_TICKER,
)
from data_fetcher import (
    fetch_all_data, build_rs_cache, fetch_batch_scores, CACHE_FILE,
    start_background_cache_build, get_build_status, is_build_running,
)
from mfm_engine import (
    score_price_momentum, score_relative_strength, score_trend_quality,
    score_volume_momentum, score_earnings_momentum, compute_composite,
    generate_interpretation, FACTOR_LABELS, WEIGHTS,
)
from universe_builder import build_universe, universe_stats, UNIVERSE_FILE

# ─────────────────────────────────────────────────────────────────────
#  PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MFM · Momentum Engine",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─────────────────────────────────────────────────────────────────────
#  SESSION STATE
# ─────────────────────────────────────────────────────────────────────
if "dark_mode"  not in st.session_state: st.session_state.dark_mode  = True
if "watchlist"  not in st.session_state: st.session_state.watchlist  = []

# ─────────────────────────────────────────────────────────────────────
#  THEME PALETTES
# ─────────────────────────────────────────────────────────────────────
DARK = dict(
    bg="#0d0b08", panel="#13110d", panel2="#1a1712",
    border="#2c2619", border2="#3d3426",
    text="#e8dcc8", muted="#7a6e5c", muted2="#9e9080",
    gold="#c9a84c", gold2="#e8c96a",
    green="#6bbf72", red="#c05a5a", amber="#c49a2a",
    chart_bg="#0d0b08", chart_grid="#1a1712", input_bg="#1a1712",
)
LIGHT = dict(
    bg="#f5f0e6", panel="#ede7d9", panel2="#e5dece",
    border="#c8bfa6", border2="#b0a488",
    text="#1c1710", muted="#6b5e4a", muted2="#4a3e2c",
    gold="#8b6612", gold2="#a07820",
    green="#2a7030", red="#8b2424", amber="#7a5c10",
    chart_bg="#f5f0e6", chart_grid="#e5dece", input_bg="#ede7d9",
)

def T(): return DARK if st.session_state.dark_mode else LIGHT


# ─────────────────────────────────────────────────────────────────────
#  CSS
# ─────────────────────────────────────────────────────────────────────
def inject_css():
    t = T()
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;0,700;1,400&family=EB+Garamond:ital,wght@0,400;0,500;1,400&display=swap');
html,body,[class*="css"]{{font-family:'EB Garamond',serif !important;background-color:{t['bg']} !important;color:{t['text']} !important;}}
.stApp{{background-color:{t['bg']} !important;}}
.block-container{{padding:1.8rem 2.5rem 3rem !important;max-width:1340px !important;}}
#MainMenu,footer,header{{visibility:hidden;}}
.stDeployButton{{display:none;}}
h1,h2,h3{{font-family:'Playfair Display',serif !important;color:{t['text']} !important;font-weight:600 !important;}}
.stTextInput>div>div>input{{background-color:{t['input_bg']} !important;border:1px solid {t['border2']} !important;border-radius:2px !important;color:{t['text']} !important;font-family:'EB Garamond',serif !important;font-size:1rem !important;padding:0.5rem 0.8rem !important;}}
.stTextInput>div>div>input:focus{{border-color:{t['gold']} !important;box-shadow:0 0 0 1px {t['gold']} !important;outline:none !important;}}
.stSelectbox>div>div{{background-color:{t['input_bg']} !important;border:1px solid {t['border2']} !important;border-radius:2px !important;color:{t['text']} !important;font-family:'EB Garamond',serif !important;font-size:0.95rem !important;}}
.stButton>button{{background:transparent !important;border:1px solid {t['gold']} !important;color:{t['gold']} !important;font-family:'Playfair Display',serif !important;font-size:0.88rem !important;letter-spacing:0.1em !important;padding:0.45rem 1.6rem !important;border-radius:1px !important;}}
.stButton>button:hover{{background:{t['gold']} !important;color:{t['bg']} !important;}}
.stTabs [data-baseweb="tab-list"]{{background:transparent !important;border-bottom:1px solid {t['border2']} !important;gap:0 !important;}}
.stTabs [data-baseweb="tab"]{{background:transparent !important;color:{t['muted']} !important;font-family:'Playfair Display',serif !important;font-size:0.95rem !important;letter-spacing:0.06em !important;padding:0.6rem 1.6rem !important;border:none !important;border-bottom:2px solid transparent !important;}}
.stTabs [aria-selected="true"]{{color:{t['gold']} !important;border-bottom:2px solid {t['gold']} !important;background:transparent !important;}}
.stTabs [data-baseweb="tab-panel"]{{padding-top:1.2rem !important;}}
[data-testid="stMetric"]{{background:{t['panel']} !important;border:1px solid {t['border']} !important;border-radius:2px !important;padding:0.7rem 1rem !important;}}
[data-testid="stMetricLabel"]{{font-family:'EB Garamond',serif !important;color:{t['muted2']} !important;font-size:0.78rem !important;letter-spacing:0.1em !important;text-transform:uppercase !important;}}
[data-testid="stMetricValue"]{{font-family:'Playfair Display',serif !important;color:{t['text']} !important;font-size:1.4rem !important;}}
.stCheckbox>label{{color:{t['muted2']} !important;font-family:'EB Garamond',serif !important;font-size:0.9rem !important;}}
.stRadio>label{{color:{t['muted2']} !important;font-family:'EB Garamond',serif !important;}}
[data-testid="stExpander"]>details>summary{{font-family:'Playfair Display',serif !important;color:{t['muted2']} !important;background:{t['panel']} !important;border:1px solid {t['border']} !important;border-radius:2px !important;}}
[data-testid="stExpander"]>details>div{{background:{t['panel']} !important;border:1px solid {t['border']} !important;border-top:none !important;}}
.stProgress>div>div{{background-color:{t['gold']} !important;}}
hr{{border-color:{t['border']} !important;margin:0.8rem 0 !important;}}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────
#  COLOUR HELPERS
# ─────────────────────────────────────────────────────────────────────
def score_color(s):
    t = T()
    if s >= 75: return t["green"]
    if s >= 55: return t["gold"]
    if s >= 35: return t["amber"]
    return t["red"]

def signal_color(label):
    t = T()
    return {"STRONG BUY": t["green"], "BUY": t["green"],
            "WATCH": t["gold"], "CAUTION": t["amber"], "AVOID": t["red"]}.get(label, t["muted2"])


# ─────────────────────────────────────────────────────────────────────
#  AUTOCOMPLETE  — NSE+BSE with exchange suffix
# ─────────────────────────────────────────────────────────────────────

def autocomplete_search():
    t = T()
    col_input, col_btn = st.columns([5, 1])
    with col_input:
        query = st.text_input(
            "", placeholder="Type ticker or company name…  e.g. 'Reliance', 'LUMAXTECH', '500325'",
            label_visibility="collapsed", key="search_input",
        )
    with col_btn:
        go_btn = st.button("Analyse ›", key="go_btn")

    selected_ticker = None

    if query and len(query) >= 2:
        ql = query.lower()
        # Search both company name and symbol parts of DISPLAY_ENTRIES
        matches = [e for e in DISPLAY_ENTRIES if ql in e.lower()][:12]

        if matches:
            count_nse = sum(1 for e in matches if "(NSE)" in e)
            count_bse = sum(1 for e in matches if "(BSE)" in e)
            label_str = f"{len(matches)} suggestion{'s' if len(matches)>1 else ''}"
            if count_nse and count_bse:
                label_str += f" ({count_nse} NSE · {count_bse} BSE)"
            st.markdown(
                f'<div style="font-size:0.73rem;color:{t["muted"]};letter-spacing:0.08em;'
                f'text-transform:uppercase;margin-bottom:2px;">{label_str}</div>',
                unsafe_allow_html=True,
            )
            chosen = st.selectbox("", options=["— select —"] + matches,
                                  label_visibility="collapsed", key="ac_dd")
            if chosen != "— select —":
                selected_ticker = DISPLAY_TO_TICKER.get(chosen)

    if selected_ticker:
        return selected_ticker
    if go_btn and query:
        return resolve_ticker(query)
    return None


# ─────────────────────────────────────────────────────────────────────
#  HTML RENDERERS
# ─────────────────────────────────────────────────────────────────────
def render_header_bar(name, symbol, cmp, day_change, sector, mkt_cap, exchange="NSE"):
    t  = T()
    cc = t["green"] if day_change >= 0 else t["red"]
    arrow = "▲" if day_change >= 0 else "▼"
    sign  = "+" if day_change >= 0 else ""
    tc    = symbol.replace(".NS","").replace(".BO","")
    exch_badge = f'<span style="font-size:0.7rem;background:{t["border2"]};color:{t["gold"]};padding:1px 6px;border-radius:1px;margin-left:6px;">{exchange}</span>'
    st.markdown(
        f'<div style="background:{t["panel"]};border:1px solid {t["border"]};border-radius:2px;'
        f'padding:1rem 1.4rem;margin-bottom:1rem;">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:0.5rem;">'
        f'<div><div style="font-family:\'Playfair Display\',serif;font-size:1.45rem;font-weight:600;'
        f'color:{t["text"]};line-height:1.1;">{name}{exch_badge}</div>'
        f'<div style="font-family:\'EB Garamond\',serif;font-size:0.82rem;color:{t["muted"]};'
        f'letter-spacing:0.1em;text-transform:uppercase;margin-top:3px;">{tc} &nbsp;·&nbsp; {sector} &nbsp;·&nbsp; {mkt_cap}</div>'
        f'</div><div style="text-align:right;">'
        f'<div style="font-family:\'Playfair Display\',serif;font-size:1.75rem;font-weight:600;color:{t["text"]};">₹{cmp:,.2f}</div>'
        f'<div style="font-family:\'EB Garamond\',serif;font-size:0.95rem;color:{cc};">{arrow} {sign}{day_change:.2f}%</div>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )


def render_composite_card(composite, signal_label):
    t = T(); sc = score_color(composite); sl = signal_color(signal_label)
    r = 42; circ = 2*3.14159265*r; fill = (composite/100)*circ
    st.markdown(
        f'<div style="background:{t["panel"]};border:1px solid {t["border"]};border-radius:2px;'
        f'padding:1.2rem 1rem;text-align:center;">'
        f'<div style="font-family:\'EB Garamond\',serif;font-size:0.72rem;letter-spacing:0.16em;'
        f'text-transform:uppercase;color:{t["muted"]};margin-bottom:0.7rem;">Composite Score</div>'
        f'<svg width="116" height="116" viewBox="0 0 116 116" style="display:block;margin:0 auto 0.6rem;">'
        f'<circle cx="58" cy="58" r="{r}" fill="none" stroke="{t["border2"]}" stroke-width="9"/>'
        f'<circle cx="58" cy="58" r="{r}" fill="none" stroke="{sc}" stroke-width="9" '
        f'stroke-dasharray="{fill:.1f} {circ:.1f}" stroke-linecap="round" transform="rotate(-90 58 58)"/>'
        f'<text x="58" y="54" text-anchor="middle" fill="{sc}" style="font-family:\'Playfair Display\',serif;font-size:28px;font-weight:700;">{composite}</text>'
        f'<text x="58" y="69" text-anchor="middle" fill="{t["muted"]}" style="font-family:\'EB Garamond\',serif;font-size:11px;">/ 100</text>'
        f'</svg><div style="font-family:\'Playfair Display\',serif;font-size:0.95rem;font-weight:600;'
        f'letter-spacing:0.12em;color:{sl};border:1px solid {sl};display:inline-block;'
        f'padding:4px 18px;border-radius:1px;">{signal_label}</div></div>',
        unsafe_allow_html=True,
    )


def render_factor_card(title, score, weight_pct, sub_scores):
    t = T(); sc = score_color(score)
    rows = ""
    for label, (val, fscore, wt) in sub_scores.items():
        fc = score_color(int(round(fscore)))
        rows += (f'<tr><td style="padding:3px 0;color:{t["muted2"]};font-family:\'EB Garamond\',serif;'
                 f'font-size:0.82rem;border-bottom:1px solid {t["border"]};">{label}</td>'
                 f'<td style="padding:3px 0;text-align:right;color:{t["text"]};font-family:\'EB Garamond\',serif;'
                 f'font-size:0.82rem;border-bottom:1px solid {t["border"]};">{val:+.1f}</td>'
                 f'<td style="padding:3px 0;text-align:right;color:{fc};font-family:\'EB Garamond\',serif;'
                 f'font-size:0.82rem;font-weight:600;border-bottom:1px solid {t["border"]};">{int(round(fscore))}</td></tr>')
    st.markdown(
        f'<div style="background:{t["panel"]};border:1px solid {t["border"]};border-radius:2px;padding:0.9rem 1rem;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
        f'<div style="font-family:\'Playfair Display\',serif;font-size:0.9rem;font-weight:600;color:{t["text"]};">{title}</div>'
        f'<div style="font-family:\'Playfair Display\',serif;font-size:1.2rem;font-weight:700;color:{sc};">{score}</div></div>'
        f'<div style="background:{t["border2"]};height:3px;border-radius:1px;margin-bottom:8px;">'
        f'<div style="background:{sc};height:3px;border-radius:1px;width:{score}%;"></div></div>'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'<thead><tr>'
        f'<th style="text-align:left;font-size:0.68rem;color:{t["muted"]};letter-spacing:0.08em;text-transform:uppercase;padding-bottom:4px;font-family:\'EB Garamond\',serif;">Metric</th>'
        f'<th style="text-align:right;font-size:0.68rem;color:{t["muted"]};letter-spacing:0.08em;text-transform:uppercase;padding-bottom:4px;font-family:\'EB Garamond\',serif;">Value</th>'
        f'<th style="text-align:right;font-size:0.68rem;color:{t["muted"]};letter-spacing:0.08em;text-transform:uppercase;padding-bottom:4px;font-family:\'EB Garamond\',serif;">Score</th>'
        f'</tr></thead><tbody>{rows}</tbody></table>'
        f'<div style="margin-top:6px;font-size:0.68rem;color:{t["muted"]};text-align:right;letter-spacing:0.06em;font-family:\'EB Garamond\',serif;">Weight: {weight_pct}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_breakdown_bar(contributions):
    t = T(); rows = ""
    for key, c in sorted(contributions.items(), key=lambda x: x[1]["contrib"], reverse=True):
        label = FACTOR_LABELS.get(key, key); sc = c["score"]; contrib = c["contrib"]; maxc = c["max_contrib"]
        pct = int(contrib/maxc*100) if maxc else 0; col = score_color(sc)
        rows += (f'<div style="margin-bottom:0.65rem;">'
                 f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">'
                 f'<span style="font-size:0.83rem;color:{t["muted2"]};font-family:\'EB Garamond\',serif;">{label}</span>'
                 f'<span style="font-size:0.83rem;color:{col};font-family:\'EB Garamond\',serif;font-weight:600;">'
                 f'{contrib:.1f}<span style="color:{t["muted"]};font-size:0.72rem;font-weight:400;"> / {maxc:.0f} pts</span></span></div>'
                 f'<div style="background:{t["border2"]};height:4px;border-radius:1px;">'
                 f'<div style="background:{col};height:4px;border-radius:1px;width:{pct}%;"></div></div></div>')
    st.markdown(
        f'<div style="background:{t["panel"]};border:1px solid {t["border"]};border-radius:2px;padding:1rem 1.1rem;">'
        f'<div style="font-family:\'Playfair Display\',serif;font-size:0.7rem;letter-spacing:0.16em;'
        f'text-transform:uppercase;color:{t["muted"]};margin-bottom:0.8rem;">Factor Contribution</div>{rows}</div>',
        unsafe_allow_html=True,
    )


def render_interpretation(insights):
    t = T()
    items = "".join(
        f'<div style="margin-bottom:0.5rem;font-size:0.92rem;color:{t["muted2"]};'
        f'font-family:\'EB Garamond\',serif;line-height:1.55;">{i}</div>'
        for i in insights
    )
    st.markdown(
        f'<div style="background:{t["panel"]};border:1px solid {t["border"]};border-radius:2px;padding:1rem 1.2rem;">'
        f'<div style="font-family:\'Playfair Display\',serif;font-size:0.7rem;letter-spacing:0.16em;'
        f'text-transform:uppercase;color:{t["muted"]};margin-bottom:0.8rem;">Signal Interpretation</div>{items}</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────
#  RSI
# ─────────────────────────────────────────────────────────────────────
def _calc_rsi(series, period=14):
    delta = series.diff()
    ag = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    al = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = ag / al.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


# ─────────────────────────────────────────────────────────────────────
#  PRICE CHART
# ─────────────────────────────────────────────────────────────────────
def render_price_chart(data, chart_opts):
    t = T(); df = data["history"].copy()
    ema200 = data["ema200"]; ema50 = data["ema50"]
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    close = df["Close"].squeeze(); volume = df["Volume"].squeeze()
    n_days = {"1M":21,"3M":63,"6M":126,"1Y":252}.get(chart_opts["range"],252)
    if n_days < len(df):
        df=df.iloc[-n_days:].copy(); close=close.iloc[-n_days:]
        volume=volume.iloc[-n_days:]; ema200=ema200.iloc[-n_days:]; ema50=ema50.iloc[-n_days:]
    dates = df.index; show_rsi = chart_opts.get("rsi",False)
    n_rows = 3 if show_rsi else 2; row_h = [0.58,0.14,0.28] if show_rsi else [0.72,0.28]
    fig = make_subplots(rows=n_rows,cols=1,shared_xaxes=True,vertical_spacing=0.015,row_heights=row_h)
    up_mask = (close >= close.shift(1).fillna(close)).values
    vol_colors = ["rgba(107,191,114,0.50)" if u else "rgba(192,90,90,0.50)" for u in up_mask]
    fig.add_trace(go.Bar(x=dates,y=volume,name="Volume",marker_color=vol_colors,showlegend=False),row=2,col=1)
    if chart_opts.get("candle",False):
        fig.add_trace(go.Candlestick(x=dates,open=df["Open"].squeeze(),high=df["High"].squeeze(),
            low=df["Low"].squeeze(),close=close,name="Price",
            increasing_line_color=t["green"],decreasing_line_color=t["red"],
            increasing_fillcolor=t["green"],decreasing_fillcolor=t["red"]),row=1,col=1)
    else:
        fig.add_trace(go.Scatter(x=dates,y=close,name="Price",
            line=dict(color=t["gold"],width=1.6),mode="lines"),row=1,col=1)
    if chart_opts.get("ema200",True):
        fig.add_trace(go.Scatter(x=dates,y=ema200,name="200 EMA",
            line=dict(color=t["muted"],width=1.1,dash="dot"),mode="lines"),row=1,col=1)
    if chart_opts.get("ema50",True):
        fig.add_trace(go.Scatter(x=dates,y=ema50,name="50 EMA",
            line=dict(color=t["muted2"],width=1.1,dash="dash"),mode="lines"),row=1,col=1)
    if show_rsi:
        full_close = data["history"]["Close"].squeeze()
        if isinstance(full_close, pd.DataFrame): full_close = full_close.iloc[:,0]
        rsi = _calc_rsi(full_close); rsi_t = rsi.iloc[-n_days:] if n_days < len(rsi) else rsi
        fig.add_trace(go.Scatter(x=dates,y=rsi_t,name="RSI (14)",
            line=dict(color=t["gold2"],width=1.3),mode="lines"),row=3,col=1)
        for level,lbl,col in [(70,"OB",t["red"]),(30,"OS",t["green"])]:
            fig.add_hline(y=level,row=3,col=1,line=dict(color=col,width=0.8,dash="dot"),
                annotation_text=lbl,annotation_position="left",
                annotation_font=dict(size=9,color=col,family="EB Garamond, serif"))
    ax = dict(showgrid=True,gridcolor=t["chart_grid"],zeroline=False,
              linecolor=t["border"],tickfont=dict(size=10,family="EB Garamond, serif",color=t["muted2"]))
    fig.update_xaxes(**ax); fig.update_yaxes(**ax)
    fig.update_yaxes(tickprefix="₹",row=1,col=1)
    fig.update_yaxes(tickprefix="",row=2,col=1,tickformat=".2s",
                     title_text="Vol",title_font=dict(size=9,color=t["muted"],family="EB Garamond, serif"))
    if show_rsi:
        fig.update_yaxes(range=[0,100],row=3,col=1,title_text="RSI",
                         title_font=dict(size=9,color=t["muted"],family="EB Garamond, serif"))
    fig.update_layout(
        xaxis_rangeslider_visible=False,plot_bgcolor=t["chart_bg"],paper_bgcolor=t["chart_bg"],
        font=dict(family="EB Garamond, serif",color=t["muted2"],size=11),
        margin=dict(l=8,r=8,t=8,b=8),hovermode="x unified",
        hoverlabel=dict(bgcolor=t["panel"],bordercolor=t["border"],
                        font=dict(family="EB Garamond, serif",size=12,color=t["text"])),
        legend=dict(orientation="h",yanchor="bottom",y=1.01,xanchor="left",x=0,
                    font=dict(size=11,family="EB Garamond, serif",color=t["muted2"]),bgcolor="rgba(0,0,0,0)"),
    )
    st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})


# ─────────────────────────────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────────────────────────────
def render_sidebar():
    t = T()
    with st.sidebar:
        st.markdown(
            f'<div style="font-family:\'Playfair Display\',serif;font-size:1rem;font-weight:600;'
            f'color:{t["gold"]};border-bottom:1px solid {t["border"]};padding-bottom:0.5rem;'
            f'margin-bottom:1rem;">◆ MFM Settings</div>',
            unsafe_allow_html=True,
        )

        mode_label = "☀  Light Mode" if st.session_state.dark_mode else "☾  Dark Mode"
        if st.button(mode_label, key="theme_toggle"):
            st.session_state.dark_mode = not st.session_state.dark_mode
            st.rerun()

        st.markdown(f"<hr style='border-color:{t['border']};margin:0.8rem 0;'>", unsafe_allow_html=True)

        # ── Universe section ──────────────────────────────────────────
        st.markdown(
            f'<div style="font-family:\'Playfair Display\',serif;font-size:0.85rem;'
            f'color:{t["gold"]};margin-bottom:0.4rem;">Stock Universe</div>',
            unsafe_allow_html=True,
        )
        stats = universe_stats()
        if stats.get("exists") and stats.get("total", 0) > 0:
            fresh_label = "✦ Current" if stats["fresh"] else "◌ Stale"
            st.markdown(
                f'<div style="font-family:\'EB Garamond\',serif;font-size:0.82rem;'
                f'color:{t["muted2"]};line-height:1.7;">'
                f'Status: <span style="color:{t["gold"]};">{fresh_label}</span><br>'
                f'Total: {stats["total"]:,} stocks<br>'
                f'NSE: {stats["nse"]:,} &nbsp;·&nbsp; BSE: {stats["bse"]:,}<br>'
                f'Last updated: {stats["age_days"]}d ago</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="font-family:\'EB Garamond\',serif;font-size:0.82rem;color:{t["muted2"]};">'
                f'Using built-in list. Click below to fetch full NSE + BSE universe.</div>',
                unsafe_allow_html=True,
            )

        if st.button("Refresh Universe", key="refresh_universe"):
            with st.spinner("Fetching NSE + BSE equity lists…"):
                build_universe(force=True)
            st.toast("Universe updated ✦")
            st.rerun()

        st.markdown(f"<hr style='border-color:{t['border']};margin:0.8rem 0;'>", unsafe_allow_html=True)

        # ── RS Cache section ──────────────────────────────────────────
        st.markdown(
            f'<div style="font-family:\'Playfair Display\',serif;font-size:0.85rem;'
            f'color:{t["gold"]};margin-bottom:0.4rem;">RS Rank Cache</div>',
            unsafe_allow_html=True,
        )

        build_status = get_build_status()
        build_running = build_status.get("running", False)

        if build_running:
            done  = build_status.get("done", 0)
            total = build_status.get("total", 1)
            curr  = build_status.get("current", "…")
            pct   = int(done / max(total, 1) * 100)
            st.markdown(
                f'<div style="font-family:\'EB Garamond\',serif;font-size:0.82rem;'
                f'color:{t["gold"]};line-height:1.7;">⟳ Building in background…<br>'
                f'{done:,} / {total:,} stocks<br>'
                f'<span style="color:{t["muted"]};font-size:0.75rem;">{curr}</span></div>',
                unsafe_allow_html=True,
            )
            st.progress(pct)
            if st.button("Refresh Status", key="refresh_status"):
                st.rerun()
        elif os.path.exists(CACHE_FILE):
            mtime = datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))
            age   = (datetime.now() - mtime).days
            try:
                n_cache = len(pd.read_parquet(CACHE_FILE))
            except Exception:
                n_cache = 0
            fresh = "✦ Current" if age < 7 else "◌ Stale"
            st.markdown(
                f'<div style="font-family:\'EB Garamond\',serif;font-size:0.82rem;'
                f'color:{t["muted2"]};line-height:1.7;">'
                f'Status: <span style="color:{t["gold"]};">{fresh}</span><br>'
                f'Stocks indexed: {n_cache:,}<br>'
                f'Last updated: {age}d ago</div>',
                unsafe_allow_html=True,
            )
            if build_status.get("error"):
                st.markdown(
                    f'<div style="font-size:0.75rem;color:{t["red"]};">Last error: {build_status["error"][:80]}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                f'<div style="font-family:\'EB Garamond\',serif;font-size:0.82rem;color:{t["muted2"]};">'
                f'No cache — RS Rank uses median fallback.</div>',
                unsafe_allow_html=True,
            )

        if not build_running:
            if st.button("Build / Refresh RS Cache", key="build_cache"):
                ok = start_background_cache_build(ALL_TICKERS)
                if ok:
                    st.toast("RS cache build started in background ✦")
                else:
                    st.toast("Build already running — check status above.")
                st.rerun()

        st.markdown(
            f'<div style="font-family:\'EB Garamond\',serif;font-size:0.72rem;color:{t["muted"]}; '
            f'margin-top:0.5rem;line-height:1.6;">Indexes 12M returns for all NSE + BSE stocks. '
            f'~8–15 min for full universe. Runs in background — app stays usable.</div>',
            unsafe_allow_html=True,
        )

        # ── Watchlist ─────────────────────────────────────────────────
        if st.session_state.watchlist:
            st.markdown(
                f'<hr style="border-color:{t["border"]};margin:1rem 0;">'
                f'<div style="font-family:\'Playfair Display\',serif;font-size:0.85rem;'
                f'color:{t["gold"]};margin-bottom:0.5rem;">Watchlist</div>',
                unsafe_allow_html=True,
            )
            for item in st.session_state.watchlist:
                sc = score_color(item["composite"])
                exch_tag = f' <span style="font-size:0.65rem;color:{t["muted"]};">({item.get("exchange","NSE")})</span>'
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
                    f'border-bottom:1px solid {t["border"]};">'
                    f'<span style="font-family:\'EB Garamond\',serif;font-size:0.82rem;color:{t["text"]};">'
                    f'{item["ticker"]}{exch_tag}</span>'
                    f'<span style="font-family:\'Playfair Display\',serif;font-size:0.88rem;'
                    f'font-weight:700;color:{sc};">{item["composite"]}</span></div>',
                    unsafe_allow_html=True,
                )
            if st.button("Clear Watchlist", key="clear_wl"):
                st.session_state.watchlist = []
                st.rerun()


# ─────────────────────────────────────────────────────────────────────
#  SINGLE STOCK VIEW
# ─────────────────────────────────────────────────────────────────────
def single_stock_view():
    t = T()
    st.markdown(
        f'<div style="font-family:\'EB Garamond\',serif;font-size:0.85rem;color:{t["muted"]}; '
        f'margin-bottom:0.5rem;font-style:italic;">'
        f'Search by ticker (LUMAXTECH, 500325) or partial name (Rel → Reliance, Baj → Bajaj…) '
        f'· NSE and BSE stocks searchable</div>',
        unsafe_allow_html=True,
    )
    symbol = autocomplete_search()
    if not symbol:
        st.markdown(
            f'<div style="background:{t["panel"]};border:1px solid {t["border"]};border-radius:2px;'
            f'padding:2.5rem;text-align:center;margin-top:1.5rem;">'
            f'<div style="font-family:\'Playfair Display\',serif;font-size:1.05rem;'
            f'color:{t["muted"]};font-style:italic;">Enter a stock to begin scoring</div></div>',
            unsafe_allow_html=True,
        )
        return

    # If no RS cache yet, offer to build it; don't block the analysis
    if not os.path.exists(CACHE_FILE) and not is_build_running():
        with st.expander("⟳ RS Rank cache not built yet — RS scores use median fallback"):
            st.markdown(
                f'<div style="font-size:0.85rem;color:{t["muted2"]};font-family:\'EB Garamond\',serif;">'
                f'The RS Rank percentile requires a background index of {len(ALL_TICKERS):,} stocks. '
                f'Build it via the sidebar — the app stays fully usable while it runs (~8–15 min).</div>',
                unsafe_allow_html=True,
            )

    with st.spinner(f"Fetching & scoring {symbol.replace('.NS','').replace('.BO','')}…"):
        data = fetch_all_data(symbol)
    if data.get("status") != "ok":
        st.error(data.get("status","Unknown error")); return

    m = data["metrics"]
    pm_res = score_price_momentum(m["ret_12m"],m["ret_6m"],m["ret_3m"],m["ret_1m"])
    rs_res = score_relative_strength(m["rs_12m"],m["rs_6m"],m["rs_rank"])
    tq_res = score_trend_quality(m["pct_above_200ema"],m["pct_above_50ema"],m["pct_from_52w_high"],m["adx"])
    vm_res = score_volume_momentum(m["vol_ratio_20_50"],m["up_down_vol_ratio"],m["breakout_vol_mult"])
    em_res = score_earnings_momentum(m["eps_growth_yoy"],m["rev_growth_yoy"],m["earnings_surprise"])
    factor_results = {
        "price_momentum": pm_res, "relative_strength": rs_res, "trend_quality": tq_res,
        "volume_momentum": vm_res, "earnings_momentum": em_res,
    }
    comp_result = compute_composite(factor_results)

    render_header_bar(
        data["name"], data["symbol"], data["cmp"], data["day_change"],
        data["sector"], data["mkt_cap_label"], data.get("exchange","NSE"),
    )

    c1,c2,c3 = st.columns([1.4,2.1,2.5])
    with c1:
        render_composite_card(comp_result["composite"],comp_result["signal"]["label"])
        tc   = data["symbol"].replace(".NS","").replace(".BO","")
        exch = data.get("exchange","NSE")
        wl_key = f"{tc}_{exch}"
        if wl_key not in [f"{w['ticker']}_{w.get('exchange','NSE')}" for w in st.session_state.watchlist]:
            if st.button("＋ Watchlist", key=f"wl_{wl_key}"):
                st.session_state.watchlist.append({
                    "ticker": tc, "composite": comp_result["composite"],
                    "signal": comp_result["signal"]["label"], "exchange": exch,
                })
                st.toast(f"{tc} ({exch}) added to watchlist ✦")
        else:
            st.markdown(
                f'<div style="font-size:0.75rem;color:{t["muted"]};font-family:\'EB Garamond\',serif;'
                f'text-align:center;margin-top:4px;">✦ On watchlist</div>',
                unsafe_allow_html=True,
            )
    with c2: render_breakdown_bar(comp_result["contributions"])
    with c3: render_interpretation(generate_interpretation(comp_result,factor_results))

    st.markdown(f"<hr style='border-color:{t['border']};'>", unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-family:\'EB Garamond\',serif;font-size:0.7rem;letter-spacing:0.18em;'
        f'text-transform:uppercase;color:{t["muted"]};margin-bottom:0.6rem;">Factor Detail</div>',
        unsafe_allow_html=True,
    )

    r1a,r1b,r1c = st.columns(3)
    with r1a: render_factor_card("Price Momentum",    pm_res["score"],"30%",pm_res["sub_scores"])
    with r1b: render_factor_card("Relative Strength", rs_res["score"],"25%",rs_res["sub_scores"])
    with r1c: render_factor_card("Trend Quality",     tq_res["score"],"20%",tq_res["sub_scores"])
    r2a,r2b,_ = st.columns(3)
    with r2a: render_factor_card("Earnings Momentum", em_res["score"],"15%",em_res["sub_scores"])
    with r2b: render_factor_card("Volume Momentum",   vm_res["score"],"10%",vm_res["sub_scores"])

    st.markdown(f"<hr style='border-color:{t['border']};'>", unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-family:\'EB Garamond\',serif;font-size:0.7rem;letter-spacing:0.18em;'
        f'text-transform:uppercase;color:{t["muted"]};margin-bottom:0.4rem;">Price Chart</div>',
        unsafe_allow_html=True,
    )

    ctl1,ctl2,ctl3,ctl4 = st.columns([2,2,3,2])
    with ctl1: date_range = st.radio(""   ,["1M","3M","6M","1Y"],index=3,horizontal=True,label_visibility="collapsed",key="chart_range")
    with ctl2: chart_type = st.radio("",  ["Line","Candle"],      index=0,horizontal=True,label_visibility="collapsed",key="chart_type")
    with ctl3:
        ec1,ec2 = st.columns(2)
        with ec1: show_ema200 = st.checkbox("200 EMA",value=True,key="ema200_chk")
        with ec2: show_ema50  = st.checkbox("50 EMA", value=True,key="ema50_chk")
    with ctl4: show_rsi = st.checkbox("RSI (14)",value=False,key="rsi_chk")

    render_price_chart(data,{"range":date_range,"candle":chart_type=="Candle",
                             "ema200":show_ema200,"ema50":show_ema50,"rsi":show_rsi})

    with st.expander("Raw Metrics"):
        raw = [
            ("Price Momentum","12M Return (skip-1)",f"{m['ret_12m']:+.2f}%"),
            ("Price Momentum","6M Return (skip-1)", f"{m['ret_6m']:+.2f}%"),
            ("Price Momentum","3M Return",           f"{m['ret_3m']:+.2f}%"),
            ("Price Momentum","1M Return",           f"{m['ret_1m']:+.2f}%"),
            ("Relative Strength","RS vs Benchmark 12M",f"{m['rs_12m']:+.2f} pp"),
            ("Relative Strength","RS vs Benchmark 6M", f"{m['rs_6m']:+.2f} pp"),
            ("Relative Strength","RS Rank (percentile)",f"{m['rs_rank']:.1f}"),
            ("Trend Quality","% above 200 EMA",       f"{m['pct_above_200ema']:+.2f}%"),
            ("Trend Quality","% above 50 EMA",        f"{m['pct_above_50ema']:+.2f}%"),
            ("Trend Quality","% from 52W High",        f"{m['pct_from_52w_high']:+.2f}%"),
            ("Trend Quality","ADX (14)",                f"{m['adx']:.1f}"),
            ("Volume","20D/50D Vol Ratio",             f"{m['vol_ratio_20_50']:.2f}x"),
            ("Volume","Up/Down Vol Ratio",             f"{m['up_down_vol_ratio']:.2f}x"),
            ("Volume","Breakout Vol Mult",             f"{m['breakout_vol_mult']:.2f}x"),
            ("Earnings","EPS Growth YoY",              f"{m['eps_growth_yoy']:+.1f}%"),
            ("Earnings","Revenue Growth YoY",          f"{m['rev_growth_yoy']:+.1f}%"),
            ("Earnings","Earnings Surprise/QoQ",       f"{m['earnings_surprise']:+.1f}%"),
        ]
        st.dataframe(pd.DataFrame(raw,columns=["Category","Metric","Value"]),
                     use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────────────
#  BATCH SCREENER
# ─────────────────────────────────────────────────────────────────────
def batch_screener_view():
    t = T()
    presets = {
        "Custom":         "",
        "NIFTY 50 Sample":"RELIANCE\nTCS\nHDFCBANK\nINFY\nICICIBANK\nSBIN\nLT\nAXISBANK\nTITAN\nBAJFINANCE",
        "NIFTY IT":       "TCS\nINFY\nWIPRO\nHCLTECH\nTECHM\nLTIM\nLTTS\nPERSISTENT\nCOFORGE\nMPHASIS",
        "NIFTY PHARMA":   "SUNPHARMA\nCIPLA\nDRREDDY\nDIVISLAB\nLUPIN\nBIOCON\nAUROPHARMA\nTORNTPHARM\nIPCALAB\nGLENMARK",
        "NIFTY AUTO":     "MARUTI\nTATAMOTORS\nM&M\nBAJAJ-AUTO\nHEREMOTOCO\nEICHERMOT\nTVSMOTOR\nMOTHERSON\nBOSCHLTD\nSCHAEFFLER",
        "Defence":        "HAL\nBEL\nBHEL\nBEML\nCOCHINSHIP\nMAZDOCK\nGRSE\nSOLARINDS\nDATAPATTNS\nMTAR",
        "My Portfolio":   "LUMAXTECH\nAVANTIFEED\nINDHOTEL\nVIMTALABS\nGALLANTT\nSARDAEN\nSHAILY\nUPL\nBLUEJET",
    }
    col_pre,_ = st.columns([2,5])
    with col_pre:
        preset = st.selectbox("",list(presets.keys()),label_visibility="collapsed",key="batch_preset")
    tickers_input = st.text_area("",value=presets[preset],height=150,
                                  placeholder="RELIANCE\nTCS\nINFY  or BSE codes: 500325\n532540",
                                  label_visibility="collapsed",key="batch_tickers")
    col_run,col_hint = st.columns([1,6])
    with col_run:
        run_btn = st.button("Run Screener ›",key="batch_run")
    with col_hint:
        st.markdown(
            f'<div style="font-family:\'EB Garamond\',serif;font-size:0.8rem;color:{t["muted"]};'
            f'padding-top:0.45rem;">~4–8 sec per stock · Enter NSE symbols or BSE scrip codes</div>',
            unsafe_allow_html=True,
        )
    if not run_btn: return
    raw_tickers = [tk.strip().upper() for tk in tickers_input.strip().splitlines() if tk.strip()]
    if not raw_tickers: st.warning("Enter at least one ticker."); return
    symbols = [s for s in (resolve_ticker(tk) for tk in raw_tickers) if s]
    prog = st.progress(0); stat = st.empty()
    def cb(i,total,sym):
        prog.progress(int((i/max(total,1))*100))
        stat.markdown(
            f'<div style="font-size:0.8rem;color:{t["muted"]};font-family:\'EB Garamond\',serif;">'
            f'Analysing {sym.replace(".NS","").replace(".BO","")}…</div>',
            unsafe_allow_html=True,
        )
    with st.spinner("Scoring…"):
        results = fetch_batch_scores(symbols,progress_callback=cb)
    prog.progress(100); stat.empty()
    if not results: st.warning("No results. Verify tickers are valid NSE symbols or BSE scrip codes."); return
    df = pd.DataFrame(results)
    col_hdr,col_exp = st.columns([5,1])
    with col_hdr:
        st.markdown(
            f'<div style="font-family:\'EB Garamond\',serif;font-size:0.7rem;letter-spacing:0.18em;'
            f'text-transform:uppercase;color:{t["muted"]};margin:0.8rem 0 0.5rem;">'
            f'{len(df)} stocks · ranked by composite momentum</div>',
            unsafe_allow_html=True,
        )
    with col_exp:
        buf = io.BytesIO(); df.to_csv(buf,index=False)
        st.download_button("⬇ CSV",data=buf.getvalue(),
                           file_name=f"mfm_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                           mime="text/csv",key="dl_csv")
    def _cs(v): return f"color:{signal_color(v)};"
    def _cc(v):
        try: return f"color:{score_color(int(v))};font-weight:bold;"
        except: return ""
    def _cp(v):
        try:
            vf = float(str(v).replace("%","").replace("+",""))
            return f"color:{t['green']};" if vf >= 0 else f"color:{t['red']};"
        except: return ""
    styled = (df.style
        .applymap(_cs, subset=["Signal"])
        .applymap(_cc, subset=["Composite","Price Mom","Rel Strength","Trend Quality","Earnings Mom","Volume Mom"])
        .applymap(_cp, subset=["Day (%)","12M Ret (%)"])
        .format({"CMP (₹)":"₹{:,.0f}","Day (%)":"{:+.2f}%","12M Ret (%)":"{:+.1f}%",
                 "RS Rank":"{:.0f}","ADX":"{:.1f}"})
        .set_properties(**{"font-family":"EB Garamond, serif","font-size":"0.88rem"}))
    st.dataframe(styled,use_container_width=True,hide_index=True,height=min(580,54*len(df)+42))
    if len(results) >= 2:
        st.markdown(f"<hr style='border-color:{t['border']};'>", unsafe_allow_html=True)
        bar_colors = [score_color(s) for s in df["Composite"]]
        ticker_labels = [f"{r['Ticker']}\n({r['Exchange']})" for _,r in df.iterrows()]
        fig = go.Figure(go.Bar(
            x=ticker_labels,y=df["Composite"],marker_color=bar_colors,
            text=df["Composite"],textposition="outside",
            textfont=dict(family="EB Garamond, serif",size=11,color=t["gold"]),
        ))
        fig.update_layout(
            plot_bgcolor=t["chart_bg"],paper_bgcolor=t["chart_bg"],
            font=dict(family="EB Garamond, serif",color=t["muted2"],size=11),
            margin=dict(l=8,r=8,t=18,b=8),
            xaxis=dict(showgrid=False,linecolor=t["border"],tickfont=dict(size=10,color=t["muted2"])),
            yaxis=dict(showgrid=True,gridcolor=t["chart_grid"],range=[0,108],
                       linecolor=t["border"],tickfont=dict(size=10,color=t["muted2"])),
            showlegend=False,
        )
        st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})


# ─────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────
def main():
    inject_css(); render_sidebar(); t = T()
    universe_count = len(ALL_TICKERS)
    st.markdown(
        f'<div style="display:flex;align-items:baseline;justify-content:space-between;'
        f'border-bottom:1px solid {t["border"]};padding-bottom:0.9rem;margin-bottom:1.2rem;">'
        f'<div><span style="font-family:\'Playfair Display\',serif;font-size:1.55rem;font-weight:600;'
        f'color:{t["text"]};">Multi-Factor Momentum</span>'
        f'<span style="font-family:\'EB Garamond\',serif;font-size:0.78rem;color:{t["muted"]}; '
        f'letter-spacing:0.16em;text-transform:uppercase;margin-left:0.7rem;">Scoring Engine · NSE & BSE</span>'
        f'<div style="font-family:\'EB Garamond\',serif;font-size:0.82rem;color:{t["muted"]}; '
        f'margin-top:2px;font-style:italic;">'
        f'Price momentum · Relative strength · Trend quality · Earnings momentum · Volume confirmation</div></div>'
        f'<div style="font-size:0.72rem;color:{t["muted"]};font-family:\'EB Garamond\',serif;'
        f'text-align:right;line-height:1.7;">'
        f'Weights: Price Mom 30% · RS 25% · Trend 20% · Earnings 15% · Volume 10%<br>'
        f'Universe: {universe_count:,} stocks (NSE + BSE) · Benchmark: NIFTY 50 / SENSEX</div></div>',
        unsafe_allow_html=True,
    )
    tab1,tab2 = st.tabs(["  Single Stock  ","  Batch Screener  "])
    with tab1: single_stock_view()
    with tab2: batch_screener_view()

if __name__ == "__main__":
    main()
