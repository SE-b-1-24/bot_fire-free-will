"""Handle region selection and digests in private Telegram chats."""

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from ..keyboards import UNSUBSCRIBE, region_keyboard
from ..rendering import render_digest
from ..services.digests import DigestSource
from ..storage import SubscriberStore


logger = logging.getLogger(__name__)
DIGEST_UNAVAILABLE = "Сводка временно недоступна. Попробуйте позже."


def build_regions_router() -> Router:
    router = Router(name="regions")
    router.message.register(
        show_digest,
        F.chat.type == ChatType.PRIVATE,
        F.text,
        Command("start", "today"),
    )
    router.callback_query.register(select_region)
    return router


async def show_digest(
    message: Message,
    command: CommandObject,
    digest_source: DigestSource,
    subscribers: SubscriberStore,
) -> None:
    selected = subscribers.get(message.chat.id)
    try:
        digest, regions = await digest_source.read()
        if command.command == "today" and selected in regions.values():
            summary = render_digest(digest, region=selected)
        else:
            summary = None
    except (OSError, ValueError) as error:
        logger.error("Ошибка чтения сводки: %s", error)
        await message.answer(DIGEST_UNAVAILABLE)
        return

    if summary is not None:
        await message.answer(summary)
        return
    if command.command == "start":
        text = "🔥 Пожарная сводка Кыргызстана. Выберите область для команды /today."
        if selected is not None and selected in regions.values():
            text += f"\nВыбрана область: <b>{escape(selected)}</b>."
    else:
        text = "Выберите область, чтобы получить сводку."
    await message.answer(
        text, reply_markup=region_keyboard(regions, can_unsubscribe=selected is not None)
    )


async def select_region(
    query: CallbackQuery,
    bot: Bot,
    digest_source: DigestSource,
    subscribers: SubscriberStore,
) -> None:
    if query.message is None or query.message.chat.type != ChatType.PRIVATE:
        await query.answer()
        return

    chat_id = query.message.chat.id
    if query.data == UNSUBSCRIBE:
        region = None
        response = "Подписка удалена. Выберите область заново через /start."
    else:
        try:
            _, regions = await digest_source.read()
        except (OSError, ValueError) as error:
            logger.error("Ошибка чтения сводки: %s", error)
            await query.answer()
            await bot.send_message(chat_id, DIGEST_UNAVAILABLE)
            return

        region = regions.get(query.data) if query.data is not None else None
        if region is None:
            await query.answer("Выберите область заново через /start.")
            return
        response = f"Выбрана область: <b>{escape(region)}</b>. Сводка доступна по /today."

    try:
        subscribers.set(chat_id, region)
    except OSError as error:
        logger.error("Ошибка сохранения подписки: %s", error)
        response = "Не удалось сохранить выбор. Попробуйте позже."

    await query.answer()
    await bot.send_message(chat_id, response)
