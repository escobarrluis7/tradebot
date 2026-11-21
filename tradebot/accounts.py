"""Load accounts.yaml and enforce firm automation policies."""
from pathlib import Path

import yaml

from .config import ROOT
from .firms import firm

ACCOUNTS_FILE = ROOT / "accounts.yaml"


def load_accounts(path=None) -> dict:
    p = Path(path) if path else ACCOUNTS_FILE
    if not p.exists():
        return {"accounts": [], "notify": {}}
    data = yaml.safe_load(p.read_text()) or {}
    data.setdefault("accounts", [])
    data.setdefault("notify", {})
    ids = [a["id"] for a in data["accounts"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("accounts.yaml: account ids must be unique")
    for a in data["accounts"]:
        a.setdefault("name", a["id"])
        a.setdefault("firm", "other")
        a.setdefault("mode", "track")
        a.setdefault("profile", "stocks")
        a.setdefault("rules", {})
        a.setdefault("connector", {})
        if a["mode"] not in ("auto", "signal", "track"):
            raise SystemExit(f"{a['id']}: mode must be auto, signal or track")
    return data


def get_account(acc_id, path=None) -> dict:
    for a in load_accounts(path)["accounts"]:
        if a["id"] == acc_id:
            return a
    raise SystemExit(f"No account '{acc_id}' in accounts.yaml")


def automation_check(acc) -> str | None:
    """Return a reason auto mode is blocked, or None if it may proceed."""
    if acc["mode"] != "auto":
        return None
    f = firm(acc["firm"])
    pol = f["automation"]
    if pol == "prohibited":
        return (f"{f['name']} prohibits automated trading. {acc['id']} must use mode: signal "
                f"(the bot alerts you, you place the trade).")
    if pol == "unknown" and not acc.get("i_confirm_firm_allows_automation"):
        return (f"{acc['id']}: automation policy for this firm is unknown. Check the firm's rules, then add "
                f"'i_confirm_firm_allows_automation: true' to the account if bots are allowed.")
    return None
