use std::sync::Mutex;

// ---------------------------------------------------------------------------
// Alarm sound: PlaySoundW from memory, looping. Plays at system volume; if
// Windows is muted there's no sound (we don't unmute).
// ---------------------------------------------------------------------------

pub(crate) static ALARM_WAV: &[u8] = include_bytes!("../assets/alarm.wav");
// Keep playback and its flag in the same critical section so a concurrent
// stop cannot finish before an in-flight start actually begins playback.
pub(crate) static ALARM: Mutex<bool> = Mutex::new(false);

pub(crate) fn start_alarm() {
    let mut playing = ALARM.lock().unwrap();
    if *playing {
        return;
    }
    #[cfg(windows)]
    unsafe {
        use windows::core::PCWSTR;
        use windows::Win32::Media::Audio::{PlaySoundW, SND_ASYNC, SND_LOOP, SND_MEMORY};
        let _ = PlaySoundW(PCWSTR(ALARM_WAV.as_ptr().cast()), None, SND_MEMORY | SND_ASYNC | SND_LOOP);
    }
    *playing = true;
}

pub(crate) fn stop_alarm() {
    let mut playing = ALARM.lock().unwrap();
    if !*playing {
        return;
    }
    #[cfg(windows)]
    unsafe {
        use windows::core::PCWSTR;
        use windows::Win32::Media::Audio::{PlaySoundW, SND_FLAGS};
        // A null sound name stops whatever this process is playing.
        let _ = PlaySoundW(PCWSTR::null(), None, SND_FLAGS(0));
    }
    *playing = false;
}
