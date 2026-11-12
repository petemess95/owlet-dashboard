//! Widget window modes: size (micro/compact/expanded), whole-window opacity,
//! click-through ("ghost"), the native right-click menu and edge snapping.
//!
//! Safety rule: an amber or red alert always makes the widget clickable and
//! fully opaque (see `Mode::on_alert`, called by the alert loop after the
//! overlay and alarm have been started). Every window call is queued to the
//! main thread with `run_on_main_thread`, so the alert thread never waits on
//! the UI and never creates a window.

use std::sync::mpsc::{channel, RecvTimeoutError, Sender};
use std::sync::{Mutex, MutexGuard};
use std::time::Duration;

use serde_json::{json, Map, Value};
use tauri::menu::{CheckMenuItem, MenuBuilder, SubmenuBuilder};
use tauri::{AppHandle, LogicalSize, Manager, PhysicalPosition, WebviewWindow, Wry};

use crate::settings::{data_dir, DesktopSettings, Settings, SettingsError};
use crate::log_line;

pub(crate) const LABEL: &str = "widget";
/// Snap distance in physical pixels (not scaled by DPI).
pub(crate) const SNAP_PX: i32 = 12;
const SNAP_DEBOUNCE: Duration = Duration::from_millis(250);
pub(crate) const OPACITY_CHOICES: [u8; 4] = [100, 80, 60, 40];
pub(crate) const CLICK_THROUGH_MENU_LABEL: &str = "Click-through (turn off from tray or hotkey)";
pub(crate) const TRAY_CLICK_THROUGH_ID: &str = "tray_click_through";

/// Logical size of each widget size.
pub(crate) fn size_dims(size: &str) -> Option<(f64, f64)> {
    match size {
        "micro" => Some((110.0, 32.0)),
        "compact" => Some((300.0, 120.0)),
        "expanded" => Some((300.0, 200.0)),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Geometry (physical pixels)
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Rect {
    pub(crate) x: i32,
    pub(crate) y: i32,
    pub(crate) w: i32,
    pub(crate) h: i32,
}

impl Rect {
    pub(crate) fn new(x: i32, y: i32, w: i32, h: i32) -> Self {
        Self { x, y, w, h }
    }
    fn right(&self) -> i32 {
        self.x + self.w
    }
    fn bottom(&self) -> i32 {
        self.y + self.h
    }
    fn overlap(&self, o: &Rect) -> i64 {
        let w = (self.right().min(o.right()) - self.x.max(o.x)).max(0) as i64;
        let h = (self.bottom().min(o.bottom()) - self.y.max(o.y)).max(0) as i64;
        w * h
    }
    fn centre_dist2(&self, o: &Rect) -> i64 {
        let dx = (self.x as i64 * 2 + self.w as i64) - (o.x as i64 * 2 + o.w as i64);
        let dy = (self.y as i64 * 2 + self.h as i64) - (o.y as i64 * 2 + o.h as i64);
        dx * dx + dy * dy
    }
}

/// The work area of the monitor the window is on: the one it overlaps most,
/// else the nearest one (a window left on an unplugged monitor).
/// `monitors` is `(monitor bounds, work area)`.
pub(crate) fn pick_work_area(win: Rect, monitors: &[(Rect, Rect)]) -> Option<Rect> {
    let best = monitors.iter().max_by_key(|(m, _)| win.overlap(m));
    match best {
        Some((m, work)) if win.overlap(m) > 0 => Some(*work),
        _ => monitors.iter().min_by_key(|(m, _)| win.centre_dist2(m)).map(|(_, w)| *w),
    }
}

/// Snap each edge within `threshold` of the work-area edge flush to it.
pub(crate) fn snap_position(win: Rect, work: Rect, threshold: i32) -> (i32, i32) {
    let x = if (win.x - work.x).abs() <= threshold {
        work.x
    } else if (work.right() - win.right()).abs() <= threshold {
        work.right() - win.w
    } else {
        win.x
    };
    let y = if (win.y - work.y).abs() <= threshold {
        work.y
    } else if (work.bottom() - win.bottom()).abs() <= threshold {
        work.bottom() - win.h
    } else {
        win.y
    };
    (x, y)
}

/// Where a window resized to `w`x`h` goes: it keeps the corner nearest the
/// work-area edges (so a widget snapped right/bottom stays snapped), then is
/// clamped fully into the work area (top-left wins if it can't fit).
pub(crate) fn place_resized(old: Rect, w: i32, h: i32, work: Rect) -> (i32, i32) {
    let anchor_right = work.right() - old.right() < old.x - work.x;
    let anchor_bottom = work.bottom() - old.bottom() < old.y - work.y;
    let x = if anchor_right { old.right() - w } else { old.x };
    let y = if anchor_bottom { old.bottom() - h } else { old.y };
    (x.min(work.right() - w).max(work.x), y.min(work.bottom() - h).max(work.y))
}

/// The invisible frame around the visible widget: outer minus visible, per
/// side. Windows keeps an invisible resize border (about 8 px left, right and
/// bottom, 0 top) outside what the widget draws.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct Insets {
    pub(crate) left: i32,
    pub(crate) top: i32,
    pub(crate) right: i32,
    pub(crate) bottom: i32,
}

pub(crate) fn insets(outer: Rect, visible: Rect) -> Insets {
    Insets {
        left: visible.x - outer.x,
        top: visible.y - outer.y,
        right: outer.right() - visible.right(),
        bottom: outer.bottom() - visible.bottom(),
    }
}

/// The outer (window) position that puts the visible rect at `(x, y)`.
pub(crate) fn visible_to_outer(x: i32, y: i32, ins: Insets) -> (i32, i32) {
    (x - ins.left, y - ins.top)
}

/// Snap the visible rect to the work area of the monitor it is on; returns the
/// outer position to set, or None when nothing moves.
pub(crate) fn snap_outer(outer: Rect, visible: Rect, monitors: &[(Rect, Rect)]) -> Option<(i32, i32)> {
    let work = pick_work_area(visible, monitors)?;
    let (x, y) = snap_position(visible, work, SNAP_PX);
    let pos = visible_to_outer(x, y, insets(outer, visible));
    (pos != (outer.x, outer.y)).then_some(pos)
}

// ---------------------------------------------------------------------------
// Mode decisions (pure)
// ---------------------------------------------------------------------------

/// Runtime widget mode. Click-through is never stored; it starts off.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct Mode {
    pub(crate) click_through: bool,
    /// Amber or red is active (from the last alert poll).
    pub(crate) alerting: bool,
    /// Opacity last sent to Windows; None until the first apply.
    pub(crate) applied_opacity: Option<u8>,
}

/// Window calls a decision needs; empty means "touch nothing".
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct Effects {
    pub(crate) click_through: Option<bool>,
    pub(crate) opacity: Option<u8>,
}

impl Effects {
    pub(crate) fn is_empty(&self) -> bool {
        self.click_through.is_none() && self.opacity.is_none()
    }
}

impl Mode {
    pub(crate) fn effective_opacity(&self, saved: u8) -> u8 {
        if self.alerting {
            100
        } else {
            saved
        }
    }

    fn sync_opacity(&mut self, saved: u8) -> Option<u8> {
        let want = self.effective_opacity(saved);
        (self.applied_opacity != Some(want)).then(|| {
            self.applied_opacity = Some(want);
            want
        })
    }

    /// Each alert poll: amber/red forces click-through off (it stays off
    /// afterwards) and opacity to 100; the saved opacity returns after.
    pub(crate) fn on_alert(&mut self, alerting: bool, saved: u8) -> Effects {
        self.alerting = alerting;
        let click_through = (alerting && self.click_through).then(|| {
            self.click_through = false;
            false
        });
        Effects { click_through, opacity: self.sync_opacity(saved) }
    }

    /// The saved opacity changed (or startup).
    pub(crate) fn on_saved_opacity(&mut self, saved: u8) -> Effects {
        Effects { click_through: None, opacity: self.sync_opacity(saved) }
    }

    /// A user request. Turning it on during an amber/red alert is refused.
    pub(crate) fn request_click_through(&mut self, on: bool) -> Result<Effects, &'static str> {
        if on && self.alerting {
            return Err("refused while an amber or red alert is active");
        }
        if self.click_through == on {
            return Ok(Effects::default());
        }
        self.click_through = on;
        Ok(Effects { click_through: Some(on), opacity: None })
    }
}

/// Validate a `set_widget_mode` call: the settings patch for size/opacity
/// and the click-through flag. Size/opacity are checked by the settings
/// module when applied, so errors keep its `{error: {field, message}}` shape.
pub(crate) fn mode_patch(
    size: Option<&Value>,
    opacity: Option<&Value>,
    click_through: Option<&Value>,
) -> Result<(Value, Option<bool>), SettingsError> {
    let ct = match click_through {
        None | Some(Value::Null) => None,
        Some(v) => Some(v.as_bool().ok_or_else(|| SettingsError {
            field: "click_through".into(),
            message: "must be true or false".into(),
        })?),
    };
    let mut patch = Map::new();
    if let Some(v) = size.filter(|v| !v.is_null()) {
        patch.insert("widget_size".into(), v.clone());
    }
    if let Some(v) = opacity.filter(|v| !v.is_null()) {
        patch.insert("widget_opacity".into(), v.clone());
    }
    Ok((Value::Object(patch), ct))
}

/// Windows layered-window alpha for a percentage.
pub(crate) fn alpha_byte(pct: u8) -> u8 {
    (u32::from(pct.min(100)) * 255 / 100) as u8
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum MenuAction {
    Size(&'static str),
    Opacity(u8),
    ToggleClickThrough,
    Test(&'static str),
    OpenDashboard,
    Hide,
}

pub(crate) fn menu_action(id: &str) -> Option<MenuAction> {
    Some(match id {
        "wm_size_micro" => MenuAction::Size("micro"),
        "wm_size_compact" => MenuAction::Size("compact"),
        "wm_size_expanded" => MenuAction::Size("expanded"),
        "wm_click_through" | TRAY_CLICK_THROUGH_ID => MenuAction::ToggleClickThrough,
        "wm_test_amber" => MenuAction::Test("amber"),
        "wm_test_red" => MenuAction::Test("red"),
        "wm_open" => MenuAction::OpenDashboard,
        "wm_hide" => MenuAction::Hide,
        _ => {
            let pct: u8 = id.strip_prefix("wm_opacity_")?.parse().ok()?;
            OPACITY_CHOICES.contains(&pct).then_some(MenuAction::Opacity(pct))?
        }
    })
}

// ---------------------------------------------------------------------------
// Runtime
// ---------------------------------------------------------------------------

#[derive(Default)]
pub(crate) struct WidgetState {
    mode: Mutex<Mode>,
    tray_item: Mutex<Option<CheckMenuItem<Wry>>>,
    snap_tx: Mutex<Option<Sender<()>>>,
}

impl WidgetState {
    fn mode(&self) -> MutexGuard<'_, Mode> {
        // A panic elsewhere must never stop the alert override.
        self.mode.lock().unwrap_or_else(|p| p.into_inner())
    }

    pub(crate) fn set_tray_item(&self, item: CheckMenuItem<Wry>) {
        *self.tray_item.lock().unwrap_or_else(|p| p.into_inner()) = Some(item);
    }
}

fn widget(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window(LABEL)
}

fn saved(app: &AppHandle) -> Settings {
    app.state::<DesktopSettings>().get()
}

fn monitor_rects(app: &AppHandle) -> Vec<(Rect, Rect)> {
    app.available_monitors()
        .unwrap_or_default()
        .iter()
        .map(|m| {
            let (p, s) = (m.position(), m.size());
            let wa = m.work_area();
            (
                Rect::new(p.x, p.y, s.width as i32, s.height as i32),
                Rect::new(wa.position.x, wa.position.y, wa.size.width as i32, wa.size.height as i32),
            )
        })
        .collect()
}

/// `(outer window rect, visible rect)` in physical pixels. The visible rect is
/// the client area, which the webview fills; the outer rect also holds the
/// invisible resize border.
fn window_rects(win: &WebviewWindow) -> Option<(Rect, Rect)> {
    let p = win.outer_position().ok()?;
    let s = win.outer_size().ok()?;
    let ip = win.inner_position().ok()?;
    let is = win.inner_size().ok()?;
    Some((
        Rect::new(p.x, p.y, s.width as i32, s.height as i32),
        Rect::new(ip.x, ip.y, is.width as i32, is.height as i32),
    ))
}

/// Queue window calls on the main thread; never blocks the caller.
fn push_effects(app: &AppHandle, eff: Effects, click_through_now: bool, opacity_now: u8) {
    if eff.is_empty() {
        return;
    }
    let handle = app.clone();
    let result = app.run_on_main_thread(move || {
        let Some(win) = widget(&handle) else { return };
        if let Some(on) = eff.click_through {
            if let Err(err) = win.set_ignore_cursor_events(on) {
                log_line(&format!("widget: click-through {on} failed: {err}"));
            }
        }
        // Changing click-through rewrites the window's extended style, which
        // drops the layered alpha, so opacity is re-applied after it.
        let opacity = eff.opacity.or(eff.click_through.map(|_| opacity_now));
        if let Some(pct) = opacity {
            if let Err(err) = set_window_alpha(&win, pct, click_through_now) {
                log_line(&format!("widget: opacity {pct}% failed: {err}"));
            }
        }
    });
    if let Err(err) = result {
        log_line(&format!("widget: cannot reach the main thread: {err}"));
    }
    if eff.click_through.is_some() {
        sync_tray(app);
    }
}

#[cfg(windows)]
fn set_window_alpha(win: &WebviewWindow, pct: u8, click_through: bool) -> Result<(), String> {
    use windows::Win32::Foundation::{COLORREF, HWND};
    use windows::Win32::UI::WindowsAndMessaging::{
        GetWindowLongW, SetLayeredWindowAttributes, SetWindowLongW, GWL_EXSTYLE, LWA_ALPHA, WS_EX_LAYERED,
    };
    let raw = win.hwnd().map_err(|e| e.to_string())?;
    let hwnd = HWND(raw.0 as _);
    let layered = WS_EX_LAYERED.0 as i32;
    unsafe {
        let ex = GetWindowLongW(hwnd, GWL_EXSTYLE);
        if pct >= 100 && !click_through {
            // Fully opaque and not ghosted: back to a plain window.
            if ex & layered != 0 {
                SetWindowLongW(hwnd, GWL_EXSTYLE, ex & !layered);
            }
            return Ok(());
        }
        if ex & layered == 0 {
            SetWindowLongW(hwnd, GWL_EXSTYLE, ex | layered);
        }
        SetLayeredWindowAttributes(hwnd, COLORREF(0), alpha_byte(pct), LWA_ALPHA).map_err(|e| e.to_string())
    }
}

#[cfg(not(windows))]
fn set_window_alpha(_win: &WebviewWindow, _pct: u8, _click_through: bool) -> Result<(), String> {
    Ok(())
}

fn sync_tray(app: &AppHandle) {
    let on = app.state::<WidgetState>().mode().click_through;
    let item = app.state::<WidgetState>().tray_item.lock().unwrap_or_else(|p| p.into_inner()).clone();
    if let Some(item) = item {
        // Queued: a menu call from another thread would wait on the main one.
        let _ = app.run_on_main_thread(move || {
            let _ = item.set_checked(on);
        });
    }
}

/// Alert loop hook, called after the overlay and alarm have been started.
/// Only touches Windows when the decision changes something.
pub(crate) fn alert_override(app: &AppHandle, active_level: &str) {
    let alerting = matches!(active_level, "amber" | "red");
    let saved_opacity = saved(app).widget_opacity;
    let state = app.state::<WidgetState>();
    let (eff, ct, op) = {
        let mut m = state.mode();
        let eff = m.on_alert(alerting, saved_opacity);
        (eff, m.click_through, m.effective_opacity(saved_opacity))
    };
    if eff.click_through == Some(false) {
        log_line(&format!("widget: {active_level} alert turned click-through off"));
    }
    push_effects(app, eff, ct, op);
}

/// Hotkey, tray and menu: request click-through on or off. Returns the state.
pub(crate) fn set_click_through(app: &AppHandle, on: bool) -> bool {
    let saved_opacity = saved(app).widget_opacity;
    let state = app.state::<WidgetState>();
    let (res, ct, op) = {
        let mut m = state.mode();
        let res = m.request_click_through(on);
        (res, m.click_through, m.effective_opacity(saved_opacity))
    };
    match res {
        Ok(eff) if !eff.is_empty() => {
            log_line(&format!("widget: click-through {}", if ct { "on" } else { "off" }));
            push_effects(app, eff, ct, op);
        }
        Ok(_) => sync_tray(app),
        Err(why) => {
            log_line(&format!("widget: click-through {why}"));
            // A clicked check item flipped itself; put it back.
            sync_tray(app);
        }
    }
    ct
}

pub(crate) fn toggle_click_through(app: &AppHandle) {
    let on = app.state::<WidgetState>().mode().click_through;
    set_click_through(app, !on);
}

/// Re-send the current opacity (Windows drops it whenever the window's style
/// is rewritten, e.g. on show/hide). Called on user actions and window
/// events, never per poll.
pub(crate) fn reassert_opacity(app: &AppHandle) {
    let saved_opacity = saved(app).widget_opacity;
    let (ct, op) = {
        let state = app.state::<WidgetState>();
        let mut m = state.mode();
        let op = m.effective_opacity(saved_opacity);
        m.applied_opacity = Some(op);
        (m.click_through, op)
    };
    push_effects(app, Effects { click_through: None, opacity: Some(op) }, ct, op);
}

/// Resize the widget without saving the size (used by game mode).
pub(crate) fn set_size_runtime(app: &AppHandle, size: &str) {
    if size_dims(size).is_none() {
        return;
    }
    let handle = app.clone();
    let size = size.to_string();
    let _ = app.run_on_main_thread(move || apply_size_now(&handle, &size));
}

fn apply_size_now(app: &AppHandle, size: &str) {
    let (Some(win), Some((lw, lh))) = (widget(app), size_dims(size)) else { return };
    let old = window_rects(&win);
    // `set_size` sets the client (visible) size, so work in visible rects
    // and convert back to the outer position, keeping the same insets.
    if let Err(err) = win.set_size(LogicalSize::new(lw, lh)) {
        log_line(&format!("widget: resize to {size} failed: {err}"));
        return;
    }
    let Some((old_outer, old_visible)) = old else { return };
    let scale = win.scale_factor().unwrap_or(1.0);
    let (w, h) = ((lw * scale).round() as i32, (lh * scale).round() as i32);
    if let Some(work) = pick_work_area(old_visible, &monitor_rects(app)) {
        let (x, y) = place_resized(old_visible, w, h, work);
        let pos = visible_to_outer(x, y, insets(old_outer, old_visible));
        if pos != (old_outer.x, old_outer.y) {
            let _ = win.set_position(PhysicalPosition::new(pos.0, pos.1));
        }
    }
}

/// Apply what changed between two saved settings to the window.
pub(crate) fn apply_saved_changes(app: &AppHandle, before: &Settings, after: &Settings, force_size: bool) {
    if force_size || before.widget_size != after.widget_size {
        set_size_runtime(app, &after.widget_size);
    }
    if before.widget_opacity != after.widget_opacity {
        let (eff, ct, op) = {
            let state = app.state::<WidgetState>();
            let mut m = state.mode();
            let eff = m.on_saved_opacity(after.widget_opacity);
            (eff, m.click_through, m.effective_opacity(after.widget_opacity))
        };
        push_effects(app, eff, ct, op);
    }
}

/// Startup: the saved size and opacity, after the window has its position.
pub(crate) fn apply_startup(app: &AppHandle) {
    let s = saved(app);
    set_size_runtime(app, &s.widget_size);
    let (eff, ct, op) = {
        let state = app.state::<WidgetState>();
        let mut m = state.mode();
        let eff = m.on_saved_opacity(s.widget_opacity);
        (eff, m.click_through, m.effective_opacity(s.widget_opacity))
    };
    push_effects(app, eff, ct, op);
}

/// `set_widget_mode`: validate everything first, then save and apply.
pub(crate) fn set_mode(
    app: &AppHandle,
    size: Option<Value>,
    opacity: Option<Value>,
    click_through: Option<Value>,
) -> Value {
    let (patch, ct) = match mode_patch(size.as_ref(), opacity.as_ref(), click_through.as_ref()) {
        Ok(v) => v,
        Err(err) => {
            log_line(&format!("widget mode rejected ({}): {}", err.field, err.message));
            return err.to_json();
        }
    };
    let settings = app.state::<DesktopSettings>();
    let mut before = settings.get();
    let after = if patch.as_object().is_some_and(|o| !o.is_empty()) {
        let saved = settings.apply_patch_with(
            &data_dir(),
            &patch,
            |cur, _| {
                before = cur.clone();
                Ok(())
            },
            |()| {},
            |()| {},
        );
        match saved {
            Ok(s) => s,
            Err(err) => {
                log_line(&format!("widget mode rejected ({}): {}", err.field, err.message));
                return err.to_json();
            }
        }
    } else {
        before.clone()
    };
    apply_saved_changes(app, &before, &after, patch.get("widget_size").is_some());
    let ct_now = match ct {
        Some(on) => set_click_through(app, on),
        None => app.state::<WidgetState>().mode().click_through,
    };
    json!({ "size": after.widget_size, "opacity": after.widget_opacity, "click_through": ct_now })
}

/// The native right-click menu, built fresh so its checks are current.
pub(crate) fn show_menu(app: &AppHandle) -> tauri::Result<()> {
    let Some(win) = widget(app) else { return Ok(()) };
    let s = saved(app);
    let ct = app.state::<WidgetState>().mode().click_through;
    let mut size = SubmenuBuilder::new(app, "Size");
    for (id, label, key) in [
        ("wm_size_micro", "Micro", "micro"),
        ("wm_size_compact", "Compact", "compact"),
        ("wm_size_expanded", "Expanded", "expanded"),
    ] {
        size = size.item(&CheckMenuItem::with_id(app, id, label, true, s.widget_size == key, None::<&str>)?);
    }
    let mut opacity = SubmenuBuilder::new(app, "Opacity");
    for pct in OPACITY_CHOICES {
        let item = CheckMenuItem::with_id(
            app,
            format!("wm_opacity_{pct}"),
            format!("{pct}%"),
            true,
            s.widget_opacity == pct,
            None::<&str>,
        )?;
        opacity = opacity.item(&item);
    }
    let click = CheckMenuItem::with_id(app, "wm_click_through", CLICK_THROUGH_MENU_LABEL, true, ct, None::<&str>)?;
    let test = SubmenuBuilder::new(app, "Test alert")
        .text("wm_test_amber", "Amber (20 s)")
        .text("wm_test_red", "Red (20 s)")
        .build()?;
    let menu = MenuBuilder::new(app)
        .item(&size.build()?)
        .item(&opacity.build()?)
        .item(&click)
        .separator()
        .item(&test)
        .separator()
        .text("wm_open", "Open dashboard")
        .text("wm_hide", "Hide widget")
        .build()?;
    win.popup_menu(&menu)
}

/// Menu events from the widget menu and the tray's click-through entry.
pub(crate) fn handle_menu(app: &AppHandle, id: &str) -> bool {
    let Some(action) = menu_action(id) else { return false };
    match action {
        MenuAction::Size(size) => {
            set_mode(app, Some(json!(size)), None, None);
        }
        MenuAction::Opacity(pct) => {
            set_mode(app, None, Some(json!(pct)), None);
        }
        MenuAction::ToggleClickThrough => toggle_click_through(app),
        // HTTP off the main thread.
        MenuAction::Test(level) => {
            std::thread::spawn(move || crate::commands::do_test_alert(level));
        }
        MenuAction::OpenDashboard => crate::commands::do_show_main(app),
        MenuAction::Hide => crate::commands::do_hide_widget(app),
    }
    true
}

// ---------------------------------------------------------------------------
// Edge snapping
// ---------------------------------------------------------------------------

/// One thread debounces Moved events and snaps 250 ms after the last one,
/// waiting longer while the left mouse button is still held.
pub(crate) fn spawn_snapper(app: AppHandle) {
    let (tx, rx) = channel::<()>();
    *app.state::<WidgetState>().snap_tx.lock().unwrap_or_else(|p| p.into_inner()) = Some(tx);
    let spawned = std::thread::Builder::new().name("owlet-widget-snap".into()).spawn(move || {
        while rx.recv().is_ok() {
            loop {
                match rx.recv_timeout(SNAP_DEBOUNCE) {
                    Ok(()) => continue,
                    Err(RecvTimeoutError::Timeout) if mouse_held() => continue,
                    Err(RecvTimeoutError::Timeout) => break,
                    Err(RecvTimeoutError::Disconnected) => return,
                }
            }
            let handle = app.clone();
            let _ = app.run_on_main_thread(move || snap_now(&handle));
        }
    });
    if let Err(err) = spawned {
        log_line(&format!("widget: snap thread failed: {err}"));
    }
}

/// From the widget's Moved event.
pub(crate) fn moved(app: &AppHandle) {
    if let Some(tx) = app.state::<WidgetState>().snap_tx.lock().unwrap_or_else(|p| p.into_inner()).as_ref() {
        let _ = tx.send(());
    }
}

#[cfg(windows)]
fn mouse_held() -> bool {
    use windows::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_LBUTTON};
    unsafe { GetAsyncKeyState(i32::from(VK_LBUTTON.0)) < 0 }
}

#[cfg(not(windows))]
fn mouse_held() -> bool {
    false
}

fn snap_now(app: &AppHandle) {
    let Some(win) = widget(app) else { return };
    let Some((outer, visible)) = window_rects(&win) else { return };
    if let Some((x, y)) = snap_outer(outer, visible, &monitor_rects(app)) {
        let _ = win.set_position(PhysicalPosition::new(x, y));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::validate_patch;

    const WORK: Rect = Rect { x: 0, y: 0, w: 1920, h: 1040 };

    #[test]
    fn sizes_match_the_contract() {
        assert_eq!(size_dims("micro"), Some((110.0, 32.0)));
        assert_eq!(size_dims("compact"), Some((300.0, 120.0)));
        assert_eq!(size_dims("expanded"), Some((300.0, 200.0)));
        assert_eq!(size_dims("huge"), None);
    }

    #[test]
    fn opacity_validation_rejects_rather_than_clamps() {
        let cur = Settings::default();
        for ok in [30, 65, 100] {
            let (patch, _) = mode_patch(None, Some(&json!(ok)), None).unwrap();
            assert_eq!(validate_patch(&cur, &patch).unwrap().widget_opacity, ok);
        }
        for bad in [json!(29), json!(101), json!(0), json!("80"), json!(80.5)] {
            let (patch, _) = mode_patch(None, Some(&bad), None).unwrap();
            let err = validate_patch(&cur, &patch).unwrap_err();
            assert_eq!(err.to_json()["error"]["field"], "widget_opacity");
        }
        let (patch, _) = mode_patch(Some(&json!("huge")), None, None).unwrap();
        assert_eq!(validate_patch(&cur, &patch).unwrap_err().field, "widget_size");
        let err = mode_patch(None, None, Some(&json!("yes"))).unwrap_err();
        assert_eq!(err.to_json()["error"]["field"], "click_through");
        let (patch, ct) = mode_patch(None, None, Some(&json!(true))).unwrap();
        assert_eq!((patch, ct), (json!({}), Some(true)));
        let (patch, ct) = mode_patch(Some(&Value::Null), Some(&Value::Null), Some(&Value::Null)).unwrap();
        assert_eq!((patch, ct), (json!({}), None));
    }

    #[test]
    fn alpha_bytes() {
        assert_eq!(alpha_byte(100), 255);
        assert_eq!(alpha_byte(30), 76);
        assert_eq!(alpha_byte(0), 0);
    }

    #[test]
    fn snaps_each_edge_within_12_px() {
        let w = |x, y| Rect::new(x, y, 300, 120);
        assert_eq!(snap_position(w(12, 500), WORK, SNAP_PX), (0, 500));
        assert_eq!(snap_position(w(-7, 500), WORK, SNAP_PX), (0, 500));
        assert_eq!(snap_position(w(1920 - 300 - 12, 500), WORK, SNAP_PX), (1620, 500));
        assert_eq!(snap_position(w(1920 - 300 + 5, 500), WORK, SNAP_PX), (1620, 500));
        assert_eq!(snap_position(w(800, 12), WORK, SNAP_PX), (800, 0));
        assert_eq!(snap_position(w(800, 1040 - 120 - 12), WORK, SNAP_PX), (800, 920));
    }

    #[test]
    fn snaps_corners() {
        let w = |x, y| Rect::new(x, y, 300, 120);
        assert_eq!(snap_position(w(10, 9), WORK, SNAP_PX), (0, 0));
        assert_eq!(snap_position(w(1615, 925), WORK, SNAP_PX), (1620, 920));
    }

    #[test]
    fn does_not_snap_at_13_px() {
        let w = |x, y| Rect::new(x, y, 300, 120);
        assert_eq!(snap_position(w(13, 13), WORK, SNAP_PX), (13, 13));
        assert_eq!(snap_position(w(1920 - 300 - 13, 1040 - 120 - 13), WORK, SNAP_PX), (1607, 907));
    }

    #[test]
    fn multi_monitor_picks_the_monitor_under_the_window_incl_negative_coordinates() {
        let left = (Rect::new(-2560, -200, 2560, 1440), Rect::new(-2560, -200, 2560, 1400));
        let main = (Rect::new(0, 0, 1920, 1080), WORK);
        let mons = [main, left];
        // On the left monitor, near its right work-area edge (x = 0).
        let win = Rect::new(-310, 300, 300, 120);
        let work = pick_work_area(win, &mons).unwrap();
        assert_eq!(work, left.1);
        assert_eq!(snap_position(win, work, SNAP_PX), (-300, 300));
        // Near its top (y = -200) and left (x = -2560).
        let win = Rect::new(-2550, -190, 300, 120);
        assert_eq!(snap_position(win, pick_work_area(win, &mons).unwrap(), SNAP_PX), (-2560, -200));
        // Straddling: most of it on the main monitor.
        let win = Rect::new(-50, 100, 300, 120);
        assert_eq!(pick_work_area(win, &mons), Some(WORK));
        // Off every screen: the nearest monitor.
        let win = Rect::new(5000, 100, 300, 120);
        assert_eq!(pick_work_area(win, &mons), Some(WORK));
        assert_eq!(pick_work_area(win, &[]), None);
    }

    #[test]
    fn resize_keeps_right_and_bottom_snapped_widgets_snapped() {
        // Snapped bottom-right, compact -> expanded: grows up and left.
        let old = Rect::new(1620, 920, 300, 120);
        assert_eq!(place_resized(old, 300, 200, WORK), (1620, 840));
        // -> micro: still flush right and bottom.
        assert_eq!(place_resized(old, 110, 32, WORK), (1810, 1008));
        // Top-left widget keeps its top-left.
        let old = Rect::new(0, 0, 300, 120);
        assert_eq!(place_resized(old, 110, 32, WORK), (0, 0));
        assert_eq!(place_resized(old, 300, 200, WORK), (0, 0));
    }

    #[test]
    fn resize_stays_on_screen() {
        // Nearer the bottom: grows upward.
        let old = Rect::new(100, 900, 300, 32);
        assert_eq!(place_resized(old, 300, 200, Rect::new(0, 0, 1920, 1000)), (100, 732));
        // Nearer the top but growing past the bottom of a short work area: clamped up.
        let old = Rect::new(100, 50, 300, 32);
        assert_eq!(place_resized(old, 300, 200, Rect::new(0, 0, 1920, 200)), (100, 0));
        // Partly off the left of a negative-coordinate monitor.
        let work = Rect::new(-1920, 0, 1920, 1040);
        let old = Rect::new(-1950, 500, 300, 120);
        let (x, y) = place_resized(old, 300, 200, work);
        assert!(x >= -1920 && x + 300 <= 0 && y >= 0 && y + 200 <= 1040);
        // Bigger than the work area: top-left of it.
        assert_eq!(place_resized(old, 4000, 4000, work), (-1920, 0));
    }

    /// Outer rect around a 300x120 visible rect with 8/0/8/8 insets.
    fn framed(x: i32, y: i32) -> (Rect, Rect) {
        (Rect::new(x - 8, y, 316, 128), Rect::new(x, y, 300, 120))
    }

    /// Apply a snap and return the resulting visible rect.
    fn snapped_visible(x: i32, y: i32, mons: &[(Rect, Rect)]) -> Rect {
        let (outer, visible) = framed(x, y);
        assert_eq!(insets(outer, visible), Insets { left: 8, top: 0, right: 8, bottom: 8 });
        match snap_outer(outer, visible, mons) {
            Some((ox, oy)) => Rect::new(ox + 8, oy, 300, 120),
            None => visible,
        }
    }

    #[test]
    fn snaps_the_visible_edge_flush_despite_the_invisible_border() {
        let mons = [(Rect::new(0, 0, 1920, 1080), WORK)];
        assert_eq!(snapped_visible(7, 500, &mons).x, 0);
        assert_eq!(snapped_visible(1920 - 300 - 9, 500, &mons).right(), 1920);
        assert_eq!(snapped_visible(800, 10, &mons).y, 0);
        assert_eq!(snapped_visible(800, 1040 - 120 - 11, &mons).bottom(), 1040);
        // The outer position sent for the right edge: border hangs past it.
        let (outer, visible) = framed(1610, 500);
        assert_eq!(snap_outer(outer, visible, &mons), Some((1612, 500)));
        // Nothing within 12 px: no move.
        let (outer, visible) = framed(13, 13);
        assert_eq!(snap_outer(outer, visible, &mons), None);
        let (outer, visible) = framed(1920 - 300 - 13, 1040 - 120 - 13);
        assert_eq!(snap_outer(outer, visible, &mons), None);
    }

    #[test]
    fn snap_settles_after_one_move() {
        let mons = [(Rect::new(0, 0, 1920, 1080), WORK)];
        for (x, y) in [(5, 5), (1615, 925), (3, 920)] {
            let v = snapped_visible(x, y, &mons);
            let (outer, visible) = framed(v.x, v.y);
            assert_eq!(snap_outer(outer, visible, &mons), None);
        }
    }

    #[test]
    fn snaps_the_visible_edge_to_the_monitor_to_monitor_edge() {
        let primary = (Rect::new(0, 0, 2560, 1440), Rect::new(0, 0, 2560, 1400));
        let second = (Rect::new(2560, 0, 2560, 1440), Rect::new(2560, 0, 2560, 1400));
        let mons = [primary, second];
        // Visible left edge 6 px right of 2560: on the second monitor.
        assert_eq!(snapped_visible(2566, 600, &mons).x, 2560);
        // Visible right edge 6 px short of 2560: on the primary.
        assert_eq!(snapped_visible(2560 - 300 - 6, 600, &mons).right(), 2560);
        // The measured case: outer right flush at 5120, visible 8 px short.
        assert_eq!(snapped_visible(5120 - 300 - 8, 998, &mons).right(), 5120);
    }

    #[test]
    fn zero_insets_snap_like_before() {
        let mons = [(Rect::new(0, 0, 1920, 1080), WORK)];
        for (x, y) in [(12, 500), (-7, 500), (1608, 500), (800, 908), (13, 13), (1615, 925)] {
            let r = Rect::new(x, y, 300, 120);
            let got = snap_outer(r, r, &mons).unwrap_or((x, y));
            assert_eq!(got, snap_position(r, WORK, SNAP_PX));
        }
    }

    #[test]
    fn resize_keeps_the_visible_widget_flush_right_and_bottom() {
        let (outer, visible) = framed(1620, 920);
        let (x, y) = place_resized(visible, 300, 200, WORK);
        assert_eq!((x + 300, y + 200), (1920, 1040));
        assert_eq!(visible_to_outer(x, y, insets(outer, visible)), (1612, 840));
        let (x, y) = place_resized(visible, 110, 32, WORK);
        assert_eq!((x + 110, y + 32), (1920, 1040));
    }

    #[test]
    fn amber_or_red_forces_click_through_off_and_opacity_to_100() {
        {
            let mut m = Mode::default();
            assert_eq!(m.on_saved_opacity(40).opacity, Some(40));
            m.request_click_through(true).unwrap();
            let eff = m.on_alert(true, 40);
            assert_eq!(eff, Effects { click_through: Some(false), opacity: Some(100) });
            assert!(!m.click_through);
            // Next poll, still alerting: nothing more to do.
            assert!(m.on_alert(true, 40).is_empty());
            // Alert ends: opacity returns, click-through stays off.
            assert_eq!(m.on_alert(false, 40), Effects { click_through: None, opacity: Some(40) });
            assert!(!m.click_through);
            assert!(m.on_alert(false, 40).is_empty());
        }
    }

    #[test]
    fn no_windows_calls_when_nothing_changes() {
        let mut m = Mode::default();
        assert_eq!(m.on_alert(false, 100).opacity, Some(100));
        assert!(m.on_alert(false, 100).is_empty());
        // Already opaque and not ghosted: an alert needs no call.
        assert!(m.on_alert(true, 100).is_empty());
        assert!(m.on_alert(false, 100).is_empty());
        // Saved opacity changes during an alert apply only after it.
        m.on_alert(true, 100);
        assert!(m.on_saved_opacity(60).is_empty());
        assert_eq!(m.on_alert(false, 60).opacity, Some(60));
    }

    #[test]
    fn enabling_click_through_during_an_alert_is_refused() {
        let mut m = Mode::default();
        m.on_alert(true, 100);
        assert!(m.request_click_through(true).is_err());
        assert!(!m.click_through);
        assert_eq!(m.request_click_through(false), Ok(Effects::default()));
        m.on_alert(false, 100);
        assert_eq!(m.request_click_through(true), Ok(Effects { click_through: Some(true), opacity: None }));
        assert_eq!(m.request_click_through(true), Ok(Effects::default()));
        assert_eq!(m.request_click_through(false), Ok(Effects { click_through: Some(false), opacity: None }));
    }

    #[test]
    fn menu_ids() {
        assert_eq!(menu_action("wm_size_micro"), Some(MenuAction::Size("micro")));
        assert_eq!(menu_action("wm_size_expanded"), Some(MenuAction::Size("expanded")));
        assert_eq!(menu_action("wm_opacity_40"), Some(MenuAction::Opacity(40)));
        assert_eq!(menu_action("wm_opacity_55"), None);
        assert_eq!(menu_action("wm_click_through"), Some(MenuAction::ToggleClickThrough));
        assert_eq!(menu_action(TRAY_CLICK_THROUGH_ID), Some(MenuAction::ToggleClickThrough));
        assert_eq!(menu_action("wm_test_red"), Some(MenuAction::Test("red")));
        assert_eq!(menu_action("wm_open"), Some(MenuAction::OpenDashboard));
        assert_eq!(menu_action("wm_hide"), Some(MenuAction::Hide));
        assert_eq!(menu_action("tray_quit"), None);
        assert!(CLICK_THROUGH_MENU_LABEL.contains("tray or hotkey"));
    }
}
