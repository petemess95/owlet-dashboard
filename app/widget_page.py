"""Always-on-top desktop widget: live SpO2 / heart rate / freshness, the
amber/red alert state with a snooze button, this PC's armed state (a dot that
pulses once per new sample), the acknowledged state and the soft "awake" note.

Self-contained (inline CSS + JS; the only other request is the bundled Source
Sans 3 from this same local server, with a Segoe UI fallback) so it renders
instantly in a frameless window and with no network. It stays dark whatever
the app theme is: the colour values below are copies of the dark tokens in
app/static/theme.css (a test checks they stay equal).

Three layouts, chosen from the viewport size (the shell sets the window size)
or from ``?size=micro|compact|expanded``: micro pill 110x32, compact 300x120,
expanded 300x200 (adds a 10-minute oxygen trace with the threshold lines and,
in the plain "Updated" state only, a sleep timer; any alert, snooze,
acknowledge or problem line hides it).
It still works when opened in a normal browser (the Tauri-only parts hide)."""

from __future__ import annotations

WIDGET_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="dark" />
<title>Owlet widget</title>
<style>
  @font-face { font-family: 'Source Sans 3'; font-style: normal; font-weight: 400; font-display: swap; src: url('/fonts/source-sans-3-latin-400-normal.woff2') format('woff2'); }
  @font-face { font-family: 'Source Sans 3'; font-style: normal; font-weight: 600; font-display: swap; src: url('/fonts/source-sans-3-latin-600-normal.woff2') format('woff2'); }
  @font-face { font-family: 'Source Sans 3'; font-style: normal; font-weight: 700; font-display: swap; src: url('/fonts/source-sans-3-latin-700-normal.woff2') format('woff2'); }
  /* Dark tokens, equal to app/static/theme.css (dark block). The widget is
     dark in both app themes: it sits over games and dark desktops. */
  :root {
    --bg: #070925;
    --surface-live: linear-gradient(165deg, #33294b 0%, #1d1d4e 48%, #141645 100%);
    --surface-inset: #0c0e33;
    --surface-line: rgba(190, 195, 255, .13);
    --ink: #e7e7fb;
    --dim: #a9acd9;
    --faint: #7074a8;
    --accent: #9be3c9;
    --accent-soft: rgba(155, 227, 201, .13);
    --on-accent: #0b0d33;
    --good: #9be3c9;
    --warn: #ffcf66;
    --bad: #ff7b7b;
    --series-o2: #9be3c9;
    --series-hr: #f0a6c0;
    --sleep-light: #7c80e6;
    --awake: #d9b0a5;
    --chart-phase-awake: rgba(217, 176, 165, .22);
    --pill-hover: rgba(200, 203, 255, .07);
    --radius-control: 999px;
    color-scheme: dark;
  }
  * { box-sizing: border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--ink);
    font: 13.5px/1.25 'Source Sans 3', "Segoe UI", system-ui, sans-serif; overflow:hidden;
    user-select:none; -webkit-user-select:none; cursor:default; }
  /* The window is the card: the plum-tinted live-readings surface edge to edge. */
  body { background:var(--surface-live); }
  #app { position:absolute; inset:0; }
  #frame { position:absolute; inset:0; border:2px solid transparent; border-radius:14px;
    padding:7px 10px 7px 12px; display:flex; flex-direction:column; gap:3px; }
  /* Alert flash: unchanged rhythm (1 s, hard steps), shared alert tokens. */
  #frame.alert-amber { --pulse:var(--warn); animation:pulse 1s steps(1,end) infinite; }
  #frame.alert-red { --pulse:var(--bad); animation:pulse 1s steps(1,end) infinite; }
  @keyframes pulse { 0% { border-color:var(--pulse); box-shadow:inset 0 0 0 2px var(--pulse); }
    50% { border-color:transparent; box-shadow:none; } }
  /* Acknowledged: keeps the alert colour, steady, no flash. */
  #frame.ack-amber { border-color:var(--warn); }
  #frame.ack-red { border-color:var(--bad); }
  #frame.stale .num, #frame.stale .hr, #frame.stale .heart, #frame.stale .mv { color:var(--faint) !important; }
  #frame.stale #spark { opacity:.45; }

  .top { display:flex; align-items:baseline; gap:14px; padding-right:52px; min-height:44px; }
  .num { font-size:44px; font-weight:700; letter-spacing:-0.01em; line-height:1;
    font-variant-numeric:tabular-nums; color:var(--good); }
  .num small { font-size:17px; font-weight:400; color:var(--dim); margin-left:2px; letter-spacing:0; }
  .num.amber { color:var(--warn); } .num.red { color:var(--bad); } .num.none { color:var(--faint); }
  .num.signal-lost { font-size:26px; color:var(--warn); }
  .pulse-wrap { display:flex; align-items:baseline; gap:5px; }
  .heart { width:13px; height:12px; color:var(--series-hr); align-self:center; flex:none; }
  .hr { font-size:20px; font-weight:700; color:var(--series-hr); font-variant-numeric:tabular-nums; }
  .hr small { font-size:12px; font-weight:400; color:var(--dim); margin-left:3px; }
  .hr.none { color:var(--faint); }

  .status-row { display:flex; align-items:center; gap:7px; min-width:0; }
  .status { color:var(--ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    font-variant-numeric:tabular-nums; min-width:0; }
  .status.grey { color:var(--dim); }
  .status.amber { color:var(--warn); font-weight:600; }
  .status.red { color:var(--bad); font-weight:600; }
  .awake { flex:none; color:var(--awake); background:var(--chart-phase-awake); font-weight:600;
    font-size:12px; padding:1px 9px; border-radius:var(--radius-control); }

  /* Expanded: sleep timer at the end of the status line ("Asleep 2h 14m").
     Text plus a shape (crescent asleep, open ring awake), so colour is not the
     only cue; sleep and awake hues only, never the alert colours. */
  .sleep { flex:none; margin-left:auto; display:flex; align-items:center; gap:5px; font-size:12px;
    font-weight:600; padding:1px 9px 1px 7px; border-radius:var(--radius-control);
    background:var(--surface-inset); color:var(--sleep-light); white-space:nowrap;
    font-variant-numeric:tabular-nums; }
  .sleep b { color:var(--ink); font-weight:700; }
  .sleep svg { width:10px; height:10px; flex:none; }
  .sleep.is-awake { color:var(--awake); }
  .sleep[hidden], .size-micro .sleep, .size-compact .sleep { display:none !important; }

  /* Armed dot: mint when armed; amber with "!" on a fault; grey ring when not
     monitoring; dim while checking. Shape differs, so colour is not the only cue. */
  .dot { flex:none; width:8px; height:8px; border-radius:50%; background:var(--faint);
    color:var(--faint); position:relative; }
  .dot.armed { background:var(--accent); color:var(--accent); }
  .dot.idle { background:transparent; border:1.5px solid var(--dim); color:var(--dim); }
  .dot.fault { width:13px; height:13px; background:var(--warn); color:var(--warn); }
  .dot.fault::after { content:"!"; position:absolute; inset:0; display:flex; align-items:center;
    justify-content:center; font-size:10px; font-weight:700; line-height:1; color:var(--on-accent); }
  .dot[hidden] { display:none; }
  /* Feed heartbeat: one short swell per new sample. Two identical animations so
     switching between them restarts it without touching layout. */
  .dot.beat1 { animation:beat1 .6s ease-out 1; }
  .dot.beat2 { animation:beat2 .6s ease-out 1; }
  @keyframes beat1 { 0% { transform:scale(1); box-shadow:0 0 0 0 currentColor; }
    35% { transform:scale(1.6); box-shadow:0 0 0 3px color-mix(in srgb, currentColor 40%, transparent); }
    100% { transform:scale(1); box-shadow:0 0 0 6px transparent; } }
  @keyframes beat2 { 0% { transform:scale(1); box-shadow:0 0 0 0 currentColor; }
    35% { transform:scale(1.6); box-shadow:0 0 0 3px color-mix(in srgb, currentColor 40%, transparent); }
    100% { transform:scale(1); box-shadow:0 0 0 6px transparent; } }
  @media (prefers-reduced-motion: reduce) {
    .dot.beat1 { animation-name:blink1; } .dot.beat2 { animation-name:blink2; }
    @keyframes blink1 { 0%, 100% { opacity:1; } 40% { opacity:.25; } }
    @keyframes blink2 { 0%, 100% { opacity:1; } 40% { opacity:.25; } }
  }

  .extra { display:flex; gap:8px; color:var(--dim); font-size:12px; white-space:nowrap;
    font-variant-numeric:tabular-nums; }
  .extra b { color:var(--ink); font-weight:600; overflow:hidden; text-overflow:ellipsis; max-width:120px; }
  .ctrls { position:absolute; top:4px; right:6px; display:flex; gap:2px; }
  .ctrls button { all:unset; cursor:pointer; color:var(--dim); font-size:15px; width:22px;
    height:22px; text-align:center; line-height:22px; border-radius:var(--radius-control); }
  .ctrls button:hover { color:var(--ink); background:var(--pill-hover); }
  #snooze { all:unset; cursor:pointer; position:absolute; right:10px; bottom:9px;
    padding:8px 16px; border-radius:var(--radius-control); font-weight:700; font-size:14px;
    color:var(--on-accent); background:var(--warn); }
  #snooze.red, #msnooze.red { background:var(--bad); }
  #snooze[hidden], #msnooze[hidden], .ctrls[hidden], .awake[hidden], #awakeMark[hidden] { display:none; }
  .size-compact.has-snooze #extras { padding-right:112px; }
  .size-expanded.has-snooze .status-row { padding-right:118px; }

  /* Expanded: the 10-minute oxygen trace with threshold lines. */
  #sparkBox { display:none; flex:1; min-height:0; flex-direction:column; gap:2px; margin-top:3px; }
  #spark { position:relative; flex:1; min-height:36px; background:var(--surface-inset);
    border-radius:10px; overflow:hidden; }
  #spark svg { position:absolute; inset:4px 26px 4px 6px; width:calc(100% - 32px); height:calc(100% - 8px); overflow:visible; }
  #spark .trace { fill:none; stroke:var(--series-o2); stroke-width:1.8; stroke-linejoin:round; stroke-linecap:round; }
  #spark .th { stroke-width:1; stroke-dasharray:3 3; }
  #spark .th.warn { stroke:var(--warn); } #spark .th.crit { stroke:var(--bad); }
  #spark .lbl { position:absolute; right:5px; transform:translateY(-50%); font-size:10.5px; font-weight:600;
    font-variant-numeric:tabular-nums; }
  #spark .lbl.warn { color:var(--warn); } #spark .lbl.crit { color:var(--bad); }
  #spark .end { position:absolute; width:6px; height:6px; margin:-3px 0 0 -3px; border-radius:50%; background:var(--good); }
  #spark .end.amber { background:var(--warn); } #spark .end.red { background:var(--bad); }
  /* Empty/loading text: kept inside the trace area (clear of the label gutter)
     on a solid inset-coloured pill drawn over the threshold lines. */
  #spark .msg { position:absolute; inset:0 26px 0 6px; display:flex; align-items:center; justify-content:center;
    z-index:1; pointer-events:none; }
  #spark .msg span { background:var(--surface-inset); color:var(--dim); font-size:12px; line-height:1.3;
    padding:2px 8px; border-radius:var(--radius-control); white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; max-width:100%; }
  .ticks { display:flex; justify-content:space-between; color:var(--dim); font-size:11px; padding:0 2px; }
  .ticks span:last-child { color:var(--accent); }

  /* Micro pill: one line, dot + "97 · 147" or a short word; Snooze in alerts. */
  #mrow { display:none; align-items:center; gap:6px; height:100%; min-width:0; position:relative; }
  .mtext { font-size:15px; font-weight:700; white-space:nowrap; overflow:hidden; font-variant-numeric:tabular-nums;
    color:var(--ink); min-width:0; }
  .mtext .ok { color:var(--good); } .mtext .amber { color:var(--warn); } .mtext .red { color:var(--bad); }
  .mtext .mv { color:var(--series-hr); } .mtext .sep, .mtext .word { color:var(--dim); }
  .mtext .sep { font-weight:400; margin:0 3px; }
  #msnooze { all:unset; cursor:pointer; flex:none; margin-left:auto; padding:2px 6px; border-radius:var(--radius-control);
    font-weight:700; font-size:11.5px; line-height:1.2; color:var(--on-accent); background:var(--warn); }
  .has-snooze #mrow { gap:4px; }
  .has-snooze .mtext { font-size:14px; }
  #awakeMark { position:absolute; top:3px; right:-4px; width:6px; height:6px; border-radius:50%; background:var(--awake); }

  .size-micro #frame { border-radius:var(--radius-control); padding:0 7px 0 9px; justify-content:center; }
  .size-micro.has-snooze #frame { padding:0 4px 0 7px; }
  .size-micro .top, .size-micro .status-row, .size-micro #extras, .size-micro .ctrls,
  .size-micro #snooze, .size-micro #sparkBox { display:none !important; }
  .size-micro #mrow { display:flex; }
  .size-expanded #sparkBox { display:flex; }
  .size-expanded #snooze { bottom:auto; top:47px; padding:5px 13px; font-size:13px; }
</style>
</head>
<body>
<div id="app" class="size-compact">
<div id="frame">
  <div class="top">
    <div class="num none" id="spo2">--<small>%</small></div>
    <div class="pulse-wrap">
      <svg class="heart" viewBox="0 0 13 12" aria-hidden="true"><path d="M6.5 11 1.6 6.2A3 3 0 0 1 6 2.1l.5.5.5-.5a3 3 0 0 1 4.4 4.1z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>
      <div class="hr" id="hr">--<small>bpm</small></div>
    </div>
  </div>
  <div class="status-row">
    <span class="dot" id="dot" role="img" hidden></span>
    <span class="status grey" id="status">Connecting…</span>
    <span class="awake" id="awake" hidden>Awake</span>
    <span class="sleep" id="sleep" hidden></span>
  </div>
  <div id="extras"></div>
  <div id="sparkBox">
    <div id="spark"></div>
    <div class="ticks"><span>10 min ago</span><span>now</span></div>
  </div>
  <div id="mrow">
    <span class="dot" id="mdot" role="img" hidden></span>
    <span class="mtext" id="micro"><span class="word">Connecting</span></span>
    <button id="msnooze" hidden>Snooze</button>
    <span id="awakeMark" title="Awake" aria-label="Awake" hidden></span>
  </div>
  <div class="ctrls" id="ctrls" hidden>
    <button id="open" title="Open dashboard">&#x2922;</button>
    <button id="close" title="Hide widget (re-open from the tray)">&times;</button>
  </div>
  <button id="snooze" hidden>Snooze 15 s</button>
</div>
</div>
<script>
(function () {
  var T = window.__TAURI__;
  var invoke = T && T.core && T.core.invoke;
  var $ = function (id) { return document.getElementById(id); };
  var last = null, lastAt = 0, failed = false;
  var SIGNAL_LOST_SECONDS = 600;
  var ARMED_SILENCE_MS = 15000;
  var AWAKE_NOTE_MS = 60000;
  var SERIES_MIN_MS = 30000;
  var SERIES_SPAN_MS = 600000;
  var SLEEP_FRESH_MS = 180000;   // older sleep data (failed refetches) is not shown
  var SLEEP_CAP_MIN = 960;       // the server looks back 16 h at most
  var armed = null, armedAt = 0;
  var lastSample = null, beat = 0, sampleSinceSeries = false;
  var awakeSeen = false, awakeId = null, awakeUntil = 0;
  var series = null, seriesAt = 0, seriesBusy = false, seriesOkAt = 0;
  var size = "compact";

  if (invoke) {
    $("ctrls").hidden = false;
    $("open").onclick = function () { invoke("show_main").catch(function () {}); };
    $("close").onclick = function () { invoke("hide_widget").catch(function () {}); };
    // Frameless window: drag from anywhere that isn't a button.
    document.addEventListener("mousedown", function (e) {
      if (e.button !== 0 || e.target.closest("button")) return;
      try { T.window.getCurrentWindow().startDragging(); } catch (err) {}
    });
  }

  // The one snooze path, shared by the compact/expanded button and the micro one.
  function snoozeNow() {
    var snooze = invoke ? invoke("snooze_alert") :
      fetch("/api/alert/snooze", { method: "POST", credentials: "same-origin" });
    snooze.catch(function () {}).then(refresh);
  }
  $("snooze").onclick = snoozeNow;
  $("msnooze").onclick = snoozeNow;

  // Layout follows the window, not a setting; ?size= overrides for testing.
  function chooseSize() {
    var search = (window.location && window.location.search) || "";
    var m = /[?&]size=(micro|compact|expanded)(?:&|$)/.exec(search);
    if (m) return m[1];
    var w = window.innerWidth, h = window.innerHeight;
    if (!w || !h) return "compact";
    if (h <= 64 || w <= 180) return "micro";
    if (h >= 170) return "expanded";
    return "compact";
  }
  if (window.addEventListener) window.addEventListener("resize", function () { render(); });

  function tier(spo2, d) {
    if (spo2 == null) return "none";
    if (spo2 < d.critical_threshold) return "red";
    if (spo2 < d.warn_threshold) return "amber";
    return "ok";
  }

  function ago(sec) {
    if (sec < 60) return sec + "s";
    if (sec < 3600) return Math.floor(sec / 60) + "m";
    return Math.floor(sec / 3600) + "h";
  }

  function left(sec) {
    sec = Math.max(0, sec);
    return sec < 60 ? sec + "s" : Math.ceil(sec / 60) + "m";
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function setStatus(text, cls) {
    var el = $("status");
    el.textContent = text;
    el.className = cls === true || cls === "grey" ? "status grey" : cls ? "status " + cls : "status";
  }

  // ---- armed state (desktop only) -------------------------------------------
  function pollArmed() {
    if (!invoke) return;
    var p;
    try { p = invoke("armed_state"); } catch (e) { return; }
    Promise.resolve(p).then(function (r) {
      if (r && typeof r.armed === "boolean") { armed = r; armedAt = Date.now(); render(); }
    }, function () {});
  }

  // {cls, text, status, statusCls} or null when there's no dot (browser).
  function armedView() {
    if (!invoke) return null;
    if (!armedAt) return { cls: "checking", text: "Checking alert status", status: "Checking alert status", statusCls: "grey" };
    if (Date.now() - armedAt > ARMED_SILENCE_MS)
      return { cls: "fault", text: "Alert status unknown", status: "Alert status unknown", statusCls: "" };
    var problems = armed.problems || [];
    if (armed.armed && !problems.length) return { cls: "armed", text: "Alerts armed on this PC", status: null };
    var first = problems[0];
    if (!first) return { cls: "fault", text: "Alert status unknown", status: "Alert status unknown", statusCls: "" };
    if (first.code === "not_collecting") {
      var idle = "Not monitoring · " + first.text;
      return { cls: "idle", text: idle, status: idle, statusCls: "grey" };
    }
    return { cls: "fault", text: String(first.text), status: String(first.text), statusCls: "" };
  }

  function renderDots(view) {
    ["dot", "mdot"].forEach(function (id) {
      var el = $(id);
      el.hidden = !view;
      if (!view) return;
      el.className = "dot " + view.cls + (beat ? " beat" + beat : "");
      el.title = view.text;
      if (el.setAttribute) el.setAttribute("aria-label", view.text);
    });
  }

  // ---- feed heartbeat and awake note -----------------------------------------
  function newestSample(s) {
    var best = null;
    (s.devices || []).forEach(function (x) {
      if (x.device_serial === "TEST" || !x.recorded_at) return;
      var t = Date.parse(x.recorded_at);
      if (!isNaN(t) && (best == null || t > best)) best = t;
    });
    return best;
  }

  function observe(s) {
    var t = newestSample(s);
    if (t != null) {
      if (lastSample != null && t > lastSample) { beat = beat === 1 ? 2 : 1; sampleSinceSeries = true; }
      if (lastSample == null || t > lastSample) lastSample = t;
    }
    var id = s.awake_event_id == null ? null : s.awake_event_id;
    if (!awakeSeen) { awakeSeen = true; awakeId = id; }   // first value: silent
    else if (id != null && id !== awakeId) { awakeId = id; awakeUntil = Date.now() + AWAKE_NOTE_MS; }
  }

  // ---- expanded: 10-minute oxygen trace ----------------------------------------
  function maybeFetchSeries(d, s) {
    if (size !== "expanded" || seriesBusy || failed) return;
    var now = Date.now(), since = now - seriesAt;
    var switched = series && d && s.level !== "none" && d.device_serial !== "TEST" &&
      d.device_serial !== series.device_serial && since >= 5000;
    if (series && !switched && !(sampleSinceSeries && since >= SERIES_MIN_MS)) return;
    if (!series && seriesAt && since < SERIES_MIN_MS) return;   // retry a failed fetch slowly
    seriesBusy = true; seriesAt = now; sampleSinceSeries = false;
    fetch("/api/widget-series?minutes=10", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { series = j; seriesOkAt = Date.now(); seriesBusy = false; render(); })
      .catch(function () { seriesBusy = false; });
  }

  function drawSpark(d) {
    var el = $("spark");
    if (!series) { el.innerHTML = '<div class="msg"><span>Loading oxygen trend…</span></div>'; return; }
    var now = Date.now(), start = now - SERIES_SPAN_MS;
    var warn = Number(series.warn_threshold), crit = Number(series.critical_threshold);
    var pts = [];
    (series.points || []).forEach(function (p) {
      var t = Date.parse(p.t);
      if (!isNaN(t) && p.spo2 != null && t >= start - 60000) pts.push([t, Number(p.spo2)]);
    });
    // Append the live reading so the trace keeps up between fetches.
    if (d && d.device_serial === series.device_serial && d.sock_reporting && !d.stale &&
        d.spo2 != null && d.recorded_at) {
      var lt = Date.parse(d.recorded_at);
      if (!isNaN(lt) && (!pts.length || lt > pts[pts.length - 1][0])) pts.push([lt, Number(d.spo2)]);
    }
    var lo = crit - 3;
    pts.forEach(function (p) { if (p[1] - 1 < lo) lo = p[1] - 1; });
    var hi = 100;
    var y = function (v) { return Math.max(0, Math.min(100, (hi - v) / (hi - lo) * 100)); };
    var x = function (t) { return Math.max(0, Math.min(100, (t - start) / SERIES_SPAN_MS * 100)); };
    var f = function (n) { return Math.round(n * 10) / 10; };
    var svg = '<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">' +
      '<line class="th warn" data-th="warn" x1="0" x2="100" y1="' + f(y(warn)) + '" y2="' + f(y(warn)) + '" vector-effect="non-scaling-stroke"/>' +
      '<line class="th crit" data-th="crit" x1="0" x2="100" y1="' + f(y(crit)) + '" y2="' + f(y(crit)) + '" vector-effect="non-scaling-stroke"/>';
    var seg = [], prev = null;
    var flush = function () {
      if (seg.length) svg += '<polyline class="trace" points="' + seg.join(" ") + '" vector-effect="non-scaling-stroke"/>';
      seg = [];
    };
    pts.forEach(function (p) {
      if (prev && p[0] - prev[0] > 90000) flush();     // a gap in the feed stays a gap
      seg.push(f(x(p[0])) + "," + f(y(p[1])));
      prev = p;
    });
    flush();
    svg += "</svg>";
    // Labels sit in the right gutter, at the same height as their lines.
    var html = svg +
      '<span class="lbl warn" style="top:calc(4px + (100% - 8px) * ' + f(y(warn) / 100) + ')">' + esc(warn) + "</span>" +
      '<span class="lbl crit" style="top:calc(4px + (100% - 8px) * ' + f(y(crit) / 100) + ')">' + esc(crit) + "</span>";
    if (pts.length) {
      var e = pts[pts.length - 1];
      var et = e[1] < crit ? " red" : e[1] < warn ? " amber" : "";
      html += '<span class="end' + et + '" style="left:calc(6px + (100% - 32px) * ' + f(x(e[0]) / 100) +
        '); top:calc(4px + (100% - 8px) * ' + f(y(e[1]) / 100) + ')"></span>';
    } else {
      // Centre the text in the larger clear band: above the warn line or
      // below the critical line, so it never sits on a threshold.
      var yw = y(warn) / 100, yc = y(crit) / 100;
      var band = yw >= 1 - yc ? [0, 1 - yw] : [yc, 0];
      html += '<div class="msg" style="top:calc(4px + (100% - 8px) * ' + f(band[0]) +
        '); bottom:calc(4px + (100% - 8px) * ' + f(band[1]) + ')"><span>No oxygen readings in the last 10 min</span></div>';
    }
    el.innerHTML = html;
  }

  // ---- expanded: sleep timer ----------------------------------------------------
  function sleepFor(ms) {
    var m = Math.floor(Math.max(0, ms) / 60000);
    if (m >= SLEEP_CAP_MIN) return "16h+";
    if (m < 1) return "<1m";
    var h = Math.floor(m / 60), r = m % 60;
    return h ? h + "h " + (r < 10 ? "0" : "") + r + "m" : r + "m";
  }

  // {state, label, dur} or null. `plain` is true only when the status line is
  // the ordinary "Updated" text with nothing alerting, snoozed, acknowledged
  // or wrong, so the timer can never cover, displace or delay any of those.
  // It ticks from `since` on every render; data comes with the series fetch.
  function sleepView(d, plain) {
    if (size !== "expanded" || !plain || !d || !series || !series.sleep) return null;
    var sl = series.sleep;
    if (sl.state !== "asleep" && sl.state !== "awake") return null;
    if (series.device_serial !== d.device_serial) return null;
    if (!d.sock_reporting || d.stale || d.signal_lost || d.charging) return null;
    if (Date.now() - seriesOkAt > SLEEP_FRESH_MS) return null;
    var since = Date.parse(sl.since);
    if (isNaN(since)) return null;
    return { state: sl.state, label: sl.state === "asleep" ? "Asleep" : "Awake", dur: sleepFor(Date.now() - since) };
  }

  var SLEEP_ICONS = {
    asleep: '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M6.6 1.2a4 4 0 1 0 2.2 6.3A3.3 3.3 0 0 1 6.6 1.2z" fill="currentColor"/></svg>',
    awake: '<svg viewBox="0 0 10 10" aria-hidden="true"><circle cx="5" cy="5" r="3.4" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>'
  };

  function renderSleep(view) {
    var el = $("sleep");
    el.hidden = !view;
    if (!view) return;
    el.className = view.state === "awake" ? "sleep is-awake" : "sleep";
    el.innerHTML = SLEEP_ICONS[view.state] + esc(view.label) + " <b>" + esc(view.dur) + "</b>";
    el.title = view.label + " " + view.dur;
  }

  // ---- micro pill ----------------------------------------------------------------
  function word(text, cls) { return '<span class="' + (cls || "word") + '">' + esc(text) + "</span>"; }
  function sep() { return '<span class="sep">·</span>'; }

  function microFailed(wasAlerting) {
    return wasAlerting ? word("Signal lost", "amber") : word("No server");
  }

  function micro(s, d, view) {
    // view: {spo2, hr, t, reporting}
    var lead;
    if (!d) lead = word(!s.collecting && !s.test ? "Off" : "Waiting");
    else if (d.signal_lost) lead = word("Signal lost", "amber");
    else if (view.spo2 == null) lead = word(d.stale ? "No data" : d.charging ? "Charging" : !d.sock_reporting ? "Sock off" : "--");
    else lead = word(view.spo2, view.t === "none" ? "word" : view.t);
    var elapsed = Math.floor((Date.now() - lastAt) / 1000);
    var active = s.active_level === "amber" || s.active_level === "red";
    // "Signal lost" fits alone; beside the Snooze button or a countdown, "Lost".
    // Returns [full, fallback]: the fallback drops the time left when the full
    // text doesn't fit the pill (long countdowns, or the Segoe UI fallback font).
    var short = d && d.signal_lost ? word("Lost", "amber") : lead;
    if (active) return [short, short];             // + the Snooze button
    if (s.acknowledged) return [short + sep() + word("Ack " + left((s.ack_seconds_left || 0) - elapsed)),
      short + sep() + word("Ack")];
    if (s.snoozed) return [short + sep() + word("Zz " + Math.max(0, s.snooze_seconds_left - elapsed) + "s"),
      short + sep() + word("Zz")];
    if (view.spo2 != null && !d.signal_lost) {
      var both = lead + sep() + (view.hr == null ? word("--") : word(view.hr, "mv"));
      return [both, both];
    }
    return [lead, lead];
  }

  function setMicro(parts) {
    var el = $("micro");
    el.innerHTML = parts[0];
    el.title = String(el.textContent || "");
    if (parts[1] !== parts[0] && el.scrollWidth > el.clientWidth) el.innerHTML = parts[1];
  }

  // ---- render --------------------------------------------------------------------
  function render() {
    size = chooseSize();
    var frame = $("frame");
    var aview = armedView();
    var appCls = "size-" + size;
    renderDots(aview);
    if (failed) {
      // Mirrors the shell: warn only if an alert was active within the last
      // 10 minutes; otherwise the server is simply down.
      var sinceLast = (Date.now() - lastAt) / 1000;
      var wasAlerting = last && (last.level === "amber" || last.level === "red") &&
        sinceLast < SIGNAL_LOST_SECONDS;
      var snoozed = last && last.snoozed && sinceLast < last.snooze_seconds_left;
      frame.className = wasAlerting && !snoozed ? "alert-amber" : "stale";
      $("spo2").className = wasAlerting ? "num signal-lost" : "num";
      $("spo2").textContent = wasAlerting ? "Signal lost" : "--";
      $("hr").innerHTML = "--<small>bpm</small>";
      $("hr").className = "hr none";
      $("extras").innerHTML = "";
      $("snooze").hidden = true;
      $("msnooze").hidden = true;
      $("awake").hidden = true;
      $("awakeMark").hidden = true;
      renderSleep(null);
      $("micro").innerHTML = microFailed(wasAlerting);
      $("app").className = appCls;
      setStatus("Dashboard server not responding", true);
      return;
    }
    var s = last;
    if (!s) { renderSleep(null); $("micro").innerHTML = word("Connecting"); $("app").className = appCls; return; }
    var elapsed = Math.floor((Date.now() - lastAt) / 1000);
    var devs = s.devices || [];
    var d = devs.find(function (x) { return s.level !== "none" && x.level === s.level && x.signal_lost; }) ||
      devs.find(function (x) { return s.level !== "none" && x.level === s.level; }) || devs[0];
    var active = s.active_level;
    var alerting = active === "amber" || active === "red";
    var level = s.level === "amber" || s.level === "red" ? s.level : null;

    var spo2 = null, hr = null, t = "none";
    if (d) {
      var reporting = d.sock_reporting && !d.stale;
      spo2 = reporting ? d.spo2 : null;
      hr = reporting ? d.heart_rate : null;
      t = tier(spo2, d);
    }
    // Acknowledged keeps the underlying alert colour (server `level`); once the
    // level has recovered inside the grace period, the reading's tier decides.
    var ackTier = s.acknowledged ? (level || (t === "amber" || t === "red" ? t : null)) : null;

    var cls = [];
    if (alerting) cls.push("alert-" + active);
    else if (ackTier) cls.push("ack-" + ackTier);
    if (d && d.stale && !d.signal_lost) cls.push("stale");
    frame.className = cls.join(" ");

    var sn = $("snooze"), msn = $("msnooze");
    var secs = s.snooze_seconds == null ? 15 : s.snooze_seconds;
    sn.textContent = "Snooze " + secs + " s";
    sn.hidden = msn.hidden = !alerting;
    sn.className = msn.className = active === "red" ? "red" : "";
    if (alerting) appCls += " has-snooze";

    if (d) {
      $("spo2").className = d.signal_lost ? "num signal-lost" : "num " + t;
      $("spo2").innerHTML = d.signal_lost ? "Signal lost" :
        (spo2 == null ? "--" : esc(spo2)) + "<small>%</small>";
      $("hr").innerHTML = (hr == null ? "--" : esc(hr)) + "<small>bpm</small>";
    } else {
      $("spo2").className = "num none";
      $("spo2").innerHTML = "--<small>%</small>";
      $("hr").innerHTML = "--<small>bpm</small>";
    }
    $("hr").className = hr == null ? "hr none" : "hr";

    var aview2 = aview && aview.status ? aview : null;
    var plainStatus = false;
    // An active (unacknowledged) alert always wins; the server never sends
    // both, but the widget must not let "Acknowledged" mask one if it did.
    if (s.acknowledged && !alerting) setStatus("Acknowledged — " + (d && d.signal_lost ? "signal lost" : spo2 == null ? "no reading" : spo2 + "%") +
      " · " + left((s.ack_seconds_left || 0) - elapsed) + " left", ackTier || "");
    else if (s.snoozed) setStatus("Snoozed " + Math.max(0, s.snooze_seconds_left - elapsed) + "s", false);
    else if (d && d.signal_lost) setStatus("Signal lost — check sock connection", false);
    else if (alerting) setStatus((d && d.device_serial === "TEST" ? "Test alert" :
      active === "red" ? "Low oxygen" : "Oxygen warning") +
      (d ? " · " + ago((d.age_seconds || 0) + elapsed) + " ago" : ""), active);
    else if (aview2) setStatus(aview2.status, aview2.statusCls);
    else if (!s.collecting && !s.test) setStatus(s.remote_mode ?
      "Alerts unavailable — viewing a remote server" : "No Owlet account linked", true);
    else if (!d) setStatus("Waiting for data", true);
    else if (d.stale) setStatus("No data for " + ago((d.age_seconds || 0) + elapsed), true);
    else if (!d.sock_reporting) setStatus(d.charging ? "Charging" : "Sock off", true);
    else { setStatus("Updated " + ago((d.age_seconds || 0) + elapsed) + " ago", false); plainStatus = true; }

    // Awake note: soft, never while any amber/red level is present.
    var awake = Date.now() < awakeUntil && !level && !alerting;
    $("awake").hidden = !awake;
    $("awakeMark").hidden = !awake;
    // Sleep timer: never beside an alert, snooze, acknowledge, signal loss or
    // the awake note; the status line it sits on must be the plain one.
    renderSleep(sleepView(d, plainStatus && !alerting && !level && !s.acknowledged &&
      !s.snoozed && !awake));

    $("app").className = appCls;   // before measuring the micro text
    setMicro(micro(s, d, { spo2: spo2, hr: hr, t: t }));

    var html = "";
    for (var i = 0; i < devs.length; i++) {
      var x = devs[i];
      if (x === d) continue;
      var xo = x.sock_reporting && !x.stale;
      var xt = xo ? tier(x.spo2, x) : "none";
      var col = xt === "red" ? "var(--bad)" : xt === "amber" ? "var(--warn)" : xt === "ok" ? "var(--good)" : "var(--dim)";
      html += '<div class="extra"><b>' + esc(x.account_name || x.device_serial) + "</b>" +
        '<span style="color:' + col + '">' + (x.signal_lost ? "Signal lost" : xo && x.spo2 != null ? esc(x.spo2) + "%" : (x.stale ? "no data" : "off")) + "</span>" +
        '<span style="color:var(--series-hr)">' + (xo && x.heart_rate != null ? esc(x.heart_rate) + " bpm" : "") + "</span></div>";
    }
    $("extras").innerHTML = html;

    $("app").className = appCls;
    if (size === "expanded") { maybeFetchSeries(d, s); drawSpark(d); }
  }

  function refresh() {
    pollArmed();
    return fetch("/api/alert-state", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { last = j; lastAt = Date.now(); failed = false; observe(j); render(); })
      .catch(function () { failed = true; render(); });
  }

  render();
  refresh();
  setInterval(refresh, 2000);
  setInterval(render, 1000);
})();
</script>
</body>
</html>
"""


def render() -> str:
    return WIDGET_HTML
