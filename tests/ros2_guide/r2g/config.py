"""Stage 2 settings (stage2.yaml)."""
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
CONFIG = HERE / "stage2.yaml"


def load(path: Path = CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
