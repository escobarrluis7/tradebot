"""Vectorised indicators. Every input/output is a DataFrame indexed by date, one column per symbol."""
import numpy as np
import pandas as pd


def sma(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=n).mean()


def ema(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0).where(gain.notna())


def atr(high, low, close, n: int = 14) -> pd.DataFrame:
    prev = close.shift(1)
    tr = np.maximum(high - low, np.maximum((high - prev).abs(), (low - prev).abs()))
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def macd_hist(close, fast=12, slow=26, signal=9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    return line - ema(line, signal)
