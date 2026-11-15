"""Shared app shell: theme resolution, top bar / bottom tabs, living dot, footer.

Every authenticated view renders through render_shell() so navigation, theming,
and the freshness dot behave identically across the app.
"""

from __future__ import annotations

import html

TABS = [
    ("now", "Today", "/", "•"),
    ("night", "Tonight", "/night", "☾"),
    ("rhythms", "Rhythms", "/rhythms", "▦"),
    ("data", "Data", "/data", "≡"),
]

# Runs before first paint so the page never flashes the wrong theme.
THEME_RESOLVER = """<script>
(function () {
  try {
    var saved = localStorage.getItem('owletTheme');
    var mode = saved && saved !== 'auto'
      ? saved
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.dataset.theme = mode;
  } catch (e) { document.documentElement.dataset.theme = 'dark'; }
})();
</script>"""

SHELL_JS = '<script src="/static/pages/shell.js"></script>'


# Small layout rules for the alert settings rows and the desktop-only "This PC"
# pane. Theme tokens only, so both themes follow theme.css.
SETTINGS_CSS = '<link rel="stylesheet" href="/static/pages/shell-settings.css" />'

# The desktop-only "This PC" tab and pane. A <template> is inert: in hosted mode
# none of this is rendered, focusable or read by a screen reader. SHELL_JS
# clones it into the settings modal only in desktop mode with the Tauri bridge.
DESKTOP_SETTINGS_TEMPLATE = """  <template id="desktopSettingsTpl">
    <button type="button" role="tab" data-pane="desktop" aria-selected="false">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>
      <span>This PC</span>
    </button>
    <section class="set-pane" data-pane="desktop" aria-label="This PC">
      <h3>This PC</h3>
      <p class="set-lede">How this computer flashes, sounds and responds to keys when an alert
        fires. These settings are kept on this PC only.</p>
      <p class="ds-note"><b>What “Alerts armed” means.</b> The armed indicator says this PC's
        alert path is working — the server answers, data is fresh, sound can play and the PC
        stays awake. It says nothing about your baby. This is not a medical alarm: the Owlet
        base station stays the primary alarm.</p>
      <p class="ds-msg bad" id="dsLoadMsg" role="alert"></p>

      <div class="ds-card" id="dsFirstRun" hidden>
        <b>Test your alerts</b>
        <small class="ds-msg">Fire one amber and one red test, then confirm you saw the flash and
          heard the alarm on this PC.</small>
        <div class="ds-actions">
          <button class="ds-btn" type="button" data-test="amber">Test amber</button>
          <button class="ds-btn" type="button" data-test="red">Test red</button>
        </div>
        <small class="ds-msg" data-msg="test" aria-live="polite"></small>
        <button class="pp-link-btn" type="button" id="dsFirstRunDone" disabled
          aria-describedby="dsFirstRunMsg">I saw and heard both</button>
        <small class="ds-msg" id="dsFirstRunMsg" data-msg="first_run_test_done" aria-live="polite"></small>
      </div>

      <div class="set-row" id="dsTestRow">
        <div class="set-lab"><b>Test alerts</b><small>Test red uses the real alarm path, so you
          hear the volume you chose. Each test runs for 20 seconds.</small></div>
        <div class="ds-ctl">
          <div class="ds-actions">
            <button class="ds-btn" type="button" data-test="amber">Test amber</button>
            <button class="ds-btn" type="button" data-test="red">Test red</button>
          </div>
          <small class="ds-msg" data-msg="test" aria-live="polite"></small>
        </div>
      </div>

      <span class="pp-label ds-group">Flash and sound</span>
      <div class="set-row">
        <div class="set-lab"><b><label for="dsFlashTarget">Flash target</label></b><small
          id="dsFlashHint">Where the alert flash appears.</small></div>
        <div class="ds-ctl">
          <select id="dsFlashTarget" data-key="flash_target" aria-describedby="dsFlashHint"></select>
          <small class="ds-msg" data-msg="flash_target" aria-live="polite"></small>
        </div>
      </div>
      <div class="set-row">
        <div class="set-lab"><b><label for="dsAlarmSound">Alarm sound</label></b><small
          id="dsSoundHint">Beep is the sound earlier versions used.</small></div>
        <div class="ds-ctl">
          <select id="dsAlarmSound" data-key="alarm_sound" aria-describedby="dsSoundHint"></select>
          <small class="ds-msg" data-msg="alarm_sound" aria-live="polite"></small>
        </div>
      </div>
      <div class="set-row">
        <div class="set-lab"><b><label for="dsAlarmDevice">Alarm device</label></b><small
          id="dsDeviceHint">“All outputs” plays on every active speaker and headset.</small></div>
        <div class="ds-ctl">
          <select id="dsAlarmDevice" data-key="alarm_device" aria-describedby="dsDeviceHint"></select>
          <small class="ds-msg" data-msg="alarm_device" aria-live="polite"></small>
        </div>
      </div>
      <div class="set-row">
        <div class="set-lab"><b id="dsUnmuteLab">Unmute and raise volume on red</b><small
          id="dsUnmuteHint">A red alert unmutes this PC and raises the volume, then restores mute
          and volume when the alert ends. With this off, the alarm plays at the current volume
          and a muted PC is reported as a problem.</small></div>
        <div class="ds-ctl">
          <label class="ds-switch"><input type="checkbox" role="switch" data-key="unmute_on_red"
            aria-labelledby="dsUnmuteLab" aria-describedby="dsUnmuteHint" /><span
            data-state="unmute_on_red">On</span></label>
          <small class="ds-msg" data-msg="unmute_on_red" aria-live="polite"></small>
        </div>
      </div>
      <div class="set-row">
        <div class="set-lab"><b><label for="dsAlarmVolume">Alarm volume level</label></b><small
          id="dsVolumeHint">The level a red alert raises the volume to. It only ever raises the
          volume: a PC already louder is left alone. Needs the switch above.</small></div>
        <div class="ds-ctl">
          <div class="ds-inline">
            <input id="dsAlarmVolume" type="range" min="20" max="100" step="1" data-key="alarm_volume"
              aria-describedby="dsVolumeHint" />
            <output class="ds-out" id="dsAlarmVolumeOut" for="dsAlarmVolume">60%</output>
          </div>
          <small class="ds-msg" data-msg="alarm_volume" aria-live="polite"></small>
        </div>
      </div>

      <span class="pp-label ds-group">Hotkeys</span>
      <p class="set-lede">They work while a game or another app has focus. Click a field, then
        press the keys: use Ctrl, Alt, Shift or Win with a key; F1–F24 can be used alone.
        Esc cancels.</p>
      <div class="set-row">
        <div class="set-lab"><b id="dsHkSnoozeLab">Snooze</b><small id="dsHkSnoozeHint">Silences the
          alert for the snooze length set under Alerts.</small></div>
        <div class="ds-ctl">
          <div class="ds-inline">
            <button type="button" class="ds-capture" data-hotkey="hotkey_snooze" data-name="Snooze"
              aria-describedby="dsHkSnoozeHint"></button>
            <button type="button" class="ds-btn" data-clear="hotkey_snooze"
              aria-label="Clear the Snooze hotkey">Clear</button>
          </div>
          <small class="ds-msg" data-msg="hotkey_snooze" aria-live="polite"></small>
        </div>
      </div>
      <div class="set-row">
        <div class="set-lab"><b id="dsHkAckLab">“I'm checking”</b><small id="dsHkAckHint">Acknowledge:
          the longer silence that says you're on your way.</small></div>
        <div class="ds-ctl">
          <div class="ds-inline">
            <button type="button" class="ds-capture" data-hotkey="hotkey_acknowledge"
              data-name="I'm checking" aria-describedby="dsHkAckHint"></button>
            <button type="button" class="ds-btn" data-clear="hotkey_acknowledge"
              aria-label="Clear the I'm checking hotkey">Clear</button>
          </div>
          <small class="ds-msg" data-msg="hotkey_acknowledge" aria-live="polite"></small>
        </div>
      </div>
      <div class="set-row">
        <div class="set-lab"><b id="dsHkWidgetLab">Show/hide widget</b><small id="dsHkWidgetHint">Shows
          or hides the small always-on-top widget.</small></div>
        <div class="ds-ctl">
          <div class="ds-inline">
            <button type="button" class="ds-capture" data-hotkey="hotkey_widget"
              data-name="Show/hide widget" aria-describedby="dsHkWidgetHint"></button>
            <button type="button" class="ds-btn" data-clear="hotkey_widget"
              aria-label="Clear the Show/hide widget hotkey">Clear</button>
          </div>
          <small class="ds-msg" data-msg="hotkey_widget" aria-live="polite"></small>
        </div>
      </div>
      <div class="set-row">
        <div class="set-lab"><b id="dsHkClickLab">Toggle click-through</b><small
          id="dsHkClickHint">Lets clicks pass through the widget to the window under it.</small></div>
        <div class="ds-ctl">
          <div class="ds-inline">
            <button type="button" class="ds-capture" data-hotkey="hotkey_click_through"
              data-name="Toggle click-through" aria-describedby="dsHkClickHint"></button>
            <button type="button" class="ds-btn" data-clear="hotkey_click_through"
              aria-label="Clear the Toggle click-through hotkey">Clear</button>
          </div>
          <small class="ds-msg" data-msg="hotkey_click_through" aria-live="polite"></small>
        </div>
      </div>

      <span class="pp-label ds-group">Controller</span>
      <div class="set-row">
        <div class="set-lab"><b><label for="dsControllerMode">Controller snooze</label></b><small
          id="dsControllerHint">Snooze with a button or combination you choose on the controller in
          your hands. Presses only count while an amber or red alert is showing. Presses in the
          first 2 seconds of each alert are ignored — and again each time it comes back after a
          snooze or turns red — so the flash and sound always get at least that long. A button
          already held when the alert appears doesn't count. It only snoozes, never “I'm
          checking”, and the game still receives the press. Works with Xbox and PlayStation
          (DualShock 4, DualSense) controllers; Switch pads need Steam Input.</small></div>
        <div class="ds-ctl">
          <select id="dsControllerMode" data-key="controller_mode" aria-describedby="dsControllerHint">
            <option value="off">Off</option>
            <option value="chosen">Chosen button</option>
          </select>
          <button type="button" class="ds-capture" id="dsControllerCapture" hidden
            aria-describedby="dsControllerSaved dsControllerMsg">Press a button</button>
          <small class="ds-msg" id="dsControllerSaved"></small>
          <small class="ds-msg" id="dsControllerMsg" data-msg="controller_mode" aria-live="polite"></small>
        </div>
      </div>

      <span class="pp-label ds-group">Startup and games</span>
      <div class="set-row">
        <div class="set-lab"><b id="dsAutostartLab">Start at login</b><small id="dsAutostartHint">Starts
          hidden, with the widget shown.</small></div>
        <div class="ds-ctl">
          <label class="ds-switch"><input type="checkbox" role="switch" data-key="autostart"
            aria-labelledby="dsAutostartLab" aria-describedby="dsAutostartHint" /><span
            data-state="autostart">Off</span></label>
          <small class="ds-msg" data-msg="autostart" aria-live="polite"></small>
        </div>
      </div>
      <div class="set-row">
        <div class="set-lab"><b id="dsKeepAwakeLab">Keep this PC awake while monitoring</b><small
          id="dsKeepAwakeHint">Stops the PC sleeping on its idle timer while readings are being
          collected. The display may still turn off.</small></div>
        <div class="ds-ctl">
          <label class="ds-switch"><input type="checkbox" role="switch" data-key="keep_awake"
            aria-labelledby="dsKeepAwakeLab" aria-describedby="dsKeepAwakeHint" /><span
            data-state="keep_awake">On</span></label>
          <small class="ds-msg" data-msg="keep_awake" aria-live="polite"></small>
        </div>
      </div>
      <div class="set-row">
        <div class="set-lab"><b><label for="dsGameMode">Game mode</label></b><small
          id="dsGameHint">Automatic flashes every monitor and shrinks the widget when a fullscreen
          game is running. Always does that all the time; Never keeps your usual flash target and
          widget size.</small></div>
        <div class="ds-ctl">
          <select id="dsGameMode" data-key="game_mode" aria-describedby="dsGameHint"></select>
          <small class="ds-msg" data-msg="game_mode" aria-live="polite"></small>
        </div>
      </div>
      <div class="set-row">
        <div class="set-lab"><b id="dsCaptureLab">Hide from screen capture</b><small
          id="dsCaptureHint">Keeps the widget and alert overlay out of OBS and Discord screen
          shares. They stay visible on your own screen.</small></div>
        <div class="ds-ctl">
          <label class="ds-switch"><input type="checkbox" role="switch" data-key="capture_exclusion"
            aria-labelledby="dsCaptureLab" aria-describedby="dsCaptureHint" /><span
            data-state="capture_exclusion">Off</span></label>
          <small class="ds-msg" data-msg="capture_exclusion" aria-live="polite"></small>
        </div>
      </div>
    </section>
  </template>"""

# Behaviour for the "This PC" pane. Defines window.owletDesktopSettings; nothing
# here touches the page or calls the Tauri bridge until SHELL_JS calls mount()
# in desktop mode. Raw string: the regexes keep their backslashes.
DESKTOP_SETTINGS_JS = '<script src="/static/pages/shell-desktop-settings.js"></script>'


def render_shell(
    *, view: str, title: str, head: str = "", body: str, scripts: str = "", wide: bool = False
) -> str:
    # The current page is the lighter pill segment; aria-current says the same
    # thing to assistive tech, so the active tab is never colour-only.
    def _tab_attrs(key: str) -> str:
        return 'class="active" aria-current="page"' if key == view else 'class=""'

    tabs_top = "".join(
        f'<a href="{href}" {_tab_attrs(key)}>{label}</a>'
        for key, label, href, _icon in TABS
    )
    tabs_bottom = "".join(
        f'<a href="{href}" {_tab_attrs(key)}>'
        f'<span aria-hidden="true">{icon}</span>{label}</a>'
        for key, label, href, icon in TABS
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="theme-color" content="#070925" />
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-title" content="Owlet" />
  <title>{html.escape(title)} · Owlet Dashboard</title>
  <link rel="icon" href="/favicon.ico" sizes="any" />
  {THEME_RESOLVER}
  <link rel="stylesheet" href="/theme.css" />
  {SETTINGS_CSS}
  {head}
</head>
<body class="themed">
  <header class="shell-top">
    <a class="shell-brand" href="/">Owlet Dashboard<span id="shellDot" class="shell-dot"
      role="button" tabindex="0" aria-label="Data freshness — click to refresh"></span></a>
    <nav class="shell-tabs" aria-label="Views">{tabs_top}</nav>
    <span class="shell-side" id="profileMenuWrap">
      <span class="bell-wrap">
        <button id="shellBell" class="shell-bell" type="button" aria-haspopup="true"
          aria-expanded="false" title="Notifications" aria-label="Notifications">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.7 21a2 2 0 0 1-3.4 0" />
          </svg>
          <span id="shellBellCount" class="bell-badge" hidden></span>
        </button>
        <div id="bellPop" class="bell-pop card" role="menu" aria-label="Notifications" hidden>
          <div class="bp-head"><b>Notifications</b><span class="bp-meta">newest first · opening marks read</span></div>
          <div id="bellList" class="bell-list"></div>
        </div>
      </span>
      <span class="batt-wrap">
        <button id="shellBattery" class="shell-batt" type="button" aria-haspopup="true"
          aria-expanded="false" title="Battery">
          <span class="batt-shell" aria-hidden="true"><span id="shellBattFill" class="batt-fill"></span></span>
          <span id="shellBattPct" class="batt-pct">—</span>
        </button>
        <div id="battPop" class="bell-pop card" role="menu" aria-label="Battery" hidden>
          <div class="bp-head"><b>Battery</b><span class="bp-meta" id="battMeta"></span></div>
          <div id="battBody" class="batt-body"></div>
          <div class="batt-wear-box">
            <div class="bp-head"><b>Wear history</b>
              <span class="bp-meta">%/h while draining</span></div>
            <div class="pp-seg" id="battSeg" role="group" aria-label="Wear grouping">
              <button type="button" data-group="day" class="active">Days</button>
              <button type="button" data-group="week">Weeks</button>
              <button type="button" data-group="month">Months</button>
            </div>
            <div id="battWear" class="bw-bars"></div>
            <p id="battWearNote" class="batt-note"></p>
          </div>
        </div>
      </span>
      <button id="profileMenuToggle" class="profile-chip" type="button" aria-haspopup="menu"
        aria-expanded="false" title="Account and dashboard settings">
        <span id="profileAvatar" class="pc-avatar" aria-hidden="true">·</span>
        <span class="pc-labels">
          <span id="profileAccountName" class="pc-name">Owlet profile</span>
          <span id="profileDeviceName" class="pc-device">Owlet sock</span>
        </span>
        <span class="pc-caret" aria-hidden="true">▾</span>
      </button>
      <div id="profileMenu" class="profile-pop card" role="menu"
        aria-label="Account and dashboard settings" hidden>
        <div class="pp-id">
          <span id="profileMenuAvatar" class="pp-avatar" aria-hidden="true">·</span>
          <div><b id="profileMenuTitle">…</b><small id="profileMenuSubtitle"></small></div>
        </div>
        <div class="pp-section" id="accountCluster">
          <span class="pp-label">Owlet account</span>
          <div class="pp-row">
            <select id="accountSelect"><option value="">Default</option></select>
            <button id="addAccount" class="pp-link-btn" type="button"
              title="Link another Owlet account">Link</button>
          </div>
        </div>
        <button id="openSettings" class="pp-settings-btn" type="button">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          Settings
        </button>
        <div class="pp-actions">
          <button id="installApp" class="pp-install" type="button"
            title="Install Owlet as an app">Install app</button>
          <form method="post" action="/auth/logout" class="pp-signout" style="margin:0">
            <button type="submit">Sign out</button>
          </form>
          <button id="logoutOwlet" class="pp-switch" type="button" hidden
            title="Sign out of your Owlet login — your history is kept">Log out</button>
          <button id="switchServer" class="pp-switch" type="button" hidden
            title="Connect to a different server, or collect on this PC">Switch server…</button>
        </div>
        <div class="pp-status" id="status"><span class="status-dot"></span>Checking collector…</div>
        <div class="pp-version" id="appVersion" hidden></div>
      </div>
    </span>
  </header>
  <main class="shell-main{' wide' if wide else ''}">
{body}
  </main>
  <footer class="shell-footer">Retrospective trend viewing only — not a medical monitor or alert
    replacement. Unofficial; not affiliated with Owlet Baby Care.</footer>
  <nav class="shell-bottom" aria-label="Views">{tabs_bottom}</nav>
  <div id="focusBackdrop" class="focus-backdrop" hidden>
    <div class="focus-sheet card" role="dialog" aria-modal="true" aria-label="Zoomed data">
      <header>
        <div><b id="focusTitle">—</b><small id="focusReadout" class="focus-readout">touch the chart to read a moment</small></div>
        <button id="focusClose" type="button" aria-label="Close">✕</button>
      </header>
      <div class="pp-seg" id="focusSeg" role="group" aria-label="Window">
        <button type="button" data-span="15">15m</button>
        <button type="button" data-span="45">45m</button>
        <button type="button" data-span="120">2h</button>
        <button type="button" data-span="360">6h</button>
      </div>
      <div class="focus-charts" id="focusCharts"></div>
      <a id="focusDataLink" class="focus-link" href="/data">Open in the Data workbench →</a>
    </div>
  </div>
  <div id="settingsBackdrop" class="focus-backdrop" hidden>
    <div class="settings-sheet card" role="dialog" aria-modal="true" aria-label="Settings">
      <header>
        <div><b>Settings</b><small class="focus-readout">saved to your profile, on every device</small></div>
        <button id="settingsClose" type="button" aria-label="Close">✕</button>
      </header>
      <div class="settings-body">
        <nav class="set-rail" role="tablist" aria-label="Settings sections">
          <button type="button" role="tab" data-pane="baby" class="active">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21.2l7.8-7.8 1-1a5.5 5.5 0 0 0 0-7.8z"/></svg>
            <span>Baby</span>
          </button>
          <button type="button" role="tab" data-pane="alerts">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.5 21a2 2 0 0 1-3 0"/></svg>
            <span>Alerts &amp; reports</span>
          </button>
          <button type="button" role="tab" data-pane="display">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>
            <span>Display</span>
          </button>
          <button type="button" role="tab" data-pane="signin">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zm0 0L15.5 7.5m3 3L21 8m-3.5-3.5L19 6"/></svg>
            <span>Sign-in</span>
          </button>
          <button type="button" role="tab" data-pane="device">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5a7 7 0 0 1 14 0"/><path d="M8.5 12.5a3.5 3.5 0 0 1 7 0"/><circle cx="12" cy="16" r="1.6"/><path d="M12 17.6V21"/></svg>
            <span>Device</span>
          </button>
        </nav>
        <div class="set-panes">
          <section class="set-pane active" data-pane="baby">
            <h3>Baby</h3>
            <p class="set-lede">Who the dashboard watches over, and when her night runs.</p>
            <div class="set-row">
              <div class="set-lab"><b>Name</b><small>Replaces “the baby” across every page.</small></div>
              <input id="babyNameSetting" type="text" maxlength="40" placeholder="e.g. Hazel"
                autocomplete="off" />
            </div>
            <div class="set-row">
              <div class="set-lab"><b>Date of birth</b><small id="birthDateHint">Owlet's API doesn't share
                this — set it so milestones can be age-adjusted.</small></div>
              <input id="birthDateSetting" type="date" autocomplete="off" />
            </div>
            <div class="set-row">
              <div class="set-lab"><b>Night runs from</b><small>Tonight and Rhythms count this span as
                the night; everything else is the day.</small></div>
              <div class="pp-row pp-clock-row">
                <input id="nightStartSetting" type="time" value="19:00" />
                <span class="pp-to">to</span>
                <input id="nightEndSetting" type="time" value="07:00" />
              </div>
            </div>
            <div class="set-row">
              <div class="set-lab"><b>Feed tracking</b><small>Log bottles, nursing, and solids — feeds
                the Today chip, the evening report, and feed insights on Rhythms.</small></div>
              <select id="feedTrackingSetting">
                <option value="on">On</option>
                <option value="off">Off</option>
              </select>
            </div>
          </section>
          <section class="set-pane" data-pane="alerts">
            <h3>Alerts &amp; reports</h3>
            <p class="set-lede">What the dashboard should tap you on the shoulder about.</p>
            <div class="set-row">
              <div class="set-lab"><b>Low-O₂ alert</b><small id="o2AlertHint">Crossing below rings the
                bell, shows a toast, and — if you allow notifications — pings your device.</small></div>
              <select id="o2AlertSetting">
                <option value="">Off</option>
                <option value="92">Below 92%</option>
                <option value="90">Below 90%</option>
                <option value="88">Below 88%</option>
                <option value="86">Below 86%</option>
              </select>
            </div>
            <div class="set-row">
              <div class="set-lab"><b>Oxygen colour thresholds</b><small id="o2TierHint">Where an oxygen
                reading turns amber, then red — across every chart, report and number in the
                app.</small></div>
              <div class="set-tiers">
                <label><span class="tier-swatch warn"></span>Oxygen amber below
                  <input id="o2WarnSetting" type="number" min="70" max="99" step="1" inputmode="numeric" />%</label>
                <label><span class="tier-swatch bad"></span>Oxygen red below
                  <input id="o2CritSetting" type="number" min="70" max="99" step="1" inputmode="numeric" />%</label>
              </div>
            </div>
            <div class="set-row">
              <div class="set-lab"><b><label for="alertSnoozeSetting">Snooze length</label></b><small
                id="alertSnoozeHint">How long Snooze silences an amber or red alert before it comes
                back, 5–120 seconds. With several accounts linked, the shortest snooze
                applies.</small></div>
              <div class="ds-ctl">
                <div class="ds-inline">
                  <input id="alertSnoozeSetting" type="number" min="5" max="120" step="1"
                    inputmode="numeric" aria-describedby="alertSnoozeHint alertSnoozeMsg" />
                  <span class="pp-to">sec</span>
                </div>
                <small class="ds-msg" id="alertSnoozeMsg" aria-live="polite"></small>
              </div>
            </div>
            <div class="set-row">
              <div class="set-lab"><b><label for="alertStaleSetting">Stale timeout</label></b><small
                id="alertStaleHint">Warns that no data is arriving once the newest reading is this
                old, 30–300 seconds.</small></div>
              <div class="ds-ctl">
                <div class="ds-inline">
                  <input id="alertStaleSetting" type="number" min="30" max="300" step="1"
                    inputmode="numeric" aria-describedby="alertStaleHint alertStaleMsg" />
                  <span class="pp-to">sec</span>
                </div>
                <small class="ds-msg" id="alertStaleMsg" aria-live="polite"></small>
              </div>
            </div>
            <div class="set-row">
              <div class="set-lab"><b id="awakeChimeLab">Awake chime</b><small id="awakeChimeHint">A
                gentle chime when sleep changes from asleep to awake. No flash and nothing to
                snooze; at most one chime every 5 minutes. Plays on this PC's desktop
                app.</small></div>
              <div class="ds-ctl">
                <label class="ds-switch"><input id="awakeChimeSetting" type="checkbox" role="switch"
                  aria-labelledby="awakeChimeLab" aria-describedby="awakeChimeHint" /><span
                  id="awakeChimeState">Off</span></label>
                <small class="ds-msg" id="awakeChimeMsg" aria-live="polite"></small>
              </div>
            </div>
            <div class="set-row">
              <div class="set-lab"><b>Evening prep report</b><small>A daily nudge before bedtime — awake
                time, naps, and feeds so far.</small></div>
              <select id="readinessSetting">
                <option value="">Off</option>
                <option value="17:30">5:30 PM</option>
                <option value="18:00">6:00 PM</option>
                <option value="18:30">6:30 PM</option>
                <option value="18:45">6:45 PM</option>
                <option value="19:00">7:00 PM</option>
                <option value="19:30">7:30 PM</option>
                <option value="20:00">8:00 PM</option>
              </select>
            </div>
            <div class="set-row">
              <div class="set-lab"><b>Timezone</b><small>Sets when the report fires. Auto follows
                whatever device last opened the dashboard.</small></div>
              <select id="timezoneSetting"><option value="">Auto (this device)</option></select>
            </div>
          </section>
          <section class="set-pane" data-pane="display">
            <h3>Display</h3>
            <p class="set-lede">How the dashboard looks and how the charts read.</p>
            <div class="set-row">
              <div class="set-lab"><b>Theme</b><small>Auto follows this device's light/dark setting.</small></div>
              <div class="pp-seg" role="group" aria-label="Theme">
                <button type="button" data-theme-set="auto">Auto</button>
                <button type="button" data-theme-set="light">Light</button>
                <button type="button" data-theme-set="dark">Dark</button>
              </div>
            </div>
            <div class="set-row">
              <div class="set-lab"><b>Update every</b><small>How often the live view refreshes.</small></div>
              <select id="pollIntervalSetting">
                <option value="5">5 sec</option>
                <option value="10">10 sec</option>
                <option value="30">30 sec</option>
                <option value="60">1 min</option>
                <option value="300">5 min</option>
              </select>
            </div>
            <div class="set-row">
              <div class="set-lab"><b>Movement chart</b><small>The activity level is the sock's own
                normalized 0–100 scale — steadier night to night.</small></div>
              <select id="movementSourceSetting">
                <option value="raw">Raw sensor wiggle</option>
                <option value="bucket">Owlet activity level</option>
              </select>
            </div>
            <div class="set-row">
              <div class="set-lab"><b>Oxygen chart</b><small>Smoothing calms the line; dips still show
                at full depth in counts and reports.</small></div>
              <select id="o2DisplaySetting">
                <option value="raw">Every reading</option>
                <option value="smoothed">Smoothed average</option>
              </select>
            </div>
          </section>
          <section class="set-pane" data-pane="signin">
            <h3>Sign-in</h3>
            <p class="set-lede">The email and password for this dashboard login.</p>
            <div class="set-row">
              <div class="set-lab"><b>Email</b><small>What you type on the sign-in page.</small></div>
              <input id="loginEmailSetting" type="email" autocomplete="username" />
            </div>
            <div class="set-row">
              <div class="set-lab"><b>New password</b><small>Leave blank to keep the current one.</small></div>
              <input id="newPasswordSetting" type="password" minlength="8" maxlength="128"
                autocomplete="new-password" placeholder="••••••••" />
            </div>
            <div class="set-row set-row-confirm">
              <div class="set-lab"><b>Current password</b><small>Required to save either change.
                Changing the password signs out every other device.</small></div>
              <input id="currentPasswordSetting" type="password" autocomplete="current-password" />
            </div>
            <div class="set-save-row">
              <small class="set-feedback" id="signinHint"></small>
              <button id="saveSignin" class="pp-link-btn" type="button">Save sign-in changes</button>
            </div>
          </section>
          <section class="set-pane" data-pane="device">
            <h3>Device</h3>
            <p class="set-lede">The sock and base as Owlet reports them — captured once, refreshed
              when firmware or settings change.</p>
            <div class="dev-facts" id="deviceFacts"><small class="pp-hint">Loading device details…</small></div>
            <div class="dev-signal">
              <div class="dev-sig-head"><span class="pp-label">Signal quality</span><b id="sigNow">—</b></div>
              <div class="dev-sig-nav">
                <button id="sigPrev" type="button" aria-label="Previous day">‹</button>
                <span id="sigDay">Today</span>
                <button id="sigNext" type="button" aria-label="Next day" disabled>›</button>
              </div>
              <svg id="sigChart" viewBox="0 0 320 72" preserveAspectRatio="none" aria-label="Signal strength through the day"></svg>
              <div class="dev-sig-axis"><span>12 AM</span><span>6 AM</span><span>12 PM</span><span>6 PM</span><span>12 AM</span></div>
            </div>
            <div class="set-danger">
              <div class="set-lab"><b>Unlink this Owlet account</b><small id="unlinkHint">Stops collecting
                and permanently deletes this account's stored history. Can't be undone.</small></div>
              <button id="unlinkAccount" class="set-danger-btn" type="button">Unlink…</button>
            </div>
          </section>
        </div>
      </div>
    </div>
  </div>
{DESKTOP_SETTINGS_TEMPLATE}
  <div id="installBackdrop" class="focus-backdrop" hidden>
    <div class="install-sheet card" role="dialog" aria-modal="true" aria-label="Get the app">
      <header>
        <div><b>Get the app</b><small class="focus-readout">two ways to run Owlet Dashboard</small></div>
        <button id="installClose" type="button" aria-label="Close">✕</button>
      </header>
      <div class="install-body">
        <section class="install-opt">
          <h3>Install this page as an app</h3>
          <p>Opens in its own window, no browser bars. It's still this same
            instance — your data stays wherever this page is hosted.</p>
          <button id="installPwaBtn" class="install-cta" type="button" hidden>Install web app</button>
          <p class="install-hint">
            <b>iPhone / iPad:</b> Share → <b>Add to Home Screen</b>.<br>
            <b>Android (Chrome):</b> menu ⋮ → <b>Install app</b> / Add to Home Screen.<br>
            <b>Desktop Chrome / Edge:</b> the install icon at the right of the address
            bar, or menu → <b>Install Owlet Dashboard</b>.
          </p>
        </section>
        <section class="install-opt">
          <h3>Or get the Windows desktop app</h3>
          <p>Download the installer from the releases page:</p>
          <a class="install-cta" href="https://github.com/petemess95/owlet-dashboard/releases/latest"
            target="_blank" rel="noopener noreferrer">Open the releases page →</a>
          <p class="install-hint"><b>You pick how it runs on first launch:</b></p>
          <ul class="install-modes">
            <li><b>Collect on this PC</b> — the app gathers readings itself, but only
              while it's open and the PC is awake, so expect gaps when it sleeps.</li>
            <li><b>Connect to your server</b> — point it at an always-on instance
              (Docker / Unraid) running Owlet Dashboard. The desktop app becomes a
              viewer with <b>complete data continuity</b> — nothing is lost when this
              PC sleeps. This is the best experience.</li>
          </ul>
        </section>
      </div>
    </div>
  </div>
  <div id="toastRack" class="toast-rack" aria-live="polite"></div>
  {DESKTOP_SETTINGS_JS}
  {SHELL_JS}
  {scripts}
</body>
</html>"""
