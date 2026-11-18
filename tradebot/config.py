from pathlib import Path

import os

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.yaml"
    with open(p) as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("TRADEBOT_BROKER"):          # lets the scheduled job use Alpaca without editing the file
        cfg["broker"] = os.environ["TRADEBOT_BROKER"]
    return cfg
