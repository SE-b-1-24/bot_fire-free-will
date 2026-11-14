"""Long-poll the Telegram bot and show fire digests by region."""

import argparse
import json
import os
import sys
import time
from hashlib import sha256
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile

if __package__:
    from .live import fetch_digest
    from .send import TelegramAPI, TelegramError, read_json
    from .templates import render_digest
else:
    from live import fetch_digest
    from send import TelegramAPI, TelegramError, read_json
    from templates import render_digest


DEFAULT_STATE = Path(__file__).resolve().parent / "state" / "subscribers.json"
POLL_TIMEOUT = 25
UNSUBSCRIBE = "unsubscribe"


def load_subscribers(path: Path) -> dict[str, str]:
    try:
        subscribers = read_json(path)
    except FileNotFoundError:
        return {}

    if any(
        not chat_id.isascii()
        or not chat_id.isdecimal()
        or not isinstance(region, str)
        or not region.strip()
        for chat_id, region in subscribers.items()
    ):
        raise ValueError(f"Некорректные подписки в {path}")
    return subscribers


def save_subscribers(path: Path, subscribers: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".subscribers-",
        delete=False,
    )
    temporary_path = Path(temporary_file.name)
    try:
        with temporary_file:
            json.dump(subscribers, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def region_buttons(digest: dict) -> dict[str, str]:
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


class InteractiveBot:
    def __init__(self, api: TelegramAPI, digest_path: Path | None, state_path: Path):
        self.api = api
        self.digest_path = digest_path
        self.state_path = state_path
        self.subscribers = load_subscribers(state_path)

    def read_digest(self) -> tuple[dict, dict[str, str]]:
        digest = fetch_digest() if self.digest_path is None else read_json(self.digest_path)
        return digest, region_buttons(digest)

    def show_regions(self, chat_id: int, text: str, buttons: dict[str, str]) -> None:
        keyboard = [
            [{"text": name, "callback_data": callback}]
            for callback, name in buttons.items()
        ]
        if str(chat_id) in self.subscribers:
            keyboard.append([{"text": "Отписаться", "callback_data": UNSUBSCRIBE}])
        self.api.send_message(chat_id, text, reply_markup={"inline_keyboard": keyboard})

    def digest_unavailable(self, chat_id: int, error: Exception) -> None:
        print(f"Ошибка чтения сводки: {error}", file=sys.stderr)
        self.api.send_message(chat_id, "Сводка временно недоступна. Попробуйте позже.")

    def handle_message(self, message: dict) -> None:
        chat = message["chat"]
        if chat["type"] != "private":
            return

        words = message.get("text", "").split(maxsplit=1)
        command = words[0].split("@", 1)[0] if words else ""
        if command not in {"/start", "/today"}:
            return

        chat_id = chat["id"]
        selected = self.subscribers.get(str(chat_id))
        try:
            digest, buttons = self.read_digest()
            if command == "/today" and selected in buttons.values():
                summary = render_digest(digest, region=selected)
            else:
                summary = None
        except (OSError, ValueError) as error:
            self.digest_unavailable(chat_id, error)
            return

        if summary is not None:
            self.api.send_message(chat_id, summary)
        elif command == "/start":
            text = "🔥 Пожарная сводка Кыргызстана. Выберите область для команды /today."
            if selected in buttons.values():
                text += f"\nВыбрана область: <b>{escape(selected)}</b>."
            self.show_regions(chat_id, text, buttons)
        else:
            self.show_regions(chat_id, "Выберите область, чтобы получить сводку.", buttons)

    def handle_callback(self, query: dict) -> None:
        callback_response = {"callback_query_id": query["id"]}
        chat = query.get("message", {}).get("chat", {})
        if chat.get("type") != "private":
            self.api.call("answerCallbackQuery", callback_response)
            return

        chat_id = chat["id"]
        callback = query.get("data")
        subscribers = self.subscribers.copy()
        if callback == UNSUBSCRIBE:
            subscribers.pop(str(chat_id), None)
            response = "Подписка удалена. Выберите область заново через /start."
        else:
            try:
                _, buttons = self.read_digest()
            except (OSError, ValueError) as error:
                self.api.call("answerCallbackQuery", callback_response)
                self.digest_unavailable(chat_id, error)
                return

            region = buttons.get(callback)
            if region is None:
                callback_response["text"] = "Выберите область заново через /start."
                self.api.call("answerCallbackQuery", callback_response)
                return
            subscribers[str(chat_id)] = region
            response = f"Выбрана область: <b>{escape(region)}</b>. Сводка доступна по /today."

        try:
            if subscribers != self.subscribers:
                save_subscribers(self.state_path, subscribers)
                self.subscribers = subscribers
        except OSError as error:
            print(f"Ошибка сохранения подписки: {error}", file=sys.stderr)
            response = "Не удалось сохранить выбор. Попробуйте позже."

        self.api.call("answerCallbackQuery", callback_response)
        self.api.send_message(chat_id, response)

    def run(self) -> None:
        offset = 0
        while True:
            try:
                updates = self.api.call(
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": POLL_TIMEOUT,
                        "allowed_updates": ["message", "callback_query"],
                    },
                    timeout=POLL_TIMEOUT + 10,
                )
                for update in updates:
                    try:
                        if "message" in update:
                            self.handle_message(update["message"])
                        elif "callback_query" in update:
                            self.handle_callback(update["callback_query"])
                    except TelegramError as error:
                        if error.code not in {400, 403}:
                            raise
                        print(f"Ошибка ответа на обновление: {error}", file=sys.stderr)
                    offset = update["update_id"] + 1
            except TelegramError as error:
                if error.code is not None and 400 <= error.code < 500 and error.code != 429:
                    raise
                print(f"Ошибка Telegram: {error}", file=sys.stderr)
                time.sleep(error.retry_after or 3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Интерактивная пожарная сводка в Telegram.")
    parser.add_argument(
        "digest", type=Path, nargs="?", help="JSON со сводкой по областям (по умолчанию данные BurnBeat)"
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="JSON с подписками")
    args = parser.parse_args()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        parser.error("задайте TELEGRAM_BOT_TOKEN")

    try:
        InteractiveBot(TelegramAPI(token), args.digest, args.state).run()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, TelegramError) as error:
        print(f"Бот остановлен: {error}", file=sys.stderr)
        if isinstance(error, TelegramError) and error.code == 409:
            print("Проверьте, запущен ли другой getUpdates или настроен webhook.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
