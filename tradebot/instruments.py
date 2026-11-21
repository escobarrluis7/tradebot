"""Futures contract specs and front-month codes (CME equity-index micros)."""
from dataclasses import dataclass
from datetime import date, timedelta

MONTH_CODES = "FGHJKMNQUVXZ"


@dataclass(frozen=True)
class Contract:
    data_symbol: str     # Yahoo continuous symbol used for signals
    root: str            # contract you trade
    mult: float          # $ per 1.0 point
    tick: float
    name: str


CONTRACTS = {
    "ES=F": Contract("ES=F", "MES", 5.0, 0.25, "Micro E-mini S&P 500"),
    "NQ=F": Contract("NQ=F", "MNQ", 2.0, 0.25, "Micro E-mini Nasdaq-100"),
    "YM=F": Contract("YM=F", "MYM", 0.5, 1.0, "Micro E-mini Dow"),
    "RTY=F": Contract("RTY=F", "M2K", 5.0, 0.10, "Micro E-mini Russell 2000"),
}
# Full-size equivalents if your plan trades minis
FULL_SIZE = {"MES": ("ES", 50.0), "MNQ": ("NQ", 20.0), "MYM": ("YM", 5.0), "M2K": ("RTY", 50.0)}


def spec(symbol: str, use_full_size=False) -> Contract:
    c = CONTRACTS[symbol]
    if use_full_size:
        root, mult = FULL_SIZE[c.root]
        return Contract(c.data_symbol, root, mult, c.tick, c.name.replace("Micro ", ""))
    return c


def _third_friday(y, m):
    d = date(y, m, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def front_month(root: str, today: date | None = None) -> str:
    """Quarterly equity-index contract (H/M/U/Z), rolling 8 days before expiry
    (the usual volume roll). Returns e.g. 'MESZ2026'."""
    today = today or date.today()
    y = today.year
    for m in (3, 6, 9, 12):
        if today <= _third_friday(y, m) - timedelta(days=8):
            return f"{root}{MONTH_CODES[m - 1]}{y}"
    return f"{root}H{y + 1}"


def round_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 6)


def multiplier(cfg, symbol) -> float:
    if cfg.get("profile") == "futures" and symbol in CONTRACTS:
        return spec(symbol, cfg.get("full_size", False)).mult
    return 1.0
