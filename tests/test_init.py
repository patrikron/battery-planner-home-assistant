from datetime import timedelta
from unittest.mock import patch

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_planner.const import DOMAIN


def _raw(day_offset, values):
    base = dt_util.start_of_local_day() + timedelta(days=day_offset)
    return [{"start": (base + timedelta(hours=h)).isoformat(),
             "end": (base + timedelta(hours=h + 1)).isoformat(), "value": v} for h, v in enumerate(values)]


async def test_setup_and_plan(hass):
    cheap_night = [2.0] * 6 + [4.5] * 3 + [3.0] * 7 + [6.0] * 5 + [3.0] * 3
    hass.states.async_set("sensor.price", "3.0", {"raw_today": _raw(0, cheap_night), "raw_tomorrow": _raw(1, cheap_night)})
    hass.states.async_set("sensor.sell", "1.0", {"raw_today": _raw(0, [1.0] * 24), "raw_tomorrow": _raw(1, [1.0] * 24)})

    now_h = dt_util.now().replace(minute=0, second=0, microsecond=0)
    hist = {}
    for i in range(1, 24 * 28):
        t = now_h - timedelta(hours=i)
        hist[t] = {"imp": 3.0 if 16 <= t.hour <= 20 else 1.0, "exp": 0.0, "heat": 0.0, "out": None, "fire": None}

    entry = MockConfigEntry(domain=DOMAIN, data={
        "price_entity": "sensor.price", "sell_entity": "sensor.sell",
        "grid_import_entity": "sensor.imp", "grid_export_entity": "sensor.exp",
        "capacity_kwh": 15.36, "min_soc_pct": 10, "max_charge_kw": 5, "max_discharge_kw": 5,
        "roundtrip_efficiency_pct": 90, "main_fuse_a": 16, "wear_cost": 0.0, "allow_export": False,
    })
    entry.add_to_hass(hass)
    with patch("custom_components.battery_planner.coordinator.BatteryPlannerCoordinator._history", return_value=hist):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coord = hass.data[DOMAIN][entry.entry_id]
        plan = coord.data["plan"]
        assert len(plan) >= 10
        # battery should discharge in the 6 kr hours and never in the 2 kr hours
        for row in plan:
            h = dt_util.parse_datetime(row["start"]).hour
            if h < 6:
                assert row["action"] != "discharge"
        assert any(r["action"] == "discharge" for r in plan if 16 <= dt_util.parse_datetime(r["start"]).hour <= 20)
        assert coord.data["planned_saving"] > 0

        # booking: pretend the plan for the past hour existed and book it
        prev = now_h - timedelta(hours=1)
        coord.state["last_hour"] = (prev - timedelta(hours=1)).isoformat()
        coord.state["plan"] = [{"start": prev.isoformat(), "price": 6.0, "load": 3.0, "surplus": 0.0,
                                "action": "discharge", "grid_charge": 0, "solar_charge": 0,
                                "to_load": 3.0, "to_grid": 0, "soc_end": 5.0}]
        coord.state["soc"] = 10.0
        await coord.async_refresh()
        await hass.async_block_till_done()
        assert coord.state["last_hour"] == prev.isoformat()

    state = hass.states.get("sensor.batteriplanerare_plan")
    assert state is not None and state.state in ("Laddar", "Levererar", "Väntar")
    assert hass.states.get("sensor.batteriplanerare_virtuell_laddniva") is not None
