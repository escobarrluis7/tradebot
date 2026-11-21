"""5-minute bars: Alpaca market data (years of history, needs keys) or Yahoo (last 60 days)."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

NY = ZoneInfo("America/New_York")


def _session(df):
    df = df[(df.index.strftime("%H:%M") >= "09:30") & (df.index.strftime("%H:%M") <= "15:55")]
    return df[df.index.dayofweek < 5]


def alpaca_bars(symbols, start, end=None, timeframe="5Min", feed=None) -> dict[str, pd.DataFrame]:
    h = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
    feed = feed or os.environ.get("ALPACA_DATA_FEED", "iex")       # 'sip' needs a paid data plan
    rows, token = [], None
    while True:
        p = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "adjustment": "all",
             "feed": feed, "limit": 10000}
        if end:
            p["end"] = end
        if token:
            p["page_token"] = token
        r = requests.get("https://data.alpaca.markets/v2/stocks/bars", headers=h, params=p, timeout=60)
        r.raise_for_status()
        j = r.json()
        for s, bars in (j.get("bars") or {}).items():
            rows += [(s, b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in bars]
        token = j.get("next_page_token")
        if not token:
            break
    df = pd.DataFrame(rows, columns=["sym", "t", "Open", "High", "Low", "Close", "Volume"])
    if df.empty:
        return {}
    df["t"] = pd.to_datetime(df.t, utc=True).dt.tz_convert(NY)
    return {s: _session(g.set_index("t").drop(columns="sym").sort_index()) for s, g in df.groupby("sym")}


def yahoo_bars(symbols, period="60d") -> dict[str, pd.DataFrame]:
    import yfinance as yf
    d = yf.download(symbols, period=period, interval="5m", auto_adjust=True, progress=False, group_by="column")
    d.index = d.index.tz_convert(NY)
    out = {}
    for s in symbols:
        f = pd.DataFrame({k: d[k][s] for k in ["Open", "High", "Low", "Close", "Volume"]}).dropna()
        if len(f):
            out[s] = _session(f)
    return out


def history(symbols, years=5) -> tuple[dict[str, pd.DataFrame], str]:
    if os.environ.get("ALPACA_API_KEY"):
        start = (datetime.now(NY) - timedelta(days=int(365 * years))).strftime("%Y-%m-%d")
        return alpaca_bars(symbols, start), f"Alpaca 5-minute bars since {start}"
    return yahoo_bars(symbols), "Yahoo 5-minute bars, last 60 days only (add Alpaca keys for years of history)"
