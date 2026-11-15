"""'Above normal' alerts: a region of Kyrgyzstan burns much more than a normal week.

Same rule as the BurnBeat site ("now vs normal"):
- now    = NOAA-20 VIIRS hotspots of the last 7 days inside the region (industrial sources removed);
- normal = VIIRS-era median for this calendar month (2012 onward) x 7 / days in month.
An alert is sent when a region has at least MIN_COUNT hotspots and at least RATIO x its normal week.
"""

import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .burnbeat import REGION_IDS, fetch_json
from .firms import (FIRMS_FILES_URL, _country_bounds, _download, _download_boundaries,
                    _is_static, _read_detections, _region_at, _static_mask)

BISHKEK = ZoneInfo("Asia/Bishkek")
NOAA20 = "noaa-20-viirs-c2/csv/J1_VIIRS_C2_Russia_Asia_7d.csv"
MIN_COUNT = 5        # fewer hotspots is noise, not an outbreak
RATIO = 2.0          # at least twice a normal week
MONTH_LOC = ("январе", "феврале", "марте", "апреле", "мае", "июне",
             "июле", "августе", "сентябре", "октябре", "ноябре", "декабре")


def evaluate(counts: dict[str, int], api: dict, month: int, days_in_month: int) -> list[dict]:
    """Pure rule, testable without the network."""
    out = []
    for rid in REGION_IDS:
        s = api[rid]
        medians = s.get("median_viirs_by_month") or s["median_by_month"]
        normal = medians[f"{month:02d}"] * 7 / days_in_month
        count = counts.get(rid, 0)
        if count < MIN_COUNT or count < RATIO * normal:
            continue
        out.append({"id": rid, "region": f'{s["name"]} область', "count": count, "normal": round(normal, 1),
                    "ratio": round(count / normal, 1) if normal >= 1 else None,
                    "month_loc": MONTH_LOC[month - 1], "site_url": s["site_url"]})
    return out


def fetch_anomalies(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(BISHKEK).date()
    api = fetch_json("api/all.json")["regions"]
    boundaries = _download_boundaries()
    mask = _static_mask()
    points = _read_detections("VIIRS NOAA-20", _download(FIRMS_FILES_URL + NOAA20), now,
                              _country_bounds(boundaries), window=timedelta(days=7), require_recent=False)
    counts: dict[str, int] = {}
    for p in points:
        if _is_static(p["latitude"], p["longitude"], mask):
            continue
        rid = _region_at(p["longitude"], p["latitude"], boundaries)
        if rid:
            counts[rid] = counts.get(rid, 0) + 1
    return evaluate(counts, api, today.month, calendar.monthrange(today.year, today.month)[1])
