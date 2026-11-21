from pathlib import Path

import os

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | None = None, profile: str | None = None) -> dict:
    """config.yaml, optionally with a named profile (e.g. 'futures') merged over the top."""
    p = Path(path) if path else ROOT / "config.yaml"
    with open(p) as f:
        cfg = yaml.safe_load(f)
    profiles = cfg.pop("profiles", {}) or {}
    cfg["profile"] = profile or "stocks"
    if profile and profile != "stocks":
        if profile not in profiles:
            raise SystemExit(f"Unknown profile '{profile}'. Defined: stocks, {', '.join(profiles)}")
        for k, v in profiles[profile].items():
            cfg[k] = v
    if os.environ.get("TRADEBOT_BROKER"):          # lets the scheduled job use Alpaca without editing the file
        cfg["broker"] = os.environ["TRADEBOT_BROKER"]
    return cfg
