"""Build the analytics dashboard (a single self-contained HTML file) and optionally serve it."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .analytics import build_report
from .config import ROOT

TEMPLATE = Path(__file__).with_name("dashboard_template.html")


def render(cfg, source) -> str:
    report = build_report(cfg, source)
    data = json.dumps(report, default=str).replace("</", "<\\/")
    return TEMPLATE.read_text().replace("__DATA__", data)


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
