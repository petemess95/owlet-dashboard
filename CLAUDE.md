# Owlet Dashboard: repo map

FastAPI + SQLite app (`app/`) with a Tauri Windows shell (`desktop/`). Pages are server-rendered
Python strings of HTML/CSS/JS. Branch `v0.2`. Safety-critical: the low-oxygen alert path (see the
"Not a medical alarm" notes in README) must never be weakened silently.

## Token rules (read first)

- **Never read whole** `app/dashboard.py`, `now_page.py`, `shell.py` (each 140-180 KB). Grep for the
  anchor in the table below, then `Read` with `offset`/`limit`.
- **Skip** `docs/` unless the task names it. `docs/*v0.2*` is the v0.2 build plan/handoff (~100 KB;
  current state: `docs/orchestration-v0.2-handoff.md`). `docs/archive/` is finished v0.1 history; never read. `docs/prompts/` holds ready-made task briefs; read only if told.
- **Skip** `*.png`/`*.wav`. `Cargo.lock`, `desktop/{build,dist}`, `.venv`, `data/` and caches are
  blocked from Read in `.claude/settings.json`.
- Use `Grep` for symbols; don't `ls`/`Read` directories to explore.

## Where things live

| Want to change | File (anchor to grep) |
|---|---|
| HTTP routes, API JSON, share links, desktop config | `app/main.py` (`create_app`; routes are `@app.get/post(...)` inside it; helpers at bottom) |
| Alert JSON for widget/overlay (`/api/widget`, `/api/alert-state`, snooze/ack/test) | `app/main.py` (`_widget_payload`, `alert_state`) + `app/alerts.py` |
| Amber/red alert engine, snooze, timing | `app/alerts.py` (`DeviceAlertTracker`, `AlertMonitor`, `resolve_alert_timing`) |
| Polling Owlet, notifications/events creation | `app/poller.py` (`Poller`); Owlet API adapter only in `app/owlet_client.py` |
| DB schema, migrations, queries | `app/store.py` (`ReadingStore`, `init`, `_ensure_*_schema`) |
| Users/sessions/login | `app/auth_store.py`, `auth_routes.py`, `auth_pages.py`, `security.py`, `ratelimit.py` |
| Data models | `app/models.py`, `notifications.py`, `quality.py` (offline filter) |
| Stats / trends / insights | `app/analytics.py`, `oxygen_challenges.py`; client side `app/static/insights.js` |
| Nav bar, tabs, theme, **Settings UI** (incl. "This PC" settings) | `app/shell.py` (`TABS`, `SHELL_JS`, `SETTINGS_CSS`, `DESKTOP_SETTINGS_TEMPLATE`, `DESKTOP_SETTINGS_JS`, `render_shell`) |
| Today page (`/`) | `app/now_page.py` (`NOW_HEAD` css, `NOW_BODY` html, `NOW_SCRIPTS` js) |
| Tonight page (`/night`) | css `app/static/pages/night.css`, js `app/static/pages/night.js`; html in `app/night_page.py` (`NIGHT_BODY`) |
| Page CSS/JS files (`app/static/pages/*.{css,js}`) | served by `page_asset` in `app/main.py` at `/static/pages/<name>` (public, `no-cache`) |
| Rhythms page (`/rhythms`) | `app/rhythms_page.py` (`RHYTHMS_*`) |
| Data workbench (`/data`, share pages) | `app/dashboard.py` (`DATA_HEAD`, `DATA_BODY`, `render_dashboard`) |
| Always-on-top widget page (`/widget`) | `app/widget_page.py` (`WIDGET_HTML`) |
| Colours / fonts / PWA | `app/static/theme.css`, `static/fonts`, `app/pwa.py` |
| Env settings | `app/config.py`, `.env.example` |

### Desktop (`desktop/src-tauri/src/`, Rust)

`main.rs` wiring + sidecar spawn · `settings.rs` desktop settings store · `commands.rs` Tauri commands ·
`alert_loop.rs` polls server alert state · `armed.rs` armed verdict + keep-awake · `audio.rs` alarm/chime
(WASAPI) · `overlay.rs` monitor flash · `widget.rs` widget window modes/opacity · `tray.rs`, `tray_icon.rs`
· `hotkeys.rs` · `controller.rs`, `controller_hid.rs` pad snooze · `gamemode.rs` fullscreen detection ·
`autostart.rs` start-with-Windows/first run · `http.rs` local HTTP client.
Python sidecar entry: `desktop/server_entry.py`; build: `desktop/build-sidecar.ps1`; docs: `desktop/README.md`.

### Tests

`tests/test_api.py` (routes; biggest) · `test_alerts.py` · `test_store.py` · `test_analytics.py` ·
`test_auth*.py` · `test_tenancy.py` · `test_settings_page.py` · `test_widget_js.py` runs
`tests/widget_alerts.test.cjs` (node; extracts the `<script>` from `widget_page.py`/overlay).
Rust tests: `cargo test` in `desktop/src-tauri`.

## Commands

```bash
.venv/Scripts/python -m pytest -q tests/test_alerts.py   # target one file first
.venv/Scripts/python -m pytest -q                        # full (≈230 tests)
.venv/Scripts/python -m ruff check app tests
node --test tests/widget_alerts.test.cjs
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8888
```

## Conventions

- Pages are plain Python strings: JS/CSS inside them is not linted; check by running the page tests.
- Everything is account-scoped (`account_id`/`user_id`); keep tenancy tests passing when touching `store.py`.
- Keep the map current: if you add/move a module or page, update the table above.
