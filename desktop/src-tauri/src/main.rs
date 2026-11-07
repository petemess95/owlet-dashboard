// Owlet Dashboard desktop shell: spawns the bundled owlet-server sidecar,
// waits for it to come up on 127.0.0.1:8877, then opens the dashboard, an
// always-on-top vitals widget, a tray icon, and (when SpO2 drops) a flashing
// full-monitor alert overlay with a looping alarm for red.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Mutex, MutexGuard};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tauri::menu::{MenuBuilder, MenuItem, PredefinedMenuItem, SubmenuBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder, Wry,
};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_window_state::StateFlags;

const SERVER_ADDR: (&str, u16) = ("127.0.0.1", 8877);
const ALERT_POLL: Duration = Duration::from_secs(2);
const HTTP_TIMEOUT: Duration = Duration::from_millis(1500);
const FAILS_BEFORE_GIVING_UP: u32 = 3;
/// How long a server outage after an alert keeps warning in amber (matches the
/// sidecar's SIGNAL_LOST_SECONDS), so a dead server can't flash forever.
const SIGNAL_LOST_WINDOW: Duration = Duration::from_secs(600);
const WIDGET_W: f64 = 300.0;
const WIDGET_H: f64 = 120.0;
const WIDGET_MARGIN: f64 = 16.0;
const TRAY_ID: &str = "main";

struct ServerProcess(Mutex<Option<CommandChild>>);

/// Shell-side alert/UI state shared by the alert thread, commands and menus.
struct Shell {
    tray_available: AtomicBool,
    widget_toggle: Mutex<Option<MenuItem<Wry>>>,
    /// Last payload pushed to the overlay page (it fetches this on load).
    overlay_payload: Mutex<Value>,
    /// What the alert thread last applied; cleared to force a re-apply.
    applied: Mutex<AppliedState>,
    snooze_generation: AtomicU64,
}

struct AppliedState {
    key: String,
    level: String,
    snoozed_until: Option<Instant>,
    /// When the server last answered; bounds the unreachable warning.
    last_seen: Option<Instant>,
}

impl Default for Shell {
    fn default() -> Self {
        Self {
            tray_available: AtomicBool::new(false),
            widget_toggle: Mutex::new(None),
            overlay_payload: Mutex::new(json!({ "level": "none" })),
            applied: Mutex::new(AppliedState {
                key: "none".into(),
                level: "none".into(),
                snoozed_until: None,
                last_seen: None,
            }),
            snooze_generation: AtomicU64::new(0),
        }
    }
}

impl Shell {
    fn poll_state(&self, generation: u64) -> Option<MutexGuard<'_, AppliedState>> {
        let applied = self.applied.lock().unwrap();
        // Keep this lock through all UI/audio effects: snooze cannot finish
        // between validation and starting an old alert.
        (self.snooze_generation.load(Ordering::SeqCst) == generation).then_some(applied)
    }

    fn snooze(&self, action: impl FnOnce(&mut AppliedState)) {
        let mut applied = self.applied.lock().unwrap();
        self.snooze_generation.fetch_add(1, Ordering::SeqCst);
        action(&mut applied);
        // Also discard GETs begun while the POST was still in flight.
        self.snooze_generation.fetch_add(1, Ordering::SeqCst);
    }
}

/// Breadcrumbs for "it ran but nothing appeared" reports — a windowed-subsystem
/// exe has no console, so failures are invisible without this.
fn log_line(msg: &str) {
    let dir = std::env::var("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("owlet-dashboard");
    let _ = std::fs::create_dir_all(&dir);
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(dir.join("desktop.log")) {
        let ts = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        let _ = writeln!(file, "[{ts}] {msg}");
    }
}

fn kill_sidecar(app: &AppHandle) {
    if let Some(child) = app.state::<ServerProcess>().0.lock().unwrap().take() {
        log_line("stopping sidecar");
        let _ = child.kill();
    }
}

fn launcher_url() -> String {
    format!("http://{}:{}/desktop", SERVER_ADDR.0, SERVER_ADDR.1)
}

// ---------------------------------------------------------------------------
// Localhost HTTP. HTTP/1.0 + Connection: close keeps the response un-chunked,
// so a tiny std-only client is enough (no TLS, no extra crate). Desktop mode
// treats every 127.0.0.1 request as the local admin, so no credentials.
// ---------------------------------------------------------------------------

fn http_json(method: &str, path: &str, body: Option<&str>) -> Result<Value, String> {
    let addr = SocketAddr::from(([127, 0, 0, 1], SERVER_ADDR.1));
    let mut stream = TcpStream::connect_timeout(&addr, HTTP_TIMEOUT).map_err(|e| e.to_string())?;
    let _ = stream.set_read_timeout(Some(HTTP_TIMEOUT));
    let _ = stream.set_write_timeout(Some(HTTP_TIMEOUT));
    let body = body.unwrap_or("");
    let request = format!(
        "{method} {path} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nUser-Agent: owlet-desktop\r\n\
         Content-Type: application/json\r\nContent-Length: {len}\r\nConnection: close\r\n\r\n{body}",
        port = SERVER_ADDR.1,
        len = body.len(),
    );
    stream.write_all(request.as_bytes()).map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&raw);
    let (head, payload) = text.split_once("\r\n\r\n").ok_or("malformed response")?;
    let status = head.split_whitespace().nth(1).unwrap_or("");
    if status != "200" {
        return Err(format!("HTTP {status}"));
    }
    serde_json::from_str(payload).map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// Alarm sound: PlaySoundW from memory, looping. Plays at system volume; if
// Windows is muted there's no sound (we don't unmute).
// ---------------------------------------------------------------------------

static ALARM_WAV: &[u8] = include_bytes!("../assets/alarm.wav");
// Keep playback and its flag in the same critical section so a concurrent
// stop cannot finish before an in-flight start actually begins playback.
static ALARM: Mutex<bool> = Mutex::new(false);

fn start_alarm() {
    let mut playing = ALARM.lock().unwrap();
    if *playing {
        return;
    }
    #[cfg(windows)]
    unsafe {
        use windows::core::PCWSTR;
        use windows::Win32::Media::Audio::{PlaySoundW, SND_ASYNC, SND_LOOP, SND_MEMORY};
        let _ = PlaySoundW(PCWSTR(ALARM_WAV.as_ptr().cast()), None, SND_MEMORY | SND_ASYNC | SND_LOOP);
    }
    *playing = true;
}

fn stop_alarm() {
    let mut playing = ALARM.lock().unwrap();
    if !*playing {
        return;
    }
    #[cfg(windows)]
    unsafe {
        use windows::core::PCWSTR;
        use windows::Win32::Media::Audio::{PlaySoundW, SND_FLAGS};
        // A null sound name stops whatever this process is playing.
        let _ = PlaySoundW(PCWSTR::null(), None, SND_FLAGS(0));
    }
    *playing = false;
}

// ---------------------------------------------------------------------------
// Overlay window
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq)]
struct Target {
    pos: PhysicalPosition<i32>,
    size: PhysicalSize<u32>,
    border: bool,
}

/// Re-enumerated before every show so a monitor plugged in or out mid-session
/// is handled. 2+ monitors: the left-most non-primary, fully covered.
/// One monitor: a click-through border frame around the primary.
fn pick_target(app: &AppHandle) -> Option<Target> {
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
fn ensure_overlay(app: &AppHandle) -> Option<WebviewWindow> {
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

fn place_overlay(win: &WebviewWindow, t: Target) {
    // Physical units throughout: avoids mixed-DPI scaling surprises.
    let _ = win.set_position(t.pos);
    let _ = win.set_size(t.size);
}

fn overlay_payload(state: &Value, dev: Option<&Value>, t: Target) -> Value {
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

fn show_overlay(app: &AppHandle, payload: Value, t: Target) {
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

fn update_overlay_text(app: &AppHandle, payload: Value) {
    *app.state::<Shell>().overlay_payload.lock().unwrap() = payload.clone();
    let _ = app.emit_to("overlay", "overlay-state", payload);
}

fn hide_overlay(app: &AppHandle) {
    let payload = json!({ "level": "none" });
    *app.state::<Shell>().overlay_payload.lock().unwrap() = payload.clone();
    if let Some(win) = app.get_webview_window("overlay") {
        let _ = app.emit_to("overlay", "overlay-state", payload);
        let _ = win.hide();
    }
}

// ---------------------------------------------------------------------------
// Alert thread
// ---------------------------------------------------------------------------

fn set_tooltip(app: &AppHandle, text: &str) {
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_tooltip(Some(text));
    }
}

fn tooltip_for(state: &Value, dev: Option<&Value>) -> String {
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

fn alert_loop(app: AppHandle) {
    let mut failures: u32 = 0;
    loop {
        let generation = app.state::<Shell>().snooze_generation.load(Ordering::SeqCst);
        match http_json("GET", "/api/alert-state", None) {
            Ok(state) => {
                if failures >= FAILS_BEFORE_GIVING_UP {
                    // The widget may be showing a webview error page from
                    // while the server was down: reload it.
                    if let Some(w) = app.get_webview_window("widget") {
                        let _ = w.eval("location.reload()");
                    }
                }
                failures = 0;
                apply_state(&app, &state, generation);
            }
            Err(err) => {
                failures += 1;
                if failures == FAILS_BEFORE_GIVING_UP {
                    log_line(&format!("alert-state unreachable: {err}"));
                }
                if failures >= FAILS_BEFORE_GIVING_UP {
                    let shell = app.state::<Shell>();
                    if let Some(mut applied) = shell.poll_state(generation) {
                        let state = unreachable_state(&applied, Instant::now());
                        apply_ui(&app, &state, &mut applied);
                    };
                }
            }
        }
        std::thread::sleep(ALERT_POLL);
    }
}

fn unreachable_state(applied: &AppliedState, now: Instant) -> Value {
    let snoozed = applied.snoozed_until.is_some_and(|until| now < until);
    let recent = applied.last_seen.is_some_and(|seen| now.saturating_duration_since(seen) < SIGNAL_LOST_WINDOW);
    let had_alert = recent && matches!(applied.level.as_str(), "amber" | "red");
    json!({
        "level": if had_alert { "amber" } else { "none" },
        "active_level": if had_alert && !snoozed { "amber" } else { "none" },
        "signal_lost": had_alert,
        "snoozed": snoozed,
        "message": "Dashboard server not responding",
        "devices": [],
    })
}

fn apply_state(app: &AppHandle, state: &Value, generation: u64) {
    let shell = app.state::<Shell>();
    let Some(mut applied) = shell.poll_state(generation) else { return };
    applied.level = state["level"].as_str().unwrap_or("none").into();
    applied.last_seen = Some(Instant::now());
    applied.snoozed_until = if state["snoozed"].as_bool() == Some(true) {
        Some(Instant::now() + Duration::from_secs(state["snooze_seconds_left"].as_u64().unwrap_or(0)))
    } else {
        None
    };
    apply_ui(app, state, &mut applied);
}

fn apply_ui(app: &AppHandle, state: &Value, applied: &mut AppliedState) {
    let active = state["active_level"].as_str().unwrap_or("none").to_string();
    let level = state["level"].as_str().unwrap_or("none");
    let devices = state["devices"].as_array().cloned().unwrap_or_default();
    // Show the device driving the alert, else the first device.
    let dev = devices
        .iter()
        .find(|d| d["level"].as_str() == Some(level) && level != "none" && d["signal_lost"].as_bool() == Some(true))
        .or_else(|| devices.iter().find(|d| d["level"].as_str() == Some(level) && level != "none"))
        .or_else(|| devices.first());
    set_tooltip(app, &tooltip_for(state, dev));

    if active == "amber" || active == "red" {
        if active == "red" {
            start_alarm();
        } else {
            stop_alarm();
        }
        let Some(target) = pick_target(app) else { return };
        let key = format!(
            "{active}|{},{}|{}x{}|{}",
            target.pos.x, target.pos.y, target.size.width, target.size.height, target.border
        );
        let payload = overlay_payload(state, dev, target);
        let changed = applied.key != key;
        if changed {
            show_overlay(app, payload, target);
            applied.key = key;
        } else {
            update_overlay_text(app, payload);
        }
    } else {
        let changed = applied.key != "none";
        if changed {
            hide_overlay(app);
            applied.key = "none".into();
        }
        stop_alarm();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{mpsc, Arc};

    #[test]
    fn tooltip_distinguishes_an_unlinked_account_from_remote_viewing() {
        let mut state = json!({ "collecting": false, "remote_mode": false });
        assert_eq!(tooltip_for(&state, None), "Owlet Dashboard (No Owlet account linked)");
        state["remote_mode"] = json!(true);
        assert_eq!(tooltip_for(&state, None), "Owlet Dashboard (viewing a remote server)");
    }

    #[test]
    fn snooze_rejects_old_results_and_allows_the_next_poll_even_if_post_failed() {
        let shell = Shell::default();
        let generation = shell.snooze_generation.load(Ordering::SeqCst);
        shell.applied.lock().unwrap().key = "red".into();
        shell.snooze(|applied| applied.key.clear());
        assert!(shell.poll_state(generation).is_none());
        let generation = shell.snooze_generation.load(Ordering::SeqCst);
        let mut applied = shell.poll_state(generation).unwrap();
        assert!(applied.key.is_empty());
        applied.key = "red".into();
    }

    #[test]
    fn poll_started_during_snooze_post_is_also_rejected() {
        let shell = Arc::new(Shell::default());
        let (posting, during_post) = mpsc::channel();
        let (started, poll_started) = mpsc::channel();
        let poll_shell = shell.clone();
        let poll = std::thread::spawn(move || {
            during_post.recv().unwrap();
            let generation = poll_shell.snooze_generation.load(Ordering::SeqCst);
            started.send(()).unwrap();
            poll_shell.poll_state(generation).is_some()
        });
        shell.snooze(|applied| {
            applied.key.clear();
            posting.send(()).unwrap();
            poll_started.recv().unwrap();
        });
        assert!(!poll.join().unwrap());
    }

    #[test]
    fn disconnection_after_an_alert_warns_in_amber_and_respects_snooze_expiry() {
        let now = Instant::now();
        for level in ["amber", "red"] {
            let applied = AppliedState {
                key: level.into(),
                level: level.into(),
                snoozed_until: Some(now + Duration::from_secs(15)),
                last_seen: Some(now),
            };
            let state = unreachable_state(&applied, now);
            assert_eq!(state["active_level"], "none");
            assert_eq!(state["signal_lost"], true);
            let state = unreachable_state(&applied, now + Duration::from_secs(15));
            assert_eq!(state["active_level"], "amber");
            assert_eq!(state["message"], "Dashboard server not responding");
        }
    }

    #[test]
    fn disconnection_warning_expires_after_the_signal_lost_window() {
        let now = Instant::now();
        let applied = AppliedState {
            key: "red".into(),
            level: "red".into(),
            snoozed_until: None,
            last_seen: Some(now),
        };
        let just_before = unreachable_state(&applied, now + SIGNAL_LOST_WINDOW - Duration::from_secs(1));
        assert_eq!(just_before["active_level"], "amber");
        let after = unreachable_state(&applied, now + SIGNAL_LOST_WINDOW);
        assert_eq!(after["active_level"], "none");
        assert_eq!(after["signal_lost"], false);
        assert_eq!(after["message"], "Dashboard server not responding");
    }

    #[test]
    fn disconnection_without_an_alert_does_not_start_one() {
        let shell = Shell::default();
        let state = unreachable_state(&shell.applied.lock().unwrap(), Instant::now());
        assert_eq!(state["active_level"], "none");
        assert_eq!(state["signal_lost"], false);
    }

    #[test]
    fn signal_lost_payload_hides_held_vitals_and_names_server_disconnection() {
        let target = Target {
            pos: PhysicalPosition::new(0, 0),
            size: PhysicalSize::new(1920, 1080),
            border: true,
        };
        let state = json!({ "active_level": "amber", "signal_lost": true });
        let dev = json!({ "signal_lost": true, "spo2": 80, "account_name": "Baby" });
        let payload = overlay_payload(&state, Some(&dev), target);
        assert_eq!(payload["signal_lost"], true);
        assert!(payload["spo2"].is_null());
        assert_eq!(tooltip_for(&state, Some(&dev)), "Owlet \u{2014} Signal lost");

        let shell = Shell::default();
        let mut applied = shell.applied.lock().unwrap();
        applied.level = "red".into();
        applied.last_seen = Some(Instant::now());
        let state = unreachable_state(&applied, Instant::now());
        let payload = overlay_payload(&state, None, target);
        assert_eq!(payload["level"], "amber");
        assert_eq!(payload["message"], "Dashboard server not responding");
    }

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

// ---------------------------------------------------------------------------
// Window helpers + commands (shared by the tray menu)
// ---------------------------------------------------------------------------

fn do_show_main(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

fn sync_widget_toggle(app: &AppHandle) {
    let visible = app
        .get_webview_window("widget")
        .and_then(|w| w.is_visible().ok())
        .unwrap_or(false);
    if let Some(item) = app.state::<Shell>().widget_toggle.lock().unwrap().as_ref() {
        let _ = item.set_text(if visible { "Hide widget" } else { "Show widget" });
    }
}

fn do_hide_widget(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("widget") {
        let _ = win.hide();
    }
    sync_widget_toggle(app);
}

fn do_toggle_widget(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("widget") {
        if win.is_visible().unwrap_or(false) {
            let _ = win.hide();
        } else {
            let _ = win.show();
        }
    }
    sync_widget_toggle(app);
}

fn do_snooze(app: &AppHandle) {
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

fn do_test_alert(level: &str) {
    let body = format!("{{\"level\":\"{level}\",\"seconds\":20}}");
    if let Err(err) = http_json("POST", "/api/alert/test", Some(&body)) {
        log_line(&format!("test alert failed: {err}"));
    }
}

#[tauri::command]
async fn snooze_alert(app: AppHandle) {
    do_snooze(&app);
}

#[tauri::command]
fn show_main(app: AppHandle) {
    do_show_main(&app);
}

#[tauri::command]
fn hide_widget(app: AppHandle) {
    do_hide_widget(&app);
}

#[tauri::command]
fn overlay_state(app: AppHandle) -> Value {
    app.state::<Shell>().overlay_payload.lock().unwrap().clone()
}

/// Declare our AppUserModelID so Windows accepts toast notifications from this
/// process. The installer's Start Menu shortcut carries the same id; without
/// this call the process id doesn't match and Windows silently drops toasts.
#[cfg(windows)]
fn set_app_user_model_id(identifier: &str) {
    use windows::core::PCWSTR;
    use windows::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID;
    let wide: Vec<u16> = identifier.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        if let Err(err) = SetCurrentProcessExplicitAppUserModelID(PCWSTR(wide.as_ptr())) {
            log_line(&format!("SetCurrentProcessExplicitAppUserModelID failed: {err}"));
        }
    }
}

/// Raise a native Windows toast. Called from the dashboard JS for critical
/// alerts. We build the toast directly (no activation handlers) so it matches
/// the code path that actually displays, and fire it off the command thread.
#[tauri::command]
fn show_native_toast(app: AppHandle, title: String, body: String) {
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

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

fn ensure_server(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    // Reuse an already-running server (e.g. dev instance); otherwise spawn the sidecar.
    if TcpStream::connect(SERVER_ADDR).is_err() {
        log_line("spawning sidecar");
        let sidecar = app.shell().sidecar("owlet-server")?;
        let (_rx, child) = sidecar.spawn()?;
        app.state::<ServerProcess>().0.lock().unwrap().replace(child);
        // A cold onefile exe self-extracts before booting — allow up to 45s.
        for _ in 0..300 {
            if TcpStream::connect(SERVER_ADDR).is_ok() {
                break;
            }
            std::thread::sleep(Duration::from_millis(150));
        }
    } else {
        log_line("reusing already-running server on 8877");
    }
    Ok(())
}

fn build_main_window(app: &tauri::App, url: &str) -> tauri::Result<WebviewWindow> {
    // The escape hatch from a remote instance: the remote page is sandboxed
    // from our IPC, so switching backends lives in a native menu that can
    // always navigate the window home to the launcher. Attached to "main"
    // only, so the frameless widget doesn't grow a menu bar.
    let instance = SubmenuBuilder::new(app, "Instance")
        .text("switch", "Switch server\u{2026}")
        .text("reload", "Reload")
        .build()?;
    let menu = MenuBuilder::new(app).item(&instance).build()?;
    // Hand the web UI our own version so it can show which desktop build is
    // running. Runs before page scripts.
    let version = app.package_info().version.to_string();
    let inject = format!(
        "window.__OWLET_APP_VERSION__ = {};",
        serde_json::to_string(&version).unwrap_or_else(|_| "\"\"".into())
    );
    WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse().unwrap()))
        .title("Owlet Dashboard")
        .inner_size(1280.0, 860.0)
        .center()
        .focused(true)
        .menu(menu)
        .initialization_script(&inject)
        .build()
}

/// Bottom-right of the primary monitor's work area, in physical pixels.
fn widget_default_position(app: &AppHandle) -> Option<PhysicalPosition<i32>> {
    let m = app.primary_monitor().ok().flatten()?;
    let scale = m.scale_factor();
    let wa = m.work_area();
    let w = (WIDGET_W * scale).round() as i32;
    let h = (WIDGET_H * scale).round() as i32;
    let margin = (WIDGET_MARGIN * scale).round() as i32;
    Some(PhysicalPosition::new(
        wa.position.x + wa.size.width as i32 - w - margin,
        wa.position.y + wa.size.height as i32 - h - margin,
    ))
}

fn on_any_monitor(app: &AppHandle, pos: PhysicalPosition<i32>) -> bool {
    app.available_monitors()
        .map(|ms| {
            ms.iter().any(|m| {
                let p = m.position();
                let s = m.size();
                pos.x >= p.x
                    && pos.y >= p.y
                    && pos.x < p.x + s.width as i32
                    && pos.y < p.y + s.height as i32
            })
        })
        .unwrap_or(false)
}

fn build_widget(app: &tauri::App) -> tauri::Result<WebviewWindow> {
    let handle = app.handle();
    let url = format!("http://{}:{}/widget", SERVER_ADDR.0, SERVER_ADDR.1);
    let mut builder = WebviewWindowBuilder::new(app, "widget", WebviewUrl::External(url.parse().unwrap()))
        .title("Owlet widget")
        .inner_size(WIDGET_W, WIDGET_H)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .shadow(true)
        .focused(false);
    let default_pos = widget_default_position(handle);
    if let (Some(pos), Some(m)) = (default_pos, handle.primary_monitor().ok().flatten()) {
        let scale = m.scale_factor();
        builder = builder.position(pos.x as f64 / scale, pos.y as f64 / scale);
    }
    let win = builder.build()?;
    // The window-state plugin restores a saved position on creation; if that
    // monitor is gone, bring the widget back on-screen.
    if let (Ok(pos), Some(default_pos)) = (win.outer_position(), default_pos) {
        let center = PhysicalPosition::new(pos.x + 20, pos.y + 20);
        if !on_any_monitor(handle, center) {
            let _ = win.set_position(default_pos);
        }
    }
    Ok(win)
}

fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    // Debug-only fault injection for verifying the dashboard's exit fallback.
    #[cfg(debug_assertions)]
    if std::env::var_os("OWLET_DEBUG_FAIL_TRAY").is_some() {
        return Err(std::io::Error::other("forced tray creation failure").into());
    }
    let widget_toggle = MenuItem::with_id(app, "tray_widget", "Hide widget", true, None::<&str>)?;
    let test = SubmenuBuilder::new(app, "Test alert")
        .text("test_amber", "Amber (20 s)")
        .text("test_red", "Red (20 s)")
        .text("test_stop", "Stop test")
        .build()?;
    let menu = MenuBuilder::new(app)
        .text("tray_show", "Show dashboard")
        .item(&widget_toggle)
        .text("tray_snooze", "Snooze alert (15 s)")
        .item(&test)
        .item(&PredefinedMenuItem::separator(app)?)
        .text("tray_quit", "Quit Owlet Dashboard")
        .build()?;
    *app.state::<Shell>().widget_toggle.lock().unwrap() = Some(widget_toggle);
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

fn handle_menu(app: &AppHandle, id: &str) {
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

fn main() {
    log_line("--- app start ---");
    let app = tauri::Builder::default()
        // Registered first: a second launch just shows this copy's dashboard.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            log_line("second launch: showing existing dashboard");
            do_show_main(app);
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(StateFlags::POSITION | StateFlags::SIZE | StateFlags::MAXIMIZED)
                .with_denylist(&["overlay"])
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            show_native_toast,
            snooze_alert,
            show_main,
            hide_widget,
            overlay_state
        ])
        .manage(ServerProcess(Mutex::new(None)))
        .manage(Shell::default())
        .on_menu_event(|app, event| handle_menu(app, event.id().as_ref()))
        .setup(|app| {
            #[cfg(windows)]
            set_app_user_model_id(&app.config().identifier);
            ensure_server(app)?;
            let reachable = TcpStream::connect(SERVER_ADDR).is_ok();
            log_line(&format!("server reachable: {reachable}; creating windows"));
            let url = format!("http://{}:{}/", SERVER_ADDR.0, SERVER_ADDR.1);
            match build_main_window(app, &url) {
                Ok(win) => {
                    log_line("window created");
                    let _ = win.set_focus();
                    // Auto-update is disabled in this fork: upstream's release
                    // feed would replace this build and drop local changes.
                }
                Err(err) => {
                    // The dashboard still works without our window — hand the
                    // user their browser rather than vanishing silently.
                    log_line(&format!("window creation FAILED: {err}"));
                    let _ = std::process::Command::new("cmd").args(["/C", "start", "", &url]).spawn();
                    return Err(err.into());
                }
            }
            match build_tray(app) {
                Ok(()) => app.state::<Shell>().tray_available.store(true, Ordering::SeqCst),
                Err(err) => log_line(&format!("tray creation failed: {err}")),
            }
            if let Err(err) = build_widget(app) {
                log_line(&format!("widget creation failed: {err}"));
            }
            sync_widget_toggle(app.handle());
            let handle = app.handle().clone();
            std::thread::Builder::new()
                .name("owlet-alerts".into())
                .spawn(move || alert_loop(handle))?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Without a tray, closing the dashboard must remain a way to quit.
            // RunEvent::Exit stops the alarm and sidecar on either exit path.
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                if window.label() == "main"
                    && !window.state::<Shell>().tray_available.load(Ordering::SeqCst)
                {
                    window.app_handle().exit(0);
                    return;
                }
                // Otherwise hide to the tray, including Alt+F4 on the widget.
                let _ = window.hide();
                if window.label() == "widget" {
                    sync_widget_toggle(window.app_handle());
                }
            }
        })
        .build(tauri::generate_context!());

    match app {
        Ok(app) => app.run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                // Covers every shutdown path — a setup failure or crash must
                // not leave an orphaned owlet-server running headless.
                stop_alarm();
                kill_sidecar(app_handle);
                log_line("app exit");
            }
        }),
        Err(err) => {
            log_line(&format!("app failed to build: {err}"));
            std::process::exit(1);
        }
    }
}
