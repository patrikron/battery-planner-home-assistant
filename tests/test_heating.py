from datetime import timedelta
from unittest.mock import patch

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_planner.const import DOMAIN
from custom_components.battery_planner.forecast import (
    fit_heating, heating_forecast, heating_model, hours_since_fire, learn_fire_factors,
)


def _raw(day_offset, values):
    base = dt_util.start_of_local_day() + timedelta(days=day_offset)
    return [{"start": (base + timedelta(hours=h)).isoformat(), "value": v} for h, v in enumerate(values)]


def test_fire_lowers_heating_forecast():
    # 20 days: heating 0.1 kWh per degree below 18, except while burning (every 3rd evening)
    heat, out, flags = [], [], []
    for d in range(20):
        for h in range(24):
            o = 2.0
            fire = d % 3 == 0 and 17 <= h <= 22
            heat.append(0.1 * (18 - o) * (0.1 if fire else 1.0))
            out.append(o)
            flags.append(fire)
    since = hours_since_fire(flags)
    a, b = fit_heating(heat, out, since)
    assert abs(b - 0.1) < 0.02 or abs(a + b * 16 - 1.6) < 0.05
    model = lambda o: heating_model(a, b, o)
    factors = learn_fire_factors(heat, out, since, model)
    assert factors[0] < 0.3
    no_fire = heating_forecast([2.0] * 6, model, factors, 99, 0, 6)
    fire = heating_forecast([2.0] * 6, model, factors, 0, 1, 6)
    assert sum(fire) < 0.5 * sum(no_fire)


async def test_setup_with_heating_and_fire(hass):
    prices = [2.0] * 6 + [4.5] * 3 + [3.0] * 7 + [6.0] * 5 + [3.0] * 3
    hass.states.async_set("sensor.price", "3", {"raw_today": _raw(0, prices), "raw_tomorrow": _raw(1, prices)})
    hass.states.async_set("sensor.ute", "2.0")
    hass.states.async_set("sensor.kamin", "65")
    now_h = dt_util.now().replace(minute=0, second=0, microsecond=0)
    hist = {}
    for i in range(1, 24 * 28):
        t = now_h - timedelta(hours=i)
        fire = i <= 2 or (i % 72 in range(1, 6))
        h = 1.6 * (0.1 if fire else 1.0)
        hist[t] = {"imp": 1.0 + h, "exp": 0.0, "heat": h, "out": 2.0, "fire": 70.0 if fire else 21.0}
    entry = MockConfigEntry(domain=DOMAIN, data={
        "price_entity": "sensor.price", "grid_import_entity": "sensor.imp", "grid_export_entity": "sensor.exp",
        "capacity_kwh": 15.36, "min_soc_pct": 10, "max_charge_kw": 5, "max_discharge_kw": 5,
        "roundtrip_efficiency_pct": 90, "main_fuse_a": 16, "wear_cost": 0.0, "allow_export": False,
        "heating_energy_entities": ["sensor.lvp"], "outdoor_temp_entity": "sensor.ute",
        "fire_temp_entity": "sensor.kamin", "fire_threshold": 40,
    })
    entry.add_to_hass(hass)
    with patch("custom_components.battery_planner.coordinator.BatteryPlannerCoordinator._history", return_value=hist):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        c = hass.data[DOMAIN][entry.entry_id]
        info = c.data["heating"]
        assert info["eldar"] is True
        plan = c.data["plan"]
        # heating during the first hours (fire burning) is far below normal 1.6 kWh/h
        assert plan[0]["heat"] < 0.6
        assert plan[-1]["heat"] > 1.0
