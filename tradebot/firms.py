"""Prop-firm presets: automation policy and session rules.

Plan numbers (drawdown, daily loss limit, profit target, contract limits) differ
by plan and change often, so they live in accounts.yaml where you fill in the
values from YOUR plan. Policies below were checked against public sources in
November 2026; re-check your firm's help center, since firms change them often.
"""

FIRMS = {
    "lucid": {
        "name": "Lucid Trading",
        "automation": "allowed",          # bots allowed within firm rules; no HFT / microscalping
        "flatten_by": "16:45",            # Tradovate live accounts; Rithmic live accounts 16:15
        "platforms": ["Tradovate", "NinjaTrader", "TradingView", "Rithmic platforms"],
        "notes": "Bots allowed if they follow firm rules; HFT and microscalping prohibited; "
                 "LucidDirect may require written approval for automation - confirm with Lucid.",
    },
    "take_profit_trader": {
        "name": "Take Profit Trader",
        "automation": "prohibited",       # Universal Trading Policies: no bots/algos (approved copiers only)
        "flatten_by": "16:55",
        "platforms": ["Tradovate", "NinjaTrader", "TradingView", "Quantower"],
        "notes": "Automated/algorithmic trading prohibited; copying between your own accounts "
                 "only with approved copiers. Use alerts mode and place trades yourself.",
    },
    "alpha_futures": {
        "name": "Alpha Futures",
        "automation": "prohibited",       # 'AI, bots and other automated trading mechanisms' prohibited
        "flatten_by": "16:50",
        "platforms": ["Tradovate", "NinjaTrader", "Quantower", "TradingView"],
        "notes": "Full automation prohibited on all account types; signal tools are fine if you "
                 "place and manage each trade yourself. Use alerts mode.",
    },
    "other": {
        "name": "Other prop firm",
        "automation": "unknown",
        "flatten_by": "16:45",
        "platforms": [],
        "notes": "Check the firm's rules on bots, copiers and overnight holds before using auto mode.",
    },
    "broker": {
        "name": "Personal brokerage",
        "automation": "allowed",
        "flatten_by": None,
        "platforms": [],
        "notes": "",
    },
}


def firm(key: str) -> dict:
    return FIRMS.get(key, FIRMS["other"])
