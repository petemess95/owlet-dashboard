"""'Rhythms' — a 14-day actogram and pattern reader, rendered through the shared
app shell. Editorial in light theme; the same structure holds up in dark.

Sections (each renders only when its data exists):
  actogram · O₂ dip map · pattern tiles · desat burden · baseline drift ·
  deep-sleep gap · on/off-O₂ report · feed response · challenge ledger ·
  battery wear
"""

from __future__ import annotations

from app.shell import render_shell

RHYTHMS_HEAD = '<link rel="stylesheet" href="/static/pages/rhythms.css" />'

RHYTHMS_BODY = """<h1 id="title">The shape of <em>the days</em></h1>
    <p class="lede" id="lede">Every row is a day, every column a half-hour. Indigo is sleep
      (stronger = deeper), dusty rose is awake, blank is no signal. Over weeks, a rhythm emerges —
      this page is where you watch it happen.</p>
    <div id="content"></div>"""

RHYTHMS_SCRIPTS = '<script src="/static/pages/rhythms.js"></script>'


def render_rhythms_page() -> str:
    return render_shell(
        view="rhythms",
        title="Rhythms",
        head=RHYTHMS_HEAD,
        body=RHYTHMS_BODY,
        scripts=RHYTHMS_SCRIPTS,
    )
