// Owlet Dashboard desktop shell: spawns the bundled owlet-server sidecar,
// waits for it to come up on 127.0.0.1:8877, then opens the dashboard, an
// always-on-top vitals widget, a tray icon, and (when SpO2 drops) a flashing
// full-monitor alert overlay with a looping alarm for red.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod alert_loop;
mod armed;
mod audio;
mod autostart;
mod commands;
mod controller;
mod controller_hid;
mod hotkeys;
mod http;
mod overlay;
mod settings;
mod tray;
mod widget;

use alert_loop::AppliedState;

use std::collections::HashMap;
use std::fs::OpenOptions;
use std::io::Write;
use std::net::TcpStream;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::Value;
use tauri::menu::{MenuBuilder, MenuItem, SubmenuBuilder};
use tauri::{
    AppHandle, Manager, PhysicalPosition, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder, Wry,
};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_window_state::{StateFlags, WindowExt};

pub(crate) const SERVER_ADDR: (&str, u16) = ("127.0.0.1", 8877);
const WIDGET_W: f64 = 300.0;
const WIDGET_H: f64 = 120.0;
const WIDGET_MARGIN: f64 = 16.0;

/// The sidecar we spawned (initial or watchdog restart), with a flag set
/// once it has exited.
pub(crate) struct Sidecar {
    child: CommandChild,
    exited: Arc<AtomicBool>,
}

pub(crate) struct ServerProcess(Mutex<Option<Sidecar>>);

/// Shell-side alert/UI state shared by the alert thread, commands and menus.
pub(crate) struct Shell {
    pub(crate) tray_available: AtomicBool,
    pub(crate) widget_toggle: Mutex<Option<MenuItem<Wry>>>,
    /// Last payload pushed to each overlay window, by label (each page
    /// fetches its own on load; a missing label means "none").
    pub(crate) overlay_payload: Mutex<HashMap<String, Value>>,
    /// The tray's "Snooze alert (N s)" entry, relabelled when N changes.
    pub(crate) snooze_item: Mutex<Option<MenuItem<Wry>>>,
    /// Configured snooze length from the last good /api/alert-state.
    pub(crate) snooze_seconds: AtomicU64,
    /// What the alert thread last applied; cleared to force a re-apply.
    pub(crate) applied: Mutex<AppliedState>,
    pub(crate) snooze_generation: AtomicU64,
    /// Latest armed verdict, `{armed, problems}`; see armed.rs.
    pub(crate) armed: Mutex<Value>,
}

impl Default for Shell {
    fn default() -> Self {
        Self {
            tray_available: AtomicBool::new(false),
            widget_toggle: Mutex::new(None),
            overlay_payload: Mutex::new(HashMap::new()),
            snooze_item: Mutex::new(None),
            snooze_seconds: AtomicU64::new(overlay::DEFAULT_SNOOZE_SECONDS),
            applied: Mutex::new(AppliedState {
                key: "none".into(),
                level: "none".into(),
                snoozed_until: None,
                last_seen: None,
            }),
            snooze_generation: AtomicU64::new(0),
            armed: Mutex::new(armed::pending_verdict()),
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

    pub(crate) fn armed_verdict(&self) -> Value {
        self.armed.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }

    pub(crate) fn set_armed_verdict(&self, verdict: Value) {
        *self.armed.lock().unwrap_or_else(|p| p.into_inner()) = verdict;
    }

    /// Snooze length for local silencing: the configured value, 15 s until
    /// the server has been heard.
    pub(crate) fn snooze_secs(&self) -> u64 {
        self.snooze_seconds.load(Ordering::SeqCst)
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
    let current = app.state::<ServerProcess>().0.lock().unwrap_or_else(|p| p.into_inner()).take();
    if let Some(sidecar) = current {
        log_line("stopping sidecar");
        let _ = sidecar.child.kill();
    }
}

/// Spawn the bundled owlet-server and record it as the current sidecar
/// (replacing an exited one). Does not wait for it to come up.
pub(crate) fn spawn_sidecar(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    log_line("spawning sidecar");
    let (mut rx, child) = app.shell().sidecar("owlet-server")?.spawn()?;
    let exited = Arc::new(AtomicBool::new(false));
    let flag = exited.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            if let CommandEvent::Terminated(payload) = event {
                log_line(&format!("sidecar exited: code {:?}, signal {:?}", payload.code, payload.signal));
                break;
            }
        }
        flag.store(true, Ordering::SeqCst);
    });
    let previous = app
        .state::<ServerProcess>()
        .0
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .replace(Sidecar { child, exited });
    if let Some(old) = previous {
        if !old.exited.load(Ordering::SeqCst) {
            let _ = old.child.kill();
        }
    }
    Ok(())
}

/// True while the sidecar we spawned is still running; false when it has
/// exited or we never spawned one (reused an existing server).
pub(crate) fn sidecar_running(app: &AppHandle) -> bool {
    app.state::<ServerProcess>()
        .0
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .as_ref()
        .is_some_and(|s| !s.exited.load(Ordering::SeqCst))
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
        spawn_sidecar(app.handle())?;
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

fn build_main_window(app: &tauri::App, url: &str, visible: bool) -> tauri::Result<WebviewWindow> {
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
        // Hidden at login: the widget and alert loop are unaffected.
        .visible(visible)
        .focused(visible)
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
    // Only the position is restored for the widget (it is skipped by the
    // plugin's initial restore): its size always comes from widget_size.
    if let Err(err) = win.restore_state(StateFlags::POSITION) {
        log_line(&format!("widget position restore failed: {err}"));
    }
    // If the saved position's monitor is gone, bring the widget back on-screen.
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
        // Rust-only: no window is granted the plugin's JS permissions.
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![autostart::AUTOSTART_ARG]),
        ))
        // Registration is Rust-only (no JS permission is granted to any window).
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(hotkeys::handle_event)
                .build(),
        )
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(StateFlags::POSITION | StateFlags::SIZE | StateFlags::MAXIMIZED)
                .with_filter(|label| !overlay::is_overlay_label(label))
                .skip_initial_state(widget::LABEL)
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            commands::show_native_toast,
            commands::snooze_alert,
            commands::acknowledge_alert,
            commands::show_main,
            commands::hide_widget,
            commands::overlay_state,
            commands::get_desktop_settings,
            commands::set_desktop_settings,
            commands::list_monitors,
            commands::list_audio_devices,
            commands::armed_state,
            commands::capture_controller_button,
            commands::set_widget_mode,
            commands::show_widget_menu
        ])
        .manage(ServerProcess(Mutex::new(None)))
        .manage(Shell::default())
        .manage(hotkeys::Hotkeys::default())
        .manage(widget::WidgetState::default())
        .manage(settings::DesktopSettings::new(settings::Settings::default()))
        .on_menu_event(|app, event| tray::handle_menu(app, event.id().as_ref()))
        .setup(|app| {
            #[cfg(windows)]
            set_app_user_model_id(&app.config().identifier);
            // Load shell settings before the alert thread starts. Any problem
            // degrades to defaults inside load_from; it never aborts setup.
            let loaded = settings::load_from(&settings::data_dir(), &log_line);
            app.state::<settings::DesktopSettings>().replace(loaded.clone());
            // Saved hotkeys: a combination that fails is logged, never fatal.
            hotkeys::register_saved(app.handle(), &loaded);
            // Keep the OS Run entry in step with the saved setting, off this
            // thread so it can never delay the server or the alert loop.
            let sync_handle = app.handle().clone();
            let saved_autostart = loaded.autostart;
            std::thread::spawn(move || {
                autostart::sync_at_startup(&autostart::PluginBackend(&sync_handle), saved_autostart, &log_line);
            });
            let mode = autostart::startup_mode(
                autostart::is_autostart_launch(std::env::args().skip(1)),
                loaded.first_run_test_done,
            );
            log_line(&format!("startup mode: {mode:?}"));
            ensure_server(app)?;
            let reachable = TcpStream::connect(SERVER_ADDR).is_ok();
            log_line(&format!("server reachable: {reachable}; creating windows"));
            let url = format!("http://{}:{}/", SERVER_ADDR.0, SERVER_ADDR.1);
            let main_url = autostart::main_window_url(&url, mode);
            match build_main_window(app, &main_url, mode != autostart::Startup::Hidden) {
                Ok(win) => {
                    log_line("window created");
                    if mode != autostart::Startup::Hidden {
                        let _ = win.set_focus();
                    }
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
            match build_widget(app) {
                // Saved size and opacity, now the position is restored.
                Ok(_) => widget::apply_startup(app.handle()),
                Err(err) => log_line(&format!("widget creation failed: {err}")),
            }
            widget::spawn_snapper(app.handle().clone());
            tray::sync_widget_toggle(app.handle());
            let handle = app.handle().clone();
            std::thread::Builder::new()
                .name("owlet-alerts".into())
                .spawn(move || alert_loop::alert_loop(handle))?;
            // Parked until an alert shows; makes no XInput calls before then.
            controller::spawn_poller(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == widget::LABEL {
                match event {
                    tauri::WindowEvent::Moved(_) => widget::moved(window.app_handle()),
                    // A restore or resize can rewrite the window style.
                    tauri::WindowEvent::Resized(_) => widget::reassert_opacity(window.app_handle()),
                    _ => {}
                }
            }
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
