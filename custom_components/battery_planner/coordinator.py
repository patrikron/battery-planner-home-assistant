"""Coordinator: gathers prices and history, forecasts, optimises and simulates (shadow mode)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALLOW_EXPORT, CONF_CAPACITY, CONF_EFFICIENCY, CONF_EXPORT_ENTITY, CONF_FIRE_ENTITY,
    CONF_FIRE_THRESHOLD, CONF_FUSE_A, CONF_HEATING_ENTITIES, CONF_OUTDOOR_ENTITY, CONF_WEATHER_ENTITY,
    CONF_IMPORT_ENTITY, CONF_MAX_CHARGE, CONF_MAX_DISCHARGE, CONF_MIN_SOC, CONF_PRICE_ENTITY,
    CONF_SELL_ENTITY, CONF_SELL_BUFFER, CONF_SELL_MARGIN, CONF_WEAR_COST, DEFAULTS, DOMAIN, FORECAST_WEEKS, HISTORY_DAYS,
    SOLAR_WEEKS, STEP_KWH, STORAGE_VERSION,
    CONF_CAR_CAPACITY, CONF_CAR_ENERGY_ENTITY, CONF_CAR_PLUG_ENTITY, CONF_CAR_POWER, CONF_CAR_READY_BY,
    CONF_CAR_SOC_ENTITY, CONF_CAR_TARGET_ENTITY, CONF_VVB_ENERGY_ENTITY, CONF_VVB_PLAN_ENTITY,
    CONF_PV_ENTITY, CONF_USE_SOLAR_FORECAST, CONF_MAX_SOC, CONF_BATTERY_PRICE, CONF_LIFETIME_MWH,
    HIGH_SOC, HOLD_COST, CONF_SPARE_HEAT_KW, CONF_SPARE_BUFFER, SPARE_OFF_HOURS,
    CONF_PHASE_ENTITIES, CONF_PHASE_MARGIN, GUARD_HOLD_S, GUARD_RECOVER_S, GUARD_STEP_PCT,
)
from .loads import car_need_kwh, next_ready_by, schedule_car, spans, vvb_schedule
from .forecast import (
    DEFAULT_FIRE_FACTORS, build_profile, fit_heating, forecast, heating_forecast, heating_model,
    hours_since_fire, learn_fire_factors, typical_fire_hours, update_correction,
)
from .optimizer import BatteryParams, HourPlan, apply_hour, optimize, spare_energy, stock_step
from .phase_guard import PhaseGuard, imbalance_factors, kw_to_phase_a, phase_a_to_kw, room_kwh

_LOGGER = logging.getLogger(__name__)


def _hour(t: datetime) -> datetime:
    return dt_util.as_local(t).replace(minute=0, second=0, microsecond=0)


HOURS_KEEP = 24 * 8  # hours of booked history shown in the card


def _parse_time(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        return dt_util.utc_from_timestamp(v)
    if isinstance(v, str):
        return dt_util.parse_datetime(v)
    return None


def hourly_prices(hass: HomeAssistant, entity_id: str | None) -> dict[datetime, float]:
    """Read a Nordpool-style sensor (raw_today / raw_tomorrow, 15 or 60 min) as hourly means."""
    out: dict[datetime, list[float]] = {}
    if not entity_id:
        return {}
    st = hass.states.get(entity_id)
    if st is None:
        return {}
    for key in ("raw_today", "raw_tomorrow"):
        for item in st.attributes.get(key) or []:
            t = _parse_time(item.get("start"))
            v = item.get("value")
            if t is None or v is None:
                continue
            out.setdefault(_hour(t), []).append(float(v))
    return {k: sum(v) / len(v) for k, v in out.items()}


class BatteryPlannerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Plans every 15 minutes and books the simulated result of every finished hour."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(minutes=15))
        self.entry = entry
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.state: dict[str, Any] = {}
        self._unsub = async_track_time_change(hass, self._on_new_hour, minute=1, second=30)
        self._spare_on = False
        self._unsub_phase = None
        self._imbalance: dict[int, float] = {}
        self._guard_pushed: datetime | None = None
        self.guard: PhaseGuard | None = None
        if len(self._phase_ids) == 3:
            self.guard = PhaseGuard(float(self._opt(CONF_FUSE_A)), self._phase_margin, float(self._opt(CONF_MAX_CHARGE)),
                                    GUARD_STEP_PCT, GUARD_HOLD_S, GUARD_RECOVER_S)

    # ---------- config ----------
    def _opt(self, key: str):
        return self.entry.options.get(key, self.entry.data.get(key, DEFAULTS.get(key)))

    @property
    def params(self) -> BatteryParams:
        eff = float(self._opt(CONF_EFFICIENCY)) / 100
        return BatteryParams(
            capacity_kwh=float(self._opt(CONF_CAPACITY)),
            min_soc=float(self._opt(CONF_MIN_SOC)) / 100,
            max_soc=float(self._opt(CONF_MAX_SOC)) / 100,
            high_soc=HIGH_SOC, hold_cost=HOLD_COST,
            max_charge_kw=float(self._opt(CONF_MAX_CHARGE)),
            max_discharge_kw=float(self._opt(CONF_MAX_DISCHARGE)),
            eff_charge=eff ** 0.5,
            eff_discharge=eff ** 0.5,
            wear_cost=float(self._opt(CONF_WEAR_COST)),
            allow_export=bool(self._opt(CONF_ALLOW_EXPORT)),
            sell_margin=float(self._opt(CONF_SELL_MARGIN)),
            sell_buffer=float(self._opt(CONF_SELL_BUFFER)) / 100,
            export_limit_kwh=self.fuse_kwh,
            step_kwh=STEP_KWH,
        )

    @property
    def fuse_kwh(self) -> float:
        return float(self._opt(CONF_FUSE_A)) * 230 * 3 / 1000

    @property
    def _phase_ids(self) -> list[str]:
        return list(self._opt(CONF_PHASE_ENTITIES) or [])

    @property
    def _phase_margin(self) -> float:
        return float(self._opt(CONF_PHASE_MARGIN))

    def grid_room(self, t: datetime, load: float) -> float:
        """Grid energy the battery may draw in hour ``t``.

        Without phase sensors the fuse is treated as one total. With them, the inverter is
        assumed to charge equally on all phases, so the worst phase decides: its share is the
        learned imbalance factor for that hour times the average phase.
        """
        if not self._imbalance and len(self._phase_ids) != 3:
            return max(0.0, self.fuse_kwh - load)
        return room_kwh(load, self.fuse_kwh, self._imbalance.get(t.hour, 1.0), phase_a_to_kw(self._phase_margin))

    # ---------- persistence ----------
    async def async_load(self) -> None:
        self.state = await self._store.async_load() or {}
        self.state.setdefault("soc", self.params.lo)
        self.state.setdefault("savings", {})          # "YYYY-MM-DD" -> kr
        self.state.setdefault("correction", {})       # "hour" -> factor
        self.state.setdefault("errors", [])           # [ [iso, forecast, actual], ... ]
        self.state.setdefault("plan", [])             # stored plan rows
        self.state.setdefault("last_hour", None)      # iso of last booked hour
        self.state.setdefault("days", {})             # "YYYY-MM-DD" -> scenario costs, forecast vs actual
        self.state.setdefault("pv_pairs", [])         # [ [iso, raw forecast, actual], ... ]
        self.state.setdefault("throughput", 0.0)      # kWh delivered from the battery in total
        self.state.setdefault("hours", [])            # booked hours (last HOURS_KEEP) for the card's history tab
        if "stock" not in self.state:
            self._migrate_stock()

    def _migrate_stock(self) -> None:
        """Value the energy in the battery when upgrading from a version that did not.

        Earlier versions booked the whole cost of a charge in the hour it happened. Replay the
        stored hours so the days they cover are counted the new way, and start following
        what the energy in the battery cost.
        """
        lo = self.params.lo
        kwh, value = 0.0, 0.0
        hours = self.state["hours"]
        if hours:
            # Energy already there before the first stored hour was paid for in hours that are
            # no longer stored (and already counted in their day), so it starts at no value.
            h0, bp = hours[0], self.params
            before = (float(h0["soc_end"]) - (h0["grid_charge"] + h0["solar_charge"]) * bp.eff_charge
                      + (h0["to_load"] + h0["to_grid"]) / bp.eff_discharge)
            kwh = max(0.0, before - lo)
        for h in hours:
            cash = float(h["saving"])
            kwh, value, d_value = stock_step(kwh, value, float(h["soc_end"]) - lo, -cash)
            h["saving"] = round(cash + d_value, 2)
            day = _parse_time(h["start"]).date().isoformat()
            if day in self.state["savings"]:
                self.state["savings"][day] = round(self.state["savings"][day] + d_value, 3)
            if "batt" in self.state["days"].get(day, {}):
                self.state["days"][day]["batt"] = round(self.state["days"][day]["batt"] - d_value, 3)
        if not hours:
            kwh = max(0.0, float(self.state["soc"]) - lo)
        self.state["stock"] = {"kwh": kwh, "value": value}

    async def _save(self) -> None:
        await self._store.async_save(self.state)

    @callback
    def async_shutdown_listeners(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_phase:
            self._unsub_phase()
            self._unsub_phase = None

    # ---------- fuse protection ----------
    @callback
    def async_start_phase_guard(self) -> None:
        """Follow the phase currents. Shadow mode only computes the limit, it writes nothing."""
        if self.guard is None or self._unsub_phase:
            return
        self._unsub_phase = async_track_state_change_event(self.hass, self._phase_ids, self._on_phase_change)

    def _phase_currents(self) -> list[float] | None:
        vals = []
        for eid in self._phase_ids:
            st = self.hass.states.get(eid)
            try:
                vals.append(abs(float(st.state)))
            except (AttributeError, TypeError, ValueError):
                return None
        return vals if len(vals) == 3 else None

    def _planned_grid_kw(self, now: datetime) -> float:
        """Grid charging the plan wants this hour, as average power."""
        for row in self.state.get("plan") or []:
            start = _parse_time(row.get("start"))
            if start is not None and start <= now < start + timedelta(hours=1):
                return float(row.get("grid_charge", 0.0))
        return 0.0

    @callback
    def _on_phase_change(self, _event) -> None:
        currents = self._phase_currents()
        if currents is None or self.guard is None:
            return
        now = dt_util.now()
        changed = self.guard.update(now, currents, self._planned_grid_kw(now))
        info = self.guard.info()
        days = self.state.setdefault("guard_days", {})
        days[now.date().isoformat()] = info["idag"]
        if len(days) > 31:
            for k in sorted(days)[:-31]:
                days.pop(k)
        if self.data is not None:
            self.data["guard"] = self._guard_summary()
        # Refresh the entities when the limit changes, otherwise at most every 30 s
        if changed or self._guard_pushed is None or (now - self._guard_pushed).total_seconds() >= 30:
            self._guard_pushed = now
            self.async_update_listeners()

    def _guard_summary(self) -> dict[str, Any]:
        if self.guard is None:
            return {}
        info = self.guard.info()
        days = self.state.get("guard_days", {})
        info["dagar"] = {k: days[k] for k in sorted(days)[-14:]}
        info["obalans"] = {str(h): f for h, f in sorted(self._imbalance.items())}
        info["lage"] = "skugga"
        return info

    async def _on_new_hour(self, _now) -> None:
        await self.async_request_refresh()

    # ---------- history ----------
    async def _history(self) -> dict[datetime, dict[str, float | None]]:
        """Hourly history: import, export, heating energy, outdoor temp and fire temp."""
        imp, exp = self._opt(CONF_IMPORT_ENTITY), self._opt(CONF_EXPORT_ENTITY)
        heating = list(self._opt(CONF_HEATING_ENTITIES) or [])
        outdoor, fire = self._opt(CONF_OUTDOOR_ENTITY), self._opt(CONF_FIRE_ENTITY)
        vvb_e, car_e = self._opt(CONF_VVB_ENERGY_ENTITY), self._opt(CONF_CAR_ENERGY_ENTITY)
        pv_e = self._opt(CONF_PV_ENTITY)
        phases = self._phase_ids if len(self._phase_ids) == 3 else []
        ids = {imp, exp, *heating, *phases} | {e for e in (outdoor, fire, vvb_e, car_e, pv_e) if e}
        start = dt_util.utcnow() - timedelta(days=HISTORY_DAYS)
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, None, ids, "hour", None, {"change", "mean"}
        )

        def rows(eid, key):
            res = {}
            for r in stats.get(eid, []) if eid else []:
                t = _parse_time(r.get("start"))
                v = r.get(key)
                if t is None or v is None:
                    continue
                if key == "change" and (v < 0 or v > 50):
                    continue
                res[_hour(t)] = float(v)
            return res

        ri, re_ = rows(imp, "change"), rows(exp, "change")
        heat = [rows(h, "change") for h in heating]
        ro, rf = rows(outdoor, "mean"), rows(fire, "mean")
        rv, rc = rows(vvb_e, "change"), rows(car_e, "change")
        rp = rows(pv_e, "change")
        if phases:
            self._imbalance = imbalance_factors([{t: abs(v) for t, v in rows(p, "mean").items()} for p in phases])
        out = {}
        for t, v in ri.items():
            h = sum(x.get(t, 0.0) for x in heat) if heat else 0.0
            out[t] = {"imp": v, "exp": re_.get(t, 0.0), "heat": min(h, v + re_.get(t, 0.0) + 20),
                      "out": ro.get(t), "fire": rf.get(t), "vvb": rv.get(t, 0.0), "car": rc.get(t, 0.0),
                      "pv": rp.get(t, 0.0) if pv_e else None}
        return out

    async def _outdoor_forecast(self, horizon: list[datetime], fallback: float | None) -> list[float]:
        """Hourly outdoor temperature from a weather entity, else the current outdoor temperature."""
        temps: dict[datetime, float] = {}
        weather = self._opt(CONF_WEATHER_ENTITY)
        if weather:
            try:
                resp = await self.hass.services.async_call(
                    "weather", "get_forecasts", {"entity_id": weather, "type": "hourly"},
                    blocking=True, return_response=True,
                )
                for item in (resp or {}).get(weather, {}).get("forecast", []):
                    t = _parse_time(item.get("datetime"))
                    if t is not None and item.get("temperature") is not None:
                        temps[_hour(t)] = float(item["temperature"])
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Ingen väderprognos: %s", err)
        cur = fallback
        st = self.hass.states.get(self._opt(CONF_OUTDOOR_ENTITY) or "")
        if st is not None:
            try:
                cur = float(st.state)
            except ValueError:
                pass
        res, last = [], cur if cur is not None else 5.0
        for t in horizon:
            last = temps.get(t, last)
            res.append(last)
        return res

    async def _solar_forecast(self) -> dict[datetime, float]:
        """Hourly PV forecast (kWh) from the solar forecasts configured in the Energy dashboard."""
        if not self._opt(CONF_USE_SOLAR_FORECAST) or not self._opt(CONF_PV_ENTITY):
            return {}
        try:
            from homeassistant.components.energy.data import async_get_manager
            from homeassistant.components.energy.websocket_api import async_get_energy_platforms

            manager = await async_get_manager(self.hass)
            ids: list[str] = []
            for src in (manager.data or {}).get("energy_sources", []):
                if src.get("type") == "solar":
                    ids += src.get("config_entry_solar_forecast") or []
            if not ids:
                return {}
            platforms = await async_get_energy_platforms(self.hass)
            out: dict[datetime, float] = {}
            for entry_id in dict.fromkeys(ids):
                entry = self.hass.config_entries.async_get_entry(entry_id)
                if entry is None or entry.domain not in platforms:
                    continue
                fc = await platforms[entry.domain](self.hass, entry_id)
                for k, wh in ((fc or {}).get("wh_hours") or {}).items():
                    t = _parse_time(k)
                    if t is not None and wh is not None:
                        out[_hour(t)] = out.get(_hour(t), 0.0) + float(wh) / 1000
            return out
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Ingen solprognos: %s", err)
            return {}

    def _pv_ratio(self) -> float:
        """How much the solar forecast over- or underestimates, learned from the last days."""
        pairs = self.state.get("pv_pairs", [])
        fc, act = sum(p[1] for p in pairs), sum(p[2] for p in pairs)
        if fc < 15:
            return 1.0
        return min(1.6, max(0.4, act / fc))

    @staticmethod
    def _cons(row: dict) -> float:
        """Total consumption of a history hour (needs PV production), else grid import."""
        if row.get("pv") is None:
            return row["imp"]
        return max(0.0, row["imp"] + row["pv"] - row["exp"])

    def _flex_hist(self, row: dict) -> float:
        """Energy in a history hour that comes from loads the planner schedules itself."""
        f = 0.0
        if self._opt(CONF_VVB_PLAN_ENTITY) and self._opt(CONF_VVB_ENERGY_ENTITY):
            f += row.get("vvb", 0.0)
        if self._opt(CONF_CAR_SOC_ENTITY) and self._opt(CONF_CAR_ENERGY_ENTITY):
            f += row.get("car", 0.0)
        return f

    def _float_state(self, entity_id: str | None) -> float | None:
        st = self.hass.states.get(entity_id or "")
        if st is None:
            return None
        try:
            return float(st.state)
        except (TypeError, ValueError):
            return None

    # ---------- main loop ----------
    async def _async_update_data(self) -> dict[str, Any]:
        try:
            hist = await self._history()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Kunde inte läsa statistik: {err}") from err
        prices = hourly_prices(self.hass, self._opt(CONF_PRICE_ENTITY))
        sells = hourly_prices(self.hass, self._opt(CONF_SELL_ENTITY))
        if not prices:
            raise UpdateFailed("Inga priser i prissensorn")

        bp = self.params
        self._book_finished_hours(hist, prices, sells, bp)

        now_h = _hour(dt_util.now())
        horizon = sorted(t for t in prices if t >= now_h)[:36]
        times = sorted(hist)
        heat = [hist[t]["heat"] for t in times]
        has_heat = bool(self._opt(CONF_HEATING_ENTITIES))
        pv_fc_raw = await self._solar_forecast()
        gross = bool(pv_fc_raw) and any(t in pv_fc_raw for t in horizon)
        src = (lambda r: self._cons(r)) if gross else (lambda r: r["imp"])
        base_hist = [(t, max(0.0, src(hist[t]) - self._flex_hist(hist[t]) - (hist[t]["heat"] if has_heat else 0.0)))
                     for t in times]
        hist_sur = [(t, hist[t]["exp"]) for t in times]
        corr = {int(k): v for k, v in self.state["correction"].items()}
        base = forecast(horizon, build_profile(base_hist, FORECAST_WEEKS, dt_util.now()), corr)
        surplus = forecast(horizon, build_profile(hist_sur, SOLAR_WEEKS, dt_util.now()))

        heat_fc = [0.0] * len(horizon)
        self._heat_info = {}
        if has_heat:
            thr = float(self._opt(CONF_FIRE_THRESHOLD))
            flags = [(hist[t]["fire"] or 0) >= thr for t in times]
            since = hours_since_fire(flags)
            outdoor = [hist[t]["out"] for t in times]
            fit = fit_heating(heat, outdoor, since)
            if fit is None:
                # not enough clean data: flat average heating per hour of day
                prof = build_profile(list(zip(times, heat)), FORECAST_WEEKS, dt_util.now())
                heat_fc = forecast(horizon, prof)
            else:
                a, b = fit
                model = lambda o: heating_model(a, b, o)  # noqa: E731
                factors = learn_fire_factors(heat, outdoor, since, model)
                typical = typical_fire_hours(flags)
                fire_now = False
                st = self.hass.states.get(self._opt(CONF_FIRE_ENTITY) or "")
                if st is not None:
                    try:
                        fire_now = float(st.state) >= thr
                    except ValueError:
                        fire_now = False
                burning_for = 0
                if fire_now:
                    for f in reversed(flags):
                        if not f:
                            break
                        burning_for += 1
                since_now = 0 if fire_now else (since[-1] + 1 if since else 99)
                outdoor_fc = await self._outdoor_forecast(horizon, next((o for o in reversed(outdoor) if o is not None), None))
                # Heating per hour of day from hours without fire influence, scaled for cold/warm
                # spells (> 3 degrees from the profile's mean) and for the fire and its afterglow.
                clean = [(t, h) for t, h, s in zip(times, heat, since) if s > len(DEFAULT_FIRE_FACTORS)]
                clean_out = [o for o, s in zip(outdoor, since) if s > len(DEFAULT_FIRE_FACTORS) and o is not None]
                mean_out = sum(clean_out) / len(clean_out) if clean_out else 5.0
                hprof = forecast(horizon, build_profile(clean, FORECAST_WEEKS, dt_util.now()), recent_weight=0.0)
                fire_f = heating_forecast([0.0] * len(horizon), lambda _o: 1.0, factors, since_now, burning_for, typical)
                ref = max(1e-6, model(mean_out))
                ratio = [model(o) / ref if abs(o - mean_out) > 3 else 1.0 for o in outdoor_fc]
                heat_fc = [h * r * f for h, r, f in zip(hprof, ratio, fire_f)]
                self._heat_info = {
                    "eldar": fire_now, "timmar_sedan_eld": since_now if since_now < 99 else None,
                    "typisk_eldning_h": round(typical, 1),
                    "eldfaktorer": [round(f, 2) for f in factors],
                    "uppvarmning_a": round(a, 3), "uppvarmning_b_per_grad": round(b, 4),
                    "uppvarmning_prognos_24h": round(sum(heat_fc[:24]), 1),
                }
        price = [prices[t] for t in horizon]

        # Water heater: read the plan from Varmvattenplaneraren (it stays in charge of the heater)
        vvb_fc = [0.0] * len(horizon)
        vvb_ent = self._opt(CONF_VVB_PLAN_ENTITY)
        if vvb_ent and (st := self.hass.states.get(vvb_ent)) is not None:
            vvb_fc = vvb_schedule(st.attributes.get("periods"), horizon)

        # Electric car: plan the cheapest hours before the ready-by time, within the fuse
        car_fc = [0.0] * len(horizon)
        self._car_info = {}
        if self._opt(CONF_CAR_SOC_ENTITY):
            plug = self.hass.states.get(self._opt(CONF_CAR_PLUG_ENTITY) or "")
            plugged = plug is None or plug.state == "on"
            soc_car = self._float_state(self._opt(CONF_CAR_SOC_ENTITY))
            target = self._float_state(self._opt(CONF_CAR_TARGET_ENTITY)) or 80.0
            need = car_need_kwh(soc_car, target, float(self._opt(CONF_CAR_CAPACITY))) if plugged else 0.0
            ready = next_ready_by(dt_util.now(), self._opt(CONF_CAR_READY_BY))
            other = [b_ + h_ + v_ for b_, h_, v_ in zip(base, heat_fc, vvb_fc)]
            car_fc = schedule_car(horizon, price, other, need, float(self._opt(CONF_CAR_POWER)), self.fuse_kwh, ready)
            self._car_info = {
                "inkopplad": plugged, "laddniva": soc_car, "mal": target, "behov_kwh": round(need, 1),
                "klar_senast": ready.isoformat(), "planerat_kwh": round(sum(car_fc), 1),
                "ladda_nu": bool(car_fc and car_fc[0] > 0.05), "perioder": spans(car_fc, horizon),
            }
        self._vvb_info = {"perioder": spans(vvb_fc, horizon), "planerat_kwh": round(sum(vvb_fc), 1)} if vvb_ent else {}

        load = [b_ + h_ + v_ + c_ for b_, h_, v_, c_ in zip(base, heat_fc, vvb_fc, car_fc)]
        sell = [sells.get(t, 0.0) for t in horizon]
        # With a solar forecast the load is total consumption and PV is subtracted here;
        # without one the load is grid import and the surplus comes from last week's export.
        ratio_pv = self._pv_ratio()
        pv_raw = [pv_fc_raw.get(t, 0.0) if gross else None for t in horizon]
        pv_fc = [None if v is None else v * ratio_pv for v in pv_raw]
        cons_fc = list(load)
        if gross:
            net = [l - v for l, v in zip(load, pv_fc)]
            load = [max(0.0, n) for n in net]
            surplus = [max(0.0, -n) for n in net]
        room = [self.grid_room(t, l) for t, l in zip(horizon, load)]
        self._solar_info = {
            "kalla": "solprognos" if gross else "export senaste veckan",
            "korrigering": round(ratio_pv, 2) if gross else None,
            "prognos_24h": round(sum(v or 0.0 for v in pv_fc[:24]), 1) if gross else None,
            "overskott_24h": round(sum(surplus[:24]), 1),
        }

        result = await self.hass.async_add_executor_job(
            optimize, price, sell, load, surplus, room, float(self.state["soc"]), bp
        )
        self.state["plan"] = [
            {
                "start": t.isoformat(), "price": round(p, 3), "load": round(l, 2), "base": round(bs, 2),
                "heat": round(hf, 2), "vvb": round(vv, 2), "car": round(cc, 2), "surplus": round(s, 2),
                "action": hp.action, "grid_charge": round(hp.grid_charge, 2), "solar_charge": round(hp.solar_charge, 2),
                "to_load": round(hp.to_load, 2), "to_grid": round(hp.to_grid, 2),
                "soc_end": round(hp.soc_end, 2),
                "cons": round(cf, 2), "pv": None if pv is None else round(pv, 2),
                "pv_raw": None if pr is None else round(pr, 2),
            }
            for t, p, l, bs, hf, vv, cc, s, hp, cf, pv, pr in zip(
                horizon, price, load, base, heat_fc, vvb_fc, car_fc, surplus, result.hours, cons_fc, pv_fc, pv_raw)
        ]
        self._spare_info = self._spare(result.hours, horizon, bp)
        # Snapshot of the day's forecast, taken at the first plan after midnight
        today = now_h.date().isoformat()
        day = self.state["days"].setdefault(today, {})
        if now_h.hour == 0 and "load_fc" not in day:
            rows_today = [r for r in self.state["plan"] if r["start"][:10] == today]
            day["load_fc"] = round(sum(r["cons"] for r in rows_today), 1)
            if gross:
                day["pv_fc"] = round(sum(r["pv"] or 0.0 for r in rows_today), 1)
            day["saving_fc"] = round(result.saving, 1)
        await self._save()
        return self._summary(result)

    def _spare(self, hours, horizon, bp: BatteryParams) -> dict[str, Any]:
        """Battery energy left over before the next charge, and whether extra load may run on it."""
        buffer = float(self._opt(CONF_SPARE_BUFFER)) / 100
        spare, n = spare_energy(hours, float(self.state["soc"]), bp, buffer)
        kw = float(self._opt(CONF_SPARE_HEAT_KW) or 0.0)
        # On when the spare covers a full hour at that power; stays on down to half an hour.
        need = kw * (SPARE_OFF_HOURS if self._spare_on else 1.0)
        self._spare_on = kw > 0 and spare > 0 and spare >= need
        return {
            "kwh": round(spare, 2),
            "racker_till": horizon[n].isoformat() if n < len(horizon) else None,
            "timmar_till_laddning": n,
            "effekt_kw": kw,
            "timmar_med_effekt": round(spare / kw, 1) if kw > 0 else None,
            "buffert_pct": round(buffer * 100),
            "far_kora": self._spare_on,
        }

    def _book_finished_hours(self, hist, prices, sells, bp: BatteryParams) -> None:
        """Apply the stored plan to every finished hour that has real measurements."""
        last = _parse_time(self.state["last_hour"]) if self.state["last_hour"] else None
        now_h = _hour(dt_util.now())
        plan = {r["start"]: r for r in self.state["plan"]}
        todo = sorted(t for t in hist if t < now_h and (last is None or t > last) and t >= now_h - timedelta(hours=48))
        if last is None:  # first run: start booking from now on
            self.state["last_hour"] = (now_h - timedelta(hours=1)).isoformat()
            return
        soc = float(self.state["soc"])
        for t in todo:
            if t not in prices:
                continue
            load, surplus = hist[t]["imp"], hist[t]["exp"]
            row = plan.get(t.isoformat())
            row_gross = bool(row) and row.get("pv") is not None and hist[t].get("pv") is not None
            nonflex = max(0.0, (self._cons(hist[t]) if row_gross else load) - self._flex_hist(hist[t]))
            actual_base = max(0.0, nonflex - (hist[t]["heat"] if self._opt(CONF_HEATING_ENTITIES) else 0.0))
            hp = HourPlan(0, soc, soc)
            if row:
                hp.grid_charge, hp.solar_charge = row["grid_charge"], row["solar_charge"]
                hp.to_load, hp.to_grid = row["to_load"], row["to_grid"]
                update_correction_dict = {int(k): v for k, v in self.state["correction"].items()}
                update_correction(update_correction_dict, t.hour, row.get("base", row["load"]), actual_base)
                self.state["correction"] = {str(k): v for k, v in update_correction_dict.items()}
                fc_nonflex = row.get("base", row["load"]) + row.get("heat", 0.0)
                self.state["errors"] = (self.state["errors"] + [[t.isoformat(), fc_nonflex, nonflex]])[-24 * 14:]
                if row_gross and row.get("pv_raw") is not None:
                    self.state["pv_pairs"] = (self.state["pv_pairs"] + [[t.isoformat(), row["pv_raw"], hist[t]["pv"]]])[-24 * 10:]
            soc_before = soc
            soc, cost_with, cost_without = apply_hour(
                hp, soc, prices[t], sells.get(t, 0.0), load, surplus, self.grid_room(t, load), bp
            )
            delivered = max(0.0, soc_before - soc) * bp.eff_discharge
            # Count the energy left in the battery at what it cost, so charging at night
            # does not make the day look bad and using it later does not look free.
            stock = self.state["stock"]
            stock["kwh"], stock["value"], d_value = stock_step(
                stock["kwh"], stock["value"], soc - bp.lo, cost_with - cost_without
            )
            cost_with -= d_value
            # What the battery actually did this hour, split the same way as apply_hour()
            drawn = max(0.0, soc - soc_before) / bp.eff_charge
            solar_in = min(surplus, drawn)
            to_load = min(load, delivered)
            self.state["hours"] = (self.state["hours"] + [{
                "start": t.isoformat(), "price": round(prices[t], 3),
                "grid_charge": round(drawn - solar_in, 2), "solar_charge": round(solar_in, 2),
                "to_load": round(to_load, 2), "to_grid": round(delivered - to_load, 2),
                "soc_end": round(soc, 2), "saving": round(cost_without - cost_with, 2),
            }])[-HOURS_KEEP:]
            self.state["throughput"] = round(float(self.state["throughput"]) + delivered, 3)
            day = t.date().isoformat()
            self.state["savings"][day] = round(self.state["savings"].get(day, 0.0) + cost_without - cost_with, 3)
            d = self.state["days"].setdefault(day, {})
            pv_act = hist[t].get("pv")
            for key, val in (("solar", cost_without), ("batt", cost_with), ("load_act", self._cons(hist[t])),
                             ("imp", load), ("exp", surplus),
                             ("dis", delivered), ("wear", delivered * bp.wear_cost)):
                d[key] = round(d.get(key, 0.0) + val, 3)
            if pv_act is not None:
                d["grid"] = round(d.get("grid", 0.0) + self._cons(hist[t]) * prices[t], 3)
                d["pv_act"] = round(d.get("pv_act", 0.0) + pv_act, 3)
            self.state["last_hour"] = t.isoformat()
        self.state["soc"] = soc
        # keep a year of daily savings
        keep = sorted(self.state["savings"])[-400:]
        self.state["savings"] = {k: self.state["savings"][k] for k in keep}
        self.state["days"] = {k: self.state["days"][k] for k in sorted(self.state["days"])[-400:]}

    def _health(self, days: dict) -> dict[str, Any]:
        """Battery wear so far: throughput, equivalent full cycles and share of the warranty used."""
        bp = self.params
        usable = max(0.1, bp.hi - bp.lo)
        thr = float(self.state.get("throughput", 0.0))
        recent = [days[k] for k in sorted(days)[-30:] if "dis" in days[k]]
        per_day = sum(d["dis"] for d in recent) / len(recent) if recent else None
        life = float(self._opt(CONF_LIFETIME_MWH) or 0)
        price = float(self._opt(CONF_BATTERY_PRICE) or 0)
        info: dict[str, Any] = {
            "genomstromning_kwh": round(thr, 1),
            "cykler": round(thr / usable, 1),
            "cykler_per_ar": round(365 * per_day / usable) if per_day is not None else None,
            "slitagekostnad": bp.wear_cost,
            "slitage_kr": round(sum(d.get("wear", 0.0) for d in days.values()), 1),
            "max_laddniva": round(100 * bp.max_soc),
        }
        if life > 0:
            info["garanti_forbrukad_pct"] = round(100 * thr / (life * 1000), 2)
            if per_day:
                info["garanti_racker_ar"] = round(life * 1000 / (per_day * 365), 1)
            if price > 0:
                info["pris_per_garanterad_kwh"] = round(price / (life * 1000), 2)
        return info

    def _summary(self, result) -> dict[str, Any]:
        today = dt_util.now().date()
        sav = self.state["savings"]
        month = sum(v for k, v in sav.items() if k.startswith(today.strftime("%Y-%m")))
        errs = self.state["errors"][-24 * 7:]
        err_pct = None
        tot_act = sum(e[2] for e in errs)
        if errs and tot_act > 0:
            err_pct = round(100 * sum(abs(e[1] - e[2]) for e in errs) / tot_act, 1)
        cur = self.state["plan"][0] if self.state["plan"] else None
        days = self.state["days"]
        ym = today.strftime("%Y-%m")

        def scen(keys):
            sel = [days[k] for k in keys if "solar" in days[k]]
            if not sel:
                return {}
            has_grid = all("grid" in d for d in sel)
            return {
                "bara_nat": round(sum(d["grid"] for d in sel), 1) if has_grid else None,
                "sol": round(sum(d["solar"] for d in sel), 1),
                "sol_batteri": round(sum(d["batt"] for d in sel), 1),
                "dagar": len(sel),
            }

        return {
            "scenarios": {
                "idag": scen([k for k in days if k == today.isoformat()]),
                "manad": scen([k for k in days if k.startswith(ym)]),
                "totalt": scen(list(days)),
            },
            "days": {k: days[k] for k in sorted(days)[-31:]},
            "solar": getattr(self, "_solar_info", {}),
            "health": self._health(days),
            "guard": self._guard_summary(),
            "plan": self.state["plan"],
            "history": self.state["hours"],
            "current_action": cur["action"] if cur else None,
            "soc_kwh": round(float(self.state["soc"]), 2),
            "soc_pct": round(100 * float(self.state["soc"]) / self.params.capacity_kwh, 1),
            "saving_today": round(sav.get(today.isoformat(), 0.0), 2),
            "saving_month": round(month, 2),
            "saving_total": round(sum(sav.values()), 2),
            "saving_daily": sav,
            "planned_saving": round(result.saving, 2),
            "forecast_error_pct": err_pct,
            "correction": self.state["correction"],
            "heating": getattr(self, "_heat_info", {}),
            "car": getattr(self, "_car_info", {}),
            "vvb": getattr(self, "_vvb_info", {}),
            "spare": getattr(self, "_spare_info", {}),
        }
