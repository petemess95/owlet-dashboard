//! PlayStation pads (DualShock 4, DualSense, DualSense Edge) read directly as
//! HID devices, for controller snooze and button capture. They show up as
//! extra slots next to the four XInput slots, with their buttons translated
//! into the XInput `wButtons` layout, so a button chosen on one pad type means
//! the same physical position on the other.
//!
//! Report parsing and the slot bookkeeping (`HidSlots` over a `Backend`) are
//! pure and unit-tested; the Windows layer (SetupAPI enumeration, opening, a
//! reader thread per device) is not.
//!
//! Safety:
//! - Nothing here runs unless the controller poller or a capture asks: devices
//!   are opened on the first `HidSlots::read` and closed when it is dropped.
//! - The poller never blocks on a device: a reader thread per device does the
//!   (overlapped, 100 ms wait) reads and only publishes the latest buttons
//!   through atomics. Stopping is a flag; the poller never joins.
//! - A device that errors or disconnects reads as "not connected" (None) from
//!   then on and is dropped; a slot always reads None for at least one tick
//!   between two devices, so the snooze hold rule restarts for the new one.
//! - Enumeration or open failures are logged once and treated as "no pad".

use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

use crate::controller::RECHECK_MS;
use crate::log_line;

/// PlayStation pads read at once (on top of the four XInput slots).
pub(crate) const HID_SLOTS: usize = 4;

pub(crate) type HidSlotStates = [Option<u16>; HID_SLOTS];

pub(crate) const SONY_VID: u16 = 0x054C;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Model {
    DualShock4,
    DualSense,
}

/// Supported Sony pads: DualShock 4 (v1, v2), DualSense, DualSense Edge.
pub(crate) fn model_for(vid: u16, pid: u16) -> Option<Model> {
    if vid != SONY_VID {
        return None;
    }
    match pid {
        0x05C4 | 0x09CC => Some(Model::DualShock4),
        0x0CE6 | 0x0DF2 => Some(Model::DualSense),
        _ => None,
    }
}

// XInput wButtons bits.
const UP: u16 = 0x0001;
const DOWN: u16 = 0x0002;
const LEFT: u16 = 0x0004;
const RIGHT: u16 = 0x0008;
const START: u16 = 0x0010;
const BACK: u16 = 0x0020;
const LTHUMB: u16 = 0x0040;
const RTHUMB: u16 = 0x0080;
const LSHOULDER: u16 = 0x0100;
const RSHOULDER: u16 = 0x0200;
/// Not used by XInput: PlayStation pads only.
const PS: u16 = 0x0400;
const TOUCHPAD: u16 = 0x0800;
const A: u16 = 0x1000;
const B: u16 = 0x2000;
const X: u16 = 0x4000;
const Y: u16 = 0x8000;

/// Hat switch (low nibble of the first button byte): 0 = up, clockwise in
/// eighths to 7 = up-left; 8 and above = neutral.
pub(crate) fn hat_bits(hat: u8) -> u16 {
    match hat {
        0 => UP,
        1 => UP | RIGHT,
        2 => RIGHT,
        3 => DOWN | RIGHT,
        4 => DOWN,
        5 => DOWN | LEFT,
        6 => LEFT,
        7 => UP | LEFT,
        _ => 0,
    }
}

/// The three button bytes shared by DualShock 4 and DualSense (Linux
/// hid-playstation, DS_BUTTONS0/1/2 and DS4 equivalents):
/// b0: hat (bits 0-3), Square 4, Cross 5, Circle 6, Triangle 7;
/// b1: L1 0, R1 1, L2 2, R2 3, Share/Create 4, Options 5, L3 6, R3 7;
/// b2: PS 0, touchpad click 1 (counter / mute / Edge extras above: ignored).
/// L2/R2 (digital or analog) and the sticks are not buttons here.
pub(crate) fn map_buttons(b0: u8, b1: u8, b2: u8) -> u16 {
    let mut m = hat_bits(b0 & 0x0F);
    let bit = |byte: u8, n: u8, out: u16| if byte & (1 << n) != 0 { out } else { 0 };
    m |= bit(b0, 4, X) | bit(b0, 5, A) | bit(b0, 6, B) | bit(b0, 7, Y);
    m |= bit(b1, 0, LSHOULDER) | bit(b1, 1, RSHOULDER);
    m |= bit(b1, 4, BACK) | bit(b1, 5, START) | bit(b1, 6, LTHUMB) | bit(b1, 7, RTHUMB);
    m |= bit(b2, 0, PS) | bit(b2, 1, TOUCHPAD);
    m
}

/// Buttons (XInput layout) from one input report, report ID first. None for
/// a report this doesn't understand (other IDs, too short): the caller keeps
/// the previous state.
///
/// Byte offsets of the first button byte, report ID at 0:
/// - DualShock 4 USB 0x01 (and the Bluetooth "minimal" 0x01): ID, LX, LY,
///   RX, RY, buttons -> 5. Bluetooth 0x11 has 2 extra bytes after the ID -> 7.
/// - DualSense USB 0x01: ID, LX, LY, RX, RY, L2, R2, seq, buttons -> 8.
///   Bluetooth 0x31 has 1 extra byte after the ID -> 9. Over Bluetooth before
///   the full mode is enabled it sends a "simple" 0x01 report laid out like
///   the DualShock 4 one -> 5; `bluetooth` tells the two 0x01 forms apart.
pub(crate) fn parse_report(model: Model, bluetooth: bool, report: &[u8]) -> Option<u16> {
    let off = match (model, *report.first()?, bluetooth) {
        (Model::DualShock4, 0x01, _) => 5,
        (Model::DualShock4, 0x11, _) => 7,
        (Model::DualSense, 0x01, false) => 8,
        (Model::DualSense, 0x01, true) => 5,
        (Model::DualSense, 0x31, _) => 9,
        _ => return None,
    };
    let b = report.get(off..off + 3)?;
    Some(map_buttons(b[0], b[1], b[2]))
}

// ---------------------------------------------------------------------------
// Slot bookkeeping
// ---------------------------------------------------------------------------

const NO_STATE: u32 = u32::MAX;

/// Shared between a device's reader thread and the poller.
pub(crate) struct Shared {
    stop: AtomicBool,
    dead: AtomicBool,
    /// Latest buttons, or NO_STATE before the first understood report.
    state: AtomicU32,
}

impl Shared {
    pub(crate) fn new() -> Arc<Shared> {
        Arc::new(Shared { stop: AtomicBool::new(false), dead: AtomicBool::new(false), state: AtomicU32::new(NO_STATE) })
    }
    pub(crate) fn set_buttons(&self, b: u16) {
        self.state.store(u32::from(b), Ordering::SeqCst);
    }
    pub(crate) fn set_dead(&self) {
        self.dead.store(true, Ordering::SeqCst);
    }
    pub(crate) fn stopping(&self) -> bool {
        self.stop.load(Ordering::SeqCst)
    }
    fn buttons(&self) -> Option<u16> {
        match self.state.load(Ordering::SeqCst) {
            NO_STATE => None,
            v => Some(v as u16),
        }
    }
}

pub(crate) struct Opened {
    pub(crate) path: String,
    pub(crate) shared: Arc<Shared>,
}

/// Finds and opens pads. `scan` must not return a path in `skip`, and at
/// most `max` devices; each returned device already has its reader running.
pub(crate) trait Backend {
    fn scan(&mut self, skip: &[&str], max: usize) -> Vec<Opened>;
}

/// The HID half of the controller view. Created when reading starts (alert
/// or capture), dropped when it stops: dropping stops every reader thread,
/// which closes its handle.
pub(crate) struct HidSlots<Bk: Backend = OsBackend> {
    backend: Bk,
    slots: [Option<Opened>; HID_SLOTS],
    next_scan_ms: u64,
}

impl HidSlots<OsBackend> {
    pub(crate) fn new() -> Self {
        HidSlots::with_backend(OsBackend)
    }
}

impl<Bk: Backend> HidSlots<Bk> {
    pub(crate) fn with_backend(backend: Bk) -> Self {
        HidSlots { backend, slots: Default::default(), next_scan_ms: 0 }
    }

    /// Current buttons per HID slot. A dead device reads None and is
    /// dropped; new devices are looked for at most once per `RECHECK_MS`
    /// and read None in the tick they were added.
    pub(crate) fn read(&mut self, now_ms: u64) -> HidSlotStates {
        let mut out = [None; HID_SLOTS];
        for (o, slot) in out.iter_mut().zip(self.slots.iter_mut()) {
            let Some(dev) = slot else { continue };
            if dev.shared.dead.load(Ordering::SeqCst) {
                log_once(&format!("controller: PlayStation pad stopped responding, dropped: {}", dev.path));
                dev.shared.stop.store(true, Ordering::SeqCst);
                *slot = None;
                continue;
            }
            *o = dev.shared.buttons();
        }
        let free = self.slots.iter().filter(|s| s.is_none()).count();
        if free > 0 && now_ms >= self.next_scan_ms {
            self.next_scan_ms = now_ms + RECHECK_MS;
            let open: Vec<&str> = self.slots.iter().flatten().map(|d| d.path.as_str()).collect();
            let mut found = self.backend.scan(&open, free).into_iter();
            for slot in self.slots.iter_mut().filter(|s| s.is_none()) {
                match found.next() {
                    Some(dev) => *slot = Some(dev),
                    None => break,
                }
            }
            // A backend that returned more than asked: don't leak readers.
            for extra in found {
                extra.shared.stop.store(true, Ordering::SeqCst);
            }
        }
        out
    }

    /// Every open device has sent at least one understood report.
    pub(crate) fn all_reported(&self) -> bool {
        self.slots.iter().flatten().all(|d| d.shared.buttons().is_some() || d.shared.dead.load(Ordering::SeqCst))
    }
}

impl<Bk: Backend> Drop for HidSlots<Bk> {
    fn drop(&mut self) {
        for dev in self.slots.iter().flatten() {
            dev.shared.stop.store(true, Ordering::SeqCst);
        }
    }
}

static LOGGED: Mutex<Option<HashSet<String>>> = Mutex::new(None);

/// log_line, but each distinct message only once per run (a pad that can't
/// be opened is retried every second during an alert).
fn log_once(msg: &str) {
    let mut g = LOGGED.lock().unwrap_or_else(|p| p.into_inner());
    // Unit tests drive the dead-device path; keep them out of the app log.
    if g.get_or_insert_with(HashSet::new).insert(msg.to_string()) && !cfg!(test) {
        drop(g);
        log_line(msg);
    }
}

// ---------------------------------------------------------------------------
// OS layer
// ---------------------------------------------------------------------------

pub(crate) struct OsBackend;

#[cfg(not(windows))]
impl Backend for OsBackend {
    fn scan(&mut self, _skip: &[&str], _max: usize) -> Vec<Opened> {
        Vec::new()
    }
}

#[cfg(windows)]
impl Backend for OsBackend {
    fn scan(&mut self, skip: &[&str], max: usize) -> Vec<Opened> {
        let mut out = Vec::new();
        for path in os::hid_paths() {
            if out.len() >= max {
                break;
            }
            // Sony's VID appears in both the USB ("vid_054c") and Bluetooth
            // ("vid&0002054c") forms of the path; skip everything else
            // without opening it.
            if !path.to_ascii_lowercase().contains("054c") || skip.contains(&path.as_str()) {
                continue;
            }
            if let Some(dev) = os::open(&path) {
                out.push(dev);
            }
        }
        out
    }
}

#[cfg(windows)]
mod os {
    use super::{log_once, parse_report, Model, Opened, Shared};
    use std::sync::Arc;
    use windows::core::{HRESULT, PCWSTR};
    use windows::Win32::Devices::DeviceAndDriverInstallation::{
        SetupDiDestroyDeviceInfoList, SetupDiEnumDeviceInterfaces, SetupDiGetClassDevsW,
        SetupDiGetDeviceInterfaceDetailW, DIGCF_DEVICEINTERFACE, DIGCF_PRESENT, SP_DEVICE_INTERFACE_DATA,
        SP_DEVICE_INTERFACE_DETAIL_DATA_W,
    };
    use windows::Win32::Devices::HumanInterfaceDevice::{
        HidD_FreePreparsedData, HidD_GetAttributes, HidD_GetHidGuid, HidD_GetPreparsedData, HidP_GetCaps,
        HIDD_ATTRIBUTES, HIDP_CAPS, HIDP_STATUS_SUCCESS, PHIDP_PREPARSED_DATA,
    };
    use windows::Win32::Foundation::{
        CloseHandle, ERROR_IO_PENDING, GENERIC_READ, GENERIC_WRITE, HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT,
    };
    use windows::Win32::Storage::FileSystem::{
        CreateFileW, ReadFile, FILE_FLAG_OVERLAPPED, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
    };
    use windows::Win32::System::Threading::{CreateEventW, ResetEvent, WaitForSingleObject};
    use windows::Win32::System::IO::{CancelIoEx, GetOverlappedResult, OVERLAPPED};

    /// Device paths of all present HID interfaces; empty (logged once) on error.
    pub(super) fn hid_paths() -> Vec<String> {
        let mut out = Vec::new();
        // SAFETY: plain SetupAPI calls on locals; the detail buffer is a
        // u32-aligned allocation of at least the size SetupAPI asked for, and
        // the info list is destroyed on every path.
        unsafe {
            let guid = HidD_GetHidGuid();
            let set = match SetupDiGetClassDevsW(Some(&guid), PCWSTR::null(), None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE) {
                Ok(s) => s,
                Err(err) => {
                    log_once(&format!("controller: HID enumeration failed: {err}"));
                    return out;
                }
            };
            for index in 0..1024u32 {
                let mut data = SP_DEVICE_INTERFACE_DATA {
                    cbSize: std::mem::size_of::<SP_DEVICE_INTERFACE_DATA>() as u32,
                    ..Default::default()
                };
                if SetupDiEnumDeviceInterfaces(set, None, &guid, index, &mut data).is_err() {
                    break;
                }
                let mut size = 0u32;
                let _ = SetupDiGetDeviceInterfaceDetailW(set, &data, None, 0, Some(&mut size), None);
                let header = std::mem::size_of::<SP_DEVICE_INTERFACE_DETAIL_DATA_W>();
                if (size as usize) < header || size > 64 * 1024 {
                    continue;
                }
                let mut buf = vec![0u32; (size as usize).div_ceil(4)];
                let detail = buf.as_mut_ptr() as *mut SP_DEVICE_INTERFACE_DETAIL_DATA_W;
                (*detail).cbSize = header as u32;
                if SetupDiGetDeviceInterfaceDetailW(set, &data, Some(detail), size, None, None).is_err() {
                    continue;
                }
                let start = std::ptr::addr_of!((*detail).DevicePath) as *const u16;
                let max_chars = (size as usize - (start as usize - detail as usize)) / 2;
                let chars = std::slice::from_raw_parts(start, max_chars);
                let len = chars.iter().position(|&c| c == 0).unwrap_or(max_chars);
                out.push(String::from_utf16_lossy(&chars[..len]));
            }
            let _ = SetupDiDestroyDeviceInfoList(set);
        }
        out
    }

    struct OwnedHandle(HANDLE);
    impl Drop for OwnedHandle {
        fn drop(&mut self) {
            // SAFETY: we own this handle and close it once.
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }
    // SAFETY: a kernel handle may be used and closed from any thread.
    unsafe impl Send for OwnedHandle {}

    pub(super) struct Info {
        pub(super) vid: u16,
        pub(super) pid: u16,
        pub(super) usage_page: u16,
        pub(super) usage: u16,
        pub(super) input_len: usize,
    }

    fn open_handle(path: &str) -> windows::core::Result<OwnedHandle> {
        let wide: Vec<u16> = path.encode_utf16().chain(std::iter::once(0)).collect();
        let share = FILE_SHARE_READ | FILE_SHARE_WRITE;
        // SAFETY: NUL-terminated path that outlives the calls.
        unsafe {
            CreateFileW(PCWSTR(wide.as_ptr()), GENERIC_READ.0 | GENERIC_WRITE.0, share, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
                .or_else(|_| CreateFileW(PCWSTR(wide.as_ptr()), GENERIC_READ.0, share, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None))
                .map(OwnedHandle)
        }
    }

    fn info(h: HANDLE) -> Option<Info> {
        // SAFETY: valid handle and out pointers to locals; preparsed data is
        // freed after use.
        unsafe {
            let mut attrs = HIDD_ATTRIBUTES { Size: std::mem::size_of::<HIDD_ATTRIBUTES>() as u32, ..Default::default() };
            if !HidD_GetAttributes(h, &mut attrs) {
                return None;
            }
            let mut pp = PHIDP_PREPARSED_DATA::default();
            if !HidD_GetPreparsedData(h, &mut pp) {
                return None;
            }
            let mut caps = HIDP_CAPS::default();
            let ok = HidP_GetCaps(pp, &mut caps) == HIDP_STATUS_SUCCESS;
            let _ = HidD_FreePreparsedData(pp);
            ok.then_some(Info {
                vid: attrs.VendorID,
                pid: attrs.ProductID,
                usage_page: caps.UsagePage,
                usage: caps.Usage,
                input_len: caps.InputReportByteLength as usize,
            })
        }
    }

    /// Opens a supported pad and starts its reader. None (logged once) if it
    /// can't be opened (held exclusively, hidden by HidHide, gone) or isn't a
    /// supported gamepad collection.
    pub(super) fn open(path: &str) -> Option<Opened> {
        let handle = match open_handle(path) {
            Ok(h) => h,
            Err(err) => {
                log_once(&format!("controller: could not open {path}: {err}"));
                return None;
            }
        };
        let info = info(handle.0)?;
        let model = super::model_for(info.vid, info.pid)?;
        // Generic Desktop / Joystick or Game Pad only.
        if info.usage_page != 0x01 || !(info.usage == 0x04 || info.usage == 0x05) || info.input_len < 10 {
            return None;
        }
        // USB pads have 64-byte input reports; over Bluetooth they are longer
        // (DualSense 78, DualShock 4 more), which tells the two DualSense 0x01
        // forms apart.
        let bluetooth = info.input_len > 64;
        let shared = Shared::new();
        let thread_shared = shared.clone();
        let len = info.input_len;
        let spawned = std::thread::Builder::new()
            .name("owlet-hid".into())
            .spawn(move || reader(handle, len, model, bluetooth, &thread_shared));
        if let Err(err) = spawned {
            log_once(&format!("controller: HID reader not started: {err}"));
            return None;
        }
        log_once(&format!(
            "controller: reading PlayStation pad {:04X}:{:04X} ({}) {path}",
            info.vid,
            info.pid,
            if bluetooth { "Bluetooth" } else { "USB" }
        ));
        Some(Opened { path: path.to_string(), shared })
    }

    /// Debug helper: what `open` would decide, without starting a reader.
    #[cfg(test)]
    pub(super) fn probe(path: &str) -> Result<Info, String> {
        let h = open_handle(path).map_err(|e| e.to_string())?;
        info(h.0).ok_or_else(|| "no HID attributes/caps".to_string())
    }

    /// Reads input reports until stopped or the device errors. All waits are
    /// at most 100 ms and happen only on this thread.
    fn reader(handle: OwnedHandle, len: usize, model: Model, bluetooth: bool, shared: &Arc<Shared>) {
        let run = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| read_loop(&handle, len, model, bluetooth, shared)));
        if run.is_err() {
            log_once("controller: HID reader panicked");
        }
        // Whatever happened, this device no longer reports.
        shared.set_dead();
    }

    fn read_loop(handle: &OwnedHandle, len: usize, model: Model, bluetooth: bool, shared: &Shared) {
        let h = handle.0;
        // SAFETY: event handle owned here and closed below.
        let event = match unsafe { CreateEventW(None, true, false, PCWSTR::null()) } {
            Ok(e) => OwnedHandle(e),
            Err(err) => {
                log_once(&format!("controller: HID event not created: {err}"));
                return;
            }
        };
        let mut buf = vec![0u8; len];
        let mut ov = OVERLAPPED { hEvent: event.0, ..Default::default() };
        let pending_code = HRESULT::from_win32(ERROR_IO_PENDING.0);
        while !shared.stopping() {
            // SAFETY: `buf` and `ov` live on this thread until the read has
            // completed or been cancelled and waited for (below); they are not
            // touched while it is in flight.
            unsafe {
                let _ = ResetEvent(event.0);
                if let Err(err) = ReadFile(h, Some(buf.as_mut_slice()), None, Some(&mut ov)) {
                    if err.code() != pending_code {
                        return;
                    }
                }
                loop {
                    if shared.stopping() {
                        cancel(h, &ov);
                        return;
                    }
                    let w = WaitForSingleObject(event.0, 100);
                    if w == WAIT_TIMEOUT {
                        continue;
                    }
                    if w != WAIT_OBJECT_0 {
                        cancel(h, &ov);
                        return;
                    }
                    let mut n = 0u32;
                    if GetOverlappedResult(h, &ov, &mut n, false).is_err() {
                        return;
                    }
                    if let Some(b) = parse_report(model, bluetooth, &buf[..(n as usize).min(buf.len())]) {
                        shared.set_buttons(b);
                    }
                    break;
                }
            }
        }
    }

    /// Cancel the in-flight read on `h` (issued with `ov`) and wait for it,
    /// so its buffer can be freed. Only ever blocks this reader thread.
    fn cancel(h: HANDLE, ov: &OVERLAPPED) {
        let mut n = 0u32;
        // SAFETY: `ov` belongs to a read issued on `h` by this thread.
        unsafe {
            let _ = CancelIoEx(h, Some(ov));
            let _ = GetOverlappedResult(h, ov, &mut n, true);
        }
    }

    #[cfg(test)]
    mod debug {
        /// Lists Sony HID interfaces on this PC, which of them open, and the
        /// buttons each reports for a second.
        /// `cargo test --offline debug_enumerate_sony_hid -- --ignored --nocapture`
        #[test]
        #[ignore]
        fn debug_enumerate_sony_hid() {
            let paths = super::hid_paths();
            println!("{} HID interfaces present", paths.len());
            for p in paths.iter().filter(|p| p.to_ascii_lowercase().contains("054c")) {
                match super::probe(p) {
                    Ok(i) => println!(
                        "OPENED {:04X}:{:04X} usage {:02X}/{:02X} input_len {} supported {:?}  {p}",
                        i.vid,
                        i.pid,
                        i.usage_page,
                        i.usage,
                        i.input_len,
                        super::super::model_for(i.vid, i.pid)
                    ),
                    Err(e) => println!("FAILED {e}  {p}"),
                }
            }
            let mut slots = super::super::HidSlots::new();
            let _ = slots.read(0);
            std::thread::sleep(std::time::Duration::from_millis(1000));
            println!("after 1 s: all_reported {} states {:?}", slots.all_reported(), slots.read(1000));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ds4_usb(b0: u8, b1: u8, b2: u8) -> Vec<u8> {
        let mut r = vec![0u8; 64];
        r[0] = 0x01;
        r[1..5].copy_from_slice(&[0x80, 0x80, 0x80, 0x80]);
        r[5] = b0;
        r[6] = b1;
        r[7] = b2;
        r[8] = 0xFF; // L2 analog, fully pressed: not a button
        r[9] = 0xFF;
        r
    }

    fn ds4_bt(b0: u8, b1: u8, b2: u8) -> Vec<u8> {
        let mut r = vec![0u8; 78];
        r[0] = 0x11;
        r[1] = 0xC0;
        r[2] = 0x00;
        r[3..7].copy_from_slice(&[0x80, 0x80, 0x80, 0x80]);
        r[7] = b0;
        r[8] = b1;
        r[9] = b2;
        r
    }

    fn ds_usb(b0: u8, b1: u8, b2: u8) -> Vec<u8> {
        let mut r = vec![0u8; 64];
        r[0] = 0x01;
        r[1..5].copy_from_slice(&[0x80, 0x80, 0x80, 0x80]);
        r[5] = 0xFF; // L2 analog
        r[6] = 0xFF; // R2 analog
        r[7] = 0x5A; // sequence number
        r[8] = b0;
        r[9] = b1;
        r[10] = b2;
        r
    }

    fn ds_bt(b0: u8, b1: u8, b2: u8) -> Vec<u8> {
        let mut r = vec![0u8; 78];
        r[0] = 0x31;
        r[1] = 0x10; // sequence / tag byte
        r[2..6].copy_from_slice(&[0x80, 0x80, 0x80, 0x80]);
        r[6] = 0xFF;
        r[7] = 0xFF;
        r[8] = 0x33;
        r[9] = b0;
        r[10] = b1;
        r[11] = b2;
        r
    }

    const NEUTRAL: u8 = 0x08;

    #[test]
    fn models() {
        assert_eq!(model_for(0x054C, 0x05C4), Some(Model::DualShock4));
        assert_eq!(model_for(0x054C, 0x09CC), Some(Model::DualShock4));
        assert_eq!(model_for(0x054C, 0x0CE6), Some(Model::DualSense));
        assert_eq!(model_for(0x054C, 0x0DF2), Some(Model::DualSense));
        assert_eq!(model_for(0x054C, 0x0268), None);
        assert_eq!(model_for(0x045E, 0x05C4), None);
    }

    #[test]
    fn hat_directions_diagonals_and_neutral() {
        assert_eq!(hat_bits(0), UP);
        assert_eq!(hat_bits(1), UP | RIGHT);
        assert_eq!(hat_bits(2), RIGHT);
        assert_eq!(hat_bits(3), DOWN | RIGHT);
        assert_eq!(hat_bits(4), DOWN);
        assert_eq!(hat_bits(5), DOWN | LEFT);
        assert_eq!(hat_bits(6), LEFT);
        assert_eq!(hat_bits(7), UP | LEFT);
        for n in 8..=15 {
            assert_eq!(hat_bits(n), 0, "hat {n} is neutral");
        }
    }

    #[test]
    fn every_button_maps_to_its_xinput_position() {
        let cases: [(u8, u8, u8, u16); 14] = [
            (NEUTRAL | 0x10, 0, 0, X),        // Square
            (NEUTRAL | 0x20, 0, 0, A),        // Cross
            (NEUTRAL | 0x40, 0, 0, B),        // Circle
            (NEUTRAL | 0x80, 0, 0, Y),        // Triangle
            (NEUTRAL, 0x01, 0, LSHOULDER),    // L1
            (NEUTRAL, 0x02, 0, RSHOULDER),    // R1
            (NEUTRAL, 0x10, 0, BACK),         // Share / Create
            (NEUTRAL, 0x20, 0, START),        // Options
            (NEUTRAL, 0x40, 0, LTHUMB),       // L3
            (NEUTRAL, 0x80, 0, RTHUMB),       // R3
            (NEUTRAL, 0, 0x01, PS),           // PS
            (NEUTRAL, 0, 0x02, TOUCHPAD),     // touchpad click
            (NEUTRAL, 0x0C, 0, 0),            // L2 + R2 digital: ignored
            (NEUTRAL, 0, 0xFC, 0),            // counter / mute / Edge extras: ignored
        ];
        for (b0, b1, b2, want) in cases {
            assert_eq!(map_buttons(b0, b1, b2), want, "{b0:02X} {b1:02X} {b2:02X}");
            for model in [Model::DualShock4, Model::DualSense] {
                for bt in [false, true] {
                    let (usb_r, bt_r) = match model {
                        Model::DualShock4 => (ds4_usb(b0, b1, b2), ds4_bt(b0, b1, b2)),
                        Model::DualSense => (ds_usb(b0, b1, b2), ds_bt(b0, b1, b2)),
                    };
                    let r = if bt { bt_r } else { usb_r };
                    assert_eq!(parse_report(model, bt, &r), Some(want), "{model:?} bt {bt} {b0:02X} {b1:02X} {b2:02X}");
                }
            }
        }
    }

    #[test]
    fn ds4_usb_report() {
        assert_eq!(parse_report(Model::DualShock4, false, &ds4_usb(NEUTRAL, 0, 0)), Some(0));
        // Cross + up-right + L1 + Options; the frame counter in byte 7 is ignored.
        assert_eq!(parse_report(Model::DualShock4, false, &ds4_usb(0x21, 0x21, 0xA8)), Some(A | UP | RIGHT | LSHOULDER | START));
        // Bluetooth "minimal" 0x01 report (10 bytes) has the same layout.
        let r = &ds4_usb(0x06, 0x80, 0x01)[..10];
        assert_eq!(parse_report(Model::DualShock4, true, r), Some(LEFT | RTHUMB | PS));
    }

    #[test]
    fn ds4_bt_report_is_shifted_by_two() {
        assert_eq!(parse_report(Model::DualShock4, true, &ds4_bt(NEUTRAL, 0, 0)), Some(0));
        assert_eq!(parse_report(Model::DualShock4, true, &ds4_bt(0x45, 0x02, 0x02)), Some(B | DOWN | LEFT | RSHOULDER | TOUCHPAD));
        // The USB offsets on a BT report would read stick bytes as buttons.
        assert_ne!(parse_report(Model::DualShock4, true, &ds4_bt(0x45, 0x02, 0x02)), Some(map_buttons(0x80, 0x80, 0x45)));
    }

    #[test]
    fn dualsense_usb_report() {
        assert_eq!(parse_report(Model::DualSense, false, &ds_usb(NEUTRAL, 0, 0)), Some(0));
        // Triggers fully pressed and a sequence number set: still no buttons.
        assert_eq!(parse_report(Model::DualSense, false, &ds_usb(0x0F, 0, 0)), Some(0));
        assert_eq!(parse_report(Model::DualSense, false, &ds_usb(0x83, 0x60, 0x00)), Some(Y | DOWN | RIGHT | START | LTHUMB));
    }

    #[test]
    fn dualsense_bt_report_is_shifted_by_one() {
        assert_eq!(parse_report(Model::DualSense, true, &ds_bt(NEUTRAL, 0, 0)), Some(0));
        assert_eq!(parse_report(Model::DualSense, true, &ds_bt(0x17, 0x10, 0x01)), Some(X | UP | LEFT | BACK | PS));
    }

    #[test]
    fn dualsense_bt_simple_report_uses_the_ds4_layout() {
        let mut r = vec![0u8; 78];
        r[0] = 0x01;
        r[1..5].copy_from_slice(&[0x80; 4]);
        r[5] = 0x24; // Cross + down
        r[6] = 0x01; // L1
        r[7] = 0x00;
        r[8] = 0xFF; // L2 analog
        r[9] = 0xFF;
        assert_eq!(parse_report(Model::DualSense, true, &r), Some(A | DOWN | LSHOULDER));
    }

    #[test]
    fn unknown_or_short_reports_are_ignored() {
        assert_eq!(parse_report(Model::DualShock4, false, &[]), None);
        assert_eq!(parse_report(Model::DualShock4, false, &[0x01, 0x80, 0x80, 0x80, 0x80, 0x08]), None);
        assert_eq!(parse_report(Model::DualSense, true, &ds_bt(0, 0, 0)[..10]), None);
        let mut r = ds4_usb(0x20, 0, 0);
        r[0] = 0x05;
        assert_eq!(parse_report(Model::DualShock4, false, &r), None);
        let mut r = ds_usb(0x20, 0, 0);
        r[0] = 0x11;
        assert_eq!(parse_report(Model::DualSense, false, &r), None);
    }

    /// Fake pads: `pending` are handed out on scan, in order.
    #[derive(Default)]
    pub(crate) struct FakeBackend {
        pub(crate) pending: Vec<(String, Arc<Shared>)>,
        pub(crate) scans: Vec<u64>,
        pub(crate) now: u64,
    }

    impl Backend for FakeBackend {
        fn scan(&mut self, skip: &[&str], max: usize) -> Vec<Opened> {
            self.scans.push(self.now);
            let mut out = Vec::new();
            while out.len() < max && !self.pending.is_empty() {
                let (path, shared) = self.pending.remove(0);
                assert!(!skip.contains(&path.as_str()));
                out.push(Opened { path, shared });
            }
            out
        }
    }

    fn read(h: &mut HidSlots<FakeBackend>, now: u64) -> HidSlotStates {
        h.backend.now = now;
        h.read(now)
    }

    #[test]
    fn new_device_reads_none_until_next_tick_and_first_report() {
        let pad = Shared::new();
        let mut h = HidSlots::with_backend(FakeBackend { pending: vec![("p1".into(), pad.clone())], ..Default::default() });
        pad.set_buttons(A);
        assert_eq!(read(&mut h, 0), [None; HID_SLOTS], "added this tick");
        assert_eq!(read(&mut h, 50)[0], Some(A));
        let quiet = Shared::new();
        h.backend.pending.push(("p2".into(), quiet.clone()));
        assert_eq!(read(&mut h, 1000)[1], None);
        assert!(!h.all_reported());
        assert_eq!(read(&mut h, 1050)[1], None, "no report yet");
        quiet.set_buttons(0);
        assert!(h.all_reported());
        assert_eq!(read(&mut h, 1100), [Some(A), Some(0), None, None]);
    }

    #[test]
    fn scans_at_most_once_a_second_while_a_slot_is_free() {
        let mut h = HidSlots::with_backend(FakeBackend::default());
        for t in (0..3000).step_by(50) {
            read(&mut h, t);
        }
        assert_eq!(h.backend.scans, vec![0, 1000, 2000]);
        // All slots full: no scanning at all.
        let pads: Vec<_> = (0..HID_SLOTS).map(|_| Shared::new()).collect();
        let mut h = HidSlots::with_backend(FakeBackend {
            pending: pads.iter().enumerate().map(|(i, s)| (format!("p{i}"), s.clone())).collect(),
            ..Default::default()
        });
        for t in (0..3000).step_by(50) {
            read(&mut h, t);
        }
        assert_eq!(h.backend.scans, vec![0]);
    }

    #[test]
    fn dead_device_reads_none_is_dropped_and_its_slot_stays_none_for_a_tick() {
        let pad = Shared::new();
        let mut h = HidSlots::with_backend(FakeBackend { pending: vec![("p1".into(), pad.clone())], ..Default::default() });
        read(&mut h, 0);
        pad.set_buttons(A);
        assert_eq!(read(&mut h, 50)[0], Some(A));
        // A replacement is ready to be found in the very tick the old one dies.
        let next = Shared::new();
        next.set_buttons(A);
        h.backend.pending.push(("p2".into(), next.clone()));
        pad.set_dead();
        assert_eq!(read(&mut h, 1000)[0], None, "dead device never reports its last state");
        assert!(pad.stopping(), "its reader is told to stop");
        assert_eq!(read(&mut h, 1050)[0], Some(A), "replacement only from the next tick");
    }

    #[test]
    fn drop_stops_every_reader() {
        let a = Shared::new();
        let b = Shared::new();
        let mut h = HidSlots::with_backend(FakeBackend {
            pending: vec![("a".into(), a.clone()), ("b".into(), b.clone())],
            ..Default::default()
        });
        read(&mut h, 0);
        assert!(!a.stopping() && !b.stopping());
        drop(h);
        assert!(a.stopping() && b.stopping());
    }
}
