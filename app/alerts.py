"""Amber/red oxygen alert engine for the desktop widget and monitor-flash overlay.

The server is the single source of truth for the alert level and the snooze, so
the widget, overlay and tray always agree. Everything here is a pure in-memory
state machine: every method takes ``now`` (epoch seconds) so tests control time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.models import OwletReading, raw_flag_active
from app.quality import is_offline_reading

ENTER_READINGS = 2     # consecutive distinct readings needed to enter a level
EXIT_READINGS = 2      # consecutive distinct readings needed to leave a level
HYSTERESIS = 1         # to leave a level SpO2 must reach threshold + HYSTERESIS
STALE_SECONDS = 60     # no new reading for this long -> device is stale
SNOOZE_SECONDS = 15
TEST_DEFAULT_SECONDS = 20
TEST_MIN_SECONDS = 5
TEST_MAX_SECONDS = 120
TEST_SERIAL = "TEST"

LEVELS = ("none", "amber", "red")
_RANK = {name: i for i, name in enumerate(LEVELS)}

CRIT_FLAGS = ("CRIT_OX_ALRT", "critical_oxygen_alert")
LOW_FLAGS = ("LOW_OX_ALRT", "low_oxygen_alert")


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _worst(levels: list[str]) -> str:
    return max(levels, key=lambda lvl: _RANK[lvl], default="none")


@dataclass
class DeviceAlertTracker:
    account_id: int
    device_serial: str
    account_name: str = ""
    level: str = "none"
    reason: str | None = None
    spo2: float | None = None
    heart_rate: float | None = None
    recorded_at: datetime | None = None
    last_new_reading_wall: float | None = None
    sock_reporting: bool = True
    charging: bool = False
    warn_threshold: int = 90
    critical_threshold: int = 86
    stale_after: float = STALE_SECONDS
    # consecutive-reading streaks
    _amber_in: int = 0
    _red_in: int = 0
    _amber_out: int = 0
    _red_out: int = 0
    _last_recorded_at: datetime | None = field(default=None, repr=False)

    def _reset_streaks(self) -> None:
        self._amber_in = self._red_in = self._amber_out = self._red_out = 0

    def observe(
        self,
        reading: OwletReading,
        warn: int,
        critical: int,
        now: float,
        *,
        stale_after: float = STALE_SECONDS,
    ) -> None:
        # 1. The poller fetches faster than Owlet updates: count each sample once.
        if self._last_recorded_at is not None and reading.recorded_at == self._last_recorded_at:
            return
        self._last_recorded_at = reading.recorded_at
        self.warn_threshold = warn
        self.critical_threshold = critical
        self.stale_after = stale_after
        self.recorded_at = reading.recorded_at
        self.last_new_reading_wall = now
        self.heart_rate = reading.heart_rate if reading.heart_rate and reading.heart_rate > 0 else None

        # 2. Sock off / charging / disconnected never alerts.
        if is_offline_reading(reading) or reading.oxygen_saturation is None:
            self.sock_reporting = False
            self.charging = bool(reading.charging)
            self.level = "none"
            self.reason = None
            self._reset_streaks()
            return
        self.sock_reporting = True
        self.charging = bool(reading.charging)

        spo2 = float(reading.oxygen_saturation)
        self.spo2 = spo2
        raw = reading.raw or {}
        crit_flag = raw_flag_active(raw, *CRIT_FLAGS)
        low_flag = raw_flag_active(raw, *LOW_FLAGS)

        # 3. Streaks. A red reading also counts toward amber.
        red_tier = spo2 < critical or crit_flag
        amber_tier = red_tier or spo2 < warn or low_flag
        self._red_in = self._red_in + 1 if red_tier else 0
        self._amber_in = self._amber_in + 1 if amber_tier else 0
        self._red_out = self._red_out + 1 if (spo2 >= critical + HYSTERESIS and not crit_flag) else 0
        self._amber_out = self._amber_out + 1 if (spo2 >= warn + HYSTERESIS and not low_flag and not crit_flag) else 0

        # 4. Escalate. Owlet's own alarm flags are a backstop and act immediately.
        if crit_flag and self.level != "red":
            self.level, self.reason = "red", "owlet_flag"
            return
        if low_flag and self.level == "none":
            self.level, self.reason = "amber", "owlet_flag"
            return
        if self._red_in >= ENTER_READINGS and self.level != "red":
            self.level, self.reason = "red", "below_threshold"
            return
        if self._amber_in >= ENTER_READINGS and self.level == "none":
            self.level, self.reason = "amber", "below_threshold"
            return

        # 5. De-escalate. A reading exactly at the threshold holds the level.
        if self.level == "red" and self._red_out >= EXIT_READINGS:
            if self._amber_out >= EXIT_READINGS:
                self.level, self.reason = "none", None
            else:
                self.level = "amber"
                self.reason = "owlet_flag" if low_flag else "below_threshold"
        elif self.level == "amber" and self._amber_out >= EXIT_READINGS:
            self.level, self.reason = "none", None

    def is_stale(self, now: float) -> bool:
        return self.last_new_reading_wall is None or now - self.last_new_reading_wall > self.stale_after

    def as_dict(self, now: float) -> dict[str, Any]:
        stale = self.is_stale(now)
        age = None if self.last_new_reading_wall is None else max(0, int(now - self.last_new_reading_wall))
        return {
            "account_id": self.account_id,
            "account_name": self.account_name,
            "device_serial": self.device_serial,
            "level": "none" if stale else self.level,
            "reason": None if stale else self.reason,
            "spo2": None if self.spo2 is None else round(self.spo2),
            "heart_rate": None if self.heart_rate is None else round(self.heart_rate),
            "recorded_at": _iso_dt(self.recorded_at),
            "age_seconds": age,
            "stale": stale,
            "sock_reporting": self.sock_reporting,
            "charging": self.charging,
            "warn_threshold": self.warn_threshold,
            "critical_threshold": self.critical_threshold,
        }


class AlertMonitor:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.trackers: dict[tuple[int, str], DeviceAlertTracker] = {}
        self.snoozed_until: float | None = None
        self.snoozed_level: str | None = None
        self.test_level: str | None = None
        self.test_until: float | None = None

    # ---- inputs -------------------------------------------------------------
    def observe(
        self,
        account_id: int,
        account_name: str,
        reading: OwletReading,
        warn: int,
        critical: int,
        now: float,
        *,
        stale_after: float = STALE_SECONDS,
    ) -> None:
        key = (int(account_id), str(reading.device_serial))
        tracker = self.trackers.get(key)
        if tracker is None:
            tracker = self.trackers[key] = DeviceAlertTracker(int(account_id), str(reading.device_serial))
        tracker.account_name = account_name
        tracker.observe(reading, warn, critical, now, stale_after=stale_after)

    def set_test(self, level: str, seconds: int | None, now: float) -> None:
        if level == "none":
            self.test_level = self.test_until = None
            return
        if level not in ("amber", "red"):
            raise ValueError(f"unknown level {level!r}")
        secs = TEST_DEFAULT_SECONDS if seconds is None else int(seconds)
        secs = max(TEST_MIN_SECONDS, min(TEST_MAX_SECONDS, secs))
        self.test_level = level
        self.test_until = now + secs

    def snooze(self, account_ids: list[int] | None, now: float) -> float | None:
        level = self._level(account_ids, now)
        if level == "none":
            return None
        self.snoozed_until = now + SNOOZE_SECONDS
        self.snoozed_level = level
        return self.snoozed_until

    # ---- outputs ------------------------------------------------------------
    def _test_device(self, now: float) -> dict[str, Any] | None:
        if self.test_level is None or self.test_until is None:
            return None
        if now >= self.test_until:
            self.test_level = self.test_until = None
            return None
        return {
            "account_id": 0,
            "account_name": "Test alert",
            "device_serial": TEST_SERIAL,
            "level": self.test_level,
            "reason": "test",
            "spo2": 84 if self.test_level == "red" else 88,
            "heart_rate": 130,
            "recorded_at": _iso(now),
            "age_seconds": 0,
            "stale": False,
            "sock_reporting": True,
            "charging": False,
            "warn_threshold": 90,
            "critical_threshold": 86,
        }

    def _devices(self, account_ids: list[int] | None, now: float) -> list[dict[str, Any]]:
        allowed = None if account_ids is None else {int(a) for a in account_ids}
        devices = [
            tracker.as_dict(now)
            for key, tracker in sorted(self.trackers.items())
            if allowed is None or key[0] in allowed
        ]
        test = self._test_device(now)
        if test is not None:
            devices.append(test)
        return devices

    def _level(self, account_ids: list[int] | None, now: float) -> str:
        return _worst([d["level"] for d in self._devices(account_ids, now) if not d["stale"]])

    def state(self, account_ids: list[int] | None, collecting: bool, now: float) -> dict[str, Any]:
        devices = self._devices(account_ids, now)
        level = _worst([d["level"] for d in devices if not d["stale"]])
        if self.snoozed_until is not None:
            expired = now >= self.snoozed_until
            escalated = _RANK[level] > _RANK[self.snoozed_level or "none"]
            if expired or escalated or level == "none":
                self.snoozed_until = self.snoozed_level = None
        snoozed = self.snoozed_until is not None
        return {
            "generated_at": _iso(now),
            "collecting": bool(collecting),
            "level": level,
            "active_level": "none" if snoozed else level,
            "snoozed": snoozed,
            "snoozed_until": _iso(self.snoozed_until),
            "snooze_seconds_left": max(0, int(round(self.snoozed_until - now))) if snoozed else 0,
            "snooze_seconds": SNOOZE_SECONDS,
            "test": self.test_level is not None,
            "devices": devices,
        }


ALERTS = AlertMonitor()
