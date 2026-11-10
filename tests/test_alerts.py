from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.alerts import ACK_GRACE_SECONDS, ACK_MAX_SECONDS, ALERTS, SIGNAL_LOST_SECONDS, AlertMonitor, DeviceAlertTracker
from app.models import OwletReading, normalize_reading
from app.poller import Poller
from tests.conftest import client_for, make_user

T0 = 1_800_000_000.0
BASE = datetime(2026, 11, 6, 20, 0, 0, tzinfo=UTC)


def reading(i: int, spo2: float | None, hr: float | None = 130, **kw) -> OwletReading:
    return OwletReading(
        device_serial=kw.pop("serial", "AC1"),
        recorded_at=BASE + timedelta(seconds=10 * i),
        oxygen_saturation=spo2,
        heart_rate=hr,
        **kw,
    )


def feed(tracker: DeviceAlertTracker, values, start: int = 0, warn=90, crit=86):
    for n, v in enumerate(values):
        tracker.observe(reading(start + n, v), warn, crit, T0 + 10 * (start + n))
    return tracker


@pytest.fixture(autouse=True)
def _reset_alerts():
    ALERTS.reset()
    yield
    ALERTS.reset()


# ---- tracker state machine ---------------------------------------------------

def test_one_low_reading_does_not_alert_two_do():
    t = DeviceAlertTracker(1, "AC1")
    feed(t, [97, 88])
    assert t.level == "none"
    feed(t, [88], start=2)
    assert t.level == "amber" and t.reason == "below_threshold"


def test_repeated_recorded_at_does_not_count_twice():
    t = DeviceAlertTracker(1, "AC1")
    r = reading(0, 88)
    t.observe(r, 90, 86, T0)
    t.observe(r, 90, 86, T0 + 5)
    assert t.level == "none"
    # The repeat also doesn't refresh freshness.
    assert t.last_new_reading_wall == T0


@pytest.mark.parametrize(
    ("initial", "flag", "expected"),
    [
        (95, "CRIT_OX_ALRT", "red"),
        (95, "LOW_OX_ALRT", "amber"),
        (88, "CRIT_OX_ALRT", "red"),
    ],
)
def test_duplicate_timestamp_still_escalates_on_owlet_flags(initial, flag, expected):
    t = feed(DeviceAlertTracker(1, "AC1"), [initial, initial])
    streaks = (t._red_in, t._amber_in, t._red_out, t._amber_out)
    t.observe(reading(1, initial, raw={flag: 1}), 90, 86, T0 + 15)
    assert t.level == expected and t.reason == "owlet_flag"
    assert (t._red_in, t._amber_in, t._red_out, t._amber_out) == streaks
    assert t.last_new_reading_wall == T0 + 10


def test_duplicate_low_value_does_not_advance_any_streak():
    t = feed(DeviceAlertTracker(1, "AC1"), [84])
    streaks = (t._red_in, t._amber_in, t._red_out, t._amber_out)
    t.observe(reading(0, 84), 90, 86, T0 + 5)
    assert t.level == "none"
    assert (t._red_in, t._amber_in, t._red_out, t._amber_out) == streaks
    feed(t, [84], start=1)
    assert t.level == "red"


@pytest.mark.parametrize("flag", ["CRIT_OX_ALRT", "LOW_OX_ALRT"])
def test_duplicate_flag_clear_does_not_count_as_recovery(flag):
    t = DeviceAlertTracker(1, "AC1")
    t.observe(reading(0, 95, raw={flag: 1}), 90, 86, T0)
    level = t.level
    feed(t, [95], start=1)
    streaks = (t._red_in, t._amber_in, t._red_out, t._amber_out)
    t.observe(reading(1, 95), 90, 86, T0 + 15)
    assert t.level == level
    assert (t._red_in, t._amber_in, t._red_out, t._amber_out) == streaks
    feed(t, [95], start=2)
    assert t.level == "none"


@pytest.mark.parametrize("offline", [{"sock_off": True}, {"sock_disconnected": True}])
def test_duplicate_offline_reading_clears_alert(offline):
    t = feed(DeviceAlertTracker(1, "AC1"), [80, 80])
    t.observe(reading(1, 80, raw={"CRIT_OX_ALRT": 1}, **offline), 90, 86, T0 + 15)
    assert t.level == "none" and t.reason is None
    assert t.sock_reporting is False
    assert (t._red_in, t._amber_in, t._red_out, t._amber_out) == (0, 0, 0, 0)
    assert t.last_new_reading_wall == T0 + 10


def test_amber_red_and_back_down_with_hysteresis():
    t = DeviceAlertTracker(1, "AC1")
    feed(t, [88, 84])           # two amber-or-worse readings -> amber
    assert t.level == "amber"
    feed(t, [84], start=2)      # second red reading -> red
    assert t.level == "red"
    feed(t, [86, 86], start=3)  # exactly at critical holds red
    assert t.level == "red"
    feed(t, [87, 88], start=5)  # >= critical+1 twice, still < warn -> amber
    assert t.level == "amber"
    feed(t, [90, 90], start=7)  # exactly at warn holds amber
    assert t.level == "amber"
    feed(t, [91], start=9)
    assert t.level == "amber"
    feed(t, [95], start=10)
    assert t.level == "none" and t.reason is None


def test_red_drops_straight_to_none_on_full_recovery():
    t = feed(DeviceAlertTracker(1, "AC1"), [80, 80, 97, 97])
    assert t.level == "none"


def test_reason_says_recovering_while_exit_readings_are_pending():
    t = feed(DeviceAlertTracker(1, "AC1"), [88, 88, 95])
    assert t.level == "amber" and t.reason == "recovering"
    # A single low reading during the exit window is still "low", not "recovering".
    feed(t, [88], start=3)
    assert t.level == "amber" and t.reason == "below_threshold"
    feed(t, [95, 95], start=4)
    assert t.level == "none" and t.reason is None


def test_owlet_crit_flag_goes_red_immediately():
    t = DeviceAlertTracker(1, "AC1")
    t.observe(reading(0, 95, raw={"CRIT_OX_ALRT": 1}), 90, 86, T0)
    assert t.level == "red" and t.reason == "owlet_flag"


def test_owlet_low_flag_goes_amber_immediately():
    t = DeviceAlertTracker(1, "AC1")
    t.observe(reading(0, 95, raw={"low_oxygen_alert": True}), 90, 86, T0)
    assert t.level == "amber" and t.reason == "owlet_flag"


@pytest.mark.parametrize("flag", ["LOW_OX_ALRT", "CRIT_OX_ALRT"])
def test_owlet_flag_does_not_override_qualifying_red_threshold(flag):
    t = feed(DeviceAlertTracker(1, "AC1"), [84])
    t.observe(reading(1, 84, raw={flag: 1}), 90, 86, T0 + 10)
    assert t.level == "red" and t.reason == "below_threshold"


def test_owlet_low_flag_does_not_override_qualifying_amber_threshold():
    t = feed(DeviceAlertTracker(1, "AC1"), [88])
    t.observe(reading(1, 88, raw={"LOW_OX_ALRT": 1}), 90, 86, T0 + 10)
    assert t.level == "amber" and t.reason == "below_threshold"


@pytest.mark.parametrize("flag,spo2,level", [
    ("CRIT_OX_ALRT", 84, "red"), ("LOW_OX_ALRT", 88, "amber"),
])
def test_reason_changes_from_flag_to_threshold_when_flag_clears(flag, spo2, level):
    t = DeviceAlertTracker(1, "AC1")
    t.observe(reading(0, spo2, raw={flag: 1}), 90, 86, T0)
    assert t.level == level and t.reason == "owlet_flag"
    feed(t, [spo2, spo2], start=1)
    assert t.level == level and t.reason == "below_threshold"


@pytest.mark.parametrize("flag,spo2,level", [
    ("CRIT_OX_ALRT", 84, "red"), ("LOW_OX_ALRT", 88, "amber"),
])
def test_reason_changes_to_flag_when_threshold_no_longer_qualifies(flag, spo2, level):
    t = feed(DeviceAlertTracker(1, "AC1"), [spo2, spo2])
    for i in (2, 3):
        t.observe(reading(i, 95, raw={flag: 1}), 90, 86, T0 + 10 * i)
        assert t.level == level and t.reason == "owlet_flag"
    # One low sample after those flag-only samples is insufficient threshold evidence.
    t.observe(reading(4, spo2, raw={flag: 1}), 90, 86, T0 + 40)
    assert t.reason == "owlet_flag"
    t.observe(reading(5, spo2, raw={flag: 1}), 90, 86, T0 + 50)
    assert t.reason == "below_threshold"


def test_missing_timestamp_deduplicates_vitals_and_logs_once_per_device(caplog):
    t = DeviceAlertTracker(1, "AC1")
    raw = {"oxygen_saturation": 84, "heart_rate": 130}
    for i in range(3):
        r = normalize_reading(raw, "AC1").model_copy(update={"recorded_at": BASE + timedelta(seconds=i)})
        assert r.timestamp_synthesized
        t.observe(r, 90, 86, T0 + i)
    assert t.level == "none" and t._red_in == 1
    assert t.last_new_reading_wall == T0
    assert t.is_stale(T0 + 61)
    assert len([r for r in caplog.records if "Missing Owlet timestamp" in r.message]) == 1
    t.observe(normalize_reading(raw | {"heart_rate": 131}, "AC1"), 90, 86, T0 + 62)
    assert t.level == "red" and t.last_new_reading_wall == T0 + 62


def test_missing_timestamp_still_processes_changed_alarm_and_offline_flags():
    t = DeviceAlertTracker(1, "AC1")
    raw = {"oxygen_saturation": 95, "heart_rate": 130}
    t.observe(normalize_reading(raw, "AC1"), 90, 86, T0)
    t.observe(normalize_reading(raw | {"CRIT_OX_ALRT": 1}, "AC1"), 90, 86, T0 + 1)
    assert t.level == "red" and t.reason == "owlet_flag"
    t.observe(normalize_reading(raw | {"sock_off": True}, "AC1"), 90, 86, T0 + 2)
    assert t.level == "none" and not t.sock_reporting


@pytest.mark.parametrize("timestamp_key", ["last_updated", "lastUpdated", "timestamp", "data_updated_at"])
def test_real_timestamps_count_identical_vitals_as_distinct_samples(timestamp_key):
    t = DeviceAlertTracker(1, "AC1")
    for i in range(2):
        r = normalize_reading({timestamp_key: BASE + timedelta(seconds=i),
                               "oxygen_saturation": 84, "heart_rate": 130}, "AC1")
        assert not r.timestamp_synthesized
        t.observe(r, 90, 86, T0 + i)
    assert t.level == "red" and t.last_new_reading_wall == T0 + 1


def test_offline_reading_clears_and_never_triggers():
    t = feed(DeviceAlertTracker(1, "AC1"), [80, 80])
    assert t.level == "red"
    t.observe(reading(2, 0, hr=0), 90, 86, T0 + 20)
    assert t.level == "none" and t.sock_reporting is False
    # Zeros and sock-off never count toward a level.
    feed(t, [0, 0, 0], start=3)
    t.observe(reading(6, 80, sock_off=True), 90, 86, T0 + 60)
    t.observe(reading(7, 80, sock_off=True), 90, 86, T0 + 70)
    assert t.level == "none"
    # Counters were reset: one valid low reading after offline isn't enough.
    feed(t, [80], start=8)
    assert t.level == "none" and t.sock_reporting is True


def test_charging_reported():
    t = DeviceAlertTracker(1, "AC1")
    t.observe(reading(0, 0, hr=0, charging=True), 90, 86, T0)
    d = t.as_dict(T0)
    assert d["charging"] is True and d["sock_reporting"] is False


@pytest.mark.parametrize("raw", [{}, {"CRIT_OX_ALRT": 1}])
def test_charging_with_nonzero_vitals_never_alerts(raw):
    t = DeviceAlertTracker(1, "AC1")
    for i in range(2):
        t.observe(reading(i, 80, charging=True, raw=raw), 90, 86, T0 + 10 * i)
        d = t.as_dict(T0 + 10 * i)
        assert d["level"] == "none" and d["reason"] is None
        assert d["sock_reporting"] is False and d["charging"] is True
    feed(t, [80], start=2)
    assert t.level == "none"


@pytest.mark.parametrize("sample", [1, 2])
def test_first_charging_reading_clears_red_even_with_duplicate_timestamp(sample):
    t = feed(DeviceAlertTracker(1, "AC1"), [80, 80])
    t.observe(reading(sample, 80, charging=True), 90, 86, T0 + 20)
    assert t.level == "none" and t.reason is None
    assert t.sock_reporting is False and t.charging is True
    assert (t._red_in, t._amber_in, t._red_out, t._amber_out) == (0, 0, 0, 0)
    assert t.last_new_reading_wall == T0 + (10 if sample == 1 else 20)


@pytest.mark.parametrize("spo2,level", [(80, "red"), (88, "amber")])
def test_stale_alert_becomes_amber_signal_lost(spo2, level):
    m = AlertMonitor()
    for i, v in enumerate([spo2, spo2]):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + 10 * i)
    assert m.state(1, [1], True, T0 + 10)["level"] == level
    s = m.state(1, [1], True, T0 + 10 + 61)
    assert s["level"] == s["active_level"] == "amber"
    assert s["signal_lost"] is True
    d = s["devices"][0]
    assert d["stale"] is True and d["level"] == "amber"
    assert d["signal_lost"] is True and d["reason"] == "signal_lost"


def test_stale_normal_device_does_not_alert():
    m = AlertMonitor()
    m.observe(1, "Baby", reading(0, 97), 90, 86, T0)
    s = m.state(1, [1], True, T0 + 61)
    assert s["level"] == s["active_level"] == "none"
    assert s["signal_lost"] is False
    assert s["devices"][0]["stale"] is True
    assert s["devices"][0]["signal_lost"] is False


def test_signal_lost_obeys_device_stale_after_and_requires_a_new_sample_to_clear():
    m = AlertMonitor()
    m.observe(1, "Baby", reading(0, 80, raw={"CRIT_OX_ALRT": 1}), 90, 86, T0, stale_after=90)
    assert m.state(1, [1], True, T0 + 90)["active_level"] == "red"
    assert m.state(1, [1], True, T0 + 91)["signal_lost"] is True
    m.observe(1, "Baby", reading(0, 80), 90, 86, T0 + 92, stale_after=90)
    assert m.state(1, [1], True, T0 + 92)["signal_lost"] is True
    m.observe(1, "Baby", reading(1, 97), 90, 86, T0 + 93, stale_after=90)
    assert m.state(1, [1], True, T0 + 93)["signal_lost"] is False
    m.observe(1, "Baby", reading(2, 97), 90, 86, T0 + 94, stale_after=90)
    assert m.state(1, [1], True, T0 + 94)["active_level"] == "none"


def test_signal_lost_expires_after_ten_minutes():
    m = _red_monitor()  # red, last new sample at T0 + 10, stale_after 60
    lost_until = T0 + 10 + 60 + SIGNAL_LOST_SECONDS
    s = m.state(1, [1], True, lost_until)
    assert s["active_level"] == "amber" and s["signal_lost"] is True
    s = m.state(1, [1], True, lost_until + 1)
    assert s["level"] == s["active_level"] == "none"
    assert s["signal_lost"] is False
    d = s["devices"][0]
    assert d["stale"] is True and d["signal_lost"] is False and d["reason"] is None


def test_new_sample_after_expiry_resumes_the_held_alert():
    m = _red_monitor()
    later = T0 + 10 + 60 + SIGNAL_LOST_SECONDS + 30
    assert m.state(1, [1], True, later)["active_level"] == "none"
    m.observe(1, "Baby", reading(50, 80), 90, 86, later)
    assert m.state(1, [1], True, later)["active_level"] == "red"


def test_forget_account_drops_only_that_accounts_devices():
    m = AlertMonitor()
    for i in range(2):
        m.observe(1, "A", reading(i, 80, serial="A1"), 90, 86, T0 + 10 * i)
        m.observe(2, "B", reading(i, 80, serial="B1"), 90, 86, T0 + 10 * i)
    m.forget_account(1)
    assert set(m.trackers) == {(2, "B1")}
    assert m.state(1, None, True, T0 + 100)["devices"][0]["account_id"] == 2


def test_stopping_a_poller_clears_its_alert_and_ignores_late_readings():
    poller = Poller(_FakeStore({}), read_once=None, interval_seconds=10, account_id=7)  # type: ignore[arg-type]
    for i in range(2):
        asyncio.run(poller._feed_alerts(reading(i, 80)))
    assert ALERTS.state(1, [7], True, time.time())["active_level"] == "red"
    asyncio.run(poller.stop())
    assert (7, "AC1") not in ALERTS.trackers
    # A poll that was still in flight when stop() ran must not re-add the device.
    asyncio.run(poller._feed_alerts(reading(2, 80)))
    assert ALERTS.trackers == {}


def test_signal_lost_is_snoozed_for_15_seconds():
    m = _red_monitor()
    now = T0 + 71
    assert m.snooze(1, [1], now) == now + 15
    s = m.state(1, [1], True, now + 14)
    assert s["signal_lost"] is True and s["active_level"] == "none"
    assert s["snoozed"] is True
    s = m.state(1, [1], True, now + 15)
    assert s["signal_lost"] is True and s["active_level"] == "amber"


def test_signal_lost_does_not_mask_another_devices_red_alert():
    m = _red_monitor()
    m.observe(2, "Other baby", reading(0, 80, serial="AC2", raw={"CRIT_OX_ALRT": 1}), 90, 86, T0 + 71)
    s = m.state(1, [1, 2], True, T0 + 71)
    assert s["active_level"] == "red" and s["signal_lost"] is True
    s = m.state(2, [2], True, T0 + 71)
    assert s["active_level"] == "red" and s["signal_lost"] is False


# ---- monitor: snooze and test alerts ----------------------------------------

def _red_monitor() -> AlertMonitor:
    m = AlertMonitor()
    for i, v in enumerate([80, 80]):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + 10 * i)
    return m


def test_snooze_silences_for_15s_then_returns():
    m = _red_monitor()
    now = T0 + 10
    until = m.snooze(1, [1], now)
    assert until == now + 15
    s = m.state(1, [1], True, now + 1)
    assert s["level"] == "red" and s["active_level"] == "none" and s["snoozed"] is True
    assert s["snooze_seconds_left"] == 14
    # keep it fresh
    m.observe(1, "Baby", reading(2, 80), 90, 86, now + 10)
    s = m.state(1, [1], True, now + 15)
    assert s["active_level"] == "red" and s["snoozed"] is False and s["snoozed_until"] is None


def test_snooze_is_noop_when_nothing_alerting():
    m = AlertMonitor()
    m.observe(1, "Baby", reading(0, 97), 90, 86, T0)
    assert m.snooze(1, [1], T0) is None
    assert m.state(1, [1], True, T0)["snoozed"] is False


def test_snooze_cancelled_when_amber_escalates_to_red():
    m = AlertMonitor()
    for i, v in enumerate([88, 88]):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + 10 * i)
    m.snooze(1, [1], T0 + 10)
    assert m.state(1, [1], True, T0 + 11)["active_level"] == "none"
    for i, v in enumerate([84, 84], start=2):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + 10 * i)
    s = m.state(1, [1], True, T0 + 31)
    assert s["active_level"] == "red" and s["snoozed"] is False


# ---- monitor: acknowledge ("I'm checking") -----------------------------------

def test_acknowledge_silences_but_reports_real_level():
    m = _red_monitor()
    now = T0 + 10
    assert m.acknowledge(1, [1], now) == now + ACK_MAX_SECONDS
    s = m.state(1, [1], True, now + 1)
    assert s["level"] == "red" and s["active_level"] == "none"
    assert s["acknowledged"] is True and s["acknowledged_until"].endswith("Z")
    assert s["devices"][0]["level"] == "red" and s["devices"][0]["spo2"] == 80
    assert s["ack_seconds_left"] == ACK_MAX_SECONDS - 1
    assert m.state(1, [1], True, now + 100)["ack_seconds_left"] == ACK_MAX_SECONDS - 100


def test_acknowledge_is_noop_when_nothing_alerting():
    m = AlertMonitor()
    m.observe(1, "Baby", reading(0, 97), 90, 86, T0)
    assert m.acknowledge(1, [1], T0) is None
    s = m.state(1, [1], True, T0)
    assert s["acknowledged"] is False and s["acknowledged_until"] is None and s["ack_seconds_left"] == 0


def test_acknowledge_grace_after_recovery_then_ends():
    m = AlertMonitor()
    m.set_test(1, "amber", 120, T0)
    m.acknowledge(1, None, T0)
    m.set_test(1, "none", None, T0 + 60)
    s = m.state(1, None, True, T0 + 60)  # recovery seen here
    assert s["acknowledged"] is True and s["ack_seconds_left"] == ACK_GRACE_SECONDS
    # A new amber inside the grace stays silent; the grace restarts on the next recovery.
    m.set_test(1, "amber", 120, T0 + 100)
    s = m.state(1, None, True, T0 + 100)
    assert s["active_level"] == "none" and s["acknowledged"] is True
    assert s["ack_seconds_left"] == ACK_MAX_SECONDS - 100
    m.set_test(1, "none", None, T0 + 150)
    assert m.state(1, None, True, T0 + 150)["ack_seconds_left"] == ACK_GRACE_SECONDS
    assert m.state(1, None, True, T0 + 150 + ACK_GRACE_SECONDS - 1)["acknowledged"] is True
    s = m.state(1, None, True, T0 + 150 + ACK_GRACE_SECONDS)
    assert s["acknowledged"] is False and s["acknowledged_until"] is None
    # A new alert after the acknowledge ended fires normally.
    m.set_test(1, "amber", 120, T0 + 400)
    s = m.state(1, None, True, T0 + 400)
    assert s["active_level"] == "amber" and s["acknowledged"] is False


def test_acknowledge_cap_ends_it_and_alert_returns():
    m = AlertMonitor()
    m.set_test(1, "red", 120, T0)
    m.acknowledge(1, None, T0)
    for t in range(60, ACK_MAX_SECONDS, 60):
        m.set_test(1, "red", 120, T0 + t)  # never recovers
        assert m.state(1, None, True, T0 + t)["active_level"] == "none"
    m.set_test(1, "red", 120, T0 + ACK_MAX_SECONDS)
    s = m.state(1, None, True, T0 + ACK_MAX_SECONDS)
    assert s["acknowledged"] is False and s["active_level"] == "red"


def test_acknowledge_late_recovery_grace_never_passes_cap():
    m = AlertMonitor()
    m.set_test(1, "amber", 120, T0 + 500)
    m.acknowledge(1, None, T0 + 500)
    for t in range(560, 1050, 60):
        m.set_test(1, "amber", 120, T0 + t)
        assert m.state(1, None, True, T0 + t)["acknowledged"] is True
    m.set_test(1, "none", None, T0 + 1050)
    s = m.state(1, None, True, T0 + 1050)
    assert s["acknowledged"] is True and s["ack_seconds_left"] == 50
    assert m.state(1, None, True, T0 + 1100)["acknowledged"] is False


def test_acknowledge_escalation_breaks_through():
    m = AlertMonitor()
    m.set_test(1, "amber", 120, T0)
    m.acknowledge(1, None, T0)
    m.set_test(1, "red", 120, T0 + 30)
    s = m.state(1, None, True, T0 + 30)
    assert s["active_level"] == "red" and s["acknowledged"] is False


def test_acknowledge_escalation_breaks_through_during_grace():
    m = AlertMonitor()
    m.set_test(1, "amber", 120, T0)
    m.acknowledge(1, None, T0)
    m.set_test(1, "none", None, T0 + 30)
    assert m.state(1, None, True, T0 + 30)["acknowledged"] is True
    m.set_test(1, "red", 120, T0 + 60)
    s = m.state(1, None, True, T0 + 60)
    assert s["active_level"] == "red" and s["acknowledged"] is False


def test_acknowledge_escalation_on_real_device():
    m = AlertMonitor()
    for i, v in enumerate([88, 88]):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + 10 * i)
    m.acknowledge(1, [1], T0 + 10)
    assert m.state(1, [1], True, T0 + 11)["active_level"] == "none"
    for i, v in enumerate([84, 84], start=2):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + 10 * i)
    s = m.state(1, [1], True, T0 + 31)
    assert s["active_level"] == "red" and s["acknowledged"] is False


def test_acknowledge_clears_snooze_and_snooze_does_not_shorten_it():
    m = AlertMonitor()
    m.set_test(1, "red", 120, T0)
    m.snooze(1, None, T0)
    m.acknowledge(1, None, T0 + 1)
    s = m.state(1, None, True, T0 + 2)
    assert s["snoozed"] is False and s["acknowledged"] is True
    assert m.snooze(1, None, T0 + 5) == T0 + 20
    m.set_test(1, "red", 120, T0 + 100)
    s = m.state(1, None, True, T0 + 100)
    assert s["acknowledged"] is True and s["active_level"] == "none"
    assert s["ack_seconds_left"] == ACK_MAX_SECONDS + 1 - 100


def test_snooze_under_acknowledge_never_outlasts_it():
    m = AlertMonitor()
    m.set_test(1, "red", 120, T0)
    m.acknowledge(1, None, T0)
    m.set_test(1, "red", 120, T0 + 590)
    assert m.snooze(1, None, T0 + 590) == T0 + ACK_MAX_SECONDS
    m.set_test(1, "red", 120, T0 + ACK_MAX_SECONDS)
    s = m.state(1, None, True, T0 + ACK_MAX_SECONDS)
    assert s["active_level"] == "red" and s["snoozed"] is False and s["acknowledged"] is False


def test_acknowledge_is_per_user():
    m = AlertMonitor()
    m.set_test(1, "red", 120, T0)
    m.set_test(2, "red", 120, T0)
    m.acknowledge(1, None, T0)
    assert m.state(2, None, True, T0 + 1)["active_level"] == "red"
    assert m.state(1, None, True, T0 + 1)["active_level"] == "none"


def test_test_alert_sets_level_expires_and_cancels():
    m = AlertMonitor()
    m.set_test(1, "red", 10, T0)
    s = m.state(1, [], False, T0 + 1)
    assert s["level"] == "red" and s["test"] is True
    assert s["devices"][-1]["device_serial"] == "TEST" and s["devices"][-1]["reason"] == "test"
    assert m.state(1, [], False, T0 + 10)["level"] == "none"
    m.set_test(1, "amber", 999, T0)                 # clamped to 120
    assert m.state(1, [], False, T0 + 119)["level"] == "amber"
    assert m.state(1, [], False, T0 + 120)["test"] is False
    m.set_test(1, "amber", 1, T0)                   # clamped to 5
    assert m.state(1, [], False, T0 + 4)["level"] == "amber"
    m.set_test(1, "none", None, T0)
    assert m.state(1, [], False, T0 + 1)["level"] == "none"


def test_devices_scoped_to_accounts():
    m = AlertMonitor()
    m.observe(1, "A", reading(0, 97), 90, 86, T0)
    m.observe(2, "B", reading(0, 97, serial="AC2"), 90, 86, T0)
    assert [d["account_id"] for d in m.state(1, [2], True, T0)["devices"]] == [2]


# ---- poller integration ------------------------------------------------------

class _FakeStore:
    def __init__(self, prefs):
        self.prefs = prefs

    async def get_account(self, account_id, user_id=None):
        return {"id": account_id, "display_name": "Baby", "dashboard_preferences": self.prefs}


def test_thresholds_come_from_preferences_and_critical_is_clamped():
    poller = Poller(_FakeStore({"o2_warn_threshold": 94, "o2_critical_threshold": 97}),
                    read_once=None, interval_seconds=5, account_id=7)  # type: ignore[arg-type]
    asyncio.run(poller._feed_alerts(reading(0, 95)))
    d = ALERTS.state(1, [7], True, time.time())
    tracker = ALERTS.trackers[(7, "AC1")]
    assert tracker.warn_threshold == 94 and tracker.critical_threshold == 93
    assert d["devices"][0]["account_name"] == "Baby"


def test_collection_survives_alert_engine_failure(monkeypatch):
    stored = []

    class Store(_FakeStore):
        async def insert_reading(self, r, account_id=None):
            stored.append(r)

        async def insert_custom_notification(self, **kw):
            pass

        async def update_account_tokens(self, *a, **kw):
            pass

    def boom(*a, **kw):
        raise RuntimeError("alert bug")

    monkeypatch.setattr(ALERTS, "observe", boom)

    async def run():
        poller = None

        async def read_once():
            poller._stop.set()
            return reading(0, 80)

        poller = Poller(Store({}), read_once=read_once, interval_seconds=0, account_id=1)
        poller._maybe_send_readiness = lambda: asyncio.sleep(0)  # type: ignore[method-assign]
        await poller._run()

    asyncio.run(run())
    assert len(stored) == 1


# ---- API ---------------------------------------------------------------------

DEVICE_KEYS = {
    "account_id", "account_name", "device_serial", "level", "reason", "spo2", "heart_rate",
    "recorded_at", "age_seconds", "stale", "sock_reporting", "charging", "warn_threshold",
    "critical_threshold", "signal_lost",
}
STATE_KEYS = {
    "generated_at", "collecting", "level", "active_level", "snoozed", "snoozed_until",
    "snooze_seconds_left", "snooze_seconds", "test", "devices", "signal_lost", "remote_mode",
    "acknowledged", "acknowledged_until", "ack_seconds_left",
}


def test_api_alert_routes(app_bundle):
    app, store, auth_store = app_bundle

    async def setup():
        await store.init()
        await auth_store.init()
        user, token = await make_user(auth_store, "parent@example.com")
        account = await store.create_account(email="o@example.com", user_id=user["id"])
        return token, int(account["id"])

    token, account_id = asyncio.run(setup())
    with client_for(app, token) as client:
        assert client.get("/api/alert-state").status_code == 200
        s = client.get("/api/alert-state").json()
        assert set(s) == STATE_KEYS
        assert s["devices"] == [] and s["level"] == "none" and s["collecting"] is False

        now = time.time()
        for i, v in enumerate([80, 80]):
            ALERTS.observe(account_id, "Baby", reading(i, v), 90, 86, now)
        s = client.get("/api/alert-state").json()
        assert s["level"] == "red" and s["active_level"] == "red"
        assert set(s["devices"][0]) == DEVICE_KEYS
        assert s["devices"][0]["spo2"] == 80 and s["devices"][0]["recorded_at"].endswith("Z")

        snooze = client.post("/api/alert/snooze").json()
        assert snooze["snooze_seconds"] == 15 and snooze["snoozed_until"].endswith("Z")
        s = client.get("/api/alert-state").json()
        assert s["snoozed"] is True and s["active_level"] == "none"

        ack = client.post("/api/alert/acknowledge").json()
        assert set(ack) == {"acknowledged_until"} and ack["acknowledged_until"].endswith("Z")
        s = client.get("/api/alert-state").json()
        assert s["acknowledged"] is True and s["snoozed"] is False and s["active_level"] == "none"
        assert s["level"] == "red" and s["acknowledged_until"] == ack["acknowledged_until"]
        assert 595 <= s["ack_seconds_left"] <= 600

        ALERTS.reset()
        assert client.post("/api/alert/snooze").json()["snoozed_until"] is None
        assert client.post("/api/alert/acknowledge").json() == {"acknowledged_until": None}

        r = client.post("/api/alert/test", json={"level": "amber", "seconds": 30})
        assert r.status_code == 200 and set(r.json()) == STATE_KEYS
        assert r.json()["level"] == "amber" and r.json()["test"] is True
        assert client.post("/api/alert/test", json={"level": "none"}).json()["test"] is False
        assert client.post("/api/alert/test", json={"level": "purple"}).status_code == 422

        w = client.get("/widget")
        assert w.status_code == 200 and "text/html" in w.headers["content-type"]
        assert "/api/alert-state" in w.text


def test_api_alert_state_requires_login(app_bundle):
    app, _, _ = app_bundle
    with client_for(app) as client:
        assert client.get("/api/alert-state").status_code == 401


@pytest.fixture
def two_alert_users(app_bundle):
    app, store, auth_store = app_bundle

    async def setup():
        await store.init()
        await auth_store.init()
        users = []
        for name in ("alice", "bob"):
            user, token = await make_user(auth_store, f"{name}@example.com")
            account = await store.create_account(email=f"{name}-sock@example.com", user_id=user["id"])
            for i in range(2):
                ALERTS.observe(account["id"], name, reading(i, 80), 90, 86, time.time())
            users.append((token, account["id"]))
        return users

    return app, asyncio.run(setup())


def test_api_snooze_is_user_scoped_and_other_users_polls_cannot_cancel_it(two_alert_users):
    app, ((alice_token, _), (bob_token, bob_account)) = two_alert_users
    with client_for(app, alice_token) as alice, client_for(app, bob_token) as bob:
        assert alice.post("/api/alert/snooze").status_code == 200
        assert bob.get("/api/alert-state").json()["active_level"] == "red"
        # A poll with no alert in Bob's scope must not clear Alice's snooze.
        for i in (2, 3):
            ALERTS.observe(bob_account, "bob", reading(i, 97), 90, 86, time.time())
        assert bob.get("/api/alert-state").json()["level"] == "none"
        s = alice.get("/api/alert-state").json()
        assert s["snoozed"] is True and s["active_level"] == "none"


def test_api_acknowledge_is_user_scoped(two_alert_users):
    app, ((alice_token, _), (bob_token, bob_account)) = two_alert_users
    with client_for(app, alice_token) as alice, client_for(app, bob_token) as bob:
        assert alice.post("/api/alert/acknowledge").json()["acknowledged_until"] is not None
        b = bob.get("/api/alert-state").json()
        assert b["active_level"] == "red" and b["acknowledged"] is False
        for i in (2, 3):
            ALERTS.observe(bob_account, "bob", reading(i, 97), 90, 86, time.time())
        assert bob.get("/api/alert-state").json()["level"] == "none"
        s = alice.get("/api/alert-state").json()
        assert s["acknowledged"] is True and s["active_level"] == "none" and s["level"] == "red"


def test_api_test_alerts_are_user_scoped(two_alert_users):
    app, ((alice_token, _), (bob_token, _)) = two_alert_users
    with client_for(app, alice_token) as alice, client_for(app, bob_token) as bob:
        assert alice.post("/api/alert/test", json={"level": "amber"}).status_code == 200
        a = alice.get("/api/alert-state").json()
        b = bob.get("/api/alert-state").json()
        assert a["test"] is True and any(d["device_serial"] == "TEST" for d in a["devices"])
        assert b["test"] is False and all(d["device_serial"] != "TEST" for d in b["devices"])
        assert bob.post("/api/alert/test", json={"level": "none"}).status_code == 200
        assert alice.get("/api/alert-state").json()["test"] is True


@pytest.fixture
def desktop_app(app_bundle, tmp_path):
    app, _, _ = app_bundle
    app.state.settings.desktop_mode = True
    app.state.settings.database_path = str(tmp_path / "owlet.sqlite3")
    return app


@pytest.mark.parametrize("desktop,backend,expected", [
    (True, None, False), (True, "https://dashboard.example", True),
    (False, "https://dashboard.example", False),
])
def test_alert_routes_report_remote_mode_from_desktop_configuration(desktop_app, tmp_path, desktop, backend, expected):
    desktop_app.state.settings.desktop_mode = desktop
    (tmp_path / "desktop-config.json").write_text(json.dumps({"backend": backend}))
    if desktop:
        token = None
    else:
        async def setup():
            await desktop_app.state.auth_store.init()
            _, token = await make_user(desktop_app.state.auth_store, "parent@example.com")
            return token
        token = asyncio.run(setup())
    with client_for(desktop_app, token) as client:
        s = client.get("/api/alert-state").json()
        assert s["collecting"] is False and s["remote_mode"] is expected
        s = client.post("/api/alert/test", json={"level": "red"}).json()
        assert s["remote_mode"] is expected


@pytest.mark.parametrize("origin,expected", [
    (None, 200),
    ("http://127.0.0.1:8877", 200),
    ("http://localhost:8877", 200),
    ("https://evil.example", 403),
    ("http://127.0.0.1:8877.evil.example", 403),
    ("http://127.0.0.1:8878", 403),
    ("null", 403),
    ("", 403),
])
def test_desktop_origin_guard_cannot_acknowledge_from_another_site(desktop_app, origin, expected):
    with client_for(desktop_app) as client:
        assert client.post("/api/alert/test", json={"level": "red"}).status_code == 200
        headers = {} if origin is None else {"Origin": origin}
        assert client.post("/api/alert/acknowledge", headers=headers).status_code == expected
        s = client.get("/api/alert-state").json()
        assert s["acknowledged"] is (expected == 200)
        assert s["active_level"] == ("none" if expected == 200 else "red")


@pytest.mark.parametrize("origin,expected", [
    (None, 200),
    ("http://127.0.0.1:8877", 200),
    ("http://localhost:8877", 200),
    ("https://evil.example", 403),
    ("http://127.0.0.1:8877.evil.example", 403),
    ("http://127.0.0.1:8878", 403),
    ("null", 403),
    ("", 403),
])
def test_desktop_origin_guard_cannot_snooze_from_another_site(desktop_app, origin, expected):
    with client_for(desktop_app) as client:
        assert client.post("/api/alert/test", json={"level": "red"}).status_code == 200
        headers = {} if origin is None else {"Origin": origin}
        assert client.post("/api/alert/snooze", headers=headers).status_code == expected
        s = client.get("/api/alert-state").json()
        assert s["snoozed"] is (expected == 200)
        assert s["active_level"] == ("none" if expected == 200 else "red")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_desktop_origin_guard_covers_all_mutations(desktop_app, method):
    with client_for(desktop_app) as client:
        assert client.request(method, "/desktop/reset", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/alert-state", headers={"Origin": "https://evil.example"}).status_code == 200


def test_desktop_origin_guard_rejects_multiple_origins(desktop_app):
    with client_for(desktop_app) as client:
        response = client.post("/api/alert/snooze", headers=[
            ("Origin", "http://127.0.0.1:8877"), ("Origin", "https://evil.example"),
        ])
        assert response.status_code == 403


def test_hosted_origin_behavior_is_unchanged(two_alert_users):
    app, ((token, _), _) = two_alert_users
    with client_for(app, token) as client:
        assert client.post("/api/alert/snooze", headers={"Origin": "https://hosted.example"}).status_code == 200
        assert client.get("/api/alert-state").json()["snoozed"] is True


# ---- per-account snooze / stale settings -------------------------------------

from app.alerts import resolve_alert_timing  # noqa: E402


@pytest.mark.parametrize("prefs, expected", [
    (None, (15, 60)),
    ({}, (15, 60)),
    ("garbage", (15, 60)),
    ({"alert_snooze_seconds": None, "alert_stale_seconds": None}, (15, 60)),
    ({"alert_snooze_seconds": 30, "alert_stale_seconds": 120}, (30, 120)),
    ({"alert_snooze_seconds": 0, "alert_stale_seconds": 0}, (5, 30)),
    ({"alert_snooze_seconds": -10, "alert_stale_seconds": 1}, (5, 30)),
    ({"alert_snooze_seconds": 3600, "alert_stale_seconds": 99999}, (120, 300)),
    ({"alert_snooze_seconds": "3600", "alert_stale_seconds": "5"}, (15, 60)),
    ({"alert_snooze_seconds": True, "alert_stale_seconds": False}, (15, 60)),
    ({"alert_snooze_seconds": [30], "alert_stale_seconds": {"x": 1}}, (15, 60)),
    ({"alert_snooze_seconds": float("inf"), "alert_stale_seconds": float("nan")}, (15, 60)),
    ({"alert_snooze_seconds": 29.9, "alert_stale_seconds": 45.5}, (29, 45)),
])
def test_resolve_alert_timing_defaults_and_clamps_stored_values(prefs, expected):
    assert resolve_alert_timing(prefs) == expected


def test_snooze_with_configured_length_lasts_exactly_that_long():
    m = _red_monitor()
    now = T0 + 11
    assert m.snooze(1, [1], now, snooze_seconds=30) == now + 30
    s = m.state(1, [1], True, now + 29, snooze_seconds=30)
    assert s["snoozed"] is True and s["snooze_seconds"] == 30
    s = m.state(1, [1], True, now + 30, snooze_seconds=30)
    assert s["snoozed"] is False and s["active_level"] == "red"


def test_engine_clamps_snooze_argument():
    m = _red_monitor()
    now = T0 + 11
    assert m.snooze(1, [1], now, snooze_seconds=0) == now + 5
    assert m.snooze(1, [1], now, snooze_seconds=10_000) == now + 120


def test_configured_snooze_still_cancelled_by_escalation():
    m = AlertMonitor()
    for i, v in enumerate([88, 88]):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + i)
    m.snooze(1, [1], T0 + 2, snooze_seconds=120)
    for i, v in enumerate([80, 80], start=3):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + i)
    s = m.state(1, [1], True, T0 + 5, snooze_seconds=120)
    assert s["active_level"] == "red" and s["snoozed"] is False


@pytest.mark.parametrize("interval, configured, not_stale_at, stale_at", [
    (5, 120, 90, 121),      # configured value replaces the 60 s default
    (5, None, 59, 61),      # default 60
    (60, 30, 130, 136),     # floor 2*60+15 = 135 beats configured 30
    (5, 9999, 299, 301),    # stored garbage clamped to 300
])
def test_per_account_stale_timeout(interval, configured, not_stale_at, stale_at):
    poller = Poller(_FakeStore({"alert_stale_seconds": configured}),
                    read_once=None, interval_seconds=interval, account_id=7)  # type: ignore[arg-type]
    asyncio.run(poller._feed_alerts(reading(0, 97)))
    tracker = ALERTS.trackers[(7, "AC1")]
    start = tracker.last_new_reading_wall
    assert not tracker.is_stale(start + not_stale_at)
    assert tracker.is_stale(start + stale_at)


def _user_with_accounts(app_bundle, prefs_list):
    app, store, auth_store = app_bundle

    async def setup():
        await store.init()
        await auth_store.init()
        user, token = await make_user(auth_store, "parent@example.com")
        ids = []
        for i, prefs in enumerate(prefs_list):
            account = await store.create_account(email=f"o{i}@example.com", user_id=user["id"])
            if prefs:
                await store.update_account_preferences(int(account["id"]), dashboard_preferences=prefs)
            ids.append(int(account["id"]))
        return token, ids

    token, ids = asyncio.run(setup())
    return app, token, ids


def _make_red(account_id):
    now = time.time()
    for i in range(2):
        ALERTS.observe(account_id, "Baby", reading(i, 80), 90, 86, now)


def _epoch(iso: str) -> float:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


@pytest.mark.parametrize("prefs_list, expected", [
    ([{"alert_snooze_seconds": 30}], 30),
    ([{"alert_snooze_seconds": 20}, {"alert_snooze_seconds": 60}], 20),
    ([{}, {"alert_snooze_seconds": 60}], 15),
])
def test_api_snooze_uses_shortest_configured_length(app_bundle, prefs_list, expected):
    app, token, ids = _user_with_accounts(app_bundle, prefs_list)
    with client_for(app, token) as client:
        assert client.get("/api/alert-state").json()["snooze_seconds"] == expected
        _make_red(ids[-1])
        before = time.time()
        snooze = client.post("/api/alert/snooze").json()
        assert snooze["snooze_seconds"] == expected
        until = _epoch(snooze["snoozed_until"])
        assert before + expected - 2 <= until <= before + expected + 1
        s = client.get("/api/alert-state").json()
        assert set(s) == STATE_KEYS and s["snoozed"] is True and s["snooze_seconds"] == expected
        test_state = client.post("/api/alert/test", json={"level": "amber"}).json()
        assert test_state["snooze_seconds"] == expected


def test_api_snooze_with_no_accounts_uses_default(app_bundle):
    app, token, _ = _user_with_accounts(app_bundle, [])
    with client_for(app, token) as client:
        assert client.get("/api/alert-state").json()["snooze_seconds"] == 15
        assert client.post("/api/alert/snooze").json()["snooze_seconds"] == 15


def test_changing_setting_does_not_alter_running_snooze(app_bundle):
    app, token, ids = _user_with_accounts(app_bundle, [{"alert_snooze_seconds": 60}])
    with client_for(app, token) as client:
        _make_red(ids[0])
        until = client.post("/api/alert/snooze").json()["snoozed_until"]
        client.patch(f"/api/accounts/{ids[0]}", json={"dashboard_preferences": {"alert_snooze_seconds": 5}})
        s = client.get("/api/alert-state").json()
        assert s["snoozed_until"] == until and s["snooze_seconds"] == 5


@pytest.mark.parametrize("key, sent, stored", [
    ("alert_snooze_seconds", 1, 5),
    ("alert_snooze_seconds", 500, 120),
    ("alert_snooze_seconds", 45, 45),
    ("alert_snooze_seconds", 30.7, 30),
    ("alert_stale_seconds", 5, 30),
    ("alert_stale_seconds", 1000, 300),
    ("alert_stale_seconds", 90, 90),
])
def test_preferences_api_clamps_alert_seconds(app_bundle, key, sent, stored):
    app, token, ids = _user_with_accounts(app_bundle, [{}])
    with client_for(app, token) as client:
        r = client.patch(f"/api/accounts/{ids[0]}", json={"dashboard_preferences": {key: sent}})
        assert r.status_code == 200
        assert r.json()["account"]["dashboard_preferences"][key] == stored


@pytest.mark.parametrize("key", ["alert_snooze_seconds", "alert_stale_seconds"])
def test_preferences_api_ignores_bad_types_and_none_resets(app_bundle, key):
    app, token, ids = _user_with_accounts(app_bundle, [{}])
    url = f"/api/accounts/{ids[0]}"

    def patch(v):
        r = client.patch(url, json={"dashboard_preferences": {key: v}})
        assert r.status_code == 200
        return r.json()["account"]["dashboard_preferences"]

    with client_for(app, token) as client:
        patch(90)
        for bad in (True, False, "60", "abc", [60], {"v": 60}):
            assert patch(bad)[key] == 90
        for reset in (None, ""):
            patch(90)
            assert patch(reset).get(key) is None


def test_preferences_api_awake_chime_round_trips_as_bool(app_bundle):
    app, token, ids = _user_with_accounts(app_bundle, [{}])
    url = f"/api/accounts/{ids[0]}"

    def patch(v):
        return client.patch(url, json={"dashboard_preferences": {"awake_chime": v}}).json()["account"]["dashboard_preferences"]

    with client_for(app, token) as client:
        assert patch(True)["awake_chime"] is True
        for bad in (1, "true", None, [True]):
            assert patch(bad)["awake_chime"] is True
        assert patch(False)["awake_chime"] is False
