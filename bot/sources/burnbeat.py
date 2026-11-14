"""Read published statistics from the BurnBeat site."""

import json
import os
from urllib.request import Request, urlopen


SITE_URL = os.environ.get("BURNBEAT_SITE_URL", "https://bakai4eg.github.io/fire-free-will/")
REGION_IDS = ("chui", "talas", "issykkul", "naryn", "jalalabad", "osh", "batken")


def fetch_text(path: str) -> str:
    request = Request(SITE_URL + path, headers={"User-Agent": "BurnBeat-Telegram/1.0"})
    try:
        with urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8-sig")
    except OSError as error:
        raise OSError(f"Не удалось загрузить {path} с сайта BurnBeat: {error}") from None


def fetch_json(path: str) -> dict:
    data = json.loads(fetch_text(path))
    if not isinstance(data, dict):
        raise ValueError(f"BurnBeat {path}: ожидается JSON-объект.")
    return data


def fetch_digest() -> dict:
    """Adapt the historical summaries, preserving their actual reporting month."""
    data = fetch_json("api/all.json")
    try:
        period = data["data_until"]
        regions = []
        for region_id in REGION_IDS:
            summary = data["regions"][region_id]
            current = summary["current"]
            season = summary["season"]
            if current["month"] != period:
                raise ValueError(f"Период области {region_id} не совпал с data_until сайта.")
            regions.append({
                "name": summary["name"],
                "monthly_count": current["count"],
                "monthly_normal": current["median"],
                "anomaly_pct": current["anomaly_pct"],
                "season": season["phase"],
                "peak_month": season["peak_month"],
                "site_url": summary["site_url"],
            })
    except (KeyError, TypeError) as error:
        raise ValueError(f"Неполный ответ BurnBeat api/all.json ({error}).") from None
    return {"data_until": period, "regions": regions, "site_url": SITE_URL}
