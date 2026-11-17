from custom_components.battery_planner.optimizer import BatteryParams, optimize


def _run(wear, hold=0.0, max_soc=1.0):
    bp = BatteryParams(capacity_kwh=10, min_soc=0.1, max_soc=max_soc, max_charge_kw=5, max_discharge_kw=5,
                       eff_charge=0.95, eff_discharge=0.95, wear_cost=wear, hold_cost=hold, step_kwh=0.5)
    price = [1.0] * 6 + [1.4] * 6          # spread 0.4 kr
    n = len(price)
    return optimize(price, [0.0] * n, [1.0] * n, [0.0] * n, [10.0] * n, bp.lo, bp, terminal_value=0.0), bp


def test_wear_cost_stops_marginal_cycles():
    cheap, _ = _run(0.05)
    dear, _ = _run(0.40)
    assert sum(h.grid_charge for h in cheap.hours) > 3      # spread beats losses + wear
    assert sum(h.grid_charge for h in dear.hours) == 0      # not worth the wear


def test_hold_cost_charges_late_and_max_soc_is_respected():
    early, bp = _run(0.0)
    late, _ = _run(0.0, hold=0.002)
    first = lambda r: next(i for i, h in enumerate(r.hours) if h.grid_charge > 0)  # noqa: E731
    assert first(late) >= first(early)
    high = lambda r: sum(1 for h in r.hours if h.soc_end > 8.0)  # noqa: E731
    assert high(late) <= high(early)
    assert abs(late.total_cost - early.total_cost) < 1e-6   # same electricity cost, only timing differs
    capped, bp2 = _run(0.0, max_soc=0.8)
    assert max(h.soc_end for h in capped.hours) <= 8.0 + 1e-9
