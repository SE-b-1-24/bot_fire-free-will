"""Start polling Telegram for private commands and region selections."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Dispatcher
from aiogram.exceptions import TelegramAPIError, TelegramConflictError
from aiogram.utils.token import TokenValidationError

# Allow direct execution from the previous entry point as well as python -m.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import DEFAULT_STATE, create_bot, get_token
from bot.handlers.regions import build_regions_router
from bot.services.digests import DigestSource
from bot.storage import SubscriberStore


POLL_TIMEOUT = 25
CONFLICT_HINT = "Проверьте, запущен ли другой getUpdates или настроен webhook."


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(build_regions_router())
    return dispatcher


async def run_polling(token: str, digest_path: Path | None, state_path: Path) -> None:
    subscribers = SubscriberStore(state_path)
    digest_source = DigestSource(digest_path)
    dispatcher = build_dispatcher()

    async with create_bot(token) as bot:
        if (await bot.get_webhook_info()).url:
            raise ValueError("У бота настроен webhook. Он должен быть отключён для getUpdates.")
        await dispatcher.start_polling(
            bot,
            polling_timeout=POLL_TIMEOUT,
            handle_as_tasks=False,
            close_bot_session=False,
            digest_source=digest_source,
            subscribers=subscribers,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Интерактивная пожарная сводка в Telegram.")
    parser.add_argument(
        "digest", type=Path, nargs="?", help="JSON со сводкой по областям (по умолчанию данные BurnBeat)"
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="JSON с подписками")
    args = parser.parse_args(argv)
    try:
        token = get_token()
    except ValueError as error:
        parser.error(str(error))

    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_polling(token, args.digest, args.state))
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, TelegramAPIError, TokenValidationError) as error:
        detail = str(error).replace(token, "[токен скрыт]")
        print(f"Бот остановлен: {detail}", file=sys.stderr)
        if isinstance(error, TelegramConflictError):
            print(CONFLICT_HINT, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
