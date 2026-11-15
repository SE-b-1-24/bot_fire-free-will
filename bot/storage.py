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


class SeenAlerts:
    """Remember sent detections so hourly runs do not repeat them.

    One fire seen by several satellites on the same day collapses into one key (~1 km grid)."""

    KEEP_DAYS = 3

    def __init__(self, path: Path):
        self.path = path
        try:
            self._seen = read_json(path)
        except (FileNotFoundError, ValueError):
            self._seen = {}
        self._pending: dict[str, str] = {}

    @staticmethod
    def _key(alert: dict) -> str:
        return f'{alert["detected_at"][:10]}|{alert["latitude"]:.2f}|{alert["longitude"]:.2f}'

    def new(self, alerts: list[dict]) -> list[dict]:
        fresh = []
        for alert in alerts:
            key = self._key(alert)
            if key in self._seen or key in self._pending:
                continue
            self._pending[key] = alert["detected_at"]
            fresh.append(alert)
        return fresh

    def commit(self) -> None:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.KEEP_DAYS)).isoformat()
        seen = {k: v for k, v in {**self._seen, **self._pending}.items() if v >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.path.parent, prefix=".seen-", delete=False
        )
        with temporary_file:
            json.dump(seen, temporary_file, ensure_ascii=False)
        Path(temporary_file.name).replace(self.path)
        self._seen, self._pending = seen, {}


class AnomalyLog:
    """Send an 'above normal' alert for a region at most once per REPEAT_DAYS,
    unless the count has grown by half since the last alert."""

    REPEAT_DAYS = 3

    def __init__(self, path: Path):
        self.path = path
        try:
            self._sent = read_json(path)
        except (FileNotFoundError, ValueError):
            self._sent = {}
        self._pending: dict[str, dict] = {}

    def new(self, anomalies: list[dict]) -> list[dict]:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        fresh = []
        for a in anomalies:
            last = self._sent.get(a["id"])
            if last:
                recent = datetime.fromisoformat(last["at"]) > now - timedelta(days=self.REPEAT_DAYS)
                if recent and a["count"] < 1.5 * last["count"]:
                    continue
            self._pending[a["id"]] = {"at": now.isoformat(), "count": a["count"]}
            fresh.append(a)
        return fresh

    def commit(self) -> None:
        sent = {**self._sent, **self._pending}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.path.parent, prefix=".anomaly-", delete=False
        )
        with temporary_file:
            json.dump(sent, temporary_file, ensure_ascii=False)
        Path(temporary_file.name).replace(self.path)
        self._sent, self._pending = sent, {}
