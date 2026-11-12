# Owlet Dashboard — Windows desktop app (Tauri)

A native Windows shell around the same app: a frozen `owlet-server.exe` sidecar
(FastAPI + SQLite, data in `%LOCALAPPDATA%\owlet-dashboard\`) plus a Tauri window
pointed at it. There is **no login** — it's a single-user local app bound to
127.0.0.1, so every request is implicitly the local user and the app opens
straight into the dashboard.

Unlike the hosted version, desktop mode **stores your Owlet login locally**
(in the SQLite file under `%LOCALAPPDATA%`). That way a refresh token that died
while the PC was off never strands collection — the app just signs in again.
Nothing leaves your machine.

> ## Not a medical alarm
>
> The alerts in this app are an **extra** layer on your PC. They are **not a medical
> alarm** and must never be your only way of knowing your baby is in trouble.
>
> - They depend on **Owlet's cloud, your internet connection and this PC**. If any of
>   them fails (Owlet has an outage, the Wi-Fi drops, the PC sleeps, restarts or crashes,
>   the app is closed), alerts stop and the app may not be able to tell you.
> - **The Owlet base station stays the primary alarm.** Keep it plugged in, in the
>   room, and turned up. This app does not replace it or the Owlet app.
> - The **armed** indicator means "this PC's alert path is working" and nothing more.
>   It does not mean your baby is fine, and it cannot see problems on Owlet's side or
>   in your network.
> - Exclusive-fullscreen games can hide every window on their monitor, and headphones
>   can hide the alarm from the room. There is no alert path independent of this PC.

> **Important limitation — read before relying on it**
> The desktop app only collects readings **while it is running**. If you close it
> or your PC sleeps overnight, those readings are gone for good (Owlet's API has no
> backfill). Gaps are shown honestly in the charts as "collector off" bands.
> For uninterrupted 24/7 history, run the Docker/server version instead.
>
> **One collector per Owlet login.** The desktop app is its own collector, not a
> viewer for a server instance. If a Docker/server instance is already polling
> your Owlet account, don't run this app alongside it — the two refresh the same
> Owlet token and sign each other out, leaving gaps on both sides. Server users
> should open the server's web page in a browser (installable as a PWA) instead.

## Using the alerts (version 0.2)

Everything below is in the dashboard's **Settings** (profile menu, then Settings).
**Alerts** settings are saved to your profile; **This PC** settings exist only in this
desktop app (they are hidden when you use the dashboard in a browser) and are saved on
this PC.

### First run: test your alerts

The first time the app starts, Settings opens on **This PC** with a "Test your alerts"
step: fire one amber and one red test, then confirm you saw the flash and heard the
alarm. Until you confirm, every start shows this step again. You can run the tests any
time with the **Test amber** / **Test red** buttons (each runs for 20 seconds; the tray
menu and the widget's right-click menu have them too). **Test red** uses the real alarm
path, so you hear the volume you chose.

### Settings: Alerts

| Setting | Default | What it does |
| --- | --- | --- |
| Snooze length | 15 s (range 5-120) | How long Snooze silences an amber or red alert before it comes back. The widget button, the overlay hint and the tray menu show your chosen length. With several accounts linked, the shortest one applies. |
| Stale timeout | 60 s (range 30-300) | How old the newest reading can get before the app warns that no data is arriving. |
| Awake chime | Off | A gentle chime, once, when sleep changes from asleep to awake. No flash and nothing to snooze, at most once per 5 minutes, and never during an alert. It plays through this PC's default device. |

### Settings: This PC

| Setting | Default | What it does |
| --- | --- | --- |
| Flash target | Secondary monitor | Where the alert flash appears: the secondary monitor, a border frame round the primary monitor, all monitors, or one specific monitor. With "All monitors" the primary monitor gets a border frame (so you can still see your game) and every other monitor flashes fully. With only one monitor you always get the border frame. |
| Alarm sound | Beep | Beep (the sound earlier versions used), Two-tone, or Pulse. |
| Alarm device | Default device | The default device, **all outputs** (speakers and headset together), or one device from the list. |
| Unmute and raise volume on red | On | A red alert unmutes the alarm device and raises it to the level below, then puts the previous mute and volume back when the alert ends. With it off, the alarm plays at whatever the PC is set to, and a muted PC shows up as an armed problem. |
| Alarm volume level | 60% (range 20-100) | The level a red alert raises the volume to. It only ever raises: if the PC is already louder, it is left alone. |
| Keep this PC awake | On | Stops the PC sleeping on its idle timer while readings are being collected. The screen may still turn off. |
| Start at login | Off | Starts the app with Windows, hidden: you see only the widget, not the dashboard. (First run takes priority: until you confirm the test alerts, the dashboard opens on the test step.) |
| Game mode | Automatic | Automatic: when Windows reports a fullscreen game or app, flash every monitor and shrink the widget to the small pill, then restore both afterwards. Always: do that all the time. Never: use your own flash target and widget size. |
| Hide from screen capture | Off | Keeps the widget and the alert overlay out of OBS and Discord screen shares. They stay visible on your own screen. |

### Snooze and "I'm checking"

| | Snooze | I'm checking (acknowledge) |
| --- | --- | --- |
| Silences | Flash and sound | Flash and sound |
| For how long | Your snooze length (default 15 s), then it comes back if still low | Until oxygen recovers plus 3 minutes, capped at 10 minutes from the press |
| Amber becomes red | Comes back as red when the snooze ends | Breaks through straight away |
| Use it when | You have seen it and need a few seconds | You are on your way to check |

Snooze: click anywhere on a full-screen flash, press the widget's Snooze button, use the
tray menu, or use the snooze hotkey or controller button. **"I'm checking"**: the marked
button at the bottom of the full-screen flash, the tray entry "I'm checking
(acknowledge)", or its hotkey. (A border frame round the primary monitor ignores the
mouse, so use the widget, tray, hotkey or controller there.) While acknowledged, the
widget keeps the alert colour and says "Acknowledged". After the 10-minute cap the alert
returns if oxygen is still low.

Neither is an off switch: if oxygen stays low the alert comes back.

### Hotkeys

Settings, This PC, Hotkeys. All four are **unbound by default**, so they can't clash with
your game's keys. Click a field, then press the keys you want (Esc cancels, Clear removes
the hotkey).

- Actions: **Snooze**, **I'm checking**, **Show/hide widget**, **Toggle click-through**.
- Format: one or more modifiers (Ctrl, Alt, Shift, Win) and then one key, for example
  `Ctrl+Shift+S` or `Alt+F9`. Function keys F1-F24 can stand alone. Two actions can't
  share a combination.
- If another app already holds the combination, the app says so and does not save it.
- Hotkeys are meant to work while a game has focus.

### Controller snooze

Settings, This PC, Controller. Off by default. Choose **Chosen button**, then press the
button (or hold the combination) you want in the capture field.

- It only does anything **while an amber or red alert is showing**. At other times the
  app doesn't read the controller.
- It only **snoozes**. It never uses "I'm checking": that needs the keyboard hotkey, the
  overlay button or the tray.
- **2-second guard:** presses in the first 2 seconds of every appearance of the alert
  are ignored, including each time it returns after a snooze and when amber turns red.
  This keeps a reflex press during play from silencing an alert before you noticed it.
  A button already held when the alert appears doesn't count until you let go and press
  again.
- The game still receives the press; the app can't hide it.
- Supported: Xbox pads and anything that appears as one (XInput); Sony pads (DualShock 4,
  DualSense, DualSense Edge) read directly over USB or Bluetooth, including through
  DS4Windows' virtual DualShock 4. Switch pads need Steam Input or similar.

PlayStation buttons map onto the Xbox names: Cross = A, Circle = B, Square = X,
Triangle = Y, L1/R1 = LB/RB, L3/R3 = stick clicks, Options = Menu, Share/Create = View,
D-pad = D-pad. The PS button and the touchpad click have their own entries. Triggers
(L2/R2) and the sticks themselves can't be chosen.

### The widget and the tray

The widget is a small always-on-top window. Drag it anywhere; it snaps to a monitor's
edge when you let go within 12 pixels of it. Right-click it for the menu: size, opacity,
click-through, test alert, open dashboard, hide.

- **Sizes:** *micro* (a small pill such as `97 · 147`: SpO₂ and pulse), *compact*
  (vitals and status) and *expanded* (adds the last 10 minutes of oxygen with the alert
  thresholds drawn on it, and a sleep timer such as "Asleep 2h 14m" while there is no
  alert or problem to show). The size is remembered.
- **Opacity:** 100, 80, 60 or 40% from the menu.
- **Click-through:** clicks pass through the widget to the window under it. Because you
  can't click it then, turn it off from the tray menu or the click-through hotkey. An
  amber or red alert turns click-through off and makes the widget fully opaque so Snooze
  can be reached; your opacity returns afterwards.
- **Armed dot:** see the next section. It pulses once per new reading, so a frozen feed
  is visible before the stale timeout.
- **Tray icon:** shows the current SpO₂ number in the alert-tier colour. If there is no
  trustworthy reading (stale data, sock off, no account) it shows the normal app icon.
  Left-click opens the dashboard; right-click has the menu (show dashboard, show/hide
  widget, click-through, snooze, I'm checking, test alerts, quit). The tooltip says
  "Not armed" with the first problem when something needs attention.

### What "armed" means, and what to do when it isn't

A green dot (or "Alerts armed on this PC") means **this PC's alert path is working**.
That is all. It is not a statement about your baby and not a guarantee that an alert will
reach you. When something is wrong, the dot turns amber with "!" and the first problem is
shown in words:

| You see | What it means and what to do |
| --- | --- |
| Checking alert status | Just after start-up; it should clear within a few seconds. |
| Alert status unknown | The widget stopped getting answers from the app's alert check. Restart the app. |
| Dashboard server not responding | The built-in server isn't answering. The app restarts it automatically; if this stays, quit and reopen the app. |
| Dashboard server keeps stopping | It restarted 3 times in 10 minutes and the app has stopped trying. Quit and reopen the app. |
| No audio device / Chosen audio device not found / Can't check the audio device | The alarm may have nowhere to play. Plug in or choose a working device in Settings, This PC. |
| Alarm sound fell back to basic playback | The normal alarm path failed, so a basic sound on the default device is used instead. Press Test red to check; restarting the app usually fixes it. |
| Windows is muted | Windows is muted and "Unmute and raise volume on red" is off. Unmute, or turn that setting on. |
| PC may sleep | The app isn't holding the PC awake. Turn "Keep this PC awake" on, or check the Windows power settings. |
| No data for Nm / No recent data | No new reading for longer than the stale timeout. Check the sock, the base station, the Owlet app and your internet. |
| Not monitoring: No Owlet account linked / Viewing a remote server / Sock off / Charging | Shown in grey, not as a fault: this PC isn't the one watching right now, or the sock isn't reporting. Link an account, or check the sock is on and not charging. |

With more than one baby or account, a problem is prefixed with the account name. The
armed check can only notice a problem on Owlet's side or in your network when data goes
stale, which is why the base station remains the primary alarm.

## Build prerequisites (one-time)

1. Rust toolchain: `winget install Rustlang.Rustup` then `rustup default stable-msvc`
2. Visual Studio Build Tools with the "Desktop development with C++" workload:
   `winget install Microsoft.VisualStudio.2022.BuildTools`
3. Tauri CLI: `cargo install tauri-cli --version "^2"`
4. Python deps (repo venv): `.venv\Scripts\python -m pip install -e ".[dev]" pyinstaller`

## Build

From the repository root:

```powershell
.\desktop\build-sidecar.ps1                       # freeze the server -> sidecar exe
cd desktop\src-tauri
cargo tauri icon ..\..\app\static\icon-512.png    # generate icons/ (first build only)
cargo tauri build                                  # NSIS installer in target\release\bundle\nsis\
```

Versions: the app, the Python package and the Tauri config are all **0.2.0**
(`pyproject.toml`, `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml`).

**Known problem: `build-sidecar.ps1` fails under Windows PowerShell 5.1.** PyInstaller
writes its INFO log to stderr, and the script sets `$ErrorActionPreference = "Stop"`, so
the first log line aborts it. A fix is pending. Until then, run the same PyInstaller
command from bash (Git Bash) or PowerShell 7, from the repository root, and do the copy
by hand:

```bash
.venv/Scripts/pyinstaller --onefile --name owlet-server \
  --distpath desktop/dist --workpath desktop/build --specpath desktop \
  --paths . --collect-submodules uvicorn --collect-submodules app \
  --collect-data tzdata --collect-data app \
  --hidden-import aiosqlite --noconfirm desktop/server_entry.py
mkdir -p desktop/src-tauri/binaries
cp -f desktop/dist/owlet-server.exe desktop/src-tauri/binaries/owlet-server-x86_64-pc-windows-msvc.exe
```

That is all the script does: freeze the server, then copy it into
`desktop/src-tauri/binaries/` under the target-triple name Tauri requires. If the
script's options change, change this command to match.

**Regenerating the web icons.** `scripts/generate_icons.py` redraws the icons in
`app/static` and needs Pillow, which the project `.venv` does not include. Install it
with `.venv/Scripts/python -m pip install Pillow`, or run the script with a Python that
already has it. The installer icons in `desktop/src-tauri/icons/` are separate; make
them with `cargo tauri icon` as in the build steps above.

For iterating without an installer: `cargo tauri dev` (spawns the sidecar and opens
the window; it reuses an already-running server on port 8877 if one exists).

## How it fits together

- `server_entry.py` — sidecar entry point; sets desktop defaults (`OWLET_DESKTOP=1`,
  `SEED_DEFAULT_ADMIN=true`, DB in `%LOCALAPPDATA%`) and runs uvicorn on `127.0.0.1:8877`.
- `build-sidecar.ps1` — PyInstaller freeze + copy to `src-tauri/binaries/` with the
  target-triple name Tauri requires.
- `src-tauri/src/main.rs` — spawns the sidecar, waits for the port, opens the window,
  restarts the sidecar if it dies (at most 3 times in 10 minutes), kills it on quit.
- The other modules in `src-tauri/src/`: `alert_loop.rs` (polls the server, drives the
  flash and sound), `armed.rs` (the armed verdict), `audio.rs` (alarm playback, unmute,
  volume), `overlay.rs` (flash windows), `widget.rs` (widget modes, snapping),
  `hotkeys.rs`, `controller.rs` and `controller_hid.rs` (XInput and Sony pads),
  `gamemode.rs`, `autostart.rs`, `tray.rs` and `tray_icon.rs`, and `settings.rs` (the
  shell's own settings file, `%LOCALAPPDATA%\owlet-dashboard\desktop-settings.json`).
- `src-tauri/ui-placeholder/overlay.html` — the flash overlay page.
