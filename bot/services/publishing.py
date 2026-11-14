"""Publish prepared messages and inspect pending Telegram channel updates."""

import asyncio
import sys

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import AiogramError, TelegramRetryAfter
from aiogram.types import Chat, Message


CHANNEL_MESSAGE_INTERVAL = 3.1
RATE_LIMIT_RETRIES = 3


class PublishingError(Exception):
    """Sending a batch stopped after zero or more successful deliveries."""


async def discover_channels(bot: Bot) -> dict[int, Chat]:
    updates = await bot.get_updates(
        allowed_updates=["my_chat_member", "channel_post", "message", "callback_query"],
    )
    channels = {}
    for update in updates:
        event = update.my_chat_member or update.channel_post
        if event and event.chat.type == ChatType.CHANNEL:
            channels[event.chat.id] = event.chat
    return channels


async def _send_with_retry(bot: Bot, chat_id: int | str, text: str) -> Message:
    for _ in range(RATE_LIMIT_RETRIES):
        try:
            return await bot.send_message(chat_id, text)
        except TelegramRetryAfter as error:
            delay = max(CHANNEL_MESSAGE_INTERVAL, error.retry_after + 0.1)
            print(f"Telegram ограничил частоту. Повтор через {delay:g} с.", file=sys.stderr)
            await asyncio.sleep(delay)
    return await bot.send_message(chat_id, text)


async def send_to_channel(bot: Bot, chat_id: int | str, messages: list[str]) -> None:
    total = len(messages)
    for index, text in enumerate(messages, 1):
        if index > 1:
            await asyncio.sleep(CHANNEL_MESSAGE_INTERVAL)
        try:
            message = await _send_with_retry(bot, chat_id, text)
        except AiogramError as error:
            raise PublishingError(f"Отправлено {index - 1} из {total}. {error}") from None
        print(f"Отправлено {index} из {total} · message_id={message.message_id}", flush=True)
