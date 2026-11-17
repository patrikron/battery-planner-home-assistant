"""Sensors for Batteriplanerare."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, VERSION
from .coordinator import BatteryPlannerCoordinator

ACTIONS = {"charge": "Laddar", "discharge": "Levererar", "idle": "Väntar"}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    c: BatteryPlannerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        PlanSensor(c), SocSensor(c), SocKwhSensor(c),
        SavingSensor(c, "saving_today", "Besparing idag"),
        SavingSensor(c, "saving_month", "Besparing denna månad"),
        SavingSensor(c, "saving_total", "Besparing totalt", total=True),
        ForecastErrorSensor(c), CarPlanSensor(c), SolarForecastSensor(c), CyclesSensor(c),
    ])


class _Base(CoordinatorEntity[BatteryPlannerCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, c: BatteryPlannerCoordinator, key: str, name: str) -> None:
        super().__init__(c)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{c.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, c.entry.entry_id)}, name=NAME, manufacturer="patrikron",
            model="Skuggläge", sw_version=VERSION,
        )

    @property
    def data(self) -> dict[str, Any]:
        return self.coordinator.data or {}


class PlanSensor(_Base):
    _attr_icon = "mdi:battery-clock"

    def __init__(self, c):
        super().__init__(c, "plan", "Plan")

    @property
    def native_value(self):
        return ACTIONS.get(self.data.get("current_action"), None)

    @property
    def extra_state_attributes(self):
        return {
            "plan": self.data.get("plan", []),
            "planerad_besparing": self.data.get("planned_saving"),
            "korrigering": self.data.get("correction"),
            "lage": "skugga",
            "uppvarmning": self.data.get("heating", {}),
            "elbil": self.data.get("car", {}),
            "varmvatten": self.data.get("vvb", {}),
            "sol": self.data.get("solar", {}),
            "scenarier": self.data.get("scenarios", {}),
            "dagar": self.data.get("days", {}),
            "halsa": self.data.get("health", {}),
        }


class SocSensor(_Base):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, c):
        super().__init__(c, "soc", "Virtuell laddnivå")

    @property
    def native_value(self):
        return self.data.get("soc_pct")


class SocKwhSensor(_Base):
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, c):
        super().__init__(c, "soc_kwh", "Virtuell energi i batteriet")

    @property
    def native_value(self):
        return self.data.get("soc_kwh")


class SavingSensor(_Base):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "SEK"
    _attr_icon = "mdi:piggy-bank-outline"

    def __init__(self, c, key, name, total: bool = False):
        super().__init__(c, key, name)
        if total:
            self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return self.data.get(self._key)

    @property
    def extra_state_attributes(self):
        if self._key == "saving_total":
            return {"per_dag": self.data.get("saving_daily", {})}
        return None


class ForecastErrorSensor(_Base):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(self, c):
        super().__init__(c, "forecast_error", "Prognosfel senaste veckan")

    @property
    def native_value(self):
        return self.data.get("forecast_error_pct")


class CarPlanSensor(_Base):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:car-electric"

    def __init__(self, c):
        super().__init__(c, "car_plan", "Elbil planerad laddning")

    @property
    def native_value(self):
        return (self.data.get("car") or {}).get("planerat_kwh")

    @property
    def extra_state_attributes(self):
        return self.data.get("car") or {}


class SolarForecastSensor(_Base):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, c):
        super().__init__(c, "solar_forecast", "Solprognos kommande dygn")

    @property
    def native_value(self):
        return (self.data.get("solar") or {}).get("prognos_24h")

    @property
    def extra_state_attributes(self):
        return self.data.get("solar") or {}


class CyclesSensor(_Base):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:battery-sync"

    def __init__(self, c):
        super().__init__(c, "cycles", "Laddcykler")

    @property
    def native_value(self):
        return (self.data.get("health") or {}).get("cykler")

    @property
    def extra_state_attributes(self):
        return self.data.get("health") or {}
