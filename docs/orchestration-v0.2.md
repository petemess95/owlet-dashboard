# v0.2 orchestration guide

How to build [plan-v0.2.md](plan-v0.2.md) by delegation. The plan says **what** to build; this file says **who builds each piece, in what order, and how the work is checked**.

Status: **part 1 done** (15 packages, last package commit `8938f30`). Parts 2 and 3 start from [orchestration-v0.2-handoff.md](orchestration-v0.2-handoff.md).

## 1. Roles

- **Orchestrator** (the session running this guide): splits work, writes briefs, reviews every result, runs integration. It does not write product code itself.
- **Workers**: one per work package, on the tier in section 2.
- **You**: the hands-on checks in section 8, and any decision a worker reports back as a design question.

## 2. Model tiers

| Tier | Model and effort | Use for | Examples in this plan |
|---|---|---|---|
| **H** | Haiku 5.5, high | The simplest tasks: an exact spec, one or two files, nothing to decide. | Version bump, generating sound files from a written spec, bundling font files |
| **S** | Sonnet 5.5, medium | Slightly more complex tasks that need some judgment, with a small blast radius. | Token audit, a new read-only endpoint, a refactor with no behaviour change, a settings plumbing layer |
| **O** | Opus 5.5, low | Everything else: alert logic, audio, anything where a mistake could cause a missed or stuck alert, cross-cutting native work. | Alert state machine changes, WASAPI audio, armed state, controller snooze |
| **OM** | Opus 5.5, medium | The settings page only (your decision): new interface in the new style, but mostly form wiring across many settings and two storage places. | Settings page (V7) |
| **V** | Opus 5.5, high | Visual design: any package where the worker decides how something looks. | New theme values, Today rebuild, Tonight timeline, widget and overlay restyle |

**Choosing a tier**
1. If the package decides how something looks, it is **V**, whatever its size.
2. Otherwise, if it touches `app/alerts.py`, alarm audio, snooze or acknowledge, or the alert loop in the shell, it is **O**. These are the paths where a bug means a missed alert.
3. Otherwise, if the brief can say exactly what to type and where, it is **H**.
4. Otherwise, if it needs judgment but stays inside one area, it is **S**.
5. When unsure between two tiers, pick the higher one.

**Visual packages still contain logic.** A V package such as the widget restyle includes the JavaScript that drives it. The V worker owns the whole package; it is not split into "look" and "code" halves, because the two can't be reviewed apart.

### How to spawn each tier
The installed worker definitions (`worker-mechanical`, `worker-rocket`, `worker-standard`, `worker-complex`) don't match these tiers exactly, so pass the model and effort explicitly on each Agent call. The worker type supplies the standing instructions; the overrides set the model.

| Tier | `subagent_type` | `model` | `effort` |
|---|---|---|---|
| H | `worker-mechanical` | `haiku` | `high` |
| S | `worker-standard` | `sonnet` | `medium` |
| O | `worker-standard` | `opus` | `low` |
| OM | `worker-complex` | `opus` | `medium` |
| V | `worker-complex` | `opus` | `high` |

OM and V use `worker-complex`, because its instructions ("own this task end to end, report the decisions you made") suit design and interface work. The others should stop and report a design question, not answer it themselves. OM is used for the settings page only; it is not an escalation step for other packages.

## 3. Rules for the orchestrator

1. **One package, one worker, one brief.** Use the template in section 6.
2. **File locks.** A file belongs to one running package at a time. Section 5 lists each package's files; two packages that share a file never run together.
3. **Contracts are fixed here** (section 4). A worker that needs a contract changed stops and reports; the orchestrator updates this file, then tells the affected packages.
4. **Review before moving on.** For every result: read the diff, run the checks in section 7, and confirm each acceptance criterion from the plan. A worker's summary is a claim, not proof.
5. **Commit after each verified package**, one commit per package, on a `v0.2` branch cut from `fork/widget-alerts`. Get your go-ahead for commits once at the start of the run. Never push without being asked.
6. **Failure handling.**
   - First failure: send the same worker a follow-up naming the failed criterion and the output.
   - Second failure: re-issue the package one tier up (H → S → O). V stays on V.
   - A failure on O, or a second failure on V: stop and bring the problem to you. There is no tier above O in this guide, and raising effort is your call.
7. **Design questions go to you.** If a worker reports a choice the plan doesn't settle, don't let it guess on anything that affects alert behaviour. Looks are the V worker's call within the plan's principles.
8. **Safety invariant for every brief:** a change must never make an alert less likely to fire, quieter, or easier to silence than the plan describes. Workers state in their report how they checked this when they touched an alert path.

## 4. Contracts

These let the three lanes work in parallel. Names are fixed; a worker implements against them even if the other side isn't built yet.

### Server API (additions to v0.1)

| Item | Shape |
|---|---|
| Preferences keys | `alert_snooze_seconds` (5–120, default 15), `alert_stale_seconds` (30–300, default 60), `awake_chime` (bool, default false) |
| `GET /api/alert-state` | Adds `acknowledged` (bool), `acknowledged_until` (ISO or null), `ack_seconds_left` (int), `awake_event_id` (int or null; changes each time a chime should play). Existing fields keep their meaning; `snooze_seconds` now carries the configured value. |
| `POST /api/alert/acknowledge` | No body. Returns `{"acknowledged_until": "<iso>"}`, or null when nothing is alerting. |
| `GET /api/widget-series?minutes=10` | `{"device_serial", "warn_threshold", "critical_threshold", "points": [{"t": "<iso>", "spo2": 97}]}` for the alerting device, else the first. `minutes` is clamped to 1–60. Part 3 (V8b) adds `"sleep": {"state": "asleep" \| "awake", "since": "<iso UTC>"}` or null, for the same device: null when stale, sock off, charging or unknown; a run ends at a state change, a sock-off/charging row or a gap over 10 minutes, and looks back at most 16 h. |

### Tauri commands (shell)

| Command | Returns | Notes |
|---|---|---|
| `armed_state` | `{armed: bool, problems: [{code, text}]}` | Codes: `server`, `not_collecting`, `stale`, `no_audio`, `muted`, `sleep`, `watchdog`. Built in R4; texts and order below. |
| `get_desktop_settings` | settings object, below | |
| `set_desktop_settings(patch)` | new settings object, or `{error: {field, message}}` | Rejects a clashing hotkey |
| `list_audio_devices` | `[{id, name, is_default}]` | |
| `list_monitors` | `[{id, name, is_primary}]` | `id` is the Windows monitor name; `name` is "Display N · W×H" |
| `capture_controller_button` | `{buttons: <mask>}` or null after 10 s | For the settings capture field |
| `acknowledge_alert` | nothing | POSTs acknowledge, then silences locally, like `snooze_alert` |
| `set_widget_mode({size, opacity, click_through})` | applied mode | `size`: `micro` 110×32, `compact` 300×120, `expanded` 300×200 |

Existing commands (`snooze_alert`, `show_main`, `hide_widget`, `overlay_state`) are unchanged.

**Added in part 3:**
- `show_widget_menu` (no args, returns nothing): pops the widget's native right-click menu (R9).
- `acknowledge_alert`: if the POST fails (server unreachable), it acts exactly like snooze for the configured length (your decision, R8).
- The shell's local snooze length is the last `snooze_seconds` from `/api/alert-state` (15 until first heard).
- Overlay: one window per flash target, labelled `overlay`, `overlay-1`, … (capability window glob `overlay-*`). `overlay_state` returns the calling window's payload. The payload adds `snooze_seconds` and `can_acknowledge`.
- `flash_target` "all": click-through border frame on the primary, full flash on every other monitor (your decision). A missing chosen monitor falls back to "secondary". Game mode (R11) uses "all" while active.
- `set_widget_mode`: `click_through` is runtime only (off at launch). An amber/red alert forces click-through off (it stays off) and opacity to 100% (restored after).

**`armed_state` problems (fixed in R4).**
- **Order:** faults come first, in the order `server`, `watchdog`, `no_audio`, `muted`, `sleep`, `stale`. `not_collecting` (informational) is always last.
- **`armed`** is true only when `problems` is empty.
- **Texts by code:**
  - `server`: "Dashboard server not responding" after 3 failed polls. Before the first poll it is "Checking alert status".
  - `watchdog`: "Dashboard server keeps stopping" once the restart cap (3 in 10 minutes) is reached.
  - `no_audio`: "No audio device", "Chosen audio device not found", "Can't check the audio device", or "Alarm sound fell back to basic playback".
  - `muted`: "Windows is muted". It appears only when `unmute_on_red` is off.
  - `sleep`: "PC may sleep".
  - `stale`: "No data for Nm", or "No recent data".
  - `not_collecting`: "No Owlet account linked", "Viewing a remote server", "Sock off" or "Charging".
- **Devices:** with more than one real device, device problems are prefixed "<account name>: ". The TEST device is ignored.

**Shell-internal helpers** that later packages can use:
- `audio::audio_status()` returns `AudioStatus {last_error, using_fallback, device_missing}`.
- `audio::endpoint_facts()` returns each endpoint's presence and mute state.
- `spawn_sidecar` / `sidecar_running` are in `main.rs`.
- `commands::native_toast` shows a native toast.

### Desktop settings keys

`flash_target` (`secondary` / `primary_border` / `all` / monitor id; default `secondary`), `alarm_sound` (`beep` / `two_tone` / `pulse`), `alarm_device` (`default` / `all` / device id; default `default`), `unmute_on_red` (bool, true), `alarm_volume` (20–100, 60), `hotkey_snooze`, `hotkey_acknowledge`, `hotkey_widget`, `hotkey_click_through` (strings or null), `controller_mode` (`off` / `chosen`; `any` removed in part 2), `controller_buttons` (XInput-layout mask 0–65535, or null; PlayStation pads map onto it, see below), `autostart` (bool, false), `keep_awake` (bool, true), `game_mode` (`auto` / `always` / `never`; default `auto`), `capture_exclusion` (bool, false), `widget_size` (default `compact`), `widget_opacity` (30–100, default 100), `first_run_test_done` (bool).

- **Storage:** the shell's own file, `%LOCALAPPDATA%\owlet-dashboard\desktop-settings.json`. Not `desktop-config.json`, because the server overwrites that file whole.
- **Hotkey strings (fixed in part 2, R5/V7):** modifiers from `Ctrl`, `Alt`, `Shift`, `Super`, in that order, then one key named by its `KeyboardEvent.code` value, joined with `+` (for example `Ctrl+Shift+KeyS`, `Alt+F9`). At least one modifier, except that `F1`–`F24` may stand alone. Two actions can't share a combination. A combination another app holds is rejected with `{error: {field: "<hotkey key>", message}}` and not saved.
- **Validation:** `set_desktop_settings` rejects bad values rather than clamping them. `controller_mode` "chosen" needs a non-zero `controller_buttons`.
- **Loading:** a loaded file with "chosen" and no buttons, or with the removed "any", loads as "off".
- **Controller buttons (fixed in part 2, R6b):**
  - XInput pads are read in slots 0–3. Sony HID pads (DualShock 4, DualSense, DualSense Edge) are read in slots 4–7, with their buttons translated into the XInput bits:
    - Cross→A `0x1000`, Circle→B `0x2000`, Square→X `0x4000`, Triangle→Y `0x8000`
    - L1/R1→`0x0100`/`0x0200`, L3/R3→`0x0040`/`0x0080`
    - Options→`0x0010`, Share/Create→`0x0020`
    - D-pad→`0x0001`–`0x0008`
  - Two extra bits: PS button `0x0400` and touchpad click `0x0800`.
  - L2/R2 and the sticks are never buttons.
- **Start with Windows (fixed in part 2, R7):** `autostart` drives the HKCU Run entry `Owlet Dashboard`, which launches the exe with `--autostart`. A login start builds the dashboard window hidden. A failed OS change returns `{error: {field: "autostart", message}}` and isn't saved.
- **First run (fixed in part 2, R7):** while `first_run_test_done` is false, startup shows the dashboard at `/#settings=desktop`. `app/shell.py` handles it once through `window.owletOpenSettings(name)`, which opens Settings on that pane.

## 5. Work packages

Three lanes run side by side. Inside a lane, packages run top to bottom, because they share files. "Needs" lists dependencies in other lanes; where a contract in section 4 covers the dependency, the package can start against the contract and is re-checked once the other side lands.

### Lane 1 — Server (Python)

| ID | Tier | Package | Plan | Files | Needs |
|---|---|---|---|---|---|
| P1 | O | Per-account snooze and stale settings, with clamps and tests | §6 | `app/alerts.py`, `app/poller.py`, `app/main.py`, `tests/test_alerts.py` | — |
| P2 | O | Acknowledge flow: state, routes, per-user scoping, tests | §9 | `app/alerts.py`, `app/main.py`, `tests/test_alerts.py` | P1 |
| P3 | S | `GET /api/widget-series` and tests | §8 | `app/main.py`, `app/store.py`, `tests/test_api.py` | P2 |
| P4 | O | Soft "awake" event: non-flashing alert kind, 5-minute limit, tests proving it can't mask an oxygen alert | §10 | `app/alerts.py`, `app/poller.py`, `tests/test_alerts.py` | P3 |
| P5 | H | Bump version to 0.2.0 in `pyproject.toml`, `tauri.conf.json`, `Cargo.toml` | §3 | those three | everything else |

### Lane 2 — Shell (Rust)

All of these touch `desktop/src-tauri/`, so they are strictly serial.

| ID | Tier | Package | Plan | Needs |
|---|---|---|---|---|
| R1 | S | Split `main.rs` into modules (`audio`, `overlay`, `tray`, `alert_loop`, `commands`). No behaviour change; existing Rust tests pass unchanged. | §4 | — |
| R2 | S | Desktop settings: load, save, validate, `get_desktop_settings` / `set_desktop_settings`, `list_monitors` | §6 | R1 |
| R3 | O | WASAPI alarm: device choice, unmute and raise volume on red, restore after, `PlaySoundW` fallback, `list_audio_devices` | §4 A2 | R2 |
| R4 | O | Armed state, keep-awake, sidecar watchdog, `armed_state` command, tray tooltip | §4 A1, A3, A4 | R3 |
| R5 | S | Global hotkeys with clash reporting | §5 B1 | R4 |
| R6 | O | Controller snooze: `controller.rs` with pure press-edge / hold / 2-second logic and unit tests, XInput polling only during alerts, `capture_controller_button` | §5 B2 | R5 |
| R7 | S | Start at login, start hidden, first-run flag | §4 A5 | R6 |
| R8 | O | Acknowledge in the shell: `acknowledge_alert`, overlay acknowledge target, tray entry, flash-target setting | §9, §6 | R7, P2 |
| R9 | O | Widget window modes: sizes, opacity, click-through that an alert turns off, edge snapping, native right-click menu, `set_widget_mode` | §8 | R8 |
| R10 | S | Live tray icon drawn from the current reading | §8 | R9 |
| R11 | S | Fullscreen detection and game mode; capture exclusion; play the awake chime on `awake_event_id` change | §10 | R10, P4, H1 |

Each shell package also updates `Cargo.toml`, `build.rs` and `capabilities/*.json` for the commands it adds.

### Lane 3 — Visual

| ID | Tier | Package | Plan | Files | Needs |
|---|---|---|---|---|---|
| V1 | S | Token audit: move literal colours, radii and fonts in page modules to tokens. Pages must look identical before and after. | §7 D5.1 | `app/now_page.py`, `night_page.py`, `rhythms_page.py`, `dashboard.py`, `shell.py`, `auth_pages.py`, `static/theme.css` | — |
| V2 | H | Bundle Source Sans 3 and Source Serif 4 as WOFF2 with their licence file, add `@font-face`, add to the service-worker cache list | §7 D3 | `app/static/fonts/*`, `static/theme.css`, `app/pwa.py` | V1 |
| V3 | V | New theme values, dark and light; shell top bar (starfield, pill navigation, armed chip) | §7 D2, D4 | `static/theme.css`, `app/shell.py` | V2 |
| V4 | V | Rebuild Today to the [mockup](mockups/today-night-sky.html) | §7 D4 | `app/now_page.py` | V3 |
| V5 | V | Tonight: serif headline and the night timeline as centrepiece | §7 D4 | `app/night_page.py` | V3 |
| V6 | V | Rhythms, Data, landing and sign-in: fix what the new values broke; check both themes for contrast | §7 D4 | `rhythms_page.py`, `dashboard.py`, `auth_pages.py` | V3 |
| V7 | OM | Settings page: Alerts section and desktop-only block, including the key and controller capture fields and test buttons. Uses the theme and components from V3; it does not invent new styling. | §6 | `app/shell.py` | V3, P1; contracts for R2–R7 |
| V8 | V | Widget: restyle, three sizes, sparkline, armed dot with heartbeat, acknowledged state. Update the widget tests. | §8 | `app/widget_page.py`, `tests/test_widget_js.py`, `tests/widget_alerts.test.cjs` | V3, P2, P3; contract for R4, R9 |
| V9 | V | Overlay restyle, with the acknowledge button | §7 D4 | `ui-placeholder/overlay.html` | R8 |
| V10 | O (H until part 2) | PWA theme colour, favicon and icon regeneration with the new colours via `scripts/generate_icons.py` | §7 D5.6 | `app/pwa.py`, `app/static/*`, `scripts/generate_icons.py` | V3 |

V4, V5 and V6 touch different files and can run together once V3 lands. V7 waits for V3 because both edit `app/shell.py`. V9 edits a file inside the shell lane's folder, so it runs between shell packages, never alongside one.

### Standalone

| ID | Tier | Package | Files | Needs |
|---|---|---|---|---|
| H1 | H | Generate the extra alarm choices and the awake chime from a written spec (frequencies, durations, fades) by extending `scripts/make_alarm_wav.py` | `scripts/make_alarm_wav.py`, `desktop/src-tauri/assets/*.wav` | — |
| S1 | S | Update `README.md` and `desktop/README.md` for the new settings, hotkeys, controller snooze and the "not a medical alarm" wording | the two READMEs | all feature packages |

### Suggested waves

| Wave | Runs together |
|---|---|
| 1 | P1, R1, V1, H1 |
| 2 | P2, R2, V2 |
| 3 | P3, R3, V3 |
| 4 | P4, R4, V4 + V5 + V6 |
| 5 | R5, V7 |
| 6 | R6, V8 |
| 7 | R7, V10 |
| 8 | R8, then V9 |
| 9 | R9, R10, R11 in order |
| 10 | S1, P5, then integration |

The shell lane is the long pole: eleven serial packages. If it falls behind, don't parallelise it; the other lanes simply finish earlier.

### Three parts, three sessions

The build is run as three orchestrator sessions, so none carries the whole history. Part 2 was split in two after part 1 closed, at the natural break after R7: by then every user control exists (hotkeys, controller, start at login, settings page, widget), and part 3 is the acknowledge-and-window finish plus integration.

| Part | Waves | Packages | Ends with |
|---|---|---|---|
| **1. Foundations** ✅ | 1–4 | P1–P4, R1–R4, V1–V6, H1 (15) | Alerts are trustworthy (audio, armed state, keep-awake, watchdog), the server side is complete, and every page has the new look. |
| **2. Controls** | 5–7 | R5, R6, R7, V7, V8, V10 (6) | Hotkeys, controller snooze (checkpoint "After R6"), start at login, the settings page, the restyled widget, new icons. |
| **3. Finish and integration** | 8–10 | R8, R9, R10, R11, V9, S1, P5 (7) | Shell acknowledge, overlay restyle, widget window modes, live tray icon, game mode, docs, version bump, then integration (section 9). |

**Closing a part (1 or 2).** After the last package of the part is verified, the orchestrator:
1. Runs every check in section 7 on the branch, and confirms the desktop app builds and starts.
2. Writes or updates `docs/orchestration-v0.2-handoff.md` (part 2 adds a "Part 2" section and refreshes the status table) containing:
   - the status table for all 28 packages
   - the branch name and the last commit
   - every change made to the contracts in section 4, and any place the code differs from the plan
   - decisions you made during the part that the plan and guide don't record
   - known issues, failed or skipped checks, and anything not verified by hand
   - which hands-on checkpoints were done and their results
3. Updates section 4 of this guide if any contract changed, and commits both files.
4. Gives you the next part's kickoff prompt below, with the branch and commit filled in, and stops.

Each part starts in a fresh session and knows only what is in the repository, so anything not written to the handoff file is lost.

**Kickoff prompt for parts 2 and 3** (the closing orchestrator fills in the placeholders; `<scope>` is the matching line below):

- Part 2: `part 2 (waves 5-7: R5, R6, R7, V7, V8, V10). Stop after closing part 2 as described in "Three parts, three sessions".`
- Part 3: `part 3 (waves 8-10: R8-R11, V9, S1, P5) and integration (section 9).`

```text
You are the orchestrator for part <n> of Owlet Dashboard v0.2.
Earlier parts were completed in earlier sessions. Branch: <branch>, last commit: <sha>.

Read these three files in full before doing anything else:
- docs/orchestration-v0.2-handoff.md (what earlier parts did, changed and left open)
- docs/plan-v0.2.md (what to build)
- docs/orchestration-v0.2.md (who builds each piece, the order, contracts, checks)

Where the handoff file and the other two disagree, the handoff file is the
newer record: tell me about the difference before acting on it.

First, verify the starting point yourself: confirm the branch and commit, and
run every check in section 7 of the guide. If anything fails, stop and tell me.

Then run <scope>
Follow the guide exactly:
1. You do not write product code. One worker per package, briefed in full from
   the section 6 template. Workers see nothing from this conversation.
2. Pass subagent_type, model and effort explicitly from the "How to spawn each
   tier" table.
3. Respect file locks and the wave order. The shell lane is strictly serial.
4. After each package: read the diff (git diff --stat first), run the section 7
   checks yourself, confirm each acceptance criterion from the plan, then
   commit. Never push.
5. On failure, follow rule 6 in section 3. Do not raise a tier or effort beyond
   the guide on your own.
6. Stop and wait for me at every remaining checkpoint in section 8.
7. Any design question that affects alert behaviour comes to me.

Show the 28-package status table whenever you stop for me.
```

### Counts by tier
H: 3. S: 9. O: 9. OM: 1. V: 6. Total: 28 packages. (V10 moved from H to O in part 2. R6b, an extra OM package added in part 2, is outside the 28.)

## 6. Brief template

Every brief contains all of this, written out. Workers see nothing else from this conversation.

```
Package <ID>: <title>
Repo: P:\codex\owlet-dashboard fork, branch v0.2.

Goal: <one or two sentences on what this achieves for the user>.

Read first: docs/plan-v0.2.md section <n>; docs/orchestration-v0.2.md section 4 (contracts).
<V packages: also open docs/mockups/today-night-sky.html.>

You may edit only: <files>.
Do not touch: <files other running packages hold>.

Build: <the specific behaviour, copied from the plan, including defaults and limits>.

Contracts you must match exactly: <the relevant rows from section 4>.

Done when: <the plan's acceptance criteria for this package, as a checklist>.

Run before returning: <commands from section 7>.

Safety: this app alerts a parent to low oxygen. Your change must not make an
alert less likely to fire, quieter, or easier to silence than described above.

Report: what you changed, the test output, anything you could not verify,
and any decision you made that the brief did not cover.
If the brief is ambiguous or the task is harder than described, stop and say so.
```

Extra lines by tier:
- **H:** give the exact values, file paths and expected output. Nothing left to choose.
- **V:** include the principles from plan §7 D1 and the brand rule from D6 (no Owlet logo, mascot or artwork). Ask for a screenshot of each changed page in dark and light.
- **O:** name the specific failure the package must not introduce (for example "the alarm must stop on snooze every time").

## 7. Verification

Run from the repo root.

```bash
.venv/Scripts/python -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp
```

```bash
node --test tests/widget_alerts.test.cjs
```

For shell packages, in `desktop/src-tauri`:

```bash
cargo test --locked --offline
```

```bash
cargo clippy --locked --offline --all-targets -- -D warnings
```

Packages that add a crate can't use `--offline` for their first build; the orchestrator runs that fetch once, then goes back to `--locked --offline`.

Per lane, on top of the commands:
- **Server:** every new behaviour has a test; the alert-state response still matches the v0.1 contract plus the additions in section 4.
- **Shell:** the pure logic (controller presses, armed verdict, settings validation) has unit tests. Native behaviour is listed for the hands-on pass in section 8, not assumed.
- **Visual:** the orchestrator opens each changed page in the browser in dark and light, at desktop and phone width, and compares Today against the mockup.

## 8. Checkpoints that need you

| When | What |
|---|---|
| Before wave 1 | Go-ahead to create the `v0.2` branch and commit per package. Do the v0.1 hands-on checks that were never done: overlay focus, monitor placement, alarm audio, quitting when the tray fails. |
| After R3 | Hear the alarm with Windows muted; confirm volume and mute are restored. |
| After R6 | Try controller snooze in a real game, in both modes. Confirm the 2-second guard feels right and that your controller is seen. |
| After V3 | Look at the new theme in dark and light before five more packages build on it. |
| After V4 | Compare Today with the mockup. |
| Integration | Installer build, install over the current version, full manual acceptance from the plan. |

## 9. Integration (orchestrator)

1. Run every command in section 7 on the merged branch.
2. Confirm the PyInstaller spec includes any new Python modules, then rebuild the sidecar with `desktop/build-sidecar.ps1`.
3. Build the installer.
4. Walk through each workstream's acceptance criteria in the plan with you, and record what passed, what failed and what wasn't checked.
5. Regenerate the screenshots in `docs/screenshots/` from the running app.
