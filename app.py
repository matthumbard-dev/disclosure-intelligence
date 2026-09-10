from __future__ import annotations
import os
from datetime import date, timedelta
import pandas as pd
import streamlit as st
import plotly.express as px

from db import read_events, upsert_events, clear_db
from sec_client import SecClient, ingest_ticker
from demo_data import demo_events

st.set_page_config(page_title="Disclosure Intelligence", page_icon="📡", layout="wide")

st.title("📡 Market Disclosure Intelligence")
st.caption("Daily SEC disclosure monitoring • insider trades • beneficial ownership • proposed sales • material events")

with st.sidebar:
    st.header("Data controls")
    user_agent = st.text_input("SEC User-Agent", value=os.getenv("SEC_USER_AGENT", ""), type="password", help="Use something like: DisclosureDashboard your@email.com")
    watchlist = st.text_area("Watchlist tickers", value="AAPL\nMSFT\nNVDA\nGOOGL\nMETA", height=130)
    max_filings = st.slider("Recent filings per company", 20, 200, 80, 20)
    lookback = st.slider("Dashboard lookback (days)", 1, 90, 30)

    c1, c2 = st.columns(2)
    sync = c1.button("Sync SEC", type="primary", use_container_width=True)
    demo = c2.button("Load demo", use_container_width=True)
    if st.button("Clear database", use_container_width=True):
        clear_db(); st.success("Database cleared")

if demo:
    upsert_events(demo_events())
    st.success("Demo events loaded.")

if sync:
    tickers = sorted({x.strip().upper() for x in watchlist.replace(",", "\n").splitlines() if x.strip()})
    if not user_agent:
        st.error("Enter an SEC User-Agent containing your email before syncing.")
    elif not tickers:
        st.error("Enter at least one ticker.")
    else:
        try:
            client = SecClient(user_agent)
            with st.status("Syncing SEC filings…", expanded=True) as status:
                mapping = client.ticker_map()
                all_events=[]
                for ticker in tickers:
                    info = mapping.get(ticker)
                    if not info:
                        st.write(f"⚠️ {ticker}: ticker not found in SEC mapping")
                        continue
                    st.write(f"Fetching {ticker} — {info['title']}")
                    ev = ingest_ticker(client, ticker, info["cik"], info["title"], max_filings=max_filings)
                    all_events.extend(ev)
                n=upsert_events(all_events)
                status.update(label=f"Sync complete — {n:,} normalized events", state="complete")
        except Exception as exc:
            st.exception(exc)


df = read_events(lookback)
if df.empty:
    st.info("No local events yet. Use **Load demo** to explore the UI or enter your SEC User-Agent and click **Sync SEC**.")
    st.stop()

# Derived fields
for col in ["score", "value", "shares", "price"]:
    if col in df:
        df[col] = pd.to_numeric(df[col], errors="coerce")
df["filed_date"] = pd.to_datetime(df["filed_at"], errors="coerce").dt.date

today = date.today()
today_df = df[df["filed_date"] == today]
high_df = df[df["score"] >= 80]
insider_buys = df[(df["event_type"] == "insider_transaction") & (df["transaction_code"] == "P")]

m1,m2,m3,m4 = st.columns(4)
m1.metric("New today", f"{len(today_df):,}")
m2.metric("High-signal", f"{len(high_df):,}")
m3.metric("Insider buys", f"{len(insider_buys):,}")
m4.metric("Companies", f"{df['ticker'].nunique():,}")

st.subheader("🔥 Highest-signal disclosures")
filters = st.columns([2,2,2,2])
with filters[0]:
    min_score = st.slider("Minimum score", 0, 100, 40)
with filters[1]:
    forms = st.multiselect("Forms", sorted(df["form"].dropna().unique().tolist()))
with filters[2]:
    tickers_f = st.multiselect("Tickers", sorted(df["ticker"].dropna().unique().tolist()))
with filters[3]:
    event_types = st.multiselect("Event types", sorted(df["event_type"].dropna().unique().tolist()))

view = df[df["score"] >= min_score].copy()
if forms: view=view[view["form"].isin(forms)]
if tickers_f: view=view[view["ticker"].isin(tickers_f)]
if event_types: view=view[view["event_type"].isin(event_types)]

show_cols=["score","score_band","filed_at","ticker","company","form","actor","role","summary","value","reasons","source_url"]
st.dataframe(
    view[show_cols],
    use_container_width=True,
    hide_index=True,
    column_config={
        "score": st.column_config.ProgressColumn("Signal", min_value=0, max_value=100, format="%d"),
        "value": st.column_config.NumberColumn("Value", format="$%.0f"),
        "source_url": st.column_config.LinkColumn("Primary source", display_text="SEC filing"),
    },
)

left,right=st.columns(2)
with left:
    st.subheader("Disclosure mix")
    mix=df["form"].value_counts().rename_axis("form").reset_index(name="events")
    st.plotly_chart(px.bar(mix, x="form", y="events"), use_container_width=True)
with right:
    st.subheader("Signal distribution")
    st.plotly_chart(px.histogram(df, x="score", nbins=20), use_container_width=True)

st.subheader("🗞️ Daily digest")
digest_day = st.date_input("Digest date", value=max(df["filed_date"].dropna()) if df["filed_date"].notna().any() else today)
daily=df[df["filed_date"] == digest_day].sort_values(["score","ticker"], ascending=[False,True])
if daily.empty:
    st.caption("No disclosures stored for this date.")
else:
    for _, r in daily.head(25).iterrows():
        value = f" • ${r['value']:,.0f}" if pd.notna(r.get("value")) and r.get("value",0) else ""
        with st.container(border=True):
            st.markdown(f"**{int(r['score'])}/100 · {r['ticker']} · {r['form']}**{value}")
            st.write(r["summary"])
            if r.get("reasons"):
                st.caption(r["reasons"])
            st.link_button("Open SEC source", r["source_url"])

st.subheader("Company timeline")
company_ticker = st.selectbox("Ticker", sorted(df["ticker"].dropna().unique()))
company = df[df["ticker"] == company_ticker].sort_values("filed_at", ascending=False)
st.dataframe(company[["filed_at","score","form","actor","summary","value","source_url"]], use_container_width=True, hide_index=True,
             column_config={"source_url": st.column_config.LinkColumn("Source", display_text="Open"), "value": st.column_config.NumberColumn("Value", format="$%.0f")})

st.caption("Signal scores are heuristic triage, not investment recommendations. Always review the primary filing.")
