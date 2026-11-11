use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::{AppHandle, Manager};

use crate::audio::stop_alarm;
use crate::http::http_json;
use crate::overlay::hide_overlay;
use crate::settings::{data_dir, DesktopSettings};
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
/// value (never a rejected promise). Nothing changes on error.
#[tauri::command]
pub(crate) fn set_desktop_settings(state: tauri::State<'_, DesktopSettings>, patch: Value) -> Value {
    match state.apply_patch(&data_dir(), &patch) {
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
