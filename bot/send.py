"""CLI for posting digests and alerts to Telegram."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from aiogram.exceptions import AiogramError

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import create_bot, get_chat_id, get_token
from bot.rendering import render_alerts, render_digest
from bot.services.publishing import PublishingError, discover_channels, send_to_channel
from bot.sources.burnbeat import fetch_digest
from bot.sources.firms import fetch_recent_alerts
from bot.sources.json_files import read_json


async def _run(args: argparse.Namespace) -> int:
    token = None
    try:
        if args.discover_chat:
            token = get_token()
            async with create_bot(token) as bot:
                channels = await discover_channels(bot)
            if not channels:
                print(
                    "Каналы в обновлениях не найдены. Добавьте бота администратором "
                    "с правом публикации и сделайте пост в канале.",
                    file=sys.stderr,
                )
                return 1
            for chat_id, chat in channels.items():
                print(f"{chat_id}\t{chat.title or ''}")
            return 0

        if args.digest is not None:
            data = (
                await asyncio.to_thread(fetch_digest)
                if args.digest is True
                else await asyncio.to_thread(read_json, args.digest)
            )
            messages = [render_digest(data)]
        else:
            data = (
                await asyncio.to_thread(fetch_recent_alerts)
                if args.alerts is True
                else await asyncio.to_thread(read_json, args.alerts)
            )
            messages = render_alerts(data)

        if not messages:
            print("Подходящих термоточек за 24 часа нет." if args.alerts is True else "Новых очагов нет.")
        elif args.dry_run:
            print("\n\n".join(messages))
        else:
            chat_id = get_chat_id()
            token = get_token()
            async with create_bot(token) as bot:
                await send_to_channel(bot, chat_id, messages)
        return 0
    except (OSError, ValueError, AiogramError, PublishingError) as error:
        detail = str(error)
        if token:
            detail = detail.replace(token, "[токен скрыт]")
        print(f"Ошибка: {detail}", file=sys.stderr)
        return 1


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
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
