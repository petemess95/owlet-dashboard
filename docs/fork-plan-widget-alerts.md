# Fork plan: always-on-top widget + monitor-flash oxygen alerts

Status: **draft for review**. Nothing in this document is implemented yet.

## 1. Goals

1. **Widget.** A small always-on-top window shows live SpO₂, heart rate and data freshness while you're at the computer. The full dashboard can stay hidden.
2. **Amber alert.** When SpO₂ drops below the account's **"Oxygen amber below"** setting (`o2_warn_threshold`, default 90), the non-primary monitor flashes orange.
3. **Red alert.** When SpO₂ drops below **"Oxygen red below"** (`o2_critical_threshold`, default 86), that monitor flashes red and an alarm sound loops.

## 2. Decisions

| Topic | Decision | Source |
|---|---|---|
| Closing the main window | Hides it to the system tray. Data collection, the widget and alerts keep running. Quitting happens only from the tray menu. | You |
| Snooze | Clicking the flashing monitor, the widget's Snooze button, or the tray's "Snooze alert" silences the flash and sound for **15 seconds**. If SpO₂ is still low after that, the alert comes back. | You |
| Escalation during snooze | If the alert goes from amber to red while snoozed, the snooze is cancelled and red fires immediately. | Judgment |
| Owlet's own alarm flags | `CRIT_OX_ALRT` forces red and `LOW_OX_ALRT` forces amber, both immediately. This is a backstop in case your thresholds are set lower than Owlet's. | Judgment |
| Existing single-threshold low-O₂ toast (`o2_alert_threshold`) | Kept unchanged and independent. It's opt-in, and the toast plus its notification history are still useful. | Judgment |
| Sock off, charging, or offline (SpO₂ reported as 0) | Never triggers amber or red. The widget shows "Sock off" or "Charging". | Judgment |
| No fresh data (stale) | Clears any flash. The widget turns grey and shows "No data for 2m". The Owlet base station still sounds its own alarms in that case. | Judgment |
| Only one monitor connected | The flash becomes a 40 px border frame around the primary monitor that clicks pass through, so it doesn't block work. Snooze from the widget or tray. | Judgment |
| Three or more monitors | Flash the first non-primary monitor, ordered by position from left to right. | Judgment |
| Remote-server mode ("Switch server…") | Alerts only work while this PC does the collecting. In remote mode the widget says "Alerts unavailable — viewing a remote server". | Judgment |

## 3. Architecture

```
 Owlet cloud ──5 s poll──▶ Python sidecar (127.0.0.1:8877)
                             ├─ Poller._run → AlertMonitor.observe(reading)   [new: app/alerts.py]
                             ├─ GET  /api/alert-state                          [new]
                             ├─ POST /api/alert/snooze                         [new]
                             ├─ POST /api/alert/test                           [new]
                             └─ GET  /widget  (HTML page)                      [new: app/widget_page.py]
                                   ▲
 Tauri shell (Rust)                │
  ├─ alert thread: GET /api/alert-state every 2 s ──▶ show/hide overlay, start/stop sound, tray tooltip
  ├─ "main" window   (dashboard; close = hide to tray)
  ├─ "widget" window (always on top, frameless, loads /widget, polls /api/alert-state itself)
  ├─ "overlay" window(fullscreen on the non-primary monitor, local overlay.html, click = snooze)
  └─ tray icon + menu
```

Why it's split this way:

- **The Python server decides the alert level.**
  - It already has every reading, the thresholds, and the offline logic.
  - It can be unit-tested with pytest.
  - It's the single source of truth for snooze, so the widget, overlay and tray always agree.
- **Rust drives the overlay and the sound, not a webview.**
  - WebView2 throttles timers in hidden windows, and the main window will usually be hidden.
  - WebView2 blocks autoplay audio.
  - A native thread has neither problem.
  - It also keeps working if you hide the widget.
- **No authentication is needed.** In desktop mode every request to 127.0.0.1 is treated as the local admin (`auth_routes.current_user`), so the Rust thread can call `/api/alert-state` without credentials.
- **The overlay page is a local app asset** (`ui-placeholder/overlay.html`), not served by Python. It only has to flash a colour and catch a click, and the Rust thread pushes the state to it.

## 4. API contract

Python (task A), the widget page (task B) and Rust (task C) are built in parallel against this contract, so it's fixed here.

### `GET /api/alert-state`

Uses `require_user`. Results are scoped to the user's accounts with the existing `_scope(user, None)` helper.

```json
{
  "generated_at": "2026-11-06T20:15:03Z",
  "collecting": true,
  "level": "red",
  "active_level": "none",
  "snoozed": true,
  "snoozed_until": "2026-11-06T20:15:15Z",
  "snooze_seconds_left": 12,
  "snooze_seconds": 15,
  "test": false,
  "devices": [
    {
      "account_id": 1,
      "account_name": "Owlet account",
      "device_serial": "AC000W0123",
      "level": "red",
      "reason": "below_threshold",
      "spo2": 84,
      "heart_rate": 131,
      "recorded_at": "2026-11-06T20:15:01Z",
      "age_seconds": 2,
      "stale": false,
      "sock_reporting": true,
      "charging": false,
      "warn_threshold": 90,
      "critical_threshold": 86
    }
  ]
}
```

Field meanings:

- `level`: the worst level across non-stale devices: `"none"`, `"amber"` or `"red"`.
- `active_level`: what should be shown right now. It equals `level`, except it's `"none"` while snoozed.
- `collecting`: false when no poller is running, for example in remote mode.
- `snoozed_until`: null when not snoozed.
- `test`: true while a test alert from `/api/alert/test` is active.
- `devices[].reason`: one of `below_threshold`, `owlet_flag`, `recovering` (level held while hysteresis waits out the exit readings), `signal_lost`, `test`, or null.
- `devices[].spo2`: the last valid value, or null.
- `devices[].sock_reporting`: false when the sock is off or disconnected.
- `devices[]` is empty until the first reading arrives.

### `POST /api/alert/snooze`

- No body.
- Response: `{"snoozed_until": "<iso>", "snooze_seconds": 15}`.
- If nothing is alerting, it still returns 200, but `snoozed_until` is null and no snooze is stored. A pre-emptive snooze must not hide the next real alert.

### `POST /api/alert/test`

- Body: `{"level": "amber" | "red" | "none", "seconds": 20}`. `seconds` is optional, defaults to 20 and is clamped to 5–120.
- It adds a synthetic device with serial `TEST` at that level for that many seconds, so the overlay and sound can be tested without a real desaturation.
- `"none"` cancels a running test.
- Response: same body as `GET /api/alert-state`.

### `GET /widget`

The HTML page for the widget window (section 6).

## 5. Alert engine (Python): `app/alerts.py`

### `DeviceAlertTracker`

There's one per `(account_id, device_serial)`. It holds a pure state machine with no I/O, and every method takes `now` so tests can control time.

Constants, defined at module top so they're easy to tune:

| Constant | Value | Meaning |
|---|---|---|
| `ENTER_READINGS` | 2 | Consecutive distinct readings needed to enter a level |
| `EXIT_READINGS` | 2 | Consecutive distinct readings needed to leave a level |
| `HYSTERESIS` | 1 | To leave a level, SpO₂ must reach threshold + 1 |
| `STALE_SECONDS` | 60 | Seconds without a new reading before the device counts as stale |
| `SNOOZE_SECONDS` | 15 | Snooze length |

`observe(reading, warn, critical, now)`:

1. **Ignore repeats.** If `reading.recorded_at` equals the last one seen, ignore it. The poller fetches every 5 s, but Owlet's `last_updated` timestamp changes more slowly, so the same sample would otherwise count twice toward debounce.
2. **Handle offline readings.** If `is_offline_reading(reading)` is true or SpO₂ is None:
   - set `sock_reporting=False` and `charging=reading.charging`
   - set the level to `"none"`
   - reset all counters
   - return
3. **Compute this reading's raw tier.**
   - `red` if SpO₂ < `critical`, or Owlet's `CRIT_OX_ALRT` / `critical_oxygen_alert` flag is active.
   - Otherwise `amber` if SpO₂ < `warn`, or Owlet's `LOW_OX_ALRT` / `low_oxygen_alert` flag is active.
   - Otherwise `none`.
   - Read the flags with the existing `models.raw_flag_active`.
4. **Escalate.**
   - An Owlet flag sets its level immediately, with `reason="owlet_flag"`.
   - Otherwise the level escalates to X after `ENTER_READINGS` consecutive readings whose raw tier is at least X. A red reading also counts toward amber, so amber, red reads amber then red.
5. **De-escalate.**
   - Red drops to amber or none after `EXIT_READINGS` consecutive readings with SpO₂ ≥ `critical + HYSTERESIS` and no crit flag.
   - Amber drops to none after `EXIT_READINGS` readings with SpO₂ ≥ `warn + HYSTERESIS` and no low flag.
   - A reading exactly at the threshold, like 90 with warn=90, holds the current level. That stops flicker.
6. **Record the latest values.** Store `spo2`, `heart_rate`, `recorded_at` and `last_new_reading_wall = now`.

Stale check: `stale = now - last_new_reading_wall > STALE_SECONDS`. A stale device reports level `"none"` and is left out of the overall level.

### `AlertMonitor`

A module-level singleton, `ALERTS = AlertMonitor()`, with a `reset()` for tests. It holds:

- the trackers, keyed by `(account_id, serial)`, plus the account display names
- the snooze: `snoozed_until` and `snoozed_level`
- the test alert: `level` and `until`

Methods:

- `observe(account_id, account_name, reading, warn, critical, now)` forwards the reading to the right tracker.
- `snooze(now)`:
  - If the current `level` is `"none"`, return None.
  - Otherwise set `snoozed_until = now + 15 s` and remember `snoozed_level = level`.
- `set_test(level, seconds, now)` starts or cancels a test alert.
- `state(account_ids, collecting, now)` builds the contract dict. While building it:
  - It cancels the snooze if `level` is now higher than `snoozed_level`, so escalation breaks through.
  - It clears an expired snooze.

`collecting` comes from `bool(app.state.owlet_state.get("pollers"))`.

### Changes to `app/poller.py`

- In `_run`, after `insert_reading`, call a new `_feed_alerts(reading)`.
- That method:
  - loads the account once, the same way `_check_custom_alert` does
  - gets thresholds with `resolve_o2_thresholds(prefs)`
  - calls `ALERTS.observe(...)` using wall-clock `time.time()`, or a monotonic clock converted consistently
- Wrap it in its own try/except. An alert bug must never break data collection.
- Leave `_check_custom_alert` alone.

### Changes to `app/main.py`

- Add the three `/api/alert*` routes and `GET /widget`. The widget route returns `HTMLResponse(widget_page.render())`.
- Add a pydantic body model for `/api/alert/test`.

### Tests: `tests/test_alerts.py` (new)

Unit tests of the state machine, plus API tests in the style of `tests/test_api.py`. Cases:

- Debounce: one low reading doesn't alert, two do.
- Repeated `recorded_at` doesn't count twice.
- Amber, then red, then back down to amber and none, including hysteresis: a reading exactly at the threshold holds the level.
- An Owlet `CRIT_OX_ALRT` flag goes red immediately.
- An offline or zero reading clears the alert and never triggers it.
- After 60 s with no new reading the device is stale, has level none and is left out of the overall level.
- Snooze:
  - makes `active_level` none for 15 s, and the alert comes back after
  - is a no-op when nothing is alerting
  - is cancelled when amber escalates to red
- Thresholds come from `dashboard_preferences`, and a critical value ≥ warn is clamped.
- Test alerts: they set the level and expire, and `"none"` cancels them.
- API: the shape of `/api/alert-state` matches section 4, `/api/alert/snooze` and `/api/alert/test` work, and `/widget` returns 200 HTML.

## 6. Widget page: `app/widget_page.py`

`render() -> str` returns a self-contained HTML document with inline CSS and JS and no external assets. It's styled to match the dark theme of `now_page.py`.

**Size:** about 300 × 120 CSS px.

**Layout** for one device:
- Large SpO₂ number with a `%` sign, coloured green, amber or red by that device's own `warn_threshold` and `critical_threshold`. The colour is the instant tier, so it shows dips before the debounced alert fires.
- Heart rate in bpm, smaller.
- A status line, with the first match winning:
  - "Alerts unavailable — viewing a remote server" when `!collecting`
  - "Waiting for data" when there are no devices
  - "No data for 2m" (grey) when stale
  - "Sock off" or "Charging" when `!sock_reporting`
  - "Snoozed 12s" when snoozed
  - otherwise "Updated 4s ago"
- Each device beyond the first gets a compact extra row: name, SpO₂ and heart rate.

**Alert styling:** when `active_level` is amber or red, the widget border pulses in that colour and a large **Snooze 15 s** button appears.

**Controls**, in the top-right corner:
- ⤢ opens the dashboard by invoking `show_main`.
- × hides the widget by invoking `hide_widget`. It can be re-shown from the tray.

**Dragging:** the whole body has `data-tauri-drag-region` except the buttons, so the frameless window can be moved.

**Data:**
- It polls `GET /api/alert-state` every 2 s.
- Snooze is `POST /api/alert/snooze`, then an immediate refresh.
- The "Updated Xs ago" text ticks every second on the client.
- When a fetch fails, it shows "Dashboard server not responding" in grey.

**Tauri API:** use `window.__TAURI__.core.invoke`. `withGlobalTauri` is already true. When `__TAURI__` is missing (opened in a normal browser), the page still works and the ⤢ and × buttons are hidden.

## 7. Desktop shell (Rust): `desktop/src-tauri`

### Cargo and config

- `Cargo.toml`:
  - Add the `tray-icon` feature to `tauri`.
  - Add `ureq = { version = "2", default-features = false, features = ["json"] }`. It's blocking HTTP for localhost only, so TLS isn't needed.
  - Add the `"Win32_Media_Audio"` feature to the `windows` crate, alongside its existing features.
  - Add `tauri-plugin-window-state = "2"`.
- `build.rs`: add `snooze_alert`, `show_main` and `hide_widget` to `AppManifest::commands`.
- `capabilities/default.json`: set `"windows": ["main", "widget"]` and add `allow-snooze-alert`, `allow-show-main` and `allow-hide-widget`.
- `capabilities/overlay.json` (new):
  - windows: `["overlay"]`
  - no `remote` block, because it's a local asset
  - permissions: `core:default` (event listen) and `allow-snooze-alert`
- `assets/alarm.wav`: produced by task D and embedded with `include_bytes!`.
- `ui-placeholder/overlay.html` (new). See below.

### `src/main.rs` changes

1. **Menu only on the main window.**
   - Today `Builder::menu` sets the app-wide menu, which on Windows would put a menu bar on the widget too.
   - Build the same "Instance" menu, but attach it with `WebviewWindowBuilder::menu(...)` on "main" only.
   - Keep the existing `on_menu_event` handler working.
2. **Window state.**
   - Register `tauri_plugin_window_state::Builder::default()`.
   - Use `.with_denylist(&["overlay"])` and state flags `POSITION | SIZE | MAXIMIZED`. Don't include `VISIBLE`.
   - The widget then reopens where you left it.
3. **Hide to tray.** In `on_window_event`:
   - When "main" or "widget" gets `CloseRequested`, call `api.prevent_close()` and `window.hide()`. This also catches Alt+F4 on the widget.
   - Remove the `Destroyed` handler that kills the sidecar. `RunEvent::Exit` already covers shutdown, and the per-window `Destroyed` handler would kill the server whenever any window closed.
4. **Tray.** Use `TrayIconBuilder` with the default window icon, tooltip "Owlet Dashboard", and this menu:
   - Show dashboard
   - Show widget / Hide widget (one toggle item; keep its text in sync)
   - Snooze alert (15 s)
   - Test alert ▸ Amber (20 s) / Red (20 s) / Stop test
   - separator
   - Quit Owlet Dashboard (`app.exit(0)`, which runs the existing Exit handler that kills the sidecar)

   Left-clicking the tray icon shows and focuses the dashboard. The alert thread updates the tooltip to e.g. "Owlet — SpO₂ 97% · HR 128" or "Owlet — RED ALERT 84%".
5. **Widget window,** created in `setup` after "main":
   - label `"widget"`, URL `http://127.0.0.1:8877/widget`
   - `inner_size(300, 120)`, `decorations(false)`, `always_on_top(true)`, `skip_taskbar(true)`, `resizable(false)`, `shadow(true)`, `focused(false)`
   - Default position: bottom-right of the primary monitor's work area, 16 px margin. The window-state plugin overrides this once it has a saved position.
   - If a saved position is no longer on any connected monitor, move the widget back to the default.
6. **Overlay window,** created lazily the first time an alert shows, then reused:
   - label `"overlay"`, URL `WebviewUrl::App("overlay.html")`
   - `decorations(false)`, `transparent(true)`, `always_on_top(true)`, `skip_taskbar(true)`, `resizable(false)`, `focused(false)`, `focusable(false)` (available in tauri 2.11). It must never take keyboard focus from what you're typing in.
   - Before each show:
     - Re-enumerate monitors with `available_monitors()` and `primary_monitor()`, so a monitor plugged in or out mid-session is handled.
     - Pick the target per the decisions in section 2.
     - Set position and size with **`PhysicalPosition`/`PhysicalSize`**, using the monitor's physical rect. That avoids mixed-DPI bugs.
   - Two monitors or more: cover the whole monitor and `set_ignore_cursor_events(false)`, so a click anywhere snoozes.
   - One monitor: cover the primary, emit `mode: "border"` and `set_ignore_cursor_events(true)`.
   - Push state to the page with `overlay.emit("overlay-state", {level, spo2, mode, device_name})`. Hide the window when `active_level` is `"none"`.
7. **Alert thread,** spawned in `setup`:
   - Every 2 s, `GET http://127.0.0.1:8877/api/alert-state` with a 1.5 s timeout.
   - Compare against the last applied state and only act on changes:
     - `active_level` amber: show the amber overlay and stop the sound.
     - `active_level` red: show the red overlay and start the looping sound.
     - `active_level` none: hide the overlay and stop the sound.
   - Update the tray tooltip.
   - After 3 consecutive fetch failures, hide the overlay, stop the sound and set the tooltip to "Owlet — server not responding". The widget shows the same.
   - Window operations must run on the main thread via `app.run_on_main_thread`.
8. **Sound.**
   - `PlaySoundW(PCWSTR(ALARM.as_ptr()), None, SND_MEMORY | SND_ASYNC | SND_LOOP)`, where `static ALARM: &[u8] = include_bytes!("../assets/alarm.wav")`.
   - Stop it with `PlaySoundW(PCWSTR::null(), None, SND_PURGE)`.
   - Wrap it in `start_alarm()`/`stop_alarm()` with an `AtomicBool`, so repeated starts are no-ops.
   - Caveat: it plays at the Windows system volume. If Windows is muted, there's no sound. A red alert won't unmute the system.
9. **Commands.**
   - `snooze_alert`: POSTs `/api/alert/snooze`, then immediately hides the overlay and stops the sound locally, so it doesn't wait up to 2 s for the next poll.
   - `show_main`: shows, unminimizes and focuses "main".
   - `hide_widget`: hides "widget" and updates the tray toggle text.
   - The tray menu reuses these same functions.

### `ui-placeholder/overlay.html` (new)

- Full-window `div` on a transparent body.
- Fullscreen mode: the background alternates between the alert colour at 55% opacity and transparent at 1 Hz (500 ms on, 500 ms off). That's well under the 3 Hz photosensitivity guideline.
- The centre shows large text: "SpO₂ 84%", then "Click anywhere to snooze 15 s".
- Border mode: a 40 px solid border in the alert colour, flashing the same way, with no text and `pointer-events: none`.
- Colours: amber `#ff8c00`, red `#ff1a1a`.
- It listens with `window.__TAURI__.event.listen('overlay-state', ...)`. A click calls `invoke('snooze_alert')`.

## 8. Alarm sound (task D)

- `scripts/make_alarm_wav.py` generates `desktop/src-tauri/assets/alarm.wav` using the standard library only (`wave`, `struct`, `math`).
- Format: 16-bit mono at 22,050 Hz, about 1.2 s long, so it loops cleanly.
- Pattern: three 150 ms beeps at 880 Hz with 80 ms gaps, then silence to the end. That's a classic urgent medical-alarm cadence.
- 10 ms fade in and out on each beep to avoid clicks. Peak amplitude about −3 dBFS.
- Commit both the script and the generated WAV, which is about 50 KB.

## 9. Execution plan (delegated per CLAUDE.md)

| # | Task | Worker | Files (exclusive) | Depends on |
|---|---|---|---|---|
| 0 | Commit the current baseline (updater removal) on a new branch `fork/widget-alerts` | me, with your OK | — | — |
| A | Alert engine, API routes, tests | worker-standard | `app/alerts.py` (new), `app/poller.py`, `app/main.py`, `tests/test_alerts.py` (new) | — |
| B | Widget page | worker-standard | `app/widget_page.py` (new) | contract in section 4 |
| C | Rust shell: tray, windows, overlay, alert thread, sound, capabilities | worker-complex | everything under `desktop/src-tauri/` except `assets/alarm.wav` | contract in section 4 |
| D | Alarm WAV generator | worker-mechanical | `scripts/make_alarm_wav.py`, `desktop/src-tauri/assets/alarm.wav` | — |
| E | Integration: pytest, `cargo check`, sidecar rebuild, installer build | me | — | A–D |
| F | Manual acceptance on your PC | you, with me guiding | — | E |

- A, B, C and D all touch different files, so they run in parallel.
- A's `/widget` route imports `widget_page.render`. If B isn't finished when A runs its tests, A writes a one-line stub of `render()` that B then overwrites. B owns that file.
- If a result fails its acceptance criteria, the task is resent to the next worker tier up with the failure described.

### Acceptance criteria per task

- **A:**
  - All new and existing tests pass, using `pytest --basetemp <scratch> -p no:cacheprovider` because of the sandbox temp-dir issue.
  - The `/api/alert-state` shape matches section 4 exactly.
  - Data collection keeps working if `ALERTS.observe` raises; a test covers this.
- **B:**
  - It renders standalone, opened directly at `http://127.0.0.1:8877/widget` in a browser with no Tauri.
  - Each status-line state appears correctly. Driving `/api/alert/test` shows amber and red styling and the Snooze button.
- **C:**
  - `cargo check` and `cargo clippy` are clean.
  - The app starts with the main window and the widget.
  - Closing main hides it to the tray, and "Show dashboard" brings it back.
  - Quit from the tray stops `owlet-server.exe`. Check with Task Manager or `Get-Process`.
  - Hiding the widget doesn't stop the server.
  - Test amber flashes the secondary monitor orange with no sound. Test red flashes it red with the looping sound.
  - Clicking the overlay snoozes it, and it comes back after 15 s while the test is still running.
  - The overlay never takes keyboard focus.
  - With the secondary monitor unplugged, the border mode appears on the primary monitor.
- **D:** The WAV plays in Windows Media Player, loops without clicks, and the file is under 100 KB.
- **E:**
  - The full installer builds.
  - Installing over the current version keeps your data in `%LOCALAPPDATA%\owlet-dashboard`.
  - The rebuilt `owlet-server` exe includes `alerts.py` and `widget_page.py` (check the PyInstaller hidden-imports list in `desktop/owlet-server.spec`).

## 10. Risks and caveats

- **This isn't a medical alarm.** It depends on Owlet's cloud, your internet connection, this PC being awake, and Windows audio. Keep the Owlet base station and its own alarms as the primary safety net. The widget states data age so a frozen feed is obvious.
- **Exclusive full-screen apps,** such as some games, can draw above an always-on-top window on the same monitor. The overlay is on the non-primary monitor, which helps.
- **Focus Assist / Do Not Disturb** doesn't affect the overlay or `PlaySound`. It only suppresses the existing toasts.
- **Sleep.** If the PC sleeps, nothing runs. Optionally prevent sleep while collecting with `SetThreadExecutionState`; not in this plan.
- **Alert delay.** Real-world latency is the Owlet cloud delay, plus up to 5 s of polling, plus 2 readings of debounce, plus up to 2 s for Rust to poll. Before task A starts, I'll query your existing readings database for how often `recorded_at` actually changes. If Owlet updates less often than about every 10 s, `ENTER_READINGS` drops to 1 for red so a red alert isn't delayed by a whole extra update.
- **Upstream merges.** The changes are confined to new files where possible, plus small hooks in `poller.py`, `main.py` and `main.rs`, to keep future merges from upstream manageable.

## 11. Out of scope (possible follow-ups)

- A settings UI for snooze length, which monitor to use, sound choice and volume, and stale timeout.
- Start minimized to tray, and launch at Windows login.
- Alerts for heart rate, or for the sock being off for a long time.
- A tray icon that changes colour with alert level.
