use serde_json::Value;
use tauri::menu::{MenuBuilder, MenuItem, PredefinedMenuItem, SubmenuBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};

use crate::commands::{do_acknowledge, do_show_main, do_snooze, do_test_alert, do_toggle_widget};
use crate::{launcher_url, Shell};

pub(crate) const TRAY_ID: &str = "main";

pub(crate) fn set_tooltip(app: &AppHandle, text: &str) {
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_tooltip(Some(text));
    }
}

pub(crate) fn tooltip_for(state: &Value, dev: Option<&Value>) -> String {
    let active = state["active_level"].as_str().unwrap_or("none");
    if state["snoozed"].as_bool() == Some(true) {
        return "Owlet \u{2014} alert snoozed".into();
    }
    if let Some(message) = state["message"].as_str() {
        return format!("Owlet \u{2014} {message}");
    }
    if active == "red" {
        let pct = dev.and_then(|d| d["spo2"].as_i64()).map(|v| format!(" {v}%")).unwrap_or_default();
        return format!("Owlet \u{2014} RED ALERT{pct}");
    }
    if state["signal_lost"].as_bool() == Some(true) {
        return "Owlet \u{2014} Signal lost".into();
    }
    let spo2 = dev.and_then(|d| d["spo2"].as_i64());
    let pct = spo2.map(|v| format!(" {v}%")).unwrap_or_default();
    if active == "amber" {
        return format!("Owlet \u{2014} Low oxygen{pct}");
    }
    if state["collecting"].as_bool() == Some(false) {
        return if state["remote_mode"].as_bool() == Some(true) {
            "Owlet Dashboard (viewing a remote server)".into()
        } else {
            "Owlet Dashboard (No Owlet account linked)".into()
        };
    }
    match dev {
        Some(d) if d["stale"].as_bool() == Some(true) => "Owlet \u{2014} no recent data".into(),
        Some(d) if d["sock_reporting"].as_bool() == Some(false) => "Owlet \u{2014} sock off".into(),
        Some(d) => {
            let hr = d["heart_rate"].as_i64().map(|v| format!(" \u{00b7} HR {v}")).unwrap_or_default();
            format!("Owlet \u{2014} SpO\u{2082}{pct}{hr}")
        }
        None => "Owlet Dashboard".into(),
    }
}

/// Like `tooltip_for`, but in the plain "all normal" case (the vitals line or
/// plain "Owlet Dashboard") a fault in the armed verdict replaces it. Every
/// alert, snooze, message, signal-lost, not-collecting and stale/sock-off
/// line is kept as is. Tooltips stay under 127 characters.
pub(crate) fn armed_tooltip(state: &Value, dev: Option<&Value>, fault: Option<&str>) -> String {
    let normal = state["snoozed"].as_bool() != Some(true)
        && state["message"].as_str().is_none()
        && !matches!(state["active_level"].as_str(), Some("red" | "amber"))
        && state["signal_lost"].as_bool() != Some(true)
        && state["collecting"].as_bool() != Some(false)
        && !dev.is_some_and(|d| d["stale"].as_bool() == Some(true) || d["sock_reporting"].as_bool() == Some(false));
    let text = match fault {
        Some(f) if normal => format!("Owlet \u{2014} Not armed: {f}"),
        _ => tooltip_for(state, dev),
    };
    text.chars().take(126).collect()
}

pub(crate) fn snooze_label(secs: u64) -> String {
    format!("Snooze alert ({secs} s)")
}

/// Called by the alert thread only when the configured length changes.
pub(crate) fn set_snooze_label(app: &AppHandle, secs: u64) {
    if let Some(item) = app.state::<Shell>().snooze_item.lock().unwrap().as_ref() {
        let _ = item.set_text(snooze_label(secs));
    }
}

pub(crate) fn sync_widget_toggle(app: &AppHandle) {
    let visible = app
        .get_webview_window("widget")
        .and_then(|w| w.is_visible().ok())
        .unwrap_or(false);
    if let Some(item) = app.state::<Shell>().widget_toggle.lock().unwrap().as_ref() {
        let _ = item.set_text(if visible { "Hide widget" } else { "Show widget" });
    }
}

pub(crate) fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    // Debug-only fault injection for verifying the dashboard's exit fallback.
    #[cfg(debug_assertions)]
    if std::env::var_os("OWLET_DEBUG_FAIL_TRAY").is_some() {
        return Err(std::io::Error::other("forced tray creation failure").into());
    }
    let widget_toggle = MenuItem::with_id(app, "tray_widget", "Hide widget", true, None::<&str>)?;
    let secs = app.state::<Shell>().snooze_secs();
    let snooze = MenuItem::with_id(app, "tray_snooze", snooze_label(secs), true, None::<&str>)?;
    let test = SubmenuBuilder::new(app, "Test alert")
        .text("test_amber", "Amber (20 s)")
        .text("test_red", "Red (20 s)")
        .text("test_stop", "Stop test")
        .build()?;
    let menu = MenuBuilder::new(app)
        .text("tray_show", "Show dashboard")
        .item(&widget_toggle)
        .item(&snooze)
        .text("tray_acknowledge", "I'm checking (acknowledge)")
        .item(&test)
        .item(&PredefinedMenuItem::separator(app)?)
        .text("tray_quit", "Quit Owlet Dashboard")
        .build()?;
    *app.state::<Shell>().widget_toggle.lock().unwrap() = Some(widget_toggle);
    *app.state::<Shell>().snooze_item.lock().unwrap() = Some(snooze);
    let mut tray = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip("Owlet Dashboard")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                do_show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

pub(crate) fn handle_menu(app: &AppHandle, id: &str) {
    match id {
        "switch" => {
            if let (Some(win), Ok(url)) = (app.get_webview_window("main"), launcher_url().parse::<tauri::Url>()) {
                let _ = win.navigate(url);
            }
        }
        "reload" => {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.eval("location.reload()");
            }
        }
        "tray_show" => do_show_main(app),
        "tray_widget" => do_toggle_widget(app),
        // HTTP off the main thread so the UI never stalls on the server.
        "tray_snooze" => {
            let app = app.clone();
            std::thread::spawn(move || do_snooze(&app));
        }
        "tray_acknowledge" => {
            let app = app.clone();
            std::thread::spawn(move || do_acknowledge(&app));
        }
        "test_amber" | "test_red" | "test_stop" => {
            let level = match id {
                "test_amber" => "amber",
                "test_red" => "red",
                _ => "none",
            };
            std::thread::spawn(move || do_test_alert(level));
        }
        "tray_quit" => app.exit(0),
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn snooze_label_shows_the_configured_length() {
        assert_eq!(snooze_label(15), "Snooze alert (15 s)");
        assert_eq!(snooze_label(5), "Snooze alert (5 s)");
        assert_eq!(snooze_label(45), "Snooze alert (45 s)");
        assert_eq!(snooze_label(120), "Snooze alert (120 s)");
    }

    #[test]
    fn tooltip_distinguishes_an_unlinked_account_from_remote_viewing() {
        let mut state = json!({ "collecting": false, "remote_mode": false });
        assert_eq!(tooltip_for(&state, None), "Owlet Dashboard (No Owlet account linked)");
        state["remote_mode"] = json!(true);
        assert_eq!(tooltip_for(&state, None), "Owlet Dashboard (viewing a remote server)");
    }

    #[test]
    fn armed_fault_replaces_only_the_normal_line() {
        let fault = Some("Windows is muted");
        let normal = json!({ "active_level": "none", "collecting": true });
        let dev = json!({ "spo2": 97, "heart_rate": 120 });
        assert_eq!(armed_tooltip(&normal, Some(&dev), fault), "Owlet \u{2014} Not armed: Windows is muted");
        assert_eq!(armed_tooltip(&normal, None, fault), "Owlet \u{2014} Not armed: Windows is muted");
        assert_eq!(armed_tooltip(&normal, Some(&dev), None), tooltip_for(&normal, Some(&dev)));
        for state in [
            json!({ "active_level": "red" }),
            json!({ "active_level": "amber" }),
            json!({ "snoozed": true }),
            json!({ "message": "Dashboard server not responding" }),
            json!({ "signal_lost": true }),
            json!({ "collecting": false }),
        ] {
            assert_eq!(armed_tooltip(&state, Some(&dev), fault), tooltip_for(&state, Some(&dev)));
        }
        let stale = json!({ "stale": true });
        assert_eq!(armed_tooltip(&normal, Some(&stale), fault), "Owlet \u{2014} no recent data");
        let long = "x".repeat(300);
        assert!(armed_tooltip(&normal, None, Some(&long)).chars().count() < 127);
    }
}
