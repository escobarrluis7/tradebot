"""Prop-firm rule tracking: drawdown floor, daily loss, profit target, consistency."""
import numpy as np
import pandas as pd


def floor_series(rules: dict, balances: pd.Series) -> pd.Series:
    """Liquidation level over time. balances: EOD balance (or per-trade balance for intraday trailing)."""
    size, dd = float(rules["account_size"]), float(rules["max_drawdown"])
    kind = rules.get("drawdown_type", "eod_trailing")
    if kind == "static":
        return pd.Series(size - dd, index=balances.index)
    peak = np.maximum.accumulate(np.maximum(balances.values, size))
    floor = peak - dd
    lock = rules.get("drawdown_locks_at")
    if lock:
        floor = np.minimum(floor, float(lock))
    return pd.Series(floor, index=balances.index)


def status(rules: dict, trades: pd.DataFrame, curve: pd.Series) -> dict | None:
    if not rules or not rules.get("account_size") or not rules.get("max_drawdown"):
        return None
    size = float(rules["account_size"])
    out = {"account_size": size, "drawdown_type": rules.get("drawdown_type", "eod_trailing")}
    if len(curve) == 0:
        curve = pd.Series([size], index=[pd.Timestamp.today().normalize()])
    if out["drawdown_type"] == "intraday_trailing" and len(trades):
        # best available approximation without tick data: balance after each trade
        t = trades.sort_values("exit_time" if "exit_time" in trades else "exit_date")
        per_trade = size + t.pnl.cumsum()
        floor_now = float(floor_series(rules, pd.Series(per_trade.values)).iloc[-1])
        out["approximate"] = True
    else:
        floor_now = None
    fl = floor_series(rules, curve)
    bal = float(curve.iloc[-1])
    floor = floor_now if floor_now is not None else float(fl.iloc[-1])
    out.update(balance=bal, floor=floor, buffer=bal - floor, buffer_pct=(bal - floor) / float(rules["max_drawdown"]),
               breached=bool(((curve - fl) <= 0).any()) or bal <= floor,
               floor_series=[[str(i.date()), round(float(v), 2)] for i, v in fl.items()])
    daily = trades.groupby("exit_date").pnl.sum() if len(trades) else pd.Series(dtype=float)
    out["trading_days"] = int(len(daily))
    out["today_pnl"] = float(daily.iloc[-1]) if len(daily) else 0.0
    out["worst_day"] = float(daily.min()) if len(daily) else 0.0
    dll = rules.get("daily_loss_limit")
    if dll:
        out["daily_loss_limit"] = float(dll)
        out["days_over_dll"] = int((daily <= -float(dll)).sum())
    tgt = rules.get("profit_target")
    profit = bal - size
    out["profit"] = profit
    if tgt:
        out["profit_target"] = float(tgt)
        out["target_progress"] = profit / float(tgt)
    cp = rules.get("consistency_pct")
    if cp:
        best = float(daily.max()) if len(daily) else 0.0
        out["consistency_limit"] = float(cp) / 100
        out["best_day"] = best
        out["best_day_share"] = best / profit if profit > 0 else None
        out["consistency_ok"] = profit <= 0 or best / profit <= float(cp) / 100
    if rules.get("max_contracts"):
        out["max_contracts"] = rules["max_contracts"]
    return out


def risk_budget(cfg: dict, rules: dict, st: dict | None) -> float:
    """Dollars to risk on the next trade: the smallest of the % risk setting,
    half the daily loss limit, and a quarter of the remaining drawdown buffer."""
    size = float(rules.get("account_size") or cfg["starting_cash"])
    caps = [size * cfg["risk_per_trade"]]
    if rules.get("daily_loss_limit"):
        caps.append(0.5 * float(rules["daily_loss_limit"]))
    if st:
        caps.append(max(0.0, 0.25 * st["buffer"]))
    return max(0.0, min(caps))
