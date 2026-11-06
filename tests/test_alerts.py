from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.alerts import ALERTS, AlertMonitor, DeviceAlertTracker
from app.models import OwletReading
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


def test_owlet_crit_flag_goes_red_immediately():
    t = DeviceAlertTracker(1, "AC1")
    t.observe(reading(0, 95, raw={"CRIT_OX_ALRT": 1}), 90, 86, T0)
    assert t.level == "red" and t.reason == "owlet_flag"


def test_owlet_low_flag_goes_amber_immediately():
    t = DeviceAlertTracker(1, "AC1")
    t.observe(reading(0, 95, raw={"low_oxygen_alert": True}), 90, 86, T0)
    assert t.level == "amber" and t.reason == "owlet_flag"


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


def test_stale_device_has_level_none_and_is_excluded():
    m = AlertMonitor()
    for i, v in enumerate([80, 80]):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + 10 * i)
    assert m.state([1], True, T0 + 10)["level"] == "red"
    s = m.state([1], True, T0 + 10 + 61)
    assert s["level"] == "none"
    assert s["devices"][0]["stale"] is True and s["devices"][0]["level"] == "none"


# ---- monitor: snooze and test alerts ----------------------------------------

def _red_monitor() -> AlertMonitor:
    m = AlertMonitor()
    for i, v in enumerate([80, 80]):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + 10 * i)
    return m


def test_snooze_silences_for_15s_then_returns():
    m = _red_monitor()
    now = T0 + 10
    until = m.snooze([1], now)
    assert until == now + 15
    s = m.state([1], True, now + 1)
    assert s["level"] == "red" and s["active_level"] == "none" and s["snoozed"] is True
    assert s["snooze_seconds_left"] == 14
    # keep it fresh
    m.observe(1, "Baby", reading(2, 80), 90, 86, now + 10)
    s = m.state([1], True, now + 15)
    assert s["active_level"] == "red" and s["snoozed"] is False and s["snoozed_until"] is None


def test_snooze_is_noop_when_nothing_alerting():
    m = AlertMonitor()
    m.observe(1, "Baby", reading(0, 97), 90, 86, T0)
    assert m.snooze([1], T0) is None
    assert m.state([1], True, T0)["snoozed"] is False


def test_snooze_cancelled_when_amber_escalates_to_red():
    m = AlertMonitor()
    for i, v in enumerate([88, 88]):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + 10 * i)
    m.snooze([1], T0 + 10)
    assert m.state([1], True, T0 + 11)["active_level"] == "none"
    for i, v in enumerate([84, 84], start=2):
        m.observe(1, "Baby", reading(i, v), 90, 86, T0 + 10 * i)
    s = m.state([1], True, T0 + 31)
    assert s["active_level"] == "red" and s["snoozed"] is False


def test_test_alert_sets_level_expires_and_cancels():
    m = AlertMonitor()
    m.set_test("red", 10, T0)
    s = m.state([], False, T0 + 1)
    assert s["level"] == "red" and s["test"] is True
    assert s["devices"][-1]["device_serial"] == "TEST" and s["devices"][-1]["reason"] == "test"
    assert m.state([], False, T0 + 10)["level"] == "none"
    m.set_test("amber", 999, T0)                 # clamped to 120
    assert m.state([], False, T0 + 119)["level"] == "amber"
    assert m.state([], False, T0 + 120)["test"] is False
    m.set_test("amber", 1, T0)                   # clamped to 5
    assert m.state([], False, T0 + 4)["level"] == "amber"
    m.set_test("none", None, T0)
    assert m.state([], False, T0 + 1)["level"] == "none"


def test_devices_scoped_to_accounts():
    m = AlertMonitor()
    m.observe(1, "A", reading(0, 97), 90, 86, T0)
    m.observe(2, "B", reading(0, 97, serial="AC2"), 90, 86, T0)
    assert [d["account_id"] for d in m.state([2], True, T0)["devices"]] == [2]


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
    d = ALERTS.state([7], True, time.time())
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
    "critical_threshold",
}
STATE_KEYS = {
    "generated_at", "collecting", "level", "active_level", "snoozed", "snoozed_until",
    "snooze_seconds_left", "snooze_seconds", "test", "devices",
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

        ALERTS.reset()
        assert client.post("/api/alert/snooze").json()["snoozed_until"] is None

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
