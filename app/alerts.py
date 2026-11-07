"""Amber/red oxygen alert engine for the desktop widget and monitor-flash overlay.

The server is the single source of truth for the alert level and the snooze, so
the widget, overlay and tray always agree. Everything here is a pure in-memory
state machine: every method takes ``now`` (epoch seconds) so tests control time.
"""

from __future__ import annotations

import logging
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
# A device that goes quiet mid-alert warns "signal lost" for this long after it
# turns stale, then falls back to plain "no data" so the overlay can't run forever.
SIGNAL_LOST_SECONDS = 600
TEST_DEFAULT_SECONDS = 20
TEST_MIN_SECONDS = 5
TEST_MAX_SECONDS = 120
TEST_SERIAL = "TEST"

LEVELS = ("none", "amber", "red")
_RANK = {name: i for i, name in enumerate(LEVELS)}

CRIT_FLAGS = ("CRIT_OX_ALRT", "critical_oxygen_alert")
LOW_FLAGS = ("LOW_OX_ALRT", "low_oxygen_alert")
logger = logging.getLogger(__name__)


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
    _last_sample_key: tuple[Any, ...] | None = field(default=None, repr=False)
    _warned_missing_timestamp: bool = field(default=False, repr=False)

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
        # 1. Count distinct samples once, but always process status/alarm flags:
        # those properties can change without a new vitals timestamp.
        raw = reading.raw or {}
        crit_flag = raw_flag_active(raw, *CRIT_FLAGS)
        low_flag = raw_flag_active(raw, *LOW_FLAGS)
        if reading.timestamp_synthesized:
            sample_key = ("vitals", reading.oxygen_saturation, reading.heart_rate,
                          crit_flag, low_flag, reading.charging,
                          reading.sock_off, reading.sock_disconnected)
            if not self._warned_missing_timestamp:
                logger.warning(
                    "Missing Owlet timestamp for account_id=%s device=%s; deduplicating alert samples by vitals and flags",
                    self.account_id, self.device_serial,
                )
                self._warned_missing_timestamp = True
        else:
            sample_key = ("timestamp", reading.recorded_at)
        duplicate = sample_key == self._last_sample_key
        self.warn_threshold = warn
        self.critical_threshold = critical
        self.stale_after = stale_after
        if not duplicate:
            self._last_sample_key = sample_key
            self.recorded_at = reading.recorded_at
            self.last_new_reading_wall = now
        self.heart_rate = reading.heart_rate if reading.heart_rate and reading.heart_rate > 0 else None

        # 2. Sock off / charging / disconnected never alerts.
        if reading.charging or is_offline_reading(reading) or reading.oxygen_saturation is None:
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

        # 3. Streaks only advance on distinct readings. Red also counts as amber.
        if not duplicate:
            # Flags qualify immediately and must not count as threshold evidence.
            red_tier = spo2 < critical
            amber_tier = spo2 < warn
            self._red_in = self._red_in + 1 if red_tier else 0
            self._amber_in = self._amber_in + 1 if amber_tier else 0
            self._red_out = self._red_out + 1 if (spo2 >= critical + HYSTERESIS and not crit_flag) else 0
            self._amber_out = self._amber_out + 1 if (spo2 >= warn + HYSTERESIS and not low_flag and not crit_flag) else 0

        # 4. Choose the highest qualifying target; a low flag must never delay red.
        # Duplicate samples may escalate through flags, never through thresholds.
        red_threshold = not duplicate and spo2 < critical and self._red_in >= ENTER_READINGS
        amber_threshold = not duplicate and spo2 < warn and self._amber_in >= ENTER_READINGS
        if crit_flag or red_threshold:
            target = "red"
            reason = "below_threshold" if red_threshold else "owlet_flag"
        elif low_flag or amber_threshold:
            target = "amber"
            reason = "below_threshold" if amber_threshold else "owlet_flag"
        else:
            target, reason = "none", None
        if _RANK[target] > _RANK[self.level]:
            self.level, self.reason = target, reason
            return

        if duplicate:
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

        # A held level may now be supported by a different condition, or by none
        # while hysteresis waits out the exit readings. Only the explanation
        # changes here.
        if self.level in ("amber", "red"):
            red = self.level == "red"
            flag = crit_flag if red else low_flag
            threshold = red_threshold if red else amber_threshold
            still_low = spo2 < (critical if red else warn)
            if flag and not threshold:
                self.reason = "owlet_flag"
            elif threshold or still_low:
                self.reason = "below_threshold"
            else:
                self.reason = "recovering"

    def is_stale(self, now: float) -> bool:
        return self.last_new_reading_wall is None or now - self.last_new_reading_wall > self.stale_after

    def is_signal_lost(self, now: float) -> bool:
        if self.level not in ("amber", "red") or self.last_new_reading_wall is None:
            return False
        silent = now - self.last_new_reading_wall
        return self.stale_after < silent <= self.stale_after + SIGNAL_LOST_SECONDS

    def as_dict(self, now: float) -> dict[str, Any]:
        stale = self.is_stale(now)
        signal_lost = self.is_signal_lost(now)
        age = None if self.last_new_reading_wall is None else max(0, int(now - self.last_new_reading_wall))
        return {
            "account_id": self.account_id,
            "account_name": self.account_name,
            "device_serial": self.device_serial,
            "level": "amber" if signal_lost else "none" if stale else self.level,
            "reason": "signal_lost" if signal_lost else None if stale else self.reason,
            "spo2": None if self.spo2 is None else round(self.spo2),
            "heart_rate": None if self.heart_rate is None else round(self.heart_rate),
            "recorded_at": _iso_dt(self.recorded_at),
            "age_seconds": age,
            "stale": stale,
            "signal_lost": signal_lost,
            "sock_reporting": self.sock_reporting,
            "charging": self.charging,
            "warn_threshold": self.warn_threshold,
            "critical_threshold": self.critical_threshold,
        }


@dataclass
class _UserAlerts:
    snoozed_until: float | None = None
    snoozed_level: str | None = None
    test_level: str | None = None
    test_until: float | None = None


class AlertMonitor:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.trackers: dict[tuple[int, str], DeviceAlertTracker] = {}
        self.users: dict[int, _UserAlerts] = {}

    def _user(self, user_id: int) -> _UserAlerts:
        return self.users.setdefault(user_id, _UserAlerts())

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

    def forget_account(self, account_id: int) -> None:
        """Drop an account's devices once its collector stops, so an alert that
        was active at the time can't linger as a lost signal."""
        for key in [k for k in self.trackers if k[0] == int(account_id)]:
            del self.trackers[key]

    def set_test(self, user_id: int, level: str, seconds: int | None, now: float) -> None:
        user = self._user(user_id)
        if level == "none":
            user.test_level = user.test_until = None
            return
        if level not in ("amber", "red"):
            raise ValueError(f"unknown level {level!r}")
        secs = TEST_DEFAULT_SECONDS if seconds is None else int(seconds)
        secs = max(TEST_MIN_SECONDS, min(TEST_MAX_SECONDS, secs))
        user.test_level = level
        user.test_until = now + secs

    def snooze(self, user_id: int, account_ids: list[int] | None, now: float) -> float | None:
        level = _worst([d["level"] for d in self._devices(user_id, account_ids, now)])
        if level == "none":
            return None
        user = self._user(user_id)
        user.snoozed_until = now + SNOOZE_SECONDS
        user.snoozed_level = level
        return user.snoozed_until

    # ---- outputs ------------------------------------------------------------
    def _test_device(self, user_id: int, now: float) -> dict[str, Any] | None:
        user = self._user(user_id)
        if user.test_level is None or user.test_until is None:
            return None
        if now >= user.test_until:
            user.test_level = user.test_until = None
            return None
        return {
            "account_id": 0,
            "account_name": "Test alert",
            "device_serial": TEST_SERIAL,
            "level": user.test_level,
            "reason": "test",
            "spo2": 84 if user.test_level == "red" else 88,
            "heart_rate": 130,
            "recorded_at": _iso(now),
            "age_seconds": 0,
            "stale": False,
            "signal_lost": False,
            "sock_reporting": True,
            "charging": False,
            "warn_threshold": 90,
            "critical_threshold": 86,
        }

    def _devices(self, user_id: int, account_ids: list[int] | None, now: float) -> list[dict[str, Any]]:
        allowed = None if account_ids is None else {int(a) for a in account_ids}
        devices = [
            tracker.as_dict(now)
            for key, tracker in sorted(self.trackers.items())
            if allowed is None or key[0] in allowed
        ]
        test = self._test_device(user_id, now)
        if test is not None:
            devices.append(test)
        return devices

    def state(
        self, user_id: int, account_ids: list[int] | None, collecting: bool, now: float,
        *, remote_mode: bool = False,
    ) -> dict[str, Any]:
        devices = self._devices(user_id, account_ids, now)
        level = _worst([d["level"] for d in devices])
        user = self._user(user_id)
        if user.snoozed_until is not None:
            expired = now >= user.snoozed_until
            escalated = _RANK[level] > _RANK[user.snoozed_level or "none"]
            if expired or escalated or level == "none":
                user.snoozed_until = user.snoozed_level = None
        snoozed = user.snoozed_until is not None
        return {
            "generated_at": _iso(now),
            "collecting": bool(collecting),
            "remote_mode": bool(remote_mode),
            "level": level,
            "active_level": "none" if snoozed else level,
            "signal_lost": any(d["signal_lost"] for d in devices),
            "snoozed": snoozed,
            "snoozed_until": _iso(user.snoozed_until),
            "snooze_seconds_left": max(0, int(round(user.snoozed_until - now))) if snoozed else 0,
            "snooze_seconds": SNOOZE_SECONDS,
            "test": user.test_level is not None,
            "devices": devices,
        }


ALERTS = AlertMonitor()
