"""Форматирование сводок и оповещений для Telegram в режиме HTML."""

from datetime import date, datetime, timezone
from html import escape, unescape
from math import isfinite
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


BISHKEK = ZoneInfo("Asia/Bishkek")
MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
MONTH_NAMES = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
MONTH_LOCATIVE = (
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
)
RISKS = {
    "low": ("🟢", "НИЗКИЙ"),
    "medium": ("🟠", "СРЕДНИЙ"),
    "high": ("🔴", "ВЫСОКИЙ"),
}
CONFIDENCE = {"low": "низкая", "medium": "средняя", "high": "высокая"}


def _items(data, key):
    if not isinstance(data, dict):
        raise ValueError("Ожидается JSON-объект.")
    items = data.get(key)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f"'{key}' должен содержать массив объектов.")
    return items


def _text(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' должен содержать непустую строку.")
    return " ".join(value.split())


def _number(data, key, *, minimum=0, maximum=None, integer=False):
    value = data.get(key)
    if (
        type(value) not in (int, float)
        or (integer and type(value) is not int)
        or (type(value) is float and not isfinite(value))
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        raise ValueError(f"Недопустимое число в '{key}'.")
    return value


def _map_link(url):
    if not isinstance(url, str) or any(char.isspace() or ord(char) < 32 for char in url):
        raise ValueError("'site_url' должен содержать HTTP(S)-ссылку на карту.")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("'site_url' должен содержать HTTP(S)-ссылку на карту.")
    return f'🗺 <a href="{escape(url, quote=True)}">Открыть карту</a>'


def _check_length(message):
    plain = unescape(re.sub(r"<[^>]*>", "", message))
    if len(plain.encode("utf-16-le")) // 2 > 4096:
        raise ValueError("Сообщение превышает ограничение Telegram в 4096 символов.")
    return message


def _day_text(day):
    return f"{day.day} {MONTHS[day.month - 1]}"


def _normal_text(count, normal):
    text = f"норма {normal:g}" if isinstance(normal, float) else f"норма {normal}"
    if normal == 0:
        return text
    if count == normal:
        return f"{text}, без отклонения"
    difference = abs((count - normal) * 100 / normal)
    percent = f"{difference:.0f}%" if difference >= 1 else "<1%"
    arrow = "▲ +" if count > normal else "▼ −"
    return f"{text}, {arrow}{percent}"


def _dated_period(text, *, monthly):
    try:
        day = date.fromisoformat(f"{text}-01" if monthly else text)
    except ValueError:
        raise ValueError("Некорректная дата в данных BurnBeat.") from None
    expected = day.isoformat()[:7] if monthly else day.isoformat()
    if expected != text:
        raise ValueError("Некорректная дата в данных BurnBeat.")
    return day


def _historical_header(data):
    period = _dated_period(_text(data, "data_until"), monthly=True)
    return f"🔥 <b>Пожарная сводка BurnBeat · {MONTH_NAMES[period.month - 1]} {period.year}</b>"


def _historical_region(data, entry, name):
    count = _number(entry, "monthly_count", integer=True)
    normal = _number(entry, "monthly_normal")
    anomaly = entry.get("anomaly_pct")
    stats = f"За месяц: {count} · Норма: {normal:g}"
    if anomaly is not None:
        anomaly = _number(entry, "anomaly_pct", minimum=-100)
        stats += f" · Отклонение: {anomaly:+g}%"
    season = escape(_text(entry, "season"))
    peak_month = _number(entry, "peak_month", minimum=1, maximum=12, integer=True)
    link = _map_link(entry.get("site_url", data.get("site_url")))
    return (
        f"<b>{escape(name)}</b>\n"
        f"{stats}\n"
        f"Сезон: {season} (пик в {MONTH_LOCATIVE[peak_month - 1]})\n"
        f"{link}"
    )


def render_digest(data: dict, *, region: str | None = None) -> str:
    """Сформировать одно сообщение по стране или по выбранной области."""
    regions = _items(data, "regions")
    historical = "data_until" in data
    if historical:
        blocks = [_historical_header(data)]
    else:
        day = _dated_period(_text(data, "date"), monthly=False)
        blocks = [f"🔥 <b>Пожарная сводка · {_day_text(day)}</b>"]
    seen = set()
    for entry in regions:
        name = _text(entry, "name")
        if name in seen:
            raise ValueError(f"В сводке повторяется область «{name}».")
        seen.add(name)
        if region is not None and region != name:
            continue
        if historical:
            blocks.append(_historical_region(data, entry, name))
            continue
        risk = _text(entry, "risk")
        if risk not in RISKS:
            raise ValueError(f"Для области «{name}» ожидается риск low, medium или high.")
        emoji, label = RISKS[risk]
        daily = _number(entry, "daily_count", integer=True)
        monthly = _number(entry, "monthly_count", integer=True)
        normal = _number(entry, "monthly_normal")
        normal_text = escape(_normal_text(monthly, normal))
        season = escape(_text(entry, "season"))
        reason = escape(_text(entry, "reason"))
        link = _map_link(entry.get("site_url", data.get("site_url")))
        blocks.append(
            f"{emoji} <b>{escape(name)} — риск {label}</b>\n"
            f"   За сутки: {daily} · За месяц: {monthly} ({normal_text})\n"
            f"   Сезон: {season}\n"
            f"   Почему: {reason}\n"
            f"   {link}"
        )

    if len(blocks) == 1:
        if region is not None:
            raise ValueError(f"В сводке нет области «{region}».")
        raise ValueError("В сводке нет областей.")
    if historical:
        blocks.append(
            "Показаны данные за последний опубликованный месяц. "
            "Суточных показателей и оценки риска в этой сводке нет.\n"
            "Источник: BurnBeat · NASA FIRMS (MODIS + VIIRS)."
        )
    else:
        blocks.append(
            "Риск — оценка по истории 2000–2025 и прогнозу погоды, не точный прогноз.\n"
            "Данные: NASA FIRMS (MODIS + VIIRS)."
        )
    return _check_length("\n\n".join(blocks))


def _detection_time(entry, today):
    value = _text(entry, "detected_at")
    if "T" not in value and " " not in value:
        raise ValueError("'detected_at' должен содержать дату и время в формате ISO 8601.")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        detected = datetime.fromisoformat(value)
        if detected.tzinfo is None:
            detected = detected.replace(tzinfo=timezone.utc)
        local = detected.astimezone(BISHKEK)
    except (ValueError, OverflowError):
        raise ValueError("'detected_at' должен содержать дату и время в формате ISO 8601.") from None
    day = local.date()
    label = "сегодня" if day == today else _day_text(day)
    if day.year != today.year:
        label += f" {day.year}"
    return f"{label} {local:%H:%M} (Бишкек)"


def _confidence_text(entry):
    value = entry.get("confidence")
    if value is None:
        return "нет данных"
    if isinstance(value, str) and value in CONFIDENCE:
        return CONFIDENCE[value]
    return f"{_number(entry, 'confidence', maximum=100):g}%"


def _render_alert(entry, region, today):
    lat = _number(entry, "latitude", minimum=-90, maximum=90)
    lon = _number(entry, "longitude", minimum=-180, maximum=180)
    when = _detection_time(entry, today)
    power = _number(entry, "power_mw")
    confidence = _confidence_text(entry)
    url = f"https://maps.google.com/?q={lat},{lon}"
    title = "Новый очаг"
    source_line = ""
    if "source" in entry:
        title = "Термоточка за 24 ч"
        source_line = f"Источник: NASA FIRMS · {escape(_text(entry, 'source'))}\n"
    return _check_length(
        f"🚨 <b>{title} · {escape(region)}</b>\n"
        f"{lat:.3f}, {lon:.3f} · {when}\n"
        f"Мощность: {power:g} МВт · уверенность: {confidence}\n"
        f"{source_line}"
        f'📍 Google Maps: <a href="{url}">{url}</a>'
    )


def _locative(region, entry):
    if "in_region" in entry:
        return _text(entry, "in_region")
    if region.endswith("ская область"):
        return f"{region.removesuffix('ая область')}ой области"
    if region.endswith("ская"):
        return f"{region.removesuffix('ая')}ой"
    return f"регионе {region}"


def render_alerts(data: dict, *, today: date | None = None) -> list[str]:
    """Сформировать оповещения, объединяя более пяти очагов одной области."""
    alerts = _items(data, "alerts")
    if today is None:
        today = datetime.now(BISHKEK).date()
    by_region = {}
    for entry in alerts:
        name = _text(entry, "region")
        by_region.setdefault(name, []).append(entry)

    messages = []
    for region, entries in by_region.items():
        count = len(entries)
        if count <= 5:
            messages.extend(_render_alert(entry, region, today) for entry in entries)
            continue
        place = escape(_locative(region, entries[0]))
        recent = all("source" in entry for entry in entries)
        plural = 11 <= count % 100 <= 14 or count % 10 not in (1, 2, 3, 4)
        if recent:
            if plural:
                wording = "термоточек"
            elif count % 10 == 1:
                wording = "термоточка"
            else:
                wording = "термоточки"
        elif plural:
            wording = "новых очагов"
        elif count % 10 == 1:
            wording = "новый очаг"
        else:
            wording = "новых очага"
        link = _map_link(entries[0].get("site_url", data.get("site_url")))
        source = "За последние 24 часа · NASA FIRMS\n" if recent else ""
        messages.append(_check_length(f"🚨 <b>{count} {wording} в {place}</b>\n{source}{link}"))
    return messages
