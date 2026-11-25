from homeassistant.util import dt as dt_util
from datetime import timedelta
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_planner.const import DOMAIN
from custom_components.battery_planner.coordinator import BatteryPlannerCoordinator
from custom_components.battery_planner.optimizer import stock_step


def test_charge_costs_nothing_and_discharge_earns_the_margin():
    # Night: 10 kWh stored for 25 kr.
    kwh, value, d = stock_step(0.0, 0.0, 10.0, 25.0)
    cash = -25.0
    assert (kwh, value) == (10.0, 25.0) and cash + d == 0.0
    # Day: 4 kWh leave the battery and save 14 kr; they cost 10 kr.
    kwh, value, d = stock_step(kwh, value, 6.0, 0.0)
    assert abs(value - 15.0) < 1e-9 and abs(14.0 + d - 4.0) < 1e-9
    # Empty again: everything stored has been paid back.
    kwh, value, d = stock_step(kwh, value, 0.0, 0.0)
    assert kwh == 0.0 and value == 0.0


def test_mixed_charges_use_the_average_cost():
    kwh, value, _ = stock_step(0.0, 0.0, 5.0, 10.0)       # 2 kr/kWh
    kwh, value, _ = stock_step(kwh, value, 10.0, 20.0)     # 4 kr/kWh
    kwh, value, d = stock_step(kwh, value, 9.0, 0.0)
    assert abs(d + 3.0) < 1e-9                             # average 3 kr/kWh


async def test_upgrade_rebooks_stored_hours(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"capacity_kwh": 10, "min_soc_pct": 10,
                                                 "roundtrip_efficiency_pct": 90, "wear_cost": 0.0})
    entry.add_to_hass(hass)
    c = BatteryPlannerCoordinator(hass, entry)
    eff = c.params.eff_charge
    day1 = dt_util.start_of_local_day() - timedelta(days=1)
    t = lambda h: (day1 + timedelta(hours=h)).isoformat()  # noqa: E731
    stored = 4.0 * eff
    hours = [
        # 23:00 the day before: charge 4 kWh for 8 kr
        {"start": t(-1), "price": 2.0, "grid_charge": 4.0, "solar_charge": 0.0, "to_load": 0.0, "to_grid": 0.0,
         "soc_end": 1.0 + stored, "saving": -8.0},
        # 08:00: half of it goes to the house and saves 6 kr
        {"start": t(8), "price": 3.0, "grid_charge": 0.0, "solar_charge": 0.0, "to_load": 2.0, "to_grid": 0.0,
         "soc_end": 1.0 + stored / 2, "saving": 6.0},
    ]
    old = {"soc": 1.0 + stored / 2, "hours": hours, "last_hour": t(8),
           "savings": {(day1 - timedelta(days=1)).date().isoformat(): -8.0, day1.date().isoformat(): 6.0}}

    async def _load():
        return {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in old.items()}

    c._store.async_load = _load
    await c.async_load()
    c.async_shutdown_listeners()
    sav = c.state["savings"]
    assert abs(sav[(day1 - timedelta(days=1)).date().isoformat()]) < 1e-6   # charging day: nothing lost
    assert abs(sav[day1.date().isoformat()] - 2.0) < 1e-6                   # 6 kr saved minus 4 kr it cost
    assert abs(c.state["stock"]["value"] - 4.0) < 1e-6                      # the rest is still in the battery
    assert c.state["hours"][0]["saving"] == 0.0


def test_expected_saving_counts_stored_energy_like_the_booking():
    from custom_components.battery_planner.optimizer import BatteryParams, HourPlan, expected_saving

    bp = BatteryParams(capacity_kwh=10, min_soc=0.0, eff_charge=1.0, eff_discharge=1.0, wear_cost=0.0)
    # 5 kWh stored for 5 kr (1 kr/kWh). The plan discharges them at 3 kr/kWh, then idles.
    hours = [HourPlan(0, 5.0, 0.0, to_load=5.0), HourPlan(1, 0.0, 0.0)]
    got = expected_saving(hours, 5.0, 5.0, 5.0, [3.0, 3.0], [0.0, 0.0], [5.0, 2.0], [0.0, 0.0],
                          [11.0, 11.0], bp)
    assert abs(got - 10.0) < 1e-9  # 15 kr avoided minus the 5 kr the energy cost
    # Charging now costs nothing in the day's figure.
    hours = [HourPlan(0, 0.0, 4.0, grid_charge=4.0)]
    got = expected_saving(hours, 0.0, 0.0, 0.0, [1.0], [0.0], [1.0], [0.0], [11.0], bp)
    assert abs(got) < 1e-9
