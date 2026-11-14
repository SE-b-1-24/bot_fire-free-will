"""Inline keyboards for the interactive bot."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


UNSUBSCRIBE = "unsubscribe"


def region_keyboard(
    regions: dict[str, str], *, can_unsubscribe: bool = False
) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text=name, callback_data=callback)]
        for callback, name in regions.items()
    ]
    if can_unsubscribe:
        keyboard.append([InlineKeyboardButton(text="Отписаться", callback_data=UNSUBSCRIBE)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
