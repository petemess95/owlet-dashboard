// Run with: node --test tests/widget_alerts.test.cjs
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const script = (path) => readFileSync(resolve(__dirname, '..', path), 'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1];
const flush = () => new Promise(setImmediate);
const device = (changes = {}) => ({
  level: 'red', spo2: 84, heart_rate: 130, sock_reporting: true, stale: false,
  signal_lost: false, warn_threshold: 90, critical_threshold: 86,
  account_name: 'Baby', age_seconds: 0, ...changes,
});
const state = (changes = {}) => ({
  level: 'red', active_level: 'red', collecting: true, snoozed: false,
  snooze_seconds_left: 0, signal_lost: false, devices: [device()], ...changes,
});

const SERIES = {
  device_serial: 'SOCK1', warn_threshold: 90, critical_threshold: 86,
  points: [{ t: '2027-01-15T08:00:00Z', spo2: 97 }],
};

// armed: undefined -> the command answers nothing (an older shell); an object
// -> that answer; 'hang' -> never settles; 'fail' -> rejects.
function page(path, {
  desktop = false, initial = state(), viewport = null, search = '', armed, series = SERIES,
} = {}) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      className: '', textContent: '', innerHTML: '', hidden: false, title: '',
      style: { setProperty() {} },
      setAttribute(name, value) { this[`attr:${name}`] = String(value); },
    });
    return elements.get(id);
  };
  const intervals = new Map();
  const calls = [];
  let response = initial;
  let armedAnswer = armed;
  let failure = false;
  let overlayListener;
  let resizeListener = () => {};
  const docListeners = {};
  let now = 1800000000000;
  const snooze = () => {
    response = { ...response, active_level: 'none', snoozed: true, snooze_seconds_left: 15 };
  };
  const tauri = {
    core: { invoke: async (command) => {
      calls.push(command);
      if (command === 'snooze_alert') snooze();
      if (command === 'overlay_state') return initial;
      if (command === 'armed_state') {
        if (armedAnswer === 'hang') return new Promise(() => {});
        if (armedAnswer === 'fail') throw new Error('no answer');
        return armedAnswer;
      }
    } },
    event: { listen: (_name, listener) => { overlayListener = listener; } },
  };
  const win = desktop ? { __TAURI__: tauri } : {};
  if (viewport) Object.assign(win, { innerWidth: viewport[0], innerHeight: viewport[1] });
  win.location = { search };
  win.addEventListener = (name, fn) => { if (name === 'resize') resizeListener = fn; };
  vm.runInNewContext(script(path), {
    window: win,
    document: {
      getElementById: element,
      addEventListener: (name, fn) => { docListeners[name] = fn; },
    },
    Date: { now: () => now, parse: Date.parse },
    setInterval: (fn, ms) => intervals.set(ms, fn),
    fetch: async (url, options) => {
      calls.push(`${options.method || 'GET'} ${url}`);
      if (failure) throw new Error('Server disconnected');
      if (options.method === 'POST') snooze();
      if (url.startsWith('/api/widget-series')) return { ok: true, json: async () => series };
      return { ok: true, json: async () => response };
    },
  });
  return {
    element, calls, intervals,
    respond: (value) => { response = value; failure = false; },
    disconnect: () => { failure = true; },
    advance: (ms) => { now += ms; },
    push: (value) => overlayListener({ payload: value }),
    setArmed: (value) => { armedAnswer = value; },
    resize: (w, h) => { win.innerWidth = w; win.innerHeight = h; resizeListener(); },
    poll: async () => { await p_poll(intervals); },
    now: () => now,
    docListeners,
  };
}
async function p_poll(intervals) { await intervals.get(2000)(); await flush(); }
const text = (html) => html.replace(/<span class="sep">·<\/span>/g, ' · ')
  .replace(/<[^>]*>/g, '').replace(/&amp;/g, '&');
const normal = (changes = {}) => state({
  level: 'none', active_level: 'none',
  devices: [device({ level: 'none', spo2: 97, heart_rate: 147, device_serial: 'SOCK1', ...changes })],
});

for (const desktop of [true, false]) {
  test(`widget snooze uses ${desktop ? 'Tauri' : 'HTTP'} and refreshes the countdown`, async () => {
    const p = page('app/widget_page.py', { desktop });
    await flush();
    p.element('snooze').onclick();
    await flush();
    assert.equal(p.element('status').textContent, 'Snoozed 15s');
    assert.equal(p.element('snooze').hidden, true);
    assert.ok(p.calls.includes(desktop ? 'snooze_alert' : 'POST /api/alert/snooze'));
    if (desktop) assert.ok(!p.calls.includes('POST /api/alert/snooze'));
    assert.equal(p.calls.filter((c) => c === 'GET /api/alert-state').length, 2);
  });
}

test('widget shows signal loss for the alerting device and preserves snooze status', async () => {
  const normal = device({ level: 'none', spo2: 97, account_name: 'Other baby' });
  const lost = device({ level: 'amber', signal_lost: true, stale: true });
  const initial = state({ level: 'amber', active_level: 'amber', signal_lost: true, devices: [normal, lost] });
  const p = page('app/widget_page.py', { initial });
  await flush();
  assert.equal(p.element('spo2').innerHTML, 'Signal lost');
  assert.equal(p.element('frame').className, 'alert-amber');
  assert.match(p.element('extras').innerHTML, /Other baby.*97%/);
  assert.match(p.element('status').textContent, /Signal lost/);
  p.element('snooze').onclick();
  await flush();
  assert.equal(p.element('status').textContent, 'Snoozed 15s');
});

test('widget keeps a disconnection visible after an alert and clears it on recovery', async () => {
  const p = page('app/widget_page.py');
  await flush();
  p.disconnect();
  await p.intervals.get(2000)();
  assert.equal(p.element('spo2').textContent, 'Signal lost');
  assert.equal(p.element('frame').className, 'alert-amber');
  assert.equal(p.element('status').textContent, 'Dashboard server not responding');
  p.respond(state({ level: 'none', active_level: 'none', devices: [device({ level: 'none', spo2: 97 })] }));
  await p.intervals.get(2000)();
  assert.equal(p.element('spo2').innerHTML, '97<small>%</small>');
  assert.equal(p.element('frame').className, '');
});

test('widget does not claim signal loss when the server drops without an alert', async () => {
  const p = page('app/widget_page.py', {
    initial: state({ level: 'none', active_level: 'none', devices: [device({ level: 'none', spo2: 97 })] }),
  });
  await flush();
  p.disconnect();
  await p.intervals.get(2000)();
  assert.equal(p.element('spo2').textContent, '--');
  assert.equal(p.element('frame').className, 'stale');
  assert.equal(p.element('status').textContent, 'Dashboard server not responding');
});

test('widget stops warning about a disconnection 10 minutes after the last answer', async () => {
  const p = page('app/widget_page.py');
  await flush();
  p.disconnect();
  p.advance(600 * 1000);
  await p.intervals.get(2000)();
  assert.equal(p.element('spo2').textContent, '--');
  assert.equal(p.element('frame').className, 'stale');
});

test('widget does not mask a fresh red alert when another device loses signal', async () => {
  const p = page('app/widget_page.py', { initial: state({ signal_lost: true, devices: [
    device({ level: 'amber', signal_lost: true, stale: true }), device(),
  ] }) });
  await flush();
  assert.equal(p.element('spo2').innerHTML, '84<small>%</small>');
  assert.equal(p.element('frame').className, 'alert-red');
  assert.match(p.element('extras').innerHTML, /Signal lost/);
});

test('widget selects a red test device ahead of a normal real device', async () => {
  const p = page('app/widget_page.py', { initial: state({ test: true, devices: [
    device({ level: 'none', spo2: 97, account_name: 'Real baby' }),
    device({ device_serial: 'TEST', account_name: 'Test alert' }),
  ] }) });
  await flush();
  assert.equal(p.element('spo2').innerHTML, '84<small>%</small>');
  assert.equal(p.element('frame').className, 'alert-red');
  assert.match(p.element('extras').innerHTML, /Real baby.*97%/);
  assert.doesNotMatch(p.element('extras').innerHTML, /Test alert/);
});

for (const remote_mode of [true, false]) {
  test(`widget describes ${remote_mode ? 'remote viewing' : 'an unlinked local account'}`, async () => {
    const p = page('app/widget_page.py', { initial: state({
      level: 'none', active_level: 'none', collecting: false, remote_mode, devices: [],
    }) });
    await flush();
    assert.equal(p.element('status').textContent, remote_mode ?
      'Alerts unavailable — viewing a remote server' : 'No Owlet account linked');
  });
}

test('widget disconnection warning respects the last snooze until it expires', async () => {
  const p = page('app/widget_page.py', { initial: state({ active_level: 'none', snoozed: true, snooze_seconds_left: 15 }) });
  await flush();
  p.disconnect();
  await p.intervals.get(2000)();
  assert.equal(p.element('frame').className, 'stale');
  p.advance(15000);
  p.intervals.get(1000)();
  assert.equal(p.element('frame').className, 'alert-amber');
});

// ---- overlay (overlay.html) -------------------------------------------------
// Its own fake DOM: elements take listeners, and a mouse event runs the way a
// browser runs it here: document capture listeners, then the target's own,
// then document bubble listeners unless the target stopped propagation.
const OVERLAY = 'desktop/src-tauri/ui-placeholder/overlay.html';
function overlayPage({ initial = { level: 'none' } } = {}) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) {
      let text = '';
      elements.set(id, {
        className: '', hidden: false, attrs: {}, listeners: {},
        get textContent() { return text; },
        set textContent(v) { text = String(v); },
        style: { setProperty() {} },
        toggleAttribute(name, force) { this.attrs[name] = !!force; return !!force; },
        addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); },
      });
    }
    return elements.get(id);
  };
  const intervals = new Map();
  const calls = [];
  const doc = { capture: {}, bubble: {} };
  let overlayListener;
  let now = 1800000000000;
  const tauri = {
    core: { invoke: async (command, args) => {
      calls.push(args === undefined ? command : `${command} ${JSON.stringify(args)}`);
      if (command === 'overlay_state') return initial;
    } },
    event: { listen: (_name, listener) => { overlayListener = listener; } },
  };
  vm.runInNewContext(script(OVERLAY), {
    window: { __TAURI__: tauri },
    document: {
      getElementById: element,
      addEventListener: (name, fn, capture) => { ((capture ? doc.capture : doc.bubble)[name] ||= []).push(fn); },
    },
    Date: { now: () => now },
    setInterval: (fn, ms) => intervals.set(ms, fn),
    setTimeout: () => 0,  // the arming repaint; the 500 ms paint covers it here
  });
  // targetId null = somewhere on the overlay that is not the button.
  const dispatch = (type, targetId, button = 0) => {
    const ev = { type, button, stopped: false, stopPropagation() { this.stopped = true; } };
    for (const fn of doc.capture[type] || []) fn(ev);
    if (targetId) for (const fn of element(targetId).listeners[type] || []) fn(ev);
    if (!ev.stopped) for (const fn of doc.bubble[type] || []) fn(ev);
  };
  return {
    element, intervals,
    push: (value) => overlayListener({ payload: value }),
    advance: (ms) => { now += ms; },
    // A whole click: press and release on the same target.
    click: (targetId, button = 0) => { dispatch('mousedown', targetId, button); if (button === 0) dispatch('click', targetId); },
    // Press on one target, release on another: the click lands on their common ancestor.
    drag: (fromId, toId) => { dispatch('mousedown', fromId); if (fromId === toId) dispatch('click', fromId); else dispatch('click', null); },
    actions: () => calls.filter((c) => c !== 'overlay_state'),
    calls,
    shown: () => /\bon\b/.test(element('flash').className),
  };
}
const full = (changes = {}) => ({
  level: 'amber', spo2: 86, mode: 'fullscreen', device_name: 'Baby', signal_lost: false,
  message: '', snooze_seconds: 15, can_acknowledge: true, ...changes,
});
const ARM = 1500;

test('overlay replaces held vitals with signal-loss and server messages in either monitor mode', async () => {
  const p = overlayPage();
  await flush();
  for (const mode of ['border', 'fullscreen']) {
    p.push({ level: 'amber', mode, signal_lost: true, spo2: 80, message: 'Dashboard server not responding' });
    // The held 80 % is gone: no number, the reading row hidden, "Signal lost" in its place.
    assert.equal(p.element('num').textContent, '');
    assert.equal(p.element('reading').hidden, true);
    assert.equal(p.element('bigtext').textContent, 'Signal lost');
    assert.equal(p.element('bigtext').hidden, false);
    // Border mode shows only the status line and message (CSS), so it says it there too.
    assert.match(p.element('status').textContent, mode === 'border' ? /Signal lost/ : /No oxygen readings/);
    assert.equal(p.element('msg').textContent, 'Dashboard server not responding');
    assert.match(p.element('flash').className, /signal-lost/);
    for (const id of ['status', 'num', 'bigtext', 'msg', 'dev', 'hint']) {
      assert.doesNotMatch(p.element(id).textContent, /80/, id);
    }
  }
  p.push({ level: 'red', mode: 'fullscreen', signal_lost: false, spo2: 84 });
  assert.equal(p.element('num').textContent, '84');
  assert.equal(p.element('reading').hidden, false);
  assert.equal(p.element('bigtext').hidden, true);
  assert.equal(p.element('status').textContent, 'RED ALERT — Low oxygen');
  assert.doesNotMatch(p.element('flash').className, /signal-lost/);
});

test('overlay preserves flash phase on repeated pushes and restarts on level, mode or visibility changes', async () => {
  const p = overlayPage();
  await flush();
  let value = { level: 'amber', mode: 'border', spo2: 88 };
  p.push(value);
  assert.match(p.element('flash').className, /\blit\b/);
  p.intervals.get(500)();
  assert.doesNotMatch(p.element('flash').className, /\blit\b/);
  for (let i = 0; i < 3; i++) {
    p.push({ ...value, spo2: 88 + i, device_name: 'Baby' });
    assert.doesNotMatch(p.element('flash').className, /\blit\b/);
  }
  p.intervals.get(500)();
  assert.match(p.element('flash').className, /\blit\b/);
  p.intervals.get(500)();
  value = { ...value, level: 'red' };
  p.push(value);
  assert.match(p.element('flash').className, /\blit\b/);
  p.intervals.get(500)();
  value = { ...value, mode: 'fullscreen' };
  p.push(value);
  assert.match(p.element('flash').className, /full lit/);
  p.push({ level: 'none' });
  assert.equal(p.element('flash').className, '');
  p.intervals.get(500)();
  p.push(value);
  assert.match(p.element('flash').className, /full lit/);
});

test('overlay catches up through overlay_state and flashes no faster than 1 Hz', async () => {
  const p = overlayPage({ initial: full({ level: 'red' }) });
  await flush();
  assert.equal(p.calls[0], 'overlay_state');
  assert.match(p.element('flash').className, /\bon red full lit\b/);
  assert.deepEqual([...p.intervals.keys()], [500]);  // lit/unlit every 500 ms = one cycle a second
});

test('overlay snooze text uses snooze_seconds, 15 s when missing or invalid', async () => {
  const p = overlayPage();
  await flush();
  p.push(full({ snooze_seconds: 45 }));
  assert.equal(p.element('hint').textContent, 'Click anywhere to snooze 45 s');
  const missing = full();
  delete missing.snooze_seconds;
  p.push(missing);
  assert.equal(p.element('hint').textContent, 'Click anywhere to snooze 15 s');
  p.push(full({ snooze_seconds: null }));
  assert.equal(p.element('hint').textContent, 'Click anywhere to snooze 15 s');
});

test('overlay wording differs by tier, not only colour', async () => {
  const p = overlayPage();
  await flush();
  p.push(full({ level: 'amber' }));
  assert.equal(p.element('status').textContent, 'WARNING — Low oxygen');
  assert.equal(p.element('icon-amber').attrs.hidden, false);
  assert.equal(p.element('icon-red').attrs.hidden, true);
  p.push(full({ level: 'red' }));
  assert.equal(p.element('status').textContent, 'RED ALERT — Low oxygen');
  assert.equal(p.element('icon-amber').attrs.hidden, true);
  assert.equal(p.element('icon-red').attrs.hidden, false);
});

test('overlay "I\'m checking" click invokes acknowledge_alert only and hides the overlay', async () => {
  const p = overlayPage();
  await flush();
  p.push(full());
  assert.equal(p.element('ack').hidden, false);
  p.advance(ARM);
  p.intervals.get(500)();
  assert.doesNotMatch(p.element('ack').className, /arming/);
  p.click('ack-btn');
  await flush();
  assert.deepEqual(p.actions(), ['acknowledge_alert']);
  assert.equal(p.shown(), false);
  // Nothing more fires once it is hidden.
  p.click('ack-btn');
  p.click(null);
  await flush();
  assert.deepEqual(p.actions(), ['acknowledge_alert']);
});

test('overlay mousedown anywhere but the button invokes snooze_alert only and hides the overlay', async () => {
  for (const button of [0, 2]) {
    const p = overlayPage();
    await flush();
    p.push(full({ level: 'red' }));
    p.advance(ARM);
    p.click(null, button);
    await flush();
    assert.deepEqual(p.actions(), ['snooze_alert'], `mouse button ${button}`);
    assert.equal(p.shown(), false);
  }
});

test('overlay button: right press snoozes; press then drag off does neither and keeps flashing', async () => {
  let p = overlayPage();
  await flush();
  p.push(full());
  p.advance(ARM);
  p.click('ack-btn', 2);
  await flush();
  assert.deepEqual(p.actions(), ['snooze_alert']);

  p = overlayPage();
  await flush();
  p.push(full());
  p.advance(ARM);
  p.drag('ack-btn', null);
  await flush();
  assert.deepEqual(p.actions(), []);
  assert.equal(p.shown(), true);
  p.intervals.get(500)();
  assert.match(p.element('flash').className, /\bon amber full\b/);
  // Pressing elsewhere and dragging onto the button snoozes at the press.
  p.drag(null, 'ack-btn');
  await flush();
  assert.deepEqual(p.actions(), ['snooze_alert']);
});

test('overlay button warm-up: an early press snoozes; escalation re-arms, same-level updates do not', async () => {
  let p = overlayPage();
  await flush();
  p.push(full());
  assert.match(p.element('ack').className, /arming/);
  p.advance(300);
  p.click('ack-btn');
  await flush();
  assert.deepEqual(p.actions(), ['snooze_alert']);
  assert.equal(p.shown(), false);

  // Just before the end of the warm-up it is still a snooze target.
  p = overlayPage();
  await flush();
  p.push(full());
  p.advance(ARM - 1);
  p.click('ack-btn');
  await flush();
  assert.deepEqual(p.actions(), ['snooze_alert']);

  // A same-level update keeps the button live.
  p = overlayPage();
  await flush();
  p.push(full());
  p.advance(ARM);
  p.push(full({ spo2: 85 }));
  p.click('ack-btn');
  await flush();
  assert.deepEqual(p.actions(), ['acknowledge_alert']);

  // Amber -> red restarts the warm-up.
  p = overlayPage();
  await flush();
  p.push(full());
  p.advance(ARM);
  p.push(full({ level: 'red' }));
  p.click('ack-btn');
  await flush();
  assert.deepEqual(p.actions(), ['snooze_alert']);
});

test('overlay border mode invokes neither snooze nor acknowledge and has no button', async () => {
  for (const changes of [{ level: 'red' }, { signal_lost: true, spo2: null, message: 'Dashboard server not responding' }]) {
    const p = overlayPage();
    await flush();
    p.push(full({ mode: 'border', ...changes }));
    assert.equal(p.element('ack').hidden, true);
    assert.match(p.element('flash').className, /\bborder\b/);
    p.advance(ARM);
    p.click(null);
    p.click('ack-btn');
    p.click(null, 2);
    await flush();
    assert.deepEqual(p.actions(), []);
    assert.equal(p.shown(), true);
  }
});

test('overlay hides the button when can_acknowledge is not true, and level none hides everything', async () => {
  const p = overlayPage();
  await flush();
  p.push(full({ can_acknowledge: false }));
  assert.equal(p.element('ack').hidden, true);
  p.advance(ARM);
  p.click('ack-btn');
  await flush();
  assert.deepEqual(p.actions(), ['snooze_alert']);
  p.push(full());
  p.push({ level: 'none' });
  assert.equal(p.element('flash').className, '');
  p.click(null);
  p.click('ack-btn');
  await flush();
  assert.deepEqual(p.actions(), ['snooze_alert']);
});

// ---- v0.2 widget (V8) ------------------------------------------------------

test('snooze button text follows the configured snooze length, 15 s only when missing', async () => {
  const p = page('app/widget_page.py', { initial: state({ snooze_seconds: 30 }) });
  await flush();
  assert.equal(p.element('snooze').textContent, 'Snooze 30 s');
  assert.equal(p.element('snooze').hidden, false);
  p.respond(state({ snooze_seconds: 120 }));
  await p.poll();
  assert.equal(p.element('snooze').textContent, 'Snooze 120 s');
  p.respond(state());
  await p.poll();
  assert.equal(p.element('snooze').textContent, 'Snooze 15 s');
});

for (const level of ['red', 'amber']) {
  test(`acknowledged ${level} alert keeps its colour, shows time left, and has no flash or snooze`, async () => {
    const spo2 = level === 'red' ? 84 : 88;
    const p = page('app/widget_page.py', { initial: state({
      level, active_level: 'none', acknowledged: true, ack_seconds_left: 420,
      acknowledged_until: '2027-01-15T08:07:00Z', devices: [device({ level, spo2 })],
    }) });
    await flush();
    assert.equal(p.element('status').textContent, `Acknowledged — ${spo2}% · 7m left`);
    assert.equal(p.element('status').className, `status ${level}`);
    assert.equal(p.element('frame').className, `ack-${level}`);
    assert.doesNotMatch(p.element('frame').className, /alert-/);
    assert.equal(p.element('snooze').hidden, true);
    assert.equal(p.element('msnooze').hidden, true);
    assert.equal(text(p.element('micro').innerHTML), `${spo2} · Ack 7m`);
    // Too wide for the pill (e.g. fallback font): drop the time, keep the words.
    Object.assign(p.element('micro'), { scrollWidth: 90, clientWidth: 76 });
    p.intervals.get(1000)();
    assert.equal(text(p.element('micro').innerHTML), `${spo2} · Ack`);
    Object.assign(p.element('micro'), { scrollWidth: 0, clientWidth: 0 });
    p.advance(400 * 1000);
    p.intervals.get(1000)();
    assert.equal(p.element('status').textContent, `Acknowledged — ${spo2}% · 20s left`);
  });
}

test('acknowledged after recovery shows the reading tier, and escalation flashes again', async () => {
  const p = page('app/widget_page.py', { initial: state({
    level: 'none', active_level: 'none', acknowledged: true, ack_seconds_left: 170,
    devices: [device({ level: 'none', spo2: 96 })],
  }) });
  await flush();
  assert.equal(p.element('status').textContent, 'Acknowledged — 96% · 3m left');
  assert.equal(p.element('frame').className, '');
  // The server ends the acknowledge on escalation: active_level is red again.
  p.respond(state({ acknowledged: false }));
  await p.poll();
  assert.equal(p.element('frame').className, 'alert-red');
  assert.equal(p.element('snooze').hidden, false);
});

test('armed dot: mint when armed, hidden in a browser', async () => {
  const armed = page('app/widget_page.py', { desktop: true, initial: normal(), armed: { armed: true, problems: [] } });
  await flush();
  assert.equal(armed.element('dot').hidden, false);
  assert.equal(armed.element('dot').className, 'dot armed');
  assert.equal(armed.element('dot').title, 'Alerts armed on this PC');
  assert.equal(armed.element('dot')['attr:aria-label'], 'Alerts armed on this PC');
  assert.equal(armed.element('mdot').className, 'dot armed');
  assert.equal(armed.element('status').textContent, 'Updated 0s ago');
  assert.ok(armed.calls.includes('armed_state'));

  const browser = page('app/widget_page.py', { initial: normal() });
  await flush();
  assert.equal(browser.element('dot').hidden, true);
  assert.equal(browser.element('mdot').hidden, true);
  assert.ok(!browser.calls.includes('armed_state'));
});

test('armed dot: a fault shows amber with the first problem as the status line', async () => {
  const p = page('app/widget_page.py', { desktop: true, initial: normal(), armed: { armed: false, problems: [
    { code: 'muted', text: 'Windows is muted' }, { code: 'sleep', text: 'PC may sleep' },
    { code: 'not_collecting', text: 'Sock off' },
  ] } });
  await flush();
  assert.equal(p.element('dot').className, 'dot fault');
  assert.equal(p.element('dot').title, 'Windows is muted');
  assert.equal(p.element('status').textContent, 'Windows is muted');
  // The numbers are still shown in every size.
  assert.equal(p.element('spo2').innerHTML, '97<small>%</small>');
  assert.equal(text(p.element('micro').innerHTML), '97 · 147');
});

test('armed dot: only not_collecting is a grey "Not monitoring"', async () => {
  const p = page('app/widget_page.py', {
    desktop: true, initial: normal({ sock_reporting: false }),
    armed: { armed: false, problems: [{ code: 'not_collecting', text: 'Sock off' }] },
  });
  await flush();
  assert.equal(p.element('dot').className, 'dot idle');
  assert.equal(p.element('status').textContent, 'Not monitoring · Sock off');
  assert.equal(p.element('status').className, 'status grey');
  assert.equal(text(p.element('micro').innerHTML), 'Sock off');
});

for (const silence of ['hang', 'fail']) {
  test(`armed dot: unknown after 15 s without an answer (${silence})`, async () => {
    const p = page('app/widget_page.py', { desktop: true, initial: normal(), armed: { armed: true, problems: [] } });
    await flush();
    assert.equal(p.element('dot').className, 'dot armed');
    p.setArmed(silence);
    p.advance(14000);
    await p.poll();
    assert.equal(p.element('dot').className, 'dot armed');
    p.advance(2000);
    await p.poll();
    assert.equal(p.element('dot').className, 'dot fault');
    assert.equal(p.element('status').textContent, 'Alert status unknown');
    p.setArmed({ armed: true, problems: [] });
    await p.poll();
    assert.equal(p.element('dot').className, 'dot armed');
    assert.match(p.element('status').textContent, /^Updated/);
  });
}

test('armed dot: never answered says "Checking alert status"', async () => {
  const p = page('app/widget_page.py', { desktop: true, initial: normal() });
  await flush();
  assert.equal(p.element('dot').className, 'dot checking');
  assert.equal(p.element('dot').title, 'Checking alert status');
  assert.equal(p.element('status').textContent, 'Checking alert status');
});

test('an alert status line beats the armed problem, and the dot stays visible', async () => {
  const p = page('app/widget_page.py', { desktop: true, initial: state(), armed: { armed: false, problems: [
    { code: 'muted', text: 'Windows is muted' },
  ] } });
  await flush();
  assert.equal(p.element('status').textContent, 'Low oxygen · 0s ago');
  assert.equal(p.element('status').className, 'status red');
  assert.equal(p.element('frame').className, 'alert-red');
  assert.equal(p.element('dot').hidden, false);
  assert.equal(p.element('dot').className, 'dot fault');
  p.respond(state({ level: 'amber', active_level: 'amber', devices: [device({ level: 'amber', spo2: 88 })] }));
  await p.poll();
  assert.equal(p.element('status').textContent, 'Oxygen warning · 0s ago');
  assert.equal(p.element('frame').className, 'alert-amber');
});

test('the armed dot pulses once per new sample, not on repeats or the TEST device', async () => {
  const at = (iso) => normal({ recorded_at: iso });
  const p = page('app/widget_page.py', { desktop: true, initial: at('2027-01-15T08:00:00Z'), armed: { armed: true, problems: [] } });
  await flush();
  assert.equal(p.element('dot').className, 'dot armed');
  p.respond(at('2027-01-15T08:00:00Z'));
  await p.poll();
  assert.equal(p.element('dot').className, 'dot armed');
  p.respond(at('2027-01-15T08:00:04Z'));
  await p.poll();
  assert.equal(p.element('dot').className, 'dot armed beat1');
  p.intervals.get(1000)();
  assert.equal(p.element('dot').className, 'dot armed beat1');   // re-render keeps the class: no restart
  p.respond(at('2027-01-15T08:00:08Z'));
  await p.poll();
  assert.equal(p.element('dot').className, 'dot armed beat2');
  const withTest = at('2027-01-15T08:00:08Z');
  withTest.devices.push(device({ device_serial: 'TEST', recorded_at: '2027-01-15T09:00:00Z' }));
  p.respond(withTest);
  await p.poll();
  assert.equal(p.element('dot').className, 'dot armed beat2');
});

test('size follows the viewport, and ?size= overrides it', async () => {
  for (const [vp, size] of [[[110, 32], 'micro'], [[300, 120], 'compact'], [[300, 200], 'expanded'], [null, 'compact']]) {
    const p = page('app/widget_page.py', { initial: normal(), viewport: vp });
    await flush();
    assert.match(p.element('app').className, new RegExp(`^size-${size}\\b`), `viewport ${vp}`);
  }
  const q = page('app/widget_page.py', { initial: normal(), viewport: [300, 200], search: '?size=micro' });
  await flush();
  assert.match(q.element('app').className, /^size-micro\b/);
  const r = page('app/widget_page.py', { initial: normal(), viewport: [300, 120] });
  await flush();
  r.resize(110, 32);
  assert.match(r.element('app').className, /^size-micro\b/);
});

test('micro pill says something in words in every state, not colour alone', async () => {
  const p = page('app/widget_page.py', { initial: normal(), viewport: [110, 32] });
  assert.equal(text(p.element('micro').innerHTML), 'Connecting');
  await flush();
  const cases = [
    [normal(), '97 · 147'],
    [normal({ stale: true }), 'No data'],
    [normal({ sock_reporting: false }), 'Sock off'],
    [normal({ sock_reporting: false, charging: true }), 'Charging'],
    [state({ level: 'amber', active_level: 'amber', signal_lost: true,
      devices: [device({ level: 'amber', signal_lost: true, stale: true })] }), 'Lost'],
    [state({ level: 'amber', active_level: 'none', signal_lost: true, snoozed: true, snooze_seconds_left: 9,
      devices: [device({ level: 'amber', signal_lost: true, stale: true })] }), 'Lost · Zz 9s'],
    [state(), '84'],
    [state({ active_level: 'none', snoozed: true, snooze_seconds_left: 12 }), '84 · Zz 12s'],
    [state({ level: 'none', active_level: 'none', collecting: false, devices: [] }), 'Off'],
  ];
  for (const [value, expected] of cases) {
    p.respond(value);
    await p.poll();
    assert.equal(text(p.element('micro').innerHTML), expected);
  }
  // Red alert: flashing frame plus a Snooze button with the same path.
  p.respond(state());
  await p.poll();
  assert.equal(p.element('frame').className, 'alert-red');
  assert.equal(p.element('msnooze').hidden, false);
  assert.equal(p.element('msnooze').className, 'red');
  p.element('msnooze').onclick();
  await flush();
  assert.ok(p.calls.includes('POST /api/alert/snooze'));
  assert.equal(text(p.element('micro').innerHTML), '84 · Zz 15s');
  // Server drops within 10 minutes of an alert: like compact, it says signal lost.
  p.disconnect();
  await p.poll();
  assert.equal(text(p.element('micro').innerHTML), 'Signal lost');
  // Server drops with nothing alerting: plainly "No server".
  p.respond(normal());
  await p.poll();
  p.disconnect();
  await p.poll();
  assert.equal(text(p.element('micro').innerHTML), 'No server');
  assert.equal(p.element('frame').className, 'stale');
});

test('micro pill keeps a disconnection after an alert flashing as signal lost', async () => {
  const p = page('app/widget_page.py', { initial: state(), viewport: [110, 32] });
  await flush();
  p.disconnect();
  await p.poll();
  assert.equal(p.element('frame').className, 'alert-amber');
  assert.equal(text(p.element('micro').innerHTML), 'Signal lost');
});

test('micro snooze in the desktop app uses the Tauri snooze command', async () => {
  const p = page('app/widget_page.py', { desktop: true, initial: state(), viewport: [110, 32] });
  await flush();
  p.element('msnooze').onclick();
  await flush();
  assert.ok(p.calls.includes('snooze_alert'));
  assert.ok(!p.calls.includes('POST /api/alert/snooze'));
});

test('expanded size fetches the 10-minute series, draws the thresholds, and rate-limits', async () => {
  const at = (iso, spo2 = 97) => normal({ recorded_at: iso, spo2 });
  const p = page('app/widget_page.py', { initial: at('2027-01-15T08:00:00Z'), viewport: [300, 200] });
  await flush();
  await flush();
  const fetches = () => p.calls.filter((c) => c === 'GET /api/widget-series?minutes=10').length;
  assert.equal(fetches(), 1);
  const spark = p.element('spark').innerHTML;
  assert.match(spark, /data-th="warn"/);
  assert.match(spark, /data-th="crit"/);
  assert.match(text(spark), /90.*86/);
  // No new sample: no refetch, even after 30 s.
  p.advance(31000);
  await p.poll();
  assert.equal(fetches(), 1);
  // A new sample within 30 s of the last fetch: wait for the 30 s mark.
  const p2 = page('app/widget_page.py', { initial: at('2027-01-15T08:00:00Z'), viewport: [300, 200] });
  await flush();
  const fetches2 = () => p2.calls.filter((c) => c.startsWith('GET /api/widget-series')).length;
  assert.equal(fetches2(), 1);
  p2.respond(at('2027-01-15T08:00:05Z'));
  p2.advance(5000);
  await p2.poll();
  assert.equal(fetches2(), 1);
  p2.advance(26000);
  await p2.poll();
  assert.equal(fetches2(), 2);

  const compact = page('app/widget_page.py', { initial: normal(), viewport: [300, 120] });
  await flush();
  await compact.poll();
  assert.ok(!compact.calls.some((c) => c.startsWith('GET /api/widget-series')));
});

test('expanded trace draws the series and the live reading from the same device', async () => {
  const p = page('app/widget_page.py', {
    initial: normal({ recorded_at: new Date(1800000000000).toISOString(), spo2: 95 }),
    viewport: [300, 200],
    series: { ...SERIES, points: [
      { t: new Date(1800000000000 - 200000).toISOString(), spo2: 97 },
      { t: new Date(1800000000000 - 130000).toISOString(), spo2: 96 },
    ] },
  });
  await flush();
  await flush();
  const spark = p.element('spark').innerHTML;
  const line = spark.match(/<polyline class="trace" points="([^"]+)"/);
  assert.ok(line, spark);
  assert.equal(line[1].split(' ').length, 2);   // gap > 90 s before the live point: a new segment
  assert.equal((spark.match(/<polyline/g) || []).length, 2);
  assert.match(spark, /class="end"/);
});

test('expanded trace with no points shows the empty text on its own backing, still with thresholds', async () => {
  const p = page('app/widget_page.py', {
    initial: state({ level: 'none', active_level: 'none', collecting: false, devices: [] }),
    viewport: [300, 200], series: { ...SERIES, device_serial: null, points: [] },
  });
  await flush();
  await flush();
  const spark = p.element('spark').innerHTML;
  // Placed in the band above the warn line (90 of 83–100 sits at 59% down).
  assert.match(spark, /<div class="msg" style="top:calc\(4px \+ \(100% - 8px\) \* 0\); bottom:calc\(4px \+ \(100% - 8px\) \* 0\.4\)"><span>No oxygen readings in the last 10 min<\/span><\/div>/);
  assert.doesNotMatch(spark, /<polyline/);
  assert.match(spark, /data-th="warn"/);
});

test('expanded size refetches at once when a different device starts alerting', async () => {
  const p = page('app/widget_page.py', { initial: normal(), viewport: [300, 200] });
  await flush();
  const fetches = () => p.calls.filter((c) => c.startsWith('GET /api/widget-series')).length;
  assert.equal(fetches(), 1);
  p.advance(6000);
  p.respond(state({ devices: [device({ device_serial: 'SOCK2' })] }));
  await p.poll();
  assert.equal(fetches(), 2);
});

test('awake note shows only on a change after the first value, for 60 s', async () => {
  const p = page('app/widget_page.py', { initial: { ...normal(), awake_event_id: 3 }, viewport: [300, 120] });
  await flush();
  assert.equal(p.element('awake').hidden, true);   // first value seen: silent
  p.respond({ ...normal(), awake_event_id: 3 });
  await p.poll();
  assert.equal(p.element('awake').hidden, true);
  p.respond({ ...normal(), awake_event_id: null });
  await p.poll();
  assert.equal(p.element('awake').hidden, true);
  p.respond({ ...normal(), awake_event_id: 4 });
  await p.poll();
  assert.equal(p.element('awake').hidden, false);
  assert.equal(p.element('awakeMark').hidden, false);
  assert.equal(p.element('frame').className, '');      // no flash
  assert.equal(p.element('snooze').hidden, true);      // nothing to snooze
  p.advance(59000);
  p.intervals.get(1000)();
  assert.equal(p.element('awake').hidden, false);
  p.advance(2000);
  p.intervals.get(1000)();
  assert.equal(p.element('awake').hidden, true);
});

test('awake note: a first value of null is recorded silently too', async () => {
  const p = page('app/widget_page.py', { initial: { ...normal(), awake_event_id: null } });
  await flush();
  assert.equal(p.element('awake').hidden, true);
  p.respond({ ...normal(), awake_event_id: 1 });
  await p.poll();
  assert.equal(p.element('awake').hidden, false);
});

for (const [name, alert] of [
  ['an active red alert', state()],
  ['an active amber alert', state({ level: 'amber', active_level: 'amber', devices: [device({ level: 'amber', spo2: 88 })] })],
  ['an acknowledged red alert', state({ active_level: 'none', acknowledged: true, ack_seconds_left: 300 })],
  ['a snoozed red alert', state({ active_level: 'none', snoozed: true, snooze_seconds_left: 10 })],
]) {
  test(`awake note never shows during ${name}`, async () => {
    const p = page('app/widget_page.py', { initial: { ...normal(), awake_event_id: 1 } });
    await flush();
    p.respond({ ...alert, awake_event_id: 2 });
    await p.poll();
    assert.equal(p.element('awake').hidden, true);
    assert.equal(p.element('awakeMark').hidden, true);
    if (alert.active_level !== 'none') {
      assert.equal(p.element('frame').className, `alert-${alert.active_level}`);
      assert.equal(p.element('snooze').hidden, false);
    }
    // Still inside the 60 s window once the alert clears: the note may return.
    p.respond({ ...normal(), awake_event_id: 2 });
    await p.poll();
    assert.equal(p.element('awake').hidden, false);
  });
}

test('alert flash is unchanged by the new states: alert-<level> exactly when active_level is amber/red', async () => {
  const p = page('app/widget_page.py', { desktop: true, initial: normal(), armed: { armed: true, problems: [] } });
  await flush();
  const combos = [
    [{}, ''],
    [{ level: 'red', active_level: 'red' }, 'alert-red'],
    [{ level: 'amber', active_level: 'amber' }, 'alert-amber'],
    [{ level: 'red', active_level: 'red', awake_event_id: 7 }, 'alert-red'],
    [{ level: 'red', active_level: 'red', acknowledged: false }, 'alert-red'],
    [{ level: 'red', active_level: 'none', snoozed: true, snooze_seconds_left: 5 }, ''],
    [{ level: 'red', active_level: 'none', acknowledged: true, ack_seconds_left: 100 }, 'ack-red'],
    // Never sent by the server, but an active level must still win over acknowledged.
    [{ level: 'red', active_level: 'red', acknowledged: true, ack_seconds_left: 100 }, 'alert-red'],
  ];
  for (const [changes, cls] of combos) {
    p.respond({ ...normal(), ...changes });
    await p.poll();
    assert.equal(p.element('frame').className, cls, JSON.stringify(changes));
    assert.equal(p.element('snooze').hidden, !cls.startsWith('alert-'));
    if (cls.startsWith('alert-')) assert.doesNotMatch(p.element('status').textContent, /Acknowledged/);
  }
});

// ---- expanded: sleep timer (V8b) ------------------------------------------------
const NOW = 1800000000000;
const ago = (ms) => new Date(NOW - ms).toISOString();
const sleepSeries = (sleep) => ({ ...SERIES, sleep });
const ASLEEP = sleepSeries({ state: 'asleep', since: ago((2 * 3600 + 14 * 60) * 1000 + 5000) });
const sock1 = (changes = {}) => device({ device_serial: 'SOCK1', ...changes });
const timer = (p) => (p.element('sleep').hidden ? null : text(p.element('sleep').innerHTML));

test('expanded timer shows "Asleep 2h 14m" in the plain state and ticks each minute', async () => {
  const p = page('app/widget_page.py', { initial: normal(), viewport: [300, 200], series: ASLEEP });
  await flush();
  await flush();
  assert.equal(p.element('status').textContent, 'Updated 0s ago');
  assert.equal(timer(p), 'Asleep 2h 14m');
  assert.equal(p.element('sleep').className, 'sleep');
  assert.match(p.element('sleep').innerHTML, /<svg/);            // a shape, not colour alone
  p.advance(55000);
  p.intervals.get(1000)();
  assert.equal(timer(p), 'Asleep 2h 15m');                       // ticks locally, no new fetch
  assert.equal(p.calls.filter((c) => c.startsWith('GET /api/widget-series')).length, 1);
});

test('expanded timer: awake uses the awake style; short and long durations', async () => {
  for (const [sleep, expected, cls] of [
    [{ state: 'awake', since: ago(12 * 60000 + 1000) }, 'Awake 12m', 'sleep is-awake'],
    [{ state: 'asleep', since: ago(20000) }, 'Asleep <1m', 'sleep'],
    [{ state: 'asleep', since: ago(3 * 3600000 + 5 * 60000) }, 'Asleep 3h 05m', 'sleep'],
    [{ state: 'asleep', since: ago(16 * 3600000) }, 'Asleep 16h+', 'sleep'],
  ]) {
    const p = page('app/widget_page.py', { initial: normal(), viewport: [300, 200], series: sleepSeries(sleep) });
    await flush();
    await flush();
    assert.equal(timer(p).replace(/&lt;/g, '<'), expected);
    assert.equal(p.element('sleep').className, cls);
  }
});

test('expanded timer hides when sleep is null, malformed, for another device, or old', async () => {
  for (const series of [
    sleepSeries(null), SERIES, sleepSeries({ state: 'unknown', since: ago(60000) }),
    sleepSeries({ state: 'asleep', since: 'not a date' }),
    { ...ASLEEP, device_serial: 'SOCK2' },
  ]) {
    const p = page('app/widget_page.py', { initial: normal(), viewport: [300, 200], series });
    await flush();
    await flush();
    assert.equal(timer(p), null, JSON.stringify(series));
  }
  // No successful refetch for over 3 minutes: the sleep data is not trusted.
  const p = page('app/widget_page.py', { initial: normal(), viewport: [300, 200], series: ASLEEP });
  await flush();
  await flush();
  assert.notEqual(timer(p), null);
  p.advance(181000);
  await p.poll();
  assert.equal(timer(p), null);
});

test('timer shows only in the expanded size', async () => {
  const p = page('app/widget_page.py', { initial: normal(), viewport: [300, 200], series: ASLEEP });
  await flush();
  await flush();
  assert.equal(timer(p), 'Asleep 2h 14m');
  p.resize(300, 120);
  assert.equal(p.element('app').className, 'size-compact');
  assert.equal(timer(p), null);
  p.resize(110, 32);
  assert.equal(p.element('app').className, 'size-micro');
  assert.equal(timer(p), null);
  p.resize(300, 200);
  assert.equal(timer(p), 'Asleep 2h 14m');
  for (const search of ['?size=compact', '?size=micro']) {
    const q = page('app/widget_page.py', { initial: normal(), viewport: [300, 200], search, series: ASLEEP });
    await flush();
    await flush();
    assert.equal(timer(q), null, search);
  }
});

// Every non-plain state: the timer hides, and everything else on the widget is
// exactly what it is with no sleep data at all.
const view = (p) => ({
  status: p.element('status').textContent, statusCls: p.element('status').className,
  frame: p.element('frame').className, app: p.element('app').className,
  snooze: [p.element('snooze').hidden, p.element('snooze').textContent, p.element('snooze').className],
  msnooze: [p.element('msnooze').hidden, p.element('msnooze').className],
  spo2: [p.element('spo2').className, p.element('spo2').innerHTML],
  hr: p.element('hr').innerHTML, awake: p.element('awake').hidden,
  extras: p.element('extras').innerHTML, spark: p.element('spark').innerHTML,
  micro: p.element('micro').innerHTML,
});
const HIDDEN_STATES = [
  ['stale', normal({ stale: true, age_seconds: 90 })],
  ['sock off', normal({ sock_reporting: false })],
  ['charging', normal({ sock_reporting: false, charging: true })],
  ['signal lost', state({ level: 'amber', active_level: 'amber', signal_lost: true,
    devices: [sock1({ level: 'amber', signal_lost: true, stale: true })] })],
  ['snoozed', state({ active_level: 'none', snoozed: true, snooze_seconds_left: 10, devices: [sock1()] })],
  ['acknowledged', state({ active_level: 'none', acknowledged: true, ack_seconds_left: 300, devices: [sock1()] })],
  ['acknowledged after recovery', state({ level: 'none', active_level: 'none', acknowledged: true,
    ack_seconds_left: 120, devices: [sock1({ level: 'none', spo2: 97 })] })],
  ['amber alert', state({ level: 'amber', active_level: 'amber', devices: [sock1({ level: 'amber', spo2: 88 })] })],
  ['red alert', state({ devices: [sock1()] })],
  ['test alert', state({ devices: [device({ device_serial: 'TEST' }), sock1({ level: 'none', spo2: 97 })] })],
  ['no account linked', state({ level: 'none', active_level: 'none', collecting: false, devices: [] })],
];

for (const [name, value] of HIDDEN_STATES) {
  test(`expanded timer hides during ${name} and leaves the rest unchanged`, async () => {
    const withSleep = page('app/widget_page.py', { initial: value, viewport: [300, 200], series: ASLEEP });
    const without = page('app/widget_page.py', { initial: value, viewport: [300, 200], series: sleepSeries(null) });
    await flush();
    await flush();
    assert.equal(timer(withSleep), null);
    assert.deepEqual(view(withSleep), view(without));
  });
}

test('an alert arriving while the timer shows hides it on the same poll; alert text and Snooze unchanged', async () => {
  const p = page('app/widget_page.py', { initial: normal(), viewport: [300, 200], series: ASLEEP });
  const ref = page('app/widget_page.py', { initial: normal(), viewport: [300, 200], series: sleepSeries(null) });
  await flush();
  await flush();
  assert.equal(timer(p), 'Asleep 2h 14m');
  for (const value of [
    state({ devices: [sock1()] }),
    state({ level: 'amber', active_level: 'amber', devices: [sock1({ level: 'amber', spo2: 88 })] }),
  ]) {
    for (const q of [p, ref]) { q.respond(value); await q.poll(); }
    assert.equal(timer(p), null);
    assert.equal(p.element('snooze').hidden, false);
    assert.match(p.element('status').textContent, /^(Low oxygen|Oxygen warning) · 0s ago$/);
    assert.deepEqual(view(p), view(ref));
    // Snooze still works and the snoozed line has no timer either.
    p.element('snooze').onclick();
    ref.element('snooze').onclick();
    await flush();
    assert.equal(p.element('status').textContent, 'Snoozed 15s');
    assert.equal(timer(p), null);
    assert.deepEqual(view(p), view(ref));
  }
  // Back to normal: the timer returns.
  p.respond(normal());
  await p.poll();
  assert.equal(timer(p), 'Asleep 2h 14m');
});

test('expanded timer hides when the server is down, and with the awake note', async () => {
  const p = page('app/widget_page.py', { initial: { ...normal(), awake_event_id: 1 }, viewport: [300, 200], series: ASLEEP });
  await flush();
  await flush();
  assert.equal(timer(p), 'Asleep 2h 14m');
  p.respond({ ...normal(), awake_event_id: 2 });
  await p.poll();
  assert.equal(p.element('awake').hidden, false);
  assert.equal(timer(p), null);
  p.disconnect();
  await p.poll();
  assert.equal(p.element('status').textContent, 'Dashboard server not responding');
  assert.equal(timer(p), null);
});

test('expanded timer never sits beside a desktop alert-status problem', async () => {
  const p = page('app/widget_page.py', {
    desktop: true, initial: normal(), viewport: [300, 200], series: ASLEEP,
    armed: { armed: false, problems: [{ code: 'no_audio', text: 'Alarm sound fell back to basic playback' }] },
  });
  await flush();
  await flush();
  assert.equal(p.element('status').textContent, 'Alarm sound fell back to basic playback');
  assert.equal(timer(p), null);
  p.setArmed({ armed: true, problems: [] });
  await p.poll();
  assert.equal(p.element('status').textContent, 'Updated 0s ago');
  assert.equal(timer(p), 'Asleep 2h 14m');
});

for (const desktop of [true, false]) {
  test(`right-click ${desktop ? 'opens the native widget menu' : 'is left alone in a browser'}`, async () => {
    const p = page('app/widget_page.py', { desktop });
    await flush();
    if (!desktop) {
      assert.equal(p.docListeners.contextmenu, undefined);
      assert.ok(!p.calls.includes('show_widget_menu'));
      return;
    }
    let prevented = false;
    p.docListeners.contextmenu({ preventDefault: () => { prevented = true; } });
    await flush();
    assert.ok(prevented);
    assert.equal(p.calls.filter((c) => c === 'show_widget_menu').length, 1);
  });
}
