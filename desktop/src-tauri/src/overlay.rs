use serde_json::{json, Value};
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};

use crate::{log_line, Shell};

// ---------------------------------------------------------------------------
// Overlay window
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq)]
pub(crate) struct Target {
    pub(crate) pos: PhysicalPosition<i32>,
    pub(crate) size: PhysicalSize<u32>,
    pub(crate) border: bool,
}

/// Re-enumerated before every show so a monitor plugged in or out mid-session
/// is handled. 2+ monitors: the left-most non-primary, fully covered.
/// One monitor: a click-through border frame around the primary.
pub(crate) fn pick_target(app: &AppHandle) -> Option<Target> {
    let monitors = app.available_monitors().ok()?;
    let primary_pos = app.primary_monitor().ok().flatten().map(|m| *m.position());
    let mut others: Vec<_> = monitors
        .iter()
        .filter(|m| Some(*m.position()) != primary_pos)
        .map(|m| (*m.position(), *m.size()))
        .collect();
    others.sort_by_key(|(p, _)| (p.x, p.y));
    if let Some((pos, size)) = others.first() {
        return Some(Target { pos: *pos, size: *size, border: false });
    }
    let m = app.primary_monitor().ok().flatten().or_else(|| monitors.into_iter().next())?;
    Some(Target { pos: *m.position(), size: *m.size(), border: true })
}

/// Must not be called on the main thread (window creation would deadlock on
/// Windows); the alert thread is the only caller that may create it.
pub(crate) fn ensure_overlay(app: &AppHandle) -> Option<WebviewWindow> {
    if let Some(win) = app.get_webview_window("overlay") {
        return Some(win);
    }
    match WebviewWindowBuilder::new(app, "overlay", WebviewUrl::App("overlay.html".into()))
        .title("Owlet alert")
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .shadow(false)
        .focused(false)
        .focusable(false)
        .visible(false)
        .build()
    {
        Ok(win) => Some(win),
        Err(err) => {
            log_line(&format!("overlay creation failed: {err}"));
            None
        }
    }
}

pub(crate) fn place_overlay(win: &WebviewWindow, t: Target) {
    // Physical units throughout: avoids mixed-DPI scaling surprises.
    let _ = win.set_position(t.pos);
    let _ = win.set_size(t.size);
}

pub(crate) fn overlay_payload(state: &Value, dev: Option<&Value>, t: Target) -> Value {
    let signal_lost = dev.map_or(state["signal_lost"].as_bool() == Some(true), |d| {
        d["signal_lost"].as_bool() == Some(true)
    });
    json!({
        "level": state["active_level"],
        "spo2": if signal_lost { None } else { dev.and_then(|d| d["spo2"].as_i64()) },
        "mode": if t.border { "border" } else { "fullscreen" },
        "device_name": dev.and_then(|d| d["account_name"].as_str()).unwrap_or(""),
        "signal_lost": signal_lost,
        "message": state["message"].as_str().unwrap_or(""),
    })
}

pub(crate) fn show_overlay(app: &AppHandle, payload: Value, t: Target) {
    let Some(win) = ensure_overlay(app) else { return };
    *app.state::<Shell>().overlay_payload.lock().unwrap() = payload.clone();
    place_overlay(&win, t);
    let _ = win.set_ignore_cursor_events(t.border);
    let _ = app.emit_to("overlay", "overlay-state", payload);
    let _ = win.show();
    let _ = win.set_always_on_top(true);
    // Moving onto a monitor with a different DPI can trigger a resize; re-apply.
    place_overlay(&win, t);
}

pub(crate) fn update_overlay_text(app: &AppHandle, payload: Value) {
    *app.state::<Shell>().overlay_payload.lock().unwrap() = payload.clone();
    let _ = app.emit_to("overlay", "overlay-state", payload);
}

pub(crate) fn hide_overlay(app: &AppHandle) {
    let payload = json!({ "level": "none" });
    *app.state::<Shell>().overlay_payload.lock().unwrap() = payload.clone();
    if let Some(win) = app.get_webview_window("overlay") {
        let _ = app.emit_to("overlay", "overlay-state", payload);
        let _ = win.hide();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tray::tooltip_for;

    #[test]
    fn fresh_red_alert_is_not_masked_by_another_devices_signal_loss() {
        let target = Target {
            pos: PhysicalPosition::new(0, 0),
            size: PhysicalSize::new(1920, 1080),
            border: false,
        };
        let state = json!({ "active_level": "red", "signal_lost": true });
        let dev = json!({ "signal_lost": false, "spo2": 80 });
        let payload = overlay_payload(&state, Some(&dev), target);
        assert_eq!(payload["signal_lost"], false);
        assert_eq!(payload["spo2"], 80);
        assert_eq!(tooltip_for(&state, Some(&dev)), "Owlet \u{2014} RED ALERT 80%");
    }
}
