"""Consumption and solar forecast from hourly history (pure Python)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean


def build_profile(history: list[tuple[datetime, float]], weeks: int = 4, now: datetime | None = None):
    """Average value per (weekday, hour) and per hour over the last `weeks` weeks.

    history: list of (local start time, kWh) for each hour.
    Returns (by_weekday_hour, by_hour) dictionaries.
    """
    if now is None:
        now = datetime.now().astimezone()
    cutoff = now - timedelta(days=7 * weeks)
    wd: dict[tuple[int, int], list[float]] = defaultdict(list)
    hr: dict[int, list[float]] = defaultdict(list)
    for t, v in history:
        if t < cutoff or v is None or v < 0:
            continue
        wd[(t.weekday(), t.hour)].append(v)
        hr[t.hour].append(v)
    return ({k: mean(v) for k, v in wd.items()}, {k: mean(v) for k, v in hr.items()})


def forecast(hours: list[datetime], profile, correction: dict[int, float] | None = None,
             recent_weight: float = 0.5) -> list[float]:
    """Forecast kWh for each hour: blend of weekday-hour and all-days-hour averages,
    multiplied by a learned correction factor per hour of day."""
    by_wd, by_hr = profile
    out = []
    for t in hours:
        a = by_wd.get((t.weekday(), t.hour))
        b = by_hr.get(t.hour, 0.0)
        v = b if a is None else recent_weight * a + (1 - recent_weight) * b
        if correction:
            v *= correction.get(t.hour, 1.0)
        out.append(max(0.0, v))
    return out


def update_correction(correction: dict[int, float], hour: int, forecast_kwh: float, actual_kwh: float,
                      rate: float = 0.1, lo: float = 0.6, hi: float = 1.6) -> None:
    """Move the correction for this hour of day a little towards actual/forecast."""
    if forecast_kwh <= 0.05:
        return
    ratio = actual_kwh / forecast_kwh
    old = correction.get(hour, 1.0)
    new = old * (1 - rate) + old * ratio * rate
    correction[hour] = min(hi, max(lo, new))


DEFAULT_FIRE_FACTORS = [0.15, 0.35, 0.5, 0.6, 0.75, 0.8, 0.85, 0.9, 0.95]


def hours_since_fire(fire_flags: list[bool]) -> list[int]:
    """For each hour, how many hours since the fire last burned (0 = burning, 99 = long ago)."""
    out, last = [], None
    for i, f in enumerate(fire_flags):
        if f:
            last = i
        out.append(99 if last is None else i - last)
    return out


def fit_heating(heat: list[float], outdoor: list[float | None], since_fire: list[int],
                base_temp: float = 18.0, min_hours: int = 48):
    """Fit heating_kWh = a + b * max(0, base_temp - outdoor) on hours without fire influence.

    Returns (a, b) or None if there is not enough data.
    """
    xs, ys = [], []
    for h, o, s in zip(heat, outdoor, since_fire):
        if o is None or s <= len(DEFAULT_FIRE_FACTORS):
            continue
        xs.append(max(0.0, base_temp - o))
        ys.append(h)
    if len(xs) < min_hours:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    b = 0.0 if sxx < 1e-9 else max(0.0, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx)
    a = max(0.0, my - b * mx)
    return a, b


def heating_model(a: float, b: float, outdoor: float, base_temp: float = 18.0) -> float:
    return max(0.0, a + b * max(0.0, base_temp - outdoor))


def learn_fire_factors(heat, outdoor, since_fire, model, min_samples: int = 6) -> list[float]:
    """Ratio actual heating / modelled heating for 0..8 hours after the fire, learned from history."""
    out = []
    for k, default in enumerate(DEFAULT_FIRE_FACTORS):
        act = mod = 0.0
        n = 0
        for h, o, s in zip(heat, outdoor, since_fire):
            if s == k and o is not None:
                act += h
                mod += model(o)
                n += 1
        out.append(min(1.2, max(0.0, act / mod)) if n >= min_samples and mod > 0.05 else default)
    # never let the effect grow again before it fades
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1]) if out[i] < out[i - 1] else out[i]
    return out


def typical_fire_hours(fire_flags: list[bool], default: float = 6.0) -> float:
    runs, cur = [], 0
    for f in fire_flags:
        if f:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    return sum(runs) / len(runs) if runs else default


def heating_forecast(outdoor_fc: list[float], model, fire_factors: list[float],
                     since_now: int, burning_for: int, typical_hours: float) -> list[float]:
    """Heating per future hour, scaled down while the fire burns and during the afterglow.

    since_now: hours since the fire last burned (0 = burning now, 99 = no recent fire)
    burning_for: how many hours the current fire has burned
    """
    out = []
    remaining = max(1, round(typical_hours - burning_for)) if since_now == 0 else 0
    for h, o in enumerate(outdoor_fc, start=1):
        base = model(o)
        if since_now == 0:
            s = 0 if h <= remaining else h - remaining
        else:
            s = since_now + h
        f = fire_factors[s] if s < len(fire_factors) else 1.0
        out.append(base * f)
    return out


def split_net(consumption: list[float], solar: list[float]) -> tuple[list[float], list[float]]:
    """Turn consumption + solar into what must be bought (load) and what would be exported (surplus)."""
    load = [max(0.0, c - s) for c, s in zip(consumption, solar)]
    surplus = [max(0.0, s - c) for c, s in zip(consumption, solar)]
    return load, surplus
