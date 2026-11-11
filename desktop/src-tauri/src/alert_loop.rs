use std::collections::VecDeque;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::{AppHandle, Manager};

use crate::armed::{
    first_fault, pending_verdict, prune_history, set_execution_state, verdict, want_keep_awake, watchdog_decision,
    KeepAwake, VerdictInputs, Watch,
};
use crate::audio::{audio_status, endpoint_facts, start_alarm, stop_alarm};
use crate::http::http_json;
use crate::overlay::{hide_overlay, overlay_payload, pick_target, show_overlay, update_overlay_text};
use crate::tray::{armed_tooltip, set_tooltip};
#[cfg(test)]
use crate::tray::tooltip_for;
use crate::{log_line, Shell};

const ALERT_POLL: Duration = Duration::from_secs(2);
const FAILS_BEFORE_GIVING_UP: u32 = 3;
/// How long a server outage after an alert keeps warning in amber (matches the
/// sidecar's SIGNAL_LOST_SECONDS), so a dead server can't flash forever.
const SIGNAL_LOST_WINDOW: Duration = Duration::from_secs(600);

pub(crate) struct AppliedState {
    pub(crate) key: String,
    pub(crate) level: String,
    pub(crate) snoozed_until: Option<Instant>,
    /// When the server last answered; bounds the unreachable warning.
    pub(crate) last_seen: Option<Instant>,
}

/// Alert-thread state behind the armed verdict, keep-awake and watchdog.
#[derive(Default)]
struct ArmedTracker {
    /// Last good /api/alert-state.
    last_state: Option<Value>,
    last_collecting: bool,
    keep_awake: KeepAwake,
    restarts: VecDeque<Instant>,
    last_audio_error: Option<String>,
}

pub(crate) fn alert_loop(app: AppHandle) {
    let mut failures: u32 = 0;
    let mut tracker = ArmedTracker::default();
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
                tracker.last_state = Some(state);
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
        // After the alert has been applied, so these checks never delay it.
        // A panic here must not stop the alert loop; it reports "not armed".
        let checked = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| armed_tick(&app, &mut tracker, failures)));
        if checked.is_err() {
            log_line("armed check panicked");
            app.state::<Shell>().set_armed_verdict(serde_json::json!({
                "armed": false,
                "problems": [{ "code": "server", "text": "Checking alert status" }],
            }));
        }
        std::thread::sleep(ALERT_POLL);
    }
}

/// One poll's keep-awake, watchdog and armed-verdict work. Runs on the alert
/// thread (keep-awake belongs to the calling thread) and never blocks for
/// more than the bounded audio query.
fn armed_tick(app: &AppHandle, t: &mut ArmedTracker, failures: u32) {
    let settings = app.state::<crate::settings::DesktopSettings>().get();
    let reachable = failures < FAILS_BEFORE_GIVING_UP;
    let state = if reachable { t.last_state.as_ref() } else { None };
    let collecting_now = state.map(|s| s["collecting"].as_bool() == Some(true));

    // Keep-awake: held while collecting and the setting is on; an outage
    // keeps the last decision. Only calls Windows on a change.
    let want = want_keep_awake(settings.keep_awake, collecting_now, t.last_collecting);
    if let Some(c) = collecting_now {
        t.last_collecting = c;
    }
    let held = t.keep_awake.update(want, set_execution_state);

    // Watchdog: restart a dead (or never-ours) sidecar, spaced and capped.
    let now = Instant::now();
    prune_history(&mut t.restarts, now);
    let decision = watchdog_decision(failures, crate::sidecar_running(app), &t.restarts, now);
    if decision == Watch::Restart {
        t.restarts.push_back(now);
        log_line("watchdog: dashboard server unreachable; restarting sidecar");
        match crate::spawn_sidecar(app) {
            Ok(()) => crate::commands::native_toast(
                app,
                "Owlet Dashboard".into(),
                "The dashboard server stopped and was restarted.".into(),
            ),
            Err(err) => log_line(&format!("watchdog: sidecar restart failed: {err}")),
        }
    } else if decision == Watch::Capped && failures == FAILS_BEFORE_GIVING_UP {
        log_line("watchdog: restart cap reached");
    }

    let endpoints = endpoint_facts();
    let err = endpoints.as_ref().err().cloned();
    if err.is_some() && err != t.last_audio_error {
        log_line(&format!("armed: audio check failed: {}", err.as_deref().unwrap_or("")));
    }
    t.last_audio_error = err;

    let v = if reachable && t.last_state.is_none() {
        pending_verdict()
    } else {
        verdict(&VerdictInputs {
            state,
            failures,
            endpoints: &endpoints,
            audio: &audio_status(),
            alarm_device: &settings.alarm_device,
            unmute_on_red: settings.unmute_on_red,
            collecting: collecting_now.unwrap_or(t.last_collecting),
            keep_awake_held: held,
            watchdog_capped: decision == Watch::Capped,
        })
    };
    app.state::<Shell>().set_armed_verdict(v);
}

pub(crate) fn unreachable_state(applied: &AppliedState, now: Instant) -> Value {
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

pub(crate) fn apply_state(app: &AppHandle, state: &Value, generation: u64) {
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

pub(crate) fn apply_ui(app: &AppHandle, state: &Value, applied: &mut AppliedState) {
    let active = state["active_level"].as_str().unwrap_or("none").to_string();
    let level = state["level"].as_str().unwrap_or("none");
    let devices = state["devices"].as_array().cloned().unwrap_or_default();
    // Show the device driving the alert, else the first device.
    let dev = devices
        .iter()
        .find(|d| d["level"].as_str() == Some(level) && level != "none" && d["signal_lost"].as_bool() == Some(true))
        .or_else(|| devices.iter().find(|d| d["level"].as_str() == Some(level) && level != "none"))
        .or_else(|| devices.first());
    let fault = first_fault(&app.state::<Shell>().armed_verdict());
    set_tooltip(app, &armed_tooltip(state, dev, fault.as_deref()));
    // Tell the controller poller (short lock, never blocks on XInput).
    crate::controller::publish(&active);

    if active == "amber" || active == "red" {
        if active == "red" {
            start_alarm(|| app.state::<crate::settings::DesktopSettings>().get());
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
    use crate::overlay::Target;
    use std::sync::{mpsc, Arc};
    use tauri::{PhysicalPosition, PhysicalSize};

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
}
