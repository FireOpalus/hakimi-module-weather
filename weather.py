"""Open-Meteo reader for the selected location. Only observations enter shared facts."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

from .actions import Action, ActionCall, DailyAction, HourlyAction, Outcome
from .base import BaseModule
from .scheduling import ScheduleOptions, ScheduleTime
from .weather_location import Coordinates, WeatherLocation


class Observation(BaseModel):
    time: int = Field(strict=True, ge=0)
    interval: int = Field(strict=True, gt=0, le=3600)
    temperature_2m: float = Field(strict=True, ge=-100, le=80, allow_inf_nan=False)
    weather_code: int = Field(strict=True, ge=0, le=99)
    precipitation: float | None = Field(None, ge=0, strict=True, allow_inf_nan=False)
    shortwave_radiation: float | None = Field(None, ge=0, strict=True, allow_inf_nan=False)
    is_day: int | None = Field(None, strict=True, ge=0, le=1)


class WeatherScheduleOptions(ScheduleOptions):
    observation_time: ScheduleTime = Field("09:30", title="每日 Owner 天气提醒时间（HH:MM）")

    @classmethod
    def from_settings(cls, settings):
        return cls(observation_time=settings.weather_time)


class WeatherModule(BaseModule):
    module_id = "weather"
    display_name = "天气观测"
    description = "观测地点、每小时天气与每日提醒"
    schedule_options = WeatherScheduleOptions
    current_fields = "temperature_2m,weather_code,precipitation,shortwave_radiation,is_day"

    def __init__(self, settings, http, world):
        super().__init__(settings)
        self.enabled = settings.weather_enabled
        self.http = http
        self.world = world
        self._env_location = None
        if settings.weather_latitude is not None and settings.weather_longitude is not None:
            self._env_location = Coordinates(
                latitude=settings.weather_latitude, longitude=settings.weather_longitude
            )
        self._override: WeatherLocation | None = None
        self._revision = 0
        self._location_lock = asyncio.Lock()

    @property
    def location(self) -> Coordinates | None:
        return self._override or self._env_location

    def admin_status(self, request):
        return {"location": self.location_status()}

    def location_status(self):
        if self._override:
            return self._override.model_dump()
        if self._env_location:
            return {**self._env_location.model_dump(), "source": "environment", "label": ""}
        return None

    async def load_location(self) -> None:
        store = self.world.store
        async with store.lock:
            row = await store.one("SELECT payload FROM admin_config WHERE key='weather_location'")
        if row:
            saved = store.vault.open("admin:weather_location", row[0])
            self._override = (
                WeatherLocation.model_validate(saved["place"]) if saved["place"] else None
            )
            self._revision = saved["revision"]

    @asynccontextmanager
    async def lifespan(self):
        await self.load_location()
        yield

    async def set_location(self, place: WeatherLocation | None) -> None:
        async with self._location_lock:
            if self._override == place:
                return
            current, new = self.location, place or self._env_location
            changed = (current.point if current else None) != (new.point if new else None)
            revision = self._revision + int(changed)
            await self.world.store.runtime.save_config(
                "weather_location",
                {"place": place.model_dump() if place else None, "revision": revision},
            )
            self._override, self._revision = place, revision

    @property
    def configuration_issue(self):
        if self.location is None:
            return "weather_location_missing"
        return None

    def actions(self):
        return (
            Action(
                name="weather.read",
                description="每小时读取配置地点的当前天气",
                origins=frozenset({"timer"}),
                run=self.read,
                background_sensor=True,
            ),
            Action(
                name="weather.daily_report",
                description="用最近一次天气观测准备每日 Owner 提醒",
                origins=frozenset({"timer"}),
                run=self.daily_report,
            ),
        )

    def schedules(self):
        return (
            HourlyAction("weather.read", label="天气采样"),
            DailyAction(
                "weather.daily_report",
                self.settings.weather_time,
                label="Owner 天气提醒",
                time_option="observation_time",
                enabled=lambda: self.settings.weather_owner_report,
            ),
        )

    def _sample_key(self, observed_at):
        # The world store hashes business keys; coordinates never enter the shared payload.
        return f"sample:{self.location.point}:{self._revision}:{observed_at}"

    async def _latest_sample(self):
        sample = await self.world.latest_event(self.module_id, "weather_observed")
        if sample and self.location:
            expected = self.world.make_event(
                self.module_id,
                "weather_observed",
                business_key=self._sample_key(sample.payload["time"]),
            )
            if sample.id == expected.id:
                return sample
        return None

    async def _raw_sensor_summary(self):
        sample = await self._latest_sample()
        if sample is None:
            return "等待首次天气观测"
        at = datetime.fromtimestamp(
            sample.payload.get("time", sample.created_at), ZoneInfo(self.settings.bot_timezone)
        )
        temperature = sample.payload["temperature_2m"]
        code = sample.payload["weather_code"]
        return f"最近天气 {at:%m-%d %H:%M}，{temperature} °C，天气代码 {code}"

    async def read(self, call: ActionCall) -> Outcome:
        if reason := self.inactive_reason:
            return Outcome("gated_skip", reason)
        place, revision = self.location, self._revision
        local = datetime.fromtimestamp(call.now, ZoneInfo(self.settings.bot_timezone))
        hour_start = local.replace(minute=0, second=0, microsecond=0).timestamp()
        latest = await self._latest_sample()
        if latest and hour_start <= latest.created_at <= call.now:
            return Outcome("done", "observation_cached")
        try:
            response = await self.http.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place.latitude,
                    "longitude": place.longitude,
                    "current": self.current_fields,
                    "timeformat": "unixtime",
                },
                follow_redirects=False,
                timeout=5,
            )
            response.raise_for_status()
            observed = Observation.model_validate(response.json()["current"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return Outcome("gated_skip", "weather_unavailable")
        if not 0 <= call.now - observed.time < 3600:
            return Outcome("gated_skip", "weather_stale")
        async with self._location_lock:
            if revision != self._revision:
                return Outcome("gated_skip", "weather_location_changed")
            if self.inactive_reason or not call.still_allowed():
                return Outcome("gated_skip", "action_no_longer_eligible")
            await self.world.publish_event(
                self.module_id,
                "weather_observed",
                observed.model_dump(exclude_none=True),
                importance="low",
                now=call.now,
                business_key=self._sample_key(observed.time),
                include_in_plan=False,
            )
        return Outcome("done", "observation_recorded")

    async def daily_report(self, call: ActionCall) -> Outcome:
        async with self._location_lock:
            if reason := self.inactive_reason:
                return Outcome("gated_skip", reason)
            sample = await self._latest_sample()
            if sample is None or not 0 <= call.now - sample.payload["time"] < 3600:
                # At the top of the hour both timers can become due together.
                return Outcome("not_dispatched", "awaiting_weather_sample")
            day = datetime.fromtimestamp(call.now, ZoneInfo(self.settings.bot_timezone)).date()
            await self.world.publish_event(
                self.module_id,
                "weather_report_due",
                sample.payload,
                now=call.now,
                business_key=str(day),
                include_in_plan=False,
            )
        return Outcome("done", "weather_report_ready")
