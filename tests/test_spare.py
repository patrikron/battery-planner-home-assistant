from custom_components.battery_planner.optimizer import BatteryParams, HourPlan, optimize, spare_energy


def _bp():
    return BatteryParams(capacity_kwh=20.0, min_soc=0.1, eff_charge=0.9 ** 0.5, eff_discharge=0.9 ** 0.5,
                         wear_cost=0.05)


def test_spare_is_what_is_left_before_next_charge():
    bp = _bp()
    eff = 0.9 ** 0.5
    # Evening: the battery covers 1 kWh per hour for 4 hours, then the plan charges from the grid.
    hours = [HourPlan(i, 0, 0, to_load=1.0) for i in range(4)] + [HourPlan(4, 0, 0, grid_charge=5.0)]
    hours += [HourPlan(5, 0, 0, to_load=5.0)]  # after the charge: not counted
    spare, until = spare_energy(hours, 12.0, bp, buffer=0.2)
    assert until == 4
    # 12 kWh minus 2 kWh reserve minus 4 h x 1.2 kWh, all through the discharge efficiency.
    assert abs(spare - (12.0 - 2.0 - 4 * 1.2 / eff) * eff) < 1e-9


def test_spare_from_a_real_plan():
    bp = _bp()
    # Expensive evening, cheap night, expensive morning: the plan recharges at night.
    price = [3.0] * 3 + [0.3] * 3 + [3.0] * 4
    n = len(price)
    res = optimize(price, [0.0] * n, [1.0] * n, [0.0] * n, [10.0] * n, 6.0, bp)
    spare, until = spare_energy(res.hours, 6.0, bp, buffer=0.2)
    assert 3 <= until <= 6
    assert res.hours[until].grid_charge > 0
    assert 0 <= spare < 6.0 - 2.0


def test_no_spare_when_battery_is_needed_or_charging():
    bp = _bp()
    # Barely enough for the expensive hours.
    hours = [HourPlan(i, 0, 0, to_load=2.0) for i in range(4)]
    spare, _ = spare_energy(hours, 2.0 + 8.0 / 0.9 ** 0.5, bp, buffer=0.2)
    assert spare == 0.0
    # Charging right now: nothing to give away.
    hours = [HourPlan(0, 5, 8, grid_charge=3.2)] + hours
    assert spare_energy(hours, 15.0, bp) == (0.0, 0)
