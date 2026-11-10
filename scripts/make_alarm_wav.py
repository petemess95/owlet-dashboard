"""Generate the alarm and chime WAV files under desktop/src-tauri/assets/ (standard library only).

Run from the repo root:  .venv/Scripts/python scripts/make_alarm_wav.py

All files: 16-bit signed PCM, mono, 22050 Hz, little-endian, sine tones.

- alarm.wav: exactly 1.2 s (26460 samples). Three 150 ms beeps of 880 Hz sine,
  separated by 80 ms silence, starting at t=0. Each beep has 10 ms linear fade in
  and fade out. Peak is -3 dBFS (0.708 * 32767).
- alarm_two_tone.wav: exactly 1.2 s. Four 200 ms tones (660, 880, 660, 880 Hz),
  separated by 100 ms silence. 10 ms fades. Peak 0.708 * 32767.
- alarm_pulse.wav: exactly 1.2 s. Six 80 ms tones of 1000 Hz, separated by
  100 ms silence. 5 ms fades. Peak 0.708 * 32767.
- awake_chime.wav: exactly 1.6 s. Note A 523.25 Hz (0 to 700 ms) and note B
  659.25 Hz (500 ms to 1600 ms), overlapping and summed. Amplitude 0.251 (-12 dBFS)
  per note. Each note fades in over 10 ms and fades out over its last 50%.
"""

import math
import os
import struct
import wave

SAMPLE_RATE = 22050
PEAK = round(0.708 * 32767)  # 23199, the alarm peak (-3 dBFS)
CHIME_AMPLITUDE = 0.251 * 32767  # -12 dBFS per chime note

ALARM_FRAMES = 26460  # 1.2 s
CHIME_FRAMES = 35280  # 1.6 s

# alarm.wav (original beeps)
BEEP_SAMPLES = round(0.150 * SAMPLE_RATE)  # 3308
GAP_SAMPLES = round(0.080 * SAMPLE_RATE)  # 1764
FADE_SAMPLES = round(0.010 * SAMPLE_RATE)  # 220
BEEP_COUNT = 3
ALARM_FREQ_HZ = 880.0

# alarm_two_tone.wav
TWO_TONE_SAMPLES = round(0.200 * SAMPLE_RATE)  # 4410
TWO_TONE_GAP = round(0.100 * SAMPLE_RATE)  # 2205
TWO_TONE_FREQS_HZ = [660.0, 880.0, 660.0, 880.0]

# alarm_pulse.wav
PULSE_SAMPLES = round(0.080 * SAMPLE_RATE)  # 1764
PULSE_GAP = round(0.100 * SAMPLE_RATE)  # 2205
PULSE_FADE_SAMPLES = round(0.005 * SAMPLE_RATE)  # 110
PULSE_FREQ_HZ = 1000.0
PULSE_COUNT = 6

# awake_chime.wav
CHIME_FADE_SAMPLES = round(0.010 * SAMPLE_RATE)  # 220
CHIME_NOTE_A_HZ = 523.25
CHIME_NOTE_B_HZ = 659.25
CHIME_NOTE_A_SAMPLES = round(0.700 * SAMPLE_RATE)  # 15435
CHIME_NOTE_B_START = round(0.500 * SAMPLE_RATE)  # 11025
CHIME_NOTE_B_SAMPLES = round(1.100 * SAMPLE_RATE)  # 24255

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(REPO_ROOT, "desktop", "src-tauri", "assets")


def envelope(i, n, fade_in, fade_out):
    """Linear fade in over fade_in samples and out over fade_out samples at the end of a tone."""
    env_in = i / fade_in
    env_out = (n - 1 - i) / fade_out
    return max(0.0, min(1.0, env_in, env_out))


def beep_envelope(i, n):
    return envelope(i, n, FADE_SAMPLES, FADE_SAMPLES)


def pulse_envelope(i, n):
    return envelope(i, n, PULSE_FADE_SAMPLES, PULSE_FADE_SAMPLES)


def chime_envelope(i, n):
    """10 ms fade in, full level, then linear fade out over the last n // 2 samples."""
    return envelope(i, n, CHIME_FADE_SAMPLES, max(1, n // 2))


def render(total, tones):
    """Render tones into a list of 16-bit samples.

    tones: iterable of (start, length, freq_hz, peak, envelope_fn).
    Each tone contributes peak * sin(2*pi*f*i/SAMPLE_RATE) * envelope_fn(i, length),
    where i counts from the tone's own start. Overlapping tones are summed before
    rounding and clamping to the 16-bit range.
    """
    acc = [0.0] * total
    for start, length, freq_hz, peak, env in tones:
        for i in range(length):
            idx = start + i
            if idx >= total:
                break
            value = peak * math.sin(2.0 * math.pi * freq_hz * i / SAMPLE_RATE)
            value *= env(i, length)
            acc[idx] += value
    return [max(-32768, min(32767, int(round(v)))) for v in acc]


def build_alarm():
    tones = [
        (b * (BEEP_SAMPLES + GAP_SAMPLES), BEEP_SAMPLES, ALARM_FREQ_HZ, PEAK, beep_envelope)
        for b in range(BEEP_COUNT)
    ]
    return render(ALARM_FRAMES, tones)


def build_two_tone():
    tones = []
    for k, freq in enumerate(TWO_TONE_FREQS_HZ):
        start = k * (TWO_TONE_SAMPLES + TWO_TONE_GAP)
        tones.append((start, TWO_TONE_SAMPLES, freq, PEAK, beep_envelope))
    return render(ALARM_FRAMES, tones)


def build_pulse():
    tones = []
    for k in range(PULSE_COUNT):
        start = k * (PULSE_SAMPLES + PULSE_GAP)
        tones.append((start, PULSE_SAMPLES, PULSE_FREQ_HZ, PEAK, pulse_envelope))
    return render(ALARM_FRAMES, tones)


def build_chime():
    tones = [
        (0, CHIME_NOTE_A_SAMPLES, CHIME_NOTE_A_HZ, CHIME_AMPLITUDE, chime_envelope),
        (CHIME_NOTE_B_START, CHIME_NOTE_B_SAMPLES, CHIME_NOTE_B_HZ, CHIME_AMPLITUDE, chime_envelope),
    ]
    return render(CHIME_FRAMES, tones)


def write_wav(path, samples):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack("<%dh" % len(samples), *samples))


def main():
    os.makedirs(ASSETS_DIR, exist_ok=True)
    outputs = [
        ("alarm.wav", build_alarm()),
        ("alarm_two_tone.wav", build_two_tone()),
        ("alarm_pulse.wav", build_pulse()),
        ("awake_chime.wav", build_chime()),
    ]
    for name, samples in outputs:
        path = os.path.join(ASSETS_DIR, name)
        write_wav(path, samples)
        print("wrote", path, "frames:", len(samples))


if __name__ == "__main__":
    main()
