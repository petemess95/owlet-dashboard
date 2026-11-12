"""The Data workbench: full charts, tables, exports. Rendered through the shared
app shell for signed-in users; share links get a standalone themed page."""

from app.shell import THEME_RESOLVER, render_shell

DATA_HEAD = """  <meta name="theme-color" content="#070925" />
  __PWA_HEAD__
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.2.0/dist/chartjs-plugin-zoom.min.js"></script>
<link rel="stylesheet" href="/static/pages/data.css" />"""

DATA_BODY = r"""
  <div id="initialLoading" class="initial-loading" role="status" aria-live="polite">
    <div class="loading-card">
      <span class="loading-spinner" aria-hidden="true"></span>
      <span>
        <span class="loading-title">Loading Owlet data…</span>
        <span id="initialLoadingMessage" class="loading-message">Connecting to local dashboard.</span>
      </span>
    </div>
  </div>
  <main class="shell">
    <p class="legacy-note">This is the original <b>V1 data workbench</b> — dense, but every raw
      reading lives here. Day to day, the <a href="/">Today</a>, <a href="/night">Tonight</a>,
      and <a href="/rhythms">Rhythms</a> pages are the modern views.</p>

    <section class="toolbar" aria-label="Date and data controls">
      <div class="control-group filter-cluster">
        <label class="device-label" for="deviceSelect">Device</label>
        <select id="deviceSelect"><option value="">Loading devices…</option></select>
      </div>
      <div class="control-group refresh-cluster toolbar-right">
        <button id="dailyInsightsToggle" class="daily-insights-button" type="button" aria-label="Daily insights"><span class="desktop-label">Daily insights</span><span class="mobile-label" aria-hidden="true">📊</span></button>
        <button id="challengesToggle" class="challenge-button" type="button" aria-expanded="false" aria-label="O₂ challenges"><span class="desktop-label">O₂ challenges</span><span class="mobile-label" aria-hidden="true">O₂ Ch.</span> <span id="challengeCount" class="challenge-count">0</span></button>
        <div id="challengesPanel" class="challenge-popover hidden" role="dialog" aria-modal="true" aria-label="Oxygen challenges">
          <div class="panel-title">
            <h2>Oxygen challenges</h2>
            <div class="control-group"><span class="small" id="challengePage">—</span><button id="closeChallengesPanel" type="button">Close</button></div>
          </div>
          <div class="control-group challenge-actions" id="challengeActions" style="justify-content: space-between; margin-bottom: 10px;">
            <button id="addChallenge" class="primary wide" type="button">Add new O₂ challenge</button>
            <button id="startChallenge" type="button">Start now</button>
            <button id="endChallenge" type="button">End active</button>
            <button id="markVisibleChallenge" class="wide" type="button">Use visible chart window</button>
          </div>
          <div id="challengeList" class="challenge-list"></div>
        </div>
    </section>

    <section id="glanceStrip" class="glance-strip" aria-label="At a glance">
      <article class="card glance-card">
        <span class="eyebrow">O₂ now + today</span>
        <strong id="latestOxygen">—</strong>
        <small><span class="inline-stat" id="todayOxygen">—</span> today avg · <span class="inline-stat" id="o2Compare">—</span></small>
        <small>Yesterday by now <span class="inline-stat" id="priorOxygen">—</span></small>
      </article>
      <article class="card glance-card">
        <span class="eyebrow">Heart rate</span>
        <strong id="latestHr">—</strong>
        <small><span class="inline-stat" id="avgHr">—</span> today avg · <span class="inline-stat" id="hrCompare">—</span></small>
        <small>State <span class="inline-stat" id="latestState">—</span> · Move <span class="inline-stat" id="latestMove">—</span></small>
      </article>
      <article class="card glance-card">
        <span class="eyebrow">Sleep</span>
        <strong id="sleepTotal">—</strong>
        <small id="sleepSummary">Estimated from intervals.</small>
        <div class="progress glance-progress" title="Light sleep / deep sleep / awake">
          <span id="lightBar"></span><span id="deepBar"></span><span id="awakeBar"></span>
        </div>
        <small>Light <span class="inline-stat" id="lightSleep">—</span> · Deep <span class="inline-stat" id="deepSleep">—</span> · Awake <span class="inline-stat" id="awakeTime">—</span></small>
      </article>
    </section>

    <section class="chart-stack" aria-label="Primary charts">
      <div class="panel chart-panel primary-chart">
        <div class="panel-title">
          <div>
            <h2>Main vitals trace</h2>
            <span class="chart-hint">Drag charts to pan. Desktop drag-select zooms; double-click resets.</span>
          </div>
          <div class="chart-actions">
            <span class="update-chip" id="updateChip">New data</span>
            <button id="resetZoomHeader" type="button" title="Reset the vitals and O₂ trend charts to the selected range">Reset view</button>
            <span class="o2-menu-wrap" id="o2AddWrap">
              <button id="o2AddMenuToggle" class="o2-add-button" type="button" aria-expanded="false" title="Add O₂ challenge event">O₂+</button>
              <span id="o2AddMenu" class="o2-add-menu hidden" role="menu" aria-label="Add O₂ challenge">
                <button id="menuVisibleChallenge" type="button" role="menuitem">Use current graph window</button>
                <button id="menuNewChallenge" type="button" role="menuitem">Enter new times…</button>
              </span>
            </span>
          </div>
        </div>
        <div class="chart-toolbar" aria-label="Graph controls">
          <div class="control-section view-section">
            <label class="control-field" for="window">Range
              <select id="window">
                <option value="6">6 hours</option>
                <option value="12">12 hours</option>
                <option selected value="24">24 hours</option>
                <option value="72">3 days</option>
                <option value="168">7 days</option>
                <option value="720">30 days</option>
                <option value="all">All stored data</option>
              </select>
            </label>
            <label class="control-field" for="smoothing">Smoothing
              <select id="smoothing">
                <option selected value="raw">Raw points</option>
                <option value="5">5 min avg</option>
                <option value="15">15 min avg</option>
                <option value="30">30 min avg</option>
                <option value="60">1 hour avg</option>
                <option value="240">4 hour avg</option>
              </select>
            </label>
            <label class="control-field" for="chartLayout">Layout
              <select id="chartLayout">
                <option selected value="combined">Combined</option>
                <option value="split">Split panels</option>
              </select>
            </label>
            <button id="resetZoom" type="button">Reset zoom</button>
            <button id="download" class="icon-button" title="Download CSV" aria-label="Download CSV">CSV</button>
            <span class="control-section-title">Overlays</span>
            <label class="inline-toggle"><input id="challengeBandsToggle" type="checkbox" checked /> O₂ windows</label>
            <label class="inline-toggle"><input id="sleepHighlightToggle" type="checkbox" /> Sleep colors</label>
            <label class="inline-toggle"><input id="sleepBallparkToggle" type="checkbox" disabled /> Guess sleep windows</label>
            <span class="small coverage-chip" id="coverage">—</span>
          </div>
        </div>
        <div class="chart-frame main" id="combinedFrame"><canvas id="vitalsChart"></canvas></div>
        <div class="chart-frame split-panel hidden" id="spo2Frame"><canvas id="spo2PanelChart"></canvas></div>
        <div class="chart-frame split-panel hidden" id="hrFrame"><canvas id="hrPanelChart"></canvas></div>
        <div class="chart-frame split-panel hidden" id="moveFrame"><canvas id="movePanelChart"></canvas></div>
        <div id="stateStripWrap" class="state-strip-wrap" title="Sleep/wake/offline state across the visible vitals window">
          <div id="stateStrip" class="state-strip"></div>
          <div id="stateTimeAxis" class="state-time-axis"></div>
          <div id="stateTooltip" class="state-tooltip hidden" role="tooltip"></div>
        </div>
        <div class="companion-chart" aria-label="Oxygen trend companion chart">
          <div class="chart-frame companion"><canvas id="oxygenTrendChart"></canvas>
            <span class="info-popover-wrap companion-info">
              <button class="info-button" type="button" aria-label="How to read the O₂ trend companion">i</button>
              <span class="info-popover" role="tooltip">
                O₂ trend companion: a MACD-style oxygen view. It compares recent 30-minute O₂ against the 4-hour baseline. Bars above the zero line (mint/green) mean recent O₂ is above baseline; bars below it (red) mean below. Offline gaps are not bridged; oxygen challenges stay visible as real readings behind the blue challenge band.
              </span>
            </span>
          </div>
        </div>
        <div class="time-pan-control">
          <span id="panStartLabel">—</span>
          <input id="timePan" type="range" min="0" max="1000" value="0" disabled aria-label="Scroll visible time window" />
          <span id="panEndLabel">—</span>
        </div>
      </div>
    </section>

    <section class="panel" style="margin-top: 14px;">
        <div class="panel-title">
          <h2>Drill-down table</h2>
          <span class="small">averages + sleep/awake estimates</span>
        </div>
        <div class="table-wrap"><table id="rollupTable"></table></div>
    </section>

    <section class="grid readings-grid" aria-label="Readings and selected detail">
      <div class="panel readings-panel">
        <div class="panel-title">
          <h2>Readings table</h2>
          <span class="small" id="tableCount">—</span>
        </div>
        <div class="table-wrap"><table id="readingsTable"></table></div>
      </div>
      <div class="panel reading-detail-panel">
        <div class="panel-title">
          <h2>Selected reading</h2>
          <span class="small">click any row on the left</span>
        </div>
        <pre id="raw" class="raw-box">No reading selected yet.</pre>
      </div>
    </section>
  </main>

  <div id="challengeModal" class="challenge-modal hidden" role="dialog" aria-label="Oxygen challenge detail">
    <div class="challenge-modal-card">
      <div class="panel-title">
        <div>
          <h2 id="challengeModalTitle">Oxygen challenge</h2>
          <span class="small" id="challengeModalMeta">—</span>
        </div>
        <button id="closeChallengeModal" type="button">Close</button>
      </div>
      <div id="challengeSummary" class="challenge-summary-grid"></div>
      <form id="challengeEditForm" class="challenge-edit-form">
        <label>Label
          <input id="challengeEditLabel" name="label" type="text" />
        </label>
        <label>Start time
          <input id="challengeEditStart" name="start_time" type="datetime-local" />
        </label>
        <label>End time
          <input id="challengeEditEnd" name="end_time" type="datetime-local" />
        </label>
        <label class="full">Notes
          <textarea id="challengeEditNotes" name="notes"></textarea>
        </label>
        <div class="control-group full" style="justify-content: space-between;">
          <button id="saveChallengeEdits" type="submit">Save edits</button>
          <button id="deleteChallenge" class="danger-button" type="button">Delete challenge</button>
        </div>
      </form>
      <div id="challengeDetailChartFrame" class="chart-frame secondary"><canvas id="challengeDetailChart"></canvas></div>
      <p class="small" id="challengeNotes"></p>
    </div>
  </div>

  <div id="dailyInsightsModal" class="challenge-modal hidden" role="dialog" aria-label="Daily insights">
    <div class="challenge-modal-card">
      <div class="panel-title">
        <div>
          <h2>Daily insights</h2>
          <span class="small" id="dailyInsightsMeta">Calendar days. Today is in progress; offline/sock-off and O₂ challenge samples excluded.</span>
        </div>
        <button id="closeDailyInsights" type="button">Close</button>
      </div>
      <div id="dailyInsightsSummary" class="daily-insights-summary"></div>
      <div class="daily-insights-chart" aria-label="Daily O₂, heart-rate, and skin-temperature comparison chart"><canvas id="dailyInsightsChart"></canvas></div>
      <div class="table-wrap"><table id="dailyInsightsTable" class="daily-insights-table"></table></div>
      <p class="small">Sleeping = Owlet light/deep sleep states. Waking = Owlet awake state. Inactive/unknown samples count only in overall averages.</p>
    </div>
  </div>

  <script>
    const API_BASE = "__API_BASE__";
    const SHARE_MODE = __SHARE_MODE__;
  </script>
  <script src="/static/pages/data.js"></script>
"""


def render_dashboard(api_base: str = "", *, share_mode: bool = False) -> str:
    newline = chr(10)
    pwa_links = [
        '<link rel="manifest" href="/manifest.webmanifest" />',
        '<link rel="icon" href="/favicon.ico" sizes="any" />',
        '<link rel="icon" type="image/svg+xml" href="/logo.svg" />',
        '<link rel="icon" type="image/png" sizes="32x32" href="/icon-32.png" />',
        '<link rel="apple-touch-icon" sizes="180x180" href="/icon-180.png" />',
    ]
    pwa_head = "" if share_mode else ("  " + (newline + "  ").join(pwa_links))
    head = DATA_HEAD.replace("__PWA_HEAD__", pwa_head)
    if share_mode:
        parts = [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8" />',
            '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
            "  <title>Owlet Dashboard - shared view</title>",
            THEME_RESOLVER,
            '  <link rel="stylesheet" href="/theme.css" />',
            head,
            "</head>",
            '<body class="themed">',
            DATA_BODY,
            "</body>",
            "</html>",
        ]
        page = newline.join(parts)
    else:
        page = render_shell(view="data", title="Data", head=head, body=DATA_BODY, wide=True)
    return (
        page.replace("__API_BASE__", api_base)
        .replace("__SHARE_MODE__", "true" if share_mode else "false")
    )
