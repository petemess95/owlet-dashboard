from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models import OwletReading, real_time_vitals


def is_offline_reading(reading: OwletReading) -> bool:
    """Return true when the sock/device is not producing physiological readings.

    Owlet can emit zero-valued vitals when the sock is off-body or disconnected.
    It can also keep returning stale nonzero vitals while explicit sock-off or
    sock-disconnect flags are active. Those rows are useful for graphing coverage
    gaps, but should not contribute to averages, sleep totals, or trend analysis.
    """

    return (
        bool(reading.sock_disconnected)
        or bool(reading.sock_off)
        or bool(reading.vitals_frozen)
        or _zero_or_negative(reading.heart_rate)
        or _zero_or_negative(reading.oxygen_saturation)
    )


# With the base station off, Owlet keeps streaming fresh packets (movement and
# signal strength change) but HR/SpO2 stay pinned to the last values the sock
# measured — for as long as the base stays off. Owlet's sock-off flag clears
# once the base is off, so nothing else marks these rows. Real readings with
# the base off never hold the exact same HR and SpO2 for more than ~70 s in
# recorded data; frozen stretches run 15 minutes to hours.
FROZEN_AFTER_SECONDS = 90.0


def base_station_off(raw: dict[str, Any]) -> bool:
    """True only when Owlet explicitly reports the base station off."""
    value = raw.get("base_station_on")
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        value = real_time_vitals(raw).get("bso")
    if value is None or isinstance(value, bool):
        return value is False
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False


class FrozenVitalsDetector:
    """Flags readings whose HR/SpO2 are a frozen copy, not a live measurement.

    Stateful per poller: remembers when the current HR/SpO2 pair first
    appeared. Once the base is off and the pair has not changed for
    FROZEN_AFTER_SECONDS, the reading is marked ``vitals_frozen`` (also in its
    raw payload, so the flag survives storage)."""

    def __init__(self, frozen_after_seconds: float = FROZEN_AFTER_SECONDS):
        self.frozen_after = frozen_after_seconds
        self._key: tuple[str, float, float] | None = None
        self._since: datetime | None = None

    def mark(self, reading: OwletReading) -> OwletReading:
        hr, spo2 = reading.heart_rate, reading.oxygen_saturation
        if hr is None or spo2 is None or hr <= 0 or spo2 <= 0:
            self._key = self._since = None
            return reading
        key = (reading.device_serial, float(hr), float(spo2))
        if key != self._key or self._since is None:
            self._key, self._since = key, reading.recorded_at
            return reading
        held = (reading.recorded_at - self._since).total_seconds()
        if held >= self.frozen_after and base_station_off(reading.raw or {}):
            reading.vitals_frozen = True
            reading.raw["vitals_frozen"] = True
        return reading


def _zero_or_negative(value: float | int | None) -> bool:
    return value is not None and float(value) <= 0
