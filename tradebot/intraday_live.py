"""Live day trading: watch every 5-minute bar from 9:30 ET, take ONE trade with the champion
strategy, manage it, and be flat by the end time. Safe to restart mid-day."""
import json
import math
import time as _time
from datetime import datetime, timedelta

import pandas as pd

from .config import ROOT
from .intraday import build_context, library, simulate_day
from .intraday_data import NY, alpaca_bars
from .research import OUT, load_leaderboard


def _now():
    return datetime.now(NY)


def _log(msg):
    line = f"{_now().strftime('%H:%M:%S')}  {msg}"
    print(line, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "live_log.txt", "a") as f:
        f.write(f"{_now().date()} {line}\n")


class DayTrader:
    def __init__(self, cfg, broker, dry_run=False, clock=_now, fetch=alpaca_bars, sleep=_time.sleep):
        self.cfg, self.dt, self.b = cfg, cfg["daytrade"], broker
        self.dry, self.clock, self.fetch, self.sleep = dry_run, clock, fetch, sleep
        board = load_leaderboard()
        if not board:
            raise SystemExit("No strategy leaderboard yet - run: python -m tradebot research")
        strats = {s.name: s for s in library(self.dt["universe"])}
        self.strat = strats[board["champion"]]
        self.qualified = board["champion_qualified"]
        self.all_strats = list(strats.values())
        live = getattr(broker, "live", False)
        self.shadow_only = live and self.dt.get("live_requires_qualified", True) and not self.qualified
        self.today = self.clock().date()
        self.cid = f"daytrade-{self.today}"
        self.pos, self.done = None, False

    # ---------------------------------------------------------------- broker helpers
    def _resume(self):
        """If we restarted mid-day, pick up today's order/position from the broker."""
        try:
            o = self.b._req("GET", "/v2/orders:by_client_order_id", params={"client_order_id": self.cid})
        except RuntimeError:
            return
        if not o or o.get("status") in ("canceled", "rejected", "expired"):
            return
        held = {p["symbol"]: p for p in self.b.positions()}
        if o["symbol"] in held:
            p = held[o["symbol"]]
            self.pos = {"symbol": o["symbol"], "side": 1 if float(p["qty"]) > 0 else -1,
                        "entry": float(p["avg_entry_price"]), "qty": abs(float(p["qty"]))}
            _log(f"resumed: holding {self.pos['symbol']} from earlier today")
        else:
            self.done = True
            _log("today's trade already finished earlier - nothing more to do today")

    def _enter(self, it, last_px):
        c = self.dt
        eq = self.b.equity
        qty = math.floor(min(eq * c["risk_per_trade"] / it.stop_dist, eq * c["max_position_pct"] / last_px))
        if qty <= 0:
            _log(f"{it.symbol}: size rounds to 0 shares - skipping today")
            self.done = True
            return
        stop = round(last_px - it.side * it.stop_dist, 2)
        body = {"symbol": it.symbol, "qty": str(qty), "side": "buy" if it.side > 0 else "sell",
                "type": "market", "time_in_force": "day", "client_order_id": self.cid}
        if it.target_dist:
            body.update(order_class="bracket", stop_loss={"stop_price": stop},
                        take_profit={"limit_price": round(last_px + it.side * it.target_dist, 2)})
        else:
            body.update(order_class="oto", stop_loss={"stop_price": stop})
        desc = (f"{'BUY' if it.side > 0 else 'SELL SHORT'} {qty} {it.symbol} ~{last_px:.2f}, stop {stop}"
                + (f", target {body['take_profit']['limit_price']}" if it.target_dist else "") + (f" ({it.note})" if it.note else ""))
        if self.dry or self.shadow_only:
            _log(f"[{'dry run' if self.dry else 'watch only - no qualified strategy for a live account'}] would {desc}")
        else:
            self.b._req("POST", "/v2/orders", json=body)
            _log(f"ORDER: {desc}")
        self.pos = {"symbol": it.symbol, "side": it.side, "entry": last_px, "qty": qty, "simulated": self.dry or self.shadow_only}

    def _exit(self, why):
        p = self.pos
        if not p.get("simulated"):
            try:
                for o in self.b._req("GET", "/v2/orders", params={"status": "open", "symbols": p["symbol"]}) or []:
                    self.b._req("DELETE", f"/v2/orders/{o['id']}")      # cancel only this trade's stop / target
                self.b._req("DELETE", f"/v2/positions/{p['symbol']}")
            except RuntimeError as e:
                if "404" not in str(e):
                    raise
        _log(f"EXIT {p['symbol']} ({why})")
        self.pos, self.done = None, True

    def _still_open_at_broker(self):
        if self.pos.get("simulated"):
            return True
        return any(p["symbol"] == self.pos["symbol"] for p in self.b.positions())

    # ---------------------------------------------------------------- main loop
    def run(self):
        end = self.dt.get("end_time", "15:10")
        syms = sorted({s for st in self.all_strats for s in st.symbols})
        hist = self.fetch(syms, (self.clock() - timedelta(days=30)).strftime("%Y-%m-%d"),
                          end=self.clock().replace(hour=9, minute=29).isoformat())
        ctx = build_context(hist, self.today)
        mode = "DRY RUN" if self.dry else ("WATCH ONLY" if self.shadow_only else ("paper" if not getattr(self.b, "live", False) else "LIVE"))
        _log(f"day trader started [{mode}] - strategy: {self.strat.name}"
             f"{'' if self.qualified else ' (best so far; not yet qualified for real money)'}")
        self._resume()
        seen = None
        open_t = self.clock().replace(hour=9, minute=30, second=0, microsecond=0)
        stop_at = self.clock().replace(hour=int(end[:2]), minute=int(end[3:]), second=0, microsecond=0) + timedelta(minutes=5)
        while not self.done:
            now = self.clock()
            if now >= stop_at:
                if self.pos:
                    self._exit("end of day")
                break
            if now < open_t + timedelta(minutes=5, seconds=10):
                self.sleep(15)
                continue
            bars = self.fetch(self.strat.symbols, open_t.isoformat())
            done_cut = now - timedelta(minutes=5, seconds=5)        # a bar is final 5 min after it starts
            bars = {s: b[b.index <= done_cut] for s, b in bars.items() if len(b)}
            if not bars:
                self.sleep(15)
                continue
            latest = max(b.index[-1] for b in bars.values())
            if seen is not None and latest <= seen:
                self.sleep(15)
                continue
            seen = latest
            t = latest.strftime("%H:%M")
            if self.pos is None:
                if t < end:
                    it = self.strat.entry(t, bars, ctx)
                    if it and it.stop_dist > 0:
                        self._enter(it, float(bars[it.symbol].Close.iloc[-1]))
            else:
                if not self._still_open_at_broker():
                    _log(f"{self.pos['symbol']} closed by its stop-loss or target at the broker")
                    self.pos, self.done = None, True
                elif self.strat.exit(t, self.pos, bars, ctx):
                    self._exit("strategy exit signal")
                elif t >= end:
                    self._exit("end of day")
            self.sleep(15)
        if not self.pos and not self.done:
            _log("no setup today - stayed flat")
        self._shadow(hist)

    def _shadow(self, hist):
        """After the session: score every strategy on today's bars (keeps the tournament current)."""
        try:
            syms = sorted({s for st in self.all_strats for s in st.symbols})
            today = self.fetch(syms, self.clock().replace(hour=9, minute=30).isoformat())
            today = {s: b[b.index.date == self.today] for s, b in today.items()}
            ctx = build_context(hist, self.today)
            rows = []
            for st in self.all_strats:
                t = simulate_day(st, {s: today[s] for s in st.symbols if s in today and len(today[s])}, ctx, self.dt)
                rows.append(t or {"strategy": st.name, "date": str(self.today), "ret": 0.0, "reason": "no trade"})
            p = OUT / "shadow.csv"
            df = pd.DataFrame(rows)
            df.to_csv(p, mode="a", header=not p.exists(), index=False)
            best = max(rows, key=lambda r: r["ret"])
            _log(f"today's best strategy on paper: {best['strategy']} ({best['ret'] * 100:+.2f}% of account)")
        except Exception as e:                      # never let bookkeeping break the trading day
            _log(f"shadow scoring skipped: {e}")
