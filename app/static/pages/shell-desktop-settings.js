
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

  // ---- Button mask (XInput layout; PlayStation pads are mapped onto it) -> names ----
  var XINPUT_BUTTONS = [
    [0x0001, 'D-pad up'], [0x0002, 'D-pad down'], [0x0004, 'D-pad left'], [0x0008, 'D-pad right'],
    [0x0010, 'Menu / Options'], [0x0020, 'View / Share'],
    [0x0040, 'Left stick click / L3'], [0x0080, 'Right stick click / R3'],
    [0x0100, 'LB / L1'], [0x0200, 'RB / R1'], [0x0400, 'PS button'], [0x0800, 'Touchpad click'],
    [0x1000, 'A / Cross'], [0x2000, 'B / Circle'], [0x4000, 'X / Square'], [0x8000, 'Y / Triangle']
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
