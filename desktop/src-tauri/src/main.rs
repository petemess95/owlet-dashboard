// Owlet Dashboard desktop shell: spawns the bundled owlet-server sidecar,
// waits for it to come up on 127.0.0.1:8877, then opens the dashboard, an
// always-on-top vitals widget, a tray icon, and (when SpO2 drops) a flashing
// full-monitor alert overlay with a looping alarm for red.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod alert_loop;
mod audio;
mod commands;
mod http;
mod overlay;
mod settings;
mod tray;

use alert_loop::AppliedState;

use std::fs::OpenOptions;
use std::io::Write;
use std::net::TcpStream;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Mutex, MutexGuard};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tauri::menu::{MenuBuilder, MenuItem, SubmenuBuilder};
use tauri::{
    AppHandle, Manager, PhysicalPosition, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder, Wry,
};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_window_state::StateFlags;

pub(crate) const SERVER_ADDR: (&str, u16) = ("127.0.0.1", 8877);
const WIDGET_W: f64 = 300.0;
const WIDGET_H: f64 = 120.0;
const WIDGET_MARGIN: f64 = 16.0;

struct ServerProcess(Mutex<Option<CommandChild>>);

/// Shell-side alert/UI state shared by the alert thread, commands and menus.
pub(crate) struct Shell {
    pub(crate) tray_available: AtomicBool,
    pub(crate) widget_toggle: Mutex<Option<MenuItem<Wry>>>,
    /// Last payload pushed to the overlay page (it fetches this on load).
    pub(crate) overlay_payload: Mutex<Value>,
    /// What the alert thread last applied; cleared to force a re-apply.
    pub(crate) applied: Mutex<AppliedState>,
    pub(crate) snooze_generation: AtomicU64,
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
    pub(crate) fn poll_state(&self, generation: u64) -> Option<MutexGuard<'_, AppliedState>> {
        let applied = self.applied.lock().unwrap();
        // Keep this lock through all UI/audio effects: snooze cannot finish
        // between validation and starting an old alert.
        (self.snooze_generation.load(Ordering::SeqCst) == generation).then_some(applied)
    }

    pub(crate) fn snooze(&self, action: impl FnOnce(&mut AppliedState)) {
        let mut applied = self.applied.lock().unwrap();
        self.snooze_generation.fetch_add(1, Ordering::SeqCst);
        action(&mut applied);
        // Also discard GETs begun while the POST was still in flight.
        self.snooze_generation.fetch_add(1, Ordering::SeqCst);
    }
}

/// Breadcrumbs for "it ran but nothing appeared" reports — a windowed-subsystem
/// exe has no console, so failures are invisible without this.
pub(crate) fn log_line(msg: &str) {
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

pub(crate) fn launcher_url() -> String {
    format!("http://{}:{}/desktop", SERVER_ADDR.0, SERVER_ADDR.1)
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

fn main() {
    log_line("--- app start ---");
    let app = tauri::Builder::default()
        // Registered first: a second launch just shows this copy's dashboard.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            log_line("second launch: showing existing dashboard");
            commands::do_show_main(app);
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(StateFlags::POSITION | StateFlags::SIZE | StateFlags::MAXIMIZED)
                .with_denylist(&["overlay"])
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            commands::show_native_toast,
            commands::snooze_alert,
            commands::show_main,
            commands::hide_widget,
            commands::overlay_state,
            commands::get_desktop_settings,
            commands::set_desktop_settings,
            commands::list_monitors
        ])
        .manage(ServerProcess(Mutex::new(None)))
        .manage(Shell::default())
        .manage(settings::DesktopSettings::new(settings::Settings::default()))
        .on_menu_event(|app, event| tray::handle_menu(app, event.id().as_ref()))
        .setup(|app| {
            #[cfg(windows)]
            set_app_user_model_id(&app.config().identifier);
            // Load shell settings before the alert thread starts. Any problem
            // degrades to defaults inside load_from; it never aborts setup.
            let loaded = settings::load_from(&settings::data_dir(), &log_line);
            app.state::<settings::DesktopSettings>().replace(loaded);
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
            match tray::build_tray(app) {
                Ok(()) => app.state::<Shell>().tray_available.store(true, Ordering::SeqCst),
                Err(err) => log_line(&format!("tray creation failed: {err}")),
            }
            if let Err(err) = build_widget(app) {
                log_line(&format!("widget creation failed: {err}"));
            }
            tray::sync_widget_toggle(app.handle());
            let handle = app.handle().clone();
            std::thread::Builder::new()
                .name("owlet-alerts".into())
                .spawn(move || alert_loop::alert_loop(handle))?;
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
                    tray::sync_widget_toggle(window.app_handle());
                }
            }
        })
        .build(tauri::generate_context!());

    match app {
        Ok(app) => app.run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                // Covers every shutdown path — a setup failure or crash must
                // not leave an orphaned owlet-server running headless.
                audio::stop_alarm();
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
