"""Build the analytics dashboard (a single self-contained HTML file) and optionally serve it."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .analytics import build_report
from .config import ROOT

TEMPLATE = Path(__file__).with_name("dashboard_template.html")


def _html(reports: dict) -> str:
    data = json.dumps({"reports": reports, "order": list(reports)}, default=str).replace("</", "<\\/")
    return TEMPLATE.read_text().replace("__DATA__", data)


def render_leaderboard_only() -> str:
    """Dashboard page for the strategy tournament when no account data is reachable (e.g. no keys yet)."""
    from .research import load_leaderboard
    lb = load_leaderboard()
    rep = {"source": "Day-trading strategy research", "generated": lb["generated"] if lb else "", "benchmark_name": "",
           "summary": {}, "periods": {"daily": [], "weekly": [], "monthly": [], "yearly": []}, "equity": [], "drawdown": [],
           "benchmark": [], "by_symbol": [], "by_reason": [], "trades": [], "account": None, "rules": None, "leaderboard": lb}
    return _html({"research": rep})


def render(cfg, source) -> str:
    if source == "research":
        return render_leaderboard_only()
    if source == "accounts":
        return render_accounts()
    return _html({source: build_report(cfg, source)})


def render_accounts(only=None) -> str:
    """One dashboard with a picker for every account in accounts.yaml."""
    from .accounts import load_accounts
    from .config import load_config
    reports = {}
    for acc in load_accounts()["accounts"]:
        if only and acc["id"] not in only:
            continue
        cfg = load_config(profile=acc["profile"])
        try:
            reports[acc["id"]] = build_report(cfg, "account", acc)
            if acc.get("strategy") == "daytrade":
                from .research import load_leaderboard
                reports[acc["id"]]["leaderboard"] = load_leaderboard()
        except (SystemExit, Exception) as e:          # e.g. Alpaca keys not set on this machine
            print(f"skipping {acc['id']}: {e}")
    if not reports:
        raise SystemExit("No accounts could be loaded - check accounts.yaml")
    return _html(reports)


def write(cfg, source, out=None) -> Path:
    out = Path(out) if out else ROOT / "data" / f"dashboard_{source}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(cfg, source))
    return out


def serve(cfg, source, port=8050, ttl=300):
    cache = {"t": 0, "html": ""}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            if time.time() - cache["t"] > ttl:
                cache["html"], cache["t"] = render(cfg, source), time.time()
            body = cache["html"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    print(f"Dashboard ({source}) on http://localhost:{port}  - refreshes every {ttl // 60} min. Ctrl+C to stop.")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
