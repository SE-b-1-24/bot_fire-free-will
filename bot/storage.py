"""Store each Telegram chat's selected region in a local JSON file."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from .sources.json_files import read_json


class SubscriberStore:
    def __init__(self, path: Path):
        self.path = path
        self._subscribers = self._load()

    def get(self, chat_id: int) -> str | None:
        return self._subscribers.get(str(chat_id))

    def set(self, chat_id: int, region: str | None) -> None:
        if self.get(chat_id) == region:
            return

        subscribers = self._subscribers.copy()
        if region is None:
            subscribers.pop(str(chat_id), None)
        else:
            subscribers[str(chat_id)] = region

        self._save(subscribers)
        self._subscribers = subscribers

    def _load(self) -> dict[str, str]:
        try:
            subscribers = read_json(self.path)
        except FileNotFoundError:
            return {}

        if any(
            not chat_id.isascii()
            or not chat_id.isdecimal()
            or not isinstance(region, str)
            or not region.strip()
            for chat_id, region in subscribers.items()
        ):
            raise ValueError(f"Некорректные подписки в {self.path}")
        return subscribers

    def _save(self, subscribers: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".subscribers-",
            delete=False,
        )
        temporary_path = Path(temporary_file.name)
        try:
            with temporary_file:
                json.dump(subscribers, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write("\n")
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)
