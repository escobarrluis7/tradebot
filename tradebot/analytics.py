"""Turn a trade history + equity curve into daily / weekly / monthly / yearly analytics."""
import numpy as np
import pandas as pd

from .broker import STATE

TRADE_COLS = ["entry_date", "exit_date", "symbol", "side", "qty", "entry", "exit", "pnl", "ret", "reason"]
FREQS = {"daily": "D", "weekly": "W-FRI", "monthly": "ME", "yearly": "YE"}


# ---------------------------------------------------------------- data sources
def from_backtest(cfg, start=None):
    from . import backtest as bt
    from .data import load
    from .ensemble import build
    d = load(cfg["watchlist"] + [cfg["benchmark"]], cfg["history_start"], refresh=False)
    _, _, combined, bull = build(d, cfg)
    raw_curve, trades = bt.run(d, cfg, start, combined, bull)
    first = pd.Timestamp(trades.entry_date.min()) if len(trades) else raw_curve.index[0]
    days = d["close"].index[d["close"].index >= first - pd.offsets.BDay(1)]
    curve = curve_from_trades(trades, cfg["starting_cash"], days)
    bench = bt.buy_and_hold(d, cfg["benchmark"], curve.index[0], cfg["starting_cash"])
    return trades, curve, bench, "Backtest (simulated)"


def curve_from_trades(trades, start_cash, days):
    """Equity marked at each trade's exit date (realised P&L)."""
    daily = trades.groupby(pd.to_datetime(trades.exit_date)).pnl.sum() if len(trades) else pd.Series(dtype=float)
    return float(start_cash) + daily.reindex(days, fill_value=0.0).cumsum()


def from_paper(cfg):
    import json
    s = json.loads(STATE.read_text()) if STATE.exists() else {"history": []}
    trades = pd.DataFrame(s["history"])
    start_cash = float(cfg["starting_cash"])
    if trades.empty:
        return trades, pd.Series(dtype=float), None, "Paper account"
    days = pd.bdate_range(pd.to_datetime(trades.entry_date).min() - pd.offsets.BDay(1),
                          pd.to_datetime(trades.exit_date).max())
    curve = curve_from_trades(trades, start_cash, days)
    return trades, curve, None, "Paper account"


def from_alpaca(cfg):
    from .broker import AlpacaBroker
    b = AlpacaBroker(cfg)
    acct = b._req("GET", "/v2/account")
    start = acct["created_at"][:10]
    h = b._req("GET", "/v2/account/portfolio/history",
               params={"timeframe": "1D", "start": start, "intraday_reporting": "market_hours"})
    curve = pd.Series(h["equity"], index=pd.to_datetime(h["timestamp"], unit="s").normalize(), dtype=float)
    curve = curve[curve > 0].dropna()
    fills, token = [], None
    while True:
        params = {"direction": "asc", "page_size": 100}
        if token:
            params["page_token"] = token
        page = b._req("GET", "/v2/account/activities/FILL", params=params) or []
        fills += page
        if len(page) < 100:
            break
        token = page[-1]["id"]
    trades = pair_fills(fills)
    label = "Alpaca LIVE account" if "paper" not in b.base else "Alpaca paper account"
    return trades, curve, None, label


def pair_fills(fills):
    """FIFO-match buy/sell fills into round-trip trades."""
    open_lots: dict[str, list] = {}
    out = []
    for f in fills:
        sym, qty, px = f["symbol"], float(f["qty"]), float(f["price"])
        sgn = 1 if f["side"] == "buy" else -1
        t = f["transaction_time"][:10]
        lots = open_lots.setdefault(sym, [])
        while qty > 1e-9 and lots and np.sign(lots[0][0]) != sgn:
            lq, lpx, lt = lots[0]
            m = min(qty, abs(lq))
            side = "long" if lq > 0 else "short"
            pnl = (px - lpx) * m * (1 if side == "long" else -1)
            out.append(dict(entry_date=lt, exit_date=t, symbol=sym, side=side, qty=m, entry=lpx,
                            exit=px, pnl=round(pnl, 2), ret=pnl / (lpx * m), reason="fill"))
            qty -= m
            lots[0][0] -= np.sign(lq) * m
            if abs(lots[0][0]) < 1e-9:
                lots.pop(0)
        if qty > 1e-9:
            lots.append([sgn * qty, px, t])
    return pd.DataFrame(out, columns=TRADE_COLS)


def from_account(cfg, acc):
    """Imported trade history (any platform) for an account from accounts.yaml."""
    from .importers import load_trades
    trades = load_trades(acc["id"])
    size = float(acc.get("rules", {}).get("account_size") or cfg["starting_cash"])
    if trades.empty:
        return trades, pd.Series(dtype=float), None, acc["name"]
    days = pd.bdate_range(pd.to_datetime(trades.entry_date).min() - pd.offsets.BDay(1),
                          pd.to_datetime(trades.exit_date).max())
    return trades, curve_from_trades(trades, size, days), None, acc["name"]


def load_source(cfg, source, acc=None):
    if acc is not None:
        if acc.get("connector", {}).get("type") == "alpaca":
            return from_alpaca(cfg)
        return from_account(cfg, acc)
    if source == "backtest":
        return from_backtest(cfg)
    if source == "alpaca":
        return from_alpaca(cfg)
    return from_paper(cfg)


# ---------------------------------------------------------------- aggregation
def summarize(trades: pd.DataFrame, curve: pd.Series) -> dict:
    out = {}
    if len(curve) > 1:
        rets = curve.pct_change().dropna()
        dd = curve / curve.cummax() - 1
        years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1 / 365)
        out.update(
            equity=float(curve.iloc[-1]), start_equity=float(curve.iloc[0]),
            total_return=float(curve.iloc[-1] / curve.iloc[0] - 1),
            cagr=float((curve.iloc[-1] / curve.iloc[0]) ** (1 / years) - 1) if years >= 1 else None,
            sharpe=float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else None,
            max_drawdown=float(dd.min()), current_drawdown=float(dd.iloc[-1]),
            last_day_pnl=float(curve.iloc[-1] - curve.iloc[-2]),
            last_day_ret=float(rets.iloc[-1]), as_of=str(curve.index[-1].date()),
        )
    n = len(trades)
    out["trades"] = n
    if n:
        w, l = trades[trades.pnl > 0], trades[trades.pnl <= 0]
        streak = best = worst = 0
        for p in trades.pnl:
            streak = streak + 1 if p > 0 and streak >= 0 else (1 if p > 0 else (streak - 1 if streak <= 0 else -1))
            best, worst = max(best, streak), min(worst, streak)
        out.update(
            win_rate=len(w) / n, avg_win=float(w.pnl.mean()) if len(w) else 0.0,
            avg_loss=float(l.pnl.mean()) if len(l) else 0.0,
            profit_factor=float(w.pnl.sum() / -l.pnl.sum()) if l.pnl.sum() < 0 else None,
            expectancy=float(trades.pnl.mean()), best_trade=float(trades.pnl.max()),
            worst_trade=float(trades.pnl.min()), win_streak=best, loss_streak=-worst,
        )
    return out


def by_period(trades: pd.DataFrame, curve: pd.Series, freq: str) -> list[dict]:
    rule = FREQS[freq]
    if len(curve) == 0:
        return []
    eq = curve.resample(rule).last().dropna()
    prev = eq.shift(1)
    if len(eq):
        prev.iloc[0] = curve.iloc[0]
    rows = pd.DataFrame({"end_equity": eq, "pnl": eq - prev, "ret": eq / prev - 1})
    if len(trades):
        t = trades.assign(_d=pd.to_datetime(trades.exit_date)).set_index("_d").sort_index()
        g = t.resample(rule)
        rows = rows.join(pd.DataFrame({
            "trades": g.pnl.count(), "wins": g.pnl.apply(lambda s: int((s > 0).sum())),
            "best": g.pnl.max(), "worst": g.pnl.min(),
            "top_symbol": g.apply(lambda x: x.groupby("symbol").pnl.sum().idxmax() if len(x) else None),
        }), how="outer")
    if freq == "daily":
        rows = rows[rows.index.dayofweek < 5]
    rows = rows.dropna(subset=["end_equity"])
    lab = {"daily": "%Y-%m-%d", "weekly": "wk of %Y-%m-%d", "monthly": "%b %Y", "yearly": "%Y"}[freq]
    out = []
    for ix, r in rows.iterrows():
        label = (ix - pd.Timedelta(days=4)).strftime(lab) if freq == "weekly" else ix.strftime(lab)
        rec = {"period": label, "date": str(ix.date())}
        for k, v in r.items():
            rec[k] = None if (v is None or (isinstance(v, float) and np.isnan(v))) else (
                v if isinstance(v, str) else float(v))
        out.append(rec)
    return out


def breakdown(trades: pd.DataFrame, col: str) -> list[dict]:
    if trades.empty:
        return []
    g = trades.groupby(col).pnl
    df = pd.DataFrame({"trades": g.count(), "pnl": g.sum(), "win_rate": g.apply(lambda s: (s > 0).mean())})
    return [{"name": k, **{c: float(v) for c, v in r.items()}} for k, r in df.sort_values("pnl", ascending=False).iterrows()]


def build_report(cfg, source, acc=None) -> dict:
    from .firms import firm
    from .prop import status
    trades, curve, bench, label = load_source(cfg, source, acc)
    if len(trades):
        trades = trades.sort_values("exit_date").reset_index(drop=True)
    dd = (curve / curve.cummax() - 1) if len(curve) else curve
    return {
        "source": label,
        "generated": pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d %H:%M ET"),
        "benchmark_name": cfg["benchmark"],
        "summary": summarize(trades, curve),
        "periods": {f: by_period(trades, curve, f) for f in FREQS},
        "equity": [[str(i.date()), round(float(v), 2)] for i, v in curve.items()],
        "drawdown": [[str(i.date()), round(float(v), 5)] for i, v in dd.items()],
        "benchmark": [[str(i.date()), round(float(v), 2)] for i, v in bench.items()] if bench is not None else [],
        "by_symbol": breakdown(trades, "symbol"),
        "by_reason": breakdown(trades, "side" if len(trades) and set(trades.reason) == {"import"} else "reason"),
        "trades": trades.tail(500).replace({np.nan: None}).to_dict("records") if len(trades) else [],
        "account": None if acc is None else {
            "id": acc["id"], "name": acc["name"], "firm": firm(acc["firm"])["name"],
            "platform": acc.get("platform", ""), "mode": acc["mode"]},
        "rules": status(acc.get("rules", {}), trades, curve) if acc is not None else None,
    }
