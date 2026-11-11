from __future__ import annotations

import html

from app.shell import THEME_RESOLVER

# These pages are standalone (no app shell) but share the app's colours: each
# head links /theme.css, which is public (served without a session) so sign-in
# renders before login. Only page-local, non-colour rules live here. Dark is
# the default; THEME_RESOLVER (the app's own) sets data-theme from the theme
# saved on this device, else the system setting, so sign-in looks like the app
# the visitor is about to land in.
_THEME_LINK = '<link rel="stylesheet" href="/theme.css" />'
_BASE_STYLES = """
    :root, :root[data-theme="dark"] { color-scheme: dark; --radius-field:14px; }
    :root[data-theme="light"] { color-scheme: light; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:var(--font-sans); color:var(--ink);
      background:radial-gradient(900px 420px at 50% 60px, var(--bg-accent-1), transparent 70%) no-repeat, var(--bg); }
    /* a few stars near the top, dark only; static, drawn for this project */
    body::before { content:''; display:var(--sky-display, none); position:absolute; top:0; left:0; right:0;
      height:420px; z-index:-1; pointer-events:none;
      background-image:
        radial-gradient(1.2px 1.2px at 6% 44px, var(--star-bright) 50%, transparent 51%),
        radial-gradient(1px 1px at 15% 190px, var(--star-dim) 50%, transparent 51%),
        radial-gradient(1.5px 1.5px at 27% 96px, var(--star-dim) 50%, transparent 51%),
        radial-gradient(1px 1px at 39% 28px, var(--star-bright) 50%, transparent 51%),
        radial-gradient(1px 1px at 48% 250px, var(--star-dim) 50%, transparent 51%),
        radial-gradient(1.2px 1.2px at 58% 70px, var(--star-dim) 50%, transparent 51%),
        radial-gradient(1px 1px at 69% 170px, var(--star-bright) 50%, transparent 51%),
        radial-gradient(1.5px 1.5px at 78% 38px, var(--star-dim) 50%, transparent 51%),
        radial-gradient(1px 1px at 88% 220px, var(--star-dim) 50%, transparent 51%),
        radial-gradient(1.2px 1.2px at 95% 88px, var(--star-bright) 50%, transparent 51%);
      -webkit-mask-image:linear-gradient(#000 45%, transparent); mask-image:linear-gradient(#000 45%, transparent); }
    label { display:block; font-size:13.5px; font-weight:600; margin:14px 0 6px; }
    /* 16px minimum stops mobile Safari's forced zoom-on-focus */
    input, select { width:100%; padding:12px 18px; border:1px solid var(--surface-line);
      border-radius:var(--radius-control); font:inherit; font-size:16px;
      background:var(--surface-inset); color:var(--ink); }
    input:focus-visible, select:focus-visible, button:focus-visible, a:focus-visible {
      outline:2px solid var(--accent); outline-offset:2px; }
    /* keep browser autofill on the theme instead of its pale default */
    input:-webkit-autofill { -webkit-text-fill-color:var(--ink);
      box-shadow:0 0 0 1000px var(--surface-inset) inset; caret-color:var(--ink); }
    button { width:100%; padding:13px 18px; border:0; border-radius:var(--radius-control);
      background:var(--accent); color:var(--on-accent); font:inherit; font-size:15.5px; font-weight:700;
      cursor:pointer; }
    button:hover { filter:brightness(1.07); }
    button[disabled] { opacity:.6; cursor:default; }
    a { color:var(--accent); }
    /* errors: red edge and tint, plus a warning glyph so colour is not the only signal */
    .notice { display:flex; gap:10px; align-items:flex-start; color:var(--ink);
      background:color-mix(in srgb, var(--bad) 13%, var(--surface));
      border-left:4px solid var(--bad); border-radius:var(--radius-field);
      padding:10px 14px; font-size:14px; line-height:1.45; margin-bottom:10px; }
    .notice::before { content:'\\26A0\\FE0E'; color:var(--bad); font-weight:700; flex:none; }
"""

_BASE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} · Owlet Dashboard</title>
  <link rel="icon" href="/favicon.ico" sizes="any" />
  {resolver}
  <link rel="stylesheet" href="/theme.css" />
  <style>{base_styles}
    body {{ display:flex; min-height:100vh; align-items:center; justify-content:center; padding:24px 16px; }}
    .card {{ background:var(--surface); border-radius:var(--radius-card);
            padding:36px 32px 28px; width:100%; max-width:440px; }}
    h1 {{ margin:0 0 6px; font-size:28px; font-weight:700; line-height:1.15; }}
    p.sub {{ margin:0 0 20px; color:var(--dim); font-size:15px; line-height:1.5; }}
    p.sub strong {{ color:var(--ink); font-weight:600; }}
    form > button {{ margin-top:22px; }}
    .links {{ margin-top:16px; font-size:14px; color:var(--dim); display:flex; justify-content:space-between; align-items:center; }}
    .links a {{ font-weight:600; text-decoration:none; }}
    .links button.quiet {{ background:none; color:var(--dim); margin:0; padding:12px 10px; width:auto;
            min-height:44px; font-size:14px; font-weight:600; }}
    .links button.quiet:hover {{ color:var(--ink); filter:none; }}
    footer {{ margin-top:22px; font-size:12px; line-height:1.5; color:var(--dim); text-align:center; }}
    @media (max-width: 420px) {{ .card {{ padding:28px 20px 22px; }} }}
  </style>
</head>
<body>
  <div class="card">
    {body}
    <footer>Retrospective trend viewing only — not a medical monitor or alert replacement.</footer>
  </div>
</body>
</html>"""


def _page(title: str, body: str) -> str:
    return _BASE.format(title=html.escape(title), body=body, base_styles=_BASE_STYLES, resolver=THEME_RESOLVER)


def _error(error: str | None) -> str:
    # role="alert" makes screen readers announce the failure on page load
    return f'<div class="notice" role="alert">{html.escape(error)}</div>' if error else ""


def desktop_redirect_page(url: str) -> str:
    """Bounce the desktop window to a remote instance. A plain meta/JS redirect
    so the webview lands on the always-on server and behaves as its web app."""
    safe = html.escape(url, quote=True)
    return (
        f'<!doctype html><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={safe}">'
        f'{THEME_RESOLVER}{_THEME_LINK}<style>{_BASE_STYLES}</style>'
        f'<script>location.replace({html.escape(_json_str(url))})</script>'
        f'<p style="font-size:15px;padding:24px;color:var(--dim)">Connecting to {safe}…</p>'
    )


def _json_str(value: str) -> str:
    import json as _json

    return _json.dumps(value)


def desktop_launcher_page(current_backend: str | None = None, error: str | None = None) -> str:
    change = ""
    if current_backend:
        change = (
            f'<p class="alt">Currently connected to '
            f'<b>{html.escape(current_backend)}</b>.</p>'
        )
    prefill = html.escape(current_backend or "", quote=True)
    return _LAUNCHER.format(error=_error(error), change=change, prefill=prefill,
                            base_styles=_BASE_STYLES, resolver=THEME_RESOLVER)


_LAUNCHER = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Owlet Dashboard — get started</title>
  <link rel="icon" href="/favicon.ico" sizes="any" />
  {resolver}
  <link rel="stylesheet" href="/theme.css" />
  <style>{base_styles}
    h1, h2, p {{ margin:0; }}
    body {{ min-height:100vh; display:flex; align-items:center; justify-content:center; padding:32px 16px; }}
    .wrap {{ width:100%; max-width:640px; }}
    .brand {{ display:flex; align-items:center; gap:12px; margin-bottom:8px; }}
    .brand img {{ width:40px; height:40px; border-radius:12px; }}
    .brand span {{ font-size:19px; font-weight:700; letter-spacing:-.01em; }}
    h1 {{ font-size:30px; font-weight:700; line-height:1.15; margin:16px 0 6px; }}
    .lede {{ color:var(--dim); font-size:16px; line-height:1.55; margin-bottom:22px; }}
    .opt {{ background:var(--surface); border-radius:var(--radius-card); padding:24px 26px; margin-bottom:16px; }}
    .opt h2 {{ font-size:19px; font-weight:700; margin-bottom:4px; }}
    .opt p {{ color:var(--dim); font-size:14.5px; line-height:1.5; margin-bottom:14px; }}
    /* the suggested choice is named in words; the other option's button is quieter */
    .opt .tag {{ display:inline-block; margin-bottom:8px; padding:3px 10px; border-radius:var(--radius-control);
      background:var(--accent-soft); color:var(--accent); font-size:12.5px; font-weight:700; }}
    input {{ margin-bottom:10px; }}
    button.ghost {{ background:var(--bg-accent-2); color:var(--ink); }}
    .alt {{ font-size:14px; color:var(--dim); margin-top:8px; text-align:center; }}
    .alt b {{ color:var(--ink); }}
    footer {{ text-align:center; font-size:12.5px; color:var(--dim); margin-top:14px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand"><img src="/logo.svg" alt="" /><span>Owlet Dashboard</span></div>
    <h1>How do you want to use this app?</h1>
    <p class="lede">Point this window at an always-on server you run, or collect right here on
      this PC.</p>
    {error}
    <div class="opt primary">
      <span class="tag">Recommended</span>
      <h2>Connect to a live server</h2>
      <p>Best if you run the Docker/Unraid version somewhere always-on. This window becomes a
        viewer for it — real 24/7 history, nothing lost when this PC sleeps, and a normal login.</p>
      <input id="serverUrl" type="url" inputmode="url" placeholder="https://owlet.yourdomain.com"
        value="{prefill}" autocomplete="off" />
      <button id="connectBtn" type="button">Connect</button>
    </div>
    <div class="opt">
      <h2>Collect on this PC</h2>
      <p>No server needed. This app collects readings itself — but only while it's open and the PC
        is awake, so expect gaps. Good for trying it out or travel.</p>
      <form method="post" action="/desktop/use-local"><button class="ghost" type="submit">Use this PC</button></form>
    </div>
    {change}
    <footer>You can switch anytime from the Instance menu.</footer>
  </div>
  <script>
    var btn = document.getElementById('connectBtn');
    btn.addEventListener('click', function () {{
      var url = document.getElementById('serverUrl').value.trim();
      if (!/^https?:\\/\\//i.test(url)) {{ alert('Enter a full address starting with http:// or https://'); return; }}
      btn.disabled = true; btn.textContent = 'Connecting…';
      fetch('/desktop/connect', {{ method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{ url: url }}) }})
        .then(function (r) {{ return r.json().then(function (d) {{ return {{ ok: r.ok, d: d }}; }}); }})
        .then(function (res) {{ if (!res.ok) throw new Error(res.d.detail || 'Could not connect'); window.location.href = res.d.url; }})
        .catch(function (e) {{ btn.disabled = false; btn.textContent = 'Connect'; alert(e.message); }});
    }});
  </script>
</body>
</html>"""


_LANDING = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="theme-color" content="#070925" />
  <title>Owlet Dashboard — every night, remembered</title>
  <link rel="icon" href="/favicon.ico" sizes="any" />
  {resolver}
  <link rel="stylesheet" href="/theme.css" />
  <style>{base_styles}
    /* Split screen on one sky: the story on the left, the sign-in card on the
       right. Night sky by default, morning sky in light. */
    h1, h2, p {{ margin:0; }}
    body {{ min-height:100vh; display:grid; grid-template-columns:1.08fr .92fr; }}
    /* ---- left: the pitch ------------------------------------------------ */
    .story {{ padding:52px 56px 0; display:flex; flex-direction:column; overflow:hidden; }}
    .brand {{ display:flex; align-items:center; gap:11px; margin-bottom:40px; }}
    .brand img {{ width:32px; height:32px; border-radius:10px; }}
    .brand span {{ font-size:17px; font-weight:700; letter-spacing:-.01em; }}
    /* the one-line story takes the serif (plan D1.4); everything else is sans */
    h1 {{
      font-family:var(--font-serif); font-weight:600;
      font-size:clamp(34px,3.6vw,50px); line-height:1.08; letter-spacing:-.015em;
      max-width:13ch; margin-bottom:16px;
    }}
    h1 em {{ font-style:normal; color:var(--accent); }}
    .lede {{ color:var(--dim); font-size:17px; line-height:1.6; max-width:44ch; margin-bottom:24px; }}
    .pills {{ display:flex; flex-wrap:wrap; gap:8px; }}
    .pills span {{ font-size:14px; font-weight:600; color:var(--ink); background:var(--surface);
                  padding:7px 14px; border-radius:var(--radius-control); }}
    /* The preview sits in a soft card-coloured frame and runs off the bottom
       edge: implies there is more below. */
    .shot {{ margin:38px auto -2px; width:min(320px,80%); padding:10px 10px 0;
            background:var(--surface); border-radius:36px 36px 0 0; }}
    .shot img {{ width:100%; height:auto; display:block; border-radius:var(--radius-card) var(--radius-card) 0 0; }}
    /* ---- right: sign in -------------------------------------------------- */
    .signin {{ display:flex; align-items:center; justify-content:center; padding:48px 44px; }}
    .panel {{ width:100%; max-width:420px; background:var(--surface);
             border-radius:var(--radius-card); padding:36px 34px 30px; }}
    .panel h2 {{ font-size:30px; font-weight:700; line-height:1.15; margin-bottom:6px; }}
    .panel p.sub {{ color:var(--dim); font-size:15px; line-height:1.5; margin-bottom:18px; }}
    .panel p.sub b {{ color:var(--ink); }}
    form > button {{ margin-top:22px; }}
    .alt {{ margin-top:16px; font-size:14.5px; color:var(--dim); text-align:center; }}
    .alt a {{ font-weight:700; text-decoration:none; }}
    .fine {{ margin-top:26px; padding-top:18px; border-top:1px solid var(--surface-line);
            font-size:12.5px; color:var(--dim); line-height:1.55; }}
    .fine a {{ font-weight:600; }}
    .fine p + p {{ margin-top:7px; }}
    /* Side by side, the split owns exactly one screen: the preview clips at the
       bottom edge instead of stretching the page into a scroll. The sign-in
       half scrolls on its own so a short laptop can still reach the button. */
    @media (min-width: 901px) {{
      body {{ height:100vh; }}
      .story {{ overflow:hidden; }}
      .signin {{ overflow-y:auto; }}
    }}
    /* ---- stacked ---------------------------------------------------------- */
    @media (max-width: 900px) {{
      body {{ grid-template-columns:minmax(0,1fr); }}
      .story {{ padding:34px 20px 0; }}
      .brand {{ margin-bottom:26px; }}
      h1 {{ max-width:none; }}
      /* On a phone the preview earns less than the ~550px of scroll it costs
         before the sign-in form: show the top of the Today view as a peek. */
      .shot {{ margin:26px auto 0; width:min(250px,70%); max-height:210px; overflow:hidden;
              padding:8px 8px 0; border-radius:var(--radius-card) var(--radius-card) 0 0; }}
      .shot img {{ border-radius:20px 20px 0 0; }}
      .signin {{ padding:24px 16px 40px; }}
      .panel {{ padding:28px 22px 24px; }}
    }}
  </style>
</head>
<body>
  <section class="story">
    <div class="brand"><img src="/logo.svg" alt="" /><span>Owlet Dashboard</span></div>
    <h1>Every night, <em>remembered</em>.</h1>
    <p class="lede">Sign in with your Owlet account and your sock's readings become a private
      history you can explore — live vitals, a plain-language report each night, and the
      rhythms that emerge as your baby grows.</p>
    <div class="pills">
      <span>Live vitals every 30s</span><span>Nightly reports</span>
      <span>Long-term rhythms</span><span>O₂ and feed logging</span><span>Self-hostable</span>
    </div>
    <div class="shot">{preview}</div>
  </section>
  <main class="signin">
    <div class="panel">
      <h2>Sign in</h2>
      <p class="sub">{signin_sub}</p>
      {error}
      <form method="post" action="/auth/login">
        <label for="email">{email_label}</label>
        <input id="email" name="email" type="text" inputmode="email" required
               autocomplete="username" />
        <label for="password">Password</label>
        <input id="password" name="password" type="password" required
               autocomplete="current-password" />
        <button type="submit">Sign in</button>
      </form>
      {signup_line}
      <div class="fine">
        <p>{privacy_copy}</p>
        <p>Free, open source, and unofficial — provided as-is, not affiliated with Owlet Baby
          Care. <a href="https://github.com/pbozzay/owlet-dashboard" rel="noopener">View the
          project on GitHub</a>.</p>
        <p>Retrospective trend viewing only — not a medical monitor or alert replacement.</p>
      </div>
    </div>
  </main>
</body>
</html>"""


# A real capture of the Today view (mobile width, anonymized name), shipped as a
# static asset and served by /preview-today.png. Refresh it by re-shooting the
# page into app/static/preview-today.png whenever the UI meaningfully changes.
_TODAY_PREVIEW_IMG = (
    '<img src="/preview-today.png" width="375" height="790" loading="lazy" '
    'alt="The Today view: live oxygen and heart-rate charts with baseline bands, '
    'sleep and movement tiles, and the day\'s totals" />'
)


HOSTED_PRIVACY_COPY = (
    "Your Owlet password is verified once and never stored. "
    "Your baby's data is only visible to you."
)
DESKTOP_PRIVACY_COPY = (
    "Everything lives on this computer. Your Owlet login is kept locally so "
    "collection can always reconnect - nothing is sent anywhere else."
)


def login_page(error: str | None = None, desktop_mode: bool = False) -> str:
    # Desktop is a purely local login (seeded admin account), not a web
    # account — offering "create a free account" there reads like signing up
    # for a hosted service that doesn't exist.
    if desktop_mode:
        signin_sub = ("This app runs entirely on this computer. Sign in with the local "
                      "account — <b>admin</b> / <b>password</b> until you change it in "
                      "Settings → Sign-in.")
        signup_line = ""
        email_label = "Username"
    else:
        signin_sub = "Welcome back — your charts are waiting."
        signup_line = '<p class="alt">New here? <a href="/signup">Create a free account</a></p>'
        email_label = "Email"
    return _LANDING.format(
        error=_error(error),
        privacy_copy=DESKTOP_PRIVACY_COPY if desktop_mode else HOSTED_PRIVACY_COPY,
        preview=_TODAY_PREVIEW_IMG,
        signin_sub=signin_sub,
        signup_line=signup_line,
        email_label=email_label,
        base_styles=_BASE_STYLES,
        resolver=THEME_RESOLVER,
    )


def signup_page(error: str | None = None) -> str:
    return _page(
        "Create account",
        f"""<h1>Create your account</h1>
    <p class="sub">Then link your Owlet sock to start collecting history.</p>
    {_error(error)}
    <form method="post" action="/auth/signup">
      <label for="email">Email</label>
      <input id="email" name="email" type="email" required autocomplete="email" />
      <label for="password">Password (8+ characters)</label>
      <input id="password" name="password" type="password" required minlength="8" maxlength="128"
             autocomplete="new-password" />
      <button type="submit">Create account</button>
    </form>
    <div class="links"><a href="/login">Back to sign in</a><span></span></div>""",
    )


def onboarding_page(error: str | None = None, desktop_mode: bool = False) -> str:
    promise = (
        """Your Owlet login is <strong>stored only on this computer</strong> so collection
    can always reconnect, even after weeks powered off."""
        if desktop_mode
        else """We verify it with Owlet once and
    <strong>never store your Owlet password</strong> — only a revocable access token."""
    )
    return _page(
        "Link your Owlet sock",
        f"""<h1>Link your Owlet sock</h1>
    <p class="sub">Enter the login you use in the Owlet app. {promise}</p>
    {_error(error)}
    <form method="post" action="/onboarding/link" onsubmit="const b=this.querySelector('button[type=submit]');b.disabled=true;b.textContent='Linking with Owlet…';">
      <label for="owlet_email">Owlet account email</label>
      <input id="owlet_email" name="email" type="email" required />
      <label for="owlet_password">Owlet account password</label>
      <input id="owlet_password" name="password" type="password" required autocomplete="off" />
      <label for="region">Region</label>
      <select id="region" name="region">
        <option value="world" selected>World (US and most countries)</option>
        <option value="europe">Europe</option>
      </select>
      <button type="submit">Link sock and start collecting</button>
    </form>
    <div class="links"><span></span><form method="post" action="/auth/logout" style="margin:0">
      <button type="submit" class="quiet">
        Sign out</button></form></div>""",
    )
