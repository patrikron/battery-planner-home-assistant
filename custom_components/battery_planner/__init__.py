"""Batteriplanerare – plans home battery charge/discharge from spot prices and a consumption forecast."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, VERSION
from .coordinator import BatteryPlannerCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CARD_URL = "/battery_planner/battery-planner-card.js"


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card from the integration folder and load it on every dashboard."""
    if hass.data.get(f"{DOMAIN}_card"):
        return
    path = Path(__file__).parent / "www" / "battery-planner-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), False)])
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    hass.data[f"{DOMAIN}_card"] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if getattr(hass, "http", None) is not None and "frontend" in hass.config.components:
        await _async_register_card(hass)
    coordinator = BatteryPlannerCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_phase_guard()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: BatteryPlannerCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    coordinator.async_shutdown_listeners()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
