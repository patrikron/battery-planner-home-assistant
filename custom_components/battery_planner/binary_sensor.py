"""Binary sensors for Batteriplanerare – signals other automations can follow."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .sensor import _Base


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CarChargeNow(c), BatteryGridCharge(c), SpareHeatAllowed(c), FuseLimiting(c)])


class CarChargeNow(_Base, BinarySensorEntity):
    _attr_icon = "mdi:ev-station"

    def __init__(self, c):
        super().__init__(c, "car_charge_now", "Elbil ska ladda nu")

    @property
    def is_on(self):
        return bool((self.data.get("car") or {}).get("ladda_nu"))


class BatteryGridCharge(_Base, BinarySensorEntity):
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, c):
        super().__init__(c, "grid_charge_now", "Batteri laddar från nätet nu")

    @property
    def is_on(self):
        plan = self.data.get("plan") or []
        return bool(plan and plan[0].get("grid_charge", 0) > 0.05)


class SpareHeatAllowed(_Base, BinarySensorEntity):
    """On when the battery has enough spare energy to run the configured extra load for an hour."""

    _attr_icon = "mdi:radiator"

    def __init__(self, c):
        super().__init__(c, "spare_heat_allowed", "Element får köra på batteriet")

    @property
    def is_on(self):
        return bool((self.data.get("spare") or {}).get("far_kora"))

    @property
    def extra_state_attributes(self):
        return self.data.get("spare") or {}


class FuseLimiting(_Base, BinarySensorEntity):
    _attr_icon = "mdi:fuse-alert"

    def __init__(self, c):
        super().__init__(c, "fuse_limiting", "Säkringsskyddet begränsar laddningen")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.guard is not None

    @property
    def is_on(self):
        return bool((self.data.get("guard") or {}).get("begransar"))
