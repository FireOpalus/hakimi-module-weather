"""Authenticated weather location controls, mounted under the module URL by the host."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from hakimi.admin_routes import body, service
from hakimi.life_admin import module_detail
from hakimi.modules.weather_location import (
    LocationLookupUnavailable,
    WeatherLocation,
    find_cities,
    locate_server,
)

router = APIRouter()


class WeatherCityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    query: str = Field(min_length=2, max_length=80)


@router.post("/location/cities")
async def weather_cities(request: Request):
    query = await body(request, WeatherCityQuery)
    try:
        return {"places": await find_cities(service(request).weather.http, query.query)}
    except LocationLookupUnavailable:
        raise HTTPException(503, "weather_city_lookup_unavailable") from None


@router.post("/location/detect")
async def weather_detect_location(request: Request):
    try:
        return await locate_server(service(request).weather.http)
    except LocationLookupUnavailable:
        raise HTTPException(503, "weather_ip_lookup_unavailable") from None


@router.put("/location")
@router.delete("/location")
async def weather_set_location(request: Request):
    svc = service(request)
    place = await body(request, WeatherLocation) if request.method == "PUT" else None
    async with request.app.state.admin_config_lock:
        await svc.weather.set_location(place)
        svc.life_engine.wakeup.set()
        return await module_detail(request, "weather")
