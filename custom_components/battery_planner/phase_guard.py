"""Per-phase fuse protection.

A hybrid inverter such as Growatt MID-XH charges and discharges equally on all three
phases, so grid charging is limited by the most loaded phase, not by the total.

- ``imbalance_factors()`` learns from history how much heavier the worst phase is than
  the average phase, per hour of day. Planning uses it so the hourly plan fits the fuse.
- ``PhaseGuard`` follows the phase currents in real time and says which charge limit the
  battery should have right now. Short peaks (a radiator switching on) are tolerated: the
  limit only goes down after a phase has been over the target for ``hold_s`` seconds, and
  only goes back up after ``recover_s`` seconds with room to spare. That keeps the number
  of writes to the inverter low.
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import median
from typing import Any

VOLT = 230.0


def kw_to_phase_a(kw: float) -> float:
    """Current per phase for a symmetric three-phase load."""
    return kw * 1000 / (3 * VOLT)


def phase_a_to_kw(a: float) -> float:
    return a * 3 * VOLT / 1000


def headroom_kw(currents: list[float], fuse_a: float, margin_a: float, battery_kw: float = 0.0) -> float:
    """Charge power the battery could add now without any phase going over fuse − margin.

    ``currents`` are the measured amps per phase. Once a real battery charges, its own
    current is part of the measurement; pass its power as ``battery_kw`` to take it out.
    """
    worst = max(currents) - kw_to_phase_a(max(0.0, battery_kw))
    return max(0.0, phase_a_to_kw(fuse_a - margin_a - worst))


def imbalance_factors(phase_rows: list[dict[datetime, float]], min_avg_a: float = 1.0) -> dict[int, float]:
    """Median of worst phase / average phase per hour of day, from hourly mean currents."""
    by_hour: dict[int, list[float]] = {}
    if len(phase_rows) < 2:
        return {}
    common = set(phase_rows[0]).intersection(*phase_rows[1:])
    for t in common:
        vals = [r[t] for r in phase_rows]
        avg = sum(vals) / len(vals)
        if avg >= min_avg_a:
            by_hour.setdefault(t.hour, []).append(max(vals) / avg)
    return {h: round(min(2.5, max(1.0, median(v))), 3) for h, v in by_hour.items() if len(v) >= 3}


def room_kwh(load_kwh: float, fuse_kwh: float, factor: float = 1.0, margin_kwh: float = 0.0) -> float:
    """Grid energy the battery may draw in an hour when the worst phase is ``factor`` × average."""
    return max(0.0, fuse_kwh - margin_kwh - factor * max(0.0, load_kwh))


class PhaseGuard:
    """Charge limit (% of max charge power) that keeps every phase under the fuse."""

    def __init__(self, fuse_a: float, margin_a: float, max_charge_kw: float,
                 step_pct: int = 10, hold_s: float = 45, recover_s: float = 180) -> None:
        self.fuse_a, self.margin_a = fuse_a, margin_a
        self.max_kw = max(0.1, max_charge_kw)
        self.step, self.hold_s, self.recover_s = step_pct, hold_s, recover_s
        self.limit_pct = 100
        self.room_kw: float | None = None
        self.planned_kw = 0.0
        self.currents: list[float] = []
        self._over_since: datetime | None = None
        self._free_since: datetime | None = None
        self._last: datetime | None = None
        self.changes = 0                      # limit changes since start (= writes to the inverter)
        self.day: dict[str, Any] = {}         # today's statistics, see _new_day()
        self._day_key: str | None = None

    # ---------- helpers ----------
    @property
    def limit_kw(self) -> float:
        return self.max_kw * self.limit_pct / 100

    @property
    def limiting(self) -> bool:
        return self.planned_kw > 0.05 and self.limit_kw + 1e-6 < self.planned_kw

    def _fit_pct(self, room: float) -> int:
        """Largest step that fits in ``room`` kW."""
        pct = 100 * room / self.max_kw
        return int(max(0, min(100, math.floor(pct / self.step) * self.step)))

    @staticmethod
    def _new_day() -> dict[str, Any]:
        return {"sankningar": 0, "begransad_min": 0.0, "forlorad_kwh": 0.0, "over_utan_skydd_min": 0.0,
                "minsta_utrymme_kw": None}

    # ---------- main ----------
    def update(self, now: datetime, currents: list[float], planned_kw: float, battery_kw: float = 0.0) -> bool:
        """Feed a new measurement. Returns True when the limit changed."""
        key = now.date().isoformat()
        if key != self._day_key:
            self._day_key, self.day = key, self._new_day()
        dt = 0.0 if self._last is None else max(0.0, min(300.0, (now - self._last).total_seconds()))

        # Book the period since the last measurement with the state that applied then
        if dt and self.planned_kw > 0.05:
            if self.limiting:
                self.day["begransad_min"] += dt / 60
                self.day["forlorad_kwh"] += (self.planned_kw - self.limit_kw) * dt / 3600
            if self.room_kw is not None and self.planned_kw > self.room_kw + 1e-6:
                self.day["over_utan_skydd_min"] += dt / 60
        self._last = now

        self.currents = list(currents)
        self.planned_kw = max(0.0, planned_kw)
        room = headroom_kw(currents, self.fuse_a, self.margin_a, battery_kw)
        self.room_kw = room
        if self.day["minsta_utrymme_kw"] is None or room < self.day["minsta_utrymme_kw"]:
            self.day["minsta_utrymme_kw"] = round(room, 2)

        old = self.limit_pct
        charging = min(self.planned_kw, self.limit_kw)
        if charging > room + 1e-6:
            self._free_since = None
            self._over_since = self._over_since or now
            if (now - self._over_since).total_seconds() >= self.hold_s:
                self.limit_pct = min(self.limit_pct, self._fit_pct(room))
                self._over_since = None
        else:
            self._over_since = None
            if self.limit_pct < 100 and self._fit_pct(room) > self.limit_pct:
                self._free_since = self._free_since or now
                if (now - self._free_since).total_seconds() >= self.recover_s:
                    self.limit_pct = self._fit_pct(room)
                    self._free_since = None
            else:
                self._free_since = None
        if self.limit_pct != old:
            self.changes += 1
            if self.limit_pct < old:
                self.day["sankningar"] += 1
            return True
        return False

    def info(self) -> dict[str, Any]:
        worst = max(range(len(self.currents)), key=lambda i: self.currents[i]) + 1 if self.currents else None
        return {
            "faser_a": [round(c, 1) for c in self.currents],
            "tyngsta_fas": worst,
            "mal_a": round(self.fuse_a - self.margin_a, 1),
            "utrymme_kw": None if self.room_kw is None else round(self.room_kw, 2),
            "laddgrans_pct": self.limit_pct,
            "laddgrans_kw": round(self.limit_kw, 2),
            "planerad_laddning_kw": round(self.planned_kw, 2),
            "begransar": self.limiting,
            "idag": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.day.items()},
            "andringar": self.changes,
        }
