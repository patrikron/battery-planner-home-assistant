"""Flexible loads: water heater (read from Varmvattenplaneraren) and EV charging (planned here)."""

from __future__ import annotations

from datetime import datetime, timedelta


def vvb_schedule(periods: list[dict] | None, horizon: list[datetime]) -> list[float]:
    """Spread the water heater planner's periods [{s, e, kwh}] (ms timestamps) over the horizon hours."""
    out = [0.0] * len(horizon)
    for p in periods or []:
        try:
            s = datetime.fromtimestamp(p["s"] / 1000, tz=horizon[0].tzinfo) if horizon else None
            e = datetime.fromtimestamp(p["e"] / 1000, tz=horizon[0].tzinfo) if horizon else None
            kwh = float(p.get("kwh", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if s is None or e is None or e <= s or kwh <= 0:
            continue
        total = (e - s).total_seconds()
        for i, h in enumerate(horizon):
            a, b = max(s, h), min(e, h + timedelta(hours=1))
            if b > a:
                out[i] += kwh * (b - a).total_seconds() / total
    return out


def car_need_kwh(soc: float | None, target: float | None, capacity_kwh: float, efficiency: float = 0.9) -> float:
    """Energy to take from the grid to bring the car from soc % to target %."""
    if soc is None or target is None or target <= soc:
        return 0.0
    return (target - soc) / 100 * capacity_kwh / efficiency


def next_ready_by(now: datetime, hhmm: str) -> datetime:
    try:
        h, m = (int(x) for x in str(hhmm).split(":")[:2])
    except ValueError:
        h, m = 7, 0
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def schedule_car(horizon: list[datetime], price: list[float], other_load: list[float], need_kwh: float,
                 power_kw: float, fuse_kwh: float, ready_by: datetime) -> list[float]:
    """Put the car's energy in the cheapest hours before ready_by, without exceeding the fuse."""
    out = [0.0] * len(horizon)
    if need_kwh <= 0.05:
        return out
    cand = [i for i, t in enumerate(horizon) if t < ready_by]
    left = need_kwh
    for i in sorted(cand, key=lambda k: price[k]):
        room = max(0.0, fuse_kwh - other_load[i])
        take = min(power_kw, room, left)
        if take > 0.05:
            out[i] = take
            left -= take
        if left <= 0.05:
            break
    return out


def spans(values: list[float], horizon: list[datetime], limit: float = 0.05) -> list[dict]:
    """Group consecutive hours with values above limit: [{start, end, kwh}]."""
    out = []
    for v, t in zip(values, horizon):
        if v <= limit:
            continue
        if out and out[-1]["_end"] == t:
            out[-1]["_end"] = t + timedelta(hours=1)
            out[-1]["kwh"] += v
        else:
            out.append({"_start": t, "_end": t + timedelta(hours=1), "kwh": v})
    return [{"start": s["_start"].isoformat(), "end": s["_end"].isoformat(), "kwh": round(s["kwh"], 1)} for s in out]
