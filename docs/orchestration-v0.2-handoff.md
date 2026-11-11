# v0.2 handoff (parts 1 and 2)

Written by the part 1 orchestrator on 2026-11-11, when part 1 closed, and extended by the part 2 orchestrator on 2026-11-11, when part 2 closed. Part 3 knows only what is in the repository; this file is the record of what parts 1 and 2 did, changed and left open. Where it disagrees with [plan-v0.2.md](plan-v0.2.md) or [orchestration-v0.2.md](orchestration-v0.2.md), this file is newer.

- The **"Part 2"** section below is part 2's record.
- The **package status table** is current as of part 2's close.
- Every other section is part 1's record and still applies unless the Part 2 section says otherwise.

## Part 2 (waves 5–7)

### Branch and commit

- **Branch:** `v0.2`. Never pushed.
- **Part 2 commits, in order:**
  - `1006648` R5
  - `b04f4d3` docs: controller snooze is chosen-button only
  - `9af6382` V7
  - `aa43384` R6
  - `c2a6b15` V8
  - `fd17eb8` R6b (PlayStation pads, see below)
  - `5ead3bc` V10
  - `d83325b` R7
- **Last package commit:** `d83325b` (R7). The commit that updates this file and the guide follows it.
- **Checks at close (on `d83325b`):**
  - pytest: 242 passed.
  - `node --test tests/widget_alerts.test.cjs`: 40 passed.
  - `cargo test --locked --offline`: 116 passed, 1 ignored. The ignored test is `debug_enumerate_sony_hid`, a hands-on probe.
  - clippy: clean.
- **Desktop app:**
  - The sidecar was rebuilt and `cargo build --locked --offline` succeeds.
  - The debug build started its own v0.2 server on 8877 and created its windows.
  - It logged `startup mode: ShownWithSettings`.

### User decisions

- **V10 tier and scope:** V10 ran on the **O** tier, not H, and did both jobs: the logo and icon redraw, and moving `auth_pages.py` onto `theme.css`.
- **Controller snooze is chosen-button only:** the "Any button" mode was removed. The plan (§3, §5 B2) and guide §4 were updated in `b04f4d3`.
- **The 2-second guard applies to the chosen button.**
- **PlayStation support (R6b):**
  - The user's DualSense Edge runs through DS4Windows, which outputs a virtual DualShock 4 (`ViiperDS4`), so XInput saw no controller and capture always timed out. That was the R6 hands-on failure.
  - The user chose to add PlayStation support rather than switch DS4Windows to Xbox output. It ran as an extra package, R6b, on the **OM** tier: a failure on O goes to the user, and the user took the suggested OM.

### Contract changes (all written into guide §4)

- **Hotkey strings (R5/V7):**
  - Format: modifiers from `Ctrl`, `Alt`, `Shift`, `Super`, in that order, then one `KeyboardEvent.code` key, joined with `+`.
  - At least one modifier is needed, except that `F1`–`F24` may stand alone.
  - Two actions can't share a combination.
  - A combination another app holds is rejected with `{error: {field, message}}`.
- **`controller_mode`:** `off` / `chosen` (`any` was removed). A loaded `any` loads as `off`.
- **`controller_buttons` (R6b):**
  - Still an XInput-layout mask, 0–65535. PlayStation buttons are translated into the same bits:
    - Cross→A, Circle→B, Square→X, Triangle→Y
    - L1/R1→LB/RB, L3/R3→thumb clicks
    - Options→Start, Share/Create→Back
    - D-pad→D-pad
  - Two new bits: **PS button `0x0400`** and **touchpad click `0x0800`**.
  - L2/R2 and the sticks are never buttons.
- **Start with Windows (R7):**
  - The Run entry is named `Owlet Dashboard` and launches the exe with `--autostart`.
  - Saving `autostart` changes the OS entry. A failure returns `{error: {field: "autostart", message}}` and the value isn't saved.
- **First run (R7):**
  - The main window URL gets `#settings=desktop` while `first_run_test_done` is false.
  - `app/shell.py` handles it once on load through `window.owletOpenSettings(name)`, which opens Settings on that pane.

### Where the code differs from the plan

- **R6b** is an extra package, outside the 28. Controller snooze reads two kinds of pad:
  - XInput pads in slots 0–3.
  - Sony HID pads (DualShock 4 `05C4`/`09CC`, DualSense `0CE6`, DualSense Edge `0DF2`; USB and Bluetooth reports) in slots 4–7, in the new `controller_hid.rs`.
  - It handles held pads during HID capture as follows:
    - Capture waits up to 300 ms for newly opened pads' first report, so a button already held is ignored.
    - A pad that sends nothing in that window counts as not connected.
- **R7, first run:** first run wins over a login start. Until the user confirms the test alerts, even a login start shows the dashboard with Settings → This PC open.
- **R7, Run entry sync:** if `autostart` is saved on, startup rewrites the entry every launch so it points at the running exe. If it's saved off, startup removes the entry.
- **R7, elevated runs:** `tauri-plugin-autostart` on Windows tries HKLM first and falls back to HKCU only when access is denied. If the app is run elevated, the entry lands in HKLM.
- **V8, sleep timer:** not built. The plan's expanded widget shows one, but no API the widget reads carries sleep data. It needs a small server addition, at integration or later.
- **V8, real window sizes:** the three widget sizes work in a browser. The real window sizes need R9's `set_widget_mode`.
- **V10, design choices:**
  - The owl face moved down about 30 px, and the mint pulse trace is now its mouth. The faint top arc was dropped.
  - The beak is dusty rose `--awake` #d9b0a5, kept away from the alert amber and red.
  - The service-worker cache is now **v5**, and `tests/test_api.py` asserts v5.
  - `auth_pages.py` keeps one shared block of page-local non-colour styles (`_BASE_STYLES`) plus `color-scheme` and `--radius-field: 14px`.
- **V10, desktop icons:** the Tauri app icons (`desktop/src-tauri/icons/`) were **not** regenerated; V10 covers `app/static` only. Do this at integration, for example with `tauri icon` from the new `logo.svg` or the 512 icon.
- **Snooze "15 s":**
  - The shell's `do_snooze` hard-codes 15 s, but each poll resets `snoozed_until` from the server's `snooze_seconds_left`. The real length follows the setting.
  - The tray label "Snooze alert (15 s)" is still wrong. **R8 fixes it.**
  - V8 fixed the widget text. V9 still has to fix `overlay.html`.

### Orchestrator decisions written into briefs

- **R5:**
  - Acknowledge and click-through hotkeys are logging placeholders (`do_acknowledge`, `do_toggle_click_through` in `commands.rs`). R8 and R9 wire them up.
  - Hotkey presses are handled on Pressed only, on a spawned thread.
  - `PressGate` filters key repeat, with 2 s recovery.
- **R6:**
  - The "appearance" counter that restarts the 2 s guard bumps on every level change, including amber→red.
  - The poller parks on a condvar while there's no alert and makes no controller calls.
- **R6b:** HID pads are opened only during an alert or a capture. One reader thread runs per pad, using overlapped `ReadFile` with 100 ms waits. A stuck or erroring pad can never produce a press edge.
- **R7:**
  - The plugin is registered from Rust only; no window gets the autostart JS permission.
  - The startup sync runs on its own thread so it can't delay the sidecar or the alert loop.
  - In `set_desktop_settings`, the OS entry is changed last and undone if saving fails.
- **V7:**
  - Native checkbox switches use `role=switch`.
  - Settings styles live in a `SETTINGS_CSS` block.
  - The "This PC" pane sits in an inert `<template>` and is mounted only in the desktop app, behind the Tauri bridge.

### Hands-on checkpoints

| Checkpoint | Result |
|---|---|
| After R6: controller snooze in a real game | ❌ first: no controller seen (DS4Windows outputs a DualShock 4) → R6b → ✅ capture and snooze confirmed by the user with a DualSense Edge via DS4Windows. `desktop.log` shows `controller snooze: slot 4 (PlayStation)`. The chosen button is the PS button (`0x0400`). |

### Known issues and unverified items

- **R5, not checked by hand:** OS registration of real hotkeys, the clash rejection against another app, and holding a hotkey down.
- **R6/R6b, not checked by hand:**
  - Whether the 2 s guard feels right; the user confirmed snooze works, not the guard.
  - A button held when the alert appears.
  - Xbox pads; this PC has none.
  - Bluetooth PlayStation pads, a plain DualSense, and a DualShock 4 v2.
  - Pad unplug and replug during an alert.
- **R7, not checked by hand:**
  - That the window actually shows Settings → This PC on first run.
  - A real login start: the widget only, with no dashboard.
  - Turning the setting off from the page.
- **R7, a Run entry exists on the user's PC:** the user's saved settings had `autostart: true`, from switching it on while testing before R7 existed. The debug build therefore wrote `HKCU\…\Run\Owlet Dashboard = "…\target\debug\owlet-dashboard-desktop.exe --autostart"`. It stays until "Start with Windows" is turned off, or until integration installs the real build, whose startup sync rewrites it.
- **V7:** the "sec" unit label is slightly clipped at the right edge in light at desktop width.
- **V10:**
  - The 16 px favicon is legible but coarse, because the owl outline is a polygon.
  - No phone-width screenshots in dark.
  - The project `.venv` has no Pillow, so `scripts/generate_icons.py` was run with the system Python 3.12. Add Pillow to the dev setup, or note it in the README (S1).
- **`desktop/build-sidecar.ps1`** fails under Windows PowerShell 5.1:
  - PyInstaller writes its INFO log to stderr, and with `$ErrorActionPreference = "Stop"` the first line aborts the script.
  - Part 2 ran the same pyinstaller command from Bash instead.
  - Fix at integration, for example by running pyinstaller through `cmd /c` or relaxing the preference around it.
- **Old v0.1 server:** the installed v0.1 `owlet-server.exe` on 8877 (PID 6272, parent gone, see part 1) was stopped by the part 2 orchestrator with the user's approval, so the debug build could run its own v0.2 server. The installed v0.1 app itself is unchanged.
- **`.pytest-tmp` is wiped by each pytest run** (`--basetemp`). Two workers running pytest at once must use different `--basetemp` folders, and screenshots must not be saved there.
- **The review server** (`.claude/launch.json`, port 8893, untracked) doesn't reload on code changes; restart it after edits.

## Changes to the guide after part 1 closed

- **V tier is now Opus 5.5, high** (`worker-complex`, `opus`, `high`). Fable is no longer used for any package. Applies to V8 and V9.
- **The remaining work is split into two parts** (guide section 5, "Three parts, three sessions"):
  - **Part 2, controls (waves 5–7):** R5, R6, R7, V7, V8, V10. Includes the "After R6" controller checkpoint. Ends by updating this file and giving the part 3 kickoff prompt.
  - **Part 3, finish (waves 8–10):** R8, R9, R10, R11, V9, S1, P5, then integration.

## Branch and commit (part 1)

- **Branch:** `v0.2`, cut from `fork/widget-alerts`. Never pushed.
- **Last package commit:** `8938f30` (R4). The commit that adds this file follows it.
- **Checks at close (on `8938f30`):** pytest 230 passed, `node --test tests/widget_alerts.test.cjs` 13 passed, `cargo test --locked --offline` 53 passed, clippy clean.
- **Desktop app:** `cargo build --locked --offline` succeeds. The debug build started, reused the server on 8877, created its windows and logged `keep-awake hold: ok`, and was still running after 20 s.
- **Baseline before v0.2** (at `02749e2`): pytest 144, node 13, cargo test 8, clippy clean.

## Package status (28), as of part 2 close

| ID | Tier | Status |
|---|---|---|
| P1 | O | ✅ `3e8012c` |
| P2 | O | ✅ `3c6c3f3` |
| P3 | S | ✅ `3c0c42f` |
| P4 | O | ✅ `27ed54d` |
| P5 | H | part 3 |
| R1 | S | ✅ `8b2ae12` |
| R2 | S | ✅ `8ce0485` |
| R3 | O | ✅ `0e5ebbb` |
| R4 | O | ✅ `8938f30` |
| R5 | S | ✅ `1006648` |
| R6 | O | ✅ `aa43384`, plus R6b (OM, PlayStation pads) `fd17eb8`; hands-on ✅ |
| R7 | S | ✅ `d83325b` |
| R8 | O | part 3 |
| R9 | O | part 3 |
| R10 | S | part 3 |
| R11 | S | part 3 |
| V1 | S | ✅ `93ebf10` |
| V2 | H | ✅ `f0d2a13` |
| V3 | V | ✅ `aa18692` |
| V4 | V | ✅ `b25ab0a` |
| V5 | V | ✅ `08b2d3c` |
| V6 | V | ✅ `b9aada1` |
| V7 | OM | ✅ `9af6382` |
| V8 | V | ✅ `c2a6b15` |
| V9 | V | part 3 |
| V10 | O (user's change from H) | ✅ `5ead3bc` |
| H1 | H | ✅ `e261033` |
| S1 | S | part 3 |

## Hands-on checkpoints

| Checkpoint | Result |
|---|---|
| Before wave 1: go-ahead for branch and commits | ✅ given |
| Before wave 1: v0.1 hands-on checks (overlay focus, monitor placement, alarm audio, quitting when the tray fails) | **Never reported.** Asked twice in part 1. Carry into integration. |
| After R3: hear the alarm with Windows muted; volume and mute restored | ✅ the user tested and approved it |
| After V3: theme look | ✅ approved as built |
| After V4: compare Today with the mockup | ✅ approved |

## Contract changes

All are now written into section 4 of the guide.

- **Desktop settings values:**
  - `alarm_sound` is `beep` / `two_tone` / `pulse`.
  - `controller_buttons` is an XInput mask, 0–65535, or null.
  - Defaults: `widget_size` compact, `widget_opacity` 100, `flash_target` secondary, `alarm_device` default, `game_mode` auto.
  - `set_desktop_settings` rejects bad values rather than clamping them. `controller_mode` "chosen" needs a non-zero mask.
  - A loaded file with `controller_mode` "chosen" and no buttons loads as "off".
- **Shell settings file:** `%LOCALAPPDATA%\owlet-dashboard\desktop-settings.json`. It is not `desktop-config.json`, because the server overwrites that file whole.
- **`list_monitors`:** the id is the Windows monitor name, and the label is "Display N · W×H".
- **`armed_state` texts (R4):** the code list is unchanged. The texts are fixed as follows; the top bar shows the first fault.
  - **Order:** faults first, in the order `server`, `watchdog`, `no_audio`, `muted`, `sleep`, `stale`, then `not_collecting` last.
  - `server`: "Dashboard server not responding" after 3 failed polls. Before the first poll it is "Checking alert status".
  - `watchdog`: "Dashboard server keeps stopping".
  - `no_audio`: one of "No audio device", "Chosen audio device not found", "Can't check the audio device", or "Alarm sound fell back to basic playback".
  - `muted`: "Windows is muted". It appears only when `unmute_on_red` is off.
  - `sleep`: "PC may sleep".
  - `stale`: "No data for Nm", or "No recent data".
  - `not_collecting`: "No Owlet account linked", "Viewing a remote server", "Sock off" or "Charging".
  - With more than one real device, device problems are prefixed "<account name>: ". The TEST device is ignored.
  - `armed` is true only when `problems` is empty.
- **Audio status:** `audio::audio_status()` returns `AudioStatus {last_error, using_fallback, device_missing}`. `audio::endpoint_facts()` gives the presence and mute state of each endpoint, for the armed check.

## Where the code differs from the plan

- **Snooze text (plan §6):** the acceptance item "changing snooze length changes the button text, overlay text" is **not met yet**. "15 s" is hard-coded in three places:
  - `app/widget_page.py:65` (fix in V8)
  - `desktop/src-tauri/ui-placeholder/overlay.html:30` (fix in V9)
  - the tray label "Snooze alert (15 s)" in `tray.rs` (fix in R8)
- **`awake_chime`:** P1 also whitelisted it in the preferences patch in `main.py`, because P4 doesn't own `main.py`.
- **R1 module split:** R1 added `http.rs` (`http_json`, `HTTP_TIMEOUT`) beyond the guide's five modules. R4 added `armed.rs`.
- **V2:** the font route needed `app/main.py`, so V2 ran after P2 and took that file too. The service-worker cache moved to v4.
- **Sequencing:** R3, P3, P4 and V3 started before their wave had fully finished. Each one's dependencies were done and its files were free.
- **Widget armed dot:** not built yet. `app/widget_page.py` belongs to V8, which consumes `armed_state`. The top bar (V3) and Today (V4) already call it.

## Decisions by the user (not in the plan or guide)

- **Branch and commits:** branch `v0.2`, one commit per verified package, never push.
- **Multi-account snooze length:** the **shortest** `alert_snooze_seconds` among the accounts in the user's scope. An unset account counts as 15.
- **Fonts:** downloaded from Fontsource with `npm pack` (`@fontsource/source-sans-3` and `@fontsource/source-serif-4` 5.3.1). Latin WOFF2 only: sans 400/600/700 and serif 400/600, plus the OFL licences.
- **Armed chip (V3, approved as built):**
  - It shows amber "Alert status unknown" when `armed_state` has answered once and then goes silent for 15 s.
  - Faults rank above informational problems (`not_collecting`).
  - Today's "This PC's alerts" row (V4) works the same way. When `not_collecting` is the only problem, it stays a grey "Not monitoring" info line.
- **V5 dip count:** keep it tiered. It is amber, and red with ⚠ only when a dip went under the critical threshold.
- **Rhythms O₂ trend bars:** keep red for "the lowest reading went under 92%". A test asserts this.
- **`/logo.svg` and icons (for V10):** no sock. Keep only the owl face and the line, with the line moved lower so it reads like a mouth.
- **Auth-page palette copy (for V10):** `app/auth_pages.py` keeps a local copy of the palette (`_TOKENS`, 13 token lines for dark and light). Its comment claims the auth pages can't load `theme.css`, but `/theme.css` is public: it returns 200 without a session. V10 should link `/theme.css` and delete the copy, which closes the D5 "no page repeats a theme colour" gap. The copy existed before V6, so it isn't a V6 regression.

## Orchestrator decisions written into briefs

- **H1 sound spec:**
  - `alarm_two_tone.wav`: 660/880/660/880 Hz, 200 ms tones, 100 ms gaps, 1.2 s, amplitude 0.708.
  - `alarm_pulse.wav`: 1000 Hz ×6, 80 ms tones, 100 ms gaps, 1.2 s, amplitude 0.708.
  - `awake_chime.wav`: C5 from 0 to 700 ms plus E5 from 500 to 1600 ms, amplitude 0.251, 10 ms fade in, fade out over the last half of each note.
  - `alarm.wav` is byte-identical to before.
- **P2 acknowledge:**
  - Grace is 180 s and the cap is 600 s from the press. Both are constants, not settings.
  - While acknowledged, `active_level` is "none".
  - A re-alert at the same or a lower level during grace stays silent. Grace restarts from the next recovery and never runs past press + 600 s.
  - Escalation above the acknowledged level ends the acknowledge immediately, including during grace.
  - Acknowledging clears a running snooze. A snooze taken while acknowledged is capped at the acknowledge's end, and is cleared when it ends.
  - Test alerts can be acknowledged.
- **P3 widget-series:** with nothing alerting, the series comes from the most recently reporting device in scope. spo2 values of null or 0 are skipped, and the TEST device is ignored.
- **P4 awake event:**
  - Fires on a new, non-duplicate sample where the previous state was asleep (8 or 15) and the new one is awake (1), the sock is reporting, it isn't charging, and `awake_chime` is true.
  - State 0, sock off and charging all reset the remembered state.
  - At most one event per device every 300 s. Ids come from a monitor-wide counter that only increases.
  - `awake_event_id` on alert-state is the highest id in the user's scope, or null.
  - **R11 must record the first id it sees at startup without chiming for it.**
- **V4:** the sock card is labelled "Sock", not "Dream Sock" (brand rule).
- **V6:** allowed to update only the literal-colour assertions in `tests/test_api.py`.
- **R4 brief choices:**
  - Unreachable means 3 failed polls. While unreachable, server-derived checks are skipped.
  - Keep-awake is kept through a server outage.
  - The watchdog restarts when our sidecar exited, or when we never spawned one (we had reused an existing server). Restarts are at least 45 s apart, at most 3 in a rolling 10 minutes, and a capped state reports `watchdog`.
  - The restart toast reads "The dashboard server stopped and was restarted."
  - The tray tooltip shows "Owlet — Not armed: <first fault>" only in the plain vitals and "Owlet Dashboard" cases.

## Notes per package for parts 2 and 3

- **P1:**
  - Helpers: `alerts.clamp_setting_seconds`, `alerts.resolve_alert_timing`, `main._snooze_seconds_for`.
  - The engine clamps the snooze again inside `snooze()` and `state()`. Floats are truncated.
  - The snooze scope is all of the user's accounts.
- **P2:**
  - Recovery is only seen when `state()` or `snooze()` runs.
  - Pressing acknowledge again restarts the 10-minute cap.
  - The desktop Origin guard is middleware that covers every POST.
  - The shell keys off `active_level`, so an acknowledge already silences the overlay and alarm. R8 adds the explicit shell path.
- **R2:** not run on real hardware.
- **R3:**
  - Volume is raised before unmuting.
  - A failure to read or set an endpoint's volume is recorded but doesn't trigger the fallback.
  - With "all", one failed endpoint starts the PlaySoundW fallback while the other streams keep playing.
  - Stop holds the alarm lock across bounded waits: 2 s for the worker, 2 s for the restore.
  - **`PlaySoundW(null)` in stop also stops any other PlaySoundW sound in the process, such as the awake chime. This matters for R11.**
  - Known limitation: a worker stuck in a COM call for more than 2 s keeps its stream playing until the call returns.
- **R4:**
  - The armed work runs on the alert thread after each poll's alert has been applied. It is wrapped in `catch_unwind`; a panic stores a not-armed verdict.
  - The endpoint query runs on a helper thread with its own COM objects. It is bounded to 1.5 s, and a busy flag stops hung queries piling up. It never takes the ALARM or FALLBACK lock and never sets volume or mute.
  - Polls 1–2 of an outage still use the last good server response.
  - For one device, stale takes priority over sock off.
  - `using_fallback` stays set until the next alarm starts, so a test red clears it.
  - A restart counts toward the cap even if the spawn fails.
  - The tooltip may lag the verdict by one poll (2 s).
  - A restarted child that stays alive but never answers is not restarted again.
  - `ServerProcess` now holds `Sidecar {child, exited}`. `spawn_sidecar(&AppHandle)` and `sidecar_running()` are in `main.rs`, and `commands::native_toast(&AppHandle, ..)` is reusable.
- **V1:** a no-visual-change refactor, checked by comparing every element's computed style before and after.
- **V2:** 88,580 bytes of fonts. Offline rendering in the desktop app is unverified.
- **V3:**
  - Light values were darkened from the plan for contrast: mint `#167a5c`, warn `#955800`, faint `#7f83ae`, awake `#b06f5e`.
  - Tokens added beyond the plan: `--surface-live`, `--shadow-pop`, `--pill-track`, `--pill-active`, `--pill-hover`, `--avatar-bg`, `--sky-display`.
  - `--faint` fails 4.5:1, so it is for large or non-essential text only. Some series colours fall below 4.5:1 in places; use them for fills, lines and large numerals, not small text.
- **V4:**
  - Today has a status `h1`, the plum "Live health readings" card, a movement meter, the sleep summary, a serif story card, and the sock list with "This PC's alerts" (desktop only).
  - A reading more than 2 minutes old greys the numbers and shows "Last known, Xm ago".
  - "Heart rate" is relabelled "Pulse rate".
  - Wheel and drag over the chart wells zoom and pan the chart. Touch gestures are untested.
  - Page-local tokens: `--today-well-radius` 14px, `--today-box-radius` 16px, `--today-meter`. The page overrides `.shell-main` max-width to 1120px.
- **V5:**
  - Tonight has a serif headline, a taller timeline with dip markers linked both ways to the event list, and a single starfield.
  - Untested: an in-progress night, a night with no dips or one dip, and keyboard or screen-reader use.
- **V6:**
  - Data draws vitals with `--series-o2` and `--series-move`.
  - Page-local token candidates: `--radius-field` 14px, `--radius-inner` 18px, `--sleep-faint`, `--sleep-mid`, `--feed-mark`.
  - On a phone, the Rhythms heatmaps scroll sideways inside their card.
- **For V10 or integration:**
  - The page-local radius tokens could become shared `--radius-inset` / `--radius-field` in `theme.css`.
  - `preview-today.png` on the landing page should be regenerated from the rebuilt Today page.

## Known issues and unverified items

- **Native behaviour not verified by hand:**
  - **R4:**
    - Endpoint presence and mute queries.
    - Keep-awake: `powercfg /requests` should list the app while collecting, and drop it when `keep_awake` is off.
    - The sidecar restart and its toast. Killing `owlet-server.exe` should restart it after about 6 s, and a 4th kill within 10 minutes should report `watchdog`.
    - No orphaned server after quitting.
    - The tray "Not armed" tooltip.
  - **R2:** monitor listing on real multi-monitor hardware.
  - **R3:** only the user's ear test was run. The worker's longer hand-test list (device choice, "all outputs", fallback) is still open.
- **Plan §4 acceptance items still to check at integration:**
  - Unplugging the selected device changes the armed state within one poll.
  - The PC doesn't sleep while collecting, and does after the account is unlinked.
  - Killing `owlet-server.exe` restarts it and the widget recovers.
- **Orphaned server seen at close:** an `owlet-server.exe` from the installed app (`%LOCALAPPDATA%\Owlet Dashboard`) had been running since 2026-11-10 with its parent shell gone, so no installed shell was alerting on this PC. Left running; it was not started by part 1. The R4 watchdog and the exit path should be checked against this case at integration.
- **Scratch data:** `data/owlet.sqlite3` in the repo is empty. For visual review, part 1 served a time-shifted copy of the desktop app's database with tokens stripped, from the session scratchpad, through an untracked `.claude/launch.json` (port 8893, login admin / password via `SEED_DEFAULT_ADMIN`). Neither is committed.
- **`docs/orchestration-v0.2-progress.md`:** the untracked mid-part record from part 1. This handoff supersedes it.
