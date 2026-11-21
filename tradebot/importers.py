"""Import trade history exported from trading platforms into a normalised per-account store.

Supported exports:
  tradovate   - Tradovate "Performance" report CSV (symbol, qty, buyPrice, sellPrice, pnl, boughtTimestamp, soldTimestamp)
  ninjatrader - NinjaTrader Trade Performance > Trades grid export (Instrument, Market pos., Qty, Entry/Exit price/time, Profit)
  generic     - any CSV whose headers resemble symbol / side / qty / entry / exit / pnl / entry time / exit time
"""
import re
from pathlib import Path

import pandas as pd

from .config import ROOT

STORE = ROOT / "data" / "accounts"
COLS = ["entry_time", "exit_time", "entry_date", "exit_date", "symbol", "side", "qty",
        "entry", "exit", "pnl", "ret", "reason"]

ALIASES = {
    "symbol": ["symbol", "instrument", "ticker", "contract", "market"],
    "side": ["side", "market pos.", "market pos", "position", "direction", "type", "b/s"],
    "qty": ["qty", "quantity", "contracts", "size", "filled qty"],
    "entry": ["entry price", "entry", "open price", "avg entry", "entryprice"],
    "exit": ["exit price", "exit", "close price", "avg exit", "exitprice"],
    "pnl": ["pnl", "p&l", "profit", "net p&l", "net pnl", "realized pnl", "realized p&l", "net profit", "gain"],
    "entry_time": ["entry time", "open time", "entry date", "opened", "entrytime", "date opened"],
    "exit_time": ["exit time", "close time", "exit date", "closed", "exittime", "date closed"],
}


def money(v) -> float:
    """'$1,234.50' -> 1234.5, '$(12.50)' / '($12.50)' / '-$12.50' -> -12.5"""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    neg = s.startswith("(") or s.startswith("-") or "(" in s
    s = re.sub(r"[^0-9.]", "", s)
    return -float(s) if neg and s else (float(s) if s else 0.0)


def _norm_symbol(s: str) -> str:
    return str(s).split(" ")[0].upper()


def parse_tradovate(df: pd.DataFrame) -> pd.DataFrame:
    bt = pd.to_datetime(df["boughtTimestamp"])
    st = pd.to_datetime(df["soldTimestamp"])
    long_ = bt <= st
    return pd.DataFrame({
        "entry_time": bt.where(long_, st), "exit_time": st.where(long_, bt),
        "symbol": df["symbol"].map(_norm_symbol), "side": long_.map({True: "long", False: "short"}),
        "qty": df["qty"].astype(float),
        "entry": df["buyPrice"].where(long_, df["sellPrice"]).astype(float),
        "exit": df["sellPrice"].where(long_, df["buyPrice"]).astype(float),
        "pnl": df["pnl"].map(money),
    })


def parse_generic(df: pd.DataFrame) -> pd.DataFrame:
    lower = {c.lower().strip(): c for c in df.columns}
    pick = {}
    for k, names in ALIASES.items():
        for n in names:
            if n in lower:
                pick[k] = lower[n]
                break
    missing = [k for k in ("symbol", "pnl", "exit_time") if k not in pick]
    if missing:
        raise SystemExit(f"Couldn't find columns for {missing}. Headers in file: {list(df.columns)}")
    side = df[pick["side"]].astype(str).str.lower() if "side" in pick else pd.Series("long", index=df.index)
    side = side.map(lambda s: "short" if any(w in s for w in ("short", "sell", "s")) and "long" not in s and s != "buy" else "long")
    exit_t = pd.to_datetime(df[pick["exit_time"]])
    out = pd.DataFrame({
        "entry_time": pd.to_datetime(df[pick["entry_time"]]) if "entry_time" in pick else exit_t,
        "exit_time": exit_t,
        "symbol": df[pick["symbol"]].map(_norm_symbol), "side": side,
        "qty": df[pick["qty"]].map(money).abs() if "qty" in pick else 1.0,
        "entry": df[pick["entry"]].map(money) if "entry" in pick else float("nan"),
        "exit": df[pick["exit"]].map(money) if "exit" in pick else float("nan"),
        "pnl": df[pick["pnl"]].map(money),
    })
    return out


PARSERS = {"tradovate": parse_tradovate, "ninjatrader": parse_generic, "generic": parse_generic}


def detect_format(df) -> str:
    cols = set(df.columns)
    if {"boughtTimestamp", "soldTimestamp", "buyPrice", "sellPrice"} <= cols:
        return "tradovate"
    if {"Instrument", "Market pos.", "Entry price"} <= cols:
        return "ninjatrader"
    return "generic"


def store_path(acc_id) -> Path:
    return STORE / acc_id / "trades.csv"


def load_trades(acc_id) -> pd.DataFrame:
    p = store_path(acc_id)
    if not p.exists():
        return pd.DataFrame(columns=COLS)
    return pd.read_csv(p)


def import_file(acc: dict, path: str, fmt: str | None = None) -> tuple[int, int]:
    df = pd.read_csv(path)
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    fmt = fmt or acc.get("import_format") or detect_format(df)
    if fmt == "tradovate" and "boughtTimestamp" not in df.columns:
        fmt = detect_format(df)
    t = PARSERS[fmt](df).dropna(subset=["exit_time"])
    size = float(acc.get("rules", {}).get("account_size") or 0)
    t["entry_date"] = t.entry_time.dt.date.astype(str)
    t["exit_date"] = t.exit_time.dt.date.astype(str)
    t["entry_time"] = t.entry_time.astype(str)
    t["exit_time"] = t.exit_time.astype(str)
    t["ret"] = t.pnl / size if size else float("nan")
    t["reason"] = "import"
    old = load_trades(acc["id"])
    key = ["entry_time", "exit_time", "symbol", "qty", "entry", "exit", "pnl"]
    allt = pd.concat([old, t[COLS]], ignore_index=True)
    allt[key[3:]] = allt[key[3:]].astype(float).round(6)
    allt = allt.drop_duplicates(subset=key).sort_values("exit_time")
    p = store_path(acc["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    allt.to_csv(p, index=False)
    return len(allt) - len(old), len(allt)
