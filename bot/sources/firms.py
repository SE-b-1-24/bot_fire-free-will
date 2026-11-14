"""Read NASA FIRMS thermal detections from the public near-real-time files."""

import csv
import gzip
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from math import isfinite
from urllib.request import Request, urlopen

from .burnbeat import SITE_URL, fetch_json, fetch_text

# NRT files have no "type" column, so factories / power plants (FIRMS type 2-3 in the archive)
# are masked using the list BurnBeat publishes: anything within ~1.5 km is skipped.
STATIC_RADIUS = 0.015


def _static_mask() -> list[tuple[float, float]]:
    try:
        return [(float(a), float(b)) for a, b in json.loads(fetch_text("api/static_sources.json"))]
    except (OSError, ValueError, TypeError) as error:
        import sys
        print(f"Маска промышленных источников недоступна: {error}", file=sys.stderr)
        return []


def _is_static(latitude: float, longitude: float, mask: list[tuple[float, float]]) -> bool:
    return any(abs(latitude - a) <= STATIC_RADIUS and abs(longitude - b) <= STATIC_RADIUS * 1.35
               for a, b in mask)


FIRMS_FILES_URL = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
FIRMS_FILES = {
    "VIIRS NOAA-20": "noaa-20-viirs-c2/csv/J1_VIIRS_C2_Russia_Asia_48h.csv",
    "VIIRS NOAA-21": "noaa-21-viirs-c2/csv/J2_VIIRS_C2_Russia_Asia_48h.csv",
    "MODIS": "modis-c6.1/csv/MODIS_C6_1_Russia_Asia_48h.csv",
}
FIRMS_FIELDS = {"latitude", "longitude", "acq_date", "acq_time", "confidence", "frp"}
VIIRS_CONFIDENCE = {"h": "high", "high": "high", "n": "medium", "nominal": "medium"}

# geoBoundaries KGZ ADM1, OpenStreetMap and Wambacher, ODbL 1.0.
BOUNDARIES_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/"
    "releaseData/gbOpen/KGZ/ADM1/geoBoundaries-KGZ-ADM1.geojson"
)
REGION_IDS_BY_ISO = {
    "KG-C": "chui",
    "KG-T": "talas",
    "KG-Y": "issykkul",
    "KG-N": "naryn",
    "KG-J": "jalalabad",
    "KG-O": "osh",
    "KG-B": "batken",
}


def _download(url: str) -> str:
    request = Request(
        url,
        headers={"User-Agent": "BurnBeat-Telegram/1.0", "Accept-Encoding": "gzip"},
    )
    for attempt in range(2):
        try:
            with urlopen(request, timeout=15) as response:
                content = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    content = gzip.decompress(content)
            return content.decode("utf-8-sig")
        except (OSError, EOFError) as error:
            if attempt:
                raise OSError(f"Не удалось загрузить {url}: {error}") from None


def _download_boundaries() -> dict[str, list]:
    try:
        data = json.loads(_download(BOUNDARIES_URL))
        boundaries = {}
        for feature in data["features"]:
            region_id = REGION_IDS_BY_ISO[feature["properties"]["shapeISO"]]
            geometry = feature["geometry"]
            if geometry["type"] == "Polygon":
                polygons = [geometry["coordinates"]]
            elif geometry["type"] == "MultiPolygon":
                polygons = geometry["coordinates"]
            else:
                raise ValueError("Ожидается Polygon или MultiPolygon.")
            if not polygons or any(
                not polygon or any(len(ring) < 4 for ring in polygon)
                for polygon in polygons
            ):
                raise ValueError("Граница области содержит пустой или неполный контур.")
            if region_id in boundaries:
                raise ValueError(f"Повторяется граница области {region_id}.")
            boundaries[region_id] = polygons
        if set(boundaries) != set(REGION_IDS_BY_ISO.values()):
            raise ValueError("В наборе должны быть границы семи областей.")
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"Некорректные границы областей geoBoundaries: {error}") from None
    return boundaries


def _inside_ring(longitude: float, latitude: float, ring: list) -> bool:
    """Count ray crossings using GeoJSON's longitude/latitude order."""
    inside = False
    west, south = ring[-1]
    for east, north in ring:
        if (south > latitude) != (north > latitude):
            crossing = west + (east - west) * (latitude - south) / (north - south)
            if longitude < crossing:
                inside = not inside
        west, south = east, north
    return inside


def _region_at(longitude: float, latitude: float, boundaries: dict[str, list]) -> str | None:
    for region_id, polygons in boundaries.items():
        for outer_ring, *holes in polygons:
            if _inside_ring(longitude, latitude, outer_ring) and not any(
                _inside_ring(longitude, latitude, hole) for hole in holes
            ):
                return region_id
    return None


def _country_bounds(boundaries: dict[str, list]) -> tuple[float, float, float, float]:
    points = [
        point
        for polygons in boundaries.values()
        for polygon in polygons
        for point in polygon[0]
    ]
    longitudes, latitudes = zip(*points)
    return min(latitudes), min(longitudes), max(latitudes), max(longitudes)


def _read_detections(
    source: str, content: str, now: datetime, bounds: tuple[float, float, float, float],
    window: timedelta = timedelta(days=1), require_recent: bool = True,
) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content))
    if not FIRMS_FIELDS <= set(reader.fieldnames or ()):
        raise ValueError(f"Некорректные столбцы NASA FIRMS ({source}).")

    south, west, north, east = bounds
    cutoff = now - window
    last_observation = None
    detections = []
    try:
        for row in reader:
            # FIRMS publishes UTC, with time represented as HHMM.
            observed = datetime.strptime(
                f"{row['acq_date']} {row['acq_time'].zfill(4)}", "%Y-%m-%d %H%M"
            ).replace(tzinfo=timezone.utc)
            if observed > now or observed < cutoff:
                continue
            if last_observation is None or observed > last_observation:
                last_observation = observed
            latitude, longitude = float(row["latitude"]), float(row["longitude"])
            if not (south <= latitude <= north and west <= longitude <= east):
                continue

            raw_confidence = row["confidence"].strip().lower()
            if source == "MODIS":
                confidence = float(raw_confidence)
                if not 30 <= confidence <= 100:
                    continue
            else:
                confidence = VIIRS_CONFIDENCE.get(raw_confidence)
                if confidence is None:
                    continue

            power = float(row["frp"])
            if not isfinite(power) or power < 0:
                raise ValueError("Мощность FRP должна быть конечным неотрицательным числом.")
            detections.append({
                "latitude": latitude,
                "longitude": longitude,
                "detected_at": observed.isoformat(),
                "power_mw": power,
                "confidence": confidence,
                "source": source,
            })
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"Некорректная запись NASA FIRMS ({source}): {error}") from None
    if last_observation is None and require_recent:
        raise ValueError(f"NASA FIRMS ({source}): в региональной выгрузке нет наблюдений за последние 24 часа.")
    return detections


def fetch_recent_alerts() -> dict:
    """Return thermal detections acquired within the latest 24 hours in Kyrgyzstan."""
    with ThreadPoolExecutor(max_workers=5) as pool:
        regions_future = pool.submit(fetch_json, "api/regions.json")
        mask_future = pool.submit(_static_mask)
        boundaries_future = pool.submit(_download_boundaries)
        files = {
            source: pool.submit(_download, FIRMS_FILES_URL + path)
            for source, path in FIRMS_FILES.items()
        }
        regions = regions_future.result()
        boundaries = boundaries_future.result()
        mask = mask_future.result()
        feeds = {source: future.result() for source, future in files.items()}
    try:
        names = {region_id: regions[region_id]["name"] for region_id in boundaries}
    except (KeyError, TypeError) as error:
        raise ValueError(f"Некорректный справочник областей BurnBeat: {error}") from None
    bounds = _country_bounds(boundaries)
    now = datetime.now(timezone.utc)
    alerts = []
    seen = set()
    for source, content in feeds.items():
        for point in _read_detections(source, content, now, bounds):
            latitude, longitude = point["latitude"], point["longitude"]
            if _is_static(latitude, longitude, mask):
                continue
            key = (source, point["detected_at"], latitude, longitude)
            if key in seen:
                continue
            seen.add(key)
            region_id = _region_at(longitude, latitude, boundaries)
            if region_id is not None:
                alerts.append({**point, "region": names[region_id]})
    alerts.sort(key=lambda point: point["detected_at"], reverse=True)
    return {"site_url": SITE_URL, "alerts": alerts}
