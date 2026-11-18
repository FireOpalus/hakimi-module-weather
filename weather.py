"""Open-Meteo reader for the selected location. Only observations enter shared facts."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

from ..http_json import bounded_json
from ..security import Rejected
from .actions import Action, ActionCall, Outcome
from .base import BaseModule
from .weather_location import Coordinates, WeatherLocation

CONDITIONS = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    56: "轻微冻毛毛雨",
    57: "较强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻微冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴轻微冰雹",
    99: "雷暴伴强冰雹",
}


class Observation(BaseModel):
    time: int = Field(strict=True, ge=0)
    interval: int = Field(strict=True, gt=0, le=3600)
    temperature_2m: float = Field(strict=True, ge=-100, le=80, allow_inf_nan=False)
    weather_code: int = Field(strict=True, ge=0, le=99)
    precipitation: float | None = Field(None, ge=0, strict=True, allow_inf_nan=False)
    shortwave_radiation: float | None = Field(None, ge=0, strict=True, allow_inf_nan=False)
    is_day: int | None = Field(None, strict=True, ge=0, le=1)

    def fresh_at(self, now: float) -> bool:
        return self.time <= now < self.time + self.interval

    def describe(self, zone: str) -> str:
        at = datetime.fromtimestamp(self.time, ZoneInfo(zone))
        condition = CONDITIONS.get(self.weather_code, f"天气代码 {self.weather_code}")
        rain = f"，时段降水 {self.precipitation:g} 毫米" if self.precipitation is not None else ""
        return f"{at:%m-%d %H:%M}，{condition}，{self.temperature_2m:g} °C{rain}"


class WeatherModule(BaseModule):
    module_id = "weather"
    display_name = "天气观测"
    description = "AI 和其他模块可按需查询已设地点的当前天气"
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
        self._read_lock = asyncio.Lock()

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
        # Older releases could already have queued a timer or its separate QQ reminder.
        async with self.world.store.transaction():
            await self.world.store.db.execute(
                "UPDATE scheduled_intents SET status='gated_skip', "
                "result_reason='weather_schedule_retired',updated_at=? WHERE "
                "(status='pending' OR (status='executing' AND handler_started=0)) AND "
                "((origin='timer' AND action_name IN ('weather.read','weather.daily_report')) "
                "OR id IN (SELECT intent_id FROM life_rule_receipts "
                "WHERE rule_id='weather.owner_report'))",
                (self.world.clock(),),
            )
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
                description="按需查询已设地点（你那边）的当前天气、气温与降水。仅用于当地实况，不提供异地或未来预报。",
                origins=frozenset({"planner", "query"}),
                run=self.read,
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
                return Observation.model_validate(sample.payload)
        return None

    async def _raw_sensor_summary(self):
        sample = await self._latest_sample()
        if sample is None:
            return "等待首次天气查询"
        if not sample.fresh_at(self.world.clock()):
            return "上次天气数据已过期，可按需查询"
        return "最近查询：" + sample.describe(self.settings.bot_timezone)

    def _result(self, sample: Observation, reason: str) -> Outcome:
        return Outcome(
            "done",
            reason,
            {
                "observation": sample.model_dump(exclude_none=True),
                "summary": sample.describe(self.settings.bot_timezone),
                "source": "Open-Meteo",
            },
        )

    async def read(self, call: ActionCall) -> Outcome:
        place, revision = self.location, self._revision
        loop = asyncio.get_running_loop()
        started = loop.time()

        def now():
            return call.now + (loop.time() - started)

        def issue():
            if revision != self._revision:
                return "weather_location_changed"
            return self.inactive_reason or (
                None if call.still_allowed() else "action_no_longer_eligible"
            )

        async with self._read_lock:
            if reason := issue():
                return Outcome("gated_skip", reason)
            latest = await self._latest_sample()
            if reason := issue():
                return Outcome("gated_skip", reason)
            if latest and latest.fresh_at(now()):
                return self._result(latest, "observation_cached")
            if self.http is None:
                return Outcome("gated_skip", "weather_unavailable")
            try:
                data = await bounded_json(
                    self.http,
                    "GET",
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": place.latitude,
                        "longitude": place.longitude,
                        "current": self.current_fields,
                        "timeformat": "unixtime",
                    },
                    follow_redirects=False,
                    timeout=5,
                    max_bytes=16_384,
                )
                observed = Observation.model_validate(data["current"])
            except (httpx.HTTPError, ValueError, KeyError, TypeError, Rejected):
                return Outcome("gated_skip", "weather_unavailable")
            if not observed.fresh_at(now()):
                return Outcome("gated_skip", "weather_stale")
            async with self._location_lock, self.world.store.transaction():
                if reason := issue():
                    return Outcome("gated_skip", reason)
                event = self.world.make_event(
                    self.module_id,
                    "weather_observed",
                    observed.model_dump(exclude_none=True),
                    importance="low",
                    now=call.now,
                    business_key=self._sample_key(observed.time),
                )
                event.consumed = True
                event, created = await self.world.insert_event_tx(event)
        # Subscribers can call this query again, after the observation is committed.
        if created:
            await self.world.notify_event(event)
        if reason := issue():
            return Outcome("gated_skip", reason)
        return self._result(observed, "observation_recorded")
