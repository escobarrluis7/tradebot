"""Rank every intraday strategy on history; save the leaderboard the live bot follows."""
import json
from datetime import datetime

import pandas as pd

from .config import ROOT
from .intraday import backtest, library, score
from .intraday_data import NY, history

OUT = ROOT / "data" / "intraday"


def run_research(cfg, quiet=False) -> dict:
    dt = cfg["daytrade"]
    strats = library(dt["universe"])
    syms = sorted({s for st in strats for s in st.symbols})
    bars, source = history(syms, dt.get("research_years", 5))
    if not quiet:
        print(f"Data: {source}; {len(bars)} symbols")
    tcfg = {**dt}
    trades, days = backtest(strats, bars, tcfg, progress=not quiet)
    board = []
    for st in strats:
        sc = score(trades[st.name], days, dt.get("gate", {}))
        board.append({"strategy": st.name, "source": st.paper, **sc})
    # rank: qualified first, then by efficiency (Sharpe), then by profit
    board.sort(key=lambda r: (r["qualified"], r.get("sharpe") or -9, r.get("total_return") or -9), reverse=True)
    res = {"generated": datetime.now(NY).strftime("%Y-%m-%d %H:%M ET"), "data": source,
           "first_day": days[0] if days else None, "last_day": days[-1] if days else None,
           "champion": board[0]["strategy"] if board else None,
           "champion_qualified": bool(board and board[0]["qualified"]), "leaderboard": board}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "leaderboard.json").write_text(json.dumps(res, indent=2))
    allt = [t for v in trades.values() for t in v]
    pd.DataFrame(allt).to_csv(OUT / "research_trades.csv", index=False)
    return res


def load_leaderboard():
    p = OUT / "leaderboard.json"
    return json.loads(p.read_text()) if p.exists() else None


def print_board(res):
    print(f"\nStrategy leaderboard ({res['first_day']} to {res['last_day']}; {res['data']})")
    rows = []
    for r in res["leaderboard"]:
        rows.append({"strategy": r["strategy"], "trades": r["trades"],
                     "total %": round((r.get("total_return") or 0) * 100, 1),
                     "sharpe": round(r.get("sharpe") or 0, 2), "t-stat": round(r.get("t_stat") or 0, 2),
                     "win %": round((r.get("win_rate") or 0) * 100, 1),
                     "max DD %": round((r.get("max_drawdown") or 0) * 100, 1),
                     "real money?": "YES" if r["qualified"] else "no"})
    print(pd.DataFrame(rows).to_string(index=False))
    top = res["leaderboard"][0]
    print(f"\nChampion: {top['strategy']} - {top['why']}")
    if not top["qualified"]:
        print("No strategy passes the real-money checks yet: the bot will trade the leader on PAPER only.")
