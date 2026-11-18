from custom_components.battery_planner.optimizer import BatteryParams, optimize


def _bp(**kw):
    base = dict(capacity_kwh=20.0, min_soc=0.1, max_charge_kw=10.0, max_discharge_kw=10.0,
                eff_charge=0.9 ** 0.5, eff_discharge=0.9 ** 0.5, wear_cost=0.05, allow_export=True)
    base.update(kw)
    return BatteryParams(**base)


def _run(price, sell, bp, load=0.5):
    n = len(price)
    return optimize(price, sell, [load] * n, [0.0] * n, [10.0] * n, bp.lo, bp, terminal_value=0.0)


def test_margin_blocks_small_spread():
    # Buy 1.00 at night, sell 1.20 in the evening: just above 1.00/0.9 + 0.05 = 1.16.
    price = [1.0] * 4 + [2.0] * 4
    sell = [0.5] * 4 + [1.20] * 4
    without = _run(price, sell, _bp(sell_margin=0.0), load=0.0)
    with_margin = _run(price, sell, _bp(sell_margin=0.10), load=0.0)
    assert sum(h.to_grid for h in without.hours) > 1.0
    assert sum(h.to_grid for h in with_margin.hours) < 1e-6


def test_spike_fills_battery_and_sells_at_full_power():
    price = [1.0] * 6 + [11.0] + [1.5] * 3
    sell = [0.5] * 6 + [10.0] + [0.6] * 3
    res = _run(price, sell, _bp(sell_margin=0.10))
    spike = res.hours[6]
    # Charged ahead with enough for a full hour at max power, then sold at max power.
    assert spike.soc_start - spike.soc_end >= 10.0 / 0.9 ** 0.5 - 0.3
    assert abs(spike.to_load + spike.to_grid - 10.0) < 0.3
    assert all(h.grid_charge == 0 for h in res.hours[6:7])
    assert spike.to_grid > 9.0


def test_export_limited_by_fuse():
    price = [1.0] * 4 + [11.0]
    sell = [0.5] * 4 + [10.0]
    res = _run(price, sell, _bp(export_limit_kwh=6.0), load=0.0)
    assert res.hours[4].to_grid <= 6.0 + 1e-6
