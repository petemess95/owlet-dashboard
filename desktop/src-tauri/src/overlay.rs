use serde_json::{json, Value};
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};

use std::sync::atomic::{AtomicBool, Ordering};

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

/// One monitor as the target choice sees it: its Windows name (the id
/// `list_monitors` returns), position and size, in physical units.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct MonitorInfo {
    pub(crate) name: String,
    pub(crate) pos: PhysicalPosition<i32>,
    pub(crate) size: PhysicalSize<u32>,
}

/// The overlay windows for a `flash_target` setting ("secondary",
/// "primary_border", "all" or a monitor id). Pure, so it is unit-tested and
/// reusable (game mode asks for "all"). Never empty when `monitors` is not.
/// The primary is the monitor at `primary_pos`; if that is missing or matches
/// no monitor, the monitor at (0, 0) (where Windows always puts the primary).
/// If neither exists, every monitor counts as non-primary (v0.1 behaviour)
/// and a border frame, when one is needed, goes on the first monitor.
pub(crate) fn choose_targets(
    monitors: &[MonitorInfo],
    primary_pos: Option<PhysicalPosition<i32>>,
    flash_target: &str,
) -> Vec<Target> {
    let origin = PhysicalPosition::new(0, 0);
    let primary_pos = primary_pos
        .filter(|p| monitors.iter().any(|m| m.pos == *p))
        .or_else(|| monitors.iter().any(|m| m.pos == origin).then_some(origin));
    let is_primary = |m: &MonitorInfo| Some(m.pos) == primary_pos;
    let full = |m: &MonitorInfo| Target { pos: m.pos, size: m.size, border: false };
    let border = |m: &MonitorInfo| Target { pos: m.pos, size: m.size, border: true };
    let mut others: Vec<&MonitorInfo> = monitors.iter().filter(|m| !is_primary(m)).collect();
    others.sort_by_key(|m| (m.pos.x, m.pos.y));
    let frame = monitors.iter().find(|m| is_primary(m)).or_else(|| monitors.first()).map(border);
    let secondary = || match others.first() {
        Some(m) => vec![full(m)],
        None => frame.into_iter().collect(),
    };
    match flash_target {
        "secondary" => secondary(),
        "primary_border" => frame.into_iter().collect(),
        "all" => {
            let mut out: Vec<Target> = monitors.iter().filter(|m| is_primary(m)).map(border).collect();
            out.extend(others.iter().map(|m| full(m)));
            if out.is_empty() {
                out.extend(frame);
            }
            out
        }
        id => match monitors.iter().find(|m| m.name == id) {
            Some(m) if is_primary(m) => vec![border(m)],
            Some(m) => vec![full(m)],
            None => secondary(),
        },
    }
}

/// Re-enumerated before every show so a monitor plugged in or out mid-session
/// is handled. If enumeration returns nothing, the primary monitor alone is
/// used (v0.1's fallback).
pub(crate) fn pick_targets(app: &AppHandle, flash_target: &str) -> Vec<Target> {
    let info = |i: usize, m: &tauri::Monitor| MonitorInfo {
        name: m.name().cloned().unwrap_or_else(|| format!("monitor-{i}")),
        pos: *m.position(),
        size: *m.size(),
    };
    let primary = app.primary_monitor().ok().flatten();
    let mut monitors: Vec<MonitorInfo> =
        app.available_monitors().unwrap_or_default().iter().enumerate().map(|(i, m)| info(i, m)).collect();
    if monitors.is_empty() {
        monitors.extend(primary.as_ref().map(|m| info(0, m)));
    }
    let primary_pos = primary.map(|m| *m.position());
    let targets = choose_targets(&monitors, primary_pos, flash_target);
    log_targets_on_change(&describe_targets(&monitors, primary_pos, &targets));
    targets
}

/// One line describing a target choice, for the log.
pub(crate) fn describe_targets(
    monitors: &[MonitorInfo],
    primary_pos: Option<PhysicalPosition<i32>>,
    targets: &[Target],
) -> String {
    let primary = primary_pos.map_or("none".to_string(), |p| format!("{},{}", p.x, p.y));
    let ms: Vec<String> = monitors
        .iter()
        .map(|m| format!("{}@{},{} {}x{}", m.name, m.pos.x, m.pos.y, m.size.width, m.size.height))
        .collect();
    let ts: Vec<String> = targets
        .iter()
        .map(|t| format!("{},{} {}", t.pos.x, t.pos.y, if t.border { "border" } else { "full" }))
        .collect();
    format!("overlay targets: primary={primary} monitors=[{}] -> [{}]", ms.join(", "), ts.join(", "))
}

/// pick_targets runs every poll; only log when the description changes.
fn log_targets_on_change(line: &str) {
    static LAST: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());
    let mut last = LAST.lock().unwrap_or_else(|e| e.into_inner());
    if *last != line {
        log_line(line);
        *last = line.to_string();
    }
}

/// Window label for the i-th overlay target: "overlay", "overlay-1", ...
pub(crate) fn overlay_label(i: usize) -> String {
    if i == 0 {
        "overlay".into()
    } else {
        format!("overlay-{i}")
    }
}

pub(crate) fn is_overlay_label(label: &str) -> bool {
    label == "overlay" || label.strip_prefix("overlay-").is_some_and(|n| n.parse::<usize>().is_ok())
}

/// Must not be called on the main thread (window creation would deadlock on
/// Windows); the alert thread is the only caller that may create it.
pub(crate) fn ensure_overlay(app: &AppHandle, label: &str) -> Option<WebviewWindow> {
    if let Some(win) = app.get_webview_window(label) {
        return Some(win);
    }
    match WebviewWindowBuilder::new(app, label, WebviewUrl::App("overlay.html".into()))
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
        Ok(win) => {
            // Queued; a failure is logged and never stops the window showing.
            crate::gamemode::apply_to_new_window(app, label);
            Some(win)
        }
        Err(err) => {
            log_line(&format!("overlay creation failed ({label}): {err}"));
            None
        }
    }
}

pub(crate) fn place_overlay(win: &WebviewWindow, t: Target) {
    // Physical units throughout: avoids mixed-DPI scaling surprises.
    let _ = win.set_position(t.pos);
    let _ = win.set_size(t.size);
}

pub(crate) const DEFAULT_SNOOZE_SECONDS: u64 = 15;

/// The configured snooze length from an alert-state (5-120 s, default 15).
pub(crate) fn snooze_seconds_of(state: &Value) -> Option<u64> {
    state["snooze_seconds"].as_u64().map(|s| s.clamp(5, 120))
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
        "snooze_seconds": snooze_seconds_of(state).unwrap_or(DEFAULT_SNOOZE_SECONDS),
        "can_acknowledge": true,
    })
}

/// True from show_overlays until hide_overlay: our own fullscreen topmost
/// windows are up, so a fullscreen reading would only be detecting us.
static OVERLAY_SHOWING: AtomicBool = AtomicBool::new(false);

pub(crate) fn overlay_showing() -> bool {
    OVERLAY_SHOWING.load(Ordering::SeqCst)
}

/// The payload addressed to one overlay window. The page ignores an event
/// whose label is not its own (a global JS listener hears every emit_to).
pub(crate) fn with_label(payload: &Value, label: &str) -> Value {
    let mut p = payload.clone();
    if let Some(obj) = p.as_object_mut() {
        obj.insert("label".into(), json!(label));
    }
    p
}

fn overlay_windows(app: &AppHandle) -> Vec<WebviewWindow> {
    app.webview_windows().into_iter().filter(|(l, _)| is_overlay_label(l)).map(|(_, w)| w).collect()
}

/// Show one overlay window per target (created here, on the alert thread).
/// A window that fails to create is logged and skipped; the others still
/// show. Overlay windows beyond the target count are hidden.
pub(crate) fn show_overlays(app: &AppHandle, shown: &[(Target, Value)]) {
    OVERLAY_SHOWING.store(!shown.is_empty(), Ordering::SeqCst);
    let labels: Vec<String> = (0..shown.len()).map(overlay_label).collect();
    hide_surplus(app, &labels);
    for (label, (t, payload)) in labels.iter().zip(shown) {
        let payload = &with_label(payload, label);
        let Some(win) = ensure_overlay(app, label) else { continue };
        app.state::<Shell>().overlay_payload.lock().unwrap().insert(label.clone(), payload.clone());
        place_overlay(&win, *t);
        let _ = win.set_ignore_cursor_events(t.border);
        let _ = app.emit_to(label.as_str(), "overlay-state", payload.clone());
        let _ = win.show();
        let _ = win.set_always_on_top(true);
        // Moving onto a monitor with a different DPI can trigger a resize; re-apply.
        place_overlay(&win, *t);
    }
}

pub(crate) fn update_overlay_text(app: &AppHandle, shown: &[(Target, Value)]) {
    for (i, (_, payload)) in shown.iter().enumerate() {
        let label = overlay_label(i);
        let payload = &with_label(payload, &label);
        app.state::<Shell>().overlay_payload.lock().unwrap().insert(label.clone(), payload.clone());
        let _ = app.emit_to(label.as_str(), "overlay-state", payload.clone());
    }
}

fn hide_surplus(app: &AppHandle, keep: &[String]) {
    let none = json!({ "level": "none" });
    for win in overlay_windows(app) {
        let label = win.label().to_string();
        if keep.contains(&label) {
            continue;
        }
        app.state::<Shell>().overlay_payload.lock().unwrap().remove(&label);
        let _ = app.emit_to(label.as_str(), "overlay-state", none.clone());
        let _ = win.hide();
    }
}

/// Hides every overlay window together (snooze, acknowledge, alert end).
pub(crate) fn hide_overlay(app: &AppHandle) {
    OVERLAY_SHOWING.store(false, Ordering::SeqCst);
    app.state::<Shell>().overlay_payload.lock().unwrap().clear();
    hide_surplus(app, &[]);
}

/// What `overlay_state` returns to the overlay window `label`.
pub(crate) fn payload_for(app: &AppHandle, label: &str) -> Value {
    let shell = app.state::<Shell>();
    let payload = shell.overlay_payload.lock().unwrap().get(label).cloned();
    payload.unwrap_or_else(|| json!({ "level": "none" }))
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

    fn mon(name: &str, x: i32, y: i32) -> MonitorInfo {
        MonitorInfo { name: name.into(), pos: PhysicalPosition::new(x, y), size: PhysicalSize::new(1920, 1080) }
    }

    fn summary(ts: &[Target]) -> Vec<(i32, bool)> {
        ts.iter().map(|t| (t.pos.x, t.border)).collect()
    }

    #[test]
    fn one_monitor_always_gets_the_border_frame() {
        let ms = [mon("A", 0, 0)];
        let p = Some(PhysicalPosition::new(0, 0));
        for ft in ["secondary", "primary_border", "all", "A", "missing"] {
            assert_eq!(summary(&choose_targets(&ms, p, ft)), vec![(0, true)], "{ft}");
        }
    }

    #[test]
    fn two_monitors_each_setting() {
        // Primary on the right this time.
        let ms = [mon("P", 1920, 0), mon("S", 0, 0)];
        let p = Some(PhysicalPosition::new(1920, 0));
        assert_eq!(summary(&choose_targets(&ms, p, "secondary")), vec![(0, false)]);
        assert_eq!(summary(&choose_targets(&ms, p, "primary_border")), vec![(1920, true)]);
        assert_eq!(summary(&choose_targets(&ms, p, "all")), vec![(1920, true), (0, false)]);
        assert_eq!(summary(&choose_targets(&ms, p, "S")), vec![(0, false)]);
        assert_eq!(summary(&choose_targets(&ms, p, "P")), vec![(1920, true)]);
        assert_eq!(summary(&choose_targets(&ms, p, "\\\\.\\DISPLAY9")), vec![(0, false)]);
    }

    #[test]
    fn three_monitors_each_setting() {
        // Primary in the middle; secondary picks the left-most other.
        let ms = [mon("M", 0, 0), mon("R", 1920, 0), mon("L", -1920, 0)];
        let p = Some(PhysicalPosition::new(0, 0));
        assert_eq!(summary(&choose_targets(&ms, p, "secondary")), vec![(-1920, false)]);
        assert_eq!(summary(&choose_targets(&ms, p, "primary_border")), vec![(0, true)]);
        assert_eq!(summary(&choose_targets(&ms, p, "all")), vec![(0, true), (-1920, false), (1920, false)]);
        assert_eq!(summary(&choose_targets(&ms, p, "R")), vec![(1920, false)]);
        assert_eq!(summary(&choose_targets(&ms, p, "M")), vec![(0, true)]);
        assert_eq!(summary(&choose_targets(&ms, p, "gone")), vec![(-1920, false)]);
        // Primary left-most.
        let p = Some(PhysicalPosition::new(-1920, 0));
        assert_eq!(summary(&choose_targets(&ms, p, "secondary")), vec![(0, false)]);
        assert_eq!(summary(&choose_targets(&ms, p, "all")), vec![(-1920, true), (0, false), (1920, false)]);
    }

    #[test]
    fn never_zero_targets_when_a_monitor_exists() {
        let all = [mon("A", 0, 0), mon("B", 1920, 0), mon("C", 0, 1080)];
        for n in 1..=3 {
            let ms = &all[..n];
            for p in [None, Some(PhysicalPosition::new(0, 0)), Some(PhysicalPosition::new(5, 5))] {
                for ft in ["secondary", "primary_border", "all", "A", "B", "zzz", ""] {
                    assert!(!choose_targets(ms, p, ft).is_empty(), "{ft} {p:?} {}", ms.len());
                }
            }
        }
        assert!(choose_targets(&[], None, "all").is_empty());
    }

    #[test]
    fn missing_or_mismatched_primary_uses_the_origin_monitor() {
        // DISPLAY2 primary at (0,0), DISPLAY1 to its right; enumeration order varies.
        for ms in [[mon("D1", 2560, 0), mon("D2", 0, 0)], [mon("D2", 0, 0), mon("D1", 2560, 0)]] {
            for p in [None, Some(PhysicalPosition::new(7, 7))] {
                assert_eq!(summary(&choose_targets(&ms, p, "all")), vec![(0, true), (2560, false)], "{p:?}");
                assert_eq!(summary(&choose_targets(&ms, p, "primary_border")), vec![(0, true)], "{p:?}");
                assert_eq!(summary(&choose_targets(&ms, p, "secondary")), vec![(2560, false)], "{p:?}");
                assert_eq!(summary(&choose_targets(&ms, p, "D2")), vec![(0, true)], "{p:?}");
            }
        }
    }

    #[test]
    fn no_origin_monitor_keeps_the_old_fallback() {
        let ms = [mon("A", 100, 0), mon("B", 2020, 0)];
        assert_eq!(summary(&choose_targets(&ms, None, "all")), vec![(100, false), (2020, false)]);
        assert_eq!(summary(&choose_targets(&ms, None, "primary_border")), vec![(100, true)]);
    }

    #[test]
    fn payload_label_is_additive() {
        let t = Target { pos: PhysicalPosition::new(0, 0), size: PhysicalSize::new(1, 1), border: true };
        let base = overlay_payload(&json!({ "active_level": "red" }), None, t);
        let p = with_label(&base, "overlay-1");
        assert_eq!(p["label"], "overlay-1");
        assert_eq!(p["mode"], "border");
        assert_eq!(p.as_object().unwrap().len(), base.as_object().unwrap().len() + 1);
    }

    #[test]
    fn target_description() {
        let ms = [mon("D2", 0, 0), mon("D1", 1920, 0)];
        let ts = choose_targets(&ms, None, "all");
        assert_eq!(
            describe_targets(&ms, None, &ts),
            "overlay targets: primary=none monitors=[D2@0,0 1920x1080, D1@1920,0 1920x1080] -> [0,0 border, 1920,0 full]"
        );
    }

    #[test]
    fn overlay_labels_round_trip() {
        assert_eq!(overlay_label(0), "overlay");
        assert_eq!(overlay_label(2), "overlay-2");
        for i in 0..5 {
            assert!(is_overlay_label(&overlay_label(i)));
        }
        assert!(!is_overlay_label("main") && !is_overlay_label("widget") && !is_overlay_label("overlay-x"));
    }

    #[test]
    fn payload_carries_snooze_length_and_acknowledge() {
        let t = Target { pos: PhysicalPosition::new(0, 0), size: PhysicalSize::new(1, 1), border: false };
        let p = overlay_payload(&json!({ "active_level": "amber", "snooze_seconds": 45 }), None, t);
        assert_eq!(p["snooze_seconds"], 45);
        assert_eq!(p["can_acknowledge"], true);
        assert_eq!(p["mode"], "fullscreen");
        let p = overlay_payload(&json!({ "active_level": "red" }), None, t);
        assert_eq!(p["snooze_seconds"], 15);
        let p = overlay_payload(&json!({ "active_level": "red", "snooze_seconds": 900 }), None, t);
        assert_eq!(p["snooze_seconds"], 120);
    }
}
