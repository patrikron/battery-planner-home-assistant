"""Config flow for Batteriplanerare."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOW_EXPORT, CONF_CAPACITY, CONF_EFFICIENCY, CONF_EXPORT_ENTITY, CONF_FIRE_ENTITY,
    CONF_FIRE_THRESHOLD, CONF_FUSE_A, CONF_HEATING_ENTITIES, CONF_OUTDOOR_ENTITY, CONF_WEATHER_ENTITY,
    CONF_CAR_CAPACITY, CONF_CAR_ENERGY_ENTITY, CONF_CAR_PLUG_ENTITY, CONF_CAR_POWER, CONF_CAR_READY_BY,
    CONF_CAR_SOC_ENTITY, CONF_CAR_TARGET_ENTITY, CONF_VVB_ENERGY_ENTITY, CONF_VVB_PLAN_ENTITY,
    CONF_IMPORT_ENTITY, CONF_MAX_CHARGE, CONF_MAX_DISCHARGE, CONF_MIN_SOC, CONF_PRICE_ENTITY,
    CONF_SELL_ENTITY, CONF_WEAR_COST, DEFAULTS, DOMAIN, NAME, CONF_PV_ENTITY, CONF_USE_SOLAR_FORECAST,
    CONF_MAX_SOC, CONF_BATTERY_PRICE, CONF_LIFETIME_MWH, CONF_SELL_MARGIN,
)


def _num(lo, hi, step, unit=None):
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=lo, max=hi, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX))


def _schema(d: dict[str, Any]) -> vol.Schema:
    sensor = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
    g = lambda k: d.get(k, DEFAULTS.get(k))  # noqa: E731
    fields = {
        vol.Required(CONF_PRICE_ENTITY, default=d.get(CONF_PRICE_ENTITY, vol.UNDEFINED)): sensor,
        vol.Optional(CONF_SELL_ENTITY, default=d.get(CONF_SELL_ENTITY, vol.UNDEFINED)): sensor,
        vol.Required(CONF_IMPORT_ENTITY, default=d.get(CONF_IMPORT_ENTITY, vol.UNDEFINED)): sensor,
        vol.Required(CONF_EXPORT_ENTITY, default=d.get(CONF_EXPORT_ENTITY, vol.UNDEFINED)): sensor,
        vol.Optional(CONF_PV_ENTITY, default=d.get(CONF_PV_ENTITY, vol.UNDEFINED)): sensor,
        vol.Required(CONF_USE_SOLAR_FORECAST, default=g(CONF_USE_SOLAR_FORECAST)): selector.BooleanSelector(),
        vol.Required(CONF_CAPACITY, default=g(CONF_CAPACITY)): _num(1, 100, 0.01, "kWh"),
        vol.Required(CONF_MIN_SOC, default=g(CONF_MIN_SOC)): _num(0, 50, 1, "%"),
        vol.Required(CONF_MAX_SOC, default=g(CONF_MAX_SOC)): _num(50, 100, 1, "%"),
        vol.Required(CONF_MAX_CHARGE, default=g(CONF_MAX_CHARGE)): _num(0.5, 30, 0.1, "kW"),
        vol.Required(CONF_MAX_DISCHARGE, default=g(CONF_MAX_DISCHARGE)): _num(0.5, 30, 0.1, "kW"),
        vol.Required(CONF_EFFICIENCY, default=g(CONF_EFFICIENCY)): _num(70, 100, 1, "%"),
        vol.Required(CONF_FUSE_A, default=g(CONF_FUSE_A)): _num(10, 63, 1, "A"),
        vol.Required(CONF_WEAR_COST, default=g(CONF_WEAR_COST)): _num(0, 1, 0.01, "kr/kWh"),
        vol.Required(CONF_BATTERY_PRICE, default=g(CONF_BATTERY_PRICE)): _num(0, 500000, 100, "kr"),
        vol.Required(CONF_LIFETIME_MWH, default=g(CONF_LIFETIME_MWH)): _num(0, 1000, 0.1, "MWh"),
        vol.Required(CONF_ALLOW_EXPORT, default=g(CONF_ALLOW_EXPORT)): selector.BooleanSelector(),
        vol.Required(CONF_SELL_MARGIN, default=g(CONF_SELL_MARGIN)): _num(0, 5, 0.01, "kr/kWh"),
        vol.Optional(CONF_HEATING_ENTITIES, default=d.get(CONF_HEATING_ENTITIES, [])): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", multiple=True)),
        vol.Optional(CONF_OUTDOOR_ENTITY, default=d.get(CONF_OUTDOOR_ENTITY, vol.UNDEFINED)): sensor,
        vol.Optional(CONF_WEATHER_ENTITY, default=d.get(CONF_WEATHER_ENTITY, vol.UNDEFINED)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather")),
        vol.Optional(CONF_FIRE_ENTITY, default=d.get(CONF_FIRE_ENTITY, vol.UNDEFINED)): sensor,
        vol.Required(CONF_FIRE_THRESHOLD, default=g(CONF_FIRE_THRESHOLD)): _num(20, 200, 1, "°C"),
        vol.Optional(CONF_VVB_PLAN_ENTITY, default=d.get(CONF_VVB_PLAN_ENTITY, vol.UNDEFINED)): sensor,
        vol.Optional(CONF_VVB_ENERGY_ENTITY, default=d.get(CONF_VVB_ENERGY_ENTITY, vol.UNDEFINED)): sensor,
        vol.Optional(CONF_CAR_SOC_ENTITY, default=d.get(CONF_CAR_SOC_ENTITY, vol.UNDEFINED)): sensor,
        vol.Optional(CONF_CAR_TARGET_ENTITY, default=d.get(CONF_CAR_TARGET_ENTITY, vol.UNDEFINED)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "number", "input_number"])),
        vol.Optional(CONF_CAR_PLUG_ENTITY, default=d.get(CONF_CAR_PLUG_ENTITY, vol.UNDEFINED)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")),
        vol.Optional(CONF_CAR_ENERGY_ENTITY, default=d.get(CONF_CAR_ENERGY_ENTITY, vol.UNDEFINED)): sensor,
        vol.Required(CONF_CAR_CAPACITY, default=g(CONF_CAR_CAPACITY)): _num(10, 200, 0.1, "kWh"),
        vol.Required(CONF_CAR_POWER, default=g(CONF_CAR_POWER)): _num(1, 22, 0.1, "kW"),
        vol.Required(CONF_CAR_READY_BY, default=g(CONF_CAR_READY_BY)): selector.TimeSelector(),
    }
    return vol.Schema(fields)


class BatteryPlannerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title=NAME, data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
