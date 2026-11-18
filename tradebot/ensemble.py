"""Combine strategy scores, adapt weights to recent performance, pick the day's trade."""
import numpy as np
import pandas as pd

from . import indicators as ind
from .strategies import compute_all


def trade_returns(d, cfg) -> pd.DataFrame:
    """Return of the trade a signal at close t would make (open t+1 -> exit), aligned to t."""
    h = cfg["hold_days"]
    if cfg.get("exit_at", "close") == "open":
        return (d["open"].shift(-(h + 1)) / d["open"].shift(-1) - 1)
    return (d["close"].shift(-h) / d["open"].shift(-1) - 1)


def daily_ic(score: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series:
    """Cross-sectional Spearman correlation between score at t and the next-session return."""
    s = score.rank(axis=1)
    r = fwd.where(score.notna()).rank(axis=1)
    s = s.where(r.notna())
    s = s.sub(s.mean(axis=1), axis=0)
    r = r.sub(r.mean(axis=1), axis=0)
    num = (s * r).sum(axis=1)
    den = np.sqrt((s ** 2).sum(axis=1) * (r ** 2).sum(axis=1))
    return (num / den.replace(0, np.nan))


def build(d, cfg):
    names = list(cfg["strategies"])
    scores = compute_all(d, names)
    base = pd.Series(cfg["strategies"], dtype=float)
    base = base / base.abs().sum()
    idx = d["close"].index

    if cfg.get("adaptive_weights"):
        fwd = trade_returns(d, cfg)
        L = cfg.get("adaptive_lookback", 120)
        # IC for signal day t is only known once the trade has finished -> shift to stay causal.
        ic = pd.DataFrame({n: daily_ic(scores[n], fwd) for n in names})
        perf = ic.rolling(L, min_periods=L // 2).mean().shift(cfg["hold_days"] + 2)
        perf_w = perf.clip(lower=0)
        perf_w = perf_w.div(perf_w.sum(axis=1).replace(0, np.nan), axis=0)
        blend = cfg.get("adaptive_blend", 0.5)
        weights = (1 - blend) * pd.DataFrame([base] * len(idx), index=idx) + blend * perf_w.fillna(base)
        weights = weights.div(weights.sum(axis=1), axis=0)
    else:
        weights = pd.DataFrame([base] * len(idx), index=idx)

    combined = sum(scores[n].mul(weights[n], axis=0) for n in names)

    bench = cfg["benchmark"]
    bull = d["close"][bench] > ind.sma(d["close"][[bench]], 200)[bench]
    return scores, weights, combined, bull


def pick(combined_row: pd.Series, bull: bool, cfg):
    """Choose the single best trade for a day. Returns (symbol, side, score) or None."""
    thr = cfg["min_score"] * (1 if (bull or not cfg.get("regime_filter")) else 2)
    row = combined_row.dropna()
    if row.empty:
        return None
    best = row.idxmax()
    if row[best] >= thr:
        return best, "long", float(row[best])
    if cfg.get("allow_short"):
        worst = row.idxmin()
        if -row[worst] >= thr:
            return worst, "short", float(row[worst])
    return None
