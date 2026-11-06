"""Generate desktop/src-tauri/assets/alarm.wav (standard library only).

Spec: 16-bit signed PCM, mono, 22050 Hz, exactly 1.2 s (26460 samples).
Three 150 ms beeps of 880 Hz sine, separated by 80 ms silence, starting at t=0.
Each beep has 10 ms linear fade in and fade out. Peak is -3 dBFS (0.708 * 32767).
"""

import math
import os
import struct
import wave

SAMPLE_RATE = 22050
TOTAL_SAMPLES = 26460  # 1.2 s
FREQ_HZ = 880.0
BEEP_SAMPLES = round(0.150 * SAMPLE_RATE)  # 3308
GAP_SAMPLES = round(0.080 * SAMPLE_RATE)  # 1764
FADE_SAMPLES = round(0.010 * SAMPLE_RATE)  # 220
PEAK = round(0.708 * 32767)  # 23199
BEEP_COUNT = 3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
OUT_PATH = os.path.join(REPO_ROOT, "desktop", "src-tauri", "assets", "alarm.wav")


def envelope(i, n):
    """Linear fade in/out over FADE_SAMPLES at each end of a beep."""
    env_in = i / FADE_SAMPLES
    env_out = (n - 1 - i) / FADE_SAMPLES
    return max(0.0, min(1.0, env_in, env_out))


def build_samples():
    samples = [0] * TOTAL_SAMPLES
    for b in range(BEEP_COUNT):
        start = b * (BEEP_SAMPLES + GAP_SAMPLES)
        for i in range(BEEP_SAMPLES):
            idx = start + i
            if idx >= TOTAL_SAMPLES:
                break
            value = PEAK * math.sin(2.0 * math.pi * FREQ_HZ * i / SAMPLE_RATE)
            value *= envelope(i, BEEP_SAMPLES)
            samples[idx] = max(-32768, min(32767, int(round(value))))
    return samples


def main():
    samples = build_samples()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with wave.open(OUT_PATH, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack("<%dh" % len(samples), *samples))
    print("wrote", OUT_PATH, "frames:", len(samples))


if __name__ == "__main__":
    main()
