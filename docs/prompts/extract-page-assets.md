# Task: extract inline CSS/JS from the big page modules into real static files

Repo: `P:\codex\owlet-dashboard fork`, branch `v0.2`. Read `CLAUDE.md` first (the repo map).

## Why

`app/now_page.py` (142 KB), `app/dashboard.py` (178 KB), `app/shell.py` (138 KB) and, to a lesser
degree, `rhythms_page.py`, `night_page.py`, `widget_page.py` are mostly CSS/JS inside Python string
constants. Future sessions burn 35-45k tokens opening one, and the CSS/JS can't be linted or
edited in isolation. Goal: make each concern a small, separately-readable file with **zero change in
what the browser receives**.

## Do this

1. **Start from a clean tree.** Run `git status`. If files are modified that aren't yours
   (another session was editing `now_page.py`, `store.py`, `poller.py`), stop and tell me. Do not
   proceed on top of someone else's uncommitted work. Work on a new branch off `v0.2`.
2. **Pilot on one page only: `night_page.py`** (smallest page module, no share-mode variant). Move its
   `NIGHT_HEAD` `<style>` to `app/static/pages/night.css` and its `NIGHT_SCRIPTS` `<script>` to
   `app/static/pages/night.js`. Keep the HTML body in Python. Show me the pilot result (diff stats +
   test run) before doing others.
3. After I approve, repeat for `rhythms_page.py`, `now_page.py`, `dashboard.py`, then `shell.py`
   (`SHELL_JS`, `SETTINGS_CSS`, `DESKTOP_SETTINGS_JS`, `DESKTOP_SETTINGS_TEMPLATE`, the CSS in
   `render_shell`). Leave `widget_page.py` alone unless trivial: it is deliberately one
   self-contained, no-network page (see `tests/test_widget_js.py`).
4. Serve the files through **one** new generic route in `app/main.py` (whitelist of filenames or
   strict `[a-z0-9_-]+\.(css|js)` match under `app/static/pages/`, no path traversal), same
   `Cache-Control: no-cache` as `/theme.css`. Pages then use `<link>`/`<script src>`.
5. Server-side values interpolated into these blocks (Python `.format`, f-strings, `{{`/`}}`
   escapes, `__PLACEHOLDER__` replaces) must keep working. Either keep a thin inline
   `<script>window.__X = {...}</script>` config shim in Python, or fetch from an existing API.
   Decide per case and note it in the commit message. Un-double any `{{ }}` braces you move out
   of `.format` strings.

## Constraints (don't skip)

- **Behaviour-identical.** Safety-critical alert UI lives here (amber/red state, snooze, armed dot).
  No logic edits while moving. Moves only.
- **Share links** (`/share/{token}`) serve standalone pages, and `_share_path_is_authorized` in
  `main.py` gates which paths a share token may fetch. New asset URLs must work for share pages
  and for the unauthenticated/auth pages if they use them. Update the allow-list deliberately.
- **Tests currently extract `<script>` from Python output** (`tests/test_settings_page.py`
  uses `shell.SHELL_JS` / `shell.DESKTOP_SETTINGS_JS`; `tests/widget_alerts.test.cjs` regex-extracts
  `<script>` from rendered pages). Update those helpers to read the `.js` files; don't weaken
  their assertions.
- **Desktop sidecar**: `desktop/owlet-server.spec` uses `collect_data_files('app')`, so new static
  files should bundle automatically, but confirm that, and confirm the Tauri window (loads from
  `127.0.0.1:8877`) doesn't block `/static/pages/*`. I already saw `"csp": null` in
  `tauri.conf.json`; still check `capabilities/*.json` URL scopes. You can't run Tauri; state what you checked.
- Docker image (`Dockerfile`, `.dockerignore`) must include the new directory.
- Page loads should not get slower on first paint: prefer a `<link rel="stylesheet">` in
  `<head>` (render-blocking, no FOUC) and `<script src>` at the end of body, as today.
- Service worker (`app/pwa.py`, `CACHE_NAME`): it serves only an `IMMUTABLE` list cache-first and
  treats styles/scripts as network-first. Keep the new files out of `IMMUTABLE` (they change often).

## Verify

- `.venv/Scripts/python -m pytest -q` (≈230 pass), `node --test tests/widget_alerts.test.cjs`,
  `.venv/Scripts/python -m ruff check app tests`. Same pass counts as before.
- Golden check per page: before touching it, save the fully rendered HTML (app test client
  or `render_*()`), and afterwards confirm the only differences are the inline block being replaced
  by a `<link>`/`<script src>`, and that the served file bytes equal the removed block.
- Run the app (`.claude/launch.json` has a `review` config; or uvicorn on 127.0.0.1) and load each
  migrated page in the browser: no console errors, no 404s in the network panel, charts render,
  light and dark theme both fine.

## Deliver

One commit per page (so any can be reverted). Update the table in `CLAUDE.md` so each row points
to the new `.css`/`.js` file instead of a Python string constant. Report: bytes before/after per
Python module, anything you chose not to move and why.
