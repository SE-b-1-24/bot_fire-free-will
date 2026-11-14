"""Read local JSON data."""

import json
from pathlib import Path


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8-sig") as source:
        data = json.load(source)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: ожидается JSON-объект.")
    return data
