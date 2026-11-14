"""Today's digest: BurnBeat normals + NASA FIRMS near-real-time VIIRS + Open-Meteo forecast -> risk.

Counting rules (kept comparable with the BurnBeat norm, which is single-satellite VIIRS):
- "month" count  = NOAA-20 VIIRS detections only (S-NPP stopped publishing NRT, NOAA-20 is the same
  instrument), accumulated in bot/state/detections.json because the public files hold 7 days only;
- the norm is pro-rated to the days actually covered, so a half-observed month is not compared
  with a full-month median;
- "24h" count = NOAA-20 + NOAA-21, de-duplicated on a ~1 km grid so one fire is not counted twice.
"""

import calendar
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from ..config import DEFAULT_STATE
from .burnbeat import REGION_IDS, SITE_URL, fetch_json
from .firms import (FIRMS_FILES_URL, _country_bounds, _download, _download_boundaries,
                    _is_static, _read_detections, _region_at, _static_mask)

BISHKEK = ZoneInfo("Asia/Bishkek")
NOAA20 = "noaa-20-viirs-c2/csv/J1_VIIRS_C2_Russia_Asia_7d.csv"
NOAA21 = "noaa-21-viirs-c2/csv/J2_VIIRS_C2_Russia_Asia_7d.csv"
STATE = DEFAULT_STATE.parent / "detections.json"
METEO = ("https://api.open-meteo.com/v1/forecast?latitude={la}&longitude={lo}"
         "&daily=temperature_2m_max,relative_humidity_2m_min,wind_speed_10m_max,precipitation_sum"
         "&timezone=Asia%2FBishkek&forecast_days=1")
MONTH_LOC = ("январе", "феврале", "марте", "апреле", "мае", "июне",
             "июле", "августе", "сентябре", "октябре", "ноябре", "декабре")


def _weather(bboxes: list[list[float]]) -> list[dict]:
    la = ",".join(f"{(b[0] + b[2]) / 2:.3f}" for b in bboxes)
    lo = ",".join(f"{(b[1] + b[3]) / 2:.3f}" for b in bboxes)
    request = Request(METEO.format(la=la, lo=lo), headers={"User-Agent": "BurnBeat-Telegram/1.0"})
    with urlopen(request, timeout=20) as response:
        data = json.load(response)
    data = data if isinstance(data, list) else [data]
    return [{k: loc["daily"][k][0] for k in ("temperature_2m_max", "relative_humidity_2m_min",
                                              "wind_speed_10m_max", "precipitation_sum")} for loc in data]


def _load_state(month: str, earliest: date) -> dict:
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        if state.get("month") == month:
            return state
    except (OSError, ValueError):
        pass
    return {"month": month, "since": max(earliest, date.fromisoformat(month + "-01")).isoformat(), "keys": {}}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = NamedTemporaryFile("w", encoding="utf-8", dir=STATE.parent, prefix=".detections-", delete=False)
    with tmp:
        json.dump(state, tmp, ensure_ascii=False)
    Path(tmp.name).replace(STATE)


def _phase(month: int, peak: int) -> str:
    to_peak = (peak - month) % 12
    where = f"пик в {MONTH_LOC[peak - 1]}"
    if to_peak == 0:
        return "пик сезона"
    if to_peak <= 3:
        return f"до пика ({where}, через {to_peak} мес.)"
    if (month - peak) % 12 <= 3:
        return f"спад ({where})"
    return f"межсезонье ({where})"


def _risk(season_share: float, anomaly: float | None, daily: int, w: dict) -> tuple[str, str]:
    score, why = 0, []
    if season_share >= 0.6:
        score += 2; why.append("пик пожарного сезона")
    elif season_share >= 0.3:
        score += 1; why.append("пожарный сезон")
    if anomaly is not None and anomaly > 50:
        score += 1; why.append(f"выше нормы на {anomaly:.0f}%")
    if daily >= 3:
        score += 1; why.append(f"{daily} очагов за сутки")
    t, rh, wind, rain = (w["temperature_2m_max"], w["relative_humidity_2m_min"],
                         w["wind_speed_10m_max"], w["precipitation_sum"])
    if t is not None and t > 25:
        score += 1; why.append(f"жарко ({t:.0f}°C)")
    if rh is not None and rh < 30:
        score += 1; why.append(f"сухо (влажность {rh:.0f}%)")
    if wind is not None and wind > 30:
        score += 1; why.append(f"ветер до {wind:.0f} км/ч")
    if rain is not None and rain > 2:
        score -= 2; why.append(f"дождь {rain:.0f} мм")
    level = "low" if score <= 1 else "medium" if score <= 3 else "high"
    return level, ", ".join(why) or "нет факторов риска"


def fetch_today_digest() -> dict:
    """Live digest; falls back to the historical BurnBeat digest if a live source is down."""
    import sys
    from .burnbeat import fetch_digest
    try:
        return fetch_live_digest()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Живая сводка недоступна ({error}), отправляю историческую.", file=sys.stderr)
        return fetch_digest()


def fetch_live_digest() -> dict:
    now = datetime.now(timezone.utc)
    today = now.astimezone(BISHKEK).date()
    month = today.isoformat()[:7]

    api = fetch_json("api/all.json")["regions"]
    boundaries = _download_boundaries()
    bounds = _country_bounds(boundaries)
    names = {rid: api[rid]["name"] for rid in REGION_IDS}

    mask = _static_mask()

    def detections(path: str, source: str, window: timedelta) -> list[dict]:
        points = _read_detections(source, _download(FIRMS_FILES_URL + path), now, bounds,
                                  window=window, require_recent=False)
        return [p for p in points if not _is_static(p["latitude"], p["longitude"], mask)]

    n20 = detections(NOAA20, "VIIRS NOAA-20", timedelta(days=7))
    n21 = detections(NOAA21, "VIIRS NOAA-21", timedelta(days=1))

    # month-to-date, NOAA-20 only, accumulated across runs
    earliest = min((datetime.fromisoformat(p["detected_at"]).astimezone(BISHKEK).date() for p in n20),
                   default=today)
    state = _load_state(month, earliest)
    for p in n20:
        if datetime.fromisoformat(p["detected_at"]).astimezone(BISHKEK).date().isoformat()[:7] != month:
            continue
        rid = _region_at(p["longitude"], p["latitude"], boundaries)
        if rid:
            key = f'{p["detected_at"]}|{p["latitude"]:.4f}|{p["longitude"]:.4f}'
            keys = state["keys"].setdefault(rid, [])
            if key not in keys:
                keys.append(key)
    _save_state(state)

    # last 24 h, both NOAA satellites, ~1 km de-duplication
    cutoff = now - timedelta(days=1)
    daily = {rid: set() for rid in REGION_IDS}
    for p in n20 + n21:
        if datetime.fromisoformat(p["detected_at"]) < cutoff:
            continue
        rid = _region_at(p["longitude"], p["latitude"], boundaries)
        if rid in daily:
            daily[rid].add((round(p["latitude"], 2), round(p["longitude"], 2)))

    since = date.fromisoformat(state["since"])
    covered = (today - since).days + 1
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    weather = _weather([api[rid]["bbox"] for rid in REGION_IDS])

    regions = []
    for rid, w in zip(REGION_IDS, weather):
        s = api[rid]
        medians = s["median_by_month"]
        median = medians[f"{today.month:02d}"]
        normal = round(median * covered / days_in_month, 1)   # rounded first so text and % agree
        count = len(state["keys"].get(rid, []))
        # judge the anomaly only where the full-month norm is meaningful (>= 3 fires)
        anomaly = 100 * (count - normal) / normal if median >= 3 and normal > 0 else None
        share = median / max(max(medians.values()), 1e-9)
        risk, reason = _risk(share, anomaly, len(daily[rid]), w)
        regions.append({
            "name": names[rid], "risk": risk, "daily_count": len(daily[rid]),
            "monthly_count": count, "monthly_normal": round(normal, 1),
            "monthly_since": None if since.day == 1 else since.isoformat(),
            "season": _phase(today.month, s["season"]["peak_month"]), "reason": reason,
            "site_url": s["site_url"],
        })
    return {"date": today.isoformat(), "site_url": SITE_URL, "regions": regions}
