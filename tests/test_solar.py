from datetime import timedelta
from unittest.mock import patch

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_planner.const import DOMAIN
from custom_components.battery_planner.coordinator import hourly_prices


def _raw(day_offset, values):
    base = dt_util.start_of_local_day() + timedelta(days=day_offset)
    return [{"start": (base + timedelta(hours=h)).isoformat(), "value": v} for h, v in enumerate(values)]


async def test_solar_forecast_and_scenarios(hass):
    prices = [1.0] * 6 + [3.0] * 18
    hass.states.async_set("sensor.price", "3", {"raw_today": _raw(0, prices), "raw_tomorrow": _raw(1, prices)})
    hass.states.async_set("sensor.sell", "1", {"raw_today": _raw(0, [0.5] * 24), "raw_tomorrow": _raw(1, [0.5] * 24)})
    now_h = dt_util.now().replace(minute=0, second=0, microsecond=0)
    # history: 1 kWh consumption every hour, 3 kWh PV at 10-14 -> import 0 and export 2 those hours
    hist = {}
    for i in range(1, 24 * 28):
        t = now_h - timedelta(hours=i)
        sun = 10 <= t.hour < 14
        hist[t] = {"imp": 0.0 if sun else 1.0, "exp": 2.0 if sun else 0.0, "heat": 0.0, "out": None, "fire": None,
                   "vvb": 0.0, "car": 0.0, "pv": 3.0 if sun else 0.0}
    start = dt_util.start_of_local_day()
    fc = {start + timedelta(days=d, hours=h): 4.0 for d in (0, 1) for h in range(10, 14)}
    entry = MockConfigEntry(domain=DOMAIN, data={
        "price_entity": "sensor.price", "sell_entity": "sensor.sell", "grid_import_entity": "sensor.imp",
        "grid_export_entity": "sensor.exp", "pv_energy_entity": "sensor.pv", "use_solar_forecast": True,
        "capacity_kwh": 10, "min_soc_pct": 10, "max_charge_kw": 5, "max_discharge_kw": 5,
        "roundtrip_efficiency_pct": 90, "main_fuse_a": 16, "wear_cost": 0.0, "allow_export": False,
    })
    entry.add_to_hass(hass)
    cls = "custom_components.battery_planner.coordinator.BatteryPlannerCoordinator"
    with patch(f"{cls}._history", return_value=hist), patch(f"{cls}._solar_forecast", return_value=fc):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        c = hass.data[DOMAIN][entry.entry_id]
        rows = c.data["plan"]
        sunny = [r for r in rows if 10 <= int(r["start"][11:13]) < 14]
        assert sunny, "horizon should include a sunny hour"
        for r in sunny:
            assert r["pv"] == 4.0 and abs(r["cons"] - 1.0) < 0.2 and abs(r["surplus"] - 3.0) < 0.2 and r["load"] == 0
        dark = [r for r in rows if int(r["start"][11:13]) in (20, 21)]
        assert all(r["pv"] == 0 and abs(r["load"] - 1.0) < 0.2 for r in dark)
        assert c.data["solar"]["kalla"] == "solprognos"

        # book the last 24 finished hours against a plan that does nothing
        c.state["last_hour"] = (now_h - timedelta(hours=25)).isoformat()
        c.state["plan"] = [{"start": (now_h - timedelta(hours=i)).isoformat(), "grid_charge": 0, "solar_charge": 0,
                            "to_load": 0, "to_grid": 0, "load": 1.0, "base": 1.0, "heat": 0.0, "pv": 4.0 if 10 <= (now_h - timedelta(hours=i)).hour < 14 else 0.0,
                            "pv_raw": 4.0 if 10 <= (now_h - timedelta(hours=i)).hour < 14 else 0.0} for i in range(1, 25)]
        c.state["days"] = {}
        p = hourly_prices(hass, "sensor.price"); s = hourly_prices(hass, "sensor.sell")
        for t in list(hist):  # make sure yesterday has prices too
            p.setdefault(t, 3.0); s.setdefault(t, 0.5)
        c._book_finished_hours(hist, p, s, c.params)
        tot = {k: sum(d.get(k, 0) for d in c.state["days"].values()) for k in ("grid", "solar", "batt", "pv_act", "load_act")}
        assert abs(tot["load_act"] - 24.0) < 1e-6 and abs(tot["pv_act"] - 12.0) < 1e-6
        assert tot["grid"] > tot["solar"]                 # solar lowers the bill
        assert abs(tot["solar"] - tot["batt"]) < 1e-6     # idle battery changes nothing
        assert c._pv_ratio() == 0.75                      # forecast 4, actual 3
    assert hass.states.get("sensor.batteriplanerare_solprognos_kommande_dygn") is not None
