//! Desktop-shell settings: storage, validation and shared state.
//!
//! Stored in `desktop-settings.json` next to the log. `desktop-config.json` in
//! the same folder belongs to the Python server and is never touched here.
//! Everything except `load_from`/`save_to` file I/O is pure so it can be
//! unit-tested without Tauri. A load problem of any kind degrades to defaults.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

use serde_json::{json, Map, Value};

pub(crate) const FILE_NAME: &str = "desktop-settings.json";
const MAX_ID_LEN: usize = 256;
const MAX_HOTKEY_LEN: usize = 64;

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Settings {
    pub(crate) flash_target: String,
    pub(crate) alarm_sound: String,
    pub(crate) alarm_device: String,
    pub(crate) unmute_on_red: bool,
    pub(crate) alarm_volume: u8,
    pub(crate) hotkey_snooze: Option<String>,
    pub(crate) hotkey_acknowledge: Option<String>,
    pub(crate) hotkey_widget: Option<String>,
    pub(crate) hotkey_click_through: Option<String>,
    pub(crate) controller_mode: String,
    pub(crate) controller_buttons: Option<u16>,
    pub(crate) autostart: bool,
    pub(crate) keep_awake: bool,
    pub(crate) game_mode: String,
    pub(crate) capture_exclusion: bool,
    pub(crate) widget_size: String,
    pub(crate) widget_opacity: u8,
    pub(crate) first_run_test_done: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            flash_target: "secondary".into(),
            alarm_sound: "beep".into(),
            alarm_device: "default".into(),
            unmute_on_red: true,
            alarm_volume: 60,
            hotkey_snooze: None,
            hotkey_acknowledge: None,
            hotkey_widget: None,
            hotkey_click_through: None,
            controller_mode: "off".into(),
            controller_buttons: None,
            autostart: false,
            keep_awake: true,
            game_mode: "auto".into(),
            capture_exclusion: false,
            widget_size: "compact".into(),
            widget_opacity: 100,
            first_run_test_done: false,
        }
    }
}

/// A rejected patch: the offending key (empty for I/O errors) and why.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct SettingsError {
    pub(crate) field: String,
    pub(crate) message: String,
}

impl SettingsError {
    fn new(field: &str, message: impl Into<String>) -> Self {
        Self { field: field.into(), message: message.into() }
    }

    pub(crate) fn to_json(&self) -> Value {
        json!({ "error": { "field": self.field, "message": self.message } })
    }
}

const KEYS: [&str; 18] = [
    "flash_target",
    "alarm_sound",
    "alarm_device",
    "unmute_on_red",
    "alarm_volume",
    "hotkey_snooze",
    "hotkey_acknowledge",
    "hotkey_widget",
    "hotkey_click_through",
    "controller_mode",
    "controller_buttons",
    "autostart",
    "keep_awake",
    "game_mode",
    "capture_exclusion",
    "widget_size",
    "widget_opacity",
    "first_run_test_done",
];

fn want_bool(field: &str, v: &Value) -> Result<bool, SettingsError> {
    v.as_bool().ok_or_else(|| SettingsError::new(field, "must be true or false"))
}

fn want_int(field: &str, v: &Value, lo: i64, hi: i64) -> Result<i64, SettingsError> {
    let n = v
        .as_i64()
        .ok_or_else(|| SettingsError::new(field, "must be a whole number"))?;
    if n < lo || n > hi {
        return Err(SettingsError::new(field, format!("must be between {lo} and {hi}")));
    }
    Ok(n)
}

fn want_choice(field: &str, v: &Value, allowed: &[&str]) -> Result<String, SettingsError> {
    match v.as_str() {
        Some(s) if allowed.contains(&s) => Ok(s.to_string()),
        _ => Err(SettingsError::new(field, format!("must be one of: {}", allowed.join(", ")))),
    }
}

/// Free-form id: any non-empty string (a monitor or device may be unplugged).
fn want_id(field: &str, v: &Value) -> Result<String, SettingsError> {
    match v.as_str() {
        Some(s) if !s.is_empty() && s.chars().count() <= MAX_ID_LEN => Ok(s.to_string()),
        _ => Err(SettingsError::new(
            field,
            format!("must be a non-empty string of at most {MAX_ID_LEN} characters"),
        )),
    }
}

fn want_hotkey(field: &str, v: &Value) -> Result<Option<String>, SettingsError> {
    if v.is_null() {
        return Ok(None);
    }
    match v.as_str() {
        Some(s) if !s.is_empty() && s.chars().count() <= MAX_HOTKEY_LEN => Ok(Some(s.to_string())),
        _ => Err(SettingsError::new(
            field,
            format!("must be null or a non-empty string of at most {MAX_HOTKEY_LEN} characters"),
        )),
    }
}

impl Settings {
    /// Validate and set a single key. Does not check cross-field rules.
    fn set_field(&mut self, key: &str, v: &Value) -> Result<(), SettingsError> {
        match key {
            "flash_target" => self.flash_target = want_id(key, v)?,
            "alarm_sound" => self.alarm_sound = want_choice(key, v, &["beep", "two_tone", "pulse"])?,
            "alarm_device" => self.alarm_device = want_id(key, v)?,
            "unmute_on_red" => self.unmute_on_red = want_bool(key, v)?,
            "alarm_volume" => self.alarm_volume = want_int(key, v, 20, 100)? as u8,
            "hotkey_snooze" => self.hotkey_snooze = want_hotkey(key, v)?,
            "hotkey_acknowledge" => self.hotkey_acknowledge = want_hotkey(key, v)?,
            "hotkey_widget" => self.hotkey_widget = want_hotkey(key, v)?,
            "hotkey_click_through" => self.hotkey_click_through = want_hotkey(key, v)?,
            "controller_mode" => self.controller_mode = want_choice(key, v, &["off", "chosen", "any"])?,
            "controller_buttons" => {
                self.controller_buttons =
                    if v.is_null() { None } else { Some(want_int(key, v, 0, 65535)? as u16) }
            }
            "autostart" => self.autostart = want_bool(key, v)?,
            "keep_awake" => self.keep_awake = want_bool(key, v)?,
            "game_mode" => self.game_mode = want_choice(key, v, &["auto", "always", "never"])?,
            "capture_exclusion" => self.capture_exclusion = want_bool(key, v)?,
            "widget_size" => self.widget_size = want_choice(key, v, &["micro", "compact", "expanded"])?,
            "widget_opacity" => self.widget_opacity = want_int(key, v, 30, 100)? as u8,
            "first_run_test_done" => self.first_run_test_done = want_bool(key, v)?,
            _ => return Err(SettingsError::new(key, "unknown setting")),
        }
        Ok(())
    }

    fn check_combination(&self) -> Result<(), SettingsError> {
        if self.controller_mode == "chosen" && matches!(self.controller_buttons, None | Some(0)) {
            return Err(SettingsError::new(
                "controller_buttons",
                "a button or combination must be chosen when controller_mode is \"chosen\"",
            ));
        }
        Ok(())
    }

    pub(crate) fn to_json(&self) -> Value {
        json!({
            "flash_target": self.flash_target,
            "alarm_sound": self.alarm_sound,
            "alarm_device": self.alarm_device,
            "unmute_on_red": self.unmute_on_red,
            "alarm_volume": self.alarm_volume,
            "hotkey_snooze": self.hotkey_snooze,
            "hotkey_acknowledge": self.hotkey_acknowledge,
            "hotkey_widget": self.hotkey_widget,
            "hotkey_click_through": self.hotkey_click_through,
            "controller_mode": self.controller_mode,
            "controller_buttons": self.controller_buttons,
            "autostart": self.autostart,
            "keep_awake": self.keep_awake,
            "game_mode": self.game_mode,
            "capture_exclusion": self.capture_exclusion,
            "widget_size": self.widget_size,
            "widget_opacity": self.widget_opacity,
            "first_run_test_done": self.first_run_test_done,
        })
    }
}

/// Pure patch validation: all-or-nothing. `current` is never modified.
pub(crate) fn validate_patch(current: &Settings, patch: &Value) -> Result<Settings, SettingsError> {
    let obj = patch
        .as_object()
        .ok_or_else(|| SettingsError::new("", "patch must be a JSON object"))?;
    // Unknown keys are reported first, then known keys in a stable order.
    if let Some(bad) = obj.keys().find(|k| !KEYS.contains(&k.as_str())) {
        return Err(SettingsError::new(bad, "unknown setting"));
    }
    let mut next = current.clone();
    for key in KEYS {
        if let Some(v) = obj.get(key) {
            next.set_field(key, v)?;
        }
    }
    next.check_combination()?;
    Ok(next)
}

/// Parse file text leniently: anything unusable becomes defaults, key by key.
/// Returns the settings plus human-readable notes about what was defaulted.
pub(crate) fn parse_lenient(text: &str) -> (Settings, Vec<String>) {
    let mut notes = Vec::new();
    let mut s = Settings::default();
    let obj: Map<String, Value> = match serde_json::from_str::<Value>(text) {
        Ok(Value::Object(m)) => m,
        Ok(_) => {
            notes.push("settings file is not a JSON object; using defaults".into());
            return (s, notes);
        }
        Err(err) => {
            notes.push(format!("settings file is corrupt ({err}); using defaults"));
            return (s, notes);
        }
    };
    for key in KEYS {
        if let Some(v) = obj.get(key) {
            // Set on a copy so a bad value cannot half-apply.
            let mut trial = s.clone();
            match trial.set_field(key, v) {
                Ok(()) => s = trial,
                Err(err) => notes.push(format!("{key}: {}; using default", err.message)),
            }
        }
    }
    if s.check_combination().is_err() {
        // "off" can only make controller snooze unavailable, never easier.
        s.controller_mode = "off".into();
        notes.push("controller_mode \"chosen\" without buttons; using \"off\"".into());
    }
    (s, notes)
}

pub(crate) fn data_dir() -> PathBuf {
    std::env::var("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("owlet-dashboard")
}

/// Load from `dir`. Never fails and never writes: missing/unreadable/corrupt
/// files give defaults (a corrupt file is left alone until the next save).
pub(crate) fn load_from(dir: &Path, log: &dyn Fn(&str)) -> Settings {
    let path = dir.join(FILE_NAME);
    let text = match fs::read_to_string(&path) {
        Ok(t) => t,
        Err(err) => {
            if err.kind() != std::io::ErrorKind::NotFound {
                log(&format!("settings: cannot read {}: {err}; using defaults", path.display()));
            }
            return Settings::default();
        }
    };
    let (settings, notes) = parse_lenient(&text);
    for note in notes {
        log(&format!("settings: {note}"));
    }
    settings
}

/// Atomic save: write a temp file in the same directory, flush it to disk,
/// then rename over the target.
pub(crate) fn save_to(dir: &Path, settings: &Settings) -> std::io::Result<()> {
    fs::create_dir_all(dir)?;
    let target = dir.join(FILE_NAME);
    let tmp = dir.join(format!("{FILE_NAME}.tmp"));
    let body = serde_json::to_string_pretty(&settings.to_json()).map_err(std::io::Error::other)?;
    let result = (|| {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(body.as_bytes())?;
        f.sync_all()?;
        drop(f);
        fs::rename(&tmp, &target)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

/// App-managed holder so later packages can read settings from the alert loop.
pub(crate) struct DesktopSettings(Mutex<Settings>);

impl DesktopSettings {
    pub(crate) fn new(settings: Settings) -> Self {
        Self(Mutex::new(settings))
    }

    /// Poison-tolerant: a panic elsewhere must not take settings reads down.
    fn guard(&self) -> MutexGuard<'_, Settings> {
        self.0.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub(crate) fn replace(&self, settings: Settings) {
        *self.guard() = settings;
    }

    #[allow(dead_code)] // first reader arrives in a later package
    pub(crate) fn get(&self) -> Settings {
        self.guard().clone()
    }

    pub(crate) fn get_json(&self) -> Value {
        self.guard().to_json()
    }

    /// Validate, save, then swap in memory. The lock is held throughout so
    /// concurrent patches cannot interleave; on any error memory is unchanged.
    pub(crate) fn apply_patch(&self, dir: &Path, patch: &Value) -> Result<Settings, SettingsError> {
        let mut cur = self.guard();
        let next = validate_patch(&cur, patch)?;
        if next != *cur {
            save_to(dir, &next)
                .map_err(|err| SettingsError::new("", format!("could not save settings: {err}")))?;
            *cur = next.clone();
        }
        Ok(next)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn patch(v: Value) -> Result<Settings, SettingsError> {
        validate_patch(&Settings::default(), &v)
    }

    fn err_field(v: Value) -> String {
        patch(v).unwrap_err().field
    }

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "owlet-settings-test-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn defaults_match_contract() {
        let j = Settings::default().to_json();
        assert_eq!(j["flash_target"], "secondary");
        assert_eq!(j["alarm_sound"], "beep");
        assert_eq!(j["alarm_device"], "default");
        assert_eq!(j["unmute_on_red"], true);
        assert_eq!(j["alarm_volume"], 60);
        for k in ["hotkey_snooze", "hotkey_acknowledge", "hotkey_widget", "hotkey_click_through", "controller_buttons"] {
            assert!(j[k].is_null(), "{k}");
        }
        assert_eq!(j["controller_mode"], "off");
        assert_eq!(j["autostart"], false);
        assert_eq!(j["keep_awake"], true);
        assert_eq!(j["game_mode"], "auto");
        assert_eq!(j["capture_exclusion"], false);
        assert_eq!(j["widget_size"], "compact");
        assert_eq!(j["widget_opacity"], 100);
        assert_eq!(j["first_run_test_done"], false);
        assert_eq!(j.as_object().unwrap().len(), KEYS.len());
    }

    #[test]
    fn round_trip() {
        let s = Settings {
            flash_target: "\\\\.\\DISPLAY2".into(),
            alarm_sound: "pulse".into(),
            alarm_volume: 85,
            hotkey_snooze: Some("Ctrl+Alt+S".into()),
            controller_mode: "chosen".into(),
            controller_buttons: Some(0x1030),
            widget_opacity: 45,
            ..Settings::default()
        };
        let (back, notes) = parse_lenient(&s.to_json().to_string());
        assert!(notes.is_empty(), "{notes:?}");
        assert_eq!(back, s);
    }

    #[test]
    fn corrupt_and_non_object_files_give_defaults() {
        for text in ["{not json", "", "[1,2]", "42"] {
            let (s, notes) = parse_lenient(text);
            assert_eq!(s, Settings::default());
            assert_eq!(notes.len(), 1);
        }
    }

    #[test]
    fn one_bad_value_defaults_only_that_key() {
        let (s, notes) = parse_lenient(
            r#"{"alarm_volume": 5, "alarm_sound": "pulse", "mystery": 1, "keep_awake": false}"#,
        );
        assert_eq!(s.alarm_volume, 60);
        assert_eq!(s.alarm_sound, "pulse");
        assert!(!s.keep_awake);
        assert_eq!(notes.len(), 1);
        assert!(notes[0].starts_with("alarm_volume"));
    }

    #[test]
    fn load_chosen_without_buttons_falls_back_to_off() {
        let (s, _) = parse_lenient(r#"{"controller_mode": "chosen"}"#);
        assert_eq!(s.controller_mode, "off");
    }

    #[test]
    fn load_missing_corrupt_and_unreadable_file() {
        let dir = temp_dir("load");
        let logged = std::cell::RefCell::new(Vec::new());
        let log = |m: &str| logged.borrow_mut().push(m.to_string());
        assert_eq!(load_from(&dir, &log), Settings::default());
        assert!(logged.borrow().is_empty());
        fs::write(dir.join(FILE_NAME), "garbage{").unwrap();
        assert_eq!(load_from(&dir, &log), Settings::default());
        assert_eq!(logged.borrow().len(), 1);
        // The corrupt file is left in place by load.
        assert_eq!(fs::read_to_string(dir.join(FILE_NAME)).unwrap(), "garbage{");
        // Unreadable (a directory in place of the file) also degrades.
        let dir2 = temp_dir("load-dir");
        fs::create_dir(dir2.join(FILE_NAME)).unwrap();
        assert_eq!(load_from(&dir2, &log), Settings::default());
        assert_eq!(logged.borrow().len(), 2);
        let _ = fs::remove_dir_all(&dir);
        let _ = fs::remove_dir_all(&dir2);
    }

    #[test]
    fn save_is_atomic_and_round_trips() {
        let dir = temp_dir("save");
        let mut s = Settings { alarm_volume: 90, ..Settings::default() };
        save_to(&dir, &s).unwrap();
        s.alarm_volume = 70;
        save_to(&dir, &s).unwrap();
        assert!(!dir.join(format!("{FILE_NAME}.tmp")).exists());
        assert_eq!(load_from(&dir, &|_| {}), s);
        // Never touches the Python server's file.
        assert!(!dir.join("desktop-config.json").exists());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn save_creates_missing_directory() {
        let base = temp_dir("mkdir");
        let dir = base.join("nested");
        save_to(&dir, &Settings::default()).unwrap();
        assert!(dir.join(FILE_NAME).exists());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn empty_patch_and_non_object() {
        assert_eq!(patch(json!({})).unwrap(), Settings::default());
        assert_eq!(err_field(json!([1])), "");
    }

    #[test]
    fn unknown_key_rejected() {
        assert_eq!(err_field(json!({"nope": 1})), "nope");
        assert_eq!(err_field(json!({"alarm_volume": 50, "nope": 1})), "nope");
    }

    #[test]
    fn enum_keys() {
        for (k, good, bad) in [
            ("alarm_sound", "two_tone", "siren"),
            ("controller_mode", "any", "all"),
            ("game_mode", "never", "sometimes"),
            ("widget_size", "micro", "huge"),
        ] {
            assert_eq!(patch(json!({ k: good })).unwrap().to_json()[k], good);
            assert_eq!(err_field(json!({ k: bad })), k);
            assert_eq!(err_field(json!({ k: 1 })), k);
            assert_eq!(err_field(json!({ k: null })), k);
        }
    }

    #[test]
    fn id_keys() {
        for k in ["flash_target", "alarm_device"] {
            assert_eq!(patch(json!({ k: "\\\\.\\DISPLAY9" })).unwrap().to_json()[k], "\\\\.\\DISPLAY9");
            assert_eq!(err_field(json!({ k: "" })), k);
            assert_eq!(err_field(json!({ k: 3 })), k);
            assert_eq!(err_field(json!({ k: null })), k);
            assert_eq!(err_field(json!({ k: "x".repeat(257) })), k);
        }
    }

    #[test]
    fn bool_keys() {
        for k in ["unmute_on_red", "autostart", "keep_awake", "capture_exclusion", "first_run_test_done"] {
            assert_eq!(patch(json!({ k: true })).unwrap().to_json()[k], true);
            assert_eq!(patch(json!({ k: false })).unwrap().to_json()[k], false);
            assert_eq!(err_field(json!({ k: "true" })), k);
            assert_eq!(err_field(json!({ k: 1 })), k);
            assert_eq!(err_field(json!({ k: null })), k);
        }
    }

    #[test]
    fn integer_ranges() {
        for (k, lo, hi) in [("alarm_volume", 20, 100), ("widget_opacity", 30, 100)] {
            assert_eq!(patch(json!({ k: lo })).unwrap().to_json()[k], lo);
            assert_eq!(patch(json!({ k: hi })).unwrap().to_json()[k], hi);
            assert_eq!(err_field(json!({ k: lo - 1 })), k);
            assert_eq!(err_field(json!({ k: hi + 1 })), k);
            assert_eq!(err_field(json!({ k: -5 })), k);
            assert_eq!(err_field(json!({ k: 50.5 })), k);
            assert_eq!(err_field(json!({ k: "50" })), k);
            assert_eq!(err_field(json!({ k: null })), k);
        }
    }

    #[test]
    fn hotkeys() {
        for k in ["hotkey_snooze", "hotkey_acknowledge", "hotkey_widget", "hotkey_click_through"] {
            assert_eq!(patch(json!({ k: "Ctrl+F9" })).unwrap().to_json()[k], "Ctrl+F9");
            assert!(patch(json!({ k: "x".repeat(64) })).is_ok());
            assert!(patch(json!({ k: null })).unwrap().to_json()[k].is_null());
            assert_eq!(err_field(json!({ k: "" })), k);
            assert_eq!(err_field(json!({ k: "x".repeat(65) })), k);
            assert_eq!(err_field(json!({ k: 5 })), k);
        }
    }

    #[test]
    fn controller_buttons_range_and_null() {
        assert_eq!(patch(json!({"controller_buttons": 0})).unwrap().controller_buttons, Some(0));
        assert_eq!(patch(json!({"controller_buttons": 65535})).unwrap().controller_buttons, Some(65535));
        assert_eq!(patch(json!({"controller_buttons": null})).unwrap().controller_buttons, None);
        assert_eq!(err_field(json!({"controller_buttons": 65536})), "controller_buttons");
        assert_eq!(err_field(json!({"controller_buttons": -1})), "controller_buttons");
        assert_eq!(err_field(json!({"controller_buttons": "4"})), "controller_buttons");
    }

    #[test]
    fn chosen_mode_needs_buttons() {
        assert_eq!(err_field(json!({"controller_mode": "chosen"})), "controller_buttons");
        assert_eq!(
            err_field(json!({"controller_mode": "chosen", "controller_buttons": 0})),
            "controller_buttons"
        );
        assert_eq!(
            err_field(json!({"controller_mode": "chosen", "controller_buttons": null})),
            "controller_buttons"
        );
        assert!(patch(json!({"controller_mode": "chosen", "controller_buttons": 16})).is_ok());
        // Clearing the buttons while already in chosen mode is also rejected.
        let cur = Settings {
            controller_mode: "chosen".into(),
            controller_buttons: Some(16),
            ..Settings::default()
        };
        let err = validate_patch(&cur, &json!({"controller_buttons": null})).unwrap_err();
        assert_eq!(err.field, "controller_buttons");
        assert!(validate_patch(&cur, &json!({"controller_mode": "off", "controller_buttons": null})).is_ok());
    }

    #[test]
    fn nothing_applied_on_error() {
        let cur = Settings::default();
        let err = validate_patch(&cur, &json!({"alarm_volume": 80, "alarm_sound": "bogus"})).unwrap_err();
        assert_eq!(err.field, "alarm_sound");
        assert_eq!(cur, Settings::default());

        let dir = temp_dir("state");
        let state = DesktopSettings::new(Settings::default());
        assert!(state.apply_patch(&dir, &json!({"alarm_volume": 80, "alarm_sound": "bogus"})).is_err());
        assert_eq!(state.get(), Settings::default());
        assert!(!dir.join(FILE_NAME).exists());
        let ok = state.apply_patch(&dir, &json!({"alarm_volume": 80})).unwrap();
        assert_eq!(ok.alarm_volume, 80);
        assert_eq!(state.get().alarm_volume, 80);
        assert_eq!(load_from(&dir, &|_| {}).alarm_volume, 80);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn save_failure_keeps_memory_unchanged() {
        // A regular file where the directory should be makes create_dir_all fail.
        let base = temp_dir("savefail");
        let blocker = base.join("blocker");
        fs::write(&blocker, "x").unwrap();
        let state = DesktopSettings::new(Settings::default());
        let err = state.apply_patch(&blocker, &json!({"alarm_volume": 80})).unwrap_err();
        assert_eq!(err.field, "");
        assert_eq!(state.get(), Settings::default());
        assert_eq!(err.to_json()["error"]["field"], "");
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn error_json_shape() {
        let e = patch(json!({"nope": 1})).unwrap_err().to_json();
        assert_eq!(e["error"]["field"], "nope");
        assert!(e["error"]["message"].is_string());
    }
}
