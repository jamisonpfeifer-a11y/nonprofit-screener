"""Loads config.yaml from the project root (or a path you pass in)."""
from pathlib import Path
import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path=None):
    with open(path or DEFAULT_PATH) as f:
        return yaml.safe_load(f)
