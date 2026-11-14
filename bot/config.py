"""Runtime settings shared by the Telegram entry points."""

import os
import re
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import ClientDecodeError, TelegramAPIError


DEFAULT_STATE = Path(__file__).resolve().parent / "state" / "subscribers.json"
TOKEN_PATTERN = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")


def get_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("Укажите токен бота в переменной TELEGRAM_BOT_TOKEN.")
    return token


def get_chat_id() -> int | str:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        raise ValueError("Укажите ID или @username канала в TELEGRAM_CHAT_ID.")
    if chat_id.startswith("@"):
        return chat_id
    try:
        return int(chat_id)
    except ValueError:
        raise ValueError("TELEGRAM_CHAT_ID должен быть числом или @username.") from None


def create_bot(token: str) -> Bot:
    session = AiohttpSession(timeout=15)
    session.middleware(_hide_token_in_api_errors)
    return Bot(
        token,
        session=session,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )


async def _hide_token_in_api_errors(make_request, bot, method):
    try:
        return await make_request(bot, method)
    except ClientDecodeError:
        raise TelegramAPIError(method, "Некорректный ответ Telegram") from None
    except TelegramAPIError as error:
        # HTTP errors can contain the full Bot API URL.
        if error.message:
            error.message = error.message.replace(bot.token, "[токен скрыт]")
        raise error from None
