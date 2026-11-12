"""'Tonight' — a narrative night report. Calm, built for the 7am question:
"how was last night?" Rendered through the shared app shell; the night-sky
styling appears in dark theme, a morning-paper rendering in light."""

from __future__ import annotations

from app.shell import render_shell

NIGHT_HEAD = '<link rel="stylesheet" href="/static/pages/night.css" />'

NIGHT_BODY = """<div class="night-nav" role="group" aria-label="Choose a night">
      <button id="prevNight" type="button" aria-label="Previous night"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg></button>
      <span class="night-when"><b id="nightKicker">Tonight</b><span id="nightSub">loading…</span></span>
      <button id="nextNight" type="button" aria-label="Next night"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg></button>
    </div>
    <h1 class="headline" id="nightTitle">Tonight</h1>
    <p class="lede" id="lede"></p>
    <div id="content"></div>"""

NIGHT_SCRIPTS = '<script src="/static/pages/night.js"></script>'


def render_night_page() -> str:
    return render_shell(
        view="night",
        title="Tonight",
        head=NIGHT_HEAD,
        body=NIGHT_BODY,
        scripts=NIGHT_SCRIPTS,
    )
