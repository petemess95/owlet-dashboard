"""Always-on-top desktop widget: live SpO2 / heart rate / freshness, plus the
amber/red alert state and a snooze button. Self-contained (inline CSS + JS, no
external assets) so it renders instantly in a 300x120 frameless window, and it
still works when opened in a normal browser (the Tauri-only buttons hide)."""

from __future__ import annotations

WIDGET_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Owlet widget</title>
<style>
  :root { --bg:#14171c; --surface:#1c2027; --ink:#eef1f5; --dim:#9aa3ae; --faint:#646d78;
    --ok:#3ecf8e; --amber:#ff9f1a; --red:#ff4d4d; --line:#2a3039; }
  * { box-sizing: border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--ink);
    font: 13px/1.25 "Segoe UI", system-ui, -apple-system, sans-serif; overflow:hidden;
    user-select:none; -webkit-user-select:none; cursor:default; }
  #frame { position:absolute; inset:0; border:2px solid var(--line); border-radius:10px;
    padding:8px 10px 8px 12px; display:flex; flex-direction:column; gap:4px; }
  #frame.alert-amber { --pulse:var(--amber); animation:pulse 1s steps(1,end) infinite; }
  #frame.alert-red { --pulse:var(--red); animation:pulse 1s steps(1,end) infinite; }
  @keyframes pulse { 0% { border-color:var(--pulse); box-shadow:inset 0 0 0 2px var(--pulse); }
    50% { border-color:var(--line); box-shadow:none; } }
  #frame.stale .num, #frame.stale .hr { color:var(--faint) !important; }
  .top { display:flex; align-items:baseline; gap:12px; }
  .num { font-size:44px; font-weight:650; letter-spacing:-0.03em; line-height:1;
    font-variant-numeric:tabular-nums; color:var(--ok); }
  .num small { font-size:18px; font-weight:500; color:var(--dim); margin-left:1px; }
  .num.amber { color:var(--amber); } .num.red { color:var(--red); } .num.none { color:var(--faint); }
  .hr { font-size:18px; color:var(--ink); font-variant-numeric:tabular-nums; }
  .hr small { font-size:12px; color:var(--dim); margin-left:2px; }
  .status { color:var(--dim); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    font-variant-numeric:tabular-nums; }
  .status.grey { color:var(--faint); }
  .extra { display:flex; gap:8px; color:var(--dim); font-size:12px; white-space:nowrap;
    font-variant-numeric:tabular-nums; }
  .extra b { color:var(--ink); font-weight:600; overflow:hidden; text-overflow:ellipsis; max-width:120px; }
  .ctrls { position:absolute; top:4px; right:6px; display:flex; gap:2px; }
  .ctrls button { all:unset; cursor:pointer; color:var(--faint); font-size:15px; width:22px;
    height:22px; text-align:center; line-height:22px; border-radius:5px; }
  .ctrls button:hover { color:var(--ink); background:var(--surface); }
  #snooze { all:unset; cursor:pointer; position:absolute; right:10px; bottom:9px;
    padding:9px 14px; border-radius:8px; font-weight:700; font-size:14px; color:#111;
    background:var(--amber); }
  #snooze.red { background:var(--red); color:#fff; }
  #snooze[hidden], .ctrls[hidden] { display:none; }
</style>
</head>
<body>
<div id="frame">
  <div class="top">
    <div class="num none" id="spo2">--<small>%</small></div>
    <div class="hr" id="hr">--<small>bpm</small></div>
  </div>
  <div class="status grey" id="status">Connecting…</div>
  <div id="extras"></div>
  <div class="ctrls" id="ctrls" hidden>
    <button id="open" title="Open dashboard">&#x2922;</button>
    <button id="close" title="Hide widget (re-open from the tray)">&times;</button>
  </div>
  <button id="snooze" hidden>Snooze 15 s</button>
</div>
<script>
(function () {
  var T = window.__TAURI__;
  var invoke = T && T.core && T.core.invoke;
  var $ = function (id) { return document.getElementById(id); };
  var last = null, lastAt = 0, failed = false;

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

  $("snooze").onclick = function () {
    fetch("/api/alert/snooze", { method: "POST", credentials: "same-origin" })
      .catch(function () {}).then(refresh);
  };

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

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function setStatus(text, grey) {
    var el = $("status");
    el.textContent = text;
    el.className = grey ? "status grey" : "status";
  }

  function render() {
    var frame = $("frame");
    if (failed) {
      frame.className = "stale";
      $("snooze").hidden = true;
      setStatus("Dashboard server not responding", true);
      return;
    }
    var s = last;
    if (!s) return;
    var elapsed = Math.floor((Date.now() - lastAt) / 1000);
    var devs = s.devices || [];
    var d = devs[0];
    var active = s.active_level;
    frame.className = (active === "amber" || active === "red") ? "alert-" + active : "";
    var sn = $("snooze");
    sn.hidden = !(active === "amber" || active === "red");
    sn.className = active === "red" ? "red" : "";

    if (d) {
      var reporting = d.sock_reporting && !d.stale;
      var spo2 = reporting ? d.spo2 : null;
      var hr = reporting ? d.heart_rate : null;
      var t = tier(spo2, d);
      $("spo2").className = "num " + t;
      $("spo2").innerHTML = (spo2 == null ? "--" : esc(spo2)) + "<small>%</small>";
      $("hr").innerHTML = (hr == null ? "--" : esc(hr)) + "<small>bpm</small>";
      if (d.stale) frame.className += " stale";
    } else {
      $("spo2").className = "num none";
      $("spo2").innerHTML = "--<small>%</small>";
      $("hr").innerHTML = "--<small>bpm</small>";
    }

    if (!s.collecting && !s.test) setStatus("Alerts unavailable — viewing a remote server", true);
    else if (!d) setStatus("Waiting for data", true);
    else if (d.stale) setStatus("No data for " + ago((d.age_seconds || 0) + elapsed), true);
    else if (!d.sock_reporting) setStatus(d.charging ? "Charging" : "Sock off", true);
    else if (s.snoozed) setStatus("Snoozed " + Math.max(0, s.snooze_seconds_left - elapsed) + "s", false);
    else setStatus("Updated " + ago((d.age_seconds || 0) + elapsed) + " ago", false);

    var html = "";
    for (var i = 1; i < devs.length; i++) {
      var x = devs[i];
      var xo = x.sock_reporting && !x.stale;
      var xt = xo ? tier(x.spo2, x) : "none";
      var col = xt === "red" ? "var(--red)" : xt === "amber" ? "var(--amber)" : xt === "ok" ? "var(--ok)" : "var(--faint)";
      html += '<div class="extra"><b>' + esc(x.account_name || x.device_serial) + "</b>" +
        '<span style="color:' + col + '">' + (xo && x.spo2 != null ? esc(x.spo2) + "%" : (x.stale ? "no data" : "off")) + "</span>" +
        "<span>" + (xo && x.heart_rate != null ? esc(x.heart_rate) + " bpm" : "") + "</span></div>";
    }
    $("extras").innerHTML = html;
  }

  function refresh() {
    return fetch("/api/alert-state", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { last = j; lastAt = Date.now(); failed = false; render(); })
      .catch(function () { failed = true; render(); });
  }

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
