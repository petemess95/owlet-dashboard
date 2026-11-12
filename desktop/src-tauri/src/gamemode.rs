//! Game mode (fullscreen detection), capture exclusion and the awake chime's
//! decision logic. Everything decided here is pure and unit-tested; the
//! Windows calls are thin wrappers that log failures and never panic.

use tauri::{AppHandle, Manager, WebviewWindow};

use crate::log_line;

// ---------------------------------------------------------------------------
// Game behaviour
// ---------------------------------------------------------------------------

/// Whether game behaviour (flash every monitor, micro widget) applies:
/// "always" and "never" ignore detection, anything else follows it.
pub(crate) fn game_behaviour(game_mode: &str, detected: bool) -> bool {
    match game_mode {
        "always" => true,
        "never" => false,
        _ => detected,
    }
}

/// The flash target for an alert: game behaviour only ever widens it to "all".
pub(crate) fn flash_target_for(game: bool, setting: &str) -> &str {
    if game {
        "all"
    } else {
        setting
    }
}

#[derive(Debug, PartialEq)]
pub(crate) enum WidgetAction {
    /// Game behaviour began: shrink to micro (unsaved).
    Shrink,
    /// Game behaviour ended: put the saved size back.
    Restore(String),
}

/// What to do to the widget when game behaviour goes from `was` to `now`.
/// Only transitions act; a saved size of micro never needs either.
pub(crate) fn widget_action(was: bool, now: bool, saved_size: &str) -> Option<WidgetAction> {
    if was == now || saved_size == "micro" {
        return None;
    }
    Some(if now { WidgetAction::Shrink } else { WidgetAction::Restore(saved_size.to_string()) })
}

/// Apply the widget side of a game-behaviour change (queues a main-thread call).
pub(crate) fn sync_widget(app: &AppHandle, was: bool, now: bool, saved_size: &str) {
    match widget_action(was, now, saved_size) {
        Some(WidgetAction::Shrink) => {
            log_line("game mode: on, widget shrinking to micro");
            crate::widget::set_size_runtime(app, "micro");
        }
        Some(WidgetAction::Restore(size)) => {
            log_line(&format!("game mode: off, widget restored to {size}"));
            crate::widget::set_size_runtime(app, &size);
        }
        None => {
            if was != now {
                log_line(if now { "game mode: on" } else { "game mode: off" });
            }
        }
    }
}

/// `SHQueryUserNotificationState` values that mean a fullscreen app is up:
/// QUNS_BUSY (2, any fullscreen app, borderless games included) and
/// QUNS_RUNNING_D3D_FULL_SCREEN (3).
pub(crate) fn is_fullscreen_state(state: i32) -> bool {
    matches!(state, 2 | 3)
}

/// The next `game_detected` value. While one of our windows is in the way
/// (ours is foreground, or an overlay, a fullscreen topmost window, is up)
/// Windows may report QUNS_BUSY because of us, so no new reading is taken and
/// the previous value stands: a game detected before an alert keeps every
/// monitor flashing; a game started mid-alert is picked up after it.
pub(crate) fn next_detection(previous: bool, ours_in_way: bool, quns_fullscreen: impl FnOnce() -> bool) -> bool {
    if ours_in_way {
        previous
    } else {
        quns_fullscreen()
    }
}

/// Whether the foreground window belongs to this process. Any failure counts
/// as "not ours" (the reading is then taken as usual).
#[cfg(windows)]
pub(crate) fn foreground_is_ours() -> bool {
    use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};
    let hwnd = unsafe { GetForegroundWindow() };
    if hwnd.0.is_null() {
        return false;
    }
    let mut pid: u32 = 0;
    let tid = unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)) };
    tid != 0 && pid != 0 && pid == std::process::id()
}

#[cfg(not(windows))]
pub(crate) fn foreground_is_ours() -> bool {
    false
}

/// One cheap query; any error counts as "no game".
#[cfg(windows)]
pub(crate) fn detect_fullscreen() -> bool {
    use windows::Win32::UI::Shell::SHQueryUserNotificationState;
    match unsafe { SHQueryUserNotificationState() } {
        Ok(state) => is_fullscreen_state(state.0),
        Err(_) => false,
    }
}

#[cfg(not(windows))]
pub(crate) fn detect_fullscreen() -> bool {
    false
}

// ---------------------------------------------------------------------------
// Capture exclusion
// ---------------------------------------------------------------------------

const WDA_NONE: u32 = 0x0;
const WDA_EXCLUDEFROMCAPTURE: u32 = 0x11;

/// The display-affinity value for the setting.
pub(crate) fn affinity_for(exclude: bool) -> u32 {
    if exclude {
        WDA_EXCLUDEFROMCAPTURE
    } else {
        WDA_NONE
    }
}

#[cfg(windows)]
fn set_affinity(win: &WebviewWindow, exclude: bool) -> Result<(), String> {
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{SetWindowDisplayAffinity, WINDOW_DISPLAY_AFFINITY};
    let raw = win.hwnd().map_err(|e| e.to_string())?;
    let hwnd = HWND(raw.0 as _);
    unsafe { SetWindowDisplayAffinity(hwnd, WINDOW_DISPLAY_AFFINITY(affinity_for(exclude))) }.map_err(|e| e.to_string())
}

#[cfg(not(windows))]
fn set_affinity(_win: &WebviewWindow, _exclude: bool) -> Result<(), String> {
    Ok(())
}

fn apply_to_window(win: &WebviewWindow, exclude: bool) {
    match set_affinity(win, exclude) {
        Ok(()) => log_line(&format!("capture exclusion {} for {}", if exclude { "on" } else { "off" }, win.label())),
        Err(err) => log_line(&format!("capture exclusion failed for {}: {err}", win.label())),
    }
}

/// Queue the setting onto the widget and every existing overlay window.
/// Never blocks; a failure is logged and leaves the window as it was.
pub(crate) fn apply_capture_exclusion(app: &AppHandle, exclude: bool) {
    let handle = app.clone();
    let queued = app.run_on_main_thread(move || {
        for (label, win) in handle.webview_windows() {
            if label == "widget" || crate::overlay::is_overlay_label(&label) {
                apply_to_window(&win, exclude);
            }
        }
    });
    if let Err(err) = queued {
        log_line(&format!("capture exclusion: cannot reach the main thread: {err}"));
    }
}

/// A freshly created overlay window: apply the current setting. Only does
/// anything when the setting is on (a new window already captures normally).
pub(crate) fn apply_to_new_window(app: &AppHandle, label: &str) {
    if !app.state::<crate::settings::DesktopSettings>().get().capture_exclusion {
        return;
    }
    let handle = app.clone();
    let label = label.to_string();
    let queued = app.run_on_main_thread(move || {
        if let Some(win) = handle.get_webview_window(&label) {
            apply_to_window(&win, true);
        }
    });
    if let Err(err) = queued {
        log_line(&format!("capture exclusion: cannot reach the main thread: {err}"));
    }
}

// ---------------------------------------------------------------------------
// Awake chime decision
// ---------------------------------------------------------------------------

#[derive(Debug, PartialEq)]
pub(crate) enum Chime {
    Play,
    /// A new awake event, but an alarm or amber/red alert is active.
    SkipBusy,
    No,
}

/// Remembers the last `awake_event_id` the server reported.
#[derive(Default)]
pub(crate) struct ChimeMemory {
    started: bool,
    last: Option<i64>,
}

impl ChimeMemory {
    /// One successful poll. Plays on a non-null id that differs from the
    /// remembered one, never on the first poll, and only when nothing is
    /// alerting (`quiet`). The id is remembered either way, so a skipped chime
    /// is never replayed later. Failed polls must not call this.
    pub(crate) fn on_poll(&mut self, id: Option<i64>, quiet: bool) -> Chime {
        let first = !self.started;
        let new_event = id.is_some() && id != self.last;
        self.started = true;
        self.last = id;
        match (first, new_event, quiet) {
            (false, true, true) => Chime::Play,
            (false, true, false) => Chime::SkipBusy,
            _ => Chime::No,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn game_behaviour_by_mode_and_detection() {
        for detected in [false, true] {
            assert!(game_behaviour("always", detected));
            assert!(!game_behaviour("never", detected));
            assert_eq!(game_behaviour("auto", detected), detected);
        }
    }

    #[test]
    fn fullscreen_states() {
        // QUNS: 1 not present, 2 busy, 3 d3d fullscreen, 4 presentation, 5 accepts, 6 quiet, 7 app
        for (s, want) in [(1, false), (2, true), (3, true), (4, false), (5, false), (6, false), (7, false)] {
            assert_eq!(is_fullscreen_state(s), want, "state {s}");
        }
    }

    #[test]
    fn own_foreground_keeps_the_previous_detection() {
        for prev in [false, true] {
            for quns in [false, true] {
                assert_eq!(next_detection(prev, true, || quns), prev, "ours prev={prev} quns={quns}");
                assert_eq!(next_detection(prev, false, || quns), quns, "other prev={prev} quns={quns}");
            }
        }
    }

    #[test]
    fn flash_target_widens_only_in_game() {
        for setting in ["secondary", "primary_border", "all", "DISPLAY2"] {
            assert_eq!(flash_target_for(true, setting), "all");
            assert_eq!(flash_target_for(false, setting), setting);
        }
    }

    #[test]
    fn widget_shrinks_once_and_restores_the_saved_size_once() {
        // Walk a sequence of polls the way apply_ui does.
        let mut was = false;
        let mut acts = Vec::new();
        for now in [false, true, true, true, false, false] {
            acts.push(widget_action(was, now, "expanded"));
            was = now;
        }
        assert_eq!(
            acts,
            vec![
                None,
                Some(WidgetAction::Shrink),
                None,
                None,
                Some(WidgetAction::Restore("expanded".into())),
                None
            ]
        );
    }

    #[test]
    fn widget_untouched_when_saved_size_is_micro() {
        assert_eq!(widget_action(false, true, "micro"), None);
        assert_eq!(widget_action(true, false, "micro"), None);
    }

    #[test]
    fn restore_uses_the_size_saved_at_the_end() {
        // The user changed size mid-game: the end restores the new setting.
        assert_eq!(widget_action(true, false, "compact"), Some(WidgetAction::Restore("compact".into())));
    }

    #[test]
    fn capture_exclusion_affinity_values() {
        assert_eq!(affinity_for(true), 0x11);
        assert_eq!(affinity_for(false), 0);
    }

    #[test]
    fn chime_first_poll_records_without_playing() {
        let mut m = ChimeMemory::default();
        assert_eq!(m.on_poll(Some(5), true), Chime::No);
        assert_eq!(m.on_poll(Some(5), true), Chime::No);
        assert_eq!(m.on_poll(Some(6), true), Chime::Play);
        assert_eq!(m.on_poll(Some(6), true), Chime::No);
    }

    #[test]
    fn chime_null_then_new_id_plays_and_null_resets_after_restart() {
        let mut m = ChimeMemory::default();
        assert_eq!(m.on_poll(None, true), Chime::No);
        assert_eq!(m.on_poll(Some(1), true), Chime::Play);
        // Server restarted: counter gone, then it counts from 1 again.
        assert_eq!(m.on_poll(None, true), Chime::No);
        assert_eq!(m.on_poll(Some(1), true), Chime::Play);
    }

    #[test]
    fn failed_polls_leave_the_memory_alone() {
        // A failed poll never calls on_poll, so the same id after an outage is no event.
        let mut m = ChimeMemory::default();
        assert_eq!(m.on_poll(Some(3), true), Chime::No);
        assert_eq!(m.on_poll(Some(3), true), Chime::No);
        assert_eq!(m.on_poll(Some(4), true), Chime::Play);
    }

    #[test]
    fn chime_skipped_while_alerting_and_not_replayed_later() {
        let mut m = ChimeMemory::default();
        assert_eq!(m.on_poll(None, true), Chime::No);
        assert_eq!(m.on_poll(Some(2), false), Chime::SkipBusy);
        assert_eq!(m.on_poll(Some(2), true), Chime::No);
    }
}
