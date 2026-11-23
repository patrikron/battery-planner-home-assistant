from datetime import datetime, timedelta
from unittest.mock import patch

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_planner.const import DOMAIN
from custom_components.battery_planner.phase_guard import (
    PhaseGuard, headroom_kw, imbalance_factors, kw_to_phase_a, room_kwh,
)

T0 = datetime(2026, 11, 24, 1, 0, 0)


def test_headroom_follows_worst_phase():
    # 16 A fuse, 1 A margin, worst phase 9 A -> 6 A free per phase -> 4.14 kW
    assert abs(headroom_kw([3.0, 9.0, 2.0], 16, 1.0) - 4.14) < 0.01
    # A real battery charging 4 kW is part of the measurement and is taken out again
    with_battery = [c + kw_to_phase_a(4.0) for c in (3.0, 9.0, 2.0)]
    assert abs(headroom_kw(with_battery, 16, 1.0, battery_kw=4.0) - 4.14) < 0.01
    assert headroom_kw([20.0, 1, 1], 16, 1.0) == 0.0


def test_imbalance_and_room():
    t = [datetime(2026, 11, d, 2) for d in range(1, 6)]
    rows = [{x: 2.0 for x in t}, {x: 5.0 for x in t}, {x: 2.0 for x in t}]   # worst 5 A, average 3 A
    f = imbalance_factors(rows)
    assert abs(f[2] - 5 / 3) < 0.01
    # 2 kWh load in an hour, worst phase 5/3 × average: 11.04 - 3.33 = 7.7 kWh room
    assert abs(room_kwh(2.0, 11.04, f[2]) - (11.04 - 2.0 * 5 / 3)) < 0.01


def test_short_peak_is_ignored_long_peak_lowers_limit_and_it_recovers():
    g = PhaseGuard(16, 1.0, 10.0, step_pct=10, hold_s=45, recover_s=180)
    quiet, radiator = [3.0, 5.0, 3.0], [3.0, 11.0, 3.0]
    g.update(T0, quiet, planned_kw=6.0)
    assert g.limit_pct == 100 and not g.limiting
    # A radiator for 30 s: too short to react
    g.update(T0 + timedelta(seconds=10), radiator, 6.0)
    g.update(T0 + timedelta(seconds=40), radiator, 6.0)
    g.update(T0 + timedelta(seconds=45), quiet, 6.0)
    assert g.limit_pct == 100 and g.day["sankningar"] == 0
    # The radiator stays on: after 45 s the limit drops to what fits (4 A free -> 2.76 kW -> 20 %)
    g.update(T0 + timedelta(seconds=100), radiator, 6.0)
    assert g.update(T0 + timedelta(seconds=150), radiator, 6.0)
    assert g.limit_pct == 20 and g.limiting and g.day["sankningar"] == 1
    # Off again: the limit stays down for 180 s, then goes back up in one step
    g.update(T0 + timedelta(seconds=200), quiet, 6.0)
    g.update(T0 + timedelta(seconds=300), quiet, 6.0)
    assert g.limit_pct == 20
    g.update(T0 + timedelta(seconds=390), quiet, 6.0)
    assert g.limit_pct == 60      # 10 A free -> 6.9 kW -> 69 %, rounded down to the 60 % step
    assert g.changes == 2
    assert g.day["forlorad_kwh"] > 0 and g.day["begransad_min"] > 0


def test_nothing_planned_means_no_limiting():
    g = PhaseGuard(16, 1.0, 10.0)
    for s in range(0, 600, 30):
        g.update(T0 + timedelta(seconds=s), [15.5, 15.5, 15.5], planned_kw=0.0)
    assert g.limit_pct == 100 and g.day["sankningar"] == 0 and not g.limiting


def _raw(day_offset, values):
    base = dt_util.start_of_local_day() + timedelta(days=day_offset)
    return [{"start": (base + timedelta(hours=h)).isoformat(), "value": v} for h, v in enumerate(values)]


async def test_guard_in_home_assistant(hass):
    prices = [1.0] * 6 + [4.0] * 18
    hass.states.async_set("sensor.price", "3", {"raw_today": _raw(0, prices), "raw_tomorrow": _raw(1, prices)})
    for i, a in enumerate((3.0, 5.0, 3.0), 1):
        hass.states.async_set(f"sensor.fas{i}", str(a))
    now_h = dt_util.now().replace(minute=0, second=0, microsecond=0)
    hist = {now_h - timedelta(hours=i): {"imp": 1.0, "exp": 0.0, "heat": 0.0, "out": None, "fire": None,
                                          "vvb": 0.0, "car": 0.0} for i in range(1, 24 * 28)}
    entry = MockConfigEntry(domain=DOMAIN, data={
        "price_entity": "sensor.price", "grid_import_entity": "sensor.imp", "grid_export_entity": "sensor.exp",
        "capacity_kwh": 20, "min_soc_pct": 10, "max_charge_kw": 10, "max_discharge_kw": 10,
        "roundtrip_efficiency_pct": 90, "main_fuse_a": 16, "wear_cost": 0.0, "allow_export": False,
        "phase_current_entities": ["sensor.fas1", "sensor.fas2", "sensor.fas3"], "phase_margin_a": 1.0,
    })
    entry.add_to_hass(hass)
    cls = "custom_components.battery_planner.coordinator.BatteryPlannerCoordinator"
    with patch(f"{cls}._history", return_value=hist):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        c = hass.data[DOMAIN][entry.entry_id]
        assert c.guard is not None
        # Pretend the plan wants 6 kW from the grid this hour, then load phase 2 heavily
        c.state["plan"] = [{"start": now_h.isoformat(), "grid_charge": 6.0}]
        hass.states.async_set("sensor.fas2", "11.0")
        await hass.async_block_till_done()
        assert hass.states.get("sensor.batteriplanerare_sakringsutrymme_for_laddning").state == "2.76"
        assert hass.states.get("sensor.batteriplanerare_laddgrans_sakringsskydd").state == "100"   # not yet
        # Shadow mode: nothing is ever written, only computed
        assert all(not e.entity_id.startswith(("number.", "select.", "switch.")) for e in hass.states.async_all())
    assert hass.states.get("binary_sensor.batteriplanerare_sakringsskyddet_begransar_laddningen") is not None


async def test_two_phases_is_rejected(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "price_entity": "sensor.price", "grid_import_entity": "sensor.imp", "grid_export_entity": "sensor.exp",
        "phase_current_entities": ["sensor.fas1", "sensor.fas2"],
    })
    assert result["type"] == "form" and result["errors"] == {"phase_current_entities": "three_phases"}
