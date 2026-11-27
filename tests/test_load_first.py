"""Hours the plan expected the sun to cover run as Load first in the simulation."""

from custom_components.battery_planner.optimizer import BatteryParams, HourPlan, apply_hour


BP = BatteryParams(capacity_kwh=30.0, min_soc=0.1, wear_cost=0.15, eff_charge=0.95, eff_discharge=0.95)


def test_idle_sun_hour_covers_clouds():
    hp = HourPlan(0, 25.0, 25.0, load_first=True)
    soc, cost_with, cost_without = apply_hour(hp, 25.0, 6.0, 1.0, 0.95, 0.0, 10.0, BP)
    assert cost_without == 6.0 * 0.95
    assert abs(soc - 24.0) < 1e-9
    assert abs(cost_with - 0.95 * 0.15) < 1e-9


def test_idle_hour_that_holds_energy_stays_idle():
    hp = HourPlan(0, 25.0, 25.0)  # import was forecast: the plan keeps the energy for later
    soc, cost_with, cost_without = apply_hour(hp, 25.0, 3.0, 1.0, 1.5, 0.0, 10.0, BP)
    assert soc == 25.0 and cost_with == cost_without


def test_load_first_stops_at_reserve():
    hp = HourPlan(0, 3.5, 3.5, load_first=True)
    soc, cost_with, cost_without = apply_hour(hp, 3.5, 6.0, 1.0, 2.0, 0.0, 10.0, BP)
    assert abs(soc - BP.lo) < 1e-9
    assert cost_with < cost_without


def test_solar_charge_hour_covers_shortfall():
    hp = HourPlan(0, 20.0, 21.0, solar_charge=1.0, load_first=True)
    soc, cost_with, cost_without = apply_hour(hp, 20.0, 6.0, 1.0, 0.5, 0.0, 10.0, BP)
    assert abs(soc - (20.0 - 0.5 / 0.95)) < 1e-9
    assert cost_with < cost_without
