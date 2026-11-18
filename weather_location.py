"""Admin-selected weather locations and explicit, read-only lookup previews."""

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..http_json import bounded_json
from ..security import Rejected


class Coordinates(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)

    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)

    @property
    def point(self) -> tuple[float, float]:
        return self.latitude, self.longitude


class WeatherLocation(Coordinates):
    source: Literal["city", "coordinates", "server_ip"] = "coordinates"
    label: str = Field(default="", max_length=160, pattern=r"^[^\x00-\x1f\x7f]*$")


class LocationLookupUnavailable(Exception):
    pass


async def _lookup(http, url, **params):
    try:
        return await bounded_json(
            http, "GET", url, params=params, timeout=5, follow_redirects=False, max_bytes=65_536
        )
    except (httpx.HTTPError, ValueError, Rejected) as exc:
        raise LocationLookupUnavailable from exc


def _place(data, source, *parts):
    if not isinstance(data, dict):
        return None
    names = [v.strip() for key in parts if isinstance(v := data.get(key), str) and v.strip()]
    try:
        return WeatherLocation(
            source=source,
            label=" · ".join(dict.fromkeys(names))[:160],
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
        )
    except ValidationError:
        return None


async def find_cities(http, query: str) -> list[WeatherLocation]:
    data = await _lookup(
        http, "https://geocoding-api.open-meteo.com/v1/search", name=query, language="zh", count=6
    )
    rows = data.get("results", [])  # Open-Meteo omits results for an unmatched name.
    if not isinstance(rows, list) or data.get("error"):
        raise LocationLookupUnavailable
    return [
        place
        for row in rows[:6]
        if (place := _place(row, "city", "name", "admin1", "country")) and place.label
    ]


async def locate_server(http) -> WeatherLocation:
    # No IP parameter or forwarded client headers. Only ask for fields needed for the preview.
    data = await _lookup(
        http, "https://ipwho.is/", fields="success,city,region,country,latitude,longitude"
    )
    place = _place(data, "server_ip", "city", "region", "country")
    if data.get("success") is not True or place is None:
        raise LocationLookupUnavailable
    return place
