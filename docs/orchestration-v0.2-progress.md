# v0.2 part 1: progress record (mid-part handoff)

Written by the part 1 orchestrator when it stopped mid-part on 2026-11-11. This file is **not** the end-of-part `orchestration-v0.2-handoff.md`; part 1 is not finished. A fresh orchestrator session continues part 1 from here, then writes the real handoff when part 1 closes.

## Where things stand

- **Branch:** `v0.2`, cut from `fork/widget-alerts`. Never pushed.
- **Part 1 closed:** R4 `8938f30`, closing commit `ea79145`. Superseded by `docs/orchestration-v0.2-handoff.md`.
- **Working tree:** clean apart from the untracked files below. R4 is next.
- **Checks on the working tree as left:** pytest 230 passed, node 13 passed, cargo test 37 passed, clippy clean.
- **The user's last instruction (session 2):** finish reviewing V4–V6, bring the open decisions and checkpoints, then finish part 1 (R4) and close it. This replaces the earlier "don't start any new tasks".

## Package status (28)

| ID | Tier | Status |
|---|---|---|
| P1 | O | ✅ `3e8012c` |
| P2 | O | ✅ `3c6c3f3` |
| P3 | S | ✅ `3c0c42f` |
| P4 | O | ✅ `27ed54d` |
| P5 | H | part 2 |
| R1 | S | ✅ `8b2ae12` |
| R2 | S | ✅ `8ce0485` |
| R3 | O | ✅ `0e5ebbb` (user passed the ear test) |
| R4 | O | ✅ `8938f30` |
| R5–R11 | — | part 2 |
| V1 | S | ✅ `93ebf10` |
| V2 | H | ✅ `f0d2a13` |
| V3 | V | ✅ `aa18692` (user approved at the "After V3" checkpoint) |
| V4 | V | ✅ `b25ab0a` (user approved at the "After V4" checkpoint) |
| V5 | V | ✅ `08b2d3c` |
| V6 | V | ✅ `b9aada1` |
| V7–V10 | — | part 2 |
| H1 | H | ✅ `e261033` |
| S1 | S | part 2 |

**Untracked, not to be committed:** `.claude/launch.json`. Session 2 rewrote it as a "review" config (port 8893) serving the main tree from a scratchpad copy of the desktop app's database (`%LOCALAPPDATA%\owlet-dashboard\owlet.sqlite3`, tokens stripped). `data/owlet.sqlite3` in the repo is empty. Login on that copy: admin / password (seed).

## Checkpoints

| Checkpoint | Status |
|---|---|
| Before wave 1: go-ahead for branch and commits | ✅ given |
| Before wave 1: v0.1 hands-on checks (overlay focus, monitor placement, alarm audio, tray-failure quit) | **No results reported yet** (asked again in session 2) |
| After R3: hear the alarm with Windows muted; volume and mute restored | ✅ user tested and approved (session 2) |
| After V3: theme look | ✅ approved as built |
| After V4: compare Today with the mockup | ✅ approved (session 2) |

## Decisions by the user (not in the plan or guide)

- Branch `v0.2` with one commit per verified package; never push.
- **Multi-account snooze length:** the **shortest** `alert_snooze_seconds` among the accounts in the user's scope (an unset account counts as 15).
- **Fonts:** downloaded from Fontsource with `npm pack` (`@fontsource/source-sans-3` and `@fontsource/source-serif-4` 5.3.1), latin WOFF2 only: sans 400/600/700 and serif 400/600, plus the OFL licences.
- **V3 approved as built**, including two armed-chip behaviours:
  - The chip shows amber "Alert status unknown" if `armed_state` has answered once and then goes silent for 15 s.
  - Faults rank above informational problems (`not_collecting`).
- "Don't start any new tasks" (2026-11-11): stop after the in-flight V4, V5 and V6. Replaced in session 2 by: finish part 1 (R4) and close it.
- **Session 2 decisions (2026-11-11):**
  - **V5 dip count:** keep it tiered (amber; red with ⚠ only when a dip went under critical).
  - **V4 armed row:** `not_collecting` alone stays grey "Not monitoring" (info), as built. Same as the V3 chip.
  - **`/logo.svg` (for V10):** no sock. Keep only the owl face and the line, with the line moved lower so it reads like a mouth.
  - **Rhythms red trend bars:** keep red for "lowest reading under 92%".
  - **Auth-page palette copy:** fold into V10 (link `/theme.css`, drop the local `_TOKENS` copy).

## Orchestrator decisions written into briefs

- **H1 sound spec:**
  - `alarm_two_tone.wav`: 660/880/660/880 Hz, 200 ms tones, 100 ms gaps, 1.2 s, amplitude 0.708.
  - `alarm_pulse.wav`: 1000 Hz ×6, 80 ms tones, 100 ms gaps, 1.2 s, amplitude 0.708.
  - `awake_chime.wav`: C5 from 0 to 700 ms plus E5 from 500 to 1600 ms, amplitude 0.251, 10 ms fade in, fade out over the last half of each note.
  - `alarm.wav` is byte-identical to before.
- **P2 acknowledge:**
  - Grace is 180 s and the cap is 600 s from the press. Both are constants, not settings.
  - While acknowledged, `active_level` is "none".
  - A re-alert at the same or a lower level during the grace period stays silent; the grace restarts from the next recovery and never runs past press + 600 s.
  - Escalation above the acknowledged level ends the acknowledge immediately, including during the grace period.
  - Acknowledging clears a running snooze. A snooze taken while acknowledged is capped at the acknowledge's end, and is cleared when the acknowledge ends.
  - Test alerts can be acknowledged.
- **P3 widget-series:** with nothing alerting, the series comes from the most recently reporting device in scope. spo2 values of null or 0 are skipped, and the TEST device is ignored.
- **P4 awake event:**
  - Fires on a new, non-duplicate sample where the previous state was asleep (8 or 15) and the new one is awake (1), the sock is reporting, it isn't charging, and `awake_chime` is true.
  - State 0 resets the remembered state, and so do sock off and charging.
  - At most one event per device every 300 s. Ids come from a monitor-wide counter that only increases.
  - `awake_event_id` on alert-state is the highest id in the user's scope, or null.
  - **R11 must record the first id it sees at startup without chiming** for it.
- **V2:** the font route needs `app/main.py`, so V2 ran after P2 and gained that file. The service-worker cache moved to v4.
- **V4:** the sock card is labelled "Sock", not "Dream Sock" (brand rule).
- **V6:** allowed to update only the literal-colour assertions in `tests/test_api.py`.

## Contract changes (section 4 of the guide is not yet updated)

- **Desktop settings:**
  - `alarm_sound` is `beep` / `two_tone` / `pulse`.
  - `controller_buttons` is an XInput mask, 0–65535, or null.
  - Defaults: `widget_size` compact, `widget_opacity` 100, `flash_target` secondary, `alarm_device` default, `game_mode` auto.
  - `set_desktop_settings` rejects bad values rather than clamping them. `controller_mode` "chosen" requires a non-zero mask.
- **Shell settings file:** `%LOCALAPPDATA%\owlet-dashboard\desktop-settings.json`, not `desktop-config.json`, because the server overwrites that file whole.
- **Audio status for R4:** `audio::audio_status()` returns `AudioStatus {last_error, using_fallback, device_missing}`. It carries `#[allow(dead_code)]` until R4 uses it.

## Differences from the plan

- **Snooze text (plan §6):** the acceptance item "changing snooze length changes the button text, overlay text" is not met yet. The text "15 s" is literal in `app/widget_page.py:65`, `desktop/src-tauri/ui-placeholder/overlay.html:30` and the tray label "Snooze alert (15 s)" in `tray.rs`. Fix in V8, V9 and R8.
- **`awake_chime`:** P1 also whitelisted it in the preferences patch in `main.py`, because P4 doesn't own `main.py`.
- **R1 module split:** R1 added `http.rs` (`http_json`, `HTTP_TIMEOUT`) beyond the guide's five modules.
- **Sequencing:** R3, P3, P4 and V3 started before their wave had fully finished. Each one's dependencies were done and its files were free.

## Notes per package for later work

- **P1:** helpers `alerts.clamp_setting_seconds`, `alerts.resolve_alert_timing` and `main._snooze_seconds_for`.
  - The engine clamps the snooze again inside `snooze()` and `state()`.
  - Floats are truncated.
  - The snooze scope is all of the user's accounts.
- **P2:**
  - Recovery is only seen when `state()` or `snooze()` runs.
  - Pressing acknowledge again restarts the 10-minute cap.
  - The desktop Origin guard is middleware that covers every POST.
  - The shell keys off `active_level`, so an acknowledge already silences the overlay and alarm.
- **R2:**
  - A loaded file with `controller_mode` "chosen" and no buttons loads as "off".
  - `list_monitors` uses the Windows monitor name as id, with the label "Display N · W×H".
  - Not run on real hardware.
- **R3 (uncommitted):**
  - Volume is raised before unmuting.
  - A failure to read or set an endpoint's volume is recorded but doesn't trigger the fallback.
  - With "all", one failed endpoint starts the PlaySoundW fallback while the other streams keep playing.
  - Stop holds the alarm lock across bounded waits: 2 s for the worker, 2 s for the restore.
  - `PlaySoundW(null)` in stop also stops any other PlaySoundW sound in the process, such as the awake chime. This matters for R11.
  - Known limitation: a worker stuck in a COM call for more than 2 s keeps its stream playing until the call returns.
  - All native audio is unverified, and the worker gave a 10-step hand-test list.
- **V1:** a no-visual-change refactor, checked by comparing every element's computed style before and after.
- **V2:** 88,580 bytes of fonts. Offline rendering in the desktop app is unverified.
- **V3:**
  - Light values darkened from the plan for contrast: mint `#167a5c`, warn `#955800`, faint `#7f83ae`, awake `#b06f5e`.
  - Tokens added beyond the plan: `--surface-live`, `--shadow-pop`, `--pill-track`, `--pill-active`, `--pill-hover`, `--avatar-bg`, `--sky-display`.
  - `--faint` fails 4.5:1, so it is for large or non-essential text only.
  - Some series colours fall below 4.5:1 in places; use them for fills, lines and large numerals, not small text.
- **V4 (uncommitted, reviewed in session 2):**
  - Today is rebuilt: a status `h1`, the plum "Live health readings" card, a movement meter, the sleep summary, a serif story card, and the sock list with "This PC's alerts".
  - **New stale state:** a reading more than 2 minutes old greys the numbers and shows "Last known, Xm ago".
  - "Heart rate" is relabelled "Pulse rate".
  - Pan, pinch and hold-to-inspect gestures were rebound to the chart well; touch is untested.
  - The armed row shows grey "Not monitoring" when the only problem is `not_collecting`; the worker asks whether it should be amber.
  - Page-local tokens: `--today-well-radius` 14px, `--today-box-radius` 16px, `--today-meter`.
  - The page overrides `.shell-main` max-width to 1120px.
  - The worker modified the scratch database copy (time-shifted readings) for its screenshots.
  - Some screenshots predate small edits.
- **V5 (`08b2d3c`):**
  - Tonight has a serif headline, a taller timeline with dip markers and a two-way link to the event list, and a single starfield.
  - **Decision for the user:** the dip-count stat is now tiered (amber, or red only if a dip went under critical); before, it was always red.
  - Untested: an in-progress night, a night with no dips or one dip, and keyboard or screen-reader use.
- **V6 (`b9aada1`):**
  - Data draws vitals with `--series-o2` and `--series-move`. Card borders are gone. The textarea and notice are no longer pills, and the desktop notice uses `--warn` with ⚠.
  - The auth pages import `shell.THEME_RESOLVER` and carry a local copy of the palette, which must be kept in step with `theme.css` by hand.
  - Page-local token candidates: `--radius-field` 14px, `--radius-inner` 18px, `--sleep-faint`, `--sleep-mid`, `--feed-mark`.
  - **Open points for the user:**
    - `/logo.svg` contains a "sock mark", which conflicts with the D6 brand rule.
    - The O₂ trend bars use red for "below baseline", and a test asserts it.
- **Page-local radius tokens** from V4, V5 and V6 could become a shared `--radius-inset` / `--radius-field` in `theme.css`. That is a theme.css change for a later package, or V10.
- **`preview-today.png`** on the landing page should be regenerated from the rebuilt Today page (V10 or integration).

## Session 2 review findings (2026-11-11)

- **V6 / D5 acceptance gap (pre-existing):** `auth_pages.py` keeps a local copy of the palette (13 token lines in dark and light). Its comment says the auth pages can't load `theme.css`, but `/theme.css` is public (200 without a session). The old auth pages had their own 14-colour palette too, so this isn't a V6 regression. Linking `/theme.css` would remove the copy.
- **`/logo.svg`** is this project's own owl icon (commit `9553bea`); its description mentions "a small sock mark". Whether that breaks D6 is the user's call; V10 regenerates icons.
- **Today in hosted mode** has no "This PC's alerts" row; it appears only with the Tauri bridge. Wheel and drag over the chart wells zoom and pan the chart (by design since V4), so the page scrolls from outside the wells.
- **Rhythms on a phone:** the heatmaps scroll sideways inside their card; the page itself has no sideways scroll.

## Baseline

At `02749e2`: pytest 144, node 13, cargo test 8, clippy clean.
