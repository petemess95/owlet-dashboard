# Owlet Dashboard v0.2 plan

Status: **draft for review**. Nothing in this document is implemented yet.
Builds on: [fork-plan-widget-alerts.md](fork-plan-widget-alerts.md) (v0.1 widget and alerts) and [review-widget-alerts-findings.md](review-widget-alerts-findings.md) (its remediation).
Visual reference: [mockups/today-night-sky.html](mockups/today-night-sky.html).
How the work is delegated: [orchestration-v0.2.md](orchestration-v0.2.md).

## 1. Theme

**You can trust it while a game is fullscreen and your headphones are on, and it looks like its own product.**

v0.1 proved the loop: widget, monitor flash, alarm. Its weak points are the ones that matter most at a gaming PC:

- The alarm plays at system volume and is silent when Windows is muted.
- Nothing tells you whether alerts are actually working right now.
- Snoozing means clicking another monitor, which pulls focus out of a game.
- The PC can go to sleep while collecting.
- Every tuning value is hard-coded.
- The dashboard looks generic (cream and violet cards), and the widget uses a third palette that matches neither dashboard theme.

## 2. Scope

| Order | Workstream | Section |
|---|---|---|
| 1 | A. Trust: armed state, audio guarantees, keep-awake, watchdog, start at login | 4 |
| 1 | B. Global hotkeys and game-controller snooze | 5 |
| 1 | C. Alert settings page | 6 |
| 2 | D. Visual redesign: night-sky theme in the spirit of the Owlet app, across dashboard, widget and overlay | 7 |
| 2 | E. Widget: sizes, click-through, opacity, sparkline, live tray icon | 8 |
| 2 | F. Acknowledge flow ("I'm checking") | 9 |
| 3 | G. Game-mode extras: fullscreen detection, capture exclusion, soft "awake" alert | 10 |

**All seven workstreams ship in 0.2.0** (your decision). The number is build order, and the priority if something has to be fixed first. It is not a cut line. Phone push is out of scope for now (your decision; see section 13).

## 3. Decisions

All are my judgment unless marked. Section 13 lists the ones that need your answer before work starts.

| Topic | Decision |
|---|---|
| Unmute on red | **Decided by you.** A red alert unmutes the alarm device and raises it to a chosen volume level, then restores the previous state when the alert ends. On by default. The settings page has a switch to turn it off and a control for the level. |
| Alarm output | Default audio device by default. Setting to pick a device or "all outputs" (headphones and speakers together). |
| Keep-awake | The PC is kept from sleeping while a poller is running. The display may still turn off. |
| Armed state | Computed by the shell, since only the shell knows about audio and sleep. Shown on the widget, tray and dashboard top bar. |
| Hotkeys | Off until the user sets them, to avoid clashing with game bindings. |
| Controller snooze | **Asked for by you.** A game controller can snooze an alert. One mode in settings: a chosen button or combination ("any button" was dropped in part 2, your decision). Details and the 2-second guard are in section 5. |
| Acknowledge timing | **Confirmed by you.** Silent until recovery plus 3 minutes, capped at 10 minutes. |
| Phone push | **Decided by you.** Not in v0.2. |
| Redesign | **Decided by you.** A night-sky theme with the feel of the Owlet phone app: deep navy, soft rounded cards, mint accent. Same mood, none of their logo or artwork. Dark is the default; a light theme is kept (your decision) and re-coloured to match. |
| Vital colours | One fixed colour per vital, used identically in the dashboard, widget and overlay. Amber and red are reserved for alert tiers. |
| Typeface | Source Sans 3 and Source Serif 4, both open-licence, bundled with the app so desktop, hosted and phone views match and work offline. |
| Settings storage | Alert and widget settings go in the existing per-account `dashboard_preferences`, next to the oxygen thresholds. Shell-only settings (hotkeys, audio device, start at login) go in the desktop config file. |
| Versions | `tauri.conf.json` is at 0.1.13 and `pyproject.toml` at 0.1.12. Both move to 0.2.0 together. |

## 4. Workstream A — Trust

### A1. Armed state
The shell computes one `armed` verdict every poll from these checks:

| Check | Source | Failure text |
|---|---|---|
| Server reachable | existing alert-thread poll | "Dashboard server not responding" |
| Collecting | `collecting` in `/api/alert-state` | "No Owlet account linked" / "Viewing a remote server" |
| Data fresh | no device stale | "No data for 2m" |
| Sock reporting | `sock_reporting` | "Sock off" / "Charging" (informational, not a fault) |
| Audio device present | Windows audio endpoint query | "No audio device" |
| Audio audible | not muted, or unmute-on-red is on | "Windows is muted" |
| Keep-awake held | A3 | "PC may sleep" |

- A new Tauri command `armed_state` returns `{armed, problems[]}`. The widget and the dashboard top bar call it; the tray tooltip uses it directly.
- The widget shows a small green dot when armed and an amber dot with the first problem as its status line when not.
- Sock off or charging is shown as "not monitoring" in grey, distinct from a fault.

### A2. Audio guarantees
- Replace `PlaySoundW` with WASAPI playback through the `windows` crate so the alarm can target a specific endpoint.
- On red, when "Unmute and raise volume on red" is on: read the endpoint's mute and volume, unmute, raise to the configured alarm volume level (default 60%), play. On alert end, snooze or acknowledge: restore both.
- The level only ever raises the volume. If the device is already louder than the setting, it is left alone.
- When the setting is off, the alarm plays at whatever the device is set to, and a muted device is reported as an armed-state problem (A1).
- A "Test red" press in settings uses the same path, so the user hears the level they chose.
- "All outputs" plays on every active render endpoint.
- If playback fails, fall back to `PlaySoundW` on the default device and report it as an armed-state problem.
- Keep the existing mutex discipline from v0.1 task 4: no playback call outside the alarm lock.

### A3. Keep-awake
- While `collecting` is true, hold `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` from the alert thread. Release it when collecting stops and on exit.

### A4. Sidecar watchdog
- The alert thread already counts failed polls. After 3 failures, if the sidecar process has exited, restart it (the code path `ensure_server` already uses) and raise a tray notification.
- Cap restarts at 3 per 10 minutes, then stop and report it as an armed-state problem.

### A5. Start at login, minimised
- Add `tauri-plugin-autostart`. A setting toggles it; the app starts with the main window hidden and the widget shown.
- First run shows a short "Test your alerts" step that fires the existing amber and red test alerts and asks the user to confirm they saw and heard them.

**Files:** `desktop/src-tauri/src/main.rs` (likely split into `audio.rs`, `armed.rs`), `Cargo.toml`, `capabilities/default.json`, `app/widget_page.py`, `app/shell.py` (top-bar indicator).

**Acceptance criteria**
- With Windows muted and unmute-on-red on, a red test alert is audible, and mute and volume return to their previous values after it ends.
- With unmute-on-red off and Windows muted, the widget shows "Windows is muted" and the armed dot is amber.
- Unplugging the selected audio device changes the armed state within one poll.
- The PC does not sleep on its idle timer while collecting, and does after the account is unlinked.
- Killing `owlet-server.exe` restarts it and the widget recovers without user action.
- After a reboot with start-at-login on, the widget appears and no dashboard window opens.

## 5. Workstream B — Global hotkeys and controller snooze

### B1. Keyboard hotkeys
- Add `tauri-plugin-global-shortcut`.
- Actions: snooze, acknowledge (F), show/hide widget, toggle click-through (E).
- Unbound by default. Set in the settings page with a "press a key" capture field.
- If registration fails because another app holds the combination, the settings page says so and the binding is not saved.
- A hotkey press calls the same functions as the tray menu (`do_snooze`, `do_toggle_widget`).

### B2. Game-controller snooze
Snooze an alert from the controller already in your hands, without reaching for the keyboard.

**Setting: "Controller snooze"**

| Mode | Behaviour |
|---|---|
| Off | Controller input is ignored. The default until you choose a mode. |
| Chosen button | One button or a combination (for example both stick clicks, or View + Menu), captured with a "press a button" field. |

"Any button" mode was removed in part 2 (your decision): only a designated button or combination snoozes.

**Rules**
- Controller input only does anything while an amber or red alert is showing. At all other times the app doesn't read the controller.
- It triggers the normal snooze (length from section 6, default 15 s), through the same `do_snooze` path as the hotkey and tray. It never acknowledges: the longer "I'm checking" silence (F) needs the keyboard hotkey, the overlay button or the tray.
- A press counts on button-down. A button already held when the alert appears doesn't count until it is released and pressed again.
- The game also receives the press. The app can't hide it from the game.

**2-second guard** (applies to the chosen button, your decision in part 2)
While playing, you press buttons constantly. To keep a reflex press from silencing an alert before you noticed it:
- Presses are ignored for the first 2 seconds of each alert appearance, so the flash and sound always get at least that long.
- The same 2 seconds apply every time the alert returns after a snooze. During active play, an unresolved alert therefore cycles roughly 2 s on, 15 s off, and gameplay can't silence it for good.
- Escalation from amber to red restarts the 2 seconds.
- The settings page explains this next to the option.

The 2-second figure is your choice. It is a constant that is easy to change after trying it in a real game.

**How it works**
- The alert thread polls XInput (`XInputGetState`, through the `windows` crate with the `Win32_UI_Input_XboxController` feature) about 20 times a second, only while an alert is showing or the settings capture field is open. XInput can be read while another app has focus, and reading it doesn't take input away from the game. Confirm both points on your PC early in the task.
- All four XInput controller slots are checked.
- **Limitation:** XInput covers Xbox controllers and anything presented as one (most third-party PC pads; PlayStation and Switch controllers when routed through Steam Input or similar). A PlayStation or Switch controller connected directly is not seen. Supporting those natively is a follow-up, to be scoped once this is tested with your controller.

**Files:** `desktop/src-tauri/src/` (new `controller.rs`, alert loop), `Cargo.toml`, settings page.

**Acceptance criteria**
- With a borderless-fullscreen game focused, the snooze hotkey silences a red test alert and the game keeps focus.
- A clashing keyboard combination is rejected with a visible message.
- The chosen button snoozes a test alert while a game has focus; other buttons do nothing.
- A press of the chosen button in the first 2 seconds of an alert does nothing; a press after that snoozes; the alert returns after the snooze and again ignores presses for 2 seconds.
- A button held from before the alert doesn't snooze it.
- With no alert showing, controller input has no effect and XInput is not polled.
- With no controller connected, nothing errors and alerts behave as before.
- Unit tests cover the press-edge, hold and 2-second logic with a fake controller state.

## 6. Workstream C — Alert settings page

A new "Alerts" section in the existing settings, plus a desktop-only block.

| Setting | Default | Stored in |
|---|---|---|
| Snooze length | 15 s | preferences |
| Stale timeout | 60 s | preferences |
| Flash target: secondary / primary border / all monitors / specific monitor | secondary | desktop config |
| Alarm sound: built-in choices | current beep | desktop config |
| Alarm device | default device | desktop config |
| Unmute and raise volume on red (switch) | on | desktop config |
| Alarm volume level (slider, 20–100%, enabled when the switch is on) | 60% | desktop config |
| Hotkeys | unbound | desktop config |
| Controller snooze: off / chosen button | off | desktop config |
| Start at login, keep PC awake | off, on | desktop config |
| Test amber / test red buttons | — | — |

- `SNOOZE_SECONDS` and `STALE_SECONDS` in `app/alerts.py` become per-account values read where the oxygen thresholds are read today, with the current constants as defaults and clamps (snooze 5–120 s, stale 30–300 s).
- The `snooze_seconds` field already in the `/api/alert-state` contract carries the configured value, so the widget and overlay text follow it without a contract change.
- Desktop-only settings are hidden in hosted mode.

**Acceptance criteria**
- Changing snooze length changes the button text, the overlay text and the actual snooze duration.
- Out-of-range values are clamped server-side, with tests.
- Hosted mode shows no desktop-only settings.

## 7. Workstream D — Visual redesign

Direction: **night sky, in the spirit of the Owlet phone app** (your choice, replacing the earlier "night-light instrument" direction). See the [mockup](mockups/today-night-sky.html) for Today. The aim is the same feel, so the dashboard reads as a companion to the app, not a copy of it.

### D1. Principles
1. **Deep navy night sky.** A near-black indigo page with a soft glow and a sparse starfield behind the header. Dark is the main theme.
2. **Soft, rounded cards.** Large radius (28 px), no borders or shadows, a slightly lighter navy than the page. The live-readings card gets a faint plum tint to mark it as the important one.
3. **Mint is the accent.** Icons, switches, the "now" marker and healthy status. Lavender-white text, never pure white.
4. **Friendly type.** A rounded humanist sans in bold for headings and big numbers, and a serif for the one-line stories ("Slept 11h 2m last night").
5. **Plain-language status first.** Each page opens with a sentence ("Your little one is asleep."), then numbers.
6. **Pill controls.** Navigation and range selectors are rounded capsules with a lighter active segment.

Kept from the earlier direction, because they serve monitoring and not just looks:

7. **Oxygen leads** on every page that shows it.
8. **One colour per vital**, identical in the dashboard, widget and overlay. Amber and red mean alert and nothing else.
9. **Thresholds are drawn on the oxygen trace**, and charts covering the same period share one crosshair.

### D2. Tokens
New values for [theme.css](../app/static/theme.css). Token names stay the same where they exist, so pages pick up the change without edits. The current dark theme is already indigo, so this is a shift of the existing palette more than a replacement.

| Token | Dark (default) | Role |
|---|---|---|
| `--bg` | `#070925` | page |
| `--bg-accent-1` | `#1c2052` | header glow |
| `--surface` / `--surface-solid` | `#121442` | cards, popovers, widget |
| `--surface-inset` (new) | `#0c0e33` | chart wells |
| `--surface-line` | `rgba(190,195,255,.13)` | dividers inside cards |
| `--ink` / `--dim` / `--faint` | `#e7e7fb` / `#a9acd9` / `#7074a8` | text |
| `--accent` | `#9be3c9` | mint: icons, switches, focus ring, "now" |
| `--series-o2` (new) | `#9be3c9` | oxygen |
| `--series-hr` | `#f0a6c0` | heart rate |
| `--sleep-light` / `--sleep-deep` | `#7c80e6` / `#4a4fc0` | sleep stages |
| `--awake` | `#d9b0a5` | awake (dusty rose, was amber) |
| `--series-move` (new) | `#b9bcf0` | movement |
| `--good` / `--warn` / `--bad` | `#9be3c9` / `#ffcf66` / `#ff7b7b` | status and alert tiers |
| `--radius-card` / `--radius-control` | `28px` / `999px` | was 18 / 10 |

- "Awake" moves from amber to dusty rose, as in the Owlet app. That frees amber to mean only "warning".
- Oxygen and "good" share mint. That is deliberate: a healthy oxygen reading is the good state. When the reading drops, the number takes the tier colour.
- The user-defined oxygen colour tiers (commit `7e93af2`) keep working: they override the tier colours, not the series colour.
**Light theme (kept, your decision).** Same layout, radii, type and vital hues as dark; only the values change. A "morning sky" counterpart: pale lavender page, white cards, no starfield, a soft lilac glow behind the header. Starting values, to be tuned for contrast during the work:

| Token | Light | Note |
|---|---|---|
| `--bg` | `#f1f0fb` | pale lavender page |
| `--bg-accent-1` | `#dfdcf7` | header glow |
| `--surface` / `--surface-solid` | `#ffffff` | cards |
| `--surface-inset` | `#f4f3fc` | chart wells |
| `--surface-line` | `rgba(40,44,110,.12)` | dividers |
| `--ink` / `--dim` / `--faint` | `#1b1d4d` / `#555a8c` / `#8b8fb8` | navy text |
| `--accent` | `#1f8f6d` | mint darkened to read on white |
| `--series-o2` | `#1f8f6d` | |
| `--series-hr` | `#c2487a` | |
| `--sleep-light` / `--sleep-deep` | `#7c80e6` / `#3d42b0` | |
| `--awake` | `#c98d7e` | |
| `--series-move` | `#7d81c9` | |
| `--good` / `--warn` / `--bad` | `#1f8f6d` / `#a86400` / `#c8322f` | |

- The theme switch and the "follow system" behaviour stay as they are today.
- The widget and the alert overlay stay dark in both themes: the widget sits over games and dark desktops, and the overlay must look the same every time.
- The live-readings card's plum tint becomes a faint lilac tint in light.

### D3. Typeface
- **Source Sans 3** for interface text and numerals, **Source Serif 4** for story lines. Both are open-licence (SIL OFL) and close in feel to the Owlet app's type.
- Bundle them in `app/static/` as WOFF2 (roughly 150–250 KB for the weights used) and add them to the service-worker cache, so the desktop app and phones look the same and work offline.
- The widget must render with no network requests, so it uses the bundled sans from the local server with a Segoe UI fallback.

### D4. Page changes

| Page | Change |
|---|---|
| Shell / top bar ([shell.py](../app/shell.py)) | Starfield and glow behind the header, avatar, pill navigation, armed indicator (A1). |
| Today ([now_page.py](../app/now_page.py)) | Status sentence; a "Live health readings" card with oxygen and pulse side by side, each with icon, big number and trace; movement as a Peaceful–Wiggling meter; sleep summary with a sleep-cycle bar; a serif "last night" story card; a sock status list that also shows this PC's alert state. Per the mockup. |
| Tonight ([night_page.py](../app/night_page.py)) | Serif headline ("Slept 4h 57m so far"); the night timeline becomes the centrepiece, taller, with oxygen dips marked on it and the event list linked to it. |
| Rhythms ([rhythms_page.py](../app/rhythms_page.py)) | Token and type changes only. |
| Data ([dashboard.py](../app/dashboard.py)) | Token and type changes only. It has its own hard-coded colours that need moving to tokens first. |
| Landing and sign-in ([auth_pages.py](../app/auth_pages.py)) | Token changes; replace the screenshot once Today is done. |
| Widget ([widget_page.py](../app/widget_page.py)) | Drop its private grey palette for the shared values: plum-tinted navy card, bold numerals, mint armed dot. |
| Overlay (`overlay.html`) | Alert colours from the shared `--warn` / `--bad` values; same typeface for the SpO₂ text. |

### D5. Order of work
1. Audit hard-coded colours, radii and fonts in the page modules (a first pass found about 40) and move them to tokens. No visual change.
2. Bundle the fonts; swap token values. Every page changes at once; fix what breaks.
3. Rebuild Today to the mockup.
4. Rebuild the Tonight timeline.
5. Restyle the widget and overlay.
6. Regenerate screenshots, icons and the PWA theme colour ([pwa.py](../app/pwa.py)).

Steps 1–2 are safe to ship alone if 3–4 slip.

### D6. Staying clear of Owlet's brand
The goal is a similar mood, not their identity. Do not use Owlet's logo, baby mascot, sock and base-station artwork, cloud illustrations or exact screen layouts. Icons and illustrations are drawn for this project. This matters most for the hosted, public version.

**Acceptance criteria**
- No page module contains a literal colour that duplicates a token.
- Oxygen, heart rate, sleep and movement use the same colour on every page, the widget and the overlay.
- Body text meets 4.5:1 contrast and large numerals 3:1, in both themes.
- Every page renders correctly in both themes, with no dark-only colours left in page modules.
- Tier colour is never the only signal: alert states also change text or show an icon.
- Today matches the mockup at desktop width and stacks cleanly on a phone.
- With the network disconnected, the desktop app and widget render in the bundled fonts.
- No Owlet artwork or logo appears anywhere in the app.
- Existing tests pass, including `tests/test_widget_js.py` and `tests/widget_alerts.test.cjs`.

## 8. Workstream E — Widget

- **Three sizes**, chosen from a right-click menu and remembered:
  - micro pill, about 110 × 32: `97 · 147`
  - compact, the current 300 × 120
  - expanded, about 300 × 200: adds a 10-minute oxygen sparkline with the threshold line and the sleep timer
- **Click-through** ("ghost") mode using `set_ignore_cursor_events(true)`, with opacity 30–100%. Because a click-through window can't be clicked, it is toggled by hotkey (B) and from the tray. Any amber or red alert turns click-through off so Snooze is reachable.
- **Sparkline data.** A new `GET /api/widget-series?minutes=10` returns recent SpO₂ points for the alerting or first device, so the widget doesn't pull the full readings API.
- **Feed heartbeat.** The armed dot pulses once per new sample, so a frozen feed is visible before the stale timeout.
- **Live tray icon.** The shell renders the SpO₂ number into a 32 × 32 icon, coloured by tier, and updates it when the value or tier changes. Falls back to the static icon when there's no reading.
- **Right-click menu** on the widget: size, opacity, click-through, test alert, open dashboard, hide.
- **Edge snapping** within 12 px of a monitor's work-area edge.

**Acceptance criteria**
- Each size shows correct values in every status state the current widget handles (stale, sock off, charging, signal lost, snoozed, server down).
- In click-through mode, clicks reach the window underneath; an alert makes the widget clickable again within one poll.
- The tray icon shows the current value and returns to the static icon when data goes stale.

## 9. Workstream F — Acknowledge flow

A 15 s snooze re-fires while you're walking to the nursery. Add a second response:

- **"I'm checking"** silences the flash and sound until the level returns to `none`, plus a grace period (default 3 minutes), capped at 10 minutes total. The widget stays in its alert colour with "Acknowledged — 84%".
- Escalation still breaks through: amber acknowledged, then red, fires red.
- After the cap, the alert returns if the level is still amber or red.
- API: `POST /api/alert/acknowledge`, and `acknowledged`, `acknowledged_until` fields on `/api/alert-state`. These are additions; existing fields keep their meaning.
- The overlay shows two targets: click anywhere to snooze, a marked button to acknowledge. Hotkey and tray entries for both.
- State is per user, the same way snooze is after v0.1 task 8.

**Acceptance criteria**
- Tests cover: acknowledge silences until recovery plus grace; escalation breaks through; the cap restores the alert; acknowledge with nothing alerting is a no-op; one user's acknowledge doesn't affect another's.

## 10. Workstream G — Game-mode extras

- **Fullscreen detection.** `SHQueryUserNotificationState` reports a Direct3D fullscreen app. In that state, flash all monitors instead of one and shrink the widget to the micro pill; restore both when the game closes. A "game mode" setting: automatic (default), always, never.
- **Capture exclusion.** `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` on the widget and overlay, so OBS and Discord screen share don't broadcast the baby's vitals. A setting, off by default.
- **Soft "awake" alert.** A gentle chime and a widget note when sleep state changes from asleep to awake. No flash, no looping sound, nothing to snooze. A setting, off by default, with its own sound. Needs a non-flashing alert kind in `app/alerts.py`, limited to one chime per 5 minutes so a brief stir doesn't chime repeatedly.

**Acceptance criteria**
- With a fullscreen game running, a test alert flashes every monitor; after the game closes, only the configured one.
- With capture exclusion on, the widget and overlay are absent from an OBS display capture and still visible on screen.
- A sleep-to-awake change chimes once and never raises the overlay. Tests cover the 5-minute limit and that the chime can't mask or delay an oxygen alert.

## 11. Execution plan

This table is the outline. [orchestration-v0.2.md](orchestration-v0.2.md) breaks it into 28 work packages with a model tier, files and dependencies for each, and is the document to run the build from.

| # | Task | Files (exclusive) | Depends on |
|---|---|---|---|
| 1 | Settings backend: per-account snooze and stale values, clamps, tests | `app/alerts.py`, `app/poller.py`, `app/main.py`, `tests/test_alerts.py` | — |
| 2 | Shell: split `main.rs`; WASAPI audio, armed state, keep-awake, watchdog | `desktop/src-tauri/src/*` | — |
| 3 | Token audit, no visual change (D5 step 1) | page modules, `theme.css` | — |
| 4 | Shell: hotkeys, controller snooze, autostart, desktop config for settings | `desktop/src-tauri/*` | 2 |
| 5 | Settings page UI | `app/shell.py`, `app/main.py` | 1, 4 |
| 6 | New tokens and typeface; Today rebuild (D5 steps 2–3) | `theme.css`, `app/now_page.py`, `app/shell.py` | 3, 5 |
| 7 | Acknowledge flow, server and tests | `app/alerts.py`, `app/main.py`, `tests/test_alerts.py` | 1 |
| 8 | Widget: restyle, sizes, sparkline, armed dot, acknowledge | `app/widget_page.py`, widget tests | 6, 7 |
| 9 | Shell: click-through, tray icon, overlay restyle and acknowledge | `desktop/src-tauri/*` | 4, 8 |
| 10 | Tonight timeline; remaining pages (D5 steps 4, 6) | `app/night_page.py`, others | 6 |
| 11 | Game-mode extras: fullscreen detection, capture exclusion, soft awake alert | `desktop/src-tauri/*`, `app/alerts.py`, `app/poller.py` | 7, 9 |
| 12 | Integration: full test run, sidecar rebuild, installer, manual acceptance | — | all |

- Tasks 1, 2 and 3 touch different files and can run in parallel.
- `main.rs`, `app/alerts.py`, `app/main.py` and `app/shell.py` each appear in several tasks; those tasks run one after another.
- Verification for every task is the same as v0.1: the pytest command with `--basetemp`, `node --test tests/widget_alerts.test.cjs`, and `cargo test` plus `cargo clippy -- -D warnings` for shell work.

## 12. Risks

- **This is still not a medical alarm.** It depends on Owlet's cloud, the internet connection and this PC. The base station stays the primary alarm. The armed indicator must never suggest otherwise: it means "this PC's alert path is working", and the settings page should say so.
- **Changing system volume is intrusive.** Unmute-on-red will startle someone on a call. It's the right default for a safety alert, it's restorable, and it can be turned off.
- **WASAPI is more code than `PlaySoundW`** and the place a silent failure would hurt most. Keep the fallback and surface failures in the armed state.
- **Controller snooze makes silencing easier.** That is its purpose and also its risk. Only a designated button counts, and the 2-second guard, the snooze limit and the no-acknowledge rule keep gameplay from hiding an alert.
- **Exclusive fullscreen** still hides every window on that monitor. Audio and other monitors are the answer; there is no alert path independent of this PC in v0.2.
- **The v0.1 native checks were never done by hand** (overlay focus, monitor placement, audio, tray-failure exit). Do them before building on that code.
- **The redesign touches the two largest files** (`dashboard.py` at 3,267 lines, `now_page.py` at 2,038), which makes merges from upstream harder. The token audit is done first and separately to keep the visual diff small.
- **Resembling a commercial product.** The look is inspired by the Owlet app. Keep to mood and palette, and use none of their logo, mascot or artwork (D6), especially on the hosted public version.

## 13. Out of scope and open questions

**Out of scope for v0.2**
- Phone push (your decision). It remains the only idea that would add an alert path independent of this PC, so it's worth revisiting later.
- Native support for PlayStation and Switch controllers connected without Steam Input.
- Heart-rate alerts, RGB lighting, multiple-baby layouts.

**Open questions**
None blocking. The light-theme colours (section 7) are proposals to confirm by trying them.
