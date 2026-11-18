"""Brokers. PaperBroker simulates fills from real daily bars using the same
engine as the backtester. AlpacaBroker places real orders (paper endpoint by default)."""
import json
import os
from dataclasses import asdict

import pandas as pd
import requests

from .config import ROOT
from .engine import Order, simulate

STATE = ROOT / "data" / "paper_state.json"


class PaperBroker:
    """Orders are queued at night and filled from the next real daily bars."""

    def __init__(self, cfg):
        self.cfg = cfg
        if STATE.exists():
            self.s = json.loads(STATE.read_text())
        else:
            self.s = {"cash": float(cfg["starting_cash"]), "orders": [], "history": []}
        self._bars_seen = {}

    def save(self):
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(self.s, indent=2))

    @property
    def equity(self):
        return self.s["cash"]

    def _bars(self, d, o):
        idx = d["close"].index
        after = idx[idx > pd.Timestamp(o["signal_date"])]
        return pd.DataFrame({f: d[f][o["symbol"]].reindex(after) for f in ("open", "high", "low", "close")})

    def settle(self, d):
        fills, keep = [], []
        for p in self.s["orders"]:
            bars = self._bars(d, p)
            self._bars_seen[p["signal_date"]] = len(bars)
            f = simulate(Order(**p), bars, self.cfg)
            if f is None:
                keep.append(p)
            else:
                self.s["cash"] += f.pnl
                self.s["history"].append(f.dict())
                fills.append(f)
        self.s["orders"] = keep
        return fills

    def busy(self):
        """Busy unless every open trade's only remaining event is an exit at the next open
        (then today's new trade simply buys at that same open - one rotation per day)."""
        c = self.cfg
        if c.get("exit_at", "close") != "open":
            return bool(self.s["orders"])
        return any(self._bars_seen.get(p["signal_date"], 0) < c["hold_days"] for p in self.s["orders"])

    def submit(self, order: Order, last_close: float):
        self.s["orders"].append(asdict(order))
        return f"paper order queued: {order.side} {order.symbol} at next open"


class AlpacaBroker:
    """Alpaca REST API. Needs ALPACA_API_KEY / ALPACA_SECRET_KEY env vars.
    Paper endpoint by default. Real money requires BOTH `allow_live: true` in
    config.yaml AND the env var ALPACA_LIVE=1, so it can't be switched on by accident."""

    def __init__(self, cfg):
        self.cfg = cfg
        live = os.environ.get("ALPACA_LIVE") == "1"
        if live and not cfg.get("allow_live"):
            raise SystemExit("ALPACA_LIVE=1 is set but config.yaml has allow_live: false - refusing to trade real money.")
        self.live = live
        self.base = "https://api.alpaca.markets" if live else "https://paper-api.alpaca.markets"
        try:
            self.h = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
                      "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
        except KeyError:
            raise SystemExit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY (see README: Connecting Alpaca).")

    def _req(self, method, path, **kw):
        r = requests.request(method, self.base + path, headers=self.h, timeout=30, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Alpaca {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else None

    @property
    def equity(self):
        return float(self._req("GET", "/v2/account")["equity"])

    def account(self):
        return self._req("GET", "/v2/account")

    def clock(self):
        return self._req("GET", "/v2/clock")

    def busy(self):
        if self.cfg.get("exit_at", "close") == "open":
            return False          # yesterday's position is being sold at this same open
        return bool(self._req("GET", "/v2/positions"))

    def settle(self, d):
        return []

    def positions(self):
        return self._req("GET", "/v2/positions") or []

    def flatten(self):
        """Cancel open orders (incl. stop legs) and close every position at market (exit_at: close mode)."""
        return self._req("DELETE", "/v2/positions", params={"cancel_orders": "true"})

    def _order_exists(self, cid):
        try:
            o = self._req("GET", "/v2/orders:by_client_order_id", params={"client_order_id": cid})
            return o and o.get("status") not in ("canceled", "rejected", "expired")
        except RuntimeError as e:
            if "404" in str(e):
                return False
            raise

    def execute(self, order: Order | None, last_close: float) -> list[str]:
        """Daily rotation, safe to re-run: sell what we no longer want at the open, buy today's pick
        with a protective stop. If today's pick is already held, keep it and renew its stop."""
        from .engine import position_size
        c, log = self.cfg, []
        cid = f"tradebot-{order.signal_date}-{order.symbol}" if order else None
        if cid and self._order_exists(cid):
            return [f"today's order ({cid}) is already in place - nothing to do"]
        held = {p["symbol"]: p for p in self.positions()}
        self._req("DELETE", "/v2/orders")                       # cancel yesterday's stop legs
        for sym in held:
            if not order or sym != order.symbol:
                self._req("DELETE", f"/v2/positions/{sym}")       # market sell, fills at the open
                log.append(f"closing {sym} ({held[sym]['qty']} shares) at the open")
        if not order:
            return log + ["no trade today - account will be in cash"]
        sgn = 1 if order.side == "long" else -1
        stop = round(last_close - sgn * c["stop_atr_mult"] * order.atr, 2)
        if order.symbol in held:
            qty = abs(int(float(held[order.symbol]["qty"])))
            self._req("POST", "/v2/orders", json={
                "symbol": order.symbol, "qty": str(qty), "side": "sell" if sgn > 0 else "buy",
                "type": "stop", "stop_price": stop, "time_in_force": "day", "client_order_id": cid})
            return log + [f"{order.symbol} is still the top pick - holding {qty} shares, stop renewed at {stop}"]
        qty = int(position_size(order.equity, last_close, order.atr, c))
        if qty <= 0:
            return log + [f"position size for {order.symbol} rounds to 0 shares - skipped (account too small)"]
        r = self._req("POST", "/v2/orders", json={
            "symbol": order.symbol, "qty": str(qty), "side": "buy" if sgn > 0 else "sell",
            "type": "market", "time_in_force": "day", "client_order_id": cid,
            "order_class": "oto", "stop_loss": {"stop_price": stop}})
        return log + [f"{'LIVE' if self.live else 'paper'} order {r['id']}: "
                      f"{'buy' if sgn > 0 else 'sell short'} {qty} {order.symbol} at the open, protective stop {stop}"]


def get_broker(cfg):
    return AlpacaBroker(cfg) if cfg["broker"] == "alpaca" else PaperBroker(cfg)
