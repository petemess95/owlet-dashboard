//! Live tray icon: the current SpO2 number drawn into a 32x32 icon, coloured
//! by alert tier. The decision (`icon_choice`) mirrors what the tooltip shows
//! (same device, same state); anything that is not a trustworthy reading falls
//! back to the static app icon. The alert thread only queues the `set_icon`
//! call on the main thread and never waits for it.

use std::sync::atomic::Ordering;
use std::sync::Mutex;

use serde_json::Value;
use tauri::image::Image;
use tauri::{AppHandle, Manager};

use crate::tray::TRAY_ID;
use crate::{log_line, Shell};

pub(crate) const SIZE: usize = 32;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Tier {
    Normal,
    Amber,
    Red,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum IconChoice {
    Static,
    Number { value: u8, tier: Tier },
}

// Dark-theme --good / --warn / --bad from app/static/theme.css.
const GOOD: [u8; 3] = [0x9b, 0xe3, 0xc9];
const WARN: [u8; 3] = [0xff, 0xcf, 0x66];
const BAD: [u8; 3] = [0xff, 0x7b, 0x7b];
const NAVY: [u8; 3] = [0x12, 0x14, 0x42];
const CORNER_RADIUS: f32 = 6.0;

fn tier_color(tier: Tier) -> [u8; 3] {
    match tier {
        Tier::Normal => GOOD,
        Tier::Amber => WARN,
        Tier::Red => BAD,
    }
}

/// 3 columns x 5 rows per digit, one row per entry (bit 2 = left column).
const DIGITS: [[u8; 5]; 10] = [
    [0b111, 0b101, 0b101, 0b101, 0b111],
    [0b010, 0b110, 0b010, 0b010, 0b111],
    [0b111, 0b001, 0b111, 0b100, 0b111],
    [0b111, 0b001, 0b111, 0b001, 0b111],
    [0b101, 0b101, 0b111, 0b001, 0b001],
    [0b111, 0b100, 0b111, 0b001, 0b111],
    [0b111, 0b100, 0b111, 0b101, 0b111],
    [0b111, 0b001, 0b001, 0b010, 0b010],
    [0b111, 0b101, 0b111, 0b101, 0b111],
    [0b111, 0b101, 0b111, 0b001, 0b111],
];

fn inside_rounded_square(x: usize, y: usize) -> bool {
    let (px, py) = (x as f32 + 0.5, y as f32 + 0.5);
    let r = CORNER_RADIUS;
    let cx = px.clamp(r, SIZE as f32 - r);
    let cy = py.clamp(r, SIZE as f32 - r);
    (px - cx).powi(2) + (py - cy).powi(2) <= r * r
}

/// 32x32 RGBA: navy rounded square with the value in the tier colour. Values
/// 0-99 are one or two wide digits; 100 is three narrower digits ("100").
pub(crate) fn render_spo2_icon(value: u8, tier: Tier) -> Vec<u8> {
    let mut px = vec![0u8; SIZE * SIZE * 4];
    for y in 0..SIZE {
        for x in 0..SIZE {
            if inside_rounded_square(x, y) {
                let i = (y * SIZE + x) * 4;
                px[i..i + 3].copy_from_slice(&NAVY);
                px[i + 3] = 255;
            }
        }
    }
    let digits: Vec<usize> = value.min(100).to_string().bytes().map(|b| (b - b'0') as usize).collect();
    let cols = 4 * digits.len() - 1;
    // Even-sized cells at even offsets, so Windows' 2x downscale to 16x16 lands
    // every stroke and gap on whole pixels (1-px strokes and holes survive).
    let sx = (28 / cols) & !1; // 1 digit: 8, 2 digits: 4, 3 digits: 2
    let sy = 4;
    let left = ((SIZE - cols * sx) / 2) & !1;
    let top = ((SIZE - 5 * sy) / 2) & !1;
    let color = tier_color(tier);
    for (k, d) in digits.iter().enumerate() {
        for (row, bits) in DIGITS[*d].iter().enumerate() {
            for col in 0..3 {
                if (bits >> (2 - col)) & 1 == 0 {
                    continue;
                }
                let x0 = left + (k * 4 + col) * sx;
                let y0 = top + row * sy;
                for y in y0..y0 + sy {
                    for x in x0..x0 + sx {
                        let i = (y * SIZE + x) * 4;
                        px[i..i + 3].copy_from_slice(&color);
                    }
                }
            }
        }
    }
    px
}

/// What the tray icon should show for this poll. `state` and `dev` are the
/// ones `apply_ui` passes to the tooltip.
pub(crate) fn icon_choice(state: &Value, dev: Option<&Value>) -> IconChoice {
    // Server unreachable or other server message, signal lost, not collecting.
    if state["message"].as_str().is_some()
        || state["signal_lost"].as_bool() == Some(true)
        || state["collecting"].as_bool() == Some(false)
    {
        return IconChoice::Static;
    }
    let Some(d) = dev else { return IconChoice::Static };
    if d["stale"].as_bool() == Some(true)
        || d["signal_lost"].as_bool() == Some(true)
        || d["sock_reporting"].as_bool() == Some(false)
        || d["charging"].as_bool() == Some(true)
    {
        return IconChoice::Static;
    }
    let Some(v) = d["spo2"].as_i64() else { return IconChoice::Static };
    if !(1..=100).contains(&v) {
        return IconChoice::Static;
    }
    // The reading's own level, so snoozed and acknowledged alerts keep colour.
    let tier = match d["level"].as_str() {
        Some("red") => Tier::Red,
        Some("amber") => Tier::Amber,
        _ => Tier::Normal,
    };
    IconChoice::Number { value: v as u8, tier }
}

/// What the tray is believed to show (startup: the static icon). `None`
/// means unknown, so the next poll always applies.
static CURRENT: Mutex<Option<IconChoice>> = Mutex::new(Some(IconChoice::Static));

fn current() -> std::sync::MutexGuard<'static, Option<IconChoice>> {
    CURRENT.lock().unwrap_or_else(|e| e.into_inner())
}

/// `Some(next)` only when it differs from what is showing.
fn changed(current: Option<IconChoice>, next: IconChoice) -> Option<IconChoice> {
    (current != Some(next)).then_some(next)
}

/// Called by the alert thread last in `apply_ui`. Queues the icon change on
/// the main thread; never waits for it.
pub(crate) fn update(app: &AppHandle, state: &Value, dev: Option<&Value>) {
    if !app.state::<Shell>().tray_available.load(Ordering::SeqCst) {
        return;
    }
    let next = {
        let mut cur = current();
        let Some(next) = changed(*cur, icon_choice(state, dev)) else { return };
        *cur = Some(next);
        next
    };
    let handle = app.clone();
    let queued = app.run_on_main_thread(move || {
        let Some(tray) = handle.tray_by_id(TRAY_ID) else { return };
        let image = match next {
            IconChoice::Static => handle.default_window_icon().cloned(),
            IconChoice::Number { value, tier } => {
                Some(Image::new_owned(render_spo2_icon(value, tier), SIZE as u32, SIZE as u32))
            }
        };
        if let Err(err) = tray.set_icon(image) {
            log_line(&format!("tray icon update failed: {err}"));
            *current() = None; // retry on the next poll
        }
    });
    if queued.is_err() {
        *current() = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn px(img: &[u8], x: usize, y: usize) -> [u8; 4] {
        let i = (y * SIZE + x) * 4;
        [img[i], img[i + 1], img[i + 2], img[i + 3]]
    }

    fn lit(img: &[u8], color: [u8; 3]) -> Vec<bool> {
        (0..SIZE * SIZE).map(|i| img[i * 4..i * 4 + 3] == color && img[i * 4 + 3] == 255).collect()
    }

    #[test]
    fn output_is_32x32_rgba_with_navy_background() {
        let img = render_spo2_icon(97, Tier::Normal);
        assert_eq!(img.len(), 32 * 32 * 4);
        assert_eq!(px(&img, 16, 1), [0x12, 0x14, 0x42, 255]);
        assert_eq!(px(&img, 1, 16), [0x12, 0x14, 0x42, 255]);
        assert_eq!(px(&img, 30, 22), [0x12, 0x14, 0x42, 255]);
        // Rounded: the extreme corners are transparent.
        assert_eq!(px(&img, 0, 0)[3], 0);
        assert_eq!(px(&img, 31, 31)[3], 0);
    }

    #[test]
    fn every_digit_has_a_distinct_pattern() {
        let imgs: Vec<_> = (0..10u8).map(|d| lit(&render_spo2_icon(d, Tier::Normal), GOOD)).collect();
        for a in 0..10 {
            assert!(imgs[a].iter().any(|b| *b), "digit {a} draws nothing");
            for b in a + 1..10 {
                assert_ne!(imgs[a], imgs[b], "digits {a} and {b} look the same");
            }
        }
        assert_ne!(render_spo2_icon(97, Tier::Normal), render_spo2_icon(79, Tier::Normal));
        assert_ne!(render_spo2_icon(91, Tier::Normal), render_spo2_icon(81, Tier::Normal));
    }

    #[test]
    fn colour_follows_the_tier() {
        for (tier, color) in [(Tier::Normal, GOOD), (Tier::Amber, WARN), (Tier::Red, BAD)] {
            let img = render_spo2_icon(88, tier);
            assert!(lit(&img, color).iter().any(|b| *b));
            for other in [GOOD, WARN, BAD].into_iter().filter(|c| *c != color) {
                assert!(!lit(&img, other).iter().any(|b| *b));
            }
        }
    }

    #[test]
    fn one_hundred_shows_three_digits_inside_the_square() {
        let img = render_spo2_icon(100, Tier::Normal);
        assert_eq!(img.len(), 32 * 32 * 4);
        let on = lit(&img, GOOD);
        assert!(on.iter().any(|b| *b));
        assert_ne!(on, lit(&render_spo2_icon(10, Tier::Normal), GOOD));
        for i in 0..SIZE {
            for edge in [0usize, 1, 30, 31] {
                assert!(!on[i * SIZE + edge] && !on[edge * SIZE + i]);
            }
        }
        // Out-of-range input is clamped rather than panicking.
        assert_eq!(render_spo2_icon(255, Tier::Red), render_spo2_icon(100, Tier::Red));
    }

    fn dev(level: &str, spo2: i64) -> Value {
        json!({ "level": level, "spo2": spo2, "stale": false, "signal_lost": false,
                "sock_reporting": true, "charging": false })
    }
    fn st() -> Value {
        json!({ "level": "none", "active_level": "none", "collecting": true, "snoozed": false })
    }

    #[test]
    fn tiers_and_values() {
        let n = |value, tier| IconChoice::Number { value, tier };
        assert_eq!(icon_choice(&st(), Some(&dev("none", 97))), n(97, Tier::Normal));
        assert_eq!(icon_choice(&st(), Some(&dev("amber", 91))), n(91, Tier::Amber));
        assert_eq!(icon_choice(&st(), Some(&dev("red", 84))), n(84, Tier::Red));
        assert_eq!(icon_choice(&st(), Some(&dev("none", 100))), n(100, Tier::Normal));
    }

    #[test]
    fn snoozed_and_acknowledged_keep_the_reading_tier() {
        let n = |value, tier| IconChoice::Number { value, tier };
        let snoozed = json!({ "level": "red", "active_level": "none", "collecting": true, "snoozed": true });
        assert_eq!(icon_choice(&snoozed, Some(&dev("red", 84))), n(84, Tier::Red));
        let acked = json!({ "level": "amber", "active_level": "none", "collecting": true,
                            "acknowledged": true, "snoozed": false });
        assert_eq!(icon_choice(&acked, Some(&dev("amber", 89))), n(89, Tier::Amber));
    }

    #[test]
    fn every_fallback_is_static() {
        let ok = dev("none", 97);
        assert_eq!(icon_choice(&st(), None), IconChoice::Static);
        assert_eq!(icon_choice(&st(), Some(&json!({ "level": "none" }))), IconChoice::Static);
        for (k, v) in [("stale", true), ("signal_lost", true), ("sock_reporting", false), ("charging", true)] {
            let mut d = ok.clone();
            d[k] = json!(v);
            assert_eq!(icon_choice(&st(), Some(&d)), IconChoice::Static, "{k}");
        }
        let mut s = st();
        s["collecting"] = json!(false);
        assert_eq!(icon_choice(&s, Some(&ok)), IconChoice::Static);
        let mut s = st();
        s["signal_lost"] = json!(true);
        assert_eq!(icon_choice(&s, Some(&ok)), IconChoice::Static);
        // The unreachable-server state.
        let down = json!({ "level": "none", "active_level": "none", "signal_lost": false,
                           "message": "Dashboard server not responding", "devices": [] });
        assert_eq!(icon_choice(&down, Some(&ok)), IconChoice::Static);
        assert_eq!(icon_choice(&down, None), IconChoice::Static);
        // Implausible values are not trusted.
        assert_eq!(icon_choice(&st(), Some(&dev("none", 0))), IconChoice::Static);
        assert_eq!(icon_choice(&st(), Some(&dev("none", 101))), IconChoice::Static);
    }

    #[test]
    fn test_device_shows_while_the_tooltip_would() {
        // The server only lists the TEST device while a test alert is active.
        let test = json!({ "device_serial": "TEST", "level": "red", "spo2": 84, "stale": false,
                           "signal_lost": false, "sock_reporting": true, "charging": false });
        let s = json!({ "level": "red", "active_level": "red", "collecting": true });
        assert_eq!(icon_choice(&s, Some(&test)), IconChoice::Number { value: 84, tier: Tier::Red });
        assert!(crate::tray::tooltip_for(&s, Some(&test)).contains("84%"));
    }

    #[test]
    fn no_update_when_nothing_changed() {
        let a = IconChoice::Number { value: 97, tier: Tier::Normal };
        assert_eq!(changed(Some(a), a), None);
        assert_eq!(changed(Some(IconChoice::Static), IconChoice::Static), None);
        assert!(changed(Some(a), IconChoice::Number { value: 96, tier: Tier::Normal }).is_some());
        assert!(changed(Some(a), IconChoice::Number { value: 97, tier: Tier::Amber }).is_some());
        assert_eq!(changed(Some(a), IconChoice::Static), Some(IconChoice::Static));
        assert_eq!(changed(Some(IconChoice::Static), a), Some(a));
        // Unknown (failed update) always re-applies.
        assert_eq!(changed(None, IconChoice::Static), Some(IconChoice::Static));
    }

    /// Writes raw RGBA previews when OWLET_ICON_PREVIEW_DIR is set (hand review only).
    #[test]
    fn dump_previews_when_asked() {
        let Some(dir) = std::env::var_os("OWLET_ICON_PREVIEW_DIR") else { return };
        let dir = std::path::PathBuf::from(dir);
        std::fs::create_dir_all(&dir).unwrap();
        for (name, v, t) in [
            ("97_normal", 97, Tier::Normal),
            ("91_amber", 91, Tier::Amber),
            ("84_red", 84, Tier::Red),
            ("100_normal", 100, Tier::Normal),
        ] {
            std::fs::write(dir.join(format!("{name}.rgba")), render_spo2_icon(v, t)).unwrap();
        }
    }
}
