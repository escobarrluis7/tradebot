"""Signal library. Each strategy returns a DataFrame of scores in [-1, 1]
(date x symbol) computed only from data available at that day's close.
Positive = expect price to rise over the next session."""
import numpy as np
import pandas as pd

from . import indicators as ind


def trend(d):
    """Classic trend following: price vs 200-day SMA plus 50/200 spread."""
    c = d["close"]
    s50, s200 = ind.sma(c, 50), ind.sma(c, 200)
    return 0.5 * np.sign(c - s200) + 0.5 * np.tanh((s50 / s200 - 1) * 20)


def tsmom(d):
    """Time-series momentum: 12-month return skipping the last month, scaled by volatility."""
    c = d["close"]
    r = c.shift(21) / c.shift(252) - 1
    vol = c.pct_change().rolling(60).std() * np.sqrt(252)
    return np.tanh(r / vol)


def xsmom(d):
    """Cross-sectional momentum: rank 6-month return across the watchlist -> [-1, 1]."""
    c = d["close"]
    r = c / c.shift(126) - 1
    rank = r.rank(axis=1)
    n = r.notna().sum(axis=1)
    return rank.sub(1).div((n - 1).replace(0, np.nan), axis=0) * 2 - 1


def mean_reversion(d):
    """Short-term pullback in an uptrend (RSI(2) oversold above the 200-day SMA).
    Overbought readings score negative."""
    c = d["close"]
    r2 = ind.rsi(c, 2)
    up = c > ind.sma(c, 200)
    raw = (50 - r2) / 50                          # +1 deeply oversold, -1 overbought
    stretch = np.tanh((ind.sma(c, 5) / c - 1) * 40)  # how far below the 5-day mean
    score = 0.7 * raw + 0.3 * stretch
    return score.where(up, score.clip(upper=0) + 0.25 * score.clip(lower=0))


def breakout(d):
    """Donchian 20-day breakout, confirmed by above-average volume."""
    c, h, l, v = d["close"], d["high"], d["low"], d["volume"]
    hi, lo = h.rolling(20).max().shift(1), l.rolling(20).min().shift(1)
    pos = ((c - lo) / (hi - lo)).clip(0, 1.2) * 2 - 1        # channel position
    vol_ratio = (v / v.rolling(20).mean().shift(1)).clip(0, 3)
    boost = np.where(c > hi, np.minimum(vol_ratio, 2) / 2, 0.0)
    return (0.6 * pos + 0.4 * boost).clip(-1, 1)


def macd(d):
    """MACD histogram normalised by ATR."""
    c = d["close"]
    a = ind.atr(d["high"], d["low"], c, 14)
    return np.tanh(ind.macd_hist(c) / a * 2)


REGISTRY = {
    "trend": trend,
    "tsmom": tsmom,
    "xsmom": xsmom,
    "mean_reversion": mean_reversion,
    "breakout": breakout,
    "macd": macd,
}


def compute_all(d, names) -> dict[str, pd.DataFrame]:
    out = {}
    for n in names:
        s = REGISTRY[n](d)
        out[n] = pd.DataFrame(s, index=d["close"].index, columns=d["close"].columns).clip(-1, 1)
    return out
