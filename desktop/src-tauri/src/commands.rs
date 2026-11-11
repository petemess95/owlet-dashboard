use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::{AppHandle, Manager};

use crate::audio::stop_alarm;
use crate::http::http_json;
use crate::overlay::hide_overlay;
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
    app.state::<Shell>().snooze(|applied| {
        // Silence locally before doing any HTTP, including when the server is
        // down. Preserve the previous level for an amber disconnection warning.
        hide_overlay(app);
        stop_alarm();
        applied.key.clear();
        applied.snoozed_until = Some(Instant::now() + Duration::from_secs(15));
        if let Err(err) = http_json("POST", "/api/alert/snooze", None) {
            log_line(&format!("snooze failed: {err}"));
        }
        // The next fresh poll always re-applies, even if the POST failed.
    });
}

/// Placeholder for the acknowledge hotkey (built in R8). Deliberately does
/// nothing to the alert: no POST, no silencing.
pub(crate) fn do_acknowledge(_app: &AppHandle) {
    log_line("acknowledge hotkey: not built yet");
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
pub(crate) fn show_main(app: AppHandle) {
    do_show_main(&app);
}

#[tauri::command]
pub(crate) fn hide_widget(app: AppHandle) {
    do_hide_widget(&app);
}

#[tauri::command]
pub(crate) fn overlay_state(app: AppHandle) -> Value {
    app.state::<Shell>().overlay_payload.lock().unwrap().clone()
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
            Ok(plan)
        },
        |plan| {
            hotkeys::release_old(&be, &plan, &log_line);
            hk.set_bound(plan.bound_after);
        },
        |plan| hotkeys::abort_new(&be, &plan),
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
