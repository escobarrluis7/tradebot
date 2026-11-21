import argparse
import json

import pandas as pd

from . import backtest as bt
from . import indicators as ind
from .broker import get_broker
from .config import ROOT, load_config
from .data import load
from .engine import Order
from .ensemble import build, pick


def _data(cfg, refresh=True):
    return load(cfg["watchlist"] + [cfg["benchmark"]], cfg["history_start"], refresh=refresh)


def cmd_signal(cfg, args, d=None):
    d = d or _data(cfg, refresh=not args.cached)
    scores, weights, combined, bull = build(d, cfg)
    t = combined.dropna(how="all").index[-1]
    table = pd.DataFrame({n: s.loc[t] for n, s in scores.items()})
    table["COMBINED"] = combined.loc[t]
    table = table.sort_values("COMBINED", ascending=False)
    print(f"\nSignals as of close {t.date()}  |  market regime: {'BULL' if bull.loc[t] else 'BEAR'}")
    print("Strategy weights today:", ", ".join(f"{k}={v:.2f}" for k, v in weights.loc[t].items()))
    print(table.round(3).to_string())
    choice = pick(combined.loc[t], bool(bull.loc[t]), cfg)
    print("\nTODAY'S TRADE:", f"{choice[1].upper()} {choice[0]} (score {choice[2]:.3f})" if choice
          else "none - no setup strong enough, stay in cash")
    return d, t, choice


def cmd_backtest(cfg, args):
    d = _data(cfg, refresh=not args.cached)
    scores, weights, combined, bull = build(d, cfg)
    start = args.start or str(combined.dropna(how="all").index[0].date())
    curve, trades = bt.run(d, cfg, start, combined, bull)
    rows = {"ENSEMBLE": bt.metrics(curve, trades)}
    if args.per_strategy:
        for n in scores:
            c1, t1 = bt.run(d, cfg, start, scores[n], bull)
            rows[n] = bt.metrics(c1, t1)
    bh = bt.buy_and_hold(d, cfg["benchmark"], curve.index[0], cfg["starting_cash"])
    rows[f"buy&hold {cfg['benchmark']}"] = bt.metrics(bh, pd.DataFrame())
    print(pd.DataFrame(rows).T.to_string())
    out = ROOT / "data"
    out.mkdir(exist_ok=True)
    trades.to_csv(out / "backtest_trades.csv", index=False)
    curve.to_csv(out / "backtest_equity.csv")
    if len(trades):
        print("\nLast 10 trades:")
        print(trades.tail(10)[["signal_date", "symbol", "side", "entry", "exit", "reason", "pnl"]].to_string(index=False))
    print(f"\nSaved trades and equity curve to {out}/")


def _journal(event: dict):
    import csv
    from datetime import datetime
    p = ROOT / "data" / "journal.csv"
    p.parent.mkdir(exist_ok=True)
    new = not p.exists()
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", "event", "detail"])
        if new:
            w.writeheader()
        w.writerow({"time": datetime.now().isoformat(timespec="seconds"), **event})


def _market_check(broker):
    """For Alpaca: only act when a session is open now or opens later today (ET)."""
    if not hasattr(broker, "clock"):
        return True
    from datetime import datetime
    from zoneinfo import ZoneInfo
    c = broker.clock()
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    if c["is_open"] or c["next_open"][:10] == today:
        return True
    print(f"Market is not open today (next open {c['next_open']}). Nothing to do.")
    return False


def cmd_run(cfg, args):
    if getattr(args, "account", None) or getattr(args, "all_accounts", False):
        from .runner import run_account, selected
        for acc in selected(args):
            print(f"\n=== {acc['name']} ({acc['mode']}) ===")
            try:
                for line in run_account(acc, args):
                    print(line)
            except Exception as e:           # one account failing must not stop the others
                print(f"ERROR in {acc['id']}: {e}")
        return
    broker = get_broker(cfg)
    if cfg["broker"] == "alpaca":
        return _run_alpaca(cfg, args, broker)
    d = _data(cfg)
    for f in broker.settle(d):
        print(f"Closed: {f.side} {f.symbol} {f.entry} -> {f.exit} ({f.reason})  P&L ${f.pnl:+.2f}")
        _journal({"event": "closed", "detail": f"{f.symbol} pnl {f.pnl:+.2f} ({f.reason})"})
    if broker.busy():
        print("A trade is still open/pending - no new trade today.")
    else:
        d, t, choice = cmd_signal(cfg, args, d)
        if choice:
            sym, side, score = choice
            a = float(ind.atr(d["high"], d["low"], d["close"], 14).at[t, sym])
            msg = broker.submit(Order(sym, side, str(t.date()), score, a, broker.equity), float(d["close"].at[t, sym]))
            print(msg)
            _journal({"event": "order", "detail": msg})
        else:
            _journal({"event": "no_trade", "detail": "no setup above threshold"})
    broker.save()
    print(f"Equity: ${broker.equity:,.2f}")


def _run_alpaca(cfg, args, broker):
    if not args.any_time and not _market_check(broker):
        _journal({"event": "skip", "detail": "market closed today"})
        return
    if cfg.get("exit_at") != "open" or cfg["hold_days"] != 1:
        print("note: Alpaca mode implements the default daily rotation (exit_at: open, hold_days: 1).")
    d, t, choice = cmd_signal(cfg, args)
    order, last = None, 0.0
    if choice:
        sym, side, score = choice
        a = float(ind.atr(d["high"], d["low"], d["close"], 14).at[t, sym])
        order, last = Order(sym, side, str(t.date()), score, a, broker.equity), float(d["close"].at[t, sym])
    for msg in broker.execute(order, last):
        print(msg)
        _journal({"event": "alpaca", "detail": msg})
    print(f"Equity: ${broker.equity:,.2f}")


def cmd_status(cfg, args):
    broker = get_broker(cfg)
    if hasattr(broker, "s"):
        s = broker.s
        print(f"Cash/equity: ${s['cash']:,.2f}  (start ${cfg['starting_cash']:,.2f})")
        for o in s["orders"]:
            print(f"Open/pending: {o['side']} {o['symbol']} (signal {o['signal_date']}, score {o['score']:.3f})")
        if s["history"]:
            print(pd.DataFrame(s["history"]).tail(20)[["entry_date", "symbol", "side", "entry", "exit", "reason", "pnl"]].to_string(index=False))
    else:
        a = broker.account()
        print(f"Alpaca {'LIVE' if broker.live else 'paper'} account  equity ${float(a['equity']):,.2f}  "
              f"cash ${float(a['cash']):,.2f}  buying power ${float(a['buying_power']):,.2f}")
        for p in broker.positions():
            print(f"  {p['symbol']} {p['qty']} @ {float(p['avg_entry_price']):.2f}  unrealised ${float(p['unrealized_pl']):+,.2f}")


def cmd_flatten(cfg, args):
    if getattr(args, "account", None) or getattr(args, "all_accounts", False):
        from .runner import flatten_account, selected
        for acc in selected(args):
            print(flatten_account(acc))
        return
    broker = get_broker(cfg)
    if hasattr(broker, "flatten"):
        print(broker.flatten())
    else:
        print("Paper broker exits automatically at the close - nothing to do.")


def cmd_accounts(cfg, args):
    from .accounts import automation_check, load_accounts
    from .firms import firm
    from .importers import load_trades
    for a in load_accounts()["accounts"]:
        f = firm(a["firm"])
        n = len(load_trades(a["id"]))
        print(f"{a['id']:<16} {a['name']:<26} {f['name']:<20} mode={a['mode']:<6} profile={a['profile']:<7} "
              f"bots: {f['automation']:<10} trades imported: {n}")
        warn = automation_check(a)
        if warn:
            print(f"   ! {warn}")


def cmd_import(cfg, args):
    from .accounts import get_account
    from .importers import import_file
    acc = get_account(args.account)
    for f in args.files:
        added, total = import_file(acc, f, args.format)
        print(f"{f}: {added} new trades added to {acc['id']} ({total} total)")


def cmd_daytrade(cfg, args):
    from .broker import AlpacaBroker
    from .intraday_live import DayTrader
    cfg["broker"] = "alpaca"
    b = AlpacaBroker(cfg)
    if not args.any_time and not _market_check(b):
        return
    DayTrader(cfg, b, dry_run=args.dry_run).run()
    a = b.account()
    print(f"Equity ${float(a['equity']):,.2f}  (today {float(a['equity']) - float(a['last_equity']):+,.2f})")


def cmd_research(cfg, args):
    from .research import print_board, run_research
    print_board(run_research(cfg))


def cmd_log(cfg, args):
    from datetime import date
    from .accounts import get_account
    from .importers import log_day
    acc = get_account(args.account)
    day = args.date or date.today().isoformat()
    log_day(acc, args.pnl, day, args.note or "")
    print(f"Logged {'+' if args.pnl >= 0 else ''}{args.pnl:,.2f} for {day} on {acc['name']}. "
          f"Run 'python -m tradebot dashboard' to see it in the daily/weekly/monthly/yearly views.")


def cmd_dashboard(cfg, args):
    from .dashboard import serve, write
    from .accounts import ACCOUNTS_FILE
    source = args.source or ("accounts" if ACCOUNTS_FILE.exists() else "alpaca" if cfg["broker"] == "alpaca" else "paper")
    if args.serve:
        serve(cfg, source, args.port)
    else:
        print(f"Dashboard written to {write(cfg, source, args.out)}")


def main():
    p = argparse.ArgumentParser(prog="tradebot", description="One-trade-a-day ensemble trading bot")
    p.add_argument("--config")
    p.add_argument("--profile", help="config profile, e.g. futures (default: stocks)")
    p.add_argument("--cached", action="store_true", help="use cached data, don't re-download")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("signal", help="show today's ranked signals and the chosen trade")
    b = sub.add_parser("backtest", help="historical simulation")
    b.add_argument("--start")
    b.add_argument("--per-strategy", action="store_true", help="also backtest each strategy alone")
    r = sub.add_parser("run", help="settle yesterday's trade and place today's (run once per day)")
    r.add_argument("--any-time", action="store_true", help="skip the market-open-today check")
    sub.add_parser("status", help="show paper / Alpaca account")
    fl = sub.add_parser("flatten", help="close bot positions (webhook accounts at the cutoff; Alpaca exit_at=close)")
    for sp in (r, fl):
        sp.add_argument("--account", help="an account id from accounts.yaml")
        sp.add_argument("--all-accounts", action="store_true", help="every account in accounts.yaml")
        sp.add_argument("--only-profile", dest="profile_filter", choices=["stocks", "futures"],
                        help="with --all-accounts: only accounts using this profile")
    sub.add_parser("accounts", help="list accounts from accounts.yaml with firm policies")
    im = sub.add_parser("import", help="import a trade-history CSV from your platform into an account")
    im.add_argument("--account", required=True)
    im.add_argument("--format", choices=["tradovate", "ninjatrader", "generic"])
    im.add_argument("files", nargs="+")
    dtp = sub.add_parser("daytrade", help="day trading: watch the market from 9:30 ET and take one trade (Alpaca)")
    dtp.add_argument("--dry-run", action="store_true", help="watch and log decisions, place no orders")
    dtp.add_argument("--any-time", action="store_true", help="skip the market-open-today check")
    lg = sub.add_parser("log", help="enter a day's total profit/loss by hand for an account")
    lg.add_argument("--account", required=True)
    lg.add_argument("--pnl", type=float, required=True, help="the day's P&L in dollars, e.g. 125.50 or -80")
    lg.add_argument("--date", help="YYYY-MM-DD (default today)")
    lg.add_argument("--note", help="optional label, e.g. 'NQ scalp'")
    sub.add_parser("research", help="day trading: test every intraday strategy and pick the champion")
    db = sub.add_parser("dashboard", help="build the analytics dashboard (HTML)")
    db.add_argument("--source", choices=["accounts", "paper", "alpaca", "backtest", "research"],
                    help="accounts = every account in accounts.yaml (default)")
    db.add_argument("--out", help="output file (default data/dashboard_<source>.html)")
    db.add_argument("--serve", action="store_true", help="run a local web server instead of writing a file")
    db.add_argument("--port", type=int, default=8050)
    args = p.parse_args()
    cfg = load_config(args.config, args.profile)
    {"signal": cmd_signal, "backtest": cmd_backtest, "run": cmd_run,
     "status": cmd_status, "flatten": cmd_flatten, "dashboard": cmd_dashboard,
     "accounts": cmd_accounts, "import": cmd_import, "research": cmd_research, "daytrade": cmd_daytrade, "log": cmd_log}[args.cmd](cfg, args)
