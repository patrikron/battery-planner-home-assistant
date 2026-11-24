"""Battery dispatch optimizer (pure Python, no external dependencies).

Dynamic programming over a discretised state of charge. For every hour in the
horizon it chooses the next state of charge so that the total electricity cost
(buying minus selling) is minimised, given:

- price[h]        buy price incl. surcharges (kr/kWh)
- sell[h]         price received for exported energy (kr/kWh)
- load[h]         energy the house must import if there were no battery (kWh)
- surplus[h]      solar energy that would be exported if there were no battery (kWh)
- grid_room[h]    how much extra energy may be taken from the grid this hour (fuse)
"""

from __future__ import annotations

from dataclasses import dataclass, field

INF_CAP = float("inf")


@dataclass
class BatteryParams:
    capacity_kwh: float = 23.04
    min_soc: float = 0.10          # fraction of capacity never used (reserve)
    max_soc: float = 1.0
    max_charge_kw: float = 10.0
    max_discharge_kw: float = 10.0
    eff_charge: float = 0.95
    eff_discharge: float = 0.95
    wear_cost: float = 0.05        # kr per kWh delivered from the battery
    allow_export: bool = False     # may the battery sell to the grid
    sell_margin: float = 0.0       # extra kr/kWh a sale must beat (planning only, not part of reported cost)
    export_limit_kwh: float | None = None  # max kWh exported per hour (main fuse); None = no limit
    sell_buffer: float = 0.0       # sell only what is left if the load were this much higher (0.2 = +20 %)
    step_kwh: float = 0.25         # state of charge resolution
    high_soc: float = 0.8          # fraction above which standing charge is discouraged
    hold_cost: float = 0.0         # kr per kWh and hour kept above high_soc (not part of reported cost)

    @property
    def lo(self) -> float:
        return self.capacity_kwh * self.min_soc

    @property
    def hi(self) -> float:
        return self.capacity_kwh * self.max_soc


@dataclass
class HourPlan:
    index: int
    soc_start: float
    soc_end: float
    grid_charge: float = 0.0       # kWh drawn from grid into battery
    solar_charge: float = 0.0      # kWh of solar surplus put into battery
    to_load: float = 0.0           # kWh delivered to the house
    to_grid: float = 0.0           # kWh sold from battery
    cost: float = 0.0              # cost this hour with battery
    cost_without: float = 0.0      # cost this hour without battery

    @property
    def action(self) -> str:
        if self.grid_charge + self.solar_charge > 0.05:
            return "charge"
        if self.to_load + self.to_grid > 0.05:
            return "discharge"
        return "idle"


@dataclass
class PlanResult:
    hours: list[HourPlan] = field(default_factory=list)
    total_cost: float = 0.0
    total_cost_without: float = 0.0

    @property
    def saving(self) -> float:
        return self.total_cost_without - self.total_cost


def _transition(a: float, b: float, h: int, price, sell, load, surplus, grid_room, bp: BatteryParams,
                export_cap=None):
    """Cost of moving from soc a to soc b during hour h. Returns (cost, detail) or None if infeasible."""
    p, s, L, S = price[h], sell[h], load[h], surplus[h]
    base = L * p - S * s
    delta = b - a
    if delta > 1e-9:
        drawn = delta / bp.eff_charge
        if drawn > bp.max_charge_kw + 1e-9:
            return None
        solar = min(S, drawn)
        grid = drawn - solar
        if grid > grid_room[h] + 1e-9:
            return None
        cost = base + grid * p + solar * s
        return cost, (grid, solar, 0.0, 0.0)
    if delta < -1e-9:
        delivered = -delta * bp.eff_discharge
        if delivered > bp.max_discharge_kw + 1e-9:
            return None
        to_load = min(L, delivered)
        to_grid = delivered - to_load
        if to_grid > 1e-6 and not bp.allow_export:
            return None
        if export_cap is not None and to_grid > export_cap[h] + 1e-9:
            return None
        if bp.export_limit_kwh is not None and to_grid > 1e-6 and to_grid + S > bp.export_limit_kwh + 1e-9:
            return None
        cost = base - to_load * p - to_grid * s + delivered * bp.wear_cost
        return cost, (0.0, 0.0, to_load, to_grid)
    return base, (0.0, 0.0, 0.0, 0.0)


def optimize(price, sell, load, surplus, grid_room, soc0: float, bp: BatteryParams,
             terminal_value: float | None = None, export_cap=None) -> PlanResult:
    """Return the cost-minimising plan for the horizon.

    terminal_value: kr per kWh left in the battery at the end of the horizon.
    Defaults to a conservative estimate (lower quartile of buy prices) so the
    planner does not empty the battery just because the horizon ends.

    With bp.sell_buffer > 0 the plan is first made for a load that much higher;
    what that plan sells each hour caps what the real plan may sell, so a sale
    never relies on the load forecast being right.
    """
    n = len(price)
    if n == 0:
        return PlanResult()
    step = bp.step_kwh
    lo, hi = bp.lo, bp.hi
    levels = [lo + i * step for i in range(int((hi - lo) / step + 1e-9) + 1)]
    if levels[-1] < hi - 1e-9:
        levels.append(hi)
    soc0 = min(max(soc0, lo), hi)
    if terminal_value is None:
        sp = sorted(price)
        terminal_value = sp[len(sp) // 4] * bp.eff_discharge * 0.9
    if export_cap is None and bp.allow_export and bp.sell_buffer > 0:
        hi_load = [l * (1 + bp.sell_buffer) for l in load]
        hi_room = [max(0.0, r - (hl - l)) for r, l, hl in zip(grid_room, load, hi_load)]
        cautious = optimize(price, sell, hi_load, surplus, hi_room, soc0, bp, terminal_value, export_cap=[INF_CAP] * n)
        # One state of charge step of slack: rounding to steps can force a little export.
        export_cap = [hp.to_grid + step for hp in cautious.hours]

    # Precompute the allowed range of next states for speed.
    max_up = bp.max_charge_kw * bp.eff_charge
    max_down = bp.max_discharge_kw / bp.eff_discharge

    # value[j] = min cost from hour h to end starting at levels[j]
    INF = float("inf")
    hold, hold_from = bp.hold_cost, bp.capacity_kwh * bp.high_soc
    margin = bp.sell_margin
    value = [-(lv - lo) * terminal_value for lv in levels]
    choice: list[list[int]] = [[0] * len(levels) for _ in range(n)]
    for h in range(n - 1, -1, -1):
        new_value = [INF] * len(levels)
        for i, a in enumerate(levels):
            best, best_j = INF, i
            for j, b in enumerate(levels):
                if b - a > max_up + 1e-9 or a - b > max_down + 1e-9:
                    continue
                t = _transition(a, b, h, price, sell, load, surplus, grid_room, bp, export_cap)
                if t is None:
                    continue
                c = t[0] + value[j]
                if margin and t[1][3] > 0:
                    c += t[1][3] * margin
                if hold and b > hold_from:
                    c += (b - hold_from) * hold
                if c < best - 1e-12:
                    best, best_j = c, j
            new_value[i] = best
            choice[h][i] = best_j
        value = new_value

    # Start from the level closest to soc0 (soc0 is rounded to the grid).
    i = min(range(len(levels)), key=lambda k: abs(levels[k] - soc0))
    result = PlanResult()
    for h in range(n):
        j = choice[h][i]
        a, b = levels[i], levels[j]
        t = _transition(a, b, h, price, sell, load, surplus, grid_room, bp, export_cap)
        cost, (grid, solar, to_load, to_grid) = t if t else (0.0, (0, 0, 0, 0))
        base = load[h] * price[h] - surplus[h] * sell[h]
        hp = HourPlan(h, a, b, grid, solar, to_load, to_grid, cost, base)
        result.hours.append(hp)
        result.total_cost += cost
        result.total_cost_without += base
        i = j
    return result


def apply_hour(planned: HourPlan, soc: float, price: float, sell: float, load: float, surplus: float,
               grid_room: float, bp: BatteryParams):
    """Simulate one real hour: follow the planned action as far as reality allows.

    Returns (new_soc, cost_with_battery, cost_without_battery).
    """
    base = load * price - surplus * sell
    lo, hi = bp.lo, bp.hi
    if planned.action == "charge":
        want = planned.grid_charge + planned.solar_charge
        room = (hi - soc) / bp.eff_charge
        drawn = max(0.0, min(want, bp.max_charge_kw, room))
        solar = min(surplus, drawn)
        grid = min(drawn - solar, max(0.0, planned.grid_charge), grid_room)
        drawn = solar + grid
        soc += drawn * bp.eff_charge
        return soc, base + grid * price + solar * sell, base
    if planned.action == "discharge":
        avail = (soc - lo) * bp.eff_discharge
        want = planned.to_load + planned.to_grid
        to_load = max(0.0, min(load, avail, bp.max_discharge_kw))
        to_grid = 0.0
        if bp.allow_export and planned.to_grid > 0:
            to_grid = max(0.0, min(planned.to_grid, avail - to_load, bp.max_discharge_kw - to_load))
            if bp.export_limit_kwh is not None:
                to_grid = max(0.0, min(to_grid, bp.export_limit_kwh - surplus))
        # Do not discharge more than planned unless load is higher than forecast
        delivered = min(to_load + to_grid, max(want, to_load))
        to_load = min(to_load, delivered)
        to_grid = delivered - to_load
        soc -= delivered / bp.eff_discharge
        return soc, base - to_load * price - to_grid * sell + delivered * bp.wear_cost, base
    # idle: solar surplus that would be exported may still be stored if it pays
    return soc, base, base


def spare_energy(hours: list[HourPlan], soc0: float, bp: BatteryParams, buffer: float = 0.2):
    """Energy the battery can give away now without missing anything the plan needs.

    Looks at the hours up to the next time the plan charges the battery (from the
    grid or from solar). Until then the state of charge only goes down, so whatever
    is left above the reserve at that point is spare. The planned discharge to the
    house is counted with the load buffer (0.2 = the load turns out 20 % higher).

    Returns (spare kWh delivered to the house, number of hours until the next charge).
    The spare is 0 while the battery is charging.
    """
    needed = 0.0
    end = len(hours)
    for k, hp in enumerate(hours):
        if hp.grid_charge + hp.solar_charge > 0.05:
            end = k
            break
        needed += (hp.to_load * (1 + buffer) + hp.to_grid) / bp.eff_discharge
    if end == 0:
        return 0.0, 0
    spare = (min(soc0, bp.hi) - bp.lo - needed) * bp.eff_discharge
    return max(0.0, spare), end


def stock_step(kwh: float, value: float, new_kwh: float, charge_cost: float) -> tuple[float, float, float]:
    """Follow what the energy above the reserve cost to put there.

    ``kwh``/``value`` is the stored energy and what it cost (kr). ``charge_cost`` is what
    charging cost this hour (grid price, or the export income given up for solar). Energy
    that leaves takes its share of the value at the average cost.

    Returns (new kwh, new value, change in value). Adding the change in value to the hour's
    cash saving makes a charging hour cost nothing and a discharging hour earn only the
    margin over what the energy cost, so a day is not judged by when the battery is full.
    """
    new_kwh = max(0.0, new_kwh)
    if new_kwh > kwh + 1e-9:
        new_value = value + max(0.0, charge_cost)
    elif kwh > 1e-9:
        new_value = value * new_kwh / kwh
    else:
        new_value = 0.0
    return new_kwh, new_value, new_value - value
