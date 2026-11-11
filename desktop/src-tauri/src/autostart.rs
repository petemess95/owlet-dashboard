//! Start with Windows, start hidden, and the first-run walk-through.
//!
//! The OS entry (the HKCU `Run` value on Windows) is managed by
//! `tauri-plugin-autostart`, registered from Rust only: no window is granted
//! the plugin's JS permissions. The entry launches the exe with
//! `--autostart`, which only changes whether the *dashboard* window is shown;
//! the sidecar, alert loop, overlay, sound and widget start the same way
//! either way. The decisions here are pure and unit-tested; `PluginBackend`
//! is the thin OS layer.

use tauri::{AppHandle, Manager};
use tauri_plugin_autostart::AutoLaunchManager;

/// Argument the Run entry passes so a login start can be told from a manual one.
pub(crate) const AUTOSTART_ARG: &str = "--autostart";

/// URL fragment that makes the dashboard page open Settings on "This PC"
/// once, on load (handled in app/shell.py, then cleared).
pub(crate) const SETTINGS_FRAGMENT: &str = "#settings=desktop";

/// True when the process was started by the OS Run entry. `args` excludes the
/// program name.
pub(crate) fn is_autostart_launch<I, S>(args: I) -> bool
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    args.into_iter().any(|a| a.as_ref() == AUTOSTART_ARG)
}

/// What the dashboard window does at startup.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Startup {
    /// Login start: window built hidden, widget only.
    Hidden,
    /// Manual start: window shown as before.
    Shown,
    /// First run: window shown, Settings opened on "This PC".
    ShownWithSettings,
}

/// First run wins over a login start: until the user has confirmed the test
/// alerts, the dashboard is shown so the test card is in front of them.
pub(crate) fn startup_mode(autostart_launch: bool, first_run_test_done: bool) -> Startup {
    if !first_run_test_done {
        Startup::ShownWithSettings
    } else if autostart_launch {
        Startup::Hidden
    } else {
        Startup::Shown
    }
}

pub(crate) fn main_window_url(base: &str, mode: Startup) -> String {
    match mode {
        Startup::ShownWithSettings => format!("{base}{SETTINGS_FRAGMENT}"),
        _ => base.to_string(),
    }
}

/// The OS side, so tests can use a fake.
pub(crate) trait Backend {
    fn is_enabled(&self) -> Result<bool, String>;
    fn enable(&self) -> Result<(), String>;
    fn disable(&self) -> Result<(), String>;
}

/// Turn the OS entry on or off. The error text is shown on the autostart field.
pub(crate) fn set_enabled(be: &dyn Backend, want: bool) -> Result<(), String> {
    if want { be.enable() } else { be.disable() }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum SyncAction {
    Enable,
    Disable,
    Nothing,
}

/// Make the OS entry match the saved setting. When on, always rewrite the
/// entry so it points at the exe that is running now (it may have moved);
/// when off, remove it if present (or if we can't tell).
pub(crate) fn sync_action(saved: bool, os_enabled: Result<bool, String>) -> SyncAction {
    match (saved, os_enabled) {
        (true, _) => SyncAction::Enable,
        (false, Ok(false)) => SyncAction::Nothing,
        (false, _) => SyncAction::Disable,
    }
}

/// Startup sync. Never fails: problems are logged.
pub(crate) fn sync_at_startup(be: &dyn Backend, saved: bool, log: &dyn Fn(&str)) {
    let action = sync_action(saved, be.is_enabled());
    let result = match action {
        SyncAction::Enable => be.enable(),
        SyncAction::Disable => be.disable(),
        SyncAction::Nothing => Ok(()),
    };
    if let Err(err) = result {
        log(&format!("autostart: could not sync the OS entry ({action:?}): {err}"));
    }
}

/// The plugin-backed OS layer. The manager only exists if the plugin's setup
/// succeeded, so look it up fallibly.
pub(crate) struct PluginBackend<'a>(pub(crate) &'a AppHandle);

impl PluginBackend<'_> {
    fn with<T>(&self, f: impl FnOnce(&AutoLaunchManager) -> Result<T, tauri_plugin_autostart::Error>) -> Result<T, String> {
        let manager = self
            .0
            .try_state::<AutoLaunchManager>()
            .ok_or_else(|| "autostart is not available".to_string())?;
        f(&manager).map_err(|e| e.to_string())
    }
}

impl Backend for PluginBackend<'_> {
    fn is_enabled(&self) -> Result<bool, String> {
        self.with(|m| m.is_enabled())
    }
    fn enable(&self) -> Result<(), String> {
        self.with(|m| m.enable())
    }
    fn disable(&self) -> Result<(), String> {
        self.with(|m| m.disable())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    #[test]
    fn detects_the_autostart_argument() {
        assert!(is_autostart_launch(["--autostart"]));
        assert!(is_autostart_launch(["--other", "--autostart"]));
        assert!(!is_autostart_launch(Vec::<String>::new()));
        assert!(!is_autostart_launch(["--autostarted", "autostart", "--AUTOSTART"]));
    }

    #[test]
    fn startup_decision() {
        assert_eq!(startup_mode(true, true), Startup::Hidden);
        assert_eq!(startup_mode(false, true), Startup::Shown);
        // First run always shows the dashboard with Settings, login start or not.
        assert_eq!(startup_mode(false, false), Startup::ShownWithSettings);
        assert_eq!(startup_mode(true, false), Startup::ShownWithSettings);
    }

    #[test]
    fn only_first_run_carries_the_settings_fragment() {
        let base = "http://127.0.0.1:8877/";
        assert_eq!(main_window_url(base, Startup::Hidden), base);
        assert_eq!(main_window_url(base, Startup::Shown), base);
        assert_eq!(main_window_url(base, Startup::ShownWithSettings), "http://127.0.0.1:8877/#settings=desktop");
    }

    #[test]
    fn sync_decision() {
        assert_eq!(sync_action(true, Ok(true)), SyncAction::Enable);
        assert_eq!(sync_action(true, Ok(false)), SyncAction::Enable);
        assert_eq!(sync_action(true, Err("x".into())), SyncAction::Enable);
        assert_eq!(sync_action(false, Ok(true)), SyncAction::Disable);
        assert_eq!(sync_action(false, Ok(false)), SyncAction::Nothing);
        assert_eq!(sync_action(false, Err("x".into())), SyncAction::Disable);
    }

    #[derive(Default)]
    struct Fake {
        enabled: Cell<bool>,
        fail: Cell<bool>,
        calls: RefCell<Vec<&'static str>>,
    }

    impl Backend for Fake {
        fn is_enabled(&self) -> Result<bool, String> {
            self.calls.borrow_mut().push("is_enabled");
            if self.fail.get() { Err("denied".into()) } else { Ok(self.enabled.get()) }
        }
        fn enable(&self) -> Result<(), String> {
            self.calls.borrow_mut().push("enable");
            if self.fail.get() { return Err("denied".into()); }
            self.enabled.set(true);
            Ok(())
        }
        fn disable(&self) -> Result<(), String> {
            self.calls.borrow_mut().push("disable");
            if self.fail.get() { return Err("denied".into()); }
            self.enabled.set(false);
            Ok(())
        }
    }

    #[test]
    fn set_enabled_turns_the_entry_on_and_off() {
        let be = Fake::default();
        set_enabled(&be, true).unwrap();
        assert!(be.enabled.get());
        set_enabled(&be, false).unwrap();
        assert!(!be.enabled.get());
    }

    #[test]
    fn set_enabled_reports_failure() {
        let be = Fake::default();
        be.fail.set(true);
        assert_eq!(set_enabled(&be, true), Err("denied".to_string()));
    }

    #[test]
    fn startup_sync_matches_the_saved_setting() {
        let be = Fake::default();
        sync_at_startup(&be, true, &|_| {});
        assert!(be.enabled.get());
        sync_at_startup(&be, false, &|_| {});
        assert!(!be.enabled.get());
        // Already off and saved off: no write at all.
        be.calls.borrow_mut().clear();
        sync_at_startup(&be, false, &|_| {});
        assert_eq!(*be.calls.borrow(), vec!["is_enabled"]);
    }

    #[test]
    fn startup_sync_logs_and_does_not_fail() {
        let be = Fake::default();
        be.fail.set(true);
        let logged = RefCell::new(Vec::new());
        sync_at_startup(&be, true, &|m| logged.borrow_mut().push(m.to_string()));
        sync_at_startup(&be, false, &|m| logged.borrow_mut().push(m.to_string()));
        assert_eq!(logged.borrow().len(), 2);
        assert!(logged.borrow()[0].contains("denied"));
    }
}
