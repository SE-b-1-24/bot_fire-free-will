"""Send wildfire digests and alerts through the Telegram Bot API."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__:
    from .firms import fetch_recent_alerts
    from .live import fetch_digest
    from .templates import render_alerts, render_digest
else:
    from firms import fetch_recent_alerts
    from live import fetch_digest
    from templates import render_alerts, render_digest


CHANNEL_MESSAGE_INTERVAL = 3.1
RATE_LIMIT_RETRIES = 3
TOKEN_PATTERN = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")


class TelegramError(Exception):
    def __init__(
        self, message: str, *, code: int | None = None, retry_after: int | None = None
    ):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class TelegramAPI:
    def __init__(self, token: str):
        if not TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Укажите токен бота в переменной TELEGRAM_BOT_TOKEN.")
        self.token = token

    def call(self, method: str, payload: dict | None = None, *, timeout: float = 15):
        request = Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return self._read_response(method, response)
        except HTTPError as error:
            with error:
                return self._read_response(method, error, error.code)
        except (URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            detail = str(reason).replace(self.token, "[токен скрыт]")
            raise TelegramError(f"{method}: ошибка соединения ({detail})") from None

    def _read_response(self, method: str, response, http_code: int | None = None):
        try:
            body = json.load(response)
        except (ValueError, OSError):
            raise TelegramError(f"{method}: некорректный ответ Telegram", code=http_code) from None
        if not isinstance(body, dict):
            raise TelegramError(f"{method}: некорректный ответ Telegram", code=http_code)
        if body.get("ok") is True and "result" in body:
            return body["result"]

        code = body.get("error_code", http_code)
        parameters = body.get("parameters") or {}
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        description = str(body.get("description", "неизвестная ошибка"))
        description = description.replace(self.token, "[токен скрыт]")
        raise TelegramError(
            f"{method}: Telegram {code} — {description}",
            code=code,
            retry_after=retry_after,
        )

    def send_message(
        self, chat_id: int | str, text: str, *, reply_markup: dict | None = None
    ) -> dict:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("sendMessage", payload)


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8-sig") as source:
        data = json.load(source)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: ожидается JSON-объект.")
    return data


def discover_channels(api: TelegramAPI) -> dict[int, dict]:
    updates = api.call(
        "getUpdates",
        {"allowed_updates": ["my_chat_member", "channel_post", "message", "callback_query"]},
    )
    channels = {}
    for update in updates:
        event = update.get("my_chat_member") or update.get("channel_post") or {}
        chat = event.get("chat", {})
        if chat.get("type") == "channel":
            channels[chat["id"]] = chat
    return channels


def send_to_channel(api: TelegramAPI, chat_id: int | str, messages: list[str]) -> None:
    for index, message in enumerate(messages, 1):
        if index > 1:
            time.sleep(CHANNEL_MESSAGE_INTERVAL)
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                result = api.send_message(chat_id, message)
                break
            except TelegramError as error:
                if error.code != 429 or attempt == RATE_LIMIT_RETRIES:
                    raise TelegramError(
                        f"Отправлено {index - 1} из {len(messages)}. {error}",
                        code=error.code,
                        retry_after=error.retry_after,
                    ) from None
                delay = max(CHANNEL_MESSAGE_INTERVAL, (error.retry_after or 0) + 0.1)
                print(f"Telegram ограничил частоту. Повтор через {delay:g} с.", file=sys.stderr)
                time.sleep(delay)
        print(f"Отправлено {index} из {len(messages)} · message_id={result['message_id']}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Отправить пожарную сводку или оповещения в Telegram.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--digest", metavar="JSON", type=Path, nargs="?", const=True,
        help="сводка с сайта BurnBeat или из указанного файла",
    )
    mode.add_argument(
        "--alerts", metavar="JSON", type=Path, nargs="?", const=True,
        help="термоточки NASA FIRMS за 24 часа или оповещения из указанного файла",
    )
    mode.add_argument("--discover-chat", action="store_true", help="найти ID каналов в обновлениях бота")
    parser.add_argument("--dry-run", action="store_true", help="показать HTML сообщений в консоли")
    args = parser.parse_args(argv)
    if args.discover_chat and args.dry_run:
        parser.error("--dry-run применяется к --digest и --alerts")

    try:
        if args.discover_chat:
            api = TelegramAPI(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())
            channels = discover_channels(api)
            if not channels:
                print(
                    "Каналы в обновлениях не найдены. Добавьте бота администратором "
                    "с правом публикации и сделайте пост в канале.",
                    file=sys.stderr,
                )
                return 1
            for chat_id, chat in channels.items():
                print(f"{chat_id}\t{chat.get('title', '')}")
            return 0

        if args.digest is not None:
            data = fetch_digest() if args.digest is True else read_json(args.digest)
            messages = [render_digest(data)]
        else:
            data = fetch_recent_alerts() if args.alerts is True else read_json(args.alerts)
            messages = render_alerts(data)
        if not messages:
            print("Подходящих термоточек за 24 часа нет." if args.alerts is True else "Новых очагов нет.")
        elif args.dry_run:
            print("\n\n".join(messages))
        else:
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
            if not chat_id:
                raise ValueError("Укажите ID или @username канала в TELEGRAM_CHAT_ID.")
            if not chat_id.startswith("@"):
                try:
                    chat_id = int(chat_id)
                except ValueError:
                    raise ValueError("TELEGRAM_CHAT_ID должен быть числом или @username.") from None
            api = TelegramAPI(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())
            send_to_channel(api, chat_id, messages)
        return 0
    except (OSError, ValueError, TelegramError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
