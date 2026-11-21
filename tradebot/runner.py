"""Run the daily decision for one account from accounts.yaml."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from . import indicators as ind
from .accounts import automation_check, load_accounts
from .config import ROOT, load_config
from .connectors import WebhookRelay, notify
from .engine import position_size
from .firms import firm
from .instruments import CONTRACTS, front_month, round_tick, spec

NY = ZoneInfo("America/New_York")


def account_cfg(acc) -> dict:
    cfg = load_config(profile=acc["profile"])
    mc = acc.get("rules", {}).get("max_contracts")
    if mc and cfg["profile"] == "futures":
        cfg["max_contracts"] = mc if cfg.get("full_size") else mc * 10   # plan limits are in minis; 10 micros = 1 mini
    if acc.get("rules", {}).get("account_size"):
        cfg["starting_cash"] = acc["rules"]["account_size"]
    return cfg


def _journal(acc_id, event, detail):
    import csv
    p = ROOT / "data" / "accounts" / acc_id / "journal.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with open(p, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "event", "detail"])
        w.writerow([datetime.now(NY).isoformat(timespec="seconds"), event, detail])


def plan_trade(acc, cfg, args):
    """Signal + sizing for an account. Returns (plan | None, reason)."""
    from .analytics import from_account
    from .cli import cmd_signal
    from .prop import risk_budget, status

    rules = acc.get("rules", {})
    trades, curve, _, _ = from_account(cfg, acc)
    st = status(rules, trades, curve) if rules.get("max_drawdown") else None
    if st and st["buffer"] <= 0:
        return None, "drawdown limit reached - no trading"
    d, t, choice = cmd_signal(cfg, args)
    if not choice:
        return None, "no setup strong enough today - stay flat"
    sym, side, score = choice
    a = float(ind.atr(d["high"], d["low"], d["close"], 14).at[t, sym])
    last = float(d["close"].at[t, sym])
    budget = risk_budget(cfg, rules, st)
    futures = cfg["profile"] == "futures" and sym in CONTRACTS
    c = spec(sym, cfg.get("full_size", False)) if futures else None
    mult = c.mult if futures else 1.0
    qty = int(position_size(cfg["starting_cash"], last, a, cfg, mult, budget))
    if qty <= 0:
        return None, f"{sym}: one unit would risk more than the ${budget:,.0f} budget - skipped"
    sgn = 1 if side == "long" else -1
    stop = last - sgn * cfg["stop_atr_mult"] * a
    plan = {
        "account": acc["id"], "signal_date": str(t.date()), "symbol": sym, "side": side,
        "contract": front_month(c.root) if futures else sym,
        "qty": qty, "ref_price": round(last, 2),
        "stop": round_tick(stop, c.tick) if futures else round(stop, 2),
        "risk_dollars": round(cfg["stop_atr_mult"] * a * mult * qty, 2), "score": round(score, 3),
        "enter": "at the 6:00 PM ET Globex open" if futures else "at the next market open",
        "flatten_by": min([x for x in (cfg.get("flatten_time"), firm(acc["firm"])["flatten_by"]) if x] or [None]),
    }
    return plan, "ok"


def describe(acc, plan) -> str:
    unit = "contract" if plan["contract"] != plan["symbol"] else "share"
    s = "" if plan["qty"] == 1 else "s"
    lines = [f"{acc['name']}: {'BUY' if plan['side'] == 'long' else 'SELL SHORT'} {plan['qty']} {plan['contract']} {unit}{s} {plan['enter']}",
             f"Stop-loss {plan['stop']} (risk about ${plan['risk_dollars']:,.0f})"]
    if plan.get("flatten_by"):
        lines.append(f"Close the position by {plan['flatten_by']} ET")
    lines.append(f"Signal from close of {plan['signal_date']}, score {plan['score']}")
    return "\n".join(lines)


def run_account(acc, args, notify_env="NOTIFY_URL") -> list[str]:
    out = []
    if acc["mode"] == "track":
        return [f"{acc['id']}: tracking only - nothing to do"]
    if acc.get("strategy") == "daytrade":
        return [f"{acc['id']}: day-trading account - handled by 'python -m tradebot daytrade'"]
    blocked = automation_check(acc)
    if blocked:
        _journal(acc["id"], "blocked", blocked)
        return [f"BLOCKED: {blocked}"]
    cfg = account_cfg(acc)
    if acc.get("connector", {}).get("type") == "alpaca" and acc["mode"] == "auto":
        from .broker import AlpacaBroker
        from .cli import _run_alpaca
        cfg["broker"] = "alpaca"
        _run_alpaca(cfg, args, AlpacaBroker(cfg))
        return [f"{acc['id']}: Alpaca run complete"]

    plan, reason = plan_trade(acc, cfg, args)
    if plan is None:
        msg = f"{acc['name']}: {reason}"
        _journal(acc["id"], "no_trade", reason)
        out.append(msg)
        if acc["mode"] == "signal":
            out.append(notify(msg, notify_env))
        return out

    text = describe(acc, plan)
    out.append(text)
    if acc["mode"] == "signal":
        out.append(notify("TRADE IDEA (place it yourself)\n" + text, notify_env))
        _journal(acc["id"], "signal", json.dumps(plan))
    else:
        relay = WebhookRelay(acc)
        out.append(relay.entry(plan))
        (ROOT / "data" / "accounts" / acc["id"]).mkdir(parents=True, exist_ok=True)
        (ROOT / "data" / "accounts" / acc["id"] / "open_plan.json").write_text(json.dumps(plan))
        _journal(acc["id"], "order", json.dumps(plan))
        out.append(notify("BOT ORDER SENT\n" + text, notify_env))
    return out


def flatten_account(acc) -> str:
    p = ROOT / "data" / "accounts" / acc["id"] / "open_plan.json"
    if acc["mode"] != "auto" or acc.get("connector", {}).get("type") != "webhook":
        return f"{acc['id']}: not a webhook auto account - nothing to flatten"
    if not p.exists():
        return f"{acc['id']}: no open bot position on record"
    plan = json.loads(p.read_text())
    msg = WebhookRelay(acc).exit(plan["contract"])
    p.unlink()
    _journal(acc["id"], "flatten", plan["contract"])
    return msg


def selected(args):
    accs = load_accounts()["accounts"]
    if getattr(args, "account", None):
        accs = [a for a in accs if a["id"] == args.account]
        if not accs:
            raise SystemExit(f"No account '{args.account}' in accounts.yaml")
    elif getattr(args, "profile_filter", None):
        accs = [a for a in accs if a["profile"] == args.profile_filter]
    return accs
