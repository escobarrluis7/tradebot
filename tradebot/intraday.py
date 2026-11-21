"""Intraday strategy library, shared by the research backtester and the live day-trading loop.

Every strategy sees only 5-minute bars that have already closed, takes at most ONE trade per day,
and is flat by the end time. Stops are hard stops (placed at the broker live; checked against the
bar's high/low in backtests, stop assumed first if both stop and target are touched in one bar)."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

BAR = "5min"


# ----------------------------------------------------------------- daily context
def day_groups(bars: pd.DataFrame):
    return {d: g for d, g in bars.groupby(bars.index.date)}


def build_context(hist: dict[str, pd.DataFrame], day, lookback=14) -> dict:
    """Facts known before `day`'s open, per symbol: daily ATR, previous close, average first-bar
    volume (for relative volume), and the 'noise area' sigma by time of day."""
    ctx = {"atr": {}, "prev_close": {}, "rvol_base": {}, "sigma": {}}
    for s, b in hist.items():
        past = b[b.index.date < day]
        if past.empty:
            continue
        g = past.groupby(past.index.date)
        dly = pd.DataFrame({"h": g.High.max(), "l": g.Low.min(), "c": g.Close.last(), "o": g.Open.first()}).tail(lookback + 1)
        if len(dly) < lookback + 1:
            continue
        tr = np.maximum(dly.h - dly.l, np.maximum((dly.h - dly.c.shift()).abs(), (dly.l - dly.c.shift()).abs()))
        ctx["atr"][s] = float(tr.iloc[1:].mean())
        ctx["prev_close"][s] = float(dly.c.iloc[-1])
        first = past[past.index.strftime("%H:%M") == "09:30"].Volume.tail(lookback)
        ctx["rvol_base"][s] = float(first.mean()) if len(first) else np.nan
        recent_days = sorted(set(past.index.date))[-lookback:]
        mv = []
        for d in recent_days:
            gd = past[past.index.date == d]
            m = (gd.Close / gd.Open.iloc[0] - 1).abs()
            m.index = gd.index.strftime("%H:%M")
            mv.append(m)
        ctx["sigma"][s] = pd.concat(mv, axis=1).mean(axis=1)
    return ctx


# ----------------------------------------------------------------- strategies
@dataclass
class Intent:
    symbol: str
    side: int          # +1 long, -1 short
    stop_dist: float   # $ distance from entry to hard stop
    target_dist: float | None = None
    note: str = ""


class Strategy:
    name = "base"
    symbols: list[str] = []
    paper = ""

    def entry(self, now: str, today: dict[str, pd.DataFrame], ctx: dict) -> Intent | None:
        return None

    def exit(self, now: str, pos: dict, today: dict[str, pd.DataFrame], ctx: dict) -> bool:
        return False


class ORB5(Strategy):
    """5-minute opening range: trade in the direction of the first 5-minute candle,
    stop 10% of daily ATR away, hold to the close (Zarattini & Aziz 2023)."""
    def __init__(self, symbol, stop_atr=0.10):
        self.symbol, self.stop_atr = symbol, stop_atr
        self.symbols = [symbol]
        self.name = f"ORB-5min {symbol}"
        self.paper = "Zarattini & Aziz (2023), 'Can Day Trading Really Be Profitable?'"

    def entry(self, now, today, ctx):
        b = today.get(self.symbol)
        if b is None or now != "09:30" or self.symbol not in ctx["atr"]:
            return None
        f = b.iloc[0]
        if f.Close == f.Open:
            return None
        return Intent(self.symbol, 1 if f.Close > f.Open else -1, self.stop_atr * ctx["atr"][self.symbol])


class ORB5StocksInPlay(Strategy):
    """Same 5-minute ORB, but on the one stock with the highest relative volume in its first
    5 minutes (the 'stock in play') - Zarattini, Barbon & Aziz (2024)."""
    def __init__(self, universe, stop_atr=0.10, min_rvol=1.0):
        self.symbols, self.stop_atr, self.min_rvol = list(universe), stop_atr, min_rvol
        self.name = "ORB-5min stock in play"
        self.paper = "Zarattini, Barbon & Aziz (2024), 'A Profitable Day Trading Strategy for the U.S. Equity Market'"

    def entry(self, now, today, ctx):
        if now != "09:30":
            return None
        best, best_rv = None, self.min_rvol
        for s in self.symbols:
            b = today.get(s)
            base = ctx["rvol_base"].get(s)
            if b is None or b.empty or not base or np.isnan(base) or s not in ctx["atr"]:
                continue
            rv = b.Volume.iloc[0] / base
            if rv > best_rv and b.Close.iloc[0] != b.Open.iloc[0]:
                best, best_rv = s, rv
        if not best:
            return None
        f = today[best].iloc[0]
        return Intent(best, 1 if f.Close > f.Open else -1, self.stop_atr * ctx["atr"][best], note=f"rvol {best_rv:.1f}")


class ORB30(Strategy):
    """Classic 30-minute opening-range breakout: enter on the first close beyond the range,
    stop at the other side, target 2R."""
    def __init__(self, symbol):
        self.symbol, self.symbols = symbol, [symbol]
        self.name = f"ORB-30min breakout {symbol}"
        self.paper = "Crabel (1990), opening range breakout"

    def entry(self, now, today, ctx):
        b = today.get(self.symbol)
        if b is None or now < "10:00":
            return None
        rng = b[b.index.strftime("%H:%M") < "10:00"]
        hi, lo, c = rng.High.max(), rng.Low.min(), b.Close.iloc[-1]
        if c > hi:
            return Intent(self.symbol, 1, c - lo, 2 * (c - lo))
        if c < lo:
            return Intent(self.symbol, -1, hi - c, 2 * (hi - c))
        return None


class NoiseArea(Strategy):
    """Intraday momentum: trade when price leaves its usual 'noise area' around the open,
    checked every 30 minutes; exit when it falls back inside (Zarattini, Aziz & Barbon 2024)."""
    def __init__(self, symbol, stop_atr=0.5):
        self.symbol, self.symbols, self.stop_atr = symbol, [symbol], stop_atr
        self.name = f"Noise-area momentum {symbol}"
        self.paper = "Zarattini, Aziz & Barbon (2024), 'Beat the Market: An Effective Intraday Momentum Strategy for SPY'"

    def _bounds(self, now, b, ctx):
        sig = ctx["sigma"].get(self.symbol)
        if sig is None or now not in sig.index:
            return None
        o, pc = b.Open.iloc[0], ctx["prev_close"][self.symbol]
        return max(o, pc) * (1 + sig[now]), min(o, pc) * (1 - sig[now])

    def _check_time(self, now):
        return now >= "09:55" and now[-2:] in ("25", "55")     # bar closing at :00 / :30

    def entry(self, now, today, ctx):
        b = today.get(self.symbol)
        if b is None or not self._check_time(now) or self.symbol not in ctx["atr"]:
            return None
        bd = self._bounds(now, b, ctx)
        if not bd:
            return None
        c = b.Close.iloc[-1]
        a = self.stop_atr * ctx["atr"][self.symbol]
        if c > bd[0]:
            return Intent(self.symbol, 1, a)
        if c < bd[1]:
            return Intent(self.symbol, -1, a)
        return None

    def exit(self, now, pos, today, ctx):
        if not self._check_time(now):
            return False
        b = today[self.symbol]
        bd = self._bounds(now, b, ctx)
        if not bd:
            return False
        c = b.Close.iloc[-1]
        return (pos["side"] > 0 and c < bd[0]) or (pos["side"] < 0 and c > bd[1])


class FirstHourMomentum(Strategy):
    """Trade in the direction of the first hour from 10:30 to the close (Gao, Han, Li & Zhou 2018 variant)."""
    def __init__(self, symbol, stop_atr=0.5):
        self.symbol, self.symbols, self.stop_atr = symbol, [symbol], stop_atr
        self.name = f"First-hour momentum {symbol}"
        self.paper = "Gao, Han, Li & Zhou (2018), 'Market intraday momentum'"

    def entry(self, now, today, ctx):
        b = today.get(self.symbol)
        if b is None or now != "10:25" or self.symbol not in ctx["atr"]:
            return None
        r = b.Close.iloc[-1] / b.Open.iloc[0] - 1
        if r == 0:
            return None
        return Intent(self.symbol, 1 if r > 0 else -1, self.stop_atr * ctx["atr"][self.symbol])


def library(universe) -> list[Strategy]:
    return [ORB5StocksInPlay(universe), ORB5("QQQ"), ORB5("SPY"), NoiseArea("SPY"), NoiseArea("QQQ"),
            ORB30("QQQ"), FirstHourMomentum("QQQ")]


# ----------------------------------------------------------------- backtest of one day
def simulate_day(strat: Strategy, today: dict[str, pd.DataFrame], ctx: dict, cfg: dict) -> dict | None:
    """Walk the day's 5-minute bars. Entry at the open of the bar after the signal."""
    slip = cfg.get("slippage_bps", 2) / 1e4
    end = cfg.get("end_time", "15:50")            # last bar START time; exit at its close
    tstr = {sym: b.index.strftime("%H:%M").values for sym, b in today.items()}   # sorted bar start times
    times = sorted({t for ts in tstr.values() for t in ts if t <= end})
    pos = None
    for i, now in enumerate(times):
        upto = {sym: b.iloc[:int(np.searchsorted(tstr[sym], now, side="right"))] for sym, b in today.items()}
        if pos is None:
            if now >= end:
                return None
            it = strat.entry(now, upto, ctx)
            if not it or it.stop_dist <= 0:
                continue
            b = today[it.symbol]
            k = int(np.searchsorted(tstr[it.symbol], now, side="right"))
            if k >= len(b):
                return None
            entry = b.Open.iloc[k] * (1 + it.side * slip)
            pos = {"symbol": it.symbol, "side": it.side, "entry": entry, "entry_time": b.index[k],
                   "stop": entry - it.side * it.stop_dist,
                   "target": entry + it.side * it.target_dist if it.target_dist else None, "note": it.note}
            continue
        b = today[pos["symbol"]]
        k = int(np.searchsorted(tstr[pos["symbol"]], now))
        if k >= len(b) or tstr[pos["symbol"]][k] != now or b.index[k] < pos["entry_time"]:
            continue
        r = b.iloc[k]
        row = b.iloc[k:k + 1]
        s = pos["side"]
        px, why = None, None
        if (s > 0 and r.Low <= pos["stop"]) or (s < 0 and r.High >= pos["stop"]):
            px, why = pos["stop"], "stop"
        elif pos["target"] and ((s > 0 and r.High >= pos["target"]) or (s < 0 and r.Low <= pos["target"])):
            px, why = pos["target"], "target"
        elif strat.exit(now, pos, upto, ctx):
            px, why = r.Close, "signal"
        elif now >= end:
            px, why = r.Close, "end of day"
        if px is not None:
            exit_px = px * (1 - s * slip)
            move = s * (exit_px / pos["entry"] - 1)
            stop_pct = abs(pos["entry"] - pos["stop"]) / pos["entry"]
            frac = min(cfg.get("risk_per_trade", 0.01) / stop_pct, cfg.get("max_position_pct", 1.0))
            return {"strategy": strat.name, "date": str(row.index[0].date()), "symbol": pos["symbol"],
                    "side": "long" if s > 0 else "short", "entry_time": pos["entry_time"].strftime("%H:%M"),
                    "exit_time": now, "entry": round(pos["entry"], 4), "exit": round(exit_px, 4),
                    "reason": why, "move": move, "ret": move * frac, "frac": frac, "note": pos["note"]}
    return None


def backtest(strats, bars: dict[str, pd.DataFrame], cfg, lookback=14, progress=False):
    days = sorted({d for b in bars.values() for d in b.index.date})
    trades = {s.name: [] for s in strats}
    tested_days = []
    for k, day in enumerate(days[lookback:]):
        ctx = build_context({s: b[(b.index.date < day)].tail(80 * (lookback + 2)) for s, b in bars.items()}, day, lookback)
        today = {s: b[b.index.date == day] for s, b in bars.items()}
        today = {s: b for s, b in today.items() if len(b) > 40}
        if not today:
            continue
        tested_days.append(str(day))
        for st in strats:
            sub = {s: today[s] for s in st.symbols if s in today}
            if not sub:
                continue
            t = simulate_day(st, sub, ctx, cfg)
            if t:
                trades[st.name].append(t)
        if progress and k % 50 == 0:
            print(f"  {day}  ({k}/{len(days) - lookback} days)")
    return trades, tested_days


def score(trades: list[dict], days: list[str], gate: dict) -> dict:
    """Profit and efficiency, plus the qualification gate for real-money trading."""
    daily = pd.Series(0.0, index=pd.to_datetime(days))
    for t in trades:
        daily[pd.Timestamp(t["date"])] += t["ret"]
    n = len(trades)
    out = {"trades": n, "days": len(days)}
    if n < 2:
        return {**out, "qualified": False, "why": "not enough trades"}
    r = pd.Series([t["ret"] for t in trades])
    eq = (1 + daily).cumprod()
    years = max(len(days) / 252, 1e-9)
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0.0
    tstat = r.mean() / (r.std() / np.sqrt(n)) if r.std() > 0 else 0.0
    half = len(daily) // 2
    h1, h2 = daily.iloc[:half].sum(), daily.iloc[half:].sum()
    wins, losses = r[r > 0].sum(), -r[r <= 0].sum()
    out.update(
        total_return=float(eq.iloc[-1] - 1), cagr=float(eq.iloc[-1] ** (1 / years) - 1) if years >= 0.5 else None,
        sharpe=float(sharpe), t_stat=float(tstat), win_rate=float((r > 0).mean()), avg_trade=float(r.mean()),
        profit_factor=float(wins / losses) if losses > 0 else None,
        max_drawdown=float((eq / eq.cummax() - 1).min()), first_half=float(h1), second_half=float(h2),
    )
    why = []
    if n < gate.get("min_trades", 150):
        why.append(f"needs {gate.get('min_trades', 150)}+ trades (has {n})")
    if tstat < gate.get("min_t_stat", 2.0):
        why.append(f"not statistically significant (t={tstat:.2f} < {gate.get('min_t_stat', 2.0)})")
    if h1 <= 0 or h2 <= 0:
        why.append("not profitable in both halves of the test")
    if (out["profit_factor"] or 0) < gate.get("min_profit_factor", 1.1):
        why.append("profit factor too low")
    out["qualified"] = not why
    out["why"] = "; ".join(why) if why else "passes every check"
    return out
