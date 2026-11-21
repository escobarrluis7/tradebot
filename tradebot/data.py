"""Daily OHLCV download (Yahoo Finance via yfinance) with a local cache."""
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .config import ROOT

CACHE_DIR = ROOT / "data"


def _cache(symbols):
    import hashlib
    return CACHE_DIR / f"cache_{hashlib.md5(','.join(symbols).encode()).hexdigest()[:8]}.pkl"
FIELDS = ["open", "high", "low", "close", "volume"]
NY = ZoneInfo("America/New_York")


def _drop_incomplete_bar(df: pd.DataFrame) -> pd.DataFrame:
    """During the trading session Yahoo returns a partial bar for today; never trade on it."""
    now = datetime.now(NY)
    if len(df) and df.index[-1].date() == now.date() and now.time() < time(16, 15):
        return df.iloc[:-1]
    return df


def load(symbols: list[str], start: str, refresh: bool = True) -> dict[str, pd.DataFrame]:
    symbols = sorted(set(symbols))
    CACHE = _cache(symbols)
    if CACHE.exists() and not refresh:
        d = pd.read_pickle(CACHE)
        if set(symbols) <= set(d["close"].columns):
            return {k: v[symbols] for k, v in d.items()}

    raw = _yahoo(symbols, start)
    if raw is None:
        raw = _alpaca(symbols, start)
    if raw is None:
        raise RuntimeError("No market data from Yahoo or Alpaca - check network access / symbols.")
    raw = _drop_incomplete_bar(raw)
    out = {}
    for f in FIELDS:
        frame = raw[f.capitalize()]
        if isinstance(frame, pd.Series):
            frame = frame.to_frame(symbols[0])
        frame.index = pd.DatetimeIndex(frame.index).tz_localize(None)
        out[f] = frame.reindex(columns=symbols).astype(float)
    missing = [s for s in symbols if out["close"][s].dropna().empty]
    if missing:
        print(f"warning: no data for {missing}")
    Path(CACHE).parent.mkdir(exist_ok=True)
    pd.to_pickle(out, CACHE)
    return out


def _yahoo(symbols, start, tries=3):
    import time as _t

    import yfinance as yf
    for i in range(tries):
        try:
            raw = yf.download(symbols, start=start, auto_adjust=True, progress=False,
                              group_by="column", threads=True)
            if not raw.empty and raw["Close"].iloc[-5:].notna().mean().mean() > 0.9:
                return raw
        except Exception as e:  # network / rate limit
            print(f"Yahoo download failed ({e}); retrying")
        _t.sleep(5 * (i + 1))
    return None


def _alpaca(symbols, start):
    """Fallback: Alpaca market-data API (free IEX feed). Needs ALPACA_API_KEY/SECRET."""
    import os

    import requests
    if "ALPACA_API_KEY" not in os.environ:
        return None
    print("Using Alpaca market data (IEX feed) as fallback")
    h = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
    rows, token = [], None
    while True:
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start,
                  "adjustment": "all", "feed": "iex", "limit": 10000}
        if token:
            params["page_token"] = token
        r = requests.get("https://data.alpaca.markets/v2/stocks/bars", headers=h, params=params, timeout=60)
        r.raise_for_status()
        j = r.json()
        for sym, bars in (j.get("bars") or {}).items():
            rows += [dict(sym=sym, t=b["t"][:10], Open=b["o"], High=b["h"], Low=b["l"], Close=b["c"], Volume=b["v"])
                     for b in bars]
        token = j.get("next_page_token")
        if not token:
            break
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"])
    return df.pivot(index="t", columns="sym", values=["Open", "High", "Low", "Close", "Volume"])
