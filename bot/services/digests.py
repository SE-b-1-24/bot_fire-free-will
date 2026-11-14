"""Load a digest and pair regions with stable Telegram callback data."""

import asyncio
from hashlib import sha256
from pathlib import Path

from ..sources.burnbeat import fetch_digest
from ..sources.json_files import read_json


class DigestSource:
    def __init__(self, path: Path | None):
        self.path = path

    async def read(self) -> tuple[dict, dict[str, str]]:
        if self.path is None:
            digest = await asyncio.to_thread(fetch_digest)
        else:
            digest = await asyncio.to_thread(read_json, self.path)
        return digest, _region_buttons(digest)


def _region_buttons(digest: dict) -> dict[str, str]:
    regions = digest.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError("В сводке отсутствует список областей")

    buttons = {}
    for region in regions:
        name = region.get("name") if isinstance(region, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ValueError("В сводке указана область без названия")
        name = " ".join(name.split())
        callback = "region:" + sha256(name.encode("utf-8")).hexdigest()[:24]
        if callback in buttons:
            raise ValueError(f"В сводке повторяется область «{name}»")
        buttons[callback] = name
    return buttons
