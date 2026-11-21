"""Trade simulation shared by the backtester and the paper broker, so both behave identically."""
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class Order:
    symbol: str
    side: str            # long | short
    signal_date: str
    score: float
    atr: float
    equity: float
    mult: float = 1.0          # $ per 1.0 price move per unit (1 for stocks, e.g. 5 for MES)


@dataclass
class Fill:
    symbol: str
    side: str
    signal_date: str
    entry_date: str
    exit_date: str
    qty: float
    entry: float
    exit: float
    stop: float
    target: float
    reason: str
    pnl: float
    ret: float

    def dict(self):
        return asdict(self)


def position_size(equity, price, atr, cfg, mult=1.0, risk_dollars=None):
    """Units (shares or contracts) so that a stop-out loses about risk_dollars."""
    if risk_dollars is None:
        risk_dollars = equity * cfg["risk_per_trade"]
    per_unit_risk = max(cfg["stop_atr_mult"] * atr, price * 0.001) * mult
    qty = risk_dollars / per_unit_risk
    if mult == 1.0:                                   # stocks: cap notional exposure
        qty = min(qty, equity * cfg["max_position_pct"] / price)
    if cfg.get("max_contracts"):
        qty = min(qty, cfg["max_contracts"])
    return float(np.floor(qty))


def simulate(order: Order, bars: pd.DataFrame, cfg) -> Fill | None:
    """bars: OHLC rows for this symbol strictly after the signal date.
    Enter at the first open, exit on stop / target (stop assumed first if both
    touched in one bar) or at the close of the hold_days-th bar.
    Returns None if the trade has not completed in the given bars."""
    if bars.empty or np.isnan(bars["open"].iloc[0]):
        return None
    slip = cfg["slippage_bps"] / 1e4
    sgn = 1 if order.side == "long" else -1
    entry = bars["open"].iloc[0] * (1 + sgn * slip)
    stop = entry - sgn * cfg["stop_atr_mult"] * order.atr
    target = entry + sgn * cfg["target_atr_mult"] * order.atr
    qty = position_size(order.equity, entry, order.atr, cfg, order.mult)
    if qty <= 0:
        return None
    hold = cfg["hold_days"]
    at_open = cfg.get("exit_at", "close") == "open"
    n_bars = hold + 1 if at_open else hold          # exit_at=open sells at the open after hold_days sessions
    if len(bars) < n_bars or bars.iloc[:n_bars].isna().any().any() or not np.isfinite(order.atr):
        return None
    for i in range(n_bars):
        b = bars.iloc[i]
        o = b["open"]
        if i > 0 and (sgn * (o - stop) <= 0):        # gapped through stop overnight
            px, why = o, "stop(gap)"
        elif i > 0 and (sgn * (o - target) >= 0):
            px, why = o, "target(gap)"
        elif at_open and i == hold:
            px, why = o, "time(open)"
        elif sgn * (b["low" if sgn > 0 else "high"] - stop) <= 0:
            px, why = stop, "stop"
        elif sgn * (b["high" if sgn > 0 else "low"] - target) >= 0:
            px, why = target, "target"
        elif not at_open and i == hold - 1:
            px, why = b["close"], "close"
        else:
            continue
        exit_px = px * (1 - sgn * slip)
        fees = 2 * cfg["commission"] * (qty if order.mult != 1.0 else 1)   # futures: per contract per side
        pnl = sgn * (exit_px - entry) * qty * order.mult - fees
        return Fill(order.symbol, order.side, order.signal_date,
                    str(bars.index[0].date()), str(bars.index[i].date()),
                    qty, round(entry, 4), round(exit_px, 4), round(stop, 4), round(target, 4),
                    why, round(pnl, 2), pnl / order.equity)
    return None
