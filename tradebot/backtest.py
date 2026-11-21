"""Walk-forward backtest of the one-trade-a-day rule (no look-ahead: signals at close t, trade on t+1)."""
import numpy as np
import pandas as pd

from . import indicators as ind
from .engine import Order, simulate
from .instruments import multiplier
from .ensemble import build, pick


def _bars(d, sym, after, n):
    i = d["close"].index.get_loc(after) + 1
    return pd.DataFrame({f: d[f][sym].iloc[i:i + n] for f in ("open", "high", "low", "close")})


def run(d, cfg, start=None, combined=None, bull=None, quiet=False):
    if combined is None:
        _, _, combined, bull = build(d, cfg)
    a = ind.atr(d["high"], d["low"], d["close"], 14)
    dates = combined.index
    if start:
        dates = dates[dates >= pd.Timestamp(start)]
    equity = float(cfg["starting_cash"])
    curve, fills = {}, []
    busy_until = None
    for t in dates[:-1]:
        curve[t] = equity
        if busy_until is not None and t < busy_until:
            continue
        choice = pick(combined.loc[t], bool(bull.loc[t]), cfg)
        if not choice:
            continue
        sym, side, score = choice
        order = Order(sym, side, str(t.date()), score, float(a.at[t, sym]), equity, multiplier(cfg, sym))
        f = simulate(order, _bars(d, sym, t, cfg["hold_days"] + 1), cfg)
        if f is None:
            continue
        equity += f.pnl
        fills.append(f.dict())
        pos = d["close"].index.get_loc(pd.Timestamp(f.exit_date))
        if "open" in f.reason or "gap" in f.reason:
            pos -= 1                       # sold at the open -> can buy again that same morning
        busy_until = d["close"].index[pos]
    curve = pd.Series(curve, name="equity")
    trades = pd.DataFrame(fills)
    return curve, trades


def metrics(curve: pd.Series, trades: pd.DataFrame) -> dict:
    if len(curve) < 2:
        return {}
    rets = curve.pct_change().dropna()
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    dd = curve / curve.cummax() - 1
    m = {
        "start": str(curve.index[0].date()),
        "end": str(curve.index[-1].date()),
        "final_equity": round(curve.iloc[-1], 2),
        "total_return_%": round((curve.iloc[-1] / curve.iloc[0] - 1) * 100, 2),
        "CAGR_%": round(((curve.iloc[-1] / curve.iloc[0]) ** (1 / years) - 1) * 100, 2) if years > 0 else np.nan,
        "sharpe": round(rets.mean() / rets.std() * np.sqrt(252), 2) if rets.std() > 0 else np.nan,
        "max_drawdown_%": round(dd.min() * 100, 2),
        "trades": len(trades),
    }
    if len(trades):
        wins = trades[trades.pnl > 0]
        losses = trades[trades.pnl <= 0]
        m["win_rate_%"] = round(len(wins) / len(trades) * 100, 1)
        m["avg_trade_%"] = round(trades.ret.mean() * 100, 3)
        m["profit_factor"] = round(wins.pnl.sum() / -losses.pnl.sum(), 2) if losses.pnl.sum() < 0 else np.inf
        m["days_in_market_%"] = round(len(trades) / len(curve) * 100, 1)
    return m


def buy_and_hold(d, sym, start, cash):
    c = d["close"][sym].dropna()
    c = c[c.index >= pd.Timestamp(start)]
    return c / c.iloc[0] * cash
