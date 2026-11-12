"""'Today' — the app's home. The ten-second check: live vitals with personal
context, today so far, and doors into the deeper views. The hero charts are
touchable: tap or hold to read a value, pull right to scroll back in time.
Chips open detail sheets, and care events (O₂ on/off, sock off…) can be
logged and show up as markers on the charts."""

from __future__ import annotations

from app.shell import render_shell

NOW_HEAD = """<link rel="manifest" href="/manifest.webmanifest" />
  <link rel="stylesheet" href="/static/pages/now.css" />"""

_ICON_O2 = (
    '<svg class="v-icon" viewBox="0 0 24 24" aria-hidden="true">'
    '<circle cx="9" cy="11" r="5.5"/><circle cx="17.5" cy="15.5" r="3.5"/></svg>'
)
_ICON_HR = (
    '<svg class="v-icon" viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M2.5 12.5h4l2.2-6 4.3 11.5 2.4-5.500h6.100"/></svg>'
)
_ICON_EXPAND = (
    '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"/></svg>'
)

_EXPAND_BTN = (
    '<button class="vital-expand" data-expand="{key}" type="button" '
    'aria-label="Expand {label} chart" title="Bigger chart &amp; stats">' + _ICON_EXPAND + "</button>"
)

_PLOT = """<div class="plot chartzone" id="{key}Plot">
          <svg viewBox="0 0 100 42" preserveAspectRatio="none" aria-hidden="true"><g id="{key}G"></g></svg>
          <div class="thlabels" id="{key}Th"></div>
          <div class="gaplabels" id="{key}Gaps"></div>
          <div class="xline" id="{key}X" hidden></div>{extra}
        </div>"""

_TICKS = '<div class="ticks"><span class="ts-start"></span><span class="ts-end"></span></div>'

_VITAL = (
    """<div class="vital {key}" id="card-{key}" data-metric="{key}">
        <div class="reading">
          {icon}
          <div class="big"><span class="num" id="{key}Value">—</span><span class="unit" id="{key}Unit">{unit}</span></div>
          <span class="tier" id="{key}Tier" hidden></span>
          <span class="vital-tools">{tools}"""
    + _EXPAND_BTN
    + """</span>
        </div>
        <div class="label"><span>{label}</span><span class="band" id="{key}Band"></span></div>
        """
    + _PLOT
    + """
        """
    + _TICKS
    + """
      </div>"""
)

NOW_BODY = (
    """<div class="today">
    <section class="hello" id="hello">
      <div class="hello-text">
        <h1 class="status-line" id="statusLine">Checking in…</h1>
        <p id="statusDetail"></p>
      </div>
      <div class="range-seg" id="rangeSeg" role="group" aria-label="Chart window">
        <button type="button" data-mins="60">1h</button>
        <button type="button" data-mins="180">3h</button>
        <button type="button" data-mins="360">6h</button>
        <button type="button" data-mins="720">12h</button>
        <button type="button" data-mins="1440">24h</button>
        <span class="rs-custom" id="rangeCustom" hidden>Custom</span>
      </div>
    </section>
    <div class="grid" id="todayGrid">
      <section class="card tcard live-card" aria-labelledby="liveTitle">
        <div class="card-head">
          <div>
            <h2 id="liveTitle">Live health readings</h2>
            <div class="note" id="liveNote" role="status">Waiting for the first reading…</div>
          </div>
          <button class="live-chip" id="tsLive" type="button">● live</button>
        </div>
        <div class="vitals">
      """
    + _VITAL.format(
        key="o2", label="Oxygen", unit="%", icon=_ICON_O2,
        tools=(
            '<button class="vital-o2toggle" id="o2ToggleBtn" type="button" '
            'title="Log supplemental O₂ on/off at a point on the chart">O₂</button>'
        ),
        extra='<div class="place-hint" id="placeHint" hidden></div>',
    )
    + _VITAL.format(key="hr", label="Pulse rate", unit="BPM", icon=_ICON_HR, tools="", extra="")
    + """
        </div>
      </section>

      <section class="card tcard move-card" id="card-move" data-metric="move" aria-labelledby="moveTitle">
        <div class="card-head">
          <div>
            <h2 id="moveTitle">Movement</h2>
            <div class="note"><b id="moveValue">—</b><span class="band" id="moveBand"></span></div>
          </div>
          """
    + _EXPAND_BTN.format(key="move", label="Movement")
    + """
        </div>
        <div class="meter" id="moveMeter" role="img" aria-label="Movement level"><i id="moveMark" hidden></i></div>
        <div class="ends" aria-hidden="true"><span>Peaceful</span><span>Wiggling</span></div>
        """
    + _PLOT.format(key="move", extra="")
    + """
      </section>

      <section class="card tcard sleep-card" id="card-sleep" data-metric="sleep" aria-labelledby="sleepTitle">
        <div class="card-head">
          <div>
            <h2 id="sleepTitle">Sleep summary</h2>
            <div class="note"><b id="sleepValue">—</b><span class="band" id="sleepBand"></span></div>
          </div>
          """
    + _EXPAND_BTN.format(key="sleep", label="Sleep")
    + """
        </div>
        <div class="pair">
          <button type="button" class="pair-btn" data-open="sleep" title="Sleep sessions and stages">
            <span class="k">Time asleep today</span>
            <span class="v" id="sleepToday">—</span>
            <span class="more">Sessions ›</span>
          </button>
          <div><span class="k">Wakings today</span><span class="v" id="wakingsToday">—</span></div>
        </div>
        <h3>Sleep cycle</h3>
        """
    + _PLOT.format(key="sleep", extra="")
    + """
        """
    + _TICKS
    + """
        <div class="legend">
          <span><i class="lg-light"></i>Light</span>
          <span><i class="lg-deep"></i>Deep</span>
          <span><i class="lg-awake"></i>Awake</span>
        </div>
      </section>

      <section class="card tcard story" id="storyCard" aria-label="Last night">
        <div class="moon" aria-hidden="true"></div>
        <p>Reading last night…</p>
        <a href="/night">Last night's report →</a>
      </section>

      <section class="card tcard list" aria-labelledby="sockTitle">
        <h2 id="sockTitle">Sock</h2>
        <div class="note" id="sockSub">Checking…</div>
        <dl id="sockRows"></dl>
        <dl>
          <div class="lrow" id="armedRow" hidden>
            <dt>This PC's alerts</dt><dd id="armedValue" role="status"></dd>
          </div>
        </dl>
      </section>

      <div id="belowHero"></div>
    </div>
    <div class="sheet-backdrop" id="sheetBackdrop" hidden>
      <div class="sheet card" role="dialog" aria-modal="true" aria-labelledby="sheetTitle">
        <header><b id="sheetTitle"></b><button id="sheetClose" type="button" aria-label="Close">✕</button></header>
        <div id="sheetBody"></div>
      </div>
    </div>
    </div>"""
)

NOW_SCRIPTS = """<script src="/insights.js"></script>
  <script src="/static/pages/now.js"></script>"""


def render_now_page() -> str:
    return render_shell(
        view="now",
        title="Today",
        head=NOW_HEAD,
        body=NOW_BODY,
        scripts=NOW_SCRIPTS,
    )
