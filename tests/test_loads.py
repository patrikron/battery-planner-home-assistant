from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_planner.const import DOMAIN
from custom_components.battery_planner.loads import car_need_kwh, schedule_car, vvb_schedule


def test_vvb_schedule_spreads_periods():
    tz = timezone(timedelta(hours=1))
    h0 = datetime(2026, 11, 12, 3, 0, tzinfo=tz)
    horizon = [h0 + timedelta(hours=i) for i in range(4)]
    s = (h0 + timedelta(minutes=30)).timestamp() * 1000
    e = (h0 + timedelta(minutes=90)).timestamp() * 1000
    out = vvb_schedule([{"s": s, "e": e, "kwh": 3.0}], horizon)
    assert abs(out[0] - 1.5) < 1e-6 and abs(out[1] - 1.5) < 1e-6 and out[2] == 0


def test_car_uses_cheapest_hours_within_fuse():
    tz = timezone(timedelta(hours=1))
    h0 = datetime(2026, 11, 12, 0, 0, tzinfo=tz)
    horizon = [h0 + timedelta(hours=i) for i in range(10)]
    price = [3, 2, 1, 1, 2, 3, 4, 5, 1, 1]
    other = [0.5] * 10
    other[2] = 9.0  # cheap hour but the fuse is nearly full (VVB etc.)
    out = schedule_car(horizon, price, other, 20.0, 11.0, 11.04, h0 + timedelta(hours=7))
    assert out[8] == 0 and out[9] == 0          # after ready-by
    assert out[2] <= 11.04 - 9.0 + 1e-6         # respects fuse
    assert abs(sum(out) - 20.0) < 1e-6
    assert car_need_kwh(60, 80, 77.4, 0.9) > 17


def _raw(day_offset, values):
    base = dt_util.start_of_local_day() + timedelta(days=day_offset)
    return [{"start": (base + timedelta(hours=h)).isoformat(), "value": v} for h, v in enumerate(values)]


async def test_setup_with_vvb_and_car(hass):
    prices = [1.0] * 6 + [4.0] * 18
    hass.states.async_set("sensor.price", "3", {"raw_today": _raw(0, prices), "raw_tomorrow": _raw(1, prices)})
    tomorrow3 = dt_util.start_of_local_day() + timedelta(days=1, hours=3)
    hass.states.async_set("sensor.vvb_status", "waiting_cheap", {"periods": [
        {"s": tomorrow3.timestamp() * 1000, "e": (tomorrow3 + timedelta(hours=1)).timestamp() * 1000, "kwh": 3.0}]})
    hass.states.async_set("sensor.soc", "50")
    hass.states.async_set("sensor.target", "80")
    hass.states.async_set("binary_sensor.plug", "on")
    now_h = dt_util.now().replace(minute=0, second=0, microsecond=0)
    hist = {now_h - timedelta(hours=i): {"imp": 1.0, "exp": 0.0, "heat": 0.0, "out": None, "fire": None,
                                          "vvb": 0.0, "car": 0.0} for i in range(1, 24 * 28)}
    entry = MockConfigEntry(domain=DOMAIN, data={
        "price_entity": "sensor.price", "grid_import_entity": "sensor.imp", "grid_export_entity": "sensor.exp",
        "capacity_kwh": 15.36, "min_soc_pct": 10, "max_charge_kw": 5, "max_discharge_kw": 5,
        "roundtrip_efficiency_pct": 90, "main_fuse_a": 16, "wear_cost": 0.0, "allow_export": False,
        "vvb_plan_entity": "sensor.vvb_status", "vvb_energy_entity": "sensor.vvb_e",
        "car_soc_entity": "sensor.soc", "car_target_entity": "sensor.target", "car_plug_entity": "binary_sensor.plug",
        "car_energy_entity": "sensor.goe", "car_capacity_kwh": 77.4, "car_power_kw": 11.0, "car_ready_by": (dt_util.now() + timedelta(hours=8)).strftime("%H:00:00"),  # independent of when the test runs
    })
    entry.add_to_hass(hass)
    with patch("custom_components.battery_planner.coordinator.BatteryPlannerCoordinator._history", return_value=hist):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        c = hass.data[DOMAIN][entry.entry_id]
        car = c.data["car"]
        assert car["behov_kwh"] > 25
        assert car["planerat_kwh"] > 20
        assert sum(r["vvb"] for r in c.data["plan"]) > 2.9
        # nothing exceeds the fuse
        for r in c.data["plan"]:
            assert r["load"] + r["grid_charge"] <= 11.04 + 0.05
    assert hass.states.get("sensor.batteriplanerare_elbil_planerad_laddning") is not None
    assert hass.states.get("binary_sensor.batteriplanerare_elbil_ska_ladda_nu") is not None
