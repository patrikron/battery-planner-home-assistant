from datetime import timedelta

from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.components.recorder.models import StatisticMetaData, StatisticData
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.battery_planner.coordinator import BatteryPlannerCoordinator
from custom_components.battery_planner.const import DOMAIN


async def test_history_reads_statistics(hass):
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=10)
    for eid, per_hour in (("sensor.imp", 1.5), ("sensor.exp", 0.5)):
        meta = StatisticMetaData(has_mean=False, has_sum=True, name=None, source="recorder",
                                 statistic_id=eid, unit_of_measurement="kWh", mean_type=0, unit_class="energy")
        rows = [StatisticData(start=start + timedelta(hours=i), state=per_hour * (i + 1), sum=per_hour * (i + 1)) for i in range(10)]
        async_import_statistics(hass, meta, rows)
    await async_wait_recording_done(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={"grid_import_entity": "sensor.imp", "grid_export_entity": "sensor.exp"})
    entry.add_to_hass(hass)
    c = BatteryPlannerCoordinator(hass, entry)
    hist = await c._history()
    c.async_shutdown_listeners()
    assert len(hist) >= 9
    vals = list(hist.values())
    assert all(abs(v["imp"] - 1.5) < 1e-6 and abs(v["exp"] - 0.5) < 1e-6 for v in vals[1:])
