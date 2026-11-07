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

function page(path, { desktop = false, initial = state() } = {}) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      className: '', textContent: '', innerHTML: '', hidden: false,
      style: { setProperty() {} },
    });
    return elements.get(id);
  };
  const intervals = new Map();
  const calls = [];
  let response = initial;
  let failure = false;
  let overlayListener;
  let now = 1800000000000;
  const snooze = () => {
    response = { ...response, active_level: 'none', snoozed: true, snooze_seconds_left: 15 };
  };
  const tauri = {
    core: { invoke: async (command) => {
      calls.push(command);
      if (command === 'snooze_alert') snooze();
      if (command === 'overlay_state') return initial;
    } },
    event: { listen: (_name, listener) => { overlayListener = listener; } },
  };
  vm.runInNewContext(script(path), {
    window: desktop ? { __TAURI__: tauri } : {},
    document: { getElementById: element, addEventListener() {} },
    Date: { now: () => now },
    setInterval: (fn, ms) => intervals.set(ms, fn),
    fetch: async (url, options) => {
      calls.push(`${options.method || 'GET'} ${url}`);
      if (failure) throw new Error('Server disconnected');
      if (options.method === 'POST') snooze();
      return { ok: true, json: async () => response };
    },
  });
  return {
    element, calls, intervals,
    respond: (value) => { response = value; failure = false; },
    disconnect: () => { failure = true; },
    advance: (ms) => { now += ms; },
    push: (value) => overlayListener({ payload: value }),
  };
}

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

test('overlay replaces held vitals with signal-loss and server messages in either monitor mode', async () => {
  const p = page('desktop/src-tauri/ui-placeholder/overlay.html', { desktop: true, initial: { level: 'none' } });
  await flush();
  for (const mode of ['border', 'fullscreen']) {
    p.push({ level: 'amber', mode, signal_lost: true, spo2: 80, message: 'Dashboard server not responding' });
    assert.equal(p.element('big').textContent, 'Signal lost');
    assert.equal(p.element('who').textContent, 'Dashboard server not responding');
    assert.match(p.element('flash').className, /signal-lost/);
  }
  p.push({ level: 'red', mode: 'fullscreen', signal_lost: false, spo2: 84 });
  assert.equal(p.element('big').textContent, 'SpO₂ 84%');
  assert.doesNotMatch(p.element('flash').className, /signal-lost/);
});

test('overlay preserves flash phase on repeated pushes and restarts on level, mode or visibility changes', async () => {
  const p = page('desktop/src-tauri/ui-placeholder/overlay.html', { desktop: true, initial: { level: 'none' } });
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
