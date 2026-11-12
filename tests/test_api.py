from datetime import datetime, timedelta, timezone

import pytest

from app.auth_store import AuthStore
from app.config import Settings
from app.main import create_app
from app.models import normalize_reading
from app.store import ReadingStore
from tests.conftest import client_for, make_user


def _test_settings(**kwargs):
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


async def _default_account_id(store: ReadingStore):
    accounts = await store.list_accounts()
    if accounts:
        return accounts[0]["id"]
    return (await store.create_account(email="seed@example.test"))["id"]


async def _seed_reading(
    store: ReadingStore,
    timestamp: str,
    *,
    hr=120,
    spo2=96,
    sleep_state=1,
    skin_temp=32,
    device_serial="AC123",
    account_id=None,
):
    await store.insert_reading(
        normalize_reading(
            {
                "heart_rate": hr,
                "oxygen_saturation": spo2,
                "sleep_state": sleep_state,
                "last_updated": timestamp,
                "battery": 100,
                "skin_temperature": skin_temp,
            },
            device_serial,
        ),
        account_id=account_id or await _default_account_id(store),
    )


@pytest.mark.asyncio
async def test_health_reports_app_version(tmp_path):
    from app.main import _app_version

    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    _, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        health = client.get("/api/health").json()

    # The UI version badge reads this field; it must always be a usable string.
    assert health["version"] == _app_version()
    assert isinstance(health["version"], str) and health["version"]


@pytest.mark.asyncio
async def test_account_api_is_public_metadata_only_and_scopes_data(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    first = await store.create_account(email="first@example.test")
    second = await store.create_account(
        email="second@example.test",
        region="world",
        display_name="Second baby",
        api_token="secret-api-token",
        api_token_expiry=12345,
        refresh_token="secret-refresh-token",
    )
    await _seed_reading(store, "2026-07-02T01:00:00Z", hr=120, spo2=98, device_serial="SAME", account_id=first["id"])
    await _seed_reading(store, "2026-07-02T01:00:00Z", hr=150, spo2=90, device_serial="SAME", account_id=second["id"])
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        accounts = client.get("/api/accounts").json()["accounts"]
        first_readings = client.get(f"/api/readings?account={first['id']}&hours=24").json()
        second_readings = client.get(f"/api/readings?account={second['id']}&hours=24").json()
        second_devices = client.get(f"/api/devices?account={second['id']}").json()["devices"]
        updated = client.patch(
            f"/api/accounts/{second['id']}",
            json={
                "display_name": "Second profile",
                "dashboard_preferences": {
                    "chart_visibility": {"heartRate": True},
                    "chart_settings": {"window": "72", "smoothing": "15", "challenge_bands": False},
                    "secret": "ignored",
                },
            },
        ).json()["account"]

    assert len(accounts) == 2
    second_payload = next(account for account in accounts if account["id"] == second["id"])
    assert second_payload["display_name"] == "Second baby"
    assert second_payload["has_refresh_token"] is True
    assert updated["display_name"] == "Second profile"
    assert updated["dashboard_preferences"]["chart_visibility"] == {"heartRate": True}
    assert updated["dashboard_preferences"]["chart_settings"]["window"] == "72"
    assert "secret" not in updated["dashboard_preferences"]
    assert "refresh_token" not in second_payload
    assert "api_token" not in second_payload
    assert first_readings[0]["heart_rate"] == 120
    assert second_readings[0]["heart_rate"] == 150
    assert second_devices[0]["account_id"] == second["id"]


@pytest.mark.asyncio
async def test_summary_excludes_zero_offline_vitals_from_metric_averages(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    await _seed_reading(store, "2026-07-02T01:00:00Z", hr=120, spo2=98, skin_temp=31)
    await _seed_reading(store, "2026-07-02T01:05:00Z", hr=0, spo2=0, sleep_state=0, skin_temp=35)
    await _seed_reading(store, "2026-07-02T01:10:00Z", hr=140, spo2=96, skin_temp=33)
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        summary = client.get("/api/summary?hours=24").json()
        rollups = client.get("/api/rollups?bucket=hour&hours=24").json()

    assert summary["count"] == 3
    assert summary["valid_count"] == 2
    assert summary["offline_count"] == 1
    assert summary["heart_rate"]["avg"] == 130
    assert summary["oxygen_saturation"]["min"] == 96
    assert summary["skin_temperature"]["avg"] == 32
    assert summary["skin_temperature"]["min"] == 31
    assert rollups["rollups"][0]["offline_samples"] == 1
    assert rollups["rollups"][0]["avg_skin_temperature"] == 32


@pytest.mark.asyncio
async def test_sock_disconnected_nonzero_vitals_are_treated_as_offline(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    await _seed_reading(store, "2026-07-02T01:00:00Z", hr=120, spo2=98)
    await store.insert_reading(
        normalize_reading(
            {
                "heart_rate": 120,
                "oxygen_saturation": 98,
                "battery": 95,
                "last_updated": "2026-07-02T01:05:00Z",
                "sock_disconnected": True,
                "SOCK_DISCON_ALRT": {"value": 1},
                "alerts_mask": 16,
            },
            "AC123",
        ),
        account_id=await _default_account_id(store),
    )
    await _seed_reading(store, "2026-07-02T01:10:00Z", hr=130, spo2=97)
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        readings = client.get("/api/readings?hours=24").json()
        raw_readings = client.get("/api/readings?hours=24&include_raw=true").json()
        summary = client.get("/api/summary?hours=24").json()
        rollups = client.get("/api/rollups?bucket=hour&hours=24").json()

    assert readings[1]["sock_disconnected"] is True
    assert readings[1]["sock_off"] is False
    assert readings[1]["heart_rate"] == 0
    assert readings[1]["oxygen_saturation"] == 0
    assert readings[1]["movement"] == 0
    assert raw_readings[1]["heart_rate"] == 0
    assert raw_readings[1]["oxygen_saturation"] == 0
    # slimmed raw keeps the flags that explain WHY the row reads as offline
    assert raw_readings[1]["raw"]["sock_disconnected"] is True
    assert raw_readings[1]["raw"]["alerts_mask"] == 16
    assert summary["count"] == 3
    assert summary["valid_count"] == 2
    assert summary["offline_count"] == 1
    assert summary["heart_rate"]["avg"] == 125
    assert summary["oxygen_saturation"]["avg"] == 97.5
    assert rollups["rollups"][0]["offline_samples"] == 1


@pytest.mark.asyncio
async def test_oxygen_challenges_are_stored_and_excluded_from_normal_stats(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    await _seed_reading(store, "2026-07-02T00:00:00Z", hr=120, spo2=98, sleep_state=8, skin_temp=31)
    await _seed_reading(store, "2026-07-02T00:30:00Z", hr=122, spo2=97, sleep_state=8, skin_temp=32)
    await _seed_reading(store, "2026-07-02T01:00:00Z", hr=150, spo2=90, sleep_state=15, skin_temp=40)
    await _seed_reading(store, "2026-07-02T01:30:00Z", hr=155, spo2=89, sleep_state=15, skin_temp=41)
    await _seed_reading(store, "2026-07-02T02:00:00Z", hr=126, spo2=97, sleep_state=1, skin_temp=33)
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        created = client.post(
            "/api/oxygen-challenges",
            json={
                "start_time": "2026-07-02T01:00:00+00:00",
                "end_time": "2026-07-02T01:30:00+00:00",
                "label": "Nap off oxygen",
            },
        ).json()
        summary = client.get("/api/summary?hours=24").json()
        insights = client.get("/api/insights?hours=24").json()
        detail = client.get(f"/api/oxygen-challenges/{created['id']}").json()
        updated = client.patch(
            f"/api/oxygen-challenges/{created['id']}",
            json={
                "start_time": "2026-07-02T00:55:00+00:00",
                "end_time": "2026-07-02T01:35:00+00:00",
                "label": "Edited oxygen challenge",
                "notes": "Adjusted start/stop times",
            },
        ).json()
        active_again = client.patch(
            f"/api/oxygen-challenges/{created['id']}",
            json={"end_time": None},
        ).json()
        deleted = client.delete(f"/api/oxygen-challenges/{created['id']}").json()
        after_delete = client.get("/api/oxygen-challenges?hours=24").json()

    assert created["label"] == "Nap off oxygen"
    assert summary["challenge_count"] == 2
    assert summary["oxygen_saturation"]["min"] == 97
    assert summary["skin_temperature"]["avg"] == 32
    assert summary["skin_temperature"]["max"] == 33
    assert insights["breathing"]["low_oxygen_samples"] == 0
    assert detail["summary"]["avg_oxygen_saturation"] == 89.5
    assert detail["summary"]["low_oxygen_samples"] == 2
    assert detail["comparison"]["avg_oxygen_delta"] == -7.5
    assert len(detail["readings"]) == 2
    assert updated["label"] == "Edited oxygen challenge"
    assert updated["notes"] == "Adjusted start/stop times"
    assert updated["start_time"].startswith("2026-07-02T00:55:00")
    assert updated["end_time"].startswith("2026-07-02T01:35:00")
    assert active_again["end_time"] is None
    assert active_again["active"] is True
    assert deleted == {"ok": True}
    assert after_delete["total"] == 0


@pytest.mark.asyncio
async def test_oxygen_challenge_list_summaries_honor_device_filter(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    await _seed_reading(store, "2026-07-02T01:00:00Z", hr=120, spo2=90, device_serial="AC123")
    await _seed_reading(store, "2026-07-02T01:30:00Z", hr=122, spo2=92, device_serial="AC123")
    await _seed_reading(store, "2026-07-02T01:00:00Z", hr=150, spo2=70, device_serial="AC999")
    await _seed_reading(store, "2026-07-02T01:30:00Z", hr=152, spo2=72, device_serial="AC999")
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        client.post(
            "/api/oxygen-challenges",
            json={
                "start_time": "2026-07-02T01:00:00+00:00",
                "end_time": "2026-07-02T01:30:00+00:00",
                "label": "Device-specific challenge",
            },
        )
        ac123 = client.get("/api/oxygen-challenges?hours=24&device=AC123").json()
        ac999 = client.get("/api/oxygen-challenges?hours=24&device=AC999").json()

    assert ac123["items"][0]["summary"]["avg_oxygen_saturation"] == 91
    assert ac999["items"][0]["summary"]["avg_oxygen_saturation"] == 71


@pytest.mark.asyncio
async def test_notifications_endpoint_extracts_alerts_and_offline_periods(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    await store.insert_reading(
        normalize_reading(
            {
                "heart_rate": 0,
                "oxygen_saturation": 0,
                "battery": 82,
                "last_updated": "2026-07-02T01:00:00Z",
                "low_oxygen_alert": True,
                "SOCK_DISCON_ALRT": {"value": 1},
                "alerts_mask": 16,
            },
            "AC123",
        ),
        account_id=await _default_account_id(store),
    )
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        response = client.get("/api/notifications?hours=24")

    assert response.status_code == 200
    payload = response.json()
    event_types = {item["event_type"] for item in payload["items"]}
    assert "low_oxygen" in event_types
    assert "sock_disconnected" in event_types
    assert "offline_zero_vitals" not in event_types
    assert "alerts_mask" not in event_types
    assert "Owlet REAL_TIME_VITALS alert mask is 16" not in response.text
    assert payload["total"] == 2


@pytest.mark.asyncio
async def test_notifications_endpoint_does_not_derive_low_oxygen_from_readings(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    for timestamp, spo2 in [
        ("2026-07-02T01:00:00Z", 97),
        ("2026-07-02T01:01:00Z", 91),
        ("2026-07-02T01:02:00Z", 90),
        ("2026-07-02T01:03:00Z", 87),
    ]:
        await _seed_reading(store, timestamp, hr=130, spo2=spo2)
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        response = client.get("/api/notifications?hours=24")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["total"] == 0
    assert "Low oxygen reading" not in response.text
    assert "Critical oxygen reading" not in response.text
    assert "Measured SpO₂ dropped below" not in response.text


@pytest.mark.asyncio
async def test_widget_endpoint_returns_compact_status_payload(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    await _seed_reading(store, "2026-07-02T01:00:00Z", hr=120, spo2=98)
    await _seed_reading(store, "2026-07-02T01:05:00Z", hr=130, spo2=96)
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        payload = client.get("/api/widget?hours=24").json()

    assert payload["oxygen_now"] == 96
    assert payload["oxygen_avg"] == 97
    assert payload["heart_rate"] == 130
    assert payload["sock_reporting"] is True
    assert payload["trend"] in {"improving", "worsening", "stable"}

    await _seed_reading(store, "2026-07-02T01:10:00Z", hr=0, spo2=0)
    with client_for(app, session) as client:
        offline_payload = client.get("/api/widget?hours=24").json()
    assert offline_payload["sock_reporting"] is False


@pytest.mark.asyncio
async def test_api_returns_readings_and_summary(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    await store.insert_reading(
        normalize_reading(
            {"heart_rate": 120, "oxygen_saturation": 98, "last_updated": "2026-07-02T01:00:00Z"},
            "AC123",
        ),
        account_id=await _default_account_id(store),
    )
    await store.insert_reading(
        normalize_reading(
            {"heart_rate": 130, "oxygen_saturation": 96, "last_updated": "2026-07-02T02:00:00Z"},
            "AC123",
        ),
        account_id=await _default_account_id(store),
    )

    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        readings = client.get("/api/readings?hours=24").json()
        summary = client.get("/api/summary?hours=24").json()

    assert len(readings) == 2
    assert readings[0]["device_serial"] == "AC123"
    assert readings[1]["heart_rate"] == 130
    assert "raw" not in readings[0]
    assert summary["count"] == 2
    assert summary["heart_rate"]["trend"] == "up"

    with client_for(app, session) as client:
        raw_readings = client.get("/api/readings?hours=24&include_raw=true").json()

    # Stored raw is slimmed to the dynamic whitelist — the static Ayla envelope
    # (and pyowletapi's duplicate flattened vitals) no longer bloat every row.
    assert "heart_rate" not in raw_readings[0]["raw"]
    assert raw_readings[0]["heart_rate"] == 120   # ...because the value lives in its column


@pytest.mark.asyncio
async def test_api_lists_and_filters_devices(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    await _seed_reading(store, "2026-07-02T01:00:00Z", hr=120, spo2=98, device_serial="AC123")
    await _seed_reading(store, "2026-07-02T02:00:00Z", hr=130, spo2=96, device_serial="AC999")

    token = "test-share-token-123456"
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(
        store=store,
        settings=_test_settings(owlet_share_token=token),
        start_poller=False,
        auth_store=auth,
    )

    with client_for(app, session) as client:
        devices = client.get("/api/devices").json()["devices"]
        readings = client.get("/api/readings?device=AC999").json()
        summary = client.get("/api/summary?device=AC999").json()
        shared_devices_response = client.get(f"/share/{token}/api/devices")
        shared_readings = client.get(f"/share/{token}/api/readings?device=AC999").json()
        shared_summary = client.get(f"/share/{token}/api/summary?device=AC999").json()
        shared_widget = client.get(f"/share/{token}/api/widget?device=AC999").json()

    assert {device["serial"] for device in devices} == {"AC123", "AC999"}
    assert readings[0]["device_serial"] == "AC999"
    assert summary["device_serial"] == "AC999"
    assert summary["count"] == 1
    assert shared_devices_response.status_code == 200
    assert {device["serial"] for device in shared_devices_response.json()["devices"]} == {"AC123", "AC999"}
    assert shared_readings[0]["device_serial"] == "AC999"
    assert shared_summary["device_serial"] == "AC999"
    assert shared_widget["heart_rate"] == 130


@pytest.mark.asyncio
async def test_api_defaults_to_all_data_and_still_supports_hours_filter(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()

    for timestamp, hr in [
        ("2026-06-30T02:00:00Z", 110),
        ("2026-07-02T01:00:00Z", 120),
        ("2026-07-02T02:00:00Z", 130),
    ]:
        await store.insert_reading(
            normalize_reading(
                {"heart_rate": hr, "oxygen_saturation": 97, "last_updated": timestamp},
                "AC123",
            ),
            account_id=await _default_account_id(store),
        )

    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        all_readings = client.get("/api/readings").json()
        recent_readings = client.get("/api/readings?hours=24").json()
        all_summary = client.get("/api/summary").json()

    assert [row["heart_rate"] for row in all_readings] == [110, 120, 130]
    assert [row["heart_rate"] for row in recent_readings] == [120, 130]
    assert all_summary["window"] == "all"
    assert all_summary["count"] == 3


@pytest.mark.asyncio
async def test_api_exposes_insights_and_rollups(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    await _seed_reading(store, "2026-07-03T10:00:00Z", hr=100, spo2=92, sleep_state=8)
    await _seed_reading(store, "2026-07-03T10:30:00Z", hr=110, spo2=93, sleep_state=15)
    await _seed_reading(store, "2026-07-03T11:00:00Z", hr=120, spo2=96, sleep_state=1)
    await _seed_reading(store, "2026-07-03T11:30:00Z", hr=130, spo2=97, sleep_state=1)

    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        insights = client.get("/api/insights").json()
        rollups = client.get("/api/rollups?bucket=hour").json()

    assert insights["breathing"]["direction"] == "improving"
    assert insights["sleep"]["sleep_seconds"] == 3600
    assert rollups["bucket"] == "hour"
    assert [row["avg_oxygen_saturation"] for row in rollups["rollups"]] == [92.5, 96.5]


@pytest.mark.asyncio
async def test_dashboard_endpoint_serves_html(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    await store.create_account(email="sock@example.test")  # adopted by the first user below
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        response = client.get("/data")
        home = client.get("/")

    assert home.status_code == 200
    assert "status-line" in home.text          # Now page
    assert "/insights.js" in home.text
    assert response.status_code == 200
    assert "Owlet Dashboard" in response.text
    assert "/api/readings" in response.text
    assert "All stored data" in response.text
    assert '<option selected value="24">24 hours</option>' in response.text
    assert '<option value="72">3 days</option>' in response.text
    assert '<option value="168">7 days</option>' in response.text
    assert '<option value="all">All stored data</option>' in response.text
    assert 'id="profileMenuToggle"' in response.text
    assert 'id="profileAvatar"' in response.text
    assert 'id="profileMenu"' in response.text
    assert 'id="showCryptoSetting"' not in response.text
    assert "Crypto widget" not in response.text
    assert 'id="profileCurrentName"' not in response.text
    assert "Current profile" not in response.text
    assert "Switch profile" not in response.text
    assert "Display preferences" not in response.text
    assert 'id="deviceSelect"' in response.text
    assert 'id="accountSelect"' in response.text
    assert 'id="addAccount"' in response.text
    assert "Link another Owlet account" in response.text
    assert "renderProfileMenu" in response.text
    assert "showCryptoEnabled" not in response.text
    assert "updateCurrentAccountPreference" in response.text
    assert "show_crypto" not in response.text
    assert "el('profileMenuWrap')?.classList.toggle('share-only-hidden', SHARE_MODE);" in response.text
    assert "cluster.classList.toggle('hidden', SHARE_MODE);" in response.text
    assert "/api/accounts" in response.text
    assert "has_refresh_token" not in response.text
    assert "/api/devices" in response.text
    assert 'id="smoothing"' in response.text
    assert '<option selected value="raw">Raw points</option>' in response.text
    assert '<option value="5">5 min avg</option>' in response.text
    assert 'id="o2AddMenuToggle"' in response.text
    assert 'id="menuVisibleChallenge"' in response.text
    assert 'id="menuNewChallenge"' in response.text
    assert 'id="profileDeviceName"' in response.text
    assert '<button id="download" class="icon-button"' in response.text
    assert 'id="resetZoom"' in response.text
    assert 'id="resetZoomHeader"' in response.text
    assert "Reset view" in response.text
    assert "chartjs-plugin-zoom" in response.text
    assert "mobile-web-app-capable" in response.text
    assert 'rel="manifest" href="/manifest.webmanifest"' in response.text
    assert 'rel="icon" href="/favicon.ico" sizes="any"' in response.text
    assert 'rel="icon" type="image/svg+xml" href="/logo.svg"' in response.text
    assert 'rel="icon" type="image/png" sizes="32x32" href="/icon-32.png"' in response.text
    assert 'rel="apple-touch-icon" sizes="180x180" href="/icon-180.png"' in response.text
    assert "serviceWorker" in response.text
    assert "offlineBands" in response.text
    assert "themeToken('--chart-offline-fill', 'transparent')" in response.text
    assert "Device offline / sock off" in response.text
    assert "row?.sock_disconnected" in response.text
    assert "row?.sock_off" in response.text
    assert "notificationGlyphs" in response.text
    assert "oxygenCriticalThreshold" in response.text
    assert "${critical}% O₂" in response.text
    assert "notificationHoverPriority" in response.text
    assert "attachNotificationHover" in response.text
    assert "function tooltipTitle(items)" in response.text
    assert "title: tooltipTitle" in response.text
    assert "label: tooltipLabel" in response.text
    assert "chartValue(context.parsed.y, '%')" in response.text
    assert "callback: axisTick" in response.text
    assert "notificationHit" in response.text
    assert "hitRadius: 24" in response.text
    assert "/api/notifications" in response.text
    assert "Notifications" in response.text
    assert 'id="dailyInsightsToggle"' in response.text
    assert 'id="dailyInsightsModal"' in response.text
    assert 'id="dailyInsightsChart"' in response.text
    assert "dailyInsightsChart = new Chart" in response.text
    assert "dailyOxygenAvg" in response.text
    assert "dailyOxygenSleep" in response.text
    assert "dailyOxygenWake" in response.text
    assert "dailyHeartRateAvg" in response.text
    assert "dailySkinTempAvg" in response.text
    assert "Skin temp °C" in response.text
    assert "Temp avg" in response.text
    assert "Avg skin temp" in response.text
    assert "renderDailyInsightsChart(periods)" in response.text
    assert "Daily insights" in response.text
    assert "Calendar days. Today is in progress" in response.text
    assert "dailyInsightPeriods" in response.text
    assert "sleepBucket(row) === 'sleeping'" in response.text
    assert "sleepBucket(row) === 'waking'" in response.text
    assert "O₂ sleep" in response.text
    assert "HR wake" in response.text
    assert "offline/sock-off and O₂ challenge samples excluded" in response.text
    assert "/api/crypto" not in response.text
    assert "cryptoCard" not in response.text
    assert "btcPrice" not in response.text
    assert "O₂ trend companion" in response.text
    assert "How to read the O₂ trend companion" in response.text
    assert "MACD-style oxygen view" in response.text
    assert "oxygen challenges stay visible as real readings" in response.text
    assert "Offline gaps and oxygen challenges are not bridged" not in response.text
    assert "O₂ avg (%)" in response.text
    assert "30m − 4h signal" in response.text
    assert "Blue line:" not in response.text
    assert "trendLineLabel" not in response.text
    assert "Raw points O₂ avg" not in response.text
    assert "Trend signal" in response.text
    assert "legend: { display: false }" in response.text
    assert "TREND_MAX_SAMPLE_GAP_MS" in response.text
    assert "TREND_MAX_SOURCE_POINTS_DESKTOP = 800" in response.text
    assert "TREND_MAX_SOURCE_POINTS_MOBILE = 420" in response.text
    assert "trendSourceRows" in response.text
    assert "rollingOxygenAverage(shortMinutes, trendRows)" in response.text
    assert "rollingOxygenAverage(longMinutes, trendRows)" in response.text
    assert "y: null" in response.text
    assert "spanGaps: false" in response.text
    assert "Trend gap — offline or missing data." in response.text
    assert "Trend gap — offline, missing data, or O₂ challenge." not in response.text
    assert "function rollingOxygenAverage(minutes, rows = readings)" in response.text
    assert "const offline = isOffline(row) || !Number.isFinite(value)" in response.text
    assert "addGapMarker(time, inChallenge ? 'challenge' : 'offline')" not in response.text
    assert "const inChallenge = timeInIntervals(time, challengeWindows)" not in response.text
    assert "companion-info" in response.text
    assert "companion-header" not in response.text
    assert "requestIdleCallback" in response.text
    assert "hydrateSecondaryData" in response.text
    assert "renderCharts({ deferTrend: true })" in response.text
    assert "historyHoursForSelection" in response.text
    assert "Math.ceil(hours) + HISTORY_PAN_BUFFER_HOURS" in response.text
    assert "defaultVisibleRange" in response.text
    assert "extendPointsToVisibleEdges" in response.text
    assert "readingLineDataset('heartRate', 'Heart rate', 'heart_rate'" in response.text
    assert 'id="chartLayout"' in response.text
    assert "gapIntervals" in response.text
    assert "noDataBands" in response.text
    assert "rollingAverageForKey" in response.text
    assert "const value = metricValue(row, key)" in response.text
    assert "if (isOffline(row) || value === null)" in response.text
    assert "key === 'skin_temperature' ? null : 0" in response.text
    assert "reason: key === 'skin_temperature' ? 'missing' : 'offline-zero'" in response.text
    assert "offset: false" in response.text
    assert "hr: { type: 'linear', position: 'left', min: 0" in response.text
    assert "spo2: { type: 'linear', position: 'right', min: 0" in response.text
    assert "'skinTemperature', 'Skin temp °C', 'skin_temperature'" in response.text
    assert "metric-grid" not in response.text
    assert "renderMetricCards" not in response.text
    assert 'id="metricCards"' not in response.text
    assert "dataQs" in response.text
    assert response.text.count("fetchJson(`${API_BASE}/api/readings?${dataQs}`)") == 1
    assert "refreshInFlight" in response.text
    assert "if (refreshInFlight && !force) return refreshInFlight" in response.text
    assert "attachReadingsTableSelection" in response.text
    assert "selectionAttached" in response.text
    assert "filtered.map((row, index)" not in response.text
    assert "TABLE_ROW_LIMIT = 500" in response.text
    assert "newest ${tableRows.length} of ${filtered.length} loaded" in response.text
    assert "tableRows.map((row, index)" in response.text
    assert "HISTORY_PAN_BUFFER_HOURS = 2" in response.text
    assert "OLDER_HISTORY_CHUNK_HOURS = 24" in response.text
    assert "function chartPoints(points)" in response.text
    assert "downsamplePoints" not in response.text
    assert "CHART_MAX_POINTS" not in response.text
    assert "bucket.forEach(item" not in response.text
    assert "offlineTransition" in response.text
    assert "table.addEventListener('click'" in response.text
    assert "loadOlderHistoryIfNeeded" in response.text
    assert "release near the left edge to load more" in response.text
    assert "oxygenTrendSignal(shortAvg, longAvg)" in response.text
    assert "sleepPhaseHover" in response.text
    assert "sleepBands" in response.text
    assert "stateChartHover" in response.text
    assert "stateTooltip" in response.text
    assert "dayBoundaries" in response.text
    assert "dayBoundaryTimes" in response.text
    assert "state-day-boundary" in response.text
    assert "timeTickValues(range)" in response.text
    assert "sleepStageInfo" in response.text
    assert "sleepHighlightToggle" in response.text
    assert "sleepBallparkToggle" in response.text
    assert "challengeBandsToggle" in response.text
    assert "Guess sleep windows" in response.text
    assert "smoothBallparkIntervals" in response.text
    assert "function rollupBucket()" in response.text
    assert "return '30m';" in response.text
    assert "movementSeconds" in response.text
    assert "awakeLikeSeconds" in response.text
    assert "subtractIntervals" in response.text
    assert "setStateStripHoverFromEvent" in response.text
    assert "attachStateChartHover" in response.text
    assert "id=\"timePan\"" in response.text
    assert "panToSliderValue" in response.text
    assert response.text.index("chart-toolbar") < response.text.index("id=\"vitalsChart\"")
    assert response.text.index("challengeBandsToggle") < response.text.index("id=\"vitalsChart\"")
    assert response.text.index("id=\"vitalsChart\"") < response.text.index("id=\"stateStrip\"") < response.text.index("id=\"oxygenTrendChart\"")
    assert "wheel:" in response.text
    assert "pinch:" in response.text
    assert "selector.value = '6'" not in response.text
    assert "drag: { enabled: !mobile" in response.text
    assert "pan: { enabled: true" in response.text
    assert "onPanComplete: ({ chart }) => { syncZoomFrom(chart); loadOlderHistoryIfNeeded().catch(console.error); }" in response.text
    assert "touch-action: pan-y" in response.text
    assert "attachMobileDragPan" in response.text
    assert "panVisibleWindowByPixels" in response.text
    assert "pointerType === 'touch'" in response.text
    assert "visibleWindowSnapshot" in response.text
    assert "refreshedVisibleRange" in response.text
    assert "sliderAtEnd" in response.text
    assert "onPanComplete" in response.text
    assert "O₂ challenges" in response.text
    assert "O₂ challenge" in response.text
    assert "O₂ challenges / add" not in response.text
    assert "O₂+" in response.text
    assert "Use current graph window" in response.text
    assert "Enter new times…" in response.text
    assert "Add new O₂ challenge" in response.text
    assert "Add one from this popup" in response.text
    assert "data-add-challenge-empty" in response.text
    assert "data-visible-challenge-empty" in response.text
    assert "Use visible chart window" in response.text
    assert "/api/oxygen-challenges" in response.text
    assert "challengeBands" in response.text
    assert "beforeDraw(chart, _args, options)" in response.text
    assert "const isTrendCompanion = chart.canvas.id === 'oxygenTrendChart'" in response.text
    assert "themeToken('--chart-challenge-fill-trend', 'transparent')" in response.text
    assert "ctx.setLineDash(isTrendCompanion ? [7, 5] : [8, 5])" in response.text
    assert "ctx.strokeRect(left, chartArea.top + 1" in response.text
    assert "seriesAlpha(SERIES.good, 'f2')" in response.text
    assert "seriesAlpha(SERIES.bad, 'f2')" in response.text
    assert "stateStrip" in response.text
    assert "Challenge data is excluded" in response.text
    assert "challengeEditForm" in response.text
    assert "Save edits" in response.text
    assert "Delete challenge" in response.text
    assert "closeNotificationsPanel" not in response.text  # bell moved to the shell
    assert 'id="notificationsToggle"' not in response.text
    assert 'id="shellBell"' in response.text
    assert "closeChallengesPanel" in response.text
    assert "safeRefresh" in response.text
    assert 'id="refresh"' not in response.text  # refresh lives on the shell's living dot
    assert 'id="shellDot"' in response.text
    assert 'class="shell-tabs"' in response.text  # unified top bar
    assert "initialLoading" in response.text
    assert "Loading Owlet data" in response.text
    assert "setInitialLoading('Loading readings and notifications" in response.text
    assert "hideInitialLoading" in response.text
    assert "titleStatusDot" in response.text
    assert "device-label" in response.text
    assert "mobile-label" in response.text
    assert "O₂ Ch." in response.text
    assert "compactDeviceName" in response.text
    assert "Sock ${digits}" in response.text
    assert "Auto-refresh in ${secondsUntilRefresh}s" in response.text
    assert "updateTitleDotProgress" in response.text
    assert "lastDataAt" in response.text
    assert "barThickness: isMobileViewport() ? 3 : 4" in response.text
    assert "minBarLength: 2" in response.text
    assert '<span class="control-section-title">View</span>' not in response.text
    assert "refreshNote" not in response.text
    assert "batteryStatus" not in response.text  # battery lives in the shell top bar now
    assert 'id="shellBattery"' in response.text
    assert "latestBattery" not in response.text
    assert "lowOxygen" not in response.text
    assert "oxygen-value.good" in response.text
    assert "function oxygenValueClass" in response.text
    assert "if (value >= o2.warn) return 'good'" in response.text   # tiers come from settings
    assert "if (value >= o2.critical) return 'caution'" in response.text
    assert "return 'danger'" in response.text
    assert "const visibility = new Map" in response.text
    assert "readings-grid" in response.text
    assert "reading-detail-panel" in response.text
    assert "click any row on the left" in response.text
    assert "selected-row" in response.text
    assert "O₂ now + today" in response.text
    assert "Crypto" not in response.text
    assert 'id="installApp"' in response.text
    assert response.text.index("aria-label=\"At a glance\"") < response.text.index("id=\"vitalsChart\"")
    assert 'id="rollupChart"' not in response.text
    assert 'id="stateChart"' not in response.text
    assert response.text.index("Readings table") < response.text.index("Selected reading")
    assert "O₂ now + today" in response.text
    assert "Heart rate" in response.text
    assert response.text.index('id="latestOxygen"') < response.text.index('id="sleepTotal"')
    assert "sleepTotal" in response.text
    assert "state-legend" not in response.text


@pytest.mark.asyncio
async def test_pwa_assets_are_served(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        manifest = client.get("/manifest.webmanifest")
        worker = client.get("/sw.js")
        favicon = client.get("/favicon.ico")
        logo = client.get("/logo.svg")
        icon_32 = client.get("/icon-32.png")
        icon_180 = client.get("/icon-180.png")
        icon_192 = client.get("/icon-192.png")
        icon_512 = client.get("/icon-512.png")

    assert manifest.status_code == 200
    payload = manifest.json()
    assert payload["id"] == "/"
    assert payload["display"] == "standalone"
    assert payload["start_url"] == "/"
    assert {icon["sizes"] for icon in payload["icons"]} >= {"32x32", "192x192", "512x512"}
    assert worker.status_code == 200
    assert "CACHE_NAME" in worker.text
    assert "owlet-dashboard-v5" in worker.text
    assert "networkFirst" in worker.text     # styles/scripts/navigations must not be served cache-first
    assert "/favicon.ico" in worker.text
    assert "/logo.svg" in worker.text
    assert favicon.status_code == 200
    assert favicon.headers["content-type"] == "image/x-icon"
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/svg+xml"
    assert "Owlet Dashboard logo" in logo.text
    for icon in (icon_32, icon_180, icon_192, icon_512):
        assert icon.status_code == 200
        assert icon.headers["content-type"] == "image/png"
        assert icon.content.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_bundled_fonts_are_served_and_precached(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)
    font_files = [
        "source-sans-3-latin-400-normal.woff2",
        "source-sans-3-latin-600-normal.woff2",
        "source-sans-3-latin-700-normal.woff2",
        "source-serif-4-latin-400-normal.woff2",
        "source-serif-4-latin-600-normal.woff2",
    ]

    with client_for(app, session) as client:
        font = client.get(f"/fonts/{font_files[0]}")
        missing = client.get("/fonts/nope.woff2")
        licence = client.get("/fonts/LICENSE-SourceSans3.txt")
        traversal = client.get("/fonts/..%2Ftheme.css")
        worker = client.get("/sw.js")

    assert font.status_code == 200
    assert font.headers["content-type"] == "font/woff2"
    assert font.content.startswith(b"wOF2")
    assert missing.status_code == 404
    assert licence.status_code == 404
    assert traversal.status_code == 404
    assert "owlet-dashboard-v5" in worker.text
    for name in font_files:
        assert f"/fonts/{name}" in worker.text


@pytest.mark.asyncio
async def test_page_assets_are_public_no_cache_and_strictly_named(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    app = create_app(store=store, settings=_test_settings(), start_poller=False)

    # No session: share pages and sign-in pages must be able to load these.
    with client_for(app) as anon:
        css = anon.get("/static/pages/night.css")
        js = anon.get("/static/pages/night.js")
        rejected = [
            anon.get(path).status_code
            for path in (
                "/static/pages/missing.js",
                "/static/pages/night.CSS",
                "/static/pages/night.css.map",
                "/static/pages/..%2Ftheme.css",
                "/static/pages/%2E%2E%2F..%2Fmain.py",
                "/static/pages/..%5C..%5Cmain.py",
                "/static/pages/night",
            )
        ]
        worker = anon.get("/sw.js").text

    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert js.status_code == 200 and js.headers["content-type"].startswith("application/javascript")
    assert css.headers["cache-control"] == js.headers["cache-control"] == "no-cache"
    assert rejected == [404] * len(rejected)
    # They change every release: never cache-first in the service worker.
    immutable = worker.split("const IMMUTABLE = [", 1)[1].split("];", 1)[0]
    assert "/static/pages/" not in immutable


@pytest.mark.asyncio
async def test_share_link_serves_read_only_dashboard_and_api_without_basic_auth(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    await store.insert_reading(
        normalize_reading(
            {
                "heart_rate": 120,
                "oxygen_saturation": 98,
                "last_updated": "2026-07-02T01:00:00Z",
                "raw": {"secretish": "not exposed"},
            },
            "AC123",
        ),
        account_id=await _default_account_id(store),
    )
    token = "share-token-with-enough-length"
    settings = _test_settings(
        owlet_basic_auth_username="parent",
        owlet_basic_auth_password="secret-pass",
        owlet_share_token=token,
    )
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=settings, start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        dashboard = client.get(f"/share/{token}")
        readings = client.get(f"/share/{token}/api/readings?include_raw=true")
        summary = client.get(f"/share/{token}/api/summary?limit=100000&hours=24")
        insights = client.get(f"/share/{token}/api/insights?limit=100000&hours=24")
        rollups = client.get(f"/share/{token}/api/rollups?limit=100000&bucket=hour&hours=24")
        wrong = client.get("/share/wrong-token-with-enough-length")
    with client_for(app) as anonymous:
        normal_api = anonymous.get("/api/readings")

    assert dashboard.status_code == 200
    assert f'const API_BASE = "/share/{token}";' in dashboard.text
    assert "Shared read-only view" in dashboard.text
    assert 'rel="manifest"' not in dashboard.text
    assert "serviceWorker.register" in dashboard.text
    assert readings.status_code == 200
    assert readings.json()[0]["heart_rate"] == 120
    assert "raw" not in readings.json()[0]
    assert summary.status_code == 200
    assert insights.status_code == 200
    assert rollups.status_code == 200
    assert rollups.json()["bucket"] == "hour"
    assert wrong.status_code == 404
    assert normal_api.status_code == 401


@pytest.mark.asyncio
async def test_poll_interval_preference_updates_account_and_live_poller(tmp_path):
    from types import SimpleNamespace

    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    auth = AuthStore(db_path)
    user, session = await make_user(auth, "owner@example.test")
    account = await store.create_account(email="sock@x.y", user_id=user["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)
    fake_poller = SimpleNamespace(account_id=account["id"], interval_seconds=30)
    app.state.owlet_state.setdefault("pollers", []).append(fake_poller)

    with client_for(app, session) as client:
        updated = client.patch(
            f"/api/accounts/{account['id']}", json={"poll_interval_seconds": 10}
        ).json()["account"]
        assert updated["poll_interval_seconds"] == 10
        assert fake_poller.interval_seconds == 10
        # values outside the whitelist are ignored
        rejected = client.patch(
            f"/api/accounts/{account['id']}", json={"poll_interval_seconds": 7}
        ).json()["account"]
        assert rejected["poll_interval_seconds"] == 10
        assert fake_poller.interval_seconds == 10


@pytest.mark.asyncio
async def test_night_and_rhythms_pages_are_session_gated(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app) as anon:
        for path in ("/night", "/rhythms"):
            response = anon.get(path, follow_redirects=False)
            assert response.status_code == 303 and response.headers["location"] == "/login"

    with client_for(app, session) as client:
        night = client.get("/night")
        assert night.status_code == 200
        assert '<link rel="stylesheet" href="/static/pages/night.css" />' in night.text
        assert '<script src="/static/pages/night.js"></script>' in night.text
        night_js = client.get("/static/pages/night.js").text
        assert "The night, minute by minute" in night_js
        assert "/api/rollups?bucket=5m" in night_js
        rhythms = client.get("/rhythms")
        assert rhythms.status_code == 200
        assert '<link rel="stylesheet" href="/static/pages/rhythms.css" />' in rhythms.text
        assert '<script src="/static/pages/rhythms.js"></script>' in rhythms.text
        rhythms_js = client.get("/static/pages/rhythms.js").text
        assert "Two weeks, half-hour by half-hour" in rhythms_js
        assert "/api/rollups?bucket=30m" in rhythms_js


@pytest.mark.asyncio
async def test_care_events_crud_and_tenancy(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    auth = AuthStore(db_path)
    owner, owner_session = await make_user(auth, "owner@example.test")
    other, other_session = await make_user(auth, "other@example.test")
    await store.create_account(email="sock@x.y", user_id=owner["id"])
    await store.create_account(email="other@x.y", user_id=other["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, owner_session) as client:
        created = client.post("/api/events", json={"kind": "O2 on", "note": "starting flow"}).json()["event"]
        with_time = client.post(
            "/api/events", json={"kind": "Sock off", "at": "2026-07-12T18:30:00+00:00"}
        ).json()["event"]
        listed = client.get("/api/events?hours=8760").json()["events"]
        bad = client.post("/api/events", json={"kind": ""})
        bad_time = client.post("/api/events", json={"kind": "Feed", "at": "not-a-time"})

    assert created["kind"] == "O2 on"
    assert created["note"] == "starting flow"
    assert with_time["at"].startswith("2026-07-12T18:30:00")
    assert {event["id"] for event in listed} >= {created["id"], with_time["id"]}
    assert bad.status_code == 400
    assert bad_time.status_code == 400

    with client_for(app, other_session) as other_client:
        assert other_client.get("/api/events?hours=8760").json()["events"] == []
        assert other_client.delete(f"/api/events/{created['id']}").status_code == 404

    with client_for(app, owner_session) as client:
        assert client.delete(f"/api/events/{created['id']}").json() == {"ok": True}
        remaining = client.get("/api/events?hours=8760").json()["events"]
    assert [event["id"] for event in remaining] == [with_time["id"]]


@pytest.mark.asyncio
async def test_unlink_account_removes_it_and_its_data_scoped(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    owner, owner_session = await make_user(auth, "owner@example.test")
    other, other_session = await make_user(auth, "other@example.test")
    mine = await store.create_account(email="sock@x.y", user_id=owner["id"])
    theirs = await store.create_account(email="theirs@x.y", user_id=other["id"])
    await _seed_reading(store, "2026-07-02T01:00:00Z", account_id=mine["id"])
    await _seed_reading(store, "2026-07-02T01:05:00Z", account_id=mine["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, owner_session) as client:
        # can't delete another tenant's account
        assert client.delete(f"/api/accounts/{theirs['id']}").status_code == 404
        # own account: gone, along with its readings
        assert client.delete(f"/api/accounts/{mine['id']}").json() == {"ok": True}
        assert client.get("/api/accounts").json()["accounts"] == []
        # second delete is a 404
        assert client.delete(f"/api/accounts/{mine['id']}").status_code == 404

    assert await store.get_readings(hours=None, account_ids=[mine["id"]]) == []
    assert (await store.list_accounts(user_id=other["id"]))[0]["id"] == theirs["id"]


@pytest.mark.asyncio
async def test_relinking_same_owlet_email_updates_not_duplicates(tmp_path, monkeypatch):
    import app.main as main_module

    class FakeOwletClient:
        counter = 0

        def __init__(self, email=None, password=None, region="world", **kwargs):
            FakeOwletClient.counter += 1
            self._n = FakeOwletClient.counter

        async def connect(self):
            return None

        def discard_password(self):
            return None

        @property
        def tokens(self):
            return {"api_token": f"tok{self._n}", "expiry": None, "refresh": f"ref{self._n}"}

        async def close(self):
            return None

    monkeypatch.setattr(main_module, "OwletClient", FakeOwletClient)

    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        first = client.post("/api/accounts", json={"email": "SOCK@owlet.test", "password": "pw"})
        assert first.status_code == 200
        second = client.post("/api/accounts", json={"email": "sock@owlet.test", "password": "pw"})
        assert second.status_code == 200
        accounts = client.get("/api/accounts").json()["accounts"]

    # same Owlet login (case-insensitive) collapses to one account, tokens refreshed
    assert len(accounts) == 1
    assert first.json()["account"]["id"] == second.json()["account"]["id"]
    refreshed = await store.get_account(accounts[0]["id"])
    assert refreshed["refresh_token"] == "ref2"


@pytest.mark.asyncio
async def test_disconnect_keeps_data_and_reactivates_on_relink(tmp_path, monkeypatch):
    import app.main as main_module

    class FakeOwletClient:
        def __init__(self, email=None, password=None, region="world", **kwargs):
            pass

        async def connect(self):
            return None

        def discard_password(self):
            return None

        @property
        def tokens(self):
            return {"api_token": "tok", "expiry": None, "refresh": "ref"}

        async def close(self):
            return None

    monkeypatch.setattr(main_module, "OwletClient", FakeOwletClient)

    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    account = await store.create_account(
        email="sock@owlet.test", user_id=user["id"], owlet_password="pw"
    )
    await _seed_reading(store, "2026-07-02T01:00:00Z", account_id=account["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        # log out: non-destructive — drops off the picker and home falls to onboarding
        assert client.post(f"/api/accounts/{account['id']}/disconnect").json() == {"ok": True}
        assert client.get("/api/accounts").json()["accounts"] == []
        assert "link your owlet sock" in client.get("/").text.lower()
        # disconnecting a stranger's / missing account is a 404
        assert client.post("/api/accounts/9999/disconnect").status_code == 404

    # the row and its readings survive; stored credentials are wiped
    row = await store.get_account(account["id"])
    assert row["status"] == "disconnected"
    assert row["refresh_token"] is None and row["owlet_password"] is None
    assert len(await store.get_readings(hours=None, account_ids=[account["id"]])) == 1

    # re-linking the same login reactivates THIS account (same id) with data intact
    with client_for(app, session) as client:
        relink = client.post("/api/accounts", json={"email": "sock@owlet.test", "password": "pw"})
        assert relink.status_code == 200
        assert relink.json()["account"]["id"] == account["id"]
    reactivated = await store.get_account(account["id"])
    assert reactivated["status"] == "active"
    assert len(await store.get_readings(hours=None, account_ids=[account["id"]])) == 1


@pytest.mark.asyncio
async def test_feed_events_carry_method_and_amounts(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    await store.create_account(email="sock@x.y", user_id=user["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        bottle = client.post("/api/events", json={
            "kind": "Feeding", "method": "bottle", "amount_ml": 118.3,
        }).json()["event"]
        nursing = client.post("/api/events", json={
            "kind": "Feeding", "method": "nursing", "duration_min": 15,
        }).json()["event"]
        solids = client.post("/api/events", json={
            "kind": "Feeding", "method": "solids", "note": "oatmeal",
        }).json()["event"]
        # junk values are dropped, not stored
        junk = client.post("/api/events", json={
            "kind": "Feeding", "method": "psychic", "amount_ml": 9000, "duration_min": -5,
        }).json()["event"]
        listed = client.get("/api/events?hours=24").json()["events"]

    assert bottle["method"] == "bottle" and bottle["amount_ml"] == 118.3
    assert nursing["method"] == "nursing" and nursing["duration_min"] == 15
    assert solids["method"] == "solids" and solids["note"] == "oatmeal"
    assert junk["method"] == "" and junk["amount_ml"] is None and junk["duration_min"] is None
    by_id = {e["id"]: e for e in listed}
    assert by_id[bottle["id"]]["amount_ml"] == 118.3
    assert by_id[nursing["id"]]["duration_min"] == 15


@pytest.mark.asyncio
async def test_notifications_read_tracking(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    account_id = await _default_account_id(store)
    await store.insert_reading(
        normalize_reading(
            {
                "heart_rate": 120,
                "oxygen_saturation": 95,
                "battery": 82,
                "last_updated": "2026-07-02T01:00:00Z",
                "low_oxygen_alert": True,
            },
            "AC123",
        ),
        account_id=account_id,
    )
    auth = AuthStore(db_path)
    user, session = await make_user(auth, "owner@example.test")
    other, other_session = await make_user(auth, "other@example.test")
    await store.create_account(email="other@x.y", user_id=other["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        listed = client.get("/api/notifications?hours=8760").json()
        widget = client.get("/api/widget?hours=1").json()
        assert listed["unread_total"] == listed["total"] > 0
        assert all(item["read_at"] is None and item["id"] for item in listed["items"])
        assert widget["unread_notifications"] == listed["unread_total"]
        assert widget["latest_notification"]["read_at"] is None

        # another user marking read must not touch this account's notifications
        with client_for(app, other_session) as other_client:
            assert other_client.post("/api/notifications/read").json()["marked"] == 0
        still = client.get("/api/notifications?hours=8760").json()
        assert still["unread_total"] == listed["unread_total"]

        marked = client.post("/api/notifications/read").json()
        assert marked["ok"] is True and marked["marked"] == listed["unread_total"]
        after = client.get("/api/notifications?hours=8760").json()
        assert after["unread_total"] == 0
        assert all(item["read_at"] for item in after["items"])
        assert client.get("/api/widget?hours=1").json()["unread_notifications"] == 0


@pytest.mark.asyncio
async def test_custom_low_oxygen_alert_fires_once_per_crossing(tmp_path):
    from app.poller import Poller

    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    account = await store.create_account(email="alert@example.test")
    await store.update_account_preferences(
        account["id"], dashboard_preferences={"o2_alert_threshold": 90}
    )
    poller = Poller(store, read_once=None, account_id=account["id"])

    def reading(o2, ts):
        return normalize_reading(
            {"heart_rate": 120, "oxygen_saturation": o2, "battery": 80, "last_updated": ts},
            "AC123",
        )

    await poller._check_custom_alert(reading(95, "2026-07-13T01:00:00Z"))
    await poller._check_custom_alert(reading(89, "2026-07-13T01:00:05Z"))  # crossing -> alert
    await poller._check_custom_alert(reading(88, "2026-07-13T01:00:10Z"))  # still low -> no repeat
    notifications = await store.get_notifications(hours=None, account_ids=[account["id"]])
    customs = [n for n in notifications["items"] if n["event_type"] == "custom_low_oxygen"]
    assert len(customs) == 1
    assert customs[0]["severity"] == "critical"
    assert "89%" in customs[0]["title"]
    assert "90% alert level" in customs[0]["message"]

    # threshold preference round-trips through the PATCH whitelist
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    owned = await store.create_account(email="mine@x.y", user_id=user["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)
    with client_for(app, session) as client:
        updated = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {"o2_alert_threshold": 88}},
        ).json()["account"]
        assert updated["dashboard_preferences"]["o2_alert_threshold"] == 88
        cleared = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {"o2_alert_threshold": None}},
        ).json()["account"]
        assert cleared["dashboard_preferences"].get("o2_alert_threshold") is None
        rejected = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {"o2_alert_threshold": 40}},
        ).json()["account"]
        assert rejected["dashboard_preferences"].get("o2_alert_threshold") is None


@pytest.mark.asyncio
async def test_night_and_readiness_preferences_whitelist(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    owned = await store.create_account(email="mine@x.y", user_id=user["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)
    with client_for(app, session) as client:
        updated = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {
                "night_start": "20:30",
                "night_end": "06:00",
                "readiness_report_time": "18:45",
                "tz_offset_minutes": -300,
            }},
        ).json()["account"]
        prefs = updated["dashboard_preferences"]
        assert prefs["night_start"] == "20:30"
        assert prefs["night_end"] == "06:00"
        assert prefs["readiness_report_time"] == "18:45"
        assert prefs["tz_offset_minutes"] == -300

        cleared = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {"readiness_report_time": None}},
        ).json()["account"]
        assert cleared["dashboard_preferences"].get("readiness_report_time") is None

        garbage = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {
                "night_start": "25:99",
                "night_end": "not a clock",
                "readiness_report_time": "7pm",
                "tz_offset_minutes": 99999,
            }},
        ).json()["account"]
        prefs = garbage["dashboard_preferences"]
        assert prefs["night_start"] == "20:30"       # bad values leave prior setting alone
        assert prefs["night_end"] == "06:00"
        assert prefs.get("readiness_report_time") is None
        assert prefs["tz_offset_minutes"] == -300


@pytest.mark.asyncio
async def test_new_vitals_fields_round_trip_and_snapshot(tmp_path):
    from app.store import slim_raw

    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    account_id = await _default_account_id(store)
    rtv = ('{"ox":97,"hr":140,"mv":12,"st":33,"bat":76,"btt":832,"chg":1,'
           '"rsi":64,"ss":8,"mvb":18,"oxta":96,"alrt":0}')
    payload = {
        "last_updated": "2026-07-14T02:00:00Z",
        "heart_rate": 140,
        "oxygen_saturation": 97,
        "battery": 76,
        "battery_minutes": 832,
        "movement": 12,
        "movement_bucket": 18,
        "oxygen_10_av": 96,
        "signal_strength": 64,
        "charging": 1,
        "sleep_state": 8,
        "REAL_TIME_VITALS": {"type": "Property", "base_type": "string",
                             "display_name": "Real-Time Vitals", "value": rtv},
        "oem_sock_version": {"type": "Property", "value": '{"version":"2.10.8"}'},
        "CONFIG_STATUS": {"type": "Property", "value": '{"smac":"AA11","bmac":"BB22"}'},
    }
    await store.insert_reading(normalize_reading(payload, "AC123"), account_id=account_id)

    rows = await store.get_readings(hours=24, account_ids=[account_id])
    row = rows[0]
    assert row.battery_minutes == 832
    assert row.movement_bucket == 18
    assert row.oxygen_10_av == 96
    assert row.signal_strength == 64
    assert row.charging is True
    # analysis path carries the same columns
    analysis = await store.get_analysis_readings(hours=24, account_ids=[account_id])
    assert analysis[0].signal_strength == 64 and analysis[0].charging is True

    # raw was slimmed but the vitals value string survives and re-normalizes
    assert "oem_sock_version" not in row.raw
    recovered = normalize_reading(row.raw | {"last_updated": "2026-07-14T02:00:00Z"}, "AC123")
    assert recovered.heart_rate == 140 and recovered.oxygen_saturation == 97
    assert recovered.movement_bucket == 18 and recovered.charging is True

    # the static envelope landed in the snapshot, once
    snapshot = await store.get_device_snapshot(account_ids=[account_id])
    assert snapshot is not None
    assert snapshot["payload"]["oem_sock_version"]["value"] == '{"version":"2.10.8"}'

    # oxta sentinel 255 = "no smoothed reading yet"
    assert normalize_reading({"oxygen_10_av": 255}, "AC123").oxygen_10_av is None
    # slim_raw keeps flags out of a full payload
    slim = slim_raw(payload | {"alerts_mask": 16, "sock_off": True})
    assert set(slim) == {"REAL_TIME_VITALS", "alerts_mask", "sock_off"}


@pytest.mark.asyncio
async def test_timezone_and_display_preferences_whitelist(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    owned = await store.create_account(email="mine@x.y", user_id=user["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)
    with client_for(app, session) as client:
        updated = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {
                "timezone": "America/Chicago",
                "movement_source": "bucket",
                "o2_display": "smoothed",
            }},
        ).json()["account"]
        prefs = updated["dashboard_preferences"]
        assert prefs["timezone"] == "America/Chicago"
        assert prefs["movement_source"] == "bucket"
        assert prefs["o2_display"] == "smoothed"

        garbage = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {
                "timezone": "Mars/Olympus_Mons",
                "movement_source": "psychic",
                "o2_display": 42,
            }},
        ).json()["account"]
        prefs = garbage["dashboard_preferences"]
        assert prefs["timezone"] == "America/Chicago"     # junk leaves prior values alone
        assert prefs["movement_source"] == "bucket"
        assert prefs["o2_display"] == "smoothed"

        cleared = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {"timezone": None}},
        ).json()["account"]
        assert cleared["dashboard_preferences"].get("timezone") is None


@pytest.mark.asyncio
async def test_birth_date_preference_whitelist(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    owned = await store.create_account(email="mine@x.y", user_id=user["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)
    future = (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()
    with client_for(app, session) as client:
        set_ok = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {"birth_date": "2026-05-01"}},
        ).json()["account"]
        assert set_ok["dashboard_preferences"]["birth_date"] == "2026-05-01"

        # future dates, junk, and absurdly old dates are rejected (prior value kept)
        for bad in (future, "not-a-date", "1990-01-01", "2026-13-40"):
            kept = client.patch(
                f"/api/accounts/{owned['id']}",
                json={"dashboard_preferences": {"birth_date": bad}},
            ).json()["account"]
            assert kept["dashboard_preferences"]["birth_date"] == "2026-05-01"

        cleared = client.patch(
            f"/api/accounts/{owned['id']}",
            json={"dashboard_preferences": {"birth_date": None}},
        ).json()["account"]
        assert cleared["dashboard_preferences"].get("birth_date") is None


@pytest.mark.asyncio
async def test_readiness_report_fires_once_and_summarizes_day(tmp_path):
    from app.poller import Poller

    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    account = await store.create_account(email="prep@example.test")

    # Pin "local" time to roughly noon so the report window never straddles
    # midnight regardless of when the test runs.
    now_utc = datetime.now(timezone.utc)
    offset = max(-840, min(840, 12 * 60 - (now_utc.hour * 60 + now_utc.minute)))
    local_now = now_utc.astimezone(timezone(timedelta(minutes=offset)))
    report_time = (local_now - timedelta(minutes=30)).strftime("%H:%M")
    await store.update_account_preferences(
        account["id"],
        dashboard_preferences={
            "readiness_report_time": report_time,
            "tz_offset_minutes": offset,
        },
    )

    def reading(state, minutes_ago):
        ts = (now_utc - timedelta(minutes=minutes_ago)).isoformat()
        return normalize_reading(
            {"heart_rate": 120, "oxygen_saturation": 96, "battery": 80,
             "sleep_state": state, "last_updated": ts},
            "AC123",
        )

    # 40m awake, a 20m nap, 10m awake — all within today's day window.
    for minutes_ago in range(70, 30, -1):
        await store.insert_reading(reading(1, minutes_ago), account_id=account["id"])
    for minutes_ago in range(30, 10, -1):
        await store.insert_reading(reading(8, minutes_ago), account_id=account["id"])
    for minutes_ago in range(10, 0, -1):
        await store.insert_reading(reading(1, minutes_ago), account_id=account["id"])
    await store.create_care_event(
        account_id=account["id"], at=now_utc - timedelta(minutes=45), kind="Feeding"
    )

    poller = Poller(store, read_once=None, account_id=account["id"])
    await poller._maybe_send_readiness()
    await poller._maybe_send_readiness()   # same scheduled moment -> no repeat
    poller._last_readiness_key = None      # a restart mustn't re-fire either
    await poller._maybe_send_readiness()

    notifications = await store.get_notifications(hours=None, account_ids=[account["id"]])
    reports = [n for n in notifications["items"] if n["event_type"] == "night_readiness"]
    assert len(reports) == 1
    assert reports[0]["severity"] == "info"
    assert "awake today" in reports[0]["title"]
    assert "1 nap" in reports[0]["message"]
    assert "1 feed logged" in reports[0]["message"]


@pytest.mark.asyncio
async def test_reading_limit_keeps_newest_rows(tmp_path):
    """A window holding more rows than the limit must drop the OLDEST, not
    today — an ASC limit zeroed every rollup widget on 5s-cadence instances."""
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    account_id = await _default_account_id(store)
    for minute in range(10):
        await _seed_reading(store, f"2026-07-02T01:{minute:02d}:00Z", hr=100 + minute, spo2=95)

    analysis = await store.get_analysis_readings(hours=24, limit=4, account_ids=[account_id])
    assert [r.heart_rate for r in analysis] == [106, 107, 108, 109]   # newest 4, ascending

    full = await store.get_readings(hours=24, limit=4, account_ids=[account_id])
    assert [r.heart_rate for r in full] == [106, 107, 108, 109]


@pytest.mark.asyncio
async def test_readings_window_returns_precise_slice(tmp_path):
    db_path = tmp_path / "owlet.sqlite3"
    store = ReadingStore(db_path)
    await store.init()
    for minute in range(0, 60, 5):
        await _seed_reading(store, f"2026-07-02T01:{minute:02d}:00Z", hr=120 + minute, spo2=95)
    auth = AuthStore(db_path)
    user, session = await make_user(auth, "owner@example.test")
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        rows = client.get("/api/readings/window?around=2026-07-02T01:30:00Z&span=20").json()
        bad = client.get("/api/readings/window?around=not-a-time&span=20")

    times = [row["recorded_at"] for row in rows]
    assert times == [
        "2026-07-02T01:20:00Z",
        "2026-07-02T01:25:00Z",
        "2026-07-02T01:30:00Z",
        "2026-07-02T01:35:00Z",
    ]
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_o2_thresholds_resolve_with_defaults_and_stay_ordered():
    from app.analytics import DEFAULT_O2_CRITICAL, DEFAULT_O2_WARN, resolve_o2_thresholds

    defaults = (DEFAULT_O2_WARN, DEFAULT_O2_CRITICAL)
    assert resolve_o2_thresholds(None) == defaults
    assert resolve_o2_thresholds({}) == defaults
    assert resolve_o2_thresholds({"o2_warn_threshold": "nonsense"}) == defaults
    assert resolve_o2_thresholds({"o2_warn_threshold": 200}) == defaults  # out of range
    # honoured when sane
    assert resolve_o2_thresholds({"o2_warn_threshold": 94, "o2_critical_threshold": 88}) == (94, 88)
    # an inverted pair must never paint every reading red
    assert resolve_o2_thresholds({"o2_warn_threshold": 90, "o2_critical_threshold": 93}) == (90, 89)


@pytest.mark.asyncio
async def test_o2_threshold_prefs_round_trip_and_retint_rollups(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, "owner@example.test")
    account = await store.create_account(email="sock@x.y", user_id=user["id"])
    # one reading at 91%: fine under the default 90 bound, "low" once warn moves to 93
    await _seed_reading(store, "2026-07-02T01:00:00Z", spo2=91, account_id=account["id"])
    await _seed_reading(store, "2026-07-02T01:05:00Z", spo2=91, account_id=account["id"])
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)

    with client_for(app, session) as client:
        before = client.get("/api/rollups?bucket=hour").json()["rollups"]
        assert sum(r["low_oxygen_seconds"] for r in before) == 0

        saved = client.patch(
            f"/api/accounts/{account['id']}",
            json={"dashboard_preferences": {"o2_warn_threshold": 93, "o2_critical_threshold": 88}},
        ).json()["account"]["dashboard_preferences"]
        assert saved["o2_warn_threshold"] == 93 and saved["o2_critical_threshold"] == 88

        # the same readings now count as low, and the cache must not serve stale counts
        after = client.get("/api/rollups?bucket=hour").json()["rollups"]
        assert sum(r["low_oxygen_seconds"] for r in after) > 0

        # out-of-range values are rejected rather than stored
        rejected = client.patch(
            f"/api/accounts/{account['id']}",
            json={"dashboard_preferences": {"o2_warn_threshold": 400}},
        ).json()["account"]["dashboard_preferences"]
        assert rejected["o2_warn_threshold"] == 93


@pytest.mark.asyncio
async def test_poller_stop_is_bounded_when_read_hangs():
    """Logging out / unlinking calls poller.stop(); if the poll loop is mid-flight
    on a wedged Owlet request, stop() must still return promptly rather than hang
    the HTTP request behind it (which stranded a real user on 'Logging out…')."""
    import asyncio
    import time

    from app.poller import Poller

    started = asyncio.Event()

    async def slow_read():
        started.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                await asyncio.sleep(3.0)
            except asyncio.CancelledError:
                if time.monotonic() >= deadline:
                    raise
        raise asyncio.CancelledError

    class _Store:
        async def insert_reading(self, *a, **k):
            return None

    poller = Poller(store=_Store(), read_once=slow_read, interval_seconds=5, account_id=1)
    poller.start()
    await started.wait()

    began = time.monotonic()
    await poller.stop(drain_timeout=1.0)
    elapsed = time.monotonic() - began
    assert elapsed < 1.6, f"stop() blocked {elapsed:.2f}s — not bounded"

    # let the shielded task unwind so it doesn't leak into other tests
    await asyncio.sleep(2.5)


# ---- /api/widget-series ----------------------------------------------------------


def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _series_setup(tmp_path, email="owner@example.test"):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    user, session = await make_user(auth, email)
    app = create_app(store=store, settings=_test_settings(), start_poller=False, auth_store=auth)
    return store, auth, user, session, app


@pytest.mark.asyncio
async def test_widget_series_requires_login(tmp_path):
    *_, app = await _series_setup(tmp_path)
    with client_for(app) as client:
        assert client.get("/api/widget-series").status_code == 401


@pytest.mark.asyncio
async def test_widget_series_window_order_format_and_skips(tmp_path):
    store, _, user, session, app = await _series_setup(tmp_path)
    account = await store.create_account(email="sock@x.y", user_id=user["id"])
    aid = account["id"]
    await _seed_reading(store, _ago(minutes=20), spo2=91, account_id=aid)  # outside window
    await _seed_reading(store, _ago(minutes=8), spo2=95, account_id=aid)
    await _seed_reading(store, _ago(minutes=6), spo2=0, account_id=aid)  # sock off
    await _seed_reading(store, _ago(minutes=4), spo2=None, account_id=aid)
    await _seed_reading(store, _ago(minutes=2), spo2=97, account_id=aid)
    await _seed_reading(store, _ago(minutes=3), spo2=96, account_id=aid, device_serial="OTHER")

    with client_for(app, session) as client:
        body = client.get("/api/widget-series?minutes=10").json()
    assert body["device_serial"] == "AC123"
    assert [p["spo2"] for p in body["points"]] == [95, 97]
    assert all(isinstance(p["spo2"], int) for p in body["points"])
    times = [p["t"] for p in body["points"]]
    assert times == sorted(times)
    assert all(t.endswith("Z") and len(t) == 20 for t in times)
    assert set(body) == {"device_serial", "warn_threshold", "critical_threshold", "points", "sleep"}


@pytest.mark.asyncio
async def test_widget_series_minutes_default_and_clamping(tmp_path):
    store, _, user, session, app = await _series_setup(tmp_path)
    account = await store.create_account(email="sock@x.y", user_id=user["id"])
    for m in (0.5, 5, 30, 59, 90):
        await _seed_reading(store, _ago(minutes=m), account_id=account["id"], spo2=95)

    def count(client, query):
        return len(client.get(f"/api/widget-series{query}").json()["points"])

    with client_for(app, session) as client:
        assert count(client, "") == 2  # default 10 minutes
        assert count(client, "?minutes=0") == 1  # clamped to 1
        assert count(client, "?minutes=-5") == 1
        assert count(client, "?minutes=500") == 4  # clamped to 60
        assert client.get("/api/widget-series?minutes=abc").status_code == 422


@pytest.mark.asyncio
async def test_widget_series_prefers_alerting_device_then_most_recent(tmp_path):
    import time

    from app.alerts import ALERTS
    from app.models import OwletReading

    store, _, user, session, app = await _series_setup(tmp_path)
    account = await store.create_account(email="sock@x.y", user_id=user["id"])
    aid = account["id"]
    await _seed_reading(store, _ago(minutes=5), spo2=80, device_serial="ALERTING", account_id=aid)
    await _seed_reading(store, _ago(minutes=1), spo2=97, device_serial="FRESH", account_id=aid)

    ALERTS.reset()
    try:
        with client_for(app, session) as client:
            # Nothing alerting: the most recently reporting device.
            assert client.get("/api/widget-series").json()["device_serial"] == "FRESH"

            now = datetime.now(timezone.utc)
            for i in range(3):
                ALERTS.observe(
                    aid, "Baby",
                    OwletReading(device_serial="ALERTING", recorded_at=now + timedelta(seconds=i),
                                 oxygen_saturation=80, heart_rate=130),
                    90, 86, time.time(),
                )
            before = client.get("/api/alert-state").json()
            assert before["level"] == "red"
            body = client.get("/api/widget-series").json()
            after = client.get("/api/alert-state").json()
        assert body["device_serial"] == "ALERTING"
        assert [p["spo2"] for p in body["points"]] == [80]
        # read-only: no snooze / ack side effects
        assert (before["snoozed"], before["acknowledged"]) == (False, False)
        assert (after["snoozed"], after["acknowledged"]) == (False, False)
    finally:
        ALERTS.reset()


@pytest.mark.asyncio
async def test_widget_series_ignores_test_alert_device(tmp_path):
    from app.alerts import ALERTS

    store, _, user, session, app = await _series_setup(tmp_path)
    account = await store.create_account(email="sock@x.y", user_id=user["id"])
    await _seed_reading(store, _ago(minutes=1), spo2=97, account_id=account["id"])
    ALERTS.reset()
    try:
        with client_for(app, session) as client:
            assert client.post("/api/alert/test", json={"level": "red", "seconds": 60}).status_code == 200
            body = client.get("/api/widget-series").json()
        assert body["device_serial"] == "AC123"
    finally:
        ALERTS.reset()


@pytest.mark.asyncio
async def test_widget_series_thresholds_come_from_chosen_account(tmp_path):
    store, _, user, session, app = await _series_setup(tmp_path)
    a = await store.create_account(email="a@x.y", user_id=user["id"])
    b = await store.create_account(email="b@x.y", user_id=user["id"])
    await _seed_reading(store, _ago(minutes=5), spo2=97, device_serial="DEVA", account_id=a["id"])
    await _seed_reading(store, _ago(minutes=1), spo2=97, device_serial="DEVB", account_id=b["id"])

    with client_for(app, session) as client:
        client.patch(
            f"/api/accounts/{a['id']}",
            json={"dashboard_preferences": {"o2_warn_threshold": 94, "o2_critical_threshold": 88}},
        )
        client.patch(
            f"/api/accounts/{b['id']}",
            json={"dashboard_preferences": {"o2_warn_threshold": 92, "o2_critical_threshold": 85}},
        )
        body = client.get("/api/widget-series").json()
    assert body["device_serial"] == "DEVB"
    assert (body["warn_threshold"], body["critical_threshold"]) == (92, 85)


@pytest.mark.asyncio
async def test_widget_series_no_readings_returns_defaults(tmp_path):
    from app.analytics import DEFAULT_O2_CRITICAL, DEFAULT_O2_WARN

    store, _, user, session, app = await _series_setup(tmp_path)
    await store.create_account(email="sock@x.y", user_id=user["id"])
    with client_for(app, session) as client:
        body = client.get("/api/widget-series").json()
    assert body == {
        "device_serial": None,
        "warn_threshold": DEFAULT_O2_WARN,
        "critical_threshold": DEFAULT_O2_CRITICAL,
        "points": [],
        "sleep": None,
    }


@pytest.mark.asyncio
async def test_widget_series_is_user_scoped(tmp_path):
    store, auth, alice, alice_session, app = await _series_setup(tmp_path, "alice@example.test")
    bob, bob_session = await make_user(auth, "bob@example.test")
    alice_acct = await store.create_account(email="a@x.y", user_id=alice["id"])
    bob_acct = await store.create_account(email="b@x.y", user_id=bob["id"])
    await _seed_reading(store, _ago(minutes=1), spo2=91, device_serial="ALICE1", account_id=alice_acct["id"])
    await _seed_reading(store, _ago(minutes=1), spo2=99, device_serial="BOB1", account_id=bob_acct["id"])

    with client_for(app, alice_session) as a, client_for(app, bob_session) as b:
        sa = a.get("/api/widget-series").json()
        sb = b.get("/api/widget-series").json()
    assert (sa["device_serial"], [p["spo2"] for p in sa["points"]]) == ("ALICE1", [91])
    assert (sb["device_serial"], [p["spo2"] for p in sb["points"]]) == ("BOB1", [99])


# ---- /api/widget-series: sleep timer ------------------------------------------------


async def _seed_raw(store, timestamp, account_id, device_serial="AC123", **raw):
    values = {
        "heart_rate": 120, "oxygen_saturation": 97, "sleep_state": 8,
        "last_updated": timestamp, "battery": 100,
    }
    values.update(raw)
    await store.insert_reading(normalize_reading(values, device_serial), account_id=account_id)


async def _sleep_setup(tmp_path):
    store, _, user, session, app = await _series_setup(tmp_path)
    account = await store.create_account(email="sock@x.y", user_id=user["id"])
    return store, session, app, account["id"]


def _sleep_of(app, session):
    with client_for(app, session) as client:
        return client.get("/api/widget-series?minutes=10").json()


@pytest.mark.asyncio
async def test_widget_sleep_asleep_run_starts_at_earliest_reading_of_the_run(tmp_path):
    store, session, app, aid = await _sleep_setup(tmp_path)
    await _seed_raw(store, _ago(minutes=50), aid, sleep_state=8)   # an earlier, separate run
    await _seed_raw(store, _ago(minutes=45), aid, sleep_state=1)   # awake ends it
    start = _ago(minutes=40)
    await _seed_raw(store, start, aid, sleep_state=8)
    for m, st in ((30, 15), (20, 15), (10, 8), (5, 8)):            # light and deep both count
        await _seed_raw(store, _ago(minutes=m), aid, sleep_state=st)
    await _seed_raw(store, _ago(seconds=10), aid, sleep_state="8")
    body = _sleep_of(app, session)
    assert body["sleep"] == {"state": "asleep", "since": start}
    assert body["device_serial"] == "AC123"


@pytest.mark.asyncio
async def test_widget_sleep_awake(tmp_path):
    store, session, app, aid = await _sleep_setup(tmp_path)
    await _seed_raw(store, _ago(minutes=30), aid, sleep_state=15)
    start = _ago(minutes=12)
    await _seed_raw(store, start, aid, sleep_state=1)
    await _seed_raw(store, _ago(minutes=6), aid, sleep_state=1)
    await _seed_raw(store, _ago(seconds=5), aid, sleep_state=1)
    assert _sleep_of(app, session)["sleep"] == {"state": "awake", "since": start}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [0, None, 3])
async def test_widget_sleep_unknown_state_is_null(tmp_path, state):
    store, session, app, aid = await _sleep_setup(tmp_path)
    await _seed_raw(store, _ago(minutes=5), aid, sleep_state=8)
    await _seed_raw(store, _ago(seconds=5), aid, sleep_state=state)
    body = _sleep_of(app, session)
    assert body["sleep"] is None
    assert body["device_serial"] == "AC123"


@pytest.mark.asyncio
async def test_widget_sleep_stale_latest_reading_is_null_using_account_timeout(tmp_path):
    store, session, app, aid = await _sleep_setup(tmp_path)
    await _seed_raw(store, _ago(minutes=20), aid, sleep_state=8)
    await _seed_raw(store, _ago(seconds=90), aid, sleep_state=8)
    body = _sleep_of(app, session)
    assert body["sleep"] is None                        # default 60 s
    assert [p["spo2"] for p in body["points"]] == [97]  # the rest is unchanged
    # A longer configured stale timeout (alert_stale_seconds) keeps it.
    with client_for(app, session) as client:
        r = client.patch(f"/api/accounts/{aid}", json={"dashboard_preferences": {"alert_stale_seconds": 120}})
        assert r.status_code == 200, r.text
    assert _sleep_of(app, session)["sleep"]["state"] == "asleep"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        {"oxygen_saturation": 0, "heart_rate": 0},   # sock off: zero vitals
        {"sock_off": 1},                             # sock-off flag with old vitals
        {"charging": 1},
    ],
)
async def test_widget_sleep_sock_off_or_charging_is_null(tmp_path, raw):
    store, session, app, aid = await _sleep_setup(tmp_path)
    await _seed_raw(store, _ago(minutes=10), aid, sleep_state=8)
    await _seed_raw(store, _ago(seconds=5), aid, sleep_state=8, **raw)
    assert _sleep_of(app, session)["sleep"] is None


@pytest.mark.asyncio
async def test_widget_sleep_run_ends_at_a_charging_row_or_a_long_gap(tmp_path):
    store, session, app, aid = await _sleep_setup(tmp_path)
    await _seed_raw(store, _ago(minutes=60), aid, sleep_state=8)
    await _seed_raw(store, _ago(minutes=55), aid, sleep_state=8, charging=1)
    after_charge = _ago(minutes=50)
    await _seed_raw(store, after_charge, aid, sleep_state=8)
    await _seed_raw(store, _ago(minutes=42), aid, sleep_state=8)
    newest = _ago(seconds=5)
    await _seed_raw(store, newest, aid, sleep_state=8)   # 42 min of silence before it
    assert _sleep_of(app, session)["sleep"] == {"state": "asleep", "since": newest}
    for m in (33, 24, 15, 6):                            # fill the gap: one run again
        await _seed_raw(store, _ago(minutes=m), aid, sleep_state=8)
    assert _sleep_of(app, session)["sleep"] == {"state": "asleep", "since": after_charge}


@pytest.mark.asyncio
async def test_widget_sleep_is_bounded_to_16_hours(tmp_path):
    store, session, app, aid = await _sleep_setup(tmp_path)
    for m in range(17 * 60, 0, -8):          # asleep every 8 min for 17 h
        await _seed_raw(store, _ago(minutes=m), aid, sleep_state=15)
    await _seed_raw(store, _ago(seconds=5), aid, sleep_state=15)
    before = datetime.now(timezone.utc) - timedelta(hours=16)
    sleep = _sleep_of(app, session)["sleep"]
    after = datetime.now(timezone.utc) - timedelta(hours=16)
    since = datetime.fromisoformat(sleep["since"].replace("Z", "+00:00"))
    assert sleep["state"] == "asleep"
    assert before - timedelta(seconds=1) <= since <= after


@pytest.mark.asyncio
async def test_widget_sleep_run_just_inside_16_hours_is_not_clipped(tmp_path):
    store, session, app, aid = await _sleep_setup(tmp_path)
    await _seed_raw(store, _ago(hours=16, minutes=5), aid, sleep_state=1)
    start = _ago(hours=15, minutes=58)
    await _seed_raw(store, start, aid, sleep_state=8)
    for m in range(15 * 60 + 50, 0, -8):
        await _seed_raw(store, _ago(minutes=m), aid, sleep_state=8)
    await _seed_raw(store, _ago(seconds=5), aid, sleep_state=8)
    assert _sleep_of(app, session)["sleep"] == {"state": "asleep", "since": start}


@pytest.mark.asyncio
async def test_widget_sleep_ignores_the_test_alert_device(tmp_path):
    from app.alerts import ALERTS

    store, session, app, aid = await _sleep_setup(tmp_path)
    start = _ago(minutes=30)
    await _seed_raw(store, start, aid, sleep_state=8)
    for m in (22, 14, 6):
        await _seed_raw(store, _ago(minutes=m), aid, sleep_state=8)
    await _seed_raw(store, _ago(seconds=5), aid, sleep_state=8)
    ALERTS.reset()
    try:
        with client_for(app, session) as client:
            assert client.post("/api/alert/test", json={"level": "red", "seconds": 60}).status_code == 200
            body = client.get("/api/widget-series").json()
            state = client.get("/api/alert-state").json()
        assert body["device_serial"] == "AC123"
        assert body["sleep"] == {"state": "asleep", "since": start}
        assert state["active_level"] == "red"          # the test alert is untouched
    finally:
        ALERTS.reset()


@pytest.mark.asyncio
async def test_widget_sleep_is_read_only_for_alert_state(tmp_path):
    import time

    from app.alerts import ALERTS
    from app.models import OwletReading

    store, session, app, aid = await _sleep_setup(tmp_path)
    await _seed_raw(store, _ago(minutes=5), aid, sleep_state=8, oxygen_saturation=80)
    await _seed_raw(store, _ago(seconds=5), aid, sleep_state=8, oxygen_saturation=80)
    ALERTS.reset()
    try:
        now = datetime.now(timezone.utc)
        for i in range(3):
            ALERTS.observe(
                aid, "Baby",
                OwletReading(device_serial="AC123", recorded_at=now + timedelta(seconds=i),
                             oxygen_saturation=80, heart_rate=130, sleep_state="8"),
                90, 86, time.time(),
            )
        with client_for(app, session) as client:
            before = client.get("/api/alert-state").json()
            body = client.get("/api/widget-series").json()
            after = client.get("/api/alert-state").json()
        assert body["sleep"]["state"] == "asleep"
        for key in ("level", "active_level", "snoozed", "acknowledged", "awake_event_id"):
            assert before[key] == after[key], key
        assert after["active_level"] == "red"
    finally:
        ALERTS.reset()


@pytest.mark.asyncio
async def test_widget_sleep_internal_error_is_null_with_the_rest_intact(tmp_path):
    store, session, app, aid = await _sleep_setup(tmp_path)
    await _seed_raw(store, _ago(minutes=3), aid, sleep_state=8, oxygen_saturation=95)
    await _seed_raw(store, _ago(seconds=5), aid, sleep_state=8, oxygen_saturation=96)
    healthy = _sleep_of(app, session)
    assert healthy["sleep"]["state"] == "asleep"

    async def broken(**_kwargs):
        raise RuntimeError("disk on fire")

    store.get_sleep_state_history = broken
    body = _sleep_of(app, session)
    assert body["sleep"] is None
    assert {k: v for k, v in body.items() if k != "sleep"} == {
        k: v for k, v in healthy.items() if k != "sleep"
    }
    assert [p["spo2"] for p in body["points"]] == [95, 96]
