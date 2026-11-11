//! Global hotkeys: the pure string format, planning and press gating are
//! unit-tested; the thin OS layer (`OsBackend`, the plugin handler) is not.
//!
//! Format (fixed contract): modifiers from `Ctrl`, `Alt`, `Shift`, `Super` in
//! that order, then one key named by its `KeyboardEvent.code`, joined by `+`
//! ("Ctrl+Shift+KeyS", "Alt+F9"). At least one modifier, except F1-F24.
//!
//! Safety: the only alert-affecting action is snooze, and it goes through
//! `commands::do_snooze` exactly like the tray item. Only key presses act;
//! releases, auto-repeat and registration itself never do. Acknowledge and
//! click-through are placeholders that only write a log line.

use std::collections::HashMap;
use std::str::FromStr;
use std::sync::Mutex;

use tauri::{AppHandle, Manager};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutEvent, ShortcutState};

use crate::settings::Settings;
use crate::{commands, log_line};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) enum Action {
    Snooze,
    Acknowledge,
    Widget,
    ClickThrough,
}

impl Action {
    pub(crate) const ALL: [Action; 4] =
        [Action::Snooze, Action::Acknowledge, Action::Widget, Action::ClickThrough];

    /// The settings key.
    pub(crate) fn key(self) -> &'static str {
        match self {
            Action::Snooze => "hotkey_snooze",
            Action::Acknowledge => "hotkey_acknowledge",
            Action::Widget => "hotkey_widget",
            Action::ClickThrough => "hotkey_click_through",
        }
    }

    /// Readable name used in clash messages.
    pub(crate) fn label(self) -> &'static str {
        match self {
            Action::Snooze => "Snooze alert",
            Action::Acknowledge => "Acknowledge alert",
            Action::Widget => "Show/hide widget",
            Action::ClickThrough => "Widget click-through",
        }
    }

    pub(crate) fn get(self, s: &Settings) -> Option<&str> {
        match self {
            Action::Snooze => s.hotkey_snooze.as_deref(),
            Action::Acknowledge => s.hotkey_acknowledge.as_deref(),
            Action::Widget => s.hotkey_widget.as_deref(),
            Action::ClickThrough => s.hotkey_click_through.as_deref(),
        }
    }
}

// ---------------------------------------------------------------------------
// Format
// ---------------------------------------------------------------------------

const MODIFIER_ORDER: [(&str, Modifiers); 4] = [
    ("Ctrl", Modifiers::CONTROL),
    ("Alt", Modifiers::ALT),
    ("Shift", Modifiers::SHIFT),
    ("Super", Modifiers::SUPER),
];

const NAMED_KEYS: [&str; 33] = [
    "Space", "Enter", "Tab", "Backspace", "Insert", "Delete", "Home", "End", "PageUp", "PageDown",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Minus", "Equal", "Comma", "Period",
    "Slash", "Backslash", "Semicolon", "Quote", "Backquote", "BracketLeft", "BracketRight",
    "NumpadAdd", "NumpadSubtract", "NumpadMultiply", "NumpadDivide", "NumpadDecimal",
    "NumpadEnter", "NumpadEqual", "NumpadComma",
];

/// A key we accept: letters, digits, F1-F24, numpad digits and a fixed set of
/// named keys. Modifier keys, Escape, media keys etc. are rejected.
fn is_allowed_key(name: &str) -> bool {
    let single = |t: &str, ok: fn(&u8) -> bool| t.len() == 1 && ok(&t.as_bytes()[0]);
    name.strip_prefix("Key").is_some_and(|t| single(t, u8::is_ascii_uppercase))
        || name.strip_prefix("Digit").is_some_and(|t| single(t, u8::is_ascii_digit))
        || name.strip_prefix("Numpad").is_some_and(|t| single(t, u8::is_ascii_digit))
        || is_function_key(name)
        || NAMED_KEYS.contains(&name)
}

fn is_function_key(name: &str) -> bool {
    name.strip_prefix('F')
        .filter(|n| !n.is_empty() && !n.starts_with('0') && n.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|n| n.parse::<u8>().ok())
        .is_some_and(|n| (1..=24).contains(&n))
}

/// Pure validator and converter. `Err` is a readable reason.
pub(crate) fn parse(s: &str) -> Result<Shortcut, String> {
    if s.is_empty() {
        return Err("is empty".into());
    }
    let parts: Vec<&str> = s.split('+').collect();
    if parts.iter().any(|p| p.is_empty()) {
        return Err("has an empty part".into());
    }
    let (key, mods) = parts.split_last().expect("split gives at least one part");
    let mut mask = Modifiers::empty();
    let mut next_allowed = 0usize;
    for m in mods {
        let Some(i) = MODIFIER_ORDER.iter().position(|(n, _)| n == m) else {
            return Err(format!("\"{m}\" is not a modifier (use Ctrl, Alt, Shift, Super)"));
        };
        if mask.contains(MODIFIER_ORDER[i].1) {
            return Err(format!("repeats the {m} modifier"));
        }
        if i < next_allowed {
            return Err("modifiers must be in the order Ctrl, Alt, Shift, Super".into());
        }
        mask |= MODIFIER_ORDER[i].1;
        next_allowed = i + 1;
    }
    if !is_allowed_key(key) {
        return Err(format!("\"{key}\" is not a supported key name"));
    }
    if mods.is_empty() && !is_function_key(key) {
        return Err("needs at least one modifier (only F1-F24 may stand alone)".into());
    }
    let code = Code::from_str(key).map_err(|_| format!("\"{key}\" is not a supported key name"))?;
    Ok(Shortcut::new(if mask.is_empty() { None } else { Some(mask) }, code))
}

// ---------------------------------------------------------------------------
// Planning (pure)
// ---------------------------------------------------------------------------

/// Actions whose combination differs between `cur` and `next`.
pub(crate) fn changed_actions(cur: &Settings, next: &Settings) -> Vec<(Action, Option<String>)> {
    Action::ALL
        .iter()
        .filter(|a| a.get(cur) != a.get(next))
        .map(|a| (*a, a.get(next).map(str::to_string)))
        .collect()
}

#[derive(Debug, PartialEq)]
pub(crate) struct Plan {
    /// Combinations to register with the OS now (not already held by us).
    pub(crate) register: Vec<(Action, String)>,
    /// Combinations to release once everything else has succeeded.
    pub(crate) unregister: Vec<String>,
    /// What is registered after the plan completes.
    pub(crate) bound_after: Vec<(Action, String)>,
}

/// Decide OS work for `changes` given what we currently hold. Actions not in
/// `changes` are left alone (so a saved-but-unregistrable combo is not retried
/// by an unrelated patch).
pub(crate) fn plan(bound: &[(Action, String)], changes: &[(Action, Option<String>)]) -> Plan {
    let changing = |a: Action| changes.iter().any(|(c, _)| *c == a);
    let mut bound_after: Vec<(Action, String)> =
        bound.iter().filter(|(a, _)| !changing(*a)).cloned().collect();
    let mut register = Vec::new();
    for (action, new) in changes {
        if let Some(combo) = new {
            bound_after.push((*action, combo.clone()));
            if !bound.iter().any(|(_, c)| c == combo) {
                register.push((*action, combo.clone()));
            }
        }
    }
    let mut unregister = Vec::new();
    for (action, combo) in bound {
        if changing(*action)
            && !bound_after.iter().any(|(_, c)| c == combo)
            && !unregister.contains(combo)
        {
            unregister.push(combo.clone());
        }
    }
    Plan { register, unregister, bound_after }
}

/// Thin OS interface, faked in tests.
pub(crate) trait Backend {
    fn register(&self, combo: &str) -> Result<(), String>;
    fn unregister(&self, combo: &str) -> Result<(), String>;
}

#[derive(Debug, PartialEq)]
pub(crate) struct Failure {
    pub(crate) action: Action,
    pub(crate) combo: String,
    pub(crate) reason: String,
}

impl Failure {
    pub(crate) fn message(&self) -> String {
        format!(
            "Another app is already using {} (or Windows reserves it). Choose a different combination.",
            self.combo
        )
    }
}

/// Register every new combination; if one fails, release the ones this call
/// took so nothing changes. Old bindings are never touched here.
pub(crate) fn register_new(be: &dyn Backend, plan: &Plan) -> Result<(), Failure> {
    let mut done: Vec<&str> = Vec::new();
    for (action, combo) in &plan.register {
        if let Err(reason) = be.register(combo) {
            for c in done {
                let _ = be.unregister(c);
            }
            return Err(Failure { action: *action, combo: combo.clone(), reason });
        }
        done.push(combo);
    }
    Ok(())
}

/// Undo `register_new` (e.g. the settings file could not be saved).
pub(crate) fn abort_new(be: &dyn Backend, plan: &Plan) {
    for (_, combo) in &plan.register {
        let _ = be.unregister(combo);
    }
}

/// After a successful save: release the replaced combinations.
pub(crate) fn release_old(be: &dyn Backend, plan: &Plan, log: &dyn Fn(&str)) {
    for combo in &plan.unregister {
        if let Err(err) = be.unregister(combo) {
            log(&format!("hotkey: could not unregister {combo}: {err}"));
        }
    }
}

// ---------------------------------------------------------------------------
// Press gating (pure)
// ---------------------------------------------------------------------------

/// Windows repeats WM_HOTKEY while a key is held, which arrives as repeated
/// Pressed events. A press counts only if the key was released since the last
/// counted press, or if no press event was seen for `STALE_MS` (a held key
/// repeats every few tens of ms, so a long gap means a lost release).
const STALE_MS: u64 = 2000;

#[derive(Default)]
pub(crate) struct PressGate {
    down: bool,
    last_press_ms: u64,
}

impl PressGate {
    pub(crate) fn on_press(&mut self, now_ms: u64) -> bool {
        let fresh = !self.down || now_ms.saturating_sub(self.last_press_ms) > STALE_MS;
        self.down = true;
        self.last_press_ms = now_ms;
        fresh
    }

    pub(crate) fn on_release(&mut self) {
        self.down = false;
    }
}

// ---------------------------------------------------------------------------
// Runtime state and OS layer (not unit-tested)
// ---------------------------------------------------------------------------

#[derive(Default)]
pub(crate) struct Hotkeys {
    bound: Mutex<Vec<(Action, String)>>,
    gates: Mutex<HashMap<Action, PressGate>>,
    started: Mutex<Option<std::time::Instant>>,
}

impl Hotkeys {
    pub(crate) fn bound(&self) -> Vec<(Action, String)> {
        self.bound.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }

    pub(crate) fn set_bound(&self, bound: Vec<(Action, String)>) {
        *self.bound.lock().unwrap_or_else(|p| p.into_inner()) = bound;
    }

    fn action_for(&self, id: u32) -> Option<Action> {
        self.bound()
            .iter()
            .find(|(_, c)| parse(c).is_ok_and(|s| s.id() == id))
            .map(|(a, _)| *a)
    }

    fn now_ms(&self) -> u64 {
        let mut started = self.started.lock().unwrap_or_else(|p| p.into_inner());
        started.get_or_insert_with(std::time::Instant::now).elapsed().as_millis() as u64
    }
}

pub(crate) struct OsBackend<'a>(pub(crate) &'a AppHandle);

impl Backend for OsBackend<'_> {
    fn register(&self, combo: &str) -> Result<(), String> {
        let shortcut = parse(combo)?;
        self.0.global_shortcut().register(shortcut).map_err(|e| e.to_string())
    }

    fn unregister(&self, combo: &str) -> Result<(), String> {
        let shortcut = parse(combo)?;
        self.0.global_shortcut().unregister(shortcut).map_err(|e| e.to_string())
    }
}

/// Plugin-wide handler: acts on key presses only, off the event loop.
pub(crate) fn handle_event(app: &AppHandle, shortcut: &Shortcut, event: ShortcutEvent) {
    let hk = app.state::<Hotkeys>();
    let Some(action) = hk.action_for(shortcut.id()) else { return };
    if event.state != ShortcutState::Pressed {
        if let Ok(mut gates) = hk.gates.lock() {
            gates.entry(action).or_default().on_release();
        }
        return;
    }
    let now = hk.now_ms();
    let fresh = hk
        .gates
        .lock()
        .map(|mut g| g.entry(action).or_default().on_press(now))
        .unwrap_or(false);
    if !fresh {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || run_action(&app, action));
}

fn run_action(app: &AppHandle, action: Action) {
    match action {
        Action::Snooze => commands::do_snooze(app),
        Action::Widget => commands::do_toggle_widget(app),
        Action::Acknowledge => commands::do_acknowledge(app),
        Action::ClickThrough => commands::do_toggle_click_through(app),
    }
}

/// Startup: register each saved combination. A failure is logged and left in
/// the file; it never stops the others or the app.
pub(crate) fn register_saved(app: &AppHandle, settings: &Settings) {
    let hk = app.state::<Hotkeys>();
    let be = OsBackend(app);
    let mut bound: Vec<(Action, String)> = Vec::new();
    for action in Action::ALL {
        let Some(combo) = action.get(settings) else { continue };
        if bound.iter().any(|(_, c)| c == combo) {
            log_line(&format!(
                "hotkey: {} ({combo}) is already used by another action; not registered",
                action.label()
            ));
            continue;
        }
        match be.register(combo) {
            Ok(()) => bound.push((action, combo.to_string())),
            Err(err) => log_line(&format!("hotkey: could not register {} ({combo}): {err}", action.label())),
        }
    }
    hk.set_bound(bound);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    fn ok(s: &str) -> Shortcut {
        parse(s).unwrap_or_else(|e| panic!("{s}: {e}"))
    }

    #[test]
    fn accepted_forms_convert_to_shortcuts() {
        for s in [
            "Ctrl+Shift+KeyS", "Alt+F9", "Ctrl+Digit1", "Ctrl+Alt+Numpad5", "F1", "F24", "F13",
            "Ctrl+Alt+Shift+Super+KeyZ", "Super+Space", "Shift+ArrowUp", "Ctrl+Minus",
            "Ctrl+NumpadAdd", "Alt+Backquote", "Ctrl+BracketLeft", "Ctrl+PageDown", "Alt+Enter",
        ] {
            let sc = ok(s);
            // The plugin's own string form must parse back to the same key.
            assert_eq!(Shortcut::from_str(&sc.into_string()).unwrap(), sc, "{s}");
        }
        assert_eq!(ok("Ctrl+Shift+KeyS"), Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyS));
        assert_eq!(ok("F1"), Shortcut::new(None, Code::F1));
        assert_eq!(ok("Ctrl+Alt+Numpad5"), Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::Numpad5));
    }

    #[test]
    fn every_allowed_key_name_converts() {
        let mut names: Vec<String> = NAMED_KEYS.iter().map(|s| s.to_string()).collect();
        names.extend(('A'..='Z').map(|c| format!("Key{c}")));
        names.extend((0..=9).flat_map(|d| [format!("Digit{d}"), format!("Numpad{d}")]));
        names.extend((1..=24).map(|n| format!("F{n}")));
        for n in names {
            assert!(is_allowed_key(&n), "{n}");
            assert!(Code::from_str(&n).is_ok(), "{n}");
            ok(&format!("Ctrl+{n}"));
        }
    }

    #[test]
    fn rejected_forms() {
        for s in [
            "", "+", "Ctrl+", "+KeyS", "Ctrl++KeyS", "KeyS", "F0", "F25", "F01", "Digit1", "Space",
            "ctrl+KeyS", "Control+KeyS", "Ctrl+keys", "Ctrl+S", "Ctrl+Alt+S", "Ctrl+1", "Alt+F",
            "Ctrl+Ctrl+KeyS", "Alt+Ctrl+KeyS", "Shift+Alt+KeyS", "Super+Shift+KeyS",
            "Ctrl+KeyS+Alt", "Ctrl+KeyS+KeyD", "Ctrl+ControlLeft", "Ctrl+Escape", "Ctrl+Unidentified",
            "Ctrl+KeyS ", " Ctrl+KeyS", "Ctrl+Alt", "Ctrl+Shift", "Ctrl+KeyAA", "Ctrl+Numpad10",
            "Ctrl+Numlock", "Ctrl+F", "Meta+KeyS", "Cmd+KeyS",
        ] {
            assert!(parse(s).is_err(), "should reject {s:?}");
        }
    }

    fn b(a: Action, c: &str) -> (Action, String) {
        (a, c.to_string())
    }

    #[test]
    fn plan_new_binding_registers_only_it() {
        let p = plan(&[], &[(Action::Snooze, Some("Ctrl+F9".into()))]);
        assert_eq!(p.register, vec![b(Action::Snooze, "Ctrl+F9")]);
        assert!(p.unregister.is_empty());
        assert_eq!(p.bound_after, vec![b(Action::Snooze, "Ctrl+F9")]);
    }

    #[test]
    fn plan_change_registers_new_and_releases_old() {
        let bound = [b(Action::Snooze, "Ctrl+F9"), b(Action::Widget, "Alt+F1")];
        let p = plan(&bound, &[(Action::Snooze, Some("Ctrl+F10".into()))]);
        assert_eq!(p.register, vec![b(Action::Snooze, "Ctrl+F10")]);
        assert_eq!(p.unregister, vec!["Ctrl+F9".to_string()]);
        assert!(p.bound_after.contains(&b(Action::Widget, "Alt+F1")));
        assert!(p.bound_after.contains(&b(Action::Snooze, "Ctrl+F10")));
    }

    #[test]
    fn plan_null_unregisters_and_unbound_registers_nothing() {
        let bound = [b(Action::Snooze, "Ctrl+F9")];
        let p = plan(&bound, &[(Action::Snooze, None)]);
        assert!(p.register.is_empty());
        assert_eq!(p.unregister, vec!["Ctrl+F9".to_string()]);
        assert!(p.bound_after.is_empty());
        let p = plan(&[], &[(Action::Widget, None)]);
        assert_eq!(p, Plan { register: vec![], unregister: vec![], bound_after: vec![] });
        assert_eq!(plan(&bound, &[]).bound_after, bound.to_vec());
    }

    #[test]
    fn plan_swap_does_not_reregister_or_release_held_combos() {
        let bound = [b(Action::Snooze, "Ctrl+F9"), b(Action::Widget, "Ctrl+F10")];
        let p = plan(
            &bound,
            &[(Action::Snooze, Some("Ctrl+F10".into())), (Action::Widget, Some("Ctrl+F9".into()))],
        );
        assert!(p.register.is_empty());
        assert!(p.unregister.is_empty());
        assert!(p.bound_after.contains(&b(Action::Snooze, "Ctrl+F10")));
        assert!(p.bound_after.contains(&b(Action::Widget, "Ctrl+F9")));
    }

    #[test]
    fn changed_actions_reports_only_differences() {
        let cur = Settings { hotkey_snooze: Some("Ctrl+F9".into()), ..Settings::default() };
        let next = Settings { hotkey_snooze: None, hotkey_widget: Some("Alt+F1".into()), ..cur.clone() };
        assert_eq!(
            changed_actions(&cur, &next),
            vec![(Action::Snooze, None), (Action::Widget, Some("Alt+F1".into()))]
        );
        assert!(changed_actions(&cur, &cur).is_empty());
    }

    #[derive(Default)]
    struct Fake {
        held: RefCell<Vec<String>>,
        taken_by_other: Vec<String>,
    }

    impl Backend for Fake {
        fn register(&self, c: &str) -> Result<(), String> {
            if self.taken_by_other.iter().any(|t| t == c) || self.held.borrow().iter().any(|t| t == c) {
                return Err("taken".into());
            }
            self.held.borrow_mut().push(c.into());
            Ok(())
        }
        fn unregister(&self, c: &str) -> Result<(), String> {
            self.held.borrow_mut().retain(|h| h != c);
            Ok(())
        }
    }

    #[test]
    fn clash_leaves_previous_binding_registered() {
        let fake = Fake { held: RefCell::new(vec!["Ctrl+F9".into()]), taken_by_other: vec!["Ctrl+F11".into()] };
        let bound = [b(Action::Snooze, "Ctrl+F9")];
        let p = plan(&bound, &[(Action::Snooze, Some("Ctrl+F11".into()))]);
        let f = register_new(&fake, &p).unwrap_err();
        assert_eq!((f.action, f.combo.as_str()), (Action::Snooze, "Ctrl+F11"));
        assert!(f.message().contains("Another app is already using Ctrl+F11"));
        assert_eq!(*fake.held.borrow(), vec!["Ctrl+F9".to_string()]);
    }

    #[test]
    fn partial_failure_releases_what_this_call_took() {
        let fake = Fake { taken_by_other: vec!["Ctrl+F11".into()], ..Fake::default() };
        let p = plan(
            &[],
            &[(Action::Snooze, Some("Ctrl+F10".into())), (Action::Widget, Some("Ctrl+F11".into()))],
        );
        assert_eq!(register_new(&fake, &p).unwrap_err().action, Action::Widget);
        assert!(fake.held.borrow().is_empty());
    }

    #[test]
    fn success_then_release_old_and_abort() {
        let fake = Fake { held: RefCell::new(vec!["Ctrl+F9".into()]), ..Fake::default() };
        let p = plan(&[b(Action::Snooze, "Ctrl+F9")], &[(Action::Snooze, Some("Ctrl+F10".into()))]);
        register_new(&fake, &p).unwrap();
        assert_eq!(fake.held.borrow().len(), 2);
        release_old(&fake, &p, &|_| {});
        assert_eq!(*fake.held.borrow(), vec!["Ctrl+F10".to_string()]);
        // Save failure path: new combination released again.
        abort_new(&fake, &p);
        assert!(fake.held.borrow().is_empty());
    }

    #[test]
    fn gate_ignores_repeats_until_release() {
        let mut g = PressGate::default();
        assert!(g.on_press(0));
        for t in [500, 530, 560, 1000, 2400] {
            assert!(!g.on_press(t), "repeat at {t}");
        }
        g.on_release();
        assert!(g.on_press(2500));
        assert!(!g.on_press(2530));
    }

    #[test]
    fn gate_recovers_from_a_lost_release() {
        let mut g = PressGate::default();
        assert!(g.on_press(100));
        assert!(!g.on_press(300));
        assert!(g.on_press(300 + STALE_MS + 1));
    }
}
