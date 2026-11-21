"""Order relays and alert delivery for non-Alpaca accounts."""
import json
import os
from urllib.parse import urlparse

import requests


def _post(url, **kw):
    r = requests.post(url, timeout=20, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {urlparse(url).netloc} -> {r.status_code}: {r.text[:200]}")
    return r


def notify(text: str, url_env: str = "NOTIFY_URL") -> str:
    """Send an alert to Discord, Slack or ntfy (by URL), else just print."""
    url = os.environ.get(url_env or "NOTIFY_URL")
    if not url:
        return "alert not sent (set NOTIFY_URL to receive alerts on your phone)"
    host = urlparse(url).netloc
    if "discord" in host:
        _post(url, json={"content": text[:1900]})
    elif "slack" in host:
        _post(url, json={"text": text})
    else:                                   # ntfy.sh and anything that accepts a plain body
        _post(url, data=text.encode(), headers={"Title": "Trading bot"})
    return f"alert sent ({host})"


class WebhookRelay:
    """Sends orders to a relay (TradersPost, PickMyTrade, ...) that executes on your platform.
    format 'traderspost' follows TradersPost's signal JSON; 'generic' sends the full trade plan."""

    def __init__(self, acc):
        c = acc["connector"]
        self.fmt = c.get("format", "traderspost")
        env = c.get("url_env")
        self.url = os.environ.get(env or "")
        if not self.url:
            raise SystemExit(f"{acc['id']}: set the env var {env} to your relay's webhook URL")

    def entry(self, plan: dict) -> str:
        if self.fmt == "traderspost":
            body = {"ticker": plan["contract"], "action": "buy" if plan["side"] == "long" else "sell",
                    "quantity": plan["qty"], "orderType": "market", "signalPrice": plan["ref_price"],
                    "stopLoss": {"type": "stop", "stopPrice": plan["stop"]}}
        else:
            body = {"event": "entry", **plan}
        _post(self.url, json=body)
        return f"sent to relay: {json.dumps(body)}"

    def exit(self, contract: str) -> str:
        body = {"ticker": contract, "action": "exit"} if self.fmt == "traderspost" else {"event": "exit", "contract": contract}
        _post(self.url, json=body)
        return f"sent to relay: {json.dumps(body)}"
