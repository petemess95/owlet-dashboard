//! Controller snooze: a chosen controller button (or combination) snoozes an
//! amber or red alert. Slots 0-3 are XInput; slots 4.. are PlayStation pads
//! read as HID devices (`controller_hid`), their buttons translated into the
//! XInput layout. All slots go through the same rules. The press-edge / hold /
//! guard rules, the poll predicate, the disconnected-slot back-off and button
//! capture are pure and unit-tested; the thin OS layer (`read_slot`, the
//! poller thread, the capture command) is not.
//!
//! Safety:
//! - The only action is `commands::do_snooze`, the same path as the tray and
//!   the snooze hotkey. Controller input never acknowledges.
//! - Presses in the first `GUARD_MS` of an appearance, and presses that began
//!   before the guard ended (including buttons held when the alert appeared),
//!   never count: a slot must be seen fully released after the guard first.
//! - XInput and HID are only read while `should_poll` holds (an alert showing
//!   on this PC and controller_mode "chosen"), or inside a running capture.
//!   HID devices are closed (reader threads stopped) as soon as that ends.
//! - The poller runs on its own thread, never holds the alarm, fallback or
//!   applied locks while reading controllers, and a panic stops only the
//!   poller. HID reads happen on per-device threads; the poller only loads
//!   their latest state, so a stuck device can't block it.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Condvar, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::{AppHandle, Manager};

use crate::controller_hid::{HidSlotStates, HidSlots, HID_SLOTS};
use crate::log_line;
use crate::settings::DesktopSettings;

/// Presses during the first 2 s of each appearance are ignored.
pub(crate) const GUARD_MS: u64 = 2000;
/// ~20 Hz.
pub(crate) const POLL_MS: u64 = 50;
/// A slot that reported "not connected" is re-checked at most this often.
pub(crate) const RECHECK_MS: u64 = 1000;
pub(crate) const CAPTURE_TIMEOUT_MS: u64 = 10_000;
pub(crate) const CAPTURE_COLLECT_MS: u64 = 1500;
/// At capture start, how long to wait for newly opened HID pads' first report.
pub(crate) const CAPTURE_HID_SETTLE_MS: u64 = 300;
/// XInput slots.
pub(crate) const SLOTS: usize = 4;
/// XInput slots followed by HID (PlayStation) slots.
pub(crate) const ALL_SLOTS: usize = SLOTS + HID_SLOTS;

/// wButtons per XInput slot, None = not connected.
pub(crate) type Slots = [Option<u16>; SLOTS];
/// The combined view: XInput slots 0-3, then HID slots.
pub(crate) type AllSlots = [Option<u16>; ALL_SLOTS];

/// One view of every controller: XInput first, then HID pads.
pub(crate) fn merge(xinput: &Slots, hid: &HidSlotStates) -> AllSlots {
    let mut out = [None; ALL_SLOTS];
    out[..SLOTS].copy_from_slice(xinput);
    out[SLOTS..].copy_from_slice(hid);
    out
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Level {
    None,
    Amber,
    Red,
}

impl Level {
    pub(crate) fn from_active(active: &str) -> Level {
        match active {
            "amber" => Level::Amber,
            "red" => Level::Red,
            _ => Level::None,
        }
    }
}

// ---------------------------------------------------------------------------
// Pure logic
// ---------------------------------------------------------------------------

/// Whether the snooze poller may call XInput at all.
pub(crate) fn should_poll(level: Level, controller_mode: &str, buttons: Option<u16>) -> bool {
    level != Level::None && controller_mode == "chosen" && buttons.is_some_and(|b| b != 0)
}

/// One tick's input to the snooze logic. `slots` is any per-slot list
/// (XInput only, or the combined `AllSlots`); slots past `ALL_SLOTS` are
/// ignored and missing ones count as not connected.
pub(crate) struct Tick<S = Slots> {
    pub(crate) now_ms: u64,
    pub(crate) level: Level,
    /// Bumped by the shell whenever the alert starts showing or changes level.
    pub(crate) appearance: u64,
    pub(crate) slots: S,
}

#[derive(Default)]
pub(crate) struct SnoozeLogic {
    /// (appearance marker, level, start ms) of the current appearance.
    current: Option<(u64, Level, u64)>,
    /// Slot was seen with none of the chosen bits down after the guard ended.
    armed: [bool; ALL_SLOTS],
    fired: bool,
}

impl SnoozeLogic {
    /// Forget the current appearance (no alert, or polling stopped).
    pub(crate) fn clear(&mut self) {
        *self = SnoozeLogic::default();
    }

    /// Returns the slot that snoozed, if any. `mask` is controller_buttons;
    /// `chosen` is controller_mode == "chosen".
    pub(crate) fn tick<S: AsRef<[Option<u16>]>>(&mut self, t: &Tick<S>, chosen: bool, mask: u16) -> Option<usize> {
        if t.level == Level::None {
            self.clear();
            return None;
        }
        // Any change of marker or level (none -> alert, amber -> red, and
        // red -> amber too) is a new appearance: guard and hold rules restart.
        let new = match self.current {
            Some((a, l, _)) => a != t.appearance || l != t.level,
            None => true,
        };
        if new {
            self.current = Some((t.appearance, t.level, t.now_ms));
            self.armed = [false; ALL_SLOTS];
            self.fired = false;
        }
        if !chosen || mask == 0 || self.fired {
            return None;
        }
        let start = self.current.map(|c| c.2).unwrap_or(t.now_ms);
        if t.now_ms.saturating_sub(start) < GUARD_MS {
            // Inside the guard nothing arms: a press started here (or held
            // from before the alert) must be released after the guard.
            self.armed = [false; ALL_SLOTS];
            return None;
        }
        let slots = t.slots.as_ref();
        for i in 0..ALL_SLOTS {
            let Some(b) = slots.get(i).copied().flatten() else {
                self.armed[i] = false;
                continue;
            };
            if b & mask == 0 {
                self.armed[i] = true;
            } else if b & mask == mask && self.armed[i] {
                self.armed[i] = false;
                self.fired = true;
                return Some(i);
            }
        }
        None
    }
}

/// Back-off for disconnected slots: after "not connected", skip that slot
/// for `RECHECK_MS`.
#[derive(Default)]
pub(crate) struct SlotReader {
    retry_at: [Option<u64>; SLOTS],
}

impl SlotReader {
    pub(crate) fn read(&mut self, now_ms: u64, mut get: impl FnMut(u32) -> Option<u16>) -> Slots {
        let mut out = [None; SLOTS];
        for (i, o) in out.iter_mut().enumerate() {
            if self.retry_at[i].is_some_and(|at| now_ms < at) {
                continue;
            }
            *o = get(i as u32);
            self.retry_at[i] = if o.is_none() { Some(now_ms + RECHECK_MS) } else { None };
        }
        out
    }
}

/// Button capture for the settings page.
pub(crate) struct Capture {
    start_ms: u64,
    /// Per slot, bits that were down at start and not yet released.
    blocked: [u16; ALL_SLOTS],
    /// (slot, first fresh press ms, union) once a fresh press happened.
    pressed: Option<(usize, u64, u16)>,
}

pub(crate) enum CaptureStep {
    Waiting,
    Done(Option<u16>),
}

impl Capture {
    /// `initial`: the state of every slot when capture starts (XInput only
    /// or `AllSlots`; missing slots count as not connected).
    pub(crate) fn new(now_ms: u64, initial: &[Option<u16>]) -> Capture {
        let mut blocked = [0u16; ALL_SLOTS];
        for (b, s) in blocked.iter_mut().zip(initial) {
            *b = s.unwrap_or(0);
        }
        Capture { start_ms: now_ms, blocked, pressed: None }
    }

    pub(crate) fn tick(&mut self, now_ms: u64, slots: &[Option<u16>]) -> CaptureStep {
        let mut fresh = [0u16; ALL_SLOTS];
        for (i, (f, blocked)) in fresh.iter_mut().zip(self.blocked.iter_mut()).enumerate() {
            let cur = slots.get(i).copied().flatten().unwrap_or(0);
            // A bit released once is never blocked again.
            *blocked &= cur;
            *f = cur & !*blocked;
        }
        match self.pressed {
            None => {
                if let Some(i) = (0..ALL_SLOTS).find(|&i| fresh[i] != 0) {
                    self.pressed = Some((i, now_ms, fresh[i]));
                    return CaptureStep::Waiting;
                }
                if now_ms.saturating_sub(self.start_ms) >= CAPTURE_TIMEOUT_MS {
                    return CaptureStep::Done(None);
                }
                CaptureStep::Waiting
            }
            Some((i, t0, union)) => {
                // Only the slot that pressed first: snooze needs one slot.
                let union = union | fresh[i];
                self.pressed = Some((i, t0, union));
                if fresh[i] == 0 || now_ms.saturating_sub(t0) >= CAPTURE_COLLECT_MS {
                    CaptureStep::Done(Some(union))
                } else {
                    CaptureStep::Waiting
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Shared alert state (written by the alert thread, read by the poller)
// ---------------------------------------------------------------------------

struct Published {
    level: Level,
    appearance: u64,
}

static PUBLISHED: Mutex<Published> = Mutex::new(Published { level: Level::None, appearance: 0 });
static WAKE: Condvar = Condvar::new();
static CAPTURING: AtomicBool = AtomicBool::new(false);

fn lock() -> MutexGuard<'static, Published> {
    PUBLISHED.lock().unwrap_or_else(|p| p.into_inner())
}

/// Called by the alert thread with the level it just applied ("amber",
/// "red" or anything else for none). Never blocks beyond a short lock.
pub(crate) fn publish(active: &str) {
    let level = Level::from_active(active);
    let mut p = lock();
    if p.level != level {
        p.level = level;
        p.appearance = p.appearance.wrapping_add(1);
        WAKE.notify_all();
    }
}

// ---------------------------------------------------------------------------
// OS layer
// ---------------------------------------------------------------------------

/// wButtons of one slot, or None if not connected (or any error).
#[cfg(windows)]
fn read_slot(i: u32) -> Option<u16> {
    use windows::Win32::UI::Input::XboxController::{XInputGetState, XINPUT_STATE};
    let mut st = XINPUT_STATE::default();
    // SAFETY: valid out pointer to a local XINPUT_STATE.
    let r = unsafe { XInputGetState(i, &mut st) };
    (r == 0).then_some(st.Gamepad.wButtons.0)
}

#[cfg(not(windows))]
fn read_slot(_i: u32) -> Option<u16> {
    None
}

fn ms_since(epoch: Instant) -> u64 {
    epoch.elapsed().as_millis() as u64
}

/// Start the snooze poller thread. It parks on a condvar while no alert is
/// showing and makes no XInput calls then, with no HID device open.
pub(crate) fn spawn_poller(app: AppHandle) {
    let spawned = std::thread::Builder::new().name("owlet-controller".into()).spawn(move || {
        let run = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| poller(&app)));
        if run.is_err() {
            log_line("controller poller panicked; controller snooze stopped");
        }
    });
    if let Err(err) = spawned {
        log_line(&format!("controller poller not started: {err}"));
    }
}

fn poller(app: &AppHandle) {
    let epoch = Instant::now();
    let mut logic = SnoozeLogic::default();
    let mut reader = SlotReader::default();
    // Open only while polling; dropping it stops the readers and closes the
    // devices.
    let mut hid: Option<HidSlots> = None;
    loop {
        // Snapshot under the short lock; never held during reads or snooze.
        let (level, appearance) = {
            let p = lock();
            (p.level, p.appearance)
        };
        let s = app.state::<DesktopSettings>().get();
        if !should_poll(level, &s.controller_mode, s.controller_buttons) {
            logic.clear();
            reader = SlotReader::default();
            hid = None;
            // Park: woken by publish(); the timeout only re-reads settings
            // (a settings change during an alert), it never touches XInput.
            let p = lock();
            if p.level == level && p.appearance == appearance {
                let _ = WAKE.wait_timeout(p, Duration::from_millis(1000));
            }
            continue;
        }
        let now = ms_since(epoch);
        let hid_slots = hid.get_or_insert_with(HidSlots::new).read(now);
        let slots = merge(&reader.read(now, read_slot), &hid_slots);
        let tick = Tick { now_ms: now, level, appearance, slots };
        if let Some(slot) = logic.tick(&tick, true, s.controller_buttons.unwrap_or(0)) {
            let kind = if slot < SLOTS { "XInput" } else { "PlayStation" };
            log_line(&format!("controller snooze: slot {slot} ({kind})"));
            crate::commands::do_snooze(app);
        }
        std::thread::sleep(Duration::from_millis(POLL_MS));
    }
}

/// Blocking capture; returns `{buttons}` or null. One at a time.
pub(crate) fn capture_blocking() -> Value {
    if CAPTURING.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst).is_err() {
        return Value::Null;
    }
    struct Reset;
    impl Drop for Reset {
        fn drop(&mut self) {
            CAPTURING.store(false, Ordering::SeqCst);
        }
    }
    let _reset = Reset;
    let epoch = Instant::now();
    let mut reader = SlotReader::default();
    let mut hid = HidSlots::new();
    // Opens the pads; their first reports arrive a few ms later. Wait for
    // them (briefly) so a button already held now is blocked like XInput's.
    let _ = hid.read(0);
    while !hid.all_reported() && ms_since(epoch) < CAPTURE_HID_SETTLE_MS {
        std::thread::sleep(Duration::from_millis(10));
    }
    let now = ms_since(epoch);
    let mut cap = Capture::new(0, &merge(&reader.read(now, read_slot), &hid.read(now)));
    loop {
        std::thread::sleep(Duration::from_millis(POLL_MS));
        let now = ms_since(epoch);
        let slots = merge(&reader.read(now, read_slot), &hid.read(now));
        if let CaptureStep::Done(r) = cap.tick(now, &slots) {
            return match r {
                Some(mask) if mask != 0 => json!({ "buttons": mask }),
                _ => Value::Null,
            };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const A: u16 = 0x1000;
    const B: u16 = 0x2000;
    const LB: u16 = 0x0100;
    const RB: u16 = 0x0200;

    struct Sim {
        logic: SnoozeLogic,
        now: u64,
        level: Level,
        appearance: u64,
        mode_chosen: bool,
        mask: u16,
    }

    impl Sim {
        fn new(mask: u16) -> Sim {
            Sim { logic: SnoozeLogic::default(), now: 0, level: Level::None, appearance: 0, mode_chosen: true, mask }
        }
        fn show(&mut self, level: Level) {
            if level != self.level {
                self.appearance += 1;
            }
            self.level = level;
        }
        fn at(&mut self, now: u64, slots: Slots) -> Option<usize> {
            self.now = now;
            let t = Tick { now_ms: now, level: self.level, appearance: self.appearance, slots };
            self.logic.tick(&t, self.mode_chosen, self.mask)
        }
        fn one(&mut self, now: u64, b: u16) -> Option<usize> {
            self.at(now, [Some(b), None, None, None])
        }
    }

    #[test]
    fn press_edge_after_guard_snoozes_once() {
        let mut s = Sim::new(A);
        s.show(Level::Amber);
        assert_eq!(s.one(0, 0), None);
        assert_eq!(s.one(2000, 0), None);
        assert_eq!(s.one(2050, A), Some(0));
        // Held, released, pressed again: at most one snooze per appearance.
        assert_eq!(s.one(2100, A), None);
        assert_eq!(s.one(2150, 0), None);
        assert_eq!(s.one(2200, A), None);
    }

    #[test]
    fn held_state_alone_never_fires() {
        let mut s = Sim::new(A);
        s.show(Level::Red);
        for t in (0..6000).step_by(50) {
            assert_eq!(s.one(t, A), None);
        }
    }

    #[test]
    fn held_before_alert_counts_only_after_release_and_repress() {
        let mut s = Sim::new(A);
        assert_eq!(s.one(0, A), None);
        s.show(Level::Red);
        assert_eq!(s.one(50, A), None);
        assert_eq!(s.one(3000, A), None);
        assert_eq!(s.one(3050, 0), None);
        assert_eq!(s.one(3100, A), Some(0));
    }

    #[test]
    fn guard_ignores_presses_in_first_two_seconds() {
        let mut s = Sim::new(A);
        s.show(Level::Amber);
        assert_eq!(s.one(0, 0), None);
        for t in (50..GUARD_MS).step_by(100) {
            assert_eq!(s.one(t, A), None);
            assert_eq!(s.one(t + 50, 0), None);
        }
    }

    #[test]
    fn press_held_across_guard_end_does_not_count() {
        let mut s = Sim::new(A);
        s.show(Level::Amber);
        assert_eq!(s.one(0, 0), None);
        assert_eq!(s.one(1950, A), None);
        assert_eq!(s.one(2000, A), None);
        assert_eq!(s.one(2500, A), None);
        assert_eq!(s.one(2550, 0), None);
        assert_eq!(s.one(2600, A), Some(0));
    }

    #[test]
    fn press_landing_right_after_guard_without_a_seen_release_does_not_count() {
        let mut s = Sim::new(A);
        s.show(Level::Amber);
        assert_eq!(s.one(1990, 0), None);
        // First observation after the guard already has it down.
        assert_eq!(s.one(2040, A), None);
    }

    #[test]
    fn return_after_snooze_reapplies_guard_and_hold_rule() {
        let mut s = Sim::new(A);
        s.show(Level::Amber);
        assert_eq!(s.one(0, 0), None);
        assert_eq!(s.one(2000, 0), None);
        assert_eq!(s.one(2050, A), Some(0));
        s.show(Level::None);
        assert_eq!(s.one(2100, A), None);
        s.show(Level::Amber);
        // Held through the return: not armed.
        assert_eq!(s.one(20_000, A), None);
        assert_eq!(s.one(20_500, 0), None);
        assert_eq!(s.one(21_000, A), None, "inside new guard");
        assert_eq!(s.one(21_500, 0), None);
        assert_eq!(s.one(22_000, 0), None);
        assert_eq!(s.one(22_050, A), Some(0));
    }

    #[test]
    fn new_appearance_marker_alone_restarts() {
        let mut s = Sim::new(A);
        s.show(Level::Amber);
        assert_eq!(s.one(0, 0), None);
        assert_eq!(s.one(2000, 0), None);
        s.appearance += 1;
        assert_eq!(s.one(2050, A), None);
    }

    #[test]
    fn amber_to_red_escalation_restarts_the_guard() {
        let mut s = Sim::new(A);
        s.show(Level::Amber);
        assert_eq!(s.one(0, 0), None);
        assert_eq!(s.one(5000, 0), None);
        s.show(Level::Red);
        assert_eq!(s.one(5050, 0), None);
        assert_eq!(s.one(5100, A), None);
        assert_eq!(s.one(6000, 0), None);
        assert_eq!(s.one(7050, 0), None);
        assert_eq!(s.one(7100, A), Some(0));
    }

    #[test]
    fn other_buttons_do_nothing() {
        let mut s = Sim::new(A);
        s.show(Level::Red);
        assert_eq!(s.one(0, 0), None);
        assert_eq!(s.one(2000, 0), None);
        for b in [B, LB, RB, 0x0001, 0x0010, 0x8000, !A] {
            assert_eq!(s.one(s.now + 50, b), None);
            assert_eq!(s.one(s.now + 50, 0), None);
        }
        // Extra buttons on the same slot don't block the chosen one.
        assert_eq!(s.one(s.now + 50, A | LB), Some(0));
    }

    #[test]
    fn partial_combination_does_nothing() {
        let mut s = Sim::new(LB | RB);
        s.show(Level::Red);
        assert_eq!(s.one(0, 0), None);
        assert_eq!(s.one(2000, 0), None);
        assert_eq!(s.one(2050, LB), None);
        assert_eq!(s.one(2100, 0), None);
        assert_eq!(s.one(2150, RB), None);
        // Partial then completing the combination is the edge into "all down".
        assert_eq!(s.one(2200, RB | LB), Some(0));
    }

    #[test]
    fn combination_split_across_two_slots_does_nothing() {
        let mut s = Sim::new(LB | RB);
        s.show(Level::Amber);
        assert_eq!(s.at(0, [None; SLOTS]), None);
        assert_eq!(s.at(2000, [Some(0), Some(0), None, None]), None);
        assert_eq!(s.at(2050, [Some(LB), Some(RB), None, None]), None);
        assert_eq!(s.at(2100, [Some(0), Some(0), None, None]), None);
        assert_eq!(s.at(2150, [Some(0), Some(LB | RB), None, None]), Some(1));
    }

    #[test]
    fn mode_off_or_zero_mask_does_nothing() {
        let mut s = Sim::new(A);
        s.mode_chosen = false;
        s.show(Level::Red);
        assert_eq!(s.one(2000, 0), None);
        assert_eq!(s.one(2050, A), None);
        let mut s = Sim::new(0);
        s.show(Level::Red);
        assert_eq!(s.one(2000, 0), None);
        assert_eq!(s.one(2050, 0xFFFF), None);
    }

    #[test]
    fn no_controller_connected_does_nothing() {
        let mut s = Sim::new(A);
        s.show(Level::Red);
        for t in (0..5000).step_by(50) {
            assert_eq!(s.at(t, [None; SLOTS]), None);
        }
    }

    #[test]
    fn slot_connecting_mid_appearance_with_combo_held_must_release() {
        let mut s = Sim::new(A);
        s.show(Level::Amber);
        assert_eq!(s.at(0, [None; SLOTS]), None);
        assert_eq!(s.at(2000, [None; SLOTS]), None);
        assert_eq!(s.at(2050, [None, Some(A), None, None]), None);
        assert_eq!(s.at(2100, [None, Some(0), None, None]), None);
        assert_eq!(s.at(2150, [None, Some(A), None, None]), Some(1));
    }

    #[test]
    fn should_poll_only_with_an_alert_and_chosen_mode() {
        assert!(!should_poll(Level::None, "chosen", Some(A)));
        assert!(!should_poll(Level::Amber, "off", Some(A)));
        assert!(!should_poll(Level::Red, "chosen", None));
        assert!(!should_poll(Level::Red, "chosen", Some(0)));
        assert!(should_poll(Level::Amber, "chosen", Some(A)));
        assert!(should_poll(Level::Red, "chosen", Some(A)));
    }

    #[test]
    fn publish_bumps_appearance_on_level_changes_only() {
        // Uses the process-wide state; this is the only test that touches it.
        let a0 = lock().appearance;
        publish("none");
        publish("amber");
        publish("amber");
        let a1 = lock().appearance;
        publish("red");
        publish("none");
        let a2 = lock().appearance;
        assert_eq!(lock().level, Level::None);
        assert!(a1 > a0);
        assert_eq!(a2, a1 + 2);
    }

    #[test]
    fn disconnected_slot_is_rechecked_at_most_once_a_second() {
        let mut r = SlotReader::default();
        let mut calls = [0u32; SLOTS];
        let mut get = |i: u32| {
            calls[i as usize] += 1;
            (i == 0).then_some(0u16)
        };
        for t in (0..2000).step_by(50) {
            r.read(t, &mut get);
        }
        assert_eq!(calls[0], 40);
        for c in &calls[1..] {
            assert_eq!(*c, 2);
        }
    }

    #[test]
    fn capture_ignores_held_buttons_until_released() {
        let mut c = Capture::new(0, &[Some(A), None, None, None]);
        assert!(matches!(c.tick(50, &[Some(A), None, None, None]), CaptureStep::Waiting));
        assert!(matches!(c.tick(100, &[Some(0), None, None, None]), CaptureStep::Waiting));
        assert!(matches!(c.tick(150, &[Some(A), None, None, None]), CaptureStep::Waiting));
        assert!(matches!(c.tick(200, &[Some(0), None, None, None]), CaptureStep::Done(Some(A))));
    }

    #[test]
    fn capture_collects_union_until_release_or_timeout() {
        let mut c = Capture::new(0, &[None; SLOTS]);
        assert!(matches!(c.tick(50, &[None, Some(LB), None, None]), CaptureStep::Waiting));
        assert!(matches!(c.tick(100, &[None, Some(LB | RB), None, None]), CaptureStep::Waiting));
        assert!(matches!(c.tick(150, &[None, Some(RB), None, None]), CaptureStep::Waiting));
        assert!(matches!(c.tick(200, &[None, Some(0), None, None]), CaptureStep::Done(Some(m)) if m == LB | RB));

        let mut c = Capture::new(0, &[None; SLOTS]);
        assert!(matches!(c.tick(50, &[Some(B), None, None, None]), CaptureStep::Waiting));
        assert!(matches!(c.tick(1000, &[Some(B), None, None, None]), CaptureStep::Waiting));
        assert!(matches!(c.tick(1550, &[Some(B), None, None, None]), CaptureStep::Done(Some(B))));
    }

    // ---- Combined XInput + HID (PlayStation) slots ----

    const PS: usize = SLOTS; // first HID slot in the combined view

    fn all(x: [Option<u16>; SLOTS], h: [Option<u16>; HID_SLOTS]) -> AllSlots {
        merge(&x, &h)
    }

    fn ps(b: u16) -> AllSlots {
        all([None; SLOTS], [Some(b), None, None, None])
    }

    fn at_all(s: &mut Sim, now: u64, slots: AllSlots) -> Option<usize> {
        s.now = now;
        let t = Tick { now_ms: now, level: s.level, appearance: s.appearance, slots };
        s.logic.tick(&t, s.mode_chosen, s.mask)
    }

    #[test]
    fn merge_puts_xinput_first_then_hid() {
        let m = all([Some(1), None, Some(3), None], [None, Some(5), None, Some(7)]);
        assert_eq!(m, [Some(1), None, Some(3), None, None, Some(5), None, Some(7)]);
    }

    #[test]
    fn playstation_pad_follows_guard_and_press_edge() {
        let mut s = Sim::new(A);
        s.show(Level::Amber);
        assert_eq!(at_all(&mut s, 0, ps(0)), None);
        for t in (50..GUARD_MS).step_by(100) {
            assert_eq!(at_all(&mut s, t, ps(A)), None, "inside guard");
            assert_eq!(at_all(&mut s, t + 50, ps(0)), None);
        }
        assert_eq!(at_all(&mut s, 2000, ps(0)), None);
        assert_eq!(at_all(&mut s, 2050, ps(B)), None, "other button");
        assert_eq!(at_all(&mut s, 2100, ps(0)), None);
        assert_eq!(at_all(&mut s, 2150, ps(A)), Some(PS));
        assert_eq!(at_all(&mut s, 2200, ps(0)), None);
        assert_eq!(at_all(&mut s, 2250, ps(A)), None, "once per appearance");
    }

    #[test]
    fn playstation_button_held_from_before_the_alert_must_be_released() {
        let mut s = Sim::new(LB | RB);
        assert_eq!(at_all(&mut s, 0, ps(LB | RB)), None);
        s.show(Level::Red);
        for t in (50..6000).step_by(50) {
            assert_eq!(at_all(&mut s, t, ps(LB | RB)), None);
        }
        assert_eq!(at_all(&mut s, 6000, ps(LB)), None, "partial release is not a release");
        assert_eq!(at_all(&mut s, 6050, ps(LB | RB)), None);
        assert_eq!(at_all(&mut s, 6100, ps(0)), None);
        assert_eq!(at_all(&mut s, 6150, ps(LB | RB)), Some(PS));
    }

    #[test]
    fn playstation_pad_appearing_with_button_held_must_release_first() {
        let mut s = Sim::new(A);
        s.show(Level::Amber);
        assert_eq!(at_all(&mut s, 0, all([None; SLOTS], [None; HID_SLOTS])), None);
        assert_eq!(at_all(&mut s, 2000, all([None; SLOTS], [None; HID_SLOTS])), None);
        // Opened during the alert (first report already has A down).
        assert_eq!(at_all(&mut s, 2050, ps(A)), None);
        // Device drops (error) and comes back still held: still not armed.
        assert_eq!(at_all(&mut s, 2100, all([None; SLOTS], [None; HID_SLOTS])), None);
        assert_eq!(at_all(&mut s, 3100, ps(A)), None);
        assert_eq!(at_all(&mut s, 3150, ps(0)), None);
        assert_eq!(at_all(&mut s, 3200, ps(A)), Some(PS));
    }

    #[test]
    fn xinput_and_playstation_pads_together() {
        let mut s = Sim::new(LB | RB);
        s.show(Level::Red);
        let xs = |x: u16, p: u16| all([Some(x), None, None, None], [None, Some(p), None, None]);
        assert_eq!(at_all(&mut s, 0, xs(0, 0)), None);
        assert_eq!(at_all(&mut s, 2000, xs(0, 0)), None);
        // Combination split across an Xbox pad and a PlayStation pad: no.
        assert_eq!(at_all(&mut s, 2050, xs(LB, RB)), None);
        assert_eq!(at_all(&mut s, 2100, xs(0, 0)), None);
        // XInput behaves exactly as before alongside HID slots.
        assert_eq!(at_all(&mut s, 2150, xs(LB | RB, 0)), Some(0));
        let mut s = Sim::new(LB | RB);
        s.show(Level::Red);
        assert_eq!(at_all(&mut s, 0, xs(0, 0)), None);
        assert_eq!(at_all(&mut s, 2000, xs(0, 0)), None);
        assert_eq!(at_all(&mut s, 2050, xs(0, LB | RB)), Some(PS + 1));
    }

    #[test]
    fn capture_sees_playstation_pads_and_ignores_their_held_buttons() {
        let mut c = Capture::new(0, &ps(A));
        assert!(matches!(c.tick(50, &ps(A)), CaptureStep::Waiting));
        assert!(matches!(c.tick(100, &ps(0)), CaptureStep::Waiting));
        assert!(matches!(c.tick(150, &ps(0x0400)), CaptureStep::Waiting));
        assert!(matches!(c.tick(200, &ps(0)), CaptureStep::Done(Some(0x0400))));

        let mut c = Capture::new(0, &all([Some(0), None, None, None], [None, Some(0), None, None]));
        let both = |x: u16, p: u16| all([Some(x), None, None, None], [None, Some(p), None, None]);
        assert!(matches!(c.tick(50, &both(0, 0x0001 | 0x0008)), CaptureStep::Waiting));
        // Only the slot that pressed first is collected.
        assert!(matches!(c.tick(100, &both(B, 0x0001 | 0x0008)), CaptureStep::Waiting));
        assert!(matches!(c.tick(150, &both(B, 0)), CaptureStep::Done(Some(m)) if m == 0x0009));
    }

    #[test]
    fn capture_times_out_with_null() {
        let mut c = Capture::new(0, &[Some(A), None, None, None]);
        assert!(matches!(c.tick(9_950, &[Some(A), None, None, None]), CaptureStep::Waiting));
        assert!(matches!(c.tick(10_000, &[Some(A), None, None, None]), CaptureStep::Done(None)));
    }
}
