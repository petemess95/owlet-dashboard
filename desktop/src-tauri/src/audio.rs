//! Red-alert alarm: WASAPI playback on the chosen output(s), unmute and raise
//! on red, exact restore afterwards, PlaySoundW fallback.
//!
//! Discipline (from v0.1): `start_alarm` and `stop_alarm` take the same ALARM
//! lock, and the playing state plus the start/stop of playback happen inside
//! it, so a stop can never race a start. Playback runs on its own thread (COM
//! MTA, initialised and torn down there); stop signals it, waits (bounded),
//! and then restores the endpoints' volume and mute, all under the lock.
//! A worker that falls back to PlaySoundW checks the stop flag under the
//! FALLBACK lock, which stop also takes, so a late fallback cannot start after
//! a stop.

use std::ops::Range;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::log_line;
use crate::settings::Settings;

static SOUND_BEEP: &[u8] = include_bytes!("../assets/alarm.wav");
static SOUND_TWO_TONE: &[u8] = include_bytes!("../assets/alarm_two_tone.wav");
static SOUND_PULSE: &[u8] = include_bytes!("../assets/alarm_pulse.wav");

const STOP_WAIT: Duration = Duration::from_secs(2);
const RESTORE_WAIT: Duration = Duration::from_secs(2);

// ---------------------------------------------------------------------------
// Pure logic (unit-tested)
// ---------------------------------------------------------------------------

/// Settings `alarm_sound` -> embedded WAV. Unknown names play the beep.
pub(crate) fn sound_for(name: &str) -> &'static [u8] {
    match name {
        "two_tone" => SOUND_TWO_TONE,
        "pulse" => SOUND_PULSE,
        _ => SOUND_BEEP,
    }
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct WavInfo {
    pub(crate) channels: u16,
    pub(crate) sample_rate: u32,
    pub(crate) bits: u16,
    pub(crate) block_align: u16,
    /// Byte range of the PCM data, trimmed to whole frames.
    pub(crate) data: Range<usize>,
}

fn u16le(b: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([b[at], b[at + 1]])
}

fn u32le(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

/// Walks the RIFF chunks (any order, odd sizes padded) for `fmt ` and `data`.
pub(crate) fn parse_wav(b: &[u8]) -> Result<WavInfo, String> {
    if b.len() < 12 || &b[0..4] != b"RIFF" || &b[8..12] != b"WAVE" {
        return Err("not a RIFF/WAVE file".into());
    }
    let mut pos = 12usize;
    let mut fmt: Option<(u16, u16, u32, u16, u16)> = None;
    let mut data: Option<Range<usize>> = None;
    while pos + 8 <= b.len() {
        let id = &b[pos..pos + 4];
        let size = u32le(b, pos + 4) as usize;
        let start = pos + 8;
        let end = start.saturating_add(size).min(b.len());
        if id == b"fmt " {
            if end - start < 16 {
                return Err("fmt chunk too short".into());
            }
            fmt = Some((
                u16le(b, start),
                u16le(b, start + 2),
                u32le(b, start + 4),
                u16le(b, start + 12),
                u16le(b, start + 14),
            ));
        } else if id == b"data" && data.is_none() {
            data = Some(start..end);
        }
        pos = start.saturating_add(size).saturating_add(size & 1);
    }
    let (tag, channels, sample_rate, block_align, bits) = fmt.ok_or("no fmt chunk")?;
    let data = data.ok_or("no data chunk")?;
    if tag != 1 {
        return Err(format!("unsupported format tag {tag} (PCM only)"));
    }
    if channels == 0 || sample_rate == 0 || !matches!(bits, 8 | 16 | 24 | 32) {
        return Err("invalid PCM format".into());
    }
    if u32::from(block_align) != u32::from(channels) * u32::from(bits) / 8 {
        return Err("block align does not match channels and bits".into());
    }
    let len = (data.end - data.start) / block_align as usize * block_align as usize;
    if len == 0 {
        return Err("empty data chunk".into());
    }
    Ok(WavInfo { channels, sample_rate, bits, block_align, data: data.start..data.start + len })
}

/// Settings `alarm_volume` (20-100) -> endpoint scalar (0.2-1.0).
pub(crate) fn alarm_level(setting: u8) -> f32 {
    f32::from(setting.clamp(20, 100)) / 100.0
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct AdjustPlan {
    pub(crate) unmute: bool,
    /// Only ever above the current level; None leaves the volume alone.
    pub(crate) raise_to: Option<f32>,
}

pub(crate) fn plan_adjust(muted: bool, current: f32, setting: u8) -> AdjustPlan {
    let target = alarm_level(setting);
    AdjustPlan { unmute: muted, raise_to: (current < target).then_some(target) }
}

/// What we found on an endpoint and what we actually changed.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Adjusted {
    pub(crate) id: String,
    pub(crate) prev_muted: bool,
    pub(crate) prev_volume: f32,
    pub(crate) changed_mute: bool,
    pub(crate) changed_volume: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct RestoreStep {
    pub(crate) id: String,
    pub(crate) volume: Option<f32>,
    pub(crate) mute: Option<bool>,
}

/// Restore only what we changed (volume first, then mute), skip the rest.
pub(crate) fn restore_plan(list: &[Adjusted]) -> Vec<RestoreStep> {
    list.iter()
        .filter(|a| a.changed_mute || a.changed_volume)
        .map(|a| RestoreStep {
            id: a.id.clone(),
            volume: a.changed_volume.then_some(a.prev_volume),
            mute: a.changed_mute.then_some(a.prev_muted),
        })
        .collect()
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Selection {
    pub(crate) ids: Vec<String>,
    pub(crate) device_missing: bool,
}

/// Settings `alarm_device` -> endpoint ids. A missing id (unplugged) falls
/// back to the default endpoint and is flagged.
pub(crate) fn select_devices(setting: &str, active: &[String], default: Option<&str>) -> Selection {
    let default_ids = || default.map(|d| vec![d.to_string()]).unwrap_or_default();
    match setting {
        "default" | "" => Selection { ids: default_ids(), device_missing: false },
        "all" if !active.is_empty() => Selection { ids: active.to_vec(), device_missing: false },
        "all" => Selection { ids: default_ids(), device_missing: false },
        id if active.iter().any(|a| a == id) => Selection { ids: vec![id.to_string()], device_missing: false },
        _ => Selection { ids: default_ids(), device_missing: true },
    }
}

// ---------------------------------------------------------------------------
// Status for the armed-state indicator
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct AudioStatus {
    pub(crate) last_error: Option<String>,
    pub(crate) using_fallback: bool,
    pub(crate) device_missing: bool,
}

static STATUS: Mutex<AudioStatus> = Mutex::new(AudioStatus { last_error: None, using_fallback: false, device_missing: false });

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|p| p.into_inner())
}

/// Snapshot of the last alarm's audio problems. Separate short lock, never
/// the ALARM lock. Consumed by the armed-state check.
pub(crate) fn audio_status() -> AudioStatus {
    lock(&STATUS).clone()
}

fn note_problem(msg: &str) {
    log_line(&format!("alarm audio: {msg}"));
    lock(&STATUS).last_error = Some(msg.to_string());
}

// ---------------------------------------------------------------------------
// Alarm state
// ---------------------------------------------------------------------------

struct Cfg {
    sound: &'static [u8],
    device: String,
    unmute: bool,
    volume: u8,
}

pub(crate) struct Shared {
    stop: AtomicBool,
    fallback_on: AtomicBool,
    sound: &'static [u8],
    /// Endpoints we changed. `None` once stop has taken them for restore; a
    /// late worker that finds `None` restores its own change immediately.
    adjusted: Mutex<Option<Vec<Adjusted>>>,
}

pub(crate) struct Active {
    shared: Arc<Shared>,
    handle: Option<JoinHandle<()>>,
}

pub(crate) static ALARM: Mutex<Option<Active>> = Mutex::new(None);
static FALLBACK: Mutex<()> = Mutex::new(());

/// Switch to PlaySoundW looping on the default device (once per alarm),
/// unless the alarm has already been stopped.
fn fallback(shared: &Shared, reason: &str) {
    note_problem(reason);
    lock(&STATUS).using_fallback = true;
    let _g = lock(&FALLBACK);
    if shared.stop.load(Ordering::SeqCst) || shared.fallback_on.swap(true, Ordering::SeqCst) {
        return;
    }
    log_line("alarm audio: playing PlaySoundW fallback");
    #[cfg(windows)]
    unsafe {
        use windows::core::PCWSTR;
        use windows::Win32::Media::Audio::{PlaySoundW, SND_ASYNC, SND_LOOP, SND_MEMORY};
        if !PlaySoundW(PCWSTR(shared.sound.as_ptr().cast()), None, SND_MEMORY | SND_ASYNC | SND_LOOP).as_bool() {
            log_line("alarm audio: PlaySoundW fallback failed too");
        }
    }
}

fn record_adjusted(shared: &Shared, a: Adjusted) {
    let mut g = lock(&shared.adjusted);
    match g.as_mut() {
        Some(list) => list.push(a),
        None => {
            drop(g);
            native::restore_endpoints(&[a]);
        }
    }
}

fn wait_for_stop(shared: &Shared) {
    while !shared.stop.load(Ordering::SeqCst) {
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn join_bounded(handle: JoinHandle<()>, limit: Duration) -> bool {
    let deadline = Instant::now() + limit;
    while !handle.is_finished() {
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    let _ = handle.join();
    true
}

/// Idempotent: called every poll while red. Settings are read only when an
/// alarm actually starts.
pub(crate) fn start_alarm(settings: impl FnOnce() -> Settings) {
    let mut guard = lock(&ALARM);
    if guard.is_some() {
        return;
    }
    let s = settings();
    let cfg = Cfg { sound: sound_for(&s.alarm_sound), device: s.alarm_device, unmute: s.unmute_on_red, volume: s.alarm_volume };
    *lock(&STATUS) = AudioStatus::default();
    let shared = Arc::new(Shared {
        stop: AtomicBool::new(false),
        fallback_on: AtomicBool::new(false),
        sound: cfg.sound,
        adjusted: Mutex::new(Some(Vec::new())),
    });
    let worker_shared = shared.clone();
    let handle = match std::thread::Builder::new()
        .name("alarm-audio".into())
        .spawn(move || native::run_worker(cfg, worker_shared))
    {
        Ok(h) => Some(h),
        Err(err) => {
            fallback(&shared, &format!("could not start audio thread: {err}"));
            None
        }
    };
    *guard = Some(Active { shared, handle });
}

/// Idempotent. Stops playback first, then restores what we changed.
pub(crate) fn stop_alarm() {
    let mut guard = lock(&ALARM);
    let Some(mut active) = guard.take() else { return };
    {
        let _g = lock(&FALLBACK);
        active.shared.stop.store(true, Ordering::SeqCst);
        #[cfg(windows)]
        unsafe {
            use windows::core::PCWSTR;
            use windows::Win32::Media::Audio::{PlaySoundW, SND_FLAGS};
            // A null sound name stops whatever this process is playing.
            let _ = PlaySoundW(PCWSTR::null(), None, SND_FLAGS(0));
        }
    }
    if let Some(h) = active.handle.take() {
        if !join_bounded(h, STOP_WAIT) {
            log_line("alarm audio: playback thread did not stop within 2 s; restoring anyway");
        }
    }
    let adjusted = lock(&active.shared.adjusted).take().unwrap_or_default();
    if !restore_plan(&adjusted).is_empty() {
        match std::thread::Builder::new()
            .name("alarm-restore".into())
            .spawn(move || native::restore_endpoints(&adjusted))
        {
            Ok(h) => {
                if !join_bounded(h, RESTORE_WAIT) {
                    log_line("alarm audio: volume restore did not finish within 2 s");
                }
            }
            Err(err) => log_line(&format!("alarm audio: could not start restore thread: {err}")),
        }
    }
    drop(guard);
}

/// `[{id, name, is_default}]` for active render endpoints; empty on error.
pub(crate) fn list_audio_devices() -> Value {
    let result = std::thread::Builder::new()
        .name("audio-list".into())
        .spawn(native::list_devices)
        .map_err(|e| e.to_string())
        .and_then(|h| h.join().map_err(|_| "audio list thread panicked".to_string()))
        .and_then(|r| r);
    match result {
        Ok(list) => Value::Array(
            list.into_iter()
                .map(|(id, name, is_default)| json!({ "id": id, "name": name, "is_default": is_default }))
                .collect(),
        ),
        Err(err) => {
            log_line(&format!("list_audio_devices failed: {err}"));
            Value::Array(Vec::new())
        }
    }
}

/// Active render endpoints with their mute state (`None` when it could not be
/// read) plus the default endpoint id, for the armed-state check.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct EndpointFacts {
    pub(crate) endpoints: Vec<(String, Option<bool>)>,
    pub(crate) default: Option<String>,
}

const ENDPOINT_QUERY_WAIT: Duration = Duration::from_millis(1500);
static ENDPOINT_QUERY_BUSY: AtomicBool = AtomicBool::new(false);

/// Presence and mute of the render endpoints, read with short-lived COM
/// objects on a helper thread. Never touches ALARM, FALLBACK, any stream,
/// volume or mute. Bounded: a hung query returns an error instead of
/// blocking the caller, and no second query starts while one is stuck.
pub(crate) fn endpoint_facts() -> Result<EndpointFacts, String> {
    if ENDPOINT_QUERY_BUSY.swap(true, Ordering::SeqCst) {
        return Err("previous audio endpoint query still running".into());
    }
    let (tx, rx) = std::sync::mpsc::channel();
    let spawned = std::thread::Builder::new().name("audio-armed".into()).spawn(move || {
        let result = native::endpoint_facts();
        ENDPOINT_QUERY_BUSY.store(false, Ordering::SeqCst);
        let _ = tx.send(result);
    });
    if let Err(err) = spawned {
        ENDPOINT_QUERY_BUSY.store(false, Ordering::SeqCst);
        return Err(format!("could not start audio query thread: {err}"));
    }
    match rx.recv_timeout(ENDPOINT_QUERY_WAIT) {
        Ok(result) => result,
        Err(_) => Err("audio endpoint query timed out".into()),
    }
}

// ---------------------------------------------------------------------------
// Native WASAPI / endpoint-volume code (kept thin)
// ---------------------------------------------------------------------------

#[cfg(windows)]
mod native {
    use super::*;
    use windows::core::{HSTRING, PWSTR};
    use windows::Win32::Devices::FunctionDiscovery::PKEY_Device_FriendlyName;
    use windows::Win32::Media::Audio::Endpoints::IAudioEndpointVolume;
    use windows::Win32::Media::Audio::{
        eConsole, eRender, IAudioClient, IAudioRenderClient, IMMDevice, IMMDeviceEnumerator, MMDeviceEnumerator,
        AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM, AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
        DEVICE_STATE_ACTIVE, WAVEFORMATEX, WAVE_FORMAT_PCM,
    };
    use windows::Win32::System::Com::StructuredStorage::{PropVariantClear, PropVariantToStringAlloc};
    use windows::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoTaskMemFree, CoUninitialize, CLSCTX_ALL, COINIT_MULTITHREADED, STGM_READ,
    };

    type WinResult<T> = windows::core::Result<T>;

    /// COM for this thread; declare first so it drops after every interface.
    struct Com;
    impl Com {
        fn init() -> Result<Com, String> {
            let hr = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
            if hr.is_err() {
                return Err(format!("CoInitializeEx failed: {hr:?}"));
            }
            Ok(Com)
        }
    }
    impl Drop for Com {
        fn drop(&mut self) {
            unsafe { CoUninitialize() }
        }
    }

    fn enumerator() -> WinResult<IMMDeviceEnumerator> {
        unsafe { CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL) }
    }

    unsafe fn take_pwstr(p: PWSTR) -> String {
        let s = p.to_string().unwrap_or_default();
        CoTaskMemFree(Some(p.0 as *const _));
        s
    }

    fn device_id(d: &IMMDevice) -> WinResult<String> {
        unsafe { Ok(take_pwstr(d.GetId()?)) }
    }

    fn device_name(d: &IMMDevice) -> WinResult<String> {
        unsafe {
            let store = d.OpenPropertyStore(STGM_READ)?;
            let mut pv = store.GetValue(&PKEY_Device_FriendlyName)?;
            let name = PropVariantToStringAlloc(&pv).map(|p| take_pwstr(p));
            let _ = PropVariantClear(&mut pv);
            name
        }
    }

    fn active_devices(e: &IMMDeviceEnumerator) -> WinResult<Vec<(String, IMMDevice)>> {
        unsafe {
            let coll = e.EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE)?;
            let mut out = Vec::new();
            for i in 0..coll.GetCount()? {
                let d = coll.Item(i)?;
                out.push((device_id(&d)?, d));
            }
            Ok(out)
        }
    }

    fn default_id(e: &IMMDeviceEnumerator) -> Option<String> {
        unsafe { e.GetDefaultAudioEndpoint(eRender, eConsole).ok().and_then(|d| device_id(&d).ok()) }
    }

    pub(super) fn list_devices() -> Result<Vec<(String, String, bool)>, String> {
        let _com = Com::init()?;
        let e = enumerator().map_err(|e| e.to_string())?;
        let def = default_id(&e);
        let devices = active_devices(&e).map_err(|e| e.to_string())?;
        Ok(devices
            .into_iter()
            .map(|(id, d)| {
                let name = device_name(&d).unwrap_or_else(|_| id.clone());
                let is_default = def.as_deref() == Some(id.as_str());
                (id, name, is_default)
            })
            .collect())
    }

    pub(super) fn endpoint_facts() -> Result<EndpointFacts, String> {
        let _com = Com::init()?;
        let e = enumerator().map_err(|e| e.to_string())?;
        let default = default_id(&e);
        let devices = active_devices(&e).map_err(|e| e.to_string())?;
        let endpoints = devices
            .into_iter()
            .map(|(id, d)| {
                let muted = unsafe {
                    d.Activate::<IAudioEndpointVolume>(CLSCTX_ALL, None)
                        .and_then(|v| v.GetMute())
                        .map(|m| m.as_bool())
                        .ok()
                };
                (id, muted)
            })
            .collect();
        Ok(EndpointFacts { endpoints, default })
    }

    fn adjust(dev: &IMMDevice, id: &str, setting: u8) -> WinResult<Adjusted> {
        unsafe {
            let vol: IAudioEndpointVolume = dev.Activate(CLSCTX_ALL, None)?;
            let muted = vol.GetMute()?.as_bool();
            let current = vol.GetMasterVolumeLevelScalar()?;
            let plan = plan_adjust(muted, current, setting);
            let mut a = Adjusted {
                id: id.to_string(),
                prev_muted: muted,
                prev_volume: current,
                changed_mute: false,
                changed_volume: false,
            };
            if let Some(level) = plan.raise_to {
                match vol.SetMasterVolumeLevelScalar(level, std::ptr::null()) {
                    Ok(()) => a.changed_volume = true,
                    Err(err) => note_problem(&format!("could not raise volume on {id}: {err}")),
                }
            }
            if plan.unmute {
                match vol.SetMute(false, std::ptr::null()) {
                    Ok(()) => a.changed_mute = true,
                    Err(err) => note_problem(&format!("could not unmute {id}: {err}")),
                }
            }
            Ok(a)
        }
    }

    pub(super) fn restore_endpoints(list: &[Adjusted]) {
        let steps = restore_plan(list);
        if steps.is_empty() {
            return;
        }
        let _com = match Com::init() {
            Ok(c) => c,
            Err(err) => return log_line(&format!("alarm audio: restore: {err}")),
        };
        let e = match enumerator() {
            Ok(e) => e,
            Err(err) => return log_line(&format!("alarm audio: restore: {err}")),
        };
        for step in steps {
            let r: WinResult<()> = unsafe {
                (|| {
                    let dev = e.GetDevice(&HSTRING::from(step.id.as_str()))?;
                    let vol: IAudioEndpointVolume = dev.Activate(CLSCTX_ALL, None)?;
                    if let Some(v) = step.volume {
                        vol.SetMasterVolumeLevelScalar(v, std::ptr::null())?;
                    }
                    if let Some(m) = step.mute {
                        vol.SetMute(m, std::ptr::null())?;
                    }
                    Ok(())
                })()
            };
            match r {
                Ok(()) => log_line(&format!("alarm audio: restored {} ({:?})", step.id, step)),
                Err(err) => log_line(&format!("alarm audio: restore failed on {}: {err}", step.id)),
            }
        }
    }

    struct Stream {
        id: String,
        client: IAudioClient,
        render: IAudioRenderClient,
        frames: u32,
        pos: usize,
        failed: bool,
    }

    impl Stream {
        fn open(dev: &IMMDevice, id: &str, wav: &WavInfo, data: &[u8]) -> WinResult<Stream> {
            unsafe {
                let client: IAudioClient = dev.Activate(CLSCTX_ALL, None)?;
                let fmt = WAVEFORMATEX {
                    wFormatTag: WAVE_FORMAT_PCM as u16,
                    nChannels: wav.channels,
                    nSamplesPerSec: wav.sample_rate,
                    nAvgBytesPerSec: wav.sample_rate * u32::from(wav.block_align),
                    nBlockAlign: wav.block_align,
                    wBitsPerSample: wav.bits,
                    cbSize: 0,
                };
                client.Initialize(
                    AUDCLNT_SHAREMODE_SHARED,
                    AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                    2_000_000, // 200 ms, in 100 ns units
                    0,
                    &fmt,
                    None,
                )?;
                let frames = client.GetBufferSize()?;
                let render: IAudioRenderClient = client.GetService()?;
                let mut s = Stream { id: id.to_string(), client, render, frames, pos: 0, failed: false };
                s.fill(data, usize::from(wav.block_align))?;
                s.client.Start()?;
                Ok(s)
            }
        }

        /// Top the buffer up with the sound, looping.
        fn fill(&mut self, data: &[u8], block_align: usize) -> WinResult<()> {
            unsafe {
                let padding = self.client.GetCurrentPadding()?;
                let avail = self.frames.saturating_sub(padding);
                if avail == 0 {
                    return Ok(());
                }
                let buf = self.render.GetBuffer(avail)?;
                let n = avail as usize * block_align;
                let out = std::slice::from_raw_parts_mut(buf, n);
                let mut w = 0;
                while w < n {
                    let chunk = (data.len() - self.pos).min(n - w);
                    out[w..w + chunk].copy_from_slice(&data[self.pos..self.pos + chunk]);
                    w += chunk;
                    self.pos = (self.pos + chunk) % data.len();
                }
                self.render.ReleaseBuffer(avail, 0)
            }
        }
    }

    pub(super) fn run_worker(cfg: Cfg, shared: Arc<Shared>) {
        let _com = match Com::init() {
            Ok(c) => c,
            Err(err) => {
                fallback(&shared, &err);
                return wait_for_stop(&shared);
            }
        };
        play(&cfg, &shared);
    }

    fn play(cfg: &Cfg, shared: &Shared) {
        let wav = match parse_wav(cfg.sound) {
            Ok(w) => w,
            Err(err) => {
                fallback(shared, &format!("alarm sound unreadable: {err}"));
                return wait_for_stop(shared);
            }
        };
        let data = &cfg.sound[wav.data.clone()];
        let block_align = usize::from(wav.block_align);
        let e = match enumerator() {
            Ok(e) => e,
            Err(err) => {
                fallback(shared, &format!("no audio device enumerator: {err}"));
                return wait_for_stop(shared);
            }
        };
        let devices = active_devices(&e).unwrap_or_else(|err| {
            note_problem(&format!("could not list audio outputs: {err}"));
            Vec::new()
        });
        let ids: Vec<String> = devices.iter().map(|(id, _)| id.clone()).collect();
        let sel = select_devices(&cfg.device, &ids, default_id(&e).as_deref());
        if sel.device_missing {
            lock(&STATUS).device_missing = true;
            note_problem(&format!("selected output {} not found; using the default output", cfg.device));
        }
        let mut streams: Vec<Stream> = Vec::new();
        for id in &sel.ids {
            if shared.stop.load(Ordering::SeqCst) {
                break;
            }
            let dev = match devices.iter().find(|(d, _)| d == id) {
                Some((_, d)) => Ok(d.clone()),
                None => unsafe { e.GetDevice(&HSTRING::from(id.as_str())) },
            };
            let dev = match dev {
                Ok(d) => d,
                Err(err) => {
                    fallback(shared, &format!("could not open output {id}: {err}"));
                    continue;
                }
            };
            if cfg.unmute {
                match adjust(&dev, id, cfg.volume) {
                    Ok(a) => record_adjusted(shared, a),
                    Err(err) => note_problem(&format!("could not read volume on {id}: {err}")),
                }
            }
            match Stream::open(&dev, id, &wav, data) {
                Ok(s) => streams.push(s),
                Err(err) => fallback(shared, &format!("WASAPI playback failed on {id}: {err}")),
            }
        }
        if streams.is_empty() {
            fallback(shared, "no WASAPI output could play the alarm");
        }
        while !shared.stop.load(Ordering::SeqCst) {
            for s in streams.iter_mut().filter(|s| !s.failed) {
                if let Err(err) = s.fill(data, block_align) {
                    s.failed = true;
                    fallback(shared, &format!("playback failed mid-alarm on {}: {err}", s.id));
                }
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        for s in &streams {
            unsafe {
                let _ = s.client.Stop();
            }
        }
    }
}

#[cfg(not(windows))]
mod native {
    use super::*;
    pub(super) fn run_worker(_cfg: Cfg, shared: Arc<Shared>) {
        wait_for_stop(&shared);
    }
    pub(super) fn restore_endpoints(_list: &[Adjusted]) {}
    pub(super) fn list_devices() -> Result<Vec<(String, String, bool)>, String> {
        Ok(Vec::new())
    }
    pub(super) fn endpoint_facts() -> Result<EndpointFacts, String> {
        Err("audio endpoints are only available on Windows".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wav(chunks: &[(&[u8; 4], Vec<u8>)]) -> Vec<u8> {
        let mut body = b"WAVE".to_vec();
        for (id, data) in chunks {
            body.extend_from_slice(*id);
            body.extend_from_slice(&(data.len() as u32).to_le_bytes());
            body.extend_from_slice(data);
            if data.len() % 2 == 1 {
                body.push(0);
            }
        }
        let mut out = b"RIFF".to_vec();
        out.extend_from_slice(&(body.len() as u32).to_le_bytes());
        out.extend(body);
        out
    }

    fn fmt16(channels: u16, rate: u32) -> Vec<u8> {
        let ba = channels * 2;
        let mut f = Vec::new();
        f.extend_from_slice(&1u16.to_le_bytes());
        f.extend_from_slice(&channels.to_le_bytes());
        f.extend_from_slice(&rate.to_le_bytes());
        f.extend_from_slice(&(rate * u32::from(ba)).to_le_bytes());
        f.extend_from_slice(&ba.to_le_bytes());
        f.extend_from_slice(&16u16.to_le_bytes());
        f
    }

    #[test]
    fn bundled_sounds_parse_as_16bit_mono_22050() {
        for name in ["beep", "two_tone", "pulse"] {
            let info = parse_wav(sound_for(name)).unwrap();
            assert_eq!((info.channels, info.sample_rate, info.bits, info.block_align), (1, 22050, 16, 2), "{name}");
            assert!(info.data.end - info.data.start > 1000);
        }
    }

    #[test]
    fn sound_mapping_defaults_to_beep() {
        assert!(std::ptr::eq(sound_for("beep"), SOUND_BEEP));
        assert!(std::ptr::eq(sound_for("two_tone"), SOUND_TWO_TONE));
        assert!(std::ptr::eq(sound_for("pulse"), SOUND_PULSE));
        assert!(std::ptr::eq(sound_for("siren"), SOUND_BEEP));
        assert!(std::ptr::eq(sound_for(""), SOUND_BEEP));
    }

    #[test]
    fn wav_parser_skips_extra_chunks_and_odd_padding() {
        let b = wav(&[(b"LIST", vec![1, 2, 3]), (b"fmt ", fmt16(1, 22050)), (b"data", vec![1, 2, 3, 4, 5])]);
        let info = parse_wav(&b).unwrap();
        // LIST is 3 bytes + 1 pad; data trimmed to whole 2-byte frames.
        assert_eq!(info.data.end - info.data.start, 4);
        assert_eq!(&b[info.data.clone()], &[1, 2, 3, 4]);
    }

    #[test]
    fn wav_parser_accepts_data_before_fmt_and_truncated_data() {
        let mut b = wav(&[(b"data", vec![9; 8]), (b"fmt ", fmt16(2, 44100))]);
        let info = parse_wav(&b).unwrap();
        assert_eq!(info.channels, 2);
        assert_eq!(info.data.end - info.data.start, 8);
        // Declared size longer than the file: clamp to what's there.
        b = wav(&[(b"fmt ", fmt16(1, 22050)), (b"data", vec![7; 6])]);
        let len = b.len();
        b[len - 10..len - 6].copy_from_slice(&1000u32.to_le_bytes());
        let info = parse_wav(&b).unwrap();
        assert_eq!(info.data.end, len);
    }

    #[test]
    fn wav_parser_rejects_bad_input() {
        assert!(parse_wav(b"").is_err());
        assert!(parse_wav(b"RIFF\0\0\0\0WAVX").is_err());
        assert!(parse_wav(&wav(&[(b"fmt ", fmt16(1, 22050))])).is_err());
        assert!(parse_wav(&wav(&[(b"data", vec![0; 4])])).is_err());
        assert!(parse_wav(&wav(&[(b"fmt ", fmt16(1, 22050)), (b"data", vec![0; 1])])).is_err());
        let mut f = fmt16(1, 22050);
        f[0] = 3; // float
        assert!(parse_wav(&wav(&[(b"fmt ", f), (b"data", vec![0; 4])])).is_err());
        assert!(parse_wav(&wav(&[(b"fmt ", vec![1, 0]), (b"data", vec![0; 4])])).is_err());
    }

    #[test]
    fn alarm_level_maps_and_clamps() {
        assert_eq!(alarm_level(20), 0.2);
        assert_eq!(alarm_level(60), 0.6);
        assert_eq!(alarm_level(100), 1.0);
        assert_eq!(alarm_level(5), 0.2);
        assert_eq!(alarm_level(150), 1.0);
    }

    #[test]
    fn plan_never_lowers_volume() {
        assert_eq!(plan_adjust(true, 0.1, 60), AdjustPlan { unmute: true, raise_to: Some(0.6) });
        assert_eq!(plan_adjust(false, 0.6, 60), AdjustPlan { unmute: false, raise_to: None });
        assert_eq!(plan_adjust(false, 0.9, 60), AdjustPlan { unmute: false, raise_to: None });
        assert_eq!(plan_adjust(true, 1.0, 100), AdjustPlan { unmute: true, raise_to: None });
        assert_eq!(plan_adjust(false, 0.0, 20), AdjustPlan { unmute: false, raise_to: Some(0.2) });
    }

    #[test]
    fn restore_only_what_changed() {
        let a = |id: &str, cm, cv| Adjusted { id: id.into(), prev_muted: true, prev_volume: 0.15, changed_mute: cm, changed_volume: cv };
        let plan = restore_plan(&[a("x", true, true), a("y", false, false), a("z", false, true), a("w", true, false)]);
        assert_eq!(
            plan,
            vec![
                RestoreStep { id: "x".into(), volume: Some(0.15), mute: Some(true) },
                RestoreStep { id: "z".into(), volume: Some(0.15), mute: None },
                RestoreStep { id: "w".into(), volume: None, mute: Some(true) },
            ]
        );
        assert!(restore_plan(&[]).is_empty());
    }

    #[test]
    fn device_selection() {
        let active = vec!["spk".to_string(), "hp".to_string()];
        let sel = |s| select_devices(s, &active, Some("spk"));
        assert_eq!(sel("default"), Selection { ids: vec!["spk".into()], device_missing: false });
        assert_eq!(sel("all"), Selection { ids: active.clone(), device_missing: false });
        assert_eq!(sel("hp"), Selection { ids: vec!["hp".into()], device_missing: false });
        assert_eq!(sel("gone"), Selection { ids: vec!["spk".into()], device_missing: true });
        assert_eq!(select_devices("all", &[], Some("spk")).ids, vec!["spk".to_string()]);
        assert_eq!(select_devices("gone", &[], None), Selection { ids: vec![], device_missing: true });
    }
}
