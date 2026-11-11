use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::{AppHandle, Manager};

use crate::audio::stop_alarm;
use crate::autostart;
use crate::http::http_json;
use crate::overlay::{hide_overlay, payload_for};
use crate::hotkeys::{self, Hotkeys};
use crate::settings::{data_dir, DesktopSettings, SettingsError};
use crate::tray::sync_widget_toggle;
use crate::{log_line, Shell};

pub(crate) fn do_show_main(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

pub(crate) fn do_hide_widget(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("widget") {
        let _ = win.hide();
    }
    sync_widget_toggle(app);
}

pub(crate) fn do_toggle_widget(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("widget") {
        if win.is_visible().unwrap_or(false) {
            let _ = win.hide();
        } else {
            let _ = win.show();
        }
    }
    sync_widget_toggle(app);
}

pub(crate) fn do_snooze(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let secs = shell.snooze_secs();
    shell.snooze(|applied| {
        // Silence locally before doing any HTTP, including when the server is
        // down. Preserve the previous level for an amber disconnection warning.
        hide_overlay(app);
        stop_alarm();
        applied.key.clear();
        applied.snoozed_until = Some(Instant::now() + Duration::from_secs(secs));
        if let Err(err) = http_json("POST", "/api/alert/snooze", None) {
            log_line(&format!("snooze failed: {err}"));
        }
        // The next fresh poll always re-applies, even if the POST failed.
    });
}

/// "I'm checking": silence every overlay and the alarm, then POST
/// acknowledge. The server then reports active_level "none" until recovery
/// plus grace, and ends it itself on escalation, so the next poll fires red
/// normally. If the POST fails, act exactly like snooze.
pub(crate) fn do_acknowledge(app: &AppHandle) {
    let shell = app.state::<Shell>();
    acknowledge_on(
        &shell,
        || {
            hide_overlay(app);
            stop_alarm();
        },
        || http_json("POST", "/api/alert/acknowledge", None),
    );
}

/// The testable body of `do_acknowledge`: everything runs inside
/// `Shell::snooze`, so a poll begun before (or during the POST) is discarded.
pub(crate) fn acknowledge_on(
    shell: &Shell,
    silence: impl FnOnce(),
    post: impl FnOnce() -> Result<Value, String>,
) {
    let secs = shell.snooze_secs();
    shell.snooze(|applied| {
        silence();
        applied.key.clear();
        if let Err(err) = post() {
            log_line(&format!("acknowledge failed, snoozing {secs} s instead: {err}"));
            applied.snoozed_until = Some(Instant::now() + Duration::from_secs(secs));
        }
    });
}

/// Placeholder for the click-through hotkey (built in R9).
pub(crate) fn do_toggle_click_through(_app: &AppHandle) {
    log_line("click-through hotkey: not built yet");
}

pub(crate) fn do_test_alert(level: &str) {
    let body = format!("{{\"level\":\"{level}\",\"seconds\":20}}");
    if let Err(err) = http_json("POST", "/api/alert/test", Some(&body)) {
        log_line(&format!("test alert failed: {err}"));
    }
}

#[tauri::command]
pub(crate) async fn snooze_alert(app: AppHandle) {
    do_snooze(&app);
}

#[tauri::command]
pub(crate) async fn acknowledge_alert(app: AppHandle) {
    do_acknowledge(&app);
}

#[tauri::command]
pub(crate) fn show_main(app: AppHandle) {
    do_show_main(&app);
}

#[tauri::command]
pub(crate) fn hide_widget(app: AppHandle) {
    do_hide_widget(&app);
}

#[tauri::command]
pub(crate) fn overlay_state(app: AppHandle, window: tauri::WebviewWindow) -> Value {
    payload_for(&app, window.label())
}

#[tauri::command]
pub(crate) fn get_desktop_settings(state: tauri::State<'_, DesktopSettings>) -> Value {
    state.get_json()
}

/// Returns the full new settings, or `{error: {field, message}}` as a normal
/// value (never a rejected promise). Nothing changes on error. A changed
/// hotkey is registered with the OS before anything is saved; if the OS
/// refuses (another app holds it) the patch is rejected and the previous
/// binding stays registered.
#[tauri::command]
pub(crate) fn set_desktop_settings(
    app: AppHandle,
    state: tauri::State<'_, DesktopSettings>,
    patch: Value,
) -> Value {
    let hk = app.state::<Hotkeys>();
    let be = hotkeys::OsBackend(&app);
    let os = autostart::PluginBackend(&app);
    let result = state.apply_patch_with(
        &data_dir(),
        &patch,
        |cur, next| {
            let changes = hotkeys::changed_actions(cur, next);
            let plan = hotkeys::plan(&hk.bound(), &changes);
            hotkeys::register_new(&be, &plan).map_err(|f| {
                log_line(&format!("hotkey: cannot register {} ({}): {}", f.action.label(), f.combo, f.reason));
                SettingsError { field: f.action.key().into(), message: f.message() }
            })?;
            // The OS entry is changed last; if that fails, undo the hotkeys.
            let restore = if cur.autostart != next.autostart {
                if let Err(why) = autostart::set_enabled(&os, next.autostart) {
                    hotkeys::abort_new(&be, &plan);
                    log_line(&format!("autostart: cannot set to {}: {why}", next.autostart));
                    return Err(SettingsError {
                        field: "autostart".into(),
                        message: format!("Could not change Start with Windows: {why}"),
                    });
                }
                Some(cur.autostart)
            } else {
                None
            };
            Ok((plan, restore))
        },
        |(plan, _)| {
            hotkeys::release_old(&be, &plan, &log_line);
            hk.set_bound(plan.bound_after);
        },
        |(plan, restore)| {
            hotkeys::abort_new(&be, &plan);
            // Saving failed after the OS entry changed: put it back.
            if let Some(prev) = restore {
                if let Err(why) = autostart::set_enabled(&os, prev) {
                    log_line(&format!("autostart: cannot restore to {prev}: {why}"));
                }
            }
        },
    );
    match result {
        Ok(new) => new.to_json(),
        Err(err) => {
            log_line(&format!("settings rejected ({}): {}", err.field, err.message));
            err.to_json()
        }
    }
}

/// "Display 1 · 2560×1440" from the Windows name `\\.\DISPLAY1`.
fn monitor_label(name: &str, width: u32, height: u32) -> String {
    let digits = name
        .to_ascii_uppercase()
        .rsplit_once("DISPLAY")
        .map(|(_, d)| d.to_string())
        .filter(|d| !d.is_empty() && d.chars().all(|c| c.is_ascii_digit()));
    let base = match digits {
        Some(d) => format!("Display {d}"),
        None => name.to_string(),
    };
    format!("{base} \u{b7} {width}\u{d7}{height}")
}

#[tauri::command]
pub(crate) fn list_monitors(app: AppHandle) -> Value {
    let key = |m: &tauri::Monitor| {
        (m.name().cloned().unwrap_or_default(), m.position().x, m.position().y)
    };
    let primary = app.primary_monitor().ok().flatten().map(|m| key(&m));
    let monitors = app.available_monitors().unwrap_or_default();
    Value::Array(
        monitors
            .iter()
            .enumerate()
            .map(|(i, m)| {
                let name = m.name().cloned().unwrap_or_else(|| format!("monitor-{i}"));
                let size = m.size();
                serde_json::json!({
                    "id": name,
                    "name": monitor_label(&name, size.width, size.height),
                    "is_primary": primary.as_ref() == Some(&key(m)),
                })
            })
            .collect(),
    )
}

/// Raise a native Windows toast. Called from the dashboard JS for critical
/// alerts. We build the toast directly (no activation handlers) so it matches
/// the code path that actually displays, and fire it off the command thread.
#[tauri::command]
pub(crate) fn show_native_toast(app: AppHandle, title: String, body: String) {
    native_toast(&app, title, body);
}

/// The toast path behind `show_native_toast`, also used by the watchdog.
pub(crate) fn native_toast(app: &AppHandle, title: String, body: String) {
    #[cfg(windows)]
    {
        let app_id = app.config().identifier.clone();
        std::thread::spawn(move || {
            use tauri_winrt_notification::{Duration, Toast};
            let result = Toast::new(&app_id)
                .title(&title)
                .text1(&body)
                .duration(Duration::Short)
                .show();
            if let Err(err) = result {
                log_line(&format!("toast failed: {err:?}"));
            }
        });
    }
    #[cfg(not(windows))]
    let _ = (app, title, body);
}

#[tauri::command]
pub(crate) async fn list_audio_devices() -> Value {
    crate::audio::list_audio_devices()
}

/// Capture a controller button or combination for the settings page:
/// `{buttons: mask}`, or null after 10 s without a fresh press (or when a
/// capture is already running). Saves nothing.
#[tauri::command]
pub(crate) async fn capture_controller_button() -> Value {
    tauri::async_runtime::spawn_blocking(crate::controller::capture_blocking)
        .await
        .unwrap_or_else(|_| {
            log_line("controller capture panicked");
            Value::Null
        })
}

/// Whether this PC will sound an alert right now; see armed.rs.
#[tauri::command]
pub(crate) fn armed_state(app: AppHandle) -> Value {
    app.state::<Shell>().armed_verdict()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::Ordering;

    #[test]
    fn acknowledge_post_failure_snoozes_for_the_configured_length() {
        let shell = Shell::default();
        shell.snooze_seconds.store(60, Ordering::SeqCst);
        shell.applied.lock().unwrap().key = "red|x".into();
        let mut silenced = false;
        let before = Instant::now();
        acknowledge_on(&shell, || silenced = true, || Err("connection refused".into()));
        assert!(silenced);
        let applied = shell.applied.lock().unwrap();
        assert!(applied.key.is_empty());
        let until = applied.snoozed_until.expect("fallback snooze");
        assert!(until >= before + Duration::from_secs(60));
        assert!(until <= Instant::now() + Duration::from_secs(60));
    }

    #[test]
    fn acknowledge_post_success_sets_no_local_snooze() {
        let shell = Shell::default();
        acknowledge_on(&shell, || {}, || Ok(serde_json::json!({ "acknowledged_until": "x" })));
        assert!(shell.applied.lock().unwrap().snoozed_until.is_none());
        // Nothing alerting: the server returns null; still harmless.
        acknowledge_on(&shell, || {}, || Ok(Value::Null));
        assert!(shell.applied.lock().unwrap().snoozed_until.is_none());
    }

    #[test]
    fn acknowledge_discards_polls_started_before_and_during_the_post() {
        let shell = Shell::default();
        let before = shell.snooze_generation.load(Ordering::SeqCst);
        let mut during = None;
        acknowledge_on(&shell, || {}, || {
            during = Some(shell.snooze_generation.load(Ordering::SeqCst));
            Ok(Value::Null)
        });
        assert!(shell.poll_state(before).is_none());
        assert!(shell.poll_state(during.unwrap()).is_none());
        let after = shell.snooze_generation.load(Ordering::SeqCst);
        assert!(shell.poll_state(after).is_some());
    }

    #[test]
    fn acknowledge_falls_back_to_15_s_before_the_server_is_heard() {
        let shell = Shell::default();
        let before = Instant::now();
        acknowledge_on(&shell, || {}, || Err("down".into()));
        let until = shell.applied.lock().unwrap().snoozed_until.unwrap();
        assert!(until >= before + Duration::from_secs(15) && until < before + Duration::from_secs(16));
    }
}
