# Widget and alerts: remediation plan

**Branch:** `fork/widget-alerts`. Reviewed feature commit `6af7cc9` against baseline `960d9d9`.
**Brief:** [review-widget-alerts.md](review-widget-alerts.md). **Spec:** [fork-plan-widget-alerts.md](fork-plan-widget-alerts.md).

This file merges two independent reviews into one ordered task list. Each task stands on its own: it names the files, the defect, the fix and how to prove it. Line numbers refer to `6af7cc9` and will shift as tasks land.

## How the order was chosen

1. **A missed alert** is the worst failure, so it comes first.
2. **A false or stuck alarm** comes next.
3. **A slow or confusing alert** follows.
4. **Hosted-mode and hardening issues** come after that, because the desktop app is single-user.
5. **Polish** comes last.

| # | Pri | Task | Files | Source |
|---|---|---|---|---|
| 1 | P1 | Process alarm flags on duplicate-timestamp readings | `app/alerts.py` | Review A |
| 2 | P1 | Never alert while the sock is charging | `app/alerts.py` | Review A |
| 3 | P1 | Owlet's low flag must not delay a red alert | `app/alerts.py` | Review B |
| 4 | P1 | Serialize the alarm flag with `PlaySoundW` | `main.rs` | Both |
| 5 | P2 | Drop alert-thread results fetched before a snooze | `main.rs` | Review B |
| 6 | P2 | Widget Snooze must stop the overlay and alarm at once | `app/widget_page.py` | Review B |
| 7 | P2 | Signal lost during an alert must not look like recovery | `app/alerts.py`, `main.rs`, `app/widget_page.py`, `overlay.html` | Review B |
| 8 | P2 | Scope snooze and test alerts to the user | `app/alerts.py`, `app/main.py` | Both |
| 9 | P2 | Reject cross-origin POSTs in desktop mode | `app/main.py` | Review B |
| 10 | P3 | Keep a way to quit when the tray fails | `main.rs` | Review B |
| 11 | P3 | Steady overlay flash rhythm | `overlay.html` | Review B |
| 12 | P3 | Keep `reason` accurate after a flag clears | `app/alerts.py` | Review B |
| 13 | P3 | Widget shows the alerting device | `app/widget_page.py` | Review B |
| 14 | P3 | Don't say "remote server" when no account is linked | `app/widget_page.py`, `app/alerts.py`, `app/main.py` | Review B |
| 15 | P3 | Narrow the overlay capability | `capabilities/overlay.json` | Review B |
| 16 | P3 | Guard against a missing Owlet timestamp | `app/alerts.py` or `app/models.py` | Review B |

Review A is the earlier written review that this file replaces. Review B is the follow-up review. Both reported the alarm race (task 4) and the shared snooze (task 8).

**Scheduling.** Several tasks edit the same file, so they can't run in parallel:
- Tasks 1, 2, 3, 12 and 16 all edit `DeviceAlertTracker.observe`. Do them one after another, in that order.
- Tasks 4, 5 and 10 all edit `main.rs`. Do them one after another.
- Task 7 touches four files. Run it after tasks 1–6 have landed.

**Verification for every task.** Run the full suite with a writable temp folder, because the default pytest temp folder is blocked in this sandbox:

```bash
.venv/Scripts/python -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp
```

For Rust tasks, also run `cargo clippy` in `desktop/src-tauri` with no new warnings.

---

## Task 1 — [P1] Process alarm flags on duplicate-timestamp readings

**Files:** `app/alerts.py` (`DeviceAlertTracker.observe`, lines 87–89); `tests/test_alerts.py`.

**Problem.** A reading whose `recorded_at` matches the previous one is discarded before its alarm or offline flags are looked at. `recorded_at` comes from `REAL_TIME_VITALS`, but Owlet's alarm properties can change without a new vitals sample. Reproduced: a normal reading at 95%, then the same timestamp with `CRIT_OX_ALRT=1`, stays at `none`.

**Fix.** On a duplicate timestamp:
- Still apply the offline/sock-off check and Owlet's `CRIT`/`LOW` flags. Those can escalate immediately.
- Don't advance any streak counter.
- Don't refresh `last_new_reading_wall`. Freshness tracks new samples only.
- Don't de-escalate. Leaving a level still needs `EXIT_READINGS` distinct readings.

**Acceptance criteria.**
- A duplicate timestamp with `CRIT_OX_ALRT=1` moves the tracker to `red` (reason `owlet_flag`).
- A duplicate timestamp with `LOW_OX_ALRT=1` moves it from `none` to `amber`.
- A duplicate with a sub-threshold SpO₂ and no flag doesn't change any streak. The existing test `test_repeated_recorded_at_does_not_count_twice` still passes.
- A duplicate with `sock_off` set drops the level to `none` (see task 2).
- A duplicate doesn't change `last_new_reading_wall`.

---

## Task 2 — [P1] Never alert while the sock is charging

**Files:** `app/alerts.py` (lines 98–105); `tests/test_alerts.py`.

**Problem.** `is_offline_reading` doesn't check `charging`. A charging sock can keep reporting non-zero held vitals. Reproduced: two `charging=True` readings at 80% produce a red alert with `sock_reporting=True`. This contradicts the plan's charging behaviour.

**Fix.** Add `reading.charging` to the condition that sets the level to `none`, clears `reason`, sets `sock_reporting=False` and resets the streaks. Leave `is_offline_reading` in `app/quality.py` unchanged, because analytics also uses it.

**Acceptance criteria.**
- Two `charging=True` readings at 80% leave the level at `none`, with `sock_reporting=False` and `charging=True`.
- An active red alert drops to `none` on the first charging reading.
- A new test covers charging with non-zero vitals.

---

## Task 3 — [P1] Owlet's low flag must not delay a red alert

**Files:** `app/alerts.py` (lines 123–135); `tests/test_alerts.py`.

**Problem.** The `LOW_OX_ALRT` branch runs, and returns, before the red-threshold branch. Reproduced: two readings at 84% (critical threshold 86) where the second carries `LOW_OX_ALRT` gives `amber`, not `red`. Red then waits one more reading, about 10 s.

**Fix.** Work out the escalation target from all the conditions together, then move to the highest one above the current level:
- red if `crit_flag` or `_red_in >= ENTER_READINGS`
- otherwise amber if `low_flag` or `_amber_in >= ENTER_READINGS`

Set `reason` from whichever condition produced that target. Prefer `owlet_flag` only when the flag alone qualifies.

**Acceptance criteria.**
- The reproduction above ends at `red` with reason `below_threshold`.
- All existing escalation tests still pass.
- A new test covers the reproduction.

---

## Task 4 — [P1] Serialize the alarm flag with `PlaySoundW`

**Files:** `desktop/src-tauri/src/main.rs` (`ALARM_ON`, `start_alarm`, `stop_alarm`, lines 106–132).

**Problem.** `ALARM_ON` is flipped before the native call, with nothing serializing the two. Possible interleaving:
1. The alert thread sets `ALARM_ON=true`.
2. A snooze on another thread sets it to `false` and stops playback.
3. The alert thread starts the looping sound.

The alarm now loops while `ALARM_ON=false`, so every later `stop_alarm` returns early. It keeps playing through the snooze and beyond, until red fires again and is snoozed. This came from reading the code; native audio wasn't tested.

**Fix.** Replace the atomic with `static ALARM: Mutex<bool>` (rename the WAV static to avoid a clash). Hold the lock across both the check and the `PlaySoundW` call in `start_alarm` and `stop_alarm`. Don't take any other lock while holding it.

**Acceptance criteria.**
- No `PlaySoundW` call happens outside the alarm mutex.
- `cargo clippy` shows no warnings.
- Manual check: start a red test alert from the tray, then snooze quickly several times. The sound always stops.

---

## Task 5 — [P2] Drop alert-thread results fetched before a snooze

**Files:** `desktop/src-tauri/src/main.rs` (`Shell`, `alert_loop`, `apply_state`, `do_snooze`, lines 37–44 and 271–393). Do it after task 4.

**Problem.** `do_snooze` hides the overlay, stops the alarm and clears `applied` to force a re-apply. If the alert thread fetched `/api/alert-state` just before the snooze POST reached the server, it then applies that old red or amber state. The overlay comes back and the alarm restarts for one poll, about 2 s. This is also the most likely way to hit task 4's race.

**Fix.** Add a snooze counter, `AtomicU64`, to `Shell`:
- `do_snooze` increments it before sending the POST.
- `alert_loop` reads it before each GET.
- `apply_state` skips the result if the counter has changed since that read.

Keep the existing "clear `applied` to force a re-apply" behaviour, so a failed snooze POST still lets the alert return on the next poll.

**Acceptance criteria.**
- A state fetched before a snooze is never applied after it.
- If the snooze POST fails (server stopped), the alert returns within one poll once the server is back.
- `cargo clippy` shows no warnings.

---

## Task 6 — [P2] Widget Snooze must stop the overlay and alarm at once

**Files:** `app/widget_page.py` (lines 84–87).

**Problem.** The widget's Snooze button calls `fetch("/api/alert/snooze")` directly. The Rust side doesn't hear about it until the next 2 s poll, so the overlay and alarm keep going for up to 2 s after the click. The overlay and tray paths act immediately.

**Fix.** When `invoke` is available (inside Tauri), call `invoke("snooze_alert")`, then `refresh()`. Keep the `fetch` path for a plain browser.

**Acceptance criteria.**
- Inside the desktop app, the widget button goes through `snooze_alert`.
- In a plain browser, Snooze still works through `fetch`.
- The widget shows "Snoozed 15s" after either path.

---

## Task 7 — [P2] Signal lost during an alert must not look like recovery

**Files:** `app/alerts.py` (`as_dict`, line 157, and `AlertMonitor.state`); `desktop/src-tauri/src/main.rs` (`alert_loop`, lines 286–294, `apply_state`, `tooltip_for`); `app/widget_page.py`; `desktop/src-tauri/ui-placeholder/overlay.html`; `tests/test_alerts.py`. Do it after tasks 1–6.

**Problem.** Two ways of losing data look exactly like the baby recovering:
- A red device with no new reading for `stale_after` (60 s or more) reports `level: none`, so the overlay and alarm stop.
- After three failed polls of the sidecar, the shell hides the overlay and stops the alarm, and only the tray tooltip changes.

The brief describes stale-means-none as intended, but losing the signal during an alert is the moment the user most needs to know.

**Fix (design; confirm before building).**
- **Server.** When a stale device's last non-stale level was amber or red, report a new device field `signal_lost: true`. Add a top-level `signal_lost` flag, and treat it as at least amber for `active_level`. A snooze silences it like any other alert. Keep `level: none` for devices that were already `none` when they went stale.
- **Shell.** When the server is unreachable and the last applied level was amber or red, keep the overlay in amber with a "Dashboard server not responding" message instead of hiding it.
- **Widget and overlay.** Show "Signal lost" text instead of an SpO₂ value.

**Acceptance criteria.**
- Red, then no data for longer than `stale_after`, gives an amber `active_level` with `signal_lost: true`.
- None, then no data, stays `none`.
- New data clears `signal_lost`.
- A snooze silences signal-lost for 15 s.
- Manual check: stopping `owlet-server.exe` during a red test alert leaves an amber overlay with the server message.

---

## Task 8 — [P2] Scope snooze and test alerts to the user

**Files:** `app/alerts.py` (`AlertMonitor`, lines 175–279); `app/main.py` (alert routes, lines 1037–1053); `tests/test_alerts.py`.

**Problem.** Devices are filtered to each user's accounts, but `snoozed_until`, `snoozed_level`, `test_level` and `test_until` are shared by every signed-in user. Reproduced:
- When users A and B both have red alerts, A's snooze also silences B.
- Another user's GET, with nothing alerting in their scope, cancels A's snooze.
- One user's test alert appears for every user.

This doesn't affect the single-user desktop app, which is why it ranks below tasks 1–7. Review A rated it P1.

**Fix.** Pick one:
- **(a) Per-user state.** Key the snooze and test state by user id. `state()`, `snooze()` and `set_test()` take that key.
- **(b) Desktop only.** The alert routes return 404 when the app isn't in desktop mode.

(a) keeps hosted mode working and is preferred. (b) is acceptable if hosted mode doesn't need alerts.

**Acceptance criteria.**
- With two users, A's snooze doesn't change B's `active_level`.
- B's polls don't cancel A's snooze.
- A's test alert doesn't appear in B's device list.
- New tests cover all three.

---

## Task 9 — [P2] Reject cross-origin POSTs in desktop mode

**Files:** `app/main.py` (add a middleware next to `share_guard`, line 222); `tests/test_alerts.py` or a new test file.

**Problem.** In desktop mode, every request counts as the local admin, and no route checks `Origin`. `POST /api/alert/snooze` has no body, so a browser sends it from another site without a preflight check. Any web page the user has open could silence alerts every 15 s. Newer Chrome asks permission before a website can reach localhost; Firefox doesn't. `POST /api/alert/test` is already safe, because FastAPI rejects a non-JSON body. Every other POST route in the app has the same gap.

**Fix.** In desktop mode, reject (403) any state-changing request (`POST`, `PUT`, `PATCH`, `DELETE`) whose `Origin` header is present and isn't `http://127.0.0.1:8877`. Requests without an `Origin`, such as the Rust shell's `http_json`, still pass. If the Tauri webviews send `http://localhost:8877` or a `tauri://` origin, allow that too, and confirm it during the manual test.

**Acceptance criteria.**
- A POST to `/api/alert/snooze` with `Origin: https://evil.example` returns 403 and doesn't snooze.
- The same POST with no `Origin`, or with `Origin: http://127.0.0.1:8877`, works.
- Hosted mode is unchanged.
- The dashboard's existing POST flows still work in the desktop app (manual check).

---

## Task 10 — [P3] Keep a way to quit when the tray fails

**Files:** `desktop/src-tauri/src/main.rs` (setup, lines 690–692; `on_window_event`, lines 703–713).

**Problem.** A tray creation error is only logged, but closing any window is always intercepted and only hides it. Without a tray, Quit is unreachable and the user needs Task Manager.

**Fix.** Record whether the tray was built, for example in `Shell` or an `AtomicBool`. If it wasn't, closing the main window exits the app instead of hiding it.

**Acceptance criteria.**
- With tray creation forced to fail in a debug build, closing the dashboard exits the app and stops the sidecar.
- When the tray works, behaviour is unchanged.

---

## Task 11 — [P3] Steady overlay flash rhythm

**Files:** `desktop/src-tauri/ui-placeholder/overlay.html` (`apply`, line 45).

**Problem.** `apply()` sets `lit = true` on every state push, which happens every 2 s. That fights the 500 ms timer and causes an uneven rhythm, including brief dark flickers.

**Fix.** Reset `lit` only when the level or mode changes, or when the overlay goes from off to on.

**Acceptance criteria.**
- Repeated pushes of the same state don't change the on/off timing.
- A change from amber to red restarts the flash at "on".

---

## Task 12 — [P3] Keep `reason` accurate after a flag clears

**Files:** `app/alerts.py` (`observe`); `tests/test_alerts.py`. Do it after task 3.

**Problem.** A level entered through an Owlet flag keeps `reason = "owlet_flag"` after the flag clears, even when the threshold is what's now holding it. Reproduced: `CRIT_OX_ALRT` at 84%, then plain 84% readings, stays `red` with reason `owlet_flag`. The reverse also happens: a threshold alert keeps `below_threshold` when a flag arrives.

**Fix.** On each new reading that holds the level, recompute `reason`:
- `owlet_flag` if the flag for the current level is active and the threshold alone wouldn't qualify
- otherwise `below_threshold`

**Acceptance criteria.** The reproduction ends with reason `below_threshold`, and a new test covers it.

---

## Task 13 — [P3] Widget shows the alerting device

**Files:** `app/widget_page.py` (`render`, line 126).

**Problem.** The big number always shows `devices[0]`. With several devices, or during a test alert, the border can flash red around a green reading from a device that isn't alerting.

**Fix.** Pick the main device the way `apply_state` in `main.rs` does: the first device whose `level` equals the top-level `level` when that isn't `none`, otherwise `devices[0]`. List the rest under "extras".

**Acceptance criteria.** During a red test alert alongside a normal real device, the big number shows the test device (84%), and the real device is listed as an extra.

---

## Task 14 — [P3] Don't say "remote server" when no account is linked

**Files:** `app/main.py` (alert routes); `app/alerts.py` (`state`); `app/widget_page.py` (line 148).

**Problem.** `collecting` is `bool(state["pollers"])`. In local mode with no account linked yet, it's false, so the widget says "Alerts unavailable — viewing a remote server".

**Fix.** Add a `remote_mode` field to the alert-state response. Use the same check as `main.py` line 149: desktop mode and a configured backend. The widget says "viewing a remote server" only when `remote_mode` is true, and "No Owlet account linked" when `collecting` is false otherwise. Update `tooltip_for` in `main.rs` to match, if task 4/5/10 is already open in that file.

**Acceptance criteria.** With no pollers in local mode, the widget and tooltip don't mention a remote server. Remote mode is unchanged.

---

## Task 15 — [P3] Narrow the overlay capability

**Files:** `desktop/src-tauri/capabilities/overlay.json`.

**Problem.** The overlay gets `core:default`, which is broader than it needs. It only listens for one event and calls two commands.

**Fix.** Replace `core:default` with `core:event:allow-listen` and `core:event:allow-unlisten`, keeping `allow-snooze-alert` and `allow-overlay-state`.

**Acceptance criteria.**
- `cargo check` passes.
- Manual check: the overlay still receives `overlay-state` and snoozes on click.

---

## Task 16 — [P3] Guard against a missing Owlet timestamp

**Files:** `app/alerts.py` (`observe`) or `app/models.py` (`normalize_reading`, line 127); `tests/test_alerts.py`.

**Problem.** If none of `last_updated`, `lastUpdated`, `timestamp` or `data_updated_at` is present, `recorded_at` falls back to the current time. Every poll then looks like a new sample, and `ENTER_READINGS=2` can trigger on one Owlet sample polled twice. Current data has the timestamp, so this is a hidden dependency rather than a live bug.

**Fix.** Mark readings whose timestamp was synthesized, for example with an `OwletReading` flag or by checking the raw keys in `_feed_alerts`. For those readings, deduplicate on the vitals tuple (SpO₂, heart rate, flags) instead of on `recorded_at`. Log once per device when this fallback is in use.

**Acceptance criteria.** Two polls with no timestamp and identical vitals count as one reading. Readings with real timestamps behave as before.

---

## Not a defect (answered during review)

- **Red → amber → none can't stall.** The two exit counters run at the same time, and two readings at or above the warn threshold + 1 go straight from red to none.
- **Removing the `Destroyed` handler is fine.** `RunEvent::Exit` covers shutdown, and `app.exit(0)` doesn't go through the intercepted close.
- **`http_json` is adequate for localhost.** `Connection: close` plus `read_to_end` handles partial reads, and anything other than a 200 is treated as an error.
- **The widget's `esc()` is correct.** Every value inserted into `innerHTML` is escaped.

## Validation status at review time

- All 89 tests pass.
- `cargo check --locked --offline` passes.
- Not verified: the native overlay, focus, monitor placement and audio. Plan section 9, task F covers these.
- No source changes were made during either review.

## P2 implementation — 2026-11-07

Tasks 5–9 are implemented on top of the existing P1 changes:

- Snooze invalidates poll results from before and during its POST. UI/audio updates
  are serialized with snooze, and a fresh poll can restore an alert if the POST fails.
- Widget Snooze invokes the native command in Tauri and uses HTTP in a browser.
- A stale amber/red device reports amber with `signal_lost: true`; stale normal
  devices stay at `none`. New samples clear signal loss, and snooze lasts 15 seconds.
  Server disconnection during an alert keeps an amber overlay, stops red audio,
  and displays “Dashboard server not responding.” Widget and overlay replace held
  vitals with “Signal lost.” A different device's fresh red alert keeps priority.
- Snooze and test state use the authenticated user id (task 8, option a).
- Desktop mutation requests accept no Origin or the exact local origins
  `http://127.0.0.1:8877` and `http://localhost:8877`; other origins return 403.
  The overlay's native snooze POST sends no Origin and needs no Tauri-origin exception.

Automated validation:

```powershell
.venv/Scripts/python -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp
node --test tests/widget_alerts.test.cjs
# In desktop/src-tauri:
cargo test --locked --offline
cargo clippy --locked --offline --all-targets -- -D warnings
```

Results: 125 Python tests, 7 JavaScript tests, and 6 Rust tests passed; Clippy
reported no warnings. Native visual/audio checks and actual webview POST origins
remain to be verified manually in a rebuilt desktop app.

## P3 implementation — 2026-11-07

Tasks 10–16 are implemented on top of P1/P2:

- The shell records successful tray creation. If the tray fails, closing the
  dashboard exits through the normal alarm/sidecar shutdown path. With a tray,
  closing still hides the window. Debug builds accept `OWLET_DEBUG_FAIL_TRAY=1`
  to force the failure for a manual check; unset it afterward.
- Repeated overlay state pushes preserve the flash phase. A level/mode change
  or reactivation starts lit again.
- New readings recompute the reason for a held alert. Threshold streaks count
  actual sub-threshold samples, so repeated flag-only samples cannot incorrectly
  change the reason to `below_threshold`.
- P2 already selects the alerting device in the widget. A regression test now
  verifies that a red test device supplies the large 84% reading while a normal
  real device appears in extras.
- Alert-state and test responses include `remote_mode`, using the same desktop
  backend configuration check as collector startup. The widget and tray tooltip
  distinguish remote viewing from “No Owlet account linked.”
- The overlay capability grants only event listen/unlisten and the existing
  snooze/state commands.
- Normalized readings mark synthesized timestamps internally. The alert tracker
  deduplicates those readings by SpO₂, heart rate and alarm/status flags, preserves
  freshness on repeats, and logs the fallback once per device. Real Owlet
  timestamps still distinguish samples with identical vitals.

Automated validation:

```powershell
.venv/Scripts/python -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp
node --test tests/widget_alerts.test.cjs
# In desktop/src-tauri:
cargo test --locked --offline
cargo clippy --locked --offline --all-targets -- -D warnings
cargo check --locked --offline
```

Results: 138 Python tests, 11 JavaScript tests, and 7 Rust tests passed. Clippy
reported no warnings, and Cargo check passed. Python emitted the existing
dependency/test-configuration warnings. Native tray-failure exit/sidecar shutdown
and overlay event/click checks remain to be verified manually in a rebuilt app.
