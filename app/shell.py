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

SHELL_JS = """<script>
(function () {
  // ---- oxygen severity tiers, shared by every page ------------------------
  // One resolver so the card tint, the charts, the heatmap, the night report
  // and the copy can never disagree about what "low" means. Amber below warn,
  // red below critical; critical is clamped under warn so the tiers can't
  // invert. Defaults match what the app shipped with (90 / 86).
  window.OWLET_O2_DEFAULTS = { warn: 90, critical: 86, min: 70, max: 99 };
  window.owletO2 = function (prefs) {
    var d = window.OWLET_O2_DEFAULTS;
    prefs = prefs || {};
    function bound(value, fallback) {
      var n = Number(value);
      return (isFinite(n) && n >= d.min && n <= d.max) ? Math.round(n) : fallback;
    }
    var warn = bound(prefs.o2_warn_threshold, d.warn);
    var critical = bound(prefs.o2_critical_threshold, d.critical);
    if (critical >= warn) critical = warn - 1;
    return { warn: warn, critical: critical };
  };

  // ---- theme: auto | light | dark, segmented control in the profile menu ----
  function currentSetting() { return localStorage.getItem('owletTheme') || 'auto'; }
  function applyTheme(setting) {
    var mode = setting !== 'auto'
      ? setting
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    var changed = document.documentElement.dataset.theme !== mode;
    document.documentElement.dataset.theme = mode;
    document.querySelectorAll('[data-theme-set]').forEach(function (button) {
      button.classList.toggle('active', button.dataset.themeSet === setting);
    });
    // Only pages that need a hard restyle (chart re-init) hook this; fire it
    // solely on real mode flips so theme sync never causes reload loops.
    if (changed && typeof window.__onThemeChange === 'function') window.__onThemeChange(mode);
  }
  applyTheme(currentSetting());
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
    applyTheme(currentSetting());
  });
  function persistThemePreference(next, accountId) {
    // keepalive: applyTheme() reloads chart pages immediately, and without it
    // the PATCH gets cancelled mid-flight — the server preference never
    // updates and the shell syncs the old theme right back after reload.
    return fetch('/api/accounts/' + accountId, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dashboard_preferences: { theme: next } }),
      keepalive: true
    }).catch(function () {});
  }
  document.querySelectorAll('[data-theme-set]').forEach(function (button) {
    button.addEventListener('click', function () {
      var next = button.dataset.themeSet;
      localStorage.setItem('owletTheme', next);
      var account = shellAccounts[0];
      if (account) {
        persistThemePreference(next, account.id);
        applyTheme(next);
      } else {
        fetch('/api/accounts').then(function (r) { return r.json(); }).then(function (data) {
          var found = (data.accounts || [])[0];
          if (found) return persistThemePreference(next, found.id);
        }).catch(function () {}).then(function () { applyTheme(next); });
      }
    });
  });

  // ---- profile dropdown (the transplanted account & settings menu) ----
  var byId = function (id) { return document.getElementById(id); };
  var profileButton = byId('profileMenuToggle');
  var profileMenu = byId('profileMenu');
  function closeProfile() {
    if (!profileMenu) return;
    profileMenu.hidden = true;
    if (profileButton) profileButton.setAttribute('aria-expanded', 'false');
  }
  if (profileButton && profileMenu) {
    profileButton.addEventListener('click', function (event) {
      event.stopPropagation();
      if (typeof closeBell === 'function') closeBell();
      if (typeof closeBatt === 'function') closeBatt();
      profileMenu.hidden = !profileMenu.hidden;
      profileButton.setAttribute('aria-expanded', String(!profileMenu.hidden));
    });
    document.addEventListener('click', function (event) {
      if (!profileMenu.hidden && !profileMenu.contains(event.target)) closeProfile();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeProfile();
    });
  }

  // ---- app version badge (so you can see which build you're on) ----
  (function () {
    var el = byId('appVersion');
    if (!el) return;
    function show(v, label) {
      if (!v) return;
      el.textContent = label + ' v' + String(v).replace(/^v/, '');
      el.hidden = false;
    }
    if (window.__OWLET_APP_VERSION__) {
      // Desktop app: the Tauri shell injected its own version. This is the value
      // the auto-updater compares, so it confirms whether an update has landed.
      show(window.__OWLET_APP_VERSION__, 'Desktop app');
    } else {
      // Hosted / PWA: fall back to the server's reported package version.
      fetch('/api/health').then(function (r) { return r.json(); }).then(function (h) {
        if (h && h.version) show(h.version, 'Server');
      }).catch(function () {});
    }
  })();

  // ---- settings modal ----
  var settingsBackdrop = byId('settingsBackdrop');
  function openSettingsModal() {
    if (!settingsBackdrop) return;
    closeProfile();
    settingsBackdrop.hidden = false;
    document.body.style.overflow = 'hidden';
    showSettingsPane(localStorage.getItem('owletSettingsPane') || 'baby');
    populateTimezones();
    loadDevicePanel();
    loadSigninEmail();
  }
  function loadSigninEmail() {
    var input = byId('loginEmailSetting');
    // re-fetch until a value actually lands — a latched empty field would
    // make every save look like an email change
    if (!input || input.dataset.saved) return;
    fetch('/api/me').then(function (r) { return r.json(); }).then(function (data) {
      if (data.email) {
        input.dataset.saved = data.email;
        if (document.activeElement !== input && !input.value) input.value = data.email;
      }
    }).catch(function () {});
  }
  if (byId('saveSignin')) byId('saveSignin').addEventListener('click', function () {
    var hint = byId('signinHint');
    var current = byId('currentPasswordSetting').value;
    var email = byId('loginEmailSetting').value.trim();
    var savedEmail = byId('loginEmailSetting').dataset.saved || '';
    var newPassword = byId('newPasswordSetting').value;
    var wantsEmail = email && email.toLowerCase() !== savedEmail.toLowerCase();
    var wantsPassword = newPassword.length > 0;
    var say = function (text, bad) {
      hint.textContent = text;
      hint.style.color = bad ? 'var(--bad)' : 'var(--good)';
    };
    if (!wantsEmail && !wantsPassword) { say('Nothing to change — edit the email or set a new password first.', true); return; }
    if (!current) { say('Enter your current password to confirm the change.', true); return; }
    var post = function (url, body) {
      return fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) throw new Error(data.detail || ('Failed (' + r.status + ')'));
          return data;
        });
      });
    };
    var steps = Promise.resolve();
    if (wantsEmail) steps = steps.then(function () {
      return post('/auth/change-email', { current_password: current, email: email })
        .then(function (data) { byId('loginEmailSetting').dataset.saved = data.email; });
    });
    if (wantsPassword) steps = steps.then(function () {
      return post('/auth/change-password', { current_password: current, new_password: newPassword });
    });
    byId('saveSignin').disabled = true;
    steps.then(function () {
      byId('currentPasswordSetting').value = '';
      byId('newPasswordSetting').value = '';
      say(wantsPassword
        ? 'Saved. Other devices were signed out — use the new password there.'
        : 'Saved. Use the new email next time you sign in.', false);
      loadShellAccounts();
    }).catch(function (error) {
      say(error.message, true);
    }).then(function () { byId('saveSignin').disabled = false; });
  });
  function closeSettingsModal() {
    if (!settingsBackdrop || settingsBackdrop.hidden) return;
    settingsBackdrop.hidden = true;
    document.body.style.overflow = '';
  }
  if (byId('openSettings')) byId('openSettings').addEventListener('click', openSettingsModal);
  if (byId('settingsClose')) byId('settingsClose').addEventListener('click', closeSettingsModal);
  function showSettingsPane(name) {
    var known = false;
    document.querySelectorAll('.set-pane').forEach(function (pane) {
      if (pane.dataset.pane === name) known = true;
    });
    if (!known) name = 'baby';
    document.querySelectorAll('.set-rail [data-pane]').forEach(function (tab) {
      var active = tab.dataset.pane === name;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
      if (active && tab.scrollIntoView) tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    });
    document.querySelectorAll('.set-pane').forEach(function (pane) {
      pane.classList.toggle('active', pane.dataset.pane === name);
    });
    localStorage.setItem('owletSettingsPane', name);
    if (name === 'desktop' && desktopSettings) desktopSettings.refresh();
  }
  // ---- "This PC" pane (desktop app only) ----
  // The pane lives in an inert <template> and is only cloned into the modal
  // when the server says desktop_mode AND the Tauri invoke bridge exists.
  var desktopSettings = null;
  function mountDesktopSettings() {
    var invoke = tauriInvoke();
    if (desktopSettings || !desktopMode || !invoke || !window.owletDesktopSettings) return;
    try {
      desktopSettings = window.owletDesktopSettings.mount(invoke, { showPane: showSettingsPane });
    } catch (error) { desktopSettings = null; }
    if (desktopSettings && settingsBackdrop && !settingsBackdrop.hidden
        && localStorage.getItem('owletSettingsPane') === 'desktop') {
      showSettingsPane('desktop');
    }
  }
  document.querySelectorAll('.set-rail [data-pane]').forEach(function (tab) {
    tab.addEventListener('click', function () { showSettingsPane(tab.dataset.pane); });
  });
  if (settingsBackdrop) {
    settingsBackdrop.addEventListener('click', function (event) {
      if (event.target === settingsBackdrop) closeSettingsModal();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeSettingsModal();
    });
  }
  var timezonesReady = false;
  function populateTimezones() {
    var select = byId('timezoneSetting');
    if (!select || timezonesReady) return;
    timezonesReady = true;
    var zones = [];
    try { zones = Intl.supportedValuesOf('timeZone'); } catch (error) {
      zones = ['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
               'America/Anchorage', 'Pacific/Honolulu', 'Europe/London', 'Europe/Paris',
               'Europe/Berlin', 'Australia/Sydney', 'Asia/Tokyo'];
    }
    var guess = '';
    try { guess = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (error) {}
    var current = select.dataset.pendingValue || '';
    zones.forEach(function (zone) {
      var option = document.createElement('option');
      option.value = zone;
      option.textContent = zone.replace(/_/g, ' ') + (zone === guess ? ' (device)' : '');
      select.appendChild(option);
    });
    if (current) select.value = current;
  }

  // ---- device panel: static facts + signal-quality day graph ----
  var devicePanelLoaded = false;
  var sigRollups = [];
  var sigDayOffset = 0;
  function loadDevicePanel() {
    if (devicePanelLoaded) return;
    devicePanelLoaded = true;
    fetch('/api/device-info').then(function (r) { return r.json(); }).then(function (data) {
      var facts = byId('deviceFacts');
      if (!facts) return;
      var info = data.info;
      if (!info) { facts.innerHTML = '<small class="pp-hint">No device details yet — they appear after the first poll.</small>'; return; }
      var version = function (value) {
        if (value == null) return '—';
        if (typeof value === 'string' && value.charAt(0) === '{') {
          try {
            var parsed = JSON.parse(value);
            value = parsed.app || parsed.version || parsed.fw || value;
          } catch (error) {}
        }
        return String(value);
      };
      var rows = [
        ['Serial', info.device_serial || '—'],
        ['Hardware', version(info.hardware_version)],
        ['Sock firmware', version(info.sock_firmware)],
        ['Base firmware', version(info.base_firmware)],
        ['Sock radio', info.sock_mac || '—'],
        ['Base radio', info.base_mac || '—'],
        ['Captured', info.captured_at ? new Date(info.captured_at + (String(info.captured_at).includes('Z') || String(info.captured_at).includes('+') ? '' : 'Z')).toLocaleDateString() : '—']
      ];
      facts.innerHTML = rows.map(function (row) {
        return '<div class="dev-row"><span>' + row[0] + '</span><b>' + escapeHtml(String(row[1])) + '</b></div>';
      }).join('');
    }).catch(function () {});
    fetch('/api/rollups?bucket=5m&hours=192&limit=100000').then(function (r) { return r.json(); })
      .then(function (data) {
        sigRollups = (data.rollups || []).filter(function (row) { return row.avg_signal_strength != null; });
        renderSignalDay();
      }).catch(function () {});
  }
  function renderSignalDay() {
    var svg = byId('sigChart');
    if (!svg) return;
    var dayStart = new Date(); dayStart.setHours(0, 0, 0, 0);
    dayStart = dayStart.getTime() - sigDayOffset * 86400000;
    var dayEnd = dayStart + 86400000;
    var points = sigRollups.filter(function (row) {
      var t = Date.parse(row.bucket_start);
      return t >= dayStart && t < dayEnd;
    });
    var label = sigDayOffset === 0 ? 'Today'
      : sigDayOffset === 1 ? 'Yesterday'
      : new Date(dayStart).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    if (byId('sigDay')) byId('sigDay').textContent = label;
    if (byId('sigPrev')) byId('sigPrev').disabled = sigDayOffset >= 7;
    if (byId('sigNext')) byId('sigNext').disabled = sigDayOffset <= 0;
    var latest = sigRollups.length ? sigRollups[sigRollups.length - 1].avg_signal_strength : null;
    if (byId('sigNow')) byId('sigNow').textContent = latest != null ? Math.round(latest) + ' / 100' : '—';
    if (!points.length) {
      svg.innerHTML = '<text x="160" y="40" text-anchor="middle" class="sig-empty">no signal data this day</text>';
      return;
    }
    var W = 320, H = 72;
    var x = function (t) { return ((t - dayStart) / 86400000) * W; };
    var y = function (v) { return H - 4 - (Math.max(0, Math.min(100, v)) / 100) * (H - 8); };
    var segments = [], run = [];
    var last = null;
    points.forEach(function (row) {
      var t = Date.parse(row.bucket_start);
      if (last != null && t - last > 15 * 60 * 1000) { if (run.length > 1) segments.push(run); run = []; }
      run.push(x(t).toFixed(1) + ',' + y(row.avg_signal_strength).toFixed(1));
      last = t;
    });
    if (run.length > 1) segments.push(run);
    svg.innerHTML = segments.map(function (seg) {
      return '<polyline points="' + seg.join(' ') + '" class="sig-line"/>';
    }).join('') || '<text x="160" y="40" text-anchor="middle" class="sig-empty">not enough points</text>';
  }
  if (byId('sigPrev')) byId('sigPrev').addEventListener('click', function () {
    sigDayOffset = Math.min(7, sigDayOffset + 1); renderSignalDay();
  });
  if (byId('sigNext')) byId('sigNext').addEventListener('click', function () {
    sigDayOffset = Math.max(0, sigDayOffset - 1); renderSignalDay();
  });

  var shellAccounts = [];
  function shellSelectedAccount() {
    var select = byId('accountSelect');
    var id = select && select.value;
    return shellAccounts.find(function (a) { return String(a.id) === String(id); }) || shellAccounts[0] || null;
  }
  // Preferences of the account currently in view — feeds the shared oxygen
  // tier resolver so the focus modal matches the page behind it.
  function shellPrefs() {
    var account = shellSelectedAccount();
    return (account && account.dashboard_preferences) || null;
  }
  function renderShellMenu(devices) {
    var account = shellSelectedAccount();
    var device = (devices || [])[0];
    var name = account ? (account.display_name || account.email || 'Owlet profile') : 'Owlet profile';
    var babyName = account && account.dashboard_preferences && account.dashboard_preferences.baby_name;
    var deviceName = babyName || (device ? (device.baby_name || device.name || 'Owlet sock') : 'Owlet sock');
    var initials = name.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || 'O';
    ['profileAvatar', 'profileMenuAvatar'].forEach(function (id) {
      var node = byId(id); if (node) node.textContent = initials;
    });
    if (byId('profileAccountName')) byId('profileAccountName').textContent = name;
    if (byId('profileMenuTitle')) byId('profileMenuTitle').textContent = name;
    if (byId('profileDeviceName')) byId('profileDeviceName').textContent = deviceName;
    if (byId('profileMenuSubtitle')) byId('profileMenuSubtitle').textContent = deviceName;
    if (byId('pollIntervalSetting')) byId('pollIntervalSetting').value = String((account && account.poll_interval_seconds) || 5);
    if (byId('babyNameSetting') && document.activeElement !== byId('babyNameSetting')) {
      byId('babyNameSetting').value = babyName || '';
    }
    if (byId('o2AlertSetting')) {
      var threshold = account && account.dashboard_preferences && account.dashboard_preferences.o2_alert_threshold;
      byId('o2AlertSetting').value = threshold ? String(threshold) : '';
    }
    if (byId('o2WarnSetting') && document.activeElement !== byId('o2WarnSetting')
        && document.activeElement !== byId('o2CritSetting')) {
      var tiers = window.owletO2(account && account.dashboard_preferences);
      byId('o2WarnSetting').value = String(tiers.warn);
      byId('o2CritSetting').value = String(tiers.critical);
    }
    var prefs = (account && account.dashboard_preferences) || {};
    renderAlertTiming(prefs, false);
    if (byId('birthDateSetting') && document.activeElement !== byId('birthDateSetting')) {
      byId('birthDateSetting').value = prefs.birth_date || '';
      var hint = byId('birthDateHint');
      if (hint && prefs.birth_date) hint.textContent = babyAgeText(prefs.birth_date) + ' old.';
    }
    if (byId('nightStartSetting')) byId('nightStartSetting').value = prefs.night_start || '19:00';
    if (byId('nightEndSetting')) byId('nightEndSetting').value = prefs.night_end || '07:00';
    if (byId('readinessSetting')) byId('readinessSetting').value = prefs.readiness_report_time || '';
    if (byId('movementSourceSetting')) byId('movementSourceSetting').value = prefs.movement_source || 'raw';
    if (byId('o2DisplaySetting')) byId('o2DisplaySetting').value = prefs.o2_display || 'raw';
    if (byId('feedTrackingSetting')) byId('feedTrackingSetting').value = prefs.feed_tracking === false ? 'off' : 'on';
    var tzSelect = byId('timezoneSetting');
    if (tzSelect) {
      tzSelect.dataset.pendingValue = prefs.timezone || '';
      if (timezonesReady) tzSelect.value = prefs.timezone || '';
    }
  }
  function loadShellAccounts() {
    return Promise.all([
      fetch('/api/accounts').then(function (r) {
        if (r.status === 401) { window.location.href = '/login'; throw new Error('unauthenticated'); }
        return r.json();
      }),
      fetch('/api/devices').then(function (r) { return r.json(); })
    ]).then(function (results) {
      shellAccounts = results[0].accounts || [];
      var account = shellAccounts[0];
      if (account && account.poll_interval_seconds) pollInterval = account.poll_interval_seconds;
      // DST shifts and travel would silently skew the server-side prep report,
      // so keep the stored offset current whenever a report time is set.
      var accountPrefs = (account && account.dashboard_preferences) || {};
      var tzNow = -new Date().getTimezoneOffset();
      if (accountPrefs.readiness_report_time && accountPrefs.tz_offset_minutes !== tzNow) {
        patchSelectedAccount({ dashboard_preferences: { tz_offset_minutes: tzNow } });
      }
      var saved = account && account.dashboard_preferences && account.dashboard_preferences.theme;
      if (saved && saved !== currentSetting()) { localStorage.setItem('owletTheme', saved); applyTheme(saved); }
      // Pages with their own account-switching logic (the Data workbench) own
      // the select's contents; everywhere else the shell fills it.
      var select = byId('accountSelect');
      if (select && !window.__pageOwnsAccountSelect) {
        var current = select.value;
        select.innerHTML = shellAccounts.map(function (a) {
          return '<option value="' + a.id + '">' + (a.display_name || a.email) + '</option>';
        }).join('');
        if (current && shellAccounts.some(function (a) { return String(a.id) === current; })) select.value = current;
      }
      renderShellMenu(results[1].devices || []);
    }).catch(function () {});
  }
  function patchSelectedAccount(body) {
    var account = shellSelectedAccount();
    if (!account) return Promise.resolve(null);
    return fetch('/api/accounts/' + account.id, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.account) {
        var index = shellAccounts.findIndex(function (a) { return String(a.id) === String(data.account.id); });
        if (index >= 0) shellAccounts[index] = data.account;
      }
      document.dispatchEvent(new CustomEvent('owlet:prefs-changed', { detail: data.account || null }));
      return data.account;
    }).catch(function () { return null; });
  }
  if (byId('pollIntervalSetting')) byId('pollIntervalSetting').addEventListener('change', function (event) {
    patchSelectedAccount({ poll_interval_seconds: Number(event.target.value) }).then(function (account) {
      if (account && account.poll_interval_seconds) pollInterval = account.poll_interval_seconds;
    });
  });
  if (byId('babyNameSetting')) byId('babyNameSetting').addEventListener('change', function (event) {
    patchSelectedAccount({ dashboard_preferences: { baby_name: event.target.value.trim() } })
      .then(function () { loadShellAccounts(); });
  });
  function babyAgeText(iso) {
    var birth = new Date(iso + 'T00:00:00');
    if (isNaN(birth)) return '';
    var days = Math.floor((Date.now() - birth) / 86400000);
    if (days < 0) return '';
    if (days < 14) return days + (days === 1 ? ' day' : ' days');
    if (days < 8 * 7) { var w = Math.floor(days / 7); return w + (w === 1 ? ' week' : ' weeks'); }
    var months = Math.floor(days / 30.44);
    if (months < 24) return months + (months === 1 ? ' month' : ' months');
    var years = Math.floor(days / 365.25);
    return years + (years === 1 ? ' year' : ' years');
  }
  if (byId('birthDateSetting')) byId('birthDateSetting').addEventListener('change', function (event) {
    patchSelectedAccount({ dashboard_preferences: { birth_date: event.target.value || null } })
      .then(function () { loadShellAccounts(); });
  });
  // The desktop app exposes Tauri's invoke bridge (withGlobalTauri); a plain
  // browser has none, so we fall back to the web Notification API there.
  function tauriInvoke() {
    var t = window.__TAURI__;
    return (t && t.core && typeof t.core.invoke === 'function') ? t.core.invoke : null;
  }
  function ensureNotifyPermission() {
    // Windows has no per-app prompt: the native command works straight away,
    // so treat the desktop app as already granted.
    if (tauriInvoke()) return Promise.resolve('granted');
    if ('Notification' in window && Notification.permission !== 'granted') {
      return Notification.requestPermission().catch(function () { return 'denied'; });
    }
    return Promise.resolve('Notification' in window ? Notification.permission : 'denied');
  }
  function pingDevice(title, body, tag) {
    var invoke = tauriInvoke();
    if (invoke) {
      try { Promise.resolve(invoke('show_native_toast', { title: title, body: body })).catch(function () {}); }
      catch (error) { /* bridge missing at runtime */ }
      return;
    }
    if ('Notification' in window && Notification.permission === 'granted') {
      try { new Notification(title, { body: body, tag: tag }); } catch (error) { /* SW-only platforms */ }
    }
  }
  function updateNotifHint() {
    var hint = byId('o2AlertHint');
    if (!hint) return;
    if (tauriInvoke()) {
      hint.textContent = 'Crossing below rings the bell, shows a toast, and raises a Windows notification while the app is open.';
    } else if (!('Notification' in window)) {
      hint.textContent = 'This browser cannot show system notifications; alerts still ring the bell and toast in-app.';
    } else if (Notification.permission === 'granted') {
      hint.textContent = 'Crossing below rings the bell, shows a toast, and pings this device while the dashboard is open in any tab.';
    } else if (Notification.permission === 'denied') {
      hint.textContent = 'Notifications are blocked for this site — allow them in your browser settings to get device pings. In-app toasts still work.';
    } else {
      hint.textContent = 'Enabling an alert will ask permission to ping this device; the bell and in-app toasts work either way.';
    }
  }
  updateNotifHint();
  // Two colour tiers. Rather than rejecting an inverted pair we nudge the red
  // bound below the amber one — the user sees what was saved and moves on.
  function saveO2Tiers() {
    var warnEl = byId('o2WarnSetting');
    var critEl = byId('o2CritSetting');
    if (!warnEl || !critEl) return;
    var limits = window.OWLET_O2_DEFAULTS;
    var hint = byId('o2TierHint');
    var warn = Math.round(Number(warnEl.value));
    var critical = Math.round(Number(critEl.value));
    var sane = function (n) { return isFinite(n) && n >= limits.min && n <= limits.max; };
    if (!sane(warn) || !sane(critical)) {
      if (hint) hint.textContent = 'Enter whole numbers between ' + limits.min + ' and ' + limits.max + '%.';
      return;
    }
    if (critical >= warn) {
      critical = warn - 1;
      critEl.value = String(critical);
    }
    if (hint) hint.textContent = 'Oxygen: amber below ' + warn + '%, red below ' + critical + '%.';
    patchSelectedAccount({ dashboard_preferences: {
      o2_warn_threshold: warn, o2_critical_threshold: critical
    } });
  }
  ['o2WarnSetting', 'o2CritSetting'].forEach(function (id) {
    if (byId(id)) byId(id).addEventListener('change', saveO2Tiers);
  });
  // Snooze length / stale timeout / awake chime, per account. The server
  // clamps out-of-range seconds; we show whatever it saved. Unset shows the
  // server default (15 s / 60 s / off).
  var ALERT_TIMING = [
    { id: 'alertSnoozeSetting', msg: 'alertSnoozeMsg', key: 'alert_snooze_seconds', min: 5, max: 120, def: 15 },
    { id: 'alertStaleSetting', msg: 'alertStaleMsg', key: 'alert_stale_seconds', min: 30, max: 300, def: 60 }
  ];
  function savedSeconds(prefs, spec) {
    var n = Number(prefs && prefs[spec.key]);
    return (prefs && prefs[spec.key] != null && isFinite(n)) ? Math.round(n) : spec.def;
  }
  function renderAlertTiming(prefs, force) {
    (ALERT_TIMING || []).forEach(function (spec) {
      var input = byId(spec.id);
      if (input && (force || document.activeElement !== input)) input.value = String(savedSeconds(prefs, spec));
    });
    var chime = byId('awakeChimeSetting');
    if (chime) {
      chime.checked = !!(prefs && prefs.awake_chime === true);
      if (byId('awakeChimeState')) byId('awakeChimeState').textContent = chime.checked ? 'On' : 'Off';
    }
  }
  function sayIn(id, text, tone) {
    var el = byId(id);
    if (!el) return;
    el.textContent = text || '';
    el.className = 'ds-msg' + (tone ? ' ' + tone : '');
  }
  ALERT_TIMING.forEach(function (spec) {
    var input = byId(spec.id);
    if (!input) return;
    input.addEventListener('change', function () {
      var n = Math.round(Number(input.value));
      if (input.value === '' || !isFinite(n)) {
        renderAlertTiming(shellPrefs() || {}, true);
        sayIn(spec.msg, 'Enter a number of seconds from ' + spec.min + ' to ' + spec.max + '.', 'bad');
        return;
      }
      var patch = {};
      patch[spec.key] = n;
      sayIn(spec.msg, 'Saving…', '');
      patchSelectedAccount({ dashboard_preferences: patch }).then(function (account) {
        var prefs = (account && account.dashboard_preferences) || shellPrefs() || {};
        renderAlertTiming(prefs, true);
        if (!account) { sayIn(spec.msg, 'Could not save — the previous value is kept.', 'bad'); return; }
        var saved = savedSeconds(prefs, spec);
        sayIn(spec.msg, saved === n ? 'Saved: ' + saved + ' s.'
          : 'Saved as ' + saved + ' s — the allowed range is ' + spec.min + '–' + spec.max + ' s.',
          saved === n ? 'good' : '');
      });
    });
  });
  if (byId('awakeChimeSetting')) byId('awakeChimeSetting').addEventListener('change', function (event) {
    var wanted = !!event.target.checked;
    sayIn('awakeChimeMsg', 'Saving…', '');
    patchSelectedAccount({ dashboard_preferences: { awake_chime: wanted } }).then(function (account) {
      renderAlertTiming((account && account.dashboard_preferences) || shellPrefs() || {}, true);
      sayIn('awakeChimeMsg', account ? '' : 'Could not save — the previous setting is kept.', account ? '' : 'bad');
    });
  });
  if (byId('o2AlertSetting')) byId('o2AlertSetting').addEventListener('change', function (event) {
    var value = event.target.value;
    patchSelectedAccount({ dashboard_preferences: { o2_alert_threshold: value ? Number(value) : null } });
    if (value) {
      ensureNotifyPermission().then(updateNotifHint).catch(updateNotifHint);
    } else {
      updateNotifHint();
    }
  });
  ['nightStartSetting', 'nightEndSetting'].forEach(function (id) {
    var input = byId(id);
    if (!input) return;
    input.addEventListener('change', function (event) {
      if (!event.target.value) { renderShellMenu([]); return; }
      var key = id === 'nightStartSetting' ? 'night_start' : 'night_end';
      var patch = { tz_offset_minutes: -new Date().getTimezoneOffset() };
      patch[key] = event.target.value;
      patchSelectedAccount({ dashboard_preferences: patch });
    });
  });
  if (byId('readinessSetting')) byId('readinessSetting').addEventListener('change', function (event) {
    var value = event.target.value;
    patchSelectedAccount({ dashboard_preferences: {
      readiness_report_time: value || null,
      tz_offset_minutes: -new Date().getTimezoneOffset()
    } });
    if (value) { ensureNotifyPermission().catch(function () {}); }
  });
  if (byId('movementSourceSetting')) byId('movementSourceSetting').addEventListener('change', function (event) {
    patchSelectedAccount({ dashboard_preferences: { movement_source: event.target.value } });
  });
  if (byId('o2DisplaySetting')) byId('o2DisplaySetting').addEventListener('change', function (event) {
    patchSelectedAccount({ dashboard_preferences: { o2_display: event.target.value } });
  });
  if (byId('feedTrackingSetting')) byId('feedTrackingSetting').addEventListener('change', function (event) {
    patchSelectedAccount({ dashboard_preferences: { feed_tracking: event.target.value === 'on' } });
  });
  if (byId('timezoneSetting')) byId('timezoneSetting').addEventListener('change', function (event) {
    patchSelectedAccount({ dashboard_preferences: {
      timezone: event.target.value || null,
      tz_offset_minutes: -new Date().getTimezoneOffset()
    } });
  });
  if (byId('accountSelect')) byId('accountSelect').addEventListener('change', function () {
    renderShellMenu([]);
  });
  if (byId('addAccount')) byId('addAccount').addEventListener('click', function () {
    var email = prompt('Owlet email');
    if (!email) return;
    var password = prompt('Owlet password (verified once with Owlet)');
    if (!password) return;
    var region = prompt('Owlet region', 'world') || 'world';
    var displayName = prompt('Display name', email) || email;
    fetch('/api/accounts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, password: password, region: region, display_name: displayName })
    }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    }).then(function (data) {
      var event = new CustomEvent('owlet:account-added', { detail: data.account || null, cancelable: true });
      var handled = !document.dispatchEvent(event);
      if (!handled) window.location.reload();
    }).catch(function () {
      alert('Could not validate that Owlet account. Check the email/password/region and try again.');
    });
  });

  if (byId('unlinkAccount')) byId('unlinkAccount').addEventListener('click', function () {
    var account = shellSelectedAccount();
    if (!account) { alert('No account selected.'); return; }
    var label = account.email || account.display_name || 'this account';
    var typed = prompt('This unlinks ' + label + ' and permanently deletes its stored '
      + 'history. This cannot be undone.\\n\\nType the account email to confirm:');
    if (typed == null) return;
    if (typed.trim().toLowerCase() !== String(account.email || '').trim().toLowerCase()) {
      alert('That didn\\u2019t match — nothing was removed.');
      return;
    }
    var btn = byId('unlinkAccount');
    btn.disabled = true; btn.textContent = 'Unlinking…';
    fetch('/api/accounts/' + account.id, { method: 'DELETE' })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function () {
        // no accounts left -> home bounces to onboarding; otherwise refresh
        return fetch('/api/accounts').then(function (r) { return r.json(); }).then(function (data) {
          if (!(data.accounts || []).length) { window.location.href = '/'; return; }
          window.location.reload();
        });
      })
      .catch(function () {
        btn.disabled = false; btn.textContent = 'Unlink…';
        alert('Could not unlink the account — try again.');
      });
  });

  // ---- get the app (PWA install + desktop download) ----
  var deferredInstall = null;
  window.addEventListener('beforeinstallprompt', function (event) {
    event.preventDefault();
    deferredInstall = event;
  });
  var installBackdrop = byId('installBackdrop');
  function openInstallModal() {
    if (!installBackdrop) return;
    closeProfile();
    // The one-tap prompt only exists where the browser offered it; elsewhere
    // the written steps are the way in.
    var btn = byId('installPwaBtn');
    if (btn) btn.hidden = !deferredInstall;
    installBackdrop.hidden = false;
    document.body.style.overflow = 'hidden';
  }
  function closeInstallModal() {
    if (!installBackdrop || installBackdrop.hidden) return;
    installBackdrop.hidden = true;
    document.body.style.overflow = '';
  }
  if (byId('installApp')) byId('installApp').addEventListener('click', openInstallModal);
  if (byId('installClose')) byId('installClose').addEventListener('click', closeInstallModal);
  if (installBackdrop) {
    installBackdrop.addEventListener('click', function (event) {
      if (event.target === installBackdrop) closeInstallModal();
    });
  }
  if (byId('installPwaBtn')) byId('installPwaBtn').addEventListener('click', function () {
    if (!deferredInstall) return;
    deferredInstall.prompt();
    deferredInstall = null;
    closeInstallModal();
  });

  // ---- notifications: shell bell, unread badge, toasts ----
  var bellButton = byId('shellBell');
  var bellPop = byId('bellPop');
  var unreadCount = 0;
  function escapeHtml(text) {
    return String(text == null ? '' : text).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function notificationTime(iso) {
    var date = new Date(iso);
    var h = date.getHours();
    var minutes = String(date.getMinutes()).padStart(2, '0');
    var ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
    var label = h + ':' + minutes + ' ' + ap;
    if (date.toDateString() !== new Date().toDateString()) {
      label = (date.getMonth() + 1) + '/' + date.getDate() + ' ' + label;
    }
    return label;
  }
  function focusLink(notification) {
    return '/data?focus=' + encodeURIComponent(notification.recorded_at) + '&span=30'
      + '&label=' + encodeURIComponent(notification.title || 'Event');
  }
  function paintBell() {
    var badge = byId('shellBellCount');
    if (!badge) return;
    badge.hidden = unreadCount <= 0;
    badge.textContent = unreadCount > 99 ? '99+' : String(unreadCount);
  }
  function closeBell() {
    if (!bellPop) return;
    bellPop.hidden = true;
    if (bellButton) bellButton.setAttribute('aria-expanded', 'false');
  }
  function openBell() {
    closeProfile(); closeBatt();
    bellPop.hidden = false;
    bellButton.setAttribute('aria-expanded', 'true');
    byId('bellList').innerHTML = '<div class="bell-empty">Loading…</div>';
    fetch('/api/notifications?hours=168&limit=25')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var items = data.items || [];
        byId('bellList').innerHTML = items.length
          ? items.map(function (n) {
              return '<a class="bell-item ' + (n.severity || 'info') + (n.read_at ? '' : ' unread') + '"'
                + ' href="' + focusLink(n) + '">'
                + '<b>' + escapeHtml(n.title) + '</b>'
                + '<span>' + notificationTime(n.recorded_at) + ' · ' + escapeHtml(n.message) + '</span>'
                + '</a>';
            }).join('')
          : '<div class="bell-empty">Nothing yet — alerts from the sock land here.</div>';
        // Opening the panel is reading it.
        if (unreadCount > 0) fetch('/api/notifications/read', { method: 'POST' }).catch(function () {});
        unreadCount = 0;
        paintBell();
      })
      .catch(function () {
        byId('bellList').innerHTML = '<div class="bell-empty">Could not load notifications.</div>';
      });
  }
  if (bellButton && bellPop) {
    bellButton.addEventListener('click', function (event) {
      event.stopPropagation();
      if (bellPop.hidden) openBell(); else closeBell();
    });
    document.addEventListener('click', function (event) {
      if (!bellPop.hidden && !bellPop.contains(event.target) && !bellButton.contains(event.target)) closeBell();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeBell();
    });
  }

  // ---- battery: top-bar glyph + charge/drain detail popover ----
  var battButton = byId('shellBattery');
  var battPop = byId('battPop');
  var battLevel = null;
  var battMinutes = null;   // the sock's own runtime estimate (btt)
  function paintBattery() {
    if (!battButton) return;
    var pct = byId('shellBattPct'), fill = byId('shellBattFill');
    if (battLevel == null) {
      pct.textContent = '—';
      fill.style.width = '0%';
      battButton.className = 'shell-batt';
      return;
    }
    var level = Math.round(battLevel);
    pct.textContent = level + '%';
    fill.style.width = Math.max(6, Math.min(100, level)) + '%';
    battButton.className = 'shell-batt ' + (level <= 20 ? 'low' : level <= 50 ? 'mid' : 'good');
    battButton.title = 'Battery ' + level + '%';
  }
  function closeBatt() {
    if (!battPop) return;
    battPop.hidden = true;
    if (battButton) battButton.setAttribute('aria-expanded', 'false');
  }
  function openBatt() {
    closeProfile(); closeBell();
    battPop.hidden = false;
    battButton.setAttribute('aria-expanded', 'true');
    loadBattWear(battWearGroup);
    byId('battBody').innerHTML = '<div class="bell-empty">Reading the last few hours…</div>';
    fetch('/api/rollups?bucket=5m&hours=6&limit=200')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var series = (data.rollups || [])
          .map(function (row) { return { t: Date.parse(row.bucket_start), y: row.avg_battery }; })
          .filter(function (pt) { return pt.y != null; });
        if (series.length < 3) {
          byId('battBody').innerHTML = '<div class="bell-empty">Not enough recent readings to measure a trend.</div>';
          return;
        }
        // slope over the most recent ~45 minutes
        var tail = series.slice(-9);
        var hours = (tail[tail.length - 1].t - tail[0].t) / 3600000;
        var rate = hours > 0 ? (tail[tail.length - 1].y - tail[0].y) / hours : 0;
        var state = rate > 1 ? 'charging' : rate < -0.5 ? 'draining' : 'holding steady';
        var lines = [
          '<div class="batt-row"><span>Charge now</span><b>' + (battLevel != null ? Math.round(battLevel) + '%' : '—') + '</b></div>',
          '<div class="batt-row"><span>State</span><b>' + state + '</b></div>',
          '<div class="batt-row"><span>Rate</span><b>' + (rate > 0 ? '+' : '') + rate.toFixed(1) + '%/h</b></div>',
        ];
        if (battMinutes != null && battMinutes > 0 && state !== 'charging') {
          var h = Math.floor(battMinutes / 60), m = Math.round(battMinutes % 60);
          lines.push('<div class="batt-row"><span>Sock\\u2019s own estimate</span><b>~' + (h ? h + 'h ' + m + 'm' : m + 'm') + ' left</b></div>');
        }
        if (state === 'charging' && battLevel != null && rate > 0) {
          lines.push('<div class="batt-row"><span>Full in</span><b>~' + Math.max(1, Math.round(((100 - battLevel) / rate) * 60)) + ' min</b></div>');
        } else if (state === 'draining' && battLevel != null && rate < 0) {
          lines.push('<div class="batt-row"><span>Empty in</span><b>~' + (battLevel / -rate).toFixed(1) + ' h</b></div>');
        }
        byId('battBody').innerHTML = lines.join('')
          + '<p class="batt-note">Rate is measured over the last 45 minutes of readings.</p>';
      })
      .catch(function () {
        byId('battBody').innerHTML = '<div class="bell-empty">Could not load battery history.</div>';
      });
  }
  // ---- wear history: discharge rate grouped by day / week / month ----
  var battWearCache = {};
  var battWearGroup = 'day';
  function battGroupInfo(t, group) {
    if (group === 'day') {
      var d = new Date(t.getFullYear(), t.getMonth(), t.getDate());
      return { key: String(d.getTime()), label: 'SMTWTFS'[t.getDay()], order: d.getTime() };
    }
    if (group === 'week') {
      var monday = new Date(t.getFullYear(), t.getMonth(), t.getDate() - ((t.getDay() + 6) % 7));
      return { key: String(monday.getTime()), label: (monday.getMonth() + 1) + '/' + monday.getDate(), order: monday.getTime() };
    }
    var first = new Date(t.getFullYear(), t.getMonth(), 1);
    return { key: String(first.getTime()), label: first.toLocaleDateString([], { month: 'short' }), order: first.getTime() };
  }
  function renderBattWear(rollups, group) {
    var byKey = new Map();
    var prev = null;
    rollups.forEach(function (row) {
      if (row.avg_battery == null) { prev = null; return; }
      var t = new Date(row.bucket_start);
      if (prev && row.avg_battery < prev.level) {
        var info = battGroupInfo(t, group);
        var rec = byKey.get(info.key) || { drop: 0, hours: 0, label: info.label, order: info.order };
        rec.drop += prev.level - row.avg_battery;
        rec.hours += (t - prev.t) / 3600000;
        byKey.set(info.key, rec);
      }
      prev = { level: row.avg_battery, t: t };
    });
    var keep = group === 'day' ? 14 : group === 'week' ? 12 : 6;
    var list = Array.from(byKey.values())
      .filter(function (d) { return d.hours >= 2; })
      .sort(function (a, b) { return a.order - b.order; })
      .slice(-keep)
      .map(function (d) { return { label: d.label, rate: d.drop / d.hours }; });
    var wear = byId('battWear'), note = byId('battWearNote');
    if (!list.length) {
      wear.innerHTML = '<div class="bell-empty">Not enough draining time in this window yet.</div>';
      note.textContent = '';
      return;
    }
    var maxRate = Math.max.apply(null, list.map(function (d) { return d.rate; }));
    wear.innerHTML = list.map(function (d) {
      return '<div class="bwb"><b>' + d.rate.toFixed(1) + '</b>'
        + '<i style="height:' + Math.max(5, (d.rate / maxRate) * 56).toFixed(0) + 'px"></i>'
        + '<span>' + d.label + '</span></div>';
    }).join('');
    var mean = list.reduce(function (a, d) { return a + d.rate; }, 0) / list.length;
    note.textContent = 'Averaging ' + mean.toFixed(1) + '%/h while draining across '
      + list.length + ' ' + group + (list.length === 1 ? '' : 's')
      + '. A rising trend means the battery is aging.';
  }
  function loadBattWear(group) {
    battWearGroup = group;
    if (byId('battSeg')) byId('battSeg').querySelectorAll('button').forEach(function (button) {
      button.classList.toggle('active', button.dataset.group === group);
    });
    var hours = group === 'day' ? 336 : group === 'week' ? 2016 : 4380;
    if (battWearCache[group]) { renderBattWear(battWearCache[group], group); return; }
    byId('battWear').innerHTML = '<div class="bell-empty">Reading history…</div>';
    fetch('/api/rollups?bucket=30m&hours=' + hours + '&limit=100000')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        battWearCache[group] = data.rollups || [];
        if (battWearGroup === group) renderBattWear(battWearCache[group], group);
      })
      .catch(function () {
        byId('battWear').innerHTML = '<div class="bell-empty">Could not load history.</div>';
      });
  }
  if (byId('battSeg')) byId('battSeg').addEventListener('click', function (event) {
    var group = event.target.dataset && event.target.dataset.group;
    if (group) loadBattWear(group);
  });

  if (battButton && battPop) {
    battButton.addEventListener('click', function (event) {
      event.stopPropagation();
      if (battPop.hidden) openBatt(); else closeBatt();
    });
    document.addEventListener('click', function (event) {
      if (!battPop.hidden && !battPop.contains(event.target) && !battButton.contains(event.target)) closeBatt();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeBatt();
    });
  }

  // ---- focus modal: fast zoom on one moment; intercepts /data?focus= links ----
  var focusState = null;   // { center: ms, span: minutes, rows: [] }
  function closeFocus() {
    var backdrop = byId('focusBackdrop');
    if (backdrop) backdrop.hidden = true;
    focusState = null;
  }
  function focusOffline(row) {
    return !!(row.sock_disconnected || row.sock_off
      || (row.heart_rate != null && row.heart_rate <= 0)
      || (row.oxygen_saturation != null && row.oxygen_saturation <= 0));
  }
  function focusO2Zone(value) {
    var o2 = window.owletO2(shellPrefs());
    if (value == null || value <= 0) return 'var(--faint)';   // sock off the foot
    return value < o2.critical ? 'var(--bad)' : value < o2.warn ? 'var(--warn)' : 'var(--accent)';
  }
  function focusChartSvg(pts, t0, t1, height, zoneOf, isO2) {
    if (pts.length < 2) return null;
    var min = Infinity, max = -Infinity;
    pts.forEach(function (p) { if (p.y < min) min = p.y; if (p.y > max) max = p.y; });
    var loShown = min, hiShown = max;
    if (isO2) { min = Math.min(min, 91); max = Math.max(max, 99); }
    var pad = Math.max(0.5, (max - min) * 0.1); min -= pad; max += pad;
    var xOf = function (tv) { return ((tv - t0) / (t1 - t0)) * 360; };
    var yOf = function (v) { return (height - 2) - ((v - min) / (max - min)) * (height - 6); };
    var thresholds = '';
    if (isO2) {
      [window.owletO2(shellPrefs()).warn, window.owletO2(shellPrefs()).critical].forEach(function (v) {
        if (v > min && v < max) thresholds += '<line class="threshold" x1="0" x2="360" y1="' + yOf(v).toFixed(1) + '" y2="' + yOf(v).toFixed(1) + '"/>';
      });
    }
    var zone = zoneOf || function () { return 'var(--accent)'; };
    var segs = [], run = { color: zone(pts[0].y), coords: [xOf(pts[0].x).toFixed(1) + ',' + yOf(pts[0].y).toFixed(1)] };
    for (var i = 1; i < pts.length; i++) {
      var gap = pts[i].x - pts[i - 1].x > 90000;
      var color = zone(pts[i].y);
      var coord = xOf(pts[i].x).toFixed(1) + ',' + yOf(pts[i].y).toFixed(1);
      if (gap) { segs.push(run); run = { color: color, coords: [coord] }; continue; }
      run.coords.push(coord);
      if (color !== run.color) { segs.push(run); run = { color: color, coords: [coord] }; }
    }
    segs.push(run);
    var lines = segs.filter(function (s) { return s.coords.length > 1; })
      .map(function (s) { return '<polyline points="' + s.coords.join(' ') + '" style="stroke:' + s.color + '"/>'; })
      .join('');
    return {
      svg: '<svg viewBox="0 0 360 ' + height + '" preserveAspectRatio="none" aria-hidden="true">' + thresholds + lines + '</svg>',
      min: loShown, max: hiShown,
    };
  }
  function focusClock(ms) {
    var d = new Date(ms);
    var h = d.getHours(); var m = String(d.getMinutes()).padStart(2, '0');
    var ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
    return h + ':' + m + ' ' + ap;
  }
  function renderFocusCharts() {
    var rows = focusState.rows;
    var t0 = focusState.center - focusState.span * 30000;
    var t1 = focusState.center + focusState.span * 30000;
    var valid = rows.filter(function (r) { return !focusOffline(r); });
    var o2pts = valid.filter(function (r) { return r.oxygen_saturation > 0; })
      .map(function (r) { return { x: Date.parse(r.recorded_at), y: r.oxygen_saturation }; });
    var hrpts = valid.filter(function (r) { return r.heart_rate > 0; })
      .map(function (r) { return { x: Date.parse(r.recorded_at), y: r.heart_rate }; });
    var o2 = focusChartSvg(o2pts, t0, t1, 130, focusO2Zone, true);
    var hr = focusChartSvg(hrpts, t0, t1, 84, null, false);
    var html = '';
    if (o2) html += '<div class="focus-chart">' + o2.svg
      + '<span class="fc-label">Oxygen</span>'
      + '<span class="fc-ylab" style="top:3px">' + Math.round(o2.max) + '</span>'
      + '<span class="fc-ylab" style="bottom:3px">' + Math.round(o2.min) + '</span></div>';
    if (hr) html += '<div class="focus-chart">' + hr.svg
      + '<span class="fc-label">Heart rate</span>'
      + '<span class="fc-ylab" style="top:3px">' + Math.round(hr.max) + '</span>'
      + '<span class="fc-ylab" style="bottom:3px">' + Math.round(hr.min) + '</span></div>';
    if (!html) html = '<div class="focus-empty">No readings in this window — the sock or collector was off.</div>';
    else {
      // Dip-type labels snap the marker onto the lowest raw reading near the
      // center, so it sits exactly on the event regardless of the bucket
      // resolution the link came from.
      var markerFrac = 0.5;
      if (/dip|below|low/i.test(focusState.label || '')) {
        var lowest = null;
        o2pts.forEach(function (p) {
          if (Math.abs(p.x - focusState.center) <= 5 * 60000 && (lowest === null || p.y < lowest.y)) lowest = p;
        });
        if (lowest && lowest.y < window.owletO2(shellPrefs()).warn + 2) markerFrac = (lowest.x - t0) / (t1 - t0);
      }
      html += '<div class="focus-marker" style="left:' + (markerFrac * 100).toFixed(2) + '%"><span>'
        + escapeHtml(focusState.label || 'this moment') + '</span></div>';
    }
    html += '<div class="focus-xline" id="focusX" hidden></div>';
    byId('focusCharts').innerHTML = html;
    var axis = '<span>' + focusClock(t0) + '</span><span>' + focusClock(focusState.center) + '</span><span>' + focusClock(t1) + '</span>';
    var existing = document.getElementById('focusAxis');
    if (!existing) byId('focusCharts').insertAdjacentHTML('afterend', '<div class="focus-axis" id="focusAxis"></div>');
    document.getElementById('focusAxis').innerHTML = axis;
  }
  function renderFocus() {
    if (!focusState) return;
    byId('focusSeg').querySelectorAll('button').forEach(function (button) {
      button.classList.toggle('active', Number(button.dataset.span) === focusState.span);
    });
    var when = new Date(focusState.center);
    var whenText = focusClock(focusState.center) + ' · '
      + when.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    byId('focusTitle').textContent = focusState.label
      ? focusState.label + ' — ' + whenText
      : 'Around ' + whenText;
    byId('focusReadout').textContent = 'touch the chart to read a moment';
    var iso = when.toISOString();
    byId('focusDataLink').href = '/data?focus=' + encodeURIComponent(iso) + '&span=' + focusState.span;
    byId('focusCharts').innerHTML = '<div class="focus-empty">Loading…</div>';
    var requested = focusState;
    fetch('/api/readings/window?around=' + encodeURIComponent(iso) + '&span=' + focusState.span)
      .then(function (r) { return r.json(); })
      .then(function (rows) {
        if (focusState !== requested) return;   // closed or changed meanwhile
        focusState.rows = rows || [];
        renderFocusCharts();
      })
      .catch(function () {
        if (focusState === requested) byId('focusCharts').innerHTML = '<div class="focus-empty">Could not load this window.</div>';
      });
  }
  function openFocus(centerMs, spanMin, label) {
    focusState = { center: centerMs, span: spanMin, label: label || '', rows: [] };
    byId('focusBackdrop').hidden = false;
    renderFocus();
  }
  if (byId('focusBackdrop')) {
    byId('focusClose').addEventListener('click', closeFocus);
    byId('focusBackdrop').addEventListener('click', function (event) {
      if (event.target === byId('focusBackdrop')) closeFocus();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeFocus();
    });
    byId('focusSeg').addEventListener('click', function (event) {
      var span = event.target.dataset && event.target.dataset.span;
      if (!span || !focusState) return;
      focusState.span = Number(span);
      renderFocus();
    });
    byId('focusCharts').addEventListener('pointermove', focusScrub);
    byId('focusCharts').addEventListener('pointerdown', focusScrub);
    // Any focus deep-link anywhere in the app opens the modal instead of the
    // full workbench; modified clicks and the modal's own link pass through.
    document.addEventListener('click', function (event) {
      if (event.defaultPrevented || event.button !== 0
        || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      var anchor = event.target.closest && event.target.closest('a[href^="/data?focus="]');
      if (!anchor || anchor.closest('#focusBackdrop') || anchor.hasAttribute('data-workbench')) return;
      var url = new URL(anchor.getAttribute('href'), window.location.origin);
      var center = Date.parse(url.searchParams.get('focus'));
      if (!Number.isFinite(center)) return;
      event.preventDefault();
      var span = Number(url.searchParams.get('span')) || 45;
      openFocus(center, Math.min(360, Math.max(15, span)), url.searchParams.get('label') || '');
    });
  }
  function focusScrub(event) {
    if (!focusState || !focusState.rows.length) return;
    var charts = byId('focusCharts');
    var rect = charts.getBoundingClientRect();
    var frac = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    var t0 = focusState.center - focusState.span * 30000;
    var t = t0 + frac * focusState.span * 60000;
    var best = null, bestDist = Infinity;
    focusState.rows.forEach(function (row) {
      var dist = Math.abs(Date.parse(row.recorded_at) - t);
      if (dist < bestDist) { bestDist = dist; best = row; }
    });
    var x = byId('focusX');
    if (x) { x.hidden = false; x.style.left = (frac * 100) + '%'; }
    if (!best || bestDist > 90000 || focusOffline(best)) {
      byId('focusReadout').textContent = 'no reading at ' + focusClock(t);
      return;
    }
    byId('focusReadout').textContent = 'O₂ ' + Math.round(best.oxygen_saturation) + '% · '
      + Math.round(best.heart_rate) + ' bpm · ' + focusClock(Date.parse(best.recorded_at));
  }

  var TOAST_FRESH_MS = 15 * 60 * 1000;
  function showToast(notification) {
    var rack = byId('toastRack');
    if (!rack) return;
    var toast = document.createElement('a');
    toast.className = 'toast card ' + (notification.severity || 'info');
    toast.href = focusLink(notification);
    toast.innerHTML = '<b>' + escapeHtml(notification.title) + '</b>'
      + '<span>' + notificationTime(notification.recorded_at) + ' · tap to inspect</span>';
    rack.appendChild(toast);
    while (rack.children.length > 3) rack.removeChild(rack.firstChild);
    setTimeout(function () { toast.classList.add('leaving'); }, 6000);
    setTimeout(function () { toast.remove(); }, 6500);
  }
  function maybeToast(widget) {
    var latest = widget && widget.latest_notification;
    if (!latest || !latest.id || latest.read_at) return;
    var age = Date.now() - Date.parse(latest.recorded_at);
    if (!(age >= 0) || age > TOAST_FRESH_MS) return;
    // localStorage so a page switch doesn't replay the same alert
    var lastToasted = Number(localStorage.getItem('owletLastToastId') || 0);
    if (latest.id <= lastToasted) return;
    localStorage.setItem('owletLastToastId', String(latest.id));
    var wantsPing = latest.severity === 'critical' || latest.event_type === 'night_readiness';
    if (wantsPing) {
      pingDevice(
        latest.title,
        (latest.message || '') + ' · ' + notificationTime(latest.recorded_at),
        'owlet-' + latest.id
      );
    }
    if (bellPop && !bellPop.hidden) return;   // already looking at the list
    showToast(latest);
  }

  // ---- living dot: freshness of the newest reading ----
  var pollInterval = 5;
  var lastReadingAt = null;
  var sockReporting = true;
  // Dot colours live in theme.css (--live-*); read at paint time so a theme
  // switch is picked up on the next tick. Hex tokens only; fallback = old literal.
  function liveRgb(name, fb) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    var m = /^#([0-9a-f]{6})$/i.exec(v);
    var n = parseInt(m ? m[1] : fb, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function paintDot() {
    var dot = document.getElementById('shellDot');
    if (!dot) return;
    if (lastReadingAt === null) {
      dot.style.backgroundColor = '';
      dot.title = 'No readings yet';
      return;
    }
    var totalMs = pollInterval * 1000;
    var age = Date.now() - lastReadingAt;
    var intervals = age / totalMs;
    var mix = function (from, to, fade) { return Math.round(from + (to - from) * fade); };
    // The collector is alive but the sock itself has nothing to say
    // (off, charging, out of range) — amber, not green.
    if (!sockReporting && intervals <= 4) {
      dot.style.backgroundColor = 'rgb(' + liveRgb('--live-notreporting', 'f59e0b').join(',') + ')';
      dot.title = 'Sock not reporting — it may be off or charging · click to refresh';
      return;
    }
    if (intervals <= 1.5) {
      var f = intervals / 1.5;
      var c0 = liveRgb('--live-fresh', '10dc60'), c1 = liveRgb('--live-idle', '94a3b8');
      dot.style.backgroundColor = 'rgb(' + mix(c0[0], c1[0], f) + ',' + mix(c0[1], c1[1], f) + ',' + mix(c0[2], c1[2], f) + ')';
      dot.title = 'Live — data ' + Math.round(age / 1000) + 's old · click to refresh';
    } else {
      var g = Math.min(1, (intervals - 1.5) / 2.5);
      var d0 = liveRgb('--live-idle', '94a3b8'), d1 = liveRgb('--live-stale', 'ef4444');
      dot.style.backgroundColor = 'rgb(' + mix(d0[0], d1[0], g) + ',' + mix(d0[1], d1[1], g) + ',' + mix(d0[2], d1[2], g) + ')';
      dot.title = 'No new data for ' + Math.round(age / 1000) + 's · click to refresh';
    }
  }
  function paintStatus() {
    var status = byId('status');
    if (!status) return;
    var fresh = lastReadingAt !== null && (Date.now() - lastReadingAt) < pollInterval * 3000;
    var dotClass = lastReadingAt === null ? '' : (fresh ? (sockReporting ? 'good' : 'warn') : 'offline');
    var label = lastReadingAt === null
      ? 'Waiting for first reading…'
      : (fresh
        ? (sockReporting ? 'Collecting live' : 'Collector live — sock not reporting')
        : 'No new data for ' + Math.round((Date.now() - lastReadingAt) / 1000) + 's');
    if (desktopMode && fresh) label += ' — only while this PC is awake';
    status.innerHTML = '<span class="status-dot ' + dotClass + '"></span>' + label;
  }
  function pollFreshness() {
    fetch('/api/widget?hours=1').then(function (r) { return r.json(); }).then(function (widget) {
      if (widget.updated_at) lastReadingAt = Date.parse(widget.updated_at);
      sockReporting = widget.sock_reporting !== false;
      if (typeof widget.battery === 'number') { battLevel = widget.battery; paintBattery(); }
      battMinutes = typeof widget.battery_minutes === 'number' ? widget.battery_minutes : battMinutes;
      if (typeof widget.unread_notifications === 'number' && (bellPop == null || bellPop.hidden)) {
        unreadCount = widget.unread_notifications;
        paintBell();
      }
      maybeToast(widget);
      paintDot();
      paintStatus();
    }).catch(function () {});
  }
  loadShellAccounts();
  pollFreshness();
  setInterval(pollFreshness, 10000);
  setInterval(paintDot, 1000);

  // ---- desktop-mode notice: collection stops when this PC sleeps ----------
  var desktopMode = false;
  fetch('/api/health').then(function (r) { return r.json(); }).then(function (health) {
    desktopMode = !!health.desktop_mode;
    if (!desktopMode) return;
    // No login in desktop mode, so signing out, installing the app, and
    // credential settings are meaningless — hide them rather than offer a
    // door to nowhere. Give the profile menu a real exit instead: a way back
    // to the launcher to switch servers or re-pick local collection.
    document.querySelectorAll('.pp-signout').forEach(function (el) { el.hidden = true; });
    document.querySelectorAll('.pp-install').forEach(function (el) { el.hidden = true; });
    var switchBtn = document.getElementById('switchServer');
    if (switchBtn) {
      switchBtn.hidden = false;
      switchBtn.addEventListener('click', function () {
        // the launcher is always served by the local sidecar, even in remote mode
        window.location.href = 'http://127.0.0.1:8877/desktop';
      });
    }
    // Log out = disconnect the Owlet login but keep the history. There's no
    // online session here, so this is the local equivalent of signing out;
    // re-linking the same login later restores everything.
    var logoutBtn = document.getElementById('logoutOwlet');
    if (logoutBtn) {
      logoutBtn.hidden = false;
      logoutBtn.addEventListener('click', function () {
        var account = shellSelectedAccount();
        if (!account) { window.location.href = '/'; return; }
        var label = account.email || account.display_name || 'your Owlet login';
        if (!confirm('Log out of ' + label + '? Collection stops, but all of your '
          + 'history is kept — sign back in anytime to pick up where you left off.')) return;
        logoutBtn.disabled = true; logoutBtn.textContent = 'Logging out…';
        fetch('/api/accounts/' + account.id + '/disconnect', { method: 'POST' })
          .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
          // Reset the local/remote choice so we land on the launcher, not the
          // local link page — logging out should let you re-pick how to connect.
          .then(function () { return fetch('/desktop/reset', { method: 'POST' }).catch(function () {}); })
          .then(function () { window.location.href = 'http://127.0.0.1:8877/desktop'; })
          .catch(function () {
            logoutBtn.disabled = false; logoutBtn.textContent = 'Log out';
            alert('Could not log out — try again.');
          });
      });
    }
    var signinTab = document.querySelector('.set-rail [data-pane="signin"]');
    if (signinTab) signinTab.hidden = true;
    if (localStorage.getItem('owletSettingsPane') === 'signin') {
      localStorage.setItem('owletSettingsPane', 'baby');
    }
    // The "This PC" settings tab exists only here: desktop mode AND the Tauri
    // bridge. Hosted pages never get the tab, the pane, or an invoke call.
    mountDesktopSettings();
    if (localStorage.getItem('owletDesktopNoticeAck') === '1') return;
    var main = document.querySelector('.shell-main');
    if (!main) return;
    var notice = document.createElement('div');
    notice.className = 'desktop-notice';
    notice.innerHTML = '<b>Desktop app:</b> readings are collected only while this window is '
      + 'open and the PC is awake — a closed laptop or sleeping PC leaves permanent gaps '
      + '(Owlet cannot backfill). If you already run the Docker server version, use its web '
      + 'page instead of this app: two collectors on one Owlet login sign each other out '
      + 'and BOTH end up with gaps. '
      + '<button type="button" id="desktopNoticeAck">Got it</button>';
    main.parentNode.insertBefore(notice, main);
    document.getElementById('desktopNoticeAck').addEventListener('click', function () {
      localStorage.setItem('owletDesktopNoticeAck', '1');
      notice.remove();
    });
  }).catch(function () {});

  // ---- armed chip: desktop app only ---------------------------------------
  // Asks the desktop shell (Tauri command `armed_state`) whether THIS PC's
  // alert path is working: server reachable, data fresh, audio audible, PC
  // kept awake. It never says anything about the baby. Contract:
  //   { armed: bool, problems: [{ code, text }] }
  //   codes: server, not_collecting, stale, no_audio, muted, sleep, watchdog
  // A hosted page has no Tauri bridge and gets no chip and no placeholder.
  // If the command is missing or fails (an older shell), the chip stays
  // hidden. Once it HAS answered, a shell that goes quiet shows "unknown"
  // in amber: a stale "Alerts armed" would be a false reassurance.
  (function () {
    var invoke = tauriInvoke();
    if (!invoke) return;
    var POLL_MS = 5000, QUIET_MS = 15000;
    var chip = null, everOk = false, lastOkAt = 0, pendingSince = 0, shown = '';
    function ensureChip() {
      if (chip) return chip;
      var top = document.querySelector('.shell-top');
      if (!top) return null;
      var side = byId('profileMenuWrap');
      chip = document.createElement('span');
      chip.id = 'armedChip';
      chip.setAttribute('role', 'status');
      var dot = document.createElement('i');
      dot.className = 'ac-dot';
      dot.setAttribute('aria-hidden', 'true');
      var text = document.createElement('span');
      text.className = 'ac-text';
      chip.appendChild(dot);
      chip.appendChild(text);
      top.insertBefore(chip, side && side.parentNode === top ? side : null);
      return chip;
    }
    // kind: 'armed' | 'fault' | 'info'. The words always carry the state;
    // colour and dot shape only repeat it.
    function show(kind, text, title) {
      var el = ensureChip();
      if (!el) return;
      var key = kind + '|' + text + '|' + title;
      el.hidden = false;
      if (key === shown) return;   // unchanged: don't re-announce every 5 s
      shown = key;
      el.className = 'armed-chip ' + kind;
      el.title = title;
      el.querySelector('.ac-text').textContent = text;
    }
    function hide() { if (chip) chip.hidden = true; shown = ''; }
    function render(state) {
      if (!state || typeof state.armed !== 'boolean') return false;
      var problems = (Array.isArray(state.problems) ? state.problems : []).filter(function (p) {
        return p && typeof p.text === 'string' && p.text;
      });
      // not_collecting (sock off, no account linked, viewing a remote server)
      // is "not monitoring", not a fault. Anything else is a fault and wins.
      var faults = problems.filter(function (p) { return p.code !== 'not_collecting'; });
      var all = problems.map(function (p) { return p.text; }).join(' · ');
      if (faults.length || (!state.armed && !problems.length)) {
        show('fault', faults.length ? faults[0].text : 'Alerts not armed',
          'This PC may not sound an alert' + (all ? ': ' + all : '.'));
      } else if (problems.length) {
        show('info', problems[0].text, 'Not monitoring on this PC: ' + all);
      } else {
        show('armed', 'Alerts armed',
          'This PC\\'s alert path is working. It says nothing about your baby, '
          + 'only that this PC can raise an alert.');
      }
      return true;
    }
    function settle(ok) {
      pendingSince = 0;
      if (ok) { everOk = true; lastOkAt = Date.now(); }
      else if (!everOk) hide();
    }
    function tick() {
      var now = Date.now();
      if (everOk && now - lastOkAt > QUIET_MS) {
        show('fault', 'Alert status unknown',
          'The desktop app has stopped reporting whether this PC can sound an alert.');
      }
      if (pendingSince && now - pendingSince < QUIET_MS) return;   // still waiting
      pendingSince = now;
      var call;
      try { call = Promise.resolve(invoke('armed_state')); }
      catch (error) { call = Promise.reject(error); }
      call.then(function (state) { settle(render(state)); }, function () { settle(false); });
    }
    tick();
    setInterval(tick, POLL_MS);
  })();

  // Warm the other tabs into the service-worker cache so the first switch
  // paints instantly too.
  setTimeout(function () {
    if (!('serviceWorker' in navigator) || !navigator.serviceWorker.controller) return;
    ['/', '/night', '/rhythms', '/data'].forEach(function (path) {
      if (window.location.pathname !== path) fetch(path, { credentials: 'same-origin' }).catch(function () {});
    });
  }, 2500);
  var dot = document.getElementById('shellDot');
  if (dot) dot.addEventListener('click', function () { window.location.reload(); });
})();
</script>"""


# Small layout rules for the alert settings rows and the desktop-only "This PC"
# pane. Theme tokens only, so both themes follow theme.css.
SETTINGS_CSS = """<style>
.ds-ctl { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.ds-inline { display: flex; align-items: center; gap: 8px; min-width: 0; }
.ds-inline > input[type="number"] { flex: 1; min-width: 0; }
.ds-msg { display: block; font-size: 12px; line-height: 1.4; color: var(--dim); }
.ds-msg.bad { color: var(--bad); }
.ds-msg.good { color: var(--good); }
.set-row .ds-switch { display: flex; align-items: center; gap: 10px; cursor: pointer;
  font-size: 13px; font-weight: 600; color: var(--dim); }
.set-row .ds-switch input { width: 18px; height: 18px; margin: 0; padding: 0; flex: none;
  accent-color: var(--accent); cursor: pointer; }
.set-row input[type="range"] { flex: 1; min-width: 0; padding: 0; border: 0; background: none;
  accent-color: var(--accent); }
.set-row input:disabled, .set-row select:disabled { opacity: .5; cursor: default; }
.ds-out { flex: none; min-width: 4ch; text-align: right; font-size: 13px; font-weight: 600;
  font-variant-numeric: tabular-nums; }
.ds-note { font-size: 12.5px; line-height: 1.5; color: var(--dim); margin: 10px 0 4px;
  padding: 10px 14px; border-radius: 18px; background: var(--surface-inset);
  border: 1px solid var(--surface-line); }
.ds-note b { color: var(--ink); }
.ds-card { display: grid; gap: 10px; margin: 12px 0 4px; padding: 14px;
  border-radius: 18px; background: var(--surface-inset); border: 1px solid var(--surface-line); }
.ds-card[hidden], .set-row[hidden], .ds-capture[hidden] { display: none; }
.ds-card > b { font-size: 13.5px; }
.ds-card .pp-link-btn { text-align: center; justify-self: start; }
.ds-card .pp-link-btn:disabled { opacity: .5; cursor: default; }
.ds-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.ds-btn { all: unset; box-sizing: border-box; cursor: pointer; text-align: center;
  padding: 8px 12px; border-radius: var(--radius-control); border: 1px solid var(--surface-line);
  color: var(--dim); font-size: 13px; font-weight: 600; }
.ds-btn:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
.ds-btn:disabled { opacity: .5; cursor: default; }
.ds-capture { all: unset; box-sizing: border-box; cursor: pointer; flex: 1; min-width: 0;
  padding: 8px 14px; border-radius: var(--radius-control); border: 1px solid var(--surface-line);
  background: var(--surface-inset); color: var(--ink); font-size: 13.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ds-capture.unset { color: var(--dim); }
.ds-capture.armed { color: var(--accent); border-color: var(--accent); }
.ds-capture:disabled { opacity: .5; cursor: default; }
.ds-btn:focus-visible, .ds-capture:focus-visible, .ds-card .pp-link-btn:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px; }
.ds-group { margin: 22px 0 0; }
.ds-group + .set-lede { margin-top: 2px; }
</style>"""

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
          checking”, and the game still receives the press. Xbox-style (XInput) controllers only;
          PlayStation and Switch pads need Steam Input.</small></div>
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
DESKTOP_SETTINGS_JS = r"""<script>
(function () {
  'use strict';
  // Contract defaults (docs/orchestration-v0.2.md section 4). Shown whenever a
  // key is missing, never sent on their own.
  var DEFAULTS = {
    flash_target: 'secondary', alarm_sound: 'beep', alarm_device: 'default',
    unmute_on_red: true, alarm_volume: 60,
    hotkey_snooze: null, hotkey_acknowledge: null, hotkey_widget: null, hotkey_click_through: null,
    controller_mode: 'off', controller_buttons: null,
    autostart: false, keep_awake: true, game_mode: 'auto', capture_exclusion: false,
    first_run_test_done: false
  };
  var HOTKEY_KEYS = ['hotkey_snooze', 'hotkey_acknowledge', 'hotkey_widget', 'hotkey_click_through'];
  var SWITCH_KEYS = ['unmute_on_red', 'autostart', 'keep_awake', 'capture_exclusion'];
  var VOLUME_MIN = 20, VOLUME_MAX = 100;

  // ---- hotkey strings: Ctrl, Alt, Shift, Super, then KeyboardEvent.code ----
  var MODIFIER_CODE = /^(Control|Alt|Shift|Meta|OS)(Left|Right)?$|^AltGraph$/;
  var MODIFIER_KEY = /^(Control|Alt|Shift|Meta|OS|AltGraph|Super|Hyper)$/;
  var FUNCTION_KEY = /^F([1-9]|1[0-9]|2[0-4])$/;
  // Returns {combo} | {modifierOnly: true} | {error}.
  function hotkeyFromEvent(event) {
    var code = String((event && event.code) || '');
    var key = String((event && event.key) || '');
    if (MODIFIER_CODE.test(code) || MODIFIER_KEY.test(key)) return { modifierOnly: true };
    if (!code || code === 'Unidentified') return { error: 'That key can’t be used for a hotkey.' };
    var mods = [];
    if (event.ctrlKey) mods.push('Ctrl');
    if (event.altKey) mods.push('Alt');
    if (event.shiftKey) mods.push('Shift');
    if (event.metaKey) mods.push('Super');
    if (!mods.length && !FUNCTION_KEY.test(code)) {
      return { error: 'Add Ctrl, Alt, Shift or Win. Only F1–F24 can be used on their own.' };
    }
    return { combo: mods.concat([code]).join('+') };
  }
  var MODIFIER_NAMES = { Ctrl: 'Ctrl', Alt: 'Alt', Shift: 'Shift', Super: 'Win' };
  var KEY_NAMES = {
    Backquote: '`', Minus: '-', Equal: '=', BracketLeft: '[', BracketRight: ']',
    Backslash: '\\', IntlBackslash: '\\', Semicolon: ';', Quote: "'", Comma: ',', Period: '.',
    Slash: '/', Space: 'Space', Enter: 'Enter', Tab: 'Tab', Backspace: 'Backspace',
    Delete: 'Delete', Insert: 'Insert', Home: 'Home', End: 'End', PageUp: 'Page Up',
    PageDown: 'Page Down', ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left',
    ArrowRight: 'Right', Pause: 'Pause', PrintScreen: 'Print Screen', ScrollLock: 'Scroll Lock',
    CapsLock: 'Caps Lock', NumLock: 'Num Lock', ContextMenu: 'Menu', Escape: 'Esc',
    NumpadAdd: 'Num +', NumpadSubtract: 'Num -', NumpadMultiply: 'Num *',
    NumpadDivide: 'Num /', NumpadDecimal: 'Num .', NumpadEnter: 'Num Enter'
  };
  function keyName(code) {
    if (Object.prototype.hasOwnProperty.call(KEY_NAMES, code)) return KEY_NAMES[code];
    var m = /^Key([A-Z])$/.exec(code) || /^Digit([0-9])$/.exec(code);
    if (m) return m[1];
    m = /^Numpad([0-9])$/.exec(code);
    if (m) return 'Num ' + m[1];
    return code;
  }
  function hotkeyLabel(combo) {
    if (!combo) return 'Not set';
    var parts = String(combo).split('+');
    return parts.map(function (part, i) {
      return i < parts.length - 1 ? (MODIFIER_NAMES[part] || part) : keyName(part);
    }).join(' + ');
  }

  // ---- XInput button mask -> names ----
  var XINPUT_BUTTONS = [
    [0x0001, 'D-pad up'], [0x0002, 'D-pad down'], [0x0004, 'D-pad left'], [0x0008, 'D-pad right'],
    [0x0010, 'Menu (Start)'], [0x0020, 'View (Back)'],
    [0x0040, 'Left stick click'], [0x0080, 'Right stick click'],
    [0x0100, 'LB'], [0x0200, 'RB'],
    [0x1000, 'A'], [0x2000, 'B'], [0x4000, 'X'], [0x8000, 'Y']
  ];
  function buttonNames(mask) {
    mask = Number(mask);
    if (!isFinite(mask) || mask <= 0) return [];
    mask = mask & 0xFFFF;
    var names = [], known = 0;
    XINPUT_BUTTONS.forEach(function (b) {
      known |= b[0];
      if (mask & b[0]) names.push(b[1]);
    });
    for (var bit = 1; bit <= 0x8000; bit <<= 1) {
      if ((mask & bit) && !(known & bit)) names.push('Button 0x' + ('000' + bit.toString(16).toUpperCase()).slice(-4));
    }
    return names;
  }
  function validMask(value) {
    var n = Number(value);
    return (isFinite(n) && Math.floor(n) === n && n > 0 && n <= 0xFFFF) ? n : 0;
  }

  // A rejected invoke usually means this build lacks the command.
  function invokeProblem(error, fallback) {
    var text = String((error && (error.message || error)) || '');
    if (/not found|not allowed|unknown command|denied|permission/i.test(text) || !text) return fallback;
    return fallback + ' (' + text.slice(0, 120) + ')';
  }

  function mount(invoke, hooks) {
    var tpl = document.getElementById('desktopSettingsTpl');
    var rail = document.querySelector('.set-rail');
    var panes = document.querySelector('.set-panes');
    if (!tpl || !tpl.content || !rail || !panes) return null;
    if (panes.querySelector('.set-pane[data-pane="desktop"]')) return null;
    var fragment = tpl.content.cloneNode(true);
    var tab = fragment.querySelector('[role="tab"][data-pane="desktop"]');
    var pane = fragment.querySelector('.set-pane[data-pane="desktop"]');
    rail.appendChild(tab);
    panes.appendChild(pane);
    tab.addEventListener('click', function () { if (hooks && hooks.showPane) hooks.showPane('desktop'); });

    var q = function (sel) { return pane.querySelector(sel); };
    var qa = function (sel) { return Array.prototype.slice.call(pane.querySelectorAll(sel)); };
    var settings = null;        // last object the shell returned; never edited locally
    var loadFailed = false;
    var loading = null;
    var monitors = null, devices = null;
    var pending = {};           // field -> true while a save is in flight
    var capturing = null;       // hotkey key being captured
    var controllerCapture = null;
    var tested = { amber: false, red: false };

    function call(name, args) {
      try { return Promise.resolve(args === undefined ? invoke(name) : invoke(name, args)); }
      catch (error) { return Promise.reject(error); }
    }
    function get(key) {
      var value = settings ? settings[key] : undefined;
      return (value === undefined || value === null) ? DEFAULTS[key] : value;
    }
    function msgEl(field) { return q('[data-msg="' + field + '"]'); }
    function say(field, text, tone) {
      var el = typeof field === 'string' ? msgEl(field) : field;
      if (!el) return;
      el.textContent = text || '';
      el.className = 'ds-msg' + (tone ? ' ' + tone : '');
    }

    // ---- render everything from the last saved settings ----
    function fillSelect(select, options, current, missingLabel) {
      if (!select) return;
      var list = options.slice();
      var present = list.some(function (o) { return o[0] === current; });
      if (!present && current != null && current !== '') list.push([current, missingLabel]);
      select.textContent = '';
      list.forEach(function (o) {
        var option = document.createElement('option');
        option.value = o[0];
        option.textContent = o[1];
        select.appendChild(option);
      });
      select.value = current;
    }
    function render() {
      var ready = !!settings && !loadFailed;
      var showFirstRun = ready && get('first_run_test_done') !== true;
      q('#dsFirstRun').hidden = !showFirstRun;
      q('#dsTestRow').hidden = showFirstRun;
      q('#dsFirstRunDone').disabled = !(tested.amber && tested.red) || !!pending.first_run_test_done;

      fillSelect(q('#dsFlashTarget'), [
        ['secondary', 'Secondary monitor'], ['primary_border', 'Primary monitor border'],
        ['all', 'All monitors']
      ].concat((monitors || []).filter(function (m) { return m && m.id != null; }).map(function (m) {
        return [String(m.id), String(m.name || m.id)];
      })), String(get('flash_target')), 'Saved display (not connected)');
      fillSelect(q('#dsAlarmSound'), [['beep', 'Beep'], ['two_tone', 'Two-tone'], ['pulse', 'Pulse']],
        String(get('alarm_sound')), 'Saved sound (' + get('alarm_sound') + ')');
      fillSelect(q('#dsAlarmDevice'), [['default', 'Default device'], ['all', 'All outputs']]
        .concat((devices || []).filter(function (d) { return d && d.id != null; }).map(function (d) {
          return [String(d.id), String(d.name || d.id)];
        })), String(get('alarm_device')), 'Saved device (not connected)');
      fillSelect(q('#dsGameMode'), [['auto', 'Automatic'], ['always', 'Always'], ['never', 'Never']],
        String(get('game_mode')), 'Saved (' + get('game_mode') + ')');

      SWITCH_KEYS.forEach(function (key) {
        var input = q('input[data-key="' + key + '"]');
        input.checked = get(key) === true;
        q('[data-state="' + key + '"]').textContent = input.checked ? 'On' : 'Off';
      });

      var volume = q('#dsAlarmVolume');
      var level = Math.min(VOLUME_MAX, Math.max(VOLUME_MIN, Math.round(Number(get('alarm_volume')) || DEFAULTS.alarm_volume)));
      volume.value = String(level);
      q('#dsAlarmVolumeOut').textContent = level + '%';

      HOTKEY_KEYS.forEach(function (key) {
        var button = q('[data-hotkey="' + key + '"]');
        var combo = get(key);
        var name = button.getAttribute('data-name');
        if (capturing === key) {
          button.textContent = 'Press keys… (Esc cancels)';
          button.className = 'ds-capture armed';
          button.setAttribute('aria-label', name + ' hotkey: waiting for keys. Esc cancels.');
        } else {
          button.textContent = hotkeyLabel(combo);
          button.className = 'ds-capture' + (combo ? '' : ' unset');
          button.setAttribute('aria-label', name + ' hotkey: ' + hotkeyLabel(combo) + '. Press to change.');
        }
        button.disabled = !ready || !!pending[key];
        q('[data-clear="' + key + '"]').disabled = !ready || !combo || !!pending[key] || capturing === key;
      });

      // Only "off" and "chosen" exist in v0.2. Anything else (an old "any")
      // is shown as Off; nothing is sent until the user changes it.
      var mode = String(get('controller_mode')) === 'chosen' ? 'chosen' : 'off';
      var mask = validMask(get('controller_buttons'));
      var select = q('#dsControllerMode');
      select.value = controllerCapture ? 'chosen' : mode;
      var captureBtn = q('#dsControllerCapture');
      captureBtn.hidden = !(controllerCapture || mode === 'chosen');
      captureBtn.disabled = !ready || !!controllerCapture || !!pending.controller_mode;
      if (!controllerCapture) captureBtn.textContent = mask ? 'Change button' : 'Press a button';
      q('#dsControllerSaved').textContent = (mode === 'chosen' && mask)
        ? 'Snooze button: ' + buttonNames(mask).join(' + ') : '';

      qa('[data-key]').forEach(function (el) {
        var key = el.getAttribute('data-key');
        el.disabled = !ready || !!pending[key]
          || (key === 'alarm_volume' && get('unmute_on_red') !== true)
          || (key === 'controller_mode' && !!controllerCapture);
      });
    }

    // ---- load ----
    function refresh() {
      if (loading || capturing || controllerCapture || Object.keys(pending).length) return loading;
      var settingsCall = call('get_desktop_settings');
      var monitorsCall = call('list_monitors').then(function (v) { return Array.isArray(v) ? v : null; },
        function () { return null; });
      var devicesCall = call('list_audio_devices').then(function (v) { return Array.isArray(v) ? v : null; },
        function () { return null; });
      loading = Promise.all([
        settingsCall.then(function (v) { return { ok: true, value: v }; },
          function (error) { return { ok: false, error: error }; }),
        monitorsCall, devicesCall
      ]).then(function (results) {
        monitors = results[1];
        devices = results[2];
        var got = results[0];
        if (got.ok && got.value && typeof got.value === 'object' && !got.value.error) {
          settings = got.value;
          loadFailed = false;
          say(q('#dsLoadMsg'), '');
        } else {
          loadFailed = true;
          say(q('#dsLoadMsg'), got.ok
            ? 'This PC’s settings couldn’t be read. Close and reopen Settings to try again.'
            : invokeProblem(got.error, 'This PC’s settings aren’t available in this version of the app.'), 'bad');
        }
        say('flash_target', monitors ? '' : 'Couldn’t list displays in this version; the choices above still work.');
        say('alarm_device', devices ? '' : 'Couldn’t list audio devices in this version; the choices above still work.');
        render();
      }).then(function () { loading = null; }, function () { loading = null; });
      return loading;
    }

    // ---- save: one patch per user action; re-render from what the shell returns ----
    function save(patch, field) {
      if (!settings || loadFailed) return Promise.resolve(false);
      pending[field] = true;
      say(field, 'Saving…');
      render();
      return call('set_desktop_settings', { patch: patch }).then(function (result) {
        if (result && typeof result === 'object' && result.error) {
          var errField = result.error.field;
          var target = (errField && errField !== field && msgEl(errField)) ? errField : field;
          if (target !== field) say(field, '');
          say(target, String(result.error.message || 'That setting wasn’t saved.'), 'bad');
          return false;
        }
        if (!result || typeof result !== 'object') {
          say(field, 'The app didn’t confirm the change; the saved setting is shown.', 'bad');
          return false;
        }
        settings = result;
        say(field, 'Saved.', 'good');
        return true;
      }, function (error) {
        say(field, invokeProblem(error, 'This setting isn’t available in this version of the app.'), 'bad');
        return false;
      }).then(function (ok) {
        delete pending[field];
        render();
        return ok;
      });
    }
    function saveOne(key, value) {
      var patch = {};
      patch[key] = value;
      return save(patch, key);
    }

    ['#dsFlashTarget', '#dsAlarmSound', '#dsAlarmDevice', '#dsGameMode'].forEach(function (sel) {
      var select = q(sel);
      select.addEventListener('change', function () { saveOne(select.getAttribute('data-key'), select.value); });
    });
    SWITCH_KEYS.forEach(function (key) {
      var input = q('input[data-key="' + key + '"]');
      input.addEventListener('change', function () { saveOne(key, input.checked === true); });
    });
    var volumeInput = q('#dsAlarmVolume');
    volumeInput.addEventListener('input', function () {
      q('#dsAlarmVolumeOut').textContent = volumeInput.value + '%';
    });
    // Saved on release ('change'), never on every input event; always 20-100.
    volumeInput.addEventListener('change', function () {
      var n = Math.round(Number(volumeInput.value));
      if (!isFinite(n)) { render(); return; }
      n = Math.min(VOLUME_MAX, Math.max(VOLUME_MIN, n));
      if (n === Number(get('alarm_volume'))) { render(); return; }
      saveOne('alarm_volume', n);
    });

    // ---- hotkey capture ----
    function stopHotkeyCapture() {
      window.removeEventListener('keydown', onCaptureKey, true);
      capturing = null;
      render();
    }
    function startHotkeyCapture(key) {
      if (capturing) stopHotkeyCapture();
      capturing = key;
      window.addEventListener('keydown', onCaptureKey, true);
      say(key, 'Press the key combination now.');
      render();
    }
    function onCaptureKey(event) {
      var key = capturing;
      if (!key) return;
      if (!pane.offsetParent) { say(key, ''); stopHotkeyCapture(); return; }   // modal closed
      // Plain Tab / Shift+Tab leaves the field so keyboard users are never trapped.
      if (event.key === 'Tab' && !event.ctrlKey && !event.altKey && !event.metaKey) {
        say(key, 'Cancelled; the hotkey is unchanged.');
        stopHotkeyCapture();
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      if (event.key === 'Escape') {
        say(key, 'Cancelled; the hotkey is unchanged.');
        stopHotkeyCapture();
        return;
      }
      if (event.repeat) return;
      var result = hotkeyFromEvent(event);
      if (result.modifierOnly) return;
      if (result.error) { say(key, result.error, 'bad'); return; }
      stopHotkeyCapture();
      if (result.combo === get(key)) { say(key, 'That is already the ' + hotkeyName(key) + ' hotkey.'); return; }
      saveOne(key, result.combo).then(function (ok) {
        if (ok) say(key, 'Saved: ' + hotkeyLabel(get(key)) + '.', 'good');
      });
    }
    function hotkeyName(key) { return q('[data-hotkey="' + key + '"]').getAttribute('data-name'); }
    HOTKEY_KEYS.forEach(function (key) {
      var button = q('[data-hotkey="' + key + '"]');
      button.addEventListener('click', function () {
        if (capturing === key) { say(key, 'Cancelled; the hotkey is unchanged.'); stopHotkeyCapture(); return; }
        startHotkeyCapture(key);
      });
      button.addEventListener('blur', function () {
        if (capturing === key) { say(key, ''); stopHotkeyCapture(); }
      });
      q('[data-clear="' + key + '"]').addEventListener('click', function () {
        if (!get(key)) return;
        saveOne(key, null).then(function (ok) { if (ok) say(key, 'Cleared.', 'good'); });
      });
    });

    // ---- controller snooze ----
    var CAPTURE_SECONDS = 10;
    function startControllerCapture() {
      if (controllerCapture || !settings || loadFailed) return;
      var state = { left: CAPTURE_SECONDS, done: false, timer: null, guard: null };
      controllerCapture = state;
      var button = q('#dsControllerCapture');
      function tickText() {
        button.textContent = 'Waiting… (' + state.left + ' s)';
        say('controller_mode', 'Press a button or combination on your controller… (' + state.left + ' s)');
      }
      render();
      tickText();
      state.timer = setInterval(function () { state.left = Math.max(0, state.left - 1); tickText(); }, 1000);
      function finish(outcome, value) {
        if (state.done) return;
        state.done = true;
        clearInterval(state.timer);
        clearTimeout(state.guard);
        controllerCapture = null;
        if (outcome === 'missing') {
          render();
          say('controller_mode', 'Controller capture isn’t available in this version.', 'bad');
          return;
        }
        var mask = validMask(value && value.buttons);
        if (!mask) {
          render();
          say('controller_mode', 'No controller press seen. Nothing was changed.');
          return;
        }
        save({ controller_mode: 'chosen', controller_buttons: mask }, 'controller_mode').then(function (ok) {
          if (ok) say('controller_mode', 'Saved: ' + buttonNames(get('controller_buttons')).join(' + ') + '.', 'good');
        });
      }
      // The shell answers within 10 s; the guard only unsticks a shell that never does.
      state.guard = setTimeout(function () { finish('timeout', null); }, (CAPTURE_SECONDS + 5) * 1000);
      call('capture_controller_button').then(function (value) { finish('result', value); },
        function () { finish('missing', null); });
    }
    var controllerSelect = q('#dsControllerMode');
    controllerSelect.addEventListener('change', function () {
      var value = controllerSelect.value;
      if (value === 'chosen') {
        if (validMask(get('controller_buttons'))) { saveOne('controller_mode', 'chosen'); return; }
        // "chosen" without a button is rejected by the shell: capture one first.
        startControllerCapture();
        return;
      }
      saveOne('controller_mode', 'off');
    });
    q('#dsControllerCapture').addEventListener('click', startControllerCapture);

    // ---- test alerts (server route; same-origin POST like the rest of the page) ----
    function testAlert(level, button) {
      var box = button.closest('.ds-card, .set-row');
      var out = box && box.querySelector('[data-msg="test"]');
      var name = level === 'red' ? 'Red' : 'Amber';
      say(out, 'Sending ' + name.toLowerCase() + ' test…');
      button.disabled = true;
      fetch('/api/alert/test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ level: level, seconds: 20 })
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Failed (' + r.status + ')');
          return data;
        });
      }).then(function () {
        tested[level] = true;
        say(out, name + ' test sent. It runs for 20 seconds.', 'good');
      }, function (error) {
        say(out, name + ' test failed: ' + (error && error.message ? error.message : 'no answer from the server') + '.', 'bad');
      }).then(function () {
        button.disabled = false;
        render();
      });
    }
    qa('[data-test]').forEach(function (button) {
      button.addEventListener('click', function () { testAlert(button.getAttribute('data-test'), button); });
    });
    q('#dsFirstRunDone').addEventListener('click', function () {
      saveOne('first_run_test_done', true).then(function (ok) {
        if (ok) say(q('#dsTestRow [data-msg="test"]'), 'Thanks. The test buttons stay here whenever you need them.', 'good');
      });
    });

    render();
    return { refresh: refresh };
  }

  window.owletDesktopSettings = {
    mount: mount,
    hotkeyFromEvent: hotkeyFromEvent,
    hotkeyLabel: hotkeyLabel,
    buttonNames: buttonNames,
    DEFAULTS: DEFAULTS
  };
})();
</script>"""


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
          <a class="install-cta" href="https://github.com/pbozzay/owlet-dashboard/releases/latest"
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
