//! "Armed" verdict (will this PC sound an alert right now?), keep-awake and
//! the sidecar watchdog decision. The decisions are pure functions over plain
//! inputs so they are unit-tested without Windows; the alert thread gathers
//! the facts and applies the results.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::audio::{select_devices, AudioStatus, EndpointFacts};

/// Polls before the server counts as unreachable (alert_loop's limit).
pub(crate) const UNREACHABLE_AFTER: u32 = 3;

// ---------------------------------------------------------------------------
// Verdict
// ---------------------------------------------------------------------------

pub(crate) struct VerdictInputs<'a> {
    /// Last /api/alert-state; `None` while the server is unreachable.
    pub(crate) state: Option<&'a Value>,
    pub(crate) failures: u32,
    /// Endpoint presence/mute, or the error from querying them.
    pub(crate) endpoints: &'a Result<EndpointFacts, String>,
    pub(crate) audio: &'a AudioStatus,
    pub(crate) alarm_device: &'a str,
    pub(crate) unmute_on_red: bool,
    /// Collecting now, or last known while the server is unreachable.
    pub(crate) collecting: bool,
    pub(crate) keep_awake_held: bool,
    pub(crate) watchdog_capped: bool,
}

/// Fault order; `not_collecting` (informational) always last.
const ORDER: [&str; 7] = ["server", "watchdog", "no_audio", "muted", "sleep", "stale", "not_collecting"];

fn problem(code: &str, text: impl Into<String>) -> Value {
    json!({ "code": code, "text": text.into() })
}

/// Shown before the first poll has finished.
pub(crate) fn pending_verdict() -> Value {
    json!({ "armed": false, "problems": [problem("server", "Checking alert status")] })
}

pub(crate) fn verdict(i: &VerdictInputs) -> Value {
    let mut problems: Vec<Value> = Vec::new();
    let unreachable = i.failures >= UNREACHABLE_AFTER;
    if unreachable {
        problems.push(problem("server", "Dashboard server not responding"));
        if i.watchdog_capped {
            problems.push(problem("watchdog", "Dashboard server keeps stopping"));
        }
    }
    if let (false, Some(state)) = (unreachable, i.state) {
        server_checks(state, &mut problems);
    }
    audio_checks(i, &mut problems);
    if i.collecting && !i.keep_awake_held {
        problems.push(problem("sleep", "PC may sleep"));
    }
    problems.sort_by_key(|p| ORDER.iter().position(|c| p["code"] == *c).unwrap_or(ORDER.len()));
    json!({ "armed": problems.is_empty(), "problems": problems })
}

fn server_checks(state: &Value, problems: &mut Vec<Value>) {
    if state["collecting"].as_bool() == Some(false) {
        let text = if state["remote_mode"].as_bool() == Some(true) {
            "Viewing a remote server"
        } else {
            "No Owlet account linked"
        };
        problems.push(problem("not_collecting", text));
    }
    let devices: Vec<&Value> = state["devices"]
        .as_array()
        .map(|a| a.iter().filter(|d| d["device_serial"].as_str() != Some("TEST")).collect())
        .unwrap_or_default();
    let multi = devices.len() > 1;
    for d in devices {
        let prefix = if multi {
            format!("{}: ", d["account_name"].as_str().unwrap_or("Sock"))
        } else {
            String::new()
        };
        if d["stale"].as_bool() == Some(true) {
            let text = match d["age_seconds"].as_f64() {
                Some(age) => format!("No data for {}m", ((age / 60.0).floor() as i64).max(1)),
                None => "No recent data".into(),
            };
            problems.push(problem("stale", format!("{prefix}{text}")));
        } else if d["sock_reporting"].as_bool() == Some(false) {
            let text = if d["charging"].as_bool() == Some(true) { "Charging" } else { "Sock off" };
            problems.push(problem("not_collecting", format!("{prefix}{text}")));
        }
    }
}

fn audio_checks(i: &VerdictInputs, problems: &mut Vec<Value>) {
    match i.endpoints {
        Err(_) => problems.push(problem("no_audio", "Can't check the audio device")),
        Ok(facts) => {
            let ids: Vec<String> = facts.endpoints.iter().map(|(id, _)| id.clone()).collect();
            let sel = select_devices(i.alarm_device, &ids, facts.default.as_deref());
            if ids.is_empty() {
                problems.push(problem("no_audio", "No audio device"));
            } else if sel.device_missing {
                problems.push(problem("no_audio", "Chosen audio device not found"));
            } else if sel.ids.is_empty() {
                problems.push(problem("no_audio", "No audio device"));
            } else if !i.unmute_on_red {
                let mutes: Vec<Option<bool>> = sel
                    .ids
                    .iter()
                    .map(|id| facts.endpoints.iter().find(|(e, _)| e == id).and_then(|(_, m)| *m))
                    .collect();
                if mutes.iter().any(|m| m.is_none()) {
                    problems.push(problem("no_audio", "Can't check the audio device"));
                } else if mutes.contains(&Some(true)) {
                    problems.push(problem("muted", "Windows is muted"));
                }
            }
        }
    }
    if i.audio.using_fallback {
        problems.push(problem("no_audio", "Alarm sound fell back to basic playback"));
    }
}

/// First problem that is a fault (anything but `not_collecting`).
pub(crate) fn first_fault(verdict: &Value) -> Option<String> {
    verdict["problems"]
        .as_array()?
        .iter()
        .find(|p| p["code"] != "not_collecting")
        .and_then(|p| p["text"].as_str())
        .map(String::from)
}

// ---------------------------------------------------------------------------
// Keep-awake
// ---------------------------------------------------------------------------

/// Whether to hold ES_SYSTEM_REQUIRED. `collecting` is `None` while the
/// server is unreachable: the last known value is kept, so an outage never
/// releases it on its own.
pub(crate) fn want_keep_awake(setting: bool, collecting: Option<bool>, last_collecting: bool) -> bool {
    setting && collecting.unwrap_or(last_collecting)
}

/// Tracks what we asked Windows for; only calls on changes.
#[derive(Default)]
pub(crate) struct KeepAwake {
    /// What was last applied successfully; `None` forces the next call.
    applied: Option<bool>,
}

impl KeepAwake {
    /// Apply `want` if it changed. Returns whether keep-awake is held.
    /// Must run on the long-lived alert thread: the execution state belongs
    /// to the calling thread, and process exit releases it, which covers
    /// "release on exit".
    pub(crate) fn update(&mut self, want: bool, set: impl FnOnce(bool) -> bool) -> bool {
        if self.applied != Some(want) {
            self.applied = set(want).then_some(want);
        }
        self.applied == Some(true)
    }
}

#[cfg(windows)]
pub(crate) fn set_execution_state(hold: bool) -> bool {
    use windows::Win32::System::Power::{SetThreadExecutionState, ES_CONTINUOUS, ES_SYSTEM_REQUIRED};
    let flags = if hold { ES_CONTINUOUS | ES_SYSTEM_REQUIRED } else { ES_CONTINUOUS };
    let ok = unsafe { SetThreadExecutionState(flags) }.0 != 0;
    crate::log_line(&format!("keep-awake {}: {}", if hold { "hold" } else { "release" }, if ok { "ok" } else { "FAILED" }));
    ok
}

#[cfg(not(windows))]
pub(crate) fn set_execution_state(_hold: bool) -> bool {
    false
}

// ---------------------------------------------------------------------------
// Sidecar watchdog
// ---------------------------------------------------------------------------

pub(crate) const RESTART_SPACING: Duration = Duration::from_secs(45);
pub(crate) const RESTART_WINDOW: Duration = Duration::from_secs(600);
pub(crate) const MAX_RESTARTS: usize = 3;

#[derive(Debug, PartialEq)]
pub(crate) enum Watch {
    /// Nothing to do (server fine, child still running, or waiting).
    Idle,
    Restart,
    /// Cap reached while the server is still unreachable.
    Capped,
}

/// `child_running`: the sidecar we spawned has not exited (false when we
/// never spawned one). `history`: restart times, oldest first.
pub(crate) fn watchdog_decision(failures: u32, child_running: bool, history: &VecDeque<Instant>, now: Instant) -> Watch {
    if failures < UNREACHABLE_AFTER || child_running {
        return Watch::Idle;
    }
    let recent = history.iter().filter(|t| now.saturating_duration_since(**t) < RESTART_WINDOW).count();
    if recent >= MAX_RESTARTS {
        return Watch::Capped;
    }
    if history.back().is_some_and(|t| now.saturating_duration_since(*t) < RESTART_SPACING) {
        return Watch::Idle;
    }
    Watch::Restart
}

/// Drop restarts older than the rolling window.
pub(crate) fn prune_history(history: &mut VecDeque<Instant>, now: Instant) {
    while history.front().is_some_and(|t| now.saturating_duration_since(*t) >= RESTART_WINDOW) {
        history.pop_front();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok_audio() -> Result<EndpointFacts, String> {
        Ok(EndpointFacts { endpoints: vec![("spk".into(), Some(false))], default: Some("spk".into()) })
    }

    struct Case {
        state: Option<Value>,
        failures: u32,
        endpoints: Result<EndpointFacts, String>,
        audio: AudioStatus,
        alarm_device: String,
        unmute_on_red: bool,
        collecting: bool,
        held: bool,
        capped: bool,
    }

    impl Case {
        fn new(state: Value) -> Self {
            Case {
                state: Some(state),
                failures: 0,
                endpoints: ok_audio(),
                audio: AudioStatus::default(),
                alarm_device: "default".into(),
                unmute_on_red: true,
                collecting: true,
                held: true,
                capped: false,
            }
        }
        fn run(&self) -> Value {
            verdict(&VerdictInputs {
                state: self.state.as_ref(),
                failures: self.failures,
                endpoints: &self.endpoints,
                audio: &self.audio,
                alarm_device: &self.alarm_device,
                unmute_on_red: self.unmute_on_red,
                collecting: self.collecting,
                keep_awake_held: self.held,
                watchdog_capped: self.capped,
            })
        }
    }

    fn codes(v: &Value) -> Vec<String> {
        v["problems"].as_array().unwrap().iter().map(|p| p["code"].as_str().unwrap().to_string()).collect()
    }
    fn texts(v: &Value) -> Vec<String> {
        v["problems"].as_array().unwrap().iter().map(|p| p["text"].as_str().unwrap().to_string()).collect()
    }

    fn healthy() -> Value {
        json!({ "collecting": true, "devices": [{ "device_serial": "A1", "account_name": "Baby", "sock_reporting": true }] })
    }

    #[test]
    fn pending_default_is_not_armed() {
        let v = pending_verdict();
        assert_eq!(v["armed"], false);
        assert_eq!(codes(&v), ["server"]);
        assert_eq!(texts(&v), ["Checking alert status"]);
    }

    #[test]
    fn armed_only_with_no_problems() {
        let v = Case::new(healthy()).run();
        assert_eq!(v, json!({ "armed": true, "problems": [] }));
        let mut c = Case::new(json!({ "collecting": true, "devices": [{ "sock_reporting": false }] }));
        c.collecting = true;
        let v = c.run();
        assert_eq!(v["armed"], false);
        assert_eq!(codes(&v), ["not_collecting"]);
        assert_eq!(texts(&v), ["Sock off"]);
    }

    #[test]
    fn unreachable_server_skips_server_checks_but_runs_local_ones() {
        let mut c = Case::new(json!({ "collecting": false, "devices": [{ "stale": true }] }));
        c.failures = 3;
        c.state = None;
        c.held = false;
        c.endpoints = Ok(EndpointFacts::default());
        let v = c.run();
        assert_eq!(codes(&v), ["server", "no_audio", "sleep"]);
        assert_eq!(texts(&v)[0], "Dashboard server not responding");
        // Two failures are not yet "unreachable".
        let mut c = Case::new(healthy());
        c.failures = 2;
        assert_eq!(c.run()["armed"], true);
    }

    #[test]
    fn watchdog_reported_only_when_capped_and_unreachable() {
        let mut c = Case::new(healthy());
        c.capped = true;
        assert_eq!(c.run()["armed"], true);
        c.failures = 3;
        c.state = None;
        let v = c.run();
        assert_eq!(codes(&v), ["server", "watchdog"]);
        assert_eq!(texts(&v)[1], "Dashboard server keeps stopping");
    }

    #[test]
    fn not_collecting_texts() {
        let v = Case::new(json!({ "collecting": false, "remote_mode": true, "devices": [] })).run();
        assert_eq!(texts(&v), ["Viewing a remote server"]);
        let v = Case::new(json!({ "collecting": false, "devices": [] })).run();
        assert_eq!(codes(&v), ["not_collecting"]);
        assert_eq!(texts(&v), ["No Owlet account linked"]);
        let v = Case::new(json!({ "collecting": true, "devices": [{ "sock_reporting": false, "charging": true }] })).run();
        assert_eq!(texts(&v), ["Charging"]);
    }

    #[test]
    fn stale_texts() {
        let dev = |age: Value| json!({ "collecting": true, "devices": [{ "stale": true, "age_seconds": age }] });
        assert_eq!(texts(&Case::new(dev(json!(30))).run()), ["No data for 1m"]);
        assert_eq!(texts(&Case::new(dev(json!(359))).run()), ["No data for 5m"]);
        assert_eq!(texts(&Case::new(dev(Value::Null)).run()), ["No recent data"]);
        assert_eq!(codes(&Case::new(dev(json!(600))).run()), ["stale"]);
    }

    #[test]
    fn multi_device_prefix_and_test_device_ignored() {
        let state = json!({ "collecting": true, "devices": [
            { "device_serial": "A", "account_name": "Ann", "stale": true, "age_seconds": 120 },
            { "device_serial": "B", "account_name": "Bob", "sock_reporting": false },
            { "device_serial": "TEST", "account_name": "Test", "stale": true },
        ]});
        assert_eq!(texts(&Case::new(state).run()), ["Ann: No data for 2m", "Bob: Sock off"]);
        // One real device plus TEST: no prefix, TEST ignored.
        let state = json!({ "collecting": true, "devices": [
            { "device_serial": "A", "account_name": "Ann", "stale": true, "age_seconds": 120 },
            { "device_serial": "TEST", "account_name": "Test", "sock_reporting": false },
        ]});
        assert_eq!(texts(&Case::new(state).run()), ["No data for 2m"]);
    }

    #[test]
    fn faults_ordered_before_not_collecting() {
        let mut c = Case::new(json!({ "collecting": false, "devices": [
            { "device_serial": "A", "account_name": "A", "sock_reporting": false },
            { "device_serial": "B", "account_name": "B", "stale": true },
        ]}));
        c.collecting = true;
        c.held = false;
        c.unmute_on_red = false;
        c.endpoints = Ok(EndpointFacts { endpoints: vec![("spk".into(), Some(true))], default: Some("spk".into()) });
        c.audio.using_fallback = true;
        let v = c.run();
        assert_eq!(codes(&v), ["no_audio", "muted", "sleep", "stale", "not_collecting", "not_collecting"]);
        assert_eq!(first_fault(&v).as_deref(), Some("Alarm sound fell back to basic playback"));
    }

    #[test]
    fn audio_device_checks() {
        let mut c = Case::new(healthy());
        c.endpoints = Ok(EndpointFacts::default());
        assert_eq!(texts(&c.run()), ["No audio device"]);
        c.endpoints = ok_audio();
        c.alarm_device = "gone".into();
        assert_eq!(texts(&c.run()), ["Chosen audio device not found"]);
        c.alarm_device = "spk".into();
        assert_eq!(c.run()["armed"], true);
        c.endpoints = Err("boom".into());
        let v = c.run();
        assert_eq!(codes(&v), ["no_audio"]);
        assert_eq!(texts(&v), ["Can't check the audio device"]);
        c.endpoints = ok_audio();
        c.audio.using_fallback = true;
        assert_eq!(texts(&c.run()), ["Alarm sound fell back to basic playback"]);
    }

    #[test]
    fn muted_only_matters_without_unmute_on_red() {
        let mut c = Case::new(healthy());
        c.endpoints = Ok(EndpointFacts {
            endpoints: vec![("spk".into(), Some(true)), ("hp".into(), Some(false))],
            default: Some("spk".into()),
        });
        c.unmute_on_red = true;
        assert_eq!(c.run()["armed"], true);
        c.unmute_on_red = false;
        let v = c.run();
        assert_eq!(codes(&v), ["muted"]);
        assert_eq!(texts(&v), ["Windows is muted"]);
        // Chosen device not muted -> fine; "all" -> any muted counts.
        c.alarm_device = "hp".into();
        assert_eq!(c.run()["armed"], true);
        c.alarm_device = "all".into();
        assert_eq!(codes(&c.run()), ["muted"]);
        // Unknown mute state fails towards "not armed".
        c.endpoints = Ok(EndpointFacts { endpoints: vec![("spk".into(), None)], default: Some("spk".into()) });
        c.alarm_device = "default".into();
        assert_eq!(texts(&c.run()), ["Can't check the audio device"]);
    }

    #[test]
    fn sleep_when_collecting_without_keep_awake() {
        let mut c = Case::new(healthy());
        c.held = false;
        let v = c.run();
        assert_eq!(codes(&v), ["sleep"]);
        assert_eq!(texts(&v), ["PC may sleep"]);
        c.collecting = false;
        assert_eq!(c.run()["armed"], true);
    }

    #[test]
    fn keep_awake_decision() {
        assert!(want_keep_awake(true, Some(true), false));
        assert!(!want_keep_awake(true, Some(false), true));
        assert!(!want_keep_awake(false, Some(true), true));
        // Unreachable server keeps the last known decision.
        assert!(want_keep_awake(true, None, true));
        assert!(!want_keep_awake(true, None, false));
        assert!(!want_keep_awake(false, None, true));
    }

    #[test]
    fn keep_awake_calls_only_on_change_and_retries_failures() {
        let mut ka = KeepAwake::default();
        let mut calls = Vec::new();
        assert!(ka.update(true, |w| { calls.push(w); true }));
        assert!(ka.update(true, |w| { calls.push(w); true }));
        assert!(!ka.update(false, |w| { calls.push(w); true }));
        assert_eq!(calls, [true, false]);
        // A failed hold is not held and is retried next poll.
        assert!(!ka.update(true, |_| false));
        let mut retried = false;
        assert!(ka.update(true, |_| { retried = true; true }));
        assert!(retried);
    }

    #[test]
    fn watchdog_waits_for_three_failures_and_a_dead_child() {
        let now = Instant::now();
        let h = VecDeque::new();
        assert_eq!(watchdog_decision(2, false, &h, now), Watch::Idle);
        assert_eq!(watchdog_decision(3, true, &h, now), Watch::Idle);
        assert_eq!(watchdog_decision(3, false, &h, now), Watch::Restart);
    }

    #[test]
    fn watchdog_spacing_cap_and_rolling_recovery() {
        let t0 = Instant::now();
        let mut h = VecDeque::from([t0]);
        assert_eq!(watchdog_decision(5, false, &h, t0 + Duration::from_secs(44)), Watch::Idle);
        assert_eq!(watchdog_decision(5, false, &h, t0 + RESTART_SPACING), Watch::Restart);
        h.push_back(t0 + Duration::from_secs(60));
        h.push_back(t0 + Duration::from_secs(120));
        let t = t0 + Duration::from_secs(300);
        assert_eq!(watchdog_decision(5, false, &h, t), Watch::Capped);
        // Server back: no Capped report.
        assert_eq!(watchdog_decision(0, false, &h, t), Watch::Idle);
        // Oldest restart leaves the window -> restarts possible again.
        let t = t0 + RESTART_WINDOW;
        assert_eq!(watchdog_decision(5, false, &h, t), Watch::Restart);
        prune_history(&mut h, t);
        assert_eq!(h.len(), 2);
    }
}
