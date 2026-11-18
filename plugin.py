"""Hourly weather integration."""

from hakimi.modules.weather import WeatherModule

from .weather_admin import router as admin_router

__all__ = ["admin_router", "create_module"]


def create_module(service):
    return WeatherModule(service.s, getattr(service.sender, "http", None), service.world_state)
